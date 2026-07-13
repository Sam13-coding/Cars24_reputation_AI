"""Collector Agent: finds and extracts Reddit discussions mentioning Cars24.

Workflow: for each search query -> find Reddit URLs via a pluggable search
provider -> download each Reddit page -> extract title/subreddit/url/main
post text -> clean the text. Results are merged and deduplicated by URL
across all queries. Responsible ONLY for this; does not score sentiment,
predict reputation, or generate reports, and does not talk to the Reddit
API — no Reddit credentials of any kind are used.
"""

from __future__ import annotations

import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from streamlit import write

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT_SECONDS = 10
REQUEST_DELAY_SECONDS = 1.0
QUERY_DELAY_MIN_SECONDS = 1.0
QUERY_DELAY_MAX_SECONDS = 2.0
brand = "Cars24"

templates = [
    "{} review",
    "{} experience",
    "{} buying experience",
    "{} selling experience",
    "{} used car",
    "{} purchase",
    "{} reliable",
    "{} customer support",
    "{} refund",
    "{} RC transfer",
    "{} delivery",
    "{} warranty",
    "{} inspection",
    "{} worth it",
]

DEFAULT_QUERIES = [template.format(brand) for template in templates]

DEFAULT_MAX_RESULTS = 50


class SearchProvider(ABC):
    """Finds Reddit discussion URLs matching a search query.

    Abstracted so the search backend can be swapped (a different search
    engine or a paid search API) without touching the rest of the collector.
    """

    @abstractmethod
    def search(self, query: str, max_results: int) -> list[str]:
        """Return up to `max_results` Reddit discussion URLs matching `query`."""


class DuckDuckGoSearchProvider(SearchProvider):
    """Finds Reddit URLs via DuckDuckGo's HTML search, restricted to reddit.com."""

    SEARCH_URL = "https://html.duckduckgo.com/html/"

    def search(self, query: str, max_results: int) -> list[str]:
        print(f"[collector] Searching DuckDuckGo for: {query}")
        params = {"q": f"site:reddit.com {query}"}
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(
            self.SEARCH_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for link in soup.select("a.result__a"):
            href = _unwrap_ddg_redirect(str(link.get("href") or ""))
            if "reddit.com" not in urlparse(href).netloc:
                continue
            if href in seen:
                continue
            seen.add(href)
            urls.append(href)
            if len(urls) >= max_results:
                break

        print(f"[collector] Found {len(urls)} Reddit URL(s)")
        return urls


class BingSearchProvider(SearchProvider):
    """Finds Reddit URLs via Bing's HTML search, restricted to reddit.com."""

    SEARCH_URL = "https://www.bing.com/search"

    def search(self, query: str, max_results: int) -> list[str]:
        print(f"[collector] Searching Bing for: {query}")
        params = {"q": f"site:reddit.com {query}", "count": max_results}
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(
            self.SEARCH_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for link in soup.select("li.b_algo h2 a"):
            href = str(link.get("href", ""))
            if "reddit.com" not in urlparse(href).netloc:
                continue
            if href in seen:
                continue
            seen.add(href)
            urls.append(href)
            if len(urls) >= max_results:
                break

        print(f"[collector] Found {len(urls)} Reddit URL(s) via Bing")
        return urls


class FallbackSearchProvider(SearchProvider):
    """Tries each provider in order, falling back to the next if the previous found nothing.

    Merges and deduplicates URLs across whichever provider(s) actually ran.
    """

    def __init__(self, providers: list[SearchProvider]) -> None:
        self._providers = providers

    def search(self, query: str, max_results: int) -> list[str]:
        combined: list[str] = []
        seen: set[str] = set()

        for i, provider in enumerate(self._providers):
            for url in provider.search(query, max_results):
                if url not in seen:
                    seen.add(url)
                    combined.append(url)
            if combined:
                break
            if i < len(self._providers) - 1:
                print(f"[collector] {type(provider).__name__} returned 0 URLs, trying next provider")

        return combined[:max_results]


def _unwrap_ddg_redirect(href: str) -> str:
    """Unwrap a DuckDuckGo redirect link (//duckduckgo.com/l/?uddg=...) to the real target URL."""
    parsed = urlparse(href)
    if "duckduckgo.com" not in parsed.netloc:
        return href
    target = parse_qs(parsed.query).get("uddg")
    return target[0] if target else href


def _to_old_reddit(url: str) -> str:
    """Rewrite a reddit.com URL to old.reddit.com, which serves server-rendered HTML."""
    return urlparse(url)._replace(netloc="old.reddit.com").geturl()


def clean_text(text: str) -> str:
    """Normalize whitespace in text extracted from a Reddit page."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _parse_int(text: Any) -> int:
    """Extract the first integer found in `text`, or 0 if none."""
    if not text:
        return 0
    if isinstance(text, list):
        text = text[0] if text else ""
    match = re.search(r"-?\d+", str(text).replace(",", ""))
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
        "score": _parse_int(score_el.get("title") if score_el else None),
        "num_comments": _parse_int(comments_el.get_text() if comments_el else None),
    }


def _fetch_post(url: str) -> dict[str, Any] | None:
    """Download a single Reddit discussion page and extract its content."""
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(_to_old_reddit(url), headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    print(f"[collector] Downloaded page: {url}")

    post = _extract_post(response.text, url)
    if post is None:
        print(f"[collector] No post extracted from: {url}")
    else:
        print(f"[collector] Extracted post: {post['title']}")
    return post


def _deduplicate_urls(urls: list[str]) -> list[str]:
    """Remove duplicate Reddit URLs (exact match), keeping first-occurrence order."""
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def collect_posts(
    queries: list[str] | None = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    search_provider: SearchProvider | None = None,
) -> list[dict[str, Any]]:
    """Search for Reddit discussions matching each of `queries` and return cleaned records.

    Runs every query in turn and collects all resulting URLs first. Duplicate
    URLs (the same Reddit discussion found under more than one query) are
    removed BEFORE any page is downloaded. Continues past a query that
    returns zero results. Each record has `title`, `subreddit`, `url`, and
    `text`, plus `score`/`num_comments` metadata that the risk agent uses
    (e.g. to detect large discussions).
    """
    queries = queries or DEFAULT_QUERIES
    search_provider = search_provider or FallbackSearchProvider(
        [DuckDuckGoSearchProvider(), BingSearchProvider()]
    )

    # --- Phase 1: search every query, accumulating raw URLs (not yet deduplicated) ---
    all_urls: list[str] = []
    for i, query in enumerate(queries):
        try:
            urls = search_provider.search(query, max_results)
        except Exception as exc:
            print(f"[collector] Query {query!r} failed: {exc}")
        else:
            if not urls:
                print(f"[collector] Query {query!r} returned 0 URLs, continuing to next query")
            else:
                all_urls.extend(urls)
        finally:
            if i < len(queries) - 1:
                time.sleep(random.uniform(QUERY_DELAY_MIN_SECONDS, QUERY_DELAY_MAX_SECONDS))

    # --- Phase 2: deduplicate BEFORE downloading anything, using the Reddit URL as the key ---
    unique_urls = _deduplicate_urls(all_urls)
    duplicates_removed = len(all_urls) - len(unique_urls)
    print(f"[collector] Removed {duplicates_removed} duplicate URLs.")

    # --- Phase 3: download and extract each unique post ---
    posts: list[dict[str, Any]] = []
    for url in unique_urls:
        try:
            post = _fetch_post(url)
        except requests.RequestException as exc:
            print(f"[collector] Failed to download {url}: {exc}")
            continue
        if post is not None:
            posts.append(post)
        time.sleep(REQUEST_DELAY_SECONDS)  # be polite to the pages we scrape

    print(f"[collector] Collected {len(posts)} unique post(s) across {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}")
    return posts
