#!/usr/bin/env python3
"""激进版参数敏感性分析。"""
import copy
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from backtest_macd_aggressive import EXIT_PARAMS, backtest_macd_aggressive
import strategy_macd_aggressive as aggressive_strategy

INTRADAY_FILE = str(REPO_ROOT / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv")
HOURLY_FILE = str(REPO_ROOT / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv")


def run_variant(strategy_delta=None, exit_delta=None):
    strategy_params = copy.deepcopy(aggressive_strategy.PARAMS)
    exit_params = copy.deepcopy(EXIT_PARAMS)
    if strategy_delta:
        strategy_params.update(strategy_delta)
    if exit_delta:
        exit_params.update(exit_delta)
    return backtest_macd_aggressive(
        strategy_func=aggressive_strategy.strategy,
        intraday_file=INTRADAY_FILE,
        hourly_file=HOURLY_FILE,
        start_date="2025-01-01",
        end_date="2026-02-28",
        strategy_params=strategy_params,
        exit_params=exit_params,
    )


def print_exit_sweep(title, key, values):
    print(f"\n[{title}]")
    for value in values:
        result = run_variant(exit_delta={key: value})
        print(
            f"{key}={value}: return={result['return']:.2f}%, "
            f"dd={result['max_drawdown']:.2f}%, trades={result['trades']}, liq={result['liquidations']}"
        )


def print_strategy_sweep(title, key, values):
    print(f"\n[{title}]")
    for value in values:
        result = run_variant(strategy_delta={key: value})
        print(
            f"{key}={value}: return={result['return']:.2f}%, "
            f"dd={result['max_drawdown']:.2f}%, trades={result['trades']}, liq={result['liquidations']}"
        )


def main():
    print_exit_sweep("杠杆敏感性", "leverage", [14, 16, 18, 20])
    print_exit_sweep("突破首止盈敏感性", "breakout_tp1_pnl_pct", [16.0, 18.0, 20.0, 24.0])
    print_exit_sweep("突破ATR止损敏感性", "breakout_stop_atr_mult", [2.2, 2.6, 2.8, 3.2])
    print_exit_sweep("并发仓位敏感性", "max_concurrent_positions", [2, 3, 4, 5])
    print_strategy_sweep("突破小时级趋势扩张敏感性", "breakout_hourly_spread_min", [0.003, 0.0045, 0.006, 0.008])


if __name__ == "__main__":
    main()
