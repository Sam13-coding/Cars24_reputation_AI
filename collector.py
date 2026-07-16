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

    def search(self, query: str, max_results: int) -> list[str]:
        print(f"[collector] Searching SerpApi (Google) for: {query}")
        page_size = 10  # Google returns organic results in pages of this size
        urls: list[str] = []
        seen: set[str] = set()
        start = 0

        while len(urls) < max_results:
            params = {
                "engine": "google",
                "q": f"site:reddit.com {query}",
                "num": page_size,
                "start": start,
                "api_key": self._api_key,
            }
            results = GoogleSearch(params).get_dict()
            if "error" in results:
                raise RuntimeError(f"SerpApi search failed: {results['error']}")

            organic_results = results.get("organic_results", [])
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
    response = requests.get(_to_old_reddit(url), headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    print(f"[collector] Downloaded page: {url}")

    post = _extract_post(response.text, url)
    if post is None:
        print(f"[collector] No post extracted from: {url}")
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
    search_provider = search_provider or SerpApiSearchProvider()
    urls = search_provider.search(query, max_results)

    posts: list[dict[str, Any]] = []
    for url in urls:
        try:
            post = _fetch_post(url)
        except requests.RequestException as exc:
            print(f"[collector] Failed to download {url}: {exc}")
            continue
        if post is not None:
            posts.append(post)
        time.sleep(REQUEST_DELAY_SECONDS)  # be polite to the pages we scrape

    return posts