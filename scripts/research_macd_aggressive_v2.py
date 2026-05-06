#!/usr/bin/env python3
"""激进版 MACD 研究器 v2。

目标：
1. 不再把研究器限制为纯参数搜索。
2. 每轮允许模型在受控边界内改写策略文件。
3. 把评估、记忆、代码校验拆开，避免主脚本继续膨胀。
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
import contextlib
import importlib
import json
import logging
import multiprocessing
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import backtest_macd_aggressive as backtest_module
import strategy_macd_aggressive as strategy_module
from codex_exec_client import (
    StrategyGenerationSessionError,
    StrategyGenerationTransientError,
    generate_text_response,
    load_strategy_client_config,
)
from deepseek_planner_client import (
    generate_text_response as generate_deepseek_planner_text_response,
    load_deepseek_planner_config,
)
from research_v2.config import ResearchRuntimeConfig, load_research_runtime_config
from research_v2.charting import PerformanceChartPaths, charts_available, render_performance_chart
from research_v2.champion_artifacts import (
    archive_champion_snapshot as archive_champion_snapshot_helper,
    build_chart_note as build_chart_note_helper,
    champion_snapshot_stamp as champion_snapshot_stamp_helper,
    safe_snapshot_slug as safe_snapshot_slug_helper,
)
from research_v2.exit_range_scan import (
    infer_exit_range_scan_spec,
    replace_exit_param_value,
    run_exit_range_scan,
    run_plateau_probe,
)
from research_v2.evaluation import (
    EvaluationReport,
    normalize_test_metrics_payload,
    partial_eval_gate_snapshot,
    summarize_evaluation,
    summarize_test_result,
)
from research_v2.journal import (
    append_journal_archive,
    append_journal_entry,
    build_journal_prompt_summary,
    count_recent_result_basin,
    evaluate_candidate_failure_wiki_guard,
    evaluate_candidate_exploration_guard,
    cluster_key_for_components,
    exploration_signature_for_candidate,
    load_failure_wiki_index,
    has_recent_code_hash,
    load_journal_entries,
    region_families_for_regions,
    result_basin_key_for_entry,
    target_family_from_text,
    maybe_compact,
)
from research_v2.notifications import build_discord_summary_message, load_discord_config, send_discord_message
from research_v2.prompting import (
    build_strategy_agents_instructions,
    build_strategy_candidate_summary_prompt,
    build_strategy_edit_worker_system_prompt,
    build_strategy_edit_worker_prompt,
    build_strategy_exploration_repair_prompt,
    build_strategy_no_edit_repair_prompt,
    build_strategy_planner_system_prompt,
    build_strategy_repair_worker_system_prompt,
    build_strategy_round_brief_repair_prompt,
    build_strategy_reviewer_prompt,
    build_strategy_reviewer_repair_prompt,
    build_strategy_reviewer_revise_prompt,
    build_strategy_reviewer_system_prompt,
    build_strategy_research_prompt,
    build_strategy_summary_worker_system_prompt,
    build_strategy_runtime_repair_prompt,
)
from research_v2.rejected_test_runner import run_round_artifact_test
from research_v2.round_artifacts import (
    load_round_artifact_metadata,
    persist_round_artifact as persist_round_artifact_helper,
    update_round_artifact_test_payload,
)
from research_v2.strategy_code import (
    REQUIRED_FUNCTIONS,
    StrategyCandidate,
    StrategyCoreFactor,
    StrategySourceError,
    build_diff_summary,
    build_strategy_complexity_pressure,
    build_system_edit_signature,
    build_strategy_complexity_delta,
    load_strategy_source,
    normalize_strategy_source,
    source_hash,
    validate_strategy_source,
    write_strategy_source,
)
from research_v2.reference_state import (
    load_saved_reference_state as load_saved_reference_state_helper,
    parse_state_timestamp as parse_state_timestamp_helper,
    persist_best_state as persist_best_state_helper,
    recover_reference_stage_state as recover_reference_stage_state_helper,
    reference_manifest_payload as reference_manifest_payload_helper,
    report_from_saved_payload as report_from_saved_payload_helper,
    saved_report_payload as saved_report_payload_helper,
)
from research_v2.windows import ResearchWindow, build_research_windows


# ==================== 全局状态 ====================


RUNTIME = load_research_runtime_config(REPO_ROOT)
WINDOWS = build_research_windows(RUNTIME.windows)
DISCORD_CONFIG = load_discord_config()
EVAL_WINDOW_COUNT = sum(1 for window in WINDOWS if window.group == "eval")
VALIDATION_WINDOW_COUNT = sum(1 for window in WINDOWS if window.group == "validation")
TEST_WINDOW_COUNT = sum(1 for window in WINDOWS if window.group == "test")
SCORE_REGIME = "trend_capture_v14_midfreq_sharpe_floor_balance"
MODEL_WORKSPACE_STRATEGY_PATH = Path("src/strategy_macd_aggressive.py")
PRIMARY_DIRECTION_DOMAINS = frozenset({"long", "short", "mixed", "structure"})
PLANNER_BRIEF_REQUIRED_FIELDS = ("primary_direction", "hypothesis", "change_plan", "novelty_proof", "change_tags")
MAX_PLANNER_BRIEF_REPAIR_ATTEMPTS = 1
REVIEWER_REQUIRED_FIELDS = (
    "verdict",
    "reviewer_summary",
    "rejection_type",
    "matched_evidence",
    "must_change",
    "why_not_new",
)
MAX_REVIEWER_REPAIR_ATTEMPTS = 1
MAX_REVIEWER_REVISE_ATTEMPTS = 2
PREPARED_BACKTEST_CONTEXT_CACHE_LIMIT = 2
NON_PERSISTENT_CODEX_TRANSIENT_RETRY_LIMIT = 1
CONTEXT_RELEVANT_EXIT_PARAM_KEYS = frozenset({"execution_use_1m", "funding_fee_enabled"})
BEHAVIOR_FUNNEL_ABS_DELTA = 20
BEHAVIOR_FUNNEL_REL_DELTA = 0.08
BEHAVIOR_FUNNEL_REL_MIN_ABS_DELTA = 5
BEHAVIOR_FUNNEL_STAGES = ("outer_context_pass", "path_pass", "final_veto_pass", "filled_entries")
REJECT_TEST_ELIGIBLE_OUTCOMES = frozenset({"rejected", "duplicate_skipped"})

best_source = ""
best_report: EvaluationReport | None = None
champion_source = ""
champion_report: EvaluationReport | None = None
champion_test_metrics: dict[str, float] | None = None
iteration_counter = 0
reference_stage_started_at = ""
reference_stage_iteration = 0
research_session_state: dict[str, Any] = {}
rejected_test_executor: ProcessPoolExecutor | None = None
rejected_test_futures: dict[Any, dict[str, Any]] = {}
queued_rejected_test_round_dirs: set[str] = set()


def active_exit_params() -> dict[str, Any]:
    return dict(getattr(strategy_module, "EXIT_PARAMS", backtest_module.EXIT_PARAMS))
prepared_backtest_context_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
cached_base_behavior_key = ""
cached_base_behavior_profile: list[dict[str, Any]] | None = None

logging.basicConfig(
    filename=RUNTIME.paths.log_file,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


@dataclass(frozen=True)
class StrategyRoundBrief:
    candidate_id: str
    primary_direction: str
    hypothesis: str
    change_plan: str
    novelty_proof: str
    change_tags: tuple[str, ...]
    expected_effects: tuple[str, ...]
    core_factors: tuple[StrategyCoreFactor, ...]
    exit_range_scan: dict[str, object] | None = None


@dataclass(frozen=True)
class PlannerBriefInvalid(Exception):
    candidate: StrategyCandidate
    block_info: dict[str, Any]
    errors: tuple[str, ...]

    def __str__(self) -> str:
        return self.errors[-1] if self.errors else "planner round brief invalid"


@dataclass(frozen=True)
class StrategyReviewerDecision:
    verdict: str
    reviewer_summary: str
    rejection_type: str
    matched_evidence: str
    must_change: str
    why_not_new: str

    @property
    def is_pass(self) -> bool:
        return self.verdict == "PASS"


@dataclass(frozen=True)
class ReviewerRejected(Exception):
    candidate: StrategyCandidate
    block_info: dict[str, Any]
    errors: tuple[str, ...]

    def __str__(self) -> str:
        return self.errors[-1] if self.errors else "reviewer rejected planner brief"


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


def _round_dir_key(round_dir: Path) -> str:
    return str(round_dir.resolve())


def _test_evaluation_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    payload = metadata.get("test_evaluation")
    return dict(payload) if isinstance(payload, dict) else {}


def _round_artifact_needs_async_test(metadata: dict[str, Any]) -> bool:
    if not metadata:
        return False
    if str(metadata.get("stop_stage", "")).strip() != "full_eval":
        return False
    if str(metadata.get("outcome", "")).strip() not in REJECT_TEST_ELIGIBLE_OUTCOMES:
        return False
    if normalize_test_metrics_payload(metadata.get("test_metrics")):
        return False
    status = str(_test_evaluation_payload(metadata).get("status", "")).strip()
    return status not in {"completed", "failed"}


def _ensure_rejected_test_executor() -> None:
    global rejected_test_executor
    if rejected_test_executor is not None:
        return
    rejected_test_executor = ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
    )


def _queue_round_artifact_test(round_dir: Path, *, reason: str) -> bool:
    metadata = load_round_artifact_metadata(round_dir)
    if not _round_artifact_needs_async_test(metadata):
        return False
    round_key = _round_dir_key(round_dir)
    if round_key in queued_rejected_test_round_dirs:
        return False

    queued_at = datetime.now(UTC).isoformat()
    evaluation = _test_evaluation_payload(metadata)
    if str(evaluation.get("queued_at", "")).strip():
        queued_at = str(evaluation.get("queued_at", "")).strip()
    update_round_artifact_test_payload(
        round_dir,
        test_evaluation={
            "status": "pending",
            "mode": "rejected_async",
            "queued_at": queued_at,
            "error": "",
        },
    )

    _ensure_rejected_test_executor()
    assert rejected_test_executor is not None
    future = rejected_test_executor.submit(
        run_round_artifact_test,
        str(REPO_ROOT),
        str(round_dir),
    )
    queued_rejected_test_round_dirs.add(round_key)
    rejected_test_futures[future] = {
        "round_dir": round_dir,
        "iteration": int(metadata.get("iteration", 0) or 0),
        "candidate_id": str(metadata.get("candidate_id", "")).strip(),
        "queued_at": queued_at,
    }
    log_info(
        f"reject test已入队: iteration={int(metadata.get('iteration', 0) or 0)} "
        f"candidate={str(metadata.get('candidate_id', '')).strip() or '-'} "
        f"reason={reason}"
    )
    return True


def _queue_pending_round_artifact_tests(*, reason: str) -> int:
    rounds_root = RUNTIME.paths.round_artifacts_dir / "rounds"
    if not rounds_root.exists():
        return 0
    queued = 0
    for round_dir in sorted(path for path in rounds_root.iterdir() if path.is_dir()):
        if _queue_round_artifact_test(round_dir, reason=reason):
            queued += 1
    return queued


def _drain_rejected_test_futures() -> None:
    for future, task in list(rejected_test_futures.items()):
        if not future.done():
            continue
        rejected_test_futures.pop(future, None)
        round_dir = Path(task["round_dir"])
        queued_rejected_test_round_dirs.discard(_round_dir_key(round_dir))
        metadata = load_round_artifact_metadata(round_dir)
        queued_at = str(_test_evaluation_payload(metadata).get("queued_at", "")).strip() or str(task["queued_at"])
        completed_at = datetime.now(UTC).isoformat()
        try:
            payload = future.result()
            test_metrics = normalize_test_metrics_payload(payload.get("test_metrics"))
        except Exception as exc:
            update_round_artifact_test_payload(
                round_dir,
                test_evaluation={
                    "status": "failed",
                    "mode": "rejected_async",
                    "queued_at": queued_at,
                    "completed_at": completed_at,
                    "error": str(exc),
                },
            )
            log_info(
                f"reject test失败: iteration={int(task['iteration'])} "
                f"candidate={str(task['candidate_id']) or '-'} error={exc}"
            )
            logging.exception(
                "reject test failed(iteration=%s,candidate=%s)",
                task["iteration"],
                task["candidate_id"],
            )
            continue

        update_round_artifact_test_payload(
            round_dir,
            test_metrics=test_metrics,
            test_evaluation={
                "status": "completed",
                "mode": "rejected_async",
                "queued_at": queued_at,
                "completed_at": completed_at,
                "error": "",
            },
        )
        log_info(
            f"reject test完成: iteration={int(task['iteration'])} "
            f"candidate={str(task['candidate_id']) or '-'} "
            f"return={test_metrics.get('test_total_return_pct', 0.0):.2f}% "
            f"sharpe={test_metrics.get('test_sharpe_ratio', 0.0):.2f} "
            f"max_dd={test_metrics.get('test_max_drawdown', 0.0):.2f}%"
        )


def _build_model_progress_callback(phase: str, *, repair_attempt: int | None = None):
    def callback(payload: dict[str, Any]) -> None:
        event = str(payload.get("event", "")).strip() or "heartbeat"
        timeout_seconds = int(payload.get("timeout_seconds", 0) or 0)
        elapsed_seconds = int(payload.get("elapsed_seconds", 0) or 0)

        message = f"iteration {iteration_counter} {phase}"
        if event == "started":
            message += " started"
        elif event == "thread_started":
            thread_id = str(payload.get("thread_id", "")).strip()
            message += f" thread_started {thread_id}" if thread_id else " thread_started"
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
        if str(payload.get("thread_id", "")).strip():
            heartbeat_payload["session_id"] = str(payload.get("thread_id", "")).strip()
        if repair_attempt is not None:
            heartbeat_payload["repair_attempt"] = repair_attempt

        write_heartbeat("model_waiting", **heartbeat_payload)

    return callback


def _estimate_prompt_tokens(*parts: str) -> int:
    total_chars = sum(len(str(part or "")) for part in parts)
    return max(1, (total_chars + 3) // 4)


def _append_model_call_telemetry(payload: dict[str, Any]) -> None:
    path = RUNTIME.paths.model_call_log_file
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _benchmark_role() -> str:
    return "champion" if champion_report is not None else "baseline"


def _reference_role() -> str:
    return "champion" if champion_report is not None else "baseline"


def _session_scope_payload() -> dict[str, Any]:
    reference_code_hash = source_hash(best_source) if best_source else ""
    return {
        "score_regime": SCORE_REGIME,
        "reference_role": _reference_role() if best_report is not None else "",
        "reference_code_hash": reference_code_hash,
        "reference_stage_started_at": reference_stage_started_at,
        "reference_stage_iteration": int(reference_stage_iteration or 0),
    }


def _load_research_session_state() -> dict[str, Any]:
    global research_session_state
    if research_session_state:
        return dict(research_session_state)
    path = RUNTIME.paths.session_state_file
    if not path.exists():
        research_session_state = {}
        return {}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        research_session_state = {}
        return {}
    research_session_state = payload if isinstance(payload, dict) else {}
    return dict(research_session_state)


def _persist_research_session_state(payload: dict[str, Any]) -> None:
    global research_session_state
    path = RUNTIME.paths.session_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2))
    temp_path.replace(path)
    research_session_state = normalized


def _clear_research_session_state(*, remove_workspace: bool = False, reason: str = "") -> None:
    global research_session_state
    research_session_state = {}
    if RUNTIME.paths.session_state_file.exists():
        RUNTIME.paths.session_state_file.unlink()
    if remove_workspace and RUNTIME.paths.agent_workspace_dir.exists():
        shutil.rmtree(RUNTIME.paths.agent_workspace_dir, ignore_errors=True)
    if reason:
        log_info(f"研究 session 已重置: {reason}")


def _session_state_matches_current_stage(
    state: dict[str, Any] | None,
) -> bool:
    if not isinstance(state, dict) or not state:
        return False
    scope = _session_scope_payload()
    if not scope["reference_code_hash"]:
        return False
    return all(str(state.get(key, "")).strip() == str(scope.get(key, "")).strip() for key in scope)


def _active_research_session_id() -> str:
    state = _load_research_session_state()
    if not _session_state_matches_current_stage(state):
        return ""
    return str(state.get("session_id", "")).strip()


def _store_research_session_metadata(
    *,
    session_id: str,
    workspace_root: Path,
) -> None:
    if not session_id:
        return
    previous = _load_research_session_state()
    now = datetime.now(UTC).isoformat()
    payload = {
        **_session_scope_payload(),
        "session_id": session_id,
        "workspace_root": str(workspace_root),
        "created_at": str(previous.get("created_at", "")).strip() or now,
        "updated_at": now,
    }
    _persist_research_session_state(payload)


def _align_research_session_scope(
    *,
    force_reset: bool = False,
) -> None:
    state = _load_research_session_state()
    if force_reset:
        _clear_research_session_state(remove_workspace=True, reason="reference scope changed by explicit reset")
        return
    if state and not _session_state_matches_current_stage(state):
        _clear_research_session_state(remove_workspace=True, reason="reference scope changed")


def _refresh_prompt_memory_snapshots() -> str:
    if best_report is None:
        return ""
    benchmark_report = _reference_benchmark_report()
    reference_metrics = benchmark_report.metrics if benchmark_report is not None else best_report.metrics
    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    return build_journal_prompt_summary(
        journal_entries,
        limit=RUNTIME.max_recent_journal_entries,
        journal_path=RUNTIME.paths.journal_file,
        prompt_compact=True,
        current_score_regime=SCORE_REGIME,
        current_iteration=iteration_counter or (
            max((int(entry.get("iteration", 0) or 0) for entry in journal_entries), default=0) + 1
        ),
        active_stage_started_at=reference_stage_started_at,
        active_stage_iteration=reference_stage_iteration,
        active_reference_code_hash=source_hash(best_source) if best_source else "",
        reference_role=_reference_role(),
        reference_metrics=reference_metrics,
        memory_root=RUNTIME.paths.memory_dir,
    )


def _load_operator_focus_text(*, max_chars: int = 1800) -> str:
    path = RUNTIME.paths.operator_focus_file
    if not path.exists():
        return ""
    text = path.read_text().strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n\n[operator focus 已按长度截断]"


def _extract_champion_review_hash(text: str) -> str:
    match = re.search(r"(?im)^\s*(?:[-*]\s*)?champion_code_hash\s*:\s*`?([0-9a-f]{64})`?\s*$", text)
    return match.group(1).lower() if match else ""


def _load_champion_review_text(*, active_code_hash: str, max_chars: int = 1800) -> str:
    path = RUNTIME.paths.champion_review_file
    if not path.exists() or not active_code_hash:
        return ""
    text = path.read_text().strip()
    if not text:
        return ""
    expected_hash = _extract_champion_review_hash(text)
    if expected_hash != active_code_hash.lower():
        return ""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n\n[champion review 已按长度截断]"


def _ensure_workspace_link(link_path: Path, target_path: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        try:
            if link_path.resolve() == target_path.resolve():
                return
        except OSError:
            pass
        link_path.unlink()
    elif link_path.exists():
        if link_path.is_dir():
            shutil.rmtree(link_path)
        else:
            link_path.unlink()
    link_path.symlink_to(target_path, target_is_directory=target_path.is_dir())


def _prepare_agent_workspace(
    *,
    base_source: str,
    reset_strategy: bool,
) -> Path:
    _refresh_prompt_memory_snapshots()
    workspace_root = RUNTIME.paths.agent_workspace_dir
    workspace_root.mkdir(parents=True, exist_ok=True)

    (workspace_root / "src").mkdir(parents=True, exist_ok=True)
    (workspace_root / "wiki").mkdir(parents=True, exist_ok=True)
    workspace_strategy_file = workspace_root / MODEL_WORKSPACE_STRATEGY_PATH
    if reset_strategy or not workspace_strategy_file.exists():
        write_strategy_source(workspace_strategy_file, base_source)

    agents_path = workspace_root / "AGENTS.md"
    agents_path.write_text(
        build_strategy_agents_instructions(),
    )

    _ensure_workspace_link(workspace_root / "src/backtest_macd_aggressive.py", RUNTIME.paths.backtest_file)
    if RUNTIME.paths.operator_focus_file.exists():
        _ensure_workspace_link(
            workspace_root / "config/research_v2_operator_focus.md",
            RUNTIME.paths.operator_focus_file,
        )
    if RUNTIME.paths.champion_review_file.exists():
        _ensure_workspace_link(
            workspace_root / "config/research_v2_champion_review.md",
            RUNTIME.paths.champion_review_file,
        )
    if (REPO_ROOT / "data").exists():
        _ensure_workspace_link(workspace_root / "data", REPO_ROOT / "data")
    _ensure_workspace_link(workspace_root / "memory", RUNTIME.paths.memory_dir)
    reviewer_summary_path = _reviewer_summary_card_path()
    if not reviewer_summary_path.exists():
        reviewer_summary_path.parent.mkdir(parents=True, exist_ok=True)
        reviewer_summary_path.write_text("")
    memory_paths = {
        "history_package": RUNTIME.paths.memory_dir / "prompt/latest_history_package.md",
        "duplicate_watchlist": RUNTIME.paths.memory_dir / "wiki/duplicate_watchlist.md",
        "failure_wiki_md": RUNTIME.paths.memory_dir / "wiki/failure_wiki.md",
        "failure_wiki_json": RUNTIME.paths.memory_dir / "wiki/failure_wiki_index.json",
        "direction_board_md": RUNTIME.paths.memory_dir / "wiki/direction_board.md",
        "direction_board_json": RUNTIME.paths.memory_dir / "wiki/direction_board.json",
        "reviewer_summary_card": RUNTIME.paths.memory_dir / "wiki/reviewer_summary_card.md",
        "last_rejected_candidate": RUNTIME.paths.memory_dir / "wiki/last_rejected_candidate.py",
        "last_rejected_snapshot": RUNTIME.paths.memory_dir / "wiki/last_rejected_snapshot.md",
    }
    for key, target_path in memory_paths.items():
        if target_path.exists():
            link_name = {
                "history_package": workspace_root / "wiki/latest_history_package.md",
                "duplicate_watchlist": workspace_root / "wiki/duplicate_watchlist.md",
                "failure_wiki_md": workspace_root / "wiki/failure_wiki.md",
                "failure_wiki_json": workspace_root / "wiki/failure_wiki_index.json",
                "direction_board_md": workspace_root / "wiki/direction_board.md",
                "direction_board_json": workspace_root / "wiki/direction_board.json",
                "reviewer_summary_card": workspace_root / "wiki/reviewer_summary_card.md",
                "last_rejected_candidate": workspace_root / "wiki/last_rejected_candidate.py",
                "last_rejected_snapshot": workspace_root / "wiki/last_rejected_snapshot.md",
            }[key]
            _ensure_workspace_link(link_name, target_path)
    return workspace_root


def _reviewer_summary_card_path(memory_root: Path | None = None) -> Path:
    root = memory_root or RUNTIME.paths.memory_dir
    return root / "wiki/reviewer_summary_card.md"


def _load_reviewer_summary_text(memory_root: Path | None = None) -> str:
    path = _reviewer_summary_card_path(memory_root)
    if not path.exists():
        return ""
    try:
        return path.read_text()
    except OSError:
        return ""


def _persist_reviewer_summary_card(
    *,
    memory_root: Path,
    round_brief: StrategyRoundBrief,
    decision: StrategyReviewerDecision,
    iteration_id: int,
    stage_label: str,
) -> None:
    path = _reviewer_summary_card_path(memory_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Reviewer Summary Card",
                "",
                "说明：",
                "- 这张卡来自上一轮 reviewer 审稿，不是人工硬限制。",
                "- 这张卡只保留当前轮最后一次 reviewer 判定；如果同轮先 REVISE 后 PASS，最终卡记录最后一次 PASS。",
                "- planner 下一版开始前先看这里，再决定是否继续原方向。",
                "- 若 `verdict=REVISE`，说明上一版 draft 当前不值得进入落码；必须先吸收打回理由，再重写 draft。",
                "",
                f"- iteration: {iteration_id}",
                f"- stage_label: {stage_label}",
                f"- candidate_id: {round_brief.candidate_id or '-'}",
                f"- primary_direction: {round_brief.primary_direction or '-'}",
                f"- verdict: {decision.verdict}",
                f"- reviewer_summary: {decision.reviewer_summary or '-'}",
                f"- rejection_type: {decision.rejection_type or '-'}",
                f"- matched_evidence: {decision.matched_evidence or '-'}",
                f"- must_change: {decision.must_change or '-'}",
                f"- why_not_new: {decision.why_not_new or '-'}",
            ]
        )
        + "\n"
    )


def _workspace_strategy_path(workspace_root: Path) -> Path:
    return workspace_root / MODEL_WORKSPACE_STRATEGY_PATH


def _rebase_workspace_strategy_to_base(*, workspace_root: Path, base_source: str) -> None:
    # 同轮重生必须回到当前正确基底，避免在已知坏版本上继续叠改。
    write_strategy_source(_workspace_strategy_path(workspace_root), base_source)


def _persist_last_rejected_candidate_snapshot(
    *,
    memory_root: Path,
    base_source: str,
    failed_candidate: StrategyCandidate,
    block_info: dict[str, Any],
) -> None:
    wiki_root = memory_root / "wiki"
    wiki_root.mkdir(parents=True, exist_ok=True)

    candidate_path = wiki_root / "last_rejected_candidate.py"
    snapshot_path = wiki_root / "last_rejected_snapshot.md"
    candidate_path.write_text(normalize_strategy_source(failed_candidate.strategy_code))

    diff_summary = build_diff_summary(base_source, failed_candidate.strategy_code, limit=20)
    diff_lines = "\n".join(f"- {line}" for line in diff_summary) if diff_summary else "- 无有效 diff 摘要"
    expected_effects = (
        "\n".join(f"- {item}" for item in failed_candidate.expected_effects)
        if failed_candidate.expected_effects
        else "- 无"
    )
    snapshot_path.write_text(
        "\n".join(
            [
                "# Last Rejected Candidate Snapshot",
                "",
                "说明：",
                "- 这个快照只用于参考刚被系统拒收的代码与失败原因。",
                "- 新一轮同轮重生必须以当前正确基底为起点，不要直接在这版错误代码上继续叠改。",
                "- 如果上一版里有局部思路仍然值得保留，必须在正确基底上重新实现。",
                "",
                f"- candidate_id: {failed_candidate.candidate_id or '-'}",
                f"- primary_direction: {failed_candidate.primary_direction or '-'}",
                f"- blocked_kind: {str(block_info.get('block_kind', '')).strip() or '-'}",
                f"- blocked_cluster: {str(block_info.get('blocked_cluster', '')).strip() or '-'}",
                f"- blocked_reason: {str(block_info.get('blocked_reason', '')).strip() or '-'}",
                f"- hypothesis: {failed_candidate.hypothesis or '-'}",
                f"- change_plan: {failed_candidate.change_plan or '-'}",
                f"- change_tags: {', '.join(failed_candidate.change_tags) or '-'}",
                f"- edited_regions: {', '.join(failed_candidate.edited_regions) or '-'}",
                "",
                "expected_effects:",
                expected_effects,
                "",
                "diff_summary:",
                diff_lines,
                "",
                f"- failed_candidate_code: `{candidate_path.name}`",
                "- editable_base: `src/strategy_macd_aggressive.py`",
            ]
        )
        + "\n"
    )


# ==================== 模块热加载 ====================


def reload_strategy_module() -> None:
    global strategy_module
    importlib.invalidate_caches()
    importlib.reload(strategy_module)


def _model_client_config():
    return replace(
        load_strategy_client_config(),
        approval_policy="never",
        sandbox="danger-full-access",
        use_ephemeral=False,
        dangerously_bypass_approvals_and_sandbox=True,
    )


def _planner_uses_deepseek(session_kind: str) -> bool:
    return session_kind == "planner" and load_deepseek_planner_config().enabled


def _embed_workspace_agents_for_api(system_prompt: str, workspace_root: Path) -> str:
    agents_path = workspace_root / "AGENTS.md"
    if not agents_path.exists():
        return system_prompt
    try:
        agents_text = agents_path.read_text().strip()
    except OSError:
        return system_prompt
    if not agents_text:
        return system_prompt
    return (
        f"{system_prompt.strip()}\n\n"
        "以下是当前工作区 `AGENTS.md` 的完整内容。"
        "你无法自动读取本地文件，因此必须把下面这份文本当成已展开的长期规则严格遵守：\n\n"
        f"{agents_text}"
    )


def _complexity_level_rank(level: str) -> int:
    return {
        "normal": 0,
        "warning_1": 1,
        "warning_2": 2,
        "hard_cap": 3,
    }.get(str(level or "normal").strip(), 0)

def _current_stage_journal_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scoped = [
        entry for entry in entries
        if str(entry.get("score_regime", "")).strip() == SCORE_REGIME
    ]
    if not scoped:
        return []
    stage_started_at_dt = _parse_state_timestamp(reference_stage_started_at)
    if stage_started_at_dt is not None:
        timed_current_entries = [
            entry
            for entry in scoped
            if (
                _parse_state_timestamp(entry.get("timestamp")) is not None
                and _parse_state_timestamp(entry.get("timestamp")) >= stage_started_at_dt
            )
        ]
        if timed_current_entries:
            boundary_iteration = next(
                (
                    int(entry.get("iteration", 0) or 0)
                    for entry in timed_current_entries
                    if int(entry.get("iteration", 0) or 0) > 0
                ),
                0,
            )
            current_stage_entries: list[dict[str, Any]] = []
            for entry in scoped:
                entry_ts = _parse_state_timestamp(entry.get("timestamp"))
                try:
                    entry_iteration = int(entry.get("iteration", 0) or 0)
                except (TypeError, ValueError):
                    entry_iteration = 0
                if entry_ts is not None:
                    if entry_ts >= stage_started_at_dt:
                        current_stage_entries.append(entry)
                    continue
                if boundary_iteration > 0 and entry_iteration >= boundary_iteration:
                    current_stage_entries.append(entry)
            return current_stage_entries
    if reference_stage_iteration <= 0:
        return scoped
    return [
        entry for entry in scoped
        if int(entry.get("iteration", 0) or 0) >= reference_stage_iteration
    ]

def _load_saved_reference_state() -> dict[str, Any]:
    return load_saved_reference_state_helper(RUNTIME.paths.best_state_file)


def _reference_benchmark_report() -> EvaluationReport | None:
    return best_report


def _discord_data_range_text() -> str:
    return (
        f"train {RUNTIME.windows.development_start_date}~{RUNTIME.windows.development_end_date} / "
        f"val {RUNTIME.windows.validation_start_date}~{RUNTIME.windows.validation_end_date} / "
        f"test {RUNTIME.windows.test_start_date}~{RUNTIME.windows.test_end_date}"
    )


def _state_test_metrics(saved_state: dict[str, Any]) -> dict[str, float]:
    return normalize_test_metrics_payload(
        saved_state.get("test_metrics") or saved_state.get("shadow_test_metrics")
    )


def _parse_state_timestamp(value: Any) -> datetime | None:
    return parse_state_timestamp_helper(value)


def _recover_reference_stage_state(
    saved_state: dict[str, Any],
    journal_entries: list[dict[str, Any]],
    *,
    reference_code_hash: str,
) -> tuple[str, int]:
    return recover_reference_stage_state_helper(
        saved_state,
        journal_entries,
        score_regime=SCORE_REGIME,
        reference_code_hash=reference_code_hash,
    )


def _saved_report_payload(
    source: str,
    report: EvaluationReport,
    *,
    test_metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    return saved_report_payload_helper(
        source,
        report,
        test_metrics=test_metrics,
    )


def _report_from_saved_payload(payload: dict[str, Any]) -> EvaluationReport | None:
    return report_from_saved_payload_helper(payload)


def _reference_manifest_payload(
    source: str,
    report: EvaluationReport,
    *,
    test_metrics: dict[str, float] | None = None,
    stage_started_at: str = "",
    stage_iteration: int = 0,
    suppress_initialize_saved_reference_discord_once: bool = False,
) -> dict[str, Any]:
    return reference_manifest_payload_helper(
        source,
        report,
        score_regime=SCORE_REGIME,
        test_metrics=test_metrics,
        stage_started_at=stage_started_at,
        stage_iteration=stage_iteration,
        suppress_initialize_saved_reference_discord_once=suppress_initialize_saved_reference_discord_once,
    )


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

    def __init__(self, candidate: StrategyCandidate, errors: list[str], failure_stage: str = ""):
        self.candidate = candidate
        self.errors = errors
        self.failure_stage = failure_stage
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


def _stable_cache_text(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _file_cache_signature(path: Path | str) -> dict[str, Any]:
    resolved = Path(path).expanduser()
    try:
        stat = resolved.stat()
    except OSError:
        return {
            "path": str(resolved),
            "exists": False,
        }
    try:
        normalized_path = str(resolved.resolve())
    except OSError:
        normalized_path = str(resolved)
    return {
        "path": normalized_path,
        "exists": True,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _backtest_data_file_signatures() -> dict[str, Any]:
    return {
        "intraday": _file_cache_signature(backtest_module.DEFAULT_INTRADAY_FILE),
        "hourly": _file_cache_signature(backtest_module.DEFAULT_HOURLY_FILE),
        "fourh": _file_cache_signature(backtest_module.DEFAULT_FOURH_FILE),
        "sentiment": _file_cache_signature(backtest_module.DEFAULT_SENTIMENT_FILE),
        "execution": _file_cache_signature(backtest_module.DEFAULT_EXECUTION_FILE),
        "funding": _file_cache_signature(backtest_module.DEFAULT_FUNDING_FILE),
    }


def _round_artifact_engine_file_signatures() -> dict[str, Any]:
    return {
        "research_script": _file_cache_signature(SCRIPT_DIR / "research_macd_aggressive_v2.py"),
        "backtest": _file_cache_signature(RUNTIME.paths.backtest_file),
        "config": _file_cache_signature(SRC_DIR / "research_v2/config.py"),
        "evaluation": _file_cache_signature(SRC_DIR / "research_v2/evaluation.py"),
        "evaluation_summary": _file_cache_signature(SRC_DIR / "research_v2/evaluation_summary.py"),
        "windows": _file_cache_signature(SRC_DIR / "research_v2/windows.py"),
    }


def _round_artifact_chart_paths(chart_paths: PerformanceChartPaths | None) -> dict[str, Path | None]:
    if chart_paths is None:
        return {}
    return {
        "validation_chart": chart_paths.validation_chart,
        "selection_chart": chart_paths.selection_chart,
    }


def _persist_round_artifact(
    entry: dict[str, Any],
    *,
    strategy_source: str,
    test_metrics: dict[str, float] | None = None,
    test_evaluation: dict[str, Any] | None = None,
    champion_snapshot_dir: Path | None = None,
    chart_paths: PerformanceChartPaths | None = None,
) -> Path:
    return persist_round_artifact_helper(
        RUNTIME.paths.round_artifacts_dir,
        repo_root=RUNTIME.paths.repo_root,
        entry=entry,
        strategy_source=strategy_source,
        windows=asdict(RUNTIME.windows),
        gates=asdict(RUNTIME.gates),
        scoring=asdict(RUNTIME.scoring),
        data_fingerprints=_backtest_data_file_signatures(),
        engine_fingerprints=_round_artifact_engine_file_signatures(),
        test_metrics=test_metrics,
        test_evaluation=test_evaluation,
        champion_snapshot_dir=champion_snapshot_dir,
        chart_paths=_round_artifact_chart_paths(chart_paths),
    )


def _append_research_journal_entry(
    entry: dict[str, Any],
    *,
    strategy_source: str,
    test_metrics: dict[str, float] | None = None,
    test_evaluation: dict[str, Any] | None = None,
    persist_round_artifact: bool = True,
) -> Path | None:
    round_dir: Path | None = None
    append_journal_entry(RUNTIME.paths.journal_file, entry)
    append_journal_archive(RUNTIME.paths.memory_dir, entry)
    if persist_round_artifact:
        try:
            round_dir = _persist_round_artifact(
                entry,
                strategy_source=strategy_source,
                test_metrics=test_metrics,
                test_evaluation=test_evaluation,
            )
        except Exception as exc:
            log_info(f"round artifact 归档失败: {exc}")
            logging.exception("round artifact persist failed(iteration=%s)", entry.get("iteration"))
    _refresh_prompt_memory_snapshots()
    return round_dir


def _context_relevant_exit_params(exit_params: dict[str, Any]) -> dict[str, Any]:
    return {
        key: exit_params.get(key)
        for key in sorted(CONTEXT_RELEVANT_EXIT_PARAM_KEYS)
        if key in exit_params
    }


def _prepared_backtest_context_cache_key(
    strategy_params: dict[str, Any],
    *,
    exit_params: dict[str, Any],
) -> str:
    return _stable_cache_text(
        {
            "strategy_params": strategy_params,
            "context_exit_params": _context_relevant_exit_params(exit_params),
            "files": _backtest_data_file_signatures(),
        }
    )


def _prepare_backtest_context() -> dict[str, Any]:
    exit_params = active_exit_params()
    cache_key = _prepared_backtest_context_cache_key(
        strategy_module.PARAMS,
        exit_params=exit_params,
    )
    cached = prepared_backtest_context_cache.pop(cache_key, None)
    if cached is not None:
        prepared_backtest_context_cache[cache_key] = cached
        return cached

    prepared_context = backtest_module.prepare_backtest_context(
        strategy_module.PARAMS,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        exit_params=exit_params,
    )
    prepared_backtest_context_cache[cache_key] = prepared_context
    while len(prepared_backtest_context_cache) > PREPARED_BACKTEST_CONTEXT_CACHE_LIMIT:
        prepared_backtest_context_cache.popitem(last=False)
    return prepared_context


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
    early_reject_milestones = set(getattr(RUNTIME, "early_reject_milestones", tuple()) or tuple())
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
            exit_params=active_exit_params(),
            include_diagnostics=include_diagnostics,
            prepared_context=runtime_context,
        )
        results.append({"window": window, "result": result})

        if allow_early_reject and window.group == "eval":
            eval_count += 1
            if eval_start_date is None:
                eval_start_date = window.start_date
            should_check_early_reject = (
                check_at > 0
                and eval_count >= check_at
                and (not early_reject_milestones or eval_count in early_reject_milestones)
            )
            if should_check_early_reject:
                snapshot_result = backtest_module.backtest_macd_aggressive(
                    strategy_func=strategy_module.strategy,
                    intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
                    hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
                    start_date=eval_start_date or window.start_date,
                    end_date=window.end_date,
                    strategy_params=strategy_module.PARAMS,
                    exit_params=active_exit_params(),
                    include_diagnostics=True,
                    prepared_context=runtime_context,
                )
                snapshot = partial_eval_gate_snapshot(snapshot_result)
                if (
                    snapshot["segment_count"] >= float(RUNTIME.early_reject_min_segments)
                    and snapshot["trend_score"] < RUNTIME.early_reject_trend_score_threshold
                    and snapshot["period_score"] < RUNTIME.early_reject_trend_score_threshold
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
        exit_params=active_exit_params(),
        include_diagnostics=include_diagnostics,
        prepared_context=prepared_context,
    )


def _run_test_backtest(
    prepared_context: dict[str, Any],
    *,
    include_diagnostics: bool = True,
    heartbeat_phase: str = "test_eval",
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
        exit_params=active_exit_params(),
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


def _champion_history_dir() -> Path:
    path = RUNTIME.paths.best_strategy_file.parent / "champion_history"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _champion_snapshot_stamp(timestamp_text: str) -> str:
    return champion_snapshot_stamp_helper(timestamp_text)


def _safe_snapshot_slug(text: str, default: str = "champion") -> str:
    return safe_snapshot_slug_helper(text, default=default)


def _archive_champion_snapshot(
    *,
    iteration_id: int,
    accepted_at: str,
    candidate: StrategyCandidate,
    source: str,
    report: EvaluationReport,
    test_metrics: dict[str, float] | None = None,
    chart_paths: PerformanceChartPaths | None = None,
) -> Path:
    snapshot_dir = archive_champion_snapshot_helper(
        _champion_history_dir(),
        iteration_id=iteration_id,
        accepted_at=accepted_at,
        candidate=candidate,
        source=source,
        report=report,
        test_metrics=test_metrics,
        chart_paths=chart_paths,
    )
    log_info(f"champion快照已归档: {snapshot_dir}")
    return snapshot_dir


def _build_chart_note(message: str) -> str:
    return build_chart_note_helper(message)


def _generate_new_champion_charts(
    iteration_id: int,
    *,
    test_result: dict[str, Any] | None = None,
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
        exit_params=active_exit_params(),
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
        secondary_daily_equity_curve=(test_result or {}).get("daily_equity_curve", []),
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


def evaluate_current_strategy(
    allow_early_reject: bool = False,
    *,
    plateau_probe_candidate: StrategyCandidate | None = None,
    plateau_probe_base_source: str = "",
) -> EvaluationReport:
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
    plateau_probe_result: dict[str, Any] | None = None
    if plateau_probe_candidate is not None and plateau_probe_base_source:
        plateau_probe_result = _run_candidate_plateau_probe(
            plateau_probe_candidate,
            base_source=plateau_probe_base_source,
        )
    return summarize_evaluation(
        base_results,
        RUNTIME.gates,
        selection_period_result=selection_period_result,
        validation_continuous_result=validation_continuous_result,
        scoring=RUNTIME.scoring,
        plateau_probe=plateau_probe_result,
    )


def evaluate_test_metrics() -> dict[str, float]:
    return summarize_test_result(evaluate_test_result())


def evaluate_test_result() -> dict[str, Any]:
    prepared_context = _prepare_backtest_context()
    return _run_test_backtest(prepared_context, include_diagnostics=True)


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
    return _behavior_profile_from_results(results)


def _behavior_profile_from_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "window": item["window"].label,
            "fingerprint": _window_behavior_fingerprint(item["result"]),
            "funnel": item["result"].get("strategy_funnel", {}) or {},
            "filled_side_entries": item["result"].get("filled_side_entries", {}) or {},
        }
        for item in results
    ]


def _smoke_window_signature() -> str:
    return _stable_cache_text(
        [
            {
                "group": window.group,
                "label": window.label,
                "start_date": window.start_date,
                "end_date": window.end_date,
            }
            for window in _selected_smoke_windows()
        ]
    )


def _base_behavior_profile_cache_key(base_source: str) -> str:
    return _stable_cache_text(
        {
            "base_source_hash": source_hash(base_source),
            "smoke_windows": _smoke_window_signature(),
            "files": _backtest_data_file_signatures(),
        }
    )


def _base_behavior_profile(base_source: str) -> list[dict[str, Any]]:
    global cached_base_behavior_key, cached_base_behavior_profile

    cache_key = _base_behavior_profile_cache_key(base_source)
    if cache_key == cached_base_behavior_key and cached_base_behavior_profile is not None:
        return cached_base_behavior_profile

    write_strategy_source(RUNTIME.paths.strategy_file, base_source)
    reload_strategy_module()
    profile = _smoke_behavior_profile(heartbeat_phase="base_smoke_behavior")
    cached_base_behavior_key = cache_key
    cached_base_behavior_profile = profile
    return profile


def _behavior_funnel_stage_changed(base_value: int, candidate_value: int) -> bool:
    delta = abs(candidate_value - base_value)
    if delta >= BEHAVIOR_FUNNEL_ABS_DELTA:
        return True
    baseline = max(abs(base_value), 1)
    return delta >= BEHAVIOR_FUNNEL_REL_MIN_ABS_DELTA and (delta / baseline) >= BEHAVIOR_FUNNEL_REL_DELTA


def _behavior_funnel_changed(
    base_profile: list[dict[str, Any]],
    candidate_profile: list[dict[str, Any]],
) -> bool:
    base_funnel = _behavior_profile_summary(base_profile).get("funnel", {})
    candidate_funnel = _behavior_profile_summary(candidate_profile).get("funnel", {})
    for side in ("long", "short"):
        base_side = base_funnel.get(side, {}) or {}
        candidate_side = candidate_funnel.get(side, {}) or {}
        for stage in BEHAVIOR_FUNNEL_STAGES:
            if _behavior_funnel_stage_changed(
                int(base_side.get(stage, 0) or 0),
                int(candidate_side.get(stage, 0) or 0),
            ):
                return True
    return False


def _behavior_profile_changed(
    base_profile: list[dict[str, Any]],
    candidate_profile: list[dict[str, Any]],
) -> bool:
    base_fingerprints = [(item.get("window"), item.get("fingerprint")) for item in base_profile]
    candidate_fingerprints = [(item.get("window"), item.get("fingerprint")) for item in candidate_profile]
    if base_fingerprints != candidate_fingerprints:
        return True
    return _behavior_funnel_changed(base_profile, candidate_profile)


def _behavior_profile_summary(profile: list[dict[str, Any]]) -> dict[str, Any]:
    funnel_summary = {
        side: {
            "sideways_pass": 0,
            "outer_context_pass": 0,
            "path_pass": 0,
            "final_veto_pass": 0,
            "filled_entries": 0,
        }
        for side in ("long", "short")
    }
    trades = sum(int(item["fingerprint"].get("trades", 0) or 0) for item in profile)
    returns = [
        float(item["fingerprint"].get("return", 0.0) or 0.0)
        for item in profile
    ]
    signal_counts: dict[str, int] = {}
    for item in profile:
        for signal, entries, *_rest in item["fingerprint"].get("signal_stats", ()):
            signal_counts[str(signal)] = signal_counts.get(str(signal), 0) + int(entries)
        for side in ("long", "short"):
            side_funnel = item.get("funnel", {}).get(side, {}) or {}
            for stage in ("sideways_pass", "outer_context_pass", "path_pass", "final_veto_pass"):
                funnel_summary[side][stage] += int(side_funnel.get(stage, 0) or 0)
            side_entries = item.get("filled_side_entries", {}) or {}
            funnel_summary[side]["filled_entries"] += int(side_entries.get(side, 0) or 0)
    return {
        "window_count": len(profile),
        "total_trades": trades,
        "returns": returns,
        "signal_entries": signal_counts,
        "funnel": funnel_summary,
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


def _format_funnel_summary(summary: dict[str, Any]) -> str:
    funnel = summary.get("funnel", {}) or {}
    parts: list[str] = []
    for side in ("long", "short"):
        side_payload = funnel.get(side, {}) or {}
        parts.append(
            f"{side}: 横盘后{int(side_payload.get('sideways_pass', 0) or 0)} -> "
            f"outer {int(side_payload.get('outer_context_pass', 0) or 0)} -> "
            f"path {int(side_payload.get('path_pass', 0) or 0)} -> "
            f"veto {int(side_payload.get('final_veto_pass', 0) or 0)} -> "
            f"出单 {int(side_payload.get('filled_entries', 0) or 0)}"
        )
    return " | ".join(parts)


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
    candidate_signature = exploration_signature_for_candidate(
        candidate,
        base_source=base_source,
    )
    cluster_key = (
        str(candidate_signature.get("cluster_key", "")).strip()
        or cluster_key_for_components("", candidate.change_tags)
        or "-"
    )
    changed_regions = ", ".join(sorted(candidate_signature["changed_regions"])) or "-"
    ordinary_families = ", ".join(sorted(candidate_signature["ordinary_region_families"])) or "-"
    target_family = str(candidate_signature.get("target_family", "")).strip() or "-"
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
        f"- 当前参考漏斗: {_format_funnel_summary(behavior_diff.get('base', {}) or {})}",
        f"- 候选漏斗: {_format_funnel_summary(behavior_diff.get('candidate', {}) or {})}",
        f"- 最近连续 behavioral_noop: {noop_streak}",
        "- 先复盘: 上一版局部假设在当前基底上没有触达真实行为层；先判断是目标层选错，还是步长太小，再决定继续同方向还是转向。",
    ]
    if "strategy" not in candidate_signature["region_families"]:
        feedback_lines.append(
            "- 本轮没有改 `strategy()` 最终放行层；只改 helper 时，若现有 `strategy()` 本身进不到那条路径，真实成交集合大概率不会变。"
        )
    if not candidate_signature["ordinary_region_families"]:
        feedback_lines.append(
            "- 本轮真实 diff 只动了 `strategy()` / `PARAMS` 特殊区域；这是允许的，但前提是你必须明确改变最终信号集合或退出集合，否则最容易继续落成 behavioral_noop。"
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
        feedback_lines.append(
            "- 最近已连续多次 behavior 无变化；这次必须明显加大步长：优先切不同方向簇。"
        )
        feedback_lines.append(
            "- 如果仍留在同簇，必须明确更换外层 choke point、最终 veto/followthrough 或目标侧，不要只拨单个细阈值。"
        )
        feedback_lines.append(
            "- 默认把上一版局部 hypothesis / change_plan 视为已被证伪，不要只换候选名、tag 或措辞。"
        )
    return {
        "block_kind": "behavioral_noop",
        "blocked_cluster": cluster_key,
        "blocked_reason": "smoke 行为指纹与当前主参考完全一致",
        "current_locks": tuple(),
        "feedback_note": "\n".join(feedback_lines),
    }


def _candidate_invalid_generation_block_info(
    candidate: StrategyCandidate,
    *,
    base_source: str,
) -> dict[str, Any] | None:
    candidate_signature = exploration_signature_for_candidate(
        candidate,
        base_source=base_source,
    )
    candidate_hash = source_hash(candidate.strategy_code)
    base_hash = source_hash(base_source)
    actual_changed_regions = sorted(candidate_signature["changed_regions"])
    diff_summary = build_diff_summary(base_source, candidate.strategy_code, limit=12)

    invalid_reasons: list[str] = []
    if candidate_hash == base_hash:
        invalid_reasons.append("候选源码与当前主参考完全相同")
    if not actual_changed_regions:
        invalid_reasons.append("系统未检测到任何真实可编辑区域改动")
    if not diff_summary:
        invalid_reasons.append("候选没有形成可验证的有效 diff")

    if not invalid_reasons:
        return None

    blocked_tags = sorted(
        tag
        for tag in candidate.change_tags
        if str(tag).strip().lower() in {"blocked", "no_edit", "no_change", "blocked_env", "sandbox_blocked"}
    )
    feedback_lines = [
        "候选无效：本轮没有形成真实代码改动。",
        "- 你的任务只有两步：先确定一个单一策略方向，再把这个方向直接落到 `src/strategy_macd_aggressive.py`。",
        "- 先做策略判断，再落代码；不要把“解释为什么没改”当成提交结果。",
        f"- 系统检测改动区域: {', '.join(actual_changed_regions) or '-'}",
        f"- diff 摘要条数: {len(diff_summary)}",
        f"- 无效原因: {'；'.join(invalid_reasons)}",
        "- `wiki/direction_board.md` / `wiki/duplicate_watchlist.md` / `wiki/failure_wiki.md` / `wiki/latest_history_package.md` 是高优先级参考，但读不到它们不是合法 no-edit 理由。",
        "- 即使辅助记忆文件暂不可用，也必须继续以 `src/strategy_macd_aggressive.py` 为事实源直接改代码。",
        "- 禁止继续提交 `blocked` / `no_edit` / `no_change` / “未执行代码改动” 这类占位答案。",
        "- 下一版提交前自检：至少一处真实源码区域真的发生变化，且文本摘要只描述你已经实际落到代码里的改动。",
    ]
    if blocked_tags:
        feedback_lines.append(f"- 当前候选使用了占位标签: {', '.join(blocked_tags)}")

    return {
        "block_kind": "invalid_generation",
        "stop_stage": "blocked_invalid_generation",
        "blocked_cluster": str(candidate_signature.get("cluster_key", "")).strip() or "-",
        "blocked_reason": "候选未产生真实代码改动，不能作为有效研究候选进入后续流程",
        "current_locks": tuple(),
        "invalid_reasons": tuple(invalid_reasons),
        "actual_changed_regions": tuple(actual_changed_regions),
        "declared_regions_match_system": True,
        "feedback_note": "\n".join(feedback_lines),
    }


# ==================== 候选策略生成 ====================


MODEL_RESPONSE_FIELDS = (
    "candidate_id",
    "primary_direction",
    "hypothesis",
    "change_plan",
    "change_tags",
    "expected_effects",
    "novelty_proof",
    "core_factors",
    "exit_range_scan",
)
def _build_field_pattern(*field_names: str) -> re.Pattern[str]:
    joined = "|".join(re.escape(name) for name in field_names)
    return re.compile(rf"^({joined})\s*:\s*(.*)$", re.IGNORECASE)


MODEL_RESPONSE_FIELD_PATTERN = _build_field_pattern(
    "candidate_id",
    "primary_direction",
    "hypothesis",
    "change_plan",
    "change_tags",
    "expected_effects",
    "novelty_proof",
    "core_factors",
    "exit_range_scan",
)
REVIEWER_RESPONSE_FIELD_PATTERN = _build_field_pattern(
    "verdict",
    "reviewer_summary",
    "rejection_type",
    "matched_evidence",
    "must_change",
    "why_not_new",
)
UNKNOWN_RESPONSE_FIELD_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*\s*:\s*.+$", re.IGNORECASE)
EDIT_COMPLETION_TOKEN = "EDIT_DONE"
NO_EDIT_ERROR_MESSAGE = "candidate missing actual changed regions"


def _is_complexity_error_message(message: str) -> bool:
    normalized = str(message).strip().lower()
    return "complexity budget exceeded" in normalized or "complexity growth too large" in normalized


def _is_no_edit_error_message(message: str) -> bool:
    return NO_EDIT_ERROR_MESSAGE in str(message or "").strip().lower()


def _response_field_lines(raw_text: str, *, field_pattern: re.Pattern[str] = MODEL_RESPONSE_FIELD_PATTERN) -> dict[str, list[str]]:
    field_lines: dict[str, list[str]] = {}
    current_field = ""
    for raw_line in str(raw_text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = field_pattern.match(stripped)
        if match:
            current_field = match.group(1).lower()
            value = match.group(2).strip()
            field_lines.setdefault(current_field, [])
            if value:
                field_lines[current_field].append(value)
            continue
        if UNKNOWN_RESPONSE_FIELD_PATTERN.match(stripped):
            current_field = ""
            continue
        if not current_field:
            continue
        continuation = re.sub(r"^\s*[-*]\s*", "", stripped).strip()
        if continuation:
            field_lines.setdefault(current_field, []).append(continuation)
    return field_lines


def _split_inline_items(value: str) -> list[str]:
    cleaned = str(value or "").strip()
    if not cleaned:
        return []
    if cleaned.lower() in {"none", "null", "[]", "无"}:
        return []
    normalized = cleaned.replace("；", "||").replace(";", "||")
    if "||" in normalized:
        chunks = normalized.split("||")
    elif any(token in normalized for token in [",", "，"]):
        chunks = re.split(r"[,，]+", normalized)
    else:
        chunks = [normalized]
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _collapse_field_text(lines: list[str]) -> str:
    return " ".join(str(line).strip() for line in lines if str(line).strip()).strip()


def _parse_change_tags(lines: list[str]) -> tuple[str, ...]:
    tags: list[str] = []
    for line in lines:
        for item in re.split(r"[,\s，]+", str(line).strip()):
            normalized = item.strip()
            if normalized:
                tags.append(normalized)
    deduped: list[str] = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    return tuple(deduped[:6])


def _normalize_primary_direction(value: str) -> str:
    cleaned = " ".join(str(value or "").split())
    if not cleaned:
        raise StrategySourceError("primary_direction is empty")
    parts = [part.strip() for part in cleaned.split("|", 1)]
    if len(parts) != 2:
        raise StrategySourceError("primary_direction must use `domain | label` format")
    domain, label = parts
    domain = domain.lower()
    if domain not in PRIMARY_DIRECTION_DOMAINS:
        raise StrategySourceError(
            "primary_direction domain must be one of: "
            + ", ".join(sorted(PRIMARY_DIRECTION_DOMAINS))
        )
    if not label:
        raise StrategySourceError("primary_direction label is empty")
    if "|" in label:
        raise StrategySourceError("primary_direction only allows one `domain | label` layer")
    return f"{domain} | {label}"


def _parse_expected_effects(lines: list[str]) -> tuple[str, ...]:
    effects: list[str] = []
    for line in lines:
        effects.extend(_split_inline_items(line))
    deduped: list[str] = []
    for effect in effects:
        if effect not in deduped:
            deduped.append(effect)
    return tuple(deduped[:5])


def _parse_core_factors_field(lines: list[str]) -> tuple[StrategyCoreFactor, ...]:
    factors: list[StrategyCoreFactor] = []
    for line in lines:
        for item in _split_inline_items(line):
            parts = [part.strip() for part in item.split("|")]
            if len(parts) != 3:
                continue
            name, thesis, current_signal = parts
            if not name or not thesis or not current_signal:
                continue
            factors.append(
                StrategyCoreFactor(
                    name=name,
                    thesis=thesis,
                    current_signal=current_signal,
                )
            )
    return tuple(factors[:4])


def _parse_model_candidate_payload(raw_text: str) -> dict[str, Any]:
    # 这里只做宽松归一化，真正的合法性由 `_validate_round_brief_payload()`
    # 和 `_request_validated_round_brief()` 负责；不要把 parser 当成最终校验层。
    text = str(raw_text or "").strip()
    if not text:
        raise StrategySourceError("candidate response is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        normalized = dict(payload)
        normalized["__raw_text__"] = text
        return normalized

    field_lines = _response_field_lines(text, field_pattern=MODEL_RESPONSE_FIELD_PATTERN)
    if not field_lines:
        return {
            "__raw_text__": text,
            "__format_error__": "missing_field_contract",
            "candidate_id": "",
            "primary_direction": "",
            "hypothesis": "",
            "change_plan": "",
            "change_tags": [],
            "expected_effects": [],
            "novelty_proof": "",
            "core_factors": [],
            "exit_range_scan": None,
        }

    return {
        "__raw_text__": text,
        "candidate_id": _collapse_field_text(field_lines.get("candidate_id", [])),
        "primary_direction": _collapse_field_text(field_lines.get("primary_direction", [])),
        "hypothesis": _collapse_field_text(field_lines.get("hypothesis", [])),
        "change_plan": _collapse_field_text(field_lines.get("change_plan", [])),
        "change_tags": list(_parse_change_tags(field_lines.get("change_tags", []))),
        "expected_effects": list(_parse_expected_effects(field_lines.get("expected_effects", []))),
        "novelty_proof": _collapse_field_text(field_lines.get("novelty_proof", [])),
        "core_factors": [
            {
                "name": factor.name,
                "thesis": factor.thesis,
                "current_signal": factor.current_signal,
            }
            for factor in _parse_core_factors_field(field_lines.get("core_factors", []))
        ],
        "exit_range_scan": _collapse_field_text(field_lines.get("exit_range_scan", [])),
    }


def _parse_model_reviewer_payload(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise StrategySourceError("reviewer response is empty")

    field_lines = _response_field_lines(text, field_pattern=REVIEWER_RESPONSE_FIELD_PATTERN)
    if not field_lines:
        return {
            "__raw_text__": text,
            "__format_error__": "missing_reviewer_field_contract",
            "verdict": "",
            "reviewer_summary": "",
            "rejection_type": "",
            "matched_evidence": "",
            "must_change": "",
            "why_not_new": "",
        }

    return {
        "__raw_text__": text,
        "verdict": _collapse_field_text(field_lines.get("verdict", [])).upper(),
        "reviewer_summary": _collapse_field_text(field_lines.get("reviewer_summary", [])),
        "rejection_type": _collapse_field_text(field_lines.get("rejection_type", [])),
        "matched_evidence": _collapse_field_text(field_lines.get("matched_evidence", [])),
        "must_change": _collapse_field_text(field_lines.get("must_change", [])),
        "why_not_new": _collapse_field_text(field_lines.get("why_not_new", [])),
    }


def _round_brief_missing_fields(payload: dict[str, Any]) -> tuple[str, ...]:
    missing: list[str] = []
    if str(payload.get("__format_error__", "")).strip() == "missing_field_contract":
        return PLANNER_BRIEF_REQUIRED_FIELDS

    hypothesis = str(payload.get("hypothesis", "")).strip()
    primary_direction = str(payload.get("primary_direction", "")).strip()
    change_plan = str(payload.get("change_plan", "")).strip()
    novelty_proof = str(payload.get("novelty_proof", "")).strip()
    change_tags = tuple(str(item).strip() for item in payload.get("change_tags", []) if str(item).strip())

    if not primary_direction:
        missing.append("primary_direction")
    if not hypothesis:
        missing.append("hypothesis")
    if not change_plan:
        missing.append("change_plan")
    if not novelty_proof:
        missing.append("novelty_proof")
    if not change_tags:
        missing.append("change_tags")
    return tuple(missing)


def _validate_round_brief_payload(payload: dict[str, Any]) -> None:
    missing_fields = _round_brief_missing_fields(payload)
    if missing_fields:
        raise StrategySourceError(
            "planner round brief missing required fields: "
            + ", ".join(missing_fields)
        )
    _normalize_primary_direction(str(payload.get("primary_direction", "")).strip())


def _reviewer_missing_fields(payload: dict[str, Any]) -> tuple[str, ...]:
    missing: list[str] = []
    if str(payload.get("__format_error__", "")).strip() == "missing_reviewer_field_contract":
        return REVIEWER_REQUIRED_FIELDS

    verdict = str(payload.get("verdict", "")).strip().upper()
    reviewer_summary = str(payload.get("reviewer_summary", "")).strip()
    rejection_type = str(payload.get("rejection_type", "")).strip()
    matched_evidence = str(payload.get("matched_evidence", "")).strip()
    must_change = str(payload.get("must_change", "")).strip()
    why_not_new = str(payload.get("why_not_new", "")).strip()

    if verdict not in {"PASS", "REVISE"}:
        missing.append("verdict")
    if not reviewer_summary:
        missing.append("reviewer_summary")
    if not rejection_type:
        missing.append("rejection_type")
    if not matched_evidence:
        missing.append("matched_evidence")
    if not must_change:
        missing.append("must_change")
    if not why_not_new:
        missing.append("why_not_new")
    return tuple(missing)


def _validate_reviewer_payload(payload: dict[str, Any]) -> None:
    missing_fields = _reviewer_missing_fields(payload)
    if missing_fields:
        raise StrategySourceError(
            "reviewer decision missing required fields: "
            + ", ".join(missing_fields)
        )


def _reviewer_decision_from_payload(payload: dict[str, Any]) -> StrategyReviewerDecision:
    _validate_reviewer_payload(payload)
    return StrategyReviewerDecision(
        verdict=str(payload.get("verdict", "")).strip().upper(),
        reviewer_summary=str(payload.get("reviewer_summary", "")).strip(),
        rejection_type=str(payload.get("rejection_type", "")).strip(),
        matched_evidence=str(payload.get("matched_evidence", "")).strip(),
        must_change=str(payload.get("must_change", "")).strip(),
        why_not_new=str(payload.get("why_not_new", "")).strip(),
    )


def _raw_response_excerpt(payload: dict[str, Any], *, max_chars: int = 240) -> str:
    text = " ".join(str(payload.get("__raw_text__", "")).split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _auto_change_tags(edited_regions: tuple[str, ...]) -> tuple[str, ...]:
    tags = [
        tag
        for tag in region_families_for_regions(edited_regions)
        if str(tag).strip()
    ]
    if not tags and edited_regions:
        tags = [str(edited_regions[0]).strip()]
    deduped: list[str] = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    return tuple(deduped[:6])


def _slug_text(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return slug.strip("_")


def _auto_candidate_id(
    *,
    change_tags: tuple[str, ...],
    edited_regions: tuple[str, ...],
) -> str:
    parts = [_slug_text(item) for item in (*change_tags, *edited_regions) if _slug_text(item)]
    prefix = "_".join(parts[:3]) or "candidate"
    return f"auto_{prefix}_{int(time.time())}"


def _actual_changed_regions(
    *,
    base_source: str,
    strategy_code: str,
) -> tuple[str, ...]:
    system_signature = build_system_edit_signature(
        base_source,
        strategy_code,
    )
    return tuple(sorted(str(item).strip() for item in system_signature["changed_regions"] if str(item).strip()))


def _workspace_strategy_source_or_raise(
    *,
    workspace_strategy_file: Path,
) -> str:
    if not workspace_strategy_file.exists():
        raise StrategySourceError(f"workspace strategy file missing: {workspace_strategy_file}")
    try:
        return normalize_strategy_source(load_strategy_source(workspace_strategy_file))
    except OSError as exc:
        raise StrategySourceError(f"failed to read workspace strategy file: {exc}") from exc


def _workspace_strategy_changed_source_or_raise(
    *,
    workspace_strategy_file: Path,
    base_source: str,
) -> str:
    strategy_code = _workspace_strategy_source_or_raise(
        workspace_strategy_file=workspace_strategy_file,
    )
    if source_hash(strategy_code) == source_hash(base_source):
        raise StrategySourceError(NO_EDIT_ERROR_MESSAGE)
    return strategy_code


def _no_edit_failure_candidate(
    *,
    workspace_strategy_file: Path,
    base_source: str,
    context_label: str,
    last_response_text: str = "",
    round_brief: StrategyRoundBrief | None = None,
) -> StrategyCandidate:
    strategy_code = (
        normalize_strategy_source(load_strategy_source(workspace_strategy_file))
        if workspace_strategy_file.exists()
        else normalize_strategy_source(base_source)
    )
    response_excerpt = " ".join(str(last_response_text or "").split())
    if len(response_excerpt) > 160:
        response_excerpt = response_excerpt[:160] + "..."
    expected_effect = (
        f"上一条回复摘录: {response_excerpt}"
        if response_excerpt
        else "目标文件 hash 未变化，上一条回复已被丢弃。"
    )
    candidate_id = (
        round_brief.candidate_id
        if round_brief and round_brief.candidate_id
        else f"no_edit_{_slug_text(context_label)}_{int(time.time())}"
    )
    change_tags = round_brief.change_tags if round_brief and round_brief.change_tags else ("no_edit", "edit_required")
    expected_effects = (
        round_brief.expected_effects + (expected_effect,)
        if round_brief and round_brief.expected_effects
        else (expected_effect,)
    )
    novelty_proof = (
        f"{round_brief.novelty_proof}；但本次失败点是没有真实落码。"
        if round_brief and round_brief.novelty_proof
        else "系统只接受真实源码 diff；当前失败原因是目标文件未变化，而不是策略方向未定义。"
    )
    return StrategyCandidate(
        candidate_id=candidate_id,
        hypothesis=(
            round_brief.hypothesis
            if round_brief and round_brief.hypothesis
            else "本轮失败不是策略假设本身，而是模型没有把改动真正落到策略源码。"
        ),
        change_plan=(
            round_brief.change_plan
            if round_brief and round_brief.change_plan
            else "先直接修改 src/strategy_macd_aggressive.py；若文件 hash 不变，本轮不再承认任何说明文本。"
        ),
        primary_direction=(
            round_brief.primary_direction
            if round_brief and round_brief.primary_direction
            else "structure | no-edit 源码落地修复"
        ),
        closest_failed_cluster=(
            cluster_key_for_components("", change_tags) or "no_edit"
        ),
        novelty_proof=novelty_proof,
        change_tags=change_tags,
        edited_regions=tuple(),
        expected_effects=expected_effects,
        core_factors=round_brief.core_factors if round_brief else tuple(),
        strategy_code=strategy_code,
        exit_range_scan=round_brief.exit_range_scan if round_brief else None,
    )


def _core_factors_from_payload(payload: dict[str, Any]) -> tuple[StrategyCoreFactor, ...]:
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
    return tuple(core_factors)


def _round_brief_from_payload(payload: dict[str, Any]) -> StrategyRoundBrief:
    _validate_round_brief_payload(payload)
    change_tags = tuple(str(item).strip() for item in payload.get("change_tags", []) if str(item).strip())
    primary_direction = _normalize_primary_direction(str(payload.get("primary_direction", "")).strip())
    hypothesis = str(payload.get("hypothesis", "")).strip()
    change_plan = str(payload.get("change_plan", "")).strip()
    novelty_proof = str(payload.get("novelty_proof", "")).strip()
    candidate_id = str(payload.get("candidate_id", "")).strip() or _auto_candidate_id(
        change_tags=change_tags,
        edited_regions=tuple(),
    )
    return StrategyRoundBrief(
        candidate_id=candidate_id,
        primary_direction=primary_direction,
        hypothesis=hypothesis,
        change_plan=change_plan,
        novelty_proof=novelty_proof,
        change_tags=change_tags,
        expected_effects=tuple(str(item).strip() for item in payload.get("expected_effects", []) if str(item).strip()),
        core_factors=_core_factors_from_payload(payload),
        exit_range_scan=payload.get("exit_range_scan") if isinstance(payload.get("exit_range_scan"), dict) else ({"raw": str(payload.get("exit_range_scan", "")).strip()} if str(payload.get("exit_range_scan", "")).strip() else None),
    )


def _round_brief_from_candidate(candidate: StrategyCandidate) -> StrategyRoundBrief:
    return StrategyRoundBrief(
        candidate_id=candidate.candidate_id,
        primary_direction=candidate.primary_direction,
        hypothesis=candidate.hypothesis,
        change_plan=candidate.change_plan,
        novelty_proof=candidate.novelty_proof,
        change_tags=candidate.change_tags,
        expected_effects=candidate.expected_effects,
        core_factors=candidate.core_factors,
        exit_range_scan=candidate.exit_range_scan,
    )


def _round_brief_task_summary(round_brief: StrategyRoundBrief) -> str:
    lines = [
        f"- candidate_id: {round_brief.candidate_id or '-'}",
        f"- primary_direction: {round_brief.primary_direction or '-'}",
        f"- hypothesis: {round_brief.hypothesis or '-'}",
        f"- change_plan: {round_brief.change_plan or '-'}",
        f"- change_tags: {', '.join(round_brief.change_tags) or '-'}",
        f"- expected_effects: {'；'.join(round_brief.expected_effects) or '-'}",
        f"- novelty_proof: {round_brief.novelty_proof or '-'}",
        f"- exit_range_scan: {round_brief.exit_range_scan or '-'}",
    ]
    return "\n".join(lines)


def _rebase_candidate_metadata_to_final_code(
    *,
    candidate: StrategyCandidate,
    round_brief: StrategyRoundBrief,
    base_source: str,
    workspace_root: Path,
    context_label: str,
) -> StrategyCandidate:
    diff_summary = tuple(build_diff_summary(base_source, candidate.strategy_code, limit=18))
    region_families = tuple(sorted(region_families_for_regions(candidate.edited_regions)))
    prompt = build_strategy_candidate_summary_prompt(
        candidate_id=candidate.candidate_id,
        primary_direction=round_brief.primary_direction,
        hypothesis=round_brief.hypothesis,
        change_plan=round_brief.change_plan,
        change_tags=round_brief.change_tags,
        expected_effects=round_brief.expected_effects,
        novelty_proof=round_brief.novelty_proof,
        edited_regions=candidate.edited_regions,
        region_families=region_families,
        diff_summary=diff_summary,
    )
    try:
        final_brief = _request_validated_round_brief(
            base_source=base_source,
            prompt=prompt,
            system_prompt=build_strategy_summary_worker_system_prompt(),
            phase="model_summary_worker",
            workspace_root=workspace_root,
            retry_phase="model_summary_brief_repair",
            session_kind="summary_worker",
            use_persistent_session=False,
        )
    except Exception as exc:
        log_info(f"{context_label}最终代码元信息回写失败，回退为原 round brief: {exc}")
        return candidate

    return StrategyCandidate(
        candidate_id=candidate.candidate_id,
        hypothesis=final_brief.hypothesis,
        change_plan=final_brief.change_plan,
        primary_direction=final_brief.primary_direction,
        closest_failed_cluster=cluster_key_for_components("", final_brief.change_tags) or candidate.closest_failed_cluster,
        novelty_proof=final_brief.novelty_proof,
        change_tags=final_brief.change_tags,
        edited_regions=candidate.edited_regions,
        expected_effects=final_brief.expected_effects,
        core_factors=final_brief.core_factors,
        strategy_code=candidate.strategy_code,
        exit_range_scan=candidate.exit_range_scan or final_brief.exit_range_scan,
        exit_range_scan_result=candidate.exit_range_scan_result,
    )


def _candidate_stub_from_round_brief(
    round_brief: StrategyRoundBrief,
    *,
    workspace_strategy_file: Path,
    fallback_source: str,
) -> StrategyCandidate:
    strategy_code = (
        normalize_strategy_source(load_strategy_source(workspace_strategy_file))
        if workspace_strategy_file.exists()
        else normalize_strategy_source(fallback_source)
    )
    return StrategyCandidate(
        candidate_id=round_brief.candidate_id or _auto_candidate_id(change_tags=round_brief.change_tags, edited_regions=tuple()),
        hypothesis=round_brief.hypothesis or "未提供 hypothesis，等待同轮修复。",
        change_plan=round_brief.change_plan or "未提供 change_plan，等待同轮修复。",
        primary_direction=round_brief.primary_direction or "structure | 源码校验修复",
        closest_failed_cluster=cluster_key_for_components("", round_brief.change_tags) or "unknown_cluster",
        novelty_proof=round_brief.novelty_proof or "源码校验失败，等待同轮修复。",
        change_tags=round_brief.change_tags,
        edited_regions=tuple(),
        expected_effects=round_brief.expected_effects,
        core_factors=round_brief.core_factors,
        strategy_code=strategy_code,
        exit_range_scan=round_brief.exit_range_scan,
    )


def _candidate_from_round_brief(
    round_brief: StrategyRoundBrief,
    *,
    workspace_strategy_file: Path,
    base_source: str,
) -> StrategyCandidate:
    if not workspace_strategy_file.exists():
        raise StrategySourceError(f"workspace strategy file missing: {workspace_strategy_file}")
    strategy_code = normalize_strategy_source(load_strategy_source(workspace_strategy_file))
    validate_strategy_source(
        strategy_code,
        base_source=base_source,
    )
    actual_changed_regions = _actual_changed_regions(
        base_source=base_source,
        strategy_code=strategy_code,
    )
    change_tags = round_brief.change_tags
    if not change_tags:
        change_tags = _auto_change_tags(actual_changed_regions)
    candidate_id = round_brief.candidate_id or _auto_candidate_id(
        change_tags=change_tags,
        edited_regions=actual_changed_regions,
    )
    candidate = StrategyCandidate(
        candidate_id=candidate_id,
        hypothesis=round_brief.hypothesis or "未提供 hypothesis；本轮以真实源码 diff 为准。",
        change_plan=round_brief.change_plan or "未提供 change_plan；本轮以真实源码 diff 为准。",
        primary_direction=round_brief.primary_direction or "structure | 源码差异回写",
        closest_failed_cluster=cluster_key_for_components("", change_tags) or "unknown_cluster",
        novelty_proof=round_brief.novelty_proof or "未提供 novelty_proof；系统将以真实源码 diff 判定本轮改动。",
        change_tags=change_tags,
        edited_regions=actual_changed_regions,
        expected_effects=round_brief.expected_effects,
        core_factors=round_brief.core_factors,
        strategy_code=strategy_code,
        exit_range_scan=round_brief.exit_range_scan,
    )
    if not candidate.edited_regions:
        raise StrategySourceError("candidate missing actual changed regions")
    if len(set(candidate.edited_regions)) != len(candidate.edited_regions):
        raise StrategySourceError("candidate edited_regions contains duplicates")
    return candidate


def _candidate_from_payload(
    payload: dict[str, Any],
    *,
    workspace_strategy_file: Path,
    base_source: str,
) -> StrategyCandidate:
    return _candidate_from_round_brief(
        _round_brief_from_payload(payload),
        workspace_strategy_file=workspace_strategy_file,
        base_source=base_source,
    )


def _run_model_text_request(
    *,
    prompt: str,
    system_prompt: str,
    phase: str,
    workspace_root: Path,
    repair_attempt: int | None = None,
    session_kind: str = "planner",
    use_persistent_session: bool = True,
) -> str:
    session_id = _active_research_session_id() if use_persistent_session else ""
    requested_session_id = session_id
    use_deepseek_planner = _planner_uses_deepseek(session_kind)
    provider_name = "deepseek" if use_deepseek_planner else "codex"
    resolved_system_prompt = (
        _embed_workspace_agents_for_api(system_prompt, workspace_root)
        if use_deepseek_planner else system_prompt
    )

    def _invoke(active_session_id: str | None, metadata: dict[str, Any]) -> str:
        with _temporary_cwd(workspace_root):
            if use_deepseek_planner:
                return generate_deepseek_planner_text_response(
                    prompt=prompt,
                    system_prompt=resolved_system_prompt,
                    workspace_root=workspace_root,
                    max_output_tokens=RUNTIME.prompt_max_output_tokens,
                    config=load_deepseek_planner_config(),
                    progress_callback=_build_model_progress_callback(
                        phase,
                        repair_attempt=repair_attempt,
                    ),
                    session_id=active_session_id,
                    response_metadata=metadata,
                )
            return generate_text_response(
                prompt=prompt,
                system_prompt=resolved_system_prompt,
                max_output_tokens=RUNTIME.prompt_max_output_tokens,
                config=_model_client_config(),
                progress_callback=_build_model_progress_callback(
                    phase,
                    repair_attempt=repair_attempt,
                ),
                session_id=active_session_id,
                response_metadata=metadata,
            )

    response_metadata: dict[str, Any] = {}
    started_at = time.monotonic()
    transient_retry_limit = (
        NON_PERSISTENT_CODEX_TRANSIENT_RETRY_LIMIT
        if provider_name == "codex" and not use_persistent_session
        else 0
    )
    transient_retry_attempt = 0

    while True:
        try:
            raw_text = _invoke(session_id or None, response_metadata)
            break
        except StrategyGenerationSessionError as exc:
            if not session_id or not use_persistent_session:
                raise
            log_info(f"{provider_name} session 无法恢复，改为新 session 重试: {exc}")
            _clear_research_session_state(remove_workspace=False, reason="invalid provider session")
            response_metadata = {}
            session_id = ""
        except StrategyGenerationTransientError as exc:
            if transient_retry_attempt >= transient_retry_limit:
                raise
            transient_retry_attempt += 1
            response_metadata = {}
            log_info(
                f"{provider_name} {phase} 短重试 "
                f"{transient_retry_attempt}/{transient_retry_limit}: "
                f"{str(exc).splitlines()[0]}"
            )

    resolved_session_id = (
        str(response_metadata.get("session_id", "")).strip()
        or str(response_metadata.get("thread_id", "")).strip()
        or session_id
    )
    if use_persistent_session and resolved_session_id:
        _store_research_session_metadata(
            session_id=resolved_session_id,
            workspace_root=workspace_root,
        )
    elapsed_seconds = round(max(0.0, time.monotonic() - started_at), 3)

    def _metadata_int(name: str, default: int) -> int:
        try:
            return max(0, int(response_metadata.get(name, default)))
        except (TypeError, ValueError):
            return max(0, default)

    system_prompt_chars_sent = _metadata_int(
        "system_prompt_chars_sent",
        len(resolved_system_prompt),
    )
    history_message_chars_sent = _metadata_int("history_message_chars_sent", 0)
    history_message_count_sent = _metadata_int("history_message_count_sent", 0)
    total_message_chars_sent = _metadata_int(
        "total_message_chars_sent",
        system_prompt_chars_sent + history_message_chars_sent + len(prompt),
    )
    estimated_prompt_tokens_sent = _metadata_int(
        "estimated_prompt_tokens_sent",
        max(1, (total_message_chars_sent + 3) // 4),
    )
    _append_model_call_telemetry(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "iteration": iteration_counter,
            "phase": phase,
            "provider_name": provider_name,
            "session_kind": session_kind,
            "persistent_session": bool(use_persistent_session),
            "requested_resume": bool(requested_session_id),
            "resolved_session_id": resolved_session_id if use_persistent_session else "",
            "resumed": bool(response_metadata.get("resumed", False)),
            "prompt_chars": len(prompt),
            "system_prompt_chars": len(system_prompt),
            "estimated_prompt_tokens": _estimate_prompt_tokens(prompt, system_prompt),
            "system_prompt_chars_sent": system_prompt_chars_sent,
            "history_message_chars_sent": history_message_chars_sent,
            "history_message_count_sent": history_message_count_sent,
            "total_message_chars_sent": total_message_chars_sent,
            "estimated_prompt_tokens_sent": estimated_prompt_tokens_sent,
            "response_chars": len(raw_text),
            "workspace_root": str(workspace_root),
            "repair_attempt": repair_attempt,
            "duration_seconds": elapsed_seconds,
        }
    )
    return raw_text


def _run_model_candidate_request(
    *,
    prompt: str,
    system_prompt: str,
    phase: str,
    workspace_root: Path,
    repair_attempt: int | None = None,
    session_kind: str = "planner",
    use_persistent_session: bool = True,
) -> dict[str, Any]:
    raw_text = _run_model_text_request(
        prompt=prompt,
        system_prompt=system_prompt,
        phase=phase,
        workspace_root=workspace_root,
        repair_attempt=repair_attempt,
        session_kind=session_kind,
        use_persistent_session=use_persistent_session,
    )
    return _parse_model_candidate_payload(raw_text)


def _candidate_from_invalid_round_brief_payload(
    *,
    payload: dict[str, Any],
    base_source: str,
) -> StrategyCandidate:
    change_tags = tuple(str(item).strip() for item in payload.get("change_tags", []) if str(item).strip()) or ("invalid_brief",)
    primary_direction_raw = str(payload.get("primary_direction", "")).strip()
    try:
        primary_direction = _normalize_primary_direction(primary_direction_raw)
    except StrategySourceError:
        primary_direction = "structure | 非法摘要修复"
    closest_failed_cluster = cluster_key_for_components("", change_tags) or "invalid_brief"
    return StrategyCandidate(
        candidate_id=(
            str(payload.get("candidate_id", "")).strip()
            or f"invalid_brief_{int(time.time())}"
        ),
        hypothesis=(
            str(payload.get("hypothesis", "")).strip()
            or "planner 未按字段契约返回有效 round brief。"
        ),
        change_plan=(
            str(payload.get("change_plan", "")).strip()
            or "重新按字段契约返回 hypothesis、change_plan、novelty_proof、change_tags。"
        ),
        primary_direction=primary_direction,
        closest_failed_cluster=closest_failed_cluster,
        novelty_proof=(
            str(payload.get("novelty_proof", "")).strip()
            or "上一条 planner 回复缺少核心字段，未形成可执行 brief。"
        ),
        change_tags=change_tags,
        edited_regions=tuple(),
        expected_effects=tuple(str(item).strip() for item in payload.get("expected_effects", []) if str(item).strip()),
        core_factors=_core_factors_from_payload(payload),
        strategy_code=base_source,
    )


def _invalid_round_brief_block_info(
    *,
    payload: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    missing_fields = _round_brief_missing_fields(payload)
    blocked_cluster = cluster_key_for_components(
        "",
        tuple(str(item).strip() for item in payload.get("change_tags", []) if str(item).strip()),
    ) or "-"
    raw_excerpt = _raw_response_excerpt(payload)
    error_text = errors[-1] if errors else "planner round brief invalid"
    feedback_lines = [
        "planner 输出的 round brief 不合法，当前轮次没有拿到可执行的策略摘要。",
        f"- 无效原因: {error_text}",
        f"- 缺失或无效字段: {', '.join(missing_fields) or '-'}",
        f"- 原始回复摘录: {raw_excerpt or '-'}",
        "- 你的任务不是写随笔，而是返回可执行的 round brief。",
        "- 至少保证 `primary_direction`、`hypothesis`、`change_plan`、`novelty_proof`、`change_tags` 非空。",
        "- 继续只返回纯文本字段，不要 JSON，不要 markdown，不要解释。",
    ]
    return {
        "block_kind": "invalid_round_brief",
        "stop_stage": "blocked_invalid_generation",
        "blocked_cluster": blocked_cluster,
        "blocked_reason": error_text,
        "current_locks": tuple(),
        "invalid_reasons": tuple(errors),
        "feedback_note": "\n".join(feedback_lines),
    }


def _request_validated_round_brief(
    *,
    base_source: str,
    prompt: str,
    system_prompt: str,
    phase: str,
    workspace_root: Path,
    retry_phase: str,
    session_kind: str = "planner",
    use_persistent_session: bool = True,
) -> StrategyRoundBrief:
    payload = _run_model_candidate_request(
        prompt=prompt,
        system_prompt=system_prompt,
        phase=phase,
        workspace_root=workspace_root,
        session_kind=session_kind,
        use_persistent_session=use_persistent_session,
    )
    errors: list[str] = []
    current_payload = payload
    for attempt in range(MAX_PLANNER_BRIEF_REPAIR_ATTEMPTS + 1):
        try:
            _validate_round_brief_payload(current_payload)
            return _round_brief_from_payload(current_payload)
        except StrategySourceError as exc:
            errors.append(str(exc))
            if attempt >= MAX_PLANNER_BRIEF_REPAIR_ATTEMPTS:
                raise PlannerBriefInvalid(
                    candidate=_candidate_from_invalid_round_brief_payload(
                        payload=current_payload,
                        base_source=base_source,
                    ),
                    block_info=_invalid_round_brief_block_info(
                        payload=current_payload,
                        errors=errors,
                    ),
                    errors=tuple(errors),
                ) from exc

            retry_attempt = attempt + 1
            log_info(
                f"第 {iteration_counter} 轮 planner brief 非法，尝试同轮补正 "
                f"{retry_attempt}/{MAX_PLANNER_BRIEF_REPAIR_ATTEMPTS}: {errors[-1]}"
            )
            write_heartbeat(
                "planner_brief_repairing",
                message=f"iteration {iteration_counter} repairing planner brief",
                repair_attempt=retry_attempt,
                max_repair_attempts=MAX_PLANNER_BRIEF_REPAIR_ATTEMPTS,
                error=errors[-1],
            )
            repair_prompt = build_strategy_round_brief_repair_prompt(
                retry_attempt=retry_attempt,
                invalid_reason=errors[-1],
                missing_fields=_round_brief_missing_fields(current_payload),
                raw_response_excerpt=_raw_response_excerpt(current_payload),
            )
            current_payload = _run_model_candidate_request(
                prompt=repair_prompt,
                system_prompt=system_prompt,
                phase=retry_phase,
                workspace_root=workspace_root,
                repair_attempt=retry_attempt,
                session_kind=session_kind,
                use_persistent_session=use_persistent_session,
            )

    raise StrategySourceError("planner round brief validation loop exhausted")


def _run_model_reviewer_request(
    *,
    prompt: str,
    system_prompt: str,
    phase: str,
    workspace_root: Path,
    repair_attempt: int | None = None,
) -> dict[str, Any]:
    raw_text = _run_model_text_request(
        prompt=prompt,
        system_prompt=system_prompt,
        phase=phase,
        workspace_root=workspace_root,
        repair_attempt=repair_attempt,
        session_kind="reviewer",
        use_persistent_session=False,
    )
    return _parse_model_reviewer_payload(raw_text)


def _request_validated_reviewer_decision(
    *,
    prompt: str,
    workspace_root: Path,
    phase: str,
    retry_phase: str,
) -> StrategyReviewerDecision:
    payload = _run_model_reviewer_request(
        prompt=prompt,
        system_prompt=build_strategy_reviewer_system_prompt(),
        phase=phase,
        workspace_root=workspace_root,
    )
    errors: list[str] = []
    current_payload = payload
    for attempt in range(MAX_REVIEWER_REPAIR_ATTEMPTS + 1):
        try:
            return _reviewer_decision_from_payload(current_payload)
        except StrategySourceError as exc:
            errors.append(str(exc))
            if attempt >= MAX_REVIEWER_REPAIR_ATTEMPTS:
                raise ReviewerRejected(
                    candidate=StrategyCandidate(
                        candidate_id=f"reviewer_invalid_{int(time.time())}",
                        hypothesis="reviewer 未按字段契约返回有效审稿结果。",
                        change_plan="重新按 reviewer 字段契约返回 verdict、reviewer_summary 与必要的打回信息。",
                        primary_direction="structure | reviewer 摘要修复",
                        closest_failed_cluster="reviewer_invalid",
                        novelty_proof="reviewer 输出缺少核心字段，未形成可执行审稿结论。",
                        change_tags=("reviewer_invalid",),
                        edited_regions=tuple(),
                        expected_effects=tuple(),
                        core_factors=tuple(),
                        strategy_code=best_source,
                    ),
                    block_info={
                        "block_kind": "reviewer_invalid",
                        "stop_stage": "blocked_invalid_generation",
                        "blocked_cluster": "reviewer_invalid",
                        "blocked_reason": errors[-1],
                        "current_locks": tuple(),
                        "invalid_reasons": tuple(errors),
                        "feedback_note": "\n".join(
                            [
                                "reviewer 输出无效，当前轮次没有拿到可执行审稿结论。",
                                f"- 无效原因: {errors[-1]}",
                                f"- 原始回复摘录: {_raw_response_excerpt(current_payload) or '-'}",
                                "- reviewer 只能返回 PASS/REVISE 审稿卡，不要输出随笔、JSON 或 markdown。",
                            ]
                        ),
                    },
                    errors=tuple(errors),
                ) from exc

            retry_attempt = attempt + 1
            log_info(
                f"第 {iteration_counter} 轮 reviewer 结果非法，尝试同轮补正 "
                f"{retry_attempt}/{MAX_REVIEWER_REPAIR_ATTEMPTS}: {errors[-1]}"
            )
            write_heartbeat(
                "reviewer_repairing",
                message=f"iteration {iteration_counter} repairing reviewer result",
                repair_attempt=retry_attempt,
                max_repair_attempts=MAX_REVIEWER_REPAIR_ATTEMPTS,
                error=errors[-1],
            )
            repair_prompt = build_strategy_reviewer_repair_prompt(
                retry_attempt=retry_attempt,
                invalid_reason=errors[-1],
                raw_response_excerpt=_raw_response_excerpt(current_payload),
            )
            current_payload = _run_model_reviewer_request(
                prompt=repair_prompt,
                system_prompt=build_strategy_reviewer_system_prompt(),
                phase=retry_phase,
                workspace_root=workspace_root,
                repair_attempt=retry_attempt,
            )

    raise StrategySourceError("reviewer validation loop exhausted")


def _reviewer_rejected_candidate(
    *,
    round_brief: StrategyRoundBrief,
    base_source: str,
) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=round_brief.candidate_id or f"reviewer_rejected_{int(time.time())}",
        hypothesis=round_brief.hypothesis or "reviewer 判定当前 draft brief 不值得继续。",
        change_plan=round_brief.change_plan or "根据 reviewer 打回信息重写 round brief。",
        primary_direction=round_brief.primary_direction or "structure | reviewer 打回",
        closest_failed_cluster=cluster_key_for_components("", round_brief.change_tags) or "reviewer_rejected",
        novelty_proof=round_brief.novelty_proof or "reviewer 已判定当前 draft 仍属旧失败近邻。",
        change_tags=round_brief.change_tags or ("reviewer_rejected",),
        edited_regions=tuple(),
        expected_effects=round_brief.expected_effects,
        core_factors=round_brief.core_factors,
        strategy_code=base_source,
    )


def _reviewer_rejection_block_info(
    *,
    round_brief: StrategyRoundBrief,
    decision: StrategyReviewerDecision,
) -> dict[str, Any]:
    blocked_cluster = cluster_key_for_components("", round_brief.change_tags) or "reviewer_rejected"
    blocked_reason = decision.reviewer_summary or "reviewer 判定当前 draft 不值得继续"
    feedback_lines = [
        "reviewer 已打回当前 planner draft brief；本轮没有进入 edit_worker。",
        f"- reviewer_summary: {decision.reviewer_summary or '-'}",
        f"- rejection_type: {decision.rejection_type or '-'}",
        f"- matched_evidence: {decision.matched_evidence or '-'}",
        f"- must_change: {decision.must_change or '-'}",
        f"- why_not_new: {decision.why_not_new or '-'}",
        "- 下一版 planner draft 必须先吸收 reviewer 打回信息，再重写方向。",
    ]
    return {
        "block_kind": "reviewer_rejected",
        "stop_stage": "blocked_invalid_generation",
        "blocked_cluster": blocked_cluster,
        "blocked_reason": blocked_reason,
        "current_locks": tuple(),
        "invalid_reasons": (decision.rejection_type, decision.must_change),
        "feedback_note": "\n".join(feedback_lines),
    }


def _review_and_revise_round_brief(
    *,
    round_brief: StrategyRoundBrief,
    base_source: str,
    evaluation_summary: str,
    journal_summary: str,
    workspace_root: Path,
    planner_session_kind: str,
    use_persistent_planner_session: bool,
    reviewer_phase: str,
    reviewer_retry_phase: str,
    planner_revise_phase: str,
    planner_revise_retry_phase: str,
    stage_label: str,
) -> StrategyRoundBrief:
    current_brief = round_brief
    for attempt in range(MAX_REVIEWER_REVISE_ATTEMPTS + 1):
        reviewer_prompt = build_strategy_reviewer_prompt(
            evaluation_summary=evaluation_summary,
            journal_summary=journal_summary,
            round_brief_text=_round_brief_task_summary(current_brief),
        )
        decision = _request_validated_reviewer_decision(
            prompt=reviewer_prompt,
            workspace_root=workspace_root,
            phase=reviewer_phase,
            retry_phase=reviewer_retry_phase,
        )
        _persist_reviewer_summary_card(
            memory_root=RUNTIME.paths.memory_dir,
            round_brief=current_brief,
            decision=decision,
            iteration_id=iteration_counter,
            stage_label=stage_label,
        )
        if decision.is_pass:
            return current_brief

        if attempt >= MAX_REVIEWER_REVISE_ATTEMPTS:
            raise ReviewerRejected(
                candidate=_reviewer_rejected_candidate(
                    round_brief=current_brief,
                    base_source=base_source,
                ),
                block_info=_reviewer_rejection_block_info(
                    round_brief=current_brief,
                    decision=decision,
                ),
                errors=(decision.reviewer_summary, decision.must_change),
            )

        revise_attempt = attempt + 1
        log_info(
            f"第 {iteration_counter} 轮 reviewer 打回 planner draft，尝试同轮重写 "
            f"{revise_attempt}/{MAX_REVIEWER_REVISE_ATTEMPTS}: {decision.reviewer_summary}"
        )
        write_heartbeat(
            "reviewer_revising",
            message=f"iteration {iteration_counter} planner revising after reviewer reject",
            repair_attempt=revise_attempt,
            max_repair_attempts=MAX_REVIEWER_REVISE_ATTEMPTS,
            gate=decision.rejection_type,
        )
        revise_prompt = build_strategy_reviewer_revise_prompt(
            round_brief_text=_round_brief_task_summary(current_brief),
            reviewer_verdict=decision.verdict,
            reviewer_summary=decision.reviewer_summary,
            rejection_type=decision.rejection_type,
            matched_evidence=decision.matched_evidence,
            must_change=decision.must_change,
            why_not_new=decision.why_not_new,
        )
        current_brief = _request_validated_round_brief(
            base_source=base_source,
            prompt=revise_prompt,
            system_prompt=build_strategy_planner_system_prompt(),
            phase=planner_revise_phase,
            workspace_root=workspace_root,
            retry_phase=planner_revise_retry_phase,
            session_kind=planner_session_kind,
            use_persistent_session=use_persistent_planner_session,
        )

    raise StrategySourceError("reviewer revise loop exhausted")


def _build_model_round_brief(
    base_source: str,
    journal_entries: list[dict[str, Any]],
    *,
    workspace_root: Path,
    planner_session_kind: str = "planner",
    use_persistent_planner_session: bool = True,
) -> StrategyRoundBrief:
    report = best_report
    if report is None:
        raise StrategySourceError("reference report is not initialized")
    benchmark_report = _reference_benchmark_report()
    if benchmark_report is None:
        raise StrategySourceError("reference benchmark is not initialized")
    journal_summary = build_journal_prompt_summary(
        journal_entries,
        limit=RUNTIME.max_recent_journal_entries,
        journal_path=RUNTIME.paths.journal_file,
        prompt_compact=True,
        current_score_regime=SCORE_REGIME,
        current_iteration=iteration_counter,
        active_stage_started_at=reference_stage_started_at,
        active_stage_iteration=reference_stage_iteration,
        active_reference_code_hash=source_hash(best_source) if best_source else "",
        reference_role=_reference_role(),
        reference_metrics=benchmark_report.metrics,
        memory_root=RUNTIME.paths.memory_dir,
    )

    session_mode = (
        "resume" if _active_research_session_id() else "bootstrap"
    )
    active_reference_code_hash = source_hash(best_source) if best_source else ""
    prompt = build_strategy_research_prompt(
        evaluation_summary=report.prompt_summary_text,
        journal_summary=journal_summary,
        previous_best_score=benchmark_report.metrics["promotion_score"],
        reference_metrics=benchmark_report.metrics,
        benchmark_label=_benchmark_role(),
        current_base_role=_reference_role(),
        score_regime=SCORE_REGIME,
        session_mode=session_mode,
        operator_focus_text=_load_operator_focus_text(),
        operator_focus_path="config/research_v2_operator_focus.md",
        champion_review_text=_load_champion_review_text(
            active_code_hash=active_reference_code_hash,
        ),
        champion_review_path="config/research_v2_champion_review.md",
        champion_review_code_hash=active_reference_code_hash,
        reviewer_summary_text=_load_reviewer_summary_text(),
        promotion_accept_margin=RUNTIME.promotion_accept_margin,
        promotion_accept_quality_drop_margin=RUNTIME.promotion_accept_quality_drop_margin,
        validation_block_count=RUNTIME.gates.validation_block_count,
        min_validation_block_floor=RUNTIME.gates.min_validation_block_floor,
        min_validation_closed_trades=RUNTIME.gates.min_validation_closed_trades,
        max_dev_validation_gap=RUNTIME.gates.max_dev_validation_gap,
        robustness_sharpe_gap_warn_threshold=RUNTIME.scoring.robustness_sharpe_gap_warn_threshold,
        robustness_sharpe_gap_fail_threshold=RUNTIME.scoring.robustness_sharpe_gap_fail_threshold,
    )
    round_brief = _request_validated_round_brief(
        base_source=base_source,
        prompt=prompt,
        system_prompt=build_strategy_planner_system_prompt(),
        phase="model_planner",
        workspace_root=workspace_root,
        retry_phase="model_planner_brief_repair",
        session_kind=planner_session_kind,
        use_persistent_session=use_persistent_planner_session,
    )
    return _review_and_revise_round_brief(
        round_brief=round_brief,
        base_source=base_source,
        evaluation_summary=report.prompt_summary_text,
        journal_summary=journal_summary,
        workspace_root=workspace_root,
        planner_session_kind=planner_session_kind,
        use_persistent_planner_session=use_persistent_planner_session,
        reviewer_phase="model_reviewer",
        reviewer_retry_phase="model_reviewer_repair",
        planner_revise_phase="model_planner_reviewer_revise",
        planner_revise_retry_phase="model_planner_reviewer_revise_brief_repair",
        stage_label="planner_review",
    )


def _metric_float(metrics: dict[str, Any], key: str) -> float:
    try:
        return float(metrics.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _build_edit_worker_evaluation_digest(report: EvaluationReport | None) -> str:
    if report is None:
        return ""
    metrics = report.metrics
    bull = _metric_float(metrics, "validation_bull_capture_score")
    bear = _metric_float(metrics, "validation_bear_capture_score")
    hit_rate = _metric_float(metrics, "validation_segment_hit_rate")
    weakest_candidates = (
        ("val趋势捕获", _metric_float(metrics, "validation_trend_capture_score")),
        ("val到来", _metric_float(metrics, "validation_arrival_capture_score")),
        ("val陪跑", _metric_float(metrics, "validation_escort_capture_score")),
        ("val掉头", _metric_float(metrics, "validation_turn_adaptation_score")),
        ("val多头捕获", bull),
        ("val空头捕获", bear),
    )
    weakest_name, weakest_value = min(weakest_candidates, key=lambda item: item[1])
    return "\n".join(
        [
            f"- gate: {report.gate_reason}",
            f"- 最弱维度: {weakest_name}={weakest_value:.2f}",
            f"- val多/空捕获={bull:.2f}/{bear:.2f}，命中率={hit_rate:.0%}",
        ]
    )


def _run_edit_worker(
    *,
    base_source: str,
    round_brief: StrategyRoundBrief,
    workspace_root: Path,
    phase: str,
    repair_attempt: int | None = None,
) -> str:
    prompt = build_strategy_edit_worker_prompt(
        candidate_id=round_brief.candidate_id,
        primary_direction=round_brief.primary_direction,
        hypothesis=round_brief.hypothesis,
        change_plan=round_brief.change_plan,
        change_tags=round_brief.change_tags,
        expected_effects=round_brief.expected_effects,
        novelty_proof=round_brief.novelty_proof,
        exit_range_scan=round_brief.exit_range_scan,
        evaluation_digest_text=_build_edit_worker_evaluation_digest(best_report),
    )
    return _run_model_text_request(
        prompt=prompt,
        system_prompt=build_strategy_edit_worker_system_prompt(),
        phase=phase,
        workspace_root=workspace_root,
        repair_attempt=repair_attempt,
        session_kind="edit_worker",
        use_persistent_session=False,
    )


def _repair_no_edit_model_response(
    *,
    round_brief: StrategyRoundBrief,
    error_message: str,
    no_edit_attempt: int,
    workspace_root: Path,
    last_response_text: str,
) -> str:
    prompt = build_strategy_no_edit_repair_prompt(
        no_edit_attempt=no_edit_attempt,
        error_message=error_message,
        last_response_text=last_response_text,
        task_summary=_round_brief_task_summary(round_brief),
    )
    return _run_model_text_request(
        prompt=prompt,
        system_prompt=build_strategy_repair_worker_system_prompt(),
        phase="model_no_edit_repair",
        workspace_root=workspace_root,
        repair_attempt=no_edit_attempt,
        session_kind="repair_worker",
        use_persistent_session=False,
    )


def _repair_model_candidate_source(
    *,
    failed_candidate: StrategyCandidate,
    error_message: str,
    repair_attempt: int,
    workspace_root: Path,
) -> str:
    prompt = build_strategy_runtime_repair_prompt(
        candidate_id=failed_candidate.candidate_id,
        primary_direction=failed_candidate.primary_direction,
        hypothesis=failed_candidate.hypothesis,
        change_plan=failed_candidate.change_plan,
        change_tags=failed_candidate.change_tags,
        edited_regions=failed_candidate.edited_regions,
        expected_effects=failed_candidate.expected_effects,
        novelty_proof=failed_candidate.novelty_proof,
        error_message=error_message,
        repair_attempt=repair_attempt,
    )
    return _run_model_text_request(
        prompt=prompt,
        system_prompt=build_strategy_repair_worker_system_prompt(),
        phase="model_repair",
        workspace_root=workspace_root,
        repair_attempt=repair_attempt,
        session_kind="repair_worker",
        use_persistent_session=False,
    )


def _regenerate_model_round_brief(
    *,
    base_source: str,
    failed_candidate: StrategyCandidate,
    block_info: dict[str, Any],
    regeneration_attempt: int,
    workspace_root: Path,
    planner_session_kind: str = "planner",
    use_persistent_planner_session: bool = True,
) -> StrategyRoundBrief:
    prompt = build_strategy_exploration_repair_prompt(
        candidate_id=failed_candidate.candidate_id,
        primary_direction=failed_candidate.primary_direction,
        hypothesis=failed_candidate.hypothesis,
        change_plan=failed_candidate.change_plan,
        change_tags=failed_candidate.change_tags,
        edited_regions=failed_candidate.edited_regions,
        expected_effects=failed_candidate.expected_effects,
        novelty_proof=failed_candidate.novelty_proof,
        block_kind=str(block_info.get("block_kind", "")).strip() or "same_cluster",
        blocked_cluster=str(block_info.get("blocked_cluster", "")).strip() or "-",
        blocked_reason=str(block_info.get("blocked_reason", "")).strip() or "系统拒收",
        locked_clusters=tuple(block_info.get("current_locks", ()) or ()),
        regeneration_attempt=regeneration_attempt,
        feedback_note=str(block_info.get("feedback_note", "")).strip(),
    )
    round_brief = _request_validated_round_brief(
        base_source=base_source,
        prompt=prompt,
        system_prompt=build_strategy_planner_system_prompt(),
        phase="model_regenerate",
        workspace_root=workspace_root,
        retry_phase="model_regenerate_brief_repair",
        session_kind=planner_session_kind,
        use_persistent_session=use_persistent_planner_session,
    )
    benchmark_report = _reference_benchmark_report()
    if benchmark_report is None or best_report is None:
        raise StrategySourceError("reference benchmark is not initialized")
    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    journal_summary = build_journal_prompt_summary(
        journal_entries,
        limit=RUNTIME.max_recent_journal_entries,
        journal_path=RUNTIME.paths.journal_file,
        prompt_compact=True,
        current_score_regime=SCORE_REGIME,
        current_iteration=iteration_counter,
        active_stage_started_at=reference_stage_started_at,
        active_stage_iteration=reference_stage_iteration,
        active_reference_code_hash=source_hash(best_source) if best_source else "",
        reference_role=_reference_role(),
        reference_metrics=benchmark_report.metrics,
        memory_root=RUNTIME.paths.memory_dir,
    )
    return _review_and_revise_round_brief(
        round_brief=round_brief,
        base_source=base_source,
        evaluation_summary=best_report.prompt_summary_text,
        journal_summary=journal_summary,
        workspace_root=workspace_root,
        planner_session_kind=planner_session_kind,
        use_persistent_planner_session=use_persistent_planner_session,
        reviewer_phase="model_regenerate_reviewer",
        reviewer_retry_phase="model_regenerate_reviewer_repair",
        planner_revise_phase="model_regenerate_reviewer_revise",
        planner_revise_retry_phase="model_regenerate_reviewer_revise_brief_repair",
        stage_label="regenerate_review",
    )


def _candidate_from_round_brief_with_validation_repair(
    *,
    round_brief: StrategyRoundBrief,
    base_source: str,
    workspace_root: Path,
    context_label: str,
) -> StrategyCandidate:
    workspace_strategy_file = workspace_root / MODEL_WORKSPACE_STRATEGY_PATH
    try:
        candidate = _candidate_from_round_brief(
            round_brief,
            workspace_strategy_file=workspace_strategy_file,
            base_source=base_source,
        )
        return _rebase_candidate_metadata_to_final_code(
            candidate=candidate,
            round_brief=round_brief,
            base_source=base_source,
            workspace_root=workspace_root,
            context_label=context_label,
        )
    except StrategySourceError as exc:
        failed_candidate = _candidate_stub_from_round_brief(
            round_brief,
            workspace_strategy_file=workspace_strategy_file,
            fallback_source=base_source,
        )
        errors = [str(exc)]
        for attempt in range(1, max(0, RUNTIME.max_repair_attempts) + 1):
            write_heartbeat(
                "candidate_repairing",
                message=f"iteration {iteration_counter} repairing candidate validation",
                repair_attempt=attempt,
                max_repair_attempts=RUNTIME.max_repair_attempts,
                error=errors[-1],
                repair_context=context_label,
            )
            log_info(
                f"第 {iteration_counter} 轮{context_label}源码校验失败，尝试同轮修复 "
                f"{attempt}/{RUNTIME.max_repair_attempts}: {errors[-1]}"
            )
            _repair_model_candidate_source(
                failed_candidate=failed_candidate,
                error_message="\n".join(errors[-3:]),
                repair_attempt=attempt,
                workspace_root=workspace_root,
            )
            failed_candidate = _candidate_stub_from_round_brief(
                _round_brief_from_candidate(failed_candidate),
                workspace_strategy_file=workspace_strategy_file,
                fallback_source=failed_candidate.strategy_code,
            )
            try:
                candidate = _candidate_from_round_brief(
                    round_brief,
                    workspace_strategy_file=workspace_strategy_file,
                    base_source=base_source,
                )
                return _rebase_candidate_metadata_to_final_code(
                    candidate=candidate,
                    round_brief=round_brief,
                    base_source=base_source,
                    workspace_root=workspace_root,
                    context_label=context_label,
                )
            except StrategySourceError as repair_exc:
                errors.append(str(repair_exc))
        raise CandidateRepairExhausted(
            failed_candidate,
            errors,
            failure_stage="candidate_validation",
        ) from exc


def _build_model_candidate(
    base_source: str,
    journal_entries: list[dict[str, Any]],
    *,
    workspace_root: Path,
    planner_session_kind: str = "planner",
    use_persistent_planner_session: bool = True,
) -> StrategyCandidate:
    workspace_strategy_file = workspace_root / MODEL_WORKSPACE_STRATEGY_PATH
    round_brief = _build_model_round_brief(
        base_source,
        journal_entries,
        workspace_root=workspace_root,
        planner_session_kind=planner_session_kind,
        use_persistent_planner_session=use_persistent_planner_session,
    )
    last_response_text = _run_edit_worker(
        base_source=base_source,
        round_brief=round_brief,
        workspace_root=workspace_root,
        phase="model_edit_worker",
    )
    no_edit_errors: list[str] = []
    try:
        _workspace_strategy_changed_source_or_raise(
            workspace_strategy_file=workspace_strategy_file,
            base_source=base_source,
        )
    except StrategySourceError as exc:
        if not _is_no_edit_error_message(str(exc)):
            raise
        no_edit_errors.append(str(exc))
        failed_candidate = _no_edit_failure_candidate(
            workspace_strategy_file=workspace_strategy_file,
            base_source=base_source,
            context_label="initial_candidate",
            last_response_text=last_response_text,
            round_brief=round_brief,
        )
        for attempt in range(1, max(0, RUNTIME.max_no_edit_repair_attempts) + 1):
            write_heartbeat(
                "candidate_repairing",
                message=f"iteration {iteration_counter} repairing no-edit candidate",
                repair_attempt=attempt,
                max_repair_attempts=RUNTIME.max_no_edit_repair_attempts,
                error=no_edit_errors[-1],
                repair_context="no_edit",
            )
            log_info(
                f"第 {iteration_counter} 轮初始候选未落码，尝试 no-edit 修复 "
                f"{attempt}/{RUNTIME.max_no_edit_repair_attempts}: {no_edit_errors[-1]}"
            )
            last_response_text = _repair_no_edit_model_response(
                round_brief=round_brief,
                error_message="\n".join(no_edit_errors[-3:]),
                no_edit_attempt=attempt,
                workspace_root=workspace_root,
                last_response_text=last_response_text,
            )
            try:
                edited_source = _workspace_strategy_changed_source_or_raise(
                    workspace_strategy_file=workspace_strategy_file,
                    base_source=base_source,
                )
                break
            except StrategySourceError as repair_exc:
                if not _is_no_edit_error_message(str(repair_exc)):
                    raise
                no_edit_errors.append(str(repair_exc))
                failed_candidate = _no_edit_failure_candidate(
                    workspace_strategy_file=workspace_strategy_file,
                    base_source=base_source,
                    context_label=f"no_edit_repair_{attempt}",
                    last_response_text=last_response_text,
                    round_brief=round_brief,
                )
        else:
            raise CandidateRepairExhausted(
                failed_candidate,
                no_edit_errors,
                failure_stage="candidate_no_edit",
            ) from exc

    return _candidate_from_round_brief_with_validation_repair(
        round_brief=round_brief,
        base_source=base_source,
        workspace_root=workspace_root,
        context_label="初始候选",
    )


def _regenerate_model_candidate(
    *,
    base_source: str,
    failed_candidate: StrategyCandidate,
    block_info: dict[str, Any],
    regeneration_attempt: int,
    workspace_root: Path,
    planner_session_kind: str = "planner",
    use_persistent_planner_session: bool = True,
) -> StrategyCandidate:
    _persist_last_rejected_candidate_snapshot(
        memory_root=RUNTIME.paths.memory_dir,
        base_source=base_source,
        failed_candidate=failed_candidate,
        block_info=block_info,
    )
    _rebase_workspace_strategy_to_base(
        workspace_root=workspace_root,
        base_source=base_source,
    )
    round_brief = _regenerate_model_round_brief(
        base_source=base_source,
        failed_candidate=failed_candidate,
        block_info=block_info,
        regeneration_attempt=regeneration_attempt,
        workspace_root=workspace_root,
        planner_session_kind=planner_session_kind,
        use_persistent_planner_session=use_persistent_planner_session,
    )
    _run_edit_worker(
        base_source=base_source,
        round_brief=round_brief,
        workspace_root=workspace_root,
        phase="model_regenerate_edit_worker",
        repair_attempt=regeneration_attempt,
    )
    return _candidate_from_round_brief_with_validation_repair(
        round_brief=round_brief,
        base_source=base_source,
        workspace_root=workspace_root,
        context_label="候选重生",
    )


def _repair_model_candidate(
    *,
    base_source: str,
    failed_candidate: StrategyCandidate,
    error_message: str,
    repair_attempt: int,
    workspace_root: Path,
) -> StrategyCandidate:
    _repair_model_candidate_source(
        failed_candidate=failed_candidate,
        error_message=error_message,
        repair_attempt=repair_attempt,
        workspace_root=workspace_root,
    )
    return _candidate_from_round_brief_with_validation_repair(
        round_brief=_round_brief_from_candidate(failed_candidate),
        base_source=base_source,
        workspace_root=workspace_root,
        context_label="运行修复",
    )


def build_strategy_candidate(
    base_source: str,
    journal_entries: list[dict[str, Any]],
    *,
    workspace_root: Path,
    planner_session_kind: str = "planner",
    use_persistent_planner_session: bool = True,
) -> StrategyCandidate:
    try:
        return _build_model_candidate(
            base_source,
            journal_entries,
            workspace_root=workspace_root,
            planner_session_kind=planner_session_kind,
            use_persistent_planner_session=use_persistent_planner_session,
        )
    except StrategyGenerationTransientError:
        raise
    except Exception:
        raise


# ==================== 主参考状态管理 ====================


def _persist_best_state(
    source: str,
    report: EvaluationReport,
    *,
    test_metrics: dict[str, float] | None = None,
    stage_started_at: str = "",
    stage_iteration: int = 0,
    suppress_initialize_saved_reference_discord_once: bool = False,
) -> None:
    persist_best_state_helper(
        RUNTIME.paths.best_state_file,
        RUNTIME.paths.best_strategy_file,
        RUNTIME.paths.champion_strategy_file,
        source,
        report,
        score_regime=SCORE_REGIME,
        test_metrics=test_metrics,
        stage_started_at=stage_started_at,
        stage_iteration=stage_iteration,
        suppress_initialize_saved_reference_discord_once=suppress_initialize_saved_reference_discord_once,
    )


def initialize_best_state(force_rebuild: bool = False) -> None:
    global best_source, best_report, champion_source, champion_report
    global champion_test_metrics, reference_stage_started_at, reference_stage_iteration

    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    saved_state = _load_saved_reference_state()
    saved_regime = str(saved_state.get("score_regime", "")).strip()
    saved_reference = saved_state.get("reference") or saved_state.get("working_base")
    can_load_saved_reference = (
        not force_rebuild
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
            champion_source = best_source if best_report.gate_passed else ""
            champion_report = best_report if best_report.gate_passed else None
            champion_test_metrics = _state_test_metrics(saved_state) if champion_report is not None else None
            suppress_initialize_saved_reference_discord_once = bool(
                saved_state.get("suppress_initialize_saved_reference_discord_once", False)
            )

            reference_stage_started_at, reference_stage_iteration = _recover_reference_stage_state(
                saved_state,
                journal_entries,
                reference_code_hash=source_hash(best_source),
            )
            _persist_best_state(
                best_source,
                best_report,
                test_metrics=champion_test_metrics,
                stage_started_at=reference_stage_started_at,
                stage_iteration=reference_stage_iteration,
                suppress_initialize_saved_reference_discord_once=False,
            )
            load_message = "已加载已保存主参考"
            if saved_regime and saved_regime != SCORE_REGIME:
                load_message = f"已按新评分口径重算已保存主参考({saved_regime} -> {SCORE_REGIME})"
            log_info(
                f"{load_message}: "
                f"role={_reference_role()}, "
                f"quality={best_report.metrics['quality_score']:.2f}, "
                f"promotion={best_report.metrics['promotion_score']:.2f}, "
                f"gate={best_report.gate_reason}"
            )
            _align_research_session_scope(force_reset=False)
            write_heartbeat(
                "initialized",
                message="loaded saved reference",
                reference_role=_reference_role(),
                promotion=best_report.metrics["promotion_score"],
                quality=best_report.metrics["quality_score"],
            )
            if suppress_initialize_saved_reference_discord_once:
                log_info("跳过 Discord 启动播报: 上次退出原因是 new champion accepted")
            else:
                maybe_send_discord(
                    build_discord_summary_message(
                        title=f"📌 研究器 v2 已加载{_reference_role()}参考",
                        report=best_report,
                        eval_window_count=EVAL_WINDOW_COUNT,
                        validation_window_count=VALIDATION_WINDOW_COUNT,
                        test_window_count=TEST_WINDOW_COUNT,
                        data_range_text=_discord_data_range_text(),
                        test_metrics=champion_test_metrics,
                    ),
                    context="initialize_saved_reference",
                )
            return

    best_source = load_strategy_source(RUNTIME.paths.strategy_file)
    validate_strategy_source(best_source)
    reload_strategy_module()
    best_report = evaluate_current_strategy()
    champion_source = best_source if best_report.gate_passed else ""
    champion_report = best_report if best_report.gate_passed else None
    champion_test_metrics = _state_test_metrics(saved_state) if champion_report else None
    reference_stage_started_at = datetime.now(UTC).isoformat()
    reference_stage_iteration = (
        max((int(entry.get("iteration", 0) or 0) for entry in journal_entries), default=0) + 1
    )
    _persist_best_state(
        best_source,
        best_report,
        test_metrics=champion_test_metrics,
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
    _align_research_session_scope(force_reset=force_rebuild)
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
            test_metrics=champion_test_metrics,
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
    )
    actual_changed_regions = sorted(candidate_signature["changed_regions"])
    actual_region_families = sorted(candidate_signature["region_families"])
    actual_ordinary_region_families = sorted(candidate_signature["ordinary_region_families"])
    actual_special_region_families = sorted(candidate_signature["special_region_families"])
    actual_ordinary_changed_regions = sorted(candidate_signature["ordinary_changed_regions"])
    actual_param_families = sorted(candidate_signature["param_families"])
    actual_structural_tokens = sorted(candidate_signature["structural_tokens"])
    complexity_delta = build_strategy_complexity_delta(base_source, candidate.strategy_code)
    base_complexity_pressure = build_strategy_complexity_pressure(base_source)
    candidate_complexity_pressure = build_strategy_complexity_pressure(
        candidate.strategy_code,
        base_source=base_source,
    )
    runtime_failure_stage = str((extra_fields or {}).get("runtime_failure_stage", "")).strip()
    smoke_passed = (
        candidate_report is not None
        or outcome in {"behavioral_noop", "early_rejected"}
        or runtime_failure_stage == "full_eval"
    )
    full_eval_reached = (
        candidate_report is not None
        or outcome == "early_rejected"
        or runtime_failure_stage == "full_eval"
    )
    direction_domain = (
        candidate.primary_direction.split("|", 1)[0].strip().lower()
        if "|" in candidate.primary_direction
        else (candidate_signature.get("target_family") or "structure")
    )
    if outcome == "behavioral_noop" or stop_stage == "behavioral_noop":
        direction_failure_layer = "behavioral_noop"
    elif outcome == "runtime_failed":
        direction_failure_layer = runtime_failure_stage or "runtime"
    elif outcome == "early_rejected" or stop_stage == "early_reject":
        direction_failure_layer = "early_reject"
    elif outcome == "duplicate_skipped" and stop_stage == "duplicate_history":
        direction_failure_layer = "duplicate_history"
    elif outcome == "duplicate_skipped" and stop_stage == "duplicate_result_basin":
        direction_failure_layer = "result_basin"
    else:
        direction_failure_layer = (actual_ordinary_region_families[0] if actual_ordinary_region_families else stop_stage or outcome or "unknown")
    direction_shadow_key = "|".join(
        [
            str(direction_domain or "structure").strip() or "structure",
            direction_failure_layer,
            ",".join(actual_ordinary_region_families[:3] or actual_param_families[:2]) or "none",
        ]
    )
    promotion_delta = promotion_score - base_promotion if promotion_score is not None else None
    direction_exception_win = outcome == "accepted" or (
        promotion_delta is not None and promotion_delta > 0.01
    )
    entry = {
        "iteration": iteration_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "candidate_id": candidate.candidate_id,
        "outcome": outcome,
        "stop_stage": stop_stage,
        "primary_direction": candidate.primary_direction,
        "declared_primary_direction": candidate.primary_direction,
        "direction_shadow_key": direction_shadow_key,
        "direction_exception_win": direction_exception_win,
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
        "exit_range_scan": candidate.exit_range_scan or {},
        "exit_range_scan_result": candidate.exit_range_scan_result or {},
        "plateau_probe": candidate.plateau_probe_result or {},
        "cluster_key": str(candidate_signature.get("cluster_key", "")).strip()
        or cluster_key_for_components("", candidate.change_tags),
        "quality_score": quality_score,
        "promotion_score": promotion_score,
        "promotion_delta": promotion_delta,
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
        "system_complexity_families": complexity_delta["families"],
        "system_complexity_summary": complexity_delta["summary"],
        "system_complexity_flags": list(complexity_delta["flags"]),
        "system_bloat_flag": bool(complexity_delta["bloat_flag"]),
        "system_base_complexity_level": str(base_complexity_pressure.get("level", "normal")).strip() or "normal",
        "system_base_complexity_summary": str(base_complexity_pressure.get("summary", "")).strip(),
        "system_candidate_complexity_level": str(candidate_complexity_pressure.get("level", "normal")).strip()
        or "normal",
        "system_candidate_complexity_headroom_level": str(
            candidate_complexity_pressure.get("headroom_level", "normal")
        ).strip()
        or "normal",
        "system_candidate_complexity_growth_level": str(
            candidate_complexity_pressure.get("growth_level", "normal")
        ).strip()
        or "normal",
        "system_candidate_complexity_summary": str(candidate_complexity_pressure.get("summary", "")).strip(),
        "declared_regions_match_system": sorted(candidate.edited_regions) == actual_changed_regions,
        "smoke_passed": smoke_passed,
        "full_eval_reached": full_eval_reached,
        "target_family": target_family_from_text(
            candidate.change_tags,
            candidate.hypothesis,
            candidate.expected_effects,
        ),
        "core_factor_names": sorted(candidate_signature["core_factor_names"]),
        "score_regime": SCORE_REGIME,
        "reference_code_hash": source_hash(base_source),
        "reference_role": _reference_role(),
    }
    entry["result_basin_key"] = result_basin_key_for_entry(entry)
    if extra_fields:
        entry.update(extra_fields)
        entry["result_basin_key"] = result_basin_key_for_entry(entry)
    return entry


def _promotion_acceptance_decision(report: EvaluationReport) -> tuple[bool, str]:
    if best_report is None:
        return False, "reference state is not initialized"
    if not report.gate_passed:
        return False, report.gate_reason

    if not best_report.gate_passed:
        return True, "通过(首个 gate-passed champion)"

    benchmark_report = _reference_benchmark_report()
    if benchmark_report is None:
        return False, "reference benchmark is not initialized"
    current_best_score = float(benchmark_report.metrics["promotion_score"])
    candidate_score = float(report.metrics["promotion_score"])
    current_best_quality = benchmark_report.metrics.get("quality_score")
    candidate_quality = report.metrics.get("quality_score")
    accept_margin = max(0.0, float(RUNTIME.promotion_accept_margin))
    quality_drop_margin = max(accept_margin, float(RUNTIME.promotion_accept_quality_drop_margin))
    required_score = current_best_score + accept_margin
    quality_score_dropped = (
        current_best_quality is not None
        and candidate_quality is not None
        and float(candidate_quality) < float(current_best_quality)
    )
    if quality_score_dropped:
        required_score = current_best_score + quality_drop_margin
    if candidate_score + 1e-12 < required_score:
        if quality_score_dropped:
            return (
                False,
                f"质量分回落时未达到更高晋级门槛({candidate_score:.2f} < {required_score:.2f})",
            )
        return (
            False,
            f"未达到当前{_benchmark_role()}晋级门槛({candidate_score:.2f} < {required_score:.2f})",
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
        ),
        strategy_source=candidate.strategy_code,
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _record_generation_invalid(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    block_info: dict[str, Any],
    note: str,
) -> None:
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            outcome="generation_invalid",
            stop_stage=str(block_info.get("stop_stage", "blocked_invalid_generation")),
            gate_reason=str(block_info.get("blocked_reason", "")).strip() or "候选未产生真实代码改动",
            note=note,
            extra_fields={
                "block_kind": str(block_info.get("block_kind", "")).strip(),
                "blocked_cluster": str(block_info.get("blocked_cluster", "")).strip(),
                "technical_generation_invalid": True,
                "current_locks": list(block_info.get("current_locks", ()) or ()),
                "invalid_reasons": list(block_info.get("invalid_reasons", ()) or ()),
            },
        ),
        strategy_source=candidate.strategy_code,
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
        ),
        strategy_source=candidate.strategy_code,
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
        ),
        strategy_source=candidate.strategy_code,
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _record_runtime_failure(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    errors: list[str],
    failure_stage: str,
    stop_stage: str,
) -> None:
    last_error = errors[-1] if errors else "运行失败"
    extra_fields: dict[str, Any] = {
        "runtime_failure_stage": failure_stage,
        "decision_reason": last_error,
    }
    if _is_complexity_error_message(last_error):
        extra_fields["system_bloat_flag"] = True
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            outcome="runtime_failed",
            stop_stage=stop_stage,
            gate_reason="运行失败",
            note="；".join(errors),
            extra_fields=extra_fields,
        ),
        strategy_source=candidate.strategy_code,
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _consecutive_no_edit_runtime_failures(entries: list[dict[str, Any]]) -> int:
    count = 0
    for entry in reversed(entries):
        if str(entry.get("outcome", "")).strip() != "runtime_failed":
            break
        failure_stage = str(entry.get("runtime_failure_stage", "")).strip()
        decision_reason = str(entry.get("decision_reason", "")).strip()
        if failure_stage not in {"candidate_no_edit", "candidate_validation"}:
            break
        if not _is_no_edit_error_message(decision_reason):
            break
        count += 1
    return count


def _maybe_stop_for_no_edit_stall(*, iteration_id: int) -> bool:
    entries = load_journal_entries(RUNTIME.paths.journal_file)
    consecutive_failures = _consecutive_no_edit_runtime_failures(entries)
    if consecutive_failures < int(RUNTIME.max_consecutive_no_edit_failures_before_stop):
        return False
    stop_reason = (
        f"连续 {consecutive_failures} 轮未把改动落到 src/strategy_macd_aggressive.py，"
        "研究器已自动停止。"
    )
    RUNTIME.paths.stop_file.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME.paths.stop_file.write_text(stop_reason + "\n")
    log_info(f"第 {iteration_id} 轮触发自动停机: {stop_reason}")
    write_heartbeat(
        "stopped",
        message=stop_reason,
        stop_reason="consecutive_no_edit_failures",
        consecutive_no_edit_failures=consecutive_failures,
    )
    maybe_send_discord(
        stop_reason,
        context=f"no_edit_stop_iteration_{iteration_id}",
    )
    return True


def _activate_candidate(candidate: StrategyCandidate) -> None:
    write_strategy_source(RUNTIME.paths.strategy_backup_file, candidate.strategy_code)
    write_strategy_source(RUNTIME.paths.strategy_file, candidate.strategy_code)
    reload_strategy_module()


def _smoke_candidate(candidate: StrategyCandidate) -> list[dict[str, Any]]:
    _activate_candidate(candidate)
    try:
        prepared_context = _prepare_backtest_context()
        return _run_base_backtests(
            windows=_selected_smoke_windows(),
            include_diagnostics=True,
            heartbeat_phase="smoke_test",
            prepared_context=prepared_context,
        )
    except Exception as exc:
        raise CandidateRuntimeFailure("smoke_test", exc) from exc


def _evaluate_candidate(
    candidate: StrategyCandidate,
    base_source: str | None = None,
) -> EvaluationReport:
    _activate_candidate(candidate)
    resolved_base_source = base_source if base_source is not None else best_source
    try:
        return evaluate_current_strategy(
            allow_early_reject=True,
            plateau_probe_candidate=candidate,
            plateau_probe_base_source=resolved_base_source,
        )
    except EarlyRejection:
        raise
    except Exception as exc:
        raise CandidateRuntimeFailure("full_eval", exc) from exc


def _exit_range_scan_windows() -> list[Any]:
    return select_smoke_windows(WINDOWS, max(0, RUNTIME.exit_range_scan_windows))


def _format_exit_range_scan_log(result: dict[str, Any]) -> str:
    rows = result.get("summary", []) if isinstance(result, dict) else []
    compact = []
    for row in rows[:5]:
        compact.append(
            f"{row.get('value')}=>ret={float(row.get('mean_return', 0.0)):.2f}%,"
            f"dd={float(row.get('max_drawdown', 0.0)):.2f}%,"
            f"fee={float(row.get('mean_fee_drag', 0.0)):.2f}%"
        )
    return "; ".join(compact)


def _split_window_evenly(window: ResearchWindow, *, parts: int, label_prefix: str) -> list[ResearchWindow]:
    start_dt = datetime.strptime(window.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(window.end_date, "%Y-%m-%d")
    total_days = (end_dt - start_dt).days + 1
    actual_parts = max(1, min(int(parts), total_days))
    base_size, remainder = divmod(total_days, actual_parts)
    windows: list[ResearchWindow] = []
    cursor = start_dt
    for index in range(actual_parts):
        window_days = base_size + (1 if index < remainder else 0)
        part_end = cursor + timedelta(days=window_days - 1)
        windows.append(
            ResearchWindow(
                group=window.group,
                label=f"{label_prefix}{index + 1}",
                start_date=cursor.strftime("%Y-%m-%d"),
                end_date=part_end.strftime("%Y-%m-%d"),
                weight=window.weight,
            )
        )
        cursor = part_end + timedelta(days=1)
    return windows


def _plateau_probe_windows() -> list[ResearchWindow]:
    return _split_window_evenly(_validation_window(), parts=3, label_prefix="val_probe")


def _format_plateau_probe_log(result: dict[str, Any]) -> str:
    rows = result.get("summary", []) if isinstance(result, dict) else []
    compact = []
    for row in rows[:5]:
        compact.append(
            f"{row.get('value')}=>score={float(row.get('mean_period_score', 0.0)):.2f},"
            f"ret={float(row.get('mean_return', 0.0)):.2f}%,"
            f"dd={float(row.get('max_drawdown', 0.0)):.2f}%"
        )
    return "; ".join(compact)


def _run_candidate_plateau_probe(
    candidate: StrategyCandidate,
    *,
    base_source: str,
) -> dict[str, Any]:
    if not RUNTIME.plateau_probe_enabled:
        return {
            "enabled": False,
            "skipped_reason": "plateau_probe_disabled",
        }
    spec = infer_exit_range_scan_spec(
        base_source,
        candidate.strategy_code,
        candidate.exit_range_scan,
        max_values=RUNTIME.exit_range_scan_max_values,
    )
    if spec is None:
        return {
            "enabled": False,
            "skipped_reason": "no_exit_param_probe_target",
        }

    probe_windows = _plateau_probe_windows()
    write_heartbeat(
        "plateau_probing",
        message=f"iteration {iteration_counter} plateau probe",
        phase="plateau_probe",
        current_window=spec.param,
        window_index=0,
        window_count=len(probe_windows),
    )
    outcome = run_plateau_probe(
        repo_root=REPO_ROOT,
        spec=spec,
        current_exit_params=active_exit_params(),
        windows=probe_windows,
        workers=RUNTIME.exit_range_scan_workers,
    )
    result_payload = outcome.to_dict()
    log_info(
        f"plateau probe: param={outcome.param}, values={list(outcome.values)}, "
        f"center={outcome.center_period_score:.2f}, best={outcome.best_period_score:.2f}, "
        f"gap={outcome.center_gap:.2f}, score_span={outcome.score_span:.2f}, "
        f"dd_span={outcome.drawdown_span:.2f}; {_format_plateau_probe_log(result_payload)}"
    )
    return result_payload


def _maybe_apply_exit_range_scan(candidate: StrategyCandidate, *, base_source: str) -> StrategyCandidate:
    if not RUNTIME.exit_range_scan_enabled:
        return candidate
    spec = infer_exit_range_scan_spec(
        base_source,
        candidate.strategy_code,
        candidate.exit_range_scan,
        max_values=RUNTIME.exit_range_scan_max_values,
    )
    if spec is None:
        return candidate

    _activate_candidate(candidate)
    scan_windows = _exit_range_scan_windows()
    write_heartbeat(
        "exit_range_scanning",
        message=f"iteration {iteration_counter} exit range scan",
        phase="exit_range_scan",
        current_window=spec.param,
        window_index=0,
        window_count=len(scan_windows),
    )
    try:
        outcome = run_exit_range_scan(
            repo_root=REPO_ROOT,
            spec=spec,
            current_exit_params=active_exit_params(),
            windows=scan_windows,
            max_fee_drag_pct=RUNTIME.gates.max_fee_drag_pct,
            max_fee_mult=RUNTIME.exit_range_scan_max_fee_mult,
            workers=RUNTIME.exit_range_scan_workers,
        )
    except Exception as exc:
        log_info(f"exit range scan 失败，保留原候选: {exc}")
        logging.exception("exit range scan failed")
        return replace(
            candidate,
            exit_range_scan_result={
                "enabled": True,
                "applied": False,
                "param": spec.param,
                "values": list(spec.values),
                "skipped_reason": str(exc),
            },
        )

    result_payload = outcome.to_dict()
    log_info(
        f"exit range scan: param={outcome.param}, values={list(outcome.values)}, "
        f"selected={outcome.selected_value}, applied={outcome.applied}; "
        f"{_format_exit_range_scan_log(result_payload)}"
    )
    if not outcome.applied or outcome.selected_value is None:
        return replace(candidate, exit_range_scan_result=result_payload)

    updated_source = replace_exit_param_value(candidate.strategy_code, outcome.param, outcome.selected_value)
    validate_strategy_source(updated_source, base_source=base_source)
    updated_regions = _actual_changed_regions(base_source=base_source, strategy_code=updated_source)
    updated_tags = candidate.change_tags
    if "exit_range_scan" not in updated_tags:
        updated_tags = tuple([*updated_tags, "exit_range_scan"][:6])
    updated = replace(
        candidate,
        strategy_code=updated_source,
        edited_regions=updated_regions,
        change_tags=updated_tags,
        exit_range_scan_result=result_payload,
    )
    write_strategy_source(RUNTIME.paths.strategy_file, updated.strategy_code)
    reload_strategy_module()
    return updated


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
            base_behavior = _base_behavior_profile(base_source)
            candidate_behavior = _behavior_profile_from_results(_smoke_candidate(current))
            behavior_diff = _behavior_diff_payload(base_behavior, candidate_behavior)
            if not behavior_diff["changed"]:
                raise CandidateBehavioralNoop(current, behavior_diff)
            current = _maybe_apply_exit_range_scan(current, base_source=base_source)
            report = _evaluate_candidate(current)
            plateau_probe_result = {}
            if isinstance(report.artifacts, dict):
                raw_plateau_probe = report.artifacts.get("plateau_probe")
                if isinstance(raw_plateau_probe, dict):
                    plateau_probe_result = dict(raw_plateau_probe)
            if plateau_probe_result:
                current = replace(current, plateau_probe_result=plateau_probe_result)
            return current, report
        except CandidateRuntimeFailure as exc:
            error_message = "".join(
                traceback.format_exception_only(type(exc.error), exc.error)
            ).strip()
            errors.append(f"{exc.stage}: {error_message}")
            write_strategy_source(RUNTIME.paths.strategy_file, base_source)
            reload_strategy_module()
            if attempt >= RUNTIME.max_repair_attempts:
                raise CandidateRepairExhausted(current, errors, failure_stage=exc.stage) from exc
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
    global best_source, best_report, champion_source, champion_report
    global champion_test_metrics, reference_stage_started_at, reference_stage_iteration

    if best_report is None:
        raise RuntimeError("reference state is not initialized")

    _drain_rejected_test_futures()
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

    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    planner_session_kind = "planner"
    use_persistent_planner_session = True
    write_heartbeat(
        "iteration_preparing",
        message=f"iteration {iteration_id} preparing candidate",
    )
    if use_persistent_planner_session:
        _align_research_session_scope(force_reset=False)
    workspace_root = _prepare_agent_workspace(
        base_source=best_source,
        reset_strategy=True,
    )

    try:
        candidate = build_strategy_candidate(
            best_source,
            journal_entries,
            workspace_root=workspace_root,
            planner_session_kind=planner_session_kind,
            use_persistent_planner_session=use_persistent_planner_session,
        )
    except (PlannerBriefInvalid, ReviewerRejected) as exc:
        write_strategy_source(RUNTIME.paths.strategy_file, best_source)
        reload_strategy_module()
        block_kind = str(exc.block_info.get("block_kind", "")).strip()
        if isinstance(exc, ReviewerRejected) and block_kind == "reviewer_rejected":
            _record_exploration_block(
                iteration_id=iteration_id,
                candidate=exc.candidate,
                base_source=best_source,
                block_info=exc.block_info,
            )
            log_info(f"第 {iteration_id} 轮 reviewer 打回 planner brief: {exc}")
            write_heartbeat(
                "iteration_exploration_blocked",
                message=f"iteration {iteration_id} reviewer rejected planner brief",
                block_kind=block_kind,
                blocked_cluster=str(exc.block_info.get('blocked_cluster', '')).strip(),
            )
            return "exploration_blocked"

        _record_generation_invalid(
            iteration_id=iteration_id,
            candidate=exc.candidate,
            base_source=best_source,
            block_info=exc.block_info,
            note=(
                "planner/reviewer 未按约定返回合法结果；系统已在同一轮内补正一次，"
                "仍未拿到可执行摘要，因此按 generation_invalid 记账。"
            ),
        )
        if isinstance(exc, ReviewerRejected):
            log_info(f"第 {iteration_id} 轮 reviewer 结果作废: {exc}")
        else:
            log_info(f"第 {iteration_id} 轮 planner brief 作废: {exc}")
        write_heartbeat(
            "iteration_generation_invalid",
            message=(
                f"iteration {iteration_id} reviewer invalid"
                if isinstance(exc, ReviewerRejected)
                else f"iteration {iteration_id} planner brief invalid"
            ),
            block_kind=block_kind,
            blocked_cluster=str(exc.block_info.get('blocked_cluster', '')).strip(),
        )
        return "generation_invalid"
    except CandidateRepairExhausted as exc:
        write_strategy_source(RUNTIME.paths.strategy_file, best_source)
        reload_strategy_module()
        last_error = exc.errors[-1] if exc.errors else str(exc)
        _record_runtime_failure(
            iteration_id=iteration_id,
            candidate=exc.candidate,
            base_source=best_source,
            errors=exc.errors,
            failure_stage=exc.failure_stage or "candidate_validation",
            stop_stage="candidate_validation",
        )
        log_info(f"第 {iteration_id} 轮候选源码校验失败并已记录: {exc}")
        write_heartbeat(
            "iteration_runtime_failed",
            message=f"iteration {iteration_id} candidate validation failed",
            error=str(exc),
        )
        if _is_no_edit_error_message(last_error) and _maybe_stop_for_no_edit_stall(iteration_id=iteration_id):
            return "stopped"
        return "runtime_failed"
    candidate_report: EvaluationReport | None = None
    exploration_regeneration_attempt = 0
    behavioral_noop_regeneration_attempt = 0

    while True:
        candidate_report = None
        journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
        invalid_generation_block = _candidate_invalid_generation_block_info(
            candidate,
            base_source=best_source,
        )
        if invalid_generation_block is not None:
            if exploration_regeneration_attempt >= RUNTIME.max_exploration_regen_attempts:
                _record_generation_invalid(
                    iteration_id=iteration_id,
                    candidate=candidate,
                    base_source=best_source,
                    block_info=invalid_generation_block,
                    note=(
                        "候选未产出真实代码改动；该轮已按技术性空转记账，"
                        "并从 failure wiki / 方向风险记忆中隔离。"
                    ),
                )
                log_info(
                    f"第 {iteration_id} 轮候选作废: "
                    f"{invalid_generation_block['blocked_reason']}"
                )
                write_heartbeat(
                    "iteration_generation_invalid",
                    message=f"iteration {iteration_id} generation invalid",
                    block_kind=invalid_generation_block["block_kind"],
                    blocked_cluster=invalid_generation_block["blocked_cluster"],
                )
                return "generation_invalid"

            write_heartbeat(
                "candidate_regenerating",
                message=f"iteration {iteration_id} regenerating invalid candidate",
                regeneration_attempt=exploration_regeneration_attempt + 1,
                max_regeneration_attempts=RUNTIME.max_exploration_regen_attempts,
                block_kind=invalid_generation_block["block_kind"],
                blocked_cluster=invalid_generation_block["blocked_cluster"],
            )
            exploration_regeneration_attempt += 1
            candidate = _regenerate_model_candidate(
                base_source=best_source,
                failed_candidate=candidate,
                block_info=invalid_generation_block,
                regeneration_attempt=exploration_regeneration_attempt,
                workspace_root=workspace_root,
                planner_session_kind=planner_session_kind,
                use_persistent_planner_session=use_persistent_planner_session,
            )
            continue

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

        failure_wiki_index = load_failure_wiki_index(RUNTIME.paths.memory_dir)
        block_info = evaluate_candidate_failure_wiki_guard(
            candidate,
            failure_wiki_index,
            base_source=best_source,
        )
        if block_info is None:
            block_info = evaluate_candidate_exploration_guard(
                candidate,
                journal_entries,
                journal_path=RUNTIME.paths.journal_file,
                score_regime=SCORE_REGIME,
                current_iteration=iteration_id,
                base_source=best_source,
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
                planner_session_kind=planner_session_kind,
                use_persistent_planner_session=use_persistent_planner_session,
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
                        planner_session_kind=planner_session_kind,
                        use_persistent_planner_session=use_persistent_planner_session,
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
                ),
                strategy_source=candidate.strategy_code,
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
            _record_runtime_failure(
                iteration_id=iteration_id,
                candidate=exc.candidate,
                base_source=best_source,
                errors=exc.errors,
                failure_stage=exc.failure_stage or "runtime_error",
                stop_stage="runtime_error",
            )
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

    champion_accepted, decision_reason = _promotion_acceptance_decision(candidate_report)
    accepted = champion_accepted
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
        extra_fields={
            "reference_update_kind": "champion" if champion_accepted else "none",
        },
    )
    if accepted:
        entry_base["gate_reason"] = decision_reason
        entry_base["decision_reason"] = decision_reason
    recent_entries = load_journal_entries(RUNTIME.paths.journal_file)
    if not accepted:
        basin_key = str(entry_base.get("result_basin_key", "")).strip()
        repeated_basin_hits = count_recent_result_basin(recent_entries, basin_key)
        if repeated_basin_hits > 0:
            original_reason = str(entry_base.get("decision_reason", "")).strip() or decision_reason
            entry_base.update(
                {
                    "outcome": "duplicate_skipped",
                    "stop_stage": "duplicate_result_basin",
                    "gate_reason": "候选结果命中最近重复失败盆地",
                    "decision_reason": "候选结果命中最近重复失败盆地",
                    "note": (
                        f"完整评估已完成，但本轮结果与最近 {repeated_basin_hits} 条研究历史"
                        f"落在同一结果盆地；原始拒收原因：{original_reason}"
                    ),
                    "matched_result_basin_key": basin_key,
                    "matched_result_basin_hits": repeated_basin_hits,
                }
            )

    if champion_accepted:
        best_source = candidate.strategy_code
        best_report = candidate_report
        champion_source = candidate.strategy_code
        champion_report = candidate_report
        reference_stage_started_at = datetime.now(UTC).isoformat()
        reference_stage_iteration = iteration_id
        test_result: dict[str, Any] | None = None
        test_metrics: dict[str, float] | None = None
        try:
            test_result = evaluate_test_result()
            test_metrics = summarize_test_result(test_result)
            log_info(
                "test验收: "
                f"score={test_metrics['test_score']:.2f}, "
                f"return={test_metrics['test_total_return_pct']:.2f}%, "
                f"segments={int(test_metrics['test_segment_count'])}, "
                f"hit={test_metrics['test_hit_rate']:.0%}"
            )
        except Exception as exc:
            log_info(f"test评估失败，但本轮 champion 已按 val 保留: {exc}")
            logging.exception("test评估失败(iteration=%s)", iteration_id)
        champion_test_metrics = test_metrics or {}

        _persist_best_state(
            best_source,
            best_report,
            test_metrics=test_metrics,
            stage_started_at=reference_stage_started_at,
            stage_iteration=reference_stage_iteration,
            suppress_initialize_saved_reference_discord_once=True,
        )
        _clear_research_session_state(remove_workspace=True, reason="new champion accepted")
        _append_research_journal_entry(
            entry_base,
            strategy_source=best_source,
            persist_round_artifact=False,
        )
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
        champion_snapshot_dir: Path | None = None
        try:
            chart_paths = _generate_new_champion_charts(
                iteration_id,
                test_result=test_result,
            )
            if chart_paths.selection_chart is not None:
                log_info(f"train+val图已保存: {chart_paths.selection_chart}")
            if chart_paths.validation_chart is not None:
                log_info(f"val图已保存: {chart_paths.validation_chart}")
        except Exception as exc:
            log_info(f"新 champion 图表生成失败: {exc}")
            logging.exception("新 champion 图表生成失败(iteration=%s)", iteration_id)
        try:
            champion_snapshot_dir = _archive_champion_snapshot(
                iteration_id=iteration_id,
                accepted_at=reference_stage_started_at,
                candidate=candidate,
                source=best_source,
                report=best_report,
                test_metrics=test_metrics,
                chart_paths=chart_paths,
            )
        except Exception as exc:
            log_info(f"新 champion 快照归档失败: {exc}")
            logging.exception("新 champion 快照归档失败(iteration=%s)", iteration_id)
        try:
            _persist_round_artifact(
                entry_base,
                strategy_source=best_source,
                test_metrics=test_metrics,
                test_evaluation={
                    "status": "completed",
                    "mode": "accepted_sync",
                    "completed_at": datetime.now(UTC).isoformat(),
                },
                champion_snapshot_dir=champion_snapshot_dir,
                chart_paths=chart_paths,
            )
        except Exception as exc:
            log_info(f"新 champion round artifact 归档失败: {exc}")
            logging.exception("new champion round artifact persist failed(iteration=%s)", iteration_id)
        discord_message = build_discord_summary_message(
            title=f"🚀 研究器 v2 新 champion #{iteration_id}",
            report=best_report,
            eval_window_count=EVAL_WINDOW_COUNT,
            validation_window_count=VALIDATION_WINDOW_COUNT,
            test_window_count=TEST_WINDOW_COUNT,
            data_range_text=_discord_data_range_text(),
            test_metrics=test_metrics,
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

    round_dir = _append_research_journal_entry(
        entry_base,
        strategy_source=candidate.strategy_code,
    )
    if round_dir is not None:
        _queue_round_artifact_test(round_dir, reason="iteration_rejected")
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")
    write_strategy_source(RUNTIME.paths.strategy_file, best_source)
    reload_strategy_module()
    if str(entry_base.get("outcome", "")).strip() == "duplicate_skipped":
        log_info(
            f"第 {iteration_id} 轮跳过: 候选完整评估后命中最近重复结果盆地 "
            f"(hits={int(entry_base.get('matched_result_basin_hits', 0) or 0) + 1})"
        )
        write_heartbeat(
            "iteration_skipped",
            message=f"iteration {iteration_id} duplicate result basin",
            promotion=candidate_report.metrics["promotion_score"],
            quality=candidate_report.metrics["quality_score"],
            gate="候选结果命中最近重复失败盆地",
        )
        return "duplicate_skipped"
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
        _drain_rejected_test_futures()
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
    RUNTIME.paths.champion_strategy_file.parent.mkdir(parents=True, exist_ok=True)
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

    queued_backfill = _queue_pending_round_artifact_tests(reason="startup_backfill")
    if queued_backfill > 0:
        log_info(f"已补挂历史 reject test 队列: {queued_backfill} 条")

    while True:
        _drain_rejected_test_futures()
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
                "generation_invalid",
                "behavioral_noop",
                "runtime_failed",
                "exploration_blocked",
                "stopped",
            } else 1

        if outcome == "stopped":
            return 0

        _drain_rejected_test_futures()
        write_heartbeat(
            "sleeping",
            message=f"iteration {iteration_counter} sleeping",
            last_outcome=outcome,
            sleep_seconds=RUNTIME.loop_interval_seconds,
        )
        _sleep_with_stop(RUNTIME.loop_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
