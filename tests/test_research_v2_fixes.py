import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import backtest_macd_aggressive as backtest
from research_v2.config import GateConfig
from research_v2.evaluation import _collect_daily_path, _collect_trend_path, _trend_score_report, summarize_evaluation
from research_v2.journal import _format_compact_for_prompt, build_journal_prompt_summary, cluster_for_tags
from research_v2.strategy_code import StrategySourceError, validate_strategy_source


class BacktestFixesTest(unittest.TestCase):
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
        )

        report = summarize_evaluation(results, gates)
        expected_eval_points = _collect_trend_path(results, "eval").points
        expected_all_points = _collect_trend_path(results, None).points
        expected_eval_report = _trend_score_report(expected_eval_points)
        expected_all_report = _trend_score_report(expected_all_points)

        self.assertAlmostEqual(report.metrics["eval_trend_capture_score"], expected_eval_report.trend_score)
        self.assertAlmostEqual(report.metrics["combined_trend_capture_score"], expected_all_report.trend_score)
        self.assertAlmostEqual(
            report.metrics["quality_score"],
            0.70 * expected_eval_report.trend_score + 0.30 * expected_eval_report.return_score,
        )
        self.assertAlmostEqual(
            report.metrics["promotion_score"],
            0.70 * expected_all_report.trend_score + 0.30 * expected_all_report.return_score,
        )
        self.assertEqual(report.metrics["eval_unique_trend_points"], 15.0)
        self.assertEqual(report.metrics["eval_overlap_trend_points"], 2.0)
        self.assertEqual(report.metrics["eval_overlap_trend_points_dropped"], 2.0)
        self.assertEqual(report.metrics["validation_overlap_trend_points"], 0.0)
        self.assertTrue(report.gate_passed)

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

        report = summarize_evaluation(results, gates)

        self.assertFalse(report.gate_passed)
        self.assertIn("过拟合风险", report.gate_reason)
        self.assertGreater(report.metrics["overfit_risk_score"], 0.0)
        self.assertGreater(report.metrics["overfit_top1_positive_share"], 0.60)
        self.assertEqual(report.metrics["overfit_hard_fail"], 1.0)


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


class JournalPromptFixesTest(unittest.TestCase):
    def test_cluster_for_tags_groups_ownership_variants(self):
        self.assertEqual(cluster_for_tags(["acceptance_continuity", "ownership_transfer"]), "ownership_cluster")

    def test_compact_prompt_includes_cluster_risk_summary(self):
        compact_data = {
            "rounds": [
                {
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

        summary = "\n".join(_format_compact_for_prompt(compact_data, limit=6))
        self.assertIn("历史方向簇摘要", summary)
        self.assertIn("ownership_cluster", summary)
        self.assertIn("EXHAUSTED", summary)

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

    def test_journal_summary_keeps_empty_table_after_reset(self):
        summary = build_journal_prompt_summary([], limit=8)

        self.assertIn("方向风险表", summary)
        self.assertIn("最近未压缩轮次表", summary)
        self.assertIn("等待新历史", summary)


if __name__ == "__main__":
    unittest.main()
