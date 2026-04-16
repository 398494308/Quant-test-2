#!/usr/bin/env python3
"""研究日志与历史记忆。"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from research_v2.evaluation import OVERFIT_WARN_SCORE, overfit_reference_action, overfit_risk_level_from_score


# ==================== 常量 ====================


COMPACT_INTERVAL = 20
NEGATIVE_OUTCOMES = {"rejected", "early_rejected", "runtime_failed"}

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
    if best_delta > 0.0:
        return "ACTIVE_WINNER"
    if attempts >= 2 and failures == attempts:
        return "WARM"
    return "OPEN"


def _compact_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """把一批 journal 条目压缩成结构化的经验摘要。"""
    if not entries:
        return {}

    accepted = [e for e in entries if _outcome_bucket(str(e.get("outcome", ""))) == "accepted"]
    rejected = [e for e in entries if _outcome_bucket(str(e.get("outcome", ""))) == "rejected"]
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
        reason = entry.get("gate_reason", "")
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

    return {
        "entry_count": len(entries),
        "score_regime": _entry_score_regime(entries[-1]),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "early_rejected_count": early_rejected_count,
        "runtime_failed_count": runtime_failed_count,
        "accept_rate": len(accepted) / len(entries) if entries else 0.0,
        "tag_summary": tag_summary,
        "cluster_summary": cluster_summary,
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
    total_early_rejected = sum(r.get("early_rejected_count", 0) for r in rounds)
    total_runtime_failed = sum(r.get("runtime_failed_count", 0) for r in rounds)
    total_entries = sum(r.get("entry_count", 0) for r in rounds)
    lines.append(
        f"共 {total_entries} 轮历史，{total_accepted} 次通过，"
        f"{total_rejected} 次失败，其中提前淘汰 {total_early_rejected} 次，"
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
    lines.append(
        "若某方向簇被标为 `SATURATED` / `EXHAUSTED` / `RUNTIME_RISK`，除非能明确说明会改变交易路径，否则不要继续把它当主方向。"
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
    lines.append("若某轮被标为 `高` / `严重`，不要把它当作可直接复用的成功模板；若仍要借鉴，必须说明这次如何打破它对少数行情的依赖。")
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
    if any(str(entry.get("gate_reason", "")).strip() != "通过" for entry in tail):
        return []
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


def _exploration_trigger_lines(entries: list[dict[str, Any]], limit: int) -> list[str]:
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
        f"- 近期近邻方向簇：{', '.join(clusters[:limit]) or '-'}",
        f"- 近期近邻标签：{', '.join(tags[:limit]) or '-'}",
        f"- 近期近邻核心因子：{', '.join(factors[:limit]) or '-'}",
        f"- 近期高频编辑区域：{', '.join(regions[:limit]) or '-'}",
        "- 下一轮必须作为探索轮，不要继续沿用上述主因子解释或其近邻改写。",
        "- 探索轮必须至少做到以下之一：切换到不同核心因子家族；切换到不同 edited region family；若继续相近方向，必须明确说明将改变哪类交易路径。",
        "- 探索轮允许结果变差，但不能只是换措辞；应尽量让 segment_hit_rate、bull_capture_score、bear_capture_score、avg_fee_drag、total_trades 这类关键诊断至少两项出现明显变化。",
    ]
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
        "runtime_failed": "运行失败",
    }.get(raw_outcome, raw_outcome or "-")


def _display_stage(entry: dict[str, Any]) -> str:
    stage = str(entry.get("stop_stage") or "")
    if stage == "early_reject":
        return "提前淘汰"
    if stage == "runtime_error":
        return "运行失败"
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
        "| 轮次 | 候选 | 结果 | 阶段 | promotion | quality | trend | return | hit | seg | bull | bear | gate | tags | regions | "
        + " | ".join(factor_headers)
        + (" | " if factor_headers else "")
        + "摘要 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
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
        trend = _format_metric(_metric_from_entry(entry, "combined_trend_capture_score"))
        return_score = _format_metric(_metric_from_entry(entry, "combined_return_score"))
        hit_rate = _format_metric(_metric_from_entry(entry, "segment_hit_rate"))
        segment_count = _format_metric(_metric_from_entry(entry, "major_segment_count"))
        bull = _format_metric(_metric_from_entry(entry, "bull_capture_score"))
        bear = _format_metric(_metric_from_entry(entry, "bear_capture_score"))
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
            f"{quality} | {trend} | {return_score} | {hit_rate} | {segment_count} | {bull} | {bear} | "
            f"{gate} | {tags} | {regions} | "
            + " | ".join(factor_cells)
            + (" | " if factor_cells else "")
            + f"{summary} |"
        )
    return lines


def _empty_prompt_tables() -> list[str]:
    return [
        "方向风险表（必须先读）:",
        "| 方向簇 | 最近尝试 | 失败 | 零增益 | 运行报错 | 最佳delta | 标签 | 最近标签 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        "| - | 0 | 0 | 0 | 0 | 0.00 | OPEN | - |",
        "",
        "过拟合风险表（谨慎参考）:",
        "| 轮次 | 结果 | promotion | 风险 | 分数 | top1+ | chain+ | 覆盖 | 多空偏科 | 落差 | 建议 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| - | - | - | 低 | 0 | - | - | - | - | - | 暂无需要降权的高风险轮次 |",
        "",
        "最近未压缩轮次共 0 条：保留 0，未保留 0，提前淘汰 0，运行失败 0。",
        "最近未压缩轮次表:",
        "| 轮次 | 候选 | 结果 | 阶段 | promotion | quality | trend | return | hit | seg | bull | bear | gate | tags | regions | 摘要 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        "| - | - | - | - | - | - | - | - | - | - | - | - | - | - | - | 新评分口径已重置，等待新历史。 |",
    ]


def build_journal_prompt_summary(entries: list[dict[str, Any]], limit: int = 6, journal_path: Path | None = None) -> str:
    parts: list[str] = []
    if not entries:
        parts.extend(_empty_prompt_tables())
        return "\n".join(parts)

    recent_entries, recent_start = _uncompacted_recent_entries(entries, journal_path)
    if recent_entries:
        latest_regime = _latest_score_regime(entries)
        same_regime_recent = [
            entry for entry in recent_entries
            if not latest_regime or str(entry.get("score_regime", "") or latest_regime) == latest_regime
        ]
        display_entries = same_regime_recent or recent_entries
        board_entries = display_entries
        board_lines = _direction_risk_board(board_entries, limit=min(8, limit))
        if board_lines:
            parts.extend(board_lines)
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
        early_rejected_count = sum(1 for entry in display_entries if entry.get("outcome") == "early_rejected")
        runtime_failed_count = sum(1 for entry in display_entries if entry.get("outcome") == "runtime_failed")
        parts.append(
            f"最近未压缩轮次共 {len(display_entries)} 条："
            f"保留 {accepted_count}，未保留 {rejected_count}，"
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
        parts.append("最近未压缩轮次表:")
        parts.extend(_format_recent_rounds_table(display_entries, recent_start, core_factor_columns))

    # 压缩历史放在近期方向风险之后，避免长历史淹没最近连续失败。
    if journal_path is not None:
        compact_data = load_compact(journal_path)
        compact_lines = _format_compact_for_prompt(
            compact_data,
            limit=min(8, limit),
            score_regime=_latest_score_regime(entries),
        )
        if compact_lines:
            if parts:
                parts.append("")
            parts.extend(compact_lines)

    return "\n".join(parts) if parts else "暂无研究历史。"


def has_recent_code_hash(entries: list[dict[str, Any]], code_hash: str, lookback: int = 12) -> bool:
    for entry in reversed(entries[-lookback:]):
        if entry.get("code_hash") == code_hash:
            return True
    return False
