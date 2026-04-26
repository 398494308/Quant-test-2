#!/usr/bin/env python3
"""研究器 v2 的评估与打分。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any

from research_v2.config import GateConfig


# ==================== 数据结构 ====================


@dataclass(frozen=True)
class EvaluationReport:
    metrics: dict[str, float]
    gate_passed: bool
    gate_reason: str
    summary_text: str
    prompt_summary_text: str


@dataclass(frozen=True)
class DailyReturnPath:
    returns: list[float]
    unique_days: int
    overlap_days: int
    dropped_points: int


@dataclass(frozen=True)
class PointSeriesPath:
    points: list[dict[str, Any]]
    unique_points: int
    overlap_points: int
    dropped_points: int


@dataclass(frozen=True)
class TrendSegment:
    direction: int
    start_idx: int
    end_idx: int
    move_pct: float
    weight: float


@dataclass(frozen=True)
class SegmentScoreDetail:
    direction: int
    score: float
    weight: float
    duration_bars: int
    avg_atr_ratio: float


@dataclass(frozen=True)
class TrendScoreReport:
    trend_score: float
    return_score: float
    arrival_score: float
    escort_score: float
    turn_score: float
    turn_protection_score: float
    turn_protection_event_count: int
    bull_score: float
    bear_score: float
    hit_rate: float
    segment_count: int
    bull_segment_count: int
    bear_segment_count: int
    path_return_pct: float
    segment_details: tuple[SegmentScoreDetail, ...]


@dataclass(frozen=True)
class OverfitRiskReport:
    risk_score: float
    risk_level: str
    top1_positive_share: float
    max_chain_positive_share: float
    duration_coverage_ratio: float
    volatility_coverage_ratio: float
    coverage_ratio: float
    bull_bear_gap: float
    weak_side_capture_score: float
    capture_drop_abs: float
    hard_fail: bool
    hard_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ValidationBlockReport:
    block_scores: tuple[float, ...]
    mean_score: float
    std_score: float
    min_score: float
    fail_count: int
    used_block_count: int


HIT_SCORE_THRESHOLD = 0.25
OVERFIT_WARN_SCORE = 20.0
OVERFIT_HIGH_SCORE = 40.0
OVERFIT_GATE_SCORE = 60.0
OVERFIT_TOP1_WARN = 0.35
OVERFIT_TOP1_HIGH = 0.50
OVERFIT_TOP1_HARD = 0.60
OVERFIT_CHAIN_WARN = 0.45
OVERFIT_CHAIN_HIGH = 0.65
OVERFIT_CHAIN_HARD = 0.75
OVERFIT_COVERAGE_WARN = 0.67
OVERFIT_COVERAGE_HIGH = 0.45
OVERFIT_COVERAGE_HARD = 0.34
OVERFIT_SIDE_GAP_WARN = 0.55
OVERFIT_SIDE_GAP_HIGH = 0.85
OVERFIT_WEAK_SIDE_WARN = 0.15
OVERFIT_CAPTURE_DROP_WARN = 0.20
OVERFIT_CAPTURE_DROP_HIGH = 0.35
DURATION_SHORT_MAX_BARS = 96
DURATION_MEDIUM_MAX_BARS = 288
VOLATILITY_LOW_MAX_ATR_RATIO = 0.010
VOLATILITY_MEDIUM_MAX_ATR_RATIO = 0.016
TREND_INIT_MOVE_MULTIPLIER = 3.0
TREND_INIT_MOVE_FLOOR = 0.04
TREND_SEGMENT_MOVE_MULTIPLIER = 4.0
TREND_SEGMENT_MOVE_FLOOR = 0.05
TREND_REVERSAL_MOVE_MULTIPLIER = 2.0
TREND_REVERSAL_MOVE_FLOOR = 0.025
TREND_MIN_SEGMENT_BARS = 3
MIN_VALIDATION_BLOCK_POINTS = 60
TRAIN_VAL_SCORE_WEIGHT = 0.50
PROMOTION_CAPTURE_SCORE_WEIGHT = 0.75
PROMOTION_TIMED_RETURN_SCORE_WEIGHT = 0.15
PROMOTION_TURN_PROTECTION_SCORE_WEIGHT = 0.10
TURN_PROTECTION_NEUTRAL_DD_PCT = 12.0
TURN_PROTECTION_DD_STEP_PCT = 6.0
TURN_PROTECTION_SCORE_MIN = -1.0
TURN_PROTECTION_SCORE_MAX = 1.0


# ==================== 基础统计 ====================


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def _quantile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    if abs(denominator) <= 1e-9:
        return default
    return numerator / denominator


def _annualized_sharpe(daily_returns: list[float]) -> float:
    if len(daily_returns) < 8:
        return 0.0
    avg = _mean(daily_returns)
    deviation = _std(daily_returns)
    if deviation <= 1e-12:
        return 0.0
    return avg / deviation * math.sqrt(365.0)


def _annualized_sortino(daily_returns: list[float]) -> float:
    if len(daily_returns) < 8:
        return 0.0
    avg = _mean(daily_returns)
    downside = [min(0.0, value) for value in daily_returns]
    downside_deviation = math.sqrt(sum(value * value for value in downside) / len(downside))
    if downside_deviation <= 1e-12:
        return 0.0
    return avg / downside_deviation * math.sqrt(365.0)


# ==================== 聚合诊断 ====================


def _window_payloads(results: list[dict[str, Any]], group: str | None) -> list[dict[str, Any]]:
    if group is None:
        return list(results)
    return [item for item in results if item["window"].group == group]


def _result_daily_return_points(result: dict[str, Any], fallback_prefix: str) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    return_points = result.get("daily_return_points", [])
    if return_points:
        for point in return_points:
            day = str(point.get("date", "")).strip()
            if not day:
                continue
            points.append((day, float(point.get("return", 0.0))))
        return points
    return [
        (f"{fallback_prefix}:{index}", float(value))
        for index, value in enumerate(result.get("daily_returns", []))
    ]


def _collect_daily_path(results: list[dict[str, Any]], group: str) -> DailyReturnPath:
    assigned_returns: dict[str, float] = {}
    seen_counts: dict[str, int] = {}
    point_count = 0
    for item in _window_payloads(results, group):
        for day, value in _result_daily_return_points(item["result"], item["window"].label):
            assigned_returns[day] = value
            seen_counts[day] = seen_counts.get(day, 0) + 1
            point_count += 1

    ordered_days = sorted(assigned_returns)
    overlap_days = sum(1 for count in seen_counts.values() if count > 1)
    return DailyReturnPath(
        returns=[assigned_returns[day] for day in ordered_days],
        unique_days=len(ordered_days),
        overlap_days=overlap_days,
        dropped_points=max(0, point_count - len(ordered_days)),
    )


def _result_trend_capture_points(result: dict[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for point in result.get("trend_capture_points", []):
        timestamp = point.get("timestamp")
        if timestamp in (None, ""):
            continue
        try:
            normalized_timestamp = int(timestamp)
        except (TypeError, ValueError):
            continue
        strategy_return = point.get("strategy_return")
        if strategy_return in (None, ""):
            normalized_return = None
        else:
            normalized_return = float(strategy_return)
        points.append(
            {
                "timestamp": normalized_timestamp,
                "label": str(point.get("label", normalized_timestamp)),
                "market_close": float(point.get("market_close", 0.0)),
                "atr_ratio": float(point.get("atr_ratio", 0.0)),
                "strategy_equity": float(point.get("strategy_equity", 0.0)),
                "strategy_return": normalized_return,
            }
        )
    return points


def _normalize_trend_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_points = sorted(points, key=lambda item: int(item["timestamp"]))
    normalized_points: list[dict[str, Any]] = []
    for idx, point in enumerate(ordered_points):
        strategy_return = point.get("strategy_return")
        if strategy_return is None:
            if idx == 0:
                strategy_return = 0.0
            else:
                prev_equity = float(ordered_points[idx - 1].get("strategy_equity", 0.0))
                current_equity = float(point.get("strategy_equity", 0.0))
                strategy_return = _simple_return(prev_equity, current_equity)
        normalized = dict(point)
        normalized["strategy_return"] = float(strategy_return)
        normalized_points.append(normalized)
    return normalized_points


def _collect_trend_path(results: list[dict[str, Any]], group: str | None) -> PointSeriesPath:
    assigned_points: dict[int, dict[str, Any]] = {}
    seen_counts: dict[int, int] = {}
    point_count = 0
    for item in _window_payloads(results, group):
        for point in _result_trend_capture_points(item["result"]):
            timestamp = int(point["timestamp"])
            assigned_points[timestamp] = point
            seen_counts[timestamp] = seen_counts.get(timestamp, 0) + 1
            point_count += 1

    ordered_keys = sorted(assigned_points)
    overlap_points = sum(1 for count in seen_counts.values() if count > 1)
    normalized_points = _normalize_trend_points([assigned_points[key] for key in ordered_keys])

    return PointSeriesPath(
        points=normalized_points,
        unique_points=len(ordered_keys),
        overlap_points=overlap_points,
        dropped_points=max(0, point_count - len(ordered_keys)),
    )


def _aggregate_signal_stats(
    results: list[dict[str, Any]],
    group: str,
    *,
    stats_key: str = "signal_stats",
) -> list[str]:
    aggregate: dict[str, dict[str, float]] = {}
    for item in _window_payloads(results, group):
        signal_stats = item["result"].get(stats_key, {})
        for signal, stats in signal_stats.items():
            bucket = aggregate.setdefault(signal, {"pnl_amount": 0.0, "closed_trades": 0.0, "wins": 0.0})
            bucket["pnl_amount"] += float(stats.get("pnl_amount", 0.0))
            bucket["closed_trades"] += float(stats.get("closed_trades", 0.0))
            bucket["wins"] += float(stats.get("win_rate", 0.0)) * float(stats.get("closed_trades", 0.0)) / 100.0

    ranked = sorted(aggregate.items(), key=lambda item: item[1]["pnl_amount"])
    lines = []
    for signal, stats in ranked[:3]:
        trades = int(stats["closed_trades"])
        win_rate = _safe_ratio(stats["wins"], trades, default=0.0) * 100.0
        lines.append(f"{signal}: pnl={stats['pnl_amount']:.0f}, trades={trades}, win={win_rate:.0f}%")
    return lines


def _build_window_lines(results: list[dict[str, Any]], include_validation: bool) -> list[str]:
    lines: list[str] = []
    for item in results:
        window = item["window"]
        if window.group == "validation" and not include_validation:
            continue
        result = item["result"]
        group_label = {
            "eval": "train",
            "validation": "val",
            "test": "test",
        }.get(window.group, window.group)
        lines.append(
            f"{window.label}({group_label}) {window.start_date}~{window.end_date} | "
            f"收益{result['return']:.1f}% | 回撤{result['max_drawdown']:.1f}% | "
            f"交易{result['trades']}"
        )
    return lines


def _empty_funnel_counts() -> dict[str, dict[str, int]]:
    return {
        side: {
            "sideways_pass": 0,
            "outer_context_pass": 0,
            "path_pass": 0,
            "final_veto_pass": 0,
            "filled_entries": 0,
        }
        for side in ("long", "short")
    }


def _result_funnel_counts(result: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    counts = _empty_funnel_counts()
    if not isinstance(result, dict):
        return counts
    funnel_payload = result.get("strategy_funnel", {})
    if isinstance(funnel_payload, dict):
        for side in counts:
            side_payload = funnel_payload.get(side, {})
            if not isinstance(side_payload, dict):
                continue
            for stage in ("sideways_pass", "outer_context_pass", "path_pass", "final_veto_pass"):
                counts[side][stage] = int(side_payload.get(stage, 0) or 0)
    filled_payload = result.get("filled_side_entries", {})
    if isinstance(filled_payload, dict):
        for side in counts:
            counts[side]["filled_entries"] = int(filled_payload.get(side, 0) or 0)
    return counts


def _aggregate_funnel_counts(results: list[dict[str, Any]], group: str) -> dict[str, dict[str, int]]:
    aggregated = _empty_funnel_counts()
    for item in _window_payloads(results, group):
        result_counts = _result_funnel_counts(item.get("result"))
        for side in aggregated:
            for stage in aggregated[side]:
                aggregated[side][stage] += int(result_counts[side].get(stage, 0))
    return aggregated


def _format_funnel_line(label: str, counts: dict[str, int]) -> str:
    return (
        f"{label}: 横盘后{counts['sideways_pass']} -> outer_context {counts['outer_context_pass']} -> "
        f"path {counts['path_pass']} -> final_veto {counts['final_veto_pass']} -> 真正出单 {counts['filled_entries']}"
    )


def _append_funnel_metrics(metrics: dict[str, float], prefix: str, counts: dict[str, dict[str, int]]) -> None:
    for side in ("long", "short"):
        for stage, value in counts[side].items():
            metrics[f"{prefix}_{side}_{stage}"] = float(value)


def _funnel_choke_point_text(side: str, counts: dict[str, int]) -> str:
    side_label = "多头" if side == "long" else "空头"
    sideways = int(counts.get("sideways_pass", 0) or 0)
    outer = int(counts.get("outer_context_pass", 0) or 0)
    path = int(counts.get("path_pass", 0) or 0)
    final = int(counts.get("final_veto_pass", 0) or 0)
    filled = int(counts.get("filled_entries", 0) or 0)

    if sideways <= 0:
        return f"{side_label} 当前没有可用样本"
    if outer <= max(1, int(sideways * 0.03)):
        return f"{side_label} 主要卡在 outer_context（{sideways} -> {outer}）"
    if path <= 0:
        return f"{side_label} outer_context 已放行，但 path 仍未触达（{outer} -> 0）"
    if final <= 0:
        return f"{side_label} path 可达，但 final_veto 全死（{path} -> 0）"
    if filled <= 0:
        return f"{side_label} final_veto 可达，但真实出单仍为 0（{final} -> 0）"
    if filled < final:
        return f"{side_label} 最终成交仍有明显流失（{final} -> {filled}）"
    return f"{side_label} 已能稳定出单（{filled}）"


def _low_activity_signal_payload(
    *,
    validation_counts: dict[str, dict[str, int]],
    selection_counts: dict[str, dict[str, int]],
    validation_closed_trades: int,
    selection_closed_trades: int,
) -> dict[str, Any]:
    issues: list[str] = []
    tags: set[str] = set()
    for side, label in (("long", "多头"), ("short", "空头")):
        validation_side = validation_counts[side]
        selection_side = selection_counts[side]
        if validation_side["filled_entries"] == 0 and selection_side["filled_entries"] == 0:
            issues.append(
                f"{label}连续 0 单: train+val/val 出单 {selection_side['filled_entries']} / {validation_side['filled_entries']}"
            )
            tags.update({"remove_dead_gate", "widen_outer_context"})
        if (
            selection_side["sideways_pass"] >= 24
            and selection_side["outer_context_pass"] <= max(1, int(selection_side["sideways_pass"] * 0.03))
        ):
            issues.append(
                f"{label} outer_context 基本放不行: 横盘后 {selection_side['sideways_pass']}，outer_context 仅 {selection_side['outer_context_pass']}"
            )
            tags.add("widen_outer_context")
        if (
            selection_side["path_pass"] >= 8
            and selection_side["final_veto_pass"] <= max(1, int(selection_side["path_pass"] * 0.10))
        ):
            issues.append(
                f"{label} path 能过但 final_veto 基本全死: path {selection_side['path_pass']}，final_veto 仅 {selection_side['final_veto_pass']}"
            )
            tags.update({"merge_veto", "remove_dead_gate"})
    if selection_closed_trades < 12 or validation_closed_trades < 4:
        issues.append(
            f"总交易偏少: train+val/val 平仓 {selection_closed_trades} / {validation_closed_trades}"
        )
        tags.update({"remove_dead_gate", "widen_outer_context"})
    if not issues:
        return {
            "count": 0,
            "lines": [],
            "prompt_line": "",
            "tags": (),
        }
    ordered_tags = tuple(sorted(tags))
    return {
        "count": len(issues),
        "lines": [
            "低活动度信号（软触发，不是硬 gate）:",
            *[f"- {issue}" for issue in issues],
            (
                "- 下一轮优先做放宽/删减/合并类假设，允许 `change_tags` 使用 "
                + " / ".join(f"`{tag}`" for tag in ordered_tags)
                + "；先减少死分支、提高 reachability，不要继续加条件。"
            ),
        ],
        "prompt_line": (
            "低活动度软触发="
            + "；".join(issues[:3])
            + "；下一轮优先放宽/删减/合并，先提高 reachability。"
        ),
        "tags": ordered_tags,
    }


# ==================== 趋势切段评分 ====================


def _simple_return(start_value: float, end_value: float) -> float:
    if start_value <= 1e-9:
        return 0.0
    return end_value / start_value - 1.0


def _move_pct(start_price: float, end_price: float) -> float:
    if start_price <= 1e-9:
        return 0.0
    return abs(end_price / start_price - 1.0)


def _log_weight(start_price: float, end_price: float) -> float:
    if start_price <= 1e-9 or end_price <= 1e-9:
        return 0.0
    return abs(math.log(end_price / start_price))


def _trend_threshold(multiplier: float, floor: float, atr_ratio: float) -> float:
    return max(floor, multiplier * max(0.0, atr_ratio))


def _detect_major_trend_segments(points: list[dict[str, Any]]) -> list[TrendSegment]:
    if len(points) < 6:
        return []

    closes = [float(point.get("market_close", 0.0)) for point in points]
    atrs = [float(point.get("atr_ratio", 0.0)) for point in points]
    segments: list[TrendSegment] = []

    initial_high_idx = 0
    initial_low_idx = 0
    direction = 0
    pivot_idx = 0
    extreme_idx = 0
    for idx in range(1, len(closes)):
        if closes[idx] >= closes[initial_high_idx]:
            initial_high_idx = idx
        if closes[idx] <= closes[initial_low_idx]:
            initial_low_idx = idx

        up_move = _move_pct(closes[initial_low_idx], closes[initial_high_idx]) if initial_high_idx > initial_low_idx else 0.0
        down_move = _move_pct(closes[initial_high_idx], closes[initial_low_idx]) if initial_low_idx > initial_high_idx else 0.0
        if up_move >= _trend_threshold(
            TREND_INIT_MOVE_MULTIPLIER,
            TREND_INIT_MOVE_FLOOR,
            max(atrs[initial_low_idx], atrs[initial_high_idx]),
        ):
            pivot_idx = initial_low_idx
            extreme_idx = initial_high_idx
            direction = 1
            break
        if down_move >= _trend_threshold(
            TREND_INIT_MOVE_MULTIPLIER,
            TREND_INIT_MOVE_FLOOR,
            max(atrs[initial_low_idx], atrs[initial_high_idx]),
        ):
            pivot_idx = initial_high_idx
            extreme_idx = initial_low_idx
            direction = -1
            break

    if direction == 0:
        return []

    for idx in range(max(pivot_idx, extreme_idx) + 1, len(closes)):
        price = closes[idx]
        if direction > 0:
            if price >= closes[extreme_idx]:
                extreme_idx = idx
                continue
            pullback = _move_pct(closes[extreme_idx], price)
            move_pct = _move_pct(closes[pivot_idx], closes[extreme_idx])
            atr_ratio = max(atrs[pivot_idx], atrs[extreme_idx])
            min_segment = _trend_threshold(TREND_SEGMENT_MOVE_MULTIPLIER, TREND_SEGMENT_MOVE_FLOOR, atr_ratio)
            min_reversal = _trend_threshold(
                TREND_REVERSAL_MOVE_MULTIPLIER,
                TREND_REVERSAL_MOVE_FLOOR,
                max(atrs[extreme_idx], atrs[idx]),
            )
            if move_pct < min_segment:
                if price <= closes[pivot_idx]:
                    direction = -1
                    extreme_idx = idx
                continue
            if (
                pullback >= min_reversal
                and move_pct >= min_segment
                and extreme_idx - pivot_idx >= TREND_MIN_SEGMENT_BARS
            ):
                weight = _log_weight(closes[pivot_idx], closes[extreme_idx])
                if weight > 1e-9:
                    segments.append(
                        TrendSegment(
                            direction=1,
                            start_idx=pivot_idx,
                            end_idx=extreme_idx,
                            move_pct=move_pct,
                            weight=weight,
                        )
                    )
                pivot_idx = extreme_idx
                extreme_idx = idx
                direction = -1
        else:
            if price <= closes[extreme_idx]:
                extreme_idx = idx
                continue
            rebound = _move_pct(closes[extreme_idx], price)
            move_pct = _move_pct(closes[pivot_idx], closes[extreme_idx])
            atr_ratio = max(atrs[pivot_idx], atrs[extreme_idx])
            min_segment = _trend_threshold(TREND_SEGMENT_MOVE_MULTIPLIER, TREND_SEGMENT_MOVE_FLOOR, atr_ratio)
            min_reversal = _trend_threshold(
                TREND_REVERSAL_MOVE_MULTIPLIER,
                TREND_REVERSAL_MOVE_FLOOR,
                max(atrs[extreme_idx], atrs[idx]),
            )
            if move_pct < min_segment:
                if price >= closes[pivot_idx]:
                    direction = 1
                    extreme_idx = idx
                continue
            if (
                rebound >= min_reversal
                and move_pct >= min_segment
                and extreme_idx - pivot_idx >= TREND_MIN_SEGMENT_BARS
            ):
                weight = _log_weight(closes[pivot_idx], closes[extreme_idx])
                if weight > 1e-9:
                    segments.append(
                        TrendSegment(
                            direction=-1,
                            start_idx=pivot_idx,
                            end_idx=extreme_idx,
                            move_pct=move_pct,
                            weight=weight,
                        )
                    )
                pivot_idx = extreme_idx
                extreme_idx = idx
                direction = 1

    final_move = _move_pct(closes[pivot_idx], closes[extreme_idx])
    final_threshold = _trend_threshold(
        TREND_SEGMENT_MOVE_MULTIPLIER,
        TREND_SEGMENT_MOVE_FLOOR,
        max(atrs[pivot_idx], atrs[extreme_idx]),
    )
    if extreme_idx - pivot_idx >= TREND_MIN_SEGMENT_BARS and final_move >= final_threshold:
        weight = _log_weight(closes[pivot_idx], closes[extreme_idx])
        if weight > 1e-9:
            segments.append(
                TrendSegment(
                    direction=1 if closes[extreme_idx] >= closes[pivot_idx] else -1,
                    start_idx=pivot_idx,
                    end_idx=extreme_idx,
                    move_pct=final_move,
                    weight=weight,
                )
            )
    return segments


def _capture_ratio(
    *,
    start_market: float,
    end_market: float,
    strategy_return: float,
    direction: int,
) -> float:
    market_return = _simple_return(start_market, end_market)
    return _clamp(direction * _safe_ratio(strategy_return, abs(market_return), default=0.0), -1.0, 3.0)


def _point_window_drawdown_pct(points: list[dict[str, Any]], start_idx: int, end_idx: int) -> float:
    if not points or end_idx <= start_idx:
        return 0.0
    start_idx = max(0, min(start_idx, len(points) - 1))
    end_idx = max(start_idx, min(end_idx, len(points) - 1))
    peak = float(points[start_idx].get("strategy_equity", 0.0))
    max_drawdown = 0.0
    for idx in range(start_idx, end_idx + 1):
        equity = float(points[idx].get("strategy_equity", 0.0))
        peak = max(peak, equity)
        if peak > 0.0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
    return max_drawdown


def _turn_protection_score_from_drawdown(drawdown_pct: float) -> float:
    return _clamp(
        (TURN_PROTECTION_NEUTRAL_DD_PCT - drawdown_pct) / TURN_PROTECTION_DD_STEP_PCT,
        TURN_PROTECTION_SCORE_MIN,
        TURN_PROTECTION_SCORE_MAX,
    )


def _segment_split_indices(segment: TrendSegment) -> tuple[int, int]:
    span = segment.end_idx - segment.start_idx
    arrival_end = min(segment.end_idx - 1, segment.start_idx + max(1, int(math.ceil(span * 0.25))))
    escort_end = min(segment.end_idx, segment.start_idx + max(arrival_end - segment.start_idx + 1, int(math.ceil(span * 0.75))))
    escort_end = max(arrival_end + 1, escort_end)
    escort_end = min(segment.end_idx, escort_end)
    return arrival_end, escort_end


def _weighted_average(pairs: list[tuple[float, float]]) -> float:
    total_weight = sum(weight for _, weight in pairs if weight > 0.0)
    if total_weight <= 1e-9:
        return 0.0
    return sum(value * weight for value, weight in pairs if weight > 0.0) / total_weight


def _compound_strategy_return(points: list[dict[str, Any]], start_idx: int, end_idx: int) -> float:
    if end_idx <= start_idx:
        return 0.0
    growth = 1.0
    for idx in range(start_idx + 1, end_idx + 1):
        step_return = float(points[idx].get("strategy_return", 0.0))
        growth *= max(1e-9, 1.0 + step_return)
    return growth - 1.0


def _return_score(points: list[dict[str, Any]]) -> tuple[float, float]:
    if len(points) < 2:
        return 0.0, 0.0
    growth = 1.0
    for idx in range(1, len(points)):
        growth *= max(1e-9, 1.0 + float(points[idx].get("strategy_return", 0.0)))
    path_return_pct = (growth - 1.0) * 100.0
    score = _clamp(math.log(max(growth, 1e-9), 2.0), -2.0, 3.0)
    return score, path_return_pct


def _annualized_return_score(daily_returns: list[float]) -> float:
    if not daily_returns:
        return 0.0
    growth = 1.0
    for value in daily_returns:
        growth *= max(1e-9, 1.0 + float(value))
    annualized_growth = max(growth, 1e-9) ** (365.0 / len(daily_returns))
    return _clamp(math.log(max(annualized_growth, 1e-9), 2.0), -2.0, 3.0)


def _trend_score_report(points: list[dict[str, Any]]) -> TrendScoreReport:
    if len(points) < 6:
        return TrendScoreReport(
            trend_score=0.0,
            return_score=0.0,
            arrival_score=0.0,
            escort_score=0.0,
            turn_score=0.0,
            turn_protection_score=0.0,
            turn_protection_event_count=0,
            bull_score=0.0,
            bear_score=0.0,
            hit_rate=0.0,
            segment_count=0,
            bull_segment_count=0,
            bear_segment_count=0,
            path_return_pct=0.0,
            segment_details=(),
        )

    segments = _detect_major_trend_segments(points)
    return_score, path_return_pct = _return_score(points)
    if not segments:
        return TrendScoreReport(
            trend_score=0.0,
            return_score=return_score,
            arrival_score=0.0,
            escort_score=0.0,
            turn_score=0.0,
            turn_protection_score=0.0,
            turn_protection_event_count=0,
            bull_score=0.0,
            bear_score=0.0,
            hit_rate=0.0,
            segment_count=0,
            bull_segment_count=0,
            bear_segment_count=0,
            path_return_pct=path_return_pct,
            segment_details=(),
        )

    arrival_pairs: list[tuple[float, float]] = []
    escort_pairs: list[tuple[float, float]] = []
    turn_pairs: list[tuple[float, float]] = []
    turn_protection_pairs: list[tuple[float, float]] = []
    bull_pairs: list[tuple[float, float]] = []
    bear_pairs: list[tuple[float, float]] = []
    segment_pairs: list[tuple[float, float]] = []
    segment_details: list[SegmentScoreDetail] = []
    hit_count = 0
    bull_segment_count = 0
    bear_segment_count = 0

    for idx, segment in enumerate(segments):
        arrival_end, escort_end = _segment_split_indices(segment)
        start_point = points[segment.start_idx]
        arrival_point = points[arrival_end]
        escort_point = points[escort_end]
        end_point = points[segment.end_idx]

        arrival_score = _capture_ratio(
            start_market=float(start_point["market_close"]),
            end_market=float(arrival_point["market_close"]),
            strategy_return=_compound_strategy_return(points, segment.start_idx, arrival_end),
            direction=segment.direction,
        )
        escort_score = _capture_ratio(
            start_market=float(arrival_point["market_close"]),
            end_market=float(escort_point["market_close"]),
            strategy_return=_compound_strategy_return(points, arrival_end, escort_end),
            direction=segment.direction,
        )

        weighted_score = 0.30 * arrival_score + 0.50 * escort_score
        weighted_sum = 0.80

        arrival_pairs.append((arrival_score, segment.weight))
        escort_pairs.append((escort_score, segment.weight))

        next_segment = segments[idx + 1] if idx + 1 < len(segments) else None
        if next_segment is not None and next_segment.direction != segment.direction:
            next_arrival_end, _ = _segment_split_indices(next_segment)
            turn_start = segment.end_idx
            turn_end = max(turn_start + 1, next_arrival_end)
            turn_end = min(next_segment.end_idx, turn_end)
            if turn_end > turn_start:
                turn_start_point = points[turn_start]
                turn_end_point = points[turn_end]
                turn_score = _capture_ratio(
                    start_market=float(turn_start_point["market_close"]),
                    end_market=float(turn_end_point["market_close"]),
                    strategy_return=_compound_strategy_return(points, turn_start, turn_end),
                    direction=next_segment.direction,
                )
                weighted_score += 0.20 * turn_score
                weighted_sum += 0.20
                turn_pairs.append((turn_score, segment.weight))
                protection_start = max(segment.start_idx, escort_end)
                protection_end = turn_end
                protection_drawdown = _point_window_drawdown_pct(points, protection_start, protection_end)
                turn_protection_pairs.append(
                    (_turn_protection_score_from_drawdown(protection_drawdown), segment.weight)
                )

        segment_score = weighted_score / max(weighted_sum, 1e-9)
        segment_pairs.append((segment_score, segment.weight))
        avg_atr_ratio = _mean([
            float(point.get("atr_ratio", 0.0))
            for point in points[segment.start_idx:segment.end_idx + 1]
        ])
        segment_details.append(
            SegmentScoreDetail(
                direction=segment.direction,
                score=segment_score,
                weight=segment.weight,
                duration_bars=segment.end_idx - segment.start_idx,
                avg_atr_ratio=avg_atr_ratio,
            )
        )
        if segment_score >= HIT_SCORE_THRESHOLD:
            hit_count += 1
        if segment.direction > 0:
            bull_pairs.append((segment_score, segment.weight))
            bull_segment_count += 1
        else:
            bear_pairs.append((segment_score, segment.weight))
            bear_segment_count += 1

    trend_score = _weighted_average(segment_pairs)
    return TrendScoreReport(
        trend_score=trend_score,
        return_score=return_score,
        arrival_score=_weighted_average(arrival_pairs),
        escort_score=_weighted_average(escort_pairs),
        turn_score=_weighted_average(turn_pairs),
        turn_protection_score=_weighted_average(turn_protection_pairs),
        turn_protection_event_count=len(turn_protection_pairs),
        bull_score=_weighted_average(bull_pairs),
        bear_score=_weighted_average(bear_pairs),
        hit_rate=_safe_ratio(hit_count, len(segments)),
        segment_count=len(segments),
        bull_segment_count=bull_segment_count,
        bear_segment_count=bear_segment_count,
        path_return_pct=path_return_pct,
        segment_details=tuple(segment_details),
    )


def _duration_bucket(duration_bars: int) -> str:
    if duration_bars < DURATION_SHORT_MAX_BARS:
        return "short"
    if duration_bars < DURATION_MEDIUM_MAX_BARS:
        return "medium"
    return "long"


def _volatility_bucket(avg_atr_ratio: float) -> str:
    if avg_atr_ratio < VOLATILITY_LOW_MAX_ATR_RATIO:
        return "low"
    if avg_atr_ratio < VOLATILITY_MEDIUM_MAX_ATR_RATIO:
        return "medium"
    return "high"


def _band_score(value: float, warn_threshold: float, high_threshold: float) -> float:
    if value > high_threshold:
        return 20.0
    if value > warn_threshold:
        return 10.0
    return 0.0


def _reverse_band_score(value: float, warn_threshold: float, high_threshold: float) -> float:
    if value < high_threshold:
        return 20.0
    if value < warn_threshold:
        return 10.0
    return 0.0


def overfit_risk_level_from_score(score: float) -> str:
    if score >= OVERFIT_GATE_SCORE:
        return "严重"
    if score >= OVERFIT_HIGH_SCORE:
        return "高"
    if score >= OVERFIT_WARN_SCORE:
        return "观察"
    return "低"


def overfit_reference_action(score: float, hard_fail: bool) -> str:
    if hard_fail or score >= OVERFIT_GATE_SCORE:
        return "直接淘汰"
    if score >= OVERFIT_HIGH_SCORE:
        return "慎重参考"
    if score >= OVERFIT_WARN_SCORE:
        return "谨慎借鉴"
    return "正常参考"


def _overfit_risk_report(trend_report: TrendScoreReport, capture_drop: float) -> OverfitRiskReport:
    details = list(trend_report.segment_details)
    positive_contributions = [
        max(0.0, detail.score) * max(detail.weight, 0.0)
        for detail in details
    ]
    positive_total = sum(positive_contributions)
    top1_positive_share = (
        max(positive_contributions) / positive_total
        if positive_total > 1e-9 else 0.0
    )

    max_chain_positive = 0.0
    current_chain_positive = 0.0
    current_direction: int | None = None
    for detail, contribution in zip(details, positive_contributions):
        if current_direction is None or detail.direction != current_direction:
            max_chain_positive = max(max_chain_positive, current_chain_positive)
            current_direction = detail.direction
            current_chain_positive = contribution
        else:
            current_chain_positive += contribution
    max_chain_positive = max(max_chain_positive, current_chain_positive)
    max_chain_positive_share = (
        max_chain_positive / positive_total
        if positive_total > 1e-9 else 0.0
    )

    hit_details = [detail for detail in details if detail.score >= HIT_SCORE_THRESHOLD]
    duration_populated = {_duration_bucket(detail.duration_bars) for detail in details}
    duration_covered = {_duration_bucket(detail.duration_bars) for detail in hit_details}
    duration_coverage_ratio = _safe_ratio(len(duration_covered), len(duration_populated), default=1.0)

    volatility_populated = {_volatility_bucket(detail.avg_atr_ratio) for detail in details}
    volatility_covered = {_volatility_bucket(detail.avg_atr_ratio) for detail in hit_details}
    volatility_coverage_ratio = _safe_ratio(len(volatility_covered), len(volatility_populated), default=1.0)

    coverage_parts = [
        ratio for ratio, populated in (
            (duration_coverage_ratio, duration_populated),
            (volatility_coverage_ratio, volatility_populated),
        )
        if populated
    ]
    coverage_ratio = _mean(coverage_parts) if coverage_parts else 1.0

    weak_side_capture_score = min(trend_report.bull_score, trend_report.bear_score)
    bull_bear_gap = abs(trend_report.bull_score - trend_report.bear_score)
    capture_drop_abs = abs(capture_drop)

    risk_score = 0.0
    if positive_total > 1e-9:
        risk_score += _band_score(top1_positive_share, OVERFIT_TOP1_WARN, OVERFIT_TOP1_HIGH)
        risk_score += _band_score(max_chain_positive_share, OVERFIT_CHAIN_WARN, OVERFIT_CHAIN_HIGH)
    if hit_details:
        risk_score += _reverse_band_score(coverage_ratio, OVERFIT_COVERAGE_WARN, OVERFIT_COVERAGE_HIGH)
    if bull_bear_gap > OVERFIT_SIDE_GAP_HIGH and weak_side_capture_score < 0.0:
        risk_score += 20.0
    elif bull_bear_gap > OVERFIT_SIDE_GAP_WARN and weak_side_capture_score < OVERFIT_WEAK_SIDE_WARN:
        risk_score += 10.0
    if capture_drop_abs > OVERFIT_CAPTURE_DROP_HIGH:
        risk_score += 20.0
    elif capture_drop_abs > OVERFIT_CAPTURE_DROP_WARN:
        risk_score += 10.0

    hard_reasons: list[str] = []
    if top1_positive_share > OVERFIT_TOP1_HARD:
        hard_reasons.append(f"单段正向贡献占比过高({top1_positive_share:.0%})")
    if max_chain_positive_share > OVERFIT_CHAIN_HARD:
        hard_reasons.append(f"同向连续段贡献占比过高({max_chain_positive_share:.0%})")
    if coverage_ratio < OVERFIT_COVERAGE_HARD and bull_bear_gap > OVERFIT_SIDE_GAP_HIGH:
        hard_reasons.append(
            f"有效覆盖率过低且多空偏科严重({coverage_ratio:.0%}, gap={bull_bear_gap:.2f})"
        )

    risk_score = min(100.0, risk_score)
    return OverfitRiskReport(
        risk_score=risk_score,
        risk_level=overfit_risk_level_from_score(risk_score),
        top1_positive_share=top1_positive_share,
        max_chain_positive_share=max_chain_positive_share,
        duration_coverage_ratio=duration_coverage_ratio,
        volatility_coverage_ratio=volatility_coverage_ratio,
        coverage_ratio=coverage_ratio,
        bull_bear_gap=bull_bear_gap,
        weak_side_capture_score=weak_side_capture_score,
        capture_drop_abs=capture_drop_abs,
        hard_fail=bool(hard_reasons),
        hard_reasons=tuple(hard_reasons),
    )


def _trend_report_from_result(result: dict[str, Any] | None) -> TrendScoreReport:
    if not result:
        return _trend_score_report([])
    points = _normalize_trend_points(_result_trend_capture_points(result))
    return _trend_score_report(points)


def _validation_block_report(
    result: dict[str, Any] | None,
    *,
    block_count: int,
    fallback_score: float,
) -> ValidationBlockReport:
    points = _normalize_trend_points(_result_trend_capture_points(result or {}))
    if block_count <= 1 or len(points) < MIN_VALIDATION_BLOCK_POINTS:
        return ValidationBlockReport(
            block_scores=(),
            mean_score=fallback_score,
            std_score=0.0,
            min_score=fallback_score,
            fail_count=1 if fallback_score < 0.0 else 0,
            used_block_count=0,
        )

    actual_block_count = min(block_count, len(points))
    if actual_block_count <= 1:
        return ValidationBlockReport(
            block_scores=(),
            mean_score=fallback_score,
            std_score=0.0,
            min_score=fallback_score,
            fail_count=1 if fallback_score < 0.0 else 0,
            used_block_count=0,
        )

    base_size, remainder = divmod(len(points), actual_block_count)
    block_scores: list[float] = []
    start_idx = 0
    for block_index in range(actual_block_count):
        extra = 1 if block_index < remainder else 0
        end_idx = start_idx + base_size + extra
        block_points = points[start_idx:end_idx]
        if block_points:
            block_report = _trend_score_report(block_points)
            block_scores.append(0.70 * block_report.trend_score + 0.30 * block_report.return_score)
        start_idx = end_idx

    if not block_scores:
        return ValidationBlockReport(
            block_scores=(),
            mean_score=fallback_score,
            std_score=0.0,
            min_score=fallback_score,
            fail_count=1 if fallback_score < 0.0 else 0,
            used_block_count=0,
        )

    return ValidationBlockReport(
        block_scores=tuple(block_scores),
        mean_score=_mean(block_scores),
        std_score=_std(block_scores),
        min_score=min(block_scores),
        fail_count=sum(1 for score in block_scores if score < 0.0),
        used_block_count=len(block_scores),
    )


def partial_eval_gate_snapshot(result: dict[str, Any] | None) -> dict[str, float]:
    points = _normalize_trend_points(_result_trend_capture_points(result or {}))
    report = _trend_score_report(points)
    return {
        "segment_count": float(report.segment_count),
        "trend_score": report.trend_score,
        "return_score": report.return_score,
        "period_score": _period_score(report),
        "hit_rate": report.hit_rate,
        "path_return_pct": report.path_return_pct,
        "unique_points": float(len(points)),
    }


def _period_score(trend_report: TrendScoreReport) -> float:
    return 0.70 * trend_report.trend_score + 0.30 * trend_report.return_score


def _trade_side_counts(result: dict[str, Any] | None) -> tuple[int, int]:
    trades = list((result or {}).get("trades_detail", []))
    long_count = 0
    short_count = 0
    for trade in trades:
        signal = str(trade.get("entry_signal", "")).strip().lower()
        if signal.startswith("long_"):
            long_count += 1
        elif signal.startswith("short_"):
            short_count += 1
    return long_count, short_count


def _validation_weakest_axis(
    trend_report: TrendScoreReport,
    block_report: ValidationBlockReport,
) -> str:
    if block_report.used_block_count >= 2 and block_report.block_scores:
        tail_score = float(block_report.block_scores[-1])
        if tail_score < block_report.mean_score - 0.15:
            return "val后半段明显偏弱"

    candidates = [
        ("到来能力", trend_report.arrival_score),
        ("陪跑能力", trend_report.escort_score),
        ("掉头处理", trend_report.turn_score),
        ("多头捕获", trend_report.bull_score),
        ("空头捕获", trend_report.bear_score),
    ]
    weakest_label, _ = min(candidates, key=lambda item: item[1])
    return f"val短板={weakest_label}"


def summarize_hidden_test_result(result: dict[str, Any] | None) -> dict[str, float]:
    trend_report = _trend_report_from_result(result)
    long_trades, short_trades = _trade_side_counts(result)
    daily_returns = [float(value) for value in (result or {}).get("daily_returns", [])]
    return {
        "shadow_test_score": _period_score(trend_report),
        "shadow_test_trend_capture_score": trend_report.trend_score,
        "shadow_test_return_score": trend_report.return_score,
        "shadow_test_arrival_score": trend_report.arrival_score,
        "shadow_test_escort_score": trend_report.escort_score,
        "shadow_test_turn_score": trend_report.turn_score,
        "shadow_test_bull_capture_score": trend_report.bull_score,
        "shadow_test_bear_capture_score": trend_report.bear_score,
        "shadow_test_hit_rate": trend_report.hit_rate,
        "shadow_test_segment_count": float(trend_report.segment_count),
        "shadow_test_path_return_pct": trend_report.path_return_pct,
        "shadow_test_total_return_pct": float((result or {}).get("return", 0.0)),
        "shadow_test_max_drawdown": float((result or {}).get("max_drawdown", 0.0)),
        "shadow_test_fee_drag_pct": float((result or {}).get("fee_drag_pct", 0.0)),
        "shadow_test_sharpe_ratio": _annualized_sharpe(daily_returns),
        "shadow_test_closed_trades": float((result or {}).get("trades", long_trades + short_trades)),
        "shadow_test_long_closed_trades": float(long_trades),
        "shadow_test_short_closed_trades": float(short_trades),
    }


# ==================== 总分计算 ====================


def summarize_evaluation(
    results: list[dict[str, Any]],
    gates: GateConfig,
    selection_period_result: dict[str, Any] | None = None,
    validation_continuous_result: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> EvaluationReport:
    if selection_period_result is None:
        selection_period_result = _kwargs.get("full_period_result")
    eval_results = _window_payloads(results, "eval")
    validation_results = _window_payloads(results, "validation")
    validation_source = validation_continuous_result
    if validation_source is None and len(validation_results) == 1:
        validation_source = validation_results[0]["result"]
    validation_source = validation_source or {}
    selection_source = selection_period_result or {}

    eval_returns = [float(item["result"].get("return", 0.0)) for item in eval_results]
    validation_returns = [float(item["result"].get("return", 0.0)) for item in validation_results]
    eval_avg_return = _mean(eval_returns)
    eval_median_return = median(eval_returns) if eval_returns else 0.0
    eval_p25_return = _quantile(eval_returns, 0.25)
    eval_worst_return = min(eval_returns) if eval_returns else 0.0
    validation_avg_return = _mean(validation_returns)
    validation_worst_return = min(validation_returns) if validation_returns else 0.0

    worst_drawdown = max((float(item["result"].get("max_drawdown", 0.0)) for item in results), default=0.0)
    avg_fee_drag = _mean([float(item["result"].get("fee_drag_pct", 0.0)) for item in results])
    eval_funding_coverage = _mean([float(item["result"].get("funding_coverage_ratio", 0.0)) for item in eval_results])
    validation_funding_coverage = float(validation_source.get("funding_coverage_ratio", 0.0))
    selection_funding_coverage = float(selection_source.get("funding_coverage_ratio", 0.0))
    liquidations = sum(int(item["result"].get("liquidations", 0)) for item in results)
    total_trades = sum(int(item["result"].get("trades", 0)) for item in results)
    eval_trades = sum(int(item["result"].get("trades", 0)) for item in eval_results)
    validation_trades = int(validation_source.get("trades", validation_results[0]["result"].get("trades", 0) if validation_results else 0))

    eval_daily_path = _collect_daily_path(results, "eval")
    validation_daily_path = _collect_daily_path(results, "validation")
    eval_path = _collect_trend_path(results, "eval")
    validation_path = _collect_trend_path(results, "validation")

    development_window_reports = [
        _trend_report_from_result(item["result"])
        for item in eval_results
    ]
    development_window_scores = [_period_score(report) for report in development_window_reports]
    development_mean_score = _mean(development_window_scores)
    development_median_score = median(development_window_scores) if development_window_scores else 0.0
    development_score_std = _std(development_window_scores)
    profitable_window_ratio = _safe_ratio(
        sum(1 for score in development_window_scores if score > 0.0),
        len(development_window_scores),
        default=0.0,
    )
    development_mean_trend_score = _mean([report.trend_score for report in development_window_reports])
    development_mean_return_score = _mean([report.return_score for report in development_window_reports])
    development_mean_hit_rate = _mean([report.hit_rate for report in development_window_reports])
    development_mean_segment_count = _mean([float(report.segment_count) for report in development_window_reports])

    train_continuous_trend_report = _trend_score_report(eval_path.points)
    validation_trend_report = (
        _trend_score_report(validation_path.points)
        if validation_path.points
        else _trend_report_from_result(validation_source)
    )
    selection_trend_report = _trend_report_from_result(selection_source)

    train_capture_score = train_continuous_trend_report.trend_score
    validation_capture_score = validation_trend_report.trend_score
    capture_score = (
        TRAIN_VAL_SCORE_WEIGHT * train_capture_score
        + TRAIN_VAL_SCORE_WEIGHT * validation_capture_score
    )
    train_timed_return_score = _annualized_return_score(eval_daily_path.returns)
    validation_timed_return_score = _annualized_return_score(validation_daily_path.returns)
    timed_return_score = (
        TRAIN_VAL_SCORE_WEIGHT * train_timed_return_score
        + TRAIN_VAL_SCORE_WEIGHT * validation_timed_return_score
    )
    train_turn_protection_score = train_continuous_trend_report.turn_protection_score
    validation_turn_protection_score = validation_trend_report.turn_protection_score
    turn_protection_score = (
        TRAIN_VAL_SCORE_WEIGHT * train_turn_protection_score
        + TRAIN_VAL_SCORE_WEIGHT * validation_turn_protection_score
    )
    quality_score = train_capture_score
    raw_validation_score = _period_score(validation_trend_report)
    validation_block_report = _validation_block_report(
        validation_source,
        block_count=gates.validation_block_count,
        fallback_score=raw_validation_score,
    )
    capture_drop = train_capture_score - validation_capture_score
    promotion_gap = capture_drop
    overfit_report = _overfit_risk_report(selection_trend_report, capture_drop)
    promotion_score = (
        PROMOTION_CAPTURE_SCORE_WEIGHT * capture_score
        + PROMOTION_TIMED_RETURN_SCORE_WEIGHT * timed_return_score
        + PROMOTION_TURN_PROTECTION_SCORE_WEIGHT * turn_protection_score
    )
    validation_long_trades, validation_short_trades = _trade_side_counts(validation_source)
    selection_long_trades, selection_short_trades = _trade_side_counts(selection_source)
    validation_closed_trades = int(validation_source.get("trades", validation_long_trades + validation_short_trades))
    selection_closed_trades = int(selection_source.get("trades", selection_long_trades + selection_short_trades))
    eval_funnel_counts = _aggregate_funnel_counts(results, "eval")
    validation_funnel_counts = _result_funnel_counts(validation_source)
    selection_funnel_counts = _result_funnel_counts(selection_source)
    low_activity_payload = _low_activity_signal_payload(
        validation_counts=validation_funnel_counts,
        selection_counts=selection_funnel_counts,
        validation_closed_trades=validation_closed_trades,
        selection_closed_trades=selection_closed_trades,
    )
    validation_weakest_axis = _validation_weakest_axis(validation_trend_report, validation_block_report)
    selection_total_return = float(selection_source.get("return", 0.0))
    selection_max_drawdown = float(selection_source.get("max_drawdown", 0.0))
    selection_fee_drag_pct = float(selection_source.get("fee_drag_pct", 0.0))
    eval_sharpe_ratio = _annualized_sharpe(eval_daily_path.returns)
    validation_sharpe_ratio = _annualized_sharpe(validation_daily_path.returns)
    selection_sharpe_ratio = _annualized_sharpe([float(value) for value in selection_source.get("daily_returns", [])])

    gate_reasons: list[str] = []
    if development_mean_score < gates.min_development_mean_score:
        gate_reasons.append(f"train均值分偏低({development_mean_score:.2f})")
    if development_median_score < gates.min_development_median_score:
        gate_reasons.append(f"train中位分偏低({development_median_score:.2f})")
    if validation_trend_report.hit_rate < gates.min_validation_hit_rate:
        gate_reasons.append(f"val命中率偏低({validation_trend_report.hit_rate:.0%})")
    if validation_trend_report.trend_score < gates.min_validation_trend_score:
        gate_reasons.append(f"val趋势捕获分偏低({validation_trend_report.trend_score:.2f})")
    if promotion_gap > gates.max_dev_validation_gap:
        gate_reasons.append(f"train/val分数落差过大({promotion_gap:.2f})")
    if validation_trend_report.bull_score < gates.min_validation_bull_capture:
        gate_reasons.append(f"val多头捕获偏低({validation_trend_report.bull_score:.2f})")
    if validation_trend_report.bear_score < gates.min_validation_bear_capture:
        gate_reasons.append(f"val空头捕获偏低({validation_trend_report.bear_score:.2f})")
    if avg_fee_drag > gates.max_fee_drag_pct:
        gate_reasons.append(f"手续费拖累过高({avg_fee_drag:.2f}%)")
    if validation_block_report.used_block_count >= 2:
        if validation_block_report.min_score < gates.min_validation_block_floor:
            gate_reasons.append(
                "val最差分块过弱"
                f"({validation_block_report.min_score:.2f})"
            )
        if validation_block_report.fail_count > gates.max_validation_block_failures:
            gate_reasons.append(
                "val负分块过多"
                f"({validation_block_report.fail_count})"
            )
    if overfit_report.hard_fail:
        gate_reasons.append(
            "train+val过拟合集中度严重"
            f"({'; '.join(overfit_report.hard_reasons)})"
        )

    gate_passed = not gate_reasons
    gate_reason = "通过" if gate_passed else "；".join(gate_reasons)

    weakest_signals = _aggregate_signal_stats(results, "eval")
    weakest_signal_paths = _aggregate_signal_stats(results, "eval", stats_key="signal_path_stats")
    summary_lines = [
        "研究评估摘要（15m 为唯一事实源，1h/4h 只是由 15m 聚合的确认层；成交量诊断同时看总量、OKX K 线方向流量代理和成交活跃度）",
        (
            "train滚动分(均值/中位/std/盈利窗比): "
            f"{development_mean_score:.2f} / {development_median_score:.2f} / "
            f"{development_score_std:.2f} / {profitable_window_ratio:.0%}"
        ),
        (
            "train/val连续趋势抓取分 / 抓取主分: "
            f"{train_capture_score:.2f} / {validation_capture_score:.2f} / {capture_score:.2f}"
        ),
        (
            "train/val按日收益年化分 / 收益补充分 / 掉头保护分 / 晋级分: "
            f"{train_timed_return_score:.2f} / {validation_timed_return_score:.2f} / "
            f"{timed_return_score:.2f} / {turn_protection_score:.2f} / {promotion_score:.2f}"
        ),
        f"val到来 / 陪跑 / 掉头: {validation_trend_report.arrival_score:.2f} / {validation_trend_report.escort_score:.2f} / {validation_trend_report.turn_score:.2f}",
        (
            "train/val掉头保护分(事件数): "
            f"{train_turn_protection_score:.2f}({train_continuous_trend_report.turn_protection_event_count}) / "
            f"{validation_turn_protection_score:.2f}({validation_trend_report.turn_protection_event_count})"
        ),
        f"val多头 / 空头捕获: {validation_trend_report.bull_score:.2f} / {validation_trend_report.bear_score:.2f}",
        f"val趋势段 / 命中率: {validation_trend_report.segment_count} / {validation_trend_report.hit_rate:.0%}",
        f"val连续综合分 / 收益分: {raw_validation_score:.2f} / {validation_trend_report.return_score:.2f}",
        f"val多 / 空平仓数: {validation_long_trades} / {validation_short_trades}",
        _format_funnel_line("train滚动漏斗(long)", eval_funnel_counts["long"]),
        _format_funnel_line("train滚动漏斗(short)", eval_funnel_counts["short"]),
        _format_funnel_line("val连续漏斗(long)", validation_funnel_counts["long"]),
        _format_funnel_line("val连续漏斗(short)", validation_funnel_counts["short"]),
        f"val短板: {validation_weakest_axis}",
        (
            "val分块门控分(均值/std/最差/负分块): "
            f"{validation_block_report.mean_score:.2f} / "
            f"{validation_block_report.std_score:.2f} / "
            f"{validation_block_report.min_score:.2f} / "
            f"{validation_block_report.fail_count}"
            + (
                f" (分块数={validation_block_report.used_block_count})"
                if validation_block_report.used_block_count > 0 else " (未启用)"
            )
        ),
        (
            "train+val集中度诊断: "
            f"{overfit_report.risk_level}({overfit_report.risk_score:.0f})，"
            f"单段正向贡献={overfit_report.top1_positive_share:.0%}，"
            f"同向链贡献={overfit_report.max_chain_positive_share:.0%}，"
            f"覆盖率={overfit_report.coverage_ratio:.0%}，"
            f"多空落差={overfit_report.bull_bear_gap:.2f}，"
            f"处置={overfit_reference_action(overfit_report.risk_score, overfit_report.hard_fail)}"
        ),
        f"train+val连续趋势捕获分 / 收益分: {selection_trend_report.trend_score:.2f} / {selection_trend_report.return_score:.2f}",
        f"train+val连续到来 / 陪跑 / 掉头: {selection_trend_report.arrival_score:.2f} / {selection_trend_report.escort_score:.2f} / {selection_trend_report.turn_score:.2f}",
        f"train+val连续多头 / 空头捕获: {selection_trend_report.bull_score:.2f} / {selection_trend_report.bear_score:.2f}",
        f"train+val期间收益 / 路径收益: {selection_total_return:.2f}% / {selection_trend_report.path_return_pct:.2f}%",
        f"Sharpe(train / val / train+val): {eval_sharpe_ratio:.2f} / {validation_sharpe_ratio:.2f} / {selection_sharpe_ratio:.2f}",
        f"train窗口收益均值 / 中位 / P25 / 最差: {eval_avg_return:.2f}% / {eval_median_return:.2f}% / {eval_p25_return:.2f}% / {eval_worst_return:.2f}%",
        f"val窗口收益均值 / 最差: {validation_avg_return:.2f}% / {validation_worst_return:.2f}%",
        f"train/val趋势抓取落差: {promotion_gap:.2f}",
        f"train 4h唯一路径点 / 重叠点 / 被覆盖点: {eval_path.unique_points} / {eval_path.overlap_points} / {eval_path.dropped_points}",
        f"funding覆盖(train均值 / val / train+val): {eval_funding_coverage:.0%} / {validation_funding_coverage:.0%} / {selection_funding_coverage:.0%}",
        f"最大回撤 / 手续费拖累: {worst_drawdown:.2f}% / {avg_fee_drag:.2f}%",
        f"总交易 / train交易 / val交易 / 爆仓: {total_trades} / {eval_trades} / {validation_trades} / {liquidations}",
        f"质量分(train连续趋势分) / 晋级分: {quality_score:.2f} / {promotion_score:.2f}",
        f"Gate: {gate_reason}",
        "",
        "窗口明细:",
        *_build_window_lines(results, include_validation=True),
    ]
    if low_activity_payload["lines"]:
        summary_lines.extend(["", *low_activity_payload["lines"]])
    if weakest_signals:
        summary_lines.extend(["", "拖累较大的执行标签:", *weakest_signals])
    if weakest_signal_paths:
        summary_lines.extend(["", "拖累较大的路径标签:", *weakest_signal_paths])

    prompt_lines = [
        "当前诊断（必须先读）:",
        (
            f"- 当前基底: 质量分(train连续趋势分)={quality_score:.2f}，晋级分={promotion_score:.2f}，"
            f"抓取主分={capture_score:.2f}，收益补充分={timed_return_score:.2f}，"
            f"掉头保护分={turn_protection_score:.2f}，"
            f"gate={gate_reason}"
        ),
        f"- 当前主短板: {validation_weakest_axis}",
        f"- 当前 gate 主失败项: {gate_reason}",
        (
            f"- val 现状: 趋势段/命中率={validation_trend_report.segment_count}/"
            f"{validation_trend_report.hit_rate:.0%}，"
            f"多头/空头捕获={validation_trend_report.bull_score:.2f}/"
            f"{validation_trend_report.bear_score:.2f}，"
            f"多/空平仓数={validation_long_trades}/{validation_short_trades}"
        ),
        (
            "- val 漏斗堵点: "
            + _funnel_choke_point_text("long", validation_funnel_counts["long"])
            + "；"
            + _funnel_choke_point_text("short", validation_funnel_counts["short"])
        ),
        (
            f"- train 滚动状态: 均值/中位/std/盈利窗比="
            f"{development_mean_score:.2f}/{development_median_score:.2f}/"
            f"{development_score_std:.2f}/{profitable_window_ratio:.0%}"
        ),
        (
            f"- 当前评分组成: train/val 连续趋势抓取={train_capture_score:.2f}/{validation_capture_score:.2f}，"
            f"train/val 按日收益年化分={train_timed_return_score:.2f}/{validation_timed_return_score:.2f}，"
            f"train/val 掉头保护分={train_turn_protection_score:.2f}/{validation_turn_protection_score:.2f}"
        ),
        (
            f"- train+val 状态: 趋势分/收益分={selection_trend_report.trend_score:.2f}/"
            f"{selection_trend_report.return_score:.2f}，"
            f"多头/空头捕获={selection_trend_report.bull_score:.2f}/"
            f"{selection_trend_report.bear_score:.2f}，"
            f"期间收益={selection_total_return:.2f}%"
        ),
        (
            f"- 风险与成本: 最大回撤={worst_drawdown:.2f}%，"
            f"手续费拖累={avg_fee_drag:.2f}%，"
            f"train/val 抓取分差={promotion_gap:.2f}"
        ),
        (
            f"- 集中度诊断: {overfit_report.risk_level}({overfit_report.risk_score:.0f})，"
            f"覆盖率={overfit_report.coverage_ratio:.0%}，"
            f"多空落差={overfit_report.bull_bear_gap:.2f}，"
            f"处置={overfit_reference_action(overfit_report.risk_score, overfit_report.hard_fail)}"
        ),
    ]
    if low_activity_payload["prompt_line"]:
        prompt_lines.append(low_activity_payload["prompt_line"])
    if weakest_signals:
        prompt_lines.append("train拖累执行标签: " + " | ".join(weakest_signals))
    if weakest_signal_paths:
        prompt_lines.append("train拖累路径标签: " + " | ".join(weakest_signal_paths))

    metrics = {
        "development_mean_score": development_mean_score,
        "development_median_score": development_median_score,
        "development_score_std": development_score_std,
        "development_profitable_window_ratio": profitable_window_ratio,
        "development_window_count": float(len(development_window_scores)),
        "development_mean_trend_capture_score": development_mean_trend_score,
        "eval_avg_return": eval_avg_return,
        "eval_median_return": eval_median_return,
        "eval_p25_return": eval_p25_return,
        "eval_worst_return": eval_worst_return,
        "validation_avg_return": validation_avg_return,
        "validation_worst_return": validation_worst_return,
        "development_funding_coverage_ratio": eval_funding_coverage,
        "validation_funding_coverage_ratio": validation_funding_coverage,
        "selection_funding_coverage_ratio": selection_funding_coverage,
        "worst_drawdown": worst_drawdown,
        "avg_fee_drag": avg_fee_drag,
        "liquidations": float(liquidations),
        "total_trades": float(total_trades),
        "eval_trades": float(eval_trades),
        "validation_trades": float(validation_trades),
        "eval_unique_trend_points": float(eval_path.unique_points),
        "eval_overlap_trend_points": float(eval_path.overlap_points),
        "eval_overlap_trend_points_dropped": float(eval_path.dropped_points),
        "validation_unique_trend_points": float(validation_path.unique_points),
        "validation_overlap_trend_points": float(validation_path.overlap_points),
        "validation_overlap_trend_points_dropped": float(validation_path.dropped_points),
        "validation_score": raw_validation_score,
        "train_capture_score": train_capture_score,
        "validation_capture_score": validation_capture_score,
        "capture_score": capture_score,
        "train_timed_return_score": train_timed_return_score,
        "validation_timed_return_score": validation_timed_return_score,
        "timed_return_score": timed_return_score,
        "train_turn_protection_score": train_turn_protection_score,
        "validation_turn_protection_score": validation_turn_protection_score,
        "turn_protection_score": turn_protection_score,
        "train_turn_protection_event_count": float(train_continuous_trend_report.turn_protection_event_count),
        "validation_turn_protection_event_count": float(validation_trend_report.turn_protection_event_count),
        "eval_trend_capture_score": development_mean_trend_score,
        "eval_return_score": development_mean_return_score,
        "eval_segment_hit_rate": development_mean_hit_rate,
        "eval_major_segment_count": development_mean_segment_count,
        "validation_trend_capture_score": validation_trend_report.trend_score,
        "selection_trend_capture_score": selection_trend_report.trend_score,
        "combined_trend_capture_score": selection_trend_report.trend_score,
        "full_period_trend_capture_score": selection_trend_report.trend_score,
        "validation_return_score": validation_trend_report.return_score,
        "selection_return_score": selection_trend_report.return_score,
        "combined_return_score": selection_trend_report.return_score,
        "full_period_return_score": selection_trend_report.return_score,
        "validation_arrival_capture_score": validation_trend_report.arrival_score,
        "validation_escort_capture_score": validation_trend_report.escort_score,
        "validation_turn_adaptation_score": validation_trend_report.turn_score,
        "selection_arrival_capture_score": selection_trend_report.arrival_score,
        "selection_escort_capture_score": selection_trend_report.escort_score,
        "selection_turn_adaptation_score": selection_trend_report.turn_score,
        "selection_turn_protection_score": selection_trend_report.turn_protection_score,
        "arrival_capture_score": selection_trend_report.arrival_score,
        "escort_capture_score": selection_trend_report.escort_score,
        "turn_adaptation_score": selection_trend_report.turn_score,
        "selection_turn_protection_event_count": float(selection_trend_report.turn_protection_event_count),
        "validation_bull_capture_score": validation_trend_report.bull_score,
        "validation_bear_capture_score": validation_trend_report.bear_score,
        "selection_bull_capture_score": selection_trend_report.bull_score,
        "selection_bear_capture_score": selection_trend_report.bear_score,
        "bull_capture_score": selection_trend_report.bull_score,
        "bear_capture_score": selection_trend_report.bear_score,
        "validation_segment_hit_rate": validation_trend_report.hit_rate,
        "selection_segment_hit_rate": selection_trend_report.hit_rate,
        "segment_hit_rate": selection_trend_report.hit_rate,
        "full_period_segment_hit_rate": selection_trend_report.hit_rate,
        "validation_major_segment_count": float(validation_trend_report.segment_count),
        "selection_major_segment_count": float(selection_trend_report.segment_count),
        "major_segment_count": float(selection_trend_report.segment_count),
        "full_period_major_segment_count": float(selection_trend_report.segment_count),
        "validation_bull_segment_count": float(validation_trend_report.bull_segment_count),
        "validation_bear_segment_count": float(validation_trend_report.bear_segment_count),
        "validation_long_closed_trades": float(validation_long_trades),
        "validation_short_closed_trades": float(validation_short_trades),
        "selection_long_closed_trades": float(selection_long_trades),
        "selection_short_closed_trades": float(selection_short_trades),
        "validation_closed_trades": float(validation_closed_trades),
        "selection_closed_trades": float(selection_closed_trades),
        "validation_path_return_pct": validation_trend_report.path_return_pct,
        "validation_total_return_pct": float(validation_source.get("return", validation_avg_return)),
        "selection_path_return_pct": selection_trend_report.path_return_pct,
        "selection_total_return_pct": selection_total_return,
        "selection_max_drawdown": selection_max_drawdown,
        "selection_fee_drag_pct": selection_fee_drag_pct,
        "eval_sharpe_ratio": eval_sharpe_ratio,
        "validation_sharpe_ratio": validation_sharpe_ratio,
        "selection_sharpe_ratio": selection_sharpe_ratio,
        "combined_path_return_pct": selection_trend_report.path_return_pct,
        "full_period_return_pct": selection_total_return,
        "capture_drop": capture_drop,
        "dev_validation_gap": promotion_gap,
        "promotion_gap": promotion_gap,
        "validation_block_score_mean": validation_block_report.mean_score,
        "validation_block_score_std": validation_block_report.std_score,
        "validation_block_score_min": validation_block_report.min_score,
        "validation_block_fail_count": float(validation_block_report.fail_count),
        "validation_block_count_used": float(validation_block_report.used_block_count),
        "overfit_risk_score": overfit_report.risk_score,
        "overfit_top1_positive_share": overfit_report.top1_positive_share,
        "overfit_chain_positive_share": overfit_report.max_chain_positive_share,
        "overfit_duration_coverage_ratio": overfit_report.duration_coverage_ratio,
        "overfit_volatility_coverage_ratio": overfit_report.volatility_coverage_ratio,
        "overfit_coverage_ratio": overfit_report.coverage_ratio,
        "overfit_bull_bear_gap": overfit_report.bull_bear_gap,
        "overfit_weak_side_capture_score": overfit_report.weak_side_capture_score,
        "overfit_capture_drop_abs": overfit_report.capture_drop_abs,
        "overfit_hard_fail": 1.0 if overfit_report.hard_fail else 0.0,
        "low_activity_signal_count": float(low_activity_payload["count"]),
        "quality_score": quality_score,
        "promotion_score": promotion_score,
    }
    _append_funnel_metrics(metrics, "eval", eval_funnel_counts)
    _append_funnel_metrics(metrics, "validation", validation_funnel_counts)
    _append_funnel_metrics(metrics, "selection", selection_funnel_counts)
    return EvaluationReport(
        metrics=metrics,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        summary_text="\n".join(summary_lines),
        prompt_summary_text="\n".join(prompt_lines),
    )
