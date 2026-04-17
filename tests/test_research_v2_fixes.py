import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import backtest_macd_aggressive as backtest
from codex_exec_client import StrategyClientConfig, StrategyGenerationTransientError, generate_json_object
from scripts.research_macd_aggressive_v2 import select_smoke_windows
from research_v2.config import GateConfig
from research_v2.evaluation import (
    EvaluationReport,
    _collect_daily_path,
    _collect_trend_path,
    _trend_score_report,
    partial_eval_gate_snapshot,
    summarize_evaluation,
)
from research_v2.charting import charts_available, render_performance_chart
from research_v2.journal import (
    _format_compact_for_prompt,
    build_journal_prompt_summary,
    cluster_for_tags,
    cluster_key_for_components,
    cluster_key_for_entry,
)
from research_v2.notifications import build_discord_summary_message
from research_v2.prompting import EDITABLE_REGIONS
from research_v2.strategy_code import (
    StrategySourceError,
    validate_editable_region_boundaries,
    validate_strategy_source,
)


class BacktestFixesTest(unittest.TestCase):
    def test_daily_equity_point_keeps_latest_market_close(self):
        points = []

        backtest._append_daily_equity_point(points, 1_700_000_000_000, 100000.0, 35000.0)
        backtest._append_daily_equity_point(points, 1_700_000_100_000, 101000.0, 35250.0)

        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0]["equity"], 101000.0)
        self.assertAlmostEqual(points[0]["market_close"], 35250.0)

    def test_closed_trade_rollup_treats_tp1_and_final_exit_as_one_trade(self):
        position = {
            "trade_id": 7,
            "entry_signal": "short_breakdown",
            "opened_size_total": 10000.0,
            "pyramids_done": 1,
            "realized_pnl_amount": 0.0,
            "realized_gross_pnl_amount": 0.0,
            "realized_fee_amount": 0.0,
            "realized_funding_amount": 0.0,
            "realized_hold_bars_weighted": 0.0,
            "realized_closed_size": 0.0,
            "realized_leg_count": 0,
        }
        tp1_leg = {
            "trade_id": 7,
            "entry_signal": "short_breakdown",
            "size": 2200.0,
            "pnl_amount": 330.0,
            "gross_pnl_amount": 350.0,
            "fee_amount": 20.0,
            "funding_amount": 0.0,
            "hold_bars": 8,
            "reason": "第一止盈",
            "pnl_pct": 15.0,
            "pyramids_done": 1,
        }
        final_leg = {
            "trade_id": 7,
            "entry_signal": "short_breakdown",
            "size": 7800.0,
            "pnl_amount": -78.0,
            "gross_pnl_amount": -40.0,
            "fee_amount": 38.0,
            "funding_amount": 0.0,
            "hold_bars": 20,
            "reason": "止损",
            "pnl_pct": -1.0,
            "pyramids_done": 1,
        }

        backtest._apply_trade_leg_rollup(position, tp1_leg)
        backtest._apply_trade_leg_rollup(position, final_leg)
        closed_trade = backtest._build_closed_trade(position)

        self.assertEqual(closed_trade["trade_id"], 7)
        self.assertEqual(closed_trade["leg_count"], 2)
        self.assertEqual(closed_trade["reason"], "止损")
        self.assertAlmostEqual(closed_trade["size"], 10000.0)
        self.assertAlmostEqual(closed_trade["closed_size"], 10000.0)
        self.assertAlmostEqual(closed_trade["pnl_amount"], 252.0)
        self.assertAlmostEqual(closed_trade["gross_pnl_amount"], 310.0)
        self.assertAlmostEqual(closed_trade["fee_amount"], 58.0)
        self.assertAlmostEqual(closed_trade["hold_bars"], 17.36, places=2)
        self.assertAlmostEqual(closed_trade["pnl_pct"], 2.52, places=2)

    def test_stop_price_uses_actual_entry_fill_reference(self):
        stop_price, valid_stop = backtest._stop_price_from_entry(
            entry_price=100.03,
            side="short",
            atr=2.0,
            stop_mult=1.5,
            stop_max_loss_pct=50.0,
            leverage=10.0,
        )

        self.assertTrue(valid_stop)
        self.assertAlmostEqual(stop_price, 103.03, places=6)

    def test_pyramid_refresh_reanchors_stop_without_loosen(self):
        position = {
            "entry_signal": "short_breakdown",
            "entry_price": 95.0,
            "stop_price": 110.0,
        }
        market_state = {"atr": 2.0}
        exit_params = {
            "stop_atr_mult": 3.0,
            "breakout_stop_atr_mult": 3.0,
            "stop_max_loss_pct": 50.0,
        }

        backtest._refresh_stop_after_resize(position, market_state, exit_params, leverage=10.0)

        self.assertAlmostEqual(position["stop_price"], 99.75, places=6)


class EvaluationFixesTest(unittest.TestCase):
    def test_collect_daily_path_assigns_overlapping_days_to_latest_window(self):
        window1 = type("Window", (), {"group": "eval", "label": "评估1", "start_date": "2026-01-01", "end_date": "2026-01-02"})()
        window2 = type("Window", (), {"group": "eval", "label": "评估2", "start_date": "2026-01-02", "end_date": "2026-01-03"})()
        results = [
            {
                "window": window1,
                "result": {
                    "daily_return_points": [
                        {"date": "2026-01-01", "return": 0.01},
                        {"date": "2026-01-02", "return": 0.02},
                    ]
                },
            },
            {
                "window": window2,
                "result": {
                    "daily_return_points": [
                        {"date": "2026-01-02", "return": 0.04},
                        {"date": "2026-01-03", "return": -0.01},
                    ]
                },
            },
        ]

        path = _collect_daily_path(results, "eval")
        self.assertEqual(path.returns, [0.01, 0.04, -0.01])
        self.assertEqual(path.unique_days, 3)
        self.assertEqual(path.overlap_days, 1)
        self.assertEqual(path.dropped_points, 1)

    def test_summarize_evaluation_scores_unique_trend_capture_path(self):
        eval_window1 = type("Window", (), {"group": "eval", "label": "评估1", "start_date": "2026-01-01", "end_date": "2026-01-12"})()
        eval_window2 = type("Window", (), {"group": "eval", "label": "评估2", "start_date": "2026-01-10", "end_date": "2026-01-15"})()
        validation_window = type("Window", (), {"group": "validation", "label": "验证1", "start_date": "2026-01-16", "end_date": "2026-01-23"})()
        eval_points_1 = [
            {"timestamp": idx, "label": f"t{idx}", "market_close": close, "atr_ratio": 0.01, "strategy_equity": equity}
            for idx, close, equity in [
                (1, 100.0, 100000.0),
                (2, 103.0, 101000.0),
                (3, 107.0, 104000.0),
                (4, 112.0, 108000.0),
                (5, 118.0, 115000.0),
                (6, 116.0, 114000.0),
                (7, 111.0, 112000.0),
                (8, 103.0, 108000.0),
                (9, 94.0, 114000.0),
                (10, 90.0, 120000.0),
                (11, 94.0, 121000.0),
            ]
        ]
        eval_points_2 = [
            {"timestamp": idx, "label": f"t{idx}", "market_close": close, "atr_ratio": 0.01, "strategy_equity": equity}
            for idx, close, equity in [
                (10, 90.0, 119500.0),
                (11, 94.0, 122000.0),
                (12, 102.0, 125000.0),
                (13, 112.0, 132000.0),
                (14, 121.0, 140000.0),
                (15, 116.0, 139000.0),
            ]
        ]
        validation_points = [
            {"timestamp": idx, "label": f"t{idx}", "market_close": close, "atr_ratio": 0.01, "strategy_equity": equity}
            for idx, close, equity in [
                (16, 116.0, 139000.0),
                (17, 112.0, 141000.0),
                (18, 107.0, 143000.0),
                (19, 100.0, 145000.0),
                (20, 93.0, 148000.0),
                (21, 90.0, 150000.0),
                (22, 94.0, 149000.0),
                (23, 101.0, 147000.0),
            ]
        ]
        results = [
            {
                "window": eval_window1,
                "result": {
                    "return": 3.0,
                    "max_drawdown": 5.0,
                    "trades": 8,
                    "fee_drag_pct": 0.5,
                    "liquidations": 0,
                    "trend_capture_points": eval_points_1,
                },
            },
            {
                "window": eval_window2,
                "result": {
                    "return": 4.0,
                    "max_drawdown": 6.0,
                    "trades": 7,
                    "fee_drag_pct": 0.8,
                    "liquidations": 0,
                    "trend_capture_points": eval_points_2,
                },
            },
            {
                "window": validation_window,
                "result": {
                    "return": 2.0,
                    "max_drawdown": 4.0,
                    "trades": 6,
                    "fee_drag_pct": 0.2,
                    "liquidations": 0,
                    "trend_capture_points": validation_points,
                },
            },
        ]
        gates = GateConfig(
            min_eval_segments=0,
            min_validation_segments=0,
            min_eval_hit_rate=0.0,
            min_validation_hit_rate=0.0,
            min_eval_trend_score=-10.0,
            min_validation_trend_score=-10.0,
            max_capture_drop=100.0,
            min_bull_capture=-10.0,
            min_bear_capture=-10.0,
            max_fee_drag_pct=100.0,
            max_promotion_gap=100.0,
        )

        expected_eval_points = _collect_trend_path(results, "eval").points
        expected_validation_points = _collect_trend_path(results, "validation").points
        full_period_result = {
            "return": 12.34,
            "max_drawdown": 7.8,
            "trend_capture_points": expected_eval_points + expected_validation_points,
        }
        report = summarize_evaluation(
            results,
            gates,
            eval_continuous_result={
                "return": 6.78,
                "max_drawdown": 4.5,
                "trend_capture_points": expected_eval_points,
            },
            validation_continuous_result={
                "return": 2.34,
                "max_drawdown": 3.2,
                "trend_capture_points": expected_validation_points,
            },
            full_period_result=full_period_result,
        )
        expected_eval_report = _trend_score_report(expected_eval_points)
        expected_validation_report = _trend_score_report(expected_validation_points)
        expected_full_period_report = _trend_score_report(full_period_result["trend_capture_points"])

        self.assertAlmostEqual(report.metrics["eval_trend_capture_score"], expected_eval_report.trend_score)
        self.assertAlmostEqual(report.metrics["combined_trend_capture_score"], expected_full_period_report.trend_score)
        self.assertAlmostEqual(report.metrics["full_period_trend_capture_score"], expected_full_period_report.trend_score)
        self.assertAlmostEqual(
            report.metrics["quality_score"],
            0.70 * expected_eval_report.trend_score + 0.30 * expected_eval_report.return_score,
        )
        self.assertAlmostEqual(
            report.metrics["promotion_score"],
            0.70 * expected_validation_report.trend_score + 0.30 * expected_validation_report.return_score,
        )
        self.assertAlmostEqual(
            report.metrics["promotion_gap"],
            report.metrics["quality_score"] - report.metrics["promotion_score"],
        )
        self.assertEqual(report.metrics["validation_block_count_used"], 0.0)
        self.assertEqual(report.metrics["eval_unique_trend_points"], 15.0)
        self.assertEqual(report.metrics["eval_overlap_trend_points"], 2.0)
        self.assertEqual(report.metrics["eval_overlap_trend_points_dropped"], 2.0)
        self.assertEqual(report.metrics["validation_overlap_trend_points"], 0.0)
        self.assertEqual(report.metrics["full_period_return_pct"], 12.34)
        self.assertAlmostEqual(report.metrics["combined_path_return_pct"], expected_full_period_report.path_return_pct)
        self.assertTrue(report.gate_passed)

    def test_summarize_evaluation_rejects_large_quality_promotion_gap(self):
        eval_window = type("Window", (), {"group": "eval", "label": "评估1", "start_date": "2026-01-01", "end_date": "2026-01-12"})()
        validation_window = type("Window", (), {"group": "validation", "label": "验证1", "start_date": "2026-01-13", "end_date": "2026-01-23"})()
        eval_points = [
            {"timestamp": idx, "label": f"e{idx}", "market_close": close, "atr_ratio": 0.01, "strategy_equity": equity}
            for idx, close, equity in [
                (1, 100.0, 100000.0),
                (2, 105.0, 104000.0),
                (3, 111.0, 110000.0),
                (4, 118.0, 118000.0),
                (5, 124.0, 126000.0),
                (6, 122.0, 125000.0),
                (7, 116.0, 123000.0),
                (8, 108.0, 130000.0),
                (9, 98.0, 138000.0),
                (10, 93.0, 143000.0),
                (11, 97.0, 142500.0),
                (12, 104.0, 141000.0),
            ]
        ]
        validation_points = [
            {"timestamp": idx, "label": f"v{idx}", "market_close": close, "atr_ratio": 0.01, "strategy_equity": equity}
            for idx, close, equity in [
                (13, 104.0, 141000.0),
                (14, 101.0, 140000.0),
                (15, 99.0, 139000.0),
                (16, 96.0, 138000.0),
                (17, 94.0, 137000.0),
                (18, 97.0, 136500.0),
                (19, 101.0, 136000.0),
                (20, 105.0, 135500.0),
                (21, 103.0, 135000.0),
                (22, 100.0, 134500.0),
                (23, 98.0, 134000.0),
            ]
        ]
        results = [
            {
                "window": eval_window,
                "result": {
                    "return": 20.0,
                    "max_drawdown": 7.0,
                    "trades": 5,
                    "fee_drag_pct": 0.3,
                    "liquidations": 0,
                    "trend_capture_points": eval_points,
                },
            },
            {
                "window": validation_window,
                "result": {
                    "return": -2.0,
                    "max_drawdown": 4.0,
                    "trades": 4,
                    "fee_drag_pct": 0.2,
                    "liquidations": 0,
                    "trend_capture_points": validation_points,
                },
            },
        ]
        gates = GateConfig(
            min_eval_segments=0,
            min_validation_segments=0,
            min_eval_hit_rate=0.0,
            min_validation_hit_rate=0.0,
            min_eval_trend_score=-10.0,
            min_validation_trend_score=-10.0,
            max_capture_drop=100.0,
            min_bull_capture=-10.0,
            min_bear_capture=-10.0,
            max_fee_drag_pct=100.0,
            max_promotion_gap=0.05,
        )

        report = summarize_evaluation(
            results,
            gates,
            eval_continuous_result={"return": 20.0, "max_drawdown": 7.0, "trend_capture_points": eval_points},
            validation_continuous_result={"return": -2.0, "max_drawdown": 4.0, "trend_capture_points": validation_points},
            full_period_result={"return": 18.0, "max_drawdown": 8.0, "trend_capture_points": eval_points + validation_points},
        )

        self.assertFalse(report.gate_passed)
        self.assertIn("综合分落差过大", report.gate_reason)

    def test_summarize_evaluation_rejects_severe_overfit_concentration(self):
        eval_window = type("Window", (), {"group": "eval", "label": "评估1", "start_date": "2026-01-01", "end_date": "2026-02-10"})()
        validation_window = type("Window", (), {"group": "validation", "label": "验证1", "start_date": "2026-02-11", "end_date": "2026-03-10"})()
        eval_points = [
            {"timestamp": idx, "label": f"t{idx}", "market_close": close, "atr_ratio": 0.01, "strategy_equity": equity}
            for idx, close, equity in [
                (1, 100.0, 100000.0),
                (2, 110.0, 118000.0),
                (3, 125.0, 145000.0),
                (4, 145.0, 182000.0),
                (5, 165.0, 228000.0),
                (6, 182.0, 286000.0),
                (7, 175.0, 286500.0),
                (8, 160.0, 287000.0),
                (9, 142.0, 287500.0),
                (10, 122.0, 288000.0),
                (11, 102.0, 288500.0),
                (12, 110.0, 289000.0),
                (13, 125.0, 289200.0),
                (14, 138.0, 289400.0),
                (15, 149.0, 289600.0),
            ]
        ]
        validation_points = [
            {"timestamp": idx, "label": f"t{idx}", "market_close": close, "atr_ratio": 0.01, "strategy_equity": equity}
            for idx, close, equity in [
                (16, 140.0, 289700.0),
                (17, 128.0, 289750.0),
                (18, 116.0, 289800.0),
                (19, 105.0, 289850.0),
                (20, 95.0, 289900.0),
                (21, 102.0, 289920.0),
                (22, 112.0, 289940.0),
            ]
        ]
        results = [
            {
                "window": eval_window,
                "result": {
                    "return": 180.0,
                    "max_drawdown": 10.0,
                    "trades": 9,
                    "fee_drag_pct": 0.4,
                    "liquidations": 0,
                    "trend_capture_points": eval_points,
                },
            },
            {
                "window": validation_window,
                "result": {
                    "return": 0.3,
                    "max_drawdown": 2.0,
                    "trades": 2,
                    "fee_drag_pct": 0.2,
                    "liquidations": 0,
                    "trend_capture_points": validation_points,
                },
            },
        ]
        gates = GateConfig(
            min_eval_segments=0,
            min_validation_segments=0,
            min_eval_hit_rate=0.0,
            min_validation_hit_rate=0.0,
            min_eval_trend_score=-10.0,
            min_validation_trend_score=-10.0,
            max_capture_drop=100.0,
            min_bull_capture=-10.0,
            min_bear_capture=-10.0,
            max_fee_drag_pct=100.0,
        )

        report = summarize_evaluation(
            results,
            gates,
            eval_continuous_result={
                "return": 180.0,
                "max_drawdown": 10.0,
                "trend_capture_points": eval_points,
            },
            validation_continuous_result={
                "return": 0.3,
                "max_drawdown": 2.0,
                "trend_capture_points": validation_points,
            },
            full_period_result={
                "return": 180.3,
                "max_drawdown": 10.0,
                "trend_capture_points": eval_points + validation_points,
            },
        )

        self.assertFalse(report.gate_passed)
        self.assertIn("过拟合风险", report.gate_reason)
        self.assertGreater(report.metrics["overfit_risk_score"], 0.0)
        self.assertGreater(report.metrics["overfit_top1_positive_share"], 0.60)
        self.assertEqual(report.metrics["overfit_hard_fail"], 1.0)

    def test_partial_eval_gate_snapshot_normalizes_missing_strategy_return(self):
        gate_snapshot = partial_eval_gate_snapshot(
            {
                "trend_capture_points": [
                    {"timestamp": 1, "label": "a", "market_close": 100.0, "atr_ratio": 0.01, "strategy_equity": 100000.0},
                    {"timestamp": 2, "label": "b", "market_close": 110.0, "atr_ratio": 0.01, "strategy_equity": 110000.0},
                ]
            }
        )

        self.assertEqual(gate_snapshot["unique_points"], 2.0)
        self.assertGreaterEqual(gate_snapshot["path_return_pct"], 0.0)


class StrategyValidationFixesTest(unittest.TestCase):
    def test_validate_strategy_source_rejects_reversed_param_relations(self):
        source = """
# PARAMS_START
PARAMS = {
    'intraday_adx_min': 10,
    'hourly_adx_min': 10,
    'fourh_adx_min': 10,
    'breakout_adx_min': 10,
    'breakdown_adx_min': 10,
    'breakout_lookback': 10,
    'breakdown_lookback': 10,
    'breakout_rsi_min': 60,
    'breakout_rsi_max': 55,
    'breakdown_rsi_min': 20,
    'breakdown_rsi_max': 60,
    'breakout_volume_ratio_min': 1.0,
    'breakdown_volume_ratio_min': 1.0,
    'breakout_body_ratio_min': 0.3,
    'breakdown_body_ratio_min': 0.3,
    'breakout_close_pos_min': 0.5,
    'breakdown_close_pos_max': 0.5,
    'intraday_ema_fast': 20,
    'intraday_ema_slow': 10,
    'hourly_ema_fast': 10,
    'hourly_ema_slow': 20,
    'fourh_ema_fast': 10,
    'fourh_ema_slow': 20,
    'macd_fast': 12,
    'macd_slow': 26,
    'macd_signal': 9,
    'volume_lookback': 10,
}
# PARAMS_END

def _is_sideways_regime(*args, **kwargs):
    return False

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def strategy(*args, **kwargs):
    return None
"""
        with self.assertRaises(StrategySourceError):
            validate_strategy_source(source)

    def test_validate_editable_boundaries_rejects_non_editable_changes(self):
        base_source = """
# PARAMS_START
PARAMS = {'intraday_adx_min': 10, 'hourly_adx_min': 10, 'fourh_adx_min': 10, 'breakout_adx_min': 10, 'breakdown_adx_min': 10, 'breakout_lookback': 10, 'breakdown_lookback': 10, 'breakout_rsi_min': 40, 'breakout_rsi_max': 60, 'breakdown_rsi_min': 20, 'breakdown_rsi_max': 60, 'breakout_volume_ratio_min': 1.0, 'breakdown_volume_ratio_min': 1.0, 'breakout_body_ratio_min': 0.3, 'breakdown_body_ratio_min': 0.3, 'breakout_close_pos_min': 0.5, 'breakdown_close_pos_max': 0.5, 'intraday_ema_fast': 9, 'intraday_ema_slow': 20, 'hourly_ema_fast': 10, 'hourly_ema_slow': 20, 'fourh_ema_fast': 10, 'fourh_ema_slow': 20, 'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9, 'volume_lookback': 10}
# PARAMS_END

def helper():
    return 1

def _is_sideways_regime(*args, **kwargs):
    return False

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def strategy(*args, **kwargs):
    return helper()
"""
        candidate_source = base_source.replace("return 1", "return 2", 1)

        with self.assertRaises(StrategySourceError):
            validate_editable_region_boundaries(base_source, candidate_source, EDITABLE_REGIONS)

    def test_validate_editable_boundaries_allows_strategy_change(self):
        base_source = """
# PARAMS_START
PARAMS = {'intraday_adx_min': 10, 'hourly_adx_min': 10, 'fourh_adx_min': 10, 'breakout_adx_min': 10, 'breakdown_adx_min': 10, 'breakout_lookback': 10, 'breakdown_lookback': 10, 'breakout_rsi_min': 40, 'breakout_rsi_max': 60, 'breakdown_rsi_min': 20, 'breakdown_rsi_max': 60, 'breakout_volume_ratio_min': 1.0, 'breakdown_volume_ratio_min': 1.0, 'breakout_body_ratio_min': 0.3, 'breakdown_body_ratio_min': 0.3, 'breakout_close_pos_min': 0.5, 'breakdown_close_pos_max': 0.5, 'intraday_ema_fast': 9, 'intraday_ema_slow': 20, 'hourly_ema_fast': 10, 'hourly_ema_slow': 20, 'fourh_ema_fast': 10, 'fourh_ema_slow': 20, 'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9, 'volume_lookback': 10}
# PARAMS_END

def helper():
    return 1

def _is_sideways_regime(*args, **kwargs):
    return False

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def strategy(*args, **kwargs):
    return helper()
"""
        candidate_source = base_source.replace("return helper()", "return None", 1)

        validate_editable_region_boundaries(base_source, candidate_source, EDITABLE_REGIONS)


class JournalPromptFixesTest(unittest.TestCase):
    def test_cluster_for_tags_groups_ownership_variants(self):
        self.assertEqual(cluster_for_tags(["acceptance_continuity", "ownership_transfer"]), "ownership_cluster")

    def test_cluster_key_prefers_stable_canonical_cluster(self):
        self.assertEqual(
            cluster_key_for_components("arrival_reallocation", ["ownership_takeover", "continuation_prune"]),
            "ownership_cluster",
        )
        self.assertEqual(
            cluster_key_for_components("ownership_cluster", ["position_state_takeover"]),
            "ownership_cluster",
        )

    def test_cluster_key_for_entry_uses_stored_cluster_when_present(self):
        entry = {
            "cluster_key": "ownership_cluster",
            "closest_failed_cluster": "arrival_reallocation",
            "change_tags": ["ownership_takeover"],
        }
        self.assertEqual(cluster_key_for_entry(entry), "ownership_cluster")

    def test_compact_prompt_includes_cluster_risk_summary(self):
        compact_data = {
            "rounds": [
                {
                    "score_regime": "trend_capture_v1",
                    "entry_count": 20,
                    "accepted_count": 0,
                    "rejected_count": 20,
                    "early_rejected_count": 0,
                    "runtime_failed_count": 1,
                    "cluster_summary": {
                        "ownership_cluster": {
                            "attempts": 6,
                            "failures": 6,
                            "zero_delta": 6,
                            "runtime_errors": 0,
                            "best_delta": 0.0,
                            "label": "EXHAUSTED",
                        }
                    },
                }
            ]
        }

        summary = "\n".join(_format_compact_for_prompt(compact_data, limit=6, score_regime="trend_capture_v1"))
        self.assertIn("历史方向簇摘要", summary)
        self.assertIn("ownership_cluster", summary)
        self.assertIn("EXHAUSTED", summary)

    def test_compact_prompt_filters_rounds_by_score_regime(self):
        compact_data = {
            "rounds": [
                {
                    "score_regime": "old_regime",
                    "entry_count": 20,
                    "accepted_count": 10,
                    "rejected_count": 10,
                    "early_rejected_count": 0,
                    "runtime_failed_count": 0,
                    "cluster_summary": {
                        "sideways_cluster": {
                            "attempts": 3,
                            "failures": 2,
                            "zero_delta": 1,
                            "runtime_errors": 0,
                            "best_delta": 0.1,
                        }
                    },
                },
                {
                    "score_regime": "trend_capture_v1",
                    "entry_count": 20,
                    "accepted_count": 0,
                    "rejected_count": 20,
                    "early_rejected_count": 0,
                    "runtime_failed_count": 1,
                    "cluster_summary": {
                        "ownership_cluster": {
                            "attempts": 6,
                            "failures": 6,
                            "zero_delta": 6,
                            "runtime_errors": 0,
                            "best_delta": 0.0,
                        }
                    },
                },
            ]
        }

        summary = "\n".join(_format_compact_for_prompt(compact_data, limit=6, score_regime="trend_capture_v1"))
        self.assertIn("ownership_cluster", summary)
        self.assertNotIn("sideways_cluster", summary)
        self.assertIn("已跳过 1 段非当前评分口径", summary)

    def test_journal_summary_puts_direction_risk_board_first(self):
        entries = []
        for idx in range(5):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"candidate_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 1.0,
                    "quality_score": 1.0,
                    "promotion_delta": 0.0,
                    "gate_reason": "通过",
                    "change_tags": ["ownership_reset", "acceptance_continuity"],
                    "edited_regions": ["strategy"],
                    "hypothesis": "重复测试 ownership 方向",
                    "score_regime": "trend_capture_v1",
                }
            )

        summary = build_journal_prompt_summary(entries, limit=8)
        first_line = summary.splitlines()[0]

        self.assertIn("方向风险表", first_line)
        self.assertIn("ownership_cluster", summary)
        self.assertIn("EXHAUSTED", summary)

    def test_journal_summary_emits_overfit_risk_board_after_direction_risk(self):
        entries = [
            {
                "iteration": 7,
                "candidate_id": "candidate_overfit",
                "outcome": "accepted",
                "stop_stage": "full_eval",
                "promotion_score": 0.72,
                "quality_score": 0.44,
                "promotion_delta": 0.12,
                "gate_reason": "通过",
                "change_tags": ["long_breakout"],
                "edited_regions": ["strategy"],
                "hypothesis": "结果主要吃到少数强趋势段。",
                "metrics": {
                    "overfit_risk_score": 48.0,
                    "overfit_top1_positive_share": 0.52,
                    "overfit_chain_positive_share": 0.67,
                    "overfit_coverage_ratio": 0.33,
                    "overfit_bull_bear_gap": 0.72,
                    "overfit_capture_drop_abs": 0.18,
                },
                "score_regime": "trend_capture_v1",
            }
        ]

        summary = build_journal_prompt_summary(entries, limit=8)

        direction_index = summary.index("方向风险表")
        overfit_index = summary.index("过拟合风险表")
        self.assertLess(direction_index, overfit_index)
        self.assertIn("| 7 | 保留 | 0.72 | 高 | 48 |", summary)
        self.assertIn("慎重参考", summary)

    def test_journal_summary_emits_exploration_trigger_after_three_low_change_rounds(self):
        entries = []
        for idx in range(3):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"candidate_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 0.88,
                    "quality_score": 0.84,
                    "promotion_delta": 0.0,
                    "gate_reason": "通过",
                    "change_tags": ["reload_impulse_filter", "short_trend_quality"],
                    "edited_regions": ["strategy"],
                    "closest_failed_cluster": "impulse_persistence",
                    "hypothesis": "连续几轮都只是成熟空头的近邻微调",
                    "core_factors": [
                        {
                            "name": "continuation_energy_decay",
                            "thesis": "成熟空头尾段的弱续跌更容易拖累手续费。",
                            "current_signal": "最近几轮都在同一解释框架里打转。",
                        }
                    ],
                    "metrics": {
                        "total_trades": 98.0,
                        "avg_fee_drag": 0.76,
                        "combined_trend_capture_score": 0.81,
                        "segment_hit_rate": 0.42,
                        "bull_capture_score": 0.18,
                        "bear_capture_score": 0.53,
                    },
                    "score_regime": "trend_capture_v1",
                }
            )

        summary = build_journal_prompt_summary(entries, limit=8)

        self.assertIn("探索触发（必须执行）", summary)
        self.assertIn("impulse_persistence", summary)
        self.assertIn("continuation_energy_decay", summary)

    def test_journal_summary_emits_exploration_trigger_after_three_duplicate_skips(self):
        entries = []
        for idx in range(3):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"duplicate_{idx}",
                    "outcome": "duplicate_skipped",
                    "stop_stage": "duplicate_source",
                    "promotion_delta": None,
                    "gate_reason": "候选源码与当前最优完全相同",
                    "change_tags": ["ownership_takeover", "arrival_entry"],
                    "edited_regions": ["strategy"],
                    "closest_failed_cluster": "ownership_cluster",
                    "hypothesis": "模型重复回传了原文件。",
                    "score_regime": "trend_capture_v1",
                }
            )

        summary = build_journal_prompt_summary(entries, limit=8)

        self.assertIn("重复跳过 3", summary)
        self.assertIn("探索触发（必须执行）", summary)
        self.assertIn("没有产生有效代码改动", summary)
        self.assertIn("ownership_cluster", summary)

    def test_journal_summary_prefers_current_score_regime_when_provided(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "old_regime",
                "outcome": "accepted",
                "stop_stage": "full_eval",
                "promotion_score": 0.80,
                "quality_score": 0.60,
                "promotion_delta": 0.05,
                "gate_reason": "通过",
                "change_tags": ["long_breakout"],
                "edited_regions": ["strategy"],
                "hypothesis": "旧评分口径历史。",
                "score_regime": "trend_capture_v2",
            }
        ]

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v3")

        self.assertIn("方向风险表", summary)
        self.assertIn("等待新历史", summary)
        self.assertIn("旧评分口径弱参考", summary)
        self.assertIn("trend_capture_v2", summary)
        self.assertNotIn("| 1 | old_regime |", summary)

    def test_journal_summary_keeps_empty_table_after_reset(self):
        summary = build_journal_prompt_summary([], limit=8)

        self.assertIn("方向风险表", summary)
        self.assertIn("最近未压缩轮次表", summary)
        self.assertIn("等待新历史", summary)

    def test_journal_summary_keeps_current_regime_recent_rows_after_global_compaction(self):
        entries = []
        for idx in range(30):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"legacy_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 0.10,
                    "quality_score": 0.10,
                    "promotion_delta": 0.0,
                    "gate_reason": "通过",
                    "change_tags": ["legacy_breakout"],
                    "edited_regions": ["strategy"],
                    "hypothesis": "旧口径历史。",
                    "score_regime": "trend_capture_v1",
                }
            )
        for idx in range(5):
            entries.append(
                {
                    "iteration": 31 + idx,
                    "candidate_id": f"current_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 0.40,
                    "quality_score": 0.50,
                    "promotion_delta": 0.0,
                    "gate_reason": "通过",
                    "change_tags": ["current_breakout"],
                    "edited_regions": ["strategy"],
                    "hypothesis": "当前 v4 历史。",
                    "score_regime": "trend_capture_v4",
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            with journal_path.open("w") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            journal_path.with_suffix(".compact.json").write_text(
                json.dumps(
                    {
                        "compacted_up_to": 30,
                        "total_compacted_entries": 30,
                        "rounds": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

            summary = build_journal_prompt_summary(
                entries,
                limit=8,
                journal_path=journal_path,
                current_score_regime="trend_capture_v4",
            )

        self.assertIn("最近未压缩轮次共 5 条", summary)
        self.assertIn("current_0", summary)
        self.assertIn("current_4", summary)
        self.assertNotIn("legacy_29", summary)

    def test_journal_summary_appends_legacy_regime_as_secondary_reference(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "legacy_pass",
                "outcome": "accepted",
                "stop_stage": "full_eval",
                "promotion_score": 0.75,
                "quality_score": 0.65,
                "promotion_delta": 0.08,
                "gate_reason": "通过",
                "change_tags": ["legacy_breakout"],
                "edited_regions": ["strategy"],
                "hypothesis": "旧口径下可参考的方向。",
                "score_regime": "trend_capture_v1",
            },
            {
                "iteration": 2,
                "candidate_id": "current_fail",
                "outcome": "rejected",
                "stop_stage": "full_eval",
                "promotion_score": 0.40,
                "quality_score": 0.45,
                "promotion_delta": 0.0,
                "gate_reason": "通过",
                "change_tags": ["current_breakout"],
                "edited_regions": ["strategy"],
                "hypothesis": "当前口径主参考。",
                "score_regime": "trend_capture_v4",
            },
        ]

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v4")

        self.assertIn("方向风险表", summary)
        self.assertIn("current_breakout", summary)
        self.assertIn("旧评分口径弱参考", summary)
        self.assertIn("trend_capture_v1", summary)
        self.assertIn("legacy_breakout", summary)


class SmokeWindowSelectionTest(unittest.TestCase):
    def test_select_smoke_windows_includes_validation_when_count_is_three(self):
        Window = type("Window", (), {})
        windows = []
        for idx in range(1, 6):
            window = Window()
            window.group = "eval"
            window.label = f"评估{idx}"
            windows.append(window)
        validation = Window()
        validation.group = "validation"
        validation.label = "验证1"
        windows.append(validation)

        selected = select_smoke_windows(windows, 3)

        self.assertEqual([window.label for window in selected], ["评估1", "验证1", "评估3"])


class CodexExecClientTest(unittest.TestCase):
    def test_generate_json_object_emits_progress_heartbeats(self):
        events: list[dict[str, object]] = []

        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 43210
                self.returncode = 0
                self.communicate_calls = 0

            def communicate(self, input=None, timeout=None):
                self.communicate_calls += 1
                if self.communicate_calls < 3:
                    raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout)
                return (json.dumps({"ok": True}), "")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        config = StrategyClientConfig(
            codex_bin="codex",
            model="gpt-5.4",
            reasoning_effort="medium",
            approval_policy="never",
            sandbox="read-only",
            timeout_seconds=90,
            use_ephemeral=True,
        )

        with mock.patch("codex_exec_client.shutil.which", return_value="/usr/local/bin/codex"):
            with mock.patch("codex_exec_client.subprocess.Popen", return_value=FakePopen()):
                payload = generate_json_object(
                    prompt="test",
                    system_prompt="system",
                    config=config,
                    text_format={
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                            "additionalProperties": False,
                        },
                    },
                    progress_callback=events.append,
                )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(events[0]["event"], "started")
        self.assertIn("heartbeat", [event["event"] for event in events])
        self.assertEqual(events[-1]["event"], "completed")

    def test_generate_json_object_passes_noninteractive_approval_policy(self):
        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 43210
                self.returncode = 0

            def communicate(self, input=None, timeout=None):
                return (json.dumps({"ok": True}), "")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

        config = StrategyClientConfig(
            codex_bin="codex",
            model="gpt-5.4",
            reasoning_effort="medium",
            approval_policy="never",
            sandbox="danger-full-access",
            timeout_seconds=90,
            use_ephemeral=True,
        )

        with mock.patch("codex_exec_client.shutil.which", return_value="/usr/local/bin/codex"):
            with mock.patch("codex_exec_client.subprocess.Popen", return_value=FakePopen()) as popen:
                generate_json_object(
                    prompt="test",
                    system_prompt="system",
                    config=config,
                    text_format={
                        "type": "json_schema",
                        "schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                            "additionalProperties": False,
                        },
                    },
                )

        command = popen.call_args.args[0]
        self.assertEqual(command[:4], ["codex", "-a", "never", "exec"])

    def test_generate_json_object_kills_process_group_after_timeout(self):
        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 54321
                self.returncode = None

            def communicate(self, input=None, timeout=None):
                raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout)

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

            def wait(self, timeout=None):
                self.returncode = -15
                return self.returncode

        config = StrategyClientConfig(
            codex_bin="codex",
            model="gpt-5.4",
            reasoning_effort="medium",
            approval_policy="never",
            sandbox="read-only",
            timeout_seconds=30,
            use_ephemeral=True,
        )
        monotonic_values = iter([0.0, 5.0, 15.0, 31.0, 31.5, 32.0])

        with mock.patch("codex_exec_client.shutil.which", return_value="/usr/local/bin/codex"):
            with mock.patch("codex_exec_client.subprocess.Popen", return_value=FakePopen()):
                with mock.patch("codex_exec_client.time.monotonic", side_effect=lambda: next(monotonic_values)):
                    with mock.patch("codex_exec_client.os.killpg") as killpg:
                        with self.assertRaises(StrategyGenerationTransientError):
                            generate_json_object(
                                prompt="test",
                                system_prompt="system",
                                config=config,
                                text_format={
                                    "type": "json_schema",
                                    "schema": {
                                        "type": "object",
                                        "properties": {"ok": {"type": "boolean"}},
                                        "required": ["ok"],
                                        "additionalProperties": False,
                                    },
                                },
                            )

        self.assertGreaterEqual(killpg.call_count, 1)


class DiscordSummaryFormattingTest(unittest.TestCase):
    def test_discord_summary_uses_comparable_eval_validation_rows(self):
        report = EvaluationReport(
            metrics={
                "eval_trend_capture_score": 0.61,
                "validation_trend_capture_score": 0.35,
                "eval_segment_hit_rate": 0.58,
                "validation_segment_hit_rate": 0.41,
                "eval_major_segment_count": 11.0,
                "validation_major_segment_count": 10.0,
                "capture_drop": 0.26,
                "promotion_gap": 0.16,
                "quality_score": 0.55,
                "promotion_score": 0.71,
                "validation_block_score_mean": 0.42,
                "validation_block_score_std": 0.11,
                "validation_block_score_min": 0.21,
                "validation_block_fail_count": 0.0,
                "validation_block_count_used": 3.0,
                "combined_trend_capture_score": 0.48,
                "combined_return_score": 1.22,
                "arrival_capture_score": 0.08,
                "escort_capture_score": 0.73,
                "turn_adaptation_score": 0.31,
                "bull_capture_score": 0.28,
                "bear_capture_score": 0.66,
                "segment_hit_rate": 0.50,
                "major_segment_count": 22.0,
                "full_period_return_pct": 123.4,
                "eval_path_return_pct": 12.3,
                "eval_avg_return": 1.23,
                "validation_path_return_pct": 34.5,
                "validation_avg_return": 45.67,
                "combined_path_return_pct": 88.9,
                "overfit_risk_score": 20.0,
                "overfit_hard_fail": 0.0,
                "overfit_top1_positive_share": 0.12,
                "overfit_chain_positive_share": 0.19,
                "overfit_coverage_ratio": 1.0,
                "eval_unique_trend_points": 3655.0,
                "worst_drawdown": 18.2,
                "total_trades": 123.0,
                "avg_fee_drag": 1.8,
            },
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )

        message = build_discord_summary_message(
            title="test",
            report=report,
            eval_window_count=29,
            validation_window_count=1,
        )

        self.assertIn("评/验趋势分", message)
        self.assertIn("评/验命中率", message)
        self.assertIn("评/验趋势段", message)
        self.assertIn("全段连续收益", message)
        self.assertIn("评估连续收益", message)
        self.assertIn("验证连续收益", message)
        self.assertIn("验证分块均值/std", message)
        self.assertIn("验证最差块/负块数", message)
        self.assertIn("评估窗口均值收益", message)
        self.assertIn("全段趋势/收益分", message)
        self.assertNotIn("验证整段收益", message)
        self.assertNotIn("拼接路径收益", message)
        self.assertNotIn("综合路径收益", message)
        self.assertNotIn("| 收益 | 1.23% / 45.67% |", message)
        self.assertLess(message.index("全段连续收益"), message.index("评/验趋势分"))
        self.assertLess(message.index("评估连续收益"), message.index("评/验趋势分"))
        self.assertLess(message.index("验证连续收益"), message.index("评/验趋势分"))
        self.assertLess(message.index("评估窗口均值收益"), message.index("评/验趋势分"))


class ChartRenderingTest(unittest.TestCase):
    def test_render_performance_chart_writes_png(self):
        if not charts_available():
            self.skipTest("matplotlib not installed for current interpreter")

        curve = [
            {"date": "2026-01-01", "equity": 100000.0, "market_close": 50000.0},
            {"date": "2026-01-02", "equity": 103000.0, "market_close": 51000.0},
            {"date": "2026-01-03", "equity": 101000.0, "market_close": 50800.0},
            {"date": "2026-01-04", "equity": 108000.0, "market_close": 52000.0},
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "chart.png"
            result = render_performance_chart(
                daily_equity_curve=curve,
                output_path=output_path,
                title="Test",
                subtitle="Window",
            )

            self.assertEqual(result, output_path)
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
            self.assertEqual(output_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
