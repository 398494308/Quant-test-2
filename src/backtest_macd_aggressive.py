#!/usr/bin/env python3
"""激进版趋势策略回测器：15m 执行，1h/4h/情绪过滤。"""
import csv
import math
from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INTRADAY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv"
DEFAULT_HOURLY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv"
DEFAULT_SENTIMENT_FILE = REPO_ROOT / "data/index/crypto_fear_greed_daily_20240601_20260401.csv"


# EXIT_PARAMS_START
EXIT_PARAMS = {
    "break_even_activation_pct": 16.0,
    "break_even_buffer_pct": 0.25,
    "breakout_break_even_activation_pct": 18.0,
    "pullback_break_even_activation_pct": 10.0,
    "short_breakdown_break_even_activation_pct": 12.0,
    "short_bounce_fail_break_even_activation_pct": 8.0,
    "dynamic_hold_adx_strong_threshold": 34.0,
    "dynamic_hold_adx_threshold": 24.0,
    "dynamic_hold_extension_bars": 24,
    "dynamic_hold_max_bars": 96,
    "leverage": 18,
    "max_concurrent_positions": 3,
    "max_hold_bars": 72,
    "position_fraction": 0.22,
    "position_size_max": 32000,
    "position_size_min": 4500,
    "pyramid_adx_min": 30.0,
    "pyramid_enabled": 1,
    "pyramid_max_times": 1,
    "pyramid_size_ratio": 0.60,
    "pyramid_trigger_pnl": 18.0,
    "regime_close_below_hourly_fast": 1,
    "regime_exit_enabled": 1,
    "regime_hist_floor": -30.0,
    "breakout_max_hold_bars": 84,
    "pullback_max_hold_bars": 32,
    "short_breakdown_max_hold_bars": 56,
    "short_bounce_fail_max_hold_bars": 24,
    "stop_atr_mult": 2.2,
    "breakout_stop_atr_mult": 2.2,
    "pullback_stop_atr_mult": 1.6,
    "short_breakdown_stop_atr_mult": 1.8,
    "short_bounce_fail_stop_atr_mult": 1.4,
    "breakout_tp1_close_fraction": 0.20,
    "pullback_tp1_close_fraction": 0.45,
    "short_breakdown_tp1_close_fraction": 0.35,
    "short_bounce_fail_tp1_close_fraction": 0.50,
    "breakout_tp1_pnl_pct": 20.0,
    "pullback_tp1_pnl_pct": 10.0,
    "short_breakdown_tp1_pnl_pct": 14.0,
    "short_bounce_fail_tp1_pnl_pct": 8.0,
    "breakout_trailing_activation_pct": 30.0,
    "pullback_trailing_activation_pct": 14.0,
    "short_breakdown_trailing_activation_pct": 20.0,
    "short_bounce_fail_trailing_activation_pct": 10.0,
    "breakout_trailing_giveback_pct": 12.0,
    "pullback_trailing_giveback_pct": 6.0,
    "short_breakdown_trailing_giveback_pct": 7.0,
    "short_bounce_fail_trailing_giveback_pct": 5.0,
    "stop_max_loss_pct": 40.0,
    "tp1_close_fraction": 0.20,
    "tp1_pnl_pct": 20.0,
    "trailing_activation_pct": 30.0,
    "trailing_giveback_pct": 12.0,
}
# EXIT_PARAMS_END


def _exit_value(exit_params, position, key):
    signal = position.get("entry_signal", "")
    exact_key = f"{signal}_{key}"
    if exact_key in exit_params:
        return exit_params[exact_key]
    if signal in {"long_breakout", "short_breakdown"}:
        breakout_key = f"breakout_{key}"
        if breakout_key in exit_params:
            return exit_params[breakout_key]
    elif signal in {"long_pullback", "short_bounce_fail"}:
        pullback_key = f"pullback_{key}"
        if pullback_key in exit_params:
            return exit_params[pullback_key]
    return exit_params[key]


def _signal_side(signal):
    return "short" if signal.startswith("short_") else "long"


def _position_side(position):
    return _signal_side(position.get("entry_signal", "long_breakout"))


@lru_cache(maxsize=12)
def load_ohlcv_data(filename):
    data = []
    with open(filename, "r") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            data.append(
                {
                    "timestamp": int(row["timestamp"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
    return data


@lru_cache(maxsize=4)
def load_sentiment_data(filename):
    rows = []
    with open(filename, "r") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                {
                    "timestamp": int(row["timestamp"]),
                    "value": float(row["value"]),
                    "classification": row.get("classification", ""),
                }
            )
    return rows


def _beijing_dt(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC) + timedelta(hours=8)


def _beijing_timestamp_ms(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC) - timedelta(hours=8)
    return int(dt.timestamp() * 1000)


def _slice_by_beijing_window(data, start_date, end_date):
    start_ts = _beijing_timestamp_ms(start_date)
    end_exclusive_ts = _beijing_timestamp_ms(end_date) + 24 * 60 * 60 * 1000
    timestamps = [row["timestamp"] for row in data]
    start_idx = bisect_right(timestamps, start_ts - 1)
    end_idx = bisect_right(timestamps, end_exclusive_ts - 1)
    return data[start_idx:end_idx]


def _ema_series(values, length):
    alpha = 2.0 / (length + 1.0)
    output = []
    ema = values[0]
    for value in values:
        ema = alpha * value + (1.0 - alpha) * ema
        output.append(ema)
    return output


def _macd_series(values, fast_length, slow_length, signal_length):
    fast = _ema_series(values, fast_length)
    slow = _ema_series(values, slow_length)
    macd_line = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
    signal = _ema_series(macd_line, signal_length)
    histogram = [line - signal_value for line, signal_value in zip(macd_line, signal)]
    return fast, slow, macd_line, signal, histogram


def _true_range_series(data):
    tr = [max(data[0]["high"] - data[0]["low"], 0.0)]
    for idx in range(1, len(data)):
        high = data[idx]["high"]
        low = data[idx]["low"]
        prev_close = data[idx - 1]["close"]
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return tr


def _atr_series(data, length=14):
    return _ema_series(_true_range_series(data), length)


def _adx_series(data, length=14):
    tr = [0.0]
    plus_dm = [0.0]
    minus_dm = [0.0]
    for idx in range(1, len(data)):
        high = data[idx]["high"]
        low = data[idx]["low"]
        prev_high = data[idx - 1]["high"]
        prev_low = data[idx - 1]["low"]
        prev_close = data[idx - 1]["close"]
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    tr_smooth = _ema_series(tr, length)
    plus_dm_smooth = _ema_series(plus_dm, length)
    minus_dm_smooth = _ema_series(minus_dm, length)
    dx = []
    for idx in range(len(data)):
        if tr_smooth[idx] <= 1e-9:
            dx.append(0.0)
            continue
        plus_di = 100.0 * plus_dm_smooth[idx] / tr_smooth[idx]
        minus_di = 100.0 * minus_dm_smooth[idx] / tr_smooth[idx]
        denom = plus_di + minus_di
        dx.append(0.0 if denom <= 1e-9 else 100.0 * abs(plus_di - minus_di) / denom)
    return _ema_series(dx, length)


def _rsi_series(values, length=14):
    gains = [0.0]
    losses = [0.0]
    for idx in range(1, len(values)):
        change = values[idx] - values[idx - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = _ema_series(gains, length)
    avg_loss = _ema_series(losses, length)
    output = []
    for gain, loss in zip(avg_gain, avg_loss):
        if loss <= 1e-9:
            output.append(100.0 if gain > 0 else 50.0)
        else:
            rs = gain / loss
            output.append(100.0 - (100.0 / (1.0 + rs)))
    return output


def _choppiness_series(data, length=14):
    tr = _true_range_series(data)
    output = []
    for idx in range(len(data)):
        if idx < length:
            output.append(55.0)
            continue
        tr_sum = sum(tr[idx - length + 1 : idx + 1])
        highest = max(row["high"] for row in data[idx - length + 1 : idx + 1])
        lowest = min(row["low"] for row in data[idx - length + 1 : idx + 1])
        spread = max(highest - lowest, data[idx]["close"] * 1e-9)
        value = 100.0 * math.log10(max(tr_sum / spread, 1.0)) / math.log10(length)
        output.append(value)
    return output


def _aggregate_bars(data, bars_per_bucket):
    buckets = []
    current = []
    for row in data:
        current.append(row)
        if len(current) == bars_per_bucket:
            buckets.append(
                {
                    "timestamp": current[0]["timestamp"],
                    "open": current[0]["open"],
                    "high": max(item["high"] for item in current),
                    "low": min(item["low"] for item in current),
                    "close": current[-1]["close"],
                    "volume": sum(item["volume"] for item in current),
                }
            )
            current = []
    return buckets


def _prepare_state(data, ema_fast_len, ema_slow_len, macd_fast, macd_slow, macd_signal, ema_anchor_len=None):
    closes = [row["close"] for row in data]
    ema_fast, ema_slow, macd_line, signal_line, histogram = _macd_series(
        closes,
        macd_fast,
        macd_slow,
        macd_signal,
    )
    trend_fast = _ema_series(closes, ema_fast_len)
    trend_slow = _ema_series(closes, ema_slow_len)
    trend_anchor = _ema_series(closes, ema_anchor_len) if ema_anchor_len else trend_slow
    atr = _atr_series(data, 14)
    adx = _adx_series(data, 14)
    rsi = _rsi_series(closes, 14)
    chop = _choppiness_series(data, 14)
    output = []
    for idx, row in enumerate(data):
        prev_slow = trend_slow[idx - 1] if idx > 0 else trend_slow[idx]
        trend_base = max(abs(trend_slow[idx]), row["close"] * 1e-9)
        output.append(
            {
                "timestamp": row["timestamp"],
                "close": row["close"],
                "ema_fast": trend_fast[idx],
                "ema_slow": trend_slow[idx],
                "ema_anchor": trend_anchor[idx],
                "trend_spread_pct": (trend_fast[idx] - trend_slow[idx]) / trend_base,
                "ema_slow_slope_pct": (trend_slow[idx] - prev_slow) / trend_base,
                "macd_line": macd_line[idx],
                "signal_line": signal_line[idx],
                "histogram": histogram[idx],
                "atr": atr[idx],
                "atr_ratio": atr[idx] / max(row["close"], 1e-9),
                "adx": adx[idx],
                "rsi": rsi[idx],
                "chop": chop[idx],
            }
        )
    return output


def _prepare_sentiment_state(rows):
    values = [row["value"] for row in rows]
    ema7 = _ema_series(values, 7)
    output = []
    for idx, row in enumerate(rows):
        prev3 = values[idx - 3] if idx >= 3 else values[0]
        prev7 = values[idx - 7] if idx >= 7 else values[0]
        output.append(
            {
                "timestamp": row["timestamp"],
                "value": row["value"],
                "classification": row["classification"],
                "ema7": ema7[idx],
                "delta3": row["value"] - prev3,
                "delta7": row["value"] - prev7,
            }
        )
    return output


def _position_pnl_pct(position, price, leverage):
    direction = -1.0 if _position_side(position) == "short" else 1.0
    return direction * ((price - position["entry_price"]) / position["entry_price"]) * leverage * 100.0


def _close_trade(position, price, reason, leverage):
    pnl_pct = _position_pnl_pct(position, price, leverage)
    pnl_amount = position["size"] * (pnl_pct / 100.0)
    return {
        "pnl_pct": pnl_pct,
        "pnl_amount": pnl_amount,
        "hold_bars": position["hold_bars"],
        "reason": reason,
        "entry_signal": position["entry_signal"],
        "size": position["size"],
        "pyramids_done": position.get("pyramids_done", 0),
    }, pnl_amount


def _resolve_hold_limit(position, exit_params, market_state, close_pnl_pct):
    base_limit = int(_exit_value(exit_params, position, "max_hold_bars"))
    base_limit = max(20, base_limit)

    hourly = market_state.get("hourly")
    four_hour = market_state.get("four_hour")
    if hourly is None or four_hour is None or close_pnl_pct <= 0:
        return base_limit

    if _position_side(position) == "short":
        trend_alive = (
            hourly["close"] < hourly["ema_fast"] < hourly["ema_slow"]
            and hourly["macd_line"] < hourly["signal_line"]
            and four_hour["close"] < four_hour["ema_fast"] < four_hour["ema_slow"]
            and market_state["adx"] >= float(exit_params["dynamic_hold_adx_threshold"])
            and market_state["macd_line"] < market_state["signal_line"]
        )
    else:
        trend_alive = (
            hourly["close"] > hourly["ema_fast"] > hourly["ema_slow"]
            and hourly["macd_line"] > hourly["signal_line"]
            and four_hour["close"] > four_hour["ema_fast"] > four_hour["ema_slow"]
            and market_state["adx"] >= float(exit_params["dynamic_hold_adx_threshold"])
            and market_state["macd_line"] > market_state["signal_line"]
        )
    if not trend_alive:
        return base_limit

    extension = int(exit_params["dynamic_hold_extension_bars"])
    if market_state["adx"] >= float(exit_params["dynamic_hold_adx_strong_threshold"]):
        extension *= 2
    return min(int(exit_params["dynamic_hold_max_bars"]), base_limit + extension)


def _should_pyramid(position, market_state, close_pnl_pct, exit_p):
    side = _position_side(position)
    return (
        int(exit_p["pyramid_enabled"]) > 0
        and position.get("pyramids_done", 0) < int(exit_p["pyramid_max_times"])
        and position.get("entry_signal") in {"long_breakout", "short_breakdown"}
        and close_pnl_pct >= float(exit_p["pyramid_trigger_pnl"])
        and market_state["adx"] >= float(exit_p["pyramid_adx_min"])
        and (
            market_state["macd_line"] > market_state["signal_line"]
            if side == "long"
            else market_state["macd_line"] < market_state["signal_line"]
        )
        and market_state["hourly"] is not None
        and (
            market_state["hourly"]["close"] > market_state["hourly"]["ema_fast"]
            if side == "long"
            else market_state["hourly"]["close"] < market_state["hourly"]["ema_fast"]
        )
    )


def backtest_macd_aggressive(
    strategy_func,
    intraday_file,
    hourly_file,
    start_date,
    end_date,
    strategy_params,
    exit_params=None,
    sentiment_file=None,
):
    exit_p = dict(EXIT_PARAMS)
    if exit_params:
        exit_p.update(exit_params)

    intraday_file = str(intraday_file or DEFAULT_INTRADAY_FILE)
    hourly_file = str(hourly_file or DEFAULT_HOURLY_FILE)
    sentiment_file = str(sentiment_file or DEFAULT_SENTIMENT_FILE)

    intraday_all = load_ohlcv_data(intraday_file)
    hourly_all = load_ohlcv_data(hourly_file)
    intraday_data = _slice_by_beijing_window(intraday_all, start_date, end_date)
    hourly_data = _slice_by_beijing_window(hourly_all, start_date, end_date)
    if not intraday_data or not hourly_data:
        raise ValueError(f"missing data for window {start_date}~{end_date}")

    sentiment_rows = load_sentiment_data(sentiment_file) if Path(sentiment_file).exists() else []
    sentiment_state = _prepare_sentiment_state(sentiment_rows) if sentiment_rows else []
    sentiment_timestamps = [row["timestamp"] for row in sentiment_state]

    hourly_state = _prepare_state(
        hourly_data,
        strategy_params["hourly_ema_fast"],
        strategy_params["hourly_ema_slow"],
        strategy_params["macd_fast"],
        strategy_params["macd_slow"],
        strategy_params["macd_signal"],
        strategy_params.get("hourly_ema_anchor"),
    )
    four_hour_bars = _aggregate_bars(hourly_data, 4)
    four_hour_state = _prepare_state(
        four_hour_bars,
        strategy_params["fourh_ema_fast"],
        strategy_params["fourh_ema_slow"],
        strategy_params["macd_fast"],
        strategy_params["macd_slow"],
        strategy_params["macd_signal"],
    )
    intraday_state = _prepare_state(
        intraday_data,
        strategy_params["intraday_ema_fast"],
        strategy_params["intraday_ema_slow"],
        strategy_params["macd_fast"],
        strategy_params["macd_slow"],
        strategy_params["macd_signal"],
    )

    hourly_timestamps = [row["timestamp"] for row in hourly_state]
    four_hour_timestamps = [row["timestamp"] for row in four_hour_state]

    capital = 100000.0
    initial_capital = capital
    leverage = float(exit_p["leverage"])
    position_fraction = float(exit_p["position_fraction"])
    position_size_min = float(exit_p["position_size_min"])
    position_size_max = float(exit_p["position_size_max"])
    max_concurrent_positions = int(exit_p["max_concurrent_positions"])
    positions = []
    trades = []
    signal_entries = {}
    signal_closed_pnl = {}
    signal_closed_trades = {}
    signal_closed_wins = {}
    max_equity = capital
    max_drawdown = 0.0
    pyramid_add_count = 0

    def record_trade(trade):
        trades.append(trade)
        signal = trade["entry_signal"]
        signal_closed_pnl[signal] = signal_closed_pnl.get(signal, 0.0) + trade["pnl_amount"]
        signal_closed_trades[signal] = signal_closed_trades.get(signal, 0) + 1
        if trade["pnl_pct"] > 0:
            signal_closed_wins[signal] = signal_closed_wins.get(signal, 0) + 1

    for idx, bar in enumerate(intraday_data):
        current_ts = bar["timestamp"]
        current_dt = _beijing_dt(current_ts)
        current_hour_open = current_dt.replace(minute=0, second=0, microsecond=0)
        current_hour_open_ts = int((current_hour_open - timedelta(hours=8)).timestamp() * 1000)
        hourly_idx = bisect_right(hourly_timestamps, current_hour_open_ts - 1) - 1
        four_hour_idx = bisect_right(four_hour_timestamps, current_hour_open_ts - 1) - 1
        sentiment_idx = bisect_right(sentiment_timestamps, current_ts) - 1

        hourly_context = hourly_state[hourly_idx] if hourly_idx >= 0 else None
        four_hour_context = four_hour_state[four_hour_idx] if four_hour_idx >= 0 else None
        sentiment_context = sentiment_state[sentiment_idx] if sentiment_idx >= 0 else None
        intraday_context = intraday_state[idx]
        market_state = {
            "hourly": hourly_context,
            "four_hour": four_hour_context,
            "sentiment": sentiment_context,
            "ema_fast": intraday_context["ema_fast"],
            "ema_slow": intraday_context["ema_slow"],
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

        remaining = []
        for position in positions:
            side = _position_side(position)
            position["hold_bars"] += 1
            close_pnl_pct = _position_pnl_pct(position, bar["close"], leverage)
            if side == "short":
                best_pnl_pct = _position_pnl_pct(position, bar["low"], leverage)
                worst_pnl_pct = _position_pnl_pct(position, bar["high"], leverage)
                position["favorable_price"] = min(position["favorable_price"], bar["low"])
            else:
                best_pnl_pct = _position_pnl_pct(position, bar["high"], leverage)
                worst_pnl_pct = _position_pnl_pct(position, bar["low"], leverage)
                position["favorable_price"] = max(position["favorable_price"], bar["high"])
            position["peak_pnl_pct"] = max(position["peak_pnl_pct"], best_pnl_pct)

            if worst_pnl_pct <= -100.0:
                record_trade(
                    {
                        "pnl_pct": -100.0,
                        "pnl_amount": -position["size"],
                        "hold_bars": position["hold_bars"],
                        "reason": "爆仓",
                        "entry_signal": position["entry_signal"],
                        "size": position["size"],
                        "pyramids_done": position.get("pyramids_done", 0),
                    }
                )
                continue

            stop_hit = bar["high"] >= position["stop_price"] if side == "short" else bar["low"] <= position["stop_price"]
            if stop_hit:
                trade, pnl_amount = _close_trade(position, position["stop_price"], "止损", leverage)
                capital += position["size"] + pnl_amount
                record_trade(trade)
                continue

            if _should_pyramid(position, market_state, close_pnl_pct, exit_p):
                add_size = min(capital, position["size"] * float(exit_p["pyramid_size_ratio"]), position_size_max)
                if add_size >= position_size_min:
                    total_size = position["size"] + add_size
                    position["entry_price"] = (
                        position["entry_price"] * position["size"] + bar["close"] * add_size
                    ) / total_size
                    position["size"] = total_size
                    position["pyramids_done"] = position.get("pyramids_done", 0) + 1
                    capital -= add_size
                    pyramid_add_count += 1

            tp1_pnl_pct = float(_exit_value(exit_p, position, "tp1_pnl_pct"))
            tp1_close_fraction = float(_exit_value(exit_p, position, "tp1_close_fraction"))
            if (not position["tp1_done"]) and best_pnl_pct >= tp1_pnl_pct:
                close_size = position["size"] * tp1_close_fraction
                remaining_size = position["size"] - close_size
                partial_position = dict(position)
                partial_position["size"] = close_size
                trade, pnl_amount = _close_trade(partial_position, bar["close"], "第一止盈", leverage)
                capital += close_size + pnl_amount
                record_trade(trade)
                position["size"] = remaining_size
                position["tp1_done"] = True
                if position["size"] <= 1e-9:
                    continue

            break_even_activation_pct = float(_exit_value(exit_p, position, "break_even_activation_pct"))
            if position["peak_pnl_pct"] >= break_even_activation_pct:
                if side == "short":
                    breakeven_price = position["entry_price"] * (1.0 - float(exit_p["break_even_buffer_pct"]) / 100.0)
                    position["stop_price"] = min(position["stop_price"], breakeven_price)
                else:
                    breakeven_price = position["entry_price"] * (1.0 + float(exit_p["break_even_buffer_pct"]) / 100.0)
                    position["stop_price"] = max(position["stop_price"], breakeven_price)

            trailing_activation_pct = float(_exit_value(exit_p, position, "trailing_activation_pct"))
            trailing_giveback_pct = float(_exit_value(exit_p, position, "trailing_giveback_pct"))
            if position["peak_pnl_pct"] >= trailing_activation_pct:
                trailing_gap_raw = trailing_giveback_pct / leverage / 100.0
                if side == "short":
                    trailing_price = position["favorable_price"] * (1.0 + trailing_gap_raw)
                    position["stop_price"] = min(position["stop_price"], trailing_price)
                else:
                    trailing_price = position["favorable_price"] * (1.0 - trailing_gap_raw)
                    position["stop_price"] = max(position["stop_price"], trailing_price)

            if int(exit_p["regime_exit_enabled"]) > 0 and hourly_context is not None:
                if side == "short":
                    regime_broken = (
                        hourly_context["histogram"] > abs(float(exit_p["regime_hist_floor"]))
                        or (
                            int(exit_p["regime_close_below_hourly_fast"]) > 0
                            and bar["close"] > hourly_context["ema_fast"]
                            and market_state["ema_fast"] > market_state["ema_slow"]
                        )
                    )
                else:
                    regime_broken = (
                        hourly_context["histogram"] < float(exit_p["regime_hist_floor"])
                        or (
                            int(exit_p["regime_close_below_hourly_fast"]) > 0
                            and bar["close"] < hourly_context["ema_fast"]
                            and market_state["ema_fast"] < market_state["ema_slow"]
                        )
                    )
                if regime_broken and close_pnl_pct < trailing_activation_pct:
                    trade, pnl_amount = _close_trade(position, bar["close"], "趋势失效", leverage)
                    capital += position["size"] + pnl_amount
                    record_trade(trade)
                    continue

            hold_limit = _resolve_hold_limit(position, exit_p, market_state, close_pnl_pct)
            if position["hold_bars"] >= hold_limit:
                trade, pnl_amount = _close_trade(position, bar["close"], "时间退出", leverage)
                capital += position["size"] + pnl_amount
                record_trade(trade)
                continue

            remaining.append(position)

        positions = remaining

        signal = strategy_func(intraday_data, idx, positions, market_state)
        target_position_size = capital * position_fraction
        target_position_size = max(position_size_min, target_position_size)
        target_position_size = min(position_size_max, target_position_size, capital)
        if (
            signal
            and len(positions) < max_concurrent_positions
            and capital >= position_size_min
            and target_position_size > 0
            and market_state["atr"] > 0
            and (not positions or _signal_side(signal) == _position_side(positions[0]))
        ):
            stop_mult = float(_exit_value(exit_p, {"entry_signal": signal}, "stop_atr_mult"))
            signal_side = _signal_side(signal)
            if signal_side == "short":
                atr_stop = bar["close"] + market_state["atr"] * stop_mult
                hard_stop = bar["close"] * (1.0 + float(exit_p["stop_max_loss_pct"]) / leverage / 100.0)
                stop_price = min(atr_stop, hard_stop)
                valid_stop = stop_price > bar["close"]
            else:
                atr_stop = bar["close"] - market_state["atr"] * stop_mult
                hard_stop = bar["close"] * (1.0 - float(exit_p["stop_max_loss_pct"]) / leverage / 100.0)
                stop_price = max(atr_stop, hard_stop)
                valid_stop = stop_price < bar["close"]
            if valid_stop:
                capital -= target_position_size
                signal_entries[signal] = signal_entries.get(signal, 0) + 1
                positions.append(
                    {
                        "entry_price": bar["close"],
                        "entry_signal": signal,
                        "size": target_position_size,
                        "hold_bars": 0,
                        "peak_pnl_pct": 0.0,
                        "favorable_price": bar["close"],
                        "tp1_done": False,
                        "pyramids_done": 0,
                        "stop_price": stop_price,
                    }
                )

        equity = capital + sum(
            position["size"] + position["size"] * (_position_pnl_pct(position, bar["close"], leverage) / 100.0)
            for position in positions
        )
        max_equity = max(max_equity, equity)
        if max_equity > 0:
            max_drawdown = max(max_drawdown, (max_equity - equity) / max_equity * 100.0)

    last_close = intraday_data[-1]["close"]
    for position in positions:
        trade, pnl_amount = _close_trade(position, last_close, "数据结束", leverage)
        capital += position["size"] + pnl_amount
        record_trade(trade)

    total_return = (capital - initial_capital) / initial_capital * 100.0
    wins = sum(1 for trade in trades if trade["pnl_pct"] > 0)
    signal_stats = {}
    for signal in sorted(set(signal_entries) | set(signal_closed_pnl)):
        closed_trades = signal_closed_trades.get(signal, 0)
        signal_stats[signal] = {
            "entries": signal_entries.get(signal, 0),
            "closed_trades": closed_trades,
            "pnl_amount": signal_closed_pnl.get(signal, 0.0),
            "win_rate": (signal_closed_wins.get(signal, 0) / closed_trades * 100.0 if closed_trades else 0.0),
        }

    return {
        "trades": len(trades),
        "return": total_return,
        "max_drawdown": max_drawdown,
        "score": total_return - max(0.0, max_drawdown - 28.0) * 1.5,
        "win_rate": wins / len(trades) * 100.0 if trades else 0.0,
        "avg_pnl_pct": sum(trade["pnl_pct"] for trade in trades) / len(trades) if trades else 0.0,
        "avg_hold_bars": sum(trade["hold_bars"] for trade in trades) / len(trades) if trades else 0.0,
        "first_tp_count": sum(1 for trade in trades if trade["reason"] == "第一止盈"),
        "stop_exit_count": sum(1 for trade in trades if trade["reason"] == "止损"),
        "regime_exit_count": sum(1 for trade in trades if trade["reason"] == "趋势失效"),
        "time_exit_count": sum(1 for trade in trades if trade["reason"] == "时间退出"),
        "liquidations": sum(1 for trade in trades if trade["reason"] == "爆仓"),
        "pyramid_add_count": pyramid_add_count,
        "signal_stats": signal_stats,
    }
