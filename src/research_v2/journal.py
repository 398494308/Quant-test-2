#!/usr/bin/env python3
"""研究日志与历史记忆。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


# ==================== 常量 ====================


COMPACT_INTERVAL = 20


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


def _compact_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """把一批 journal 条目压缩成结构化的经验摘要。"""
    if not entries:
        return {}

    accepted = [e for e in entries if e.get("outcome") == "accepted"]
    rejected = [e for e in entries if e.get("outcome") == "rejected"]

    # 标签统计：哪些方向有效 / 无效
    tag_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"accepted": 0, "rejected": 0, "scores": []}
    )
    for entry in entries:
        outcome = entry.get("outcome", "")
        score = entry.get("promotion_score", 0.0)
        for tag in entry.get("change_tags", []):
            bucket = tag_stats[tag]
            bucket[outcome] = bucket.get(outcome, 0) + 1
            bucket["scores"].append(score)

    # 区域统计
    region_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"accepted": 0, "rejected": 0}
    )
    for entry in entries:
        outcome = entry.get("outcome", "")
        for region in entry.get("edited_regions", []):
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
        "weighted_eval_return", "holdout_avg_return", "worst_drawdown",
        "avg_fee_drag", "daily_sharpe", "daily_sortino", "profit_factor",
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
    best_entry = max(entries, key=lambda e: e.get("promotion_score", -9999))
    worst_entry = min(entries, key=lambda e: e.get("promotion_score", 9999))

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
        "accept_rate": len(accepted) / len(entries) if entries else 0.0,
        "tag_summary": tag_summary,
        "region_summary": dict(region_stats),
        "top_failure_reasons": top_failures,
        "accepted_metric_ranges": accepted_metric_ranges,
        "best_candidate": {
            "id": best_entry.get("candidate_id", ""),
            "score": best_entry.get("promotion_score", 0.0),
            "tags": best_entry.get("change_tags", []),
            "hypothesis": best_entry.get("hypothesis", ""),
        },
        "worst_candidate": {
            "id": worst_entry.get("candidate_id", ""),
            "score": worst_entry.get("promotion_score", 0.0),
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


def _format_compact_for_prompt(compact_data: dict[str, Any]) -> list[str]:
    """把压缩数据转成 AI 可读的文本。"""
    rounds = compact_data.get("rounds", [])
    if not rounds:
        return []

    lines = ["历史经验压缩摘要:"]
    total_accepted = sum(r.get("accepted_count", 0) for r in rounds)
    total_rejected = sum(r.get("rejected_count", 0) for r in rounds)
    total_entries = sum(r.get("entry_count", 0) for r in rounds)
    lines.append(
        f"共 {total_entries} 轮历史，{total_accepted} 次通过，"
        f"{total_rejected} 次被拒，通过率 {total_accepted / total_entries:.0%}"
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
        ranked = sorted(
            merged_tags.items(),
            key=lambda x: x[1]["accepted"] - x[1]["rejected"],
            reverse=True,
        )
        lines.append("方向标签历史效果:")
        for tag, stats in ranked[:10]:
            avg = _mean(stats["scores"]) if stats["scores"] else 0.0
            lines.append(
                f"  {tag}: 通过{stats['accepted']}次 拒绝{stats['rejected']}次 均分{avg:.1f}"
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


def _recent_by_outcome(entries: list[dict[str, Any]], outcome: str, limit: int) -> list[dict[str, Any]]:
    filtered = [entry for entry in entries if entry.get("outcome") == outcome]
    return filtered[-limit:]


def _format_recent_entries(entries: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        tags = ", ".join(entry.get("change_tags", [])) or "无标签"
        regions = ", ".join(entry.get("edited_regions", [])) or "未标注区域"
        lines.append(
            f"- {entry.get('candidate_id', 'unknown')}: "
            f"score={entry.get('promotion_score', 0.0):.2f}, "
            f"gate={entry.get('gate_reason', 'unknown')}, "
            f"tags={tags}, regions={regions}, "
            f"hypothesis={entry.get('hypothesis', '')}"
        )
    return lines


def _scoreboard(entries: list[dict[str, Any]], key: str, limit: int) -> list[str]:
    bucket: dict[str, dict[str, float]] = {}
    for entry in entries:
        outcome = entry.get("outcome", "")
        direction = 1.0 if outcome == "accepted" else -1.0 if outcome == "rejected" else 0.0
        if direction == 0.0:
            continue
        for value in entry.get(key, []):
            item = bucket.setdefault(value, {"score": 0.0, "accepted": 0.0, "rejected": 0.0})
            item["score"] += direction
            if direction > 0:
                item["accepted"] += 1
            else:
                item["rejected"] += 1
    ranked = sorted(bucket.items(), key=lambda item: (-item[1]["score"], item[0]))
    return [
        f"- {name}: score={stats['score']:.1f}, accepted={int(stats['accepted'])}, rejected={int(stats['rejected'])}"
        for name, stats in ranked[:limit]
    ]


def build_journal_prompt_summary(entries: list[dict[str, Any]], limit: int = 6, journal_path: Path | None = None) -> str:
    # 最近 20 条的明细
    recent = entries[-COMPACT_INTERVAL:]
    accepted = _recent_by_outcome(recent, "accepted", limit=min(3, limit))
    rejected = _recent_by_outcome(recent, "rejected", limit=min(5, limit))

    parts: list[str] = []

    # 先放压缩的历史经验
    if journal_path is not None:
        compact_data = load_compact(journal_path)
        compact_lines = _format_compact_for_prompt(compact_data)
        if compact_lines:
            parts.extend(compact_lines)
            parts.append("")

    # 再放最近的明细
    if accepted:
        parts.append(f"最近{COMPACT_INTERVAL}轮有效方向:")
        parts.extend(_format_recent_entries(accepted))
    if rejected:
        parts.append(f"最近{COMPACT_INTERVAL}轮无效方向:")
        parts.extend(_format_recent_entries(rejected))

    tag_board = _scoreboard(recent, "change_tags", limit=6)
    if tag_board:
        parts.append("标签得分板:")
        parts.extend(tag_board)

    region_board = _scoreboard(recent, "edited_regions", limit=6)
    if region_board:
        parts.append("代码区域得分板:")
        parts.extend(region_board)

    return "\n".join(parts) if parts else "暂无研究历史。"


def has_recent_code_hash(entries: list[dict[str, Any]], code_hash: str, lookback: int = 12) -> bool:
    for entry in reversed(entries[-lookback:]):
        if entry.get("code_hash") == code_hash:
            return True
    return False

