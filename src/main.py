import os
import requests

URL = "https://books.toscrape.com/"
CACHE_FILE = "cache/catalogue-page-1.html"

HEADERS = {
    "User-Agent": "FlyRankInternship-A9/1.0"
}

TIMEOUT = 10


def fetch_page():
    os.makedirs("cache", exist_ok=True)

    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            html = file.read()

        print(f"CACHE HIT | size={len(html)} bytes")
        return html

    print("FETCH")

    response = requests.get(
        URL,
        headers=HEADERS,
        timeout=TIMEOUT
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Fetch failed: HTTP {response.status_code}"
        )

    html = response.text

    with open(CACHE_FILE, "w", encoding="utf-8") as file:
        file.write(html)

    print(f"FETCHED | size={len(html)} bytes")

    return html


if __name__ == "__main__":
    fetch_page()