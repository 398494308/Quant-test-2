#!/usr/bin/env python3
"""Shared OpenAI Responses API client for the aggressive research loop."""
import os
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = [
    REPO_ROOT / "config/research.env",
    REPO_ROOT.parent / "test1/freqtrade.service.env",
]


def _load_local_env_file():
    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        for line in env_file.read_text().splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_local_env_file()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
RAW_OPENAI_RESPONSES_URL = os.getenv("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "high")
OPENAI_CONNECT_TIMEOUT = float(os.getenv("OPENAI_CONNECT_TIMEOUT", "15"))
OPENAI_READ_TIMEOUT = float(os.getenv("OPENAI_READ_TIMEOUT", "75"))
OPENAI_RETRY_COUNT = int(os.getenv("OPENAI_RETRY_COUNT", "2"))
OPENAI_RETRY_BACKOFF = float(os.getenv("OPENAI_RETRY_BACKOFF", "6"))
OPENAI_TOTAL_BUDGET_SECONDS = float(os.getenv("OPENAI_TOTAL_BUDGET_SECONDS", "180"))
OPENAI_FALLBACK_REASONING = [
    item.strip()
    for item in os.getenv("OPENAI_FALLBACK_REASONING", "high,medium,low").split(",")
    if item.strip()
]


def _normalize_responses_url(url):
    normalized = url.rstrip("/")
    if normalized.endswith("/responses"):
        return normalized
    return f"{normalized}/responses"


OPENAI_RESPONSES_URL = _normalize_responses_url(RAW_OPENAI_RESPONSES_URL)


def _extract_output_text(payload):
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            text = content.get("text")
            if content.get("type") in {"output_text", "text"} and isinstance(text, str) and text.strip():
                return text
    raise ValueError(f"Responses API returned no text output: {payload}")


def _clean_code_block(text):
    if "```python" in text:
        return text.split("```python", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text


def _build_attempt_plan():
    plan = []
    for effort in OPENAI_FALLBACK_REASONING:
        if effort not in plan:
            plan.append(effort)
    if OPENAI_REASONING_EFFORT not in plan:
        plan.insert(0, OPENAI_REASONING_EFFORT)
    elif plan[0] != OPENAI_REASONING_EFFORT:
        plan.remove(OPENAI_REASONING_EFFORT)
        plan.insert(0, OPENAI_REASONING_EFFORT)
    return plan


def _is_retryable_error(exc):
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
    return False


def _request_once(session, headers, payload, timeout):
    resp = session.post(OPENAI_RESPONSES_URL, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    return _clean_code_block(_extract_output_text(resp.json()))


def _resolve_attempt_timeout(timeout, started_at):
    if timeout is not None:
        return timeout
    elapsed = time.time() - started_at
    remaining = OPENAI_TOTAL_BUDGET_SECONDS - elapsed
    if remaining <= OPENAI_CONNECT_TIMEOUT + 5:
        return None
    read_timeout = min(OPENAI_READ_TIMEOUT, max(10.0, remaining - OPENAI_CONNECT_TIMEOUT))
    return (OPENAI_CONNECT_TIMEOUT, read_timeout)


def generate_strategy_code(prompt, system_prompt, max_output_tokens=3200, timeout=None):
    if not OPENAI_API_KEY:
        raise RuntimeError(f"missing OPENAI_API_KEY in environment or config files: {ENV_FILES}")

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "x-api-key": OPENAI_API_KEY,
    }
    attempt_plan = _build_attempt_plan()
    errors = []
    session = requests.Session()
    started_at = time.time()

    for effort_index, effort in enumerate(attempt_plan):
        payload = {
            "model": OPENAI_MODEL,
            "reasoning": {"effort": effort},
            "instructions": system_prompt,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "max_output_tokens": max_output_tokens,
        }
        for retry_index in range(OPENAI_RETRY_COUNT + 1):
            try:
                request_timeout = _resolve_attempt_timeout(timeout, started_at)
                if request_timeout is None:
                    break
                return _request_once(session, headers, payload, request_timeout)
            except Exception as exc:
                errors.append(
                    f"attempt {len(errors) + 1} model={OPENAI_MODEL} effort={effort} "
                    f"retry={retry_index + 1}/{OPENAI_RETRY_COUNT + 1}: {type(exc).__name__}: {exc}"
                )
                if not _is_retryable_error(exc):
                    break
                is_last_retry = retry_index >= OPENAI_RETRY_COUNT
                is_last_effort = effort_index >= len(attempt_plan) - 1
                if is_last_retry and is_last_effort:
                    break
                if is_last_retry:
                    break
                time.sleep(OPENAI_RETRY_BACKOFF * (retry_index + 1))

    summary = "\n".join(errors[-8:]) if errors else "unknown provider failure"
    raise RuntimeError(f"strategy generation failed after retries:\n{summary}")
