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
BOOKS_FILE = os.path.join(OUTPUT_DIR, "books.json")
ERRORS_FILE = os.path.join(OUTPUT_DIR, "errors.json")

HEADERS = {
    "User-Agent": "FlyRankInternship-A9/1.0"
}

TIMEOUT = 10
DELAY = 0.5

# Time of the last real HTTP request.
last_request_time = 0.0


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
# POLITE REQUEST HANDLING
# ============================================================

def wait_before_request():
    """
    Make sure at least 0.5 seconds passes between
    real network requests.
    """

    global last_request_time

    if last_request_time:
        elapsed = time.monotonic() - last_request_time

        if elapsed < DELAY:
            time.sleep(DELAY - elapsed)


def fetch_page(url, cache_file):
    """
    Load HTML from cache if available.
    Otherwise fetch it from the website and cache it.

    Timeout:
        Retry once.

    Status:
        Only HTTP 200 is accepted.
    """

    global last_request_time

    # Make sure the cache directory exists.
    cache_directory = os.path.dirname(cache_file)

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

        print(
            f"CACHE HIT | size={len(html)} bytes"
        )

        return html

    # --------------------------------------------------------
    # REAL REQUEST
    # --------------------------------------------------------

    wait_before_request()

    print(f"FETCH | {url}")

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

    except requests.exceptions.Timeout:

        print("TIMEOUT | retrying once...")

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

    # Record time of real request.
    last_request_time = time.monotonic()

    # --------------------------------------------------------
    # STATUS CHECK
    # --------------------------------------------------------

    if response.status_code != 200:

        raise RuntimeError(
            f"Fetch failed: HTTP {response.status_code} "
            f"for {url}"
        )

    # --------------------------------------------------------
    # SAVE TO CACHE
    # --------------------------------------------------------

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
    """
    Return cache path for a catalogue page.
    """

    return os.path.join(
        CACHE_DIR,
        f"catalogue-page-{page_number}.html"
    )


# ============================================================
# BOOK CACHE
# ============================================================

def book_cache_path(book_url):
    """
    Create a safe filename from the book URL.

    A hash is used so URLs cannot create strange
    or invalid filenames.
    """

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
    """
    Discover books from the first 3 catalogue pages.

    Returns:
        List of dictionaries containing:
        - product_url
        - source_page
    """

    current_url = BASE_URL

    discovered_books = []

    catalogue_pages = 0

    # --------------------------------------------------------
    # VISIT MAXIMUM 3 CATALOGUE PAGES
    # --------------------------------------------------------

    while current_url and catalogue_pages < 3:

        catalogue_pages += 1

        print(
            f"\nCATALOGUE PAGE {catalogue_pages}"
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
        # FOLLOW THE WEBSITE'S NEXT LINK
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

        product_url = book["product_url"]

        if product_url not in seen_urls:

            seen_urls.add(product_url)

            unique_books.append(book)

    # --------------------------------------------------------
    # CHECKPOINT
    # --------------------------------------------------------

    print()
    print(
        f"catalogue_pages={catalogue_pages}"
    )

    print(
        f"discovered={len(discovered_books)}"
    )

    print(
        f"unique_urls={len(unique_books)}"
    )

    return unique_books


# ============================================================
# FETCHED AT
# ============================================================

def get_cached_fetched_at(cache_file):
    """
    Get the cache file modification time and use it
    as the fetched_at timestamp.
    """

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
# EXTRACT ONE BOOK
# ============================================================

def extract_book(book):
    """
    Extract the 8 required raw fields from one book page.
    """

    product_url = book["product_url"]

    source_page = book["source_page"]

    cache_file = book_cache_path(
        product_url
    )

    # --------------------------------------------------------
    # FETCH BOOK PAGE
    # --------------------------------------------------------

    html = fetch_page(
        product_url,
        cache_file
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # --------------------------------------------------------
    # PRODUCT AREA
    # --------------------------------------------------------

    product_main = soup.select_one(
        "div.product_main"
    )

    if product_main is None:

        raise ValueError(
            f"Product area not found: {product_url}"
        )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title_element = product_main.select_one(
        "h1"
    )

    if title_element:

        title = title_element.get_text(
            strip=True
        )

    else:

        title = None

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    price_element = product_main.select_one(
        "p.price_color"
    )

    if price_element:

        price_text = price_element.get_text(
            strip=True
        )

    else:

        price_text = None

    # --------------------------------------------------------
    # AVAILABILITY
    # --------------------------------------------------------

    availability_element = product_main.select_one(
        "p.instock.availability"
    )

    if availability_element:

        availability_text = (
            availability_element.get_text(
                " ",
                strip=True
            )
        )

    else:

        availability_text = None

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
    # RETURN RAW RECORD
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
    """
    Convert a price such as:

        £51.77

    into:

        51.77
    """

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
# NORMALIZE + VALIDATE
# ============================================================

def validate_record(raw_record):
    """
    Add price_gbp and validate the record
    against the Pydantic schema.
    """

    price_gbp = normalize_price(
        raw_record["price_text"]
    )

    normalized_record = {
        "title": raw_record["title"],
        "product_url": raw_record["product_url"],
        "price_text": raw_record["price_text"],
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

    validated = BookRecord(
        **normalized_record
    )

    return validated


# ============================================================
# STAGE 4
# VALIDATE + STORE
# ============================================================

def validate_and_store(raw_records):
    """
    Validate every raw record.

    Valid records:
        output/books.json

    Invalid records:
        output/errors.json
    """

    # Make output directory.
    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    valid_records = []

    errors = []

    seen_urls = set()

    # --------------------------------------------------------
    # VALIDATE EVERY RECORD
    # --------------------------------------------------------

    for raw_record in raw_records:

        try:

            validated = validate_record(
                raw_record
            )

            # Pydantic v2
            record = validated.model_dump()

            product_url = record[
                "product_url"
            ]

            # ------------------------------------------------
            # DUPLICATE CHECK
            # ------------------------------------------------

            if product_url in seen_urls:

                errors.append({
                    "record": raw_record,
                    "reason": "Duplicate product_url"
                })

                continue

            seen_urls.add(product_url)

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

    # --------------------------------------------------------
    # WRITE books.json
    # --------------------------------------------------------

    with open(
        BOOKS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            valid_records,
            file,
            indent=2,
            ensure_ascii=False
        )

    # --------------------------------------------------------
    # WRITE errors.json
    # --------------------------------------------------------

    with open(
        ERRORS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            errors,
            file,
            indent=2,
            ensure_ascii=False
        )

    # --------------------------------------------------------
    # STAGE 4 CHECKPOINT
    # --------------------------------------------------------

    print("\n--- STAGE 4 CHECKPOINT ---")

    print(
        f"valid_records={len(valid_records)}"
    )

    print(
        f"invalid_records={len(errors)}"
    )

    print(
        f"books.json={BOOKS_FILE}"
    )

    print(
        f"errors.json={ERRORS_FILE}"
    )

    return valid_records, errors


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    # --------------------------------------------------------
    # STAGE 2
    # --------------------------------------------------------

    books = discover_books()

    # --------------------------------------------------------
    # STAGE 3
    # --------------------------------------------------------

    raw_records = []

    for index, book in enumerate(
        books,
        start=1
    ):

        print(
            f"\nBOOK {index}/{len(books)}"
        )

        raw_record = extract_book(
            book
        )

        raw_records.append(
            raw_record
        )

    # --------------------------------------------------------
    # STAGE 3 CHECKPOINT
    # --------------------------------------------------------

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
        f"detail_pages={len(raw_records)}"
    )

    # --------------------------------------------------------
    # STAGE 4
    # --------------------------------------------------------

    validate_and_store(
        raw_records
    )