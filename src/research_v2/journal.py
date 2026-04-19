#!/usr/bin/env python3
"""研究日志与历史记忆。"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from research_v2.evaluation import OVERFIT_WARN_SCORE, overfit_reference_action, overfit_risk_level_from_score
from research_v2.strategy_code import build_system_edit_signature


# ==================== 常量 ====================


COMPACT_INTERVAL = 20
NEGATIVE_OUTCOMES = {"rejected", "early_rejected", "runtime_failed", "duplicate_skipped"}
NO_OP_STOP_STAGES = {"duplicate_source", "duplicate_history", "empty_diff"}

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


def cluster_for_tags(tags: list[str] | tuple[str, ...]) -> str:
    normalized = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    for cluster_name, keywords in CLUSTER_KEYWORDS:
        if any(keyword in tag for tag in normalized for keyword in keywords):
            return cluster_name
    return normalized[0] if normalized else "unclassified"


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


def cluster_key_for_components(closest_failed_cluster: Any, change_tags: list[str] | tuple[str, ...]) -> str:
    declared = _normalize_cluster_name(closest_failed_cluster)
    inferred = cluster_for_tags(change_tags)
    if declared and declared == inferred:
        return declared
    if declared in CANONICAL_CLUSTER_NAMES:
        return declared
    if inferred in CANONICAL_CLUSTER_NAMES:
        return inferred
    return declared or inferred


def cluster_key_for_entry(entry: dict[str, Any]) -> str:
    stored = _normalize_cluster_name(entry.get("cluster_key", ""))
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
        "_trend_followthrough_ok": "followthrough",
        "_trend_followthrough_long": "followthrough",
        "_trend_followthrough_short": "followthrough",
        "_long_entry_signal": "long_entry",
        "_short_entry_signal": "short_entry",
        "strategy": "strategy",
    }
    normalized = []
    for region in regions:
        name = str(region).strip()
        family = family_map.get(name, name)
        if family and family not in normalized:
            normalized.append(family)
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

    return {
        "cluster_key": cluster_key_for_entry(entry),
        "tags": {
            str(tag).strip()
            for tag in entry.get("change_tags", [])
            if str(tag).strip()
        },
        "region_families": set(region_families),
        "changed_regions": changed_regions,
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

    return {
        "cluster_key": cluster_key_for_components(
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

    accepted = [e for e in entries if _outcome_bucket(str(e.get("outcome", ""))) == "accepted"]
    rejected = [e for e in entries if _outcome_bucket(str(e.get("outcome", ""))) == "rejected"]
    duplicate_skipped_count = sum(1 for e in entries if e.get("outcome") == "duplicate_skipped")
    exploration_blocked_count = sum(1 for e in entries if e.get("outcome") == "exploration_blocked")
    early_rejected_count = sum(1 for e in entries if e.get("outcome") == "early_rejected")
    runtime_failed_count = sum(1 for e in entries if e.get("outcome") == "runtime_failed")

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
    best_entry = max(entries, key=lambda e: _score_value(e.get("promotion_score")))
    worst_entry = min(entries, key=lambda e: _score_value(e.get("promotion_score")))

    tag_summary = {}
    for tag, stats in tag_stats.items():
        tag_summary[tag] = {
            "accepted": stats.get("accepted", 0),
            "rejected": stats.get("rejected", 0),
            "avg_score": _mean(stats["scores"]),
        }

    cluster_summary: dict[str, dict[str, Any]] = {}
    for entry in entries:
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
        "score_regime": _entry_score_regime(entries[-1]),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "duplicate_skipped_count": duplicate_skipped_count,
        "exploration_blocked_count": exploration_blocked_count,
        "early_rejected_count": early_rejected_count,
        "runtime_failed_count": runtime_failed_count,
        "accept_rate": len(accepted) / len(entries) if entries else 0.0,
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
    total_exploration_blocked = sum(r.get("exploration_blocked_count", 0) for r in rounds)
    total_early_rejected = sum(r.get("early_rejected_count", 0) for r in rounds)
    total_runtime_failed = sum(r.get("runtime_failed_count", 0) for r in rounds)
    total_entries = sum(r.get("entry_count", 0) for r in rounds)
    lines.append(
        f"共 {total_entries} 轮历史，{total_accepted} 次通过，"
        f"{total_rejected} 次失败，探索拦截 {total_exploration_blocked} 次，其中提前淘汰 {total_early_rejected} 次，"
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
    tail = _low_change_tail(_entries_for_score_regime(entries, score_regime))
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
        if signature["target_family"] not in {"", "unknown"}:
            target_families.add(signature["target_family"])
        core_factor_names.update(signature["core_factor_names"])
        param_families.update(signature["param_families"])
        structural_tokens.update(signature["structural_tokens"])
        if signature["signature_hash"]:
            signature_hashes.add(signature["signature_hash"])

    return {
        "cluster": cluster,
        "entries": cluster_entries,
        "entry_signatures": entry_signatures,
        "tag_union": tag_union,
        "region_families": region_families,
        "changed_regions": changed_regions,
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
    candidate_regions = candidate_signature["region_families"]
    candidate_changed_regions = candidate_signature["changed_regions"]
    candidate_target = candidate_signature["target_family"]
    candidate_factors = candidate_signature["core_factor_names"]
    candidate_tags = candidate_signature["tags"]
    candidate_param_families = candidate_signature["param_families"]
    candidate_structural_tokens = candidate_signature["structural_tokens"]
    candidate_signature_hash = candidate_signature["signature_hash"]

    if candidate_regions and not candidate_regions.issubset(low_change_context["region_families"]):
        return True
    if candidate_changed_regions and not candidate_changed_regions.issubset(low_change_context["changed_regions"]):
        return True
    if candidate_target in {"long", "short"} and candidate_target not in low_change_context["target_families"]:
        return True
    if candidate_factors - low_change_context["core_factor_names"]:
        return True
    if candidate_param_families - low_change_context["param_families"]:
        return True
    if candidate_signature_hash and candidate_signature_hash in low_change_context["signature_hashes"]:
        return False
    if candidate_structural_tokens:
        overlap_ratio = len(candidate_structural_tokens & low_change_context["structural_tokens"]) / max(len(candidate_structural_tokens), 1)
        if len(candidate_structural_tokens) >= 4 and overlap_ratio <= STRUCTURAL_TOKEN_NOVELTY_MAX_OVERLAP:
            return True
    if candidate_tags:
        overlap_ratio = len(candidate_tags & low_change_context["tag_union"]) / max(len(candidate_tags), 1)
        if len(candidate_tags) >= 2 and overlap_ratio <= TAG_NOVELTY_MAX_OVERLAP:
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
    return {
        "history_counts": _merged_exploration_history(
            entries,
            journal_path=journal_path,
            score_regime=score_regime,
            current_iteration=current_iteration,
        ),
        "active_locks": _current_round_lock_state(
            entries,
            score_regime=score_regime,
            current_iteration=current_iteration,
            include_current_round_locks=include_current_round_locks,
        ),
        "low_change_context": _same_cluster_low_change_context(entries, score_regime),
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
    if lock_applied:
        schedule_index = min(max(lock_level - 1, 0), max(len(lock_schedule) - 1, 0))
        lock_rounds = int(lock_schedule[schedule_index])
        lock_expires_before_iteration = current_iteration + lock_rounds + 1
        blocked_reason = (
            f"同簇低变化近邻：`{candidate_cluster}` 最近持续低变化打转，"
            f"当前候选未切出新交易路径；该簇将冷却 {lock_rounds} 轮。"
        )
    else:
        lock_rounds = 0
        lock_expires_before_iteration = 0
        blocked_reason = (
            f"同簇低变化近邻：`{candidate_cluster}` 最近持续低变化打转，"
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
        "low_change_regions": tuple(sorted(low_change_context["region_families"])),
        "low_change_changed_regions": tuple(sorted(low_change_context["changed_regions"])),
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


def _noop_tail(entries: list[dict[str, Any]], streak: int = LOW_CHANGE_STREAK) -> list[dict[str, Any]]:
    if len(entries) < streak:
        return []
    tail = entries[-streak:]
    if all(str(entry.get("outcome", "")) == "duplicate_skipped" for entry in tail):
        return tail
    if all(str(entry.get("stop_stage", "")) in NO_OP_STOP_STAGES for entry in tail):
        return tail
    return []


def _exploration_trigger_lines(entries: list[dict[str, Any]], limit: int) -> list[str]:
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
            for region in entry.get("edited_regions", []):
                region_text = str(region).strip()
                if region_text and region_text not in regions:
                    regions.append(region_text)
            reason_text = _entry_decision_reason(entry)
            if reason_text and reason_text not in reasons:
                reasons.append(reason_text)

        return [
            "探索触发（必须执行）:",
            f"最近 {LOW_CHANGE_STREAK} 轮都没有产生有效代码改动，已按重复探索记入历史。",
            f"- 重复原因：{'；'.join(reasons[:limit]) or '-'}",
            f"- 近期近邻方向簇：{', '.join(clusters[:limit]) or '-'}；标签：{', '.join(tags[:limit]) or '-'}；高频区域：{', '.join(regions[:limit]) or '-'}",
            "- 下一轮必须先产出有效 diff；若继续沿用相近方向，至少切换方向簇、edited region family、long-short target 中的一项。",
        ]

    tail = _low_change_tail(entries)
    if not tail:
        return []

    clusters: list[str] = []
    factors: list[str] = []
    tags: list[str] = []
    regions: list[str] = []
    for entry in tail:
        cluster = cluster_key_for_entry(entry)
        if cluster and cluster not in clusters:
            clusters.append(cluster)
        for factor in entry.get("core_factors", []):
            if not isinstance(factor, dict):
                continue
            name = str(factor.get("name", "")).strip()
            if name and name not in factors:
                factors.append(name)
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text and tag_text not in tags:
                tags.append(tag_text)
        for region in entry.get("edited_regions", []):
            region_text = str(region).strip()
            if region_text and region_text not in regions:
                regions.append(region_text)

    lines = [
        "探索触发（必须执行）:",
        f"最近 {LOW_CHANGE_STREAK} 轮都属于低变化轮次：晋级分没有实质提升，且 trend_score / hit_rate / bull_bear_capture / fee_drag 基本不变。",
        f"- 近期近邻方向簇：{', '.join(clusters[:limit]) or '-'}；标签：{', '.join(tags[:limit]) or '-'}；核心因子：{', '.join(factors[:limit]) or '-'}；高频区域：{', '.join(regions[:limit]) or '-'}",
        "- 下一轮必须把它当作探索轮：优先切簇；若无法切簇，至少切换 edited region family、long-short target 或核心因子家族中的一项。",
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
    factors: list[str] = []
    regions: list[str] = []
    for entry in hot_entries:
        for tag in entry.get("change_tags", []):
            tag_text = str(tag).strip()
            if tag_text and tag_text not in tags:
                tags.append(tag_text)
        for factor in entry.get("core_factors", []):
            if not isinstance(factor, dict):
                continue
            factor_name = str(factor.get("name", "")).strip()
            if factor_name and factor_name not in factors:
                factors.append(factor_name)
        for region in entry.get("edited_regions", []):
            region_text = str(region).strip()
            if region_text and region_text not in regions:
                regions.append(region_text)

    return [
        "主簇过热（必须先读）:",
        f"最近 {len(recent)} 轮里，`{hot_cluster}` 占比 {hot_share:.0%}（{hot_count}/{len(recent)}），且最佳 promotion_delta 仅 {best_delta:.2f}，继续留在该簇大概率仍是近邻试错。",
        f"- 过热方向簇：{hot_cluster}",
        f"- 近期近邻标签：{', '.join(tags[:limit]) or '-'}；核心因子：{', '.join(factors[:limit]) or '-'}；高频区域：{', '.join(regions[:limit]) or '-'}",
        "- 下一轮默认应切到不同方向簇；若仍留在该簇，必须证明这次会改变不同交易路径。",
    ]


# ==================== 摘要生成 ====================


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
    lines = [
        "| 轮次 | 结果 | 阶段 | quality | promotion | val_hit | val_bull | val_bear | gap | gate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for offset, entry in enumerate(entries, start=1):
        round_label = entry.get("iteration")
        if round_label in (None, "", 0):
            round_label = f"j{start_index + offset}"
        outcome = _display_outcome(str(entry.get("outcome", "")))
        stage = _display_stage(entry)
        promotion = _format_metric(entry.get("promotion_score"))
        quality = _format_metric(entry.get("quality_score"))
        hit_rate = _format_metric(_metric_from_entry(entry, "validation_segment_hit_rate"))
        bull = _format_metric(_metric_from_entry(entry, "validation_bull_capture_score"))
        bear = _format_metric(_metric_from_entry(entry, "validation_bear_capture_score"))
        gap = _format_metric(_metric_from_entry(entry, "dev_validation_gap") or entry.get("promotion_gap"))
        gate = _truncate(_entry_decision_reason(entry), 20) or "-"
        lines.append(
            f"| {round_label} | {outcome} | {stage} | {quality} | {promotion} | "
            f"{hit_rate} | {bull} | {bear} | {gap} | {gate} |"
        )
    return lines


def _recent_round_meta_lines(entries: list[dict[str, Any]], start_index: int) -> list[str]:
    lines = ["最近轮次元信息:"]
    for offset, entry in enumerate(entries, start=1):
        round_label = entry.get("iteration")
        if round_label in (None, "", 0):
            round_label = f"j{start_index + offset}"
        candidate_id = _truncate(entry.get("candidate_id", "unknown"), 28)
        cluster = _truncate(cluster_key_for_entry(entry) or "-", 24)
        tags = _truncate(",".join(entry.get("change_tags", [])) or "-", 40)
        regions = _truncate(",".join(entry.get("edited_regions", [])) or "-", 20)
        summary = _truncate(entry.get("note") or entry.get("hypothesis") or "-", 64)
        lines.append(
            f"- {round_label} {candidate_id}: cluster={cluster}; tags={tags}; regions={regions}; 摘要={summary}"
        )
    return lines


def _empty_prompt_tables() -> list[str]:
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
        "最近未压缩轮次共 0 条：保留 0，未保留 0，重复跳过 0，探索拦截 0，提前淘汰 0，运行失败 0。",
        "最近核心指标表:",
        "| 轮次 | 结果 | 阶段 | quality | promotion | val_hit | val_bull | val_bear | gap | gate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| - | - | - | - | - | - | - | - | - | 新评分口径已重置，等待新历史。 |",
        "",
        "最近轮次元信息:",
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
) -> str:
    all_entries = list(entries)
    active_score_regime = current_score_regime or _latest_score_regime(all_entries)
    if current_iteration <= 0:
        current_iteration = max(
            (int(entry.get("iteration", 0) or 0) for entry in all_entries),
            default=0,
        ) + 1
    current_entries = _entries_for_score_regime(all_entries, active_score_regime)
    parts: list[str] = []
    if not current_entries:
        parts.extend(_empty_prompt_tables())
    else:
        recent_entries, recent_start = _uncompacted_recent_entries(
            all_entries,
            journal_path,
            score_regime=active_score_regime,
        )
        if recent_entries:
            display_entries = recent_entries
            board_entries = display_entries
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

            accepted_count = sum(1 for entry in display_entries if entry.get("outcome") == "accepted")
            rejected_count = sum(1 for entry in display_entries if entry.get("outcome") == "rejected")
            duplicate_skipped_count = sum(1 for entry in display_entries if entry.get("outcome") == "duplicate_skipped")
            exploration_blocked_count = sum(1 for entry in display_entries if entry.get("outcome") == "exploration_blocked")
            early_rejected_count = sum(1 for entry in display_entries if entry.get("outcome") == "early_rejected")
            runtime_failed_count = sum(1 for entry in display_entries if entry.get("outcome") == "runtime_failed")
            parts.append(
                f"最近未压缩轮次共 {len(display_entries)} 条："
                f"保留 {accepted_count}，未保留 {rejected_count}，"
                f"重复跳过 {duplicate_skipped_count}，"
                f"探索拦截 {exploration_blocked_count}，"
                f"提前淘汰 {early_rejected_count}，运行失败 {runtime_failed_count}。"
            )
            failure_tag_lines = _recent_failure_tag_lines(display_entries, limit=min(8, limit))
            if failure_tag_lines:
                parts.append("最近高频失败标签:")
                parts.extend(failure_tag_lines)
            core_factor_columns = _recent_core_factor_columns(display_entries, limit=min(4, limit))
            core_factor_lines = _recent_core_factor_lines(display_entries, core_factor_columns)
            if core_factor_lines:
                parts.extend(core_factor_lines)
            parts.append("最近核心指标表:")
            parts.extend(_format_recent_rounds_table(display_entries, recent_start))
            parts.append("")
            parts.extend(_recent_round_meta_lines(display_entries, recent_start))

        # 压缩历史放在近期方向风险之后，避免长历史淹没最近连续失败。
        if journal_path is not None:
            compact_data = load_compact(journal_path)
            compact_lines = _format_compact_for_prompt(
                compact_data,
                limit=min(8, limit),
                score_regime=active_score_regime,
            )
            if compact_lines:
                if parts:
                    parts.append("")
                parts.extend(compact_lines)

    legacy_lines = _legacy_regime_reference_lines(
        all_entries,
        current_score_regime=active_score_regime,
        limit=min(LEGACY_REFERENCE_REGIME_LIMIT, limit),
    )
    if legacy_lines:
        if parts:
            parts.append("")
        parts.extend(legacy_lines)

    return "\n".join(parts) if parts else "暂无研究历史。"


def has_recent_code_hash(entries: list[dict[str, Any]], code_hash: str, lookback: int = 12) -> bool:
    for entry in reversed(entries[-lookback:]):
        if entry.get("code_hash") == code_hash:
            return True
    return False
