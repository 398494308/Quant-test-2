#!/usr/bin/env python3
"""研究器 v2 的评估与打分。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any

from research_v2.config import GateConfig
from research_v2.windows import ResearchWindow


# ==================== 数据结构 ====================


@dataclass(frozen=True)
class EvaluationReport:
    metrics: dict[str, float]
    gate_passed: bool
    gate_reason: str
    summary_text: str
    prompt_summary_text: str


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


def _window_payloads(results: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    return [item for item in results if item["window"].group == group]


def _weighted_average(values: list[tuple[float, float]]) -> float:
    if not values:
        return 0.0
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 1e-9:
        return 0.0
    return sum(value * weight for value, weight in values) / total_weight


def _collect_daily_returns(results: list[dict[str, Any]], group: str) -> list[float]:
    collected: list[float] = []
    for item in _window_payloads(results, group):
        collected.extend(item["result"].get("daily_returns", []))
    return collected


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


def _build_window_lines(results: list[dict[str, Any]], include_holdout: bool) -> list[str]:
    lines: list[str] = []
    for item in results:
        window = item["window"]
        if window.group == "holdout" and not include_holdout:
            continue
        result = item["result"]
        group_label = "评" if window.group == "eval" else "留"
        lines.append(
            f"{window.label}({group_label}) {window.start_date}~{window.end_date} | "
            f"收益{result['return']:.1f}% | 回撤{result['max_drawdown']:.1f}% | "
            f"交易{result['trades']}"
        )
    return lines


# ==================== 总分计算 ====================


def summarize_evaluation(
    results: list[dict[str, Any]],
    stress_results: list[dict[str, Any]],
    gates: GateConfig,
) -> EvaluationReport:
    eval_results = _window_payloads(results, "eval")
    holdout_results = _window_payloads(results, "holdout")

    eval_returns = [item["result"]["return"] for item in eval_results]
    holdout_returns = [item["result"]["return"] for item in holdout_results]
    weighted_eval_return = _weighted_average(
        [(item["result"]["return"], item["window"].weight) for item in eval_results]
    )
    eval_median_return = median(eval_returns) if eval_returns else 0.0
    eval_p25_return = _quantile(eval_returns, 0.25)
    eval_worst_return = min(eval_returns) if eval_returns else 0.0
    holdout_avg_return = _mean(holdout_returns)
    holdout_worst_return = min(holdout_returns) if holdout_returns else 0.0
    eval_positive_ratio = _safe_ratio(sum(1 for value in eval_returns if value > 0.0), len(eval_returns))
    eval_std = _std(eval_returns)

    worst_drawdown = max((item["result"]["max_drawdown"] for item in results), default=0.0)
    avg_fee_drag = _mean([item["result"].get("fee_drag_pct", 0.0) for item in results])
    liquidations = sum(int(item["result"].get("liquidations", 0)) for item in results)
    total_trades = sum(int(item["result"]["trades"]) for item in results)
    eval_trades = sum(int(item["result"]["trades"]) for item in eval_results)
    holdout_trades = sum(int(item["result"]["trades"]) for item in holdout_results)

    eval_daily_returns = _collect_daily_returns(results, "eval")
    daily_sharpe = _annualized_sharpe(eval_daily_returns)
    daily_sortino = _annualized_sortino(eval_daily_returns)

    profit_factor = _safe_ratio(
        sum(max(0.0, trade["pnl_amount"]) for item in eval_results for trade in item["result"].get("trades_detail", [])),
        abs(sum(min(0.0, trade["pnl_amount"]) for item in eval_results for trade in item["result"].get("trades_detail", []))),
        default=0.0,
    )

    stress_returns = [item["result"]["return"] for item in stress_results]
    stress_worst_return = min(stress_returns) if stress_returns else 0.0
    stress_avg_return = _mean(stress_returns)
    stress_worst_drawdown = max((item["result"]["max_drawdown"] for item in stress_results), default=0.0)

    sortino_component = _clamp(daily_sortino, -2.5, 5.0) * 4.0
    sharpe_component = _clamp(daily_sharpe, -2.5, 5.0) * 2.0
    profit_factor_component = _clamp(profit_factor - 1.0, -1.0, 2.5) * 3.0
    weighted_return_component = weighted_eval_return * 0.42
    median_component = eval_median_return * 0.24
    p25_component = eval_p25_return * 0.20
    holdout_component = holdout_avg_return * 0.18

    drawdown_penalty = max(0.0, worst_drawdown - 32.0) * 0.45
    fee_penalty = max(0.0, avg_fee_drag - 4.0) * 1.60
    liquidation_penalty = liquidations * 8.0
    tail_penalty = max(0.0, -eval_worst_return - 10.0) * 0.28
    consistency_penalty = max(0.0, eval_std - 16.0) * 0.25
    holdout_gap = weighted_eval_return - holdout_avg_return
    holdout_gap_penalty = max(0.0, holdout_gap) * 0.18
    stress_penalty = max(0.0, -stress_worst_return - 4.0) * 0.20

    quality_score = (
        weighted_return_component
        + median_component
        + p25_component
        + sortino_component
        + sharpe_component
        + profit_factor_component
        - drawdown_penalty
        - fee_penalty
        - liquidation_penalty
        - tail_penalty
        - consistency_penalty
    )
    promotion_score = quality_score + holdout_component - holdout_gap_penalty - stress_penalty

    gate_reasons: list[str] = []
    if total_trades < gates.min_total_trades:
        gate_reasons.append(f"总交易不足({total_trades})")
    if eval_trades < gates.min_eval_trades:
        gate_reasons.append(f"评估交易不足({eval_trades})")
    if holdout_trades < gates.min_holdout_trades:
        gate_reasons.append(f"留出交易不足({holdout_trades})")
    if eval_positive_ratio < gates.min_positive_ratio:
        gate_reasons.append(f"正收益窗比例偏低({eval_positive_ratio:.0%})")
    if worst_drawdown > gates.max_drawdown_pct:
        gate_reasons.append(f"最大回撤过大({worst_drawdown:.1f}%)")
    if liquidations > gates.max_liquidations:
        gate_reasons.append(f"爆仓次数过多({liquidations})")
    if holdout_avg_return < gates.min_holdout_return:
        gate_reasons.append(f"留出收益不足({holdout_avg_return:.2f}%)")
    if holdout_gap > gates.max_eval_holdout_gap:
        gate_reasons.append(f"评估/留出落差过大({holdout_gap:.2f})")
    if avg_fee_drag > gates.max_fee_drag_pct:
        gate_reasons.append(f"手续费拖累过高({avg_fee_drag:.2f}%)")
    if stress_results and stress_worst_return < gates.min_stress_return:
        gate_reasons.append(f"压力测试过弱({stress_worst_return:.2f}%)")

    gate_passed = not gate_reasons
    gate_reason = "通过" if gate_passed else "；".join(gate_reasons)

    weakest_signals = _aggregate_signal_stats(results, "eval")
    summary_lines = [
        "研究评估摘要",
        f"评估加权收益: {weighted_eval_return:.2f}%",
        f"评估中位收益: {eval_median_return:.2f}%",
        f"评估P25收益: {eval_p25_return:.2f}%",
        f"留出收益: {holdout_avg_return:.2f}%",
        f"日度 Sortino / Sharpe: {daily_sortino:.2f} / {daily_sharpe:.2f}",
        f"最大回撤: {worst_drawdown:.2f}%",
        f"手续费拖累: {avg_fee_drag:.2f}%",
        f"总交易 / 爆仓: {total_trades} / {liquidations}",
        f"质量分 / 晋级分: {quality_score:.2f} / {promotion_score:.2f}",
        f"Gate: {gate_reason}",
        "",
        "窗口明细:",
        *_build_window_lines(results, include_holdout=True),
    ]
    if stress_results:
        summary_lines.extend(
            [
                "",
                f"压力测试均值 / 最差: {stress_avg_return:.2f}% / {stress_worst_return:.2f}%",
                f"压力测试最大回撤: {stress_worst_drawdown:.2f}%",
            ]
        )
    if weakest_signals:
        summary_lines.extend(["", "拖累较大的信号:", *weakest_signals])

    prompt_lines = [
        f"当前基底质量分={quality_score:.2f}，晋级分={promotion_score:.2f}，gate={gate_reason}",
        f"评估加权收益={weighted_eval_return:.2f}%，中位数={eval_median_return:.2f}%，P25={eval_p25_return:.2f}%",
        f"留出收益={holdout_avg_return:.2f}%，评估-留出差值={holdout_gap:.2f}",
        f"日度Sortino={daily_sortino:.2f}，Sharpe={daily_sharpe:.2f}，最大回撤={worst_drawdown:.2f}%",
        f"手续费拖累={avg_fee_drag:.2f}%，总交易={total_trades}，爆仓={liquidations}",
    ]
    if weakest_signals:
        prompt_lines.append("拖累信号: " + " | ".join(weakest_signals))
    if stress_results:
        prompt_lines.append(
            f"压力测试: avg={stress_avg_return:.2f}% worst={stress_worst_return:.2f}% dd={stress_worst_drawdown:.2f}%"
        )

    metrics = {
        "weighted_eval_return": weighted_eval_return,
        "eval_median_return": eval_median_return,
        "eval_p25_return": eval_p25_return,
        "eval_worst_return": eval_worst_return,
        "holdout_avg_return": holdout_avg_return,
        "holdout_worst_return": holdout_worst_return,
        "eval_positive_ratio": eval_positive_ratio,
        "eval_std": eval_std,
        "worst_drawdown": worst_drawdown,
        "avg_fee_drag": avg_fee_drag,
        "liquidations": float(liquidations),
        "total_trades": float(total_trades),
        "eval_trades": float(eval_trades),
        "holdout_trades": float(holdout_trades),
        "daily_sharpe": daily_sharpe,
        "daily_sortino": daily_sortino,
        "profit_factor": profit_factor,
        "holdout_gap": holdout_gap,
        "quality_score": quality_score,
        "promotion_score": promotion_score,
        "stress_avg_return": stress_avg_return,
        "stress_worst_return": stress_worst_return,
        "stress_worst_drawdown": stress_worst_drawdown,
    }
    return EvaluationReport(
        metrics=metrics,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        summary_text="\n".join(summary_lines),
        prompt_summary_text="\n".join(prompt_lines),
    )

