"""Claude Agent: filters collected posts to those genuinely about Cars24.

Sits between collector.py and risk_agent.py. Passes through only posts whose
title/text actually mention the Cars24 brand, dropping the rest; surviving
posts pass through UNMODIFIED — it does not score risk or add/remove fields.

TEMPORARY: this is a local keyword heuristic, not an LLM judgment. It cannot
tell a genuine coincidental match (a different "Cars24"-named entity, a typo,
someone's username) from a real one — it only checks for the brand name.
This is a placeholder for a future LLM-based relevance check.
"""

from __future__ import annotations

import re
from typing import Any

BRAND_PATTERN = re.compile(r"cars\s*-?\s*24", re.IGNORECASE)


def _is_relevant(post: dict[str, Any]) -> bool:
    """Whether a post's title/text mentions the Cars24 brand name."""
    combined = f"{post.get('title', '')} {post.get('text', '')}"
    return bool(BRAND_PATTERN.search(combined))


def filter_relevant_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop collected posts that don't mention Cars24; pass the rest through unmodified."""
    return [post for post in posts if _is_relevant(post)]
