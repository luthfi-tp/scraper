import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://books.toscrape.com/"

CACHE_DIR = "cache"
BOOK_CACHE_DIR = os.path.join(CACHE_DIR, "books")

OUTPUT_DIR = "output"

BOOKS_FILE = os.path.join(
    OUTPUT_DIR,
    "books.json"
)

ERRORS_FILE = os.path.join(
    OUTPUT_DIR,
    "errors.json"
)

REPORT_FILE = os.path.join(
    OUTPUT_DIR,
    "run-report.json"
)

HEADERS = {
    "User-Agent": "FlyRankInternship-A9/1.0"
}

TIMEOUT = 10
DELAY = 0.5

last_request_time = 0.0


# ============================================================
# RUN STATISTICS
# ============================================================

stats = {
    "pages_fetched": 0,
    "cache_hits": 0,
    "valid_records": 0,
    "invalid_records": 0,
    "failed_pages": 0
}


# ============================================================
# PYDANTIC SCHEMA
# ============================================================

class BookRecord(BaseModel):
    title: str
    product_url: str
    price_text: str
    price_gbp: float
    availability_text: str
    rating_text: str
    description: Optional[str] = None
    source_page: str
    fetched_at: str


# ============================================================
# TIME
# ============================================================

def current_time():
    """Return current UTC time in ISO format."""

    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


# ============================================================
# POLITE REQUEST DELAY
# ============================================================

def wait_before_request():
    """Wait at least 0.5 seconds between real requests."""

    global last_request_time

    if last_request_time:

        elapsed = (
            time.monotonic()
            - last_request_time
        )

        if elapsed < DELAY:

            time.sleep(
                DELAY - elapsed
            )


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_page(url, cache_file):
    """
    Read from cache when available.

    Otherwise make a real HTTP request.

    Timeout:
        Retry once.

    5xx:
        Retry once.

    403 / 404:
        Do not retry.
    """

    global last_request_time

    cache_directory = os.path.dirname(
        cache_file
    )

    if cache_directory:

        os.makedirs(
            cache_directory,
            exist_ok=True
        )

    # --------------------------------------------------------
    # CACHE HIT
    # --------------------------------------------------------

    if os.path.exists(cache_file):

        with open(
            cache_file,
            "r",
            encoding="utf-8"
        ) as file:

            html = file.read()

        stats["cache_hits"] += 1

        print(
            f"CACHE HIT | size={len(html)} bytes"
        )

        return html

    # --------------------------------------------------------
    # REAL REQUEST
    # --------------------------------------------------------

    wait_before_request()

    print(
        f"FETCH | {url}"
    )

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

    except requests.exceptions.Timeout:

        print(
            "TIMEOUT | retrying once..."
        )

        time.sleep(1)

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT
            )

        except requests.exceptions.Timeout:

            raise RuntimeError(
                f"Request timed out twice: {url}"
            )

    except requests.exceptions.RequestException as error:

        raise RuntimeError(
            f"Request failed: {error}"
        )

    last_request_time = time.monotonic()

    # --------------------------------------------------------
    # 5XX RETRY
    # --------------------------------------------------------

    if 500 <= response.status_code <= 599:

        print(
            f"HTTP {response.status_code} "
            "| retrying once..."
        )

        time.sleep(1)

        wait_before_request()

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT
            )

        except requests.exceptions.RequestException as error:

            raise RuntimeError(
                f"Retry failed: {error}"
            )

        last_request_time = time.monotonic()

    # --------------------------------------------------------
    # STATUS CHECK
    # --------------------------------------------------------

    if response.status_code != 200:

        raise RuntimeError(
            f"Fetch failed: HTTP "
            f"{response.status_code} for {url}"
        )

    # --------------------------------------------------------
    # SUCCESSFUL NETWORK REQUEST
    # --------------------------------------------------------

    stats["pages_fetched"] += 1

    html = response.text

    with open(
        cache_file,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(html)

    print(
        f"FETCHED | size={len(html)} bytes"
    )

    return html


# ============================================================
# CATALOGUE CACHE
# ============================================================

def catalogue_cache_path(page_number):

    return os.path.join(
        CACHE_DIR,
        f"catalogue-page-{page_number}.html"
    )


# ============================================================
# BOOK CACHE
# ============================================================

def book_cache_path(book_url):

    url_hash = hashlib.sha256(
        book_url.encode("utf-8")
    ).hexdigest()[:16]

    return os.path.join(
        BOOK_CACHE_DIR,
        f"{url_hash}.html"
    )


# ============================================================
# STAGE 2
# DISCOVER BOOKS
# ============================================================

def discover_books():

    current_url = BASE_URL

    discovered_books = []

    catalogue_pages = 0

    while current_url and catalogue_pages < 3:

        catalogue_pages += 1

        print(
            f"\nCATALOGUE PAGE "
            f"{catalogue_pages}"
        )

        cache_file = catalogue_cache_path(
            catalogue_pages
        )

        html = fetch_page(
            current_url,
            cache_file
        )

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # ----------------------------------------------------
        # FIND BOOK LINKS
        # ----------------------------------------------------

        for article in soup.select(
            "article.product_pod"
        ):

            link = article.select_one(
                "h3 a"
            )

            if link and link.get("href"):

                product_url = urljoin(
                    current_url,
                    link["href"]
                )

                discovered_books.append({
                    "product_url": product_url,
                    "source_page": current_url
                })

        # ----------------------------------------------------
        # NEXT PAGE
        # ----------------------------------------------------

        next_link = soup.select_one(
            "li.next a"
        )

        if next_link and next_link.get("href"):

            current_url = urljoin(
                current_url,
                next_link["href"]
            )

        else:

            current_url = None

    # --------------------------------------------------------
    # REMOVE DUPLICATES
    # --------------------------------------------------------

    unique_books = []

    seen_urls = set()

    for book in discovered_books:

        product_url = book[
            "product_url"
        ]

        if product_url not in seen_urls:

            seen_urls.add(
                product_url
            )

            unique_books.append(
                book
            )

    print()

    print(
        f"catalogue_pages="
        f"{catalogue_pages}"
    )

    print(
        f"discovered="
        f"{len(discovered_books)}"
    )

    print(
        f"unique_urls="
        f"{len(unique_books)}"
    )

    return unique_books


# ============================================================
# FETCHED AT
# ============================================================

def get_cached_fetched_at(cache_file):

    timestamp = os.path.getmtime(
        cache_file
    )

    return (
        datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


# ============================================================
# STAGE 3
# EXTRACT BOOK
# ============================================================

def extract_book(book):

    product_url = book[
        "product_url"
    ]

    source_page = book[
        "source_page"
    ]

    cache_file = book_cache_path(
        product_url
    )

    html = fetch_page(
        product_url,
        cache_file
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    product_main = soup.select_one(
        "div.product_main"
    )

    if product_main is None:

        raise ValueError(
            f"Product area not found: "
            f"{product_url}"
        )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title_element = product_main.select_one(
        "h1"
    )

    title = (
        title_element.get_text(
            strip=True
        )
        if title_element
        else None
    )

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price_element = product_main.select_one(
        "p.price_color"
    )

    price_text = (
        price_element.get_text(
            strip=True
        )
        if price_element
        else None
    )

    # --------------------------------------------------------
    # AVAILABILITY
    # --------------------------------------------------------

    availability_element = (
        product_main.select_one(
            "p.instock.availability"
        )
    )

    availability_text = (
        availability_element.get_text(
            " ",
            strip=True
        )
        if availability_element
        else None
    )

    # --------------------------------------------------------
    # RATING
    # --------------------------------------------------------

    rating_element = product_main.select_one(
        "p.star-rating"
    )

    rating_text = None

    if rating_element:

        classes = rating_element.get(
            "class",
            []
        )

        for class_name in classes:

            if class_name != "star-rating":

                rating_text = class_name

                break

    # --------------------------------------------------------
    # DESCRIPTION
    # --------------------------------------------------------

    description_heading = soup.select_one(
        "#product_description"
    )

    description = None

    if description_heading:

        description_element = (
            description_heading.find_next_sibling(
                "p"
            )
        )

        if description_element:

            description = (
                description_element.get_text(
                    " ",
                    strip=True
                )
            )

    # --------------------------------------------------------
    # RAW RECORD
    # --------------------------------------------------------

    return {
        "title": title,
        "product_url": product_url,
        "price_text": price_text,
        "availability_text": availability_text,
        "rating_text": rating_text,
        "description": description,
        "source_page": source_page,
        "fetched_at": get_cached_fetched_at(
            cache_file
        )
    }


# ============================================================
# PRICE NORMALIZATION
# ============================================================

def normalize_price(price_text):

    if not price_text:

        raise ValueError(
            "Missing price"
        )

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        price_text
    )

    if not match:

        raise ValueError(
            f"Could not normalize price: "
            f"{price_text}"
        )

    return float(
        match.group(1)
    )


# ============================================================
# VALIDATE RECORD
# ============================================================

def validate_record(raw_record):

    price_gbp = normalize_price(
        raw_record["price_text"]
    )

    normalized_record = {
        "title": raw_record["title"],
        "product_url": raw_record[
            "product_url"
        ],
        "price_text": raw_record[
            "price_text"
        ],
        "price_gbp": price_gbp,
        "availability_text": raw_record[
            "availability_text"
        ],
        "rating_text": raw_record[
            "rating_text"
        ],
        "description": raw_record[
            "description"
        ],
        "source_page": raw_record[
            "source_page"
        ],
        "fetched_at": raw_record[
            "fetched_at"
        ]
    }

    return BookRecord(
        **normalized_record
    )


# ============================================================
# WRITE JSON
# ============================================================

def write_json(file_path, data):

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# STAGE 4
# VALIDATE + STORE
# ============================================================

def validate_and_store(raw_records):

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    valid_records = []

    errors = []

    seen_urls = set()

    for raw_record in raw_records:

        try:

            validated = validate_record(
                raw_record
            )

            record = (
                validated.model_dump()
            )

            product_url = record[
                "product_url"
            ]

            # Duplicate check
            if product_url in seen_urls:

                errors.append({
                    "record": raw_record,
                    "reason": "Duplicate product_url"
                })

                stats["invalid_records"] += 1

                continue

            seen_urls.add(
                product_url
            )

            valid_records.append(
                record
            )

        except (
            ValidationError,
            ValueError,
            TypeError
        ) as error:

            errors.append({
                "record": raw_record,
                "reason": str(error)
            })

            stats["invalid_records"] += 1

    stats["valid_records"] = len(
        valid_records
    )

    write_json(
        BOOKS_FILE,
        valid_records
    )

    write_json(
        ERRORS_FILE,
        errors
    )

    return valid_records, errors


# ============================================================
# STAGE 5
# PROCESS ONE BOOK SAFELY
# ============================================================

def process_book_safely(book):

    try:

        print(
            f"\nPROCESSING | "
            f"{book['product_url']}"
        )

        raw_record = extract_book(
            book
        )

        return raw_record, None

    except Exception as error:

        print(
            f"FAILED | "
            f"{book['product_url']} | "
            f"{error}"
        )

        return None, {
            "url": book["product_url"],
            "reason": str(error)
        }


# ============================================================
# RUN REPORT
# ============================================================

def write_run_report(
    start_time,
    end_time,
    failed_pages
):

    duration = (
        end_time - start_time
    ).total_seconds()

    report = {
        "start_time": start_time.isoformat().replace(
            "+00:00",
            "Z"
        ),
        "end_time": end_time.isoformat().replace(
            "+00:00",
            "Z"
        ),
        "duration_seconds": duration,
        "pages_fetched": stats[
            "pages_fetched"
        ],
        "cache_hits": stats[
            "cache_hits"
        ],
        "valid_records": stats[
            "valid_records"
        ],
        "invalid_records": stats[
            "invalid_records"
        ],
        "failed_pages": len(
            failed_pages
        )
    }

    write_json(
        REPORT_FILE,
        report
    )

    return report


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    start_time = datetime.now(
        timezone.utc
    )

    # --------------------------------------------------------
    # STAGE 2
    # --------------------------------------------------------

    books = discover_books()

    # --------------------------------------------------------
    # STAGE 3
    # --------------------------------------------------------

    raw_records = []

    failed_pages = []

    for index, book in enumerate(
        books,
        start=1
    ):

        print(
            f"\nBOOK {index}/{len(books)}"
        )

        raw_record, error = (
            process_book_safely(book)
        )

        if raw_record:

            raw_records.append(
                raw_record
            )

        if error:

            failed_pages.append(
                error
            )

    # --------------------------------------------------------
    # SHOW STAGE 3 SAMPLE
    # --------------------------------------------------------

    if raw_records:

        print(
            "\n--- SAMPLE RAW RECORD ---"
        )

        print(
            json.dumps(
                raw_records[0],
                indent=2,
                ensure_ascii=False
            )
        )

    print()

    print(
        f"detail_pages="
        f"{len(raw_records)}"
    )

    # --------------------------------------------------------
    # STAGE 4
    # --------------------------------------------------------

    valid_records, errors = (
        validate_and_store(
            raw_records
        )
    )

    # --------------------------------------------------------
    # DELIBERATELY BROKEN URL
    # --------------------------------------------------------
    # This is ONLY for testing our failure handling.
    # It must not be stored in books.json.

    fake_book = {
        "product_url":
            "https://books.toscrape.com/"
            "catalogue/this-page-does-not-exist-999999/",
        "source_page":
            BASE_URL
    }

    print(
        "\n--- FAILURE TEST ---"
    )

    _, fake_error = (
        process_book_safely(
            fake_book
        )
    )

    if fake_error:

        failed_pages.append(
            fake_error
        )

    # --------------------------------------------------------
    # UPDATE FAILED PAGE COUNT
    # --------------------------------------------------------

    stats["failed_pages"] = len(
        failed_pages
    )

    # --------------------------------------------------------
    # WRITE RUN REPORT
    # --------------------------------------------------------

    end_time = datetime.now(
        timezone.utc
    )

    report = write_run_report(
        start_time,
        end_time,
        failed_pages
    )

    # --------------------------------------------------------
    # FINAL CHECKPOINT
    # --------------------------------------------------------

    print(
        "\n--- STAGE 5 CHECKPOINT ---"
    )

    print(
        f"valid_records="
        f"{stats['valid_records']}"
    )

    print(
        f"invalid_records="
        f"{stats['invalid_records']}"
    )

    print(
        f"failed_pages="
        f"{report['failed_pages']}"
    )

    print(
        f"pages_fetched="
        f"{report['pages_fetched']}"
    )

    print(
        f"cache_hits="
        f"{report['cache_hits']}"
    )

    print(
        f"duration_seconds="
        f"{report['duration_seconds']}"
    )

    print(
        f"run_report="
        f"{REPORT_FILE}"
    )