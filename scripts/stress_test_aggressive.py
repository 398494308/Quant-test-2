#!/usr/bin/env python3
"""激进版策略压力测试。"""
import copy
import csv
import tempfile
from contextlib import suppress
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from backtest_macd_aggressive import EXIT_PARAMS, backtest_macd_aggressive, load_ohlcv_data
import backtest_macd_aggressive as aggressive_backtest
import strategy_macd_aggressive as aggressive_strategy

INTRADAY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv"
HOURLY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv"


def _write_temp_csv(rows):
    handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", newline="")
    fieldnames = ["timestamp", "open", "high", "low", "close", "volume"]
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    handle.close()
    return handle.name


def _run(exit_override=None, intraday_file=None):
    params = copy.deepcopy(EXIT_PARAMS)
    if exit_override:
        params.update(exit_override)
    return backtest_macd_aggressive(
        strategy_func=aggressive_strategy.strategy,
        intraday_file=str(intraday_file or INTRADAY_FILE),
        hourly_file=str(HOURLY_FILE),
        start_date="2025-01-01",
        end_date="2026-02-28",
        strategy_params=aggressive_strategy.PARAMS,
        exit_params=params,
    )


def _flash_crash_file():
    rows = copy.deepcopy(load_ohlcv_data(str(INTRADAY_FILE)))
    crash_idx = int(len(rows) * 0.62)
    rows[crash_idx]["low"] *= 0.90
    rows[crash_idx]["close"] *= 0.92
    rows[crash_idx]["high"] = max(rows[crash_idx]["high"], rows[crash_idx]["open"], rows[crash_idx]["close"])
    rows[crash_idx]["low"] = min(rows[crash_idx]["low"], rows[crash_idx]["open"], rows[crash_idx]["close"])
    return _write_temp_csv(rows)


def _flat_market_file():
    rows = copy.deepcopy(load_ohlcv_data(str(INTRADAY_FILE)))
    start_idx = int(len(rows) * 0.35)
    end_idx = int(len(rows) * 0.48)
    anchor = rows[start_idx]["close"]
    for idx in range(start_idx, end_idx):
        rows[idx]["open"] = anchor
        rows[idx]["close"] = anchor * 1.0002
        rows[idx]["high"] = anchor * 1.0012
        rows[idx]["low"] = anchor * 0.9988
        rows[idx]["volume"] *= 0.6
    return _write_temp_csv(rows)


def _max_consecutive_losses():
    result = _run()
    return (
        result.get("stop_exit_count", 0)
        + result.get("regime_exit_count", 0)
        + result.get("time_exit_count", 0),
        result,
    )


def main():
    baseline = _run()
    flash_crash_file = _flash_crash_file()
    flat_market_file = _flat_market_file()
    flash_crash = _run(intraday_file=flash_crash_file)
    flat_market = _run(intraday_file=flat_market_file)
    leverage_20 = _run({"leverage": 20})
    leverage_25 = _run({"leverage": 25})
    full_exposure = _run({"max_concurrent_positions": 5, "position_fraction": 0.24})
    loss_proxy, _ = _max_consecutive_losses()

    tests = {
        "baseline": baseline,
        "flash_crash": flash_crash,
        "flat_market": flat_market,
        "leverage_20": leverage_20,
        "leverage_25": leverage_25,
        "full_exposure": full_exposure,
    }

    for name, result in tests.items():
        print(
            f"{name}: return={result['return']:.2f}%, dd={result['max_drawdown']:.2f}%, "
            f"trades={result['trades']}, liq={result['liquidations']}, "
            f"avg_hold={result['avg_hold_bars']:.2f}, pyramid={result['pyramid_add_count']}"
        )
    print(f"连续亏损压力代理指标: {loss_proxy}")
    with suppress(FileNotFoundError):
        Path(flash_crash_file).unlink()
    with suppress(FileNotFoundError):
        Path(flat_market_file).unlink()


if __name__ == "__main__":
    main()
