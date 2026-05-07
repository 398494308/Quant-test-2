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
import deepseek_planner_client as deepseek_planner_client
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
from research_v2.backtest_window_runtime import prepare_backtest_window_runtime
from research_v2.champion_artifacts import archive_champion_snapshot
from research_v2.charting import PerformanceChartPaths, charts_available, render_performance_chart
from research_v2.config import GateConfig, ScoringConfig
from research_v2.evaluation import (
    EvaluationReport,
    ValidationBlockReport,
    _annualized_return_score,
    _collect_daily_path,
    _collect_trend_path,
    _max_trade_idle_days_from_timestamps,
    _robustness_penalty_payload,
    _trade_activity_shortfall,
    _trade_idle_shortfall,
    _trend_score_report,
    partial_eval_gate_snapshot,
    summarize_evaluation,
)
from research_v2.journal import (
    ORDINARY_REGION_FAMILIES,
    append_journal_archive,
    build_direction_board_payload,
    build_exploration_guard_state,
    _format_compact_for_prompt,
    build_journal_prompt_summary,
    cluster_for_tags,
    cluster_key_for_components,
    cluster_key_for_entry,
    evaluate_candidate_failure_wiki_guard,
    evaluate_candidate_exploration_guard,
    format_direction_board_markdown,
    load_failure_wiki_index,
    ordinary_region_families,
    region_families_for_regions,
)
from research_v2.notifications import build_discord_summary_message
from research_v2.reference_state import load_saved_reference_state, persist_best_state
from research_v2.prompting import (
    EDITABLE_REGIONS,
    build_candidate_response_format_instructions,
    build_reviewer_response_format_instructions,
    build_strategy_candidate_summary_prompt,
    build_strategy_edit_worker_system_prompt,
    build_edit_completion_instructions,
    build_strategy_agents_instructions,
    build_strategy_edit_worker_prompt,
    build_strategy_exploration_repair_prompt,
    build_strategy_no_edit_repair_prompt,
    build_strategy_planner_system_prompt,
    build_strategy_repair_worker_system_prompt,
    build_strategy_round_brief_repair_prompt,
    build_strategy_reviewer_prompt,
    build_strategy_reviewer_repair_prompt,
    build_strategy_reviewer_system_prompt,
    build_strategy_research_prompt,
    build_strategy_summary_worker_system_prompt,
    build_strategy_runtime_repair_prompt,
)
from research_v2.round_artifacts import load_round_artifact_metadata, persist_round_artifact
from research_v2.strategy_code import (
    REQUIRED_FUNCTIONS,
    REQUIRED_TOP_LEVEL_CONSTANTS,
    StrategyCandidate,
    StrategyCoreFactor,
    StrategySourceError,
    build_strategy_complexity_pressure,
    build_strategy_complexity_delta,
    build_system_edit_signature,
    format_strategy_complexity_headroom,
    repair_editable_region_drift,
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
        "min_validation_closed_trades": 0,
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
        "normalize_entry_signal": "return ''",
        "strategy_decision": "return None",
        "strategy": "return None",
    }
    blocks = [
        "# PARAMS_START",
        "PARAMS = {'breakout_volume_ratio_min': 1.0}",
        "# PARAMS_END",
        "",
        "# EXIT_PARAMS_START",
        "EXIT_PARAMS = {'leverage': 20, 'position_fraction': 0.17, 'position_size_min': 5000, 'position_size_max': 30000, 'max_concurrent_positions': 4, 'pyramid_enabled': 1, 'pyramid_max_times': 2, 'pyramid_size_ratio': 0.28, 'tp1_pnl_pct': 65.7}",
        "# EXIT_PARAMS_END",
        "",
        "ENTRY_SIGNAL_ALIASES = {}",
        "ENTRY_PATH_TAGS = {}",
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

    def test_update_protective_stops_uses_signal_specific_break_even_buffer(self):
        position = {
            "entry_signal": "long_pullback",
            "entry_price": 100.0,
            "stop_price": 90.0,
            "peak_pnl_pct": 50.0,
            "favorable_price": 102.0,
        }
        exit_params = dict(backtest.EXIT_PARAMS)
        exit_params["break_even_activation_pct"] = 39.0
        exit_params["break_even_buffer_pct"] = 0.35
        exit_params["long_pullback_break_even_buffer_pct"] = 0.28
        exit_params["trailing_activation_pct"] = 999.0

        backtest._update_protective_stops(position, exit_params, leverage=20.0)

        self.assertAlmostEqual(position["stop_price"], 100.28)

    def test_closed_trade_rollup_treats_tp1_and_final_exit_as_one_trade(self):
        position = {
            "trade_id": 7,
            "entry_signal": "short_breakdown",
            "entry_timestamp": 1_700_000_000_000,
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
            "exit_timestamp": 1_700_000_900_000,
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
            "exit_timestamp": 1_700_001_800_000,
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
        self.assertEqual(closed_trade["entry_timestamp"], 1_700_000_000_000)
        self.assertEqual(closed_trade["exit_timestamp"], 1_700_001_800_000)

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

    def test_should_pyramid_accepts_long_pullback_execution_signal(self):
        position = {
            "entry_signal": "long_pullback",
            "pyramids_done": 0,
        }
        market_state = {
            "adx": 30.0,
            "macd_line": 2.0,
            "signal_line": 1.0,
            "hourly": {"close": 105.0, "ema_fast": 100.0},
        }
        exit_params = {
            "pyramid_enabled": 1,
            "pyramid_max_times": 2,
            "pyramid_trigger_pnl": 16.0,
            "pyramid_adx_min": 19.0,
        }

        allowed = backtest._should_pyramid(position, market_state, close_pnl_pct=18.0, exit_p=exit_params)

        self.assertTrue(allowed)


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
                    "daily_returns": [0.010, 0.018, -0.004],
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
                    "daily_returns": [0.012, 0.011, 0.006],
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
                    "daily_returns": [0.007, -0.003, 0.009],
                    "trades": 6,
                    "fee_drag_pct": 0.2,
                    "liquidations": 0,
                    "trend_capture_points": validation_points,
                },
            },
        ]
        gates = make_gate_config(
            min_development_mean_score=999.0,
            min_development_median_score=999.0,
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
        expected_eval_window_scores = [
            _trend_score_report(_collect_trend_path([results[0]], "eval").points),
            _trend_score_report(_collect_trend_path([results[1]], "eval").points),
        ]
        expected_validation_report = _trend_score_report(expected_validation_points)
        expected_full_period_report = _trend_score_report(full_period_result["trend_capture_points"])
        expected_train_timed_return_score = _annualized_return_score(_collect_daily_path(results, "eval").returns)
        expected_validation_timed_return_score = _annualized_return_score(
            _collect_daily_path(results, "validation").returns
        )

        self.assertAlmostEqual(
            report.metrics["eval_trend_capture_score"],
            sum(item.trend_score for item in expected_eval_window_scores) / len(expected_eval_window_scores),
        )
        expected_train_capture_equal_score = sum(
            detail.score for detail in expected_eval_report.segment_details
        ) / len(expected_eval_report.segment_details)
        expected_validation_capture_equal_score = sum(
            detail.score for detail in expected_validation_report.segment_details
        ) / len(expected_validation_report.segment_details)
        expected_train_capture_weighted_score = expected_eval_report.trend_score
        expected_validation_capture_weighted_score = expected_validation_report.trend_score
        expected_train_capture_score = (
            0.50 * expected_train_capture_equal_score + 0.50 * expected_train_capture_weighted_score
        )
        expected_validation_capture_score = (
            0.50 * expected_validation_capture_equal_score + 0.50 * expected_validation_capture_weighted_score
        )
        self.assertAlmostEqual(report.metrics["combined_trend_capture_score"], expected_full_period_report.trend_score)
        self.assertAlmostEqual(report.metrics["full_period_trend_capture_score"], expected_full_period_report.trend_score)
        self.assertAlmostEqual(report.metrics["train_capture_equal_score"], expected_train_capture_equal_score)
        self.assertAlmostEqual(report.metrics["validation_capture_equal_score"], expected_validation_capture_equal_score)
        self.assertAlmostEqual(report.metrics["train_capture_weighted_score"], expected_train_capture_weighted_score)
        self.assertAlmostEqual(
            report.metrics["validation_capture_weighted_score"],
            expected_validation_capture_weighted_score,
        )
        self.assertAlmostEqual(report.metrics["train_capture_score"], expected_train_capture_score)
        self.assertAlmostEqual(report.metrics["validation_capture_score"], expected_validation_capture_score)
        self.assertAlmostEqual(
            report.metrics["capture_score"],
            0.50 * (expected_train_capture_score + expected_validation_capture_score),
        )
        self.assertAlmostEqual(
            report.metrics["quality_score"],
            expected_train_capture_score,
        )
        self.assertAlmostEqual(
            report.metrics["validation_score"],
            0.70 * expected_validation_report.trend_score + 0.30 * expected_validation_report.return_score,
        )
        self.assertAlmostEqual(report.metrics["train_timed_return_score"], expected_train_timed_return_score)
        self.assertAlmostEqual(report.metrics["validation_timed_return_score"], expected_validation_timed_return_score)
        self.assertAlmostEqual(
            report.metrics["timed_return_score"],
            0.50 * (expected_train_timed_return_score + expected_validation_timed_return_score),
        )
        self.assertAlmostEqual(
            report.metrics["promotion_gap"],
            report.metrics["train_capture_score"] - report.metrics["validation_capture_score"],
        )
        expected_drawdown_penalty = (
            0.20 * report.metrics["drawdown_risk_score"]
            + 1.00 * max(report.metrics["drawdown_risk_score"] - 1.25, 0.0)
        )
        expected_promotion_score = (
            0.45 * report.metrics["capture_score"]
            + 0.30 * report.metrics["timed_return_score"]
            + 0.25 * report.metrics["sharpe_floor_score"]
            - expected_drawdown_penalty
            - report.metrics["robustness_penalty_score"]
            - report.metrics["trade_activity_penalty"]
        )
        self.assertAlmostEqual(report.metrics["drawdown_penalty_score"], expected_drawdown_penalty)
        self.assertAlmostEqual(report.metrics["promotion_score"], expected_promotion_score)
        self.assertIn("trade_activity_penalty", report.metrics)
        self.assertGreaterEqual(report.metrics["drawdown_risk_score"], 0.0)
        self.assertEqual(report.metrics["validation_block_count_used"], 0.0)
        self.assertEqual(report.metrics["eval_unique_trend_points"], 15.0)
        self.assertEqual(report.metrics["eval_overlap_trend_points"], 2.0)
        self.assertEqual(report.metrics["eval_overlap_trend_points_dropped"], 2.0)
        self.assertEqual(report.metrics["validation_overlap_trend_points"], 0.0)
        self.assertEqual(report.metrics["full_period_return_pct"], 12.34)
        self.assertAlmostEqual(report.metrics["combined_path_return_pct"], expected_full_period_report.path_return_pct)
        self.assertTrue(report.gate_passed)
        self.assertEqual(report.gate_reason, "通过")

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
        expected_drawdown_penalty = (
            0.20 * report.metrics["drawdown_risk_score"]
            + 1.00 * max(report.metrics["drawdown_risk_score"] - 1.25, 0.0)
        )
        self.assertAlmostEqual(report.metrics["drawdown_penalty_score"], expected_drawdown_penalty)
        self.assertAlmostEqual(
            report.metrics["promotion_score"],
            (
                0.45 * report.metrics["capture_score"]
                + 0.30 * report.metrics["timed_return_score"]
                + 0.25 * report.metrics["sharpe_floor_score"]
                - expected_drawdown_penalty
                - report.metrics["robustness_penalty_score"]
                - report.metrics["trade_activity_penalty"]
            ),
        )

    def test_trade_activity_shortfall_only_penalizes_below_floor(self):
        self.assertAlmostEqual(_trade_activity_shortfall(320, 270), 0.0)
        self.assertAlmostEqual(_trade_activity_shortfall(270, 270), 0.0)
        self.assertAlmostEqual(_trade_activity_shortfall(180, 270), 90.0 / 270.0)
        self.assertAlmostEqual(_trade_activity_shortfall(0, 180), 1.0)

    def test_trade_idle_shortfall_penalizes_long_no_entry_gap(self):
        day_ms = 24 * 60 * 60 * 1000
        start_ts = 0
        end_ts = 20 * day_ms
        entry_timestamps = [2 * day_ms, 5 * day_ms, 14 * day_ms]

        max_idle_days = _max_trade_idle_days_from_timestamps(
            entry_timestamps,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
        )

        self.assertAlmostEqual(max_idle_days, 9.0)
        self.assertAlmostEqual(_trade_idle_shortfall(max_idle_days, 7.0), 2.0 / 7.0)
        self.assertAlmostEqual(_trade_idle_shortfall(7.0, 7.0), 0.0)

    def test_summarize_evaluation_drawdown_risk_penalizes_persistent_underwater_path(self):
        scoring = ScoringConfig(
            risk_window_days=5,
            risk_window_step_days=2,
            drawdown_risk_tail_quantile=0.75,
            drawdown_risk_tail_weight=0.60,
            drawdown_risk_scale_pct=6.0,
        )
        eval_window = type("Window", (), {"group": "eval", "label": "train1", "start_date": "2026-01-01", "end_date": "2026-01-10"})()
        validation_window = type("Window", (), {"group": "validation", "label": "val1", "start_date": "2026-01-11", "end_date": "2026-01-20"})()
        base_eval_points = [
            {"timestamp": idx, "label": f"e{idx}", "market_close": 100.0 + idx, "atr_ratio": 0.01, "strategy_equity": 100000.0 + idx * 1200.0}
            for idx in range(1, 11)
        ]
        base_validation_points = [
            {"timestamp": idx + 100, "label": f"v{idx}", "market_close": 110.0 + idx, "atr_ratio": 0.01, "strategy_equity": 112000.0 + idx * 900.0}
            for idx in range(1, 11)
        ]

        def build_report(eval_daily_returns, validation_daily_returns):
            results = [
                {
                    "window": eval_window,
                    "result": {
                        "return": 8.0,
                        "max_drawdown": 6.0,
                        "daily_returns": eval_daily_returns,
                        "trades": 5,
                        "fee_drag_pct": 0.2,
                        "liquidations": 0,
                        "trend_capture_points": base_eval_points,
                    },
                },
                {
                    "window": validation_window,
                    "result": {
                        "return": 5.0,
                        "max_drawdown": 5.0,
                        "daily_returns": validation_daily_returns,
                        "trades": 4,
                        "fee_drag_pct": 0.2,
                        "liquidations": 0,
                        "trend_capture_points": base_validation_points,
                    },
                },
            ]
            return summarize_evaluation(
                results,
                make_gate_config(),
                selection_period_result={
                    "return": 13.0,
                    "max_drawdown": 7.0,
                    "daily_returns": list(eval_daily_returns) + list(validation_daily_returns),
                    "trend_capture_points": base_eval_points + base_validation_points,
                },
                validation_continuous_result={
                    "return": 5.0,
                    "max_drawdown": 5.0,
                    "daily_returns": validation_daily_returns,
                    "trend_capture_points": base_validation_points,
                },
                scoring=scoring,
            )

        fast_recovery_report = build_report(
            [0.03, 0.03, -0.12, 0.08, 0.06, 0.03, 0.03, 0.02, 0.01, 0.01],
            [0.02, 0.02, -0.08, 0.05, 0.04, 0.02, 0.02, 0.01, 0.01, 0.01],
        )
        persistent_underwater_report = build_report(
            [0.03, 0.03, -0.12, -0.04, -0.03, 0.01, 0.01, 0.00, 0.01, 0.01],
            [0.02, 0.02, -0.08, -0.03, -0.02, 0.00, 0.00, 0.01, 0.00, 0.01],
        )

        self.assertGreater(
            persistent_underwater_report.metrics["drawdown_risk_score"],
            fast_recovery_report.metrics["drawdown_risk_score"],
        )
        self.assertGreater(
            persistent_underwater_report.metrics["drawdown_penalty_score"],
            fast_recovery_report.metrics["drawdown_penalty_score"],
        )
        self.assertGreater(
            persistent_underwater_report.metrics["validation_window_ulcer_p75_pct"],
            fast_recovery_report.metrics["validation_window_ulcer_p75_pct"],
        )
        self.assertLess(
            persistent_underwater_report.metrics["promotion_score"],
            fast_recovery_report.metrics["promotion_score"],
        )

    def test_robustness_penalty_payload_caps_total_penalty_and_tracks_plateau(self):
        block_report = ValidationBlockReport(
            block_scores=(0.32, 0.18, -0.05),
            mean_score=0.15,
            std_score=0.25,
            min_score=-0.05,
            tail_score=-0.05,
            fail_count=1,
            used_block_count=3,
        )
        payload = _robustness_penalty_payload(
            promotion_gap=0.29,
            block_report=block_report,
            scoring=ScoringConfig(),
            plateau_probe={
                "enabled": True,
                "param": "stop_max_loss_pct",
                "values": [70.0, 82.0, 94.0],
                "current_value": 82.0,
                "best_value": 94.0,
                "center_period_score": 0.41,
                "best_period_score": 0.57,
                "center_gap": 0.16,
                "score_span": 0.14,
                "drawdown_span": 9.2,
                "current_is_best": False,
            },
        )

        self.assertAlmostEqual(payload["gap_penalty_score"], 0.10)
        self.assertAlmostEqual(payload["block_std_penalty_score"], 0.03)
        self.assertAlmostEqual(payload["block_floor_penalty_score"], 0.06)
        self.assertAlmostEqual(payload["block_tail_penalty_score"], 0.03)
        self.assertAlmostEqual(payload["block_fail_penalty_score"], 0.03)
        self.assertAlmostEqual(payload["sharpe_gap_penalty_score"], 0.0)
        self.assertAlmostEqual(payload["sharpe_floor_penalty_score"], 0.0)
        self.assertAlmostEqual(payload["plateau_penalty_score"], 0.15)
        self.assertAlmostEqual(payload["validation_block_tail_gap"], 0.20)
        self.assertAlmostEqual(payload["robustness_penalty_score_raw"], 0.40)
        self.assertAlmostEqual(payload["robustness_penalty_score"], 0.25)
        self.assertEqual(payload["plateau_probe_enabled"], 1.0)
        self.assertEqual(payload["plateau_current_is_best"], 0.0)

    def test_robustness_penalty_payload_adds_train_val_sharpe_balance_penalties(self):
        payload = _robustness_penalty_payload(
            promotion_gap=0.05,
            block_report=ValidationBlockReport(
                block_scores=(),
                mean_score=0.0,
                std_score=0.0,
                min_score=0.0,
                tail_score=0.0,
                fail_count=0,
                used_block_count=0,
            ),
            scoring=ScoringConfig(),
            train_sharpe_ratio=1.90,
            validation_sharpe_ratio=0.80,
        )

        self.assertAlmostEqual(payload["train_val_sharpe_gap"], 1.10)
        self.assertAlmostEqual(payload["train_val_sharpe_floor"], 0.80)
        self.assertAlmostEqual(payload["sharpe_gap_penalty_score"], 0.06)
        self.assertAlmostEqual(payload["sharpe_floor_penalty_score"], 0.03)

    def test_summarize_evaluation_emits_funnel_without_low_activity_soft_signal(self):
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
        self.assertNotIn("低活动度信号（软触发，不是硬 gate）", report.summary_text)
        self.assertNotIn("下一轮优先做放宽/删减/合并类假设", report.summary_text)
        self.assertNotIn("低活动度软触发=", report.prompt_summary_text)
        self.assertEqual(report.metrics["validation_long_path_pass"], 4.0)
        self.assertEqual(report.metrics["selection_long_final_veto_pass"], 0.0)
        self.assertEqual(report.metrics["low_activity_signal_count"], 0.0)

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
        self.assertIn("return_score", gate_snapshot)
        self.assertIn("period_score", gate_snapshot)


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
            "normalize_entry_signal": "return ''",
            "strategy_decision": "return None",
            "strategy": "return None",
        }
        if overrides:
            body_by_function.update(overrides)

        blocks = [
            "# PARAMS_START",
            "PARAMS = {'intraday_adx_min': 10}",
            "# PARAMS_END",
            "",
            "# EXIT_PARAMS_START",
            "EXIT_PARAMS = {'leverage': 20, 'position_fraction': 0.17, 'position_size_min': 5000, 'position_size_max': 30000, 'max_concurrent_positions': 4, 'pyramid_enabled': 1, 'pyramid_max_times': 2, 'pyramid_size_ratio': 0.28, 'tp1_pnl_pct': 65.7}",
            "# EXIT_PARAMS_END",
            "",
            "ENTRY_SIGNAL_ALIASES = {}",
            "ENTRY_PATH_TAGS = {}",
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
            elif function_name == "normalize_entry_signal":
                signature = "(signal, fallback_side='')"
            elif function_name in {"strategy_decision", "strategy"}:
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

    def test_validate_strategy_source_rejects_missing_contract_function(self):
        source = self._minimal_validation_source().replace(
            "def strategy_decision(data, idx, positions, market_state):\n    return None\n\n",
            "",
            1,
        )

        with self.assertRaisesRegex(StrategySourceError, r"strategy_decision"):
            validate_strategy_source(source)

    def test_validate_strategy_source_rejects_missing_contract_constant(self):
        source = self._minimal_validation_source().replace("ENTRY_PATH_TAGS = {}\n", "", 1)

        with self.assertRaisesRegex(StrategySourceError, r"ENTRY_PATH_TAGS"):
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
            "mild_long_pullback": False,
            "extreme_compression": False,
        }

        with mock.patch.object(strategy_module, "_sideways_release_flags", return_value=release_flags):
            result = strategy_module._is_sideways_regime(market_state)

        self.assertIsInstance(result, bool)

    def test_strategy_funnel_diagnostics_track_long_and_short_gate_passes(self):
        strategy_module.reset_funnel_diagnostics()

        strategy_context = {
            "ready": True,
            "current": {"open": 99.5, "high": 100.5, "close": 100.0, "volume": 100.0},
            "recent_volume_avg": 100.0,
        }
        with mock.patch.object(strategy_module, "_build_signal_context", return_value=strategy_context):
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

    def test_strategy_decision_returns_long_path_tag(self):
        strategy_context = {
            "ready": True,
            "current": {"open": 99.5, "high": 100.5, "close": 100.0, "volume": 100.0},
            "recent_volume_avg": 100.0,
        }
        with mock.patch.object(strategy_module, "_build_signal_context", return_value=strategy_context):
            with mock.patch.object(strategy_module, "_is_sideways_regime", return_value=False):
                with mock.patch.object(strategy_module, "long_outer_context_ok", return_value=True):
                    with mock.patch.object(strategy_module, "long_breakout_ok", return_value=True):
                        with mock.patch.object(strategy_module, "long_pullback_ok", return_value=False):
                            with mock.patch.object(strategy_module, "long_trend_reaccel_ok", return_value=False):
                                with mock.patch.object(strategy_module, "_flow_entry_ok", return_value=False):
                                    with mock.patch.object(strategy_module, "long_final_veto_clear", return_value=True):
                                        decision = strategy_module.strategy_decision(
                                            [{}] * (strategy_module.PARAMS["min_history"] + 1),
                                            strategy_module.PARAMS["min_history"],
                                            [],
                                            {},
                                        )

        self.assertEqual(decision["entry_signal"], "long_pullback")
        self.assertEqual(decision["entry_path_key"], "long_breakout")
        self.assertEqual(decision["entry_path_tag"], "long_impulse")
        self.assertEqual(strategy_module.normalize_entry_signal(decision["entry_path_tag"]), "long_pullback")

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

ENTRY_SIGNAL_ALIASES = {}
ENTRY_PATH_TAGS = {}

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

def normalize_entry_signal(signal, fallback_side=''):
    return ''

def strategy_decision(data, idx, positions, market_state):
    return None

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

    def test_validate_strategy_source_rejects_new_param_key(self):
        base_source = self._minimal_validation_source()
        source = base_source.replace(
            "PARAMS = {'intraday_adx_min': 10}",
            "PARAMS = {'intraday_adx_min': 10, 'new_factor_gate': 1.0}",
            1,
        )

        with self.assertRaisesRegex(StrategySourceError, "new PARAMS keys are not allowed"):
            validate_strategy_source(source, base_source=base_source)

    def test_validate_strategy_source_allows_exit_param_change(self):
        base_source = self._minimal_validation_source()
        source = base_source.replace("'tp1_pnl_pct': 65.7", "'tp1_pnl_pct': 70.0")

        validate_strategy_source(source, base_source=base_source)

    def test_validate_strategy_source_rejects_fixed_exit_param_change(self):
        base_source = self._minimal_validation_source()
        source = base_source.replace("'leverage': 20", "'leverage': 21")

        with self.assertRaisesRegex(StrategySourceError, "fixed EXIT_PARAMS key leverage"):
            validate_strategy_source(source, base_source=base_source)

    def test_validate_strategy_source_keeps_default_mode_complexity_growth_as_warning_only(self):
        base_source = self._minimal_validation_source()
        expanded_body = "\n    ".join(
            [*(f"x_{index} = {index}" for index in range(48)), "return None"]
        )
        source = self._minimal_validation_source({"strategy": expanded_body})

        validate_strategy_source(source, base_source=base_source)
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

    def test_validate_editable_boundaries_no_longer_rejects_full_file_edits(self):
        base_source = """
# PARAMS_START
PARAMS = {'intraday_adx_min': 10, 'hourly_adx_min': 10, 'fourh_adx_min': 10, 'breakout_adx_min': 10, 'breakdown_adx_min': 10, 'breakout_lookback': 10, 'breakdown_lookback': 10, 'breakout_rsi_min': 40, 'breakout_rsi_max': 60, 'breakdown_rsi_min': 20, 'breakdown_rsi_max': 60, 'breakout_volume_ratio_min': 1.0, 'breakdown_volume_ratio_min': 1.0, 'breakout_body_ratio_min': 0.3, 'breakdown_body_ratio_min': 0.3, 'breakout_close_pos_min': 0.5, 'breakdown_close_pos_max': 0.5, 'intraday_ema_fast': 9, 'intraday_ema_slow': 20, 'hourly_ema_fast': 10, 'hourly_ema_slow': 20, 'fourh_ema_fast': 10, 'fourh_ema_slow': 20, 'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9, 'volume_lookback': 10}
# PARAMS_END

ENTRY_SIGNAL_ALIASES = {}
ENTRY_PATH_TAGS = {}

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

        validate_editable_region_boundaries(base_source, candidate_source, EDITABLE_REGIONS)

    def test_validate_editable_boundaries_allows_strategy_change(self):
        base_source = """
# PARAMS_START
PARAMS = {'intraday_adx_min': 10, 'hourly_adx_min': 10, 'fourh_adx_min': 10, 'breakout_adx_min': 10, 'breakdown_adx_min': 10, 'breakout_lookback': 10, 'breakdown_lookback': 10, 'breakout_rsi_min': 40, 'breakout_rsi_max': 60, 'breakdown_rsi_min': 20, 'breakdown_rsi_max': 60, 'breakout_volume_ratio_min': 1.0, 'breakdown_volume_ratio_min': 1.0, 'breakout_body_ratio_min': 0.3, 'breakdown_body_ratio_min': 0.3, 'breakout_close_pos_min': 0.5, 'breakdown_close_pos_max': 0.5, 'intraday_ema_fast': 9, 'intraday_ema_slow': 20, 'hourly_ema_fast': 10, 'hourly_ema_slow': 20, 'fourh_ema_fast': 10, 'fourh_ema_slow': 20, 'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9, 'volume_lookback': 10}
# PARAMS_END

ENTRY_SIGNAL_ALIASES = {}
ENTRY_PATH_TAGS = {}

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

ENTRY_SIGNAL_ALIASES = {}
ENTRY_PATH_TAGS = {}

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

def normalize_entry_signal(signal, fallback_side=''):
    return ''

def strategy_decision(data, idx, positions, market_state):
    return None

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

    def test_repair_editable_region_drift_becomes_noop_when_whole_file_is_editable(self):
        base_source = """
# PARAMS_START
PARAMS = {'intraday_adx_min': 10}
# PARAMS_END

ENTRY_SIGNAL_ALIASES = {}
ENTRY_PATH_TAGS = {}

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

def normalize_entry_signal(signal, fallback_side=''):
    return ''

def strategy_decision(data, idx, positions, market_state):
    return None

def strategy(*args, **kwargs):
    return helper()
"""
        candidate_source = (
            base_source
            .replace("return 1", "return 2", 1)
            .replace("return helper()", "return None", 1)
        )

        repaired_source, repaired = repair_editable_region_drift(
            base_source,
            candidate_source,
            EDITABLE_REGIONS,
        )

        self.assertFalse(repaired)
        self.assertIn("def helper():\n    return 2", repaired_source)
        self.assertIn("def strategy(*args, **kwargs):\n    return None", repaired_source)
        validate_editable_region_boundaries(base_source, repaired_source, EDITABLE_REGIONS)


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
            "normalize_entry_signal": "return ''",
            "strategy_decision": "return None",
            "strategy": "return None",
        }
        blocks = [
            "# PARAMS_START",
            "PARAMS = {'breakout_volume_ratio_min': 1.0}",
            "# PARAMS_END",
            "",
            "ENTRY_SIGNAL_ALIASES = {}",
            "ENTRY_PATH_TAGS = {}",
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
        self.assertIn("wiki/reviewer_summary_card.md", prompt)
        self.assertIn("wiki/direction_board.md", prompt)
        self.assertIn("wiki/duplicate_watchlist.md", prompt)
        self.assertIn("wiki/failure_wiki.md", prompt)
        self.assertIn("先想再写", prompt)
        self.assertIn("不要 hard code", prompt)
        self.assertIn("整份文件都允许修改", prompt)

    def test_build_strategy_runtime_prompt_mentions_refresh_rule(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
        )

        self.assertIn("过 `gate` 即可刷新 active reference", prompt)
        self.assertIn("0.45 / 0.30 / 0.25", prompt)
        self.assertIn("按日收益年化补分", prompt)
        self.assertIn("Sharpe floor", prompt)
        self.assertIn("分段回撤惩罚", prompt)
        self.assertIn("鲁棒性软惩罚", prompt)
        self.assertIn("低频 + 长空窗", prompt)
        self.assertIn("train 180-270 / val 120-180", prompt)
        self.assertIn("最长无新开仓", prompt)
        self.assertIn("负分块最多 3 个", prompt)
        self.assertIn("默认优先找更稳的平台", prompt)
        self.assertNotIn("promotion_delta >", prompt)
        self.assertIn("当前回合任务", prompt)
        self.assertIn("卡片摘要（已展开，不需要再假设自己能读本地文件）", prompt)
        self.assertIn("reviewer_summary_card.md", prompt)
        self.assertIn("direction_board.md", prompt)
        self.assertIn("duplicate_watchlist.md", prompt)
        self.assertIn("failure_wiki.md", prompt)
        self.assertIn("方向与历史摘要来源", prompt)
        self.assertIn("先判断上一版为什么失败，再决定继续还是转向", prompt)
        self.assertIn("继续还是转向", prompt)
        self.assertIn("若最近连续 `behavioral_noop` 或结果盆地重复", prompt)
        self.assertIn("如果本轮主要改 `EXIT_PARAMS` 里的连续数值", prompt)
        self.assertNotIn("当前因子模式", prompt)
        self.assertIn("本轮硬完成条件", prompt)
        self.assertIn("round brief", prompt)
        self.assertIn("change_plan", prompt)
        self.assertIn("novelty_proof", prompt)
        self.assertIn("draft", prompt)

    def test_build_strategy_runtime_prompt_can_include_reviewer_summary_card(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            reviewer_summary_text="# Reviewer Summary Card\n- verdict: REVISE\n- must_change: 换机制层",
            reviewer_summary_path="wiki/reviewer_summary_card.md",
        )

        self.assertIn("上一轮 reviewer 摘要", prompt)
        self.assertIn("wiki/reviewer_summary_card.md", prompt)
        self.assertIn("换机制层", prompt)

    def test_build_strategy_runtime_prompt_can_include_operator_focus(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            operator_focus_text="- 优先检查多头外层 choke point\n- 降权同义 gate 堆叠",
            operator_focus_path="config/research_v2_operator_focus.md",
        )

        self.assertIn("人工方向卡摘要", prompt)
        self.assertIn("config/research_v2_operator_focus.md", prompt)
        self.assertIn("优先检查多头外层 choke point", prompt)

    def test_build_strategy_runtime_prompt_can_include_champion_review_card(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            champion_review_text="champion_code_hash: " + "a" * 64 + "\n- 退出过晚，利润回吐偏大",
            champion_review_path="config/research_v2_champion_review.md",
            champion_review_code_hash="a" * 64,
        )

        self.assertIn("当前 champion 人工观察摘要", prompt)
        self.assertIn("config/research_v2_champion_review.md", prompt)
        self.assertIn("退出过晚", prompt)
        self.assertIn("绑定 champion hash", prompt)

    def test_load_champion_review_text_requires_matching_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            card = temp_root / "review.md"
            matching_hash = "b" * 64
            stale_hash = "c" * 64
            card.write_text(f"champion_code_hash: {matching_hash}\n\n- 人工观察")
            temp_paths = replace(research_script.RUNTIME.paths, champion_review_file=card)
            temp_runtime = replace(research_script.RUNTIME, paths=temp_paths)

            with mock.patch.object(research_script, "RUNTIME", temp_runtime):
                self.assertIn(
                    "人工观察",
                    research_script._load_champion_review_text(active_code_hash=matching_hash),
                )
                self.assertEqual(
                    research_script._load_champion_review_text(active_code_hash=stale_hash),
                    "",
                )

    def test_build_strategy_runtime_prompt_softens_side_bias_when_hit_rate_is_weak(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            reference_metrics={
                "validation_bull_capture_score": 0.06,
                "validation_bear_capture_score": 0.28,
                "validation_segment_hit_rate": 0.22,
            },
        )

        self.assertIn("整体命中率也偏低", prompt)
        self.assertIn("不要自动锁定为单纯补 long", prompt)

    def test_build_strategy_agents_instructions_mentions_all_required_symbols(self):
        prompt = build_strategy_agents_instructions()

        for function_name in REQUIRED_FUNCTIONS:
            self.assertIn(f"`{function_name}()`", prompt)

    def test_build_strategy_agents_instructions_mentions_ordinary_family_budget(self):
        prompt = build_strategy_agents_instructions()

        self.assertIn("整份文件都允许修改", prompt)
        self.assertIn("真实 diff 自动归类 region / family", prompt)
        self.assertIn("不允许新增 `PARAMS` 键", prompt)

    def test_build_strategy_agents_instructions_mentions_delete_first_rule(self):
        prompt = build_strategy_agents_instructions()

        self.assertIn("默认先做删减、合并、替换", prompt)
        self.assertIn("不要继续叠分叉", prompt)
        self.assertIn("不要“堆屎”", prompt)
        self.assertIn("不要换个名字再写一份重复条件", prompt)
        self.assertIn("2 核 8G", prompt)

    def test_build_strategy_planner_system_prompt_mentions_agents_md_and_text_contract(self):
        prompt = build_strategy_planner_system_prompt()

        self.assertIn("AGENTS.md", prompt)
        self.assertIn("纯文本候选摘要", prompt)
        self.assertIn("未读到文件", prompt)
        self.assertIn("你只能产出 round brief", prompt)

    def test_build_candidate_response_format_instructions_mentions_system_derived_regions(self):
        prompt = build_candidate_response_format_instructions()

        self.assertIn("不要输出 `edited_regions`", prompt)
        self.assertIn("真实 diff", prompt)

    def test_build_reviewer_response_format_instructions_mentions_pass_or_revise(self):
        prompt = build_reviewer_response_format_instructions()

        self.assertIn("verdict:", prompt)
        self.assertIn("PASS", prompt)
        self.assertIn("REVISE", prompt)
        self.assertIn("不能替 planner 发明新方向", prompt)

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
            primary_direction="long | widen outer context",
            hypothesis="放宽多头 outer_context，让内层路径真正触达到出单层。",
            change_plan="在 long_outer_context_ok 和 long_final_veto_clear 上删旧 gate，不新增平行 path。",
            change_tags=("widen_outer_context", "merge_veto"),
            expected_effects=("提高多头到来与陪跑捕获",),
            closest_failed_cluster="participation_cluster",
            novelty_proof="这次直接改最终可达性，不再停留在内层 helper 微调。",
            current_complexity_headroom_text="当前基底复杂度余量：trend_quality_family bool_ops 剩 4",
            evaluation_digest_text="- gate: 通过\n- 最弱维度: val陪跑=0.12\n- val多/空捕获=0.20/0.40，命中率=38%",
        )

        self.assertIn("round brief", prompt)
        self.assertIn("只修改 `src/strategy_macd_aggressive.py`", prompt)
        self.assertIn("整份策略文件都允许修改", prompt)
        self.assertIn("当前紧凑诊断", prompt)
        self.assertIn("最弱维度: val陪跑=0.12", prompt)
        self.assertIn("单轮改动预算只是参考，不是硬 gate", prompt)
        self.assertIn("只回复 `EDIT_DONE`", prompt)
        self.assertNotIn("当前基底复杂度余量", prompt)

    def test_build_strategy_summary_worker_system_prompt_mentions_no_edit_summary_role(self):
        prompt = build_strategy_summary_worker_system_prompt()

        self.assertIn("summary_worker", prompt)
        self.assertIn("不要修改任何文件", prompt)
        self.assertIn("回写最终候选摘要", prompt)

    def test_build_strategy_candidate_summary_prompt_mentions_final_code_alignment(self):
        prompt = build_strategy_candidate_summary_prompt(
            candidate_id="candidate_1",
            primary_direction="long | final veto rewrite",
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

    def test_build_strategy_edit_worker_system_prompt_mentions_short_lived_worker_role(self):
        prompt = build_strategy_edit_worker_system_prompt()

        self.assertIn("短生命周期 `edit_worker`", prompt)
        self.assertIn("不要重新做全量历史研究", prompt)
        self.assertIn("只根据当前提示里的 round brief", prompt)
        self.assertIn("完成编辑后只回复 `EDIT_DONE`", prompt)

    def test_build_strategy_repair_worker_system_prompt_mentions_repair_only_role(self):
        prompt = build_strategy_repair_worker_system_prompt()

        self.assertIn("短生命周期 `repair_worker`", prompt)
        self.assertIn("修技术错误、校验错误或 no-edit 问题", prompt)
        self.assertIn("不要重写研究方向", prompt)

    def test_build_strategy_reviewer_system_prompt_mentions_review_only_role(self):
        prompt = build_strategy_reviewer_system_prompt()

        self.assertIn("短生命周期 `reviewer`", prompt)
        self.assertIn("只能做两种结论：`PASS` 或 `REVISE`", prompt)
        self.assertIn("不能替 planner 发明新方向", prompt)

    def test_build_strategy_reviewer_prompt_mentions_evidence_and_boundaries(self):
        prompt = build_strategy_reviewer_prompt(
            evaluation_summary="当前主参考多头偏弱",
            journal_summary="当前 stage 执行摘要\n- 当前过热近邻/慎入区: 簇=ownership_cluster",
            round_brief_text="- candidate_id: c1\n- hypothesis: 继续修 routing",
        )

        self.assertIn("当前 draft round brief", prompt)
        self.assertIn("高信号证据包", prompt)
        self.assertIn("direction_board.md", prompt)
        self.assertIn("duplicate_watchlist.md", prompt)
        self.assertIn("failure_wiki.md", prompt)
        self.assertIn("不能替 planner 发明新方向", prompt)
        self.assertIn("预计新增、删除或迁移哪类真实交易", prompt)

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

    def test_build_strategy_reviewer_repair_prompt_mentions_required_fields(self):
        prompt = build_strategy_reviewer_repair_prompt(
            retry_attempt=1,
            invalid_reason="reviewer decision missing required fields: verdict, reviewer_summary",
            raw_response_excerpt="我觉得这版还是不太好，建议换方向。",
        )

        self.assertIn("reviewer 审稿结果无效", prompt)
        self.assertIn("PASS", prompt)
        self.assertIn("REVISE", prompt)
        self.assertIn("不要替 planner 生成新方向", prompt)

    def test_build_strategy_prompts_include_precise_complexity_budgets(self):
        system_prompt = build_strategy_agents_instructions()
        runtime_prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
        )

        self.assertNotIn("复杂度硬上限（超了直接拒收）", runtime_prompt)
        self.assertNotIn("复杂度硬上限（超了直接拒收）", system_prompt)
        self.assertNotIn("当前基底复杂度余量", runtime_prompt)
        self.assertIn("删旧、并旧、改旧", system_prompt)

    def test_build_strategy_agents_instructions_keeps_role_boundaries_out_of_shared_layer(self):
        prompt = build_strategy_agents_instructions()

        self.assertIn("工作区文件职责", prompt)
        self.assertNotIn("它只能 `PASS` 或 `REVISE`", prompt)
        self.assertNotIn("完成编辑后只回复 `EDIT_DONE`", prompt)

    def test_build_strategy_research_prompt_can_include_current_complexity_headroom(self):
        headroom_text = "当前基底复杂度余量（剩余越小越容易再次撞复杂度）:\n- family `trend_quality_family`: lines 剩 8, bool_ops 剩 0, ifs 剩 3"
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            current_complexity_headroom_text=headroom_text,
        )

        self.assertNotIn("当前基底复杂度余量", prompt)
        self.assertNotIn("trend_quality_family", prompt)

    def test_build_strategy_research_prompt_uses_active_reference_label(self):
        prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
            benchmark_label="champion",
            current_base_role="baseline",
        )

        self.assertIn("当前 active reference 角色：`baseline`", prompt)

    def test_build_strategy_runtime_repair_prompt_mentions_complexity_shrink_rule(self):
        prompt = build_strategy_runtime_repair_prompt(
            candidate_id="candidate_1",
            primary_direction="long | ownership routing",
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
            primary_direction="long | ownership routing",
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
            primary_direction="long | ownership routing",
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
            primary_direction="long | ownership routing",
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
        self.assertIn("刚才被拒的候选只作反例参考", prompt)
        self.assertIn("从当前正确基底重新修改", prompt)
        self.assertIn("wiki/last_rejected_snapshot.md", prompt)
        self.assertIn("wiki/last_rejected_candidate.py", prompt)

    def test_strategy_prompts_forbid_blocked_no_edit_placeholder_results(self):
        runtime_prompt = build_strategy_research_prompt(
            evaluation_summary="诊断",
            journal_summary="记忆",
            previous_best_score=1.23,
        )
        planner_prompt = build_strategy_planner_system_prompt()
        exploration_prompt = build_strategy_exploration_repair_prompt(
            candidate_id="candidate_1",
            primary_direction="long | ownership routing",
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

        for prompt in (runtime_prompt, planner_prompt):
            self.assertIn("no_edit", prompt)
            self.assertIn("未执行代码改动", prompt)
        self.assertIn("候选未产生真实代码改动", exploration_prompt)
        self.assertIn("从当前正确基底重新修改", exploration_prompt)

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
                "primary_direction": "long | final routing",
                "hypothesis": "只改最终入场",
                "change_plan": "调整 strategy",
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
                "primary_direction": "mixed | four family sweep",
                "hypothesis": "一次覆盖四个普通 family",
                "change_plan": "同时调整横盘、流量、质量和入场路径",
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
primary_direction: long | final veto rewrite
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

        with self.assertRaisesRegex(StrategySourceError, "primary_direction, novelty_proof, change_tags"):
            research_script._validate_round_brief_payload(payload)

    def test_round_brief_from_payload_rejects_missing_core_fields(self):
        payload = research_script._parse_model_candidate_payload(
            """
candidate_id: candidate_bad
hypothesis: 继续优化多头
change_plan: 放宽 long_outer_context_ok
"""
        )

        with self.assertRaisesRegex(StrategySourceError, "primary_direction, novelty_proof, change_tags"):
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
                    "primary_direction": "structure | route shift",
                    "hypothesis": "改最终路由",
                    "change_plan": "只动 strategy",
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
            primary_direction="long | original brief",
            hypothesis="原 brief 假设",
            change_plan="原 brief 计划",
            novelty_proof="原 brief novelty",
            change_tags=("long",),
            expected_effects=("原 effect",),
            core_factors=tuple(),
        )
        summary_brief = research_script.StrategyRoundBrief(
            candidate_id="candidate_should_not_override",
            primary_direction="structure | route shift",
            hypothesis="最终代码实际是在改 strategy 最终路由",
            change_plan="按最终 diff 重写 strategy 路由说明",
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
        self.assertIn("方向账本摘要", summary)


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

    def test_core_signal_decision_falls_back_when_strategy_contract_helpers_are_missing(self):
        original_core = ft_adapter.core_strategy

        class LegacyCore:
            strategy = staticmethod(lambda ohlcv, idx, positions, market_state: "long_breakout")

        try:
            ft_adapter.core_strategy = LegacyCore()
            signal, path_tag = ft_adapter._core_signal_decision([], 0, {})
        finally:
            ft_adapter.core_strategy = original_core

        self.assertEqual(signal, "long_pullback")
        self.assertEqual(path_tag, "long_pullback")

    def test_apply_entry_logic_uses_path_tag_as_enter_tag(self):
        frame = pd.DataFrame(
            [
                {
                    "timestamp": 0,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 10.0,
                    "trade_count": 100.0,
                    "taker_buy_volume": 6.0,
                    "taker_sell_volume": 4.0,
                },
                {
                    "timestamp": 900_000,
                    "open": 100.5,
                    "high": 101.5,
                    "low": 100.0,
                    "close": 101.0,
                    "volume": 11.0,
                    "trade_count": 110.0,
                    "taker_buy_volume": 6.4,
                    "taker_sell_volume": 4.6,
                },
                {
                    "timestamp": 1_800_000,
                    "open": 101.0,
                    "high": 101.2,
                    "low": 99.8,
                    "close": 100.0,
                    "volume": 12.0,
                    "trade_count": 120.0,
                    "taker_buy_volume": 5.0,
                    "taker_sell_volume": 7.0,
                },
            ]
        )

        with mock.patch.object(
            ft_adapter,
            "_core_signal_decision",
            side_effect=[("long_pullback", "long_impulse"), (None, None), ("short_breakdown", "short_reaccel")],
        ):
            signal_frame = ft_adapter.apply_entry_logic(frame)

        self.assertEqual(int(signal_frame.loc[0, "enter_long"]), 1)
        self.assertEqual(signal_frame.loc[0, "enter_tag"], "long_impulse")
        self.assertEqual(int(signal_frame.loc[2, "enter_short"]), 1)
        self.assertEqual(signal_frame.loc[2, "enter_tag"], "short_reaccel")

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
        self.assertIn("方向账本摘要", summary)
        self.assertIn("ownership_cluster", summary)
        self.assertIn("HOT", summary)

    def test_journal_summary_can_emit_compact_prompt_package(self):
        entries = []
        for idx in range(3):
            entries.append(
                {
                    "iteration": idx + 1,
                    "candidate_id": f"candidate_compact_{idx}",
                    "outcome": "rejected",
                    "stop_stage": "full_eval",
                    "promotion_score": 0.11,
                    "quality_score": 0.09,
                    "promotion_delta": 0.0,
                    "gate_reason": "相对当前champion晋级分提升不足(-0.03 <= 0.02)",
                    "decision_reason": "相对当前champion晋级分提升不足(-0.03 <= 0.02)",
                    "change_tags": ["long_flow_prune", "nonstrong_long"],
                    "edited_regions": ["_flow_confirmation_ok"],
                    "hypothesis": "压缩多头 flow admission",
                    "score_regime": "trend_capture_v1",
                    "target_family": "long",
                    "system_ordinary_region_families": ["flow"],
                }
            )

        summary = build_journal_prompt_summary(entries, limit=8, prompt_compact=True)

        self.assertIn("当前 stage 执行摘要", summary)
        self.assertIn("方向账本摘要", summary)
        self.assertIn("最近轮次元信息（精简）", summary)
        self.assertNotIn("当前 stage 核心指标表", summary)
        self.assertNotIn("全局方向统计", summary)
        self.assertNotIn("过拟合风险表", summary)

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

        direction_index = summary.index("方向账本摘要")
        overfit_index = summary.index("过拟合风险表")
        self.assertLess(direction_index, overfit_index)
        self.assertIn("| 7 | 保留 | 0.72 | 高 | 48 |", summary)
        self.assertIn("谨慎参考", summary)

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

    def test_active_research_session_id_returns_saved_session_when_scope_matches(self):
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
                active_session_id = research_script._active_research_session_id()

            self.assertEqual(active_session_id, "session-123")

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

        self.assertIn("方向账本摘要", summary)
        self.assertIn("等待新历史", summary)
        self.assertIn("旧评分口径弱参考", summary)
        self.assertIn("trend_capture_v2", summary)
        self.assertNotIn("| 1 | old_regime |", summary)

    def test_journal_summary_keeps_empty_table_after_reset(self):
        summary = build_journal_prompt_summary([], limit=8)

        self.assertIn("方向账本摘要", summary)
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
            self.assertTrue((memory_root / "wiki/direction_board.md").exists())
            self.assertTrue((memory_root / "wiki/direction_board.json").exists())
            self.assertIn("blocked_cuts", load_failure_wiki_index(memory_root))

    def test_journal_summary_memory_snapshots_strip_test_observation_fields(self):
        entries = [
            {
                "iteration": 5,
                "candidate_id": "current_stage_candidate",
                "outcome": "rejected",
                "stop_stage": "full_eval",
                "promotion_score": 0.12,
                "quality_score": 0.21,
                "promotion_delta": -0.03,
                "gate_reason": "未超过当前champion晋级分",
                "decision_reason": "未超过当前champion晋级分",
                "change_tags": ["ownership_takeover"],
                "edited_regions": ["strategy"],
                "system_changed_regions": ["strategy"],
                "hypothesis": "当前 stage",
                "score_regime": "trend_capture_v4",
                "test_metrics": {
                    "test_score": 0.91,
                    "test_sharpe_ratio": 0.55,
                },
                "test_evaluation": {
                    "status": "completed",
                    "mode": "rejected_async",
                },
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

            current_stage_payload = json.loads(
                (memory_root / "summaries/current_stage_rounds.json").read_text()
            )
            serialized = json.dumps(current_stage_payload, ensure_ascii=False)
            self.assertNotIn("test_metrics", serialized)
            self.assertNotIn("test_evaluation", serialized)
            self.assertNotIn("test_score", serialized)

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

    def test_direction_board_marks_repeated_noop_pattern(self):
        entries = [
            {
                "iteration": 1,
                "candidate_id": "champion_ref",
                "outcome": "accepted",
                "stop_stage": "full_eval",
                "promotion_score": 0.20,
                "quality_score": 0.20,
                "promotion_delta": 0.03,
                "gate_reason": "通过",
                "decision_reason": "通过",
                "code_hash": "ref_hash_alpha",
                "change_tags": ["champion_refresh"],
                "edited_regions": ["strategy"],
                "system_changed_regions": ["strategy"],
                "hypothesis": "新 champion",
                "target_family": "mixed",
                "closest_failed_cluster": "ownership_cluster",
                "metrics": {},
                "score_regime": "trend_capture_v6",
            },
            {
                "iteration": 2,
                "candidate_id": "cand_noop_1",
                "outcome": "behavioral_noop",
                "stop_stage": "behavioral_noop",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                "decision_reason": "smoke 行为指纹与当前主参考完全一致",
                "code_hash": "cand_hash_1",
                "reference_code_hash": "ref_hash_alpha",
                "change_tags": ["long_routing", "veto_rewire"],
                "edited_regions": ["long_final_veto_clear"],
                "system_changed_regions": ["long_final_veto_clear"],
                "system_ordinary_changed_regions": ["long_final_veto_clear"],
                "system_ordinary_region_families": ["entry_path"],
                "hypothesis": "第一次 noop",
                "target_family": "long",
                "closest_failed_cluster": "ownership_cluster",
                "metrics": {},
                "score_regime": "trend_capture_v6",
            },
            {
                "iteration": 3,
                "candidate_id": "cand_noop_2",
                "outcome": "behavioral_noop",
                "stop_stage": "behavioral_noop",
                "promotion_score": None,
                "quality_score": None,
                "promotion_delta": None,
                "gate_reason": "smoke 行为指纹与当前主参考完全一致",
                "decision_reason": "smoke 行为指纹与当前主参考完全一致",
                "code_hash": "cand_hash_2",
                "reference_code_hash": "ref_hash_alpha",
                "change_tags": ["long_routing", "relay_split"],
                "edited_regions": ["long_final_veto_clear"],
                "system_changed_regions": ["long_final_veto_clear"],
                "system_ordinary_changed_regions": ["long_final_veto_clear"],
                "system_ordinary_region_families": ["entry_path"],
                "hypothesis": "第二次 noop",
                "target_family": "long",
                "closest_failed_cluster": "ownership_cluster",
                "metrics": {},
                "score_regime": "trend_capture_v6",
            },
        ]

        payload = build_direction_board_payload(
            entries,
            score_regime="trend_capture_v6",
            active_reference_code_hash="ref_hash_alpha",
        )
        markdown = format_direction_board_markdown(payload)

        self.assertEqual(payload["scope_entry_count"], 2)
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual(payload["items"][0]["level"], "WARM")
        self.assertIn("Direction Board", markdown)
        self.assertIn("WARM", markdown)
        self.assertIn("long | 历史遗留方向", markdown)
        self.assertIn("behavioral_noop", markdown)

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

        self.assertIn("方向账本摘要", summary)
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

        self.assertIn("方向账本摘要", summary)
        self.assertIn("当前复盘优先级", summary)
        self.assertIn("当前过热近邻/慎入区", summary)
        self.assertIn("trigger_efficiency_cluster", summary)
        self.assertIn("探索触发（必须执行）", summary)
        self.assertNotIn("ACTIVE_WINNER", summary)
        self.assertIn("HOT", summary)

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
        self.assertIn("运行失败 1", summary)
        self.assertIn("complexity family", summary)


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

    def test_load_deepseek_planner_config_defaults_to_compact_history_window(self):
        with mock.patch.dict(deepseek_planner_client.os.environ, {}, clear=True):
            config = deepseek_planner_client.load_deepseek_planner_config()

        self.assertEqual(config.max_history_messages, 12)

    def test_deepseek_planner_generate_text_response_keeps_reasoning_only_in_trace(self):
        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "new answer",
                                "reasoning_content": "new reasoning",
                            }
                        }
                    ]
                }

        config = deepseek_planner_client.DeepSeekPlannerConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-pro",
            thinking_type="enabled",
            reasoning_effort="max",
            timeout_seconds=30,
            max_history_messages=12,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            session_id = "session-123"
            history_path = workspace_root / ".deepseek_planner_session_session-123.json"
            history_path.write_text(
                json.dumps(
                    [
                        {"role": "system", "content": "old system"},
                        {"role": "user", "content": "old prompt"},
                        {"role": "assistant", "content": "old answer", "reasoning_content": "old hidden reasoning"},
                    ],
                    ensure_ascii=False,
                )
            )
            metadata: dict[str, object] = {}

            with mock.patch("deepseek_planner_client.requests.post", return_value=FakeResponse()):
                result = deepseek_planner_client.generate_text_response(
                    prompt="new prompt",
                    system_prompt="fresh system",
                    workspace_root=workspace_root,
                    config=config,
                    session_id=session_id,
                    response_metadata=metadata,
                )

            self.assertEqual(result, "new answer")

            persisted_history = json.loads(history_path.read_text())
            self.assertEqual(persisted_history[0]["content"], "fresh system")
            self.assertEqual(persisted_history[-1]["content"], "new answer")
            self.assertTrue(all("reasoning_content" not in item for item in persisted_history))

            trace_path = workspace_root / ".deepseek_planner_trace_session-123.jsonl"
            trace_entries = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
            self.assertEqual(len(trace_entries), 1)
            self.assertEqual(trace_entries[0]["assistant_reasoning_content"], "new reasoning")

            self.assertEqual(metadata["system_prompt_chars_sent"], len("fresh system"))
            self.assertEqual(metadata["history_message_count_sent"], 2)
            self.assertEqual(metadata["history_message_chars_sent"], len("old prompt") + len("old answer"))
            self.assertEqual(
                metadata["total_message_chars_sent"],
                len("fresh system") + len("old prompt") + len("old answer") + len("new prompt"),
            )
            self.assertEqual(
                metadata["estimated_prompt_tokens_sent"],
                (metadata["total_message_chars_sent"] + 3) // 4,
            )

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

    def test_persist_reviewer_summary_card_writes_review_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir)
            round_brief = research_script.StrategyRoundBrief(
                candidate_id="candidate_review_1",
                primary_direction="long | routing repair",
                hypothesis="继续修 long routing",
                change_plan="继续改 strategy 与 long_final_veto_clear",
                novelty_proof="最近几轮都卡在同一盆地",
                change_tags=("long", "routing"),
                expected_effects=("改善多头捕获",),
                core_factors=(),
            )
            decision = research_script.StrategyReviewerDecision(
                verdict="REVISE",
                reviewer_summary="当前 draft 仍落在 ownership_cluster 的饱和近邻。",
                rejection_type="saturated_same_basin",
                matched_evidence="failure_wiki ownership_cluster + duplicate_watchlist",
                must_change="至少换机制层或 changed_regions",
                why_not_new="仍是 long_final_veto_clear,strategy 近邻",
            )

            research_script._persist_reviewer_summary_card(
                memory_root=memory_root,
                round_brief=round_brief,
                decision=decision,
                iteration_id=12,
                stage_label="planner_review",
            )

            card_text = (memory_root / "wiki/reviewer_summary_card.md").read_text()
            self.assertIn("candidate_review_1", card_text)
            self.assertIn("只保留当前轮最后一次 reviewer 判定", card_text)
            self.assertIn("REVISE", card_text)
            self.assertIn("saturated_same_basin", card_text)
            self.assertIn("至少换机制层或 changed_regions", card_text)

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
        original_champion_test = research_script.champion_test_metrics
        try:
            research_script.best_source = working_base_source
            research_script.champion_source = champion_source
            research_script.champion_report = champion_report
            research_script.champion_test_metrics = {"test_score": 1.23}
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
            research_script.champion_test_metrics = original_champion_test

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

    def test_promotion_acceptance_accepts_any_gate_passed_candidate(self):
        baseline_report = EvaluationReport(
            metrics={"promotion_score": 0.40, "quality_score": 0.33},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        candidate_report = EvaluationReport(
            metrics={"promotion_score": 0.42, "quality_score": 0.33},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        original_best_report = research_script.best_report
        original_champion = research_script.champion_report
        original_runtime = research_script.RUNTIME
        try:
            research_script.best_report = baseline_report
            research_script.champion_report = baseline_report
            accepted, reason = research_script._promotion_acceptance_decision(candidate_report)
        finally:
            research_script.best_report = original_best_report
            research_script.champion_report = original_champion
            research_script.RUNTIME = original_runtime

        self.assertTrue(accepted)
        self.assertEqual("通过(gate-passed refresh)", reason)

    def test_promotion_acceptance_allows_score_and_quality_drop_after_gate_pass(self):
        baseline_report = EvaluationReport(
            metrics={"promotion_score": 0.40, "quality_score": 0.33},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        candidate_report = EvaluationReport(
            metrics={"promotion_score": 0.35, "quality_score": 0.32},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        original_best_report = research_script.best_report
        original_champion = research_script.champion_report
        original_runtime = research_script.RUNTIME
        try:
            research_script.best_report = baseline_report
            research_script.champion_report = baseline_report
            accepted, reason = research_script._promotion_acceptance_decision(candidate_report)
        finally:
            research_script.best_report = original_best_report
            research_script.champion_report = original_champion
            research_script.RUNTIME = original_runtime

        self.assertTrue(accepted)
        self.assertEqual("通过(gate-passed refresh)", reason)

    def test_promotion_acceptance_rejects_only_when_gate_fails(self):
        baseline_report = EvaluationReport(
            metrics={"promotion_score": 0.40, "quality_score": 0.33},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        candidate_report = EvaluationReport(
            metrics={"promotion_score": 0.80, "quality_score": 0.70},
            gate_passed=False,
            gate_reason="val命中率偏低(7%)",
            summary_text="",
            prompt_summary_text="",
        )
        original_best_report = research_script.best_report
        original_champion = research_script.champion_report
        original_runtime = research_script.RUNTIME
        try:
            research_script.best_report = baseline_report
            research_script.champion_report = baseline_report
            accepted, reason = research_script._promotion_acceptance_decision(candidate_report)
        finally:
            research_script.best_report = original_best_report
            research_script.champion_report = original_champion
            research_script.RUNTIME = original_runtime

        self.assertFalse(accepted)
        self.assertEqual("val命中率偏低(7%)", reason)

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

    def test_initialize_best_state_skips_loaded_reference_discord_once_after_new_champion(self):
        valid_source = (REPO_ROOT / "src/strategy_macd_aggressive.py").read_text()
        report = EvaluationReport(
            metrics={"promotion_score": 0.46, "quality_score": 0.27},
            gate_passed=True,
            gate_reason="通过",
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
            temp_paths.best_strategy_file.write_text(valid_source)
            temp_paths.best_state_file.write_text(
                json.dumps(
                    {
                        "score_regime": research_script.SCORE_REGIME,
                        "reference_role": "champion",
                        "reference_stage_started_at": "2026-04-23T12:37:56+00:00",
                        "reference_stage_iteration": 21,
                        "reference": {"code_hash": research_script.source_hash(valid_source)},
                        "suppress_initialize_saved_reference_discord_once": True,
                    },
                    ensure_ascii=False,
                )
            )

            with mock.patch.object(research_script, "RUNTIME", temp_runtime), mock.patch.object(
                research_script, "best_source", ""
            ), mock.patch.object(research_script, "best_report", None), mock.patch.object(
                research_script, "champion_report", None
            ), mock.patch.object(
                research_script, "reload_strategy_module"
            ), mock.patch.object(
                research_script, "evaluate_current_strategy", return_value=report
            ), mock.patch.object(
                research_script, "maybe_send_discord"
            ) as maybe_send_discord, mock.patch.object(
                research_script, "build_discord_summary_message", return_value="msg"
            ), mock.patch.object(
                research_script, "write_heartbeat"
            ), mock.patch.object(
                research_script, "log_info"
            ):
                research_script.initialize_best_state()

            self.assertFalse(maybe_send_discord.called)
            payload = json.loads(temp_paths.best_state_file.read_text())
            self.assertFalse(payload.get("suppress_initialize_saved_reference_discord_once", False))

    def test_initialize_best_state_rebuilds_saved_reference_when_score_regime_changes(self):
        valid_source = (REPO_ROOT / "backups/strategy_macd_aggressive_v2_best.py").read_text()
        candidate_source = (REPO_ROOT / "src/strategy_macd_aggressive.py").read_text()
        report = EvaluationReport(
            metrics={"promotion_score": 0.52, "quality_score": 0.33},
            gate_passed=True,
            gate_reason="通过",
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
            temp_paths.strategy_file.write_text(candidate_source)
            temp_paths.best_strategy_file.write_text(valid_source)
            temp_paths.best_state_file.write_text(
                json.dumps(
                    {
                        "score_regime": "trend_capture_v9_turn_protection",
                        "reference_role": "champion",
                        "reference": {"code_hash": research_script.source_hash(valid_source)},
                    },
                    ensure_ascii=False,
                )
            )

            with mock.patch.object(research_script, "RUNTIME", temp_runtime), mock.patch.object(
                research_script, "best_source", ""
            ), mock.patch.object(research_script, "best_report", None), mock.patch.object(
                research_script, "champion_report", None
            ), mock.patch.object(
                research_script, "reload_strategy_module"
            ), mock.patch.object(
                research_script, "evaluate_current_strategy", return_value=report
            ), mock.patch.object(
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
                self.assertEqual(temp_paths.strategy_file.read_text(), valid_source)
                self.assertTrue(any("已按新评分口径重算已保存主参考" in str(call) for call in log_info.call_args_list))


class ResearchRuntimeOptimizationsTest(unittest.TestCase):
    def test_prepare_backtest_context_reuses_cached_context_for_same_signature(self):
        original_cache = research_script.prepared_backtest_context_cache
        research_script.prepared_backtest_context_cache = research_script.OrderedDict()
        try:
            with mock.patch.object(
                research_script,
                "_file_cache_signature",
                return_value={"path": "fixture.csv", "exists": True, "mtime_ns": 1, "size": 1},
            ):
                with mock.patch.object(research_script.strategy_module, "PARAMS", {"alpha": 1}):
                    with mock.patch.object(research_script.strategy_module, "EXIT_PARAMS", {"beta": 2}):
                        with mock.patch.object(
                            research_script.backtest_module,
                            "prepare_backtest_context",
                            return_value={"prepared": "context"},
                        ) as prepare_mock:
                            first = research_script._prepare_backtest_context()
                            second = research_script._prepare_backtest_context()
        finally:
            research_script.prepared_backtest_context_cache = original_cache

        self.assertIs(first, second)
        self.assertEqual(prepare_mock.call_count, 1)

    def test_prepare_backtest_context_invalidates_cache_when_params_change(self):
        original_cache = research_script.prepared_backtest_context_cache
        research_script.prepared_backtest_context_cache = research_script.OrderedDict()
        try:
            with mock.patch.object(
                research_script,
                "_file_cache_signature",
                return_value={"path": "fixture.csv", "exists": True, "mtime_ns": 1, "size": 1},
            ):
                with mock.patch.object(research_script.strategy_module, "EXIT_PARAMS", {"beta": 2}):
                    with mock.patch.object(
                        research_script.backtest_module,
                        "prepare_backtest_context",
                        side_effect=[{"prepared": "ctx_1"}, {"prepared": "ctx_2"}],
                    ) as prepare_mock:
                        with mock.patch.object(research_script.strategy_module, "PARAMS", {"alpha": 1}):
                            first = research_script._prepare_backtest_context()
                        with mock.patch.object(research_script.strategy_module, "PARAMS", {"alpha": 2}):
                            second = research_script._prepare_backtest_context()
        finally:
            research_script.prepared_backtest_context_cache = original_cache

        self.assertEqual(first, {"prepared": "ctx_1"})
        self.assertEqual(second, {"prepared": "ctx_2"})
        self.assertEqual(prepare_mock.call_count, 2)

    def test_run_model_text_request_retries_nonpersistent_codex_transient_once(self):
        call_count = 0

        def fake_generate_text_response(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise StrategyGenerationTransientError("codex exec failed with exit code 1: timeout")
            return "retry_ok"

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            with mock.patch.object(research_script, "_planner_uses_deepseek", return_value=False):
                with mock.patch.object(research_script, "generate_text_response", side_effect=fake_generate_text_response):
                    with mock.patch.object(research_script, "_append_model_call_telemetry") as telemetry_mock:
                        with mock.patch.object(research_script, "_store_research_session_metadata") as store_session_mock:
                            with mock.patch.object(research_script, "log_info") as log_mock:
                                result = research_script._run_model_text_request(
                                    prompt="test prompt",
                                    system_prompt="test system",
                                    phase="edit_worker",
                                    workspace_root=workspace_root,
                                    session_kind="edit_worker",
                                    use_persistent_session=False,
                                )

        self.assertEqual(result, "retry_ok")
        self.assertEqual(call_count, 2)
        telemetry_mock.assert_called_once()
        telemetry_payload = telemetry_mock.call_args.args[0]
        self.assertEqual(telemetry_payload["system_prompt_chars_sent"], len("test system"))
        self.assertEqual(telemetry_payload["history_message_chars_sent"], 0)
        self.assertEqual(telemetry_payload["history_message_count_sent"], 0)
        self.assertEqual(
            telemetry_payload["total_message_chars_sent"],
            len("test prompt") + len("test system"),
        )
        self.assertEqual(
            telemetry_payload["estimated_prompt_tokens_sent"],
            research_script._estimate_prompt_tokens("test prompt", "test system"),
        )
        store_session_mock.assert_not_called()
        self.assertTrue(any("短重试" in str(call.args[0]) for call in log_mock.call_args_list))

    def test_run_model_text_request_telemetry_uses_deepseek_sent_context_metadata(self):
        def fake_generate_deepseek(**kwargs):
            metadata = kwargs["response_metadata"]
            metadata.update(
                {
                    "session_id": "deepseek-session",
                    "resumed": True,
                    "system_prompt_chars_sent": 300,
                    "history_message_chars_sent": 140,
                    "history_message_count_sent": 4,
                    "total_message_chars_sent": 470,
                    "estimated_prompt_tokens_sent": 118,
                }
            )
            return "deepseek_ok"

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            with mock.patch.object(research_script, "_planner_uses_deepseek", return_value=True):
                with mock.patch.object(research_script, "_active_research_session_id", return_value=""):
                    with mock.patch.object(
                        research_script,
                        "_embed_workspace_agents_for_api",
                        return_value="resolved system prompt",
                    ):
                        with mock.patch.object(
                            research_script,
                            "generate_deepseek_planner_text_response",
                            side_effect=fake_generate_deepseek,
                        ) as deepseek_mock:
                            with mock.patch.object(research_script, "_append_model_call_telemetry") as telemetry_mock:
                                with mock.patch.object(research_script, "_store_research_session_metadata") as store_session_mock:
                                    result = research_script._run_model_text_request(
                                        prompt="test prompt",
                                        system_prompt="raw system prompt",
                                        phase="planner",
                                        workspace_root=workspace_root,
                                        session_kind="planner",
                                        use_persistent_session=True,
                                    )

        self.assertEqual(result, "deepseek_ok")
        self.assertEqual(deepseek_mock.call_args.kwargs["system_prompt"], "resolved system prompt")
        telemetry_mock.assert_called_once()
        telemetry_payload = telemetry_mock.call_args.args[0]
        self.assertEqual(telemetry_payload["prompt_chars"], len("test prompt"))
        self.assertEqual(telemetry_payload["system_prompt_chars"], len("raw system prompt"))
        self.assertEqual(telemetry_payload["system_prompt_chars_sent"], 300)
        self.assertEqual(telemetry_payload["history_message_chars_sent"], 140)
        self.assertEqual(telemetry_payload["history_message_count_sent"], 4)
        self.assertEqual(telemetry_payload["total_message_chars_sent"], 470)
        self.assertEqual(telemetry_payload["estimated_prompt_tokens_sent"], 118)
        store_session_mock.assert_called_once_with(
            session_id="deepseek-session",
            workspace_root=workspace_root,
        )

    def test_candidate_with_repair_uses_single_candidate_smoke_pass(self):
        candidate = StrategyCandidate(
            candidate_id="candidate_opt_test",
            hypothesis="test",
            change_plan="test",
            primary_direction="structure | test",
            closest_failed_cluster="test_cluster",
            novelty_proof="test",
            change_tags=("structure",),
            edited_regions=("strategy",),
            expected_effects=(),
            core_factors=(),
            strategy_code="def strategy():\n    return None\n",
        )
        report = EvaluationReport(
            metrics={"promotion_score": 0.1, "quality_score": 0.1},
            gate_passed=False,
            gate_reason="test",
            summary_text="test",
            prompt_summary_text="test",
        )

        with mock.patch.object(research_script, "_base_behavior_profile", return_value=[{"window": "base"}]) as base_mock:
            with mock.patch.object(research_script, "_smoke_candidate", return_value=[{"window": "candidate"}]) as smoke_mock:
                with mock.patch.object(
                    research_script,
                    "_behavior_profile_from_results",
                    return_value=[{"window": "candidate", "fingerprint": {"return": 1.0}}],
                ) as profile_mock:
                    with mock.patch.object(
                        research_script,
                        "_behavior_diff_payload",
                        return_value={"changed": True},
                    ) as diff_mock:
                        with mock.patch.object(research_script, "_evaluate_candidate", return_value=report) as eval_mock:
                            returned_candidate, returned_report = research_script._candidate_with_repair(
                                "base source\n",
                                candidate,
                                workspace_root=REPO_ROOT,
                            )

        self.assertIs(returned_candidate, candidate)
        self.assertIs(returned_report, report)
        base_mock.assert_called_once_with("base source\n")
        smoke_mock.assert_called_once_with(candidate)
        profile_mock.assert_called_once_with([{"window": "candidate"}])
        diff_mock.assert_called_once()
        eval_mock.assert_called_once_with(candidate)


class RefactorHelperRegressionTest(unittest.TestCase):
    def test_prepare_backtest_window_runtime_slices_window_payload(self):
        prepared_context = {
            "intraday_all": [
                {"timestamp": 0},
                {"timestamp": 1_000},
                {"timestamp": 2_000},
                {"timestamp": 3_000},
            ],
            "intraday_timestamps": [0, 1_000, 2_000, 3_000],
            "intraday_interval_ms": 1_000,
            "hourly_all": [{"timestamp": 0}],
            "execution_all": [{"timestamp": 900}, {"timestamp": 1_500}, {"timestamp": 3_500}],
            "execution_timestamps": [900, 1_500, 3_500],
            "funding_all": [{"timestamp": 1_000}, {"timestamp": 3_000}],
            "funding_timestamps": [1_000, 3_000],
            "four_hour_state": [{"marker": "a"}, {"marker": "b"}, {"marker": "c"}],
            "four_hour_close_timestamps": [500, 2_500, 4_500],
        }

        runtime = prepare_backtest_window_runtime(
            prepared_context,
            start_date="unused-start",
            end_date="unused-end",
            exit_params={"funding_fee_enabled": 1},
            include_diagnostics=True,
            beijing_window_indices_from_timestamps=lambda _ts, _start, _end: (1, 4),
            timestamp_window_indices_inclusive=lambda timestamps, start_ts, end_ts: (
                next((idx for idx, value in enumerate(timestamps) if value >= start_ts), len(timestamps)),
                next((idx for idx, value in enumerate(timestamps) if value > end_ts), len(timestamps)),
            ),
            funding_interval_ms=lambda _timestamps: 2_000,
            funding_window_coverage_report=lambda timestamps, _start, _end: {
                "mode": "ok",
                "ratio": len(timestamps) / 10.0,
                "gap_count": 0,
            },
        )

        self.assertEqual(runtime.intraday_start_idx, 1)
        self.assertEqual(runtime.intraday_end_idx, 4)
        self.assertEqual([bar["timestamp"] for bar in runtime.intraday_data], [1_000, 2_000, 3_000])
        self.assertEqual([row["timestamp"] for row in runtime.execution_rows], [1_500, 3_500])
        self.assertEqual([row["timestamp"] for row in runtime.funding_rows], [1_000, 3_000])
        self.assertEqual(runtime.funding_coverage["mode"], "ok")
        self.assertEqual([row["marker"] for row in runtime.four_hour_window_state], ["b"])

    def test_persist_best_state_writes_manifest_and_strategy_files(self):
        report = EvaluationReport(
            metrics={"promotion_score": 0.41, "quality_score": 0.28},
            gate_passed=False,
            gate_reason="baseline only",
            summary_text="",
            prompt_summary_text="",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            best_state_file = temp_root / "state/best.json"
            best_strategy_file = temp_root / "backups/best.py"
            champion_strategy_file = temp_root / "backups/champion.py"

            persist_best_state(
                best_state_file,
                best_strategy_file,
                champion_strategy_file,
                "def strategy():\n    return 'ok'\n",
                report,
                score_regime="trend_capture_v11_piecewise_drawdown_penalty",
                test_metrics={"test_score": 1.2},
                stage_started_at="2026-04-20T00:00:00+00:00",
                stage_iteration=9,
            )

            payload = load_saved_reference_state(best_state_file)
            self.assertEqual(payload["reference_role"], "baseline")
            self.assertEqual(payload["reference_stage_iteration"], 9)
            self.assertEqual(payload["test_metrics"]["test_score"], 1.2)
            self.assertFalse(champion_strategy_file.exists())
            self.assertTrue(best_strategy_file.exists())

    def test_archive_champion_snapshot_writes_metadata_and_copies_charts(self):
        candidate = StrategyCandidate(
            candidate_id="champion_1",
            hypothesis="hypothesis",
            change_plan="plan",
            closest_failed_cluster="",
            novelty_proof="novel",
            change_tags=("drawdown",),
            edited_regions=("strategy",),
            expected_effects=(),
            core_factors=(),
            strategy_code="def strategy():\n    return None\n",
            primary_direction="long",
        )
        report = EvaluationReport(
            metrics={"quality_score": 0.55, "promotion_score": 0.61},
            gate_passed=True,
            gate_reason="通过",
            summary_text="",
            prompt_summary_text="",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            validation_chart = temp_root / "validation.png"
            selection_chart = temp_root / "selection.png"
            validation_chart.write_bytes(b"validation")
            selection_chart.write_bytes(b"selection")

            snapshot_dir = archive_champion_snapshot(
                temp_root / "history",
                iteration_id=12,
                accepted_at="2026-04-28T01:02:03+00:00",
                candidate=candidate,
                source=candidate.strategy_code,
                report=report,
                test_metrics={"test_score": 1.5},
                chart_paths=PerformanceChartPaths(
                    validation_chart=validation_chart,
                    selection_chart=selection_chart,
                ),
            )

            metadata = json.loads((snapshot_dir / "metadata.json").read_text())
            self.assertEqual(metadata["candidate_id"], "champion_1")
            self.assertEqual(metadata["validation_chart"], "validation.png")
            self.assertEqual(metadata["selection_chart"], "selection.png")
            self.assertEqual(metadata["test_metrics"]["test_score"], 1.5)
            self.assertTrue((snapshot_dir / "validation.png").exists())
            self.assertTrue((snapshot_dir / "selection.png").exists())


class RejectedTestQueueTest(unittest.TestCase):
    def _entry(self, *, iteration: int, outcome: str = "rejected", stop_stage: str = "full_eval") -> dict[str, object]:
        return {
            "iteration": iteration,
            "timestamp": "2026-04-28T00:03:00+00:00",
            "candidate_id": f"cand_{iteration}",
            "outcome": outcome,
            "stop_stage": stop_stage,
            "score_regime": research_script.SCORE_REGIME,
            "reference_role": "champion",
            "primary_direction": "long",
            "gate_reason": "未超过当前champion晋级分",
            "decision_reason": "未超过当前champion晋级分",
            "note": "",
            "promotion_score": 0.44,
            "quality_score": 0.51,
            "promotion_delta": -0.02,
            "reference_code_hash": "reference_hash",
            "change_tags": ["drawdown_control"],
            "edited_regions": ["strategy"],
            "system_changed_regions": ["strategy"],
            "diff_summary": ["adjust stop logic"],
            "metrics": {
                "capture_score": 0.62,
                "timed_return_score": 0.28,
            },
        }

    def test_queue_round_artifact_test_marks_pending_status(self):
        strategy_source = "def strategy():\n    return None\n"
        original_runtime = research_script.RUNTIME
        original_executor = research_script.rejected_test_executor
        original_futures = research_script.rejected_test_futures
        original_queued = research_script.queued_rejected_test_round_dirs

        class FakeExecutor:
            def __init__(self):
                self.calls = []

            def submit(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return object()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_root = Path(temp_dir)
                temp_paths = replace(
                    research_script.RUNTIME.paths,
                    repo_root=temp_root,
                    round_artifacts_dir=temp_root / "backups/research_v2_round_artifacts",
                )
                research_script.RUNTIME = replace(research_script.RUNTIME, paths=temp_paths)
                research_script.rejected_test_executor = FakeExecutor()
                research_script.rejected_test_futures = {}
                research_script.queued_rejected_test_round_dirs = set()
                round_dir = persist_round_artifact(
                    temp_paths.round_artifacts_dir,
                    repo_root=temp_root,
                    entry=self._entry(iteration=5),
                    strategy_source=strategy_source,
                    windows={"test_start_date": "2026-01-01", "test_end_date": "2026-04-20"},
                    gates={},
                    scoring={},
                    data_fingerprints={},
                    engine_fingerprints={},
                )

                with mock.patch.object(research_script, "_ensure_rejected_test_executor"):
                    queued = research_script._queue_round_artifact_test(round_dir, reason="unit_test")

                self.assertTrue(queued)
                metadata = load_round_artifact_metadata(round_dir)
                self.assertEqual(metadata["test_evaluation"]["status"], "pending")
                self.assertEqual(metadata["test_evaluation"]["mode"], "rejected_async")
                self.assertEqual(len(research_script.rejected_test_futures), 1)
        finally:
            research_script.RUNTIME = original_runtime
            research_script.rejected_test_executor = original_executor
            research_script.rejected_test_futures = original_futures
            research_script.queued_rejected_test_round_dirs = original_queued

    def test_drain_rejected_test_futures_persists_completed_metrics(self):
        strategy_source = "def strategy():\n    return None\n"
        original_runtime = research_script.RUNTIME
        original_futures = research_script.rejected_test_futures
        original_queued = research_script.queued_rejected_test_round_dirs

        class CompletedFuture:
            def done(self):
                return True

            def result(self):
                return {
                    "test_metrics": {
                        "test_total_return_pct": 3.5,
                        "test_sharpe_ratio": 0.77,
                        "test_max_drawdown": 8.2,
                    }
                }

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_root = Path(temp_dir)
                temp_paths = replace(
                    research_script.RUNTIME.paths,
                    repo_root=temp_root,
                    round_artifacts_dir=temp_root / "backups/research_v2_round_artifacts",
                )
                research_script.RUNTIME = replace(research_script.RUNTIME, paths=temp_paths)
                round_dir = persist_round_artifact(
                    temp_paths.round_artifacts_dir,
                    repo_root=temp_root,
                    entry=self._entry(iteration=6),
                    strategy_source=strategy_source,
                    windows={"test_start_date": "2026-01-01", "test_end_date": "2026-04-20"},
                    gates={},
                    scoring={},
                    data_fingerprints={},
                    engine_fingerprints={},
                    test_evaluation={
                        "status": "pending",
                        "mode": "rejected_async",
                        "queued_at": "2026-04-28T00:03:01+00:00",
                    },
                )
                future = CompletedFuture()
                research_script.rejected_test_futures = {
                    future: {
                        "round_dir": round_dir,
                        "iteration": 6,
                        "candidate_id": "cand_6",
                        "queued_at": "2026-04-28T00:03:01+00:00",
                    }
                }
                research_script.queued_rejected_test_round_dirs = {str(round_dir.resolve())}

                research_script._drain_rejected_test_futures()

                metadata = load_round_artifact_metadata(round_dir)
                self.assertEqual(metadata["test_evaluation"]["status"], "completed")
                self.assertEqual(metadata["test_evaluation"]["mode"], "rejected_async")
                self.assertEqual(metadata["test_metrics"]["test_total_return_pct"], 3.5)
                self.assertEqual(metadata["test_metrics"]["test_sharpe_ratio"], 0.77)
                self.assertEqual(metadata["test_metrics"]["test_max_drawdown"], 8.2)
                self.assertEqual(research_script.rejected_test_futures, {})
                self.assertEqual(research_script.queued_rejected_test_round_dirs, set())
        finally:
            research_script.RUNTIME = original_runtime
            research_script.rejected_test_futures = original_futures
            research_script.queued_rejected_test_round_dirs = original_queued


class PlateauProbeIntegrationTest(unittest.TestCase):
    def test_run_candidate_plateau_probe_uses_validation_triplet_and_keeps_candidate_read_only(self):
        candidate = StrategyCandidate(
            candidate_id="cand_plateau",
            hypothesis="观察退出平台",
            change_plan="只读扫描退出参数邻域",
            closest_failed_cluster="",
            novelty_proof="观察平台，不自动改代码",
            change_tags=("stop",),
            edited_regions=("EXIT_PARAMS",),
            expected_effects=("确认 val 平台形态",),
            core_factors=(),
            strategy_code="def strategy():\n    return None\n",
            exit_range_scan={"raw": "stop_max_loss_pct | 72,84,96 | probe"},
        )

        class FakeOutcome:
            param = "stop_max_loss_pct"
            values = (72.0, 84.0, 96.0)
            center_period_score = 0.44
            best_period_score = 0.53
            center_gap = 0.09
            score_span = 0.11
            drawdown_span = 6.5

            def to_dict(self):
                return {
                    "enabled": True,
                    "param": self.param,
                    "values": list(self.values),
                    "current_value": 84.0,
                    "best_value": 96.0,
                    "center_period_score": self.center_period_score,
                    "best_period_score": self.best_period_score,
                    "center_gap": self.center_gap,
                    "score_span": self.score_span,
                    "drawdown_span": self.drawdown_span,
                    "current_is_best": False,
                    "summary": [],
                    "reason": "probe",
                }

        captured = {}

        def fake_run_plateau_probe(**kwargs):
            captured["windows"] = kwargs["windows"]
            captured["workers"] = kwargs["workers"]
            captured["current_exit_params"] = dict(kwargs["current_exit_params"])
            return FakeOutcome()

        spec = type(
            "Spec",
            (),
            {"param": "stop_max_loss_pct", "values": (72.0, 84.0, 96.0), "reason": "probe"},
        )()

        with mock.patch.object(research_script, "infer_exit_range_scan_spec", return_value=spec), \
             mock.patch.object(research_script, "run_plateau_probe", side_effect=fake_run_plateau_probe), \
             mock.patch.object(research_script, "active_exit_params", return_value={"stop_max_loss_pct": 84.0}):
            strategy_before = candidate.strategy_code
            payload = research_script._run_candidate_plateau_probe(candidate, base_source="base_source")

        self.assertEqual(payload["param"], "stop_max_loss_pct")
        self.assertEqual(len(captured["windows"]), 3)
        self.assertTrue(all(window.group == "validation" for window in captured["windows"]))
        self.assertEqual(captured["workers"], research_script.RUNTIME.exit_range_scan_workers)
        self.assertEqual(captured["current_exit_params"]["stop_max_loss_pct"], 84.0)
        self.assertEqual(candidate.strategy_code, strategy_before)


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
            test_metrics={
                "test_total_return_pct": 3.21,
                "test_closed_trades": 9.0,
                "test_max_drawdown": 7.4,
                "test_fee_drag_pct": 0.6,
                "test_sharpe_ratio": 0.35,
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

class ExitRangeScanUnitTest(unittest.TestCase):
    def test_parse_exit_range_scan_payload_accepts_text_contract(self):
        from research_v2.exit_range_scan import parse_exit_range_scan_payload

        spec = parse_exit_range_scan_payload(
            "trailing_activation_pct | 18,21,24 | scan nearby exit threshold",
            max_values=3,
        )
        self.assertIsNotNone(spec)
        self.assertEqual(spec.param, "trailing_activation_pct")
        self.assertEqual(spec.values, (18, 21, 24))

    def test_replace_exit_param_value_only_updates_target_key(self):
        from research_v2.exit_range_scan import replace_exit_param_value

        source = """
# PARAMS_START
PARAMS = {}
# PARAMS_END
# EXIT_PARAMS_START
EXIT_PARAMS = {
    "trailing_activation_pct": 20.0,
    "pyramid_adx_min": 19.0,
}
# EXIT_PARAMS_END
"""
        updated = replace_exit_param_value(source, "trailing_activation_pct", 21)
        self.assertIn('"trailing_activation_pct": 21', updated)
        self.assertIn('"pyramid_adx_min": 19.0', updated)

    def test_infer_exit_range_scan_spec_from_changed_exit_param(self):
        from research_v2.exit_range_scan import infer_exit_range_scan_spec

        base = """
# PARAMS_START
PARAMS = {}
# PARAMS_END
# EXIT_PARAMS_START
EXIT_PARAMS = {
    "trailing_activation_pct": 20.0,
    "pyramid_adx_min": 19.0,
}
# EXIT_PARAMS_END
"""
        candidate = base.replace('"trailing_activation_pct": 20.0', '"trailing_activation_pct": 24.0')
        spec = infer_exit_range_scan_spec(base, candidate, None, max_values=3)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.param, "trailing_activation_pct")
        self.assertIn(24.0, spec.values)

class ResearchRuntimeLeanPipelineTest(unittest.TestCase):
    def test_context_cache_key_ignores_execution_irrelevant_exit_params(self):
        original_cache = research_script.prepared_backtest_context_cache
        research_script.prepared_backtest_context_cache = research_script.OrderedDict()
        try:
            with mock.patch.object(
                research_script,
                "_file_cache_signature",
                return_value={"path": "fixture.csv", "exists": True, "mtime_ns": 1, "size": 1},
            ):
                with mock.patch.object(research_script.strategy_module, "PARAMS", {"alpha": 1}):
                    with mock.patch.object(
                        research_script.backtest_module,
                        "prepare_backtest_context",
                        return_value={"prepared": "context"},
                    ) as prepare_mock:
                        with mock.patch.object(
                            research_script.strategy_module,
                            "EXIT_PARAMS",
                            {"execution_use_1m": 1, "funding_fee_enabled": 1, "trailing_activation_pct": 100.0},
                        ):
                            first = research_script._prepare_backtest_context()
                        with mock.patch.object(
                            research_script.strategy_module,
                            "EXIT_PARAMS",
                            {"execution_use_1m": 1, "funding_fee_enabled": 1, "trailing_activation_pct": 120.0},
                        ):
                            second = research_script._prepare_backtest_context()
        finally:
            research_script.prepared_backtest_context_cache = original_cache

        self.assertIs(first, second)
        self.assertEqual(prepare_mock.call_count, 1)

    def test_context_cache_key_keeps_context_relevant_exit_params(self):
        original_cache = research_script.prepared_backtest_context_cache
        research_script.prepared_backtest_context_cache = research_script.OrderedDict()
        try:
            with mock.patch.object(
                research_script,
                "_file_cache_signature",
                return_value={"path": "fixture.csv", "exists": True, "mtime_ns": 1, "size": 1},
            ):
                with mock.patch.object(research_script.strategy_module, "PARAMS", {"alpha": 1}):
                    with mock.patch.object(
                        research_script.backtest_module,
                        "prepare_backtest_context",
                        side_effect=[{"prepared": "ctx_1"}, {"prepared": "ctx_2"}],
                    ) as prepare_mock:
                        with mock.patch.object(
                            research_script.strategy_module,
                            "EXIT_PARAMS",
                            {"execution_use_1m": 1, "funding_fee_enabled": 1},
                        ):
                            first = research_script._prepare_backtest_context()
                        with mock.patch.object(
                            research_script.strategy_module,
                            "EXIT_PARAMS",
                            {"execution_use_1m": 0, "funding_fee_enabled": 1},
                        ):
                            second = research_script._prepare_backtest_context()
        finally:
            research_script.prepared_backtest_context_cache = original_cache

        self.assertEqual(first, {"prepared": "ctx_1"})
        self.assertEqual(second, {"prepared": "ctx_2"})
        self.assertEqual(prepare_mock.call_count, 2)

    def test_early_reject_snapshot_runs_only_on_milestones(self):
        class Window:
            def __init__(self, label, start, end):
                self.label = label
                self.group = "eval"
                self.start_date = start
                self.end_date = end

        windows = [Window(f"train{i}", f"2024-01-{i:02d}", f"2024-01-{i:02d}") for i in range(1, 27)]
        temp_runtime = replace(
            research_script.RUNTIME,
            early_reject_after_windows=10,
            early_reject_milestones=(10, 18, 26),
            early_reject_min_segments=999,
        )
        calls = []

        def fake_backtest(**kwargs):
            calls.append((kwargs["start_date"], kwargs["end_date"]))
            return {"trend_capture_points": [], "return": 0.0, "trades": 0}

        with mock.patch.object(research_script, "RUNTIME", temp_runtime), mock.patch.object(
            research_script, "write_heartbeat"
        ), mock.patch.object(research_script.backtest_module, "backtest_macd_aggressive", side_effect=fake_backtest), mock.patch.object(
            research_script.strategy_module, "PARAMS", {}
        ), mock.patch.object(research_script, "active_exit_params", return_value={}):
            research_script._run_base_backtests(
                allow_early_reject=True,
                windows=windows,
                prepared_context={"prepared": True},
            )

        self.assertEqual(len(calls), 29)
        snapshot_calls = [call for call in calls if call[0] == "2024-01-01" and call[1] != "2024-01-01"]
        self.assertEqual(snapshot_calls, [
            ("2024-01-01", "2024-01-10"),
            ("2024-01-01", "2024-01-18"),
            ("2024-01-01", "2024-01-26"),
        ])

    def test_behavior_profile_changed_accepts_large_funnel_delta(self):
        fingerprint = {"return": 0.0, "score": 0.0, "max_drawdown": 0.0, "trades": 0}
        base = [{
            "window": "train1",
            "fingerprint": fingerprint,
            "funnel": {"long": {"outer_context_pass": 100, "path_pass": 20, "final_veto_pass": 10}},
            "filled_side_entries": {"long": 0, "short": 0},
        }]
        candidate = [{
            "window": "train1",
            "fingerprint": fingerprint,
            "funnel": {"long": {"outer_context_pass": 130, "path_pass": 20, "final_veto_pass": 10}},
            "filled_side_entries": {"long": 0, "short": 0},
        }]

        self.assertTrue(research_script._behavior_profile_changed(base, candidate))

    def test_behavior_profile_changed_ignores_small_funnel_delta(self):
        fingerprint = {"return": 0.0, "score": 0.0, "max_drawdown": 0.0, "trades": 0}
        base = [{
            "window": "train1",
            "fingerprint": fingerprint,
            "funnel": {"long": {"outer_context_pass": 100, "path_pass": 20, "final_veto_pass": 10}},
            "filled_side_entries": {"long": 0, "short": 0},
        }]
        candidate = [{
            "window": "train1",
            "fingerprint": fingerprint,
            "funnel": {"long": {"outer_context_pass": 103, "path_pass": 20, "final_veto_pass": 10}},
            "filled_side_entries": {"long": 0, "short": 0},
        }]

        self.assertFalse(research_script._behavior_profile_changed(base, candidate))
