import hashlib
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# =========================
# Configuration
# =========================

BASE_URL = "https://books.toscrape.com/"

CACHE_DIR = "cache"
BOOK_CACHE_DIR = os.path.join(CACHE_DIR, "books")

HEADERS = {
    "User-Agent": "FlyRankInternship-A9/1.0"
}

TIMEOUT = 10
DELAY = 0.5

# Tracks the time of the last real network request.
last_request_time = 0.0


# =========================
# Polite request handling
# =========================

def wait_before_request():
    """Wait until at least 0.5 seconds have passed since the last request."""
    global last_request_time

    if last_request_time:
        elapsed = time.monotonic() - last_request_time

        if elapsed < DELAY:
            time.sleep(DELAY - elapsed)


def fetch_page(url, cache_file):
    """
    Return page HTML from cache when available.
    Otherwise fetch it from the website and save it to cache.
    """

    global last_request_time

    # Make sure cache directory exists.
    cache_directory = os.path.dirname(cache_file)

    if cache_directory:
        os.makedirs(cache_directory, exist_ok=True)

    # =========================
    # CACHE
    # =========================

    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as file:
            html = file.read()

        print(f"CACHE HIT | size={len(html)} bytes")

        return html

    # =========================
    # REAL REQUEST
    # =========================

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

    last_request_time = time.monotonic()

    # =========================
    # STATUS CHECK
    # =========================

    if response.status_code != 200:
        raise RuntimeError(
            f"Fetch failed: HTTP {response.status_code} for {url}"
        )

    # =========================
    # SAVE CACHE
    # =========================

    html = response.text

    with open(cache_file, "w", encoding="utf-8") as file:
        file.write(html)

    print(f"FETCHED | size={len(html)} bytes")

    return html


# =========================
# Catalogue cache
# =========================

def catalogue_cache_path(page_number):
    """Return cache path for a catalogue page."""

    return os.path.join(
        CACHE_DIR,
        f"catalogue-page-{page_number}.html"
    )


# =========================
# Book cache
# =========================

def book_cache_path(book_url):
    """
    Create a safe cache filename from a book URL.
    """

    url_hash = hashlib.sha256(
        book_url.encode("utf-8")
    ).hexdigest()[:16]

    return os.path.join(
        BOOK_CACHE_DIR,
        f"{url_hash}.html"
    )


# =========================
# Stage 2
# Discover books
# =========================

def discover_books():
    """
    Discover books from the first 3 catalogue pages.

    Returns:
        A list containing:
        - product_url
        - source_page
    """

    current_url = BASE_URL

    discovered_books = []

    catalogue_pages = 0

    while current_url and catalogue_pages < 3:

        catalogue_pages += 1

        print(f"\nCATALOGUE PAGE {catalogue_pages}")

        cache_file = catalogue_cache_path(catalogue_pages)

        html = fetch_page(
            current_url,
            cache_file
        )

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # =========================
        # Find book links
        # =========================

        for article in soup.select("article.product_pod"):

            link = article.select_one("h3 a")

            if link and link.get("href"):

                product_url = urljoin(
                    current_url,
                    link["href"]
                )

                discovered_books.append({
                    "product_url": product_url,
                    "source_page": current_url
                })

        # =========================
        # Find next page
        # =========================

        next_link = soup.select_one("li.next a")

        if next_link and next_link.get("href"):

            current_url = urljoin(
                current_url,
                next_link["href"]
            )

        else:

            current_url = None

    # =========================
    # Remove duplicates
    # =========================

    unique_books = []

    seen_urls = set()

    for book in discovered_books:

        product_url = book["product_url"]

        if product_url not in seen_urls:

            seen_urls.add(product_url)

            unique_books.append(book)

    # =========================
    # Checkpoint
    # =========================

    print()
    print(f"catalogue_pages={catalogue_pages}")
    print(f"discovered={len(discovered_books)}")
    print(f"unique_urls={len(unique_books)}")

    return unique_books


# =========================
# fetched_at
# =========================

def get_cached_fetched_at(cache_file):
    """
    Use the cache file modification time as fetched_at.
    """

    timestamp = os.path.getmtime(cache_file)

    return (
        datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        )
        .isoformat()
        .replace("+00:00", "Z")
    )


# =========================
# Stage 3
# Extract one book
# =========================

def extract_book(book):
    """
    Fetch and extract one book's raw record.
    """

    product_url = book["product_url"]

    source_page = book["source_page"]

    cache_file = book_cache_path(product_url)

    # Fetch or load cached book page.
    html = fetch_page(
        product_url,
        cache_file
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # =========================
    # Product area
    # =========================

    product_main = soup.select_one(
        "div.product_main"
    )

    if product_main is None:
        raise ValueError(
            f"Product area not found: {product_url}"
        )

    # =========================
    # Title
    # =========================

    title_element = product_main.select_one("h1")

    if title_element:
        title = title_element.get_text(
            strip=True
        )
    else:
        title = None

    # =========================
    # Price
    # =========================

    price_element = product_main.select_one(
        "p.price_color"
    )

    if price_element:
        price_text = price_element.get_text(
            strip=True
        )
    else:
        price_text = None

    # =========================
    # Availability
    # =========================

    availability_element = product_main.select_one(
        "p.instock.availability"
    )

    if availability_element:
        availability_text = availability_element.get_text(
            " ",
            strip=True
        )
    else:
        availability_text = None

    # =========================
    # Rating
    # =========================

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

    # =========================
    # Description
    # =========================

    description_heading = soup.select_one(
        "#product_description"
    )

    description = None

    if description_heading:

        description_element = (
            description_heading.find_next_sibling("p")
        )

        if description_element:

            description = description_element.get_text(
                " ",
                strip=True
            )

    # =========================
    # Raw record
    # =========================

    record = {
        "title": title,
        "product_url": product_url,
        "price_text": price_text,
        "availability_text": availability_text,
        "rating_text": rating_text,
        "description": description,
        "source_page": source_page,
        "fetched_at": get_cached_fetched_at(cache_file)
    }

    return record


# =========================
# Extract all books
# =========================

def extract_all_books(books):
    """
    Extract raw records for all discovered books.
    """

    records = []

    for index, book in enumerate(
        books,
        start=1
    ):

        print(
            f"\nBOOK {index}/{len(books)}"
        )

        record = extract_book(book)

        records.append(record)

    return records


# =========================
# Main
# =========================

if __name__ == "__main__":

    # Stage 2:
    # Discover 60 unique books.
    books = discover_books()

    # Stage 3:
    # Extract all book details.
    records = extract_all_books(books)

    # =========================
    # Print one complete record
    # =========================

    print("\n--- SAMPLE RAW RECORD ---")

    print(
        json.dumps(
            records[0],
            indent=2,
            ensure_ascii=False
        )
    )

    # =========================
    # Stage 3 checkpoint
    # =========================

    print("\n--- CHECKPOINT ---")

    print(
        f"detail_pages={len(records)}"
    )