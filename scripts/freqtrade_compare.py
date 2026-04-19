#!/usr/bin/env python3
"""自研引擎 vs freqtrade 适配层对比验证脚本。

重点验证“入场信号层”是否一致。
freqtrade 侧通过 src/freqtrade_macd_aggressive.py 的适配函数生成信号，
避免再维护第三份手写近似逻辑。
"""
import sys
import os
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

# ── 第一步：用自研引擎跑回测，收集入场信号时间戳 ──
import backtest_macd_aggressive as bt_engine
import strategy_macd_aggressive as strat_module
import freqtrade_macd_aggressive as ft_adapter

START_DATE = os.getenv("COMPARE_START_DATE", "2025-10-01")
END_DATE = os.getenv("COMPARE_END_DATE", "2026-03-31")
BEIJING_TZ = timezone(timedelta(hours=8))
DIFF_SAMPLE_LIMIT = 5


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
    intraday_all = bt_engine.load_ohlcv_data(str(BASE_DIR / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv"))
    hourly_all = bt_engine._aggregate_bars(intraday_all, 4)
    start_idx, end_idx = bt_engine._beijing_window_indices(intraday_all, START_DATE, END_DATE)
    if start_idx >= end_idx or not hourly_all:
        raise ValueError(f"missing data for window {START_DATE}~{END_DATE}")

    intraday_interval_ms = bt_engine._infer_interval_ms(intraday_all, 15)
    hourly_interval_ms = bt_engine._infer_interval_ms(hourly_all, 60)
    flow_lookback = strat_module.PARAMS.get("flow_lookback", strat_module.PARAMS["volume_lookback"])
    intraday_state = bt_engine._prepare_state(
        intraday_all,
        strat_module.PARAMS["intraday_ema_fast"],
        strat_module.PARAMS["intraday_ema_slow"],
        strat_module.PARAMS["macd_fast"],
        strat_module.PARAMS["macd_slow"],
        strat_module.PARAMS["macd_signal"],
        flow_lookback=flow_lookback,
    )
    hourly_state = bt_engine._prepare_state(
        hourly_all,
        strat_module.PARAMS["hourly_ema_fast"],
        strat_module.PARAMS["hourly_ema_slow"],
        strat_module.PARAMS["macd_fast"],
        strat_module.PARAMS["macd_slow"],
        strat_module.PARAMS["macd_signal"],
        strat_module.PARAMS.get("hourly_ema_anchor"),
        flow_lookback=flow_lookback,
    )
    four_hour_data = bt_engine._aggregate_bars(intraday_all, 16)
    four_hour_state = bt_engine._prepare_state(
        four_hour_data,
        strat_module.PARAMS["fourh_ema_fast"],
        strat_module.PARAMS["fourh_ema_slow"],
        strat_module.PARAMS["macd_fast"],
        strat_module.PARAMS["macd_slow"],
        strat_module.PARAMS["macd_signal"],
        flow_lookback=flow_lookback,
    )

    four_hour_interval_ms = bt_engine._infer_interval_ms(four_hour_data, 240) if four_hour_data else 240 * 60_000
    hourly_close_timestamps = [row["timestamp"] + hourly_interval_ms for row in hourly_state]
    four_hour_close_timestamps = [row["timestamp"] + four_hour_interval_ms for row in four_hour_state]
    long_timestamps = []
    short_timestamps = []

    for idx in range(start_idx, end_idx):
        bar = intraday_all[idx]
        current_ts = bar["timestamp"]
        bar_close_ts = bar["timestamp"] + intraday_interval_ms
        context_ref_ts = bar_close_ts
        hourly_idx = bisect_right(hourly_close_timestamps, context_ref_ts) - 1
        four_hour_idx = bisect_right(four_hour_close_timestamps, context_ref_ts) - 1

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
            "trade_count": intraday_context["trade_count"],
            "trade_count_ratio": intraday_context["trade_count_ratio"],
            "taker_buy_volume": intraday_context["taker_buy_volume"],
            "taker_sell_volume": intraday_context["taker_sell_volume"],
            "taker_buy_ratio": intraday_context["taker_buy_ratio"],
            "taker_sell_ratio": intraday_context["taker_sell_ratio"],
            "flow_imbalance": intraday_context["flow_imbalance"],
            "rsi": intraday_context["rsi"],
            "chop": intraday_context["chop"],
            "macd_line": intraday_context["macd_line"],
            "signal_line": intraday_context["signal_line"],
            "histogram": intraday_context["histogram"],
            "prev_histogram": intraday_state[idx - 1]["histogram"] if idx > 0 else intraday_context["histogram"],
        }
        signal = strat_module.strategy(intraday_all, idx, [], market_state)
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

    from backtest_macd_aggressive import _beijing_timestamp_ms

    start_ts = _beijing_timestamp_ms(START_DATE)
    end_ts = _beijing_timestamp_ms(END_DATE) + 24 * 60 * 60 * 1000
    signal_frame = ft_adapter.build_signal_frame(df_15m)
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


def _overlap_metrics(core_items, freqtrade_items):
    core_set = set(core_items)
    freqtrade_set = set(freqtrade_items)
    overlap = sorted(core_set & freqtrade_set)
    only_core = sorted(core_set - freqtrade_set)
    only_freqtrade = sorted(freqtrade_set - core_set)

    if core_set or freqtrade_set:
        count_similarity = min(len(core_set), len(freqtrade_set)) / max(len(core_set), len(freqtrade_set))
        jaccard = len(overlap) / len(core_set | freqtrade_set)
    else:
        count_similarity = 1.0
        jaccard = 1.0

    precision = len(overlap) / len(freqtrade_set) if freqtrade_set else (1.0 if not core_set else 0.0)
    recall = len(overlap) / len(core_set) if core_set else (1.0 if not freqtrade_set else 0.0)
    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    return {
        "core_count": len(core_set),
        "freqtrade_count": len(freqtrade_set),
        "overlap_count": len(overlap),
        "only_core_count": len(only_core),
        "only_freqtrade_count": len(only_freqtrade),
        "count_similarity": count_similarity,
        "jaccard": jaccard,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "overlap": overlap,
        "only_core": only_core,
        "only_freqtrade": only_freqtrade,
    }


def _format_timestamp_ms(timestamp_ms):
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def _format_timestamp_list(timestamps):
    if not timestamps:
        return "-"
    return ", ".join(_format_timestamp_ms(ts) for ts in timestamps[:DIFF_SAMPLE_LIMIT])


def _print_side_diff_examples(label, metrics):
    print(f"{label}:")
    if metrics["only_core_count"] == 0 and metrics["only_freqtrade_count"] == 0:
        print("  时间戳集合完全一致")
        return
    print(
        f"  仅自研 {metrics['only_core_count']} 个，示例: {_format_timestamp_list(metrics['only_core'])}"
    )
    print(
        "  仅 freqtrade "
        f"{metrics['only_freqtrade_count']} 个，示例: {_format_timestamp_list(metrics['only_freqtrade'])}"
    )


def compare_signals(custom_result, ft_result):
    """对比两个引擎的原始入场信号数量和时间戳集合。"""
    if ft_result is None:
        print("\n[SKIP] freqtrade 适配层信号检查未运行")
        return

    custom_long = custom_result["long_signals"]
    custom_short = custom_result["short_signals"]
    custom_total = custom_result["total_signals"]

    ft_long = ft_result["long_signals"]
    ft_short = ft_result["short_signals"]
    ft_total = ft_result["total_signals"]
    long_metrics = _overlap_metrics(custom_result["long_timestamps"], ft_result["long_timestamps"])
    short_metrics = _overlap_metrics(custom_result["short_timestamps"], ft_result["short_timestamps"])
    total_metrics = _overlap_metrics(
        [("long", ts) for ts in custom_result["long_timestamps"]]
        + [("short", ts) for ts in custom_result["short_timestamps"]],
        [("long", ts) for ts in ft_result["long_timestamps"]]
        + [("short", ts) for ts in ft_result["short_timestamps"]],
    )

    print("\n" + "=" * 60)
    print("信号对比（自研引擎 vs freqtrade适配层）")
    print("=" * 60)
    print("数量对比:")
    print(f"{'指标':<20} {'自研引擎':>12} {'freqtrade适配':>12} {'差值':>10}")
    print("-" * 60)
    print(f"{'做多信号':.<20} {custom_long:>12} {ft_long:>12} {ft_long-custom_long:>+10}")
    print(f"{'做空信号':.<20} {custom_short:>12} {ft_short:>12} {ft_short-custom_short:>+10}")
    print(f"{'总信号':.<20} {custom_total:>12} {ft_total:>12} {ft_total-custom_total:>+10}")
    print()

    print("时间戳集合对比:")
    print(
        f"{'方向':<12} {'交集':>8} {'仅自研':>10} {'仅freqtrade':>14} "
        f"{'Precision':>11} {'Recall':>9} {'F1':>8}"
    )
    print("-" * 80)
    for label, metrics in (
        ("做多", long_metrics),
        ("做空", short_metrics),
        ("总体", total_metrics),
    ):
        print(
            f"{label:<12} {metrics['overlap_count']:>8} {metrics['only_core_count']:>10} "
            f"{metrics['only_freqtrade_count']:>14} {metrics['precision'] * 100:>10.1f}% "
            f"{metrics['recall'] * 100:>8.1f}% {metrics['f1'] * 100:>7.1f}%"
        )

    print()
    print(
        "总体数量接近度: "
        f"{total_metrics['count_similarity'] * 100:.1f}%"
    )
    print(
        "总体时间戳交并比(Jaccard): "
        f"{total_metrics['jaccard'] * 100:.1f}%"
    )
    print(
        "总体时间戳F1: "
        f"{total_metrics['f1'] * 100:.1f}%"
    )

    print("\n时间戳差异示例（北京时间）:")
    if total_metrics["only_core_count"] or total_metrics["only_freqtrade_count"]:
        _print_side_diff_examples("做多", long_metrics)
        _print_side_diff_examples("做空", short_metrics)
    else:
        print("做多:")
        print("  时间戳集合完全一致")
        print("做空:")
        print("  时间戳集合完全一致")

    print("\n范围说明:")
    print("  - 当前只验证“原始入场信号”的数量和具体时间戳集合")
    print("  - 这不包含退出、TP1、pyramid、逐笔成交价、权益曲线")
    print("  - 因此即使入场时间戳接近，也不能得出“行为等价”或“收益等价”的结论")

    return total_metrics


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
    total_metrics = compare_signals(core_signal_result, ft_result)

    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    if ft_result:
        if total_metrics["f1"] == 1.0 and total_metrics["core_count"] == total_metrics["freqtrade_count"]:
            print("两套引擎的原始入场时间戳集合完全一致，但这仍然只覆盖入场层。")
        elif total_metrics["f1"] >= 0.95:
            print("两套引擎的原始入场时间戳集合高度接近，但这仍然只覆盖入场层。")
        elif total_metrics["f1"] >= 0.80:
            print("两套引擎的原始入场数量接近，但具体时间戳仍有不可忽略差异。")
        elif total_metrics["f1"] >= 0.60:
            print("两套引擎的原始入场时间戳有明显差异，需要继续检查适配层实现。")
        elif total_metrics["core_count"] == 0 and total_metrics["freqtrade_count"] == 0:
            print("两套引擎在当前窗口都没有原始入场信号，暂时无法判断一致性。")
        else:
            print("两套引擎的原始入场时间戳差异较大，当前还不能认为信号层已对齐。")
        print("这份脚本不能证明退出、加仓、分批止盈或权益曲线已经等价。")
    else:
        print("freqtrade 适配层依赖不可用，无法完成对比。")


if __name__ == "__main__":
    main()
