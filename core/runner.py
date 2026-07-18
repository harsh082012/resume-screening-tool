"""
core/runner.py
--------------
The shared screening engine. Scores a batch of resumes against one JD using
BOUNDED CONCURRENCY (a small thread pool) instead of one-at-a-time, reports
progress via a callback, and tolerates per-file failures.

Why threads (not asyncio):
    The LLM calls in llm_client.py use the blocking `requests` library. A
    thread pool runs several of those blocking calls at once -- while one
    thread waits on the network (or on a 429 back-off), the others make
    progress. This needs ZERO changes to llm_client. asyncio would require
    rewriting every provider call to be async, for no extra benefit here.

Why BOUNDED (default 3 workers):
    Free-tier providers rate-limit aggressively. Firing 40 requests at once
    just produces 40x HTTP 429s. A small pool keeps enough in flight to be
    fast without overwhelming the provider; the existing retry/back-off in
    llm_client handles the occasional 429 that still slips through.

This module is front-end agnostic: the Flask app, the CLI, and (later) the
Slack bot all call run_screening() the same way. Only the progress reporting
differs -- Flask updates an in-memory job dict, the CLI prints, Slack will
update a message.
"""

import concurrent.futures

from core.extractor import extract_text
from core.scorer import score_resume, rank_candidates
from core.llm_client import LLMError


def _process_one(item, jd_text):
    """
    Extract + score a single resume. Returns ("ok", result_dict) on success
    or ("error", message) on any failure, so one bad file never crashes the
    whole batch.
    item is a (filepath, display_filename) tuple.
    """
    filepath, filename = item

    try:
        text = extract_text(filepath)
    except Exception as e:
        return ("error", f"{filename}: failed to read file ({e}).")

    if not text.strip():
        return ("error", f"{filename}: no extractable text found, skipped.")

    try:
        result = score_resume(text, jd_text, filename=filename)
        return ("ok", result)
    except LLMError as e:
        return ("error", f"{filename}: AI scoring failed ({e}).")
    except Exception as e:
        return ("error", f"{filename}: unexpected error ({e}).")


def run_screening(jd_text, resume_files, max_workers=3, on_progress=None):
    """
    Score every resume against the JD, concurrently, and return ranked results.

    Args:
        jd_text:       the job description text.
        resume_files:  list of (filepath, display_filename) tuples.
        max_workers:   how many resumes to score at once (default 3; safe for
                       OpenRouter free-tier. Raise it if you switch to a
                       provider with a higher rate limit, e.g. Gemini).
        on_progress:   optional callback(done:int, total:int, last_file:str),
                       called each time a resume finishes. Used to drive a
                       progress bar (web) or a status message (Slack).

    Returns:
        (ranked_candidates, errors)
        ranked_candidates: list of scored dicts, sorted best-first with a rank.
        errors:            list of human-readable strings for skipped/failed files.
    """
    total = len(resume_files)
    candidates = []
    errors = []
    done = 0

    if total == 0:
        return [], []

    # max_workers must be at least 1, and never more than the number of files.
    workers = max(1, min(max_workers, total))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_item = {
            pool.submit(_process_one, item, jd_text): item
            for item in resume_files
        }
        for future in concurrent.futures.as_completed(future_to_item):
            status, payload = future.result()
            if status == "ok":
                candidates.append(payload)
            else:
                errors.append(payload)

            done += 1
            if on_progress:
                _, filename = future_to_item[future]
                try:
                    on_progress(done, total, filename)
                except Exception:
                    # A misbehaving progress callback must never break scoring.
                    pass

    ranked = rank_candidates(candidates)
    return ranked, errors