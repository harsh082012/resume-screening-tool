# """
# llm_client.py
# Provider-agnostic wrapper around whichever LLM backend you configure.

# Supported providers (set via LLM_PROVIDER in .env):
#     "gemini"    -> Google Gemini API (default). Free tier, no credit card
#                    needed. Get a key at https://aistudio.google.com/apikey
#     "anthropic" -> Claude API. Pay-as-you-go, very cheap per resume, but
#                    requires billing set up at https://console.anthropic.com
#     "ollama"    -> Fully local, fully free, no API key, no internet needed.
#                    Requires https://ollama.com installed + a model pulled,
#                    e.g. `ollama pull llama3`. Cannot be deployed to a free
#                    web host (needs the model running on the server itself),
#                    so use this for local/offline demos only.

# All three expose the same call_llm(prompt) -> str interface so the rest of
# the app doesn't need to care which one is active.
# """

# import os
# import re
# import json
# import time
# import requests


# PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
# OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

# OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
# OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")


# class LLMError(Exception):
#     """Raised when the configured LLM backend fails or is misconfigured."""
#     pass


# def _call_gemini(prompt: str) -> str:
#     if not GEMINI_API_KEY:
#         raise LLMError(
#             "GEMINI_API_KEY is not set. Get a free key at "
#             "https://aistudio.google.com/apikey and add it to your .env file."
#         )

#     url = (
#         f"https://generativelanguage.googleapis.com/v1beta/models/"
#         f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
#     )
#     payload = {
#         "contents": [{"parts": [{"text": prompt}]}],
#         "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
#     }
#     resp = requests.post(url, json=payload, timeout=60)
#     if resp.status_code != 200:
#         raise LLMError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

#     data = resp.json()
#     try:
#         return data["candidates"][0]["content"]["parts"][0]["text"]
#     except (KeyError, IndexError) as e:
#         raise LLMError(f"Unexpected Gemini response shape: {data}") from e


# def _call_anthropic(prompt: str) -> str:
#     if not ANTHROPIC_API_KEY:
#         raise LLMError(
#             "ANTHROPIC_API_KEY is not set. Get one at "
#             "https://console.anthropic.com and add it to your .env file."
#         )

#     resp = requests.post(
#         "https://api.anthropic.com/v1/messages",
#         headers={
#             "x-api-key": ANTHROPIC_API_KEY,
#             "anthropic-version": "2023-06-01",
#             "content-type": "application/json",
#         },
#         json={
#             "model": ANTHROPIC_MODEL,
#             "max_tokens": 1500,
#             "messages": [{"role": "user", "content": prompt}],
#         },
#         timeout=60,
#     )
#     if resp.status_code != 200:
#         raise LLMError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")

#     data = resp.json()
#     try:
#         return "".join(
#             block["text"] for block in data["content"] if block.get("type") == "text"
#         )
#     except (KeyError, TypeError) as e:
#         raise LLMError(f"Unexpected Anthropic response shape: {data}") from e


# def _call_ollama(prompt: str) -> str:
#     resp = requests.post(
#         f"{OLLAMA_HOST}/api/generate",
#         json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"},
#         timeout=120,
#     )
#     if resp.status_code != 200:
#         raise LLMError(
#             f"Ollama error {resp.status_code}: {resp.text[:300]}. "
#             f"Is Ollama running locally with `ollama serve` and the model pulled?"
#         )
#     return resp.json().get("response", "")


# def _call_openrouter(prompt: str, max_retries: int = 3) -> str:
#     if not OPENROUTER_API_KEY:
#         raise LLMError(
#             "OPENROUTER_API_KEY is not set. Get a free key at "
#             "https://openrouter.ai/keys and add it to your .env file."
#         )

#     last_error = None

#     for attempt in range(max_retries + 1):
#         resp = requests.post(
#             "https://openrouter.ai/api/v1/chat/completions",
#             headers={
#                 "Authorization": f"Bearer {OPENROUTER_API_KEY}",
#                 "Content-Type": "application/json",
#             },
#             json={
#                 "model": OPENROUTER_MODEL,
#                 "messages": [{"role": "user", "content": prompt}],
#                 # temperature 0 = greedy decoding: the model picks the most
#                 # likely token every time, so identical inputs give identical
#                 # (or near-identical) scores instead of varying run-to-run.
#                 "temperature": 0,
#                 # seed helps providers that support it return reproducible
#                 # output; harmless for those that ignore it.
#                 "seed": 42,
#             },
#             timeout=60,
#         )

#         if resp.status_code == 200:
#             data = resp.json()
#             try:
#                 return data["choices"][0]["message"]["content"]
#             except (KeyError, IndexError) as e:
#                 raise LLMError(f"Unexpected OpenRouter response shape: {data}") from e

#         # Free-tier models get rate-limited under load (HTTP 429). OpenRouter
#         # tells us how long to wait in the error body or a Retry-After header
#         # -- honor that instead of failing the whole batch immediately.
#         if resp.status_code == 429 and attempt < max_retries:
#             wait_seconds = 5
#             try:
#                 body = resp.json()
#                 wait_seconds = float(
#                     body.get("error", {}).get("metadata", {}).get("retry_after_seconds", wait_seconds)
#                 )
#             except (ValueError, TypeError, json.JSONDecodeError):
#                 header_wait = resp.headers.get("Retry-After")
#                 if header_wait:
#                     try:
#                         wait_seconds = float(header_wait)
#                     except ValueError:
#                         pass
#             wait_seconds = max(wait_seconds, 1) + 1  # small buffer
#             last_error = f"OpenRouter rate-limited (429), retrying in {wait_seconds:.0f}s..."
#             print(f"  [retry] {last_error}")
#             time.sleep(wait_seconds)
#             continue

#         raise LLMError(f"OpenRouter API error {resp.status_code}: {resp.text[:300]}")

#     raise LLMError(
#         f"OpenRouter kept rate-limiting after {max_retries} retries. "
#         f"The free model '{OPENROUTER_MODEL}' is heavily oversubscribed right now -- "
#         f"try a different free model (e.g. 'qwen/qwen-2.5-72b-instruct:free' or "
#         f"'deepseek/deepseek-chat-v3-0324:free') in OPENROUTER_MODEL in your .env file."
#     )


# def call_llm(prompt: str) -> str:
#     """Send a prompt to whichever provider is configured, return raw text."""
#     if PROVIDER == "gemini":
#         return _call_gemini(prompt)
#     elif PROVIDER == "anthropic":
#         return _call_anthropic(prompt)
#     elif PROVIDER == "ollama":
#         return _call_ollama(prompt)
#     elif PROVIDER == "openrouter":
#         return _call_openrouter(prompt)
#     else:
#         raise LLMError(
#             f"Unknown LLM_PROVIDER '{PROVIDER}'. Use 'gemini', 'anthropic', 'openrouter', or 'ollama'."
#         )


# def _extract_json(text: str) -> str:
#     """
#     Pull the JSON object out of a raw LLM response that may be wrapped in
#     extra text. Free/instruct models sometimes ignore "return ONLY JSON" and
#     add a preamble ("User Safety: safe"), a trailing note, or markdown fences.
#     Rather than fail on any of that, we:
#       1. strip ```json ... ``` fences if present, then
#       2. slice out everything from the first '{' to the last '}'.
#     The outermost braces still correctly bound the whole JSON object even when
#     it contains nested objects, so this is safe for our single-object result.
#     """
#     cleaned = text.strip()

#     # 1. Strip markdown code fences if the model wrapped its output in them.
#     if cleaned.startswith("```"):
#         cleaned = cleaned.strip("`")
#         if cleaned.lower().startswith("json"):
#             cleaned = cleaned[4:]
#         cleaned = cleaned.strip()

#     # 2. Slice out the JSON object, ignoring any preamble/postamble text.
#     start = cleaned.find("{")
#     end = cleaned.rfind("}")
#     if start != -1 and end != -1 and end > start:
#         return cleaned[start:end + 1]

#     return cleaned


# def call_llm_json(prompt: str) -> dict:
#     """
#     Call the LLM and parse its response as JSON, tolerating any extra text the
#     model may have wrapped around the JSON object (preambles, notes, fences).
#     """
#     raw = call_llm(prompt)
#     candidate = _extract_json(raw)

#     try:
#         return json.loads(candidate)
#     except json.JSONDecodeError as e:
#         raise LLMError(f"Could not parse LLM response as JSON: {raw[:500]}") from e

"""
llm_client.py
Provider-agnostic wrapper around whichever LLM backend you configure.

Supported providers (set via LLM_PROVIDER in .env):
    "gemini"    -> Google Gemini API (default). Free tier, no credit card
                   needed. Get a key at https://aistudio.google.com/apikey
    "anthropic" -> Claude API. Pay-as-you-go, very cheap per resume, but
                   requires billing set up at https://console.anthropic.com
    "ollama"    -> Fully local, fully free, no API key, no internet needed.
                   Requires https://ollama.com installed + a model pulled,
                   e.g. `ollama pull llama3`. Cannot be deployed to a free
                   web host (needs the model running on the server itself),
                   so use this for local/offline demos only.

All three expose the same call_llm(prompt) -> str interface so the rest of
the app doesn't need to care which one is active.
"""

import os
import re
import json
import time
import requests


PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Verify the current model name at https://console.groq.com/docs/models --
# Groq's lineup changes; llama-3.3-70b-versatile is a strong general default.
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


class LLMError(Exception):
    """Raised when the configured LLM backend fails or is misconfigured."""
    pass


def _call_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise LLMError(
            "GEMINI_API_KEY is not set. Get a free key at "
            "https://aistudio.google.com/apikey and add it to your .env file."
        )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    resp = requests.post(url, json=payload, timeout=60)
    if resp.status_code != 200:
        raise LLMError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Unexpected Gemini response shape: {data}") from e


def _call_anthropic(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise LLMError(
            "ANTHROPIC_API_KEY is not set. Get one at "
            "https://console.anthropic.com and add it to your .env file."
        )

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise LLMError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    try:
        return "".join(
            block["text"] for block in data["content"] if block.get("type") == "text"
        )
    except (KeyError, TypeError) as e:
        raise LLMError(f"Unexpected Anthropic response shape: {data}") from e


def _call_ollama(prompt: str) -> str:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json"},
        timeout=120,
    )
    if resp.status_code != 200:
        raise LLMError(
            f"Ollama error {resp.status_code}: {resp.text[:300]}. "
            f"Is Ollama running locally with `ollama serve` and the model pulled?"
        )
    return resp.json().get("response", "")


def _call_openrouter(prompt: str, max_retries: int = 3) -> str:
    if not OPENROUTER_API_KEY:
        raise LLMError(
            "OPENROUTER_API_KEY is not set. Get a free key at "
            "https://openrouter.ai/keys and add it to your .env file."
        )

    last_error = None

    for attempt in range(max_retries + 1):
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                # temperature 0 = greedy decoding: the model picks the most
                # likely token every time, so identical inputs give identical
                # (or near-identical) scores instead of varying run-to-run.
                "temperature": 0,
                # seed helps providers that support it return reproducible
                # output; harmless for those that ignore it.
                "seed": 42,
            },
            timeout=60,
        )

        if resp.status_code == 200:
            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unexpected OpenRouter response shape: {data}") from e

        # Free-tier models get rate-limited under load (HTTP 429). OpenRouter
        # tells us how long to wait in the error body or a Retry-After header
        # -- honor that instead of failing the whole batch immediately.
        if resp.status_code == 429 and attempt < max_retries:
            wait_seconds = 5
            try:
                body = resp.json()
                wait_seconds = float(
                    body.get("error", {}).get("metadata", {}).get("retry_after_seconds", wait_seconds)
                )
            except (ValueError, TypeError, json.JSONDecodeError):
                header_wait = resp.headers.get("Retry-After")
                if header_wait:
                    try:
                        wait_seconds = float(header_wait)
                    except ValueError:
                        pass
            wait_seconds = max(wait_seconds, 1) + 1  # small buffer
            last_error = f"OpenRouter rate-limited (429), retrying in {wait_seconds:.0f}s..."
            print(f"  [retry] {last_error}")
            time.sleep(wait_seconds)
            continue

        raise LLMError(f"OpenRouter API error {resp.status_code}: {resp.text[:300]}")

    raise LLMError(
        f"OpenRouter kept rate-limiting after {max_retries} retries. "
        f"The free model '{OPENROUTER_MODEL}' is heavily oversubscribed right now -- "
        f"try a different free model (e.g. 'qwen/qwen-2.5-72b-instruct:free' or "
        f"'deepseek/deepseek-chat-v3-0324:free') in OPENROUTER_MODEL in your .env file."
    )


def _call_groq(prompt: str, max_retries: int = 3) -> str:
    """
    Groq — single direct provider (no OpenRouter-style routing), so scores are
    consistent, and its free tier has much higher rate limits than Gemini's.
    Uses the OpenAI-compatible chat-completions API.
    """
    if not GROQ_API_KEY:
        raise LLMError(
            "GROQ_API_KEY is not set. Get a free key at "
            "https://console.groq.com/keys and add it to your .env file."
        )

    last_error = None

    for attempt in range(max_retries + 1):
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                # temperature 0 = deterministic decoding for consistent scores.
                "temperature": 0,
                "seed": 42,
                # Ask Groq to return strict JSON so parsing is reliable.
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )

        if resp.status_code == 200:
            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                raise LLMError(f"Unexpected Groq response shape: {data}") from e

        # Honor rate-limit backoff (HTTP 429) instead of failing the batch.
        if resp.status_code == 429 and attempt < max_retries:
            wait_seconds = 5
            header_wait = resp.headers.get("Retry-After")
            if header_wait:
                try:
                    wait_seconds = float(header_wait)
                except ValueError:
                    pass
            wait_seconds = max(wait_seconds, 1) + 1
            last_error = f"Groq rate-limited (429), retrying in {wait_seconds:.0f}s..."
            print(f"  [retry] {last_error}")
            time.sleep(wait_seconds)
            continue

        raise LLMError(f"Groq API error {resp.status_code}: {resp.text[:300]}")

    raise LLMError(
        f"Groq kept rate-limiting after {max_retries} retries. "
        f"Try lowering SCREENING_MAX_WORKERS, or check your usage at "
        f"https://console.groq.com."
    )


def call_llm(prompt: str) -> str:
    """Send a prompt to whichever provider is configured, return raw text."""
    if PROVIDER == "gemini":
        return _call_gemini(prompt)
    elif PROVIDER == "anthropic":
        return _call_anthropic(prompt)
    elif PROVIDER == "ollama":
        return _call_ollama(prompt)
    elif PROVIDER == "openrouter":
        return _call_openrouter(prompt)
    elif PROVIDER == "groq":
        return _call_groq(prompt)
    else:
        raise LLMError(
            f"Unknown LLM_PROVIDER '{PROVIDER}'. Use 'gemini', 'anthropic', 'openrouter', 'groq', or 'ollama'."
        )


def _extract_json(text: str) -> str:
    """
    Pull the JSON object out of a raw LLM response that may be wrapped in
    extra text. Free/instruct models sometimes ignore "return ONLY JSON" and
    add a preamble ("User Safety: safe"), a trailing note, or markdown fences.
    Rather than fail on any of that, we:
      1. strip ```json ... ``` fences if present, then
      2. slice out everything from the first '{' to the last '}'.
    The outermost braces still correctly bound the whole JSON object even when
    it contains nested objects, so this is safe for our single-object result.
    """
    cleaned = text.strip()

    # 1. Strip markdown code fences if the model wrapped its output in them.
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    # 2. Slice out the JSON object, ignoring any preamble/postamble text.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start:end + 1]

    return cleaned


def call_llm_json(prompt: str) -> dict:
    """
    Call the LLM and parse its response as JSON, tolerating any extra text the
    model may have wrapped around the JSON object (preambles, notes, fences).
    """
    raw = call_llm(prompt)
    candidate = _extract_json(raw)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMError(f"Could not parse LLM response as JSON: {raw[:500]}") from e