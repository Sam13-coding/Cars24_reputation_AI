"""Entry point: run the collector -> risk agent -> report generator pipeline."""

from pathlib import Path

from claude_agent import filter_relevant_posts
from collector import collect_posts
from report_generator import build_report, save_report
from risk_agent import assess_posts

OUTPUT_DIR = Path("output")


def main() -> None:
    posts = collect_posts()
    print(f"Collected {len(posts)} discussions.")

    relevant_posts = filter_relevant_posts(posts)
    print(f"Claude Agent kept {len(relevant_posts)}/{len(posts)} discussions as relevant to Cars24.")

    scored_posts = assess_posts(relevant_posts)
    print("Scored discussions for reputation risk.")

    report = build_report(scored_posts)
    save_report(report, scored_posts, OUTPUT_DIR)
    print(f"Report written to {OUTPUT_DIR / 'report.json'} and {OUTPUT_DIR / 'results.csv'}.")


if __name__ == "__main__":
    main()
