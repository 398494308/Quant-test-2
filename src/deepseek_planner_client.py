#!/usr/bin/env python3
"""DeepSeek planner client for planner-only research experiments."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from codex_exec_client import (
    StrategyGenerationError,
    StrategyGenerationSessionError,
    StrategyGenerationTransientError,
)


ProgressCallback = Callable[[dict[str, Any]], None]

_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class DeepSeekPlannerConfig:
    enabled: bool
    api_key: str
    base_url: str
    model: str
    thinking_type: str
    reasoning_effort: str
    timeout_seconds: int
    max_history_messages: int


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def load_deepseek_planner_config() -> DeepSeekPlannerConfig:
    provider = os.getenv("MACD_V2_PLANNER_PROVIDER", "codex").strip().lower()
    return DeepSeekPlannerConfig(
        enabled=provider == "deepseek",
        api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        base_url=(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip() or "https://api.deepseek.com").rstrip("/"),
        model=os.getenv("DEEPSEEK_PLANNER_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro",
        thinking_type=os.getenv("DEEPSEEK_PLANNER_THINKING_TYPE", "enabled").strip() or "enabled",
        reasoning_effort=os.getenv("DEEPSEEK_PLANNER_REASONING_EFFORT", "max").strip() or "max",
        timeout_seconds=max(1, int(os.getenv("DEEPSEEK_PLANNER_TIMEOUT_SECONDS", os.getenv("CODEX_TIMEOUT_SECONDS", "600")))),
        max_history_messages=max(4, int(os.getenv("DEEPSEEK_PLANNER_MAX_HISTORY_MESSAGES", "80"))),
    )


def _emit_progress(callback: ProgressCallback | None, **payload: Any) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        return


def _resolve_timeout_seconds(timeout: float | tuple[float, float] | None, default_seconds: int) -> int:
    if timeout is None:
        return default_seconds
    if isinstance(timeout, tuple):
        values = [float(value) for value in timeout if value is not None]
        return max(1, int(max(values, default=default_seconds)))
    return max(1, int(float(timeout)))


def _session_history_path(workspace_root: Path, session_id: str) -> Path:
    safe_id = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in session_id)
    return workspace_root / f".deepseek_planner_session_{safe_id}.json"


def _session_trace_path(workspace_root: Path, session_id: str) -> Path:
    safe_id = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in session_id)
    return workspace_root / f".deepseek_planner_trace_{safe_id}.jsonl"


def _load_history(path: Path) -> list[dict[str, str | Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(payload, list):
        return []
    messages: list[dict[str, str | Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role and content:
            message: dict[str, str | Any] = {"role": role, "content": content}
            reasoning_content = str(item.get("reasoning_content", "")).strip()
            if reasoning_content:
                message["reasoning_content"] = reasoning_content
            messages.append(message)
    return messages


def _persist_history(path: Path, messages: list[dict[str, str | Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2))
    temp_path.replace(path)


def _append_trace(
    path: Path,
    *,
    session_id: str,
    resumed: bool,
    model: str,
    thinking_type: str,
    reasoning_effort: str,
    prompt: str,
    assistant_content: str,
    assistant_reasoning_content: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "resumed": resumed,
        "model": model,
        "thinking_type": thinking_type,
        "reasoning_effort": reasoning_effort,
        "prompt": prompt,
        "assistant_content": assistant_content,
        "assistant_reasoning_content": assistant_reasoning_content,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False))
        handle.write("\n")


def _trim_history(messages: list[dict[str, str | Any]], max_history_messages: int) -> list[dict[str, str | Any]]:
    if len(messages) <= 1:
        return messages
    system_message = messages[0]
    remainder = messages[1:]
    if len(remainder) <= max_history_messages:
        return [system_message, *remainder]
    return [system_message, *remainder[-max_history_messages:]]


def _normalize_messages(
    *,
    messages: list[dict[str, str | Any]],
    system_prompt: str,
    prompt: str,
    max_history_messages: int,
) -> list[dict[str, str | Any]]:
    system_text = system_prompt.strip()
    prompt_text = prompt.strip()
    normalized = list(messages)
    if normalized and normalized[0].get("role") == "system":
        normalized[0] = {"role": "system", "content": system_text}
    else:
        normalized.insert(0, {"role": "system", "content": system_text})
    normalized.append({"role": "user", "content": prompt_text})
    return _trim_history(normalized, max_history_messages=max_history_messages)


def _to_api_messages(messages: list[dict[str, str | Any]]) -> list[dict[str, str]]:
    api_messages: list[dict[str, str]] = []
    for item in messages:
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role and content:
            api_messages.append({"role": role, "content": content})
    return api_messages


def generate_text_response(
    *,
    prompt: str,
    system_prompt: str,
    workspace_root: Path,
    max_output_tokens: int = 3200,
    timeout: float | tuple[float, float] | None = None,
    config: DeepSeekPlannerConfig | None = None,
    progress_callback: ProgressCallback | None = None,
    session_id: str | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> str:
    client_config = config or load_deepseek_planner_config()
    if not client_config.enabled:
        raise StrategyGenerationError("DeepSeek planner provider is not enabled")
    if not client_config.api_key:
        raise StrategyGenerationError("missing DEEPSEEK_API_KEY")

    resumed = bool(session_id)
    resolved_session_id = str(session_id or f"deepseek-planner-{uuid.uuid4().hex[:12]}").strip()
    history_path = _session_history_path(workspace_root, resolved_session_id)
    trace_path = _session_trace_path(workspace_root, resolved_session_id)
    history = _load_history(history_path) if resumed else []
    if resumed and not history:
        raise StrategyGenerationSessionError("deepseek planner session history missing")

    messages = _normalize_messages(
        messages=history,
        system_prompt=system_prompt,
        prompt=prompt,
        max_history_messages=client_config.max_history_messages,
    )
    payload: dict[str, Any] = {
        "model": client_config.model,
        "messages": _to_api_messages(messages),
        "thinking": {"type": client_config.thinking_type},
        "reasoning_effort": client_config.reasoning_effort,
        "stream": False,
    }
    if max_output_tokens > 0:
        payload["max_tokens"] = int(max_output_tokens)

    _emit_progress(
        progress_callback,
        event="started",
        thread_id=resolved_session_id,
        model=client_config.model,
        reasoning_effort=client_config.reasoning_effort,
    )

    timeout_seconds = _resolve_timeout_seconds(timeout, default_seconds=client_config.timeout_seconds)
    try:
        response = requests.post(
            f"{client_config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {client_config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
        )
    except requests.Timeout as exc:
        raise StrategyGenerationTransientError(f"deepseek request timed out after {timeout_seconds}s") from exc
    except requests.RequestException as exc:
        raise StrategyGenerationTransientError(f"deepseek request failed: {exc}") from exc

    if response.status_code in _TRANSIENT_STATUS_CODES:
        raise StrategyGenerationTransientError(
            f"deepseek api transient failure ({response.status_code}): {response.text[:400]}"
        )
    if response.status_code >= 400:
        raise StrategyGenerationError(
            f"deepseek api failed ({response.status_code}): {response.text[:600]}"
        )

    try:
        payload_json = response.json()
    except json.JSONDecodeError as exc:
        raise StrategyGenerationError(
            f"deepseek api returned invalid JSON: {response.text[:600]}"
        ) from exc

    try:
        assistant_message = payload_json["choices"][0]["message"]
        raw_text = str(assistant_message.get("content", "")).strip()
        reasoning_text = str(assistant_message.get("reasoning_content", "")).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise StrategyGenerationError(
            f"deepseek api returned an unexpected payload: {json.dumps(payload_json, ensure_ascii=False)[:600]}"
        ) from exc
    if not raw_text:
        raise StrategyGenerationError("deepseek api returned an empty assistant content")

    assistant_record: dict[str, str | Any] = {"role": "assistant", "content": raw_text}
    if reasoning_text:
        assistant_record["reasoning_content"] = reasoning_text
    messages.append(assistant_record)
    _persist_history(
        history_path,
        _trim_history(messages, max_history_messages=client_config.max_history_messages),
    )
    _append_trace(
        trace_path,
        session_id=resolved_session_id,
        resumed=resumed,
        model=client_config.model,
        thinking_type=client_config.thinking_type,
        reasoning_effort=client_config.reasoning_effort,
        prompt=str(messages[-2].get("content", "")).strip(),
        assistant_content=raw_text,
        assistant_reasoning_content=reasoning_text,
    )

    if response_metadata is not None:
        response_metadata.update(
            {
                "session_id": resolved_session_id,
                "thread_id": resolved_session_id,
                "resumed": resumed,
                "provider": "deepseek",
                "model": client_config.model,
                "reasoning_effort": client_config.reasoning_effort,
                "thinking_type": client_config.thinking_type,
                "trace_path": str(trace_path),
                "reasoning_chars": len(reasoning_text),
            }
        )

    _emit_progress(
        progress_callback,
        event="completed",
        thread_id=resolved_session_id,
        model=client_config.model,
        reasoning_effort=client_config.reasoning_effort,
    )
    return raw_text
