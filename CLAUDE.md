# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

The pipeline is implemented (collector, claude_agent, risk_agent, report_generator, main, dashboard). No automated tests exist yet.

Key implementation decisions (confirmed with the user, not to be changed without asking):
- **Reddit access**: NO Reddit API, no Reddit credentials of any kind. `collector.py` finds discussion URLs via a pluggable `SearchProvider` (default: DuckDuckGo HTML search restricted to `site:reddit.com` — deliberately not a Google scrape), then downloads each page directly (rewritten to `old.reddit.com` for server-rendered HTML) and parses it with BeautifulSoup. The Reddit-API dependency was removed entirely on 2026-07-12 per explicit instruction.
- **Claude Agent** (`claude_agent.py`, new 2026-07-12): sits between collector and risk_agent. Calls the Anthropic API (`claude-opus-4-8`, structured JSON output) to drop posts that keyword-matched "cars24" but aren't actually about the company; passes surviving posts through unmodified. Uses whatever Anthropic credentials are already configured in the environment (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile) — the user explicitly chose not to manage a project-specific API key variable, so don't add one.
- **Risk scoring**: rule-based keyword + engagement heuristics in `risk_agent.py`, no LLM call. Unchanged since introduction — `claude_agent.py` was inserted upstream of it rather than replacing it.

### Commands

```
pip install -r requirements.txt   # install deps (requests, beautifulsoup4, anthropic, pandas, streamlit)
python main.py                    # run collector -> claude_agent -> risk_agent -> report_generator, writes output/report.json + output/results.csv
streamlit run dashboard.py        # view the report (run main.py first)
```

No lint/test tooling is configured yet. `risk_agent.py` and `report_generator.py` have been verified with synthetic data. `collector.py`'s `_extract_post` HTML parsing is unit-tested against realistic old.reddit markup and works correctly. `claude_agent.py` has not been run live (no Anthropic credentials in the dev/verification environment).

### Known issue: DuckDuckGoSearchProvider is blocked by bot detection

As of 2026-07-12, `DuckDuckGoSearchProvider.search()` returns HTTP 200 but the body is an anti-bot CAPTCHA challenge page ("Unfortunately, bots use DuckDuckGo too...") rather than search results — confirmed from a cloud/sandboxed environment; untested from a residential IP. This mirrors the earlier `403` Reddit blocked us with before the current scraping approach was adopted: any unauthenticated, non-browser HTTP client hitting a major search engine or Reddit directly is at risk of the same anti-bot wall, regardless of which one is used. If this also fails on the user's machine, the durable fixes (needs the user's decision, don't switch unprompted) are: (a) a real headless-browser fetch (e.g. Playwright) that executes JS and passes bot checks, at the cost of a much heavier dependency, or (b) a paid/authenticated search API (e.g. Bing Web Search API, SerpAPI) which requires credentials. The `SearchProvider` abstraction in `collector.py` exists specifically so either replacement is a new class, not a rewrite.

## Project

**Cars24 Reputation Intelligence Agent** — Agent 1 of a larger enterprise Agentic AI system. This repo monitors Reddit discussions about CARS24 and identifies potential reputation risks. It produces structured JSON output that future (separate) agents will consume. This repository contains ONLY Agent 1.

## Role and working agreement

- Act as the Senior AI Software Engineer on this repo. Think before writing code.
- Never rewrite the whole project when only one file needs changes; only fix the failing section when debugging, and explain why the bug happened.
- Preserve the modular architecture — do not merge modules together.
- When generating code: explain the approach and state assumptions first, then generate the code. Never generate code without explanation.
- If requirements are missing or ambiguous, ask rather than assume. Never invent APIs or credentials.
- Build features in this order and don't jump ahead: Collector → Claude Agent → Risk Agent → Report Generator → Dashboard.

## Coding style

- Python 3.12+, PEP8, type hints, docstrings
- Small, reusable functions; avoid duplicated code
- Prefer readability over cleverness

## Architecture

Pipeline, one module per stage, each with a single responsibility:

```
main.py → collector.py → claude_agent.py → risk_agent.py → report_generator.py → dashboard.py
```

### collector.py

Responsible ONLY for finding Reddit discussions related to Cars24, reading discussion content, cleaning extracted text, and returning structured data. Must NOT calculate sentiment, predict reputation, or generate reports. Uses no Reddit API and no Reddit credentials — see workflow below.

Workflow: search query → find Reddit URLs via a pluggable `SearchProvider` (default implementation searches DuckDuckGo's HTML endpoint restricted to `site:reddit.com`; intentionally not a Google scrape, and swappable for a different provider later) → download each Reddit page directly (rewritten to `old.reddit.com`, which is server-rendered HTML, easier to parse than the JS-heavy new Reddit UI) → extract title/subreddit/url/text with BeautifulSoup → clean the text.

Return format:
```json
[
    {"title": "", "subreddit": "", "url": "", "text": ""}
]
```

Implementation note: each record also carries `score` and `num_comments`, scraped from the page — needed by `risk_agent.py` to detect the "large discussion size" risk factor. Treat `title`/`subreddit`/`url`/`text` as the required base; the rest is metadata. HTML scraping is inherently fragile — if Reddit changes its old-UI markup or blocks the scraping IP, `_extract_post`'s CSS selectors are the first thing to check.

### claude_agent.py

Input: collected posts from `collector.py`.

Responsible ONLY for judging whether each post is genuinely about Cars24 (vs. a coincidental keyword match) and dropping the irrelevant ones. Does NOT score risk, summarize, or modify surviving posts — they pass through unchanged. Calls the Anthropic API once per `collect_posts()` run, batching all posts into a single structured-output request (`output_config.format: json_schema`) rather than one call per post.

Output: the same record shape as `collector.py`, filtered down to relevant posts only.

### risk_agent.py

Input: collected Reddit posts (post-relevance-filter, from `claude_agent.py`).

Output:
```json
{"risk_score": 0, "priority": "", "reasons": []}
```

Priority levels: `Low`, `Medium`, `High`, `Critical`.

Risk factors include: fraud allegations, scam allegations, refund issues, consumer court mentions, large discussion size, highly emotional language.

### report_generator.py

Generates an executive report with sections: Executive Summary, Top Complaints, Highest Risk Discussion, Recommended Actions, Statistics. Outputs both `report.json` and `results.csv`.

### dashboard.py

Streamlit app displaying: total discussions, complaint categories, highest risk discussions, executive summary. Avoid unnecessary UI complexity.

## Expected folder structure

```
Cars24_Reputation_AI/
    main.py
    collector.py
    claude_agent.py
    risk_agent.py
    report_generator.py
    dashboard.py
    prompts.py
    README.md
    requirements.txt
    output/
```

Never place project files inside a `venv/` directory.

## Goal

This is a production-quality MVP for an internship demonstration. Prioritize clean architecture, modularity, readability, and maintainability over adding many features.
