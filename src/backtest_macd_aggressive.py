#!/usr/bin/env python3
"""激进版趋势策略回测器：15m 执行，1h/4h/情绪过滤。"""
import csv
import math
from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INTRADAY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_15m_20230101_20260401.csv"
DEFAULT_HOURLY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_1h_20230101_20260401.csv"
DEFAULT_EXECUTION_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_1m_20230101_20260401.csv"
DEFAULT_SENTIMENT_FILE = REPO_ROOT / "data/index/crypto_fear_greed_daily_20230101_20260401.csv"
DEFAULT_FUNDING_FILE = REPO_ROOT / "data/funding/OKX_BTC_USDT_SWAP_funding_20230101_20260401.csv"


# EXIT_PARAMS_START
EXIT_PARAMS = {'break_even_activation_pct': 28.0,
 'break_even_buffer_pct': 0.35,
 'breakout_break_even_activation_pct': 42.0,
 'breakout_max_hold_bars': 384,
 'breakout_stop_atr_mult': 2.3,
 'breakout_tp1_close_fraction': 0.16,
 'breakout_tp1_pnl_pct': 56.0,
 'breakout_trailing_activation_pct': 85.9,
 'breakout_trailing_giveback_pct': 32.6,
 'dynamic_hold_adx_strong_threshold': 26.0,
 'dynamic_hold_adx_threshold': 16.0,
 'dynamic_hold_extension_bars': 96,
 'dynamic_hold_max_bars': 384,
 'entry_delay_minutes': 1,
 'execution_use_1m': 1,
 'funding_fee_enabled': 1,
 'leverage': 14,
 'max_concurrent_positions': 4,
 'max_hold_bars': 288,
 'okx_maker_fee_rate': 0.0002,
 'okx_taker_fee_rate': 0.0005,
 'position_fraction': 0.17,
 'position_size_max': 30000,
 'position_size_min': 5000,
 'pyramid_adx_min': 19.0,
 'pyramid_enabled': 1,
 'pyramid_max_times': 2,
 'pyramid_size_ratio': 0.28,
 'pyramid_trigger_pnl': 16.0,
 'regime_close_below_hourly_fast': 0,
 'regime_exit_confirm_bars': 1,
 'regime_exit_enabled': 1,
 'regime_hist_floor': -110.0,
 'regime_price_confirm_buffer_pct': 0.015,
 'short_breakdown_break_even_activation_pct': 18.0,
 'short_breakdown_max_hold_bars': 96,
 'short_breakdown_stop_atr_mult': 2.1,
 'short_breakdown_tp1_close_fraction': 0.22,
 'short_breakdown_tp1_pnl_pct': 22.0,
 'short_breakdown_trailing_activation_pct': 28.0,
 'short_breakdown_trailing_giveback_pct': 9.0,
 'slippage_pct': 0.0003,
 'stop_atr_mult': 3.1,
 'stop_max_loss_pct': 53.0,
 'tp1_close_fraction': 0.04,
 'tp1_pnl_pct': 46.0,
 'trading_fee_enabled': 1,
 'trailing_activation_pct': 87.0,
 'trailing_giveback_pct': 28.0}
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


@lru_cache(maxsize=4)
def load_funding_data(filename):
    rows = []
    with open(filename, "r") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                {
                    "timestamp": int(row["timestamp"]),
                    "funding_rate": float(row["funding_rate"]),
                }
            )
    return rows


def _beijing_dt(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC) + timedelta(hours=8)


def _beijing_day_label(timestamp_ms):
    return _beijing_dt(timestamp_ms).strftime("%Y-%m-%d")


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


def _beijing_window_indices(data, start_date, end_date):
    start_ts = _beijing_timestamp_ms(start_date)
    end_exclusive_ts = _beijing_timestamp_ms(end_date) + 24 * 60 * 60 * 1000
    timestamps = [row["timestamp"] for row in data]
    start_idx = bisect_right(timestamps, start_ts - 1)
    end_idx = bisect_right(timestamps, end_exclusive_ts - 1)
    return start_idx, end_idx


def _window_indices_from_timestamps(timestamps, start_ts, end_exclusive_ts):
    start_idx = bisect_right(timestamps, start_ts - 1)
    end_idx = bisect_right(timestamps, end_exclusive_ts - 1)
    return start_idx, end_idx


def _timestamp_window_indices_inclusive(timestamps, start_ts, end_ts):
    start_idx = bisect_right(timestamps, start_ts - 1)
    end_idx = bisect_right(timestamps, end_ts)
    return start_idx, end_idx


def _beijing_window_indices_from_timestamps(timestamps, start_date, end_date):
    start_ts = _beijing_timestamp_ms(start_date)
    end_exclusive_ts = _beijing_timestamp_ms(end_date) + 24 * 60 * 60 * 1000
    return _window_indices_from_timestamps(timestamps, start_ts, end_exclusive_ts)


def _slice_by_timestamp_window(data, start_ts, end_ts):
    timestamps = [row["timestamp"] for row in data]
    start_idx = bisect_right(timestamps, start_ts - 1)
    end_idx = bisect_right(timestamps, end_ts)
    return data[start_idx:end_idx]


def _infer_interval_ms(data, default_minutes):
    if len(data) >= 2:
        return max(60_000, data[1]["timestamp"] - data[0]["timestamp"])
    return default_minutes * 60_000


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


def _position_pnl_amount(position, price, leverage):
    return position["size"] * (_position_pnl_pct(position, price, leverage) / 100.0)


def _position_notional(position, price, leverage):
    return position["size"] * leverage * price / max(position["entry_price"], 1e-9)


def _trading_fee_amount(notional, exit_p):
    if int(exit_p.get("trading_fee_enabled", 1)) <= 0:
        return 0.0
    return max(0.0, notional) * float(exit_p["okx_taker_fee_rate"])


def _fill_with_slippage(price, side, is_entry, slippage_pct):
    """Apply slippage in the unfavorable direction for the trader."""
    if slippage_pct <= 0.0:
        return price
    is_buying = (side == "long") == is_entry
    if is_buying:
        return price * (1.0 + slippage_pct)
    return price * (1.0 - slippage_pct)


def _tp_trigger_price(entry_price, tp_pnl_pct, leverage, side):
    """Calculate the exact price that triggers a take-profit level."""
    pct_move = tp_pnl_pct / leverage / 100.0
    if side == "long":
        return entry_price * (1.0 + pct_move)
    return entry_price * (1.0 - pct_move)


def _execution_price(bar_timestamp, bar_close, execution_timestamps, execution_data, intraday_interval_ms, delay_minutes):
    if not execution_timestamps or not execution_data:
        return bar_close
    target_ts = bar_timestamp + intraday_interval_ms + max(0, delay_minutes - 1) * 60_000
    execution_idx = bisect_right(execution_timestamps, target_ts - 1)
    if execution_idx >= len(execution_data):
        return execution_data[-1]["close"]
    return execution_data[execution_idx]["close"]


def _price_before_timestamp(target_ts, execution_timestamps, execution_data, fallback_price):
    if not execution_timestamps or not execution_data:
        return fallback_price
    execution_idx = bisect_right(execution_timestamps, target_ts - 1) - 1
    if execution_idx < 0:
        return execution_data[0]["close"]
    return execution_data[execution_idx]["close"]


def _close_trade(position, price, reason, leverage, allocated_entry_fee=0.0, exit_fee=0.0, funding_pnl=0.0):
    gross_pnl_amount = _position_pnl_amount(position, price, leverage)
    net_pnl_amount = gross_pnl_amount - allocated_entry_fee - exit_fee + funding_pnl
    pnl_pct = net_pnl_amount / max(position["size"], 1e-9) * 100.0
    return {
        "pnl_pct": pnl_pct,
        "pnl_amount": net_pnl_amount,
        "gross_pnl_amount": gross_pnl_amount,
        "fee_amount": allocated_entry_fee + exit_fee,
        "funding_amount": funding_pnl,
        "hold_bars": position["hold_bars"],
        "reason": reason,
        "entry_signal": position["entry_signal"],
        "size": position["size"],
        "pyramids_done": position.get("pyramids_done", 0),
    }, gross_pnl_amount


def _close_cash_release(position_size, gross_pnl_amount, exit_fee=0.0, funding_pnl=0.0):
    """Cash returned to capital when a position slice is settled.

    Entry fees are paid at entry time, so settlement should only release
    margin, gross pnl, unsettled funding, and then deduct the exit fee.
    """
    return position_size + gross_pnl_amount + funding_pnl - exit_fee


def _market_risk_profile(market_state, exit_p):
    base_max_positions = max(1, int(exit_p.get("max_concurrent_positions", 1)))
    profile = {
        "position_fraction_scale": 1.0,
        "max_concurrent_positions": base_max_positions,
        "allow_pyramid": True,
    }

    hourly = market_state.get("hourly")
    four_hour = market_state.get("four_hour")
    if hourly is None or four_hour is None:
        return profile

    atr_ratio = market_state.get("atr_ratio", 0.0)
    weak_signals = 0
    if market_state.get("chop", 0.0) >= 58.0 and hourly.get("chop", 0.0) >= 56.0:
        weak_signals += 1
    if market_state.get("adx", 0.0) < 16.0 and hourly.get("adx", 0.0) < 18.0:
        weak_signals += 1
    if (
        abs(hourly.get("trend_spread_pct", 0.0)) < max(0.0018, atr_ratio * 0.75)
        and abs(four_hour.get("trend_spread_pct", 0.0)) < max(0.0021, atr_ratio * 0.95)
    ):
        weak_signals += 1
    if (
        abs(hourly.get("ema_slow_slope_pct", 0.0)) < atr_ratio * 0.07
        and abs(four_hour.get("ema_slow_slope_pct", 0.0)) < atr_ratio * 0.035
    ):
        weak_signals += 1

    severe = weak_signals >= 3 or (
        market_state.get("chop", 0.0) >= 60.0
        and hourly.get("chop", 0.0) >= 58.0
        and market_state.get("adx", 0.0) < 15.0
    )
    if severe:
        profile["position_fraction_scale"] = 0.55
        profile["max_concurrent_positions"] = min(base_max_positions, 2)
        profile["allow_pyramid"] = False
        return profile

    if weak_signals >= 2:
        profile["position_fraction_scale"] = 0.72
        profile["max_concurrent_positions"] = min(base_max_positions, 3)
        profile["allow_pyramid"] = False
    return profile


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


def _should_pyramid(position, market_state, close_pnl_pct, exit_p, allow_pyramid=True):
    side = _position_side(position)
    return (
        allow_pyramid
        and
        int(exit_p.get("pyramid_enabled", 0)) > 0
        and position.get("pyramids_done", 0) < int(exit_p.get("pyramid_max_times", 3))
        and position.get("entry_signal") in {"long_breakout", "short_breakdown"}
        and close_pnl_pct >= float(exit_p.get("pyramid_trigger_pnl", 20.0))
        and market_state["adx"] >= float(exit_p.get("pyramid_adx_min", 30.0))
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


def _confirmed_regime_break(position, exit_p, bar, prev_bar, market_state):
    hourly = market_state.get("hourly")
    prev_hourly = market_state.get("prev_hourly")
    if hourly is None or prev_hourly is None:
        return False

    confirm_bars = max(1, int(exit_p.get("regime_exit_confirm_bars", 2)))
    price_buffer = float(exit_p.get("regime_price_confirm_buffer_pct", 0.0)) / 100.0
    hist_floor = abs(float(exit_p["regime_hist_floor"]))
    side = _position_side(position)

    if side == "short":
        hist_broken_now = hourly["histogram"] > hist_floor
        hist_broken_prev = prev_hourly["histogram"] > hist_floor
        price_broken_now = (
            bar["close"] > hourly["ema_fast"] * (1.0 + price_buffer)
            and market_state["ema_fast"] > market_state["ema_slow"]
        )
        price_broken_prev = (
            prev_bar is not None
            and prev_bar["close"] > prev_hourly["ema_fast"] * (1.0 + price_buffer)
            and market_state["prev_ema_fast"] > market_state["prev_ema_slow"]
        )
    else:
        hist_broken_now = hourly["histogram"] < -hist_floor
        hist_broken_prev = prev_hourly["histogram"] < -hist_floor
        price_broken_now = (
            bar["close"] < hourly["ema_fast"] * (1.0 - price_buffer)
            and market_state["ema_fast"] < market_state["ema_slow"]
        )
        price_broken_prev = (
            prev_bar is not None
            and prev_bar["close"] < prev_hourly["ema_fast"] * (1.0 - price_buffer)
            and market_state["prev_ema_fast"] < market_state["prev_ema_slow"]
        )

    if confirm_bars <= 1:
        hist_confirmed = hist_broken_now
        price_confirmed = price_broken_now
    else:
        hist_confirmed = hist_broken_now and hist_broken_prev
        price_confirmed = price_broken_now and price_broken_prev

    if int(exit_p["regime_close_below_hourly_fast"]) > 0:
        return hist_confirmed or price_confirmed
    return hist_confirmed


def _apply_funding(position, funding_rate, settlement_price, leverage):
    notional = _position_notional(position, settlement_price, leverage)
    if _position_side(position) == "short":
        funding_pnl = notional * funding_rate
    else:
        funding_pnl = -notional * funding_rate
    position["funding_pnl"] = position.get("funding_pnl", 0.0) + funding_pnl
    return funding_pnl


def _append_daily_equity_point(points, timestamp_ms, equity):
    day = _beijing_day_label(timestamp_ms)
    payload = {"date": day, "timestamp": timestamp_ms, "equity": round(equity, 8)}
    if points and points[-1]["date"] == day:
        points[-1] = payload
    else:
        points.append(payload)


def _daily_returns_from_equity_curve(daily_equity_curve):
    returns = []
    for idx in range(1, len(daily_equity_curve)):
        prev_equity = daily_equity_curve[idx - 1]["equity"]
        current_equity = daily_equity_curve[idx]["equity"]
        if prev_equity <= 1e-9:
            continue
        returns.append((current_equity - prev_equity) / prev_equity)
    return returns


def _trade_reason_stats(trades):
    counts = {}
    for trade in trades:
        reason = trade.get("reason", "")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def prepare_backtest_context(
    strategy_params,
    *,
    intraday_file=None,
    hourly_file=None,
    sentiment_file=None,
    execution_file=None,
    funding_file=None,
    exit_params=None,
):
    exit_p = dict(EXIT_PARAMS)
    if exit_params:
        exit_p.update(exit_params)

    intraday_file = str(intraday_file or DEFAULT_INTRADAY_FILE)
    hourly_file = str(hourly_file or DEFAULT_HOURLY_FILE)
    sentiment_file = str(sentiment_file or DEFAULT_SENTIMENT_FILE)
    execution_file = str(execution_file or DEFAULT_EXECUTION_FILE)
    funding_file = str(funding_file or DEFAULT_FUNDING_FILE)

    intraday_all = load_ohlcv_data(intraday_file)
    hourly_all = load_ohlcv_data(hourly_file)
    if not intraday_all or not hourly_all:
        raise ValueError("missing source data")

    intraday_timestamps = [row["timestamp"] for row in intraday_all]
    hourly_timestamps = [row["timestamp"] for row in hourly_all]

    sentiment_rows = load_sentiment_data(sentiment_file) if Path(sentiment_file).exists() else []
    sentiment_state = _prepare_sentiment_state(sentiment_rows) if sentiment_rows else []
    sentiment_timestamps = [row["timestamp"] for row in sentiment_state]

    intraday_interval_ms = _infer_interval_ms(intraday_all, 15)
    hourly_interval_ms = _infer_interval_ms(hourly_all, 60)

    execution_all = []
    execution_timestamps = []
    if Path(execution_file).exists() and int(exit_p.get("execution_use_1m", 1)) > 0:
        execution_all = load_ohlcv_data(execution_file)
        execution_timestamps = [row["timestamp"] for row in execution_all]

    funding_all = []
    funding_timestamps = []
    if Path(funding_file).exists() and int(exit_p.get("funding_fee_enabled", 1)) > 0:
        funding_all = load_funding_data(funding_file)
        funding_timestamps = [row["timestamp"] for row in funding_all]

    hourly_state = _prepare_state(
        hourly_all,
        strategy_params["hourly_ema_fast"],
        strategy_params["hourly_ema_slow"],
        strategy_params["macd_fast"],
        strategy_params["macd_slow"],
        strategy_params["macd_signal"],
        strategy_params.get("hourly_ema_anchor"),
    )
    four_hour_bars = _aggregate_bars(hourly_all, 4)
    four_hour_state = _prepare_state(
        four_hour_bars,
        strategy_params["fourh_ema_fast"],
        strategy_params["fourh_ema_slow"],
        strategy_params["macd_fast"],
        strategy_params["macd_slow"],
        strategy_params["macd_signal"],
    )
    intraday_state = _prepare_state(
        intraday_all,
        strategy_params["intraday_ema_fast"],
        strategy_params["intraday_ema_slow"],
        strategy_params["macd_fast"],
        strategy_params["macd_slow"],
        strategy_params["macd_signal"],
    )

    four_hour_interval_ms = _infer_interval_ms(four_hour_bars, 240) if four_hour_bars else 240 * 60_000
    hourly_close_timestamps = [row["timestamp"] + hourly_interval_ms for row in hourly_state]
    four_hour_close_timestamps = [row["timestamp"] + four_hour_interval_ms for row in four_hour_state]

    return {
        "intraday_all": intraday_all,
        "hourly_all": hourly_all,
        "intraday_timestamps": intraday_timestamps,
        "hourly_timestamps": hourly_timestamps,
        "intraday_interval_ms": intraday_interval_ms,
        "hourly_interval_ms": hourly_interval_ms,
        "intraday_state": intraday_state,
        "hourly_state": hourly_state,
        "hourly_close_timestamps": hourly_close_timestamps,
        "four_hour_bars": four_hour_bars,
        "four_hour_state": four_hour_state,
        "four_hour_close_timestamps": four_hour_close_timestamps,
        "sentiment_state": sentiment_state,
        "sentiment_timestamps": sentiment_timestamps,
        "execution_all": execution_all,
        "execution_timestamps": execution_timestamps,
        "funding_all": funding_all,
        "funding_timestamps": funding_timestamps,
    }


def backtest_macd_aggressive(
    strategy_func,
    intraday_file,
    hourly_file,
    start_date,
    end_date,
    strategy_params,
    exit_params=None,
    sentiment_file=None,
    execution_file=None,
    funding_file=None,
    include_diagnostics=False,
    prepared_context=None,
):
    exit_p = dict(EXIT_PARAMS)
    if exit_params:
        exit_p.update(exit_params)

    if prepared_context is None:
        prepared_context = prepare_backtest_context(
            strategy_params,
            intraday_file=intraday_file,
            hourly_file=hourly_file,
            sentiment_file=sentiment_file,
            execution_file=execution_file,
            funding_file=funding_file,
            exit_params=exit_p,
        )

    intraday_all = prepared_context["intraday_all"]
    hourly_all = prepared_context["hourly_all"]
    intraday_timestamps = prepared_context["intraday_timestamps"]
    intraday_interval_ms = prepared_context["intraday_interval_ms"]
    hourly_state = prepared_context["hourly_state"]
    four_hour_state = prepared_context["four_hour_state"]
    intraday_state = prepared_context["intraday_state"]
    hourly_close_timestamps = prepared_context["hourly_close_timestamps"]
    four_hour_close_timestamps = prepared_context["four_hour_close_timestamps"]
    sentiment_state = prepared_context["sentiment_state"]
    sentiment_timestamps = prepared_context["sentiment_timestamps"]

    intraday_start_idx, intraday_end_idx = _beijing_window_indices_from_timestamps(intraday_timestamps, start_date, end_date)
    intraday_data = intraday_all[intraday_start_idx:intraday_end_idx]
    if not intraday_data or not hourly_all:
        raise ValueError(f"missing data for window {start_date}~{end_date}")

    start_ts = intraday_data[0]["timestamp"]
    end_ts = intraday_data[-1]["timestamp"] + intraday_interval_ms

    execution_rows = []
    execution_timestamps = []
    execution_all = prepared_context["execution_all"]
    if execution_all:
        full_execution_timestamps = prepared_context["execution_timestamps"]
        execution_start_idx, execution_end_idx = _timestamp_window_indices_inclusive(
            full_execution_timestamps,
            start_ts,
            end_ts + 60_000,
        )
        execution_rows = execution_all[execution_start_idx:execution_end_idx]
        execution_timestamps = full_execution_timestamps[execution_start_idx:execution_end_idx]

    funding_rows = []
    funding_timestamps = []
    funding_all = prepared_context["funding_all"]
    if funding_all:
        full_funding_timestamps = prepared_context["funding_timestamps"]
        funding_start_idx, funding_end_idx = _timestamp_window_indices_inclusive(
            full_funding_timestamps,
            start_ts,
            end_ts,
        )
        funding_rows = funding_all[funding_start_idx:funding_end_idx]
        funding_timestamps = full_funding_timestamps[funding_start_idx:funding_end_idx]

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
    total_trading_fees = 0.0
    total_funding_pnl = 0.0
    funding_event_count = 0
    funding_idx = 0
    daily_equity_curve = []
    delay_minutes = int(exit_p.get("entry_delay_minutes", 1))
    taker_fee_rate = float(exit_p["okx_taker_fee_rate"]) if int(exit_p.get("trading_fee_enabled", 1)) > 0 else 0.0
    slippage_pct = float(exit_p.get("slippage_pct", 0.0003))

    def record_trade(trade):
        trades.append(trade)
        signal = trade["entry_signal"]
        signal_closed_pnl[signal] = signal_closed_pnl.get(signal, 0.0) + trade["pnl_amount"]
        signal_closed_trades[signal] = signal_closed_trades.get(signal, 0) + 1
        if trade["pnl_pct"] > 0:
            signal_closed_wins[signal] = signal_closed_wins.get(signal, 0) + 1

    for idx in range(intraday_start_idx, intraday_end_idx):
        bar = intraday_all[idx]
        prev_bar = intraday_all[idx - 1] if idx > 0 else None
        prev_bar_close_ts = prev_bar["timestamp"] + intraday_interval_ms if prev_bar is not None else bar["timestamp"]
        bar_close_ts = bar["timestamp"] + intraday_interval_ms
        current_ts = bar["timestamp"]
        context_ref_ts = bar_close_ts
        hourly_idx = bisect_right(hourly_close_timestamps, context_ref_ts) - 1
        four_hour_idx = bisect_right(four_hour_close_timestamps, context_ref_ts) - 1
        sentiment_idx = bisect_right(sentiment_timestamps, current_ts) - 1

        hourly_context = hourly_state[hourly_idx] if hourly_idx >= 0 else None
        prev_hourly_context = hourly_state[hourly_idx - 1] if hourly_idx > 0 else hourly_context
        four_hour_context = four_hour_state[four_hour_idx] if four_hour_idx >= 0 else None
        sentiment_context = sentiment_state[sentiment_idx] if sentiment_idx >= 0 else None
        intraday_context = intraday_state[idx]
        prev_intraday_context = intraday_state[idx - 1] if idx > 0 else intraday_context
        market_state = {
            "hourly": hourly_context,
            "prev_hourly": prev_hourly_context,
            "four_hour": four_hour_context,
            "sentiment": sentiment_context,
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
        market_fill_price = _execution_price(
            bar["timestamp"],
            bar["close"],
            execution_timestamps,
            execution_rows,
            intraday_interval_ms,
            delay_minutes,
        )
        risk_profile = _market_risk_profile(market_state, exit_p)

        if funding_rows and positions:
            while funding_idx < len(funding_rows) and funding_timestamps[funding_idx] <= bar_close_ts:
                funding_row = funding_rows[funding_idx]
                if funding_row["timestamp"] > prev_bar_close_ts:
                    settlement_price = _price_before_timestamp(
                        funding_row["timestamp"],
                        execution_timestamps,
                        execution_rows,
                        bar["close"],
                    )
                    for position in positions:
                        funding_pnl = _apply_funding(
                            position,
                            funding_row["funding_rate"],
                            settlement_price,
                            leverage,
                        )
                        total_funding_pnl += funding_pnl
                    funding_event_count += 1
                funding_idx += 1

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
                capital += position.get("funding_pnl", 0.0)
                record_trade(
                    {
                        "pnl_pct": -100.0,
                        "pnl_amount": -position["size"] - position.get("entry_fee_paid", 0.0) + position.get("funding_pnl", 0.0),
                        "gross_pnl_amount": -position["size"],
                        "fee_amount": position.get("entry_fee_paid", 0.0),
                        "funding_amount": position.get("funding_pnl", 0.0),
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
                stop_fill = _fill_with_slippage(position["stop_price"], side, False, slippage_pct)
                exit_notional = _position_notional(position, stop_fill, leverage)
                exit_fee = _trading_fee_amount(exit_notional, exit_p)
                allocated_entry_fee = position.get("entry_fee_paid", 0.0)
                allocated_funding = position.get("funding_pnl", 0.0)
                trade, gross_pnl_amount = _close_trade(
                    position,
                    stop_fill,
                    "止损",
                    leverage,
                    allocated_entry_fee=allocated_entry_fee,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                capital += _close_cash_release(
                    position["size"],
                    gross_pnl_amount,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                total_trading_fees += exit_fee
                record_trade(trade)
                continue

            if _should_pyramid(position, market_state, close_pnl_pct, exit_p, allow_pyramid=risk_profile["allow_pyramid"]):
                pyramid_fill = _fill_with_slippage(market_fill_price, side, True, slippage_pct)
                max_affordable_size = capital / (1.0 + leverage * taker_fee_rate)
                add_size = min(max_affordable_size, position["size"] * float(exit_p.get("pyramid_size_ratio", 0.5)), position_size_max)
                if add_size >= position_size_min:
                    add_fee = _trading_fee_amount(add_size * leverage, exit_p)
                    total_size = position["size"] + add_size
                    position["entry_price"] = (
                        position["entry_price"] * position["size"] + pyramid_fill * add_size
                    ) / total_size
                    position["size"] = total_size
                    position["pyramids_done"] = position.get("pyramids_done", 0) + 1
                    position["entry_fee_paid"] = position.get("entry_fee_paid", 0.0) + add_fee
                    capital -= add_size + add_fee
                    total_trading_fees += add_fee
                    pyramid_add_count += 1

            tp1_pnl_pct = float(_exit_value(exit_p, position, "tp1_pnl_pct"))
            tp1_close_fraction = float(_exit_value(exit_p, position, "tp1_close_fraction"))
            if (not position["tp1_done"]) and best_pnl_pct >= tp1_pnl_pct:
                tp1_trigger = _tp_trigger_price(position["entry_price"], tp1_pnl_pct, leverage, side)
                tp1_fill = _fill_with_slippage(tp1_trigger, side, False, slippage_pct)
                close_size = position["size"] * tp1_close_fraction
                remaining_size = position["size"] - close_size
                close_fraction = close_size / max(position["size"], 1e-9)
                allocated_entry_fee = position.get("entry_fee_paid", 0.0) * close_fraction
                allocated_funding = position.get("funding_pnl", 0.0) * close_fraction
                partial_position = dict(position)
                partial_position["size"] = close_size
                exit_notional = _position_notional(partial_position, tp1_fill, leverage)
                exit_fee = _trading_fee_amount(exit_notional, exit_p)
                trade, gross_pnl_amount = _close_trade(
                    partial_position,
                    tp1_fill,
                    "第一止盈",
                    leverage,
                    allocated_entry_fee=allocated_entry_fee,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                capital += _close_cash_release(
                    close_size,
                    gross_pnl_amount,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                total_trading_fees += exit_fee
                record_trade(trade)
                position["size"] = remaining_size
                position["entry_fee_paid"] = position.get("entry_fee_paid", 0.0) - allocated_entry_fee
                position["funding_pnl"] = position.get("funding_pnl", 0.0) - allocated_funding
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
                regime_broken = _confirmed_regime_break(position, exit_p, bar, prev_bar, market_state)
                if regime_broken and close_pnl_pct < trailing_activation_pct:
                    regime_fill = _fill_with_slippage(market_fill_price, side, False, slippage_pct)
                    exit_notional = _position_notional(position, regime_fill, leverage)
                    exit_fee = _trading_fee_amount(exit_notional, exit_p)
                    allocated_entry_fee = position.get("entry_fee_paid", 0.0)
                    allocated_funding = position.get("funding_pnl", 0.0)
                    trade, gross_pnl_amount = _close_trade(
                        position,
                        regime_fill,
                        "趋势失效",
                        leverage,
                        allocated_entry_fee=allocated_entry_fee,
                        exit_fee=exit_fee,
                        funding_pnl=allocated_funding,
                    )
                    capital += _close_cash_release(
                        position["size"],
                        gross_pnl_amount,
                        exit_fee=exit_fee,
                        funding_pnl=allocated_funding,
                    )
                    total_trading_fees += exit_fee
                    record_trade(trade)
                    continue

            hold_limit = _resolve_hold_limit(position, exit_p, market_state, close_pnl_pct)
            if position["hold_bars"] >= hold_limit:
                time_fill = _fill_with_slippage(market_fill_price, side, False, slippage_pct)
                exit_notional = _position_notional(position, time_fill, leverage)
                exit_fee = _trading_fee_amount(exit_notional, exit_p)
                allocated_entry_fee = position.get("entry_fee_paid", 0.0)
                allocated_funding = position.get("funding_pnl", 0.0)
                trade, gross_pnl_amount = _close_trade(
                    position,
                    time_fill,
                    "时间退出",
                    leverage,
                    allocated_entry_fee=allocated_entry_fee,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                capital += _close_cash_release(
                    position["size"],
                    gross_pnl_amount,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                total_trading_fees += exit_fee
                record_trade(trade)
                continue

            remaining.append(position)

        positions = remaining

        signal = strategy_func(intraday_all, idx, positions, market_state)
        if signal and positions and _signal_side(signal) != _position_side(positions[0]):
            for position in positions:
                rev_side = _position_side(position)
                rev_fill = _fill_with_slippage(market_fill_price, rev_side, False, slippage_pct)
                exit_notional = _position_notional(position, rev_fill, leverage)
                exit_fee = _trading_fee_amount(exit_notional, exit_p)
                allocated_entry_fee = position.get("entry_fee_paid", 0.0)
                allocated_funding = position.get("funding_pnl", 0.0)
                trade, gross_pnl_amount = _close_trade(
                    position,
                    rev_fill,
                    "反向信号",
                    leverage,
                    allocated_entry_fee=allocated_entry_fee,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                capital += _close_cash_release(
                    position["size"],
                    gross_pnl_amount,
                    exit_fee=exit_fee,
                    funding_pnl=allocated_funding,
                )
                total_trading_fees += exit_fee
                record_trade(trade)
            positions = []
        target_position_size = capital * position_fraction * risk_profile["position_fraction_scale"]
        max_affordable_size = capital / (1.0 + leverage * taker_fee_rate) if taker_fee_rate > 0 else capital
        target_position_size = min(position_size_max, target_position_size, max_affordable_size)
        if (
            signal
            and len(positions) < risk_profile["max_concurrent_positions"]
            and capital >= position_size_min
            and target_position_size >= position_size_min
            and market_state["atr"] > 0
            and (not positions or _signal_side(signal) == _position_side(positions[0]))
        ):
            stop_mult = float(_exit_value(exit_p, {"entry_signal": signal}, "stop_atr_mult"))
            signal_side = _signal_side(signal)
            if signal_side == "short":
                atr_stop = market_fill_price + market_state["atr"] * stop_mult
                hard_stop = market_fill_price * (1.0 + float(exit_p["stop_max_loss_pct"]) / leverage / 100.0)
                stop_price = min(atr_stop, hard_stop)
                valid_stop = stop_price > market_fill_price
            else:
                atr_stop = market_fill_price - market_state["atr"] * stop_mult
                hard_stop = market_fill_price * (1.0 - float(exit_p["stop_max_loss_pct"]) / leverage / 100.0)
                stop_price = max(atr_stop, hard_stop)
                valid_stop = stop_price < market_fill_price
            if valid_stop:
                entry_fill = _fill_with_slippage(market_fill_price, signal_side, True, slippage_pct)
                entry_fee = _trading_fee_amount(target_position_size * leverage, exit_p)
                capital -= target_position_size + entry_fee
                total_trading_fees += entry_fee
                signal_entries[signal] = signal_entries.get(signal, 0) + 1
                positions.append(
                    {
                        "entry_price": entry_fill,
                        "entry_signal": signal,
                        "size": target_position_size,
                        "hold_bars": 0,
                        "peak_pnl_pct": 0.0,
                        "favorable_price": entry_fill,
                        "tp1_done": False,
                        "pyramids_done": 0,
                        "entry_fee_paid": entry_fee,
                        "funding_pnl": 0.0,
                        "stop_price": stop_price,
                    }
                )

        equity = capital + sum(
            position["size"]
            + position["size"] * (_position_pnl_pct(position, bar["close"], leverage) / 100.0)
            + position.get("funding_pnl", 0.0)
            for position in positions
        )
        _append_daily_equity_point(daily_equity_curve, bar_close_ts, equity)
        max_equity = max(max_equity, equity)
        if max_equity > 0:
            max_drawdown = max(max_drawdown, (max_equity - equity) / max_equity * 100.0)

    last_close = intraday_data[-1]["close"]
    for position in positions:
        end_side = _position_side(position)
        end_fill = _fill_with_slippage(last_close, end_side, False, slippage_pct)
        exit_notional = _position_notional(position, end_fill, leverage)
        exit_fee = _trading_fee_amount(exit_notional, exit_p)
        allocated_entry_fee = position.get("entry_fee_paid", 0.0)
        allocated_funding = position.get("funding_pnl", 0.0)
        trade, gross_pnl_amount = _close_trade(
            position,
            end_fill,
            "数据结束",
            leverage,
            allocated_entry_fee=allocated_entry_fee,
            exit_fee=exit_fee,
            funding_pnl=allocated_funding,
        )
        capital += _close_cash_release(
            position["size"],
            gross_pnl_amount,
            exit_fee=exit_fee,
            funding_pnl=allocated_funding,
        )
        total_trading_fees += exit_fee
        record_trade(trade)

    _append_daily_equity_point(daily_equity_curve, end_ts, capital)
    total_return = (capital - initial_capital) / initial_capital * 100.0
    fee_drag_pct = total_trading_fees / initial_capital * 100.0
    fee_penalty = fee_drag_pct * 0.35
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

    result = {
        "trades": len(trades),
        "return": total_return,
        "max_drawdown": max_drawdown,
        "score": total_return - max(0.0, max_drawdown - 28.0) * 1.5 - fee_penalty,
        "win_rate": wins / len(trades) * 100.0 if trades else 0.0,
        "avg_pnl_pct": sum(trade["pnl_pct"] for trade in trades) / len(trades) if trades else 0.0,
        "avg_hold_bars": sum(trade["hold_bars"] for trade in trades) / len(trades) if trades else 0.0,
        "gross_pnl_amount": sum(trade.get("gross_pnl_amount", trade["pnl_amount"]) for trade in trades),
        "trading_fee_amount": total_trading_fees,
        "fee_drag_pct": fee_drag_pct,
        "fee_score_penalty": fee_penalty,
        "funding_pnl_amount": total_funding_pnl,
        "funding_event_count": funding_event_count,
        "first_tp_count": sum(1 for trade in trades if trade["reason"] == "第一止盈"),
        "stop_exit_count": sum(1 for trade in trades if trade["reason"] == "止损"),
        "regime_exit_count": sum(1 for trade in trades if trade["reason"] == "趋势失效"),
        "reverse_exit_count": sum(1 for trade in trades if trade["reason"] == "反向信号"),
        "time_exit_count": sum(1 for trade in trades if trade["reason"] == "时间退出"),
        "liquidations": sum(1 for trade in trades if trade["reason"] == "爆仓"),
        "pyramid_add_count": pyramid_add_count,
        "signal_stats": signal_stats,
    }
    if include_diagnostics:
        result["daily_equity_curve"] = daily_equity_curve
        result["daily_returns"] = _daily_returns_from_equity_curve(daily_equity_curve)
        result["trade_reason_stats"] = _trade_reason_stats(trades)
        result["trades_detail"] = trades
    return result
