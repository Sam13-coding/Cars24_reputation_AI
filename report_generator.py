"""Report Generator: turns risk-scored posts into an executive report.

Writes both output/report.json (structured report) and output/results.csv
(flat table of every scored discussion).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from prompts import DEFAULT_RECOMMENDED_ACTION, EXECUTIVE_SUMMARY_TEMPLATE, RECOMMENDED_ACTIONS

PRIORITY_ORDER = ["Critical", "High", "Medium", "Low"]
CSV_FIELDNAMES = ["title", "url", "subreddit", "risk_score", "priority", "reasons", "score", "num_comments"]


def _priority_counts(posts: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(post["priority"] for post in posts)
    return {priority: counts.get(priority, 0) for priority in PRIORITY_ORDER}


def _top_complaints(posts: list[dict[str, Any]], top_n: int = 5) -> list[dict[str, Any]]:
    """Rank risk-factor reasons by how many discussions raised them."""
    reason_counts = Counter(reason for post in posts for reason in post.get("reasons", []))
    return [{"reason": reason, "count": count} for reason, count in reason_counts.most_common(top_n)]


def _highest_risk_discussion(posts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not posts:
        return None
    return max(posts, key=lambda post: post["risk_score"])


def _recommended_actions(top_complaints: list[dict[str, Any]]) -> list[str]:
    if not top_complaints:
        return [DEFAULT_RECOMMENDED_ACTION]
    return [RECOMMENDED_ACTIONS.get(item["reason"], DEFAULT_RECOMMENDED_ACTION) for item in top_complaints]


def _executive_summary(
    posts: list[dict[str, Any]], counts: dict[str, int], top_complaints: list[dict[str, Any]]
) -> str:
    top_reason = top_complaints[0]["reason"] if top_complaints else "none"
    top_reason_count = top_complaints[0]["count"] if top_complaints else 0
    return EXECUTIVE_SUMMARY_TEMPLATE.format(
        total=len(posts),
        critical=counts["Critical"],
        high=counts["High"],
        medium=counts["Medium"],
        low=counts["Low"],
        top_reason=top_reason,
        top_reason_count=top_reason_count,
    )


def build_report(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the executive report structure from risk-scored posts."""
    counts = _priority_counts(posts)
    top_complaints = _top_complaints(posts)

    return {
        "executive_summary": _executive_summary(posts, counts, top_complaints),
        "top_complaints": top_complaints,
        "highest_risk_discussion": _highest_risk_discussion(posts),
        "recommended_actions": _recommended_actions(top_complaints),
        "statistics": {
            "total_discussions": len(posts),
            "priority_breakdown": counts,
            "subreddit_breakdown": dict(Counter(post.get("subreddit", "unknown") for post in posts)),
        },
    }


def save_report(report: dict[str, Any], posts: list[dict[str, Any]], output_dir: Path) -> None:
    """Write the report to report.json and the scored posts to results.csv."""
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"STEP 8: report_generator wrote {len(posts)} discussions")
    if not posts:
        print("STEP 8 ZERO REASON: risk_agent/assess_posts() received 0 posts — see STEP 7.")

    results_path = output_dir / "results.csv"
    with results_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for post in posts:
            writer.writerow({**post, "reasons": "; ".join(post.get("reasons", []))})
