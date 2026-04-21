#!/usr/bin/env python3
"""研究日志与历史记忆。"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_v2.evaluation import OVERFIT_WARN_SCORE, overfit_reference_action, overfit_risk_level_from_score
from research_v2.strategy_code import build_system_edit_signature


# ==================== 常量 ====================


COMPACT_INTERVAL = 20
NEGATIVE_OUTCOMES = {
    "rejected",
    "early_rejected",
    "runtime_failed",
    "duplicate_skipped",
    "behavioral_noop",
}
NO_OP_STOP_STAGES = {
    "duplicate_source",
    "duplicate_history",
    "empty_diff",
    "behavioral_noop",
    "duplicate_result_basin",
}
TECHNICAL_INVALID_STOP_STAGES = frozenset({"duplicate_source", "empty_diff", "blocked_invalid_generation"})

CLUSTER_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ownership_cluster", ("ownership", "acceptance", "handoff", "transfer", "reset_reclaim")),
    ("participation_cluster", ("participation", "fourh_confirmation", "fourh_base_filter", "broadside")),
    ("post_breakdown_cluster", ("breakdown_reset", "post_breakdown", "rebreak", "incremental_discovery", "fresh_discovery")),
    ("sideways_cluster", ("sideways", "tighten_filter", "hourly_discount", "discounted_stall", "hourly_stretch")),
    ("trigger_efficiency_cluster", ("trigger_efficiency", "breakdown_entry", "breakout_entry", "reduce_false_breakdown", "reduce_false_breakout")),
)
CANONICAL_CLUSTER_NAMES = {cluster_name for cluster_name, _ in CLUSTER_KEYWORDS}
LOW_CHANGE_STREAK = 3
LOW_CHANGE_PROMOTION_DELTA_EPS = 0.02
LOW_CHANGE_PROMOTION_SCORE_SPAN = 0.05
LOW_CHANGE_QUALITY_SCORE_SPAN = 0.08
LOW_CHANGE_TREND_SCORE_SPAN = 0.08
LOW_CHANGE_HIT_RATE_SPAN = 0.08
LOW_CHANGE_SIDE_CAPTURE_SPAN = 0.12
LOW_CHANGE_TOTAL_TRADES_SPAN = 6.0
LOW_CHANGE_FEE_DRAG_SPAN = 0.20
LEGACY_REFERENCE_REGIME_LIMIT = 3
LEGACY_REFERENCE_TAG_LIMIT = 3
LEGACY_REFERENCE_CLUSTER_LIMIT = 2
CLUSTER_OVERHEAT_LOOKBACK = 10
CLUSTER_OVERHEAT_MIN_ROUNDS = 6
CLUSTER_OVERHEAT_SHARE = 0.60
DEFAULT_CLUSTER_LOCK_STEPS = (3, 6, 10)
TAG_NOVELTY_MAX_OVERLAP = 0.34
STRUCTURAL_TOKEN_NOVELTY_MAX_OVERLAP = 0.60
STRUCTURAL_REDUCTION_TAGS = frozenset({"remove_dead_gate", "merge_veto", "widen_outer_context"})
RESULT_BASIN_ROUND_DIGITS = 6
RESULT_BASIN_LOOKBACK = 24
DUPLICATE_WATCHLIST_LOOKBACK = 36
DUPLICATE_WATCHLIST_LIMIT = 5
LONG_TARGET_KEYWORDS = (
    "long",
    "bull",
    "breakout",
    "uptrend",
    "多头",
    "上涨",
    "上车",
)
SHORT_TARGET_KEYWORDS = (
    "short",
    "bear",
    "breakdown",
    "downtrend",
    "空头",
    "下跌",
    "做空",
)
SPECIAL_REGION_NAMES = frozenset({"PARAMS", "strategy"})
SPECIAL_REGION_FAMILIES = ("params", "strategy")
SPECIAL_REGION_FAMILY_SET = frozenset(SPECIAL_REGION_FAMILIES)
ORDINARY_REGION_FAMILIES = ("sideways", "flow", "trend_quality", "entry_path")
ORDINARY_REGION_FAMILY_SET = frozenset(ORDINARY_REGION_FAMILIES)


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


def _memory_archive_paths(memory_root: Path) -> dict[str, Path]:
    return {
        "raw_history": memory_root / "raw/full_history.jsonl",
        "raw_rounds_dir": memory_root / "raw/rounds",
        "current_stage": memory_root / "summaries/current_stage_rounds.json",
        "past_stages": memory_root / "summaries/past_stage_summaries.json",
        "all_time_tables": memory_root / "summaries/all_time_tables.json",
        "latest_prompt": memory_root / "prompt/latest_history_package.md",
        "failure_wiki_md": memory_root / "wiki/failure_wiki.md",
        "failure_wiki_json": memory_root / "wiki/failure_wiki_index.json",
        "duplicate_watchlist_md": memory_root / "wiki/duplicate_watchlist.md",
    }


def _slugify_archive_name(value: Any, *, default: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip()).strip("_").lower()
    return text or default


def append_journal_archive(memory_root: Path, entry: dict[str, Any]) -> None:
    paths = _memory_archive_paths(memory_root)
    raw_history_path = paths["raw_history"]
    raw_history_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_history_path.open("a") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    rounds_dir = paths["raw_rounds_dir"]
    rounds_dir.mkdir(parents=True, exist_ok=True)
    iteration = int(entry.get("iteration", 0) or 0)
    candidate_slug = _slugify_archive_name(entry.get("candidate_id"), default="candidate")
    outcome_slug = _slugify_archive_name(entry.get("outcome"), default="entry")
    base_name = f"round_{iteration:04d}_{candidate_slug}_{outcome_slug}.json"
    round_path = rounds_dir / base_name
    suffix = 2
    while round_path.exists():
        round_path = rounds_dir / f"{base_name[:-5]}_{suffix}.json"
        suffix += 1
    round_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2))


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


def _entry_decision_reason(entry: dict[str, Any]) -> str:
    decision_reason = str(entry.get("decision_reason", "")).strip()
    if decision_reason:
        return decision_reason
    gate_reason = str(entry.get("gate_reason", "")).strip()
    if gate_reason and gate_reason != "通过":
        return gate_reason
    note = str(entry.get("note", "")).strip()
    if str(entry.get("outcome", "")) == "rejected" and note:
        return note
    return gate_reason or "-"


def _entry_changed_regions(entry: dict[str, Any]) -> tuple[str, ...]:
    stored = entry.get("system_changed_regions", []) or entry.get("edited_regions", [])
    return tuple(str(item).strip() for item in stored if str(item).strip())


def _entry_system_changed_regions(entry: dict[str, Any]) -> tuple[str, ...] | None:
    if "system_changed_regions" not in entry:
        return None
    stored = entry.get("system_changed_regions") or []
    return tuple(str(item).strip() for item in stored if str(item).strip())


def _entry_region_families(entry: dict[str, Any]) -> tuple[str, ...]:
    stored = entry.get("system_region_families", []) or entry.get("region_families", [])
    if stored:
        return tuple(str(item).strip() for item in stored if str(item).strip())
    return region_families_for_regions(_entry_changed_regions(entry))


def _entry_is_technical_generation_invalid(entry: dict[str, Any]) -> bool:
    if bool(entry.get("technical_generation_invalid")):
        return True

    outcome = str(entry.get("outcome", "")).strip()
    stop_stage = str(entry.get("stop_stage", "")).strip()
    block_kind = str(entry.get("block_kind", "")).strip()
    system_changed_regions = _entry_system_changed_regions(entry)
    changed_regions = system_changed_regions if system_changed_regions is not None else _entry_changed_regions(entry)

    if outcome == "generation_invalid":
        return True
    if outcome == "exploration_blocked" and block_kind == "invalid_generation":
        return True
    if outcome == "duplicate_skipped" and stop_stage in TECHNICAL_INVALID_STOP_STAGES and not changed_regions:
        return True
    return False


def _strategy_relevant_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if not _entry_is_technical_generation_invalid(entry)]


def _round_result_basin_value(value: Any, *, digits: int = RESULT_BASIN_ROUND_DIGITS) -> float:
    return round(_score_value(value), digits)


def result_basin_key_for_entry(entry: dict[str, Any]) -> str:
    if _entry_is_technical_generation_invalid(entry):
        return ""
    raw_outcome = str(entry.get("outcome", "")).strip()
    if raw_outcome in {"accepted", "rejected", "early_rejected"}:
        metrics = entry.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        payload = {
            "bucket": raw_outcome,
            "decision": _entry_decision_reason(entry),
            "quality": _round_result_basin_value(entry.get("quality_score")),
            "promotion": _round_result_basin_value(entry.get("promotion_score")),
            "validation_hit": _round_result_basin_value(metrics.get("validation_segment_hit_rate"), digits=4),
            "validation_bull": _round_result_basin_value(metrics.get("validation_bull_capture_score"), digits=4),
            "validation_bear": _round_result_basin_value(metrics.get("validation_bear_capture_score"), digits=4),
            "total_trades": int(_score_value(metrics.get("total_trades"))),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if raw_outcome == "runtime_failed":
        payload = {
            "bucket": raw_outcome,
            "decision": _entry_decision_reason(entry),
            "runtime_stage": str(entry.get("runtime_failure_stage", "")).strip(),
            "bloat": bool(entry.get("system_bloat_flag")),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if raw_outcome in {"behavioral_noop", "duplicate_skipped", "exploration_blocked"}:
        payload = {
            "bucket": raw_outcome,
            "decision": _entry_decision_reason(entry),
            "stop_stage": str(entry.get("stop_stage", "")).strip(),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return ""


def count_recent_result_basin(
    entries: list[dict[str, Any]],
    basin_key: str,
    *,
    lookback: int = RESULT_BASIN_LOOKBACK,
) -> int:
    if not basin_key:
        return 0
    count = 0
    for entry in reversed(entries[-lookback:]):
        if result_basin_key_for_entry(entry) == basin_key:
            count += 1
    return count


def has_recent_result_basin(
    entries: list[dict[str, Any]],
    basin_key: str,
    *,
    lookback: int = RESULT_BASIN_LOOKBACK,
) -> bool:
    return count_recent_result_basin(entries, basin_key, lookback=lookback) > 0


def format_duplicate_watchlist_markdown(
    entries: list[dict[str, Any]],
    *,
    score_regime: str = "",
    lookback: int = DUPLICATE_WATCHLIST_LOOKBACK,
    limit: int = DUPLICATE_WATCHLIST_LIMIT,
) -> str:
    scoped_entries = _entries_for_score_regime(entries, score_regime) if score_regime else list(entries)
    scoped_entries = _strategy_relevant_entries(scoped_entries)
    recent_entries = scoped_entries[-max(lookback, limit):]

    grouped: dict[str, dict[str, Any]] = {}
    for entry in recent_entries:
        code_hash = str(entry.get("code_hash", "")).strip()
        if not code_hash:
            continue
        bucket = grouped.setdefault(
            code_hash,
            {
                "count": 0,
                "duplicate_history_hits": 0,
                "last_iteration": 0,
                "candidate_ids": Counter(),
                "reasons": Counter(),
                "clusters": Counter(),
                "targets": Counter(),
                "changed_regions": Counter(),
            },
        )
        bucket["count"] += 1
        bucket["last_iteration"] = max(bucket["last_iteration"], _entry_iteration(entry))
        candidate_id = str(entry.get("candidate_id", "")).strip()
        if candidate_id:
            bucket["candidate_ids"][candidate_id] += 1
        reason = _entry_decision_reason(entry)
        if reason and reason != "-":
            bucket["reasons"][reason] += 1
        cluster = cluster_key_for_entry(entry) or str(entry.get("closest_failed_cluster", "")).strip() or "unclassified"
        bucket["clusters"][cluster] += 1
        target_family = str(entry.get("target_family", "")).strip() or "unknown"
        bucket["targets"][target_family] += 1
        for region in (
            entry.get("system_changed_regions", [])
            or entry.get("edited_regions", [])
            or []
        ):
            region_text = str(region).strip()
            if region_text:
                bucket["changed_regions"][region_text] += 1
        if str(entry.get("stop_stage", "")).strip() == "duplicate_history":
            bucket["duplicate_history_hits"] += 1

    items: list[dict[str, Any]] = []
    for bucket in grouped.values():
        repeat_count = int(bucket["count"]) - 1
        if repeat_count <= 0 and int(bucket["duplicate_history_hits"]) <= 0:
            continue
        candidate_name = next((name for name, _ in bucket["candidate_ids"].most_common(1)), "-")
        cluster_name = next((name for name, _ in bucket["clusters"].most_common(1)), "unclassified")
        target_name = next((name for name, _ in bucket["targets"].most_common(1)), "unknown")
        changed_regions = [name for name, _ in bucket["changed_regions"].most_common(3)]
        top_reason = next((name for name, _ in bucket["reasons"].most_common(1)), "-")
        hint = (
            f"不要再交 {cluster_name}/{','.join(changed_regions) or '-'} 的近似版本；"
            "若仍研究这条方向，至少切不同 choke point、不同 changed_regions 或不同最终放行链。"
        )
        items.append(
            {
                "candidate_id": candidate_name,
                "repeat_count": repeat_count,
                "duplicate_history_hits": int(bucket["duplicate_history_hits"]),
                "last_iteration": int(bucket["last_iteration"]),
                "cluster": cluster_name,
                "target": target_name,
                "changed_regions": changed_regions,
                "reason": top_reason,
                "hint": hint,
            }
        )

    items.sort(
        key=lambda item: (
            -int(item["duplicate_history_hits"]),
            -int(item["repeat_count"]),
            -int(item["last_iteration"]),
            str(item["candidate_id"]),
        )
    )

    lines = [
        f"# Duplicate Watchlist ({score_regime or 'current'})",
        "",
        "用法：",
        "- 这里只列最近最容易重复提交的源码指纹摘要，不贴完整代码。",
        "- 若你当前假设与下列某条在 cluster / changed_regions / target 上高度相似，先改写假设再提交。",
        "- 这不是运行时硬拦截；后置源码 hash 判重仍然存在。",
        "",
        "## 最近高频重复源码",
        "| 候选 | 重复提交 | duplicate_history | cluster | changed_regions | target | 最近轮次 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not items:
        lines.append("| - | 0 | 0 | - | - | - | - |")
        return "\n".join(lines)

    for item in items[: max(1, limit)]:
        lines.append(
            f"| {_truncate(item['candidate_id'], 36)} | {item['repeat_count']} | "
            f"{item['duplicate_history_hits']} | {item['cluster']} | "
            f"{','.join(item['changed_regions']) or '-'} | {item['target']} | {item['last_iteration']} |"
        )
        lines.append(f"- 最近原因: {_truncate(item['reason'], 96)}")
        lines.append(f"- 避免方式: {_truncate(item['hint'], 120)}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _normalize_cluster_name(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    text = text.split("（", 1)[0].split("(", 1)[0].strip()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if text in {"", "open", "unknown", "none", "null", "na"}:
        return ""
    return text


def _canonical_cluster_name(raw: Any) -> str:
    normalized = _normalize_cluster_name(raw)
    if not normalized:
        return ""
    if normalized in CANONICAL_CLUSTER_NAMES:
        return normalized
    for cluster_name in CANONICAL_CLUSTER_NAMES:
        if (
            normalized == cluster_name
            or normalized.startswith(f"{cluster_name}_")
            or normalized.endswith(f"_{cluster_name}")
            or cluster_name in normalized
        ):
            return cluster_name
    for cluster_name, keywords in CLUSTER_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return cluster_name
    return ""


def cluster_for_tags(tags: list[str] | tuple[str, ...]) -> str:
    normalized = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    for cluster_name, keywords in CLUSTER_KEYWORDS:
        if any(keyword in tag for tag in normalized for keyword in keywords):
            return cluster_name
    for tag in normalized:
        canonical = _canonical_cluster_name(tag)
        if canonical:
            return canonical
    return normalized[0] if normalized else "unclassified"


def cluster_key_for_components(closest_failed_cluster: Any, change_tags: list[str] | tuple[str, ...]) -> str:
    declared = _normalize_cluster_name(closest_failed_cluster)
    inferred = cluster_for_tags(change_tags)
    inferred_canonical = _canonical_cluster_name(inferred)
    declared_canonical = _canonical_cluster_name(declared)
    if inferred_canonical:
        return inferred_canonical
    if declared_canonical:
        return declared_canonical
    if declared and declared == inferred:
        return declared
    return declared or inferred


def cluster_key_for_entry(entry: dict[str, Any]) -> str:
    stored = _canonical_cluster_name(entry.get("cluster_key", "")) or _normalize_cluster_name(entry.get("cluster_key", ""))
    if stored:
        return stored
    return cluster_key_for_components(
        entry.get("closest_failed_cluster", ""),
        entry.get("change_tags", []),
    )


def _entry_score_regime(entry: dict[str, Any]) -> str:
    return str(entry.get("score_regime", "")).strip()


def _risk_label(*, attempts: int, failures: int, zero_delta: int, runtime_errors: int, best_delta: float) -> str:
    if runtime_errors > 0:
        return "RUNTIME_RISK"
    if failures >= 5 and zero_delta >= 4 and best_delta <= 1e-9:
        return "EXHAUSTED"
    if failures >= 3 and zero_delta >= 2 and best_delta <= 1e-9:
        return "SATURATED"
    if best_delta > LOW_CHANGE_PROMOTION_DELTA_EPS:
        return "ACTIVE_WINNER"
    if best_delta > 0.0:
        return "WARM"
    if attempts >= 2 and failures == attempts:
        return "WARM"
    return "OPEN"


def region_families_for_regions(regions: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    family_map = {
        "PARAMS": "params",
        "_is_sideways_regime": "sideways",
        "_sideways_release_flags": "sideways",
        "_flow_signal_metrics": "flow",
        "_flow_confirmation_ok": "flow",
        "_flow_entry_ok": "flow",
        "_trend_quality_ok": "trend_quality",
        "_trend_quality_long": "trend_quality",
        "_trend_quality_short": "trend_quality",
        "_trend_followthrough_ok": "entry_path",
        "_trend_followthrough_long": "entry_path",
        "_trend_followthrough_short": "entry_path",
        "_long_entry_signal": "entry_path",
        "_short_entry_signal": "entry_path",
        "long_outer_context_ok": "trend_quality",
        "long_breakout_ok": "entry_path",
        "long_pullback_ok": "entry_path",
        "long_trend_reaccel_ok": "entry_path",
        "long_signal_path_ok": "entry_path",
        "long_final_veto_clear": "entry_path",
        "short_outer_context_ok": "trend_quality",
        "breakdown_ready": "entry_path",
        "short_breakdown_ok": "entry_path",
        "short_bounce_fail_ok": "entry_path",
        "short_trend_reaccel_ok": "entry_path",
        "short_final_veto_clear": "entry_path",
        "strategy": "strategy",
    }
    normalized = []
    for region in regions:
        name = str(region).strip()
        family = family_map.get(name, name)
        if family and family not in normalized:
            normalized.append(family)
    return tuple(normalized)


def ordinary_region_families(region_families: list[str] | tuple[str, ...] | set[str]) -> tuple[str, ...]:
    normalized = []
    for family in region_families:
        family_name = str(family).strip()
        if family_name in ORDINARY_REGION_FAMILY_SET and family_name not in normalized:
            normalized.append(family_name)
    return tuple(normalized)


def special_region_families(region_families: list[str] | tuple[str, ...] | set[str]) -> tuple[str, ...]:
    normalized = []
    for family in region_families:
        family_name = str(family).strip()
        if family_name in SPECIAL_REGION_FAMILY_SET and family_name not in normalized:
            normalized.append(family_name)
    return tuple(normalized)


def ordinary_changed_regions(regions: list[str] | tuple[str, ...] | set[str]) -> tuple[str, ...]:
    normalized = []
    for region in regions:
        region_name = str(region).strip()
        if not region_name or region_name in SPECIAL_REGION_NAMES:
            continue
        region_families = region_families_for_regions((region_name,))
        if not region_families:
            continue
        if region_families[0] in ORDINARY_REGION_FAMILY_SET and region_name not in normalized:
            normalized.append(region_name)
    return tuple(normalized)


def target_family_from_text(
    change_tags: list[str] | tuple[str, ...],
    hypothesis: Any,
    expected_effects: list[str] | tuple[str, ...],
) -> str:
    corpus = " ".join(
        [
            *[str(tag).strip().lower() for tag in change_tags if str(tag).strip()],
            str(hypothesis or "").strip().lower(),
            *[str(effect).strip().lower() for effect in expected_effects if str(effect).strip()],
        ]
    )
    long_hits = sum(1 for keyword in LONG_TARGET_KEYWORDS if keyword in corpus)
    short_hits = sum(1 for keyword in SHORT_TARGET_KEYWORDS if keyword in corpus)
    if long_hits > 0 and short_hits == 0:
        return "long"
    if short_hits > 0 and long_hits == 0:
        return "short"
    if long_hits > 0 and short_hits > 0:
        return "mixed"
    return "unknown"


def _system_cluster_key_from_regions(
    changed_regions: set[str] | tuple[str, ...] | list[str],
    ordinary_region_families_set: set[str] | tuple[str, ...] | list[str],
) -> str:
    region_set = {
        str(item).strip()
        for item in changed_regions
        if str(item).strip()
    }
    family_set = {
        str(item).strip()
        for item in ordinary_region_families_set
        if str(item).strip()
    }
    if region_set & {"_is_sideways_regime", "_sideways_release_flags"}:
        return "sideways_cluster"
    if region_set & {"_flow_signal_metrics", "_flow_confirmation_ok", "_flow_entry_ok"}:
        return "trigger_efficiency_cluster"
    if region_set & {"breakdown_ready", "short_breakdown_ok", "short_bounce_fail_ok"}:
        return "post_breakdown_cluster"
    if region_set & {"long_final_veto_clear", "short_final_veto_clear", "_trend_followthrough_long", "_trend_followthrough_short"}:
        return "ownership_cluster"
    if region_set & {"long_outer_context_ok", "short_outer_context_ok"}:
        return "participation_cluster"
    if region_set & {"long_breakout_ok", "long_pullback_ok", "long_signal_path_ok", "_long_entry_signal", "_short_entry_signal"}:
        return "trigger_efficiency_cluster"
    if family_set == {"sideways"}:
        return "sideways_cluster"
    return ""


def exploration_signature_for_entry(entry: dict[str, Any]) -> dict[str, Any]:
    stored_regions = entry.get("system_region_families", []) or entry.get("region_families", [])
    if stored_regions:
        region_families = tuple(str(item).strip() for item in stored_regions if str(item).strip())
    else:
        region_families = region_families_for_regions(
            entry.get("system_changed_regions", []) or entry.get("edited_regions", [])
        )

    stored_changed_regions = entry.get("system_changed_regions", []) or entry.get("edited_regions", [])
    changed_regions = {
        str(item).strip()
        for item in stored_changed_regions
        if str(item).strip()
    }
    stored_ordinary_region_families = entry.get("system_ordinary_region_families", []) or entry.get("ordinary_region_families", [])
    if stored_ordinary_region_families:
        ordinary_region_family_set = {
            str(item).strip()
            for item in stored_ordinary_region_families
            if str(item).strip()
        }
    else:
        ordinary_region_family_set = set(ordinary_region_families(region_families))

    stored_special_region_families = entry.get("system_special_region_families", []) or entry.get("special_region_families", [])
    if stored_special_region_families:
        special_region_family_set = {
            str(item).strip()
            for item in stored_special_region_families
            if str(item).strip()
        }
    else:
        special_region_family_set = set(special_region_families(region_families))

    stored_ordinary_changed_regions = entry.get("system_ordinary_changed_regions", []) or entry.get("ordinary_changed_regions", [])
    if stored_ordinary_changed_regions:
        ordinary_changed_region_set = {
            str(item).strip()
            for item in stored_ordinary_changed_regions
            if str(item).strip()
        }
    else:
        ordinary_changed_region_set = set(ordinary_changed_regions(changed_regions))

    stored_target = str(entry.get("target_family", "")).strip()
    if stored_target:
        target_family = stored_target
    else:
        target_family = target_family_from_text(
            entry.get("change_tags", []),
            entry.get("hypothesis", ""),
            entry.get("expected_effects", []),
        )

    stored_factors = entry.get("core_factor_names", [])
    if stored_factors:
        core_factor_names = {
            str(item).strip()
            for item in stored_factors
            if str(item).strip()
        }
    else:
        core_factor_names = {
            str(factor.get("name", "")).strip()
            for factor in entry.get("core_factors", [])
            if isinstance(factor, dict) and str(factor.get("name", "")).strip()
        }

    param_families = {
        str(item).strip()
        for item in entry.get("system_param_families", [])
        if str(item).strip()
    }
    structural_tokens = {
        str(item).strip()
        for item in entry.get("system_structural_tokens", [])
        if str(item).strip()
    }

    system_cluster_key = _system_cluster_key_from_regions(
        changed_regions,
        ordinary_region_family_set,
    )

    return {
        "cluster_key": system_cluster_key or cluster_key_for_entry(entry),
        "tags": {
            str(tag).strip()
            for tag in entry.get("change_tags", [])
            if str(tag).strip()
        },
        "region_families": set(region_families),
        "changed_regions": changed_regions,
        "ordinary_region_families": ordinary_region_family_set,
        "special_region_families": special_region_family_set,
        "ordinary_changed_regions": ordinary_changed_region_set,
        "target_family": target_family,
        "core_factor_names": core_factor_names,
        "param_families": param_families,
        "structural_tokens": structural_tokens,
        "signature_hash": str(entry.get("system_signature_hash", "")).strip(),
    }


def exploration_signature_for_candidate(
    candidate: Any,
    *,
    base_source: str | None = None,
    editable_regions: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    system_signature: dict[str, object] | None = None
    if base_source is not None and editable_regions:
        system_signature = build_system_edit_signature(
            base_source,
            getattr(candidate, "strategy_code", ""),
            editable_regions,
        )

    if system_signature is not None:
        changed_regions = {
            str(item).strip()
            for item in system_signature["changed_regions"]
            if str(item).strip()
        }
        region_families = set(
            region_families_for_regions(tuple(sorted(changed_regions)))
        )
        param_families = {
            str(item).strip()
            for item in system_signature["param_families"]
            if str(item).strip()
        }
        structural_tokens = {
            str(item).strip()
            for item in system_signature["structural_tokens"]
            if str(item).strip()
        }
        signature_hash = str(system_signature["signature_hash"]).strip()
    else:
        changed_regions = {
            str(item).strip()
            for item in getattr(candidate, "edited_regions", ())
            if str(item).strip()
        }
        region_families = set(region_families_for_regions(getattr(candidate, "edited_regions", ())))
        param_families = set()
        structural_tokens = set()
        signature_hash = ""

    ordinary_region_family_set = set(ordinary_region_families(region_families))
    special_region_family_set = set(special_region_families(region_families))
    ordinary_changed_region_set = set(ordinary_changed_regions(changed_regions))

    system_cluster_key = _system_cluster_key_from_regions(
        changed_regions,
        ordinary_region_family_set,
    )

    return {
        "cluster_key": system_cluster_key or cluster_key_for_components(
            getattr(candidate, "closest_failed_cluster", ""),
            getattr(candidate, "change_tags", ()),
        ),
        "tags": {
            str(tag).strip()
            for tag in getattr(candidate, "change_tags", ())
            if str(tag).strip()
        },
        "region_families": region_families,
        "changed_regions": changed_regions,
        "ordinary_region_families": ordinary_region_family_set,
        "special_region_families": special_region_family_set,
        "ordinary_changed_regions": ordinary_changed_region_set,
        "target_family": target_family_from_text(
            getattr(candidate, "change_tags", ()),
            getattr(candidate, "hypothesis", ""),
            getattr(candidate, "expected_effects", ()),
        ),
        "core_factor_names": {
            str(getattr(factor, "name", "")).strip()
            for factor in getattr(candidate, "core_factors", ())
            if str(getattr(factor, "name", "")).strip()
        },
        "param_families": param_families,
        "structural_tokens": structural_tokens,
        "signature_hash": signature_hash,
    }


def _compact_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """把一批 journal 条目压缩成结构化的经验摘要。"""
    if not entries:
        return {}

    relevant_entries = _strategy_relevant_entries(entries)
    technical_invalid_count = len(entries) - len(relevant_entries)
    accepted = [e for e in relevant_entries if _outcome_bucket(str(e.get("outcome", ""))) == "accepted"]
    rejected = [e for e in relevant_entries if _outcome_bucket(str(e.get("outcome", ""))) == "rejected"]
    duplicate_skipped_count = sum(1 for e in relevant_entries if e.get("outcome") == "duplicate_skipped")
    behavioral_noop_count = sum(1 for e in relevant_entries if e.get("outcome") == "behavioral_noop")
    exploration_blocked_count = sum(1 for e in relevant_entries if e.get("outcome") == "exploration_blocked")
    early_rejected_count = sum(1 for e in relevant_entries if e.get("outcome") == "early_rejected")
    runtime_failed_count = sum(1 for e in relevant_entries if e.get("outcome") == "runtime_failed")
    bloat_flag_count = sum(1 for e in relevant_entries if bool(e.get("system_bloat_flag")))

    # 标签统计：哪些方向有效 / 无效
    tag_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"accepted": 0, "rejected": 0, "scores": []}
    )
    for entry in relevant_entries:
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
    for entry in relevant_entries:
        outcome = _outcome_bucket(str(entry.get("outcome", "")))
        for region in _entry_changed_regions(entry):
            if outcome in {"accepted", "rejected"}:
                region_stats[region][outcome] = region_stats[region].get(outcome, 0) + 1

    # 常见失败原因
    gate_reasons: dict[str, int] = defaultdict(int)
    for entry in rejected:
        reason = _entry_decision_reason(entry)
        for segment in reason.split("；"):
            segment = segment.strip()
            if segment and segment != "通过":
                gate_reasons[segment] += 1
    top_failures = sorted(gate_reasons.items(), key=lambda x: -x[1])[:8]

    # 参数有效区间（从 accepted 中提取 metrics 范围）
    metric_keys = [
        "eval_trend_capture_score", "validation_trend_capture_score",
        "segment_hit_rate", "bull_capture_score", "bear_capture_score",
        "avg_fee_drag", "quality_score", "promotion_score",
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
    ranking_entries = relevant_entries or entries
    best_entry = max(ranking_entries, key=lambda e: _score_value(e.get("promotion_score")))
    worst_entry = min(ranking_entries, key=lambda e: _score_value(e.get("promotion_score")))

    tag_summary = {}
    for tag, stats in tag_stats.items():
        tag_summary[tag] = {
            "accepted": stats.get("accepted", 0),
            "rejected": stats.get("rejected", 0),
            "avg_score": _mean(stats["scores"]),
        }

    cluster_summary: dict[str, dict[str, Any]] = {}
    for entry in relevant_entries:
        cluster = cluster_key_for_entry(entry)
        outcome = _outcome_bucket(str(entry.get("outcome", "")))
        promotion_delta = _score_value(entry.get("promotion_delta"))
        bucket = cluster_summary.setdefault(
            cluster,
            {"attempts": 0, "failures": 0, "zero_delta": 0, "runtime_errors": 0, "best_delta": 0.0},
        )
        bucket["attempts"] += 1
        if outcome == "rejected":
            bucket["failures"] += 1
        if abs(promotion_delta) <= 1e-9:
            bucket["zero_delta"] += 1
        if entry.get("outcome") == "runtime_failed":
            bucket["runtime_errors"] += 1
        bucket["best_delta"] = max(bucket["best_delta"], promotion_delta)
    for stats in cluster_summary.values():
        stats["label"] = _risk_label(
            attempts=int(stats["attempts"]),
            failures=int(stats["failures"]),
            zero_delta=int(stats["zero_delta"]),
            runtime_errors=int(stats["runtime_errors"]),
            best_delta=float(stats["best_delta"]),
        )

    exploration_summary: dict[str, dict[str, int]] = {}
    same_cluster_rounds: dict[str, set[int]] = defaultdict(set)
    lock_trigger_rounds: dict[str, set[int]] = defaultdict(set)
    for entry in entries:
        if str(entry.get("outcome", "")) != "exploration_blocked":
            continue
        cluster = str(entry.get("blocked_cluster", "")).strip() or cluster_key_for_entry(entry)
        iteration = int(entry.get("iteration", 0) or 0)
        if str(entry.get("block_kind", "")).strip() == "same_cluster" and iteration > 0:
            same_cluster_rounds[cluster].add(iteration)
        if int(entry.get("lock_rounds", 0) or 0) > 0 and iteration > 0:
            lock_trigger_rounds[cluster].add(iteration)
    for cluster in set(same_cluster_rounds) | set(lock_trigger_rounds):
        exploration_summary[cluster] = {
            "same_cluster_rounds": len(same_cluster_rounds.get(cluster, set())),
            "lock_trigger_rounds": len(lock_trigger_rounds.get(cluster, set())),
        }

    return {
        "entry_count": len(entries),
        "technical_invalid_count": technical_invalid_count,
        "score_regime": _entry_score_regime(entries[-1]),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "duplicate_skipped_count": duplicate_skipped_count,
        "behavioral_noop_count": behavioral_noop_count,
        "exploration_blocked_count": exploration_blocked_count,
        "early_rejected_count": early_rejected_count,
        "runtime_failed_count": runtime_failed_count,
        "bloat_flag_count": bloat_flag_count,
        "accept_rate": len(accepted) / len(relevant_entries) if relevant_entries else 0.0,
        "tag_summary": tag_summary,
        "cluster_summary": cluster_summary,
        "exploration_summary": exploration_summary,
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

    rounds = existing_compact.get("rounds", [])
    segment_start = compacted_up_to
    current_regime = _entry_score_regime(batch[0])
    segment_entries: list[dict[str, Any]] = []
    for offset, entry in enumerate(batch):
        entry_regime = _entry_score_regime(entry)
        absolute_index = compacted_up_to + offset
        if segment_entries and entry_regime != current_regime:
            batch_summary = _compact_entries(segment_entries)
            batch_summary["range"] = f"entry {segment_start + 1} ~ {absolute_index}"
            batch_summary["score_regime"] = current_regime
            rounds.append(batch_summary)
            segment_entries = []
            segment_start = absolute_index
            current_regime = entry_regime
        segment_entries.append(entry)

    if segment_entries:
        batch_summary = _compact_entries(segment_entries)
        batch_summary["range"] = f"entry {segment_start + 1} ~ {compact_end}"
        batch_summary["score_regime"] = current_regime
        rounds.append(batch_summary)

    _save_compact(journal_path, {
        "compacted_up_to": compact_end,
        "total_compacted_entries": compact_end,
        "rounds": rounds,
    })
    return True


def _format_compact_for_prompt(
    compact_data: dict[str, Any],
    limit: int,
    *,
    score_regime: str = "",
) -> list[str]:
    """把压缩数据转成 AI 可读的文本。"""
    all_rounds = compact_data.get("rounds", [])
    if score_regime:
        rounds = [
            round_data for round_data in all_rounds
            if str(round_data.get("score_regime", "")).strip() == score_regime
        ]
    else:
        rounds = list(all_rounds)
    if not rounds:
        return []

    lines = ["历史经验压缩摘要:"]
    skipped_rounds = len(all_rounds) - len(rounds)
    total_accepted = sum(r.get("accepted_count", 0) for r in rounds)
    total_rejected = sum(r.get("rejected_count", 0) for r in rounds)
    total_behavioral_noop = sum(r.get("behavioral_noop_count", 0) for r in rounds)
    total_exploration_blocked = sum(r.get("exploration_blocked_count", 0) for r in rounds)
    total_early_rejected = sum(r.get("early_rejected_count", 0) for r in rounds)
    total_runtime_failed = sum(r.get("runtime_failed_count", 0) for r in rounds)
    total_entries = sum(r.get("entry_count", 0) for r in rounds)
    lines.append(
        f"共 {total_entries} 轮历史，{total_accepted} 次通过，"
        f"{total_rejected} 次失败，行为无变化 {total_behavioral_noop} 次，探索拦截 {total_exploration_blocked} 次，其中提前淘汰 {total_early_rejected} 次，"
        f"运行失败 {total_runtime_failed} 次，通过率 {total_accepted / total_entries:.0%}"
        if total_entries else "无历史"
    )
    if score_regime and skipped_rounds > 0:
        lines.append(f"已跳过 {skipped_rounds} 段非当前评分口径或旧版未标记口径的压缩历史。")

    merged_clusters: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"attempts": 0, "failures": 0, "zero_delta": 0, "runtime_errors": 0, "best_delta": 0.0}
    )
    for round_data in rounds:
        for cluster, stats in round_data.get("cluster_summary", {}).items():
            merged = merged_clusters[cluster]
            merged["attempts"] += int(stats.get("attempts", 0))
            merged["failures"] += int(stats.get("failures", 0))
            merged["zero_delta"] += int(stats.get("zero_delta", 0))
            merged["runtime_errors"] += int(stats.get("runtime_errors", 0))
            merged["best_delta"] = max(float(merged["best_delta"]), float(stats.get("best_delta", 0.0)))

    if merged_clusters:
        lines.append("历史方向簇摘要:")
        ranked_clusters = sorted(
            merged_clusters.items(),
            key=lambda item: (
                {"EXHAUSTED": 0, "SATURATED": 1, "RUNTIME_RISK": 2, "WARM": 3, "ACTIVE_WINNER": 4, "OPEN": 5}[
                    _risk_label(
                        attempts=int(item[1]["attempts"]),
                        failures=int(item[1]["failures"]),
                        zero_delta=int(item[1]["zero_delta"]),
                        runtime_errors=int(item[1]["runtime_errors"]),
                        best_delta=float(item[1]["best_delta"]),
                    )
                ],
                -int(item[1]["attempts"]),
                -int(item[1]["failures"]),
            ),
        )
        for cluster, stats in ranked_clusters[:limit]:
            label = _risk_label(
                attempts=int(stats["attempts"]),
                failures=int(stats["failures"]),
                zero_delta=int(stats["zero_delta"]),
                runtime_errors=int(stats["runtime_errors"]),
                best_delta=float(stats["best_delta"]),
            )
            lines.append(
                f"  {cluster}: 尝试{stats['attempts']}次 失败{stats['failures']}次 "
                f"零增益{stats['zero_delta']}次 运行报错{stats['runtime_errors']}次 "
                f"最佳delta={stats['best_delta']:.2f} 标签={label}"
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


def _latest_score_regime(entries: list[dict[str, Any]]) -> str:
    for entry in reversed(entries):
        regime = str(entry.get("score_regime", "")).strip()
        if regime:
            return regime
    return ""


def _entries_for_score_regime(entries: list[dict[str, Any]], score_regime: str) -> list[dict[str, Any]]:
    if not score_regime:
        return list(entries)
    return [
        entry for entry in entries
        if _entry_score_regime(entry) == score_regime
    ]


def _merged_exploration_history(
    entries: list[dict[str, Any]],
    *,
    journal_path: Path | None,
    score_regime: str,
    current_iteration: int,
) -> dict[str, dict[str, int]]:
    merged: dict[str, dict[str, int]] = defaultdict(
        lambda: {"same_cluster_rounds": 0, "lock_trigger_rounds": 0}
    )
    compact_data = load_compact(journal_path) if journal_path is not None else {}
    compacted_up_to = int(compact_data.get("compacted_up_to", 0) or 0)
    compacted_up_to = max(0, min(compacted_up_to, len(entries)))
    for round_data in compact_data.get("rounds", []):
        if score_regime and str(round_data.get("score_regime", "")).strip() != score_regime:
            continue
        for cluster, stats in round_data.get("exploration_summary", {}).items():
            merged[cluster]["same_cluster_rounds"] += int(stats.get("same_cluster_rounds", 0))
            merged[cluster]["lock_trigger_rounds"] += int(stats.get("lock_trigger_rounds", 0))

    local_same_cluster_rounds: dict[str, set[int]] = defaultdict(set)
    local_lock_trigger_rounds: dict[str, set[int]] = defaultdict(set)
    for entry in _entries_for_score_regime(entries[compacted_up_to:], score_regime):
        if str(entry.get("outcome", "")) != "exploration_blocked":
            continue
        iteration = int(entry.get("iteration", 0) or 0)
        if iteration <= 0 or iteration >= current_iteration:
            continue
        cluster = str(entry.get("blocked_cluster", "")).strip() or cluster_key_for_entry(entry)
        if str(entry.get("block_kind", "")).strip() == "same_cluster":
            local_same_cluster_rounds[cluster].add(iteration)
        if int(entry.get("lock_rounds", 0) or 0) > 0:
            local_lock_trigger_rounds[cluster].add(iteration)

    for cluster, rounds in local_same_cluster_rounds.items():
        merged[cluster]["same_cluster_rounds"] += len(rounds)
    for cluster, rounds in local_lock_trigger_rounds.items():
        merged[cluster]["lock_trigger_rounds"] += len(rounds)
    return merged


def _current_round_lock_state(
    entries: list[dict[str, Any]],
    *,
    score_regime: str,
    current_iteration: int,
    include_current_round_locks: bool,
) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for entry in _entries_for_score_regime(entries, score_regime):
        if str(entry.get("outcome", "")) != "exploration_blocked":
            continue
        lock_rounds = int(entry.get("lock_rounds", 0) or 0)
        if lock_rounds <= 0:
            continue
        cluster = str(entry.get("blocked_cluster", "")).strip() or cluster_key_for_entry(entry)
        trigger_iteration = int(entry.get("lock_trigger_iteration", entry.get("iteration", 0)) or 0)
        expires_before = int(
            entry.get("lock_expires_before_iteration", trigger_iteration + lock_rounds + 1) or 0
        )
        if include_current_round_locks:
            is_active = trigger_iteration <= current_iteration < expires_before
        else:
            is_active = trigger_iteration < current_iteration < expires_before
        if not is_active:
            continue
        remaining_rounds = max(0, expires_before - max(current_iteration, trigger_iteration + 1))
        active[cluster] = {
            "cluster": cluster,
            "remaining_rounds": remaining_rounds,
            "trigger_count": int(entry.get("lock_level", 0) or 0),
            "lock_rounds": lock_rounds,
            "lock_trigger_iteration": trigger_iteration,
            "lock_expires_before_iteration": expires_before,
            "reason": str(entry.get("gate_reason", "")).strip() or str(entry.get("note", "")).strip() or "-",
        }
    return active


def _same_cluster_low_change_context(entries: list[dict[str, Any]], score_regime: str) -> dict[str, Any] | None:
    scoped_entries = _entries_for_score_regime(entries, score_regime)
    tail = _repeated_result_basin_tail(scoped_entries)
    source_kind = "repeated_basin"
    if not tail:
        tail = _low_change_tail(scoped_entries)
        source_kind = "low_change"
    if not tail:
        tail = _noop_tail(scoped_entries)
        source_kind = "noop"
    if not tail:
        return None

    cluster_counts = Counter(cluster_key_for_entry(entry) for entry in tail if cluster_key_for_entry(entry))
    if not cluster_counts:
        return None
    cluster, count = cluster_counts.most_common(1)[0]
    if count < 2:
        return None

    cluster_entries = [entry for entry in tail if cluster_key_for_entry(entry) == cluster]
    tag_union: set[str] = set()
    region_families: set[str] = set()
    changed_regions: set[str] = set()
    ordinary_region_families_set: set[str] = set()
    special_region_families_set: set[str] = set()
    ordinary_changed_regions_set: set[str] = set()
    target_families: set[str] = set()
    core_factor_names: set[str] = set()
    param_families: set[str] = set()
    structural_tokens: set[str] = set()
    signature_hashes: set[str] = set()
    entry_signatures: list[dict[str, Any]] = []
    for entry in cluster_entries:
        signature = exploration_signature_for_entry(entry)
        entry_signatures.append(signature)
        tag_union.update(signature["tags"])
        region_families.update(signature["region_families"])
        changed_regions.update(signature["changed_regions"])
        ordinary_region_families_set.update(signature["ordinary_region_families"])
        special_region_families_set.update(signature["special_region_families"])
        ordinary_changed_regions_set.update(signature["ordinary_changed_regions"])
        if signature["target_family"] not in {"", "unknown"}:
            target_families.add(signature["target_family"])
        core_factor_names.update(signature["core_factor_names"])
        param_families.update(signature["param_families"])
        structural_tokens.update(signature["structural_tokens"])
        if signature["signature_hash"]:
            signature_hashes.add(signature["signature_hash"])

    return {
        "cluster": cluster,
        "source_kind": source_kind,
        "entries": cluster_entries,
        "entry_signatures": entry_signatures,
        "tag_union": tag_union,
        "region_families": region_families,
        "changed_regions": changed_regions,
        "ordinary_region_families": ordinary_region_families_set,
        "special_region_families": special_region_families_set,
        "ordinary_changed_regions": ordinary_changed_regions_set,
        "target_families": target_families,
        "core_factor_names": core_factor_names,
        "param_families": param_families,
        "structural_tokens": structural_tokens,
        "signature_hashes": signature_hashes,
    }


def _candidate_has_structural_novelty(
    candidate_signature: dict[str, Any],
    low_change_context: dict[str, Any],
) -> bool:
    candidate_regions = candidate_signature["ordinary_region_families"]
    candidate_changed_regions = candidate_signature["ordinary_changed_regions"]
    candidate_target = candidate_signature["target_family"]
    candidate_tags = candidate_signature["tags"]
    candidate_param_families = candidate_signature["param_families"]
    candidate_structural_tokens = candidate_signature["structural_tokens"]
    candidate_signature_hash = candidate_signature["signature_hash"]

    if candidate_signature_hash and candidate_signature_hash in low_change_context["signature_hashes"]:
        return False

    if candidate_regions and not candidate_regions.issubset(low_change_context["ordinary_region_families"]):
        return True
    if candidate_changed_regions and not candidate_changed_regions.issubset(low_change_context["ordinary_changed_regions"]):
        return True
    if candidate_target in {"long", "short", "mixed"} and candidate_target not in low_change_context["target_families"]:
        return True
    if candidate_regions and candidate_param_families - low_change_context["param_families"]:
        return True
    if candidate_tags & STRUCTURAL_REDUCTION_TAGS and candidate_changed_regions:
        if not candidate_changed_regions.issubset(low_change_context["ordinary_changed_regions"]):
            return True
    if candidate_regions and candidate_structural_tokens:
        if len(candidate_structural_tokens - low_change_context["structural_tokens"]) >= 2:
            return True
        overlap_ratio = len(candidate_structural_tokens & low_change_context["structural_tokens"]) / max(len(candidate_structural_tokens), 1)
        if len(candidate_structural_tokens) >= 4 and overlap_ratio <= STRUCTURAL_TOKEN_NOVELTY_MAX_OVERLAP:
            return True
    return False


def build_exploration_guard_state(
    entries: list[dict[str, Any]],
    *,
    journal_path: Path | None,
    score_regime: str,
    current_iteration: int,
    include_current_round_locks: bool = False,
) -> dict[str, Any]:
    relevant_entries = _strategy_relevant_entries(entries)
    return {
        "history_counts": _merged_exploration_history(
            relevant_entries,
            journal_path=journal_path,
            score_regime=score_regime,
            current_iteration=current_iteration,
        ),
        "active_locks": _current_round_lock_state(
            relevant_entries,
            score_regime=score_regime,
            current_iteration=current_iteration,
            include_current_round_locks=include_current_round_locks,
        ),
        "low_change_context": _same_cluster_low_change_context(relevant_entries, score_regime),
    }


def evaluate_candidate_exploration_guard(
    candidate: Any,
    entries: list[dict[str, Any]],
    *,
    journal_path: Path | None,
    score_regime: str,
    current_iteration: int,
    base_source: str | None = None,
    editable_regions: tuple[str, ...] | None = None,
    lock_schedule: tuple[int, ...] = DEFAULT_CLUSTER_LOCK_STEPS,
    include_current_round_locks: bool = False,
) -> dict[str, Any] | None:
    candidate_signature = exploration_signature_for_candidate(
        candidate,
        base_source=base_source,
        editable_regions=editable_regions,
    )
    candidate_cluster = str(candidate_signature["cluster_key"]).strip()
    if not candidate_cluster:
        return None

    state = build_exploration_guard_state(
        entries,
        journal_path=journal_path,
        score_regime=score_regime,
        current_iteration=current_iteration,
        include_current_round_locks=include_current_round_locks,
    )
    active_locks = state["active_locks"]
    if candidate_cluster in active_locks:
        lock = active_locks[candidate_cluster]
        return {
            "block_kind": "locked_cluster",
            "stop_stage": "blocked_locked_cluster",
            "blocked_cluster": candidate_cluster,
            "blocked_reason": (
                f"命中系统锁簇：`{candidate_cluster}` 仍在冷却中，"
                f"剩余 {lock['remaining_rounds']} 轮。"
            ),
            "lock_applied": False,
            "lock_rounds": 0,
            "lock_level": int(lock.get("trigger_count", 0) or 0),
            "lock_trigger_iteration": int(lock.get("lock_trigger_iteration", 0) or 0),
            "lock_expires_before_iteration": int(lock.get("lock_expires_before_iteration", 0) or 0),
            "current_locks": tuple(
                f"{cluster}(剩余{payload['remaining_rounds']}轮)"
                for cluster, payload in sorted(
                    active_locks.items(),
                    key=lambda item: (-item[1]["remaining_rounds"], item[0]),
                )
            ),
        }

    low_change_context = state["low_change_context"]
    if low_change_context is None or candidate_cluster != low_change_context["cluster"]:
        return None
    if _candidate_has_structural_novelty(candidate_signature, low_change_context):
        return None

    history_counts = state["history_counts"]
    same_cluster_rounds = int(history_counts.get(candidate_cluster, {}).get("same_cluster_rounds", 0))
    lock_trigger_rounds = int(history_counts.get(candidate_cluster, {}).get("lock_trigger_rounds", 0))
    current_round_count = same_cluster_rounds + 1
    lock_applied = current_round_count >= 2
    lock_level = lock_trigger_rounds + 1 if lock_applied else lock_trigger_rounds
    source_kind = str(low_change_context.get("source_kind", "")).strip()
    if source_kind == "noop":
        source_label = "连续行为无变化"
    elif source_kind == "repeated_basin":
        source_label = "同一结果盆地反复回落"
    else:
        source_label = "低变化打转"
    if lock_applied:
        schedule_index = min(max(lock_level - 1, 0), max(len(lock_schedule) - 1, 0))
        lock_rounds = int(lock_schedule[schedule_index])
        lock_expires_before_iteration = current_iteration + lock_rounds + 1
        blocked_reason = (
            f"同簇低变化近邻：`{candidate_cluster}` 最近持续{source_label}，"
            f"当前候选未切出新交易路径；该簇将冷却 {lock_rounds} 轮。"
        )
    else:
        lock_rounds = 0
        lock_expires_before_iteration = 0
        blocked_reason = (
            f"同簇低变化近邻：`{candidate_cluster}` 最近持续{source_label}，"
            "当前候选未切换方向簇，也没有形成足够明确的结构性换方向。"
        )

    return {
        "block_kind": "same_cluster",
        "stop_stage": "blocked_same_cluster",
        "blocked_cluster": candidate_cluster,
        "blocked_reason": blocked_reason,
        "lock_applied": lock_applied,
        "lock_rounds": lock_rounds,
        "lock_level": lock_level,
        "lock_trigger_iteration": current_iteration if lock_applied else 0,
        "lock_expires_before_iteration": lock_expires_before_iteration,
        "low_change_cluster": candidate_cluster,
        "low_change_tags": tuple(sorted(low_change_context["tag_union"])),
        "low_change_regions": tuple(sorted(low_change_context["ordinary_region_families"])),
        "low_change_changed_regions": tuple(sorted(low_change_context["ordinary_changed_regions"])),
        "low_change_targets": tuple(sorted(low_change_context["target_families"])),
        "low_change_factors": tuple(sorted(low_change_context["core_factor_names"])),
        "low_change_param_families": tuple(sorted(low_change_context["param_families"])),
        "low_change_structural_tokens": tuple(sorted(low_change_context["structural_tokens"])),
        "current_locks": tuple(
            f"{cluster}(剩余{payload['remaining_rounds']}轮)"
            for cluster, payload in sorted(
                active_locks.items(),
                key=lambda item: (-item[1]["remaining_rounds"], item[0]),
            )
        ),
    }


def _direction_risk_board(entries: list[dict[str, Any]], limit: int) -> list[str]:
    entries = _strategy_relevant_entries(entries)
    if not entries:
        return []

    buckets: dict[str, dict[str, Any]] = {}
    for entry in entries:
        cluster = cluster_key_for_entry(entry)
        outcome = _outcome_bucket(str(entry.get("outcome", "")))
        promotion_delta = _score_value(entry.get("promotion_delta"))
        bucket = buckets.setdefault(
            cluster,
            {
                "attempts": 0,
                "failures": 0,
                "zero_delta": 0,
                "runtime_errors": 0,
                "best_delta": 0.0,
                "tags": [],
            },
        )
        bucket["attempts"] += 1
        if outcome == "rejected":
            bucket["failures"] += 1
        if abs(promotion_delta) <= 1e-9:
            bucket["zero_delta"] += 1
        if str(entry.get("outcome", "")) == "runtime_failed":
            bucket["runtime_errors"] += 1
        bucket["best_delta"] = max(bucket["best_delta"], promotion_delta)
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text and tag_text not in bucket["tags"]:
                bucket["tags"].append(tag_text)

    ordered = sorted(
        buckets.items(),
        key=lambda item: (
            {"EXHAUSTED": 0, "SATURATED": 1, "RUNTIME_RISK": 2, "WARM": 3, "ACTIVE_WINNER": 4, "OPEN": 5}[
                _risk_label(
                    attempts=int(item[1]["attempts"]),
                    failures=int(item[1]["failures"]),
                    zero_delta=int(item[1]["zero_delta"]),
                    runtime_errors=int(item[1]["runtime_errors"]),
                    best_delta=float(item[1]["best_delta"]),
                )
            ],
            -int(item[1]["attempts"]),
            -int(item[1]["failures"]),
        ),
    )

    lines = [
        "方向风险表（必须先读）:",
        "| 方向簇 | 最近尝试 | 失败 | 零增益 | 运行报错 | 最佳delta | 标签 | 最近标签 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cluster, stats in ordered[:limit]:
        label = _risk_label(
            attempts=int(stats["attempts"]),
            failures=int(stats["failures"]),
            zero_delta=int(stats["zero_delta"]),
            runtime_errors=int(stats["runtime_errors"]),
            best_delta=float(stats["best_delta"]),
        )
        tags = _truncate(",".join(stats["tags"][:4]) or "-", 48)
        lines.append(
            f"| {cluster} | {stats['attempts']} | {stats['failures']} | {stats['zero_delta']} | "
            f"{stats['runtime_errors']} | {stats['best_delta']:.2f} | {label} | {tags} |"
        )
    return lines


def _direction_cooling_board(
    entries: list[dict[str, Any]],
    limit: int,
    *,
    journal_path: Path | None,
    score_regime: str,
    current_iteration: int,
) -> list[str]:
    state = build_exploration_guard_state(
        entries,
        journal_path=journal_path,
        score_regime=score_regime,
        current_iteration=current_iteration,
        include_current_round_locks=False,
    )
    active_locks = state["active_locks"]
    lines = [
        "方向冷却表（系统硬约束）:",
        "| 方向簇 | 状态 | 剩余锁定轮次 | 触发次数 | 最近原因 |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not active_locks:
        lines.append("| - | OPEN | 0 | 0 | 暂无被系统锁定的方向簇 |")
        return lines

    ordered = sorted(
        active_locks.items(),
        key=lambda item: (-item[1]["remaining_rounds"], -item[1]["trigger_count"], item[0]),
    )
    for cluster, payload in ordered[:limit]:
        lines.append(
            f"| {cluster} | COOLING | {payload['remaining_rounds']} | "
            f"{payload['trigger_count']} | {_truncate(payload['reason'], 48) or '-'} |"
        )
    return lines


def _overfit_risk_board(entries: list[dict[str, Any]], limit: int) -> list[str]:
    flagged_rows: list[tuple[float, str]] = []
    lines = [
        "过拟合风险表（谨慎参考）:",
        "| 轮次 | 结果 | promotion | 风险 | 分数 | top1+ | chain+ | 覆盖 | 多空偏科 | 落差 | 建议 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        risk_score = _metric_from_entry(entry, "overfit_risk_score")
        hard_fail = _metric_from_entry(entry, "overfit_hard_fail") > 0.5
        if not hard_fail and risk_score < OVERFIT_WARN_SCORE:
            continue
        round_label = entry.get("iteration")
        if round_label in (None, "", 0):
            round_label = "-"
        outcome = _display_outcome(str(entry.get("outcome", "")))
        promotion = _format_metric(entry.get("promotion_score"))
        level = "严重" if hard_fail else overfit_risk_level_from_score(risk_score)
        action = overfit_reference_action(risk_score, hard_fail)
        sort_key = f"| {round_label} | {outcome} | {promotion} | {level} | {risk_score:.0f} | " \
            f"{_metric_from_entry(entry, 'overfit_top1_positive_share'):.0%} | " \
            f"{_metric_from_entry(entry, 'overfit_chain_positive_share'):.0%} | " \
            f"{_metric_from_entry(entry, 'overfit_coverage_ratio'):.0%} | " \
            f"{_metric_from_entry(entry, 'overfit_bull_bear_gap'):.2f} | " \
            f"{_metric_from_entry(entry, 'overfit_capture_drop_abs'):.2f} | {action} |"
        flagged_rows.append((risk_score + (1000.0 if hard_fail else 0.0), sort_key))

    if not flagged_rows:
        lines.append("| - | - | - | 低 | 0 | - | - | - | - | - | 最近未发现需要降权的高风险轮次 |")
        return lines

    for _, row in sorted(flagged_rows, key=lambda item: item[0], reverse=True)[:limit]:
        lines.append(row)
    return lines


def _metric_from_entry(entry: dict[str, Any], key: str) -> float:
    metrics = entry.get("metrics", {})
    if isinstance(metrics, dict):
        return _score_value(metrics.get(key))
    return 0.0


def _has_metric(entry: dict[str, Any], key: str) -> bool:
    metrics = entry.get("metrics", {})
    return isinstance(metrics, dict) and key in metrics


def _span(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def _low_change_tail(entries: list[dict[str, Any]], streak: int = LOW_CHANGE_STREAK) -> list[dict[str, Any]]:
    eligible = [
        entry for entry in entries
        if str(entry.get("outcome", "")) in {"accepted", "rejected"}
    ]
    if len(eligible) < streak:
        return []
    tail = eligible[-streak:]
    if any(abs(_score_value(entry.get("promotion_delta"))) > LOW_CHANGE_PROMOTION_DELTA_EPS for entry in tail):
        return []
    if _span([_score_value(entry.get("promotion_score")) for entry in tail]) > LOW_CHANGE_PROMOTION_SCORE_SPAN:
        return []
    if _span([_score_value(entry.get("quality_score")) for entry in tail]) > LOW_CHANGE_QUALITY_SCORE_SPAN:
        return []
    if _span([_metric_from_entry(entry, "combined_trend_capture_score") for entry in tail]) > LOW_CHANGE_TREND_SCORE_SPAN:
        return []
    if _span([_metric_from_entry(entry, "segment_hit_rate") for entry in tail]) > LOW_CHANGE_HIT_RATE_SPAN:
        return []
    if _span([_metric_from_entry(entry, "bull_capture_score") for entry in tail]) > LOW_CHANGE_SIDE_CAPTURE_SPAN:
        return []
    if _span([_metric_from_entry(entry, "bear_capture_score") for entry in tail]) > LOW_CHANGE_SIDE_CAPTURE_SPAN:
        return []
    if _span([_metric_from_entry(entry, "total_trades") for entry in tail]) > LOW_CHANGE_TOTAL_TRADES_SPAN:
        return []
    if _span([_metric_from_entry(entry, "avg_fee_drag") for entry in tail]) > LOW_CHANGE_FEE_DRAG_SPAN:
        return []
    return tail


def _repeated_result_basin_tail(entries: list[dict[str, Any]], streak: int = LOW_CHANGE_STREAK) -> list[dict[str, Any]]:
    eligible = [
        entry for entry in entries
        if result_basin_key_for_entry(entry)
    ]
    if len(eligible) < streak:
        return []
    tail = eligible[-streak:]
    basin_keys = [result_basin_key_for_entry(entry) for entry in tail]
    if basin_keys and len(set(basin_keys)) == 1:
        return tail
    return []


def _noop_tail(entries: list[dict[str, Any]], streak: int = LOW_CHANGE_STREAK) -> list[dict[str, Any]]:
    if len(entries) < streak:
        return []
    tail = entries[-streak:]
    if all(str(entry.get("outcome", "")) in {"duplicate_skipped", "behavioral_noop"} for entry in tail):
        return tail
    if all(str(entry.get("stop_stage", "")) in NO_OP_STOP_STAGES for entry in tail):
        return tail
    return []


def _exploration_trigger_lines(entries: list[dict[str, Any]], limit: int) -> list[str]:
    entries = _strategy_relevant_entries(entries)
    noop_tail = _noop_tail(entries)
    if noop_tail:
        clusters: list[str] = []
        tags: list[str] = []
        regions: list[str] = []
        reasons: list[str] = []
        for entry in noop_tail:
            cluster = cluster_key_for_entry(entry)
            if cluster and cluster not in clusters:
                clusters.append(cluster)
            for tag in entry.get("change_tags", []):
                tag_text = str(tag).strip()
                if tag_text and tag_text not in tags:
                    tags.append(tag_text)
            for region in _entry_changed_regions(entry):
                region_text = str(region).strip()
                if region_text and region_text not in regions:
                    regions.append(region_text)
            reason_text = _entry_decision_reason(entry)
            if reason_text and reason_text not in reasons:
                reasons.append(reason_text)

        return [
            "探索触发（必须执行）:",
            f"最近 {LOW_CHANGE_STREAK} 轮都没有产生有效代码改动或 smoke 交易行为变化，已按重复探索记入历史。",
            f"- 重复原因：{'；'.join(reasons[:limit]) or '-'}",
            f"- 近期近邻方向簇：{', '.join(clusters[:limit]) or '-'}；标签：{', '.join(tags[:limit]) or '-'}；高频区域：{', '.join(regions[:limit]) or '-'}",
            "- 下一轮必须先产出有效 diff，并且必须改变 smoke 窗口的实际交易路径；若继续沿用相近方向，至少切换方向簇、普通 family、long-short target 中的一项。",
        ]

    repeated_basin_tail = _repeated_result_basin_tail(entries)
    if repeated_basin_tail:
        clusters: list[str] = []
        tags: list[str] = []
        regions: list[str] = []
        reasons: list[str] = []
        for entry in repeated_basin_tail:
            cluster = cluster_key_for_entry(entry)
            if cluster and cluster not in clusters:
                clusters.append(cluster)
            for tag in entry.get("change_tags", []):
                tag_text = str(tag).strip()
                if tag_text and tag_text not in tags:
                    tags.append(tag_text)
            for region in _entry_changed_regions(entry):
                region_text = str(region).strip()
                if region_text and region_text not in regions:
                    regions.append(region_text)
            reason_text = _entry_decision_reason(entry)
            if reason_text and reason_text not in reasons:
                reasons.append(reason_text)

        return [
            "探索触发（必须执行）:",
            f"最近 {LOW_CHANGE_STREAK} 轮反复落回同一结果盆地：分数、命中率、弱侧捕获和 gate 原因几乎一致，不应继续把它们当成独立新证据。",
            f"- 结果盆地原因：{'；'.join(reasons[:limit]) or '-'}",
            f"- 近期近邻方向簇：{', '.join(clusters[:limit]) or '-'}；标签：{', '.join(tags[:limit]) or '-'}；高频区域：{', '.join(regions[:limit]) or '-'}",
            "- 下一轮必须优先切不同方向簇；若仍留在同簇，必须切不同 choke point 或不同最终放行链，而不是继续换候选名、tag 或措辞。",
        ]

    tail = _low_change_tail(entries)
    if not tail:
        return []

    clusters: list[str] = []
    tags: list[str] = []
    regions: list[str] = []
    for entry in tail:
        cluster = cluster_key_for_entry(entry)
        if cluster and cluster not in clusters:
            clusters.append(cluster)
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text and tag_text not in tags:
                tags.append(tag_text)
        for region in _entry_changed_regions(entry):
            region_text = str(region).strip()
            if region_text and region_text not in regions:
                regions.append(region_text)

    lines = [
        "探索触发（必须执行）:",
        f"最近 {LOW_CHANGE_STREAK} 轮都属于低变化轮次：晋级分没有实质提升，且 trend_score / hit_rate / bull_bear_capture / fee_drag 基本不变。",
        f"- 近期近邻方向簇：{', '.join(clusters[:limit]) or '-'}；标签：{', '.join(tags[:limit]) or '-'}；高频区域：{', '.join(regions[:limit]) or '-'}",
        "- 下一轮必须把它当作探索轮：优先切簇；若无法切簇，至少切换普通 family、long-short target 或真实交易路径层级中的一项。",
        "- 目标是让 segment_hit_rate、bull_capture_score、bear_capture_score、avg_fee_drag、total_trades 中至少两项明显变化。",
    ]
    return lines


def _cluster_overheat_lines(entries: list[dict[str, Any]], limit: int) -> list[str]:
    eligible = [
        entry for entry in entries
        if _outcome_bucket(str(entry.get("outcome", ""))) in {"accepted", "rejected"}
    ]
    recent = eligible[-CLUSTER_OVERHEAT_LOOKBACK:]
    if len(recent) < CLUSTER_OVERHEAT_MIN_ROUNDS:
        return []

    cluster_counts = Counter(
        cluster_key_for_entry(entry)
        for entry in recent
        if cluster_key_for_entry(entry)
    )
    if not cluster_counts:
        return []

    hot_cluster, hot_count = cluster_counts.most_common(1)[0]
    hot_share = hot_count / len(recent)
    hot_entries = [
        entry for entry in recent
        if cluster_key_for_entry(entry) == hot_cluster
    ]
    best_delta = max((_score_value(entry.get("promotion_delta")) for entry in hot_entries), default=0.0)
    if hot_share < CLUSTER_OVERHEAT_SHARE or best_delta > LOW_CHANGE_PROMOTION_DELTA_EPS:
        return []

    tags: list[str] = []
    regions: list[str] = []
    for entry in hot_entries:
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text and tag_text not in tags:
                tags.append(tag_text)
        for region in _entry_changed_regions(entry):
            region_text = str(region).strip()
            if region_text and region_text not in regions:
                regions.append(region_text)

    return [
        "主簇过热（必须先读）:",
        f"最近 {len(recent)} 轮里，`{hot_cluster}` 占比 {hot_share:.0%}（{hot_count}/{len(recent)}），且最佳 promotion_delta 仅 {best_delta:.2f}，继续留在该簇大概率仍是近邻试错。",
        f"- 过热方向簇：{hot_cluster}",
        f"- 近期近邻标签：{', '.join(tags[:limit]) or '-'}；高频区域：{', '.join(regions[:limit]) or '-'}",
        "- 下一轮默认应切到不同方向簇；若仍留在该簇，必须证明这次会改变不同交易路径。",
    ]


# ==================== 摘要生成 ====================


def _entry_iteration(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("iteration", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_timestamp(value: Any) -> datetime | None:
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


def _partition_entries_by_stage(
    entries: list[dict[str, Any]],
    *,
    score_regime: str,
    active_stage_started_at: str = "",
    active_stage_iteration: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    current_entries = _entries_for_score_regime(entries, score_regime)
    if not current_entries:
        return [], [], {"stage_iteration": 0, "stage_started_at": str(active_stage_started_at or "").strip()}

    boundary_iteration = max(0, int(active_stage_iteration or 0))
    stage_started_at = str(active_stage_started_at or "").strip()
    stage_started_at_dt = _parse_timestamp(stage_started_at) if stage_started_at else None
    if stage_started_at_dt is not None:
        timestamp_partition = [
            (entry, _parse_timestamp(entry.get("timestamp")))
            for entry in current_entries
        ]
        timed_current_entries = [
            entry
            for entry, entry_ts in timestamp_partition
            if entry_ts is not None and entry_ts >= stage_started_at_dt
        ]
        if timed_current_entries:
            boundary_iteration = next(
                (_entry_iteration(entry) for entry in timed_current_entries if _entry_iteration(entry) > 0),
                0,
            )
            past_stage_entries: list[dict[str, Any]] = []
            current_stage_entries: list[dict[str, Any]] = []
            for entry, entry_ts in timestamp_partition:
                entry_iteration = _entry_iteration(entry)
                is_current = False
                if entry_ts is not None:
                    is_current = entry_ts >= stage_started_at_dt
                elif boundary_iteration > 0 and entry_iteration > 0:
                    is_current = entry_iteration >= boundary_iteration
                if is_current:
                    current_stage_entries.append(entry)
                else:
                    past_stage_entries.append(entry)
            return current_stage_entries, past_stage_entries, {
                "stage_iteration": boundary_iteration,
                "stage_started_at": stage_started_at,
            }

    if boundary_iteration <= 0 and stage_started_at:
        if stage_started_at_dt is not None:
            for entry in current_entries:
                entry_ts = _parse_timestamp(entry.get("timestamp"))
                entry_iteration = _entry_iteration(entry)
                if entry_ts is not None and entry_iteration > 0 and entry_ts >= stage_started_at_dt:
                    boundary_iteration = entry_iteration
                    break
            if boundary_iteration <= 0:
                boundary_iteration = max((_entry_iteration(entry) for entry in current_entries), default=0) + 1

    if boundary_iteration <= 0:
        boundary_iteration = next((value for value in (_entry_iteration(entry) for entry in current_entries) if value > 0), 0)

    past_stage_entries: list[dict[str, Any]] = []
    current_stage_entries: list[dict[str, Any]] = []
    for entry in current_entries:
        entry_iteration = _entry_iteration(entry)
        if boundary_iteration > 0 and entry_iteration > 0 and entry_iteration < boundary_iteration:
            past_stage_entries.append(entry)
        else:
            current_stage_entries.append(entry)

    return current_stage_entries, past_stage_entries, {
        "stage_iteration": boundary_iteration,
        "stage_started_at": stage_started_at,
    }


def _group_entries_into_stages(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    stages: list[list[dict[str, Any]]] = []
    current_stage: list[dict[str, Any]] = []
    for entry in entries:
        if current_stage and str(entry.get("outcome", "")) == "accepted":
            stages.append(current_stage)
            current_stage = [entry]
            continue
        current_stage.append(entry)
    if current_stage:
        stages.append(current_stage)
    return stages


def _format_optional_metric(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _weak_side_from_reference_metrics(reference_metrics: dict[str, Any] | None) -> str:
    if not isinstance(reference_metrics, dict):
        return ""
    bull_score = _score_value(reference_metrics.get("validation_bull_capture_score"))
    bear_score = _score_value(reference_metrics.get("validation_bear_capture_score"))
    if bull_score < bear_score:
        return "long"
    if bear_score < bull_score:
        return "short"
    return ""


def _entry_smoke_passed(entry: dict[str, Any]) -> bool:
    if "smoke_passed" in entry:
        return bool(entry.get("smoke_passed"))
    outcome = str(entry.get("outcome", "")).strip()
    if outcome in {"accepted", "rejected", "behavioral_noop", "early_rejected"}:
        return True
    if outcome == "runtime_failed":
        return str(entry.get("runtime_failure_stage", "")).strip() == "full_eval"
    return False


def _entry_full_eval_reached(entry: dict[str, Any]) -> bool:
    if "full_eval_reached" in entry:
        return bool(entry.get("full_eval_reached"))
    outcome = str(entry.get("outcome", "")).strip()
    if outcome in {"accepted", "rejected", "early_rejected"}:
        return True
    return outcome == "runtime_failed" and str(entry.get("runtime_failure_stage", "")).strip() == "full_eval"


def _stage_operating_metrics(
    stage_entries: list[dict[str, Any]],
    *,
    reference_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator > 0 else 0.0

    entry_count = len(stage_entries)
    accepted_count = sum(1 for entry in stage_entries if str(entry.get("outcome", "")) == "accepted")
    behavioral_noop_count = sum(1 for entry in stage_entries if str(entry.get("outcome", "")) == "behavioral_noop")
    exploration_blocked_count = sum(1 for entry in stage_entries if str(entry.get("outcome", "")) == "exploration_blocked")
    smoke_passed_count = sum(1 for entry in stage_entries if _entry_smoke_passed(entry))
    full_eval_reached_count = sum(1 for entry in stage_entries if _entry_full_eval_reached(entry))
    ordinary_family_counts = [
        len(
            [
                item
                for item in (entry.get("system_ordinary_region_families", []) or entry.get("ordinary_region_families", []))
                if str(item).strip()
            ]
        )
        for entry in stage_entries
    ]
    weak_side = _weak_side_from_reference_metrics(reference_metrics)
    weak_side_attempts = 0
    if weak_side:
        for entry in stage_entries:
            target_family = str(entry.get("target_family", "")).strip()
            if weak_side == "long" and target_family in {"long", "mixed"}:
                weak_side_attempts += 1
            elif weak_side == "short" and target_family in {"short", "mixed"}:
                weak_side_attempts += 1
    return {
        "entry_count": entry_count,
        "accept_rate": _ratio(accepted_count, entry_count),
        "behavioral_noop_rate": _ratio(behavioral_noop_count, entry_count),
        "exploration_blocked_rate": _ratio(exploration_blocked_count, entry_count),
        "smoke_to_full_eval_rate": _ratio(full_eval_reached_count, smoke_passed_count),
        "avg_ordinary_family_count": _mean([float(value) for value in ordinary_family_counts]) if ordinary_family_counts else 0.0,
        "weak_side": weak_side,
        "weak_side_share": _ratio(weak_side_attempts, entry_count),
    }


def _format_stage_operating_metrics(
    metrics: dict[str, Any],
    *,
    stage_name: str,
) -> list[str]:
    weak_side = str(metrics.get("weak_side", "")).strip()
    weak_side_label = {"long": "弱侧(long)", "short": "弱侧(short)"}.get(weak_side, "弱侧")
    weak_side_share = (
        f"{_score_value(metrics.get('weak_side_share')):.0%}"
        if weak_side else "-"
    )
    return [
        f"{stage_name} 运营指标表:",
        "| accept rate | behavioral_noop rate | exploration_blocked rate | smoke->full_eval | 平均改动 ordinary families | 弱侧探索占比 |",
        "| --- | --- | --- | --- | --- | --- |",
        (
            f"| {_score_value(metrics.get('accept_rate')):.0%} | "
            f"{_score_value(metrics.get('behavioral_noop_rate')):.0%} | "
            f"{_score_value(metrics.get('exploration_blocked_rate')):.0%} | "
            f"{_score_value(metrics.get('smoke_to_full_eval_rate')):.0%} | "
            f"{_score_value(metrics.get('avg_ordinary_family_count')):.2f} | "
            f"{weak_side_label} {weak_side_share} |"
        ),
    ]


def _entry_ordinary_region_families(entry: dict[str, Any]) -> tuple[str, ...]:
    stored = entry.get("system_ordinary_region_families")
    if isinstance(stored, (list, tuple, set)):
        return ordinary_region_families(stored)
    return ordinary_region_families(_entry_region_families(entry))


def _repeated_result_basin_entry_count(entries: list[dict[str, Any]]) -> int:
    basin_counts = Counter(
        basin_key
        for basin_key in (result_basin_key_for_entry(entry) for entry in entries)
        if basin_key
    )
    return sum(
        1
        for entry in entries
        if basin_counts.get(result_basin_key_for_entry(entry), 0) >= 2
    )


def _top_counter_labels(counter: Counter[str], limit: int) -> str:
    labels = [name for name, _ in counter.most_common(max(1, limit)) if str(name).strip()]
    return ", ".join(labels) or "-"


def _failure_nucleus_payloads(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if str(entry.get("outcome", "")).strip() not in {"rejected", "early_rejected"}:
            continue
        reason = _entry_decision_reason(entry)
        if not reason or reason == "-":
            continue
        payload = grouped.setdefault(
            reason,
            {
                "reason": reason,
                "count": 0,
                "last_iteration": 0,
                "clusters": Counter(),
                "families": Counter(),
                "targets": Counter(),
                "tags": Counter(),
            },
        )
        payload["count"] += 1
        payload["last_iteration"] = max(payload["last_iteration"], _entry_iteration(entry))
        payload["clusters"][cluster_key_for_entry(entry) or "unclassified"] += 1
        for family in _entry_ordinary_region_families(entry):
            payload["families"][family] += 1
        target_family = str(entry.get("target_family", "")).strip()
        if target_family:
            payload["targets"][target_family] += 1
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text:
                payload["tags"][tag_text] += 1
    return sorted(
        grouped.values(),
        key=lambda item: (-int(item["count"]), -int(item["last_iteration"]), str(item["reason"])),
    )


def _stage_failure_nucleus_lines(entries: list[dict[str, Any]], limit: int) -> list[str]:
    nuclei = [payload for payload in _failure_nucleus_payloads(entries) if int(payload["count"]) >= 2]
    if not nuclei:
        return []

    lines = ["当前 stage 失败核聚合（去重后再看）:"]
    for index, payload in enumerate(nuclei[: max(1, limit)], start=1):
        lines.append(
            f"- 核 {index} | {payload['count']} 轮 | "
            f"gate={_truncate(payload['reason'], 56)} | "
            f"簇={_top_counter_labels(payload['clusters'], 2)} | "
            f"ordinary family={_top_counter_labels(payload['families'], 2)} | "
            f"目标={_top_counter_labels(payload['targets'], 2)} | "
            f"标签={_top_counter_labels(payload['tags'], 4)}"
        )
    lines.append("- 同一失败核重复出现时，应把它视为同一个已知坏盆地，而不是多条独立新证据。")
    return lines


def _stage_executive_summary_lines(
    entries: list[dict[str, Any]],
    *,
    stage_name: str,
    reference_metrics: dict[str, Any] | None = None,
    limit: int = 6,
) -> list[str]:
    entries = _strategy_relevant_entries(entries)
    if not entries:
        return []

    weak_side = _weak_side_from_reference_metrics(reference_metrics)
    target_text = {
        "long": "当前弱侧是 long；默认优先补多头捕获/命中率，但不是硬锁只看多头。",
        "short": "当前弱侧是 short；默认优先补空头捕获/命中率，但不是硬锁只看空头。",
    }.get(weak_side, "当前多空没有明显弱侧，默认先看最影响 gate 的主短板。")

    accepted_count = sum(1 for entry in entries if str(entry.get("outcome", "")).strip() == "accepted")
    rejected_count = sum(1 for entry in entries if str(entry.get("outcome", "")).strip() == "rejected")
    full_eval_count = sum(1 for entry in entries if _entry_full_eval_reached(entry))
    recent_entries = entries[-max(1, limit):]

    cluster_counter: Counter[str] = Counter()
    family_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    for entry in recent_entries:
        cluster_counter[cluster_key_for_entry(entry) or "unclassified"] += 1
        for family in _entry_ordinary_region_families(entry):
            family_counter[family] += 1
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text:
                tag_counter[tag_text] += 1

    nuclei = _failure_nucleus_payloads(entries)
    repeated_nucleus = next((payload for payload in nuclei if int(payload["count"]) >= 2), None)

    lines = [f"{stage_name} 执行摘要（先看）:"]
    lines.append(f"- 当前目标: {target_text}")
    lines.append(
        f"- 当前状态: 本 stage 共 {len(entries)} 条，完整评估 {full_eval_count} 条，"
        f"保留 {accepted_count} 条，未保留 {rejected_count} 条。"
    )
    if repeated_nucleus is not None:
        lines.append(
            f"- 重复失败核: 最近 {repeated_nucleus['count']} 轮都落在 "
            f"`{_truncate(repeated_nucleus['reason'], 64)}` 这一核，不要把它当成多条独立新证据。"
        )
    else:
        lines.append("- 重复失败核: 当前还没有形成 2 轮以上的同核失败。")
    lines.append(
        f"- 当前近邻热点: 簇={_top_counter_labels(cluster_counter, 2)}；"
        f"ordinary family={_top_counter_labels(family_counter, 2)}；"
        f"标签={_top_counter_labels(tag_counter, 4)}"
    )
    lines.append("- 阅读建议: 先看这里和失败核聚合，再把下面的风险表、逐轮表格当附录。")
    return lines


def _summarize_stage_entries(stage_entries: list[dict[str, Any]], *, stage_id: int) -> dict[str, Any]:
    stage_entries = _strategy_relevant_entries(stage_entries)
    if not stage_entries:
        return {}

    cluster_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    target_counter: Counter[str] = Counter()
    failure_reason_counter: Counter[str] = Counter()
    best_promotion: float | None = None
    best_delta = 0.0
    accepted_count = 0
    rejected_count = 0
    behavioral_noop_count = 0
    runtime_failed_count = 0
    exploration_blocked_count = 0

    for entry in stage_entries:
        cluster_counter[cluster_key_for_entry(entry) or "unclassified"] += 1
        for tag in entry.get("change_tags", []):
            tag_counter[str(tag)] += 1
        target_family = str(entry.get("target_family", "")).strip()
        if target_family:
            target_counter[target_family] += 1

        outcome = str(entry.get("outcome", "")).strip()
        if outcome == "accepted":
            accepted_count += 1
        if outcome == "behavioral_noop":
            behavioral_noop_count += 1
        if outcome == "runtime_failed":
            runtime_failed_count += 1
        if outcome == "exploration_blocked":
            exploration_blocked_count += 1
        if _outcome_bucket(outcome) == "rejected":
            rejected_count += 1
            reason = _entry_decision_reason(entry)
            if reason and reason != "-":
                failure_reason_counter[reason] += 1

        promotion_score = entry.get("promotion_score")
        if promotion_score is not None:
            score = _score_value(promotion_score)
            best_promotion = score if best_promotion is None else max(best_promotion, score)
        best_delta = max(best_delta, _score_value(entry.get("promotion_delta")))

    first_entry = stage_entries[0]
    last_entry = stage_entries[-1]
    activation_kind = "accepted" if str(first_entry.get("outcome", "")) == "accepted" else "bootstrap"
    return {
        "stage_id": stage_id,
        "start_iteration": _entry_iteration(first_entry),
        "end_iteration": _entry_iteration(last_entry),
        "activation_kind": activation_kind,
        "activation_timestamp": str(first_entry.get("timestamp", "")).strip(),
        "entry_count": len(stage_entries),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "behavioral_noop_count": behavioral_noop_count,
        "runtime_failed_count": runtime_failed_count,
        "exploration_blocked_count": exploration_blocked_count,
        "best_promotion": best_promotion,
        "best_delta": best_delta,
        "dominant_clusters": [name for name, _ in cluster_counter.most_common(2)],
        "dominant_tags": [name for name, _ in tag_counter.most_common(3)],
        "dominant_targets": [name for name, _ in target_counter.most_common(2)],
        "top_failure_reasons": [name for name, _ in failure_reason_counter.most_common(2)],
    }


def _format_past_stage_summary_lines(stage_summaries: list[dict[str, Any]], limit: int) -> list[str]:
    if not stage_summaries:
        return []

    display_count = min(max(1, limit), len(stage_summaries))
    display_items = stage_summaries[-display_count:]
    lines = ["历史 stage 摘要（压缩）:"]
    if display_count < len(stage_summaries):
        lines.append(f"- 已压缩 {len(stage_summaries)} 个旧 stage，以下仅展示最近 {display_count} 个。")
    for stage in display_items:
        range_text = f"{stage['start_iteration']}~{stage['end_iteration']}"
        dominant_clusters = ", ".join(stage["dominant_clusters"]) or "-"
        dominant_tags = ", ".join(stage["dominant_tags"]) or "-"
        dominant_targets = ", ".join(stage["dominant_targets"]) or "-"
        top_failures = ", ".join(stage["top_failure_reasons"]) or "-"
        lines.append(
            f"- stage {stage['stage_id']} | 轮次 {range_text} | 激活方式 {stage['activation_kind']} | "
            f"总 {stage['entry_count']} / 保留 {stage['accepted_count']} / 未保留 {stage['rejected_count']} / "
            f"noop {stage['behavioral_noop_count']} / runtime {stage['runtime_failed_count']} | "
            f"best promotion {_format_optional_metric(stage['best_promotion'])} / best delta {stage['best_delta']:.2f} | "
            f"主簇 {dominant_clusters} | 主标签 {dominant_tags} | 目标 {dominant_targets} | 高频失败 {top_failures}"
        )
    return lines


def _all_time_tables_payload(entries: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    entries = _strategy_relevant_entries(entries)
    cluster_bucket: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "attempts": 0,
            "accepted": 0,
            "rejected": 0,
            "behavioral_noop": 0,
            "runtime_errors": 0,
            "zero_delta": 0,
            "best_delta": 0.0,
        }
    )
    failure_tags: Counter[str] = Counter()

    for entry in entries:
        cluster = cluster_key_for_entry(entry) or "unclassified"
        bucket = cluster_bucket[cluster]
        bucket["attempts"] += 1
        outcome = str(entry.get("outcome", "")).strip()
        if _outcome_bucket(outcome) == "accepted":
            bucket["accepted"] += 1
        if _outcome_bucket(outcome) == "rejected":
            bucket["rejected"] += 1
            for tag in entry.get("change_tags", []):
                failure_tags[str(tag)] += 1
        if outcome == "behavioral_noop":
            bucket["behavioral_noop"] += 1
        if outcome == "runtime_failed":
            bucket["runtime_errors"] += 1
        promotion_delta = _score_value(entry.get("promotion_delta"))
        if abs(promotion_delta) <= 1e-9:
            bucket["zero_delta"] += 1
        bucket["best_delta"] = max(bucket["best_delta"], promotion_delta)
    clusters = []
    for cluster_name, stats in cluster_bucket.items():
        label = _risk_label(
            attempts=int(stats["attempts"]),
            failures=int(stats["rejected"]),
            zero_delta=int(stats["zero_delta"]),
            runtime_errors=int(stats["runtime_errors"]),
            best_delta=float(stats["best_delta"]),
        )
        clusters.append(
            {
                "cluster": cluster_name,
                "attempts": int(stats["attempts"]),
                "accepted": int(stats["accepted"]),
                "rejected": int(stats["rejected"]),
                "behavioral_noop": int(stats["behavioral_noop"]),
                "best_delta": float(stats["best_delta"]),
                "label": label,
            }
        )
    clusters.sort(key=lambda item: (-item["attempts"], -item["rejected"], item["cluster"]))
    return {
        "clusters": clusters[: max(1, limit)],
        "failure_tags": [name for name, _ in failure_tags.most_common(max(1, limit))],
    }


def _format_all_time_tables(payload: dict[str, Any]) -> list[str]:
    clusters = list(payload.get("clusters", []))
    if not clusters:
        return []

    lines = [
        "全局方向统计（当前评分口径）:",
        "| 方向簇 | 尝试 | 保留 | 未保留 | noop | 最佳delta | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for cluster in clusters:
        lines.append(
            f"| {cluster['cluster']} | {cluster['attempts']} | {cluster['accepted']} | "
            f"{cluster['rejected']} | {cluster['behavioral_noop']} | {cluster['best_delta']:.2f} | {cluster['label']} |"
        )
    failure_tags = payload.get("failure_tags", [])
    if failure_tags:
        lines.append("")
        lines.append(f"全局高频失败标签: {', '.join(failure_tags)}")
    return lines


def _entry_identity_key(entry: dict[str, Any]) -> tuple[int, str, str]:
    return (
        _entry_iteration(entry),
        str(entry.get("candidate_id", "")).strip(),
        str(entry.get("timestamp", "")).strip(),
    )


def _failure_category_for_entry(entry: dict[str, Any]) -> str:
    if _entry_is_technical_generation_invalid(entry):
        return ""
    outcome = str(entry.get("outcome", "")).strip()
    stop_stage = str(entry.get("stop_stage", "")).strip()
    reason = _entry_decision_reason(entry).lower()
    if outcome == "behavioral_noop":
        return "BEHAVIORAL_NOOP"
    if outcome == "exploration_blocked":
        return "OVERUSED_DIRECTION"
    if outcome == "runtime_failed":
        if "complexity" in reason:
            return "COMPLEXITY_TRAP"
        if any(token in reason for token in ("editable", "boundary", "missing", "forbid", "region")):
            return "BOUNDARY_ERROR"
        return "RUNTIME_FAILURE"
    if outcome == "duplicate_skipped":
        if stop_stage == "duplicate_result_basin":
            return "FAILED_BASIN"
        if stop_stage in NO_OP_STOP_STAGES:
            return "OVERUSED_DIRECTION"
    if outcome in {"rejected", "early_rejected"}:
        return "FAILED_BASIN"
    return ""


def _failure_cut_payload_from_signature(signature: dict[str, Any]) -> dict[str, Any]:
    cluster = str(signature.get("cluster_key", "")).strip() or "unclassified"
    ordinary_families = sorted(str(item).strip() for item in signature.get("ordinary_region_families", set()) if str(item).strip())
    ordinary_changed_regions = sorted(
        str(item).strip()
        for item in signature.get("ordinary_changed_regions", set())
        if str(item).strip()
    )
    changed_regions = ordinary_changed_regions or sorted(
        str(item).strip()
        for item in signature.get("changed_regions", set())
        if str(item).strip()
    )
    param_families = sorted(
        str(item).strip()
        for item in signature.get("param_families", set())
        if str(item).strip()
    )
    target_family = str(signature.get("target_family", "")).strip() or "unknown"
    if not changed_regions and not ordinary_families and not param_families:
        return {}
    return {
        "cluster": cluster,
        "families": ordinary_families,
        "changed_regions": changed_regions,
        "target": target_family,
        "params": param_families,
    }


def failure_cut_key_for_signature(signature: dict[str, Any]) -> str:
    payload = _failure_cut_payload_from_signature(signature)
    if not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def failure_cut_key_for_entry(entry: dict[str, Any]) -> str:
    return failure_cut_key_for_signature(exploration_signature_for_entry(entry))


def _failure_wiki_block_reason(item: dict[str, Any]) -> str:
    category = str(item.get("category", "")).strip()
    count = int(item.get("count", 0) or 0)
    if category == "BEHAVIORAL_NOOP":
        return f"同一 exact cut 已连续 {count} 次未改变 smoke 真实交易路径。"
    if category == "OVERUSED_DIRECTION":
        return f"同一 exact cut 已连续 {count} 次落入系统探索拦截/重复空转。"
    if category == "COMPLEXITY_TRAP":
        return f"同一 exact cut 已连续 {count} 次撞上复杂度预算。"
    if category == "BOUNDARY_ERROR":
        return f"同一 exact cut 已连续 {count} 次触发边界或结构错误。"
    if category == "FAILED_BASIN":
        return f"同一 exact cut 已至少 {count} 次落回失败盆地，且没有产生正向 delta。"
    return "命中 failure wiki 已知失败 exact cut。"


def build_failure_wiki_payload(
    entries: list[dict[str, Any]],
    *,
    score_regime: str = "",
    current_stage_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scoped_entries = _entries_for_score_regime(entries, score_regime) if score_regime else list(entries)
    scoped_entries = _strategy_relevant_entries(scoped_entries)
    current_stage_key_set = {
        _entry_identity_key(entry)
        for entry in _strategy_relevant_entries(list(current_stage_entries or []))
    }
    grouped: dict[str, dict[str, Any]] = {}

    for entry in scoped_entries:
        category = _failure_category_for_entry(entry)
        if not category:
            continue
        signature = exploration_signature_for_entry(entry)
        cut_payload = _failure_cut_payload_from_signature(signature)
        if not cut_payload:
            continue
        cut_key = json.dumps(cut_payload, ensure_ascii=False, sort_keys=True)
        bucket_key = json.dumps(
            {"category": category, "cut_key": cut_key},
            ensure_ascii=False,
            sort_keys=True,
        )
        bucket = grouped.setdefault(
            bucket_key,
            {
                "category": category,
                "cut_key": cut_key,
                "cut": cut_payload,
                "count": 0,
                "stage_count": 0,
                "last_iteration": 0,
                "best_delta": 0.0,
                "reasons": Counter(),
                "stop_stages": Counter(),
                "tags": Counter(),
                "clusters": Counter(),
                "families": Counter(),
                "targets": Counter(),
                "examples": [],
            },
        )
        bucket["count"] += 1
        if _entry_identity_key(entry) in current_stage_key_set:
            bucket["stage_count"] += 1
        bucket["last_iteration"] = max(bucket["last_iteration"], _entry_iteration(entry))
        bucket["best_delta"] = max(bucket["best_delta"], _score_value(entry.get("promotion_delta")))
        reason = _entry_decision_reason(entry)
        if reason and reason != "-":
            bucket["reasons"][reason] += 1
        stop_stage = str(entry.get("stop_stage", "")).strip()
        if stop_stage:
            bucket["stop_stages"][stop_stage] += 1
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text:
                bucket["tags"][tag_text] += 1
        for family in signature.get("ordinary_region_families", set()):
            family_text = str(family).strip()
            if family_text:
                bucket["families"][family_text] += 1
        target_text = str(signature.get("target_family", "")).strip()
        if target_text:
            bucket["targets"][target_text] += 1
        cluster_text = str(signature.get("cluster_key", "")).strip() or "unclassified"
        bucket["clusters"][cluster_text] += 1
        bucket["examples"].append(
            {
                "iteration": _entry_iteration(entry),
                "candidate_id": str(entry.get("candidate_id", "")).strip() or "-",
                "reason": reason or "-",
                "outcome": str(entry.get("outcome", "")).strip() or "-",
            }
        )

    items: list[dict[str, Any]] = []
    blocked_cuts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in grouped.values():
        category = str(payload["category"]).strip()
        count = int(payload["count"])
        best_delta = float(payload["best_delta"])
        block_exact_cut = False
        if category in {"BEHAVIORAL_NOOP", "OVERUSED_DIRECTION", "COMPLEXITY_TRAP", "BOUNDARY_ERROR"}:
            block_exact_cut = count >= 2
        elif category == "FAILED_BASIN":
            block_exact_cut = count >= 2 and best_delta <= 0.0

        item = {
            "category": category,
            "cut_key": str(payload["cut_key"]),
            "cut": payload["cut"],
            "count": count,
            "stage_count": int(payload["stage_count"]),
            "last_iteration": int(payload["last_iteration"]),
            "best_delta": best_delta,
            "top_reasons": [name for name, _ in payload["reasons"].most_common(3)],
            "stop_stages": [name for name, _ in payload["stop_stages"].most_common(3)],
            "top_tags": [name for name, _ in payload["tags"].most_common(4)],
            "top_clusters": [name for name, _ in payload["clusters"].most_common(2)],
            "top_families": [name for name, _ in payload["families"].most_common(3)],
            "top_targets": [name for name, _ in payload["targets"].most_common(2)],
            "examples": sorted(payload["examples"], key=lambda row: row["iteration"], reverse=True)[:3],
            "block_exact_cut": block_exact_cut,
            "block_reason": _failure_wiki_block_reason(payload) if block_exact_cut else "",
        }
        items.append(item)
    items.sort(
        key=lambda item: (
            0 if item["block_exact_cut"] else 1,
            -int(item["count"]),
            -int(item["stage_count"]),
            -int(item["last_iteration"]),
            str(item["category"]),
        )
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "score_regime": score_regime,
        "entry_count": len(scoped_entries),
        "failure_item_count": len(items),
        "blocked_cut_count": sum(len(payloads) for payloads in blocked_cuts.values()),
        "blocked_cuts": dict(blocked_cuts),
        "items": items,
    }


def format_failure_wiki_markdown(payload: dict[str, Any], *, limit: int = 24) -> str:
    items = list(payload.get("items", []))
    highlighted_rows = [item for item in items if item.get("block_exact_cut")]
    highlighted_rows.sort(key=lambda item: (-int(item.get("count", 0) or 0), -int(item.get("last_iteration", 0) or 0)))

    lines = [
        f"# Failure Wiki ({str(payload.get('score_regime', '')).strip() or 'current'})",
        "",
        "用法：",
        "- 先看“高风险 exact cuts（仅提示）”。命中时优先切不同 choke point、不同最终放行链或不同真实交易路径层级，但这里不再作为运行时硬拦截。",
        "- 再看“失败模式聚合”。同一失败核或同一 exact cut 的重复出现，只算一个已知坏盆地。",
        "- 未压缩原始历史仍在 `memory/raw/rounds/` 与 `memory/raw/full_history.jsonl`。",
        "",
        "## 高风险 exact cuts（仅提示，不拦截）",
        "| 类别 | 次数 | cluster | changed_regions | target | 最近轮次 | 原因 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not highlighted_rows:
        lines.append("| - | 0 | - | - | - | - | 暂无高风险 exact cut 提示 |")
    else:
        for item in highlighted_rows[:max(1, limit)]:
            cut = item.get("cut", {}) if isinstance(item.get("cut"), dict) else {}
            changed_regions = ",".join(cut.get("changed_regions", []) or []) or "-"
            lines.append(
                f"| {item.get('category', '-')} | {int(item.get('count', 0) or 0)} | "
                f"{cut.get('cluster', '-') or '-'} | {changed_regions} | "
                f"{cut.get('target', '-') or '-'} | {int(item.get('last_iteration', 0) or 0) or '-'} | "
                f"{_truncate(item.get('block_reason', '-'), 48)} |"
            )

    lines.append("")
    lines.append("## 失败模式聚合")
    if not items:
        lines.append("- 暂无失败历史。")
        return "\n".join(lines)

    for index, item in enumerate(items[:max(1, limit)], start=1):
        cut = item.get("cut", {}) if isinstance(item.get("cut"), dict) else {}
        lines.append(
            f"### {index}. {item['category']} | {item['count']} 次 | "
            f"cluster={cut.get('cluster', '-') or '-'} | target={cut.get('target', '-') or '-'}"
        )
        lines.append(
            f"- exact cut: families={','.join(cut.get('families', []) or []) or '-'}; "
            f"changed_regions={','.join(cut.get('changed_regions', []) or []) or '-'}; "
            f"params={','.join(cut.get('params', []) or []) or '-'}"
        )
        lines.append(
            f"- 最近原因: {'；'.join(item.get('top_reasons', []) or ['-'])}"
        )
        lines.append(
            f"- 高频标签: {', '.join(item.get('top_tags', []) or ['-'])}; "
            f"stage命中={item.get('stage_count', 0)}; 最近轮次={item.get('last_iteration', 0)}"
        )
        if item.get("block_exact_cut"):
            lines.append(f"- 系统提示: {item.get('block_reason', '-')}")
        example_text = "；".join(
            f"r{row.get('iteration', 0)}:{_truncate(row.get('candidate_id', '-'), 24)}"
            for row in item.get("examples", []) or []
        ) or "-"
        lines.append(f"- 最近样本: {example_text}")
        lines.append("")
    return "\n".join(lines).rstrip()


def load_failure_wiki_index(memory_root: Path) -> dict[str, Any]:
    path = _memory_archive_paths(memory_root)["failure_wiki_json"]
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def evaluate_candidate_failure_wiki_guard(
    candidate: Any,
    failure_wiki_index: dict[str, Any],
    *,
    base_source: str | None = None,
    editable_regions: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    # exact cut 已降级为 wiki-only 风险提示：继续保留聚合与 markdown，
    # 但不再参与运行时硬拦截，避免把过宽的探索赛道整条封死。
    return None


def _write_prompt_memory_snapshots(
    memory_root: Path,
    *,
    all_entries: list[dict[str, Any]],
    current_stage_entries: list[dict[str, Any]],
    current_stage_meta: dict[str, Any],
    past_stage_summaries: list[dict[str, Any]],
    all_time_tables: dict[str, Any],
    summary_text: str,
) -> None:
    paths = _memory_archive_paths(memory_root)
    for key in (
        "current_stage",
        "past_stages",
        "all_time_tables",
        "latest_prompt",
        "failure_wiki_md",
        "failure_wiki_json",
        "duplicate_watchlist_md",
    ):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    paths["current_stage"].write_text(
        json.dumps(
            {
                "stage_meta": current_stage_meta,
                "entries": current_stage_entries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    paths["past_stages"].write_text(json.dumps(past_stage_summaries, ensure_ascii=False, indent=2))
    paths["all_time_tables"].write_text(json.dumps(all_time_tables, ensure_ascii=False, indent=2))
    paths["latest_prompt"].write_text(summary_text)
    failure_wiki_payload = build_failure_wiki_payload(
        all_entries,
        score_regime=str(current_stage_meta.get("score_regime", "")).strip(),
        current_stage_entries=current_stage_entries,
    )
    paths["failure_wiki_json"].write_text(json.dumps(failure_wiki_payload, ensure_ascii=False, indent=2))
    paths["failure_wiki_md"].write_text(format_failure_wiki_markdown(failure_wiki_payload))
    paths["duplicate_watchlist_md"].write_text(
        format_duplicate_watchlist_markdown(
            all_entries,
            score_regime=str(current_stage_meta.get("score_regime", "")).strip(),
        )
    )


def _uncompacted_recent_entries(
    entries: list[dict[str, Any]],
    journal_path: Path | None,
    *,
    score_regime: str = "",
) -> tuple[list[dict[str, Any]], int]:
    if not entries:
        return [], 0
    if journal_path is None:
        start_index = max(0, len(entries) - COMPACT_INTERVAL)
        recent_entries = entries[start_index:]
    else:
        compacted_up_to = int(load_compact(journal_path).get("compacted_up_to", 0) or 0)
        compacted_up_to = max(0, min(compacted_up_to, len(entries)))
        start_index = compacted_up_to
        recent_entries = entries[compacted_up_to:]
    if score_regime:
        recent_entries = [
            entry for entry in recent_entries
            if _entry_score_regime(entry) == score_regime
        ]
    return recent_entries, start_index


def _format_metric(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _display_outcome(raw_outcome: str) -> str:
    return {
        "accepted": "保留",
        "rejected": "未保留",
        "duplicate_skipped": "重复跳过",
        "generation_invalid": "技术退回",
        "behavioral_noop": "行为无变化",
        "exploration_blocked": "探索拦截",
        "early_rejected": "提前淘汰",
        "runtime_failed": "运行失败",
    }.get(raw_outcome, raw_outcome or "-")


def _display_stage(entry: dict[str, Any]) -> str:
    stage = str(entry.get("stop_stage") or "")
    if stage == "early_reject":
        return "提前淘汰"
    if stage == "runtime_error":
        return "运行失败"
    if stage == "duplicate_source":
        return "重复源码"
    if stage == "duplicate_history":
        return "历史重复"
    if stage == "empty_diff":
        return "无有效diff"
    if stage == "blocked_invalid_generation":
        return "无真实改动"
    if stage == "behavioral_noop":
        return "行为无变化"
    if stage == "candidate_validation":
        return "源码校验"
    if stage == "blocked_same_cluster":
        return "同簇近邻"
    if stage == "blocked_locked_cluster":
        return "命中锁簇"
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


def _format_recent_rounds_table(entries: list[dict[str, Any]], start_index: int) -> list[str]:
    display_labels = _stage_round_labels(entries, start_index)
    lines = [
        "| 轮次 | 结果 | 阶段 | quality | promotion | val_hit | val_bull | val_bear | gap | gate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for round_label, entry in zip(display_labels, entries):
        outcome = _display_outcome(str(entry.get("outcome", "")))
        stage = _display_stage(entry)
        promotion = _format_metric(entry.get("promotion_score"))
        quality = _format_metric(entry.get("quality_score"))
        if _has_metric(entry, "validation_segment_hit_rate"):
            hit_rate = _format_metric(_metric_from_entry(entry, "validation_segment_hit_rate"))
            bull = _format_metric(_metric_from_entry(entry, "validation_bull_capture_score"))
            bear = _format_metric(_metric_from_entry(entry, "validation_bear_capture_score"))
            gap = _format_metric(_metric_from_entry(entry, "dev_validation_gap") or entry.get("promotion_gap"))
        else:
            hit_rate = "-"
            bull = "-"
            bear = "-"
            gap = _format_metric(entry.get("promotion_gap")) if entry.get("promotion_gap") is not None else "-"
        gate = _truncate(_entry_decision_reason(entry), 20) or "-"
        lines.append(
            f"| {round_label} | {outcome} | {stage} | {quality} | {promotion} | "
            f"{hit_rate} | {bull} | {bear} | {gap} | {gate} |"
        )
    return lines


def _recent_round_meta_lines(entries: list[dict[str, Any]], start_index: int) -> list[str]:
    display_labels = _stage_round_labels(entries, start_index)
    lines = ["最近轮次元信息:"]
    for round_label, entry in zip(display_labels, entries):
        candidate_id = _truncate(entry.get("candidate_id", "unknown"), 28)
        cluster = _truncate(cluster_key_for_entry(entry) or "-", 24)
        tags = _truncate(",".join(entry.get("change_tags", [])) or "-", 40)
        regions = _truncate(",".join(_entry_changed_regions(entry)) or "-", 20)
        summary = _truncate(entry.get("note") or entry.get("hypothesis") or "-", 64)
        complexity = _truncate(entry.get("system_complexity_summary") or "-", 48)
        bloat_suffix = "; bloat=YES" if bool(entry.get("system_bloat_flag")) else ""
        lines.append(
            f"- {round_label} {candidate_id}: cluster={cluster}; tags={tags}; regions={regions}; complexity={complexity}{bloat_suffix}; 摘要={summary}"
        )
    return lines


def _stage_round_labels(entries: list[dict[str, Any]], start_index: int) -> list[str]:
    numeric_labels = [_entry_iteration(entry) for entry in entries]
    seen: set[int] = set()
    previous = -1
    duplicate_or_reset = False
    for value in numeric_labels:
        if value <= 0:
            duplicate_or_reset = True
            continue
        if value in seen or value <= previous:
            duplicate_or_reset = True
        seen.add(value)
        previous = max(previous, value)

    labels: list[str] = []
    for offset, value in enumerate(numeric_labels, start=1):
        if value <= 0:
            labels.append(f"j{start_index + offset}")
            continue
        if duplicate_or_reset:
            labels.append(f"s{offset}/r{value}")
            continue
        labels.append(str(value))
    return labels


def _empty_prompt_tables(stage_title: str = "当前 stage") -> list[str]:
    return [
        "方向风险表（必须先读）:",
        "| 方向簇 | 最近尝试 | 失败 | 零增益 | 运行报错 | 最佳delta | 标签 | 最近标签 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        "| - | 0 | 0 | 0 | 0 | 0.00 | OPEN | - |",
        "",
        "方向冷却表（系统硬约束）:",
        "| 方向簇 | 状态 | 剩余锁定轮次 | 触发次数 | 最近原因 |",
        "| --- | --- | --- | --- | --- |",
        "| - | OPEN | 0 | 0 | 暂无被系统锁定的方向簇 |",
        "",
        "过拟合风险表（谨慎参考）:",
        "| 轮次 | 结果 | promotion | 风险 | 分数 | top1+ | chain+ | 覆盖 | 多空偏科 | 落差 | 建议 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| - | - | - | 低 | 0 | - | - | - | - | - | 暂无需要降权的高风险轮次 |",
        "",
        f"{stage_title} 共 0 条：保留 0，未保留 0，重复跳过 0，结果盆地重复 0，探索拦截 0，提前淘汰 0，运行失败 0。",
        f"{stage_title} 运营指标表:",
        "| accept rate | behavioral_noop rate | exploration_blocked rate | smoke->full_eval | 平均改动 ordinary families | 弱侧探索占比 |",
        "| --- | --- | --- | --- | --- | --- |",
        "| 0% | 0% | 0% | 0% | 0.00 | - |",
        f"{stage_title} 核心指标表:",
        "| 轮次 | 结果 | 阶段 | quality | promotion | val_hit | val_bull | val_bear | gap | gate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| - | - | - | - | - | - | - | - | - | 新评分口径已重置，等待新历史。 |",
        "",
        f"{stage_title} 元信息:",
        "- 新评分口径已重置，等待新历史。",
    ]


def _legacy_regime_reference_lines(
    entries: list[dict[str, Any]],
    *,
    current_score_regime: str,
    limit: int,
) -> list[str]:
    if not current_score_regime:
        return []

    legacy_entries = [
        entry for entry in entries
        if _entry_score_regime(entry) and _entry_score_regime(entry) != current_score_regime
    ]
    if not legacy_entries:
        return []

    entries_by_regime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in legacy_entries:
        entries_by_regime[_entry_score_regime(entry)].append(entry)

    lines = [
        "旧评分口径弱参考（不可主导本轮）:",
        f"- 当前主参考只能是 `{current_score_regime}` 的近期轮次、方向风险表和过拟合风险表。",
        "- 旧口径只可作为因子家族或方向假设的弱启发；禁止把旧分数、旧 gate 结论或旧 champion 直接当成当前有效证据。",
        "- 如果旧口径结论与当前口径近期失败记忆冲突，一律以当前口径为准。",
    ]

    ranked_regimes = sorted(
        entries_by_regime.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    for regime, regime_entries in ranked_regimes[:limit]:
        accepted_count = sum(
            1 for entry in regime_entries
            if _outcome_bucket(str(entry.get("outcome", ""))) == "accepted"
        )
        rejected_count = sum(
            1 for entry in regime_entries
            if _outcome_bucket(str(entry.get("outcome", ""))) == "rejected"
        )

        tag_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"accepted": 0, "rejected": 0, "scores": []}
        )
        cluster_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"attempts": 0, "failures": 0, "zero_delta": 0, "runtime_errors": 0, "best_delta": 0.0}
        )
        for entry in regime_entries:
            outcome = _outcome_bucket(str(entry.get("outcome", "")))
            promotion_delta = _score_value(entry.get("promotion_delta"))
            promotion_score = _score_value(entry.get("promotion_score"))
            for tag in entry.get("change_tags", []):
                bucket = tag_stats[str(tag)]
                if outcome in {"accepted", "rejected"}:
                    bucket[outcome] = bucket.get(outcome, 0) + 1
                bucket["scores"].append(promotion_score)

            cluster = cluster_key_for_entry(entry)
            cluster_bucket = cluster_stats[cluster]
            cluster_bucket["attempts"] += 1
            if outcome == "rejected":
                cluster_bucket["failures"] += 1
            if abs(promotion_delta) <= 1e-9:
                cluster_bucket["zero_delta"] += 1
            if str(entry.get("outcome", "")) == "runtime_failed":
                cluster_bucket["runtime_errors"] += 1
            cluster_bucket["best_delta"] = max(cluster_bucket["best_delta"], promotion_delta)

        reference_tags = sorted(
            (
                (tag, stats)
                for tag, stats in tag_stats.items()
                if stats.get("accepted", 0) > 0
            ),
            key=lambda item: (
                item[1]["accepted"] - item[1]["rejected"],
                item[1]["accepted"],
                -item[1]["rejected"],
                _mean(item[1]["scores"]),
            ),
            reverse=True,
        )
        reference_text = ", ".join(
            f"{tag}(通{stats['accepted']}/败{stats['rejected']})"
            for tag, stats in reference_tags[:LEGACY_REFERENCE_TAG_LIMIT]
        ) or "-"

        ranked_clusters = sorted(
            cluster_stats.items(),
            key=lambda item: (
                {"EXHAUSTED": 0, "SATURATED": 1, "RUNTIME_RISK": 2, "WARM": 3, "ACTIVE_WINNER": 4, "OPEN": 5}[
                    _risk_label(
                        attempts=int(item[1]["attempts"]),
                        failures=int(item[1]["failures"]),
                        zero_delta=int(item[1]["zero_delta"]),
                        runtime_errors=int(item[1]["runtime_errors"]),
                        best_delta=float(item[1]["best_delta"]),
                    )
                ],
                -int(item[1]["attempts"]),
                -int(item[1]["failures"]),
            ),
        )
        cluster_text = ", ".join(
            f"{cluster}({ _risk_label(attempts=int(stats['attempts']), failures=int(stats['failures']), zero_delta=int(stats['zero_delta']), runtime_errors=int(stats['runtime_errors']), best_delta=float(stats['best_delta'])) })"
            for cluster, stats in ranked_clusters[:LEGACY_REFERENCE_CLUSTER_LIMIT]
        ) or "-"

        lines.append(
            f"- {regime}: 历史 {len(regime_entries)} 轮，保留 {accepted_count}，失败 {rejected_count}；"
            f"可弱参考标签={reference_text}；高失败簇={cluster_text}"
        )

    return lines


def build_journal_prompt_summary(
    entries: list[dict[str, Any]],
    limit: int = 6,
    journal_path: Path | None = None,
    current_score_regime: str = "",
    current_iteration: int = 0,
    active_stage_started_at: str = "",
    active_stage_iteration: int = 0,
    reference_role: str = "",
    reference_metrics: dict[str, Any] | None = None,
    memory_root: Path | None = None,
) -> str:
    all_entries = list(entries)
    active_score_regime = current_score_regime or _latest_score_regime(all_entries)
    if current_iteration <= 0:
        current_iteration = max(
            (int(entry.get("iteration", 0) or 0) for entry in all_entries),
            default=0,
        ) + 1
    current_entries = _entries_for_score_regime(all_entries, active_score_regime)
    current_relevant_entries = _strategy_relevant_entries(current_entries)
    current_stage_entries, past_stage_entries, stage_meta = _partition_entries_by_stage(
        all_entries,
        score_regime=active_score_regime,
        active_stage_started_at=active_stage_started_at,
        active_stage_iteration=active_stage_iteration,
    )
    current_stage_relevant_entries = _strategy_relevant_entries(current_stage_entries)
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
        parts.extend(_empty_prompt_tables(stage_scope))
    else:
        if current_stage_entries:
            board_entries = current_stage_relevant_entries
            if not board_entries:
                parts.extend(_empty_prompt_tables(stage_scope))
                parts.append(
                    f"{stage_scope} 最近仅出现 {current_stage_technical_invalid_count} 条技术性空转；"
                    "它们已从 failure wiki / 方向风险 / 过热统计中隔离，不作为策略失败方向证据。"
                )
            else:
                display_entries = board_entries[-max(1, limit):]
                stage_start_index = max(0, int(stage_meta.get("stage_iteration", 0) or 0) - 1)
                executive_lines = _stage_executive_summary_lines(
                    board_entries,
                    stage_name=stage_name,
                    reference_metrics=reference_metrics,
                    limit=min(8, limit),
                )
                if executive_lines:
                    parts.extend(executive_lines)
                    parts.append("")

                failure_nucleus_lines = _stage_failure_nucleus_lines(board_entries, limit=min(4, limit))
                if failure_nucleus_lines:
                    parts.extend(failure_nucleus_lines)
                    parts.append("")

                board_lines = _direction_risk_board(board_entries, limit=min(8, limit))
                if board_lines:
                    parts.extend(board_lines)
                    parts.append("")

                cooling_lines = _direction_cooling_board(
                    board_entries,
                    limit=min(8, limit),
                    journal_path=journal_path,
                    score_regime=active_score_regime,
                    current_iteration=current_iteration,
                )
                if cooling_lines:
                    parts.extend(cooling_lines)
                    parts.append("")

                overheat_lines = _cluster_overheat_lines(board_entries, limit=min(8, limit))
                if overheat_lines:
                    parts.extend(overheat_lines)
                    parts.append("")

                overfit_lines = _overfit_risk_board(board_entries, limit=min(8, limit))
                if overfit_lines:
                    parts.extend(overfit_lines)
                    parts.append("")

                exploration_lines = _exploration_trigger_lines(board_entries, limit=min(8, limit))
                if exploration_lines:
                    parts.extend(exploration_lines)
                    parts.append("")

                accepted_count = sum(1 for entry in board_entries if entry.get("outcome") == "accepted")
                rejected_count = sum(1 for entry in board_entries if entry.get("outcome") == "rejected")
                duplicate_skipped_count = sum(1 for entry in board_entries if entry.get("outcome") == "duplicate_skipped")
                behavioral_noop_count = sum(1 for entry in board_entries if entry.get("outcome") == "behavioral_noop")
                exploration_blocked_count = sum(1 for entry in board_entries if entry.get("outcome") == "exploration_blocked")
                early_rejected_count = sum(1 for entry in board_entries if entry.get("outcome") == "early_rejected")
                runtime_failed_count = sum(1 for entry in board_entries if entry.get("outcome") == "runtime_failed")
                bloat_flag_count = sum(1 for entry in board_entries if bool(entry.get("system_bloat_flag")))
                repeated_basin_count = _repeated_result_basin_entry_count(board_entries)
                operating_metrics = _stage_operating_metrics(
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
                    f"复杂度超标 {bloat_flag_count}，技术空转 {current_stage_technical_invalid_count}。"
                )
                parts.extend(
                    _format_stage_operating_metrics(
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
                failure_tag_lines = _recent_failure_tag_lines(board_entries, limit=min(8, limit))
                if failure_tag_lines:
                    parts.append("当前 stage 高频失败标签:")
                    parts.extend(failure_tag_lines)
                parts.append(f"{stage_name} 核心指标表:")
                parts.extend(_format_recent_rounds_table(display_entries, stage_start_index))
                parts.append("")
                parts.extend(_recent_round_meta_lines(display_entries, stage_start_index))
        else:
            parts.extend(_empty_prompt_tables(stage_scope))

        past_stage_groups = _group_entries_into_stages(past_stage_entries)
        past_stage_summaries = [
            _summarize_stage_entries(stage_entries, stage_id=index + 1)
            for index, stage_entries in enumerate(past_stage_groups)
            if stage_entries
        ]
        past_stage_lines = _format_past_stage_summary_lines(past_stage_summaries, limit=min(4, limit))
        if past_stage_lines:
            if parts:
                parts.append("")
            parts.extend(past_stage_lines)

        all_time_payload = _all_time_tables_payload(current_relevant_entries, limit=min(8, limit))
        all_time_lines = _format_all_time_tables(all_time_payload)
        if all_time_lines:
            if parts:
                parts.append("")
            parts.extend(all_time_lines)
    if current_entries and not past_stage_entries:
        past_stage_summaries = []
        all_time_payload = _all_time_tables_payload(current_relevant_entries, limit=min(8, limit))
    elif not current_entries:
        past_stage_summaries = []
        all_time_payload = {}

    legacy_lines = _legacy_regime_reference_lines(
        all_entries,
        current_score_regime=active_score_regime,
        limit=min(LEGACY_REFERENCE_REGIME_LIMIT, limit),
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
        }
        _write_prompt_memory_snapshots(
            memory_root,
            all_entries=current_entries,
            current_stage_entries=current_stage_entries,
            current_stage_meta=snapshot_meta,
            past_stage_summaries=past_stage_summaries,
            all_time_tables=all_time_payload,
            summary_text=summary_text,
        )
    return summary_text


def has_recent_code_hash(entries: list[dict[str, Any]], code_hash: str, lookback: int = 12) -> bool:
    for entry in reversed(entries[-lookback:]):
        if entry.get("code_hash") == code_hash:
            return True
    return False
