#!/usr/bin/env python3
"""面向激进版策略的小范围参数搜索。"""
from copy import deepcopy
from itertools import product
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import backtest_macd_aggressive as backtest_module
import strategy_macd_aggressive as strategy_module

INTRADAY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv"
HOURLY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv"

WINDOWS = [
    ("trend_a", "2025-07-01", "2025-07-31"),
    ("trend_b", "2025-08-01", "2025-08-31"),
    ("noise_a", "2025-09-01", "2025-09-30"),
    ("noise_b", "2025-10-01", "2025-10-31"),
    ("trend_c", "2026-01-01", "2026-01-31"),
]


def evaluate(strategy_params, exit_params):
    rows = []
    total_return = 0.0
    worst_dd = 0.0
    total_trades = 0
    for label, start_date, end_date in WINDOWS:
        result = backtest_module.backtest_macd_aggressive(
            strategy_func=strategy_module.strategy,
            intraday_file=INTRADAY_FILE,
            hourly_file=HOURLY_FILE,
            start_date=start_date,
            end_date=end_date,
            strategy_params=strategy_params,
            exit_params=exit_params,
        )
        total_return += result["return"]
        worst_dd = max(worst_dd, result["max_drawdown"])
        total_trades += result["trades"]
        rows.append((label, result))

    trend_return = sum(result["return"] for label, result in rows if label.startswith("trend"))
    noise_return = sum(result["return"] for label, result in rows if label.startswith("noise"))
    score = (
        trend_return * 1.5
        + noise_return * 0.7
        + total_return * 0.4
        - max(0.0, worst_dd - 16.0) * 2.2
        - max(0, 18 - total_trades) * 0.8
    )
    return {
        "score": score,
        "total_return": total_return,
        "trend_return": trend_return,
        "noise_return": noise_return,
        "worst_dd": worst_dd,
        "total_trades": total_trades,
        "rows": rows,
    }


def search():
    base_strategy = deepcopy(strategy_module.PARAMS)
    base_exit = deepcopy(backtest_module.EXIT_PARAMS)
    best = []

    breakout_space = product(
        [14.0, 16.0],
        [14, 16],
        [0.98, 1.02],
        [0.56, 0.60],
        [0.32, 0.36],
    )
    exit_space = product(
        [16, 18],
        [0.20, 0.24],
        [18.0, 22.0],
        [2.4, 2.8],
    )

    for (
        breakout_adx_min,
        breakout_lookback,
        breakout_volume_ratio_min,
        breakout_close_pos_min,
        breakout_body_ratio_min,
    ) in breakout_space:
        strategy_params = deepcopy(base_strategy)
        strategy_params.update(
            {
                "breakout_adx_min": breakout_adx_min,
                "breakout_lookback": breakout_lookback,
                "breakout_volume_ratio_min": breakout_volume_ratio_min,
                "breakout_buffer_pct": 0.0,
                "breakout_close_pos_min": breakout_close_pos_min,
                "breakout_body_ratio_min": breakout_body_ratio_min,
            }
        )
        for (
            leverage,
            position_fraction,
            breakout_tp1_pnl_pct,
            breakout_stop_atr_mult,
        ) in exit_space:
            exit_params = deepcopy(base_exit)
            exit_params.update(
                {
                    "leverage": leverage,
                    "position_fraction": position_fraction,
                    "breakout_tp1_pnl_pct": breakout_tp1_pnl_pct,
                    "breakout_trailing_activation_pct": 28.0,
                    "breakout_trailing_giveback_pct": 10.0,
                    "pullback_stop_atr_mult": 1.6,
                    "breakout_stop_atr_mult": breakout_stop_atr_mult,
                }
            )
            summary = evaluate(strategy_params, exit_params)
            best.append((summary, strategy_params, exit_params))

    best.sort(key=lambda item: item[0]["score"], reverse=True)
    return best[:12]


def main():
    for rank, (summary, strategy_params, exit_params) in enumerate(search(), start=1):
        print(
            f"#{rank} score={summary['score']:.2f} total={summary['total_return']:.2f}% "
            f"trend={summary['trend_return']:.2f}% noise={summary['noise_return']:.2f}% "
            f"dd={summary['worst_dd']:.2f}% trades={summary['total_trades']}"
        )
        print(
            "  strategy "
            f"adx={strategy_params['breakout_adx_min']} lookback={strategy_params['breakout_lookback']} "
            f"vol={strategy_params['breakout_volume_ratio_min']} "
            f"buffer={strategy_params['breakout_buffer_pct']} close_pos={strategy_params['breakout_close_pos_min']} "
            f"body={strategy_params['breakout_body_ratio_min']}"
        )
        print(
            "  exit "
            f"lev={exit_params['leverage']} frac={exit_params['position_fraction']} "
            f"b_tp1={exit_params['breakout_tp1_pnl_pct']} "
            f"b_trail={exit_params['breakout_trailing_activation_pct']}/{exit_params['breakout_trailing_giveback_pct']} "
            f"pb_stop={exit_params['pullback_stop_atr_mult']} b_stop={exit_params['breakout_stop_atr_mult']}"
        )
        for label, result in summary["rows"]:
            print(
                f"    {label}: ret={result['return']:.2f}% dd={result['max_drawdown']:.2f}% "
                f"trades={result['trades']}"
            )


if __name__ == "__main__":
    main()
