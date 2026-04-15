#!/usr/bin/env python3
"""研究日志与历史记忆。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


# ==================== 常量 ====================


COMPACT_INTERVAL = 20
NEGATIVE_OUTCOMES = {"rejected", "early_rejected"}


# ==================== 基础读写 ====================


def load_journal_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def append_journal_entry(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ==================== 压缩（compact） ====================


def _compact_file_for(journal_path: Path) -> Path:
    return journal_path.with_suffix(".compact.json")


def load_compact(journal_path: Path) -> dict[str, Any]:
    compact_path = _compact_file_for(journal_path)
    if not compact_path.exists():
        return {}
    try:
        return json.loads(compact_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_compact(journal_path: Path, payload: dict[str, Any]) -> None:
    compact_path = _compact_file_for(journal_path)
    compact_path.parent.mkdir(parents=True, exist_ok=True)
    compact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _outcome_bucket(raw_outcome: str) -> str:
    if raw_outcome == "accepted":
        return "accepted"
    if raw_outcome in NEGATIVE_OUTCOMES:
        return "rejected"
    return raw_outcome


def _score_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _truncate(text: Any, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)] + "..."


def _compact_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """把一批 journal 条目压缩成结构化的经验摘要。"""
    if not entries:
        return {}

    accepted = [e for e in entries if _outcome_bucket(str(e.get("outcome", ""))) == "accepted"]
    rejected = [e for e in entries if _outcome_bucket(str(e.get("outcome", ""))) == "rejected"]
    early_rejected_count = sum(1 for e in entries if e.get("outcome") == "early_rejected")

    # 标签统计：哪些方向有效 / 无效
    tag_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"accepted": 0, "rejected": 0, "scores": []}
    )
    for entry in entries:
        outcome = _outcome_bucket(str(entry.get("outcome", "")))
        score = _score_value(entry.get("promotion_score"))
        for tag in entry.get("change_tags", []):
            bucket = tag_stats[tag]
            if outcome in {"accepted", "rejected"}:
                bucket[outcome] = bucket.get(outcome, 0) + 1
            bucket["scores"].append(score)

    # 区域统计
    region_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"accepted": 0, "rejected": 0}
    )
    for entry in entries:
        outcome = _outcome_bucket(str(entry.get("outcome", "")))
        for region in entry.get("edited_regions", []):
            if outcome in {"accepted", "rejected"}:
                region_stats[region][outcome] = region_stats[region].get(outcome, 0) + 1

    # 常见失败原因
    gate_reasons: dict[str, int] = defaultdict(int)
    for entry in rejected:
        reason = entry.get("gate_reason", "")
        for segment in reason.split("；"):
            segment = segment.strip()
            if segment and segment != "通过":
                gate_reasons[segment] += 1
    top_failures = sorted(gate_reasons.items(), key=lambda x: -x[1])[:8]

    # 参数有效区间（从 accepted 中提取 metrics 范围）
    metric_keys = [
        "eval_avg_return", "validation_avg_return", "worst_drawdown",
        "avg_fee_drag", "daily_sharpe", "daily_sortino", "profit_factor",
        "eval_window_sortino_p25", "eval_window_sortino_worst",
    ]
    accepted_metric_ranges: dict[str, dict[str, float]] = {}
    if accepted:
        for key in metric_keys:
            values = [e["metrics"][key] for e in accepted if key in e.get("metrics", {})]
            if values:
                accepted_metric_ranges[key] = {
                    "min": min(values),
                    "max": max(values),
                    "avg": _mean(values),
                }

    # 最佳 / 最差候选摘要
    best_entry = max(entries, key=lambda e: _score_value(e.get("promotion_score")))
    worst_entry = min(entries, key=lambda e: _score_value(e.get("promotion_score")))

    tag_summary = {}
    for tag, stats in tag_stats.items():
        tag_summary[tag] = {
            "accepted": stats.get("accepted", 0),
            "rejected": stats.get("rejected", 0),
            "avg_score": _mean(stats["scores"]),
        }

    return {
        "entry_count": len(entries),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "early_rejected_count": early_rejected_count,
        "accept_rate": len(accepted) / len(entries) if entries else 0.0,
        "tag_summary": tag_summary,
        "region_summary": dict(region_stats),
        "top_failure_reasons": top_failures,
        "accepted_metric_ranges": accepted_metric_ranges,
        "best_candidate": {
            "id": best_entry.get("candidate_id", ""),
            "score": _score_value(best_entry.get("promotion_score")),
            "tags": best_entry.get("change_tags", []),
            "hypothesis": best_entry.get("hypothesis", ""),
        },
        "worst_candidate": {
            "id": worst_entry.get("candidate_id", ""),
            "score": _score_value(worst_entry.get("promotion_score")),
            "tags": worst_entry.get("change_tags", []),
            "hypothesis": worst_entry.get("hypothesis", ""),
        },
    }


def maybe_compact(journal_path: Path) -> bool:
    """检查是否需要压缩。每 COMPACT_INTERVAL 条新记录触发一次。

    压缩逻辑：
    - 读取已有 compact 文件中的 compacted_up_to 位置
    - 如果 compacted_up_to 之后积累了 >= COMPACT_INTERVAL 条，就执行压缩
    - 压缩范围是从 compacted_up_to 到倒数第 COMPACT_INTERVAL 条（最新 20 条不压缩）
    - 压缩结果追加到 compact 文件的 rounds 列表中

    返回 True 表示执行了压缩。
    """
    entries = load_journal_entries(journal_path)
    existing_compact = load_compact(journal_path)
    compacted_up_to = existing_compact.get("compacted_up_to", 0)
    uncompacted_count = len(entries) - compacted_up_to

    if uncompacted_count < COMPACT_INTERVAL * 2:
        return False

    compact_end = len(entries) - COMPACT_INTERVAL
    batch = entries[compacted_up_to:compact_end]
    if not batch:
        return False

    batch_summary = _compact_entries(batch)
    batch_summary["range"] = f"entry {compacted_up_to + 1} ~ {compact_end}"

    rounds = existing_compact.get("rounds", [])
    rounds.append(batch_summary)

    _save_compact(journal_path, {
        "compacted_up_to": compact_end,
        "total_compacted_entries": compact_end,
        "rounds": rounds,
    })
    return True


def _format_compact_for_prompt(compact_data: dict[str, Any], limit: int) -> list[str]:
    """把压缩数据转成 AI 可读的文本。"""
    rounds = compact_data.get("rounds", [])
    if not rounds:
        return []

    lines = ["历史经验压缩摘要:"]
    total_accepted = sum(r.get("accepted_count", 0) for r in rounds)
    total_rejected = sum(r.get("rejected_count", 0) for r in rounds)
    total_early_rejected = sum(r.get("early_rejected_count", 0) for r in rounds)
    total_entries = sum(r.get("entry_count", 0) for r in rounds)
    lines.append(
        f"共 {total_entries} 轮历史，{total_accepted} 次通过，"
        f"{total_rejected} 次失败，其中提前淘汰 {total_early_rejected} 次，"
        f"通过率 {total_accepted / total_entries:.0%}"
        if total_entries else "无历史"
    )

    # 合并所有 round 的标签统计
    merged_tags: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"accepted": 0, "rejected": 0, "scores": []}
    )
    for round_data in rounds:
        for tag, stats in round_data.get("tag_summary", {}).items():
            merged = merged_tags[tag]
            merged["accepted"] += stats.get("accepted", 0)
            merged["rejected"] += stats.get("rejected", 0)
            if "avg_score" in stats:
                merged["scores"].append(stats["avg_score"])

    if merged_tags:
        success_ranked = sorted(
            (
                (tag, stats)
                for tag, stats in merged_tags.items()
                if stats["accepted"] > 0
            ),
            key=lambda item: (
                item[1]["accepted"] - item[1]["rejected"],
                item[1]["accepted"],
                -item[1]["rejected"],
                _mean(item[1]["scores"]),
            ),
            reverse=True,
        )
        failure_ranked = sorted(
            (
                (tag, stats)
                for tag, stats in merged_tags.items()
                if stats["rejected"] > 0
            ),
            key=lambda item: (
                item[1]["rejected"] - item[1]["accepted"],
                item[1]["rejected"],
                -item[1]["accepted"],
                -_mean(item[1]["scores"]),
            ),
            reverse=True,
        )

        if success_ranked:
            lines.append("历史较优方向标签:")
            for tag, stats in success_ranked[:limit]:
                avg = _mean(stats["scores"]) if stats["scores"] else 0.0
                lines.append(
                    f"  {tag}: 通过{stats['accepted']}次 失败{stats['rejected']}次 均分{avg:.2f}"
                )
        if failure_ranked:
            lines.append("历史高失败方向标签:")
            for tag, stats in failure_ranked[:limit]:
                avg = _mean(stats["scores"]) if stats["scores"] else 0.0
                lines.append(
                    f"  {tag}: 失败{stats['rejected']}次 通过{stats['accepted']}次 均分{avg:.2f}"
                )

    # 合并失败原因
    merged_failures: dict[str, int] = defaultdict(int)
    for round_data in rounds:
        for reason, count in round_data.get("top_failure_reasons", []):
            merged_failures[reason] += count
    if merged_failures:
        top = sorted(merged_failures.items(), key=lambda x: -x[1])[:6]
        lines.append("最常见的失败原因:")
        for reason, count in top:
            lines.append(f"  {reason} ({count}次)")

    # 最近一轮的 accepted 指标范围
    last_round = rounds[-1]
    ranges = last_round.get("accepted_metric_ranges", {})
    if ranges:
        lines.append("最近通过的候选指标范围:")
        for key, r in ranges.items():
            lines.append(f"  {key}: {r['min']:.2f} ~ {r['max']:.2f} (均值{r['avg']:.2f})")

    return lines


# ==================== 摘要生成 ====================


def _uncompacted_recent_entries(
    entries: list[dict[str, Any]],
    journal_path: Path | None,
) -> tuple[list[dict[str, Any]], int]:
    if not entries:
        return [], 0
    if journal_path is None:
        start_index = max(0, len(entries) - COMPACT_INTERVAL)
        return entries[start_index:], start_index
    compacted_up_to = int(load_compact(journal_path).get("compacted_up_to", 0) or 0)
    compacted_up_to = max(0, min(compacted_up_to, len(entries)))
    return entries[compacted_up_to:], compacted_up_to


def _format_metric(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _display_outcome(raw_outcome: str) -> str:
    return {
        "accepted": "保留",
        "rejected": "未保留",
        "early_rejected": "提前淘汰",
    }.get(raw_outcome, raw_outcome or "-")


def _display_stage(entry: dict[str, Any]) -> str:
    stage = str(entry.get("stop_stage") or "")
    if stage == "early_reject":
        return "提前淘汰"
    if stage == "full_eval":
        return "完整评估"
    if str(entry.get("outcome", "")) in {"accepted", "rejected"}:
        return "完整评估"
    if str(entry.get("outcome", "")) == "early_rejected":
        return "提前淘汰"
    return stage or "-"


def _recent_failure_tag_lines(entries: list[dict[str, Any]], limit: int) -> list[str]:
    bucket: dict[str, int] = defaultdict(int)
    for entry in entries:
        if _outcome_bucket(str(entry.get("outcome", ""))) != "rejected":
            continue
        for tag in entry.get("change_tags", []):
            bucket[tag] += 1
    ranked = sorted(bucket.items(), key=lambda item: (-item[1], item[0]))
    return [f"- {tag}: 最近失败 {count} 次" for tag, count in ranked[:limit]]


def _recent_core_factor_columns(entries: list[dict[str, Any]], limit: int) -> list[str]:
    bucket: dict[str, int] = defaultdict(int)
    for entry in entries:
        for factor in entry.get("core_factors", []):
            if not isinstance(factor, dict):
                continue
            name = str(factor.get("name", "")).strip()
            if name:
                bucket[name] += 1
    ranked = sorted(bucket.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ranked[:limit]]


def _recent_core_factor_lines(entries: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not columns:
        return []
    latest_by_name: dict[str, dict[str, str]] = {}
    for entry in reversed(entries):
        for factor in entry.get("core_factors", []):
            if not isinstance(factor, dict):
                continue
            name = str(factor.get("name", "")).strip()
            if name in columns and name not in latest_by_name:
                latest_by_name[name] = {
                    "thesis": str(factor.get("thesis", "")).strip(),
                    "current_signal": str(factor.get("current_signal", "")).strip(),
                }
    lines = ["最近动态核心因子:"]
    for name in columns:
        payload = latest_by_name.get(name, {})
        thesis = _truncate(payload.get("thesis", ""), 64) or "-"
        signal = _truncate(payload.get("current_signal", ""), 40) or "-"
        lines.append(f"- {name}: 因子依据={thesis}; 最近信号={signal}")
    return lines


def _format_recent_rounds_table(entries: list[dict[str, Any]], start_index: int, core_factor_columns: list[str]) -> list[str]:
    factor_headers = [_truncate(name, 18) for name in core_factor_columns]
    lines = [
        "| 轮次 | 候选 | 结果 | 阶段 | promotion | quality | gate | tags | regions | "
        + " | ".join(factor_headers)
        + (" | " if factor_headers else "")
        + "摘要 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | "
        + " | ".join("---" for _ in factor_headers)
        + (" | " if factor_headers else "")
        + "--- |",
    ]
    for offset, entry in enumerate(entries, start=1):
        round_label = entry.get("iteration")
        if round_label in (None, "", 0):
            round_label = f"j{start_index + offset}"
        candidate_id = _truncate(entry.get("candidate_id", "unknown"), 36)
        outcome = _display_outcome(str(entry.get("outcome", "")))
        stage = _display_stage(entry)
        promotion = _format_metric(entry.get("promotion_score"))
        quality = _format_metric(entry.get("quality_score"))
        gate = _truncate(entry.get("gate_reason", "-"), 20) or "-"
        tags = _truncate(",".join(entry.get("change_tags", [])) or "-", 42)
        regions = _truncate(",".join(entry.get("edited_regions", [])) or "-", 22)
        summary = _truncate(entry.get("note") or entry.get("hypothesis") or "-", 42)
        factor_map: dict[str, str] = {}
        for factor in entry.get("core_factors", []):
            if not isinstance(factor, dict):
                continue
            name = str(factor.get("name", "")).strip()
            if not name:
                continue
            factor_map[name] = _truncate(str(factor.get("current_signal", "")).strip(), 18) or "关注"
        factor_cells = [factor_map.get(name, "-") for name in core_factor_columns]
        lines.append(
            f"| {round_label} | {candidate_id} | {outcome} | {stage} | {promotion} | "
            f"{quality} | {gate} | {tags} | {regions} | "
            + " | ".join(factor_cells)
            + (" | " if factor_cells else "")
            + f"{summary} |"
        )
    return lines


def build_journal_prompt_summary(entries: list[dict[str, Any]], limit: int = 6, journal_path: Path | None = None) -> str:
    parts: list[str] = []

    # 先放压缩的历史经验
    if journal_path is not None:
        compact_data = load_compact(journal_path)
        compact_lines = _format_compact_for_prompt(compact_data, limit=min(8, limit))
        if compact_lines:
            parts.extend(compact_lines)
            parts.append("")

    recent_entries, recent_start = _uncompacted_recent_entries(entries, journal_path)
    if recent_entries:
        accepted_count = sum(1 for entry in recent_entries if entry.get("outcome") == "accepted")
        rejected_count = sum(1 for entry in recent_entries if entry.get("outcome") == "rejected")
        early_rejected_count = sum(1 for entry in recent_entries if entry.get("outcome") == "early_rejected")
        parts.append(
            f"最近未压缩轮次共 {len(recent_entries)} 条："
            f"保留 {accepted_count}，未保留 {rejected_count}，提前淘汰 {early_rejected_count}。"
        )
        failure_tag_lines = _recent_failure_tag_lines(recent_entries, limit=min(8, limit))
        if failure_tag_lines:
            parts.append("最近高频失败标签:")
            parts.extend(failure_tag_lines)
        core_factor_columns = _recent_core_factor_columns(recent_entries, limit=min(4, limit))
        core_factor_lines = _recent_core_factor_lines(recent_entries, core_factor_columns)
        if core_factor_lines:
            parts.extend(core_factor_lines)
        parts.append("最近未压缩轮次表:")
        parts.extend(_format_recent_rounds_table(recent_entries, recent_start, core_factor_columns))

    return "\n".join(parts) if parts else "暂无研究历史。"


def has_recent_code_hash(entries: list[dict[str, Any]], code_hash: str, lookback: int = 12) -> bool:
    for entry in reversed(entries[-lookback:]):
        if entry.get("code_hash") == code_hash:
            return True
    return False
