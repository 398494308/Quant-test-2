import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

import backtest_macd_aggressive as backtest
from codex_exec_client import (
    StrategyClientConfig,
    StrategyGenerationSessionError,
    StrategyGenerationTransientError,
    generate_json_object,
    generate_text_response,
)
import freqtrade_macd_aggressive as ft_adapter
from market_data_catalog import default_market_data_paths
import scripts.research_macd_aggressive_v2 as research_script
import strategy_macd_aggressive as strategy_module
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
    ORDINARY_REGION_FAMILIES,
    append_journal_archive,
    build_exploration_guard_state,
    _format_compact_for_prompt,
    build_journal_prompt_summary,
    cluster_for_tags,
    cluster_key_for_components,
    cluster_key_for_entry,
    evaluate_candidate_failure_wiki_guard,
    evaluate_candidate_exploration_guard,
    load_failure_wiki_index,
    ordinary_region_families,
    region_families_for_regions,
)
from research_v2.notifications import build_discord_summary_message
from research_v2.prompting import (
    EDITABLE_REGIONS,
    build_candidate_response_format_instructions,
    build_strategy_candidate_summary_prompt,
    build_edit_completion_instructions,
    build_strategy_agents_instructions,
    build_strategy_edit_worker_prompt,
    build_strategy_exploration_repair_prompt,
    build_strategy_no_edit_repair_prompt,
    build_strategy_round_brief_repair_prompt,
    build_strategy_research_prompt,
    build_strategy_summary_worker_system_prompt,
    build_strategy_runtime_repair_prompt,
    build_strategy_system_prompt,
    build_strategy_worker_system_prompt,
)
from research_v2.strategy_code import (
    REQUIRED_FUNCTIONS,
    StrategyCandidate,
    StrategyCoreFactor,
    StrategySourceError,
    build_strategy_complexity_pressure,
    build_strategy_complexity_delta,
    build_system_edit_signature,
    format_strategy_complexity_headroom,
    repair_missing_required_functions,
    validate_editable_region_boundaries,
    validate_strategy_source,
)


def make_gate_config(**overrides):
    payload = {
        "min_development_mean_score": -10.0,
        "min_development_median_score": -10.0,
        "min_validation_hit_rate": 0.0,
        "min_validation_trend_score": -10.0,
        "max_dev_validation_gap": 100.0,
        "min_validation_bull_capture": -10.0,
        "min_validation_bear_capture": -10.0,
        "max_fee_drag_pct": 100.0,
        "validation_block_count": 3,
        "min_validation_block_floor": -100.0,
        "max_validation_block_failures": 100,
    }
    payload.update(overrides)
    return GateConfig(**payload)


def _minimal_required_strategy_source(*, trend_quality_bool_ops: int = 0) -> str:
    body_by_function = {
        "_sideways_release_flags": "return {}",
        "_is_sideways_regime": "return False",
        "_flow_signal_metrics": "return {}",
        "_flow_confirmation_ok": "return True",
        "_flow_entry_ok": "return True",
        "_trend_quality_long": "return True",
        "_trend_quality_short": "return True",
        "_trend_quality_ok": (
            "return " + " or ".join(["False"] * (trend_quality_bool_ops + 1))
            if trend_quality_bool_ops > 0
            else "return False"
        ),
        "_trend_followthrough_long": "return True",
        "_trend_followthrough_short": "return True",
        "_trend_followthrough_ok": "return True",
        "_long_entry_signal": "return None",
        "_short_entry_signal": "return None",
        "strategy": "return None",
    }
    blocks = [
        "# PARAMS_START",
        "PARAMS = {'breakout_volume_ratio_min': 1.0}",
        "# PARAMS_END",
        "",
    ]
    for function_name in REQUIRED_FUNCTIONS:
        blocks.append(f"def {function_name}(*args, **kwargs):")
        blocks.append(f"    {body_by_function[function_name]}")
        blocks.append("")
    return "\n".join(blocks)


class BacktestFixesTest(unittest.TestCase):
    def test_default_market_data_paths_use_okx_naming(self):
        paths = default_market_data_paths()

        self.assertIn("okx_btc_usdt_swap_15m", paths.intraday_15m.name)
        self.assertIn("okx_btc_usdt_swap_funding", paths.funding.name)

    def test_aggregate_bars_rolls_up_flow_columns(self):
        rows = [
            {
                "timestamp": index * 900_000,
                "open": 100.0 + index,
                "high": 101.0 + index,
                "low": 99.0 + index,
                "close": 100.5 + index,
                "volume": 10.0 + index,
                "quote_volume": 1000.0 + index * 10.0,
                "trade_count": 100.0 + index,
                "taker_buy_volume": 6.0 + index,
                "taker_sell_volume": 4.0,
            }
            for index in range(4)
        ]

        aggregated = backtest._aggregate_bars(rows, 4)

        self.assertEqual(len(aggregated), 1)
        self.assertAlmostEqual(aggregated[0]["quote_volume"], 4060.0)
        self.assertAlmostEqual(aggregated[0]["trade_count"], 406.0)
        self.assertAlmostEqual(aggregated[0]["taker_buy_volume"], 30.0)
        self.assertAlmostEqual(aggregated[0]["taker_sell_volume"], 16.0)

    def test_load_ohlcv_data_derives_okx_flow_proxy_when_flow_columns_missing(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as handle:
            handle.write("timestamp,open,high,low,close,volume,quote_volume\n")
            handle.write("1711929600000,70000,70100,69900,70080,12,840960\n")
            temp_path = handle.name

        try:
            backtest.load_ohlcv_data.cache_clear()
            rows = backtest.load_ohlcv_data(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)
            backtest.load_ohlcv_data.cache_clear()

        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["quote_volume"], 840960.0)
        self.assertGreater(rows[0]["trade_count"], 0.0)
        self.assertAlmostEqual(rows[0]["taker_buy_volume"] + rows[0]["taker_sell_volume"], rows[0]["volume"])

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
        window1 = type("Window", (), {"group": "eval", "label": "train1", "start_date": "2026-01-01", "end_date": "2026-01-02"})()
        window2 = type("Window", (), {"group": "eval", "label": "train2", "start_date": "2026-01-02", "end_date": "2026-01-03"})()
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
        eval_window1 = type("Window", (), {"group": "eval", "label": "train1", "start_date": "2026-01-01", "end_date": "2026-01-12"})()
        eval_window2 = type("Window", (), {"group": "eval", "label": "train2", "start_date": "2026-01-10", "end_date": "2026-01-15"})()
        validation_window = type("Window", (), {"group": "validation", "label": "val1", "start_date": "2026-01-16", "end_date": "2026-01-23"})()
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
        gates = make_gate_config()

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
        expected_eval_window_scores = [
            _trend_score_report(_collect_trend_path([results[0]], "eval").points),
            _trend_score_report(_collect_trend_path([results[1]], "eval").points),
        ]
        expected_validation_report = _trend_score_report(expected_validation_points)
        expected_full_period_report = _trend_score_report(full_period_result["trend_capture_points"])

        self.assertAlmostEqual(
            report.metrics["eval_trend_capture_score"],
            sum(item.trend_score for item in expected_eval_window_scores) / len(expected_eval_window_scores),
        )
        self.assertAlmostEqual(report.metrics["combined_trend_capture_score"], expected_full_period_report.trend_score)
        self.assertAlmostEqual(report.metrics["full_period_trend_capture_score"], expected_full_period_report.trend_score)
        self.assertAlmostEqual(
            report.metrics["quality_score"],
            sum(0.70 * item.trend_score + 0.30 * item.return_score for item in expected_eval_window_scores)
            / len(expected_eval_window_scores),
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
        eval_window = type("Window", (), {"group": "eval", "label": "train1", "start_date": "2026-01-01", "end_date": "2026-01-12"})()
        validation_window = type("Window", (), {"group": "validation", "label": "val1", "start_date": "2026-01-13", "end_date": "2026-01-23"})()
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
        gates = make_gate_config(max_dev_validation_gap=0.05)

        report = summarize_evaluation(
            results,
            gates,
            eval_continuous_result={"return": 20.0, "max_drawdown": 7.0, "trend_capture_points": eval_points},
            validation_continuous_result={"return": -2.0, "max_drawdown": 4.0, "trend_capture_points": validation_points},
            full_period_result={"return": 18.0, "max_drawdown": 8.0, "trend_capture_points": eval_points + validation_points},
        )

        self.assertFalse(report.gate_passed)
        self.assertIn("train/val分数落差过大", report.gate_reason)

    def test_summarize_evaluation_rejects_severe_overfit_concentration(self):
        eval_window = type("Window", (), {"group": "eval", "label": "train1", "start_date": "2026-01-01", "end_date": "2026-02-10"})()
        validation_window = type("Window", (), {"group": "validation", "label": "val1", "start_date": "2026-02-11", "end_date": "2026-03-10"})()
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
        gates = make_gate_config()

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
        self.assertIn("train+val过拟合", report.gate_reason)
        self.assertGreater(report.metrics["overfit_risk_score"], 0.0)
        self.assertGreater(report.metrics["overfit_top1_positive_share"], 0.60)
        self.assertEqual(report.metrics["overfit_hard_fail"], 1.0)

    def test_summarize_evaluation_emits_funnel_and_low_activity_soft_signal(self):
        eval_window = type("Window", (), {"group": "eval", "label": "train1", "start_date": "2026-01-01", "end_date": "2026-01-10"})()
        validation_window = type("Window", (), {"group": "validation", "label": "val1", "start_date": "2026-01-11", "end_date": "2026-01-18"})()

        def build_points(prefix: str, start_ts: int, closes: list[float], equities: list[float]) -> list[dict[str, float | str]]:
            return [
                {
                    "timestamp": start_ts + index,
                    "label": f"{prefix}{index}",
                    "market_close": close,
                    "atr_ratio": 0.01,
                    "strategy_equity": equity,
                }
                for index, (close, equity) in enumerate(zip(closes, equities), start=1)
            ]

        eval_points = build_points(
            "e",
            0,
            [100.0, 104.0, 108.0, 111.0, 109.0, 113.0],
            [100000.0, 101500.0, 104000.0, 105500.0, 105000.0, 107000.0],
        )
        validation_points = build_points(
            "v",
            100,
            [113.0, 111.0, 108.0, 104.0, 101.0, 99.0],
            [107000.0, 106500.0, 106000.0, 105000.0, 104000.0, 103500.0],
        )
        results = [
            {
                "window": eval_window,
                "result": {
                    "return": 3.2,
                    "max_drawdown": 3.0,
                    "trades": 2,
                    "fee_drag_pct": 0.3,
                    "liquidations": 0,
                    "trend_capture_points": eval_points,
                    "strategy_funnel": {
                        "long": {"sideways_pass": 12, "outer_context_pass": 8, "path_pass": 6, "final_veto_pass": 2},
                        "short": {"sideways_pass": 12, "outer_context_pass": 5, "path_pass": 2, "final_veto_pass": 0},
                    },
                    "filled_side_entries": {"long": 2, "short": 0},
                },
            },
            {
                "window": validation_window,
                "result": {
                    "return": -0.4,
                    "max_drawdown": 2.1,
                    "trades": 0,
                    "fee_drag_pct": 0.1,
                    "liquidations": 0,
                    "trend_capture_points": validation_points,
                    "strategy_funnel": {
                        "long": {"sideways_pass": 10, "outer_context_pass": 6, "path_pass": 4, "final_veto_pass": 0},
                        "short": {"sideways_pass": 10, "outer_context_pass": 3, "path_pass": 1, "final_veto_pass": 0},
                    },
                    "filled_side_entries": {"long": 0, "short": 0},
                },
            },
        ]
        validation_continuous_result = {
            "return": -0.4,
            "max_drawdown": 2.1,
            "trades": 0,
            "fee_drag_pct": 0.1,
            "liquidations": 0,
            "trend_capture_points": validation_points,
            "strategy_funnel": {
                "long": {"sideways_pass": 10, "outer_context_pass": 6, "path_pass": 4, "final_veto_pass": 0},
                "short": {"sideways_pass": 10, "outer_context_pass": 3, "path_pass": 1, "final_veto_pass": 0},
            },
            "filled_side_entries": {"long": 0, "short": 0},
        }
        full_period_result = {
            "return": 2.8,
            "max_drawdown": 3.2,
            "trades": 0,
            "fee_drag_pct": 0.4,
            "liquidations": 0,
            "trend_capture_points": eval_points + validation_points,
            "strategy_funnel": {
                "long": {"sideways_pass": 28, "outer_context_pass": 16, "path_pass": 12, "final_veto_pass": 0},
                "short": {"sideways_pass": 28, "outer_context_pass": 6, "path_pass": 2, "final_veto_pass": 0},
            },
            "filled_side_entries": {"long": 0, "short": 0},
        }

        report = summarize_evaluation(
            results,
            make_gate_config(),
            validation_continuous_result=validation_continuous_result,
            full_period_result=full_period_result,
        )

        self.assertIn("train滚动漏斗(long)", report.summary_text)
        self.assertIn("val连续漏斗(short)", report.summary_text)
        self.assertIn("低活动度信号（软触发，不是硬 gate）", report.summary_text)
        self.assertIn("下一轮优先做放宽/删减/合并类假设", report.summary_text)
        self.assertIn("低活动度软触发=", report.prompt_summary_text)
        self.assertEqual(report.metrics["validation_long_path_pass"], 4.0)
        self.assertEqual(report.metrics["selection_long_final_veto_pass"], 0.0)
        self.assertGreater(report.metrics["low_activity_signal_count"], 0.0)

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
    def _minimal_validation_source(self, overrides=None):
        body_by_function = {
            "_sideways_release_flags": "return {}",
            "_is_sideways_regime": "return False",
            "_flow_signal_metrics": "return {}",
            "_flow_confirmation_ok": "return True",
            "_flow_entry_ok": "return True",
            "_trend_quality_long": "return True",
            "_trend_quality_short": "return True",
            "_trend_quality_ok": "return True",
            "_trend_followthrough_long": "return True",
            "_trend_followthrough_short": "return True",
            "_trend_followthrough_ok": "return True",
            "_long_entry_signal": "return False",
            "_short_entry_signal": "return False",
            "strategy": "return None",
        }
        if overrides:
            body_by_function.update(overrides)

        blocks = [
            "# PARAMS_START",
            "PARAMS = {'intraday_adx_min': 10}",
            "# PARAMS_END",
            "",
        ]
        for function_name in REQUIRED_FUNCTIONS:
            signature = "(*args, **kwargs)"
            if function_name == "_flow_confirmation_ok":
                signature = "(market_state, hourly, fourh, params, side)"
            elif function_name == "_trend_quality_ok":
                signature = "(market_state, side)"
            elif function_name == "_trend_followthrough_ok":
                signature = "(market_state, side, trigger_price, current_close)"
            elif function_name == "strategy":
                signature = "(data, idx, positions, market_state)"
            blocks.append(f"def {function_name}{signature}:")
            blocks.append(f"    {body_by_function[function_name]}")
            blocks.append("")
        return "\n".join(blocks)

    def test_live_strategy_source_validates(self):
        source = (REPO_ROOT / "src/strategy_macd_aggressive.py").read_text()

        validate_strategy_source(source)

    def test_backup_strategy_source_validates(self):
        source = (REPO_ROOT / "backups/strategy_macd_aggressive_v2_best.py").read_text()

        validate_strategy_source(source)

    def test_is_sideways_regime_handles_bull_front_run_path(self):
        market_state = {
            "ema_fast": 101.5,
            "ema_slow": 100.0,
            "prev_ema_slow": 99.8,
            "hourly": {"trend_spread_pct": 0.01, "adx": 18.0},
            "four_hour": {"trend_spread_pct": 0.01, "adx": 16.0},
            "atr_ratio": 0.01,
        }
        release_flags = {
            "intraday_spread": 0.02,
            "hourly_spread": 0.01,
            "fourh_spread": 0.01,
            "hourly_slope": 0.0002,
            "fourh_slope": 0.0001,
            "intraday_chop": 45.0,
            "hourly_chop": 54.0,
            "atr_ratio": 0.01,
            "adx_soft": False,
            "aligned_trend": True,
            "convexity_release": False,
            "trend_awakening": False,
            "fresh_directional_expansion": False,
            "exhausted_drift": False,
            "hard_sideways": False,
        }

        with mock.patch.object(strategy_module, "_sideways_release_flags", return_value=release_flags):
            result = strategy_module._is_sideways_regime(market_state)

        self.assertIsInstance(result, bool)

    def test_strategy_funnel_diagnostics_track_long_and_short_gate_passes(self):
        strategy_module.reset_funnel_diagnostics()

        with mock.patch.object(strategy_module, "_build_signal_context", return_value={"ready": True}):
            with mock.patch.object(strategy_module, "_is_sideways_regime", return_value=False):
                with mock.patch.object(strategy_module, "long_outer_context_ok", return_value=True):
                    with mock.patch.object(strategy_module, "long_breakout_ok", return_value=True):
                        with mock.patch.object(strategy_module, "long_pullback_ok", return_value=False):
                            with mock.patch.object(strategy_module, "long_trend_reaccel_ok", return_value=False):
                                with mock.patch.object(strategy_module, "long_signal_path_ok", return_value=True):
                                    with mock.patch.object(strategy_module, "long_final_veto_clear", return_value=False):
                                        with mock.patch.object(strategy_module, "short_outer_context_ok", return_value=True):
                                            with mock.patch.object(strategy_module, "short_breakdown_ok", return_value=True):
                                                with mock.patch.object(strategy_module, "short_bounce_fail_ok", return_value=False):
                                                    with mock.patch.object(strategy_module, "short_trend_reaccel_ok", return_value=False):
                                                        with mock.patch.object(strategy_module, "short_final_veto_clear", return_value=True):
                                                            signal = strategy_module.strategy(
                                                                [{}] * (strategy_module.PARAMS["min_history"] + 1),
                                                                strategy_module.PARAMS["min_history"],
                                                                [],
                                                                {},
                                                            )

        funnel = strategy_module.get_funnel_diagnostics()
        self.assertEqual(signal, "short_breakdown")
        self.assertEqual(
            funnel["long"],
            {
                "sideways_pass": 1,
                "outer_context_pass": 1,
                "path_pass": 1,
                "final_veto_pass": 0,
            },
        )
        self.assertEqual(
            funnel["short"],
            {
                "sideways_pass": 1,
                "outer_context_pass": 1,
                "path_pass": 1,
                "final_veto_pass": 1,
            },
        )

    def test_validate_strategy_source_accepts_new_flow_params(self):
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
    'breakout_rsi_min': 40,
    'breakout_rsi_max': 60,
    'breakdown_rsi_min': 20,
    'breakdown_rsi_max': 60,
    'breakout_volume_ratio_min': 1.0,
    'breakdown_volume_ratio_min': 1.0,
    'breakout_trade_count_ratio_min': 1.0,
    'breakdown_trade_count_ratio_min': 1.0,
    'breakout_taker_buy_ratio_min': 0.55,
    'breakdown_taker_sell_ratio_min': 0.55,
    'breakout_flow_imbalance_min': 0.05,
    'breakdown_flow_imbalance_max': -0.05,
    'hourly_flow_confirmation_min': 0.02,
    'fourh_flow_confirmation_min': 0.02,
    'breakout_body_ratio_min': 0.3,
    'breakdown_body_ratio_min': 0.3,
    'breakout_close_pos_min': 0.5,
    'breakdown_close_pos_max': 0.5,
    'intraday_ema_fast': 9,
    'intraday_ema_slow': 20,
    'hourly_ema_fast': 10,
    'hourly_ema_slow': 20,
    'fourh_ema_fast': 10,
    'fourh_ema_slow': 20,
    'macd_fast': 12,
    'macd_slow': 26,
    'macd_signal': 9,
    'volume_lookback': 10,
    'flow_lookback': 12,
}
# PARAMS_END

def _is_sideways_regime(*args, **kwargs):
    return False

def _sideways_release_flags(*args, **kwargs):
    return {}

def _flow_signal_metrics(*args, **kwargs):
    return {}

def _flow_confirmation_ok(*args, **kwargs):
    return True

def _flow_entry_ok(*args, **kwargs):
    return True

def _trend_quality_long(*args, **kwargs):
    return True

def _trend_quality_short(*args, **kwargs):
    return True

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_long(*args, **kwargs):
    return True

def _trend_followthrough_short(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def _long_entry_signal(*args, **kwargs):
    return False

def _short_entry_signal(*args, **kwargs):
    return False

def strategy(*args, **kwargs):
    return None
"""
        validate_strategy_source(source)

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

def _sideways_release_flags(*args, **kwargs):
    return {}

def _flow_signal_metrics(*args, **kwargs):
    return {}

def _flow_confirmation_ok(*args, **kwargs):
    return True

def _flow_entry_ok(*args, **kwargs):
    return True

def _trend_quality_long(*args, **kwargs):
    return True

def _trend_quality_short(*args, **kwargs):
    return True

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_long(*args, **kwargs):
    return True

def _trend_followthrough_short(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def _long_entry_signal(*args, **kwargs):
    return False

def _short_entry_signal(*args, **kwargs):
    return False

def strategy(*args, **kwargs):
    return None
"""
        with self.assertRaises(StrategySourceError):
            validate_strategy_source(source)

    def test_validate_strategy_source_rejects_helper_p_without_binding(self):
        source = self._minimal_validation_source(
            {"_trend_quality_ok": "return p['intraday_adx_min'] > 0"}
        )

        with self.assertRaisesRegex(StrategySourceError, r"references undefined name 'p'"):
            validate_strategy_source(source)

    def test_validate_strategy_source_rejects_helper_params_without_binding(self):
        source = self._minimal_validation_source(
            {"_trend_quality_ok": "return params['intraday_adx_min'] > 0"}
        )

        with self.assertRaisesRegex(StrategySourceError, r"references undefined name 'params'"):
            validate_strategy_source(source)

    def test_validate_strategy_source_rejects_helper_intraday_without_binding(self):
        source = self._minimal_validation_source(
            {"_trend_followthrough_ok": "return intraday['spread_pct'] > 0"}
        )

        with self.assertRaisesRegex(StrategySourceError, r"references undefined name 'intraday'"):
            validate_strategy_source(source)

    def test_validate_strategy_source_accepts_helper_params_argument(self):
        source = self._minimal_validation_source(
            {"_flow_confirmation_ok": "return params['intraday_adx_min'] > 0"}
        )

        validate_strategy_source(source)

    def test_validate_strategy_source_accepts_helper_local_p_binding(self):
        source = self._minimal_validation_source(
            {
                "_trend_quality_ok": "p = PARAMS\n    return p['intraday_adx_min'] > 0",
            }
        )

        validate_strategy_source(source)

    def test_validate_strategy_source_allows_new_param_key_in_default_mode(self):
        base_source = self._minimal_validation_source()
        source = base_source.replace(
            "PARAMS = {'intraday_adx_min': 10}",
            "PARAMS = {'intraday_adx_min': 10, 'new_factor_gate': 1.0}",
            1,
        )

        validate_strategy_source(
            source,
            base_source=base_source,
            factor_change_mode="default",
        )

    def test_validate_strategy_source_allows_new_param_key_in_factor_admission_mode(self):
        base_source = self._minimal_validation_source()
        source = base_source.replace(
            "PARAMS = {'intraday_adx_min': 10}",
            "PARAMS = {'intraday_adx_min': 10, 'new_factor_gate': 1.0}",
            1,
        )

        validate_strategy_source(
            source,
            base_source=base_source,
            factor_change_mode="factor_admission",
        )

    def test_validate_strategy_source_keeps_default_mode_complexity_growth_as_warning_only(self):
        base_source = self._minimal_validation_source()
        expanded_body = "\n    ".join(
            [*(f"x_{index} = {index}" for index in range(48)), "return None"]
        )
        source = self._minimal_validation_source({"strategy": expanded_body})

        validate_strategy_source(
            source,
            base_source=base_source,
            factor_change_mode="default",
        )
        pressure = build_strategy_complexity_pressure(source, base_source=base_source)

        self.assertEqual(pressure["growth_level"], "warning_2")

    def test_build_strategy_complexity_delta_tracks_growth(self):
        base_source = self._minimal_validation_source()
        source = self._minimal_validation_source(
            {"strategy": "value = 1\n    other = 2\n    return value + other"}
        )

        complexity_delta = build_strategy_complexity_delta(base_source, source)

        self.assertIn("strategy", complexity_delta["functions"])
        self.assertIn("strategy:", complexity_delta["summary"])

    def test_format_strategy_complexity_headroom_emits_family_and_function_rows(self):
        source = Path(strategy_module.__file__).read_text(encoding="utf-8")

        summary = format_strategy_complexity_headroom(source, limit=2)

        self.assertIn("当前基底复杂度状态", summary)
        self.assertIn("当前基底复杂度余量", summary)
        self.assertIn("family", summary)
        self.assertIn("function", summary)

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

    def test_repair_missing_required_functions_restores_deleted_helpers_from_base(self):
        base_source = """
# PARAMS_START
PARAMS = {'intraday_adx_min': 10}
# PARAMS_END

def helper():
    return 1

def _sideways_release_flags(*args, **kwargs):
    return {}

def _is_sideways_regime(*args, **kwargs):
    return False

def _flow_signal_metrics(*args, **kwargs):
    return {'bias': 0.0}

def _flow_confirmation_ok(*args, **kwargs):
    return True

def _flow_entry_ok(*args, **kwargs):
    return True

def _trend_quality_long(*args, **kwargs):
    return True

def _trend_quality_short(*args, **kwargs):
    return True

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_long(*args, **kwargs):
    return True

def _trend_followthrough_short(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def _long_entry_signal(*args, **kwargs):
    return False

def _short_entry_signal(*args, **kwargs):
    return False

def strategy(*args, **kwargs):
    return helper()
"""
        candidate_source = """
# PARAMS_START
PARAMS = {'intraday_adx_min': 12}
# PARAMS_END

def helper():
    return 1

def _is_sideways_regime(*args, **kwargs):
    return False

def _flow_confirmation_ok(*args, **kwargs):
    return True

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def strategy(*args, **kwargs):
    return None
"""
        repaired_source, restored_functions = repair_missing_required_functions(
            base_source,
            candidate_source,
            EDITABLE_REGIONS,
        )

        self.assertIn("_sideways_release_flags", restored_functions)
        self.assertIn("_long_entry_signal", restored_functions)
        validate_strategy_source(repaired_source)
        validate_editable_region_boundaries(base_source, repaired_source, EDITABLE_REGIONS)
        self.assertIn("PARAMS = {'intraday_adx_min': 12}", repaired_source)
        self.assertIn("def strategy(*args, **kwargs):\n    return None", repaired_source)


class JournalPromptFixesTest(unittest.TestCase):
    def _minimal_strategy_source(self):
        body_by_function = {
            "_sideways_release_flags": "return {}",
            "_is_sideways_regime": "return False",
            "_flow_signal_metrics": "return {}",
            "_flow_confirmation_ok": "return True",
            "_flow_entry_ok": "return True",
            "_trend_quality_long": "return True",
            "_trend_quality_short": "return True",
            "_trend_quality_ok": "return True",
            "_trend_followthrough_long": "return True",
            "_trend_followthrough_short": "return True",
            "_trend_followthrough_ok": "return True",
            "_long_entry_signal": "return None",
            "_short_entry_signal": "return None",
            "strategy": "return None",
        }
        blocks = [
            "# PARAMS_START",
            "PARAMS = {'breakout_volume_ratio_min': 1.0}",
            "# PARAMS_END",
            "",
        ]
        for function_name in REQUIRED_FUNCTIONS:
            blocks.append(f"def {function_name}(*args, **kwargs):")
            blocks.append(f"    {body_by_function[function_name]}")
            blocks.append("")
        return "\n".join(blocks)

    def test_build_strategy_agents_instructions_mentions_15m_single_source_and_flow(self):
        prompt = build_strategy_agents_instructions()

        self.assertIn("15m` 是唯一事实源", prompt)
        self.assertIn("方向流量代理", prompt)
        self.assertIn("config/research_v2_operator_focus.md", prompt)
        self.assertIn("wiki/duplicate_watchlist.md", prompt)
        self.assertIn("wiki/failure_wiki.md", prompt)
        self.assertIn("先想再写", prompt)
        self.assertIn("不要 hard code", prompt)
        self.assertIn("复杂度信息只做只读诊断", prompt)

    def test_build_strategy_runtime_prompt_mentions_promotion_gate(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
        )

        self.assertIn("promotion_delta > 0.02", prompt)
        self.assertIn("当前回合任务", prompt)
        self.assertIn("本轮阅读顺序（必须执行）", prompt)
        self.assertIn("duplicate_watchlist.md", prompt)
        self.assertIn("failure_wiki.md", prompt)
        self.assertIn("先复盘最近一条最强结构化失败证据", prompt)
        self.assertIn("继续还是转向", prompt)
        self.assertIn("形成假设后，必须回看一次", prompt)
        self.assertNotIn("当前因子模式", prompt)
        self.assertIn("本轮硬完成条件", prompt)
        self.assertIn("round brief", prompt)
        self.assertIn("change_plan", prompt)
        self.assertIn("novelty_proof", prompt)

    def test_build_strategy_runtime_prompt_can_include_operator_focus(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            operator_focus_text="- 优先检查多头外层 choke point\n- 降权同义 gate 堆叠",
            operator_focus_path="config/research_v2_operator_focus.md",
        )

        self.assertIn("人工方向卡（软引导，不是硬限制", prompt)
        self.assertIn("config/research_v2_operator_focus.md", prompt)
        self.assertIn("优先检查多头外层 choke point", prompt)

    def test_build_strategy_agents_instructions_mentions_all_required_symbols(self):
        prompt = build_strategy_agents_instructions()

        for function_name in REQUIRED_FUNCTIONS:
            self.assertIn(f"`{function_name}()`", prompt)

    def test_build_strategy_agents_instructions_mentions_ordinary_family_budget(self):
        prompt = build_strategy_agents_instructions()

        self.assertIn("`strategy()` 与 `PARAMS` 属于特殊区域", prompt)
        for family_name in ORDINARY_REGION_FAMILIES:
            self.assertIn(f"`{family_name}`", prompt)
        self.assertIn("普通 family", prompt)

    def test_build_strategy_agents_instructions_mentions_delete_first_rule(self):
        prompt = build_strategy_agents_instructions()

        self.assertIn("默认先做删减、合并、替换", prompt)
        self.assertIn("不要继续叠分叉", prompt)
        self.assertIn("不要“堆屎”", prompt)
        self.assertIn("不要换个名字再写一份重复条件", prompt)

    def test_build_strategy_system_prompt_mentions_agents_md_and_text_contract(self):
        prompt = build_strategy_system_prompt()

        self.assertIn("AGENTS.md", prompt)
        self.assertIn("纯文本候选摘要", prompt)
        self.assertIn("未读到文件", prompt)

    def test_build_candidate_response_format_instructions_mentions_system_derived_regions(self):
        prompt = build_candidate_response_format_instructions()

        self.assertIn("不要输出 `edited_regions`", prompt)
        self.assertIn("真实 diff", prompt)

    def test_build_edit_completion_instructions_mentions_hash_gate_and_edit_done(self):
        prompt = build_edit_completion_instructions()

        self.assertIn("EDIT_DONE", prompt)
        self.assertIn("文件 hash 未变化", prompt)
        self.assertIn("主进程直接丢弃", prompt)

    def test_build_strategy_no_edit_repair_prompt_mentions_discarded_reply(self):
        prompt = build_strategy_no_edit_repair_prompt(
            no_edit_attempt=1,
            error_message="candidate missing actual changed regions",
            last_response_text="我建议放宽 outer_context",
        )

        self.assertIn("已被主进程直接丢弃", prompt)
        self.assertIn("文件 hash 与调用前完全相同", prompt)
        self.assertIn("只回复 `EDIT_DONE`", prompt)

    def test_build_strategy_edit_worker_prompt_mentions_round_brief_and_edit_only(self):
        prompt = build_strategy_edit_worker_prompt(
            candidate_id="candidate_1",
            hypothesis="放宽多头 outer_context，让内层路径真正触达到出单层。",
            change_plan="在 long_outer_context_ok 和 long_final_veto_clear 上删旧 gate，不新增平行 path。",
            change_tags=("widen_outer_context", "merge_veto"),
            expected_effects=("提高多头到来与陪跑捕获",),
            closest_failed_cluster="participation_cluster",
            novelty_proof="这次直接改最终可达性，不再停留在内层 helper 微调。",
            current_complexity_headroom_text="当前基底复杂度余量：trend_quality_family bool_ops 剩 4",
        )

        self.assertIn("round brief", prompt)
        self.assertIn("只修改 `src/strategy_macd_aggressive.py`", prompt)
        self.assertIn("只回复 `EDIT_DONE`", prompt)
        self.assertIn("复杂度诊断（只读）", prompt)

    def test_build_strategy_edit_worker_prompt_mentions_compaction_task_when_requested(self):
        prompt = build_strategy_edit_worker_prompt(
            candidate_id="candidate_1",
            hypothesis="压缩多头最终 veto",
            change_plan="合并重复否决，不新增 path",
            change_tags=("merge_veto",),
            expected_effects=("回收复杂度余量",),
            closest_failed_cluster="veto_cluster",
            novelty_proof="本轮只做结构性压缩。",
            iteration_lane="compaction",
            iteration_lane_status_text="当前 working_base 已到 warning_2，先压缩再研究。",
        )

        self.assertIn("本轮任务类型：`compaction`", prompt)
        self.assertIn("当前 working_base 已到 warning_2", prompt)

    def test_build_strategy_summary_worker_system_prompt_mentions_no_edit_summary_role(self):
        prompt = build_strategy_summary_worker_system_prompt()

        self.assertIn("summary_worker", prompt)
        self.assertIn("不要修改任何文件", prompt)
        self.assertIn("回写最终候选摘要", prompt)

    def test_build_strategy_candidate_summary_prompt_mentions_final_code_alignment(self):
        prompt = build_strategy_candidate_summary_prompt(
            candidate_id="candidate_1",
            hypothesis="原始多头补法",
            change_plan="原始计划",
            change_tags=("long", "merge_veto"),
            expected_effects=("提高多头到来",),
            closest_failed_cluster="ownership_cluster",
            novelty_proof="原始 novelty",
            edited_regions=("strategy", "long_final_veto_clear"),
            region_families=("entry_path", "strategy"),
            diff_summary=("- strategy: 放宽最终路由",),
        )

        self.assertIn("最终落地代码回写候选元信息", prompt)
        self.assertIn("如果 worker / repair 实际落码偏离了原 brief", prompt)
        self.assertIn("不要输出 `edited_regions`", prompt)

    def test_build_strategy_worker_system_prompt_mentions_short_lived_worker_role(self):
        prompt = build_strategy_worker_system_prompt(worker_kind="repair_worker")

        self.assertIn("短生命周期 `repair_worker`", prompt)
        self.assertIn("不要重新做全量历史研究", prompt)
        self.assertIn("只根据当前提示里的 round brief 或 repair 指令", prompt)
        self.assertIn("完成编辑后只回复 `EDIT_DONE`", prompt)

    def test_build_strategy_round_brief_repair_prompt_mentions_required_fields(self):
        prompt = build_strategy_round_brief_repair_prompt(
            retry_attempt=1,
            invalid_reason="planner round brief missing required fields: novelty_proof, change_tags",
            missing_fields=("novelty_proof", "change_tags"),
            raw_response_excerpt="我觉得应该继续优化多头，但先不写字段。",
        )

        self.assertIn("同轮补正", prompt)
        self.assertIn("novelty_proof", prompt)
        self.assertIn("change_tags", prompt)
        self.assertIn("不要输出随笔", prompt)
        self.assertIn("纯文本候选摘要", prompt)

    def test_build_strategy_prompts_include_precise_complexity_budgets(self):
        system_prompt = build_strategy_agents_instructions()
        runtime_prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
        )

        self.assertNotIn("复杂度硬上限（超了直接拒收）", runtime_prompt)
        self.assertNotIn("复杂度硬上限（超了直接拒收）", system_prompt)
        self.assertIn("复杂度信息只做只读诊断", system_prompt)
        self.assertIn("删旧、并旧、改旧", system_prompt)

    def test_build_strategy_research_prompt_can_include_current_complexity_headroom(self):
        headroom_text = "当前基底复杂度余量（剩余越小越容易再次撞复杂度）:\n- family `trend_quality_family`: lines 剩 8, bool_ops 剩 0, ifs 剩 3"
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            current_complexity_headroom_text=headroom_text,
        )

        self.assertIn("当前基底复杂度余量", prompt)
        self.assertIn("trend_quality_family", prompt)

    def test_build_strategy_research_prompt_can_still_echo_iteration_lane_when_provided(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            benchmark_label="champion",
            current_base_role="working_base",
            iteration_lane="compaction",
            iteration_lane_status_text="当前基底复杂度 warning_2，允许只更新 working_base。",
        )

        self.assertIn("当前工作基底角色：`working_base`", prompt)
        self.assertIn("本轮任务类型：`compaction`", prompt)
        self.assertNotIn("working_base_compaction", prompt)
        self.assertNotIn("满足则更新 working_base", prompt)

    def test_build_strategy_runtime_repair_prompt_mentions_complexity_shrink_rule(self):
        prompt = build_strategy_runtime_repair_prompt(
            candidate_id="candidate_1",
            hypothesis="继续优化多头上车",
            change_plan="调整 ownership 方向过滤",
            change_tags=("ownership_takeover",),
            edited_regions=("strategy",),
            expected_effects=("提高多头到来阶段捕获",),
            closest_failed_cluster="ownership_cluster",
            novelty_proof="本次修复仅修正运行错误，不改变主假设。",
            error_message="complexity family budget exceeded: trend_quality_family.lines=145 > 130",
            repair_attempt=1,
        )

        self.assertIn("代码过胖或结构失控", prompt)
        self.assertIn("不要把复杂度从一个函数搬到同 family 的别的 helper", prompt)

    def test_build_strategy_exploration_repair_prompt_mentions_blocked_cluster(self):
        prompt = build_strategy_exploration_repair_prompt(
            candidate_id="candidate_1",
            hypothesis="继续优化多头上车",
            change_plan="调整 ownership 方向过滤",
            change_tags=("ownership_takeover", "acceptance_continuity"),
            edited_regions=("strategy",),
            expected_effects=("提高多头到来阶段捕获",),
            closest_failed_cluster="ownership_cluster",
            novelty_proof="和最近失败方向相比，这次会换交易路径。",
            block_kind="same_cluster",
            blocked_cluster="ownership_cluster",
            blocked_reason="同簇低变化近邻",
            locked_clusters=("ownership_cluster(剩余3轮)",),
            regeneration_attempt=1,
        )

        self.assertIn("blocked_cluster: ownership_cluster", prompt)
        self.assertIn("近期高频失败/过热方向（软提示）", prompt)
        self.assertIn("ownership_cluster(剩余3轮)", prompt)
        self.assertIn("必须绕开刚被验证失败的近邻方向", prompt)

    def test_build_strategy_exploration_repair_prompt_mentions_feedback_note(self):
        prompt = build_strategy_exploration_repair_prompt(
            candidate_id="candidate_1",
            hypothesis="继续优化多头上车",
            change_plan="调整 ownership 方向过滤",
            change_tags=("ownership_takeover", "acceptance_continuity"),
            edited_regions=("strategy",),
            expected_effects=("提高多头到来阶段捕获",),
            closest_failed_cluster="ownership_cluster",
            novelty_proof="和最近失败方向相比，这次会换交易路径。",
            block_kind="behavioral_noop",
            blocked_cluster="ownership_cluster",
            blocked_reason="smoke 行为指纹未变化",
            locked_clusters=(),
            regeneration_attempt=1,
            feedback_note="- smoke窗口: train1, val1, train14\n- 最近连续 behavioral_noop: 2",
        )

        self.assertIn("附加反馈（本次必须处理）", prompt)
        self.assertIn("最近连续 behavioral_noop: 2", prompt)
        self.assertIn("先根据 block 原因和附加反馈复盘上一版为什么失败", prompt)
        self.assertIn("wiki/duplicate_watchlist.md", prompt)
        self.assertIn("wiki/failure_wiki.md", prompt)

    def test_build_strategy_exploration_repair_prompt_requires_rebase_to_reference(self):
        prompt = build_strategy_exploration_repair_prompt(
            candidate_id="candidate_1",
            hypothesis="继续优化多头上车",
            change_plan="调整 ownership 方向过滤",
            change_tags=("ownership_takeover",),
            edited_regions=("strategy",),
            expected_effects=("提高多头到来阶段捕获",),
            closest_failed_cluster="ownership_cluster",
            novelty_proof="和最近失败方向相比，这次会换交易路径。",
            block_kind="blocked_failure_wiki_exact_cut",
            blocked_cluster="ownership_cluster",
            blocked_reason="同一 exact cut 已至少 2 次落回失败盆地",
            locked_clusters=(),
            regeneration_attempt=1,
        )

        self.assertIn("重置为当前正确基底", prompt)
        self.assertIn("不再是这次改动的基底", prompt)
        self.assertIn("从当前正确基底重新修改", prompt)
        self.assertIn("wiki/last_rejected_snapshot.md", prompt)
        self.assertIn("wiki/last_rejected_candidate.py", prompt)

    def test_strategy_prompts_forbid_blocked_no_edit_placeholder_results(self):
        runtime_prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
        )
        agents_prompt = build_strategy_agents_instructions()
        exploration_prompt = build_strategy_exploration_repair_prompt(
            candidate_id="candidate_1",
            hypothesis="继续优化多头上车",
            change_plan="调整 ownership 方向过滤",
            change_tags=("ownership_takeover", "acceptance_continuity"),
            edited_regions=("strategy",),
            expected_effects=("提高多头到来阶段捕获",),
            closest_failed_cluster="ownership_cluster",
            novelty_proof="和最近失败方向相比，这次会换交易路径。",
            block_kind="invalid_generation",
            blocked_cluster="ownership_cluster",
            blocked_reason="候选未产生真实代码改动",
            locked_clusters=(),
            regeneration_attempt=1,
        )

        for prompt in (runtime_prompt, agents_prompt, exploration_prompt):
            self.assertIn("no_edit", prompt)
            self.assertIn("未执行代码改动", prompt)

    def test_region_family_mapping_merges_entry_path_blocks(self):
        region_families = region_families_for_regions(
            ("_trend_followthrough_long", "_short_entry_signal", "_flow_entry_ok", "strategy", "PARAMS")
        )

        self.assertEqual(region_families, ("entry_path", "flow", "strategy", "params"))
        self.assertEqual(ordinary_region_families(region_families), ("entry_path", "flow"))

    def test_candidate_from_payload_allows_strategy_only_when_source_really_changes(self):
        base_source = self._minimal_strategy_source()
        candidate_source = base_source.replace(
            "def strategy(*args, **kwargs):\n    return None\n",
            "def strategy(*args, **kwargs):\n    return 1\n",
            1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_strategy_file = Path(temp_dir) / "src/strategy_macd_aggressive.py"
            workspace_strategy_file.parent.mkdir(parents=True, exist_ok=True)
            workspace_strategy_file.write_text(candidate_source)

            payload = {
                "candidate_id": "candidate_only_strategy",
                "hypothesis": "只改最终入场",
                "change_plan": "调整 strategy",
                "closest_failed_cluster": "ownership_cluster",
                "novelty_proof": "这次会改变交易路径。",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "expected_effects": ["提高多头上车"],
                "core_factors": [],
            }

            candidate = research_script._candidate_from_payload(
                payload,
                workspace_strategy_file=workspace_strategy_file,
                base_source=base_source,
            )
            self.assertEqual(candidate.candidate_id, "candidate_only_strategy")

    def test_candidate_from_payload_allows_four_ordinary_families(self):
        base_source = self._minimal_strategy_source()
        candidate_source = base_source
        candidate_source = candidate_source.replace(
            "def _is_sideways_regime(*args, **kwargs):\n    return False\n",
            "def _is_sideways_regime(*args, **kwargs):\n    return True\n",
            1,
        )
        candidate_source = candidate_source.replace(
            "def _flow_confirmation_ok(*args, **kwargs):\n    return True\n",
            "def _flow_confirmation_ok(*args, **kwargs):\n    return False\n",
            1,
        )
        candidate_source = candidate_source.replace(
            "def _trend_quality_ok(*args, **kwargs):\n    return True\n",
            "def _trend_quality_ok(*args, **kwargs):\n    return False\n",
            1,
        )
        candidate_source = candidate_source.replace(
            "def _long_entry_signal(*args, **kwargs):\n    return None\n",
            "def _long_entry_signal(*args, **kwargs):\n    return True\n",
            1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_strategy_file = Path(temp_dir) / "src/strategy_macd_aggressive.py"
            workspace_strategy_file.parent.mkdir(parents=True, exist_ok=True)
            workspace_strategy_file.write_text(candidate_source)

            payload = {
                "candidate_id": "candidate_four_families",
                "hypothesis": "一次覆盖四个普通 family",
                "change_plan": "同时调整横盘、流量、质量和入场路径",
                "closest_failed_cluster": "ownership_cluster",
                "novelty_proof": "这次会改变交易路径。",
                "change_tags": ["ownership_takeover"],
                "edited_regions": [
                    "_is_sideways_regime",
                    "_flow_confirmation_ok",
                    "_trend_quality_ok",
                    "_long_entry_signal",
                ],
                "expected_effects": ["提高多头上车"],
                "core_factors": [],
            }

            candidate = research_script._candidate_from_payload(
                payload,
                workspace_strategy_file=workspace_strategy_file,
                base_source=base_source,
            )
            self.assertEqual(candidate.candidate_id, "candidate_four_families")

    def test_parse_model_candidate_payload_accepts_text_contract(self):
        payload = research_script._parse_model_candidate_payload(
            """
candidate_id: candidate_text_mode
hypothesis: 先放宽 long 最后 veto，让多头真实触达最终路由
change_plan: 调整 long_final_veto_clear，并同步检查 strategy 最终合流
change_tags: long, merge_veto, strategy
expected_effects: 增加多头实际入场 || 减少长侧死分支
closest_failed_cluster: ownership_cluster
novelty_proof: 这次改最终放行链，不再停在未触达的 helper 阈值
core_factors: veto_pressure | 当前长侧最后一层否决过重 | long_final_veto_clear 常年卡死
"""
        )

        self.assertEqual(payload["candidate_id"], "candidate_text_mode")
        self.assertEqual(payload["change_tags"], ["long", "merge_veto", "strategy"])
        self.assertEqual(len(payload["expected_effects"]), 2)
        self.assertEqual(payload["core_factors"][0]["name"], "veto_pressure")
        self.assertIn("__raw_text__", payload)

    def test_parse_model_candidate_payload_without_field_contract_no_longer_auto_fills_brief(self):
        payload = research_script._parse_model_candidate_payload(
            "我觉得应该继续优化多头 outer_context，但先不按字段格式返回。"
        )

        self.assertEqual(payload["hypothesis"], "")
        self.assertEqual(payload["change_plan"], "")
        self.assertEqual(payload["change_tags"], [])
        self.assertEqual(payload["novelty_proof"], "")

    def test_validate_round_brief_payload_rejects_missing_core_fields(self):
        payload = research_script._parse_model_candidate_payload(
            """
candidate_id: candidate_bad
hypothesis: 继续优化多头
change_plan: 放宽 long_outer_context_ok
expected_effects: 提高多头上车
"""
        )

        with self.assertRaisesRegex(StrategySourceError, "novelty_proof, change_tags"):
            research_script._validate_round_brief_payload(payload)

    def test_round_brief_from_payload_rejects_missing_core_fields(self):
        payload = research_script._parse_model_candidate_payload(
            """
candidate_id: candidate_bad
hypothesis: 继续优化多头
change_plan: 放宽 long_outer_context_ok
"""
        )

        with self.assertRaisesRegex(StrategySourceError, "novelty_proof, change_tags"):
            research_script._round_brief_from_payload(payload)

    def test_candidate_from_payload_uses_system_changed_regions_not_declared_regions(self):
        base_source = self._minimal_strategy_source()
        candidate_source = base_source.replace(
            "def strategy(*args, **kwargs):\n    return None\n",
            "def strategy(*args, **kwargs):\n    return 1\n",
            1,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_strategy_file = Path(temp_dir) / "src/strategy_macd_aggressive.py"
            workspace_strategy_file.parent.mkdir(parents=True, exist_ok=True)
            workspace_strategy_file.write_text(candidate_source)

            candidate = research_script._candidate_from_payload(
                {
                    "candidate_id": "candidate_real_diff",
                    "hypothesis": "改最终路由",
                    "change_plan": "只动 strategy",
                    "closest_failed_cluster": "ownership_cluster",
                    "novelty_proof": "真实 diff 会触达 strategy。",
                    "change_tags": ["route_shift"],
                    "edited_regions": ["_trend_quality_ok"],
                    "expected_effects": ["改变最终入场"],
                    "core_factors": [],
                },
                workspace_strategy_file=workspace_strategy_file,
                base_source=base_source,
            )

            self.assertEqual(candidate.edited_regions, ("strategy",))

    def test_rebase_candidate_metadata_to_final_code_prefers_summary_worker_output(self):
        base_source = self._minimal_strategy_source()
        candidate_source = base_source.replace(
            "def strategy(*args, **kwargs):\n    return None\n",
            "def strategy(*args, **kwargs):\n    return 1\n",
            1,
        )
        candidate = StrategyCandidate(
            candidate_id="candidate_realigned",
            hypothesis="原 brief 假设",
            change_plan="原 brief 计划",
            closest_failed_cluster="ownership_cluster",
            novelty_proof="原 brief novelty",
            change_tags=("long",),
            edited_regions=("strategy",),
            expected_effects=("原 effect",),
            core_factors=tuple(),
            strategy_code=candidate_source,
        )
        round_brief = research_script.StrategyRoundBrief(
            candidate_id="candidate_realigned",
            hypothesis="原 brief 假设",
            change_plan="原 brief 计划",
            closest_failed_cluster="ownership_cluster",
            novelty_proof="原 brief novelty",
            change_tags=("long",),
            expected_effects=("原 effect",),
            core_factors=tuple(),
        )
        summary_brief = research_script.StrategyRoundBrief(
            candidate_id="candidate_should_not_override",
            hypothesis="最终代码实际是在改 strategy 最终路由",
            change_plan="按最终 diff 重写 strategy 路由说明",
            closest_failed_cluster="route_shift_cluster",
            novelty_proof="这是按最终代码回写后的 novelty",
            change_tags=("strategy", "route_shift"),
            expected_effects=("改变最终入场集合",),
            core_factors=tuple(),
        )

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            research_script,
            "_request_validated_round_brief",
            return_value=summary_brief,
        ):
            rebased = research_script._rebase_candidate_metadata_to_final_code(
                candidate=candidate,
                round_brief=round_brief,
                base_source=base_source,
                workspace_root=Path(temp_dir),
                factor_change_mode="default",
                context_label="测试候选",
            )

        self.assertEqual(rebased.candidate_id, "candidate_realigned")
        self.assertEqual(rebased.hypothesis, "最终代码实际是在改 strategy 最终路由")
        self.assertEqual(rebased.change_tags, ("strategy", "route_shift"))
        self.assertEqual(rebased.edited_regions, ("strategy",))

    def test_workspace_strategy_changed_source_or_raise_requires_real_file_change(self):
        base_source = self._minimal_strategy_source()

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_strategy_file = Path(temp_dir) / "src/strategy_macd_aggressive.py"
            workspace_strategy_file.parent.mkdir(parents=True, exist_ok=True)
            workspace_strategy_file.write_text(base_source)

            with self.assertRaisesRegex(StrategySourceError, "candidate missing actual changed regions"):
                research_script._workspace_strategy_changed_source_or_raise(
                    workspace_strategy_file=workspace_strategy_file,
                    base_source=base_source,
                )

            workspace_strategy_file.write_text(
                base_source.replace(
                    "def strategy(*args, **kwargs):\n    return None\n",
                    "def strategy(*args, **kwargs):\n    return 1\n",
                    1,
                )
            )

            changed = research_script._workspace_strategy_changed_source_or_raise(
                workspace_strategy_file=workspace_strategy_file,
                base_source=base_source,
            )

            self.assertIn("return 1", changed)

    def test_cluster_for_tags_groups_ownership_variants(self):
        self.assertEqual(cluster_for_tags(["acceptance_continuity", "ownership_transfer"]), "ownership_cluster")


class ExplorationGuardFixesTest(unittest.TestCase):
    def test_build_system_edit_signature_detects_real_changed_regions_and_param_families(self):
        base_source = """
# PARAMS_START
PARAMS = {'breakout_volume_ratio_min': 1.0}
# PARAMS_END

def _sideways_release_flags(*args, **kwargs):
    return {}

def _is_sideways_regime(*args, **kwargs):
    return False

def _flow_signal_metrics(*args, **kwargs):
    return {}

def _flow_confirmation_ok(*args, **kwargs):
    return True

def _flow_entry_ok(*args, **kwargs):
    return True

def _trend_quality_long(*args, **kwargs):
    return True

def _trend_quality_short(*args, **kwargs):
    return True

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_long(*args, **kwargs):
    return True

def _trend_followthrough_short(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def _long_entry_signal(*args, **kwargs):
    return None

def _short_entry_signal(*args, **kwargs):
    return None

def strategy(*args, **kwargs):
    return None
"""
        candidate_source = base_source.replace(
            "'breakout_volume_ratio_min': 1.0",
            "'breakout_volume_ratio_min': 1.2, 'breakout_flow_imbalance_min': 0.05",
        )
        signature = build_system_edit_signature(base_source, candidate_source, EDITABLE_REGIONS)

        self.assertEqual(signature["changed_regions"], ("PARAMS",))
        self.assertIn("flow", signature["param_families"])
        self.assertIn("PARAMS", signature["structural_tokens"])

    def _candidate(self, *, cluster="ownership_cluster", tags=("ownership_takeover", "acceptance_continuity"), regions=("strategy",), hypothesis="优化多头到来阶段", effects=("提高多头上车",), factors=()):
        return StrategyCandidate(
            candidate_id="candidate_guard",
            hypothesis=hypothesis,
            change_plan="调整策略",
            closest_failed_cluster=cluster,
            novelty_proof="这次会改变交易路径。",
            change_tags=tuple(tags),
            edited_regions=tuple(regions),
            expected_effects=tuple(effects),
            core_factors=tuple(factors),
            strategy_code="def strategy():\n    return None\n",
        )

    def _scored_entry(self, iteration, *, cluster="ownership_cluster", outcome="rejected", tags=("ownership_takeover",), regions=("strategy",), promotion=0.40, quality=0.50):
        return {
            "iteration": iteration,
            "candidate_id": f"entry_{iteration}",
            "outcome": outcome,
            "stop_stage": "full_eval",
            "promotion_score": promotion,
            "quality_score": quality,
            "promotion_delta": 0.0,
            "gate_reason": "通过",
            "closest_failed_cluster": cluster,
            "change_tags": list(tags),
            "edited_regions": list(regions),
            "hypothesis": "同簇近邻调整",
            "expected_effects": ["提高多头上车"],
            "metrics": {
                "combined_trend_capture_score": 0.45,
                "segment_hit_rate": 0.42,
                "bull_capture_score": 0.20,
                "bear_capture_score": 0.48,
                "total_trades": 90.0,
                "avg_fee_drag": 0.70,
            },
            "score_regime": "trend_capture_v5",
        }

    def test_evaluate_candidate_exploration_guard_blocks_same_cluster_low_novelty(self):
        entries = [
            self._scored_entry(1),
            self._scored_entry(2),
            self._scored_entry(3),
        ]

        block = evaluate_candidate_exploration_guard(
            self._candidate(),
            entries,
            journal_path=None,
            score_regime="trend_capture_v5",
            current_iteration=4,
        )

        self.assertIsNone(block)

    def test_evaluate_candidate_exploration_guard_uses_system_changed_regions_over_declared_metadata(self):
        base_source = """
# PARAMS_START
PARAMS = {'breakout_volume_ratio_min': 1.0}
# PARAMS_END

def _sideways_release_flags(*args, **kwargs):
    return {}

def _is_sideways_regime(*args, **kwargs):
    return False

def _flow_signal_metrics(*args, **kwargs):
    return {}

def _flow_confirmation_ok(*args, **kwargs):
    return True

def _flow_entry_ok(*args, **kwargs):
    return True

def _trend_quality_long(*args, **kwargs):
    return True

def _trend_quality_short(*args, **kwargs):
    return True

def _trend_quality_ok(*args, **kwargs):
    return True

def _trend_followthrough_long(*args, **kwargs):
    return True

def _trend_followthrough_short(*args, **kwargs):
    return True

def _trend_followthrough_ok(*args, **kwargs):
    return True

def _long_entry_signal(*args, **kwargs):
    return None

def _short_entry_signal(*args, **kwargs):
    return None

def strategy(*args, **kwargs):
    return None
"""
        candidate_source = base_source.replace(
            "'breakout_volume_ratio_min': 1.0",
            "'breakout_volume_ratio_min': 1.1",
        )
        candidate = self._candidate(regions=("strategy",))
        candidate = StrategyCandidate(
            candidate_id=candidate.candidate_id,
            hypothesis=candidate.hypothesis,
            change_plan=candidate.change_plan,
            closest_failed_cluster=candidate.closest_failed_cluster,
            novelty_proof=candidate.novelty_proof,
            change_tags=candidate.change_tags,
            edited_regions=("strategy",),
            expected_effects=candidate.expected_effects,
            core_factors=candidate.core_factors,
            strategy_code=candidate_source,
        )
        entries = [
            {
                **self._scored_entry(1),
                "system_changed_regions": ["PARAMS"],
                "system_region_families": ["params"],
                "system_param_families": ["breakout"],
                "system_structural_tokens": ["PARAMS", "breakout"],
                "system_signature_hash": "sig_a",
            },
            {
                **self._scored_entry(2),
                "system_changed_regions": ["PARAMS"],
                "system_region_families": ["params"],
                "system_param_families": ["breakout"],
                "system_structural_tokens": ["PARAMS", "breakout"],
                "system_signature_hash": "sig_b",
            },
            {
                **self._scored_entry(3),
                "system_changed_regions": ["PARAMS"],
                "system_region_families": ["params"],
                "system_param_families": ["breakout"],
                "system_structural_tokens": ["PARAMS", "breakout"],
                "system_signature_hash": "sig_c",
            },
        ]

        block = evaluate_candidate_exploration_guard(
            candidate,
            entries,
            journal_path=None,
            score_regime="trend_capture_v5",
            current_iteration=4,
            base_source=base_source,
            editable_regions=EDITABLE_REGIONS,
        )

        self.assertIsNone(block)

    def test_evaluate_candidate_exploration_guard_applies_lock_on_second_blocked_round(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "blocked_old",
                "outcome": "exploration_blocked",
                "stop_stage": "blocked_same_cluster",
                "gate_reason": "同簇低变化近邻",
                "block_kind": "same_cluster",
                "blocked_cluster": "ownership_cluster",
                "lock_rounds": 0,
                "score_regime": "trend_capture_v5",
            },
            self._scored_entry(2),
            self._scored_entry(3),
            self._scored_entry(4),
        ]

        block = evaluate_candidate_exploration_guard(
            self._candidate(),
            entries,
            journal_path=None,
            score_regime="trend_capture_v5",
            current_iteration=5,
        )

        self.assertIsNone(block)

    def test_build_exploration_guard_state_avoids_double_count_with_compact(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "blocked_compacted",
                "outcome": "exploration_blocked",
                "stop_stage": "blocked_same_cluster",
                "gate_reason": "同簇低变化近邻",
                "block_kind": "same_cluster",
                "blocked_cluster": "ownership_cluster",
                "lock_rounds": 0,
                "score_regime": "trend_capture_v5",
            },
            {
                "iteration": 2,
                "candidate_id": "blocked_recent",
                "outcome": "exploration_blocked",
                "stop_stage": "blocked_same_cluster",
                "gate_reason": "同簇低变化近邻",
                "block_kind": "same_cluster",
                "blocked_cluster": "ownership_cluster",
                "lock_rounds": 0,
                "score_regime": "trend_capture_v5",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.jsonl"
            journal_path.write_text("")
            journal_path.with_suffix(".compact.json").write_text(
                json.dumps(
                    {
                        "compacted_up_to": 1,
                        "rounds": [
                            {
                                "score_regime": "trend_capture_v5",
                                "exploration_summary": {
                                    "ownership_cluster": {
                                        "same_cluster_rounds": 1,
                                        "lock_trigger_rounds": 0,
                                    }
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )

            state = build_exploration_guard_state(
                entries,
                journal_path=journal_path,
                score_regime="trend_capture_v5",
                current_iteration=3,
            )

        self.assertEqual(state["history_counts"]["ownership_cluster"]["same_cluster_rounds"], 2)

    def test_journal_summary_omits_direction_cooling_board(self):
        entries = [
            {
                "iteration": 4,
                "candidate_id": "blocked_lock",
                "outcome": "exploration_blocked",
                "stop_stage": "blocked_same_cluster",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "同簇低变化近邻：ownership_cluster 将冷却 3 轮",
                "block_kind": "same_cluster",
                "blocked_cluster": "ownership_cluster",
                "lock_rounds": 3,
                "lock_level": 1,
                "lock_trigger_iteration": 4,
                "lock_expires_before_iteration": 8,
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "hypothesis": "同簇近邻重复",
                "score_regime": "trend_capture_v5",
            }
        ]

        summary = build_journal_prompt_summary(
            entries,
            limit=8,
            current_score_regime="trend_capture_v5",
            current_iteration=5,
        )

        self.assertNotIn("方向冷却表", summary)
        self.assertIn("ownership_cluster", summary)
        self.assertIn("方向风险表", summary)


class FreqtradeAdapterFixesTest(unittest.TestCase):
    def test_build_signal_frame_derives_higher_timeframes_from_15m_with_flow_columns(self):
        rows = []
        for index in range(64):
            base_price = 100.0 + index * 0.5
            volume = 50.0 + index
            taker_buy_volume = volume * 0.58
            rows.append(
                {
                    "timestamp": index * 900_000,
                    "open": base_price,
                    "high": base_price + 1.2,
                    "low": base_price - 1.0,
                    "close": base_price + 0.6,
                    "volume": volume,
                    "trade_count": 200 + index,
                    "taker_buy_volume": taker_buy_volume,
                    "taker_sell_volume": volume - taker_buy_volume,
                }
            )
        df_15m = pd.DataFrame(rows)

        signal_frame = ft_adapter.build_signal_frame(df_15m)

        self.assertIn("trade_count_ratio_1h", signal_frame.columns)
        self.assertIn("flow_imbalance_4h", signal_frame.columns)
        tail = signal_frame.tail(1).iloc[0]
        self.assertFalse(pd.isna(tail["trade_count_ratio_1h"]))
        self.assertFalse(pd.isna(tail["flow_imbalance_4h"]))

    def test_cluster_key_prefers_stable_canonical_cluster(self):
        self.assertEqual(
            cluster_key_for_components("arrival_reallocation", ["ownership_takeover", "continuation_prune"]),
            "ownership_cluster",
        )
        self.assertEqual(
            cluster_key_for_components("ownership_cluster", ["position_state_takeover"]),
            "ownership_cluster",
        )

    def test_cluster_key_prefers_inferred_canonical_cluster_over_declared_nearest_cluster(self):
        self.assertEqual(
            cluster_key_for_components("ownership_cluster", ["sideways_filter", "hourly_discount"]),
            "sideways_cluster",
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

    def test_journal_summary_puts_stage_executive_summary_before_direction_risk_board(self):
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

        self.assertIn("当前 stage 执行摘要", first_line)
        self.assertIn("方向风险表", summary)
        self.assertIn("ownership_cluster", summary)
        self.assertIn("EXHAUSTED", summary)

    def test_journal_summary_aggregates_repeated_failure_nucleus(self):
        entries = []
        for idx in range(3):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"candidate_repeat_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 0.03,
                    "quality_score": 0.02,
                    "promotion_delta": 0.0,
                    "gate_reason": "train均值分偏低(0.02)；val命中率偏低(7%)；val多头捕获偏低(-0.02)",
                    "decision_reason": "train均值分偏低(0.02)；val命中率偏低(7%)；val多头捕获偏低(-0.02)",
                    "change_tags": ["remove_dead_gate", "entry_path", "long"],
                    "edited_regions": ["long_breakout_ok", "strategy"],
                    "hypothesis": "重复落在同一失败核",
                    "score_regime": "trend_capture_v1",
                    "target_family": "long",
                    "system_ordinary_region_families": ["entry_path"],
                }
            )

        summary = build_journal_prompt_summary(entries, limit=8)

        self.assertIn("当前 stage 失败核聚合（去重后再看）", summary)
        self.assertIn("核 1 | 3 轮", summary)
        self.assertIn("ordinary family=entry_path", summary)
        self.assertIn("同一个已知坏盆地", summary)

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
        self.assertNotIn("continuation_energy_decay", summary)

    def test_journal_summary_emits_exploration_trigger_after_three_same_gate_rejections(self):
        entries = []
        for idx in range(3):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"gate_blocked_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 0.40,
                    "quality_score": 0.33,
                    "promotion_delta": 0.0,
                    "gate_reason": "train/val分数落差过大(0.46)",
                    "change_tags": ["long_relay_entry", "fourh_participation"],
                    "edited_regions": ["_trend_quality_ok"],
                    "closest_failed_cluster": "participation_cluster",
                    "hypothesis": "连续几轮都被同一个 train/val gap gate 拦下。",
                    "core_factors": [
                        {
                            "name": "fourh_relay_ratio",
                            "thesis": "4h 接力阈值过严会错过多头早段。",
                            "current_signal": "最近几轮都在 participation_cluster 内低变化打转。",
                        }
                    ],
                    "metrics": {
                        "total_trades": 70.0,
                        "avg_fee_drag": 0.64,
                        "combined_trend_capture_score": 0.46,
                        "segment_hit_rate": 0.46,
                        "bull_capture_score": 0.16,
                        "bear_capture_score": 0.60,
                    },
                    "score_regime": "trend_capture_v5",
                }
            )

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v5")

        self.assertIn("探索触发（必须执行）", summary)
        self.assertIn("participation_cluster", summary)
        self.assertNotIn("fourh_relay_ratio", summary)

    def test_journal_summary_prefers_final_decision_reason_over_gate_pass_marker(self):
        entries = [
            {
                "iteration": 11,
                "candidate_id": "candidate_final_reason",
                "outcome": "rejected",
                "stop_stage": "full_eval",
                "promotion_score": 0.48,
                "quality_score": 0.77,
                "promotion_delta": 0.0,
                "gate_reason": "通过",
                "note": "晋级分提升不足(0.00 <= 0.02)",
                "change_tags": ["ownership_reset"],
                "edited_regions": ["strategy"],
                "hypothesis": "虽然通过 gate，但没有刷新最优。",
                "score_regime": "trend_capture_v4",
            }
        ]

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v4")

        self.assertIn("晋级分提升不足", summary)
        self.assertNotIn("| 11 | 未保留 | 完整评估 | 0.77 | 0.48 | - | - | - | - | 通过 |", summary)

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

    def test_evaluate_candidate_exploration_guard_blocks_same_cluster_after_noop_streak(self):
        entries = []
        for idx in range(3):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"noop_{idx}",
                    "outcome": "behavioral_noop",
                    "stop_stage": "behavioral_noop",
                    "promotion_score": None,
                    "quality_score": None,
                    "promotion_delta": None,
                    "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                    "closest_failed_cluster": "ownership_cluster",
                    "change_tags": ["ownership_takeover", "acceptance_continuity"],
                    "edited_regions": ["strategy"],
                    "hypothesis": "连续行为无变化",
                    "expected_effects": ["提高多头上车"],
                    "score_regime": "trend_capture_v5",
                }
            )

        candidate = StrategyCandidate(
            candidate_id="candidate_guard",
            hypothesis="连续行为无变化后的同簇近邻",
            change_plan="继续微调 ownership 方向",
            closest_failed_cluster="ownership_cluster",
            novelty_proof="这次仍是同簇近邻。",
            change_tags=("ownership_takeover", "acceptance_continuity"),
            edited_regions=("strategy",),
            expected_effects=("提高多头上车",),
            core_factors=(),
            strategy_code="def strategy():\n    return None\n",
        )
        block = evaluate_candidate_exploration_guard(
            candidate,
            entries,
            journal_path=None,
            score_regime="trend_capture_v5",
            current_iteration=4,
        )

        self.assertIsNone(block)

    def test_failure_wiki_guard_blocks_exact_cut_after_repeated_noop(self):
        entries = []
        for idx in range(2):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"noop_exact_{idx}",
                    "outcome": "behavioral_noop",
                    "stop_stage": "behavioral_noop",
                    "promotion_delta": None,
                    "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                    "closest_failed_cluster": "ownership_cluster",
                    "change_tags": ["ownership_takeover", "acceptance_continuity"],
                    "edited_regions": ["strategy"],
                    "hypothesis": "连续行为无变化",
                    "expected_effects": ["提高多头上车"],
                    "score_regime": "trend_capture_v5",
                }
            )

        candidate = StrategyCandidate(
            candidate_id="candidate_wiki_guard",
            hypothesis="继续沿用相同 exact cut",
            change_plan="继续微调 strategy 最终路由",
            closest_failed_cluster="ownership_cluster",
            novelty_proof="这次还是同一条 cut。",
            change_tags=("ownership_takeover", "acceptance_continuity"),
            edited_regions=("strategy",),
            expected_effects=("提高多头上车",),
            core_factors=(),
            strategy_code="def strategy():\n    return None\n",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir) / "memory"
            build_journal_prompt_summary(
                entries,
                limit=8,
                current_score_regime="trend_capture_v5",
                active_stage_iteration=1,
                memory_root=memory_root,
            )
            failure_wiki_index = load_failure_wiki_index(memory_root)

        block = evaluate_candidate_failure_wiki_guard(candidate, failure_wiki_index)

        self.assertIsNone(block)
        self.assertEqual(failure_wiki_index.get("blocked_cuts"), {})
        self.assertTrue(
            any(item.get("block_exact_cut") for item in failure_wiki_index.get("items", []))
        )

    def test_failure_wiki_ignores_technical_duplicate_source_no_edit_entries(self):
        entries = []
        for idx in range(3):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"stage_resume_env_blocked_v{idx}",
                    "outcome": "duplicate_skipped",
                    "stop_stage": "duplicate_source",
                    "promotion_delta": None,
                    "gate_reason": "候选源码与当前主参考完全相同",
                    "closest_failed_cluster": "environment_blocked_nearest_to_long_outer_context_widen",
                    "change_tags": ["blocked", "no_edit"],
                    "edited_regions": ["strategy"],
                    "system_changed_regions": [],
                    "hypothesis": "环境阻塞，未执行代码改动",
                    "expected_effects": ["无"],
                    "score_regime": "trend_capture_v5",
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir) / "memory"
            build_journal_prompt_summary(
                entries,
                limit=8,
                current_score_regime="trend_capture_v5",
                active_stage_iteration=1,
                memory_root=memory_root,
            )
            failure_wiki_index = load_failure_wiki_index(memory_root)

        self.assertEqual(failure_wiki_index.get("blocked_cuts"), {})

    def test_journal_summary_mentions_technical_invalid_count_and_keeps_it_out_of_failure_memory(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "stage_resume_env_blocked_v1",
                "outcome": "duplicate_skipped",
                "stop_stage": "duplicate_source",
                "promotion_delta": None,
                "gate_reason": "候选源码与当前主参考完全相同",
                "closest_failed_cluster": "environment_blocked_nearest_to_long_outer_context_widen",
                "change_tags": ["blocked", "no_edit"],
                "edited_regions": ["strategy"],
                "system_changed_regions": [],
                "hypothesis": "环境阻塞，未执行代码改动",
                "score_regime": "trend_capture_v5",
            },
            {
                "iteration": 2,
                "candidate_id": "candidate_real",
                "outcome": "rejected",
                "stop_stage": "full_eval",
                "promotion_score": 0.1,
                "quality_score": 0.2,
                "promotion_delta": -0.03,
                "gate_reason": "val命中率偏低(7%)",
                "closest_failed_cluster": "ownership_cluster",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "system_changed_regions": ["strategy"],
                "hypothesis": "真实策略失败",
                "score_regime": "trend_capture_v5",
            },
        ]

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v5")

        self.assertIn("技术空转 1", summary)
        self.assertIn("ownership_cluster", summary)

    def test_candidate_invalid_generation_block_info_flags_no_edit_candidate(self):
        base_source = (REPO_ROOT / "src/strategy_macd_aggressive.py").read_text()
        candidate = StrategyCandidate(
            candidate_id="stage_resume_env_blocked_v1",
            hypothesis="未执行代码改动",
            change_plan="环境阻塞，未执行代码改动",
            closest_failed_cluster="environment_blocked_nearest_to_long_outer_context_widen",
            novelty_proof="未形成真实 diff",
            change_tags=("blocked", "no_edit"),
            edited_regions=("strategy",),
            expected_effects=("无",),
            core_factors=(),
            strategy_code=base_source,
        )

        block = research_script._candidate_invalid_generation_block_info(
            candidate,
            base_source=base_source,
        )

        self.assertIsNotNone(block)
        self.assertEqual(block["block_kind"], "invalid_generation")
        self.assertIn("真实代码改动", block["blocked_reason"])
        self.assertIn("完全相同", "；".join(block["invalid_reasons"]))
        self.assertIn("先确定一个单一策略方向", block["feedback_note"])
        self.assertIn("把这个方向直接落到 `src/strategy_macd_aggressive.py`", block["feedback_note"])
        self.assertIn("文本摘要只描述你已经实际落到代码里的改动", block["feedback_note"])

    def test_store_research_session_metadata_no_longer_persists_invalid_generation_streak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            temp_paths = replace(
                research_script.RUNTIME.paths,
                session_state_file=temp_root / "state/session.json",
            )
            temp_runtime = replace(research_script.RUNTIME, paths=temp_paths)

            with mock.patch.object(research_script, "RUNTIME", temp_runtime), mock.patch.object(
                research_script, "best_source", "def strategy(*args, **kwargs):\n    return None\n"
            ), mock.patch.object(
                research_script, "best_report", object()
            ), mock.patch.object(
                research_script, "champion_report", None
            ), mock.patch.object(
                research_script, "reference_stage_started_at", "2026-04-21T00:00:00+00:00"
            ), mock.patch.object(
                research_script, "reference_stage_iteration", 7
            ):
                research_script._store_research_session_metadata(
                    session_id="session-123",
                    workspace_root=temp_root / "workspace",
                )

            payload = json.loads(temp_paths.session_state_file.read_text())
            self.assertEqual(payload["session_id"], "session-123")
            self.assertNotIn("invalid_generation_streak", payload)
            self.assertNotIn("factor_change_mode", payload)
            self.assertNotIn("iteration_lane", payload)

    def test_active_research_session_id_ignores_factor_mode_scope_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            temp_paths = replace(
                research_script.RUNTIME.paths,
                session_state_file=temp_root / "state/session.json",
            )
            temp_runtime = replace(research_script.RUNTIME, paths=temp_paths, base_factor_change_mode="default")

            with mock.patch.object(research_script, "RUNTIME", temp_runtime), mock.patch.object(
                research_script, "best_source", "def strategy(*args, **kwargs):\n    return None\n"
            ), mock.patch.object(
                research_script, "best_report", object()
            ), mock.patch.object(
                research_script, "champion_report", None
            ), mock.patch.object(
                research_script, "reference_stage_started_at", "2026-04-21T00:00:00+00:00"
            ), mock.patch.object(
                research_script, "reference_stage_iteration", 7
            ):
                research_script._store_research_session_metadata(
                    session_id="session-123",
                    workspace_root=temp_root / "workspace",
                    factor_change_mode="default",
                )
                active_session_id = research_script._active_research_session_id(
                    factor_change_mode="factor_admission"
                )

            self.assertEqual(active_session_id, "session-123")

    def test_active_research_session_id_ignores_iteration_lane_scope_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            temp_paths = replace(
                research_script.RUNTIME.paths,
                session_state_file=temp_root / "state/session.json",
            )
            temp_runtime = replace(research_script.RUNTIME, paths=temp_paths, base_factor_change_mode="default")

            with mock.patch.object(research_script, "RUNTIME", temp_runtime), mock.patch.object(
                research_script, "best_source", "def strategy(*args, **kwargs):\n    return None\n"
            ), mock.patch.object(
                research_script, "best_report", object()
            ), mock.patch.object(
                research_script, "champion_report", None
            ), mock.patch.object(
                research_script, "reference_stage_started_at", "2026-04-21T00:00:00+00:00"
            ), mock.patch.object(
                research_script, "reference_stage_iteration", 7
            ):
                research_script._store_research_session_metadata(
                    session_id="session-123",
                    workspace_root=temp_root / "workspace",
                    factor_change_mode="default",
                    iteration_lane="research",
                )
                active_session_id = research_script._active_research_session_id(
                    factor_change_mode="default",
                    iteration_lane="compaction",
                )

            self.assertEqual(active_session_id, "session-123")

    def test_planner_session_policy_is_always_single_persistent_planner(self):
        session_kind, use_persistent = research_script._planner_session_policy_for_iteration_lane("compaction")

        self.assertEqual(session_kind, "planner")
        self.assertTrue(use_persistent)

    def test_resolve_iteration_lane_state_always_returns_research(self):
        state = research_script._resolve_iteration_lane_state([])

        self.assertEqual(state["lane"], "research")
        self.assertEqual(state["reason"], "")

    def test_journal_summary_limits_recent_rows_and_meta_lines_to_requested_limit(self):
        entries = []
        for idx in range(5):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"candidate_{idx + 1}",
                    "outcome": "behavioral_noop",
                    "stop_stage": "behavioral_noop",
                    "promotion_score": None,
                    "quality_score": None,
                    "promotion_delta": None,
                    "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                    "closest_failed_cluster": "ownership_cluster",
                    "change_tags": ["ownership_takeover"],
                    "edited_regions": ["strategy"],
                    "hypothesis": "连续行为无变化",
                    "score_regime": "trend_capture_v4",
                }
            )

        summary = build_journal_prompt_summary(entries, limit=2, current_score_regime="trend_capture_v4")

        self.assertIn("当前 stage（自 round 1 激活以来） 共 5 条", summary)
        self.assertIn("以下表格与元信息仅展示最近 2 条", summary)
        self.assertIn("candidate_5", summary)
        self.assertIn("candidate_4", summary)
        self.assertNotIn("candidate_1", summary)

    def test_journal_summary_displays_dash_for_noop_metrics_without_full_eval(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "noop_only",
                "outcome": "behavioral_noop",
                "stop_stage": "behavioral_noop",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                "closest_failed_cluster": "ownership_cluster",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "hypothesis": "连续行为无变化",
                "score_regime": "trend_capture_v4",
            }
        ]

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v4")

        self.assertIn("| 1 | 行为无变化 | 行为无变化 | - | - | - | - | - | - |", summary)
        self.assertNotIn("| 1 | 行为无变化 | 行为无变化 | - | - | 0.00 |", summary)

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
        self.assertIn("当前 stage 核心指标表", summary)
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

        self.assertIn("当前 stage（自 round 31 激活以来） 共 5 条", summary)
        self.assertIn("current_0", summary)
        self.assertIn("current_4", summary)
        self.assertNotIn("legacy_29", summary)

    def test_journal_summary_uses_active_stage_iteration_boundary(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "bootstrap_1",
                "outcome": "rejected",
                "stop_stage": "full_eval",
                "promotion_score": 0.22,
                "quality_score": 0.30,
                "promotion_delta": 0.0,
                "gate_reason": "通过",
                "change_tags": ["legacy_breakout"],
                "edited_regions": ["strategy"],
                "hypothesis": "旧 stage 历史",
                "score_regime": "trend_capture_v4",
            },
            {
                "iteration": 2,
                "candidate_id": "bootstrap_2",
                "outcome": "accepted",
                "stop_stage": "full_eval",
                "promotion_score": 0.28,
                "quality_score": 0.34,
                "promotion_delta": 0.05,
                "gate_reason": "通过",
                "change_tags": ["legacy_breakout"],
                "edited_regions": ["strategy"],
                "hypothesis": "旧 stage 终点",
                "score_regime": "trend_capture_v4",
            },
            {
                "iteration": 3,
                "candidate_id": "current_1",
                "outcome": "behavioral_noop",
                "stop_stage": "behavioral_noop",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "hypothesis": "当前 stage",
                "score_regime": "trend_capture_v4",
            },
        ]

        summary = build_journal_prompt_summary(
            entries,
            limit=8,
            current_score_regime="trend_capture_v4",
            active_stage_iteration=3,
            reference_role="champion",
        )

        self.assertIn("当前 champion stage", summary)
        self.assertIn("round 3", summary)
        self.assertIn("当前 champion stage（自 round 3 激活以来） 共 1 条", summary)
        self.assertIn("历史 stage 摘要", summary)
        self.assertIn("bootstrap", summary)

    def test_journal_summary_prefers_stage_timestamp_when_iterations_restart(self):
        entries = [
            {
                "iteration": 84,
                "timestamp": "2026-04-21T01:20:00+00:00",
                "candidate_id": "legacy_84",
                "outcome": "accepted",
                "stop_stage": "full_eval",
                "promotion_score": 0.28,
                "quality_score": 0.31,
                "promotion_delta": 0.04,
                "gate_reason": "通过",
                "change_tags": ["legacy_breakout"],
                "edited_regions": ["strategy"],
                "hypothesis": "旧 stage",
                "score_regime": "trend_capture_v4",
            },
            {
                "iteration": 1,
                "timestamp": "2026-04-21T01:24:00+00:00",
                "candidate_id": "current_1",
                "outcome": "behavioral_noop",
                "stop_stage": "behavioral_noop",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "hypothesis": "新 stage",
                "score_regime": "trend_capture_v4",
            },
            {
                "iteration": 2,
                "timestamp": "2026-04-21T01:25:00+00:00",
                "candidate_id": "current_2",
                "outcome": "runtime_failed",
                "stop_stage": "runtime_error",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "complexity family budget exceeded",
                "change_tags": ["widen_outer_context"],
                "edited_regions": ["_trend_quality_long"],
                "hypothesis": "新 stage",
                "score_regime": "trend_capture_v4",
            },
        ]

        summary = build_journal_prompt_summary(
            entries,
            limit=8,
            current_score_regime="trend_capture_v4",
            active_stage_started_at="2026-04-21T01:23:17+00:00",
            active_stage_iteration=85,
            reference_role="champion",
        )

        self.assertIn("当前 champion stage（自 round 1 / 2026-04-21T01:23:17+00:00 激活以来） 共 2 条", summary)
        self.assertIn("current_1", summary)
        self.assertIn("current_2", summary)
        self.assertNotIn("legacy_84", summary)

    def test_journal_summary_writes_memory_snapshots(self):
        entries = [
            {
                "iteration": 5,
                "candidate_id": "current_stage_candidate",
                "outcome": "behavioral_noop",
                "stop_stage": "behavioral_noop",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "hypothesis": "当前 stage",
                "score_regime": "trend_capture_v4",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir) / "memory"
            build_journal_prompt_summary(
                entries,
                limit=8,
                current_score_regime="trend_capture_v4",
                active_stage_iteration=5,
                memory_root=memory_root,
            )

            self.assertTrue((memory_root / "summaries/current_stage_rounds.json").exists())
            self.assertTrue((memory_root / "summaries/past_stage_summaries.json").exists())
            self.assertTrue((memory_root / "summaries/all_time_tables.json").exists())
            self.assertTrue((memory_root / "prompt/latest_history_package.md").exists())
            self.assertTrue((memory_root / "wiki/duplicate_watchlist.md").exists())
            self.assertTrue((memory_root / "wiki/failure_wiki.md").exists())
            self.assertTrue((memory_root / "wiki/failure_wiki_index.json").exists())
            self.assertIn("blocked_cuts", load_failure_wiki_index(memory_root))

    def test_journal_summary_writes_duplicate_watchlist_markdown(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "cand_repeat_alpha",
                "outcome": "rejected",
                "stop_stage": "full_eval",
                "promotion_score": 0.10,
                "quality_score": 0.20,
                "promotion_delta": -0.05,
                "gate_reason": "相对当前champion晋级分提升不足(-0.05 <= 0.02)",
                "decision_reason": "相对当前champion晋级分提升不足(-0.05 <= 0.02)",
                "code_hash": "hash_repeat_alpha",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "system_changed_regions": ["strategy"],
                "hypothesis": "第一次尝试",
                "target_family": "long",
                "closest_failed_cluster": "ownership_cluster",
                "metrics": {
                    "validation_segment_hit_rate": 0.12,
                    "validation_bull_capture_score": 0.03,
                    "validation_bear_capture_score": 0.02,
                    "total_trades": 120,
                },
                "score_regime": "trend_capture_v6",
            },
            {
                "iteration": 2,
                "candidate_id": "cand_repeat_alpha",
                "outcome": "duplicate_skipped",
                "stop_stage": "duplicate_history",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "候选源码命中最近研究历史",
                "decision_reason": "候选源码命中最近研究历史",
                "code_hash": "hash_repeat_alpha",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "system_changed_regions": ["strategy"],
                "hypothesis": "第二次重复提交",
                "target_family": "long",
                "closest_failed_cluster": "ownership_cluster",
                "metrics": {},
                "score_regime": "trend_capture_v6",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir) / "memory"
            build_journal_prompt_summary(
                entries,
                limit=8,
                current_score_regime="trend_capture_v6",
                active_stage_iteration=1,
                memory_root=memory_root,
            )

            duplicate_watchlist = (memory_root / "wiki/duplicate_watchlist.md").read_text()

        self.assertIn("Duplicate Watchlist", duplicate_watchlist)
        self.assertIn("cand_repeat_alpha", duplicate_watchlist)
        self.assertIn("ownership_cluster", duplicate_watchlist)
        self.assertIn("duplicate_history", duplicate_watchlist)

    def test_append_journal_archive_writes_raw_history_files(self):
        entry = {
            "iteration": 7,
            "candidate_id": "archive_test",
            "outcome": "rejected",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_root = Path(tmpdir) / "memory"
            append_journal_archive(memory_root, entry)

            raw_history = memory_root / "raw/full_history.jsonl"
            raw_rounds = memory_root / "raw/rounds"

            self.assertTrue(raw_history.exists())
            self.assertTrue(raw_rounds.exists())
            self.assertIn("archive_test", raw_history.read_text())
            self.assertEqual(len(list(raw_rounds.glob("*.json"))), 1)

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

    def test_journal_summary_emits_hot_cluster_warning_when_one_cluster_overdominates(self):
        entries = []
        for idx in range(10):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"hot_cluster_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 0.40,
                    "quality_score": 0.50,
                    "promotion_delta": 0.01 if idx == 0 else 0.0,
                    "gate_reason": "通过",
                    "change_tags": ["breakout_entry", "reduce_false_breakout"],
                    "edited_regions": ["strategy"],
                    "closest_failed_cluster": "trigger_efficiency_cluster",
                    "hypothesis": "同一方向簇持续占据最近轮次。",
                    "score_regime": "trend_capture_v4",
                }
            )

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v4")

        self.assertIn("主簇过热（必须先读）", summary)
        self.assertIn("当前复盘优先级", summary)
        self.assertIn("当前过热近邻/慎入区", summary)
        self.assertIn("trigger_efficiency_cluster", summary)
        self.assertIn("占比 100%", summary)
        self.assertNotIn("ACTIVE_WINNER", summary)
        self.assertIn("WARM", summary)

    def test_journal_summary_emits_stage_operating_metrics_table(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "accepted_long",
                "outcome": "accepted",
                "stop_stage": "full_eval",
                "promotion_score": 0.62,
                "quality_score": 0.58,
                "promotion_delta": 0.05,
                "gate_reason": "通过",
                "change_tags": ["remove_dead_gate"],
                "edited_regions": ["strategy"],
                "hypothesis": "放宽长侧死门。",
                "score_regime": "trend_capture_v4",
                "target_family": "long",
                "system_ordinary_region_families": ["sideways", "entry_path"],
                "smoke_passed": True,
                "full_eval_reached": True,
            },
            {
                "iteration": 2,
                "candidate_id": "noop_short",
                "outcome": "behavioral_noop",
                "stop_stage": "behavioral_noop",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                "change_tags": ["merge_veto"],
                "edited_regions": ["strategy"],
                "hypothesis": "删掉无效 veto。",
                "score_regime": "trend_capture_v4",
                "target_family": "short",
                "system_ordinary_region_families": ["entry_path"],
                "smoke_passed": True,
                "full_eval_reached": False,
            },
            {
                "iteration": 3,
                "candidate_id": "blocked_long",
                "outcome": "exploration_blocked",
                "stop_stage": "blocked_same_cluster",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "同簇低变化近邻",
                "change_tags": ["widen_outer_context"],
                "edited_regions": ["strategy"],
                "hypothesis": "优先扩 reachability。",
                "score_regime": "trend_capture_v4",
                "target_family": "long",
                "system_ordinary_region_families": [],
            },
            {
                "iteration": 4,
                "candidate_id": "runtime_mixed",
                "outcome": "runtime_failed",
                "stop_stage": "runtime_failed",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "运行失败",
                "change_tags": ["remove_dead_gate"],
                "edited_regions": ["strategy"],
                "hypothesis": "full_eval 途中失败。",
                "score_regime": "trend_capture_v4",
                "target_family": "mixed",
                "system_ordinary_region_families": ["trend_quality"],
                "runtime_failure_stage": "full_eval",
            },
        ]

        summary = build_journal_prompt_summary(
            entries,
            limit=8,
            current_score_regime="trend_capture_v4",
            reference_metrics={
                "validation_bull_capture_score": 0.10,
                "validation_bear_capture_score": 0.42,
            },
        )

        self.assertIn("当前 stage 运营指标表", summary)
        self.assertIn("smoke->full_eval", summary)
        self.assertIn("| 25% | 25% | 25% | 67% | 1.00 | 弱侧(long) 75% |", summary)

    def test_journal_summary_displays_candidate_validation_stage(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "invalid_complexity",
                "outcome": "runtime_failed",
                "stop_stage": "candidate_validation",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "运行失败",
                "decision_reason": "complexity family budget exceeded: trend_quality_family.lines=145 > 130",
                "change_tags": ["merge_veto"],
                "edited_regions": ["strategy"],
                "hypothesis": "候选在源码校验阶段失败。",
                "score_regime": "trend_capture_v4",
                "system_complexity_summary": "trend_quality_family:L+23/B+6/I+0",
                "system_bloat_flag": True,
            }
        ]

        summary = build_journal_prompt_summary(entries, limit=8, current_score_regime="trend_capture_v4")

        self.assertIn("源码校验", summary)
        self.assertIn("复杂度超标 1", summary)


class SmokeWindowSelectionTest(unittest.TestCase):
    def test_select_smoke_windows_includes_validation_when_count_is_three(self):
        Window = type("Window", (), {})
        windows = []
        for idx in range(1, 6):
            window = Window()
            window.group = "eval"
            window.label = f"train{idx}"
            windows.append(window)
        validation = Window()
        validation.group = "validation"
        validation.label = "val1"
        windows.append(validation)

        selected = select_smoke_windows(windows, 3)

        self.assertEqual([window.label for window in selected], ["train1", "val1", "train3"])

    def test_select_smoke_windows_spreads_coverage_when_count_is_five(self):
        Window = type("Window", (), {})
        windows = []
        for idx in range(1, 27):
            window = Window()
            window.group = "eval"
            window.label = f"train{idx}"
            windows.append(window)
        validation = Window()
        validation.group = "validation"
        validation.label = "val1"
        windows.append(validation)

        selected = select_smoke_windows(windows, 5)

        self.assertEqual([window.label for window in selected], ["train1", "val1", "train9", "train14", "train26"])


class ResearcherAdaptiveModeTest(unittest.TestCase):
    def test_resolve_iteration_factor_change_mode_enters_reminder_band_after_five_stalls(self):
        entries = [
            {"iteration": 1, "outcome": "behavioral_noop", "score_regime": research_script.SCORE_REGIME},
            {"iteration": 2, "outcome": "exploration_blocked", "score_regime": research_script.SCORE_REGIME},
            {"iteration": 3, "outcome": "rejected", "promotion_delta": 0.0, "score_regime": research_script.SCORE_REGIME},
            {"iteration": 4, "outcome": "duplicate_skipped", "promotion_delta": 0.0, "score_regime": research_script.SCORE_REGIME},
            {"iteration": 5, "outcome": "runtime_failed", "decision_reason": "complexity budget exceeded", "score_regime": research_script.SCORE_REGIME},
        ]

        with mock.patch.object(
            research_script,
            "RUNTIME",
            replace(research_script.RUNTIME, base_factor_change_mode="default"),
        ):
            mode, reason = research_script._resolve_iteration_factor_change_mode(entries)

        self.assertEqual(mode, "default")
        self.assertIn("不再自动切换", reason)

    def test_resolve_iteration_factor_change_state_stays_manual_after_many_stalls(self):
        entries = [
            {"iteration": idx + 1, "outcome": "behavioral_noop", "score_regime": research_script.SCORE_REGIME}
            for idx in range(7)
        ]

        with mock.patch.object(
            research_script,
            "RUNTIME",
            replace(research_script.RUNTIME, base_factor_change_mode="default"),
        ):
            state = research_script._resolve_iteration_factor_change_state(entries)

        self.assertEqual(state["mode"], "default")
        self.assertEqual(state["guidance_level"], "manual")
        self.assertEqual(state["trailing_stalls"], 0)

    def test_resolve_iteration_factor_change_mode_keeps_default_after_ten_stalls(self):
        entries = [
            {"iteration": idx + 1, "outcome": "behavioral_noop", "score_regime": research_script.SCORE_REGIME}
            for idx in range(10)
        ]

        with mock.patch.object(
            research_script,
            "RUNTIME",
            replace(research_script.RUNTIME, base_factor_change_mode="default"),
        ):
            mode, reason = research_script._resolve_iteration_factor_change_mode(entries)

        self.assertEqual(mode, "default")
        self.assertIn("不再自动切换", reason)

    def test_resolve_iteration_factor_change_mode_returns_base_mode_after_positive_delta(self):
        entries = [
            {"iteration": 1, "outcome": "behavioral_noop", "score_regime": research_script.SCORE_REGIME},
            {
                "iteration": 2,
                "outcome": "accepted",
                "promotion_delta": 0.03,
                "factor_change_mode": "factor_admission",
                "score_regime": research_script.SCORE_REGIME,
            },
            {"iteration": 3, "outcome": "behavioral_noop", "score_regime": research_script.SCORE_REGIME},
        ]

        with mock.patch.object(
            research_script,
            "RUNTIME",
            replace(research_script.RUNTIME, base_factor_change_mode="default"),
        ):
            mode, reason = research_script._resolve_iteration_factor_change_mode(entries)

        self.assertEqual(mode, "default")
        self.assertIn("不再自动切换", reason)

    def test_consecutive_no_edit_runtime_failures_counts_trailing_no_edit_only(self):
        entries = [
            {"iteration": 1, "outcome": "runtime_failed", "runtime_failure_stage": "candidate_no_edit", "decision_reason": "candidate missing actual changed regions"},
            {"iteration": 2, "outcome": "runtime_failed", "runtime_failure_stage": "candidate_validation", "decision_reason": "candidate missing actual changed regions"},
            {"iteration": 3, "outcome": "runtime_failed", "runtime_failure_stage": "full_eval", "decision_reason": "boom"},
        ]

        self.assertEqual(research_script._consecutive_no_edit_runtime_failures(entries), 0)
        self.assertEqual(research_script._consecutive_no_edit_runtime_failures(entries[:2]), 2)

    def test_maybe_stop_for_no_edit_stall_writes_stop_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            journal_path = temp_root / "journal.jsonl"
            stop_path = temp_root / "research.stop"
            heartbeat_path = temp_root / "heartbeat.json"
            journal_entries = [
                {
                    "iteration": 1,
                    "outcome": "runtime_failed",
                    "runtime_failure_stage": "candidate_no_edit",
                    "decision_reason": "candidate missing actual changed regions",
                },
                {
                    "iteration": 2,
                    "outcome": "runtime_failed",
                    "runtime_failure_stage": "candidate_validation",
                    "decision_reason": "candidate missing actual changed regions",
                },
            ]
            journal_path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in journal_entries))

            patched_runtime = replace(
                research_script.RUNTIME,
                paths=replace(
                    research_script.RUNTIME.paths,
                    journal_file=journal_path,
                    stop_file=stop_path,
                    heartbeat_file=heartbeat_path,
                ),
                max_consecutive_no_edit_failures_before_stop=2,
            )

            with mock.patch.object(research_script, "RUNTIME", patched_runtime):
                with mock.patch.object(research_script, "maybe_send_discord"):
                    stopped = research_script._maybe_stop_for_no_edit_stall(iteration_id=2)

            self.assertTrue(stopped)
            self.assertTrue(stop_path.exists())
            self.assertIn("连续 2 轮未把改动落到", stop_path.read_text())

    def test_current_stage_journal_entries_prefers_stage_timestamp_after_iteration_reset(self):
        entries = [
            {
                "iteration": 84,
                "timestamp": "2026-04-21T01:20:00+00:00",
                "outcome": "accepted",
                "score_regime": research_script.SCORE_REGIME,
            },
            {
                "iteration": 1,
                "timestamp": "2026-04-21T01:24:00+00:00",
                "outcome": "behavioral_noop",
                "score_regime": research_script.SCORE_REGIME,
            },
            {
                "iteration": 2,
                "timestamp": "2026-04-21T01:25:00+00:00",
                "outcome": "runtime_failed",
                "score_regime": research_script.SCORE_REGIME,
            },
        ]

        original_started_at = research_script.reference_stage_started_at
        original_iteration = research_script.reference_stage_iteration
        try:
            research_script.reference_stage_started_at = "2026-04-21T01:23:17+00:00"
            research_script.reference_stage_iteration = 85
            current_entries = research_script._current_stage_journal_entries(entries)
        finally:
            research_script.reference_stage_started_at = original_started_at
            research_script.reference_stage_iteration = original_iteration

        self.assertEqual([entry["iteration"] for entry in current_entries], [1, 2])


class CodexExecClientTest(unittest.TestCase):
    def test_generate_text_response_supports_schema_free_new_session(self):
        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 43210
                self.returncode = 0

            def communicate(self, input=None, timeout=None):
                return ("candidate_id: text_mode\nhypothesis: test\n", "")

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
            with mock.patch("codex_exec_client.subprocess.Popen", return_value=FakePopen()) as popen:
                text = generate_text_response(
                    prompt="test",
                    system_prompt="system",
                    config=config,
                )

        command = popen.call_args.args[0]
        self.assertEqual(text, "candidate_id: text_mode\nhypothesis: test")
        self.assertNotIn("--output-schema", command)

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
        self.assertEqual(command[:6], ["codex", "-a", "never", "-s", "danger-full-access", "-C"])
        self.assertEqual(command[7], "exec")
        self.assertIn("model_max_output_tokens=3200", command)

    def test_generate_json_object_supports_dangerous_bypass_mode(self):
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
            use_ephemeral=False,
            dangerously_bypass_approvals_and_sandbox=True,
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
        self.assertEqual(command[:4], ["codex", "--dangerously-bypass-approvals-and-sandbox", "-C", str(Path.cwd())])
        self.assertEqual(command[4], "exec")
        self.assertNotIn("-a", command)
        self.assertNotIn("-s", command)
        self.assertIn("model_max_output_tokens=3200", command)

    def test_generate_json_object_supports_resume_and_response_metadata(self):
        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 43210
                self.returncode = 0

            def communicate(self, input=None, timeout=None):
                return ('{"type":"thread.started","thread_id":"thread_123"}\n', "")

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
        metadata: dict[str, object] = {}

        with mock.patch("codex_exec_client.shutil.which", return_value="/usr/local/bin/codex"):
            with mock.patch("codex_exec_client.subprocess.Popen", return_value=FakePopen()) as popen:
                with mock.patch("codex_exec_client._read_output_message", return_value='{"ok": true}'):
                    payload = generate_json_object(
                        prompt="test",
                        system_prompt="system",
                        config=config,
                        session_id="thread_legacy",
                        response_metadata=metadata,
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
        self.assertEqual(payload, {"ok": True})
        self.assertIn("resume", command)
        self.assertIn("thread_legacy", command)
        self.assertNotIn("--output-schema", command)
        self.assertEqual(metadata["thread_id"], "thread_123")
        self.assertEqual(metadata["session_id"], "thread_123")
        self.assertTrue(metadata["resumed"])

    def test_generate_json_object_raises_session_error_on_invalid_resume(self):
        class FakePopen:
            def __init__(self, *args, **kwargs):
                self.pid = 43210
                self.returncode = 1

            def communicate(self, input=None, timeout=None):
                return ("", "Error: thread/resume: thread/resume failed: no rollout found for thread id thread_old")

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
                with self.assertRaises(StrategyGenerationSessionError):
                    generate_json_object(
                        prompt="test",
                        system_prompt="system",
                        config=config,
                        session_id="thread_old",
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


class ReferenceStateFixesTest(unittest.TestCase):
    def test_model_client_config_disables_ephemeral_for_persistent_session(self):
        config = research_script._model_client_config()

        self.assertFalse(config.use_ephemeral)
        self.assertTrue(config.dangerously_bypass_approvals_and_sandbox)

    def test_rebase_workspace_strategy_to_base_replaces_failed_candidate_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            strategy_path = workspace_root / "src/strategy_macd_aggressive.py"
            strategy_path.parent.mkdir(parents=True, exist_ok=True)
            strategy_path.write_text("failed candidate\n")

            research_script._rebase_workspace_strategy_to_base(
                workspace_root=workspace_root,
                base_source="reference baseline\n",
            )

            self.assertEqual(strategy_path.read_text(), "reference baseline\n")

    def test_persist_last_rejected_candidate_snapshot_writes_reference_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            failed_candidate = StrategyCandidate(
                candidate_id="candidate_bad_1",
                hypothesis="错误方向",
                change_plan="在坏版本上继续加条件",
                closest_failed_cluster="ownership_cluster",
                novelty_proof="这次换了措辞但没换真实路径。",
                change_tags=("long", "merge_veto"),
                edited_regions=("long_final_veto_clear",),
                expected_effects=("想放出更多多头",),
                core_factors=(),
                strategy_code="print('bad candidate')\n",
            )

            research_script._persist_last_rejected_candidate_snapshot(
                memory_root=memory_root,
                base_source="print('base')\n",
                failed_candidate=failed_candidate,
                block_info={
                    "block_kind": "failure_wiki_exact_cut",
                    "blocked_cluster": "ownership_cluster",
                    "blocked_reason": "同一 exact cut 已至少 2 次落回失败盆地",
                },
            )

            snapshot_md = (memory_root / "wiki/last_rejected_snapshot.md").read_text()
            snapshot_code = (memory_root / "wiki/last_rejected_candidate.py").read_text()

            self.assertIn("candidate_bad_1", snapshot_md)
            self.assertIn("failure_wiki_exact_cut", snapshot_md)
            self.assertIn("last_rejected_candidate.py", snapshot_md)
            self.assertIn("print('bad candidate')", snapshot_code)

    def test_reference_manifest_uses_baseline_role_when_no_champion(self):
        report = EvaluationReport(
            metrics={"promotion_score": 0.31, "quality_score": 0.22},
            gate_passed=False,
            gate_reason="train/val分数落差过大(0.46)",
            summary_text="",
            prompt_summary_text="",
        )
        original_champion = research_script.champion_report
        try:
            research_script.champion_report = None
            payload = research_script._reference_manifest_payload(
                "def strategy():\n    return None\n",
                report,
                stage_started_at="2026-04-20T00:00:00+00:00",
                stage_iteration=7,
            )
        finally:
            research_script.champion_report = original_champion

        self.assertEqual(payload["reference_role"], "baseline")
        self.assertIsNone(payload["champion"])
        self.assertEqual(payload["reference_stage_started_at"], "2026-04-20T00:00:00+00:00")
        self.assertEqual(payload["reference_stage_iteration"], 7)

    def test_reference_manifest_uses_single_active_reference_payload(self):
        working_base_source = "def strategy():\n    return 'working'\n"
        champion_source = "def strategy():\n    return 'champion'\n"
        working_base_report = EvaluationReport(
            metrics={"promotion_score": 0.31, "quality_score": 0.22},
            gate_passed=False,
            gate_reason="working_base only",
            summary_text="",
            prompt_summary_text="",
        )
        champion_report = EvaluationReport(
            metrics={"promotion_score": 0.42, "quality_score": 0.35},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        original_best_source = research_script.best_source
        original_champion_source = research_script.champion_source
        original_champion_report = research_script.champion_report
        original_champion_shadow = research_script.champion_shadow_test_metrics
        try:
            research_script.best_source = working_base_source
            research_script.champion_source = champion_source
            research_script.champion_report = champion_report
            research_script.champion_shadow_test_metrics = {"shadow_test_score": 1.23}
            payload = research_script._reference_manifest_payload(
                working_base_source,
                working_base_report,
                stage_started_at="2026-04-20T00:00:00+00:00",
                stage_iteration=7,
            )
        finally:
            research_script.best_source = original_best_source
            research_script.champion_source = original_champion_source
            research_script.champion_report = original_champion_report
            research_script.champion_shadow_test_metrics = original_champion_shadow

        self.assertEqual(payload["reference_role"], "baseline")
        self.assertEqual(payload["benchmark_role"], "baseline")
        self.assertEqual(payload["reference"]["code_hash"], research_script.source_hash(working_base_source))
        self.assertNotIn("working_base", payload)
        self.assertIsNone(payload["champion"])

    def test_promotion_acceptance_accepts_first_gate_passed_champion_when_baseline_fails(self):
        baseline_report = EvaluationReport(
            metrics={"promotion_score": 0.40, "quality_score": 0.33},
            gate_passed=False,
            gate_reason="train/val分数落差过大(0.46)",
            summary_text="",
            prompt_summary_text="",
        )
        candidate_report = EvaluationReport(
            metrics={"promotion_score": 0.36, "quality_score": 0.30},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        original_best_report = research_script.best_report
        original_champion = research_script.champion_report
        try:
            research_script.best_report = baseline_report
            research_script.champion_report = None
            accepted, reason = research_script._promotion_acceptance_decision(candidate_report)
        finally:
            research_script.best_report = original_best_report
            research_script.champion_report = original_champion

        self.assertTrue(accepted)
        self.assertIn("首个 gate-passed champion", reason)

    def test_initialize_best_state_falls_back_when_saved_reference_source_is_invalid(self):
        valid_source = (REPO_ROOT / "src/strategy_macd_aggressive.py").read_text()
        invalid_saved_source = valid_source.replace("def _flow_entry_ok(", "def _flow_entry_missing(", 1)
        report = EvaluationReport(
            metrics={"promotion_score": 0.31, "quality_score": 0.22},
            gate_passed=False,
            gate_reason="baseline",
            summary_text="summary",
            prompt_summary_text="prompt summary",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            temp_paths = replace(
                research_script.RUNTIME.paths,
                repo_root=temp_root,
                strategy_file=temp_root / "src/strategy_macd_aggressive.py",
                log_file=temp_root / "logs/research.log",
                journal_file=temp_root / "state/journal.jsonl",
                memory_dir=temp_root / "state/memory",
                heartbeat_file=temp_root / "state/heartbeat.json",
                best_state_file=temp_root / "state/best.json",
                best_strategy_file=temp_root / "backups/best.py",
                champion_strategy_file=temp_root / "backups/champion.py",
                stop_file=temp_root / "state/stop",
                strategy_backup_file=temp_root / "backups/candidate.py",
            )
            temp_runtime = replace(research_script.RUNTIME, paths=temp_paths)
            temp_paths.strategy_file.parent.mkdir(parents=True, exist_ok=True)
            temp_paths.best_strategy_file.parent.mkdir(parents=True, exist_ok=True)
            temp_paths.best_state_file.parent.mkdir(parents=True, exist_ok=True)
            temp_paths.strategy_file.write_text(valid_source)
            temp_paths.best_strategy_file.write_text(invalid_saved_source)
            temp_paths.best_state_file.write_text(
                json.dumps(
                    {
                        "score_regime": research_script.SCORE_REGIME,
                        "reference_role": "champion",
                        "reference": {"code_hash": "bad-source"},
                    },
                    ensure_ascii=False,
                )
            )

            with mock.patch.object(research_script, "RUNTIME", temp_runtime), mock.patch.object(
                research_script, "best_source", ""
            ), mock.patch.object(research_script, "best_report", None), mock.patch.object(
                research_script, "champion_report", None
            ), mock.patch.object(research_script, "reload_strategy_module"), mock.patch.object(
                research_script, "evaluate_current_strategy", return_value=report
            ) as evaluate_current_strategy, mock.patch.object(
                research_script, "maybe_send_discord"
            ), mock.patch.object(
                research_script, "build_discord_summary_message", return_value="msg"
            ), mock.patch.object(
                research_script, "write_heartbeat"
            ), mock.patch.object(
                research_script, "log_info"
            ) as log_info:
                research_script.initialize_best_state()
                self.assertEqual(research_script.best_source, valid_source)
                self.assertIs(research_script.best_report, report)
                self.assertIsNone(research_script.champion_report)

            self.assertEqual(temp_paths.best_strategy_file.read_text(), valid_source)
            self.assertEqual(evaluate_current_strategy.call_count, 1)
            log_messages = [call.args[0] for call in log_info.call_args_list if call.args]
            self.assertTrue(any("已保存主参考无效" in message for message in log_messages))


class DiscordSummaryFormattingTest(unittest.TestCase):
    def test_discord_summary_uses_comparable_eval_validation_rows(self):
        report = EvaluationReport(
            metrics={
                "development_mean_score": 0.55,
                "development_median_score": 0.49,
                "development_score_std": 0.12,
                "development_profitable_window_ratio": 0.62,
                "validation_trend_capture_score": 0.35,
                "validation_return_score": 0.81,
                "validation_arrival_capture_score": 0.12,
                "validation_escort_capture_score": 0.58,
                "validation_turn_adaptation_score": 0.33,
                "validation_bull_capture_score": 0.18,
                "validation_bear_capture_score": 0.46,
                "validation_segment_hit_rate": 0.41,
                "validation_major_segment_count": 10.0,
                "validation_long_closed_trades": 7.0,
                "validation_short_closed_trades": 6.0,
                "dev_validation_gap": 0.16,
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
                "selection_total_return_pct": 123.4,
                "selection_max_drawdown": 16.4,
                "selection_fee_drag_pct": 1.4,
                "selection_closed_trades": 123.0,
                "eval_sharpe_ratio": 1.11,
                "validation_sharpe_ratio": 0.56,
                "eval_avg_return": 1.23,
                "validation_total_return_pct": 34.5,
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
            test_window_count=1,
            data_range_text="train 2023-07-01~2024-12-31 / val 2025-01-01~2025-12-31 / test 2026-01-01~2026-03-31",
        )

        self.assertIn("数据范围", message)
        self.assertIn("本轮窗口", message)
        self.assertNotIn("因子模式", message)
        self.assertIn("train+val期间收益", message)
        self.assertIn("val期间收益", message)
        self.assertIn("Sharpe(train / val / test)", message)
        self.assertIn("1.11 / 0.56 / -", message)
        self.assertIn("train+val交易数量", message)
        self.assertIn("val多/空捕获", message)
        self.assertIn("train+val期间回撤/手续费拖累", message)
        self.assertIn("test 仅新 champion 时运行", message)
        self.assertNotIn("train滚动分", message)
        self.assertNotIn("val趋势/收益分", message)
        self.assertNotIn("train+val趋势/收益分", message)
        self.assertNotIn("test期间收益", message)
        self.assertNotIn("最大回撤/手续费拖累", message)

    def test_discord_summary_shows_test_drawdown_and_fee_when_test_metrics_present(self):
        report = EvaluationReport(
            metrics={
                "selection_total_return_pct": 12.3,
                "selection_closed_trades": 18.0,
                "validation_total_return_pct": 4.5,
                "eval_sharpe_ratio": 0.88,
                "validation_sharpe_ratio": 0.42,
                "validation_bull_capture_score": 0.05,
                "validation_bear_capture_score": 0.44,
                "selection_max_drawdown": 18.2,
                "selection_fee_drag_pct": 1.8,
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
            test_window_count=1,
            data_range_text="train 2023-07-01~2024-12-31 / val 2025-01-01~2025-12-31 / test 2026-01-01~2026-03-31",
            shadow_test_metrics={
                "shadow_test_total_return_pct": 3.21,
                "shadow_test_closed_trades": 9.0,
                "shadow_test_max_drawdown": 7.4,
                "shadow_test_fee_drag_pct": 0.6,
                "shadow_test_sharpe_ratio": 0.35,
            },
        )

        self.assertIn("test期间收益", message)
        self.assertIn("test交易数量", message)
        self.assertIn("test期间回撤/手续费拖累", message)
        self.assertIn("Sharpe(train / val / test)", message)
        self.assertIn("0.88 / 0.42 / 0.35", message)
        self.assertIn("7.40% / 0.60%", message)
        self.assertNotIn("因子准入模式", message)


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

    def test_render_performance_chart_writes_png_with_secondary_test_panel(self):
        if not charts_available():
            self.skipTest("matplotlib not installed for current interpreter")

        validation_curve = [
            {"date": "2026-01-01", "equity": 100000.0, "market_close": 50000.0},
            {"date": "2026-01-02", "equity": 103000.0, "market_close": 51000.0},
            {"date": "2026-01-03", "equity": 101000.0, "market_close": 50800.0},
            {"date": "2026-01-04", "equity": 108000.0, "market_close": 52000.0},
        ]
        test_curve = [
            {"date": "2026-02-01", "equity": 100000.0, "market_close": 52000.0},
            {"date": "2026-02-02", "equity": 98000.0, "market_close": 51500.0},
            {"date": "2026-02-03", "equity": 104000.0, "market_close": 53000.0},
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "chart_with_test.png"
            result = render_performance_chart(
                daily_equity_curve=validation_curve,
                output_path=output_path,
                title="Validation",
                subtitle="Val Window",
                secondary_daily_equity_curve=test_curve,
                secondary_title="Test",
                secondary_subtitle="Test Window",
            )

            self.assertEqual(result, output_path)
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
            self.assertEqual(output_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
