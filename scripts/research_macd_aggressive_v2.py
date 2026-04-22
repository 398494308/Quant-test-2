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
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, replace
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
    StrategyGenerationSessionError,
    StrategyGenerationTransientError,
    generate_text_response,
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
    EDITABLE_REGIONS,
    build_strategy_agents_instructions,
    build_strategy_candidate_summary_prompt,
    build_strategy_edit_worker_prompt,
    build_strategy_exploration_repair_prompt,
    build_strategy_no_edit_repair_prompt,
    build_strategy_round_brief_repair_prompt,
    build_strategy_research_prompt,
    build_strategy_summary_worker_system_prompt,
    build_strategy_runtime_repair_prompt,
    build_strategy_system_prompt,
    build_strategy_worker_system_prompt,
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
    format_strategy_complexity_headroom,
    load_strategy_source,
    normalize_factor_change_mode,
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
FACTOR_ADMISSION_REMINDER_STALLS = 5
FACTOR_ADMISSION_SUGGEST_STALLS = 7
FACTOR_ADMISSION_FORCE_STALLS = 10
FACTOR_ADMISSION_MAX_BURST = 4
PLANNER_BRIEF_REQUIRED_FIELDS = ("hypothesis", "change_plan", "novelty_proof", "change_tags")
MAX_PLANNER_BRIEF_REPAIR_ATTEMPTS = 1

best_source = ""
best_report: EvaluationReport | None = None
champion_source = ""
champion_report: EvaluationReport | None = None
champion_shadow_test_metrics: dict[str, float] | None = None
iteration_counter = 0
reference_stage_started_at = ""
reference_stage_iteration = 0
research_session_state: dict[str, Any] = {}
COMPACTION_TRIGGER_STALLS = 5
COMPACTION_COMPLEXITY_LOOKBACK = 6
COMPACTION_PROMOTION_SLACK = 0.03
COMPACTION_QUALITY_SLACK = 0.03
COMPACTION_VALIDATION_TREND_SLACK = 0.05
COMPACTION_VALIDATION_HIT_RATE_SLACK = 0.05

logging.basicConfig(
    filename=RUNTIME.paths.log_file,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


@dataclass(frozen=True)
class StrategyRoundBrief:
    candidate_id: str
    hypothesis: str
    change_plan: str
    closest_failed_cluster: str
    novelty_proof: str
    change_tags: tuple[str, ...]
    expected_effects: tuple[str, ...]
    core_factors: tuple[StrategyCoreFactor, ...]


@dataclass(frozen=True)
class PlannerBriefInvalid(Exception):
    candidate: StrategyCandidate
    block_info: dict[str, Any]
    errors: tuple[str, ...]

    def __str__(self) -> str:
        return self.errors[-1] if self.errors else "planner round brief invalid"


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


def _current_base_matches_champion() -> bool:
    if not best_source or not champion_source:
        return False
    return source_hash(best_source) == source_hash(champion_source)


def _benchmark_role() -> str:
    return "champion" if champion_report is not None else "baseline"


def _reference_role() -> str:
    if champion_report is None:
        return "baseline"
    return "champion" if _current_base_matches_champion() else "working_base"


def _session_scope_payload(*, factor_change_mode: str = "", iteration_lane: str = "research") -> dict[str, Any]:
    reference_code_hash = source_hash(best_source) if best_source else ""
    return {
        "score_regime": SCORE_REGIME,
        "reference_role": _reference_role() if best_report is not None else "",
        "reference_code_hash": reference_code_hash,
        "reference_stage_started_at": reference_stage_started_at,
        "reference_stage_iteration": int(reference_stage_iteration or 0),
        "iteration_lane": str(iteration_lane or "research").strip() or "research",
        "factor_change_mode": normalize_factor_change_mode(
            str(factor_change_mode).strip()
            or str(RUNTIME.base_factor_change_mode or "default").strip()
            or "default"
        ),
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
    *,
    factor_change_mode: str = "",
    iteration_lane: str = "research",
) -> bool:
    if not isinstance(state, dict) or not state:
        return False
    scope = _session_scope_payload(
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
    )
    if not scope["reference_code_hash"]:
        return False
    return all(str(state.get(key, "")).strip() == str(scope.get(key, "")).strip() for key in scope)


def _active_research_session_id(*, factor_change_mode: str = "", iteration_lane: str = "research") -> str:
    state = _load_research_session_state()
    if not _session_state_matches_current_stage(
        state,
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
    ):
        return ""
    return str(state.get("session_id", "")).strip()


def _store_research_session_metadata(
    *,
    session_id: str,
    workspace_root: Path,
    factor_change_mode: str = "",
    iteration_lane: str = "research",
) -> None:
    if not session_id:
        return
    previous = _load_research_session_state()
    now = datetime.now(UTC).isoformat()
    resolved_factor_mode = normalize_factor_change_mode(
        str(factor_change_mode).strip()
        or str(previous.get("factor_change_mode", "")).strip()
        or str(RUNTIME.base_factor_change_mode or "default").strip()
        or "default"
    )
    payload = {
        **_session_scope_payload(
            factor_change_mode=resolved_factor_mode,
            iteration_lane=iteration_lane,
        ),
        "session_id": session_id,
        "workspace_root": str(workspace_root),
        "factor_change_mode": resolved_factor_mode,
        "iteration_lane": str(iteration_lane or "research").strip() or "research",
        "created_at": str(previous.get("created_at", "")).strip() or now,
        "updated_at": now,
    }
    _persist_research_session_state(payload)


def _align_research_session_scope(
    *,
    force_reset: bool = False,
    factor_change_mode: str = "",
    iteration_lane: str = "research",
) -> None:
    state = _load_research_session_state()
    if force_reset:
        _clear_research_session_state(remove_workspace=True, reason="reference scope changed by explicit reset")
        return
    if state and not _session_state_matches_current_stage(
        state,
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
    ):
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
        current_score_regime=SCORE_REGIME,
        current_iteration=iteration_counter or (
            max((int(entry.get("iteration", 0) or 0) for entry in journal_entries), default=0) + 1
        ),
        active_stage_started_at=reference_stage_started_at,
        active_stage_iteration=reference_stage_iteration,
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
    factor_change_mode: str,
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
        build_strategy_agents_instructions(factor_change_mode=factor_change_mode),
    )

    _ensure_workspace_link(workspace_root / "src/backtest_macd_aggressive.py", RUNTIME.paths.backtest_file)
    if RUNTIME.paths.operator_focus_file.exists():
        _ensure_workspace_link(
            workspace_root / "config/research_v2_operator_focus.md",
            RUNTIME.paths.operator_focus_file,
        )
    if (REPO_ROOT / "data").exists():
        _ensure_workspace_link(workspace_root / "data", REPO_ROOT / "data")
    _ensure_workspace_link(workspace_root / "memory", RUNTIME.paths.memory_dir)
    memory_paths = {
        "history_package": RUNTIME.paths.memory_dir / "prompt/latest_history_package.md",
        "duplicate_watchlist": RUNTIME.paths.memory_dir / "wiki/duplicate_watchlist.md",
        "failure_wiki_md": RUNTIME.paths.memory_dir / "wiki/failure_wiki.md",
        "failure_wiki_json": RUNTIME.paths.memory_dir / "wiki/failure_wiki_index.json",
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
                "last_rejected_candidate": workspace_root / "wiki/last_rejected_candidate.py",
                "last_rejected_snapshot": workspace_root / "wiki/last_rejected_snapshot.md",
            }[key]
            _ensure_workspace_link(link_name, target_path)
    return workspace_root


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


def _effective_positive_delta_threshold() -> float:
    return max(0.01, float(RUNTIME.promotion_min_delta) * 0.5)


def _complexity_level_rank(level: str) -> int:
    return {
        "normal": 0,
        "warning_1": 1,
        "warning_2": 2,
        "hard_cap": 3,
    }.get(str(level or "normal").strip(), 0)


def _complexity_compaction_delta(*, base_source: str, candidate_source: str) -> dict[str, Any]:
    base_pressure = build_strategy_complexity_pressure(base_source)
    candidate_pressure = build_strategy_complexity_pressure(candidate_source)
    base_level = str(base_pressure.get("headroom_level", "normal")).strip() or "normal"
    candidate_level = str(candidate_pressure.get("headroom_level", "normal")).strip() or "normal"
    base_items = len(tuple(base_pressure.get("headroom_items", ()) or ()))
    candidate_items = len(tuple(candidate_pressure.get("headroom_items", ()) or ()))
    material = (
        _complexity_level_rank(candidate_level) < _complexity_level_rank(base_level)
        or candidate_items < base_items
    )
    return {
        "base_level": base_level,
        "candidate_level": candidate_level,
        "base_item_count": base_items,
        "candidate_item_count": candidate_items,
        "material": material,
        "summary": (
            f"headroom {base_level}->{candidate_level}, "
            f"紧张项 {base_items}->{candidate_items}"
        ),
    }


def _recent_complexity_event_count(entries: list[dict[str, Any]], *, lookback: int = COMPACTION_COMPLEXITY_LOOKBACK) -> int:
    current_entries = _current_stage_journal_entries(entries)
    hits = 0
    checked = 0
    for entry in reversed(current_entries):
        if checked >= max(1, lookback):
            break
        checked += 1
        haystack = " | ".join(
            str(entry.get(key, "")).lower()
            for key in (
                "decision_reason",
                "gate_reason",
                "eval_gate_reason",
                "runtime_failure_stage",
                "note",
            )
        )
        if "complexity" in haystack:
            hits += 1
    return hits


def _resolve_iteration_lane_state(
    journal_entries: list[dict[str, Any]],
    *,
    factor_mode_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not best_source:
        return {"lane": "research", "reason": "当前 working_base 尚未初始化，保持常规研究。"}

    pressure = build_strategy_complexity_pressure(best_source)
    headroom_level = str(pressure.get("headroom_level", "normal")).strip() or "normal"
    complexity_events = _recent_complexity_event_count(journal_entries)
    trailing_stalls = int((factor_mode_context or {}).get("trailing_stalls", 0) or 0)
    factor_admission_rounds = int((factor_mode_context or {}).get("factor_admission_rounds", 0) or 0)
    if headroom_level in {"warning_2", "hard_cap"} and (
        complexity_events > 0
        or trailing_stalls >= COMPACTION_TRIGGER_STALLS
        or factor_admission_rounds >= FACTOR_ADMISSION_MAX_BURST
    ):
        return {
            "lane": "compaction",
            "reason": (
                "本轮切到 working_base compaction lane: "
                f"当前基底复杂度 {headroom_level}，最近 {COMPACTION_COMPLEXITY_LOOKBACK} 轮出现 "
                f"{complexity_events} 次 complexity 相关失败，"
                f"stage stall={trailing_stalls}。目标是先删旧/并旧/降复杂度，"
                "允许在不刷新 champion 的前提下把压缩后的代码沉淀成新的 working_base。"
            ),
            "complexity_pressure": pressure,
        }

    return {
        "lane": "research",
        "reason": (
            "本轮保持常规研究 lane: "
            f"当前基底复杂度 {headroom_level}，最近 {COMPACTION_COMPLEXITY_LOOKBACK} 轮 "
            f"complexity 相关失败={complexity_events}。"
        ),
        "complexity_pressure": pressure,
    }


def _working_base_compaction_acceptance_decision(
    report: EvaluationReport,
    *,
    base_source: str,
    candidate_source: str,
) -> tuple[bool, str, dict[str, Any]]:
    if best_report is None:
        return False, "reference state is not initialized", {}

    compaction_delta = _complexity_compaction_delta(
        base_source=base_source,
        candidate_source=candidate_source,
    )
    if not compaction_delta["material"]:
        return False, f"压缩未带来足够复杂度改善({compaction_delta['summary']})", compaction_delta

    promotion_delta_vs_base = float(report.metrics.get("promotion_score", 0.0)) - float(
        best_report.metrics.get("promotion_score", 0.0)
    )
    quality_delta_vs_base = float(report.metrics.get("quality_score", 0.0)) - float(
        best_report.metrics.get("quality_score", 0.0)
    )
    validation_trend_delta = float(report.metrics.get("validation_trend_capture_score", 0.0)) - float(
        best_report.metrics.get("validation_trend_capture_score", 0.0)
    )
    validation_hit_rate_delta = float(report.metrics.get("validation_segment_hit_rate", 0.0)) - float(
        best_report.metrics.get("validation_segment_hit_rate", 0.0)
    )

    if promotion_delta_vs_base < -COMPACTION_PROMOTION_SLACK:
        return (
            False,
            f"压缩后 promotion 相对 working_base 回撤过大({promotion_delta_vs_base:.2f} < -{COMPACTION_PROMOTION_SLACK:.2f})",
            compaction_delta,
        )
    if quality_delta_vs_base < -COMPACTION_QUALITY_SLACK:
        return (
            False,
            f"压缩后 quality 相对 working_base 回撤过大({quality_delta_vs_base:.2f} < -{COMPACTION_QUALITY_SLACK:.2f})",
            compaction_delta,
        )
    if validation_trend_delta < -COMPACTION_VALIDATION_TREND_SLACK:
        return (
            False,
            f"压缩后 val 趋势捕获回撤过大({validation_trend_delta:.2f} < -{COMPACTION_VALIDATION_TREND_SLACK:.2f})",
            compaction_delta,
        )
    if validation_hit_rate_delta < -COMPACTION_VALIDATION_HIT_RATE_SLACK:
        return (
            False,
            f"压缩后 val 命中率回撤过大({validation_hit_rate_delta:.2f} < -{COMPACTION_VALIDATION_HIT_RATE_SLACK:.2f})",
            compaction_delta,
        )

    return (
        True,
        (
            "压缩沉淀通过("
            f"{compaction_delta['summary']}；"
            f"promotion_vs_base={promotion_delta_vs_base:.2f}；"
            f"quality_vs_base={quality_delta_vs_base:.2f})"
        ),
        compaction_delta,
    )


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


def _entry_has_effective_positive_delta(entry: dict[str, Any]) -> bool:
    outcome = str(entry.get("outcome", "")).strip()
    if outcome not in {"accepted", "rejected"}:
        return False
    try:
        promotion_delta = float(entry.get("promotion_delta", 0.0) or 0.0)
    except (TypeError, ValueError):
        promotion_delta = 0.0
    return promotion_delta > _effective_positive_delta_threshold()


def _entry_is_factor_stall(
    entry: dict[str, Any],
    *,
    basin_counts: dict[str, int] | None = None,
    direction_counts: dict[str, int] | None = None,
) -> bool:
    outcome = str(entry.get("outcome", "")).strip()
    if outcome in {"behavioral_noop", "exploration_blocked", "duplicate_skipped", "generation_invalid"}:
        return True
    if outcome == "runtime_failed":
        return _is_complexity_error_message(str(entry.get("decision_reason", "")).strip())
    if outcome not in {"accepted", "rejected", "early_rejected"}:
        return False
    try:
        promotion_delta = float(entry.get("promotion_delta", 0.0) or 0.0)
    except (TypeError, ValueError):
        promotion_delta = 0.0
    if abs(promotion_delta) <= 1e-9:
        return True
    basin_key = result_basin_key_for_entry(entry)
    if basin_key and basin_counts and basin_counts.get(basin_key, 0) >= 2:
        return True
    direction_key = _stage_direction_key_for_entry(entry)
    if direction_key and direction_counts and direction_counts.get(direction_key, 0) >= 3 and promotion_delta <= 0.0:
        return True
    return False


def _stage_direction_key_for_entry(entry: dict[str, Any]) -> str:
    cluster = str(entry.get("cluster_key", "")).strip()
    target = str(entry.get("target_family", "")).strip()
    families = entry.get("system_ordinary_region_families", []) or entry.get("system_region_families", []) or []
    normalized_families = sorted({str(item).strip() for item in families if str(item).strip()})
    if not cluster or not target or not normalized_families:
        return ""
    return json.dumps(
        {
            "cluster": cluster,
            "target": target,
            "families": normalized_families,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _factor_change_status_text(state: dict[str, Any]) -> str:
    trailing_stalls = int(state.get("trailing_stalls", 0) or 0)
    factor_admission_rounds = int(state.get("factor_admission_rounds", 0) or 0)
    level = str(state.get("guidance_level", "normal")).strip() or "normal"
    if level == "forced":
        return (
            f"当前 stage 已连续 {trailing_stalls} 轮 stall，系统本轮强制切到 factor_admission；"
            f"当前已使用 {factor_admission_rounds}/{FACTOR_ADMISSION_MAX_BURST} 轮。"
        )
    if level == "suggest":
        return (
            f"当前 stage 已连续 {trailing_stalls} 轮 stall，已到 factor_admission 强提醒区；"
            f"若继续无效，{FACTOR_ADMISSION_FORCE_STALLS} 轮会自动切入。当前已用 {factor_admission_rounds}/{FACTOR_ADMISSION_MAX_BURST} 轮。"
        )
    if level == "reminder":
        return (
            f"当前 stage 已连续 {trailing_stalls} 轮 stall，进入 factor_admission 提醒区；"
            "优先先做删旧、换机制层、换 choke point。"
        )
    if level == "exhausted":
        return (
            f"当前 stage 已连续 {trailing_stalls} 轮 stall，但 factor_admission 已用满 "
            f"{FACTOR_ADMISSION_MAX_BURST}/{FACTOR_ADMISSION_MAX_BURST} 轮；"
            "本轮回到默认模式，优先压缩和换机制。"
        )
    return "当前 stage 尚未进入 factor_admission 提醒区，保持默认删减/替换优先。"


def _resolve_iteration_factor_change_state(journal_entries: list[dict[str, Any]]) -> dict[str, Any]:
    base_mode = normalize_factor_change_mode(str(RUNTIME.base_factor_change_mode or "default").strip() or "default")
    stage_entries = _current_stage_journal_entries(journal_entries)
    stage_tail: list[dict[str, Any]] = []
    factor_admission_rounds = 0
    for entry in reversed(stage_entries):
        if _entry_has_effective_positive_delta(entry):
            break
        stage_tail.append(entry)
    for entry in stage_tail:
        if str(entry.get("factor_change_mode", "")).strip() == "factor_admission":
            factor_admission_rounds += 1

    basin_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    for entry in stage_tail:
        basin_key = result_basin_key_for_entry(entry)
        if basin_key:
            basin_counts[basin_key] = basin_counts.get(basin_key, 0) + 1
        direction_key = _stage_direction_key_for_entry(entry)
        if direction_key:
            direction_counts[direction_key] = direction_counts.get(direction_key, 0) + 1

    trailing_stalls = 0
    for entry in stage_tail:
        if _entry_is_factor_stall(entry, basin_counts=basin_counts, direction_counts=direction_counts):
            trailing_stalls += 1
            continue
        break

    guidance_level = "normal"
    mode = base_mode
    reason = f"当前stage未触发提醒，维持 {base_mode}"
    if trailing_stalls >= FACTOR_ADMISSION_FORCE_STALLS:
        if factor_admission_rounds < FACTOR_ADMISSION_MAX_BURST:
            guidance_level = "forced"
            mode = "factor_admission"
            reason = (
                f"当前stage已连续 {trailing_stalls} 轮 behavioral_noop/exploration_blocked/重复结果盆地/复杂度连撞，"
                f"达到强制放宽阈值，临时开启 factor_admission 第 {factor_admission_rounds + 1}/{FACTOR_ADMISSION_MAX_BURST} 轮"
            )
        else:
            guidance_level = "exhausted"
            reason = (
                f"当前stage已连续 {trailing_stalls} 轮 stall，但 factor_admission 已用满 "
                f"{FACTOR_ADMISSION_MAX_BURST}/{FACTOR_ADMISSION_MAX_BURST} 轮，维持 {base_mode}"
            )
    elif trailing_stalls >= FACTOR_ADMISSION_SUGGEST_STALLS:
        guidance_level = "suggest"
        reason = (
            f"当前stage已连续 {trailing_stalls} 轮 stall，进入 factor_admission 强提醒区；"
            f"若达到 {FACTOR_ADMISSION_FORCE_STALLS} 轮仍无改善，将自动切入 factor_admission。"
        )
    elif trailing_stalls >= FACTOR_ADMISSION_REMINDER_STALLS:
        guidance_level = "reminder"
        reason = (
            f"当前stage已连续 {trailing_stalls} 轮 stall，进入 factor_admission 提醒区；"
            "本轮先优先删旧、换机制层或换 choke point。"
        )

    return {
        "mode": mode,
        "reason": reason,
        "guidance_level": guidance_level,
        "trailing_stalls": trailing_stalls,
        "factor_admission_rounds": factor_admission_rounds,
    }


def _resolve_iteration_factor_change_mode(journal_entries: list[dict[str, Any]]) -> tuple[str, str]:
    state = _resolve_iteration_factor_change_state(journal_entries)
    return str(state.get("mode", "default")).strip() or "default", str(state.get("reason", "")).strip()


def _load_saved_reference_state() -> dict[str, Any]:
    if not RUNTIME.paths.best_state_file.exists():
        return {}
    try:
        payload = json.loads(RUNTIME.paths.best_state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
    _refresh_prompt_memory_snapshots()


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


def _saved_report_payload(
    source: str,
    report: EvaluationReport,
    *,
    shadow_test_metrics: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "code_hash": source_hash(source),
        "metrics": report.metrics,
        "gate_passed": report.gate_passed,
        "gate_reason": report.gate_reason,
        "shadow_test_metrics": shadow_test_metrics or {},
    }


def _report_from_saved_payload(payload: dict[str, Any]) -> EvaluationReport | None:
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


def _reference_manifest_payload(
    source: str,
    report: EvaluationReport,
    *,
    shadow_test_metrics: dict[str, float] | None = None,
    stage_started_at: str = "",
    stage_iteration: int = 0,
) -> dict[str, Any]:
    working_base_payload = _saved_report_payload(
        source,
        report,
        shadow_test_metrics=shadow_test_metrics,
    )
    champion_payload = None
    if champion_report is not None and champion_source:
        champion_payload = _saved_report_payload(
            champion_source,
            champion_report,
            shadow_test_metrics=champion_shadow_test_metrics,
        )
    return {
        "updated_at": datetime.now(UTC).isoformat(),
        "score_regime": SCORE_REGIME,
        "reference_role": _reference_role(),
        "benchmark_role": _benchmark_role(),
        "reference_stage_started_at": stage_started_at,
        "reference_stage_iteration": stage_iteration,
        "code_hash": working_base_payload["code_hash"],
        "reference": working_base_payload,
        "working_base": working_base_payload,
        "champion": champion_payload,
        # Backward-compatible top-level fields for existing readers.
        "metrics": working_base_payload["metrics"],
        "gate_passed": working_base_payload["gate_passed"],
        "gate_reason": working_base_payload["gate_reason"],
        "shadow_test_metrics": working_base_payload["shadow_test_metrics"],
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
        "图表：每张图蓝线=策略累计增长，橙线=BTC累计增长；左轴直接显示账户价值，右轴直接显示BTC价格；若底部还有第二张图，则为test期间同口径对比。"
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
            "funnel": item["result"].get("strategy_funnel", {}) or {},
            "filled_side_entries": item["result"].get("filled_side_entries", {}) or {},
        }
        for item in results
    ]


def _behavior_profile_changed(
    base_profile: list[dict[str, Any]],
    candidate_profile: list[dict[str, Any]],
) -> bool:
    base_fingerprints = [(item.get("window"), item.get("fingerprint")) for item in base_profile]
    candidate_fingerprints = [(item.get("window"), item.get("fingerprint")) for item in candidate_profile]
    return base_fingerprints != candidate_fingerprints


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
        editable_regions=EDITABLE_REGIONS,
    )
    cluster_key = (
        str(candidate_signature.get("cluster_key", "")).strip()
        or cluster_key_for_components(
            candidate.closest_failed_cluster,
            candidate.change_tags,
        )
        or str(candidate.closest_failed_cluster).strip()
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
        editable_regions=EDITABLE_REGIONS,
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
        "- `wiki/duplicate_watchlist.md` / `wiki/failure_wiki.md` / `wiki/latest_history_package.md` 是高优先级参考，但读不到它们不是合法 no-edit 理由。",
        "- 即使辅助记忆文件暂不可用，也必须继续以 `src/strategy_macd_aggressive.py` 为事实源直接改代码。",
        "- 禁止继续提交 `blocked` / `no_edit` / `no_change` / “未执行代码改动” 这类占位答案。",
        "- 下一版提交前自检：至少一个可编辑区域真的发生变化，且文本摘要只描述你已经实际落到代码里的改动。",
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
    "hypothesis",
    "change_plan",
    "change_tags",
    "expected_effects",
    "closest_failed_cluster",
    "novelty_proof",
    "core_factors",
)
MODEL_RESPONSE_FIELD_PATTERN = re.compile(
    r"^(candidate_id|hypothesis|change_plan|change_tags|expected_effects|closest_failed_cluster|novelty_proof|core_factors)\s*:\s*(.*)$",
    re.IGNORECASE,
)
EDIT_COMPLETION_TOKEN = "EDIT_DONE"
NO_EDIT_ERROR_MESSAGE = "candidate missing actual changed regions"


def _is_complexity_error_message(message: str) -> bool:
    normalized = str(message).strip().lower()
    return "complexity budget exceeded" in normalized or "complexity growth too large" in normalized


def _is_no_edit_error_message(message: str) -> bool:
    return NO_EDIT_ERROR_MESSAGE in str(message or "").strip().lower()


def _response_field_lines(raw_text: str) -> dict[str, list[str]]:
    field_lines: dict[str, list[str]] = {}
    current_field = ""
    for raw_line in str(raw_text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = MODEL_RESPONSE_FIELD_PATTERN.match(stripped)
        if match:
            current_field = match.group(1).lower()
            value = match.group(2).strip()
            field_lines.setdefault(current_field, [])
            if value:
                field_lines[current_field].append(value)
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

    field_lines = _response_field_lines(text)
    if not field_lines:
        return {
            "__raw_text__": text,
            "__format_error__": "missing_field_contract",
            "candidate_id": "",
            "hypothesis": "",
            "change_plan": "",
            "change_tags": [],
            "expected_effects": [],
            "closest_failed_cluster": "",
            "novelty_proof": "",
            "core_factors": [],
        }

    return {
        "__raw_text__": text,
        "candidate_id": _collapse_field_text(field_lines.get("candidate_id", [])),
        "hypothesis": _collapse_field_text(field_lines.get("hypothesis", [])),
        "change_plan": _collapse_field_text(field_lines.get("change_plan", [])),
        "change_tags": list(_parse_change_tags(field_lines.get("change_tags", []))),
        "expected_effects": list(_parse_expected_effects(field_lines.get("expected_effects", []))),
        "closest_failed_cluster": _collapse_field_text(field_lines.get("closest_failed_cluster", [])),
        "novelty_proof": _collapse_field_text(field_lines.get("novelty_proof", [])),
        "core_factors": [
            {
                "name": factor.name,
                "thesis": factor.thesis,
                "current_signal": factor.current_signal,
            }
            for factor in _parse_core_factors_field(field_lines.get("core_factors", []))
        ],
    }


def _round_brief_missing_fields(payload: dict[str, Any]) -> tuple[str, ...]:
    missing: list[str] = []
    if str(payload.get("__format_error__", "")).strip() == "missing_field_contract":
        return PLANNER_BRIEF_REQUIRED_FIELDS

    hypothesis = str(payload.get("hypothesis", "")).strip()
    change_plan = str(payload.get("change_plan", "")).strip()
    novelty_proof = str(payload.get("novelty_proof", "")).strip()
    change_tags = tuple(str(item).strip() for item in payload.get("change_tags", []) if str(item).strip())

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
    if not missing_fields:
        return
    raise StrategySourceError(
        "planner round brief missing required fields: "
        + ", ".join(missing_fields)
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
        EDITABLE_REGIONS,
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
        closest_failed_cluster=(
            round_brief.closest_failed_cluster
            if round_brief and round_brief.closest_failed_cluster
            else "no_edit"
        ),
        novelty_proof=novelty_proof,
        change_tags=change_tags,
        edited_regions=tuple(),
        expected_effects=expected_effects,
        core_factors=round_brief.core_factors if round_brief else tuple(),
        strategy_code=strategy_code,
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
    hypothesis = str(payload.get("hypothesis", "")).strip()
    change_plan = str(payload.get("change_plan", "")).strip()
    novelty_proof = str(payload.get("novelty_proof", "")).strip()
    closest_failed_cluster = str(payload.get("closest_failed_cluster", "")).strip()
    candidate_id = str(payload.get("candidate_id", "")).strip() or _auto_candidate_id(
        change_tags=change_tags,
        edited_regions=tuple(),
    )
    if not closest_failed_cluster:
        closest_failed_cluster = cluster_key_for_components("", change_tags) or "unknown_cluster"
    return StrategyRoundBrief(
        candidate_id=candidate_id,
        hypothesis=hypothesis,
        change_plan=change_plan,
        closest_failed_cluster=closest_failed_cluster,
        novelty_proof=novelty_proof,
        change_tags=change_tags,
        expected_effects=tuple(str(item).strip() for item in payload.get("expected_effects", []) if str(item).strip()),
        core_factors=_core_factors_from_payload(payload),
    )


def _round_brief_from_candidate(candidate: StrategyCandidate) -> StrategyRoundBrief:
    return StrategyRoundBrief(
        candidate_id=candidate.candidate_id,
        hypothesis=candidate.hypothesis,
        change_plan=candidate.change_plan,
        closest_failed_cluster=candidate.closest_failed_cluster,
        novelty_proof=candidate.novelty_proof,
        change_tags=candidate.change_tags,
        expected_effects=candidate.expected_effects,
        core_factors=candidate.core_factors,
    )


def _round_brief_task_summary(round_brief: StrategyRoundBrief) -> str:
    lines = [
        f"- candidate_id: {round_brief.candidate_id or '-'}",
        f"- hypothesis: {round_brief.hypothesis or '-'}",
        f"- change_plan: {round_brief.change_plan or '-'}",
        f"- change_tags: {', '.join(round_brief.change_tags) or '-'}",
        f"- expected_effects: {'；'.join(round_brief.expected_effects) or '-'}",
        f"- closest_failed_cluster: {round_brief.closest_failed_cluster or '-'}",
        f"- novelty_proof: {round_brief.novelty_proof or '-'}",
    ]
    return "\n".join(lines)


def _rebase_candidate_metadata_to_final_code(
    *,
    candidate: StrategyCandidate,
    round_brief: StrategyRoundBrief,
    base_source: str,
    workspace_root: Path,
    factor_change_mode: str,
    context_label: str,
) -> StrategyCandidate:
    diff_summary = tuple(build_diff_summary(base_source, candidate.strategy_code, limit=18))
    region_families = tuple(sorted(region_families_for_regions(candidate.edited_regions)))
    prompt = build_strategy_candidate_summary_prompt(
        candidate_id=candidate.candidate_id,
        hypothesis=round_brief.hypothesis,
        change_plan=round_brief.change_plan,
        change_tags=round_brief.change_tags,
        expected_effects=round_brief.expected_effects,
        closest_failed_cluster=round_brief.closest_failed_cluster,
        novelty_proof=round_brief.novelty_proof,
        edited_regions=candidate.edited_regions,
        region_families=region_families,
        diff_summary=diff_summary,
    )
    try:
        final_brief = _request_validated_round_brief(
            base_source=base_source,
            prompt=prompt,
            system_prompt=build_strategy_summary_worker_system_prompt(
                factor_change_mode=factor_change_mode,
            ),
            phase="model_summary_worker",
            workspace_root=workspace_root,
            factor_change_mode=factor_change_mode,
            iteration_lane="research",
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
        closest_failed_cluster=final_brief.closest_failed_cluster,
        novelty_proof=final_brief.novelty_proof,
        change_tags=final_brief.change_tags,
        edited_regions=candidate.edited_regions,
        expected_effects=final_brief.expected_effects,
        core_factors=final_brief.core_factors,
        strategy_code=candidate.strategy_code,
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
        closest_failed_cluster=round_brief.closest_failed_cluster or "unknown_cluster",
        novelty_proof=round_brief.novelty_proof or "源码校验失败，等待同轮修复。",
        change_tags=round_brief.change_tags,
        edited_regions=tuple(),
        expected_effects=round_brief.expected_effects,
        core_factors=round_brief.core_factors,
        strategy_code=strategy_code,
    )


def _candidate_from_round_brief(
    round_brief: StrategyRoundBrief,
    *,
    workspace_strategy_file: Path,
    base_source: str,
    factor_change_mode: str = "default",
) -> StrategyCandidate:
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
        factor_change_mode=factor_change_mode,
    )
    validate_editable_region_boundaries(base_source, strategy_code, EDITABLE_REGIONS)
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
        closest_failed_cluster=round_brief.closest_failed_cluster or "unknown_cluster",
        novelty_proof=round_brief.novelty_proof or "未提供 novelty_proof；系统将以真实源码 diff 判定本轮改动。",
        change_tags=change_tags,
        edited_regions=actual_changed_regions,
        expected_effects=round_brief.expected_effects,
        core_factors=round_brief.core_factors,
        strategy_code=strategy_code,
    )
    if not candidate.edited_regions:
        raise StrategySourceError("candidate missing actual changed regions")
    if len(candidate.edited_regions) > len(EDITABLE_REGIONS):
        raise StrategySourceError("candidate edited_regions exceeds editable region count")
    if len(set(candidate.edited_regions)) != len(candidate.edited_regions):
        raise StrategySourceError("candidate edited_regions contains duplicates")
    return candidate


def _candidate_from_payload(
    payload: dict[str, Any],
    *,
    workspace_strategy_file: Path,
    base_source: str,
    factor_change_mode: str = "default",
) -> StrategyCandidate:
    return _candidate_from_round_brief(
        _round_brief_from_payload(payload),
        workspace_strategy_file=workspace_strategy_file,
        base_source=base_source,
        factor_change_mode=factor_change_mode,
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
    session_factor_change_mode: str = "",
    session_iteration_lane: str = "research",
) -> str:
    session_id = (
        _active_research_session_id(
            factor_change_mode=session_factor_change_mode,
            iteration_lane=session_iteration_lane,
        )
        if use_persistent_session
        else ""
    )

    def _invoke(active_session_id: str | None, metadata: dict[str, Any]) -> str:
        with _temporary_cwd(workspace_root):
            return generate_text_response(
                prompt=prompt,
                system_prompt=system_prompt,
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
    try:
        raw_text = _invoke(session_id or None, response_metadata)
    except StrategyGenerationSessionError as exc:
        if not session_id or not use_persistent_session:
            raise
        log_info(f"Codex session 无法恢复，改为新 session 重试: {exc}")
        _clear_research_session_state(remove_workspace=False, reason="invalid provider session")
        response_metadata = {}
        raw_text = _invoke(None, response_metadata)

    resolved_session_id = (
        str(response_metadata.get("session_id", "")).strip()
        or str(response_metadata.get("thread_id", "")).strip()
        or session_id
    )
    if use_persistent_session and resolved_session_id:
        _store_research_session_metadata(
            session_id=resolved_session_id,
            workspace_root=workspace_root,
            factor_change_mode=session_factor_change_mode,
            iteration_lane=session_iteration_lane,
        )
    elapsed_seconds = round(max(0.0, time.monotonic() - started_at), 3)
    _append_model_call_telemetry(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "iteration": iteration_counter,
            "phase": phase,
            "session_kind": session_kind,
            "persistent_session": bool(use_persistent_session),
            "requested_resume": bool(session_id),
            "resolved_session_id": resolved_session_id if use_persistent_session else "",
            "resumed": bool(response_metadata.get("resumed", False)),
            "prompt_chars": len(prompt),
            "system_prompt_chars": len(system_prompt),
            "estimated_prompt_tokens": _estimate_prompt_tokens(prompt, system_prompt),
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
    session_factor_change_mode: str = "",
    session_iteration_lane: str = "research",
) -> dict[str, Any]:
    raw_text = _run_model_text_request(
        prompt=prompt,
        system_prompt=system_prompt,
        phase=phase,
        workspace_root=workspace_root,
        repair_attempt=repair_attempt,
        session_kind=session_kind,
        use_persistent_session=use_persistent_session,
        session_factor_change_mode=session_factor_change_mode,
        session_iteration_lane=session_iteration_lane,
    )
    return _parse_model_candidate_payload(raw_text)


def _candidate_from_invalid_round_brief_payload(
    *,
    payload: dict[str, Any],
    base_source: str,
) -> StrategyCandidate:
    change_tags = tuple(str(item).strip() for item in payload.get("change_tags", []) if str(item).strip()) or ("invalid_brief",)
    closest_failed_cluster = (
        str(payload.get("closest_failed_cluster", "")).strip()
        or cluster_key_for_components("", change_tags)
        or "invalid_brief"
    )
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
    blocked_cluster = (
        str(payload.get("closest_failed_cluster", "")).strip()
        or cluster_key_for_components("", tuple(str(item).strip() for item in payload.get("change_tags", []) if str(item).strip()))
        or "-"
    )
    raw_excerpt = _raw_response_excerpt(payload)
    error_text = errors[-1] if errors else "planner round brief invalid"
    feedback_lines = [
        "planner 输出的 round brief 不合法，当前轮次没有拿到可执行的策略摘要。",
        f"- 无效原因: {error_text}",
        f"- 缺失或无效字段: {', '.join(missing_fields) or '-'}",
        f"- 原始回复摘录: {raw_excerpt or '-'}",
        "- 你的任务不是写随笔，而是返回可执行的 round brief。",
        "- 至少保证 `hypothesis`、`change_plan`、`novelty_proof`、`change_tags` 非空。",
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
    factor_change_mode: str,
    iteration_lane: str,
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
        session_factor_change_mode=factor_change_mode,
        session_iteration_lane=iteration_lane,
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
                session_factor_change_mode=factor_change_mode,
                session_iteration_lane=iteration_lane,
            )

    raise StrategySourceError("planner round brief validation loop exhausted")


def _build_model_round_brief(
    base_source: str,
    journal_entries: list[dict[str, Any]],
    *,
    workspace_root: Path,
    factor_change_mode: str,
    iteration_lane: str,
    iteration_lane_reason: str,
) -> StrategyRoundBrief:
    report = best_report
    if report is None:
        raise StrategySourceError("reference report is not initialized")
    benchmark_report = _reference_benchmark_report()
    if benchmark_report is None:
        raise StrategySourceError("reference benchmark is not initialized")

    session_mode = (
        "resume"
        if _active_research_session_id(
            factor_change_mode=factor_change_mode,
            iteration_lane=iteration_lane,
        )
        else "bootstrap"
    )
    factor_mode_state = _resolve_iteration_factor_change_state(journal_entries)
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
            reference_metrics=benchmark_report.metrics,
            memory_root=RUNTIME.paths.memory_dir,
        ),
        previous_best_score=benchmark_report.metrics["promotion_score"],
        reference_metrics=benchmark_report.metrics,
        benchmark_label=_benchmark_role(),
        current_base_role=_reference_role(),
        score_regime=SCORE_REGIME,
        promotion_min_delta=RUNTIME.promotion_min_delta,
        factor_change_mode=factor_change_mode,
        factor_mode_status_text=_factor_change_status_text(factor_mode_state),
        iteration_lane=iteration_lane,
        iteration_lane_status_text=iteration_lane_reason,
        current_complexity_headroom_text=format_strategy_complexity_headroom(base_source),
        session_mode=session_mode,
        operator_focus_text=_load_operator_focus_text(),
        operator_focus_path="config/research_v2_operator_focus.md",
    )
    return _request_validated_round_brief(
        base_source=base_source,
        prompt=prompt,
        system_prompt=build_strategy_system_prompt(
            factor_change_mode=factor_change_mode,
        ),
        phase="model_planner",
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
        retry_phase="model_planner_brief_repair",
    )


def _run_edit_worker(
    *,
    base_source: str,
    round_brief: StrategyRoundBrief,
    workspace_root: Path,
    factor_change_mode: str,
    iteration_lane: str,
    iteration_lane_reason: str,
    phase: str,
    repair_attempt: int | None = None,
) -> str:
    prompt = build_strategy_edit_worker_prompt(
        candidate_id=round_brief.candidate_id,
        hypothesis=round_brief.hypothesis,
        change_plan=round_brief.change_plan,
        change_tags=round_brief.change_tags,
        expected_effects=round_brief.expected_effects,
        closest_failed_cluster=round_brief.closest_failed_cluster,
        novelty_proof=round_brief.novelty_proof,
        current_complexity_headroom_text=format_strategy_complexity_headroom(base_source),
        iteration_lane=iteration_lane,
        iteration_lane_status_text=iteration_lane_reason,
    )
    return _run_model_text_request(
        prompt=prompt,
        system_prompt=build_strategy_worker_system_prompt(
            factor_change_mode=factor_change_mode,
            worker_kind="edit_worker",
        ),
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
    factor_change_mode: str,
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
        system_prompt=build_strategy_worker_system_prompt(
            factor_change_mode=factor_change_mode,
            worker_kind="repair_worker",
        ),
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
    factor_change_mode: str,
) -> str:
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
    return _run_model_text_request(
        prompt=prompt,
        system_prompt=build_strategy_worker_system_prompt(
            factor_change_mode=factor_change_mode,
            worker_kind="repair_worker",
        ),
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
    factor_change_mode: str,
    iteration_lane: str,
    iteration_lane_reason: str,
) -> StrategyRoundBrief:
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
    return _request_validated_round_brief(
        base_source=base_source,
        prompt=prompt,
        system_prompt=build_strategy_system_prompt(
            factor_change_mode=factor_change_mode,
        ),
        phase="model_regenerate",
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
        retry_phase="model_regenerate_brief_repair",
    )


def _candidate_from_round_brief_with_validation_repair(
    *,
    round_brief: StrategyRoundBrief,
    base_source: str,
    workspace_root: Path,
    factor_change_mode: str,
    context_label: str,
) -> StrategyCandidate:
    workspace_strategy_file = workspace_root / MODEL_WORKSPACE_STRATEGY_PATH
    try:
        candidate = _candidate_from_round_brief(
            round_brief,
            workspace_strategy_file=workspace_strategy_file,
            base_source=base_source,
            factor_change_mode=factor_change_mode,
        )
        return _rebase_candidate_metadata_to_final_code(
            candidate=candidate,
            round_brief=round_brief,
            base_source=base_source,
            workspace_root=workspace_root,
            factor_change_mode=factor_change_mode,
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
                factor_change_mode=factor_change_mode,
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
                    factor_change_mode=factor_change_mode,
                )
                return _rebase_candidate_metadata_to_final_code(
                    candidate=candidate,
                    round_brief=round_brief,
                    base_source=base_source,
                    workspace_root=workspace_root,
                    factor_change_mode=factor_change_mode,
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
    factor_change_mode: str,
    iteration_lane: str,
    iteration_lane_reason: str,
) -> StrategyCandidate:
    workspace_strategy_file = workspace_root / MODEL_WORKSPACE_STRATEGY_PATH
    round_brief = _build_model_round_brief(
        base_source,
        journal_entries,
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
        iteration_lane_reason=iteration_lane_reason,
    )
    last_response_text = _run_edit_worker(
        base_source=base_source,
        round_brief=round_brief,
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
        iteration_lane_reason=iteration_lane_reason,
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
                factor_change_mode=factor_change_mode,
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
        factor_change_mode=factor_change_mode,
        context_label="初始候选",
    )


def _regenerate_model_candidate(
    *,
    base_source: str,
    failed_candidate: StrategyCandidate,
    block_info: dict[str, Any],
    regeneration_attempt: int,
    workspace_root: Path,
    factor_change_mode: str,
    iteration_lane: str,
    iteration_lane_reason: str,
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
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
        iteration_lane_reason=iteration_lane_reason,
    )
    _run_edit_worker(
        base_source=base_source,
        round_brief=round_brief,
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
        iteration_lane=iteration_lane,
        iteration_lane_reason=iteration_lane_reason,
        phase="model_regenerate_edit_worker",
        repair_attempt=regeneration_attempt,
    )
    return _candidate_from_round_brief_with_validation_repair(
        round_brief=round_brief,
        base_source=base_source,
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
        context_label="候选重生",
    )


def _repair_model_candidate(
    *,
    base_source: str,
    failed_candidate: StrategyCandidate,
    error_message: str,
    repair_attempt: int,
    workspace_root: Path,
    factor_change_mode: str,
) -> StrategyCandidate:
    _repair_model_candidate_source(
        failed_candidate=failed_candidate,
        error_message=error_message,
        repair_attempt=repair_attempt,
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
    )
    return _candidate_from_round_brief_with_validation_repair(
        round_brief=_round_brief_from_candidate(failed_candidate),
        base_source=base_source,
        workspace_root=workspace_root,
        factor_change_mode=factor_change_mode,
        context_label="运行修复",
    )


def build_strategy_candidate(
    base_source: str,
    journal_entries: list[dict[str, Any]],
    *,
    workspace_root: Path,
    factor_change_mode: str,
    iteration_lane: str,
    iteration_lane_reason: str,
) -> StrategyCandidate:
    try:
        return _build_model_candidate(
            base_source,
            journal_entries,
            workspace_root=workspace_root,
            factor_change_mode=factor_change_mode,
            iteration_lane=iteration_lane,
            iteration_lane_reason=iteration_lane_reason,
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
    if champion_source and champion_report is not None:
        RUNTIME.paths.champion_strategy_file.parent.mkdir(parents=True, exist_ok=True)
        write_strategy_source(RUNTIME.paths.champion_strategy_file, champion_source)
    elif RUNTIME.paths.champion_strategy_file.exists():
        RUNTIME.paths.champion_strategy_file.unlink()


def initialize_best_state(force_rebuild: bool = False) -> None:
    global best_source, best_report, champion_source, champion_report
    global champion_shadow_test_metrics, reference_stage_started_at, reference_stage_iteration

    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    saved_state = _load_saved_reference_state()
    saved_regime = str(saved_state.get("score_regime", "")).strip()
    saved_reference = saved_state.get("working_base") or saved_state.get("reference")
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
            champion_source = ""
            champion_report = None
            champion_shadow_test_metrics = None

            saved_champion = saved_state.get("champion")
            if (
                isinstance(saved_champion, dict)
                and RUNTIME.paths.champion_strategy_file.exists()
            ):
                candidate_champion_source = load_strategy_source(RUNTIME.paths.champion_strategy_file)
                try:
                    validate_strategy_source(candidate_champion_source)
                except StrategySourceError as exc:
                    log_info(f"已保存 champion 无效，忽略该快照并继续使用 working_base: {exc}")
                else:
                    reconstructed = _report_from_saved_payload(saved_champion)
                    if reconstructed is not None:
                        champion_source = candidate_champion_source
                        champion_report = reconstructed
                        champion_shadow_test_metrics = dict(
                            saved_champion.get("shadow_test_metrics", {}) or {}
                        )

            if (
                champion_report is None
                and best_report.gate_passed
                and str(saved_state.get("reference_role", "")).strip() == "champion"
            ):
                champion_source = best_source
                champion_report = best_report
                champion_shadow_test_metrics = dict(saved_state.get("shadow_test_metrics", {}) or {})

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
            _align_research_session_scope(
                force_reset=False,
                factor_change_mode=RUNTIME.base_factor_change_mode,
                iteration_lane="research",
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
                    factor_change_mode=RUNTIME.base_factor_change_mode,
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
    champion_shadow_test_metrics = dict(saved_state.get("shadow_test_metrics", {}) or {}) if champion_report else None
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
    _align_research_session_scope(
        force_reset=force_rebuild,
        factor_change_mode=RUNTIME.base_factor_change_mode,
        iteration_lane="research",
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
            factor_change_mode=RUNTIME.base_factor_change_mode,
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
    factor_change_mode: str,
    factor_mode_context: dict[str, Any] | None = None,
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
        "cluster_key": str(candidate_signature.get("cluster_key", "")).strip()
        or cluster_key_for_components(
            candidate.closest_failed_cluster,
            candidate.change_tags,
        ),
        "quality_score": quality_score,
        "promotion_score": promotion_score,
        "promotion_delta": promotion_score - base_promotion if promotion_score is not None else None,
        "factor_change_mode": factor_change_mode,
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
        "factor_change_reason": str((factor_mode_context or {}).get("reason", "")).strip(),
        "factor_change_guidance_level": str((factor_mode_context or {}).get("guidance_level", "normal")).strip()
        or "normal",
        "factor_change_trailing_stalls": int((factor_mode_context or {}).get("trailing_stalls", 0) or 0),
        "factor_change_stage_factor_admission_rounds": int(
            (factor_mode_context or {}).get("factor_admission_rounds", 0) or 0
        ),
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
    factor_change_mode: str,
    factor_mode_context: dict[str, Any] | None = None,
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
            factor_change_mode=factor_change_mode,
            factor_mode_context=factor_mode_context,
            outcome="duplicate_skipped",
            stop_stage=stop_stage,
            gate_reason=gate_reason,
            note=note,
        )
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _record_generation_invalid(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    factor_change_mode: str,
    factor_mode_context: dict[str, Any] | None = None,
    block_info: dict[str, Any],
    note: str,
) -> None:
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            factor_change_mode=factor_change_mode,
            factor_mode_context=factor_mode_context,
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
        )
    )
    if maybe_compact(RUNTIME.paths.journal_file):
        log_info("研究日志已压缩")


def _record_exploration_block(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    factor_change_mode: str,
    factor_mode_context: dict[str, Any] | None = None,
    block_info: dict[str, Any],
) -> None:
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            factor_change_mode=factor_change_mode,
            factor_mode_context=factor_mode_context,
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
    factor_change_mode: str,
    factor_mode_context: dict[str, Any] | None = None,
    behavior_diff: dict[str, Any],
) -> None:
    _append_research_journal_entry(
        _build_journal_entry(
            iteration_id=iteration_id,
            candidate=candidate,
            base_source=base_source,
            candidate_report=None,
            factor_change_mode=factor_change_mode,
            factor_mode_context=factor_mode_context,
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


def _record_runtime_failure(
    *,
    iteration_id: int,
    candidate: StrategyCandidate,
    base_source: str,
    factor_change_mode: str,
    factor_mode_context: dict[str, Any] | None = None,
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
            factor_change_mode=factor_change_mode,
            factor_mode_context=factor_mode_context,
            outcome="runtime_failed",
            stop_stage=stop_stage,
            gate_reason="运行失败",
            note="；".join(errors),
            extra_fields=extra_fields,
        )
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
    factor_change_mode: str,
    iteration_lane: str,
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
            compaction_delta = _complexity_compaction_delta(
                base_source=base_source,
                candidate_source=current.strategy_code,
            )
            allow_behavioral_noop = iteration_lane == "compaction" and bool(compaction_delta["material"])
            if not behavior_diff["changed"] and not allow_behavioral_noop:
                raise CandidateBehavioralNoop(current, behavior_diff)
            if not behavior_diff["changed"] and allow_behavioral_noop:
                log_info(
                    f"第 {iteration_counter} 轮 compaction 候选 smoke 未变，但允许继续完整评估: "
                    f"{compaction_delta['summary']}"
                )
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
                factor_change_mode=factor_change_mode,
            )
    raise CandidateRepairExhausted(current, errors)


def run_iteration(iteration_id: int, use_model_optimization: bool = True) -> str:
    global best_source, best_report, champion_source, champion_report
    global champion_shadow_test_metrics, reference_stage_started_at, reference_stage_iteration

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

    journal_entries = load_journal_entries(RUNTIME.paths.journal_file)
    factor_mode_context = _resolve_iteration_factor_change_state(journal_entries)
    current_factor_change_mode = str(factor_mode_context.get("mode", "default")).strip() or "default"
    factor_change_reason = str(factor_mode_context.get("reason", "")).strip()
    iteration_lane_state = _resolve_iteration_lane_state(
        journal_entries,
        factor_mode_context=factor_mode_context,
    )
    current_iteration_lane = str(iteration_lane_state.get("lane", "research")).strip() or "research"
    iteration_lane_reason = str(iteration_lane_state.get("reason", "")).strip()
    log_info(f"第 {iteration_id} 轮因子模式: {current_factor_change_mode} | {factor_change_reason}")
    log_info(f"第 {iteration_id} 轮执行车道: {current_iteration_lane} | {iteration_lane_reason}")
    write_heartbeat(
        "iteration_preparing",
        message=f"iteration {iteration_id} preparing candidate",
        factor_change_mode=current_factor_change_mode,
        factor_change_reason=factor_change_reason,
        iteration_lane=current_iteration_lane,
        iteration_lane_reason=iteration_lane_reason,
    )
    _align_research_session_scope(
        force_reset=False,
        factor_change_mode=current_factor_change_mode,
        iteration_lane=current_iteration_lane,
    )
    workspace_root = _prepare_agent_workspace(
        base_source=best_source,
        factor_change_mode=current_factor_change_mode,
        reset_strategy=True,
    )

    try:
        candidate = build_strategy_candidate(
            best_source,
            journal_entries,
            workspace_root=workspace_root,
            factor_change_mode=current_factor_change_mode,
            iteration_lane=current_iteration_lane,
            iteration_lane_reason=iteration_lane_reason,
        )
    except PlannerBriefInvalid as exc:
        write_strategy_source(RUNTIME.paths.strategy_file, best_source)
        reload_strategy_module()
        _record_generation_invalid(
            iteration_id=iteration_id,
            candidate=exc.candidate,
            base_source=best_source,
            factor_change_mode=current_factor_change_mode,
            factor_mode_context=factor_mode_context,
            block_info=exc.block_info,
            note=(
                "planner 未按约定返回合法 round brief；系统已在同一 session 内补正一次，"
                "仍未拿到可执行摘要，因此按 generation_invalid 记账。"
            ),
        )
        log_info(f"第 {iteration_id} 轮 planner brief 作废: {exc}")
        write_heartbeat(
            "iteration_generation_invalid",
            message=f"iteration {iteration_id} planner brief invalid",
            block_kind=str(exc.block_info.get('block_kind', '')).strip(),
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
            factor_change_mode=current_factor_change_mode,
            factor_mode_context=factor_mode_context,
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
                    factor_change_mode=current_factor_change_mode,
                    factor_mode_context=factor_mode_context,
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
                factor_change_mode=current_factor_change_mode,
                iteration_lane=current_iteration_lane,
                iteration_lane_reason=iteration_lane_reason,
            )
            continue

        candidate_hash = source_hash(candidate.strategy_code)

        if candidate_hash == source_hash(best_source):
            _record_duplicate_skip(
                iteration_id=iteration_id,
                candidate=candidate,
                base_source=best_source,
                factor_change_mode=current_factor_change_mode,
                factor_mode_context=factor_mode_context,
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
                factor_change_mode=current_factor_change_mode,
                factor_mode_context=factor_mode_context,
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
                factor_change_mode=current_factor_change_mode,
                factor_mode_context=factor_mode_context,
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
            editable_regions=EDITABLE_REGIONS,
        )
        if block_info is None:
            block_info = evaluate_candidate_exploration_guard(
                candidate,
                journal_entries,
                journal_path=RUNTIME.paths.journal_file,
                score_regime=SCORE_REGIME,
                current_iteration=iteration_id,
                base_source=best_source,
                editable_regions=EDITABLE_REGIONS,
                include_current_round_locks=True,
            )
        if block_info is not None:
            _record_exploration_block(
                iteration_id=iteration_id,
                candidate=candidate,
                base_source=best_source,
                factor_change_mode=current_factor_change_mode,
                factor_mode_context=factor_mode_context,
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
                factor_change_mode=current_factor_change_mode,
                iteration_lane=current_iteration_lane,
                iteration_lane_reason=iteration_lane_reason,
            )
            continue

        try:
            while True:
                try:
                    candidate, candidate_report = _candidate_with_repair(
                        best_source,
                        candidate,
                        workspace_root=workspace_root,
                        factor_change_mode=current_factor_change_mode,
                        iteration_lane=current_iteration_lane,
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
                            factor_change_mode=current_factor_change_mode,
                            factor_mode_context=factor_mode_context,
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
                        factor_change_mode=current_factor_change_mode,
                        iteration_lane=current_iteration_lane,
                        iteration_lane_reason=iteration_lane_reason,
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
                    factor_change_mode=current_factor_change_mode,
                    factor_mode_context=factor_mode_context,
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
            _record_runtime_failure(
                iteration_id=iteration_id,
                candidate=exc.candidate,
                base_source=best_source,
                factor_change_mode=current_factor_change_mode,
                factor_mode_context=factor_mode_context,
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
    working_base_accepted = False
    compaction_delta: dict[str, Any] = {}
    if not champion_accepted and current_iteration_lane == "compaction":
        working_base_accepted, decision_reason, compaction_delta = _working_base_compaction_acceptance_decision(
            candidate_report,
            base_source=best_source,
            candidate_source=candidate.strategy_code,
        )
    accepted = champion_accepted or working_base_accepted
    entry_note = ""
    if not accepted and candidate_report.gate_passed:
        entry_note = decision_reason

    entry_base = _build_journal_entry(
        iteration_id=iteration_id,
        candidate=candidate,
        base_source=best_source,
        candidate_report=candidate_report,
        factor_change_mode=current_factor_change_mode,
        factor_mode_context=factor_mode_context,
        outcome="accepted" if accepted else "rejected",
        stop_stage="full_eval",
        gate_reason=decision_reason if not accepted else None,
        note=entry_note,
        extra_fields={
            "reference_update_kind": (
                "champion" if champion_accepted else "working_base" if working_base_accepted else "none"
            ),
            "iteration_lane": current_iteration_lane,
            "iteration_lane_reason": iteration_lane_reason,
            "compaction_summary": str(compaction_delta.get("summary", "")).strip(),
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
        champion_shadow_test_metrics = shadow_test_metrics or {}

        _persist_best_state(
            best_source,
            best_report,
            shadow_test_metrics=shadow_test_metrics,
            stage_started_at=reference_stage_started_at,
            stage_iteration=reference_stage_iteration,
        )
        _clear_research_session_state(remove_workspace=True, reason="new champion accepted")
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
            factor_change_mode=current_factor_change_mode,
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

    if working_base_accepted:
        best_source = candidate.strategy_code
        best_report = candidate_report
        reference_stage_started_at = datetime.now(UTC).isoformat()
        reference_stage_iteration = iteration_id
        _persist_best_state(
            best_source,
            best_report,
            stage_started_at=reference_stage_started_at,
            stage_iteration=reference_stage_iteration,
        )
        _clear_research_session_state(remove_workspace=True, reason="working base updated by compaction lane")
        _append_research_journal_entry(entry_base)
        if maybe_compact(RUNTIME.paths.journal_file):
            log_info("研究日志已压缩")
        log_info(
            f"🧹 第 {iteration_id} 轮更新 working_base: "
            f"quality={best_report.metrics['quality_score']:.2f}, "
            f"promotion={best_report.metrics['promotion_score']:.2f}, "
            f"reason={decision_reason}"
        )
        write_heartbeat(
            "working_base_updated",
            message=f"iteration {iteration_id} working base updated",
            reference_role=_reference_role(),
            benchmark_role=_benchmark_role(),
            promotion=best_report.metrics["promotion_score"],
            quality=best_report.metrics["quality_score"],
            gate=decision_reason,
        )
        return "working_base_accepted"

    _append_research_journal_entry(entry_base)
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
                "working_base_accepted",
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

        write_heartbeat(
            "sleeping",
            message=f"iteration {iteration_counter} sleeping",
            last_outcome=outcome,
            sleep_seconds=RUNTIME.loop_interval_seconds,
        )
        _sleep_with_stop(RUNTIME.loop_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
