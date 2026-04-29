#!/usr/bin/env python3
"""Reject 轮次的后台 test 回测。"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from typing import Any

import backtest_macd_aggressive as backtest_module

from research_v2.evaluation import summarize_test_result
from research_v2.round_artifacts import load_round_artifact_metadata
from research_v2.strategy_code import StrategySourceError, extract_exit_params, extract_params, load_strategy_source


def _strategy_module_from_path(strategy_path: Path):
    module_name = f"round_artifact_test_{strategy_path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, strategy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to build module spec for {strategy_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module_name, module


def run_round_artifact_test(repo_root_text: str, round_dir_text: str) -> dict[str, Any]:
    repo_root = Path(repo_root_text).resolve()
    round_dir = Path(round_dir_text).resolve()
    metadata = load_round_artifact_metadata(round_dir)
    if not metadata:
        raise FileNotFoundError(f"missing round artifact metadata: {round_dir}")

    strategy_payload = metadata.get("strategy") if isinstance(metadata.get("strategy"), dict) else {}
    strategy_snapshot_rel = str(strategy_payload.get("strategy_snapshot", "")).strip()
    source_pool_rel = str(strategy_payload.get("source_pool_path", "")).strip()
    strategy_path = repo_root / strategy_snapshot_rel if strategy_snapshot_rel else Path()
    if not strategy_path.exists() and source_pool_rel:
        strategy_path = repo_root / source_pool_rel
    if not strategy_path.exists():
        raise FileNotFoundError(f"missing strategy snapshot for round artifact: {round_dir}")

    source = load_strategy_source(strategy_path)
    strategy_params = extract_params(source)
    exit_params = dict(backtest_module.EXIT_PARAMS)
    try:
        exit_params.update(extract_exit_params(source))
    except StrategySourceError:
        pass

    windows_payload = metadata.get("evaluation_context") if isinstance(metadata.get("evaluation_context"), dict) else {}
    windows = windows_payload.get("windows") if isinstance(windows_payload.get("windows"), dict) else {}
    start_date = str(windows.get("test_start_date", "")).strip() or "2026-01-01"
    end_date = str(windows.get("test_end_date", "")).strip() or "2026-04-20"

    prepared_context = backtest_module.prepare_backtest_context(
        strategy_params,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        exit_params=exit_params,
    )
    module_name, strategy_module = _strategy_module_from_path(strategy_path)
    try:
        result = backtest_module.backtest_macd_aggressive(
            strategy_func=strategy_module.strategy,
            intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
            hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
            start_date=start_date,
            end_date=end_date,
            strategy_params=strategy_params,
            exit_params=exit_params,
            include_diagnostics=True,
            prepared_context=prepared_context,
        )
    finally:
        sys.modules.pop(module_name, None)

    return {
        "round_dir": str(round_dir),
        "candidate_id": str(metadata.get("candidate_id", "")).strip(),
        "iteration": int(metadata.get("iteration", 0) or 0),
        "test_metrics": summarize_test_result(result),
    }
