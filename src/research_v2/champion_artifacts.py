#!/usr/bin/env python3
"""Champion 快照与通知辅助。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from research_v2.charting import PerformanceChartPaths
from research_v2.evaluation import EvaluationReport, normalize_test_metrics_payload
from research_v2.strategy_code import StrategyCandidate, source_hash, write_strategy_source


def champion_snapshot_stamp(timestamp_text: str) -> str:
    try:
        dt = datetime.fromisoformat(str(timestamp_text).replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        dt = datetime.now(UTC)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def safe_snapshot_slug(text: str, default: str = "champion") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip()).strip("_").lower()
    return (slug or default)[:48]


def archive_champion_snapshot(
    history_dir: Path,
    *,
    iteration_id: int,
    accepted_at: str,
    candidate: StrategyCandidate,
    source: str,
    report: EvaluationReport,
    test_metrics: dict[str, float] | None = None,
    chart_paths: PerformanceChartPaths | None = None,
) -> Path:
    code_hash = source_hash(source)
    snapshot_dir = history_dir / (
        f"{champion_snapshot_stamp(accepted_at)}"
        f"_i{iteration_id:04d}"
        f"_{safe_snapshot_slug(candidate.candidate_id, default='candidate')}"
        f"_{code_hash[:12]}"
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    strategy_path = snapshot_dir / "strategy_macd_aggressive.py"
    metadata_path = snapshot_dir / "metadata.json"
    write_strategy_source(strategy_path, source)

    validation_chart_name = ""
    selection_chart_name = ""
    if chart_paths is not None and chart_paths.validation_chart is not None and chart_paths.validation_chart.exists():
        validation_chart_name = "validation.png"
        shutil.copy2(chart_paths.validation_chart, snapshot_dir / validation_chart_name)
    if chart_paths is not None and chart_paths.selection_chart is not None and chart_paths.selection_chart.exists():
        selection_chart_name = "selection.png"
        shutil.copy2(chart_paths.selection_chart, snapshot_dir / selection_chart_name)

    metadata = {
        "iteration": iteration_id,
        "accepted_at": accepted_at,
        "candidate_id": candidate.candidate_id,
        "primary_direction": candidate.primary_direction,
        "hypothesis": candidate.hypothesis,
        "change_plan": candidate.change_plan,
        "change_tags": list(candidate.change_tags),
        "edited_regions": list(candidate.edited_regions),
        "code_hash": code_hash,
        "quality_score": float(report.metrics.get("quality_score", 0.0)),
        "promotion_score": float(report.metrics.get("promotion_score", 0.0)),
        "gate_reason": report.gate_reason,
        "validation_chart": validation_chart_name,
        "selection_chart": selection_chart_name,
        "test_metrics": normalize_test_metrics_payload(test_metrics),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return snapshot_dir


def build_chart_note(message: str) -> str:
    return (
        f"{message}\n"
        "图表：每张图蓝线=策略累计增长，橙线=BTC累计增长；左轴直接显示账户价值，右轴直接显示BTC价格；若底部还有第二张图，则为test期间同口径对比。"
    )
