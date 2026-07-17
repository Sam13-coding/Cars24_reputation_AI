"""Risk Agent: scores collected Reddit posts for Cars24 reputation risk.

Input: collected Reddit posts from collector.py.
Output: each post augmented with `risk_score` (0-100), `priority`
(Low/Medium/High/Critical), and `reasons` (matched risk factors).

Scoring is rule-based (keyword + engagement heuristics) — no LLM call.
"""

from __future__ import annotations

import re
from typing import Any

# label -> (points awarded, keywords that trigger it)
RISK_KEYWORDS: dict[str, tuple[int, list[str]]] = {
    "fraud allegations": (30, ["fraud", "fraudulent", "cheated", "cheat"]),
    "scam allegations": (30, ["scam", "scammed", "scammer", "ripoff", "rip off"]),
    "refund issues": (20, ["refund", "not refunded", "no refund", "money back"]),
    "consumer court mentions": (25, ["consumer court", "consumer forum", "legal notice", "file a case", "fir"]),
    "highly emotional language": (10, ["never buy", "worst experience", "disgusting", "horrible", "nightmare"]),
}

LARGE_DISCUSSION_COMMENT_THRESHOLD = 50
LARGE_DISCUSSION_POINTS = 15

# (minimum score, priority label), checked highest-first
PRIORITY_THRESHOLDS: list[tuple[int, str]] = [
    (75, "Critical"),
    (50, "High"),
    (25, "Medium"),
    (0, "Low"),
]


def _score_keywords(text: str) -> tuple[int, list[str]]:
    """Match risk keyword categories against text; return points and matched reasons."""
    lowered = text.lower()
    points = 0
    reasons: list[str] = []
    for label, (weight, keywords) in RISK_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", lowered) for kw in keywords):
            points += weight
            reasons.append(label)
    return points, reasons


def _score_discussion_size(post: dict[str, Any]) -> tuple[int, list[str]]:
    """Award points for posts with unusually high engagement (large discussion size)."""
    if post.get("num_comments", 0) >= LARGE_DISCUSSION_COMMENT_THRESHOLD:
        return LARGE_DISCUSSION_POINTS, ["large discussion size"]
    return 0, []


def _priority_for_score(score: int) -> str:
    """Map a 0-100 risk score to a priority label."""
    for threshold, label in PRIORITY_THRESHOLDS:
        if score >= threshold:
            return label
    return "Low"


def assess_post(post: dict[str, Any]) -> dict[str, Any]:
    """Score a single collected post and return it augmented with risk fields."""
    combined_text = f"{post.get('title', '')} {post.get('text', '')}"
    keyword_points, keyword_reasons = _score_keywords(combined_text)
    size_points, size_reasons = _score_discussion_size(post)

    risk_score = min(keyword_points + size_points, 100)

    return {
        **post,
        "risk_score": risk_score,
        "priority": _priority_for_score(risk_score),
        "reasons": keyword_reasons + size_reasons,
    }


def assess_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score every collected post for reputation risk."""
    print(f"STEP 7: risk_agent received {len(posts)} posts")
    if not posts:
        print("STEP 7 ZERO REASON: claude_agent/analyze_posts() returned 0 relevant posts — see STEP 5/6.")
    return [assess_post(post) for post in posts]
