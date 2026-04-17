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
    bull_score: float
    bear_score: float
    hit_rate: float
    segment_count: int
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


def _aggregate_signal_stats(results: list[dict[str, Any]], group: str) -> list[str]:
    aggregate: dict[str, dict[str, float]] = {}
    for item in _window_payloads(results, group):
        signal_stats = item["result"].get("signal_stats", {})
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
        group_label = "评" if window.group == "eval" else "留"
        lines.append(
            f"{window.label}({group_label}) {window.start_date}~{window.end_date} | "
            f"收益{result['return']:.1f}% | 回撤{result['max_drawdown']:.1f}% | "
            f"交易{result['trades']}"
        )
    return lines


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


def _trend_score_report(points: list[dict[str, Any]]) -> TrendScoreReport:
    if len(points) < 6:
        return TrendScoreReport(
            trend_score=0.0,
            return_score=0.0,
            arrival_score=0.0,
            escort_score=0.0,
            turn_score=0.0,
            bull_score=0.0,
            bear_score=0.0,
            hit_rate=0.0,
            segment_count=0,
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
            bull_score=0.0,
            bear_score=0.0,
            hit_rate=0.0,
            segment_count=0,
            path_return_pct=path_return_pct,
            segment_details=(),
        )

    arrival_pairs: list[tuple[float, float]] = []
    escort_pairs: list[tuple[float, float]] = []
    turn_pairs: list[tuple[float, float]] = []
    bull_pairs: list[tuple[float, float]] = []
    bear_pairs: list[tuple[float, float]] = []
    segment_pairs: list[tuple[float, float]] = []
    segment_details: list[SegmentScoreDetail] = []
    hit_count = 0

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
        else:
            bear_pairs.append((segment_score, segment.weight))

    trend_score = _weighted_average(segment_pairs)
    return TrendScoreReport(
        trend_score=trend_score,
        return_score=return_score,
        arrival_score=_weighted_average(arrival_pairs),
        escort_score=_weighted_average(escort_pairs),
        turn_score=_weighted_average(turn_pairs),
        bull_score=_weighted_average(bull_pairs),
        bear_score=_weighted_average(bear_pairs),
        hit_rate=_safe_ratio(hit_count, len(segments)),
        segment_count=len(segments),
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


def partial_eval_gate_snapshot(result: dict[str, Any] | None) -> dict[str, float]:
    points = _normalize_trend_points(_result_trend_capture_points(result or {}))
    report = _trend_score_report(points)
    return {
        "segment_count": float(report.segment_count),
        "trend_score": report.trend_score,
        "hit_rate": report.hit_rate,
        "path_return_pct": report.path_return_pct,
        "unique_points": float(len(points)),
    }


# ==================== 总分计算 ====================


def summarize_evaluation(
    results: list[dict[str, Any]],
    gates: GateConfig,
    eval_continuous_result: dict[str, Any] | None = None,
    validation_continuous_result: dict[str, Any] | None = None,
    full_period_result: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> EvaluationReport:
    eval_results = _window_payloads(results, "eval")
    validation_results = _window_payloads(results, "validation")
    full_period_return = float((full_period_result or {}).get("return", 0.0))

    eval_returns = [item["result"]["return"] for item in eval_results]
    validation_returns = [item["result"]["return"] for item in validation_results]
    eval_avg_return = _mean(eval_returns)
    eval_median_return = median(eval_returns) if eval_returns else 0.0
    eval_p25_return = _quantile(eval_returns, 0.25)
    eval_worst_return = min(eval_returns) if eval_returns else 0.0
    validation_avg_return = _mean(validation_returns)
    validation_worst_return = min(validation_returns) if validation_returns else 0.0

    worst_drawdown = max((item["result"]["max_drawdown"] for item in results), default=0.0)
    avg_fee_drag = _mean([item["result"].get("fee_drag_pct", 0.0) for item in results])
    liquidations = sum(int(item["result"].get("liquidations", 0)) for item in results)
    total_trades = sum(int(item["result"]["trades"]) for item in results)
    eval_trades = sum(int(item["result"]["trades"]) for item in eval_results)
    validation_trades = sum(int(item["result"]["trades"]) for item in validation_results)

    eval_path = _collect_trend_path(results, "eval")
    validation_path = _collect_trend_path(results, "validation")

    eval_trend_report = _trend_report_from_result(eval_continuous_result)
    validation_source = validation_continuous_result
    if validation_source is None and len(validation_results) == 1:
        validation_source = validation_results[0]["result"]
    validation_trend_report = _trend_report_from_result(validation_source)
    full_period_trend_report = _trend_report_from_result(full_period_result)

    quality_score = 0.70 * eval_trend_report.trend_score + 0.30 * eval_trend_report.return_score
    promotion_score = 0.70 * full_period_trend_report.trend_score + 0.30 * full_period_trend_report.return_score
    capture_drop = eval_trend_report.trend_score - validation_trend_report.trend_score
    overfit_report = _overfit_risk_report(full_period_trend_report, capture_drop)

    gate_reasons: list[str] = []
    if eval_trend_report.segment_count < gates.min_eval_segments:
        gate_reasons.append(f"评估大趋势段不足({eval_trend_report.segment_count})")
    if validation_trend_report.segment_count < gates.min_validation_segments:
        gate_reasons.append(f"验证大趋势段不足({validation_trend_report.segment_count})")
    if eval_trend_report.hit_rate < gates.min_eval_hit_rate:
        gate_reasons.append(f"评估命中率偏低({eval_trend_report.hit_rate:.0%})")
    if validation_trend_report.hit_rate < gates.min_validation_hit_rate:
        gate_reasons.append(f"验证命中率偏低({validation_trend_report.hit_rate:.0%})")
    if eval_trend_report.trend_score < gates.min_eval_trend_score:
        gate_reasons.append(f"评估趋势捕获分偏低({eval_trend_report.trend_score:.2f})")
    if validation_trend_report.trend_score < gates.min_validation_trend_score:
        gate_reasons.append(f"验证趋势捕获分偏低({validation_trend_report.trend_score:.2f})")
    if capture_drop > gates.max_capture_drop:
        gate_reasons.append(f"评估/验证捕获落差过大({capture_drop:.2f})")
    if full_period_trend_report.bull_score < gates.min_bull_capture:
        gate_reasons.append(f"多头捕获偏低({full_period_trend_report.bull_score:.2f})")
    if full_period_trend_report.bear_score < gates.min_bear_capture:
        gate_reasons.append(f"空头捕获偏低({full_period_trend_report.bear_score:.2f})")
    if avg_fee_drag > gates.max_fee_drag_pct:
        gate_reasons.append(f"手续费拖累过高({avg_fee_drag:.2f}%)")
    if overfit_report.hard_fail:
        gate_reasons.append("严重过拟合风险(" + "；".join(overfit_report.hard_reasons) + ")")
    elif overfit_report.risk_score >= OVERFIT_GATE_SCORE:
        gate_reasons.append(
            f"过拟合风险过高({overfit_report.risk_level} {overfit_report.risk_score:.0f})"
        )

    gate_passed = not gate_reasons
    gate_reason = "通过" if gate_passed else "；".join(gate_reasons)

    weakest_signals = _aggregate_signal_stats(results, "eval")
    summary_lines = [
        "研究评估摘要",
        f"评估趋势捕获分 / 绝对收益分: {eval_trend_report.trend_score:.2f} / {eval_trend_report.return_score:.2f}",
        f"验证趋势捕获分 / 绝对收益分: {validation_trend_report.trend_score:.2f} / {validation_trend_report.return_score:.2f}",
        f"全段连续趋势捕获分 / 绝对收益分: {full_period_trend_report.trend_score:.2f} / {full_period_trend_report.return_score:.2f}",
        f"全段连续到来 / 陪跑 / 掉头: {full_period_trend_report.arrival_score:.2f} / {full_period_trend_report.escort_score:.2f} / {full_period_trend_report.turn_score:.2f}",
        f"全段连续多头 / 空头捕获: {full_period_trend_report.bull_score:.2f} / {full_period_trend_report.bear_score:.2f}",
        f"评估趋势段 / 命中率: {eval_trend_report.segment_count} / {eval_trend_report.hit_rate:.0%}",
        f"验证趋势段 / 命中率: {validation_trend_report.segment_count} / {validation_trend_report.hit_rate:.0%}",
        (
            "过拟合风险: "
            f"{overfit_report.risk_level}({overfit_report.risk_score:.0f})，"
            f"单段正向贡献={overfit_report.top1_positive_share:.0%}，"
            f"同向链贡献={overfit_report.max_chain_positive_share:.0%}，"
            f"覆盖率={overfit_report.coverage_ratio:.0%}，"
            f"多空落差={overfit_report.bull_bear_gap:.2f}"
        ),
        (
            "评估连续路径收益 / 验证连续路径收益 / 全段连续收益: "
            f"{eval_trend_report.path_return_pct:.2f}% / "
            f"{validation_trend_report.path_return_pct:.2f}% / "
            f"{full_period_return:.2f}%"
        ),
        f"评估平均收益 / 验证收益: {eval_avg_return:.2f}% / {validation_avg_return:.2f}%",
        f"评估中位收益 / P25 / 最差: {eval_median_return:.2f}% / {eval_p25_return:.2f}% / {eval_worst_return:.2f}%",
        f"评估4h唯一路径点 / 重叠点 / 被覆盖点: {eval_path.unique_points} / {eval_path.overlap_points} / {eval_path.dropped_points}",
        f"最大回撤 / 手续费拖累: {worst_drawdown:.2f}% / {avg_fee_drag:.2f}%",
        f"总交易 / 爆仓: {total_trades} / {liquidations}",
        f"质量分 / 晋级分: {quality_score:.2f} / {promotion_score:.2f}",
        f"Gate: {gate_reason}",
        "",
        "窗口明细:",
        *_build_window_lines(results, include_validation=True),
    ]
    if weakest_signals:
        summary_lines.extend(["", "拖累较大的信号:", *weakest_signals])

    prompt_lines = [
        f"当前策略是 BTC 激进趋势策略：15m 执行，1h/4h 确认，目标是抓大行情的到来、陪跑主趋势、在掉头时退出或反手。",
        f"当前基底主评分={quality_score:.2f}，综合晋级分={promotion_score:.2f}，gate={gate_reason}",
        f"评估趋势捕获分={eval_trend_report.trend_score:.2f}，收益分={eval_trend_report.return_score:.2f}，趋势段={eval_trend_report.segment_count}，命中率={eval_trend_report.hit_rate:.0%}",
        f"全段连续到来/陪跑/掉头={full_period_trend_report.arrival_score:.2f}/{full_period_trend_report.escort_score:.2f}/{full_period_trend_report.turn_score:.2f}",
        f"全段连续多头/空头捕获={full_period_trend_report.bull_score:.2f}/{full_period_trend_report.bear_score:.2f}，评估/验证捕获落差={capture_drop:.2f}",
        (
            f"过拟合风险={overfit_report.risk_level}({overfit_report.risk_score:.0f})，"
            f"单段正向贡献={overfit_report.top1_positive_share:.0%}，"
            f"同向链贡献={overfit_report.max_chain_positive_share:.0%}，"
            f"覆盖率={overfit_report.coverage_ratio:.0%}，"
            f"多空落差={overfit_report.bull_bear_gap:.2f}"
        ),
        (
            f"评估连续路径收益={eval_trend_report.path_return_pct:.2f}%，"
            f"验证连续路径收益={validation_trend_report.path_return_pct:.2f}%，"
            f"全段连续收益={full_period_return:.2f}%，"
            f"最大回撤={worst_drawdown:.2f}%"
        ),
        f"评估4h唯一路径点={eval_path.unique_points}，重叠点={eval_path.overlap_points}，被覆盖点={eval_path.dropped_points}",
        f"手续费拖累={avg_fee_drag:.2f}%，eval交易={eval_trades}，爆仓={liquidations}",
    ]
    if weakest_signals:
        prompt_lines.append("拖累信号: " + " | ".join(weakest_signals))

    metrics = {
        "eval_avg_return": eval_avg_return,
        "eval_median_return": eval_median_return,
        "eval_p25_return": eval_p25_return,
        "eval_worst_return": eval_worst_return,
        "validation_avg_return": validation_avg_return,
        "validation_worst_return": validation_worst_return,
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
        "eval_trend_capture_score": eval_trend_report.trend_score,
        "validation_trend_capture_score": validation_trend_report.trend_score,
        "combined_trend_capture_score": full_period_trend_report.trend_score,
        "full_period_trend_capture_score": full_period_trend_report.trend_score,
        "eval_return_score": eval_trend_report.return_score,
        "validation_return_score": validation_trend_report.return_score,
        "combined_return_score": full_period_trend_report.return_score,
        "full_period_return_score": full_period_trend_report.return_score,
        "eval_arrival_capture_score": eval_trend_report.arrival_score,
        "eval_escort_capture_score": eval_trend_report.escort_score,
        "eval_turn_adaptation_score": eval_trend_report.turn_score,
        "arrival_capture_score": full_period_trend_report.arrival_score,
        "escort_capture_score": full_period_trend_report.escort_score,
        "turn_adaptation_score": full_period_trend_report.turn_score,
        "eval_bull_capture_score": eval_trend_report.bull_score,
        "eval_bear_capture_score": eval_trend_report.bear_score,
        "bull_capture_score": full_period_trend_report.bull_score,
        "bear_capture_score": full_period_trend_report.bear_score,
        "eval_segment_hit_rate": eval_trend_report.hit_rate,
        "validation_segment_hit_rate": validation_trend_report.hit_rate,
        "segment_hit_rate": full_period_trend_report.hit_rate,
        "full_period_segment_hit_rate": full_period_trend_report.hit_rate,
        "eval_major_segment_count": float(eval_trend_report.segment_count),
        "validation_major_segment_count": float(validation_trend_report.segment_count),
        "major_segment_count": float(full_period_trend_report.segment_count),
        "full_period_major_segment_count": float(full_period_trend_report.segment_count),
        "eval_path_return_pct": eval_trend_report.path_return_pct,
        "validation_path_return_pct": validation_trend_report.path_return_pct,
        "combined_path_return_pct": full_period_trend_report.path_return_pct,
        "full_period_return_pct": full_period_return,
        "capture_drop": capture_drop,
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
        "quality_score": quality_score,
        "promotion_score": promotion_score,
    }
    return EvaluationReport(
        metrics=metrics,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        summary_text="\n".join(summary_lines),
        prompt_summary_text="\n".join(prompt_lines),
    )
