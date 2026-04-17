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
from research_v2.evaluation import EvaluationReport, partial_eval_gate_snapshot, summarize_evaluation
from research_v2.journal import (
    append_journal_entry,
    build_journal_prompt_summary,
    cluster_key_for_components,
    has_recent_code_hash,
    load_journal_entries,
    maybe_compact,
)
from research_v2.notifications import build_discord_summary_message, load_discord_config, send_discord_message
from research_v2.prompting import EDITABLE_REGIONS, build_candidate_response_schema, build_strategy_research_prompt
from research_v2.prompting import build_strategy_runtime_repair_prompt
from research_v2.strategy_code import (
    StrategyCandidate,
    StrategyCoreFactor,
    StrategySourceError,
    build_diff_summary,
    load_strategy_source,
    normalize_strategy_source,
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
SCORE_REGIME = "trend_capture_v4"
MODEL_WORKSPACE_STRATEGY_PATH = Path("src/strategy_macd_aggressive.py")

best_source = ""
best_report: EvaluationReport | None = None
iteration_counter = 0

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
        candidates.append(eval_windows[len(eval_windows) // 2])
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


def _full_period_bounds(windows: list[Any]) -> tuple[str, str]:
    if not windows:
        raise ValueError("missing research windows")
    start_date = min(window.start_date for window in windows)
    end_date = max(window.end_date for window in windows)
    return start_date, end_date


def _eval_bounds(windows: list[Any]) -> tuple[str, str]:
    eval_windows = [window for window in windows if window.group == "eval"]
    if not eval_windows:
        raise ValueError("missing eval windows")
    return eval_windows[0].start_date, eval_windows[-1].end_date


def _validation_window() -> Any:
    for window in WINDOWS:
        if window.group == "validation":
            return window
    raise ValueError("missing validation window")


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
    active_windows = windows or WINDOWS
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


def _run_full_period_backtest(
    prepared_context: dict[str, Any],
    *,
    include_diagnostics: bool = True,
    heartbeat_phase: str = "full_period_eval",
) -> dict[str, Any]:
    start_date, end_date = _full_period_bounds(WINDOWS)
    write_heartbeat(
        "iteration_running",
        message=f"iteration {iteration_counter} {heartbeat_phase}",
        phase=heartbeat_phase,
        current_window="全段连续",
        window_index=len(WINDOWS) + 1,
        window_count=len(WINDOWS) + 1,
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


def _run_eval_continuous_backtest(
    prepared_context: dict[str, Any],
    *,
    include_diagnostics: bool = True,
    heartbeat_phase: str = "eval_continuous",
) -> dict[str, Any]:
    start_date, end_date = _eval_bounds(WINDOWS)
    write_heartbeat(
        "iteration_running",
        message=f"iteration {iteration_counter} {heartbeat_phase}",
        phase=heartbeat_phase,
        current_window="评估连续",
        window_index=len(WINDOWS) + 1,
        window_count=len(WINDOWS) + 2,
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


def _run_validation_continuous_backtest(
    prepared_context: dict[str, Any],
    *,
    include_diagnostics: bool = True,
    heartbeat_phase: str = "validation_continuous",
) -> dict[str, Any]:
    validation_window = _validation_window()
    write_heartbeat(
        "iteration_running",
        message=f"iteration {iteration_counter} {heartbeat_phase}",
        phase=heartbeat_phase,
        current_window="验证连续",
        window_index=len(WINDOWS) + 1,
        window_count=len(WINDOWS) + 2,
    )
    return backtest_module.backtest_macd_aggressive(
        strategy_func=strategy_module.strategy,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        start_date=validation_window.start_date,
        end_date=validation_window.end_date,
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
        "图表：上图蓝线=策略累计增长，橙线=BTC累计增长；下图红区=策略相对自身峰值的回撤。"
    )


def _generate_new_best_charts(iteration_id: int) -> PerformanceChartPaths:
    if not charts_available():
        log_info("跳过新最优图表：matplotlib 不可用")
        return PerformanceChartPaths(validation_chart=None, full_period_chart=None)

    chart_dir = _chart_output_dir()
    validation_window = _validation_window()
    prepared_context = _prepare_backtest_context()

    write_heartbeat(
        "new_best_charting",
        message=f"iteration {iteration_id} charting validation",
        phase="new_best_charting",
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
    full_period_result = _run_full_period_backtest(
        prepared_context,
        include_diagnostics=True,
        heartbeat_phase="new_best_charting",
    )

    validation_chart = chart_dir / (
        f"new_best_{iteration_id:04d}_validation_{validation_window.start_date}_{validation_window.end_date}.png"
    )
    full_start, full_end = _full_period_bounds(WINDOWS)
    full_period_chart = chart_dir / (
        f"new_best_{iteration_id:04d}_full_period_{full_start}_{full_end}.png"
    )

    validation_chart = render_performance_chart(
        daily_equity_curve=validation_result.get("daily_equity_curve", []),
        output_path=validation_chart,
        title=f"New Best #{iteration_id} Validation",
        subtitle=f"{validation_window.start_date} to {validation_window.end_date}",
    )
    full_period_chart = render_performance_chart(
        daily_equity_curve=full_period_result.get("daily_equity_curve", []),
        output_path=full_period_chart,
        title=f"New Best #{iteration_id} Full Period",
        subtitle=f"{full_start} to {full_end}",
    )

    if validation_chart is not None:
        _write_chart_copy(validation_chart, chart_dir / "latest_validation.png")
    if full_period_chart is not None:
        _write_chart_copy(full_period_chart, chart_dir / "latest_full_period.png")

    return PerformanceChartPaths(
        validation_chart=validation_chart,
        full_period_chart=full_period_chart,
    )


def evaluate_current_strategy(allow_early_reject: bool = False) -> EvaluationReport:
    prepared_context = _prepare_backtest_context()
    base_results = _run_base_backtests(
        allow_early_reject=allow_early_reject,
        prepared_context=prepared_context,
    )
    eval_continuous_result = _run_eval_continuous_backtest(prepared_context)
    validation_continuous_result = _run_validation_continuous_backtest(prepared_context)
    full_period_result = _run_full_period_backtest(prepared_context)
    return summarize_evaluation(
        base_results,
        RUNTIME.gates,
        eval_continuous_result=eval_continuous_result,
        validation_continuous_result=validation_continuous_result,
        full_period_result=full_period_result,
    )


def smoke_test_current_strategy() -> None:
    prepared_context = _prepare_backtest_context()
    _run_base_backtests(
        windows=_selected_smoke_windows(),
        include_diagnostics=False,
        heartbeat_phase="smoke_test",
        prepared_context=prepared_context,
    )


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
    validate_strategy_source(strategy_code)
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
    if not candidate.closest_failed_cluster:
        raise StrategySourceError("candidate missing closest_failed_cluster")
    if not candidate.novelty_proof:
        raise StrategySourceError("candidate missing novelty_proof")
    return candidate


def _build_model_candidate(
    base_source: str,
    journal_entries: list[dict[str, Any]],
    *,
    workspace_root: Path,
) -> StrategyCandidate:
    report = best_report
    if report is None:
        raise StrategySourceError("best report is not initialized")

    prompt = build_strategy_research_prompt(
        evaluation_summary=report.prompt_summary_text,
        journal_summary=build_journal_prompt_summary(
            journal_entries,
            limit=RUNTIME.max_recent_journal_entries,
            journal_path=RUNTIME.paths.journal_file,
            current_score_regime=SCORE_REGIME,
        ),
        previous_best_score=report.metrics["promotion_score"],
    )
    with _temporary_cwd(workspace_root):
        payload = generate_json_object(
            prompt=prompt,
            system_prompt=(
                "你是严谨的量化研究员。"
                "只输出 JSON，不要解释，不要 markdown。"
                "你必须先阅读并直接修改工作区中的 src/strategy_macd_aggressive.py，再输出 JSON。"
                "不要在 JSON 中返回源码。"
                "除 candidate_id 与 change_tags 外，其余说明字段必须使用简体中文。"
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
            system_prompt=(
                "你是严谨的量化研究员。"
                "只输出 JSON，不要解释，不要 markdown。"
                "你正在修复同一轮候选代码，不要切换研究方向。"
                "你必须直接修改工作区中的 src/strategy_macd_aggressive.py。"
                "不要在 JSON 中返回源码。"
                "除 candidate_id 与 change_tags 外，其余说明字段必须使用简体中文。"
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


def build_strategy_candidate(base_source: str, *, workspace_root: Path) -> StrategyCandidate:
    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    try:
        return _build_model_candidate(base_source, journal_entries, workspace_root=workspace_root)
    except StrategyGenerationTransientError:
        raise
    except Exception:
        raise


# ==================== 最优状态管理 ====================


def _persist_best_state(source: str, report: EvaluationReport) -> None:
    RUNTIME.paths.best_state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(UTC).isoformat(),
        "code_hash": source_hash(source),
        "score_regime": SCORE_REGIME,
        "metrics": report.metrics,
        "gate_passed": report.gate_passed,
        "gate_reason": report.gate_reason,
    }
    RUNTIME.paths.best_state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    write_strategy_source(RUNTIME.paths.best_strategy_file, source)


def initialize_best_state(force_rebuild: bool = False) -> None:
    global best_source, best_report

    if (
        not force_rebuild
        and RUNTIME.paths.best_strategy_file.exists()
    ):
        best_source = load_strategy_source(RUNTIME.paths.best_strategy_file)
        write_strategy_source(RUNTIME.paths.strategy_file, best_source)
        reload_strategy_module()
        best_report = evaluate_current_strategy()
        _persist_best_state(best_source, best_report)
        log_info(
            "已加载已保存的最优基底: "
            f"quality={best_report.metrics['quality_score']:.2f}, "
            f"promotion={best_report.metrics['promotion_score']:.2f}, "
            f"gate={best_report.gate_reason}"
        )
        write_heartbeat(
            "initialized",
            message="loaded saved best baseline",
            promotion=best_report.metrics["promotion_score"],
            quality=best_report.metrics["quality_score"],
        )
        maybe_send_discord(
            build_discord_summary_message(
                title="📌 研究器 v2 已加载最优基底",
                report=best_report,
                eval_window_count=EVAL_WINDOW_COUNT,
                validation_window_count=VALIDATION_WINDOW_COUNT,
            ),
            context="initialize_saved_best",
        )
        return

    best_source = load_strategy_source(RUNTIME.paths.strategy_file)
    validate_strategy_source(best_source)
    reload_strategy_module()
    best_report = evaluate_current_strategy()
    _persist_best_state(best_source, best_report)
    log_info(
        "研究基底初始化完成: "
        f"quality={best_report.metrics['quality_score']:.2f}, "
        f"promotion={best_report.metrics['promotion_score']:.2f}, "
        f"gate={best_report.gate_reason}"
    )
    write_heartbeat(
        "initialized",
        message="baseline ready",
        promotion=best_report.metrics["promotion_score"],
        quality=best_report.metrics["quality_score"],
    )
    maybe_send_discord(
        build_discord_summary_message(
            title="📌 研究器 v2 基底初始化完成",
            report=best_report,
            eval_window_count=EVAL_WINDOW_COUNT,
            validation_window_count=VALIDATION_WINDOW_COUNT,
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
) -> dict[str, Any]:
    base_promotion = best_report.metrics["promotion_score"] if best_report is not None else 0.0
    diff_summary = build_diff_summary(base_source, candidate.strategy_code, limit=18)
    promotion_score = candidate_report.metrics["promotion_score"] if candidate_report is not None else None
    quality_score = candidate_report.metrics["quality_score"] if candidate_report is not None else None
    resolved_gate_reason = gate_reason or (
        candidate_report.gate_reason if candidate_report is not None else "unknown"
    )
    return {
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
        "metrics": candidate_report.metrics if candidate_report is not None else {},
        "note": note or "",
        "code_hash": source_hash(candidate.strategy_code),
        "diff_summary": diff_summary,
        "score_regime": SCORE_REGIME,
    }


def _promotion_acceptance_decision(report: EvaluationReport) -> tuple[bool, str]:
    if best_report is None:
        return False, "best state is not initialized"
    if not report.gate_passed:
        return False, report.gate_reason

    current_best_score = float(best_report.metrics["promotion_score"])
    candidate_score = float(report.metrics["promotion_score"])
    promotion_delta = candidate_score - current_best_score
    if promotion_delta <= RUNTIME.promotion_min_delta:
        return (
            False,
            f"晋级分提升不足({promotion_delta:.2f} <= {RUNTIME.promotion_min_delta:.2f})",
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
    append_journal_entry(
        RUNTIME.paths.journal_file,
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            outcome="duplicate_skipped",
            stop_stage=stop_stage,
            gate_reason=gate_reason,
            note=note,
        ),
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
            _smoke_candidate(current)
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
    global best_source, best_report

    if best_report is None:
        raise RuntimeError("best state is not initialized")

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
        candidate_hash = source_hash(candidate.strategy_code)
        journal_entries = load_journal_entries(RUNTIME.paths.journal_file)

        if candidate_hash == source_hash(best_source):
            _record_duplicate_skip(
                iteration_id=iteration_id,
                candidate=candidate,
                base_source=best_source,
                stop_stage="duplicate_source",
                gate_reason="候选源码与当前最优完全相同",
                note="模型未产生有效代码改动；本轮按重复探索记入研究历史。",
            )
            log_info(f"第 {iteration_id} 轮跳过: 候选源码与当前最优完全相同")
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

        try:
            candidate, candidate_report = _candidate_with_repair(
                best_source,
                candidate,
                workspace_root=workspace_root,
            )
        except EarlyRejection as exc:
            write_strategy_source(RUNTIME.paths.strategy_file, best_source)
            reload_strategy_module()
            append_journal_entry(
                RUNTIME.paths.journal_file,
                _build_journal_entry(
                    iteration_id=iteration_id,
                    candidate=candidate,
                    base_source=best_source,
                    candidate_report=None,
                    outcome="early_rejected",
                    stop_stage="early_reject",
                    gate_reason="前段趋势捕获过差",
                    note=str(exc),
                ),
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
            append_journal_entry(
                RUNTIME.paths.journal_file,
                _build_journal_entry(
                    iteration_id=iteration_id,
                    candidate=exc.candidate,
                    base_source=best_source,
                    candidate_report=None,
                    outcome="runtime_failed",
                    stop_stage="runtime_error",
                    gate_reason="运行失败",
                    note="；".join(exc.errors),
                ),
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
        note=entry_note,
    )

    if accepted:
        best_source = candidate.strategy_code
        best_report = candidate_report
        _persist_best_state(best_source, best_report)
        append_journal_entry(RUNTIME.paths.journal_file, entry_base)
        if maybe_compact(RUNTIME.paths.journal_file):
            log_info("研究日志已压缩")
        log_info(
            f"🚀 第 {iteration_id} 轮产生新最优: "
            f"quality={best_report.metrics['quality_score']:.2f}, "
            f"promotion={best_report.metrics['promotion_score']:.2f}"
        )
        log_info(candidate_report.summary_text)
        write_heartbeat(
            "new_best",
            message=f"iteration {iteration_id} accepted",
            promotion=best_report.metrics["promotion_score"],
            quality=best_report.metrics["quality_score"],
            gate=best_report.gate_reason,
        )
        chart_paths = PerformanceChartPaths(validation_chart=None, full_period_chart=None)
        try:
            chart_paths = _generate_new_best_charts(iteration_id)
            if chart_paths.full_period_chart is not None:
                log_info(f"全段图已保存: {chart_paths.full_period_chart}")
            if chart_paths.validation_chart is not None:
                log_info(f"验证图已保存: {chart_paths.validation_chart}")
        except Exception as exc:
            log_info(f"新最优图表生成失败: {exc}")
            logging.exception("新最优图表生成失败(iteration=%s)", iteration_id)
        discord_message = build_discord_summary_message(
            title=f"🚀 研究器 v2 新最优 #{iteration_id}",
            report=best_report,
            eval_window_count=EVAL_WINDOW_COUNT,
            validation_window_count=VALIDATION_WINDOW_COUNT,
            candidate=candidate,
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

    append_journal_entry(RUNTIME.paths.journal_file, entry_base)
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
    parser.add_argument("--reset-best", action="store_true", help="忽略历史最优，按当前源码重新初始化")
    args = parser.parse_args()

    RUNTIME.paths.log_file.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.paths.journal_file.parent.mkdir(parents=True, exist_ok=True)
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
    initialize_best_state(force_rebuild=args.reset_best)

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
            return 0 if outcome in {"accepted", "rejected", "duplicate_skipped", "runtime_failed"} else 1

        write_heartbeat(
            "sleeping",
            message=f"iteration {iteration_counter} sleeping",
            last_outcome=outcome,
            sleep_seconds=RUNTIME.loop_interval_seconds,
        )
        _sleep_with_stop(RUNTIME.loop_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
