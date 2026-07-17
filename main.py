"""Entry point: run the collector -> Gemini agent -> risk agent -> report generator pipeline."""

from pathlib import Path

from claude_agent import analyze_posts
import collector as _collector
from report_generator import build_report, save_report
from risk_agent import assess_posts

OUTPUT_DIR = Path("output")


def main() -> None:
    # Collector module may expose the posts-collecting function under different names.
    collect_func = getattr(_collector, "collect_posts", None) or getattr(_collector, "collect", None)
    if collect_func is None:
        raise ImportError("collector module does not define collect_posts or collect()")

    posts = collect_func()
    print(f"Collected {len(posts)} discussions.")

    relevant_posts, reputation_summary = analyze_posts(posts)
    print(f"Gemini kept {len(relevant_posts)}/{len(posts)} discussions as relevant to Cars24.")
    print(f"Gemini reputation summary: {reputation_summary}")

    scored_posts = assess_posts(relevant_posts)
    print("Scored discussions for reputation risk.")

    report = build_report(scored_posts)
    save_report(report, scored_posts, OUTPUT_DIR)
    print(f"Report written to {OUTPUT_DIR / 'report.json'} and {OUTPUT_DIR / 'results.csv'}.")
    print(f"[main] Final report total_discussions = {report['statistics']['total_discussions']}.")


if __name__ == "__main__":
    main()
