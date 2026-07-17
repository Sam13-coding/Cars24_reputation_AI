"""Collector Agent: finds and extracts Reddit discussions mentioning Cars24.

Workflow: search query -> find Reddit URLs via a pluggable search provider ->
download each Reddit page -> extract title/subreddit/url/main post text ->
clean the text. Responsible ONLY for this; does not score sentiment, predict
reputation, or generate reports, and does not talk to the Reddit API — no
Reddit credentials of any kind are used.
"""

from __future__ import annotations

import os
import re
import time
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from serpapi import GoogleSearch

load_dotenv(Path(__file__).resolve().parent / "output" / ".env")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 1.0
DEFAULT_QUERY = "cars24"
DEFAULT_MAX_RESULTS = 25


class SearchProvider(ABC):
    """Finds Reddit discussion URLs matching a search query.

    Abstracted so the search backend can be swapped (a different search
    engine or a paid search API) without touching the rest of the collector.
    """

    @abstractmethod
    def search(self, query: str, max_results: int) -> list[str]:
        """Return up to `max_results` Reddit discussion URLs matching `query`."""


class SerpApiSearchProvider(SearchProvider):
    """Finds Reddit URLs via SerpApi's Google Search engine, restricted to reddit.com."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("SERPAPI_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "SERPAPI_API_KEY not set. Add it to output/.env or pass api_key explicitly."
            )
        print(f"[collector] SERPAPI_API_KEY loaded (ends in ...{self._api_key[-4:]}).")

    def search(self, query: str, max_results: int) -> list[str]:
        print(f"[collector] Searching SerpApi (Google) for: {query}")
        page_size = 10  # Google returns organic results in pages of this size
        urls: list[str] = []
        seen: set[str] = set()
        start = 0
        last_response: dict[str, Any] = {}

        while len(urls) < max_results:
            params = {
                "engine": "google",
                "q": f"site:reddit.com {query}",
                "num": page_size,
                "start": start,
                "api_key": self._api_key,
            }
            results = GoogleSearch(params).get_dict()
            last_response = results
            if "error" in results:
                print(f"[collector] SerpApi returned an error at start={start}: {results['error']}")
                print(f"[collector] STEP 2 ZERO REASON: SerpApi API error. Full API response: {results!r}")
                raise RuntimeError(f"SerpApi search failed: {results['error']}")

            organic_results = results.get("organic_results", [])
            print(f"[collector] SerpApi page start={start}: {len(organic_results)} organic_result(s).")
            if not organic_results:
                break

            for result in organic_results:
                href = str(result.get("link", ""))
                if "reddit.com" not in urlparse(href).netloc:
                    continue
                if href in seen:
                    continue
                seen.add(href)
                urls.append(href)
                if len(urls) >= max_results:
                    break

            start += page_size

        if not urls:
            print(
                f"[collector] STEP 2 ZERO REASON: SerpApi returned no organic_results for query {query!r}. "
                f"Full API response: {last_response!r}"
            )
        print(f"[collector] Found {len(urls)} Reddit URL(s) via SerpApi")
        return urls


def _to_old_reddit(url: str) -> str:
    """Rewrite a reddit.com URL to old.reddit.com, which serves server-rendered HTML."""
    return urlparse(url)._replace(netloc="old.reddit.com").geturl()


def clean_text(text: str) -> str:
    """Normalize whitespace in text extracted from a Reddit page."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _parse_int(text: str | None) -> int:
    """Extract the first integer found in `text`, or 0 if none."""
    if not text:
        return 0
    match = re.search(r"-?\d+", text.replace(",", ""))
    return int(match.group()) if match else 0


def _extract_post(html: str, url: str) -> dict[str, Any] | None:
    """Parse a Reddit discussion page into a structured record."""
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("a.title") or soup.select_one("p.title a")
    if title_el is None:
        return None

    body_el = soup.select_one("div.usertext-body div.md")
    subreddit_el = soup.select_one("a.subreddit")
    score_el = soup.select_one("div.score.unvoted")
    comments_el = soup.select_one("a.comments")

    return {
        "title": clean_text(title_el.get_text()),
        "subreddit": clean_text(subreddit_el.get_text()).lstrip("r/") if subreddit_el else "",
        "url": url,
        "text": clean_text(body_el.get_text()) if body_el else "",
        "score": _parse_int(str(score_el.get("title")) if score_el else None),
        "num_comments": _parse_int(comments_el.get_text() if comments_el else None),
    }


def _fetch_post(url: str) -> dict[str, Any] | None:
    """Download a single Reddit discussion page and extract its content."""
    headers = {"User-Agent": USER_AGENT}
    fetch_url = _to_old_reddit(url)
    response = requests.get(fetch_url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    print(f"[collector] GET {fetch_url} -> HTTP {response.status_code}, {len(response.content)} byte(s).")
    response.raise_for_status()
    print(f"[collector] Downloaded page: {url}")

    post = _extract_post(response.text, url)
    if post is None:
        # Diagnostic only, not a behavior change: HTTP 200 with a body that doesn't match the
        # expected old.reddit.com markup (e.g. a bot-check/interstitial/CAPTCHA/age-gate page)
        # looks identical to a genuine markup change from here, so log everything needed to tell
        # the two apart when this is investigated from server-side logs.
        content_type = response.headers.get("Content-Type", "<no Content-Type header>")
        print(
            "[collector] STEP 4 page could not be parsed:\n"
            f"[collector]   URL: {url}\n"
            f"[collector]   HTTP status: {response.status_code}\n"
            f"[collector]   Content-Type: {content_type}\n"
            f"[collector]   First 500 chars of body: {response.text[:500]!r}"
        )
    else:
        print(f"[collector] Extracted post: {post['title']}")
    return post


def collect_posts(
    query: str = DEFAULT_QUERY,
    max_results: int = DEFAULT_MAX_RESULTS,
    search_provider: SearchProvider | None = None,
) -> list[dict[str, Any]]:
    """Search for Reddit discussions matching `query` and return cleaned records.

    Each record has `title`, `subreddit`, `url`, and `text`, plus `score`/
    `num_comments` metadata that the risk agent uses (e.g. to detect large
    discussions).
    """
    print("STEP 1: Collector started")
    search_provider = search_provider or SerpApiSearchProvider()

    try:
        urls = search_provider.search(query, max_results)
    except Exception as exc:
        print(f"STEP 2: SerpAPI returned 0 URLs — search_provider.search() raised an exception: {exc!r}")
        traceback.print_exc()
        raise

    print(f"STEP 2: SerpAPI returned {len(urls)} URLs")
    if not urls:
        print("STEP 2 ZERO REASON: see the [collector] STEP 2 ZERO REASON line above for the full API response.")

    posts: list[dict[str, Any]] = []
    downloaded_count = 0
    extracted_count = 0
    failed_downloads = 0
    for url in urls:
        try:
            post = _fetch_post(url)
            downloaded_count += 1
        except requests.RequestException as exc:
            print(f"[collector] Failed to download {url}: {exc!r}")
            failed_downloads += 1
            continue
        if post is not None:
            posts.append(post)
            extracted_count += 1
        time.sleep(REQUEST_DELAY_SECONDS)  # be polite to the pages we scrape

    print(f"STEP 3: Downloaded {downloaded_count} Reddit pages")
    if downloaded_count == 0 and urls:
        print(
            f"STEP 3 ZERO REASON: all {len(urls)} URL(s) failed to download "
            f"({failed_downloads} raised a RequestException — see per-URL logs above for each one)."
        )

    print(f"STEP 4: Successfully extracted {extracted_count} posts")
    if extracted_count == 0 and downloaded_count > 0:
        print(
            f"STEP 4 ZERO REASON: {downloaded_count} page(s) downloaded successfully but none matched "
            "the expected old.reddit.com markup — see the STEP 4 page-could-not-be-parsed logs above "
            "for each page's URL/status/Content-Type/body snippet."
        )

    return posts