#!/usr/bin/env python3
"""Shared OpenAI Responses API client for strategy optimization loops."""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILES = (
    REPO_ROOT / "config/research.env",
    REPO_ROOT.parent / "test1/freqtrade.service.env",
)

DEFAULT_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_CONNECT_TIMEOUT = 15.0
DEFAULT_READ_TIMEOUT = 75.0
DEFAULT_RETRY_COUNT = 2
DEFAULT_RETRY_BACKOFF = 6.0
DEFAULT_TOTAL_BUDGET_SECONDS = 180.0
DEFAULT_FALLBACK_REASONING = ("high", "low")
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _load_local_env_file() -> None:
    """Load KEY=VALUE pairs from local env files without overriding real env vars."""
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


class StrategyGenerationError(RuntimeError):
    """Base error for provider-backed strategy generation failures."""


class StrategyGenerationTransientError(StrategyGenerationError):
    """Raised when the provider appears reachable but too slow or temporarily unavailable."""


@dataclass(frozen=True)
class StrategyClientConfig:
    api_key: str
    responses_url: str
    model: str
    reasoning_effort: str
    connect_timeout: float
    read_timeout: float
    retry_count: int
    retry_backoff: float
    total_budget_seconds: float
    fallback_reasoning: tuple[str, ...]

    def build_attempt_plan(self) -> tuple[str, ...]:
        efforts: list[str] = []
        for candidate in (self.reasoning_effort, *self.fallback_reasoning):
            effort = candidate.strip()
            if effort and effort not in efforts:
                efforts.append(effort)
        return tuple(efforts or (DEFAULT_REASONING_EFFORT,))

    def describe(self) -> str:
        return (
            f"url={self.responses_url} "
            f"model={self.model} "
            f"effort={self.reasoning_effort} "
            f"retries={self.retry_count} "
            f"timeouts=({self.connect_timeout:.0f}s,{self.read_timeout:.0f}s) "
            f"budget={self.total_budget_seconds:.0f}s"
        )


def _normalize_responses_url(url: str) -> str:
    normalized = url.rstrip("/")
    if normalized.endswith("/responses"):
        return normalized
    return f"{normalized}/responses"


def load_strategy_client_config() -> StrategyClientConfig:
    _load_local_env_file()
    fallback_reasoning = tuple(
        item.strip()
        for item in os.getenv("OPENAI_FALLBACK_REASONING", ",".join(DEFAULT_FALLBACK_REASONING)).split(",")
        if item.strip()
    )
    return StrategyClientConfig(
        api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        responses_url=_normalize_responses_url(os.getenv("OPENAI_RESPONSES_URL", DEFAULT_RESPONSES_URL).strip()),
        model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip() or DEFAULT_REASONING_EFFORT,
        connect_timeout=float(os.getenv("OPENAI_CONNECT_TIMEOUT", str(DEFAULT_CONNECT_TIMEOUT))),
        read_timeout=float(os.getenv("OPENAI_READ_TIMEOUT", str(DEFAULT_READ_TIMEOUT))),
        retry_count=int(os.getenv("OPENAI_RETRY_COUNT", str(DEFAULT_RETRY_COUNT))),
        retry_backoff=float(os.getenv("OPENAI_RETRY_BACKOFF", str(DEFAULT_RETRY_BACKOFF))),
        total_budget_seconds=float(os.getenv("OPENAI_TOTAL_BUDGET_SECONDS", str(DEFAULT_TOTAL_BUDGET_SECONDS))),
        fallback_reasoning=fallback_reasoning or DEFAULT_FALLBACK_REASONING,
    )


def describe_client_config(config: StrategyClientConfig | None = None) -> str:
    return (config or load_strategy_client_config()).describe()


def _extract_output_text(payload: dict[str, Any]) -> str:
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

    for choice in payload.get("choices", []):
        content = choice.get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content

    raise ValueError(f"Responses API returned no text output: {payload}")


def _parse_sse_payload_bytes(body: bytes) -> dict[str, Any]:
    event_name = ""
    data_lines: list[bytes] = []
    last_payload: dict[str, Any] | None = None
    last_response: dict[str, Any] | None = None
    output_chunks: list[str] = []

    def flush_event() -> None:
        nonlocal event_name, data_lines, last_payload, last_response
        if not data_lines:
            return
        raw_data_bytes = b"\n".join(data_lines).strip()
        data_lines = []
        if not raw_data_bytes or raw_data_bytes == b"[DONE]":
            event_name = ""
            return
        raw_data = raw_data_bytes.decode("utf-8", errors="replace")
        parsed = None
        parse_error: Exception | None = None
        for candidate in (raw_data, "".join(raw_data.splitlines()).strip()):
            if not candidate:
                continue
            try:
                parsed = json.loads(candidate)
                break
            except Exception as exc:
                parse_error = exc
        if parsed is None:
            raise ValueError(f"Failed to parse SSE event {event_name!r}: {raw_data[:400]!r}") from parse_error
        if not isinstance(parsed, dict):
            event_name = ""
            return
        last_payload = parsed
        if event_name == "response.output_text.delta" and isinstance(parsed.get("delta"), str):
            output_chunks.append(parsed["delta"])
        response_payload = parsed.get("response")
        if isinstance(response_payload, dict):
            last_response = response_payload
        event_name = ""

    normalized = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    for raw_line in normalized.split(b"\n"):
        line = raw_line.strip(b"\r")
        if not line:
            # This proxy sometimes emits an empty line between `event:` and `data:`.
            # Only flush when we actually have event data buffered.
            if data_lines:
                flush_event()
            continue
        if line.startswith(b":"):
            continue
        if line.startswith(b"event:"):
            event_name = line.split(b":", 1)[1].decode("utf-8", errors="replace").strip()
            continue
        if line.startswith(b"data:"):
            data_lines.append(line.split(b":", 1)[1].lstrip())
            continue

    flush_event()

    if last_response:
        return last_response
    if last_payload:
        return last_payload
    if output_chunks:
        return {"output_text": "".join(output_chunks)}
    preview = body[:400].decode("utf-8", errors="replace")
    raise ValueError(f"Responses API returned unreadable SSE payload: {preview!r}")


def _parse_response_payload(response: requests.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    raw_body = response.content
    if "text/event-stream" in content_type:
        return _parse_sse_payload_bytes(raw_body)
    try:
        return response.json()
    except json.JSONDecodeError:
        text = raw_body.decode("utf-8", errors="replace").strip()
        if text.startswith("event:") or "\nevent:" in text or text.startswith("data:") or "\ndata:" in text:
            return _parse_sse_payload_bytes(raw_body)
        if text:
            return json.loads(text)
        raise


def _clean_code_block(text: str) -> str:
    if not text or not text.strip():
        return text
    if "```json" in text:
        return text.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```python" in text:
        return text.split("```python", 1)[1].split("```", 1)[0].strip()
    if "```" in text:
        return text.split("```", 1)[1].split("```", 1)[0].strip()
    return text.strip()


def build_json_text_format(
    *,
    schema: dict[str, Any] | None = None,
    schema_name: str = "response_payload",
    strict: bool = True,
) -> dict[str, Any]:
    if schema is None:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "name": schema_name,
        "schema": schema,
        "strict": strict,
    }


def _request_headers(config: StrategyClientConfig) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "x-api-key": config.api_key,
    }


def _build_payload(
    config: StrategyClientConfig,
    prompt: str,
    system_prompt: str,
    max_output_tokens: int,
    effort: str,
    text_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "model": config.model,
        "reasoning": {"effort": effort},
        "instructions": system_prompt,
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        "max_output_tokens": max_output_tokens,
        "stream": False,
    }
    if text_format is not None:
        payload["text"] = {"format": text_format}
    return payload


def _resolve_attempt_timeout(
    config: StrategyClientConfig,
    timeout: float | tuple[float, float] | None,
    started_at: float,
) -> float | tuple[float, float] | None:
    if timeout is not None:
        return timeout

    elapsed = time.time() - started_at
    remaining = config.total_budget_seconds - elapsed
    if remaining <= config.connect_timeout + 5:
        return None

    read_timeout = min(config.read_timeout, max(10.0, remaining - config.connect_timeout))
    return (config.connect_timeout, read_timeout)


def _format_http_error(exc: requests.HTTPError) -> str:
    response = exc.response
    if response is None:
        return str(exc)
    body = response.text.replace("\n", " ").strip()
    if len(body) > 300:
        body = body[:300] + "..."
    return f"HTTP {response.status_code}: {body}"


def _format_exception(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError):
        return _format_http_error(exc)
    return str(exc)


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    if isinstance(exc, ValueError):
        message = str(exc)
        return (
            "Responses API returned unreadable SSE payload" in message
            or "Failed to parse SSE event" in message
        )
    return False


def _request_once(
    session: requests.Session,
    config: StrategyClientConfig,
    payload: dict[str, Any],
    timeout: float | tuple[float, float],
) -> str:
    response = session.post(
        config.responses_url,
        headers=_request_headers(config),
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return _clean_code_block(_extract_output_text(_parse_response_payload(response)))


def _extract_json_candidate(raw_text: str) -> str:
    text = _clean_code_block(raw_text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    candidate = text[start : end + 1]
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return text
    return candidate


def _raise_generation_failure(errors: list[str], retryable_only: bool) -> None:
    summary = "\n".join(errors[-8:]) if errors else "unknown provider failure"
    message = f"strategy generation failed after retries:\n{summary}"
    if retryable_only:
        raise StrategyGenerationTransientError(message)
    raise StrategyGenerationError(message)


def generate_strategy_code(
    prompt: str,
    system_prompt: str,
    max_output_tokens: int = 3200,
    timeout: float | tuple[float, float] | None = None,
    config: StrategyClientConfig | None = None,
    text_format: dict[str, Any] | None = None,
) -> str:
    """Generate strategy content through a single OpenAI Responses API boundary."""
    client_config = config or load_strategy_client_config()
    if not client_config.api_key:
        raise StrategyGenerationError(
            "missing OPENAI_API_KEY in environment or env files: "
            + ", ".join(str(path) for path in ENV_FILES)
        )

    attempt_plan = client_config.build_attempt_plan()
    errors: list[str] = []
    retryable_only = True
    session = requests.Session()
    started_at = time.time()

    for effort_index, effort in enumerate(attempt_plan):
        payload = _build_payload(
            client_config,
            prompt,
            system_prompt,
            max_output_tokens,
            effort,
            text_format=text_format,
        )
        for retry_index in range(client_config.retry_count + 1):
            try:
                request_timeout = _resolve_attempt_timeout(client_config, timeout, started_at)
                if request_timeout is None:
                    break
                return _request_once(session, client_config, payload, request_timeout)
            except Exception as exc:
                is_retryable = _is_retryable_error(exc)
                retryable_only = retryable_only and is_retryable
                errors.append(
                    "attempt "
                    f"{len(errors) + 1} model={client_config.model} effort={effort} "
                    f"retry={retry_index + 1}/{client_config.retry_count + 1}: "
                    f"{type(exc).__name__}: {_format_exception(exc)}"
                )
                if not is_retryable:
                    break

                is_last_retry = retry_index >= client_config.retry_count
                is_last_effort = effort_index >= len(attempt_plan) - 1
                if is_last_retry and is_last_effort:
                    break
                if is_last_retry:
                    break

                time.sleep(client_config.retry_backoff * (retry_index + 1))

    _raise_generation_failure(errors, retryable_only and bool(errors))


def generate_json_object(
    prompt: str,
    system_prompt: str,
    max_output_tokens: int = 3200,
    timeout: float | tuple[float, float] | None = None,
    config: StrategyClientConfig | None = None,
    text_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_text = generate_strategy_code(
        prompt=prompt,
        system_prompt=system_prompt,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        config=config,
        text_format=text_format or build_json_text_format(),
    )
    json_text = _extract_json_candidate(raw_text)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        snippet = raw_text[:400].replace("\n", " ")
        raise StrategyGenerationError(
            "model returned invalid JSON "
            f"(line {exc.lineno}, column {exc.colno}): {exc.msg}. "
            f"Raw prefix: {snippet!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise StrategyGenerationError(f"model returned non-object JSON: {type(payload).__name__}")
    return payload


def _main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI strategy client smoke test")
    parser.add_argument("--show-config", action="store_true", help="print the active client config")
    parser.add_argument("--ping", action="store_true", help="run a minimal live request")
    parser.add_argument("--max-output-tokens", type=int, default=32)
    parser.add_argument("--read-timeout", type=float, default=40.0)
    args = parser.parse_args()

    config = load_strategy_client_config()
    if args.show_config or args.ping:
        print(describe_client_config(config))
    if not args.ping:
        return 0

    text = generate_strategy_code(
        prompt="Reply with exactly: pong",
        system_prompt="Output only the requested text.",
        max_output_tokens=args.max_output_tokens,
        timeout=(min(10.0, args.read_timeout), args.read_timeout),
        config=config,
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
