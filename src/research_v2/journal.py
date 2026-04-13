#!/usr/bin/env python3
"""研究日志与历史记忆。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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


def build_journal_prompt_summary(entries: list[dict[str, Any]], limit: int = 6) -> str:
    accepted = _recent_by_outcome(entries, "accepted", limit=min(3, limit))
    rejected = _recent_by_outcome(entries, "rejected", limit=min(4, limit))

    parts: list[str] = []
    if accepted:
        parts.append("最近有效方向:")
        parts.extend(_format_recent_entries(accepted))
    if rejected:
        parts.append("最近无效方向:")
        parts.extend(_format_recent_entries(rejected))

    tag_board = _scoreboard(entries[-20:], "change_tags", limit=6)
    if tag_board:
        parts.append("标签得分板:")
        parts.extend(tag_board)

    region_board = _scoreboard(entries[-20:], "edited_regions", limit=6)
    if region_board:
        parts.append("代码区域得分板:")
        parts.extend(region_board)

    return "\n".join(parts) if parts else "暂无研究历史。"


def has_recent_code_hash(entries: list[dict[str, Any]], code_hash: str, lookback: int = 12) -> bool:
    for entry in reversed(entries[-lookback:]):
        if entry.get("code_hash") == code_hash:
            return True
    return False

