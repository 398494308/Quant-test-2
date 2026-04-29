#!/usr/bin/env python3
"""主参考状态持久化与恢复辅助。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_v2.evaluation import EvaluationReport, normalize_test_metrics_payload
from research_v2.strategy_code import source_hash, write_strategy_source


def parse_state_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_saved_reference_state(best_state_file: Path) -> dict[str, Any]:
    if not best_state_file.exists():
        return {}
    try:
        payload = json.loads(best_state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    test_metrics = normalize_test_metrics_payload(
        payload.get("test_metrics") or payload.get("shadow_test_metrics")
    )
    if test_metrics:
        payload["test_metrics"] = test_metrics
    payload.pop("shadow_test_metrics", None)
    for key in ("reference", "champion", "working_base"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue
        nested_test_metrics = normalize_test_metrics_payload(
            nested.get("test_metrics") or nested.get("shadow_test_metrics")
        )
        if nested_test_metrics:
            nested["test_metrics"] = nested_test_metrics
        nested.pop("shadow_test_metrics", None)
    return payload


def recover_reference_stage_state(
    saved_state: dict[str, Any],
    journal_entries: list[dict[str, Any]],
    *,
    score_regime: str,
    reference_code_hash: str,
) -> tuple[str, int]:
    saved_stage_started_at = str(saved_state.get("reference_stage_started_at", "")).strip()
    try:
        saved_stage_iteration = int(saved_state.get("reference_stage_iteration", 0) or 0)
    except (TypeError, ValueError):
        saved_stage_iteration = 0
    if saved_stage_started_at or saved_stage_iteration > 0:
        return saved_stage_started_at, saved_stage_iteration

    current_regime_entries = [
        entry for entry in journal_entries
        if str(entry.get("score_regime", "")).strip() == score_regime
    ]
    for entry in reversed(current_regime_entries):
        if str(entry.get("outcome", "")).strip() != "accepted":
            continue
        if str(entry.get("code_hash", "")).strip() != reference_code_hash:
            continue
        return (
            str(entry.get("timestamp", "")).strip(),
            int(entry.get("iteration", 0) or 0),
        )

    saved_updated_at = str(saved_state.get("updated_at", "")).strip()
    saved_updated_dt = parse_state_timestamp(saved_updated_at)
    if saved_updated_dt is not None:
        for entry in current_regime_entries:
            entry_dt = parse_state_timestamp(entry.get("timestamp"))
            entry_iteration = int(entry.get("iteration", 0) or 0)
            if entry_dt is not None and entry_iteration > 0 and entry_dt >= saved_updated_dt:
                return saved_updated_at, entry_iteration
        max_iteration = max((int(entry.get("iteration", 0) or 0) for entry in current_regime_entries), default=0)
        return saved_updated_at, max_iteration + 1

    max_iteration = max((int(entry.get("iteration", 0) or 0) for entry in current_regime_entries), default=0)
    return "", (max_iteration + 1) if max_iteration > 0 else 0


def saved_report_payload(
    source: str,
    report: EvaluationReport,
    *,
    test_metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "code_hash": source_hash(source),
        "metrics": report.metrics,
        "gate_passed": report.gate_passed,
        "gate_reason": report.gate_reason,
        "test_metrics": normalize_test_metrics_payload(test_metrics),
    }


def report_from_saved_payload(payload: dict[str, Any]) -> EvaluationReport | None:
    if not isinstance(payload, dict):
        return None
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return None
    try:
        normalized_metrics = {str(key): float(value) for key, value in metrics.items()}
    except (TypeError, ValueError):
        return None
    return EvaluationReport(
        metrics=normalized_metrics,
        gate_passed=bool(payload.get("gate_passed", False)),
        gate_reason=str(payload.get("gate_reason", "")).strip() or "unknown",
        summary_text="",
        prompt_summary_text="",
    )


def reference_manifest_payload(
    source: str,
    report: EvaluationReport,
    *,
    score_regime: str,
    test_metrics: dict[str, float] | None = None,
    stage_started_at: str = "",
    stage_iteration: int = 0,
    suppress_initialize_saved_reference_discord_once: bool = False,
) -> dict[str, Any]:
    reference_payload = saved_report_payload(
        source,
        report,
        test_metrics=test_metrics,
    )
    reference_role = "champion" if report.gate_passed else "baseline"
    champion_payload = reference_payload if report.gate_passed else None
    return {
        "updated_at": datetime.now(UTC).isoformat(),
        "score_regime": score_regime,
        "reference_role": reference_role,
        "benchmark_role": reference_role,
        "reference_stage_started_at": stage_started_at,
        "reference_stage_iteration": stage_iteration,
        "code_hash": reference_payload["code_hash"],
        "reference": reference_payload,
        "champion": champion_payload,
        "metrics": reference_payload["metrics"],
        "gate_passed": reference_payload["gate_passed"],
        "gate_reason": reference_payload["gate_reason"],
        "test_metrics": reference_payload["test_metrics"],
        "suppress_initialize_saved_reference_discord_once": suppress_initialize_saved_reference_discord_once,
    }


def persist_best_state(
    best_state_file: Path,
    best_strategy_file: Path,
    champion_strategy_file: Path,
    source: str,
    report: EvaluationReport,
    *,
    score_regime: str,
    test_metrics: dict[str, float] | None = None,
    stage_started_at: str = "",
    stage_iteration: int = 0,
    suppress_initialize_saved_reference_discord_once: bool = False,
) -> None:
    best_state_file.parent.mkdir(parents=True, exist_ok=True)
    best_strategy_file.parent.mkdir(parents=True, exist_ok=True)
    payload = reference_manifest_payload(
        source,
        report,
        score_regime=score_regime,
        test_metrics=test_metrics,
        stage_started_at=stage_started_at,
        stage_iteration=stage_iteration,
        suppress_initialize_saved_reference_discord_once=suppress_initialize_saved_reference_discord_once,
    )
    best_state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    write_strategy_source(best_strategy_file, source)
    if report.gate_passed:
        champion_strategy_file.parent.mkdir(parents=True, exist_ok=True)
        write_strategy_source(champion_strategy_file, source)
    elif champion_strategy_file.exists():
        champion_strategy_file.unlink()
