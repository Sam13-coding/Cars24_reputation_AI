# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

The pipeline is implemented (collector, claude_agent, risk_agent, report_generator, main, dashboard). No automated tests exist yet.

Key implementation decisions (confirmed with the user, not to be changed without asking):
- **Reddit access**: NO Reddit API, no Reddit credentials of any kind. `collector.py` finds discussion URLs via a pluggable `SearchProvider` (default: DuckDuckGo HTML search restricted to `site:reddit.com` — deliberately not a Google scrape), then downloads each page directly (rewritten to `old.reddit.com` for server-rendered HTML) and parses it with BeautifulSoup. The Reddit-API dependency was removed entirely on 2026-07-12 per explicit instruction.
- **Gemini Agent** (`claude_agent.py` — filename kept as-is by explicit instruction even though it no longer calls Anthropic; switched to Gemini on 2026-07-16): sits between collector and risk_agent. `analyze_posts(posts) -> (relevant_posts, reputation_summary)` is the public interface; that signature has stayed stable across several internal rewrites, so `main.py`/`dashboard.py` never need to change when this module's internals do. Calls the Gemini API (`google-genai` SDK, the current official one — the older `google-generativeai` SDK was removed). Reads `GEMINI_API_KEY` via python-dotenv from `output/.env` (same pattern as `SERPAPI_API_KEY` in `collector.py`). Model is `gemini-3.5-flash`, **not** `gemini-2.5-flash`: that model still appears in `client.models.list()` but live `generateContent` calls against it 404 with "no longer available to new users" — confirmed against this project's real key on 2026-07-16, not a guess. Re-check `client.models.list()` before assuming that's still true in a future session.
- **Gemini batching (2026-07-16, second pass)**: posts are analyzed in batches of `BATCH_SIZE = 4` (never more per request — one giant request covering all collected posts was slow enough to trip `504 DEADLINE_EXCEEDED`), each batch judged for relevance/sentiment/confidence/reasoning independently. After every batch finishes, exactly ONE additional lightweight request generates the reputation summary from the merged, already-computed results (sentiment counts + a capped sample of reasoning strings) — it never resends the original post text, so its cost doesn't grow with post count. Batch prompts send **title + selftext only** — no subreddit, url, score, comment count, or timestamps — to minimize tokens. **Fallback (new behavior — `analyze_posts` no longer raises `GeminiUnavailableError` out to callers)**: if a batch fails after exhausting its retries, or `GEMINI_API_KEY` isn't set, that batch (or the whole run) falls back to the original keyword-only Cars24 relevance check (`_BRAND_PATTERN = re.compile(r"cars\s*-?\s*24", ...)`) instead of failing the pipeline; the summary request is only attempted if at least one batch actually succeeded via Gemini, otherwise a templated, sentiment-count-based summary is used instead. `dashboard.py` still imports and catches `GeminiUnavailableError` specially, but that branch is now effectively unreachable since the exception no longer escapes `analyze_posts` — left in place since removing it isn't required and dashboard.py is otherwise out of scope for this change.
- **Gemini retry mechanics** (shared by every request this module makes — per-batch and the summary): retries only on overload (503 / `RESOURCE_EXHAUSTED`) or a stalled attempt, fixed backoff `[2, 4, 8]`s, max 3 retries, printing `Retry X/3...` plus a full traceback (`traceback.print_exc()`) on every caught failure. **Load-bearing gotcha #1, do not remove**: `GenerateContentConfig.http_options` sets `retry_options=HttpRetryOptions(attempts=1)` — without this, `google-genai` runs its own internal tenacity retry (default up to 5 attempts, 1s→60s backoff) underneath ours, and the two compound; a live run hung for 10+ minutes before this was diagnosed. **Load-bearing gotcha #2, do not remove — and do not assume gotcha #1 alone is sufficient**: `http_options.timeout` (30s) only bounds a request if httpx's own timeout machinery actually fires for the failure in play; it is *not* a guarantee — proven empirically by patching the SDK's `_request_once` to sleep 90s and observing the call still block for the full 90s with only `http_options.timeout` set. The actual fix is `_run_with_hard_timeout`: it runs `generate_content()` on a worker thread and bounds the wait with `Future.result(timeout=30)`, built on `threading.Condition.wait()`, which unconditionally returns after 30s whether or not the worker thread ever finishes; a timed-out future is abandoned via `executor.shutdown(wait=False)`, never waited on. Also confirmed live, repeatedly: this project's Gemini API key is on the free tier, capped at `GenerateRequestsPerDayPerProjectPerModel-FreeTier` = **20 requests/day** for `gemini-3.5-flash` — with batching this is easy to exceed on a single 100-post run (25 batches + 1 summary = 26 requests > 20), independent of retries; shows up as `429 RESOURCE_EXHAUSTED`, not a code bug.
- **Risk scoring**: rule-based keyword + engagement heuristics in `risk_agent.py`, no LLM call. Unchanged since introduction — `claude_agent.py` was inserted upstream of it rather than replacing it.
- **Gemini reliability/token-efficiency pass (2026-07-16)**: `claude_agent.py`'s `_call_gemini` retries only on overload (HTTP 503 / `RESOURCE_EXHAUSTED`) or a stalled attempt, fixed backoff `[2, 4, 8]`s, max 3 retries, printing `Retry X/3...` plus a full traceback (`traceback.print_exc()`) on every caught failure, then raises `GeminiUnavailableError` with a fixed friendly message that `dashboard.py` shows verbatim (caught before the generic pipeline-error handler). **Load-bearing gotcha #1, do not remove**: `GenerateContentConfig.http_options` sets `retry_options=HttpRetryOptions(attempts=1)` — without this, `google-genai` runs its own internal tenacity retry (default up to 5 attempts, 1s→60s backoff) underneath ours, and the two compound; a live run hung for 10+ minutes before this was diagnosed. **Load-bearing gotcha #2, do not remove — and do not assume gotcha #1 alone is sufficient**: `http_options.timeout` (30s) only bounds a request if httpx's own timeout machinery actually fires for the failure in play; it is *not* a guarantee. Proven empirically: patching the SDK's `_request_once` to sleep 90s and calling through `_call_gemini` with only `http_options.timeout` set still blocked for the full 90s — the configured timeout never fired. The actual fix is `_run_with_hard_timeout`: it runs `generate_content()` on a worker thread and bounds the wait with `Future.result(timeout=30)`, built on `threading.Condition.wait()`, which unconditionally returns after 30s whether or not the worker thread ever finishes; a timed-out future is abandoned via `executor.shutdown(wait=False)`, never waited on. This is what makes "never allow an infinite wait" true by construction rather than by trusting the SDK/transport. Also confirmed live: this project's Gemini API key is on the free tier, capped at `GenerateRequestsPerDayPerProjectPerModel-FreeTier` = **20 requests/day** for `gemini-3.5-flash` — repeated manual testing exhausts it fast (each pipeline run can cost up to 4 requests via retries) and shows up as `429 RESOURCE_EXHAUSTED`, not a code bug. Schema is intentionally minimal (`relevant`/`sentiment`/`confidence`/`reasoning`\[≤160 chars\]) with no `index` field — the response array is mapped back to posts by position (`zip`), not by an id, to save output tokens.

### Commands

```
pip install -r requirements.txt   # install deps (requests, beautifulsoup4, pandas, streamlit, python-dotenv, google-search-results, google-genai)
python main.py                    # run collector -> claude_agent -> risk_agent -> report_generator, writes output/report.json + output/results.csv
streamlit run dashboard.py        # view the report (run main.py first)
```

No lint/test tooling is configured yet. `risk_agent.py` and `report_generator.py` have been verified with synthetic data. `collector.py`'s `_extract_post` HTML parsing is unit-tested against realistic old.reddit markup and works correctly. Both `collector.py` (via SerpAPI) and `claude_agent.py` (via Gemini) have been run live end-to-end against real credentials as of 2026-07-16 and produced a real `output/report.json` + `output/results.csv`.

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
