#!/usr/bin/env python3
"""评估汇总组装辅助。"""

from __future__ import annotations

from typing import Any

from research_v2 import evaluation as mod


def summarize_evaluation_impl(
    results: list[dict[str, Any]],
    gates: mod.GateConfig,
    selection_period_result: dict[str, Any] | None = None,
    validation_continuous_result: dict[str, Any] | None = None,
    scoring: mod.ScoringConfig | None = None,
    **_kwargs: Any,
) -> mod.EvaluationReport:
    scoring = scoring or mod.ScoringConfig()
    if selection_period_result is None:
        selection_period_result = _kwargs.get("full_period_result")
    eval_results = mod._window_payloads(results, "eval")
    validation_results = mod._window_payloads(results, "validation")
    validation_source = validation_continuous_result
    if validation_source is None and len(validation_results) == 1:
        validation_source = validation_results[0]["result"]
    validation_source = validation_source or {}
    selection_source = selection_period_result or {}

    eval_returns = [float(item["result"].get("return", 0.0)) for item in eval_results]
    validation_returns = [float(item["result"].get("return", 0.0)) for item in validation_results]
    eval_avg_return = mod._mean(eval_returns)
    eval_median_return = mod.median(eval_returns) if eval_returns else 0.0
    eval_p25_return = mod._quantile(eval_returns, 0.25)
    eval_worst_return = min(eval_returns) if eval_returns else 0.0
    validation_avg_return = mod._mean(validation_returns)
    validation_worst_return = min(validation_returns) if validation_returns else 0.0

    worst_drawdown = max((float(item["result"].get("max_drawdown", 0.0)) for item in results), default=0.0)
    avg_fee_drag = mod._mean([float(item["result"].get("fee_drag_pct", 0.0)) for item in results])
    eval_funding_coverage = mod._mean([float(item["result"].get("funding_coverage_ratio", 0.0)) for item in eval_results])
    validation_funding_coverage = float(validation_source.get("funding_coverage_ratio", 0.0))
    selection_funding_coverage = float(selection_source.get("funding_coverage_ratio", 0.0))
    liquidations = sum(int(item["result"].get("liquidations", 0)) for item in results)
    total_trades = sum(int(item["result"].get("trades", 0)) for item in results)
    eval_trades = sum(int(item["result"].get("trades", 0)) for item in eval_results)
    validation_trades = int(
        validation_source.get(
            "trades",
            validation_results[0]["result"].get("trades", 0) if validation_results else 0,
        )
    )

    eval_daily_path = mod._collect_daily_path(results, "eval")
    validation_daily_path = mod._collect_daily_path(results, "validation")
    eval_path = mod._collect_trend_path(results, "eval")
    validation_path = mod._collect_trend_path(results, "validation")

    development_window_reports = [
        mod._trend_report_from_result(item["result"])
        for item in eval_results
    ]
    development_window_scores = [mod._period_score(report) for report in development_window_reports]
    development_mean_score = mod._mean(development_window_scores)
    development_median_score = mod.median(development_window_scores) if development_window_scores else 0.0
    development_score_std = mod._std(development_window_scores)
    profitable_window_ratio = mod._safe_ratio(
        sum(1 for score in development_window_scores if score > 0.0),
        len(development_window_scores),
        default=0.0,
    )
    development_mean_trend_score = mod._mean([report.trend_score for report in development_window_reports])
    development_mean_return_score = mod._mean([report.return_score for report in development_window_reports])
    development_mean_hit_rate = mod._mean([report.hit_rate for report in development_window_reports])
    development_mean_segment_count = mod._mean([float(report.segment_count) for report in development_window_reports])

    train_continuous_trend_report = mod._trend_score_report(eval_path.points)
    validation_trend_report = (
        mod._trend_score_report(validation_path.points)
        if validation_path.points
        else mod._trend_report_from_result(validation_source)
    )
    selection_trend_report = mod._trend_report_from_result(selection_source)

    train_capture_score = train_continuous_trend_report.trend_score
    validation_capture_score = validation_trend_report.trend_score
    capture_score = (
        mod.TRAIN_VAL_SCORE_WEIGHT * train_capture_score
        + mod.TRAIN_VAL_SCORE_WEIGHT * validation_capture_score
    )
    train_timed_return_score = mod._annualized_return_score(eval_daily_path.returns)
    validation_timed_return_score = mod._annualized_return_score(validation_daily_path.returns)
    timed_return_score = (
        mod.TRAIN_VAL_SCORE_WEIGHT * train_timed_return_score
        + mod.TRAIN_VAL_SCORE_WEIGHT * validation_timed_return_score
    )
    train_drawdown_risk_report = mod._drawdown_risk_side_report(eval_daily_path.returns, scoring)
    validation_drawdown_risk_report = mod._drawdown_risk_side_report(validation_daily_path.returns, scoring)
    train_drawdown_risk_score = train_drawdown_risk_report.risk_score
    validation_drawdown_risk_score = validation_drawdown_risk_report.risk_score
    drawdown_risk_score = (
        mod.TRAIN_VAL_SCORE_WEIGHT * train_drawdown_risk_score
        + mod.TRAIN_VAL_SCORE_WEIGHT * validation_drawdown_risk_score
    )
    drawdown_penalty_score = mod._promotion_drawdown_penalty(drawdown_risk_score, scoring)
    train_turn_protection_score = train_continuous_trend_report.turn_protection_score
    validation_turn_protection_score = validation_trend_report.turn_protection_score
    turn_protection_score = (
        mod.TRAIN_VAL_SCORE_WEIGHT * train_turn_protection_score
        + mod.TRAIN_VAL_SCORE_WEIGHT * validation_turn_protection_score
    )
    quality_score = train_capture_score
    raw_validation_score = mod._period_score(validation_trend_report)
    validation_block_report = mod._validation_block_report(
        validation_source,
        block_count=gates.validation_block_count,
        fallback_score=raw_validation_score,
    )
    capture_drop = train_capture_score - validation_capture_score
    promotion_gap = capture_drop
    overfit_report = mod._overfit_risk_report(selection_trend_report, capture_drop)
    plateau_probe_payload = (
        dict(_kwargs.get("plateau_probe"))
        if isinstance(_kwargs.get("plateau_probe"), dict)
        else {}
    )
    robustness_penalty_payload = mod._robustness_penalty_payload(
        promotion_gap=promotion_gap,
        block_report=validation_block_report,
        scoring=scoring,
        plateau_probe=plateau_probe_payload,
    )
    robustness_penalty_score = robustness_penalty_payload["robustness_penalty_score"]
    promotion_score = (
        scoring.promotion_capture_weight * capture_score
        + scoring.promotion_timed_return_weight * timed_return_score
        - drawdown_penalty_score
        - robustness_penalty_score
    )
    validation_long_trades, validation_short_trades = mod._trade_side_counts(validation_source)
    selection_long_trades, selection_short_trades = mod._trade_side_counts(selection_source)
    validation_closed_trades = int(validation_source.get("trades", validation_long_trades + validation_short_trades))
    selection_closed_trades = int(selection_source.get("trades", selection_long_trades + selection_short_trades))
    eval_funnel_counts = mod._aggregate_funnel_counts(results, "eval")
    validation_funnel_counts = mod._result_funnel_counts(validation_source)
    selection_funnel_counts = mod._result_funnel_counts(selection_source)
    low_activity_payload = mod._low_activity_signal_payload(
        validation_counts=validation_funnel_counts,
        selection_counts=selection_funnel_counts,
        validation_closed_trades=validation_closed_trades,
        selection_closed_trades=selection_closed_trades,
    )
    validation_weakest_axis = mod._validation_weakest_axis(validation_trend_report, validation_block_report)
    selection_total_return = float(selection_source.get("return", 0.0))
    selection_max_drawdown = float(selection_source.get("max_drawdown", 0.0))
    selection_fee_drag_pct = float(selection_source.get("fee_drag_pct", 0.0))
    eval_sharpe_ratio = mod._annualized_sharpe(eval_daily_path.returns)
    validation_sharpe_ratio = mod._annualized_sharpe(validation_daily_path.returns)
    selection_sharpe_ratio = mod._annualized_sharpe([float(value) for value in selection_source.get("daily_returns", [])])

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

    weakest_signals = mod._aggregate_signal_stats(results, "eval")
    weakest_signal_paths = mod._aggregate_signal_stats(results, "eval", stats_key="signal_path_stats")
    summary_lines = [
        "研究评估摘要（15m 为唯一事实源，1h/4h 只是由 15m 聚合的确认层；成交量只读展示总量，方向确认主要看 OKX K 线方向流量代理）",
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
            "train/val按日收益年化分 / 收益补充分 / 固定窗口回撤风险分 / 回撤罚分 / 晋级分: "
            f"{train_timed_return_score:.2f} / {validation_timed_return_score:.2f} / "
            f"{timed_return_score:.2f} / {drawdown_risk_score:.2f} / {drawdown_penalty_score:.2f} / {promotion_score:.2f}"
        ),
        (
            "train/val回撤风险分(窗口数): "
            f"{train_drawdown_risk_score:.2f}({train_drawdown_risk_report.window_count}) / "
            f"{validation_drawdown_risk_score:.2f}({validation_drawdown_risk_report.window_count})"
        ),
        (
            "回撤罚分公式(base / knee / excess): "
            f"{scoring.promotion_drawdown_base_weight:.2f} / "
            f"{scoring.promotion_drawdown_knee:.2f} / "
            f"{scoring.promotion_drawdown_excess_weight:.2f}"
        ),
        (
            "train/val窗口 Ulcer 中位 / P75: "
            f"{train_drawdown_risk_report.median_ulcer_pct:.2f}/{train_drawdown_risk_report.tail_ulcer_pct:.2f} / "
            f"{validation_drawdown_risk_report.median_ulcer_pct:.2f}/{validation_drawdown_risk_report.tail_ulcer_pct:.2f}"
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
        mod._format_funnel_line("train滚动漏斗(long)", eval_funnel_counts["long"]),
        mod._format_funnel_line("train滚动漏斗(short)", eval_funnel_counts["short"]),
        mod._format_funnel_line("val连续漏斗(long)", validation_funnel_counts["long"]),
        mod._format_funnel_line("val连续漏斗(short)", validation_funnel_counts["short"]),
        f"val短板: {validation_weakest_axis}",
        (
            "val分块门控分(均值/std/最差/尾块/尾块gap/负分块): "
            f"{validation_block_report.mean_score:.2f} / "
            f"{validation_block_report.std_score:.2f} / "
            f"{validation_block_report.min_score:.2f} / "
            f"{validation_block_report.tail_score:.2f} / "
            f"{robustness_penalty_payload['validation_block_tail_gap']:.2f} / "
            f"{validation_block_report.fail_count}"
            + (
                f" (分块数={validation_block_report.used_block_count})"
                if validation_block_report.used_block_count > 0 else " (未启用)"
            )
        ),
        (
            "鲁棒性软惩罚(gap/std/floor/tail/fail/plateau/raw/cap后): "
            f"{robustness_penalty_payload['gap_penalty_score']:.2f} / "
            f"{robustness_penalty_payload['block_std_penalty_score']:.2f} / "
            f"{robustness_penalty_payload['block_floor_penalty_score']:.2f} / "
            f"{robustness_penalty_payload['block_tail_penalty_score']:.2f} / "
            f"{robustness_penalty_payload['block_fail_penalty_score']:.2f} / "
            f"{robustness_penalty_payload['plateau_penalty_score']:.2f} / "
            f"{robustness_penalty_payload['robustness_penalty_score_raw']:.2f} / "
            f"{robustness_penalty_score:.2f}"
        ),
        (
            "train+val集中度诊断: "
            f"{overfit_report.risk_level}({overfit_report.risk_score:.0f})，"
            f"单段正向贡献={overfit_report.top1_positive_share:.0%}，"
            f"同向链贡献={overfit_report.max_chain_positive_share:.0%}，"
            f"覆盖率={overfit_report.coverage_ratio:.0%}，"
            f"多空落差={overfit_report.bull_bear_gap:.2f}，"
            f"处置={mod.overfit_reference_action(overfit_report.risk_score, overfit_report.hard_fail)}"
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
        *mod._build_window_lines(results, include_validation=True),
    ]
    if plateau_probe_payload.get("enabled"):
        summary_lines.append(
            "val平台观察(只读): "
            f"{plateau_probe_payload.get('param', '-')} current={robustness_penalty_payload['plateau_current_value']:.4f}, "
            f"best={robustness_penalty_payload['plateau_best_value']:.4f}, "
            f"center={robustness_penalty_payload['plateau_center_period_score']:.2f}, "
            f"best_score={robustness_penalty_payload['plateau_best_period_score']:.2f}, "
            f"gap={robustness_penalty_payload['plateau_center_gap']:.2f}, "
            f"score_span={robustness_penalty_payload['plateau_score_span']:.2f}, "
            f"dd_span={robustness_penalty_payload['plateau_drawdown_span']:.2f}"
        )
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
            f"回撤风险分={drawdown_risk_score:.2f}，回撤罚分={drawdown_penalty_score:.2f}，"
            f"鲁棒性软惩罚={robustness_penalty_score:.2f}，"
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
            + mod._funnel_choke_point_text("long", validation_funnel_counts["long"])
            + "；"
            + mod._funnel_choke_point_text("short", validation_funnel_counts["short"])
        ),
        (
            f"- train 滚动状态: 均值/中位/std/盈利窗比="
            f"{development_mean_score:.2f}/{development_median_score:.2f}/"
            f"{development_score_std:.2f}/{profitable_window_ratio:.0%}"
        ),
        (
            f"- 当前评分组成: train/val 连续趋势抓取={train_capture_score:.2f}/{validation_capture_score:.2f}，"
            f"train/val 按日收益年化分={train_timed_return_score:.2f}/{validation_timed_return_score:.2f}，"
            f"train/val 固定窗口回撤风险分={train_drawdown_risk_score:.2f}/{validation_drawdown_risk_score:.2f}，"
            f"回撤罚分={drawdown_penalty_score:.2f}，鲁棒性软惩罚={robustness_penalty_score:.2f}"
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
            f"窗口 Ulcer(train/val blended)="
            f"{train_drawdown_risk_report.blended_ulcer_pct:.2f}/{validation_drawdown_risk_report.blended_ulcer_pct:.2f}%，"
            f"手续费拖累={avg_fee_drag:.2f}%，"
            f"train/val 抓取分差={promotion_gap:.2f}，"
            f"val尾块gap={robustness_penalty_payload['validation_block_tail_gap']:.2f}"
        ),
        (
            f"- 集中度诊断: {overfit_report.risk_level}({overfit_report.risk_score:.0f})，"
            f"覆盖率={overfit_report.coverage_ratio:.0%}，"
            f"多空落差={overfit_report.bull_bear_gap:.2f}，"
            f"处置={mod.overfit_reference_action(overfit_report.risk_score, overfit_report.hard_fail)}"
        ),
    ]
    if plateau_probe_payload.get("enabled"):
        prompt_lines.append(
            f"- val平台观察: {plateau_probe_payload.get('param', '-')} "
            f"center/best={robustness_penalty_payload['plateau_center_period_score']:.2f}/"
            f"{robustness_penalty_payload['plateau_best_period_score']:.2f}，"
            f"gap={robustness_penalty_payload['plateau_center_gap']:.2f}，"
            f"score_span={robustness_penalty_payload['plateau_score_span']:.2f}，"
            f"dd_span={robustness_penalty_payload['plateau_drawdown_span']:.2f}"
        )
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
        "train_drawdown_risk_score": train_drawdown_risk_score,
        "validation_drawdown_risk_score": validation_drawdown_risk_score,
        "drawdown_risk_score": drawdown_risk_score,
        "drawdown_penalty_score": drawdown_penalty_score,
        "train_window_ulcer_median_pct": train_drawdown_risk_report.median_ulcer_pct,
        "train_window_ulcer_p75_pct": train_drawdown_risk_report.tail_ulcer_pct,
        "train_window_ulcer_blended_pct": train_drawdown_risk_report.blended_ulcer_pct,
        "train_drawdown_window_count": float(train_drawdown_risk_report.window_count),
        "validation_window_ulcer_median_pct": validation_drawdown_risk_report.median_ulcer_pct,
        "validation_window_ulcer_p75_pct": validation_drawdown_risk_report.tail_ulcer_pct,
        "validation_window_ulcer_blended_pct": validation_drawdown_risk_report.blended_ulcer_pct,
        "validation_drawdown_window_count": float(validation_drawdown_risk_report.window_count),
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
        "validation_block_tail_score": validation_block_report.tail_score,
        "validation_block_tail_gap": robustness_penalty_payload["validation_block_tail_gap"],
        "validation_block_fail_count": float(validation_block_report.fail_count),
        "validation_block_count_used": float(validation_block_report.used_block_count),
        "gap_penalty_score": robustness_penalty_payload["gap_penalty_score"],
        "block_std_penalty_score": robustness_penalty_payload["block_std_penalty_score"],
        "block_floor_penalty_score": robustness_penalty_payload["block_floor_penalty_score"],
        "block_tail_penalty_score": robustness_penalty_payload["block_tail_penalty_score"],
        "block_fail_penalty_score": robustness_penalty_payload["block_fail_penalty_score"],
        "plateau_penalty_score": robustness_penalty_payload["plateau_penalty_score"],
        "robustness_penalty_score_raw": robustness_penalty_payload["robustness_penalty_score_raw"],
        "robustness_penalty_score": robustness_penalty_score,
        "plateau_probe_enabled": robustness_penalty_payload["plateau_probe_enabled"],
        "plateau_current_value": robustness_penalty_payload["plateau_current_value"],
        "plateau_best_value": robustness_penalty_payload["plateau_best_value"],
        "plateau_center_period_score": robustness_penalty_payload["plateau_center_period_score"],
        "plateau_best_period_score": robustness_penalty_payload["plateau_best_period_score"],
        "plateau_center_gap": robustness_penalty_payload["plateau_center_gap"],
        "plateau_score_span": robustness_penalty_payload["plateau_score_span"],
        "plateau_drawdown_span": robustness_penalty_payload["plateau_drawdown_span"],
        "plateau_current_is_best": robustness_penalty_payload["plateau_current_is_best"],
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
    mod._append_funnel_metrics(metrics, "eval", eval_funnel_counts)
    mod._append_funnel_metrics(metrics, "validation", validation_funnel_counts)
    mod._append_funnel_metrics(metrics, "selection", selection_funnel_counts)
    return mod.EvaluationReport(
        metrics=metrics,
        gate_passed=gate_passed,
        gate_reason=gate_reason,
        summary_text="\n".join(summary_lines),
        prompt_summary_text="\n".join(prompt_lines),
        artifacts={
            "plateau_probe": plateau_probe_payload,
        },
    )
