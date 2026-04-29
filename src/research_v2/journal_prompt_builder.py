#!/usr/bin/env python3
"""Journal prompt 汇总辅助。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research_v2 import journal as mod


def build_journal_prompt_summary_impl(
    entries: list[dict[str, Any]],
    limit: int = 6,
    journal_path: Path | None = None,
    prompt_compact: bool = False,
    current_score_regime: str = "",
    current_iteration: int = 0,
    active_stage_started_at: str = "",
    active_stage_iteration: int = 0,
    active_reference_code_hash: str = "",
    reference_role: str = "",
    reference_metrics: dict[str, Any] | None = None,
    memory_root: Path | None = None,
) -> str:
    all_entries = [
        mod._strip_test_observation_fields(entry)
        for entry in entries
    ]
    active_score_regime = current_score_regime or mod._latest_score_regime(all_entries)
    if current_iteration <= 0:
        current_iteration = max(
            (int(entry.get("iteration", 0) or 0) for entry in all_entries),
            default=0,
        ) + 1
    current_entries = mod._entries_for_score_regime(all_entries, active_score_regime)
    current_relevant_entries = mod._strategy_relevant_entries(current_entries)
    resolved_reference_code_hash = str(active_reference_code_hash or "").strip()
    if not resolved_reference_code_hash:
        resolved_reference_code_hash = next(
            (
                str(entry.get("code_hash", "")).strip()
                for entry in reversed(current_entries)
                if str(entry.get("outcome", "")).strip() == "accepted"
                and str(entry.get("code_hash", "")).strip()
            ),
            "",
        )
    current_stage_entries, past_stage_entries, stage_meta = mod._partition_entries_by_stage(
        all_entries,
        score_regime=active_score_regime,
        active_stage_started_at=active_stage_started_at,
        active_stage_iteration=active_stage_iteration,
    )
    current_stage_relevant_entries = mod._strategy_relevant_entries(current_stage_entries)
    current_stage_technical_invalid_count = len(current_stage_entries) - len(current_stage_relevant_entries)
    stage_name = (
        f"当前 {reference_role} stage"
        if reference_role in {"baseline", "champion"}
        else "当前 stage"
    )
    stage_anchor_parts: list[str] = []
    if int(stage_meta.get("stage_iteration", 0) or 0) > 0:
        stage_anchor_parts.append(f"round {int(stage_meta['stage_iteration'])}")
    if str(stage_meta.get("stage_started_at", "")).strip():
        stage_anchor_parts.append(str(stage_meta["stage_started_at"]))
    stage_header = (
        f"{stage_name}（自 {' / '.join(stage_anchor_parts)} 激活以来）:"
        if stage_anchor_parts
        else f"{stage_name}:"
    )
    stage_scope = stage_header.rstrip(":")
    parts: list[str] = []
    if not current_entries:
        parts.extend(mod._empty_prompt_tables(stage_scope))
    else:
        if current_stage_entries:
            board_entries = current_stage_relevant_entries
            if not board_entries:
                parts.extend(mod._empty_prompt_tables(stage_scope))
                parts.append(
                    f"{stage_scope} 最近仅出现 {current_stage_technical_invalid_count} 条技术性空转；"
                    "它们已从 failure wiki / 方向风险 / 过热统计中隔离，不作为策略失败方向证据。"
                )
            else:
                display_limit = max(1, min(4, limit)) if prompt_compact else max(1, limit)
                display_entries = board_entries[-display_limit:]
                stage_start_index = max(0, int(stage_meta.get("stage_iteration", 0) or 0) - 1)
                executive_lines = mod._stage_executive_summary_lines(
                    board_entries,
                    stage_name=stage_name,
                    reference_metrics=reference_metrics,
                    limit=min(8, limit),
                )
                if executive_lines:
                    parts.extend(executive_lines)
                    parts.append("")

                failure_nucleus_lines = mod._stage_failure_nucleus_lines(board_entries, limit=min(4, limit))
                if failure_nucleus_lines:
                    parts.extend(failure_nucleus_lines)
                    parts.append("")

                board_lines = mod._direction_risk_board(board_entries, limit=min(8, limit))
                if board_lines:
                    parts.extend(board_lines)
                    parts.append("")

                accepted_count = sum(1 for entry in board_entries if entry.get("outcome") == "accepted")
                rejected_count = sum(1 for entry in board_entries if entry.get("outcome") == "rejected")
                duplicate_skipped_count = sum(1 for entry in board_entries if entry.get("outcome") == "duplicate_skipped")
                behavioral_noop_count = sum(1 for entry in board_entries if entry.get("outcome") == "behavioral_noop")
                exploration_blocked_count = sum(1 for entry in board_entries if entry.get("outcome") == "exploration_blocked")
                early_rejected_count = sum(1 for entry in board_entries if entry.get("outcome") == "early_rejected")
                runtime_failed_count = sum(1 for entry in board_entries if entry.get("outcome") == "runtime_failed")
                repeated_basin_count = mod._repeated_result_basin_entry_count(board_entries)
                operating_metrics = mod._stage_operating_metrics(
                    board_entries,
                    reference_metrics=reference_metrics,
                )
                parts.append(
                    f"{stage_scope} 共 {len(current_stage_entries)} 条："
                    f"保留 {accepted_count}，未保留 {rejected_count}，"
                    f"重复跳过 {duplicate_skipped_count}，"
                    f"结果盆地重复 {repeated_basin_count}，"
                    f"行为无变化 {behavioral_noop_count}，"
                    f"探索拦截 {exploration_blocked_count}，"
                    f"提前淘汰 {early_rejected_count}，运行失败 {runtime_failed_count}，"
                    f"技术空转 {current_stage_technical_invalid_count}。"
                )
                if prompt_compact:
                    weak_side = str(operating_metrics.get("weak_side", "")).strip() or "-"
                    parts.append(
                        "当前 stage 运营摘记: "
                        f"accept={mod._score_value(operating_metrics.get('accept_rate')):.0%} | "
                        f"noop={mod._score_value(operating_metrics.get('behavioral_noop_rate')):.0%} | "
                        f"explore_block={mod._score_value(operating_metrics.get('exploration_blocked_rate')):.0%} | "
                        f"smoke->full={mod._score_value(operating_metrics.get('smoke_to_full_eval_rate')):.0%} | "
                        f"弱侧={weak_side}"
                    )
                else:
                    overfit_lines = mod._overfit_risk_board(board_entries, limit=min(8, limit))
                    if overfit_lines:
                        parts.extend(overfit_lines)
                        parts.append("")

                    exploration_lines = mod._exploration_trigger_lines(board_entries, limit=min(8, limit))
                    if exploration_lines:
                        parts.extend(exploration_lines)
                        parts.append("")

                    parts.extend(
                        mod._format_stage_operating_metrics(
                            operating_metrics,
                            stage_name=stage_name,
                        )
                    )
                    if len(display_entries) < len(board_entries):
                        parts.append(
                            f"以下表格与元信息仅展示最近 {len(display_entries)} 条；完整当前 stage 已写入 memory 归档。"
                        )
                if current_stage_technical_invalid_count > 0:
                    parts.append(
                        f"- 本 stage 另有 {current_stage_technical_invalid_count} 条技术性空转已被隔离；"
                        "它们不会进入 failure wiki exact cut、方向风险表或过热统计。"
                    )
                parts.append("")
                if prompt_compact:
                    recent_meta_lines = mod._recent_round_meta_lines(display_entries, stage_start_index)
                    if recent_meta_lines and str(recent_meta_lines[0]).startswith("最近轮次元信息"):
                        recent_meta_lines = recent_meta_lines[1:]
                    parts.append("最近轮次元信息（精简）:")
                    parts.extend(recent_meta_lines)
                else:
                    failure_tag_lines = mod._recent_failure_tag_lines(board_entries, limit=min(8, limit))
                    if failure_tag_lines:
                        parts.append("当前 stage 高频失败标签:")
                        parts.extend(failure_tag_lines)
                    parts.append(f"{stage_name} 核心指标表:")
                    parts.extend(mod._format_recent_rounds_table(display_entries, stage_start_index))
                    parts.append("")
                    parts.extend(mod._recent_round_meta_lines(display_entries, stage_start_index))
        else:
            parts.extend(mod._empty_prompt_tables(stage_scope))

        past_stage_groups = mod._group_entries_into_stages(past_stage_entries)
        past_stage_summaries = [
            mod._summarize_stage_entries(stage_entries, stage_id=index + 1)
            for index, stage_entries in enumerate(past_stage_groups)
            if stage_entries
        ]
        all_time_payload = mod._all_time_tables_payload(current_relevant_entries, limit=min(8, limit))
        if not prompt_compact:
            past_stage_lines = mod._format_past_stage_summary_lines(past_stage_summaries, limit=min(4, limit))
            if past_stage_lines:
                if parts:
                    parts.append("")
                parts.extend(past_stage_lines)

            all_time_lines = mod._format_all_time_tables(all_time_payload)
            if all_time_lines:
                if parts:
                    parts.append("")
                parts.extend(all_time_lines)
    if current_entries and not past_stage_entries:
        past_stage_summaries = []
        all_time_payload = mod._all_time_tables_payload(current_relevant_entries, limit=min(8, limit))
    elif not current_entries:
        past_stage_summaries = []
        all_time_payload = {}

    if not prompt_compact:
        legacy_lines = mod._legacy_regime_reference_lines(
            all_entries,
            current_score_regime=active_score_regime,
            limit=min(mod.LEGACY_REFERENCE_REGIME_LIMIT, limit),
        )
        if legacy_lines:
            if parts:
                parts.append("")
            parts.extend(legacy_lines)

    summary_text = "\n".join(parts) if parts else "暂无研究历史。"
    if memory_root is not None:
        snapshot_meta = {
            **stage_meta,
            "reference_role": reference_role,
            "score_regime": active_score_regime,
            "stage_name": stage_name,
            "active_reference_code_hash": resolved_reference_code_hash,
        }
        mod._write_prompt_memory_snapshots(
            memory_root,
            all_entries=current_entries,
            current_stage_entries=current_stage_entries,
            current_stage_meta=snapshot_meta,
            past_stage_summaries=past_stage_summaries,
            all_time_tables=all_time_payload,
            summary_text=summary_text,
        )
    return summary_text
