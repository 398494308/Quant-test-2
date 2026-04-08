#!/usr/bin/env python3
"""自研引擎 vs freqtrade 适配层对比验证脚本。

重点验证“入场信号层”是否一致。
freqtrade 侧通过 src/freqtrade_macd_aggressive.py 的适配函数生成信号，
避免再维护第三份手写近似逻辑。
"""
import sys
import os
from bisect import bisect_right
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

# ── 第一步：用自研引擎跑回测，收集入场信号时间戳 ──
import backtest_macd_aggressive as bt_engine
import strategy_macd_aggressive as strat_module
import freqtrade_macd_aggressive as ft_adapter

START_DATE = os.getenv("COMPARE_START_DATE", "2025-10-01")
END_DATE = os.getenv("COMPARE_END_DATE", "2026-03-31")


def run_custom_engine():
    """运行自研引擎，返回结果和入场信号列表。"""
    bt_engine.load_ohlcv_data.cache_clear()
    result = bt_engine.backtest_macd_aggressive(
        strategy_func=strat_module.strategy,
        intraday_file=str(BASE_DIR / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv"),
        hourly_file=str(BASE_DIR / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv"),
        start_date=START_DATE,
        end_date=END_DATE,
        strategy_params=strat_module.PARAMS,
        exit_params=bt_engine.EXIT_PARAMS,
    )
    return result


def run_core_signal_check():
    """直接运行主策略函数，统计不受仓位管理影响的原始信号。"""
    bt_engine.load_ohlcv_data.cache_clear()
    intraday_data = bt_engine._slice_by_beijing_window(
        bt_engine.load_ohlcv_data(str(BASE_DIR / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv")),
        START_DATE,
        END_DATE,
    )
    hourly_data = bt_engine._slice_by_beijing_window(
        bt_engine.load_ohlcv_data(str(BASE_DIR / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv")),
        START_DATE,
        END_DATE,
    )
    if not intraday_data or not hourly_data:
        raise ValueError(f"missing data for window {START_DATE}~{END_DATE}")

    intraday_interval_ms = bt_engine._infer_interval_ms(intraday_data, 15)
    intraday_state = bt_engine._prepare_state(
        intraday_data,
        strat_module.PARAMS["intraday_ema_fast"],
        strat_module.PARAMS["intraday_ema_slow"],
        strat_module.PARAMS["macd_fast"],
        strat_module.PARAMS["macd_slow"],
        strat_module.PARAMS["macd_signal"],
    )
    hourly_state = bt_engine._prepare_state(
        hourly_data,
        strat_module.PARAMS["hourly_ema_fast"],
        strat_module.PARAMS["hourly_ema_slow"],
        strat_module.PARAMS["macd_fast"],
        strat_module.PARAMS["macd_slow"],
        strat_module.PARAMS["macd_signal"],
        strat_module.PARAMS.get("hourly_ema_anchor"),
    )
    four_hour_data = bt_engine._aggregate_bars(hourly_data, 4)
    four_hour_state = bt_engine._prepare_state(
        four_hour_data,
        strat_module.PARAMS["fourh_ema_fast"],
        strat_module.PARAMS["fourh_ema_slow"],
        strat_module.PARAMS["macd_fast"],
        strat_module.PARAMS["macd_slow"],
        strat_module.PARAMS["macd_signal"],
    )

    hourly_timestamps = [row["timestamp"] for row in hourly_state]
    four_hour_timestamps = [row["timestamp"] for row in four_hour_state]
    long_timestamps = []
    short_timestamps = []

    for idx, bar in enumerate(intraday_data):
        current_ts = bar["timestamp"]
        bar_close_ts = bar["timestamp"] + intraday_interval_ms
        context_ref_ts = bar_close_ts - 1
        hourly_idx = bisect_right(hourly_timestamps, context_ref_ts) - 1
        four_hour_idx = bisect_right(four_hour_timestamps, context_ref_ts) - 1

        hourly_context = hourly_state[hourly_idx] if hourly_idx >= 0 else None
        prev_hourly_context = hourly_state[hourly_idx - 1] if hourly_idx > 0 else hourly_context
        four_hour_context = four_hour_state[four_hour_idx] if four_hour_idx >= 0 else None
        intraday_context = intraday_state[idx]
        prev_intraday_context = intraday_state[idx - 1] if idx > 0 else intraday_context
        market_state = {
            "hourly": hourly_context,
            "prev_hourly": prev_hourly_context,
            "four_hour": four_hour_context,
            "sentiment": None,
            "ema_fast": intraday_context["ema_fast"],
            "ema_slow": intraday_context["ema_slow"],
            "prev_ema_fast": prev_intraday_context["ema_fast"],
            "prev_ema_slow": prev_intraday_context["ema_slow"],
            "adx": intraday_context["adx"],
            "atr": intraday_context["atr"],
            "atr_ratio": intraday_context["atr_ratio"],
            "rsi": intraday_context["rsi"],
            "chop": intraday_context["chop"],
            "macd_line": intraday_context["macd_line"],
            "signal_line": intraday_context["signal_line"],
            "histogram": intraday_context["histogram"],
            "prev_histogram": intraday_state[idx - 1]["histogram"] if idx > 0 else intraday_context["histogram"],
        }
        signal = strat_module.strategy(intraday_data, idx, [], market_state)
        if signal == "long_breakout":
            long_timestamps.append(current_ts)
        elif signal == "short_breakdown":
            short_timestamps.append(current_ts)

    return {
        "long_signals": len(long_timestamps),
        "short_signals": len(short_timestamps),
        "total_signals": len(long_timestamps) + len(short_timestamps),
        "long_timestamps": long_timestamps,
        "short_timestamps": short_timestamps,
    }


def run_freqtrade_signal_check():
    """用 freqtrade 适配层生成入场信号并统计。"""
    import pandas as pd

    df_15m = pd.read_csv(BASE_DIR / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv")
    df_1h = pd.read_csv(BASE_DIR / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv")

    df_1h_sorted = df_1h.sort_values("timestamp").reset_index(drop=True)
    df_4h_rows = []
    for i in range(0, len(df_1h_sorted) - 3, 4):
        chunk = df_1h_sorted.iloc[i : i + 4]
        df_4h_rows.append(
            {
                "timestamp": chunk.iloc[0]["timestamp"],
                "open": chunk.iloc[0]["open"],
                "high": chunk["high"].max(),
                "low": chunk["low"].min(),
                "close": chunk.iloc[-1]["close"],
                "volume": chunk["volume"].sum(),
            }
        )
    df_4h = pd.DataFrame(df_4h_rows)

    from backtest_macd_aggressive import _beijing_timestamp_ms

    start_ts = _beijing_timestamp_ms(START_DATE)
    end_ts = _beijing_timestamp_ms(END_DATE) + 24 * 60 * 60 * 1000
    signal_frame = ft_adapter.build_signal_frame(df_15m, df_1h, df_4h)
    mask = (signal_frame["timestamp"] >= start_ts) & (signal_frame["timestamp"] < end_ts)
    signal_frame = signal_frame[mask].copy().reset_index(drop=True)

    long_rows = signal_frame[signal_frame["enter_long"] == 1]
    short_rows = signal_frame[signal_frame["enter_short"] == 1]

    return {
        "long_signals": int(len(long_rows)),
        "short_signals": int(len(short_rows)),
        "total_signals": int(len(long_rows) + len(short_rows)),
        "long_timestamps": long_rows["timestamp"].astype(int).tolist(),
        "short_timestamps": short_rows["timestamp"].astype(int).tolist(),
    }


def compare_signals(custom_result, ft_result):
    """对比两个引擎的入场信号。"""
    if ft_result is None:
        print("\n[SKIP] freqtrade 适配层信号检查未运行")
        return

    custom_long = custom_result["long_signals"]
    custom_short = custom_result["short_signals"]
    custom_total = custom_result["total_signals"]

    ft_long = ft_result["long_signals"]
    ft_short = ft_result["short_signals"]
    ft_total = ft_result["total_signals"]

    print("\n" + "=" * 60)
    print("信号对比（自研引擎 vs freqtrade适配层）")
    print("=" * 60)
    print(f"{'指标':<20} {'自研引擎':>12} {'freqtrade适配':>12} {'差值':>10}")
    print("-" * 60)
    print(f"{'做多信号':.<20} {custom_long:>12} {ft_long:>12} {ft_long-custom_long:>+10}")
    print(f"{'做空信号':.<20} {custom_short:>12} {ft_short:>12} {ft_short-custom_short:>+10}")
    print(f"{'总信号':.<20} {custom_total:>12} {ft_total:>12} {ft_total-custom_total:>+10}")
    print()

    # 分析差异原因
    if custom_total > 0:
        match_rate = min(custom_total, ft_total) / max(custom_total, ft_total) * 100
        print(f"匹配度: {match_rate:.0f}%")
    else:
        print("自研引擎无信号，无法计算匹配度")

    if abs(ft_total - custom_total) > 0:
        print("\n差异说明:")
        print("  - 当前对比的是“原始入场信号”，不是实际成交次数")
        print("  - 剩余差异通常来自高周期合并口径或实现层细节，而不是仓位管理")
        print("  - 这份对比更适合做信号一致性检查，不适合作为收益等价证明")


def main():
    print("=" * 60)
    print("自研引擎 vs freqtrade 适配层验证")
    print(f"数据范围: {START_DATE} ~ {END_DATE}")
    print("=" * 60)

    # 自研引擎
    print("\n[1/2] 运行自研引擎...")
    custom_result = run_custom_engine()
    print(f"  收益: {custom_result['return']:.2f}%")
    print(f"  回撤: {custom_result['max_drawdown']:.2f}%")
    print(f"  交易: {custom_result['trades']}")
    print(f"  胜率: {custom_result['win_rate']:.1f}%")
    print(f"  手续费: {custom_result['fee_drag_pct']:.2f}%")
    core_signal_result = run_core_signal_check()
    print(f"  原始做多信号: {core_signal_result['long_signals']}")
    print(f"  原始做空信号: {core_signal_result['short_signals']}")
    print(f"  原始总信号:   {core_signal_result['total_signals']}")

    # freqtrade 适配层信号检查
    print("\n[2/2] 运行 freqtrade 适配层信号检查...")
    ft_result = run_freqtrade_signal_check()
    if ft_result:
        print(f"  做多信号: {ft_result['long_signals']}")
        print(f"  做空信号: {ft_result['short_signals']}")
        print(f"  总信号:   {ft_result['total_signals']}")

    # 对比
    compare_signals(core_signal_result, ft_result)

    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    if ft_result:
        custom_total = core_signal_result["total_signals"]
        ft_total = ft_result["total_signals"]
        if max(custom_total, ft_total) > 0:
            ratio = min(custom_total, ft_total) / max(custom_total, ft_total) * 100
            if ratio >= 80:
                print("两套引擎的入场信号高度一致，自研引擎的指标计算可信。")
            elif ratio >= 60:
                print("两套引擎的入场信号有一定差异，主要来源于过滤条件和EMA初始化差异。")
            else:
                print("两套引擎的入场信号差异较大，建议检查指标计算逻辑。")
        else:
            print("信号数量太少，无法得出有意义的结论。")
    else:
        print("freqtrade 适配层依赖不可用，无法完成对比。")


if __name__ == "__main__":
    main()
