"""Gemini Agent: relevance filter, sentiment analysis, and reputation summary.

Sits between collector.py and risk_agent.py. Posts are analyzed in small
batches (BATCH_SIZE each, never more) rather than one giant request, because
a single request covering all collected posts was slow enough to trip
504 DEADLINE_EXCEEDED — the model has to read every post and produce a
correspondingly large structured response before the request can return, and
that grows with the batch size. Splitting into small batches keeps each
individual request fast and small regardless of how many posts were
collected (100 posts -> 25 tiny requests, not one huge one). For each post,
Gemini judges whether it is genuinely about Cars24 (vs. a coincidental
keyword match), its sentiment, a confidence score, and a short reasoning
string; posts judged irrelevant are dropped. After every batch has been
processed, ONE additional lightweight request generates a single reputation
summary from the already-computed per-post results — it never resends the
original post text, so its cost does not grow with how many posts were
analyzed. Does not score risk — that remains risk_agent.py's job.

If Gemini is not configured, or a batch (or the summary request) still fails
after every retry, that piece of the run falls back to the original
keyword-only Cars24 relevance check instead of failing the whole pipeline.

Transient overload errors (HTTP 503 / RESOURCE_EXHAUSTED) and a stalled
request (no response within REQUEST_TIMEOUT_SECONDS) are retried with fixed
exponential backoff; retries are recovery attempts for that one request, not
additional calls.

Root cause of a prior hang, for the record: `HttpOptions.timeout` only bounds
a request if httpx's own timeout machinery actually fires for the failure in
question. That is not a guarantee — a stalled connection, an unresponsive
proxy, or a socket that never signals an error can block the underlying
synchronous call indefinitely regardless of what timeout value is configured,
because nothing in that call path checks a wall-clock deadline; it only
reacts to socket-level events. Passing `timeout=N` cannot un-block a call
that is not itself watching the clock. The single reliable defense is
`_run_with_hard_timeout` below: it runs the request on a worker thread and
waits on it via `concurrent.futures.Future.result(timeout=...)`, which is
built on `threading.Condition.wait(timeout=...)` — a primitive that always
returns control after N seconds whether or not the worker thread ever
finishes. That is what makes the timeout here unconditional.

Second-order gotcha found while hardening the summary step specifically: even
with the above, a `ThreadPoolExecutor` worker is never a daemon thread, and
`concurrent.futures._python_exit()` joins every worker thread ever created by
ANY executor at interpreter shutdown — regardless of whether that executor
was individually shut down. So a call that genuinely never returns leaves an
abandoned worker that can still block the whole *process* from exiting, even
though the calling function already returned a fallback result on schedule.
Confirmed empirically with a permanently-hanging fake call: the function
using `_run_with_hard_timeout` returned correctly within budget, but the
interpreter then hung waiting to join the leaked thread. The summary step
(`_run_summary_with_hard_timeout`) uses a plain `daemon=True` thread instead
for exactly this reason — daemon threads are killed outright at interpreter
exit, never joined. The batch path still uses `_run_with_hard_timeout`
unchanged; this only matters in practice for a request that hangs forever
rather than erroring, which batches have not been observed to do.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv(Path(__file__).resolve().parent / "output" / ".env")

MODEL_NAME = "gemini-3.5-flash"
MAX_POST_CHARS = 600  # keep each post's contribution to a batch prompt small
BATCH_SIZE = 4  # never send more than this many posts in a single Gemini request
SUMMARY_MAX_REASONS = 15  # cap how many reasoning strings feed the summary prompt
DEFAULT_REPUTATION_SUMMARY = "No Cars24-relevant discussions were found to summarize."

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = [2, 4, 8]
REQUEST_TIMEOUT_SECONDS = 30  # hard, unconditional per-attempt budget (see module docstring)
REQUEST_TIMEOUT_MS = REQUEST_TIMEOUT_SECONDS * 1000  # also passed to httpx as a first line of defense
GEMINI_BUSY_MESSAGE = "Gemini is temporarily experiencing high demand. Please try again in a few moments."

# Free-tier RPM cap (GenerateRequestsPerMinutePerProjectPerModel-FreeTier) is easy to exceed once
# batching issues several requests back to back. 12s between requests -> at most 60/12 = 5 req/min,
# spacing every request (batch or summary, first attempt or retry) this far apart regardless of call
# site keeps the whole run under that cap without tracking a rolling request count. Configurable via
# env var because the free-tier limit is an external, changeable fact, not a code constant.
REQUEST_INTERVAL_SECONDS = float(os.getenv("GEMINI_REQUEST_INTERVAL_SECONDS", "12"))

_BRAND_PATTERN = re.compile(r"cars\s*-?\s*24", re.IGNORECASE)
_FALLBACK_REASONING = "Keyword-based fallback: Gemini was unavailable for this batch."


class GeminiUnavailableError(RuntimeError):
    """Raised when one Gemini request keeps failing (overload or timeout) after every retry."""


class _RetryableGeminiError(Exception):
    """Internal: this attempt failed in a way worth retrying (timeout, 503, RESOURCE_EXHAUSTED)."""


class _RateLimiter:
    """Centralized client-side throttle shared by every Gemini request — batch and summary alike.

    Spaces consecutive requests at least REQUEST_INTERVAL_SECONDS apart by
    wall-clock time, tracked from when each request is issued (not when it
    finishes), so free-tier RPM is respected regardless of which call site
    (a batch, a retry of a batch, or the summary) triggers the next request.
    A single module-level instance is shared by _run_with_hard_timeout and
    _run_summary_with_hard_timeout so both paths throttle against the same
    clock rather than each getting their own independent allowance. The lock
    makes this safe even though, in practice, calls happen sequentially.
    """

    def __init__(self, interval_seconds: float) -> None:
        self._interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._last_request_at: float | None = None

    def wait(self) -> None:
        """Block, if needed, so at least interval_seconds have passed since the last request."""
        with self._lock:
            now = time.monotonic()
            remaining = 0.0
            if self._last_request_at is not None:
                remaining = self._interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                print(f"Waiting {remaining:.1f} seconds to respect Gemini free-tier rate limit...")
                time.sleep(remaining)
            self._last_request_at = time.monotonic()


_rate_limiter = _RateLimiter(REQUEST_INTERVAL_SECONDS)


_BATCH_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "relevant": {"type": "boolean"},
                    "sentiment": {"type": "string", "enum": ["Positive", "Neutral", "Negative"]},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string", "maxLength": 160},
                },
                "required": ["relevant", "sentiment", "confidence", "reasoning"],
            },
        },
    },
    "required": ["results"],
}

_SUMMARY_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"reputation_summary": {"type": "string"}},
    "required": ["reputation_summary"],
}

_BATCH_PROMPT_TEMPLATE = """Cars24 is an Indian used-car marketplace. For each numbered Reddit post \
below, judge: relevant (true only if genuinely about the company Cars24, not a coincidental name \
match, a different "cars 24"-named entity, a typo, or a username), sentiment \
(Positive/Neutral/Negative), confidence (0-1), reasoning (<=20 words). Keep the same order as the posts.

{posts_block}
"""

_SUMMARY_PROMPT_TEMPLATE = """Cars24 is an Indian used-car marketplace. {count} Reddit discussions about \
Cars24 were analyzed; sentiment breakdown: {breakdown}. Representative reasons: {reasons}. Write one \
reputation_summary (3-4 sentences) describing Cars24's overall Reddit reputation based on this.
"""


def _build_client() -> genai.Client:
    """Build a Gemini API client using GEMINI_API_KEY from the environment."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set.")
    return genai.Client(api_key=api_key)


def _truncate(text: str, limit: int = MAX_POST_CHARS) -> str:
    """Trim post text so a batch prompt stays within a reasonable size."""
    return text if len(text) <= limit else f"{text[:limit]}…"


def _build_batch_prompt(batch: list[dict[str, Any]]) -> str:
    """Render one batch for a single Gemini request — title + selftext only, no other metadata."""
    posts_block = "\n".join(
        f"{index}. {post.get('title', '')} — {_truncate(post.get('text', ''))}"
        for index, post in enumerate(batch, start=1)
    )
    return _BATCH_PROMPT_TEMPLATE.format(posts_block=posts_block)


def _build_summary_prompt(analyzed_posts: list[dict[str, Any]]) -> str:
    """Render a compact digest of already-analyzed posts — never the original post text again."""
    counts = Counter(post.get("sentiment", "Neutral") for post in analyzed_posts)
    breakdown = ", ".join(f"{count} {sentiment}" for sentiment, count in counts.most_common())
    reasons = "; ".join(
        post.get("reasoning", "") for post in analyzed_posts[:SUMMARY_MAX_REASONS] if post.get("reasoning")
    )
    return _SUMMARY_PROMPT_TEMPLATE.format(count=len(analyzed_posts), breakdown=breakdown, reasons=reasons)


def _is_retryable(exc: errors.APIError) -> bool:
    """Whether an APIError is a transient overload error worth retrying (503 / RESOURCE_EXHAUSTED)."""
    return exc.code == 503 or exc.status == "RESOURCE_EXHAUSTED"


def _run_with_hard_timeout(client: genai.Client, prompt: str, config: types.GenerateContentConfig) -> Any:
    """Run one generate_content() call with an unconditional wall-clock timeout.

    `config.http_options.timeout` only bounds the request if httpx's own
    timeout machinery fires for the failure in play — it does not fire for
    every possible network stall (a proxy holding the connection open, a
    socket that never signals an error, etc.), so it is not a guarantee on
    its own. Running the call on a worker thread and bounding the wait with
    `Future.result(timeout=...)` — built on `threading.Condition.wait()` — IS
    a guarantee: that primitive always returns after REQUEST_TIMEOUT_SECONDS
    whether or not the worker thread has finished. A future that times out is
    abandoned (`shutdown(wait=False)`), never waited on, so this call itself
    can never hang past the configured budget.

    Throttled by the shared _rate_limiter before the request is issued, so
    every attempt — including retries — respects the free-tier RPM cap.
    """
    _rate_limiter.wait()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(client.models.generate_content, model=MODEL_NAME, contents=prompt, config=config)
    try:
        response = future.result(timeout=REQUEST_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        executor.shutdown(wait=False)
        raise _RetryableGeminiError(f"no response within {REQUEST_TIMEOUT_SECONDS}s") from exc
    except errors.APIError as exc:
        executor.shutdown(wait=False)
        if _is_retryable(exc):
            raise _RetryableGeminiError(f"{exc.status or exc.code}") from exc
        raise
    executor.shutdown(wait=False)
    return response


def _request_json(client: genai.Client, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Make one Gemini structured-JSON request, retrying transient failures only.

    Retries up to MAX_RETRIES times with fixed backoff (2s, 4s, 8s) when an
    attempt is overloaded (503 / RESOURCE_EXHAUSTED) or does not respond
    within REQUEST_TIMEOUT_SECONDS; any other error propagates immediately.
    Raises GeminiUnavailableError, with the full underlying traceback already
    printed, if every retry also fails. Never waits and never retries
    unboundedly — see _run_with_hard_timeout for the hard per-attempt cap.
    Shared by every Gemini call this module makes (per-batch analysis and the
    final summary) so all of them get identical retry behavior.
    """
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS,
            # The SDK retries transient errors internally by default (up to 5 attempts
            # with its own backoff). Disable that so our explicit 2s/4s/8s retry loop
            # below is the only retry layer — otherwise the two compound unpredictably.
            # (This alone is NOT sufficient to bound wall-clock time — see
            # _run_with_hard_timeout for why an unconditional watchdog is also required.)
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )

    attempt = 0
    while True:
        try:
            response = _run_with_hard_timeout(client, prompt, config)
        except _RetryableGeminiError as exc:
            traceback.print_exc()
            if attempt >= MAX_RETRIES:
                raise GeminiUnavailableError(GEMINI_BUSY_MESSAGE) from exc
            delay = RETRY_BACKOFF_SECONDS[attempt]
            print(f"Retry {attempt + 1}/{MAX_RETRIES}...")
            print(f"[claude_agent] Reason: {exc}. Waiting {delay}s before retrying...")
            time.sleep(delay)
            attempt += 1
            continue
        except errors.APIError:
            traceback.print_exc()
            raise

        response_text = response.text
        if response_text is None:
            raise RuntimeError("Gemini returned no response body.")
        return json.loads(response_text)


def _call_gemini_batch(client: genai.Client, batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze one batch (<= BATCH_SIZE posts) for relevance and sentiment in a single request."""
    return _request_json(client, _build_batch_prompt(batch), _BATCH_RESPONSE_SCHEMA)


def _run_summary_with_hard_timeout(client: genai.Client, prompt: str, config: types.GenerateContentConfig) -> Any:
    """Run the summary's generate_content() call under the same wall-clock timeout as
    batches, but on a daemon thread instead of a ThreadPoolExecutor worker.

    Deliberately not a call to _run_with_hard_timeout (the batch path): a
    ThreadPoolExecutor worker is never a daemon thread, and
    concurrent.futures._python_exit() joins every worker thread ever created
    by ANY executor at interpreter shutdown, regardless of whether that
    specific executor was shut down. So if generate_content() genuinely never
    returns, the batch path's abandoned worker would keep the whole *process*
    alive at exit even though the calling function already moved on and
    returned a fallback result. Confirmed empirically: with a fake call that
    sleeps forever, _summarize() built on _run_with_hard_timeout still
    returned correctly within budget, but the interpreter then hung for the
    leaked thread's full sleep before it could exit. A daemon thread avoids
    this: daemon threads are killed outright at interpreter exit, never
    joined, so a stuck summary call can never block the program from ending.

    Throttled by the shared _rate_limiter before the request is issued, same
    as the batch path, so the summary request (and any retries of it) also
    respects the free-tier RPM cap.
    """
    _rate_limiter.wait()
    result_box: list[Any] = []
    error_box: list[BaseException] = []
    done = threading.Event()

    def _worker() -> None:
        try:
            result_box.append(client.models.generate_content(model=MODEL_NAME, contents=prompt, config=config))
        except BaseException as exc:  # re-raised on the calling thread below, once, if it arrives in time
            error_box.append(exc)
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    if not done.wait(timeout=REQUEST_TIMEOUT_SECONDS):
        raise _RetryableGeminiError(f"no response within {REQUEST_TIMEOUT_SECONDS}s")
    if error_box:
        exc = error_box[0]
        if isinstance(exc, errors.APIError) and _is_retryable(exc):
            raise _RetryableGeminiError(f"{exc.status or exc.code}") from exc
        raise exc
    return result_box[0]


def _call_gemini_summary(client: genai.Client, analyzed_posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate the single, final reputation summary from already-analyzed posts.

    Intentionally does not go through _request_json (the shared batch/summary
    helper it used before) — see _run_summary_with_hard_timeout's docstring
    for why the summary path needs its own daemon-thread-based runner. Retry
    count, backoff schedule, and per-attempt timeout reuse the exact same
    constants as the batch path (MAX_RETRIES, RETRY_BACKOFF_SECONDS,
    REQUEST_TIMEOUT_SECONDS), so the retry behavior itself is identical; only
    the abandoned-thread lifecycle differs.
    """
    prompt = _build_summary_prompt(analyzed_posts)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_SUMMARY_RESPONSE_SCHEMA,
        http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )

    attempt = 0
    while True:
        try:
            response = _run_summary_with_hard_timeout(client, prompt, config)
        except _RetryableGeminiError as exc:
            traceback.print_exc()
            if attempt >= MAX_RETRIES:
                raise GeminiUnavailableError(GEMINI_BUSY_MESSAGE) from exc
            delay = RETRY_BACKOFF_SECONDS[attempt]
            print(f"Retry {attempt + 1}/{MAX_RETRIES}...")
            print(f"[claude_agent] Reason: {exc}. Waiting {delay}s before retrying...")
            time.sleep(delay)
            attempt += 1
            continue
        except errors.APIError:
            traceback.print_exc()
            raise

        response_text = response.text
        if response_text is None:
            raise RuntimeError("Gemini returned no response body.")
        return json.loads(response_text)


def _fallback_analyze(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keyword-only Cars24 relevance check, used when a batch's Gemini call fails after every retry."""
    relevant = []
    for post in batch:
        combined = f"{post.get('title', '')} {post.get('text', '')}"
        if _BRAND_PATTERN.search(combined):
            relevant.append(
                {**post, "sentiment": "Neutral", "confidence": 0.0, "reasoning": _FALLBACK_REASONING}
            )
    return relevant


def _fallback_summary(analyzed_posts: list[dict[str, Any]]) -> str:
    """Non-LLM reputation summary, used when the dedicated Gemini summary request is unavailable."""
    if not analyzed_posts:
        return DEFAULT_REPUTATION_SUMMARY
    counts = Counter(post.get("sentiment", "Neutral") for post in analyzed_posts)
    breakdown = ", ".join(f"{count} {sentiment}" for sentiment, count in counts.most_common())
    return (
        f"Gemini was unavailable for part or all of this run. {len(analyzed_posts)} Cars24-relevant "
        f"discussion(s) were identified; sentiment breakdown: {breakdown}."
    )


def _analyze_batch(client: genai.Client, batch: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Analyze one batch via Gemini; fall back to the keyword check if it fails after every retry.

    Returns the analyzed posts plus whether Gemini actually produced them.
    """
    try:
        payload = _call_gemini_batch(client, batch)
    except GeminiUnavailableError as exc:
        print(
            f"[claude_agent] Batch analysis failed after retries ({exc}); "
            f"falling back to keyword-based relevance for this batch."
        )
        return _fallback_analyze(batch), False

    results = payload.get("results", [])
    analyzed = [
        {
            **post,
            "sentiment": analysis.get("sentiment", "Neutral"),
            "confidence": analysis.get("confidence", 0.0),
            "reasoning": analysis.get("reasoning", ""),
        }
        for post, analysis in zip(batch, results)
        if analysis.get("relevant")
    ]
    return analyzed, True


def _summarize(client: genai.Client, analyzed_posts: list[dict[str, Any]]) -> str:
    """Generate the one final reputation summary, falling back to a rule-based digest on failure.

    _call_gemini_summary applies the same 30s hard timeout and [2, 4, 8]s
    retry backoff as the batch path (see _run_summary_with_hard_timeout for
    why it uses its own daemon-thread runner rather than the batch path's).
    The except clause here is intentionally broader than just
    GeminiUnavailableError: this is the last step of analyze_posts(), so any
    failure here (retry exhaustion, a non-retryable API error, an unparsable
    response) must fall back to a deterministic summary rather than let an
    exception escape and take down the whole pipeline on the final step.
    """
    print("Generating final summary...")
    summary_text = None
    try:
        payload = _call_gemini_summary(client, analyzed_posts)
        summary_text = payload.get("reputation_summary")
    except GeminiUnavailableError as exc:
        print(f"[claude_agent] Summary request failed after retries ({exc}); using a rule-based summary instead.")
    except Exception:
        traceback.print_exc()
        print("[claude_agent] Summary request failed unexpectedly; using a rule-based summary instead.")
    print("Summary complete.")
    return summary_text or _fallback_summary(analyzed_posts)


def analyze_posts(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Filter posts to those genuinely about Cars24 and attach Gemini sentiment metadata.

    Posts are analyzed in batches of at most BATCH_SIZE (never one request for
    everything — see module docstring for why), each batch retried
    independently and falling back to a keyword-only check if it still fails.
    Exactly one additional request generates the reputation summary after
    every batch has finished, from the merged results — never by resending
    the original post text. Returns the surviving posts — each augmented with
    `sentiment`, `confidence`, and `reasoning`, otherwise unchanged — plus
    that one reputation summary.
    """
    if not posts:
        return [], DEFAULT_REPUTATION_SUMMARY

    try:
        client = _build_client()
    except RuntimeError as exc:
        print(f"[claude_agent] {exc} Falling back to keyword-based relevance for all posts.")
        client = None

    batches = [posts[i : i + BATCH_SIZE] for i in range(0, len(posts), BATCH_SIZE)]
    total_batches = len(batches)

    analyzed_posts: list[dict[str, Any]] = []
    gemini_succeeded = False
    for batch_index, batch in enumerate(batches, start=1):
        print(f"Batch {batch_index}/{total_batches}...")
        if client is None:
            analyzed_posts.extend(_fallback_analyze(batch))
            continue
        batch_result, succeeded = _analyze_batch(client, batch)
        analyzed_posts.extend(batch_result)
        gemini_succeeded = gemini_succeeded or succeeded

    if not analyzed_posts:
        return [], DEFAULT_REPUTATION_SUMMARY

    if gemini_succeeded and client is not None:
        reputation_summary = _summarize(client, analyzed_posts)
    else:
        reputation_summary = _fallback_summary(analyzed_posts)
    return analyzed_posts, reputation_summary
