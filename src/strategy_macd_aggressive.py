#!/usr/bin/env python3
"""极致激进策略：严入场 + 慢止盈 + 慢止损 + 高杠杆。"""

SIDEWAYS_INTRADAY_CHOP_MIN = 60.0
SIDEWAYS_HOURLY_CHOP_MIN = 58.0
SIDEWAYS_HARD_INTRADAY_CHOP_MIN = 62.0
SIDEWAYS_HARD_HOURLY_CHOP_MIN = 60.0
SIDEWAYS_MIN_ATR_RATIO = 0.0020
SIDEWAYS_MIN_HOURLY_SPREAD_PCT = 0.0016
SIDEWAYS_MIN_FOURH_SPREAD_PCT = 0.0020
SIDEWAYS_MAX_HOURLY_ADX = 18.0
SIDEWAYS_MAX_FOURH_ADX = 16.0

# PARAMS_START
PARAMS = {'breakdown_adx_min': 25.6,
 'breakdown_body_ratio_min': 0.39,
 'breakdown_buffer_pct': 0.0002,
 'breakdown_close_pos_max': 0.36,
 'breakdown_hist_max': 16.0,
 'breakdown_lookback': 22,
 'breakdown_rsi_max': 42.0,
 'breakdown_rsi_min': 21.0,
 'breakdown_volume_ratio_min': 1.08,
 'breakout_adx_min': 18.3,
 'breakout_body_ratio_min': 0.22,
 'breakout_buffer_pct': 0.0,
 'breakout_close_pos_min': 0.48,
 'breakout_hist_min': -95.0,
 'breakout_lookback': 20,
 'breakout_rsi_max': 81.6,
 'breakout_rsi_min': 44.0,
 'breakout_volume_ratio_min': 1.12,
 'fourh_adx_min': 12.5,
 'fourh_ema_fast': 10,
 'fourh_ema_slow': 34,
 'hourly_adx_min': 19.0,
 'hourly_ema_anchor': 85,
 'hourly_ema_fast': 12,
 'hourly_ema_slow': 50,
 'intraday_adx_min': 12.5,
 'intraday_ema_fast': 9,
 'intraday_ema_slow': 28,
 'macd_fast': 5,
 'macd_signal': 3,
 'macd_slow': 16,
 'min_history': 260,
 'volume_lookback': 9}
# PARAMS_END


def _avg(data, start, end, key):
    total = 0.0
    count = 0
    for i in range(start, end + 1):
        total += data[i][key]
        count += 1
    return total / count if count else 0.0


def _window_max(data, start, end, key):
    value = data[start][key]
    for i in range(start + 1, end + 1):
        if data[i][key] > value:
            value = data[i][key]
    return value


def _window_min(data, start, end, key):
    value = data[start][key]
    for i in range(start + 1, end + 1):
        if data[i][key] < value:
            value = data[i][key]
    return value


def _ema(data, end_idx, length, key):
    alpha = 2.0 / (length + 1.0)
    start_idx = max(0, end_idx - length * 3)
    ema = data[start_idx][key]
    for i in range(start_idx + 1, end_idx + 1):
        ema = alpha * data[i][key] + (1.0 - alpha) * ema
    return ema


def _candle_metrics(bar):
    open_price = bar["open"]
    high = bar["high"]
    low = bar["low"]
    close = bar["close"]
    candle_range = max(high - low, close * 1e-9)
    body = close - open_price
    return {
        "body_pct": body / open_price if open_price > 0 else 0.0,
        "close_pos": (close - low) / candle_range,
        "body_ratio": abs(body) / candle_range,
    }


def _position_side(position):
    signal = position.get("entry_signal", "")
    return "short" if signal.startswith("short_") else "long"


def _intraday_trend_metrics(market_state):
    ema_fast = market_state["ema_fast"]
    ema_slow = market_state["ema_slow"]
    prev_ema_slow = market_state["prev_ema_slow"]
    trend_base = max(abs(ema_slow), 1e-9)
    return {
        "spread_pct": (ema_fast - ema_slow) / trend_base,
        "slope_pct": (ema_slow - prev_ema_slow) / trend_base,
    }


def _is_sideways_regime(market_state):
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    intraday = _intraday_trend_metrics(market_state)
    intraday_chop = market_state["chop"]
    hourly_chop = hourly["chop"]
    atr_ratio = market_state["atr_ratio"]
    intraday_spread = abs(intraday["spread_pct"])
    hourly_spread = abs(hourly["trend_spread_pct"])
    fourh_spread = abs(fourh["trend_spread_pct"])
    hourly_slope = abs(hourly["ema_slow_slope_pct"])
    fourh_slope = abs(fourh["ema_slow_slope_pct"])
    adx_soft = hourly["adx"] <= SIDEWAYS_MAX_HOURLY_ADX and fourh["adx"] <= SIDEWAYS_MAX_FOURH_ADX

    hard_sideways = (
        (
            intraday_chop >= SIDEWAYS_HARD_INTRADAY_CHOP_MIN
            and hourly_chop >= SIDEWAYS_HARD_HOURLY_CHOP_MIN
            and adx_soft
        )
        or (
            atr_ratio < SIDEWAYS_MIN_ATR_RATIO * 1.10
            and hourly_spread < SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.15
            and fourh_spread < SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.15
        )
        or (
            hourly_spread < SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.92
            and fourh_spread < SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.92
            and hourly_slope < atr_ratio * 0.06
            and fourh_slope < atr_ratio * 0.03
        )
    )
    if hard_sideways:
        return True

    signals = 0
    if intraday_chop >= SIDEWAYS_INTRADAY_CHOP_MIN and hourly_chop >= SIDEWAYS_HOURLY_CHOP_MIN:
        signals += 1
    if atr_ratio < SIDEWAYS_MIN_ATR_RATIO and hourly_chop >= SIDEWAYS_HOURLY_CHOP_MIN - 1.0:
        signals += 1
    if hourly_spread < SIDEWAYS_MIN_HOURLY_SPREAD_PCT and fourh_spread < SIDEWAYS_MIN_FOURH_SPREAD_PCT:
        signals += 1
    if intraday_spread < atr_ratio * 0.28 and hourly_slope < atr_ratio * 0.08 and fourh_slope < atr_ratio * 0.04:
        signals += 1
    if adx_soft:
        signals += 1
    return signals >= 3


def _trend_quality_ok(market_state, side):
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    intraday = _intraday_trend_metrics(market_state)
    atr_ratio = market_state["atr_ratio"]
    direction = -1.0 if side == "short" else 1.0

    confirms = 0
    if market_state["adx"] >= 14.0 and hourly["adx"] >= 18.0:
        confirms += 1
    if direction * intraday["spread_pct"] >= atr_ratio * 0.30:
        confirms += 1
    if direction * hourly["trend_spread_pct"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.20, atr_ratio * 0.75):
        confirms += 1
    if direction * fourh["trend_spread_pct"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.05, atr_ratio * 0.95):
        confirms += 1
    if (
        direction * hourly["ema_slow_slope_pct"] >= atr_ratio * 0.07
        and direction * fourh["ema_slow_slope_pct"] >= atr_ratio * 0.035
    ):
        confirms += 1
    return confirms >= 3


def _trend_followthrough_ok(market_state, side, trigger_price, current_close):
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    intraday = _intraday_trend_metrics(market_state)
    atr_ratio = market_state["atr_ratio"]
    direction = -1.0 if side == "short" else 1.0
    breakout_distance_pct = abs(current_close - trigger_price) / max(trigger_price, 1e-9)

    confirms = 0
    if direction * intraday["spread_pct"] >= atr_ratio * 0.30:
        confirms += 1
    if direction * hourly["trend_spread_pct"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.35, atr_ratio * 0.85):
        confirms += 1
    if direction * fourh["trend_spread_pct"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.10, atr_ratio * 1.05):
        confirms += 1
    if (
        direction * hourly["ema_slow_slope_pct"] >= atr_ratio * 0.08
        and direction * fourh["ema_slow_slope_pct"] >= atr_ratio * 0.04
    ):
        confirms += 1
    if breakout_distance_pct >= atr_ratio * 0.35:
        confirms += 1
    return confirms >= 3


def strategy(data, idx, positions, market_state):
    p = PARAMS
    if idx < p["min_history"]:
        return None

    current = data[idx]
    prev = data[idx - 1]
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    if hourly is None or fourh is None:
        return None

    for bar in (current, prev):
        if bar["open"] <= 0 or bar["close"] <= 0 or bar["volume"] <= 0 or bar["high"] < bar["low"]:
            return None

    if _is_sideways_regime(market_state):
        return None

    current_candle = _candle_metrics(current)
    avg_volume = max(_avg(data, idx - p["volume_lookback"] + 1, idx, "volume"), 1e-9)
    volume_ratio = current["volume"] / avg_volume
    breakout_high = _window_max(data, idx - p["breakout_lookback"], idx - 1, "high")
    breakdown_low = _window_min(data, idx - p["breakdown_lookback"], idx - 1, "low")

    # 做多：三周期共振 + Breakout
    intraday_bull = (
        current["close"] > market_state["ema_fast"] > market_state["ema_slow"]
        and market_state["adx"] >= p["intraday_adx_min"]
        and market_state["macd_line"] > market_state["signal_line"]
    )
    hourly_bull = (
        hourly["close"] > hourly["ema_fast"] > hourly["ema_slow"]
        and hourly["close"] > hourly["ema_anchor"]
        and hourly["macd_line"] > hourly["signal_line"]
        and hourly["adx"] >= p["hourly_adx_min"]
    )
    fourh_bull = (
        fourh["close"] > fourh["ema_fast"] > fourh["ema_slow"]
        and fourh["adx"] >= p["fourh_adx_min"]
    )

    if intraday_bull and hourly_bull and fourh_bull and _trend_quality_ok(market_state, "long"):
        breakout_ready = (
            current["close"] >= breakout_high * (1.0 + p["breakout_buffer_pct"])
            and current_candle["close_pos"] >= p["breakout_close_pos_min"]
            and current_candle["body_ratio"] >= p["breakout_body_ratio_min"]
            and volume_ratio >= p["breakout_volume_ratio_min"]
            and market_state["adx"] >= p["breakout_adx_min"]
            and p["breakout_rsi_min"] <= market_state["rsi"] <= p["breakout_rsi_max"]
            and market_state["histogram"] >= p["breakout_hist_min"]
        )
        if breakout_ready and _trend_followthrough_ok(market_state, "long", breakout_high, current["close"]):
            return "long_breakout"

    # 做空：三周期共振 + Breakdown
    intraday_bear = (
        current["close"] < market_state["ema_fast"] < market_state["ema_slow"]
        and market_state["adx"] >= p["intraday_adx_min"]
        and market_state["macd_line"] < market_state["signal_line"]
    )
    hourly_bear = (
        hourly["close"] < hourly["ema_fast"] < hourly["ema_slow"]
        and hourly["close"] < hourly["ema_anchor"]
        and hourly["macd_line"] < hourly["signal_line"]
        and hourly["adx"] >= p["hourly_adx_min"]
    )
    fourh_bear = (
        fourh["close"] < fourh["ema_slow"]
        and fourh["adx"] >= p["fourh_adx_min"]
    )

    if intraday_bear and hourly_bear and fourh_bear and _trend_quality_ok(market_state, "short"):
        breakdown_ready = (
            current["close"] <= breakdown_low * (1.0 - p["breakdown_buffer_pct"])
            and current_candle["close_pos"] <= p["breakdown_close_pos_max"]
            and current_candle["body_ratio"] >= p["breakdown_body_ratio_min"]
            and volume_ratio >= p["breakdown_volume_ratio_min"]
            and market_state["adx"] >= p["breakdown_adx_min"]
            and p["breakdown_rsi_min"] <= market_state["rsi"] <= p["breakdown_rsi_max"]
            and market_state["histogram"] <= p["breakdown_hist_max"]
        )
        if breakdown_ready and _trend_followthrough_ok(market_state, "short", breakdown_low, current["close"]):
            return "short_breakdown"

    return None
