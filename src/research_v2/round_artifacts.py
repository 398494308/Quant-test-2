#!/usr/bin/env python3
"""每轮最小可复现归档。"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from research_v2.evaluation import normalize_test_metrics_payload
from research_v2.strategy_code import source_hash, write_strategy_source


ROUND_STRATEGY_FILE_NAME = "strategy_macd_aggressive.py"
ROUND_METADATA_FILE_NAME = "metadata.json"
ROUND_ARTIFACT_SCHEMA_VERSION = 2


def _safe_slug(text: Any, *, default: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(text or "").strip()).strip("_").lower()
    return slug or default


def _timestamp_stamp(timestamp_text: Any) -> str:
    normalized = str(timestamp_text or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def _relative_path(repo_root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except Exception:
        return str(path)


def _copy_jsonable_mapping(mapping: Mapping[str, Any] | None) -> dict[str, Any]:
    if not mapping:
        return {}
    return json.loads(json.dumps(dict(mapping), ensure_ascii=False, default=str))


def _metadata_path(round_dir: Path) -> Path:
    return round_dir / ROUND_METADATA_FILE_NAME


def load_round_artifact_metadata(round_dir: Path) -> dict[str, Any]:
    metadata_path = _metadata_path(round_dir)
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    test_metrics = normalize_test_metrics_payload(
        payload.get("test_metrics") or payload.get("shadow_test_metrics")
    )
    payload["test_metrics"] = test_metrics
    payload.pop("shadow_test_metrics", None)
    payload["test_evaluation"] = _copy_jsonable_mapping(payload.get("test_evaluation"))
    payload["plateau_probe"] = _copy_jsonable_mapping(payload.get("plateau_probe"))
    return payload


def write_round_artifact_metadata(round_dir: Path, metadata: Mapping[str, Any]) -> Path:
    metadata_path = _metadata_path(round_dir)
    payload = _copy_jsonable_mapping(metadata)
    payload["schema_version"] = max(
        ROUND_ARTIFACT_SCHEMA_VERSION,
        int(payload.get("schema_version", 0) or 0),
    )
    payload["test_metrics"] = normalize_test_metrics_payload(
        payload.get("test_metrics") if isinstance(payload, Mapping) else None
    )
    payload.pop("shadow_test_metrics", None)
    payload["test_evaluation"] = _copy_jsonable_mapping(payload.get("test_evaluation"))
    payload["plateau_probe"] = _copy_jsonable_mapping(payload.get("plateau_probe"))
    temp_path = metadata_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    temp_path.replace(metadata_path)
    return metadata_path


def update_round_artifact_test_payload(
    round_dir: Path,
    *,
    test_metrics: Mapping[str, Any] | None = None,
    test_evaluation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = load_round_artifact_metadata(round_dir)
    if not metadata:
        raise FileNotFoundError(f"missing round artifact metadata: {round_dir}")
    if test_metrics is not None:
        metadata["test_metrics"] = normalize_test_metrics_payload(test_metrics)
    if test_evaluation is not None:
        metadata["test_evaluation"] = _copy_jsonable_mapping(test_evaluation)
    write_round_artifact_metadata(round_dir, metadata)
    return metadata


def _source_pool_path(artifacts_root: Path, code_hash: str) -> Path:
    return artifacts_root / "sources" / code_hash[:2] / f"{code_hash}.py"


def _ensure_source_pool_snapshot(
    artifacts_root: Path,
    *,
    strategy_source: str,
    code_hash: str,
) -> Path:
    pool_path = _source_pool_path(artifacts_root, code_hash)
    if not pool_path.exists():
        pool_path.parent.mkdir(parents=True, exist_ok=True)
        write_strategy_source(pool_path, strategy_source)
    return pool_path


def _link_or_copy_file(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source_path, target_path)
    except OSError:
        shutil.copy2(source_path, target_path)


def _round_dir_path(artifacts_root: Path, entry: Mapping[str, Any], code_hash: str) -> Path:
    try:
        iteration = int(entry.get("iteration", 0) or 0)
    except (TypeError, ValueError):
        iteration = 0
    stamp = _timestamp_stamp(entry.get("timestamp"))
    candidate_slug = _safe_slug(entry.get("candidate_id"), default="candidate")
    outcome_slug = _safe_slug(entry.get("outcome"), default="entry")
    base_dir = artifacts_root / "rounds" / (
        f"{stamp}_i{iteration:04d}_{candidate_slug}_{outcome_slug}_{code_hash[:12]}"
    )
    if not base_dir.exists():
        return base_dir
    suffix = 2
    while True:
        candidate_dir = artifacts_root / "rounds" / (
            f"{stamp}_i{iteration:04d}_{candidate_slug}_{outcome_slug}_{code_hash[:12]}_{suffix}"
        )
        if not candidate_dir.exists():
            return candidate_dir
        suffix += 1


def _artifact_chart_refs(
    repo_root: Path,
    *,
    champion_snapshot_dir: Path | None,
    chart_paths: Mapping[str, Path | None] | None,
) -> dict[str, str]:
    payload: dict[str, str] = {}
    if champion_snapshot_dir is not None:
        payload["snapshot_dir"] = _relative_path(repo_root, champion_snapshot_dir)
        validation_snapshot = champion_snapshot_dir / "validation.png"
        selection_snapshot = champion_snapshot_dir / "selection.png"
        if validation_snapshot.exists():
            payload["snapshot_validation_chart"] = _relative_path(repo_root, validation_snapshot)
        if selection_snapshot.exists():
            payload["snapshot_selection_chart"] = _relative_path(repo_root, selection_snapshot)
    for key, value in (chart_paths or {}).items():
        if value is None:
            continue
        path = Path(value)
        if not path.exists():
            continue
        payload[key] = _relative_path(repo_root, path)
    return payload


def persist_round_artifact(
    artifacts_root: Path,
    *,
    repo_root: Path,
    entry: Mapping[str, Any],
    strategy_source: str,
    windows: Mapping[str, Any],
    gates: Mapping[str, Any],
    scoring: Mapping[str, Any],
    data_fingerprints: Mapping[str, Any],
    engine_fingerprints: Mapping[str, Any],
    test_metrics: Mapping[str, Any] | None = None,
    test_evaluation: Mapping[str, Any] | None = None,
    champion_snapshot_dir: Path | None = None,
    chart_paths: Mapping[str, Path | None] | None = None,
) -> Path:
    artifacts_root.mkdir(parents=True, exist_ok=True)
    code_hash = source_hash(strategy_source)
    source_pool_path = _ensure_source_pool_snapshot(
        artifacts_root,
        strategy_source=strategy_source,
        code_hash=code_hash,
    )

    round_dir = _round_dir_path(artifacts_root, entry, code_hash)
    round_dir.mkdir(parents=True, exist_ok=True)
    strategy_snapshot_path = round_dir / ROUND_STRATEGY_FILE_NAME
    _link_or_copy_file(source_pool_path, strategy_snapshot_path)

    metrics_payload = _copy_jsonable_mapping(entry.get("metrics") if isinstance(entry, Mapping) else {})
    score_components = {
        key: metrics_payload.get(key)
        for key in (
            "train_capture_score",
            "validation_capture_score",
            "capture_score",
            "train_timed_return_score",
            "validation_timed_return_score",
            "timed_return_score",
            "train_drawdown_risk_score",
            "validation_drawdown_risk_score",
            "drawdown_risk_score",
            "drawdown_penalty_score",
            "validation_path_return_pct",
            "selection_path_return_pct",
            "selection_max_drawdown",
            "worst_drawdown",
            "eval_sharpe_ratio",
            "validation_sharpe_ratio",
            "selection_sharpe_ratio",
            "total_trades",
            "segment_hit_rate",
        )
        if key in metrics_payload
    }
    metadata = {
        "schema_version": ROUND_ARTIFACT_SCHEMA_VERSION,
        "stored_at": datetime.now(UTC).isoformat(),
        "iteration": int(entry.get("iteration", 0) or 0),
        "timestamp": str(entry.get("timestamp", "")).strip(),
        "candidate_id": str(entry.get("candidate_id", "")).strip(),
        "outcome": str(entry.get("outcome", "")).strip(),
        "stop_stage": str(entry.get("stop_stage", "")).strip(),
        "score_regime": str(entry.get("score_regime", "")).strip(),
        "reference_role": str(entry.get("reference_role", "")).strip(),
        "primary_direction": str(entry.get("primary_direction", "")).strip(),
        "decision": {
            "gate_reason": str(entry.get("gate_reason", "")).strip(),
            "decision_reason": str(entry.get("decision_reason", "")).strip(),
            "note": str(entry.get("note", "")).strip(),
        },
        "summary_scores": {
            "promotion_score": entry.get("promotion_score"),
            "quality_score": entry.get("quality_score"),
            "promotion_delta": entry.get("promotion_delta"),
        },
        "strategy": {
            "code_hash": code_hash,
            "reference_code_hash": str(entry.get("reference_code_hash", "")).strip(),
            "strategy_snapshot": _relative_path(repo_root, strategy_snapshot_path),
            "source_pool_path": _relative_path(repo_root, source_pool_path),
        },
        "strategy_changes": {
            "change_tags": list(entry.get("change_tags", ()) or ()),
            "edited_regions": list(entry.get("edited_regions", ()) or ()),
            "system_changed_regions": list(entry.get("system_changed_regions", ()) or ()),
            "diff_summary": list(entry.get("diff_summary", ()) or ()),
        },
        "score_components": score_components,
        "metrics": metrics_payload,
        "test_metrics": normalize_test_metrics_payload(test_metrics),
        "test_evaluation": _copy_jsonable_mapping(test_evaluation),
        "plateau_probe": _copy_jsonable_mapping(entry.get("plateau_probe")),
        "evaluation_context": {
            "windows": _copy_jsonable_mapping(windows),
            "gates": _copy_jsonable_mapping(gates),
            "scoring": _copy_jsonable_mapping(scoring),
        },
        "reproducibility": {
            "data_fingerprints": _copy_jsonable_mapping(data_fingerprints),
            "engine_fingerprints": _copy_jsonable_mapping(engine_fingerprints),
        },
        "champion_artifacts": _artifact_chart_refs(
            repo_root,
            champion_snapshot_dir=champion_snapshot_dir,
            chart_paths=chart_paths,
        ),
    }
    write_round_artifact_metadata(round_dir, metadata)
    return round_dir
