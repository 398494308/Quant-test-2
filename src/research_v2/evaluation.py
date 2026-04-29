#!/usr/bin/env python3
"""研究器 v2 的评估与打分。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from research_v2.config import GateConfig, ScoringConfig


# ==================== 数据结构 ====================


@dataclass(frozen=True)
class EvaluationReport:
    metrics: dict[str, float]
    gate_passed: bool
    gate_reason: str
    summary_text: str
    prompt_summary_text: str
    artifacts: dict[str, Any] | None = None


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
    tail_score: float
    fail_count: int
    used_block_count: int


@dataclass(frozen=True)
class DrawdownRiskSideReport:
    risk_score: float
    window_count: int
    median_ulcer_pct: float
    tail_ulcer_pct: float
    blended_ulcer_pct: float


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
    _ = (
        validation_counts,
        selection_counts,
        validation_closed_trades,
        selection_closed_trades,
    )
    return {
        "count": 0,
        "lines": [],
        "prompt_line": "",
        "tags": (),
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


def _window_slices(length: int, window_days: int, step_days: int) -> list[tuple[int, int]]:
    if length <= 0:
        return []
    window_size = max(1, min(window_days, length))
    step_size = max(1, step_days)
    if length <= window_size:
        return [(0, length)]
    ranges: list[tuple[int, int]] = []
    start_idx = 0
    while start_idx + window_size <= length:
        ranges.append((start_idx, start_idx + window_size))
        start_idx += step_size
    tail_start = max(0, length - window_size)
    tail_range = (tail_start, length)
    if not ranges or ranges[-1] != tail_range:
        ranges.append(tail_range)
    return ranges


def _ulcer_index_pct(daily_returns: list[float]) -> float:
    if not daily_returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    squared_drawdowns: list[float] = []
    for value in daily_returns:
        equity *= max(1e-9, 1.0 + float(value))
        peak = max(peak, equity)
        drawdown_pct = 0.0 if peak <= 1e-12 else (peak - equity) / peak * 100.0
        squared_drawdowns.append(drawdown_pct * drawdown_pct)
    return math.sqrt(_mean(squared_drawdowns))


def _drawdown_risk_side_report(
    daily_returns: list[float],
    scoring: ScoringConfig,
) -> DrawdownRiskSideReport:
    if not daily_returns:
        return DrawdownRiskSideReport(
            risk_score=0.0,
            window_count=0,
            median_ulcer_pct=0.0,
            tail_ulcer_pct=0.0,
            blended_ulcer_pct=0.0,
        )
    window_ranges = _window_slices(
        len(daily_returns),
        scoring.risk_window_days,
        scoring.risk_window_step_days,
    )
    window_ulcer_pcts = [
        _ulcer_index_pct(daily_returns[start_idx:end_idx])
        for start_idx, end_idx in window_ranges
    ]
    if not window_ulcer_pcts:
        return DrawdownRiskSideReport(
            risk_score=0.0,
            window_count=0,
            median_ulcer_pct=0.0,
            tail_ulcer_pct=0.0,
            blended_ulcer_pct=0.0,
        )
    tail_quantile = _clamp(scoring.drawdown_risk_tail_quantile, 0.50, 0.99)
    tail_weight = _clamp(scoring.drawdown_risk_tail_weight, 0.0, 1.0)
    median_ulcer_pct = median(window_ulcer_pcts)
    tail_ulcer_pct = _quantile(window_ulcer_pcts, tail_quantile)
    blended_ulcer_pct = (1.0 - tail_weight) * median_ulcer_pct + tail_weight * tail_ulcer_pct
    risk_score = _clamp(blended_ulcer_pct / max(scoring.drawdown_risk_scale_pct, 0.1), 0.0, 3.0)
    return DrawdownRiskSideReport(
        risk_score=risk_score,
        window_count=len(window_ulcer_pcts),
        median_ulcer_pct=median_ulcer_pct,
        tail_ulcer_pct=tail_ulcer_pct,
        blended_ulcer_pct=blended_ulcer_pct,
    )


def _promotion_drawdown_penalty(drawdown_risk_score: float, scoring: ScoringConfig) -> float:
    risk_score = max(0.0, float(drawdown_risk_score))
    knee = max(0.0, float(scoring.promotion_drawdown_knee))
    base_penalty = max(0.0, float(scoring.promotion_drawdown_base_weight)) * risk_score
    excess_penalty = max(0.0, float(scoring.promotion_drawdown_excess_weight)) * max(risk_score - knee, 0.0)
    return base_penalty + excess_penalty


def _upper_band_penalty(
    value: float,
    *,
    warn_threshold: float,
    fail_threshold: float,
    warn_penalty: float,
    fail_penalty: float,
) -> float:
    candidate = max(0.0, float(value))
    if candidate <= float(warn_threshold):
        return 0.0
    if candidate <= float(fail_threshold):
        return max(0.0, float(warn_penalty))
    return max(0.0, float(fail_penalty))


def _lower_band_penalty(
    value: float,
    *,
    warn_threshold: float,
    fail_threshold: float,
    warn_penalty: float,
    fail_penalty: float,
) -> float:
    candidate = float(value)
    if candidate >= float(warn_threshold):
        return 0.0
    if candidate >= float(fail_threshold):
        return max(0.0, float(warn_penalty))
    return max(0.0, float(fail_penalty))


def _plateau_penalty_payload(
    plateau_probe: Mapping[str, Any] | None,
    scoring: ScoringConfig,
) -> dict[str, float]:
    payload = dict(plateau_probe or {}) if isinstance(plateau_probe, Mapping) else {}
    enabled = bool(payload.get("enabled"))
    if not enabled:
        return {
            "enabled": 0.0,
            "current_value": 0.0,
            "best_value": 0.0,
            "center_period_score": 0.0,
            "best_period_score": 0.0,
            "center_gap": 0.0,
            "score_span": 0.0,
            "drawdown_span": 0.0,
            "current_is_best": 0.0,
            "penalty_score": 0.0,
        }

    center_gap = max(0.0, float(payload.get("center_gap", 0.0) or 0.0))
    score_span = max(0.0, float(payload.get("score_span", 0.0) or 0.0))
    drawdown_span = max(0.0, float(payload.get("drawdown_span", 0.0) or 0.0))
    current_is_best = bool(payload.get("current_is_best"))

    penalty_score = _upper_band_penalty(
        center_gap,
        warn_threshold=scoring.robustness_plateau_center_gap_warn_threshold,
        fail_threshold=scoring.robustness_plateau_center_gap_fail_threshold,
        warn_penalty=scoring.robustness_plateau_center_gap_warn_penalty,
        fail_penalty=scoring.robustness_plateau_center_gap_fail_penalty,
    )
    if score_span > float(scoring.robustness_plateau_score_span_threshold):
        penalty_score += max(0.0, float(scoring.robustness_plateau_extra_penalty))
    if drawdown_span > float(scoring.robustness_plateau_drawdown_span_threshold):
        penalty_score += max(0.0, float(scoring.robustness_plateau_extra_penalty))
    if (not current_is_best) and center_gap > float(scoring.robustness_plateau_center_gap_warn_threshold):
        penalty_score += max(0.0, float(scoring.robustness_plateau_extra_penalty))

    return {
        "enabled": 1.0,
        "current_value": float(payload.get("current_value", 0.0) or 0.0),
        "best_value": float(payload.get("best_value", 0.0) or 0.0),
        "center_period_score": float(payload.get("center_period_score", 0.0) or 0.0),
        "best_period_score": float(payload.get("best_period_score", 0.0) or 0.0),
        "center_gap": center_gap,
        "score_span": score_span,
        "drawdown_span": drawdown_span,
        "current_is_best": 1.0 if current_is_best else 0.0,
        "penalty_score": penalty_score,
    }


def _robustness_penalty_payload(
    *,
    promotion_gap: float,
    block_report: ValidationBlockReport,
    scoring: ScoringConfig,
    plateau_probe: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    positive_gap = max(0.0, float(promotion_gap))
    blocks_enabled = int(block_report.used_block_count) >= 2
    tail_gap = (
        max(0.0, float(block_report.mean_score) - float(block_report.tail_score))
        if blocks_enabled
        else 0.0
    )
    plateau_payload = _plateau_penalty_payload(plateau_probe, scoring)

    gap_penalty_score = _upper_band_penalty(
        positive_gap,
        warn_threshold=scoring.robustness_gap_warn_threshold,
        fail_threshold=scoring.robustness_gap_fail_threshold,
        warn_penalty=scoring.robustness_gap_warn_penalty,
        fail_penalty=scoring.robustness_gap_fail_penalty,
    )
    block_std_penalty_score = 0.0
    block_floor_penalty_score = 0.0
    block_tail_penalty_score = 0.0
    block_fail_penalty_score = 0.0
    if blocks_enabled:
        block_std_penalty_score = _upper_band_penalty(
            float(block_report.std_score),
            warn_threshold=scoring.robustness_block_std_warn_threshold,
            fail_threshold=scoring.robustness_block_std_fail_threshold,
            warn_penalty=scoring.robustness_block_std_warn_penalty,
            fail_penalty=scoring.robustness_block_std_fail_penalty,
        )
        block_floor_penalty_score = _lower_band_penalty(
            float(block_report.min_score),
            warn_threshold=scoring.robustness_block_floor_warn_threshold,
            fail_threshold=scoring.robustness_block_floor_fail_threshold,
            warn_penalty=scoring.robustness_block_floor_warn_penalty,
            fail_penalty=scoring.robustness_block_floor_fail_penalty,
        )
        block_tail_penalty_score = _upper_band_penalty(
            tail_gap,
            warn_threshold=scoring.robustness_block_tail_warn_threshold,
            fail_threshold=scoring.robustness_block_tail_fail_threshold,
            warn_penalty=scoring.robustness_block_tail_warn_penalty,
            fail_penalty=scoring.robustness_block_tail_fail_penalty,
        )
        block_fail_penalty_score = max(0.0, float(scoring.robustness_block_fail_penalty_per_block)) * min(
            int(scoring.robustness_block_fail_penalty_cap_count),
            max(0, int(block_report.fail_count)),
        )
    raw_penalty_score = (
        gap_penalty_score
        + block_std_penalty_score
        + block_floor_penalty_score
        + block_tail_penalty_score
        + block_fail_penalty_score
        + plateau_payload["penalty_score"]
    )
    penalty_score = min(max(0.0, float(scoring.robustness_penalty_cap)), raw_penalty_score)
    return {
        "validation_block_tail_gap": tail_gap,
        "gap_penalty_score": gap_penalty_score,
        "block_std_penalty_score": block_std_penalty_score,
        "block_floor_penalty_score": block_floor_penalty_score,
        "block_tail_penalty_score": block_tail_penalty_score,
        "block_fail_penalty_score": block_fail_penalty_score,
        "plateau_penalty_score": plateau_payload["penalty_score"],
        "robustness_penalty_score_raw": raw_penalty_score,
        "robustness_penalty_score": penalty_score,
        "plateau_probe_enabled": plateau_payload["enabled"],
        "plateau_current_value": plateau_payload["current_value"],
        "plateau_best_value": plateau_payload["best_value"],
        "plateau_center_period_score": plateau_payload["center_period_score"],
        "plateau_best_period_score": plateau_payload["best_period_score"],
        "plateau_center_gap": plateau_payload["center_gap"],
        "plateau_score_span": plateau_payload["score_span"],
        "plateau_drawdown_span": plateau_payload["drawdown_span"],
        "plateau_current_is_best": plateau_payload["current_is_best"],
    }


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
            tail_score=fallback_score,
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
            tail_score=fallback_score,
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
            tail_score=fallback_score,
            fail_count=1 if fallback_score < 0.0 else 0,
            used_block_count=0,
        )

    return ValidationBlockReport(
        block_scores=tuple(block_scores),
        mean_score=_mean(block_scores),
        std_score=_std(block_scores),
        min_score=min(block_scores),
        tail_score=block_scores[-1],
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


def period_score_from_result(result: dict[str, Any] | None) -> float:
    return _period_score(_trend_report_from_result(result))


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
        tail_score = float(block_report.tail_score)
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


def normalize_test_metrics_payload(metrics: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(metrics, Mapping):
        return {}
    normalized: dict[str, float] = {}
    for raw_key, raw_value in metrics.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if key.startswith("shadow_test_"):
            key = f"test_{key[len('shadow_test_'):]}"
        if not key.startswith("test_"):
            continue
        try:
            normalized[key] = float(raw_value)
        except (TypeError, ValueError):
            continue
    return normalized


def summarize_test_result(result: dict[str, Any] | None) -> dict[str, float]:
    trend_report = _trend_report_from_result(result)
    long_trades, short_trades = _trade_side_counts(result)
    daily_returns = [float(value) for value in (result or {}).get("daily_returns", [])]
    return {
        "test_score": _period_score(trend_report),
        "test_trend_capture_score": trend_report.trend_score,
        "test_return_score": trend_report.return_score,
        "test_arrival_score": trend_report.arrival_score,
        "test_escort_score": trend_report.escort_score,
        "test_turn_score": trend_report.turn_score,
        "test_bull_capture_score": trend_report.bull_score,
        "test_bear_capture_score": trend_report.bear_score,
        "test_hit_rate": trend_report.hit_rate,
        "test_segment_count": float(trend_report.segment_count),
        "test_path_return_pct": trend_report.path_return_pct,
        "test_total_return_pct": float((result or {}).get("return", 0.0)),
        "test_max_drawdown": float((result or {}).get("max_drawdown", 0.0)),
        "test_fee_drag_pct": float((result or {}).get("fee_drag_pct", 0.0)),
        "test_sharpe_ratio": _annualized_sharpe(daily_returns),
        "test_closed_trades": float((result or {}).get("trades", long_trades + short_trades)),
        "test_long_closed_trades": float(long_trades),
        "test_short_closed_trades": float(short_trades),
    }

# ==================== 总分计算 ====================


def summarize_evaluation(
    results: list[dict[str, Any]],
    gates: GateConfig,
    selection_period_result: dict[str, Any] | None = None,
    validation_continuous_result: dict[str, Any] | None = None,
    scoring: ScoringConfig | None = None,
    **_kwargs: Any,
) -> EvaluationReport:
    from research_v2.evaluation_summary import summarize_evaluation_impl

    return summarize_evaluation_impl(
        results,
        gates,
        selection_period_result=selection_period_result,
        validation_continuous_result=validation_continuous_result,
        scoring=scoring,
        **_kwargs,
    )
