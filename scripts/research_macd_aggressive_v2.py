#!/usr/bin/env python3
"""激进版 MACD 研究器 v2。

目标：
1. 不再把研究器限制为纯参数搜索。
2. 每轮允许模型在受控边界内改写策略文件。
3. 把评估、记忆、代码校验拆开，避免主脚本继续膨胀。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import backtest_macd_aggressive as backtest_module
import strategy_macd_aggressive as strategy_module
from codex_exec_client import (
    StrategyGenerationTransientError,
    build_json_text_format,
    generate_json_object,
    load_strategy_client_config,
)
from research_v2.config import ResearchRuntimeConfig, load_research_runtime_config
from research_v2.charting import PerformanceChartPaths, charts_available, render_performance_chart
from research_v2.evaluation import (
    EvaluationReport,
    partial_eval_gate_snapshot,
    summarize_evaluation,
    summarize_hidden_test_result,
)
from research_v2.journal import (
    ORDINARY_REGION_FAMILIES,
    append_journal_archive,
    append_journal_entry,
    build_journal_prompt_summary,
    evaluate_candidate_exploration_guard,
    cluster_key_for_components,
    exploration_signature_for_candidate,
    has_recent_code_hash,
    load_journal_entries,
    region_families_for_regions,
    target_family_from_text,
    maybe_compact,
)
from research_v2.notifications import build_discord_summary_message, load_discord_config, send_discord_message
from research_v2.prompting import (
    EDITABLE_REGIONS,
    build_candidate_response_schema,
    build_strategy_exploration_repair_prompt,
    build_strategy_research_prompt,
    build_strategy_runtime_repair_prompt,
    build_strategy_system_prompt,
)
from research_v2.strategy_code import (
    REQUIRED_FUNCTIONS,
    StrategyCandidate,
    StrategyCoreFactor,
    StrategySourceError,
    build_diff_summary,
    build_strategy_complexity_delta,
    load_strategy_source,
    normalize_strategy_source,
    repair_missing_required_functions,
    source_hash,
    validate_editable_region_boundaries,
    validate_strategy_source,
    write_strategy_source,
)
from research_v2.windows import build_research_windows


# ==================== 全局状态 ====================


RUNTIME = load_research_runtime_config(REPO_ROOT)
WINDOWS = build_research_windows(RUNTIME.windows)
DISCORD_CONFIG = load_discord_config()
EVAL_WINDOW_COUNT = sum(1 for window in WINDOWS if window.group == "eval")
VALIDATION_WINDOW_COUNT = sum(1 for window in WINDOWS if window.group == "validation")
TEST_WINDOW_COUNT = sum(1 for window in WINDOWS if window.group == "test")
SCORE_REGIME = "trend_capture_v6"
MODEL_WORKSPACE_STRATEGY_PATH = Path("src/strategy_macd_aggressive.py")

best_source = ""
best_report: EvaluationReport | None = None
champion_report: EvaluationReport | None = None
iteration_counter = 0
reference_stage_started_at = ""
reference_stage_iteration = 0

logging.basicConfig(
    filename=RUNTIME.paths.log_file,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ==================== 日志与心跳 ====================


def log_info(message: str) -> None:
    print(message)
    logging.info(message)


def log_exception(message: str) -> None:
    print(message)
    logging.error("%s\n%s", message, traceback.format_exc())


def maybe_send_discord(message: str, *, context: str, attachments: list[Path] | None = None) -> None:
    if not DISCORD_CONFIG.enabled:
        return
    try:
        send_discord_message(message, DISCORD_CONFIG, attachments=attachments)
    except Exception as exc:
        log_info(f"Discord 发送失败({context}): {exc}")
        logging.exception("Discord 发送失败(%s)", context)


def write_heartbeat(status: str, **extra: Any) -> None:
    RUNTIME.paths.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "pid": os.getpid(),
        "iteration": iteration_counter,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    payload.update(extra)
    temp_path = RUNTIME.paths.heartbeat_file.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    temp_path.replace(RUNTIME.paths.heartbeat_file)


def _build_model_progress_callback(phase: str, *, repair_attempt: int | None = None):
    def callback(payload: dict[str, Any]) -> None:
        event = str(payload.get("event", "")).strip() or "heartbeat"
        timeout_seconds = int(payload.get("timeout_seconds", 0) or 0)
        elapsed_seconds = int(payload.get("elapsed_seconds", 0) or 0)

        message = f"iteration {iteration_counter} {phase}"
        if event == "started":
            message += " started"
        elif event == "heartbeat" and timeout_seconds > 0:
            message += f" waiting {elapsed_seconds}s/{timeout_seconds}s"
        elif event == "timeout" and timeout_seconds > 0:
            message += f" timeout {timeout_seconds}s"
        elif event == "completed":
            message += " completed"

        heartbeat_payload: dict[str, Any] = {
            "message": message,
            "phase": phase,
            "provider_pid": payload.get("pid"),
            "timeout_seconds": timeout_seconds,
            "elapsed_seconds": elapsed_seconds,
            "provider_model": payload.get("model"),
            "provider_reasoning_effort": payload.get("reasoning_effort"),
        }
        if repair_attempt is not None:
            heartbeat_payload["repair_attempt"] = repair_attempt

        write_heartbeat("model_waiting", **heartbeat_payload)

    return callback


# ==================== 模块热加载 ====================


def reload_strategy_module() -> None:
    global strategy_module
    importlib.invalidate_caches()
    importlib.reload(strategy_module)


def _model_client_config():
    return replace(load_strategy_client_config(), approval_policy="never", sandbox="danger-full-access")


def _cluster_lock_schedule() -> tuple[int, int, int]:
    return (
        RUNTIME.cluster_lock_rounds_stage1,
        RUNTIME.cluster_lock_rounds_stage2,
        RUNTIME.cluster_lock_rounds_stage3,
    )


def _load_saved_reference_state() -> dict[str, Any]:
    if not RUNTIME.paths.best_state_file.exists():
        return {}
    try:
        payload = json.loads(RUNTIME.paths.best_state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _reference_role() -> str:
    return "champion" if champion_report is not None else "baseline"


def _reference_benchmark_report() -> EvaluationReport | None:
    return champion_report or best_report


def _discord_data_range_text() -> str:
    return (
        f"train {RUNTIME.windows.development_start_date}~{RUNTIME.windows.development_end_date} / "
        f"val {RUNTIME.windows.validation_start_date}~{RUNTIME.windows.validation_end_date} / "
        f"test {RUNTIME.windows.test_start_date}~{RUNTIME.windows.test_end_date}"
    )


def _append_research_journal_entry(entry: dict[str, Any]) -> None:
    append_journal_entry(RUNTIME.paths.journal_file, entry)
    append_journal_archive(RUNTIME.paths.memory_dir, entry)


def _parse_state_timestamp(value: Any) -> datetime | None:
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


def _recover_reference_stage_state(
    saved_state: dict[str, Any],
    journal_entries: list[dict[str, Any]],
    *,
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
        if str(entry.get("score_regime", "")).strip() == SCORE_REGIME
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
    saved_updated_dt = _parse_state_timestamp(saved_updated_at)
    if saved_updated_dt is not None:
        for entry in current_regime_entries:
            entry_dt = _parse_state_timestamp(entry.get("timestamp"))
            entry_iteration = int(entry.get("iteration", 0) or 0)
            if entry_dt is not None and entry_iteration > 0 and entry_dt >= saved_updated_dt:
                return saved_updated_at, entry_iteration
        max_iteration = max((int(entry.get("iteration", 0) or 0) for entry in current_regime_entries), default=0)
        return saved_updated_at, max_iteration + 1

    max_iteration = max((int(entry.get("iteration", 0) or 0) for entry in current_regime_entries), default=0)
    return "", (max_iteration + 1) if max_iteration > 0 else 0


def _reference_manifest_payload(
    source: str,
    report: EvaluationReport,
    *,
    shadow_test_metrics: dict[str, float] | None = None,
    stage_started_at: str = "",
    stage_iteration: int = 0,
) -> dict[str, Any]:
    reference_payload = {
        "code_hash": source_hash(source),
        "metrics": report.metrics,
        "gate_passed": report.gate_passed,
        "gate_reason": report.gate_reason,
        "shadow_test_metrics": shadow_test_metrics or {},
    }
    return {
        "updated_at": datetime.now(UTC).isoformat(),
        "score_regime": SCORE_REGIME,
        "reference_role": _reference_role(),
        "reference_stage_started_at": stage_started_at,
        "reference_stage_iteration": stage_iteration,
        "code_hash": reference_payload["code_hash"],
        "reference": reference_payload,
        "champion": reference_payload if champion_report is not None else None,
        # Backward-compatible top-level fields for existing readers.
        "metrics": reference_payload["metrics"],
        "gate_passed": reference_payload["gate_passed"],
        "gate_reason": reference_payload["gate_reason"],
        "shadow_test_metrics": reference_payload["shadow_test_metrics"],
    }


@contextlib.contextmanager
def _temporary_cwd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


# ==================== 评估执行 ====================


class EarlyRejection(Exception):
    """前若干 eval 窗口的大趋势捕获太差，提前终止回测。"""


class CandidateRuntimeFailure(Exception):
    """候选在 smoke 或完整评估中运行失败。"""

    def __init__(self, stage: str, error: Exception):
        self.stage = stage
        self.error = error
        super().__init__(f"{stage}: {error}")


class CandidateRepairExhausted(Exception):
    """候选修复次数耗尽。"""

    def __init__(self, candidate: StrategyCandidate, errors: list[str]):
        self.candidate = candidate
        self.errors = errors
        super().__init__(errors[-1] if errors else "candidate repair exhausted")


class CandidateBehavioralNoop(Exception):
    """候选能运行，但 smoke 行为与当前参考完全一致。"""

    def __init__(self, candidate: StrategyCandidate, behavior_diff: dict[str, Any]):
        self.candidate = candidate
        self.behavior_diff = behavior_diff
        super().__init__("candidate smoke behavior is identical to current reference")


def select_smoke_windows(windows: list[Any], smoke_window_count: int) -> list[Any]:
    if smoke_window_count <= 0:
        return []
    eval_windows = [window for window in windows if window.group == "eval"]
    validation_windows = [window for window in windows if window.group == "validation"]
    candidates: list[Any] = []
    if eval_windows:
        candidates.append(eval_windows[0])
    if validation_windows:
        candidates.append(validation_windows[0])
    if eval_windows:
        if smoke_window_count <= 3:
            candidates.append(eval_windows[len(eval_windows) // 2])
        elif smoke_window_count == 4:
            candidates.append(eval_windows[len(eval_windows) // 2])
            if len(eval_windows) > 1:
                candidates.append(eval_windows[-1])
        elif validation_windows:
            candidates.append(eval_windows[len(eval_windows) // 3])
            candidates.append(eval_windows[len(eval_windows) // 2])
            if len(eval_windows) > 1:
                candidates.append(eval_windows[-1])
        else:
            candidates.append(eval_windows[len(eval_windows) // 4])
            candidates.append(eval_windows[len(eval_windows) // 2])
            candidates.append(eval_windows[(len(eval_windows) * 3) // 4])
            if len(eval_windows) > 1:
                candidates.append(eval_windows[-1])

    selected: list[Any] = []
    for window in candidates:
        if window not in selected:
            selected.append(window)
        if len(selected) >= smoke_window_count:
            break
    return selected[:smoke_window_count]


def _selected_smoke_windows() -> list[Any]:
    return select_smoke_windows(WINDOWS, RUNTIME.smoke_window_count)


def _prepare_backtest_context() -> dict[str, Any]:
    return backtest_module.prepare_backtest_context(
        strategy_module.PARAMS,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        exit_params=backtest_module.EXIT_PARAMS,
    )


def _evaluation_windows() -> list[Any]:
    return [window for window in WINDOWS if window.group == "eval"]


def _scored_windows() -> list[Any]:
    return [window for window in WINDOWS if window.group in {"eval", "validation"}]


def _selection_period_bounds(windows: list[Any]) -> tuple[str, str]:
    selection_windows = [window for window in windows if window.group in {"eval", "validation"}]
    if not selection_windows:
        raise ValueError("missing selection windows")
    start_date = min(window.start_date for window in selection_windows)
    end_date = max(window.end_date for window in selection_windows)
    return start_date, end_date


def _validation_window() -> Any:
    for window in WINDOWS:
        if window.group == "validation":
            return window
    raise ValueError("missing validation window")


def _test_window() -> Any:
    for window in WINDOWS:
        if window.group == "test":
            return window
    raise ValueError("missing test window")


def _run_base_backtests(
    allow_early_reject: bool = False,
    *,
    windows: list[Any] | None = None,
    include_diagnostics: bool = True,
    heartbeat_phase: str = "full_eval",
    prepared_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    eval_count = 0
    check_at = RUNTIME.early_reject_after_windows
    active_windows = windows or _scored_windows()
    runtime_context = prepared_context or _prepare_backtest_context()
    eval_start_date: str | None = None

    for index, window in enumerate(active_windows, start=1):
        write_heartbeat(
            "iteration_running",
            message=f"iteration {iteration_counter} {heartbeat_phase}",
            phase=heartbeat_phase,
            current_window=window.label,
            window_index=index,
            window_count=len(active_windows),
        )
        result = backtest_module.backtest_macd_aggressive(
            strategy_func=strategy_module.strategy,
            intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
            hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
            start_date=window.start_date,
            end_date=window.end_date,
            strategy_params=strategy_module.PARAMS,
            exit_params=backtest_module.EXIT_PARAMS,
            include_diagnostics=include_diagnostics,
            prepared_context=runtime_context,
        )
        results.append({"window": window, "result": result})

        if allow_early_reject and window.group == "eval":
            eval_count += 1
            if eval_start_date is None:
                eval_start_date = window.start_date
            if eval_count >= check_at and check_at > 0:
                snapshot_result = backtest_module.backtest_macd_aggressive(
                    strategy_func=strategy_module.strategy,
                    intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
                    hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
                    start_date=eval_start_date or window.start_date,
                    end_date=window.end_date,
                    strategy_params=strategy_module.PARAMS,
                    exit_params=backtest_module.EXIT_PARAMS,
                    include_diagnostics=True,
                    prepared_context=runtime_context,
                )
                snapshot = partial_eval_gate_snapshot(snapshot_result)
                if (
                    snapshot["segment_count"] >= float(RUNTIME.early_reject_min_segments)
                    and snapshot["trend_score"] < RUNTIME.early_reject_trend_score_threshold
                    and snapshot["hit_rate"] < RUNTIME.early_reject_hit_rate_threshold
                ):
                    raise EarlyRejection(
                        f"前{eval_count}个eval窗口趋势捕获分={snapshot['trend_score']:.2f}，"
                        f"命中率={snapshot['hit_rate']:.0%}，趋势段={int(snapshot['segment_count'])}，提前淘汰"
                    )
    return results


def _run_selection_period_backtest(
    prepared_context: dict[str, Any],
    *,
    include_diagnostics: bool = True,
    heartbeat_phase: str = "selection_period_eval",
) -> dict[str, Any]:
    start_date, end_date = _selection_period_bounds(WINDOWS)
    write_heartbeat(
        "iteration_running",
        message=f"iteration {iteration_counter} {heartbeat_phase}",
        phase=heartbeat_phase,
        current_window="train+val连续",
        window_index=len(_scored_windows()) + 1,
        window_count=len(_scored_windows()) + 1,
    )
    return backtest_module.backtest_macd_aggressive(
        strategy_func=strategy_module.strategy,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        start_date=start_date,
        end_date=end_date,
        strategy_params=strategy_module.PARAMS,
        exit_params=backtest_module.EXIT_PARAMS,
        include_diagnostics=include_diagnostics,
        prepared_context=prepared_context,
    )


def _run_hidden_test_backtest(
    prepared_context: dict[str, Any],
    *,
    include_diagnostics: bool = True,
    heartbeat_phase: str = "hidden_test_eval",
) -> dict[str, Any]:
    test_window = _test_window()
    write_heartbeat(
        "iteration_running",
        message=f"iteration {iteration_counter} {heartbeat_phase}",
        phase=heartbeat_phase,
        current_window="test连续",
        window_index=len(_scored_windows()) + 2,
        window_count=len(_scored_windows()) + 2,
    )
    return backtest_module.backtest_macd_aggressive(
        strategy_func=strategy_module.strategy,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        start_date=test_window.start_date,
        end_date=test_window.end_date,
        strategy_params=strategy_module.PARAMS,
        exit_params=backtest_module.EXIT_PARAMS,
        include_diagnostics=include_diagnostics,
        prepared_context=prepared_context,
    )


def _chart_output_dir() -> Path:
    path = RUNTIME.paths.repo_root / "reports" / "research_v2_charts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_chart_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _build_chart_note(message: str) -> str:
    return (
        f"{message}\n"
        "图表：上图蓝线=val期间策略累计增长，橙线=val期间BTC累计增长；中图红区=val期间策略相对自身峰值的回撤；若底部还有第三图，则为test期间策略与BTC累计增长对比。"
    )


def _generate_new_champion_charts(
    iteration_id: int,
    *,
    hidden_test_result: dict[str, Any] | None = None,
) -> PerformanceChartPaths:
    if not charts_available():
        log_info("跳过新 champion 图表：matplotlib 不可用")
        return PerformanceChartPaths(validation_chart=None, selection_chart=None)

    chart_dir = _chart_output_dir()
    validation_window = _validation_window()
    test_window = _test_window()
    prepared_context = _prepare_backtest_context()

    write_heartbeat(
        "new_champion_charting",
        message=f"iteration {iteration_id} charting validation",
        phase="new_champion_charting",
        current_window=validation_window.label,
        window_index=1,
        window_count=2,
    )
    validation_result = backtest_module.backtest_macd_aggressive(
        strategy_func=strategy_module.strategy,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        start_date=validation_window.start_date,
        end_date=validation_window.end_date,
        strategy_params=strategy_module.PARAMS,
        exit_params=backtest_module.EXIT_PARAMS,
        include_diagnostics=True,
        prepared_context=prepared_context,
    )
    selection_result = _run_selection_period_backtest(
        prepared_context,
        include_diagnostics=True,
        heartbeat_phase="new_champion_charting",
    )

    validation_chart = chart_dir / (
        f"new_champion_{iteration_id:04d}_validation_{validation_window.start_date}_{validation_window.end_date}.png"
    )
    selection_start, selection_end = _selection_period_bounds(WINDOWS)
    selection_chart = chart_dir / (
        f"new_champion_{iteration_id:04d}_selection_{selection_start}_{selection_end}.png"
    )

    validation_chart = render_performance_chart(
        daily_equity_curve=validation_result.get("daily_equity_curve", []),
        output_path=validation_chart,
        title=f"New Champion #{iteration_id} Validation",
        subtitle=f"{validation_window.start_date} to {validation_window.end_date}",
        secondary_daily_equity_curve=(hidden_test_result or {}).get("daily_equity_curve", []),
        secondary_title=f"New Champion #{iteration_id} Test",
        secondary_subtitle=f"{test_window.start_date} to {test_window.end_date}",
    )
    selection_chart = render_performance_chart(
        daily_equity_curve=selection_result.get("daily_equity_curve", []),
        output_path=selection_chart,
        title=f"New Champion #{iteration_id} Selection Period",
        subtitle=f"{selection_start} to {selection_end}",
    )

    if validation_chart is not None:
        _write_chart_copy(validation_chart, chart_dir / "latest_validation.png")
    if selection_chart is not None:
        _write_chart_copy(selection_chart, chart_dir / "latest_selection.png")

    return PerformanceChartPaths(
        validation_chart=validation_chart,
        selection_chart=selection_chart,
    )


def evaluate_current_strategy(allow_early_reject: bool = False) -> EvaluationReport:
    prepared_context = _prepare_backtest_context()
    base_results = _run_base_backtests(
        allow_early_reject=allow_early_reject,
        prepared_context=prepared_context,
    )
    validation_continuous_result = next(
        (item["result"] for item in base_results if item["window"].group == "validation"),
        None,
    )
    selection_period_result = _run_selection_period_backtest(prepared_context)
    return summarize_evaluation(
        base_results,
        RUNTIME.gates,
        selection_period_result=selection_period_result,
        validation_continuous_result=validation_continuous_result,
    )


def evaluate_hidden_test_metrics() -> dict[str, float]:
    return summarize_hidden_test_result(evaluate_hidden_test_result())


def evaluate_hidden_test_result() -> dict[str, Any]:
    prepared_context = _prepare_backtest_context()
    return _run_hidden_test_backtest(prepared_context, include_diagnostics=True)


def smoke_test_current_strategy() -> None:
    prepared_context = _prepare_backtest_context()
    _run_base_backtests(
        windows=_selected_smoke_windows(),
        include_diagnostics=False,
        heartbeat_phase="smoke_test",
        prepared_context=prepared_context,
    )


def _round_behavior_value(value: Any) -> float:
    try:
        return round(float(value), 8)
    except (TypeError, ValueError):
        return 0.0


def _signal_stats_fingerprint(signal_stats: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    rows: list[tuple[Any, ...]] = []
    for signal, payload in sorted(signal_stats.items()):
        if not isinstance(payload, dict):
            continue
        rows.append(
            (
                str(signal),
                int(payload.get("entries", 0) or 0),
                int(payload.get("closed_trades", 0) or 0),
                _round_behavior_value(payload.get("pnl_amount", 0.0)),
                _round_behavior_value(payload.get("win_rate", 0.0)),
            )
        )
    return tuple(rows)


def _trade_summary_fingerprint(trades: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    rows: list[tuple[Any, ...]] = []
    for trade in trades:
        rows.append(
            (
                str(trade.get("entry_signal", "")),
                str(trade.get("reason", "")),
                int(trade.get("hold_bars", 0) or 0),
                int(trade.get("pyramids_done", 0) or 0),
                _round_behavior_value(trade.get("pnl_pct", 0.0)),
                _round_behavior_value(trade.get("pnl_amount", 0.0)),
            )
        )
    return tuple(rows)


def _window_behavior_fingerprint(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "return": _round_behavior_value(result.get("return", 0.0)),
        "score": _round_behavior_value(result.get("score", 0.0)),
        "max_drawdown": _round_behavior_value(result.get("max_drawdown", 0.0)),
        "trades": int(result.get("trades", 0) or 0),
        "win_rate": _round_behavior_value(result.get("win_rate", 0.0)),
        "fee_drag_pct": _round_behavior_value(result.get("fee_drag_pct", 0.0)),
        "liquidations": int(result.get("liquidations", 0) or 0),
        "signal_stats": _signal_stats_fingerprint(result.get("signal_stats", {}) or {}),
        "trade_reason_stats": tuple(sorted((result.get("trade_reason_stats", {}) or {}).items())),
        "trades_detail": _trade_summary_fingerprint(result.get("trades_detail", []) or []),
    }


def _smoke_behavior_profile(*, heartbeat_phase: str) -> list[dict[str, Any]]:
    prepared_context = _prepare_backtest_context()
    results = _run_base_backtests(
        windows=_selected_smoke_windows(),
        include_diagnostics=True,
        heartbeat_phase=heartbeat_phase,
        prepared_context=prepared_context,
    )
    return [
        {
            "window": item["window"].label,
            "fingerprint": _window_behavior_fingerprint(item["result"]),
        }
        for item in results
    ]


def _behavior_profile_changed(
    base_profile: list[dict[str, Any]],
    candidate_profile: list[dict[str, Any]],
) -> bool:
    return base_profile != candidate_profile


def _behavior_profile_summary(profile: list[dict[str, Any]]) -> dict[str, Any]:
    trades = sum(int(item["fingerprint"].get("trades", 0) or 0) for item in profile)
    returns = [
        float(item["fingerprint"].get("return", 0.0) or 0.0)
        for item in profile
    ]
    signal_counts: dict[str, int] = {}
    for item in profile:
        for signal, entries, *_rest in item["fingerprint"].get("signal_stats", ()):
            signal_counts[str(signal)] = signal_counts.get(str(signal), 0) + int(entries)
    return {
        "window_count": len(profile),
        "total_trades": trades,
        "returns": returns,
        "signal_entries": signal_counts,
    }


def _behavior_diff_payload(
    base_profile: list[dict[str, Any]],
    candidate_profile: list[dict[str, Any]],
) -> dict[str, Any]:
    changed_windows = [
        candidate_item["window"]
        for base_item, candidate_item in zip(base_profile, candidate_profile)
        if base_item != candidate_item
    ]
    if len(base_profile) != len(candidate_profile):
        changed_windows.append("window_count_mismatch")
    return {
        "changed": _behavior_profile_changed(base_profile, candidate_profile),
        "changed_windows": changed_windows,
        "base": _behavior_profile_summary(base_profile),
        "candidate": _behavior_profile_summary(candidate_profile),
    }


def _format_signal_entries_summary(signal_entries: dict[str, Any]) -> str:
    if not signal_entries:
        return "-"
    parts = [
        f"{signal}:{int(count)}"
        for signal, count in sorted(signal_entries.items())
    ]
    return ", ".join(parts)


def _format_behavior_summary(summary: dict[str, Any]) -> str:
    returns = ", ".join(f"{float(value):.2f}%" for value in summary.get("returns", ()))
    if not returns:
        returns = "-"
    return (
        f"windows={int(summary.get('window_count', 0) or 0)}, "
        f"trades={int(summary.get('total_trades', 0) or 0)}, "
        f"returns=[{returns}], "
        f"signals={_format_signal_entries_summary(summary.get('signal_entries', {}) or {})}"
    )


def _recent_behavioral_noop_streak(entries: list[dict[str, Any]]) -> int:
    streak = 0
    for entry in reversed(entries):
        if str(entry.get("outcome", "")) == "behavioral_noop":
            streak += 1
            continue
        break
    return streak


def _behavioral_noop_block_info(
    candidate: StrategyCandidate,
    behavior_diff: dict[str, Any],
    *,
    journal_entries: list[dict[str, Any]],
    base_source: str | None = None,
) -> dict[str, Any]:
    smoke_windows = ", ".join(window.label for window in _selected_smoke_windows()) or "-"
    noop_streak = _recent_behavioral_noop_streak(journal_entries)
    cluster_key = cluster_key_for_components(
        candidate.closest_failed_cluster,
        candidate.change_tags,
    ) or str(candidate.closest_failed_cluster).strip() or "-"
    candidate_signature = exploration_signature_for_candidate(
        candidate,
        base_source=base_source,
        editable_regions=EDITABLE_REGIONS,
    )
    changed_regions = ", ".join(sorted(candidate_signature["changed_regions"])) or "-"
    ordinary_families = ", ".join(sorted(candidate_signature["ordinary_region_families"])) or "-"
    target_family = str(candidate_signature.get("target_family", "")).strip() or "-"
    current_locks: tuple[str, ...] = ()
    feedback_lines = [
        f"- smoke窗口: {smoke_windows}",
        (
            "- changed_windows: "
            + (", ".join(str(item) for item in behavior_diff.get("changed_windows", ())) or "无")
        ),
        f"- 当前候选归属簇: {cluster_key}",
        f"- 当前候选真实改动区域: {changed_regions}",
        f"- 当前候选普通 family: {ordinary_families}",
        f"- 当前候选目标侧: {target_family}",
        f"- 当前参考摘要: {_format_behavior_summary(behavior_diff.get('base', {}) or {})}",
        f"- 候选摘要: {_format_behavior_summary(behavior_diff.get('candidate', {}) or {})}",
        f"- 最近连续 behavioral_noop: {noop_streak}",
    ]
    if "strategy" not in candidate_signature["region_families"]:
        feedback_lines.append(
            "- 本轮没有改 `strategy()` 最终放行层；只改 helper 时，若现有 `strategy()` 本身进不到那条路径，真实成交集合大概率不会变。"
        )
    if not candidate_signature["ordinary_region_families"]:
        feedback_lines.append(
            "- 本轮真实 diff 只动了 `strategy()` / `PARAMS` 特殊区域，没有碰到普通 family；这类近邻阈值微调最容易继续落成 behavioral_noop。"
        )
    if target_family in {"long", "mixed"}:
        feedback_lines.append(
            "- 长侧先看 `long_outer_context_ok`；若你要补的是 turn / reclaim / early relay，但这里仍要求 `intraday_bull + hourly_bull + fourh_bull_base`，内层新增 long path 很可能仍是死分支。"
        )
        feedback_lines.append(
            "- 即使新增 long path 成立，也还要穿过 `long_final_veto_clear` 和 `_trend_followthrough_long()`；若只补 path，不处理最终 veto/followthrough，行为仍可能完全不变。"
        )
    if target_family in {"short", "mixed"}:
        feedback_lines.append(
            "- 空侧先看 `short_outer_context_ok`；若外层趋势准入不变，很多 short path / followthrough 微调也不会真正落到出单层。"
        )
        feedback_lines.append(
            "- 空侧最终还要穿过 `breakdown_ready + short_final_veto_clear + _trend_followthrough_short()`；只补局部 confirmation，未必会改变真实 short 集合。"
        )
    if noop_streak >= 2:
        if cluster_key != "-":
            current_locks = (f"{cluster_key}(本轮 behavioral_noop 后禁止沿用原叙事)",)
        feedback_lines.append(
            "- 最近已连续多次 behavior 无变化；这次必须明显加大步长：优先切不同方向簇，"
            "若留在同簇，允许直接覆盖 2-4 个普通 family，但仍必须围绕一个单一假设，不要只改 strategy/PARAMS 或单个细阈值。"
        )
        feedback_lines.append(
            "- 默认把上一版局部 hypothesis / change_plan 视为已被证伪，不要只换候选名、tag 或措辞。"
        )
    return {
        "block_kind": "behavioral_noop",
        "blocked_cluster": cluster_key,
        "blocked_reason": "smoke 行为指纹与当前主参考完全一致",
        "current_locks": current_locks,
        "feedback_note": "\n".join(feedback_lines),
    }


# ==================== 候选策略生成 ====================


def _candidate_from_payload(
    payload: dict[str, Any],
    *,
    workspace_strategy_file: Path,
    base_source: str,
) -> StrategyCandidate:
    core_factors: list[StrategyCoreFactor] = []
    for item in payload.get("core_factors", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        thesis = str(item.get("thesis", "")).strip()
        current_signal = str(item.get("current_signal", "")).strip()
        if not name or not thesis or not current_signal:
            continue
        core_factors.append(
            StrategyCoreFactor(
                name=name,
                thesis=thesis,
                current_signal=current_signal,
            )
        )
    if not workspace_strategy_file.exists():
        raise StrategySourceError(f"workspace strategy file missing: {workspace_strategy_file}")
    strategy_code = normalize_strategy_source(load_strategy_source(workspace_strategy_file))
    strategy_code, restored_functions = repair_missing_required_functions(
        base_source,
        strategy_code,
        EDITABLE_REGIONS,
    )
    if restored_functions:
        write_strategy_source(workspace_strategy_file, strategy_code)
    validate_strategy_source(
        strategy_code,
        base_source=base_source,
        factor_change_mode=RUNTIME.factor_change_mode,
    )
    validate_editable_region_boundaries(base_source, strategy_code, EDITABLE_REGIONS)
    candidate = StrategyCandidate(
        candidate_id=str(payload["candidate_id"]).strip() or f"candidate-{int(time.time())}",
        hypothesis=str(payload["hypothesis"]).strip(),
        change_plan=str(payload["change_plan"]).strip(),
        closest_failed_cluster=str(payload.get("closest_failed_cluster", "")).strip(),
        novelty_proof=str(payload.get("novelty_proof", "")).strip(),
        change_tags=tuple(str(item).strip() for item in payload["change_tags"] if str(item).strip()),
        edited_regions=tuple(str(item).strip() for item in payload["edited_regions"] if str(item).strip()),
        expected_effects=tuple(str(item).strip() for item in payload["expected_effects"] if str(item).strip()),
        core_factors=tuple(core_factors),
        strategy_code=strategy_code,
    )
    if not candidate.change_tags:
        raise StrategySourceError("candidate missing change_tags")
    if not candidate.edited_regions:
        raise StrategySourceError("candidate missing edited_regions")
    if len(candidate.edited_regions) > len(EDITABLE_REGIONS):
        raise StrategySourceError("candidate edited_regions exceeds editable region count")
    if len(set(candidate.edited_regions)) != len(candidate.edited_regions):
        raise StrategySourceError("candidate edited_regions contains duplicates")
    if not candidate.closest_failed_cluster:
        raise StrategySourceError("candidate missing closest_failed_cluster")
    if not candidate.novelty_proof:
        raise StrategySourceError("candidate missing novelty_proof")
    candidate_signature = exploration_signature_for_candidate(
        candidate,
        base_source=base_source,
        editable_regions=EDITABLE_REGIONS,
    )
    ordinary_region_families = sorted(candidate_signature["ordinary_region_families"])
    if not ordinary_region_families:
        raise StrategySourceError(
            "candidate must change at least 1 ordinary region family from "
            f"{', '.join(ORDINARY_REGION_FAMILIES)}; strategy/PARAMS alone are not enough"
        )
    return candidate


def _build_model_candidate(
    base_source: str,
    journal_entries: list[dict[str, Any]],
    *,
    workspace_root: Path,
) -> StrategyCandidate:
    report = best_report
    if report is None:
        raise StrategySourceError("reference report is not initialized")
    benchmark_report = _reference_benchmark_report()
    if benchmark_report is None:
        raise StrategySourceError("reference benchmark is not initialized")

    prompt = build_strategy_research_prompt(
        evaluation_summary=report.prompt_summary_text,
        journal_summary=build_journal_prompt_summary(
            journal_entries,
            limit=RUNTIME.max_recent_journal_entries,
            journal_path=RUNTIME.paths.journal_file,
            current_score_regime=SCORE_REGIME,
            current_iteration=iteration_counter,
            active_stage_started_at=reference_stage_started_at,
            active_stage_iteration=reference_stage_iteration,
            reference_role=_reference_role(),
            memory_root=RUNTIME.paths.memory_dir,
        ),
        previous_best_score=benchmark_report.metrics["promotion_score"],
        reference_metrics=benchmark_report.metrics,
        score_regime=SCORE_REGIME,
        promotion_min_delta=RUNTIME.promotion_min_delta,
        factor_change_mode=RUNTIME.factor_change_mode,
    )
    with _temporary_cwd(workspace_root):
        payload = generate_json_object(
            prompt=prompt,
            system_prompt=build_strategy_system_prompt(
                factor_change_mode=RUNTIME.factor_change_mode,
            ),
            max_output_tokens=RUNTIME.prompt_max_output_tokens,
            config=_model_client_config(),
            text_format=build_json_text_format(
                schema=build_candidate_response_schema(),
                schema_name="macd_aggressive_strategy_candidate_v2",
                strict=True,
            ),
            progress_callback=_build_model_progress_callback("model_generate"),
        )
    return _candidate_from_payload(
        payload,
        workspace_strategy_file=workspace_root / MODEL_WORKSPACE_STRATEGY_PATH,
        base_source=base_source,
    )


def _repair_model_candidate(
    *,
    base_source: str,
    failed_candidate: StrategyCandidate,
    error_message: str,
    repair_attempt: int,
    workspace_root: Path,
) -> StrategyCandidate:
    prompt = build_strategy_runtime_repair_prompt(
        candidate_id=failed_candidate.candidate_id,
        hypothesis=failed_candidate.hypothesis,
        change_plan=failed_candidate.change_plan,
        change_tags=failed_candidate.change_tags,
        edited_regions=failed_candidate.edited_regions,
        expected_effects=failed_candidate.expected_effects,
        closest_failed_cluster=failed_candidate.closest_failed_cluster,
        novelty_proof=failed_candidate.novelty_proof,
        error_message=error_message,
        repair_attempt=repair_attempt,
    )
    with _temporary_cwd(workspace_root):
        payload = generate_json_object(
            prompt=prompt,
            system_prompt=build_strategy_system_prompt(
                factor_change_mode=RUNTIME.factor_change_mode,
            ),
            max_output_tokens=RUNTIME.prompt_max_output_tokens,
            config=_model_client_config(),
            text_format=build_json_text_format(
                schema=build_candidate_response_schema(),
                schema_name="macd_aggressive_strategy_candidate_repair_v2",
                strict=True,
            ),
            progress_callback=_build_model_progress_callback(
                "model_repair",
                repair_attempt=repair_attempt,
            ),
        )
    repaired = _candidate_from_payload(
        payload,
        workspace_strategy_file=workspace_root / MODEL_WORKSPACE_STRATEGY_PATH,
        base_source=base_source,
    )
    if not repaired.candidate_id:
        repaired = StrategyCandidate(
            candidate_id=failed_candidate.candidate_id,
            hypothesis=repaired.hypothesis,
            change_plan=repaired.change_plan,
            closest_failed_cluster=repaired.closest_failed_cluster,
            novelty_proof=repaired.novelty_proof,
            change_tags=repaired.change_tags,
            edited_regions=repaired.edited_regions,
            expected_effects=repaired.expected_effects,
            core_factors=repaired.core_factors,
            strategy_code=repaired.strategy_code,
    )
    return repaired


def _regenerate_model_candidate(
    *,
    base_source: str,
    failed_candidate: StrategyCandidate,
    block_info: dict[str, Any],
    regeneration_attempt: int,
    workspace_root: Path,
) -> StrategyCandidate:
    prompt = build_strategy_exploration_repair_prompt(
        candidate_id=failed_candidate.candidate_id,
        hypothesis=failed_candidate.hypothesis,
        change_plan=failed_candidate.change_plan,
        change_tags=failed_candidate.change_tags,
        edited_regions=failed_candidate.edited_regions,
        expected_effects=failed_candidate.expected_effects,
        closest_failed_cluster=failed_candidate.closest_failed_cluster,
        novelty_proof=failed_candidate.novelty_proof,
        block_kind=str(block_info.get("block_kind", "")).strip() or "same_cluster",
        blocked_cluster=str(block_info.get("blocked_cluster", "")).strip() or "-",
        blocked_reason=str(block_info.get("blocked_reason", "")).strip() or "系统拒收",
        locked_clusters=tuple(block_info.get("current_locks", ()) or ()),
        regeneration_attempt=regeneration_attempt,
        feedback_note=str(block_info.get("feedback_note", "")).strip(),
    )
    with _temporary_cwd(workspace_root):
        payload = generate_json_object(
            prompt=prompt,
            system_prompt=build_strategy_system_prompt(
                factor_change_mode=RUNTIME.factor_change_mode,
            ),
            max_output_tokens=RUNTIME.prompt_max_output_tokens,
            config=_model_client_config(),
            text_format=build_json_text_format(
                schema=build_candidate_response_schema(),
                schema_name="macd_aggressive_strategy_candidate_regeneration_v2",
                strict=True,
            ),
            progress_callback=_build_model_progress_callback(
                "model_regenerate",
                repair_attempt=regeneration_attempt,
            ),
        )
    return _candidate_from_payload(
        payload,
        workspace_strategy_file=workspace_root / MODEL_WORKSPACE_STRATEGY_PATH,
        base_source=base_source,
    )


def build_strategy_candidate(base_source: str, *, workspace_root: Path) -> StrategyCandidate:
    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    try:
        return _build_model_candidate(base_source, journal_entries, workspace_root=workspace_root)
    except StrategyGenerationTransientError:
        raise
    except Exception:
        raise


# ==================== 主参考状态管理 ====================


def _persist_best_state(
    source: str,
    report: EvaluationReport,
    *,
    shadow_test_metrics: dict[str, float] | None = None,
    stage_started_at: str = "",
    stage_iteration: int = 0,
) -> None:
    RUNTIME.paths.best_state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = _reference_manifest_payload(
        source,
        report,
        shadow_test_metrics=shadow_test_metrics,
        stage_started_at=stage_started_at,
        stage_iteration=stage_iteration,
    )
    RUNTIME.paths.best_state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    write_strategy_source(RUNTIME.paths.best_strategy_file, source)


def initialize_best_state(force_rebuild: bool = False) -> None:
    global best_source, best_report, champion_report, reference_stage_started_at, reference_stage_iteration

    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    saved_state = _load_saved_reference_state()
    saved_regime = str(saved_state.get("score_regime", "")).strip()
    saved_reference = saved_state.get("reference")
    can_load_saved_reference = (
        not force_rebuild
        and saved_regime == SCORE_REGIME
        and isinstance(saved_reference, dict)
        and RUNTIME.paths.best_strategy_file.exists()
    )

    if can_load_saved_reference:
        candidate_saved_source = load_strategy_source(RUNTIME.paths.best_strategy_file)
        try:
            validate_strategy_source(candidate_saved_source)
        except StrategySourceError as exc:
            log_info(f"已保存主参考无效，改为从当前策略文件重建: {exc}")
        else:
            best_source = candidate_saved_source
            write_strategy_source(RUNTIME.paths.strategy_file, best_source)
            reload_strategy_module()
            best_report = evaluate_current_strategy()
            champion_report = (
                best_report
                if best_report.gate_passed and str(saved_state.get("reference_role", "")).strip() == "champion"
                else None
            )
            reference_stage_started_at, reference_stage_iteration = _recover_reference_stage_state(
                saved_state,
                journal_entries,
                reference_code_hash=source_hash(best_source),
            )
            _persist_best_state(
                best_source,
                best_report,
                stage_started_at=reference_stage_started_at,
                stage_iteration=reference_stage_iteration,
            )
            log_info(
                "已加载已保存主参考: "
                f"role={_reference_role()}, "
                f"quality={best_report.metrics['quality_score']:.2f}, "
                f"promotion={best_report.metrics['promotion_score']:.2f}, "
                f"gate={best_report.gate_reason}"
            )
            write_heartbeat(
                "initialized",
                message="loaded saved reference",
                reference_role=_reference_role(),
                promotion=best_report.metrics["promotion_score"],
                quality=best_report.metrics["quality_score"],
            )
            maybe_send_discord(
                build_discord_summary_message(
                    title=f"📌 研究器 v2 已加载{_reference_role()}参考",
                    report=best_report,
                    eval_window_count=EVAL_WINDOW_COUNT,
                    validation_window_count=VALIDATION_WINDOW_COUNT,
                    test_window_count=TEST_WINDOW_COUNT,
                    data_range_text=_discord_data_range_text(),
                    factor_change_mode=RUNTIME.factor_change_mode,
                ),
                context="initialize_saved_reference",
            )
            return

    best_source = load_strategy_source(RUNTIME.paths.strategy_file)
    validate_strategy_source(best_source)
    reload_strategy_module()
    best_report = evaluate_current_strategy()
    champion_report = best_report if best_report.gate_passed else None
    reference_stage_started_at = datetime.now(UTC).isoformat()
    reference_stage_iteration = (
        max((int(entry.get("iteration", 0) or 0) for entry in journal_entries), default=0) + 1
    )
    _persist_best_state(
        best_source,
        best_report,
        stage_started_at=reference_stage_started_at,
        stage_iteration=reference_stage_iteration,
    )
    log_info(
        "研究基线初始化完成: "
        f"role={_reference_role()}, "
        f"quality={best_report.metrics['quality_score']:.2f}, "
        f"promotion={best_report.metrics['promotion_score']:.2f}, "
        f"gate={best_report.gate_reason}"
    )
    write_heartbeat(
        "initialized",
        message="reference ready",
        reference_role=_reference_role(),
        promotion=best_report.metrics["promotion_score"],
        quality=best_report.metrics["quality_score"],
    )
    maybe_send_discord(
        build_discord_summary_message(
            title=f"📌 研究器 v2 {_reference_role()}参考初始化完成",
            report=best_report,
            eval_window_count=EVAL_WINDOW_COUNT,
            validation_window_count=VALIDATION_WINDOW_COUNT,
            test_window_count=TEST_WINDOW_COUNT,
            data_range_text=_discord_data_range_text(),
            factor_change_mode=RUNTIME.factor_change_mode,
        ),
        context="initialize_baseline",
    )


# ==================== 单轮执行 ====================


def _build_journal_entry(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    candidate_report: EvaluationReport | None,
    outcome: str,
    stop_stage: str,
    gate_reason: str | None = None,
    note: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    benchmark_report = _reference_benchmark_report()
    base_promotion = benchmark_report.metrics["promotion_score"] if benchmark_report is not None else 0.0
    diff_summary = build_diff_summary(base_source, candidate.strategy_code, limit=18)
    promotion_score = candidate_report.metrics["promotion_score"] if candidate_report is not None else None
    quality_score = candidate_report.metrics["quality_score"] if candidate_report is not None else None
    eval_gate_reason = candidate_report.gate_reason if candidate_report is not None else (gate_reason or "unknown")
    resolved_gate_reason = gate_reason or (
        eval_gate_reason
    )
    candidate_signature = exploration_signature_for_candidate(
        candidate,
        base_source=base_source,
        editable_regions=EDITABLE_REGIONS,
    )
    actual_changed_regions = sorted(candidate_signature["changed_regions"])
    actual_region_families = sorted(candidate_signature["region_families"])
    actual_ordinary_region_families = sorted(candidate_signature["ordinary_region_families"])
    actual_special_region_families = sorted(candidate_signature["special_region_families"])
    actual_ordinary_changed_regions = sorted(candidate_signature["ordinary_changed_regions"])
    actual_param_families = sorted(candidate_signature["param_families"])
    actual_structural_tokens = sorted(candidate_signature["structural_tokens"])
    complexity_delta = build_strategy_complexity_delta(base_source, candidate.strategy_code)
    entry = {
        "iteration": iteration_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "candidate_id": candidate.candidate_id,
        "outcome": outcome,
        "stop_stage": stop_stage,
        "hypothesis": candidate.hypothesis,
        "change_plan": candidate.change_plan,
        "closest_failed_cluster": candidate.closest_failed_cluster,
        "novelty_proof": candidate.novelty_proof,
        "change_tags": list(candidate.change_tags),
        "edited_regions": list(candidate.edited_regions),
        "expected_effects": list(candidate.expected_effects),
        "core_factors": [
            {
                "name": factor.name,
                "thesis": factor.thesis,
                "current_signal": factor.current_signal,
            }
            for factor in candidate.core_factors
        ],
        "cluster_key": cluster_key_for_components(
            candidate.closest_failed_cluster,
            candidate.change_tags,
        ),
        "quality_score": quality_score,
        "promotion_score": promotion_score,
        "promotion_delta": promotion_score - base_promotion if promotion_score is not None else None,
        "gate_reason": resolved_gate_reason,
        "decision_reason": resolved_gate_reason,
        "eval_gate_reason": eval_gate_reason,
        "metrics": candidate_report.metrics if candidate_report is not None else {},
        "note": note or "",
        "code_hash": source_hash(candidate.strategy_code),
        "diff_summary": diff_summary,
        "region_families": sorted(region_families_for_regions(candidate.edited_regions)),
        "system_changed_regions": actual_changed_regions,
        "system_region_families": actual_region_families,
        "system_ordinary_region_families": actual_ordinary_region_families,
        "system_special_region_families": actual_special_region_families,
        "system_ordinary_changed_regions": actual_ordinary_changed_regions,
        "system_param_families": actual_param_families,
        "system_structural_tokens": actual_structural_tokens,
        "system_signature_hash": candidate_signature["signature_hash"],
        "system_complexity_functions": complexity_delta["functions"],
        "system_complexity_summary": complexity_delta["summary"],
        "system_complexity_flags": list(complexity_delta["flags"]),
        "system_bloat_flag": bool(complexity_delta["bloat_flag"]),
        "declared_regions_match_system": sorted(candidate.edited_regions) == actual_changed_regions,
        "target_family": target_family_from_text(
            candidate.change_tags,
            candidate.hypothesis,
            candidate.expected_effects,
        ),
        "core_factor_names": sorted(candidate_signature["core_factor_names"]),
        "score_regime": SCORE_REGIME,
    }
    if extra_fields:
        entry.update(extra_fields)
    return entry


def _promotion_acceptance_decision(report: EvaluationReport) -> tuple[bool, str]:
    if best_report is None:
        return False, "reference state is not initialized"
    if not report.gate_passed:
        return False, report.gate_reason

    if champion_report is None and not best_report.gate_passed:
        return True, "通过(首个 gate-passed champion)"

    benchmark_report = _reference_benchmark_report()
    if benchmark_report is None:
        return False, "reference benchmark is not initialized"
    current_best_score = float(benchmark_report.metrics["promotion_score"])
    candidate_score = float(report.metrics["promotion_score"])
    promotion_delta = candidate_score - current_best_score
    if promotion_delta <= RUNTIME.promotion_min_delta:
        benchmark_label = "champion" if champion_report is not None else "baseline"
        return (
            False,
            f"相对当前{benchmark_label}晋级分提升不足({promotion_delta:.2f} <= {RUNTIME.promotion_min_delta:.2f})",
        )
    return True, "通过"


def _record_duplicate_skip(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    stop_stage: str,
    gate_reason: str,
    note: str,
) -> None:
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            outcome="duplicate_skipped",
            stop_stage=stop_stage,
            gate_reason=gate_reason,
            note=note,
        )
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _record_exploration_block(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    block_info: dict[str, Any],
) -> None:
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            outcome="exploration_blocked",
            stop_stage=str(block_info.get("stop_stage", "blocked_same_cluster")),
            gate_reason=str(block_info.get("blocked_reason", "")).strip() or "探索方向被系统拒收",
            note=str(block_info.get("blocked_reason", "")).strip() or "探索方向被系统拒收",
            extra_fields={
                "block_kind": str(block_info.get("block_kind", "")).strip(),
                "blocked_cluster": str(block_info.get("blocked_cluster", "")).strip(),
                "lock_rounds": int(block_info.get("lock_rounds", 0) or 0),
                "lock_level": int(block_info.get("lock_level", 0) or 0),
                "lock_trigger_iteration": int(block_info.get("lock_trigger_iteration", 0) or 0),
                "lock_expires_before_iteration": int(block_info.get("lock_expires_before_iteration", 0) or 0),
                "current_locks": list(block_info.get("current_locks", ()) or ()),
                "low_change_tags": list(block_info.get("low_change_tags", ()) or ()),
                "low_change_regions": list(block_info.get("low_change_regions", ()) or ()),
                "low_change_changed_regions": list(block_info.get("low_change_changed_regions", ()) or ()),
                "low_change_targets": list(block_info.get("low_change_targets", ()) or ()),
                "low_change_factors": list(block_info.get("low_change_factors", ()) or ()),
                "low_change_param_families": list(block_info.get("low_change_param_families", ()) or ()),
                "low_change_structural_tokens": list(block_info.get("low_change_structural_tokens", ()) or ()),
            },
        )
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _record_behavioral_noop(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    behavior_diff: dict[str, Any],
) -> None:
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            outcome="behavioral_noop",
            stop_stage="behavioral_noop",
            gate_reason="smoke 行为指纹与当前主参考完全一致",
            note="候选源码有 diff 且可运行，但 smoke 窗口交易行为完全不变；已跳过 full eval。",
            extra_fields={
                "behavior_diff": behavior_diff,
            },
        )
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _activate_candidate(candidate: StrategyCandidate) -> None:
    write_strategy_source(RUNTIME.paths.strategy_backup_file, candidate.strategy_code)
    write_strategy_source(RUNTIME.paths.strategy_file, candidate.strategy_code)
    reload_strategy_module()


def _smoke_candidate(candidate: StrategyCandidate) -> None:
    _activate_candidate(candidate)
    try:
        smoke_test_current_strategy()
    except Exception as exc:
        raise CandidateRuntimeFailure("smoke_test", exc) from exc


def _evaluate_candidate(candidate: StrategyCandidate) -> EvaluationReport:
    _activate_candidate(candidate)
    try:
        return evaluate_current_strategy(allow_early_reject=True)
    except EarlyRejection:
        raise
    except Exception as exc:
        raise CandidateRuntimeFailure("full_eval", exc) from exc


def _candidate_with_repair(
    base_source: str,
    candidate: StrategyCandidate,
    *,
    workspace_root: Path,
) -> tuple[StrategyCandidate, EvaluationReport]:
    current = candidate
    errors: list[str] = []
    for attempt in range(0, max(0, RUNTIME.max_repair_attempts) + 1):
        try:
            write_strategy_source(RUNTIME.paths.strategy_file, base_source)
            reload_strategy_module()
            base_behavior = _smoke_behavior_profile(heartbeat_phase="base_smoke_behavior")
            _smoke_candidate(current)
            candidate_behavior = _smoke_behavior_profile(heartbeat_phase="candidate_smoke_behavior")
            behavior_diff = _behavior_diff_payload(base_behavior, candidate_behavior)
            if not behavior_diff["changed"]:
                raise CandidateBehavioralNoop(current, behavior_diff)
            report = _evaluate_candidate(current)
            return current, report
        except CandidateRuntimeFailure as exc:
            error_message = "".join(
                traceback.format_exception_only(type(exc.error), exc.error)
            ).strip()
            errors.append(f"{exc.stage}: {error_message}")
            write_strategy_source(RUNTIME.paths.strategy_file, base_source)
            reload_strategy_module()
            if attempt >= RUNTIME.max_repair_attempts:
                raise CandidateRepairExhausted(current, errors) from exc
            write_heartbeat(
                "candidate_repairing",
                message=f"iteration {iteration_counter} repairing candidate",
                repair_attempt=attempt + 1,
                max_repair_attempts=RUNTIME.max_repair_attempts,
                error=errors[-1],
            )
            log_info(
                f"第 {iteration_counter} 轮候选运行失败，尝试同轮修复 "
                f"{attempt + 1}/{RUNTIME.max_repair_attempts}: {errors[-1]}"
            )
            current = _repair_model_candidate(
                base_source=base_source,
                failed_candidate=current,
                error_message="\n".join(errors[-3:]),
                repair_attempt=attempt + 1,
                workspace_root=workspace_root,
            )
    raise CandidateRepairExhausted(current, errors)


def run_iteration(iteration_id: int, use_model_optimization: bool = True) -> str:
    global best_source, best_report, champion_report, reference_stage_started_at, reference_stage_iteration

    if best_report is None:
        raise RuntimeError("reference state is not initialized")

    write_strategy_source(RUNTIME.paths.strategy_file, best_source)
    reload_strategy_module()

    if not use_model_optimization:
        report = evaluate_current_strategy()
        log_info(report.summary_text)
        write_heartbeat(
            "evaluation_only",
            message=f"iteration {iteration_id} evaluation only",
            promotion=report.metrics["promotion_score"],
            quality=report.metrics["quality_score"],
        )
        return "evaluation_only"

    with tempfile.TemporaryDirectory(prefix="macd-v2-workspace-") as temp_dir:
        workspace_root = Path(temp_dir)
        workspace_strategy_file = workspace_root / MODEL_WORKSPACE_STRATEGY_PATH
        workspace_strategy_file.parent.mkdir(parents=True, exist_ok=True)
        write_strategy_source(workspace_strategy_file, best_source)

        candidate = build_strategy_candidate(best_source, workspace_root=workspace_root)
        candidate_report: EvaluationReport | None = None
        exploration_regeneration_attempt = 0
        behavioral_noop_regeneration_attempt = 0

        while True:
            candidate_report = None
            journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
            candidate_hash = source_hash(candidate.strategy_code)

            if candidate_hash == source_hash(best_source):
                _record_duplicate_skip(
                    iteration_id=iteration_id,
                    candidate=candidate,
                    base_source=best_source,
                    stop_stage="duplicate_source",
                    gate_reason="候选源码与当前主参考完全相同",
                    note="模型未产生有效代码改动；本轮按重复探索记入研究历史。",
                )
                log_info(f"第 {iteration_id} 轮跳过: 候选源码与当前主参考完全相同")
                write_heartbeat("iteration_skipped", message=f"iteration {iteration_id} duplicate source")
                return "duplicate_skipped"
            if has_recent_code_hash(journal_entries, candidate_hash):
                _record_duplicate_skip(
                    iteration_id=iteration_id,
                    candidate=candidate,
                    base_source=best_source,
                    stop_stage="duplicate_history",
                    gate_reason="候选源码命中最近研究历史",
                    note="模型重复产出了最近已出现过的候选源码；本轮按重复探索记入研究历史。",
                )
                log_info(f"第 {iteration_id} 轮跳过: 候选源码命中最近研究历史")
                write_heartbeat("iteration_skipped", message=f"iteration {iteration_id} duplicate journal hash")
                return "duplicate_skipped"

            diff_summary = build_diff_summary(best_source, candidate.strategy_code, limit=12)
            if not diff_summary:
                _record_duplicate_skip(
                    iteration_id=iteration_id,
                    candidate=candidate,
                    base_source=best_source,
                    stop_stage="empty_diff",
                    gate_reason="候选没有产生有效 diff",
                    note="候选虽然通过了解析，但没有形成可验证的有效改动；本轮按重复探索记入研究历史。",
                )
                log_info(f"第 {iteration_id} 轮跳过: 候选没有产生有效 diff")
                write_heartbeat("iteration_skipped", message=f"iteration {iteration_id} empty diff")
                return "duplicate_skipped"

            block_info = evaluate_candidate_exploration_guard(
                candidate,
                journal_entries,
                journal_path=RUNTIME.paths.journal_file,
                score_regime=SCORE_REGIME,
                current_iteration=iteration_id,
                base_source=best_source,
                editable_regions=EDITABLE_REGIONS,
                lock_schedule=_cluster_lock_schedule(),
                include_current_round_locks=True,
            )
            if block_info is not None:
                _record_exploration_block(
                    iteration_id=iteration_id,
                    candidate=candidate,
                    base_source=best_source,
                    block_info=block_info,
                )
                log_info(
                    f"第 {iteration_id} 轮候选在评估前被系统拦截: "
                    f"{block_info['blocked_reason']}"
                )
                if exploration_regeneration_attempt >= RUNTIME.max_exploration_regen_attempts:
                    write_strategy_source(RUNTIME.paths.strategy_file, best_source)
                    reload_strategy_module()
                    write_heartbeat(
                        "iteration_exploration_blocked",
                        message=f"iteration {iteration_id} exploration blocked",
                        block_kind=block_info["block_kind"],
                        blocked_cluster=block_info["blocked_cluster"],
                    )
                    return "exploration_blocked"

                write_heartbeat(
                    "candidate_regenerating",
                    message=f"iteration {iteration_id} regenerating candidate",
                    regeneration_attempt=exploration_regeneration_attempt + 1,
                    max_regeneration_attempts=RUNTIME.max_exploration_regen_attempts,
                    block_kind=block_info["block_kind"],
                    blocked_cluster=block_info["blocked_cluster"],
                )
                exploration_regeneration_attempt += 1
                candidate = _regenerate_model_candidate(
                    base_source=best_source,
                    failed_candidate=candidate,
                    block_info=block_info,
                    regeneration_attempt=exploration_regeneration_attempt,
                    workspace_root=workspace_root,
                )
                continue

            try:
                while True:
                    try:
                        candidate, candidate_report = _candidate_with_repair(
                            best_source,
                            candidate,
                            workspace_root=workspace_root,
                        )
                        break
                    except CandidateBehavioralNoop as exc:
                        write_strategy_source(RUNTIME.paths.strategy_file, best_source)
                        reload_strategy_module()
                        journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
                        if behavioral_noop_regeneration_attempt >= RUNTIME.max_exploration_regen_attempts:
                            _record_behavioral_noop(
                                iteration_id=iteration_id,
                                candidate=exc.candidate,
                                base_source=best_source,
                                behavior_diff=exc.behavior_diff,
                            )
                            log_info(
                                f"第 {iteration_id} 轮跳过: smoke 行为指纹未变化 "
                                f"(trades={exc.behavior_diff['candidate']['total_trades']})"
                            )
                            write_heartbeat(
                                "iteration_behavioral_noop",
                                message=f"iteration {iteration_id} behavioral noop",
                                gate="smoke 行为指纹与当前主参考完全一致",
                            )
                            return "behavioral_noop"

                        block_info = _behavioral_noop_block_info(
                            exc.candidate,
                            exc.behavior_diff,
                            journal_entries=journal_entries,
                            base_source=best_source,
                        )
                        behavioral_noop_regeneration_attempt += 1
                        write_heartbeat(
                            "candidate_regenerating",
                            message=f"iteration {iteration_id} regenerating candidate after behavioral noop",
                            regeneration_attempt=behavioral_noop_regeneration_attempt,
                            max_regeneration_attempts=RUNTIME.max_exploration_regen_attempts,
                            block_kind=block_info["block_kind"],
                            blocked_cluster=block_info["blocked_cluster"],
                        )
                        log_info(
                            f"第 {iteration_id} 轮候选 smoke 行为未变化，触发同轮重生 "
                            f"{behavioral_noop_regeneration_attempt}/{RUNTIME.max_exploration_regen_attempts}"
                        )
                        candidate = _regenerate_model_candidate(
                            base_source=best_source,
                            failed_candidate=exc.candidate,
                            block_info=block_info,
                            regeneration_attempt=behavioral_noop_regeneration_attempt,
                            workspace_root=workspace_root,
                        )
                        break
            except EarlyRejection as exc:
                write_strategy_source(RUNTIME.paths.strategy_file, best_source)
                reload_strategy_module()
                _append_research_journal_entry(
                    _build_journal_entry(
                        iteration_id=iteration_id,
                        candidate=candidate,
                        base_source=best_source,
                        candidate_report=None,
                        outcome="early_rejected",
                        stop_stage="early_reject",
                        gate_reason="前段趋势捕获过差",
                        note=str(exc),
                    )
                )
                if maybe_compact(RUNTIME.paths.journal_file):
                    log_info("研究日志已压缩")
                log_info(f"第 {iteration_id} 轮提前淘汰: {exc}")
                write_heartbeat(
                    "iteration_early_rejected",
                    message=f"iteration {iteration_id} early rejected: {exc}",
                    gate="前段趋势捕获过差",
                )
                return "early_rejected"
            except CandidateRepairExhausted as exc:
                write_strategy_source(RUNTIME.paths.strategy_file, best_source)
                reload_strategy_module()
                _append_research_journal_entry(
                    _build_journal_entry(
                        iteration_id=iteration_id,
                        candidate=exc.candidate,
                        base_source=best_source,
                        candidate_report=None,
                        outcome="runtime_failed",
                        stop_stage="runtime_error",
                        gate_reason="运行失败",
                        note="；".join(exc.errors),
                    )
                )
                if maybe_compact(RUNTIME.paths.journal_file):
                    log_info("研究日志已压缩")
                log_info(f"第 {iteration_id} 轮运行失败并已记录: {exc}")
                write_heartbeat(
                    "iteration_runtime_failed",
                    message=f"iteration {iteration_id} runtime failed",
                    error=str(exc),
                )
                return "runtime_failed"
            except StrategyGenerationTransientError:
                raise
            except Exception:
                write_strategy_source(RUNTIME.paths.strategy_file, best_source)
                reload_strategy_module()
                raise

            if candidate_report is None:
                continue
            break

    accepted, decision_reason = _promotion_acceptance_decision(candidate_report)
    entry_note = ""
    if not accepted and candidate_report.gate_passed:
        entry_note = decision_reason

    entry_base = _build_journal_entry(
        iteration_id=iteration_id,
        candidate=candidate,
        base_source=best_source,
        candidate_report=candidate_report,
        outcome="accepted" if accepted else "rejected",
        stop_stage="full_eval",
        gate_reason=decision_reason if not accepted else None,
        note=entry_note,
    )

    if accepted:
        best_source = candidate.strategy_code
        best_report = candidate_report
        champion_report = candidate_report
        reference_stage_started_at = datetime.now(UTC).isoformat()
        reference_stage_iteration = iteration_id
        hidden_test_result: dict[str, Any] | None = None
        shadow_test_metrics: dict[str, float] | None = None
        try:
            hidden_test_result = evaluate_hidden_test_result()
            shadow_test_metrics = summarize_hidden_test_result(hidden_test_result)
            log_info(
                "test验收: "
                f"score={shadow_test_metrics['shadow_test_score']:.2f}, "
                f"return={shadow_test_metrics['shadow_test_total_return_pct']:.2f}%, "
                f"segments={int(shadow_test_metrics['shadow_test_segment_count'])}, "
                f"hit={shadow_test_metrics['shadow_test_hit_rate']:.0%}"
            )
        except Exception as exc:
            log_info(f"test评估失败，但本轮 champion 已按 val 保留: {exc}")
            logging.exception("test评估失败(iteration=%s)", iteration_id)

        _persist_best_state(
            best_source,
            best_report,
            shadow_test_metrics=shadow_test_metrics,
            stage_started_at=reference_stage_started_at,
            stage_iteration=reference_stage_iteration,
        )
        _append_research_journal_entry(entry_base)
        if maybe_compact(RUNTIME.paths.journal_file):
            log_info("研究日志已压缩")
        log_info(
            f"🚀 第 {iteration_id} 轮产生新 champion: "
            f"quality={best_report.metrics['quality_score']:.2f}, "
            f"promotion={best_report.metrics['promotion_score']:.2f}"
        )
        log_info(candidate_report.summary_text)
        write_heartbeat(
            "new_champion",
            message=f"iteration {iteration_id} champion accepted",
            reference_role=_reference_role(),
            promotion=best_report.metrics["promotion_score"],
            quality=best_report.metrics["quality_score"],
            gate=best_report.gate_reason,
        )
        chart_paths = PerformanceChartPaths(validation_chart=None, selection_chart=None)
        try:
            chart_paths = _generate_new_champion_charts(
                iteration_id,
                hidden_test_result=hidden_test_result,
            )
            if chart_paths.selection_chart is not None:
                log_info(f"train+val图已保存: {chart_paths.selection_chart}")
            if chart_paths.validation_chart is not None:
                log_info(f"val图已保存: {chart_paths.validation_chart}")
        except Exception as exc:
            log_info(f"新 champion 图表生成失败: {exc}")
            logging.exception("新 champion 图表生成失败(iteration=%s)", iteration_id)
        discord_message = build_discord_summary_message(
            title=f"🚀 研究器 v2 新 champion #{iteration_id}",
            report=best_report,
            eval_window_count=EVAL_WINDOW_COUNT,
            validation_window_count=VALIDATION_WINDOW_COUNT,
            test_window_count=TEST_WINDOW_COUNT,
            data_range_text=_discord_data_range_text(),
            shadow_test_metrics=shadow_test_metrics,
            candidate=candidate,
            factor_change_mode=RUNTIME.factor_change_mode,
        )
        attachments = [chart_paths.validation_chart] if chart_paths.validation_chart is not None else None
        if attachments:
            discord_message = _build_chart_note(discord_message)
        maybe_send_discord(
            discord_message,
            context=f"accepted_iteration_{iteration_id}",
            attachments=attachments,
        )
        return "accepted"

    _append_research_journal_entry(entry_base)
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")
    write_strategy_source(RUNTIME.paths.strategy_file, best_source)
    reload_strategy_module()
    log_info(
        f"第 {iteration_id} 轮未保留: "
        f"quality={candidate_report.metrics['quality_score']:.2f}, "
        f"promotion={candidate_report.metrics['promotion_score']:.2f}, "
        f"reason={decision_reason}"
    )
    write_heartbeat(
        "iteration_rejected",
        message=f"iteration {iteration_id} rejected",
        promotion=candidate_report.metrics["promotion_score"],
        quality=candidate_report.metrics["quality_score"],
        gate=decision_reason,
    )
    return "rejected"


# ==================== 主循环 ====================


def _sleep_with_stop(seconds: int) -> None:
    for _ in range(max(0, seconds)):
        if RUNTIME.paths.stop_file.exists():
            return
        time.sleep(1)


def _remove_runtime_state() -> None:
    for path in (
        RUNTIME.paths.stop_file,
        RUNTIME.paths.heartbeat_file,
    ):
        if path.exists():
            path.unlink()


def main() -> int:
    global iteration_counter

    parser = argparse.ArgumentParser(description="激进版 MACD 研究器 v2")
    parser.add_argument("--once", action="store_true", help="只跑一轮")
    parser.add_argument("--no-optimize", action="store_true", help="只做评估，不生成候选")
    parser.add_argument(
        "--reset-champion",
        "--reset-best",
        dest="reset_reference",
        action="store_true",
        help="忽略历史主参考，按当前源码重新初始化",
    )
    args = parser.parse_args()

    RUNTIME.paths.log_file.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.paths.journal_file.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.paths.memory_dir.mkdir(parents=True, exist_ok=True)
    RUNTIME.paths.best_strategy_file.parent.mkdir(parents=True, exist_ok=True)
    _remove_runtime_state()

    log_info("启动激进版 MACD 研究器 v2")
    log_info(
        "窗口配置: "
        + ", ".join(
            f"{window.label}={window.start_date}~{window.end_date}[{window.group}]"
            for window in WINDOWS
        )
    )
    initialize_best_state(force_rebuild=args.reset_reference)

    if args.no_optimize:
        return 0

    while True:
        if RUNTIME.paths.stop_file.exists():
            write_heartbeat("stopped", message="stop file detected")
            return 0

        iteration_counter += 1
        write_heartbeat("iteration_running", message=f"iteration {iteration_counter} running")
        try:
            outcome = run_iteration(iteration_counter, use_model_optimization=True)
        except StrategyGenerationTransientError as exc:
            log_info(
                f"⚠️ 第 {iteration_counter} 轮延后: provider transient failure "
                f"({str(exc).splitlines()[0]}), "
                f"{RUNTIME.provider_recovery_wait_seconds} 秒后重试"
            )
            write_heartbeat(
                "provider_transient_failure",
                message=f"iteration {iteration_counter} provider transient failure",
                error=str(exc).splitlines()[0],
            )
            if args.once:
                return 1
            _sleep_with_stop(RUNTIME.provider_recovery_wait_seconds)
            continue
        except Exception as exc:
            write_strategy_source(RUNTIME.paths.strategy_file, best_source)
            reload_strategy_module()
            log_exception(f"❌ 第 {iteration_counter} 轮失败: {exc}")
            write_heartbeat("iteration_failed", message=f"iteration {iteration_counter} failed", error=str(exc))
            if args.once:
                return 1
            _sleep_with_stop(RUNTIME.failure_cooldown_seconds)
            continue

        if args.once:
            return 0 if outcome in {
                "accepted",
                "rejected",
                "duplicate_skipped",
                "behavioral_noop",
                "runtime_failed",
                "exploration_blocked",
            } else 1

        write_heartbeat(
            "sleeping",
            message=f"iteration {iteration_counter} sleeping",
            last_outcome=outcome,
            sleep_seconds=RUNTIME.loop_interval_seconds,
        )
        _sleep_with_stop(RUNTIME.loop_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
