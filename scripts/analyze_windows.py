#!/usr/bin/env python3
"""分析指定窗口的逐笔交易详情。"""
import csv
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import backtest_macd_aggressive as bt
import strategy_macd_aggressive as strat

INTRADAY_FILE = str(REPO_ROOT / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv")
HOURLY_FILE = str(REPO_ROOT / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv")


def beijing_dt(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, UTC) + timedelta(hours=8)


def run_detailed_backtest(start_date, end_date, label):
    """Run backtest and capture detailed trade info by monkey-patching."""
    detailed_trades = []
    original_close_trade = bt._close_trade

    # We need to capture entry info too, so we'll run normally and then
    # analyze the results
    result = bt.backtest_macd_aggressive(
        strategy_func=strat.strategy,
        intraday_file=INTRADAY_FILE,
        hourly_file=HOURLY_FILE,
        start_date=start_date,
        end_date=end_date,
        strategy_params=strat.PARAMS,
        exit_params=bt.EXIT_PARAMS,
    )
    return result


def analyze_window(start_date, end_date, label):
    """Run a modified backtest that captures per-trade details."""
    from bisect import bisect_right

    intraday_all = bt.load_ohlcv_data(INTRADAY_FILE)
    hourly_all = bt.load_ohlcv_data(HOURLY_FILE)
    intraday_data = bt._slice_by_beijing_window(intraday_all, start_date, end_date)
    hourly_data = bt._slice_by_beijing_window(hourly_all, start_date, end_date)

    exit_p = dict(bt.EXIT_PARAMS)
    leverage = float(exit_p["leverage"])
    position_fraction = float(exit_p["position_fraction"])

    hourly_state = bt._prepare_state(
        hourly_data,
        strat.PARAMS["hourly_ema_fast"],
        strat.PARAMS["hourly_ema_slow"],
        strat.PARAMS["macd_fast"],
        strat.PARAMS["macd_slow"],
        strat.PARAMS["macd_signal"],
        strat.PARAMS.get("hourly_ema_anchor"),
    )
    four_hour_bars = bt._aggregate_bars(hourly_data, 4)
    four_hour_state = bt._prepare_state(
        four_hour_bars,
        strat.PARAMS["fourh_ema_fast"],
        strat.PARAMS["fourh_ema_slow"],
        strat.PARAMS["macd_fast"],
        strat.PARAMS["macd_slow"],
        strat.PARAMS["macd_signal"],
    )
    intraday_state = bt._prepare_state(
        intraday_data,
        strat.PARAMS["intraday_ema_fast"],
        strat.PARAMS["intraday_ema_slow"],
        strat.PARAMS["macd_fast"],
        strat.PARAMS["macd_slow"],
        strat.PARAMS["macd_signal"],
    )

    hourly_timestamps = [r["timestamp"] for r in hourly_state]
    four_hour_timestamps = [r["timestamp"] for r in four_hour_state]
    intraday_interval_ms = bt._infer_interval_ms(intraday_data, 15)

    # Get price range for context
    prices = [bar["close"] for bar in intraday_data]
    price_high = max(bar["high"] for bar in intraday_data)
    price_low = min(bar["low"] for bar in intraday_data)
    price_start = intraday_data[0]["close"]
    price_end = intraday_data[-1]["close"]
    price_change_pct = (price_end - price_start) / price_start * 100

    print(f"\n{'='*70}")
    print(f"  {label}: {start_date} ~ {end_date}")
    print(f"{'='*70}")
    print(f"  BTC 价格: {price_start:.0f} → {price_end:.0f} (变化 {price_change_pct:+.1f}%)")
    print(f"  区间最高: {price_high:.0f}, 最低: {price_low:.0f}")
    print(f"  振幅: {(price_high-price_low)/price_low*100:.1f}%")
    print(f"  K线数: {len(intraday_data)}")
    print()

    # Run the actual backtest for results
    result = run_detailed_backtest(start_date, end_date, label)

    print(f"  总交易: {result['trades']}")
    print(f"  收益: {result['return']:.1f}%")
    print(f"  最大回撤: {result['max_drawdown']:.1f}%")
    print(f"  胜率: {result['win_rate']:.1f}%")
    print(f"  手续费拖累: {result['fee_drag_pct']:.2f}%")
    print(f"  加仓次数: {result['pyramid_add_count']}")

    # Signal stats
    print(f"\n  信号统计:")
    for sig, stats in result.get("signal_stats", {}).items():
        print(f"    {sig}: 入场{stats['entries']}次, 平仓{stats['closed_trades']}笔, "
              f"盈亏{stats['pnl_amount']:.0f}, 胜率{stats['win_rate']:.0f}%")

    # Exit reason stats
    print(f"\n  退出原因:")
    print(f"    止损: {result['stop_exit_count']}")
    print(f"    第一止盈: {result['first_tp_count']}")
    print(f"    趋势失效: {result['regime_exit_count']}")
    print(f"    反向信号: {result['reverse_exit_count']}")
    print(f"    时间退出: {result['time_exit_count']}")
    print(f"    爆仓: {result['liquidations']}")

    # Now run a more detailed version to capture individual trades
    print(f"\n  逐笔交易明细:")
    print(f"  {'序号':>4} | {'方向':<6} | {'入场日期':<12} | {'入场价':>10} | {'盈亏%':>8} | {'盈亏$':>8} | {'持仓':>4} | {'退出原因':<8} | {'加仓':>2}")

    # We need a modified backtest - let's do a simplified trace
    _run_trade_trace(intraday_data, hourly_state, four_hour_state, intraday_state,
                     hourly_timestamps, four_hour_timestamps, intraday_interval_ms, exit_p)

    return result


def _run_trade_trace(intraday_data, hourly_state, four_hour_state, intraday_state,
                     hourly_timestamps, four_hour_timestamps, intraday_interval_ms, exit_p):
    """Simplified backtest that prints each trade as it closes."""
    from bisect import bisect_right

    leverage = float(exit_p["leverage"])
    position_fraction = float(exit_p["position_fraction"])
    position_size_min = float(exit_p["position_size_min"])
    position_size_max = float(exit_p["position_size_max"])
    max_concurrent_positions = int(exit_p["max_concurrent_positions"])
    slippage_pct = float(exit_p.get("slippage_pct", 0.0003))
    delay_minutes = int(exit_p.get("entry_delay_minutes", 1))
    taker_fee_rate = float(exit_p["okx_taker_fee_rate"]) if int(exit_p.get("trading_fee_enabled", 1)) > 0 else 0.0

    capital = 100000.0
    positions = []
    trade_num = 0
    sentiment_timestamps = []
    sentiment_state = []

    # Load execution data
    execution_file = str(REPO_ROOT / "data/price/BTCUSDT_futures_1m_20240601_20260401.csv")
    execution_rows = []
    execution_timestamps = []
    if Path(execution_file).exists() and int(exit_p.get("execution_use_1m", 1)) > 0:
        start_ts = intraday_data[0]["timestamp"]
        end_ts = intraday_data[-1]["timestamp"] + intraday_interval_ms
        execution_all = bt.load_ohlcv_data(execution_file)
        execution_rows = bt._slice_by_timestamp_window(execution_all, start_ts, end_ts + 60_000)
        execution_timestamps = [r["timestamp"] for r in execution_rows]

    # Load funding data
    funding_file = str(REPO_ROOT / "data/funding/OKX_BTC_USDT_SWAP_funding_20240601_20260401.csv")
    funding_rows = []
    funding_timestamps = []
    if Path(funding_file).exists() and int(exit_p.get("funding_fee_enabled", 1)) > 0:
        start_ts = intraday_data[0]["timestamp"]
        end_ts = intraday_data[-1]["timestamp"] + intraday_interval_ms
        funding_all = bt.load_funding_data(funding_file)
        funding_rows = bt._slice_by_timestamp_window(funding_all, start_ts, end_ts)
        funding_timestamps = [r["timestamp"] for r in funding_rows]

    funding_idx = 0

    for idx, bar in enumerate(intraday_data):
        prev_bar = intraday_data[idx - 1] if idx > 0 else None
        prev_bar_close_ts = prev_bar["timestamp"] + intraday_interval_ms if prev_bar else bar["timestamp"]
        bar_close_ts = bar["timestamp"] + intraday_interval_ms
        current_ts = bar["timestamp"]
        current_dt = beijing_dt(current_ts)
        current_hour_open = current_dt.replace(minute=0, second=0, microsecond=0)
        current_hour_open_ts = int((current_hour_open - timedelta(hours=8)).timestamp() * 1000)
        hourly_idx = bisect_right(hourly_timestamps, current_hour_open_ts - 1) - 1
        four_hour_idx = bisect_right(four_hour_timestamps, current_hour_open_ts - 1) - 1

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

        market_fill_price = bt._execution_price(
            bar["timestamp"], bar["close"], execution_timestamps, execution_rows,
            intraday_interval_ms, delay_minutes,
        )
        risk_profile = bt._market_risk_profile(market_state, exit_p)

        # Funding
        if funding_rows and positions:
            while funding_idx < len(funding_rows) and funding_timestamps[funding_idx] <= bar_close_ts:
                funding_row = funding_rows[funding_idx]
                if funding_row["timestamp"] > prev_bar_close_ts:
                    settlement_price = bt._price_before_timestamp(
                        funding_row["timestamp"], execution_timestamps, execution_rows, bar["close"])
                    for pos in positions:
                        bt._apply_funding(pos, funding_row["funding_rate"], settlement_price, leverage)
                funding_idx += 1

        # Process positions
        remaining = []
        for pos in positions:
            side = bt._position_side(pos)
            pos["hold_bars"] += 1
            close_pnl_pct = bt._position_pnl_pct(pos, bar["close"], leverage)

            if side == "short":
                best_pnl_pct = bt._position_pnl_pct(pos, bar["low"], leverage)
                worst_pnl_pct = bt._position_pnl_pct(pos, bar["high"], leverage)
                pos["favorable_price"] = min(pos["favorable_price"], bar["low"])
            else:
                best_pnl_pct = bt._position_pnl_pct(pos, bar["high"], leverage)
                worst_pnl_pct = bt._position_pnl_pct(pos, bar["low"], leverage)
                pos["favorable_price"] = max(pos["favorable_price"], bar["high"])
            pos["peak_pnl_pct"] = max(pos["peak_pnl_pct"], best_pnl_pct)

            def print_trade(reason, pnl_pct, exit_price):
                nonlocal trade_num
                trade_num += 1
                direction = "做空" if side == "short" else "做多"
                entry_dt = beijing_dt(pos["entry_ts"]).strftime("%m-%d %H:%M")
                exit_dt = current_dt.strftime("%m-%d %H:%M")
                pnl_amount = pos["size"] * pnl_pct / 100
                pyramids = pos.get("pyramids_done", 0)
                print(f"  {trade_num:>4} | {direction:<6} | {entry_dt:<12} | {pos['entry_price']:>10.0f} | "
                      f"{pnl_pct:>+7.1f}% | {pnl_amount:>+7.0f}$ | {pos['hold_bars']:>4}根 | {reason:<8} | {pyramids:>2}")

            # Liquidation
            if worst_pnl_pct <= -100.0:
                capital += pos.get("funding_pnl", 0.0)
                print_trade("爆仓", -100.0, bar["close"])
                continue

            # Stop loss
            stop_hit = bar["high"] >= pos["stop_price"] if side == "short" else bar["low"] <= pos["stop_price"]
            if stop_hit:
                stop_fill = bt._fill_with_slippage(pos["stop_price"], side, False, slippage_pct)
                exit_notional = bt._position_notional(pos, stop_fill, leverage)
                exit_fee = bt._trading_fee_amount(exit_notional, exit_p)
                entry_fee = pos.get("entry_fee_paid", 0.0)
                funding = pos.get("funding_pnl", 0.0)
                gross = bt._position_pnl_amount(pos, stop_fill, leverage)
                net = gross - entry_fee - exit_fee + funding
                pnl_pct_net = net / max(pos["size"], 1e-9) * 100
                capital += bt._close_cash_release(pos["size"], gross, exit_fee=exit_fee, funding_pnl=funding)
                print_trade("止损", pnl_pct_net, stop_fill)
                continue

            # Pyramid
            if bt._should_pyramid(pos, market_state, close_pnl_pct, exit_p, allow_pyramid=risk_profile["allow_pyramid"]):
                pyramid_fill = bt._fill_with_slippage(market_fill_price, side, True, slippage_pct)
                max_affordable = capital / (1.0 + leverage * taker_fee_rate)
                add_size = min(max_affordable, pos["size"] * float(exit_p.get("pyramid_size_ratio", 0.5)), position_size_max)
                if add_size >= position_size_min:
                    add_fee = bt._trading_fee_amount(add_size * leverage, exit_p)
                    total_size = pos["size"] + add_size
                    pos["entry_price"] = (pos["entry_price"] * pos["size"] + pyramid_fill * add_size) / total_size
                    pos["size"] = total_size
                    pos["pyramids_done"] = pos.get("pyramids_done", 0) + 1
                    pos["entry_fee_paid"] = pos.get("entry_fee_paid", 0.0) + add_fee
                    capital -= add_size + add_fee

            # TP1
            tp1_pnl_pct = float(bt._exit_value(exit_p, pos, "tp1_pnl_pct"))
            tp1_close_fraction = float(bt._exit_value(exit_p, pos, "tp1_close_fraction"))
            if (not pos["tp1_done"]) and best_pnl_pct >= tp1_pnl_pct:
                tp1_trigger = bt._tp_trigger_price(pos["entry_price"], tp1_pnl_pct, leverage, side)
                tp1_fill = bt._fill_with_slippage(tp1_trigger, side, False, slippage_pct)
                close_size = pos["size"] * tp1_close_fraction
                remaining_size = pos["size"] - close_size
                close_frac = close_size / max(pos["size"], 1e-9)
                alloc_entry_fee = pos.get("entry_fee_paid", 0.0) * close_frac
                alloc_funding = pos.get("funding_pnl", 0.0) * close_frac
                partial = dict(pos)
                partial["size"] = close_size
                exit_notional = bt._position_notional(partial, tp1_fill, leverage)
                exit_fee = bt._trading_fee_amount(exit_notional, exit_p)
                gross = bt._position_pnl_amount(partial, tp1_fill, leverage)
                capital += bt._close_cash_release(close_size, gross, exit_fee=exit_fee, funding_pnl=alloc_funding)
                pos["size"] = remaining_size
                pos["entry_fee_paid"] = pos.get("entry_fee_paid", 0.0) - alloc_entry_fee
                pos["funding_pnl"] = pos.get("funding_pnl", 0.0) - alloc_funding
                pos["tp1_done"] = True
                if pos["size"] <= 1e-9:
                    trade_num += 1
                    print(f"  {trade_num:>4} | {'做空' if side=='short' else '做多':<6} | {beijing_dt(pos['entry_ts']).strftime('%m-%d %H:%M'):<12} | {pos['entry_price']:>10.0f} | {'TP1全平':<8}")
                    continue

            # Break-even
            be_act = float(bt._exit_value(exit_p, pos, "break_even_activation_pct"))
            if pos["peak_pnl_pct"] >= be_act:
                if side == "short":
                    be_price = pos["entry_price"] * (1.0 - float(exit_p["break_even_buffer_pct"]) / 100.0)
                    pos["stop_price"] = min(pos["stop_price"], be_price)
                else:
                    be_price = pos["entry_price"] * (1.0 + float(exit_p["break_even_buffer_pct"]) / 100.0)
                    pos["stop_price"] = max(pos["stop_price"], be_price)

            # Trailing
            trail_act = float(bt._exit_value(exit_p, pos, "trailing_activation_pct"))
            trail_gb = float(bt._exit_value(exit_p, pos, "trailing_giveback_pct"))
            if pos["peak_pnl_pct"] >= trail_act:
                gap = trail_gb / leverage / 100.0
                if side == "short":
                    trail_price = pos["favorable_price"] * (1.0 + gap)
                    pos["stop_price"] = min(pos["stop_price"], trail_price)
                else:
                    trail_price = pos["favorable_price"] * (1.0 - gap)
                    pos["stop_price"] = max(pos["stop_price"], trail_price)

            # Regime exit
            if int(exit_p["regime_exit_enabled"]) > 0 and hourly_context is not None:
                regime_broken = bt._confirmed_regime_break(pos, exit_p, bar, prev_bar, market_state)
                if regime_broken and close_pnl_pct < trail_act:
                    regime_fill = bt._fill_with_slippage(market_fill_price, side, False, slippage_pct)
                    exit_notional = bt._position_notional(pos, regime_fill, leverage)
                    exit_fee = bt._trading_fee_amount(exit_notional, exit_p)
                    entry_fee = pos.get("entry_fee_paid", 0.0)
                    funding = pos.get("funding_pnl", 0.0)
                    gross = bt._position_pnl_amount(pos, regime_fill, leverage)
                    net = gross - entry_fee - exit_fee + funding
                    pnl_pct_net = net / max(pos["size"], 1e-9) * 100
                    capital += bt._close_cash_release(pos["size"], gross, exit_fee=exit_fee, funding_pnl=funding)
                    print_trade("趋势失效", pnl_pct_net, regime_fill)
                    continue

            # Time exit
            hold_limit = bt._resolve_hold_limit(pos, exit_p, market_state, close_pnl_pct)
            if pos["hold_bars"] >= hold_limit:
                time_fill = bt._fill_with_slippage(market_fill_price, side, False, slippage_pct)
                exit_notional = bt._position_notional(pos, time_fill, leverage)
                exit_fee = bt._trading_fee_amount(exit_notional, exit_p)
                entry_fee = pos.get("entry_fee_paid", 0.0)
                funding = pos.get("funding_pnl", 0.0)
                gross = bt._position_pnl_amount(pos, time_fill, leverage)
                net = gross - entry_fee - exit_fee + funding
                pnl_pct_net = net / max(pos["size"], 1e-9) * 100
                capital += bt._close_cash_release(pos["size"], gross, exit_fee=exit_fee, funding_pnl=funding)
                print_trade("时间退出", pnl_pct_net, time_fill)
                continue

            remaining.append(pos)
        positions = remaining

        # Entry signals
        signal = strat.strategy(intraday_data, idx, positions, market_state)
        if signal and positions and bt._signal_side(signal) != bt._position_side(positions[0]):
            for pos in positions:
                rev_side = bt._position_side(pos)
                rev_fill = bt._fill_with_slippage(market_fill_price, rev_side, False, slippage_pct)
                exit_notional = bt._position_notional(pos, rev_fill, leverage)
                exit_fee = bt._trading_fee_amount(exit_notional, exit_p)
                entry_fee = pos.get("entry_fee_paid", 0.0)
                funding = pos.get("funding_pnl", 0.0)
                gross = bt._position_pnl_amount(pos, rev_fill, leverage)
                net = gross - entry_fee - exit_fee + funding
                pnl_pct_net = net / max(pos["size"], 1e-9) * 100
                capital += bt._close_cash_release(pos["size"], gross, exit_fee=exit_fee, funding_pnl=funding)
                direction = "做空" if rev_side == "short" else "做多"
                trade_num += 1
                entry_dt = beijing_dt(pos["entry_ts"]).strftime("%m-%d %H:%M")
                pnl_amount = pos["size"] * pnl_pct_net / 100
                print(f"  {trade_num:>4} | {direction:<6} | {entry_dt:<12} | {pos['entry_price']:>10.0f} | "
                      f"{pnl_pct_net:>+7.1f}% | {pnl_amount:>+7.0f}$ | {pos['hold_bars']:>4}根 | {'反向信号':<8} | {pos.get('pyramids_done',0):>2}")
            positions = []

        target_size = capital * position_fraction * risk_profile["position_fraction_scale"]
        max_affordable = capital / (1.0 + leverage * taker_fee_rate) if taker_fee_rate > 0 else capital
        target_size = min(position_size_max, target_size, max_affordable)
        if (
            signal
            and len(positions) < risk_profile["max_concurrent_positions"]
            and capital >= position_size_min
            and target_size >= position_size_min
            and market_state["atr"] > 0
            and (not positions or bt._signal_side(signal) == bt._position_side(positions[0]))
        ):
            signal_side = bt._signal_side(signal)
            stop_mult = float(bt._exit_value(exit_p, {"entry_signal": signal}, "stop_atr_mult"))
            if signal_side == "short":
                atr_stop = market_fill_price + market_state["atr"] * stop_mult
                hard_stop = market_fill_price * (1.0 + float(exit_p["stop_max_loss_pct"]) / leverage / 100.0)
                stop_price = min(atr_stop, hard_stop)
                valid = stop_price > market_fill_price
            else:
                atr_stop = market_fill_price - market_state["atr"] * stop_mult
                hard_stop = market_fill_price * (1.0 - float(exit_p["stop_max_loss_pct"]) / leverage / 100.0)
                stop_price = max(atr_stop, hard_stop)
                valid = stop_price < market_fill_price
            if valid:
                entry_fill = bt._fill_with_slippage(market_fill_price, signal_side, True, slippage_pct)
                entry_fee = bt._trading_fee_amount(target_size * leverage, exit_p)
                capital -= target_size + entry_fee
                positions.append({
                    "entry_price": entry_fill,
                    "entry_signal": signal,
                    "size": target_size,
                    "hold_bars": 0,
                    "peak_pnl_pct": 0.0,
                    "favorable_price": entry_fill,
                    "tp1_done": False,
                    "pyramids_done": 0,
                    "entry_fee_paid": entry_fee,
                    "funding_pnl": 0.0,
                    "stop_price": stop_price,
                    "entry_ts": current_ts,
                })

    # Close remaining positions at end
    last_close = intraday_data[-1]["close"]
    for pos in positions:
        side = bt._position_side(pos)
        end_fill = bt._fill_with_slippage(last_close, side, False, slippage_pct)
        exit_notional = bt._position_notional(pos, end_fill, leverage)
        exit_fee = bt._trading_fee_amount(exit_notional, exit_p)
        entry_fee = pos.get("entry_fee_paid", 0.0)
        funding = pos.get("funding_pnl", 0.0)
        gross = bt._position_pnl_amount(pos, end_fill, leverage)
        net = gross - entry_fee - exit_fee + funding
        pnl_pct_net = net / max(pos["size"], 1e-9) * 100
        capital += bt._close_cash_release(pos["size"], gross, exit_fee=exit_fee, funding_pnl=funding)
        direction = "做空" if side == "short" else "做多"
        trade_num += 1
        entry_dt = beijing_dt(pos["entry_ts"]).strftime("%m-%d %H:%M")
        pnl_amount = pos["size"] * pnl_pct_net / 100
        print(f"  {trade_num:>4} | {direction:<6} | {entry_dt:<12} | {pos['entry_price']:>10.0f} | "
              f"{pnl_pct_net:>+7.1f}% | {pnl_amount:>+7.0f}$ | {pos['hold_bars']:>4}根 | {'数据结束':<8} | {pos.get('pyramids_done',0):>2}")

    final_return = (capital - 100000) / 100000 * 100
    print(f"\n  最终资金: {capital:.0f} (收益 {final_return:+.1f}%)")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  窗口3 和 影测2 深度分析")
    print("=" * 70)

    analyze_window("2025-05-13", "2025-06-02", "窗口3 (5月中~6月初)")
    print("\n")
    analyze_window("2026-03-12", "2026-03-31", "影测2 (3月中~3月底)")
