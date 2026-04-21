#!/usr/bin/env python3
"""Local Codex CLI client for strategy generation."""
from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class StrategyGenerationError(RuntimeError):
    """Base error for Codex-backed strategy generation failures."""


class StrategyGenerationTransientError(StrategyGenerationError):
    """Raised when Codex appears temporarily unavailable or times out."""


class StrategyGenerationSessionError(StrategyGenerationError):
    """Raised when attempting to resume an invalid or expired Codex session."""


ProgressCallback = Callable[[dict[str, Any]], None]

_PROGRESS_POLL_SECONDS = 15.0
_TERM_GRACE_SECONDS = 3.0
_KILL_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class StrategyClientConfig:
    codex_bin: str
    model: str
    reasoning_effort: str
    approval_policy: str
    sandbox: str
    timeout_seconds: int
    use_ephemeral: bool

    def describe(self) -> str:
        return (
            f"runner={self.codex_bin} "
            f"model={self.model} "
            f"effort={self.reasoning_effort} "
            f"approval={self.approval_policy} "
            f"sandbox={self.sandbox} "
            f"timeout={self.timeout_seconds}s "
            f"ephemeral={int(self.use_ephemeral)}"
        )


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def load_strategy_client_config() -> StrategyClientConfig:
    return StrategyClientConfig(
        codex_bin=os.getenv("CODEX_BIN", "codex").strip() or "codex",
        model=os.getenv("CODEX_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.4")).strip() or "gpt-5.4",
        reasoning_effort=os.getenv("CODEX_REASONING_EFFORT", "medium").strip() or "medium",
        approval_policy=os.getenv("CODEX_APPROVAL_POLICY", "never").strip() or "never",
        sandbox=os.getenv("CODEX_SANDBOX", "read-only").strip() or "read-only",
        timeout_seconds=int(os.getenv("CODEX_TIMEOUT_SECONDS", "600")),
        use_ephemeral=_env_flag("CODEX_EPHEMERAL", True),
    )


def describe_client_config(config: StrategyClientConfig | None = None) -> str:
    return (config or load_strategy_client_config()).describe()


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


def _extract_schema(text_format: dict[str, Any] | None) -> dict[str, Any] | None:
    if not text_format:
        return None
    if text_format.get("type") == "json_schema":
        schema = text_format.get("schema")
        if isinstance(schema, dict):
            return schema
    return None


def _build_codex_prompt(
    prompt: str,
    system_prompt: str,
    *,
    inline_schema: dict[str, Any] | None = None,
) -> str:
    parts = []
    if system_prompt.strip():
        parts.append(system_prompt.strip())
    parts.append("严格遵守给定的输出 schema。不要输出 schema 之外的内容。")
    if inline_schema is not None:
        parts.append(
            "恢复 session 时 CLI 不会强制 schema；你必须自己确保最终输出是单个 JSON 对象，且严格匹配下列 schema：\n"
            + json.dumps(inline_schema, ensure_ascii=False, indent=2)
        )
    parts.append(prompt.strip())
    return "\n\n".join(parts)


def _tail(text: str, limit: int = 1200) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def _is_retryable_error(stderr: str) -> bool:
    haystack = stderr.lower()
    return any(
        needle in haystack
        for needle in (
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "connection refused",
            "connection error",
            "network error",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
        )
    )


def _is_session_resume_error(stderr: str) -> bool:
    haystack = stderr.lower()
    session_terms = ("resume", "session", "thread", "conversation")
    failure_terms = ("not found", "missing", "invalid", "expired", "unknown", "no such", "no rollout")
    return any(term in haystack for term in session_terms) and any(term in haystack for term in failure_terms)


def _read_output_message(path: Path, stdout: str) -> str:
    if path.exists():
        text = path.read_text().strip()
        if text:
            return text
    return stdout.strip()


def _parse_jsonl_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _emit_progress(callback: ProgressCallback | None, **payload: Any) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        # Heartbeat/reporting failures should not break strategy generation.
        return


def _resolve_timeout_seconds(
    timeout: float | tuple[float, float] | None,
    *,
    default_seconds: int,
) -> int:
    if timeout is None:
        return default_seconds
    if isinstance(timeout, tuple):
        values = [float(value) for value in timeout if value is not None]
        return max(1, int(max(values, default=default_seconds)))
    return max(1, int(float(timeout)))


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        try:
            process.terminate()
        except Exception:
            return
    try:
        process.wait(timeout=_TERM_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        try:
            process.kill()
        except Exception:
            return
    try:
        process.wait(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return


def generate_json_object(
    prompt: str,
    system_prompt: str,
    max_output_tokens: int = 3200,
    timeout: float | tuple[float, float] | None = None,
    config: StrategyClientConfig | None = None,
    text_format: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
    session_id: str | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client_config = config or load_strategy_client_config()
    if shutil.which(client_config.codex_bin) is None:
        raise StrategyGenerationError(f"missing Codex CLI binary: {client_config.codex_bin}")

    schema = _extract_schema(text_format)
    if schema is None:
        raise StrategyGenerationError("Codex CLI client requires a json_schema output format")

    command = [
        client_config.codex_bin,
        "-a",
        client_config.approval_policy,
        "-s",
        client_config.sandbox,
        "-C",
        str(Path.cwd()),
        "exec",
    ]
    if session_id:
        command.append("resume")
    command.extend(
        [
            "--json",
            "--skip-git-repo-check",
            "-m",
            client_config.model,
            "-c",
            f'model_reasoning_effort="{client_config.reasoning_effort}"',
        ]
    )
    if max_output_tokens > 0:
        command.extend(["-c", f"model_max_output_tokens={int(max_output_tokens)}"])
    if client_config.use_ephemeral and not session_id:
        command.append("--ephemeral")

    with tempfile.TemporaryDirectory(prefix="codex-exec-") as temp_dir:
        temp_root = Path(temp_dir)
        schema_path = temp_root / "schema.json"
        output_path = temp_root / "last_message.json"
        if session_id:
            command.extend(
                [
                    "--output-last-message",
                    str(output_path),
                    session_id,
                    "-",
                ]
            )
        else:
            schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2))
            command.extend(
                [
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    "-",
                ]
            )
        full_prompt = _build_codex_prompt(
            prompt,
            system_prompt,
            inline_schema=schema if session_id else None,
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        started_at = time.monotonic()
        timeout_seconds = _resolve_timeout_seconds(
            timeout,
            default_seconds=client_config.timeout_seconds,
        )
        deadline = started_at + float(timeout_seconds)
        input_text: str | None = full_prompt
        try:
            _emit_progress(
                progress_callback,
                event="started",
                pid=process.pid,
                timeout_seconds=timeout_seconds,
                model=client_config.model,
                reasoning_effort=client_config.reasoning_effort,
            )
            while True:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    _terminate_process_tree(process)
                    elapsed_seconds = int(max(0.0, time.monotonic() - started_at))
                    _emit_progress(
                        progress_callback,
                        event="timeout",
                        pid=process.pid,
                        elapsed_seconds=elapsed_seconds,
                        timeout_seconds=timeout_seconds,
                    )
                    raise StrategyGenerationTransientError(
                        f"codex exec timed out after {timeout_seconds}s"
                    )
                try:
                    stdout, stderr = process.communicate(
                        input=input_text,
                        timeout=min(_PROGRESS_POLL_SECONDS, remaining_seconds),
                    )
                    break
                except subprocess.TimeoutExpired:
                    input_text = None
                    _emit_progress(
                        progress_callback,
                        event="heartbeat",
                        pid=process.pid,
                        elapsed_seconds=int(max(0.0, time.monotonic() - started_at)),
                        timeout_seconds=timeout_seconds,
                        model=client_config.model,
                        reasoning_effort=client_config.reasoning_effort,
                    )
        except Exception:
            _terminate_process_tree(process)
            raise

        events = _parse_jsonl_events(stdout)
        thread_id = next(
            (
                str(event.get("thread_id", "")).strip()
                for event in events
                if str(event.get("type", "")).strip() == "thread.started"
                and str(event.get("thread_id", "")).strip()
            ),
            "",
        )
        if response_metadata is not None:
            response_metadata.clear()
            response_metadata.update(
                {
                    "thread_id": thread_id,
                    "session_id": thread_id or (session_id or ""),
                    "resumed": bool(session_id),
                    "events": events,
                }
            )
        if thread_id:
            _emit_progress(
                progress_callback,
                event="thread_started",
                pid=process.pid,
                thread_id=thread_id,
                resumed=bool(session_id),
            )

        _emit_progress(
            progress_callback,
            event="completed",
            pid=process.pid,
            elapsed_seconds=int(max(0.0, time.monotonic() - started_at)),
            timeout_seconds=timeout_seconds,
            returncode=process.returncode,
        )

        stdout = stdout or ""
        stderr = stderr or ""
        if process.returncode != 0:
            message = (
                f"codex exec failed with exit code {process.returncode}: "
                f"{_tail(stderr or stdout or 'no output')}"
            )
            if session_id and _is_session_resume_error(stderr or stdout):
                raise StrategyGenerationSessionError(message)
            if _is_retryable_error(stderr or stdout):
                raise StrategyGenerationTransientError(message)
            raise StrategyGenerationError(message)

        raw_text = _read_output_message(output_path, stdout)
        if not raw_text:
            raise StrategyGenerationError("codex exec returned an empty final message")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise StrategyGenerationError(
                f"codex exec returned invalid JSON (line {exc.lineno}, column {exc.colno}): "
                f"{exc.msg}. Raw prefix: {raw_text[:400]!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise StrategyGenerationError("codex exec returned a non-object JSON payload")
        return payload
