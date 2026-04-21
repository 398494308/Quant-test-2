#!/usr/bin/env python3
"""极致激进策略：更小、更清晰的趋势捕获基底。"""

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
PARAMS = {
    "breakdown_adx_min": 25.0,
    "breakdown_body_ratio_min": 0.39,
    "breakdown_buffer_pct": 0.0002,
    "breakdown_close_pos_max": 0.34,
    "breakdown_hist_max": 14.0,
    "breakdown_lookback": 22,
    "breakdown_rsi_max": 44.0,
    "breakdown_rsi_min": 21.0,
    "breakdown_volume_ratio_min": 1.08,
    "breakout_adx_min": 21.5,
    "breakout_body_ratio_min": 0.31,
    "breakout_buffer_pct": 0.00015,
    "breakout_close_pos_min": 0.60,
    "breakout_flow_imbalance_min": 0.02,
    "breakout_flow_score_min": 4,
    "breakout_flow_score_strong_min": 6,
    "breakout_hist_min": 4.0,
    "breakout_lookback": 28,
    "breakout_rsi_max": 69.0,
    "breakout_rsi_min": 50.0,
    "breakout_taker_buy_ratio_min": 0.50,
    "breakout_trade_count_ratio_min": 1.05,
    "breakout_volume_ratio_min": 1.12,
    "flow_lookback": 9,
    "fourh_adx_min": 12.5,
    "fourh_ema_fast": 10,
    "fourh_ema_slow": 34,
    "fourh_flow_confirmation_min": 0.0,
    "fourh_taker_buy_ratio_min": 0.49,
    "hourly_adx_min": 19.0,
    "hourly_ema_anchor": 85,
    "hourly_ema_fast": 12,
    "hourly_ema_slow": 50,
    "hourly_flow_confirmation_min": 0.0,
    "hourly_taker_buy_ratio_min": 0.495,
    "hourly_trade_count_ratio_min": 0.75,
    "intraday_adx_min": 12.5,
    "intraday_ema_fast": 9,
    "intraday_ema_slow": 28,
    "macd_fast": 5,
    "macd_signal": 3,
    "macd_slow": 16,
    "min_history": 260,
    "volume_lookback": 9,
}
# PARAMS_END


FUNNEL_SIDES = ("long", "short")
FUNNEL_STAGES = ("sideways_pass", "outer_context_pass", "path_pass", "final_veto_pass")
_FUNNEL_DIAGNOSTICS = {}


def _empty_funnel_bucket():
    return {stage: 0 for stage in FUNNEL_STAGES}


def reset_funnel_diagnostics():
    global _FUNNEL_DIAGNOSTICS
    _FUNNEL_DIAGNOSTICS = {side: _empty_funnel_bucket() for side in FUNNEL_SIDES}


def _record_funnel_pass(side, stage):
    bucket = _FUNNEL_DIAGNOSTICS.get(side)
    if bucket is None:
        bucket = _empty_funnel_bucket()
        _FUNNEL_DIAGNOSTICS[side] = bucket
    bucket[stage] = int(bucket.get(stage, 0)) + 1


def get_funnel_diagnostics():
    return {
        side: {stage: int(bucket.get(stage, 0)) for stage in FUNNEL_STAGES}
        for side, bucket in _FUNNEL_DIAGNOSTICS.items()
    }


reset_funnel_diagnostics()


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


def _bar_is_valid(bar):
    return (
        bar["open"] > 0
        and bar["close"] > 0
        and bar["volume"] > 0
        and bar["high"] >= bar["low"]
    )


def _bar_range(bar):
    return max(bar["high"] - bar["low"], bar["close"] * 1e-9)


def _candle_metrics(bar):
    open_price = bar["open"]
    low = bar["low"]
    close = bar["close"]
    candle_range = _bar_range(bar)
    body = close - open_price
    return {
        "close_pos": (close - low) / candle_range,
        "body_ratio": abs(body) / candle_range,
    }


def _recent_window_stats(data, end_idx, window, price_floor):
    range_total = 0.0
    volume_total = 0.0
    body_ratio_total = 0.0
    for i in range(end_idx - window, end_idx):
        recent_bar = data[i]
        recent_range = _bar_range(recent_bar)
        body = recent_bar["close"] - recent_bar["open"]
        range_total += recent_range
        volume_total += recent_bar["volume"]
        body_ratio_total += abs(body) / recent_range
    return {
        "range_avg": max(range_total / window, price_floor * 1e-9),
        "volume_avg": max(volume_total / window, 1e-9),
        "body_ratio_avg": body_ratio_total / window,
    }


def _intraday_trend_metrics(market_state):
    ema_fast = market_state["ema_fast"]
    ema_slow = market_state["ema_slow"]
    prev_ema_slow = market_state["prev_ema_slow"]
    trend_base = max(abs(ema_slow), 1e-9)
    return {
        "spread_pct": (ema_fast - ema_slow) / trend_base,
        "slope_pct": (ema_slow - prev_ema_slow) / trend_base,
    }


def _flow_alignment_score(market_state, hourly, fourh, params, side):
    def _safe_float(payload, key, default):
        if payload is None:
            return default
        try:
            value = float(payload.get(key, default))
        except (TypeError, ValueError, AttributeError):
            return default
        return value

    intraday_trade_ratio = _safe_float(market_state, "trade_count_ratio", 1.0)
    intraday_buy_ratio = _safe_float(market_state, "taker_buy_ratio", 0.5)
    intraday_sell_ratio = _safe_float(market_state, "taker_sell_ratio", 0.5)
    intraday_imbalance = _safe_float(market_state, "flow_imbalance", 0.0)
    hourly_trade_ratio = _safe_float(hourly, "trade_count_ratio", 1.0)
    hourly_buy_ratio = _safe_float(hourly, "taker_buy_ratio", 0.5)
    hourly_sell_ratio = _safe_float(hourly, "taker_sell_ratio", 0.5)
    hourly_imbalance = _safe_float(hourly, "flow_imbalance", 0.0)
    fourh_buy_ratio = _safe_float(fourh, "taker_buy_ratio", 0.5)
    fourh_sell_ratio = _safe_float(fourh, "taker_sell_ratio", 0.5)
    fourh_imbalance = _safe_float(fourh, "flow_imbalance", 0.0)

    if side == "long":
        score = 0
        if intraday_trade_ratio >= params["breakout_trade_count_ratio_min"]:
            score += 1
        if intraday_buy_ratio >= params["breakout_taker_buy_ratio_min"]:
            score += 1
        if intraday_imbalance >= params["breakout_flow_imbalance_min"]:
            score += 1
        if hourly_trade_ratio >= params["hourly_trade_count_ratio_min"]:
            score += 1
        if hourly_buy_ratio >= params["hourly_taker_buy_ratio_min"]:
            score += 1
        if hourly_imbalance >= params["hourly_flow_confirmation_min"]:
            score += 1
        if fourh_buy_ratio >= params["fourh_taker_buy_ratio_min"]:
            score += 1
        if fourh_imbalance >= params["fourh_flow_confirmation_min"]:
            score += 1
        return score

    score = 0
    if intraday_trade_ratio >= 1.18:
        score += 1
    if intraday_sell_ratio >= 0.54:
        score += 1
    if intraday_imbalance <= -0.08:
        score += 1
    if hourly_trade_ratio >= 0.88:
        score += 1
    if hourly_sell_ratio >= 0.505:
        score += 1
    if hourly_imbalance <= -0.01:
        score += 1
    if fourh_sell_ratio >= 0.50:
        score += 1
    if fourh_imbalance <= 0.0:
        score += 1
    return score


def _flow_confirmation_ok(market_state, hourly, fourh, params, side, strong=False):
    score = _flow_alignment_score(market_state, hourly, fourh, params, side)

    def _safe_float(payload, key, default):
        if payload is None:
            return default
        try:
            value = float(payload.get(key, default))
        except (TypeError, ValueError, AttributeError):
            return default
        return value

    intraday_imbalance = _safe_float(market_state, "flow_imbalance", 0.0)
    hourly_buy_ratio = _safe_float(hourly, "taker_buy_ratio", 0.5)
    hourly_sell_ratio = _safe_float(hourly, "taker_sell_ratio", 0.5)
    hourly_imbalance = _safe_float(hourly, "flow_imbalance", 0.0)
    fourh_buy_ratio = _safe_float(fourh, "taker_buy_ratio", 0.5)
    fourh_sell_ratio = _safe_float(fourh, "taker_sell_ratio", 0.5)
    fourh_imbalance = _safe_float(fourh, "flow_imbalance", 0.0)

    if side == "long":
        if strong:
            return (
                score >= params["breakout_flow_score_strong_min"]
                and intraday_imbalance >= max(params["breakout_flow_imbalance_min"], 0.02)
                and hourly_imbalance >= -0.01
                and fourh_imbalance >= -0.02
                and hourly_buy_ratio >= max(params["hourly_taker_buy_ratio_min"], 0.5)
                and fourh_buy_ratio >= max(params["fourh_taker_buy_ratio_min"], 0.495)
            )
        return (
            score >= params["breakout_flow_score_min"]
            and intraday_imbalance >= -0.02
            and hourly_buy_ratio >= 0.495
            and fourh_buy_ratio >= 0.49
            and fourh_imbalance >= -0.03
        )

    if strong:
        return (
            score >= 6
            and intraday_imbalance <= -0.06
            and hourly_imbalance <= 0.0
            and fourh_imbalance <= 0.0
            and hourly_sell_ratio >= 0.505
            and fourh_sell_ratio >= 0.50
        )
    return (
        score >= 5
        and intraday_imbalance <= 0.02
        and hourly_sell_ratio >= 0.50
        and fourh_sell_ratio >= 0.495
        and fourh_imbalance <= 0.02
    )


def _flow_signal_metrics(market_state, hourly, fourh, params, side):
    def _safe_float(payload, key, default):
        if payload is None:
            return default
        try:
            value = float(payload.get(key, default))
        except (TypeError, ValueError, AttributeError):
            return default
        return value

    intraday_trade_ratio = _safe_float(market_state, "trade_count_ratio", 1.0)
    intraday_buy_ratio = _safe_float(market_state, "taker_buy_ratio", 0.5)
    intraday_sell_ratio = _safe_float(market_state, "taker_sell_ratio", 0.5)
    intraday_imbalance = _safe_float(market_state, "flow_imbalance", 0.0)
    hourly_trade_ratio = _safe_float(hourly, "trade_count_ratio", 1.0)
    hourly_buy_ratio = _safe_float(hourly, "taker_buy_ratio", 0.5)
    hourly_sell_ratio = _safe_float(hourly, "taker_sell_ratio", 0.5)
    hourly_imbalance = _safe_float(hourly, "flow_imbalance", 0.0)
    fourh_buy_ratio = _safe_float(fourh, "taker_buy_ratio", 0.5)
    fourh_sell_ratio = _safe_float(fourh, "taker_sell_ratio", 0.5)
    fourh_imbalance = _safe_float(fourh, "flow_imbalance", 0.0)

    if side == "long":
        participation_bias = (
            max(intraday_trade_ratio - params["breakout_trade_count_ratio_min"], 0.0)
            + max(hourly_trade_ratio - params["hourly_trade_count_ratio_min"], 0.0) * 0.5
        )
        directional_bias = (
            max(intraday_buy_ratio - params["breakout_taker_buy_ratio_min"], 0.0)
            + max(hourly_buy_ratio - params["hourly_taker_buy_ratio_min"], 0.0)
            + max(fourh_buy_ratio - params["fourh_taker_buy_ratio_min"], 0.0)
            + max(intraday_imbalance - params["breakout_flow_imbalance_min"], 0.0) * 2.0
            + max(hourly_imbalance - params["hourly_flow_confirmation_min"], 0.0)
            + max(fourh_imbalance - params["fourh_flow_confirmation_min"], 0.0)
        )
    else:
        participation_bias = (
            max(intraday_trade_ratio - 1.18, 0.0)
            + max(hourly_trade_ratio - 0.88, 0.0) * 0.5
        )
        directional_bias = (
            max(intraday_sell_ratio - 0.54, 0.0)
            + max(hourly_sell_ratio - 0.505, 0.0)
            + max(fourh_sell_ratio - 0.50, 0.0)
            + max(-intraday_imbalance - 0.08, 0.0) * 2.0
            + max(-hourly_imbalance - 0.01, 0.0)
            + max(-fourh_imbalance, 0.0)
        )
    return {
        "score": _flow_alignment_score(market_state, hourly, fourh, params, side),
        "participation_bias": participation_bias,
        "directional_bias": directional_bias,
    }


def _flow_entry_ok(market_state, hourly, fourh, params, side, strong=False):
    if side == "long" and not strong:
        metrics = _flow_signal_metrics(market_state, hourly, fourh, params, side)
        if (
            metrics["score"] >= max(params["breakout_flow_score_min"] - 1, 3)
            and metrics["participation_bias"] >= 0.02
            and metrics["directional_bias"] >= 0.03
        ):
            return True
    return _flow_confirmation_ok(market_state, hourly, fourh, params, side, strong=strong)


def _build_signal_context(data, idx, market_state, params):
    current = data[idx]
    prev = data[idx - 1]
    pre_prev = data[idx - 2]
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    if hourly is None or fourh is None:
        return None
    if not (_bar_is_valid(current) and _bar_is_valid(prev) and _bar_is_valid(pre_prev)):
        return None

    intraday = _intraday_trend_metrics(market_state)
    current_candle = _candle_metrics(current)
    prev_candle = _candle_metrics(prev)
    pre_prev_candle = _candle_metrics(pre_prev)
    avg_volume = max(_avg(data, idx - params["volume_lookback"] + 1, idx, "volume"), 1e-9)
    breakout_high = _window_max(data, idx - params["breakout_lookback"], idx - 1, "high")
    prev_breakout_high = _window_max(data, idx - params["breakout_lookback"] - 1, idx - 2, "high")
    breakdown_low = _window_min(data, idx - params["breakdown_lookback"], idx - 1, "low")
    prev_breakdown_low = _window_min(data, idx - params["breakdown_lookback"] - 1, idx - 2, "low")
    atr_ratio = market_state["atr_ratio"]
    recent_stats = _recent_window_stats(data, idx, 6, current["close"])

    return {
        "current": current,
        "prev": prev,
        "pre_prev": pre_prev,
        "hourly": hourly,
        "fourh": fourh,
        "intraday": intraday,
        "current_candle": current_candle,
        "prev_candle": prev_candle,
        "pre_prev_candle": pre_prev_candle,
        "volume_ratio": current["volume"] / avg_volume,
        "prev_volume": max(prev["volume"], 1e-9),
        "pre_prev_volume": max(pre_prev["volume"], 1e-9),
        "breakout_high": breakout_high,
        "prev_breakout_high": prev_breakout_high,
        "breakdown_low": breakdown_low,
        "prev_breakdown_low": prev_breakdown_low,
        "atr_ratio": atr_ratio,
        "breakout_distance_pct": (current["close"] - breakout_high) / max(breakout_high, 1e-9),
        "breakout_high_penetration_pct": max((current["high"] - breakout_high) / max(breakout_high, 1e-9), 0.0),
        "prev_breakout_distance_pct": max((prev["close"] - breakout_high) / max(breakout_high, 1e-9), 0.0),
        "prev_breakout_reference_distance_pct": max((prev["close"] - prev_breakout_high) / max(prev_breakout_high, 1e-9), 0.0),
        "prev_breakout_high_penetration_pct": max((prev["high"] - prev_breakout_high) / max(prev_breakout_high, 1e-9), 0.0),
        "breakdown_distance_pct": (breakdown_low - current["close"]) / max(breakdown_low, 1e-9),
        "breakdown_low_penetration_pct": max((breakdown_low - current["low"]) / max(breakdown_low, 1e-9), 0.0),
        "prev_breakdown_distance_pct": max((breakdown_low - prev["close"]) / max(breakdown_low, 1e-9), 0.0),
        "prev_breakdown_reference_distance_pct": max((prev_breakdown_low - prev["close"]) / max(prev_breakdown_low, 1e-9), 0.0),
        "prev_breakdown_low_penetration_pct": max((prev_breakdown_low - prev["low"]) / max(prev_breakdown_low, 1e-9), 0.0),
        "hourly_fast_extension_pct": (current["close"] - hourly["ema_fast"]) / max(current["close"], 1e-9),
        "hourly_anchor_extension_pct": (current["close"] - hourly["ema_anchor"]) / max(current["close"], 1e-9),
        "hourly_fast_discount_pct": (hourly["ema_fast"] - current["close"]) / max(current["close"], 1e-9),
        "hourly_anchor_discount_pct": (hourly["ema_anchor"] - current["close"]) / max(current["close"], 1e-9),
        "current_range": _bar_range(current),
        "prev_range": _bar_range(prev),
        "prev_prev_range": _bar_range(pre_prev),
        "recent_range_avg": recent_stats["range_avg"],
        "recent_volume_avg": recent_stats["volume_avg"],
        "recent_body_ratio_avg": recent_stats["body_ratio_avg"],
    }


def _build_long_trend_state(context, market_state, params):
    current = context["current"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    return {
        "intraday_bull": (
            current["close"] > market_state["ema_fast"] > market_state["ema_slow"]
            and market_state["adx"] >= params["intraday_adx_min"]
            and market_state["macd_line"] > market_state["signal_line"]
        ),
        "hourly_bull": (
            hourly["close"] > hourly["ema_fast"] > hourly["ema_slow"]
            and hourly["close"] > hourly["ema_anchor"]
            and hourly["macd_line"] > hourly["signal_line"]
            and hourly["adx"] >= params["hourly_adx_min"]
            and hourly["trend_spread_pct"] > 0.0
            and hourly["ema_slow_slope_pct"] > 0.0
        ),
        "fourh_bull": (
            fourh["close"] > fourh["ema_fast"] > fourh["ema_slow"]
            and fourh["macd_line"] > fourh["signal_line"]
            and fourh["adx"] >= params["fourh_adx_min"]
            and fourh["trend_spread_pct"] > 0.0
            and fourh["ema_slow_slope_pct"] > 0.0
        ),
        "fourh_bull_base": (
            fourh["close"] > fourh["ema_slow"]
            and fourh["close"] >= fourh["ema_fast"]
            and fourh["macd_line"] > fourh["signal_line"]
            and fourh["adx"] >= max(params["fourh_adx_min"] - 1.0, 11.5)
            and fourh["trend_spread_pct"] > 0.0
            and fourh["ema_slow_slope_pct"] > 0.0
        ),
        "fourh_bull_turn": (
            fourh["close"] > fourh["ema_slow"]
            and fourh["macd_line"] > fourh["signal_line"]
            and fourh["trend_spread_pct"] > 0.0
            and fourh["ema_slow_slope_pct"] > 0.0
            and fourh["adx"] >= max(params["fourh_adx_min"] - 0.8, 11.8)
        ),
    }


def _build_short_trend_state(context, market_state, params):
    current = context["current"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    return {
        "intraday_bear": (
            current["close"] < market_state["ema_fast"] < market_state["ema_slow"]
            and market_state["adx"] >= params["intraday_adx_min"]
            and market_state["macd_line"] < market_state["signal_line"]
        ),
        "hourly_bear": (
            hourly["close"] < hourly["ema_fast"] < hourly["ema_slow"]
            and hourly["close"] < hourly["ema_anchor"]
            and hourly["macd_line"] < hourly["signal_line"]
            and hourly["adx"] >= params["hourly_adx_min"]
            and hourly["trend_spread_pct"] < 0.0
            and hourly["ema_slow_slope_pct"] < 0.0
        ),
        "fourh_bear": (
            fourh["close"] < fourh["ema_slow"]
            and fourh["trend_spread_pct"] < 0.0
            and fourh["ema_slow_slope_pct"] < 0.0
            and fourh["adx"] >= max(params["fourh_adx_min"] - 0.5, 12.0)
        ),
        "fourh_bear_confirmed": (
            fourh["close"] < fourh["ema_fast"] < fourh["ema_slow"]
            and fourh["macd_line"] < fourh["signal_line"]
            and fourh["trend_spread_pct"] < 0.0
            and fourh["ema_slow_slope_pct"] < 0.0
        ),
    }


def _sideways_release_flags(market_state):
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
    aligned_trend = hourly["trend_spread_pct"] * fourh["trend_spread_pct"] > 0.0
    convexity_release = (
        aligned_trend
        and intraday_spread >= atr_ratio * 0.26
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.10, atr_ratio * 0.66)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.50, atr_ratio * 0.48)
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 0.98)
        and hourly_slope >= max(hourly_spread * 0.10, atr_ratio * 0.082)
        and fourh_slope >= max(fourh_spread * 0.18, atr_ratio * 0.028)
        and market_state["adx"] >= 13.5
        and hourly["adx"] >= 18.0
        and fourh["adx"] >= 12.0
    )
    trend_awakening = (
        aligned_trend
        and intraday_spread >= atr_ratio * 0.24
        and intraday_spread <= max(hourly_spread * 1.92, atr_ratio * 0.98)
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.92, atr_ratio * 0.54)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.42, atr_ratio * 0.40)
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.08, atr_ratio * 0.90)
        and hourly_slope >= max(hourly_spread * 0.12, atr_ratio * 0.080)
        and fourh_slope >= max(fourh_spread * 0.20, atr_ratio * 0.028)
        and market_state["adx"] >= 13.0
        and hourly["adx"] >= 17.0
        and fourh["adx"] >= 11.8
        and intraday_chop < SIDEWAYS_HARD_INTRADAY_CHOP_MIN + 2.0
        and hourly_chop < SIDEWAYS_HARD_HOURLY_CHOP_MIN + 2.0
    )
    fresh_directional_expansion = (
        aligned_trend
        and intraday_spread >= atr_ratio * 0.22
        and intraday_spread <= max(hourly_spread * 1.35, atr_ratio * 0.92)
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.02, atr_ratio * 0.60)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.56, atr_ratio * 0.52)
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.52, atr_ratio * 1.18)
        and hourly_slope >= max(hourly_spread * 0.13, atr_ratio * 0.084)
        and fourh_slope >= max(fourh_spread * 0.22, atr_ratio * 0.032)
        and market_state["adx"] >= 14.0
        and hourly["adx"] >= 18.5
        and fourh["adx"] >= 12.5
    )
    exhausted_drift = (
        aligned_trend
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.40, atr_ratio * 0.96)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.08, atr_ratio * 0.96)
        and intraday_spread < max(hourly_spread * 0.26, atr_ratio * 0.28)
        and hourly_slope < max(hourly_spread * 0.07, atr_ratio * 0.070)
        and fourh_slope < max(fourh_spread * 0.08, atr_ratio * 0.032)
        and (intraday_chop >= SIDEWAYS_INTRADAY_CHOP_MIN - 2.0 or hourly_chop >= SIDEWAYS_HOURLY_CHOP_MIN - 2.0)
    )
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
            and not (convexity_release or trend_awakening or fresh_directional_expansion)
        )
        or (
            hourly_spread < SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.92
            and fourh_spread < SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.92
            and hourly_slope < atr_ratio * 0.06
            and fourh_slope < atr_ratio * 0.03
        )
    )
    return {
        "intraday_spread": intraday_spread,
        "hourly_spread": hourly_spread,
        "fourh_spread": fourh_spread,
        "hourly_slope": hourly_slope,
        "fourh_slope": fourh_slope,
        "intraday_chop": intraday_chop,
        "hourly_chop": hourly_chop,
        "atr_ratio": atr_ratio,
        "adx_soft": adx_soft,
        "aligned_trend": aligned_trend,
        "convexity_release": convexity_release,
        "trend_awakening": trend_awakening,
        "fresh_directional_expansion": fresh_directional_expansion,
        "exhausted_drift": exhausted_drift,
        "hard_sideways": hard_sideways,
    }


def _is_sideways_regime(market_state):
    intraday = _intraday_trend_metrics(market_state)
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    release_flags = _sideways_release_flags(market_state)
    intraday_spread = release_flags["intraday_spread"]
    hourly_spread = release_flags["hourly_spread"]
    fourh_spread = release_flags["fourh_spread"]
    hourly_slope = release_flags["hourly_slope"]
    fourh_slope = release_flags["fourh_slope"]
    intraday_chop = release_flags["intraday_chop"]
    hourly_chop = release_flags["hourly_chop"]
    atr_ratio = release_flags["atr_ratio"]
    adx_soft = release_flags["adx_soft"]
    convexity_release = release_flags["convexity_release"]
    trend_awakening = release_flags["trend_awakening"]
    fresh_directional_expansion = release_flags["fresh_directional_expansion"]
    exhausted_drift = release_flags["exhausted_drift"]
    hard_sideways = release_flags["hard_sideways"]
    if hard_sideways:
        return True
    if exhausted_drift and not convexity_release:
        return True
    if trend_awakening or fresh_directional_expansion:
        return False

    mixed_trend = (
        hourly["trend_spread_pct"] * fourh["trend_spread_pct"] <= 0.0
        and intraday_spread < max(atr_ratio * 0.55, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.15)
    )
    if mixed_trend and (hourly_chop >= SIDEWAYS_HOURLY_CHOP_MIN - 1.0 or adx_soft):
        return True

    weak_trend = (
        hourly_spread < max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.20, atr_ratio * 0.72)
        and fourh_spread < max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.15, atr_ratio * 0.92)
        and hourly_slope < atr_ratio * 0.09
        and fourh_slope < atr_ratio * 0.045
    )
    if weak_trend and (intraday_chop >= SIDEWAYS_INTRADAY_CHOP_MIN - 1.0 or adx_soft):
        return True

    bull_front_run = (
        hourly["trend_spread_pct"] > 0.0
        and fourh["trend_spread_pct"] > 0.0
        and intraday["spread_pct"] > max(hourly_spread * 1.85, atr_ratio * 1.00)
        and hourly_spread < max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.55, atr_ratio * 0.95)
        and fourh_spread < max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.20, atr_ratio * 1.02)
        and hourly_slope < atr_ratio * 0.10
        and fourh_slope < atr_ratio * 0.05
        and (hourly_chop >= SIDEWAYS_HOURLY_CHOP_MIN - 2.0 or adx_soft)
    )
    if bull_front_run:
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
    if convexity_release or fresh_directional_expansion:
        signals -= 2
    return signals >= 3


def _directional_trend_metrics(market_state, side):
    intraday = _intraday_trend_metrics(market_state)
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    direction = 1.0 if side == "long" else -1.0
    return {
        "intraday_spread": direction * intraday["spread_pct"],
        "hourly_spread": direction * hourly["trend_spread_pct"],
        "fourh_spread": direction * fourh["trend_spread_pct"],
        "hourly_slope": direction * hourly["ema_slow_slope_pct"],
        "fourh_slope": direction * fourh["ema_slow_slope_pct"],
        "atr_ratio": market_state["atr_ratio"],
    }


def _trend_quality_long(market_state):
    p = PARAMS
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "long")
    atr_ratio = metrics["atr_ratio"]

    confirms = 0
    if market_state["adx"] >= max(p["intraday_adx_min"], 14.0):
        confirms += 1
    if metrics["intraday_spread"] >= atr_ratio * 0.28:
        confirms += 1
    if metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.20, atr_ratio * 0.74):
        confirms += 1
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.70, atr_ratio * 0.66):
        confirms += 1
    if metrics["hourly_slope"] >= atr_ratio * 0.072 and metrics["fourh_slope"] >= atr_ratio * 0.028:
        confirms += 1

    hourly_fast_extension = (hourly["close"] - hourly["ema_fast"]) / max(hourly["close"], 1e-9)
    hourly_anchor_extension = (hourly["close"] - hourly["ema_anchor"]) / max(hourly["close"], 1e-9)
    fourh_ownership_ok = (
        metrics["fourh_spread"] >= max(metrics["hourly_spread"] * 0.72, atr_ratio * 0.74)
        and metrics["fourh_slope"] >= atr_ratio * 0.030
        and fourh["adx"] >= max(p["fourh_adx_min"] - 0.2, 12.8)
    )
    early_turn_ok = (
        metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.42, atr_ratio * 0.84)
        and metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.58, atr_ratio * 0.56)
        and metrics["fourh_spread"] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.28, atr_ratio * 1.02)
        and metrics["fourh_spread"] >= metrics["hourly_spread"] * 0.48
        and metrics["hourly_slope"] >= atr_ratio * 0.082
        and metrics["fourh_slope"] >= atr_ratio * 0.028
        and hourly_fast_extension <= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.88)
        and hourly_anchor_extension <= max(atr_ratio * 1.18, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.84)
    )
    overextended_without_fourh = (
        hourly_fast_extension >= max(atr_ratio * 0.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.00)
        and hourly_anchor_extension >= max(atr_ratio * 1.32, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.10)
        and not fourh_ownership_ok
    )
    if overextended_without_fourh:
        return False
    return confirms >= 4 and (fourh_ownership_ok or early_turn_ok or hourly["adx"] >= p["hourly_adx_min"] + 1.0)


def _trend_quality_short(market_state):
    p = PARAMS
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "short")
    atr_ratio = metrics["atr_ratio"]

    confirms = 0
    if market_state["adx"] >= max(p["intraday_adx_min"], 14.5):
        confirms += 1
    if metrics["intraday_spread"] >= atr_ratio * 0.30:
        confirms += 1
    if metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.24, atr_ratio * 0.78):
        confirms += 1
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.78, atr_ratio * 0.72):
        confirms += 1
    if metrics["hourly_slope"] >= atr_ratio * 0.076 and metrics["fourh_slope"] >= atr_ratio * 0.032:
        confirms += 1

    hourly_fast_discount = (hourly["ema_fast"] - hourly["close"]) / max(hourly["close"], 1e-9)
    hourly_anchor_discount = (hourly["ema_anchor"] - hourly["close"]) / max(hourly["close"], 1e-9)
    fourh_participation_ok = (
        metrics["fourh_spread"] >= max(metrics["hourly_spread"] * 0.74, atr_ratio * 0.78)
        and metrics["fourh_slope"] >= atr_ratio * 0.032
        and fourh["adx"] >= max(p["fourh_adx_min"] - 0.2, 13.4)
    )
    fresh_pressure_ok = (
        metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.54, atr_ratio * 0.92)
        and metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.84, atr_ratio * 0.78)
        and metrics["hourly_slope"] >= atr_ratio * 0.088
        and metrics["fourh_slope"] >= atr_ratio * 0.036
        and market_state["adx"] >= 15.2
    )
    overdiscounted_without_fourh = (
        hourly_fast_discount >= max(atr_ratio * 0.96, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.18)
        and hourly_anchor_discount >= max(atr_ratio * 1.52, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.62)
        and not fourh_participation_ok
    )
    if overdiscounted_without_fourh and not fresh_pressure_ok:
        return False
    return confirms >= 4 and (fourh_participation_ok or fresh_pressure_ok or hourly["adx"] >= p["hourly_adx_min"] + 2.0)


def _trend_quality_ok(market_state, side):
    if side == "long":
        return _trend_quality_long(market_state)
    return _trend_quality_short(market_state)


def _trend_followthrough_long(market_state, trigger_price, current_close):
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "long")
    atr_ratio = metrics["atr_ratio"]
    breakout_distance_pct = (current_close - trigger_price) / max(trigger_price, 1e-9)
    hourly_fast_extension = (current_close - hourly["ema_fast"]) / max(current_close, 1e-9)
    hourly_anchor_extension = (current_close - hourly["ema_anchor"]) / max(current_close, 1e-9)

    confirms = 0
    if metrics["intraday_spread"] >= atr_ratio * 0.26:
        confirms += 1
    if metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.20, atr_ratio * 0.76):
        confirms += 1
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.66, atr_ratio * 0.64):
        confirms += 1
    if metrics["hourly_slope"] >= atr_ratio * 0.074 and metrics["fourh_slope"] >= atr_ratio * 0.028:
        confirms += 1
    if breakout_distance_pct >= atr_ratio * 0.04:
        confirms += 1

    stale_chase = (
        breakout_distance_pct >= atr_ratio * 0.14
        and hourly_fast_extension >= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.90)
        and hourly_anchor_extension >= max(atr_ratio * 1.24, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.98)
        and (
            metrics["fourh_spread"] < max(metrics["hourly_spread"] * 0.90, atr_ratio * 0.92)
            or metrics["fourh_slope"] < atr_ratio * 0.036
        )
    )
    if stale_chase:
        return False
    return confirms >= (5 if breakout_distance_pct >= atr_ratio * 0.12 else 4)


def _trend_followthrough_short(market_state, trigger_price, current_close):
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "short")
    atr_ratio = metrics["atr_ratio"]
    breakdown_distance_pct = (trigger_price - current_close) / max(trigger_price, 1e-9)
    hourly_fast_discount = (hourly["ema_fast"] - current_close) / max(current_close, 1e-9)
    hourly_anchor_discount = (hourly["ema_anchor"] - current_close) / max(current_close, 1e-9)

    confirms = 0
    if metrics["intraday_spread"] >= atr_ratio * 0.28:
        confirms += 1
    if metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.24, atr_ratio * 0.80):
        confirms += 1
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.74, atr_ratio * 0.70):
        confirms += 1
    if metrics["hourly_slope"] >= atr_ratio * 0.078 and metrics["fourh_slope"] >= atr_ratio * 0.032:
        confirms += 1
    if breakdown_distance_pct >= atr_ratio * 0.05:
        confirms += 1

    exhausted_selloff = (
        breakdown_distance_pct >= atr_ratio * 0.20
        and hourly_fast_discount >= max(atr_ratio * 0.98, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.22)
        and hourly_anchor_discount >= max(atr_ratio * 1.58, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.72)
        and (
            metrics["fourh_spread"] < max(metrics["hourly_spread"] * 0.88, atr_ratio * 1.04)
            or metrics["fourh_slope"] < atr_ratio * 0.042
        )
    )
    if exhausted_selloff:
        return False
    return confirms >= (5 if breakdown_distance_pct >= atr_ratio * 0.18 else 4)


def _trend_followthrough_ok(market_state, side, trigger_price, current_close):
    if side == "long":
        return _trend_followthrough_long(market_state, trigger_price, current_close)
    return _trend_followthrough_short(market_state, trigger_price, current_close)


def long_outer_context_ok(context, market_state, params):
    long_state = _build_long_trend_state(context, market_state, params)
    reclaim_ready = (
        context["current"]["close"] > market_state["ema_fast"] > market_state["ema_slow"]
        and market_state["macd_line"] > market_state["signal_line"]
        and market_state["adx"] >= max(params["intraday_adx_min"] - 0.5, 12.0)
        and context["breakout_distance_pct"] >= -context["atr_ratio"] * 0.02
        and context["hourly_fast_extension_pct"] <= max(
            context["atr_ratio"] * 0.84,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.92,
        )
        and context["hourly_anchor_extension_pct"] <= max(
            context["atr_ratio"] * 1.22,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.92,
        )
    )
    base_ownership_ready = long_state["fourh_bull_base"] and (
        long_state["intraday_bull"] or reclaim_ready
    )
    early_rotation_ready = reclaim_ready and long_state["fourh_bull_turn"]
    return (
        _trend_quality_long(market_state)
        and long_state["hourly_bull"]
        and (base_ownership_ready or early_rotation_ready)
    )


def long_breakout_ok(context, market_state, params):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    current_candle = context["current_candle"]
    atr_ratio = context["atr_ratio"]
    breakout_distance_pct = context["breakout_distance_pct"]
    breakout_high = context["breakout_high"]
    breakout_high_penetration_pct = context["breakout_high_penetration_pct"]
    return (
        current["close"] >= breakout_high * (1.0 + params["breakout_buffer_pct"])
        and breakout_distance_pct >= atr_ratio * 0.025
        and breakout_distance_pct <= atr_ratio * 0.24
        and breakout_high_penetration_pct >= max(atr_ratio * 0.05, breakout_distance_pct * 0.55)
        and current["high"] > prev["high"]
        and current["close"] > prev["close"]
        and context["current_range"] >= context["recent_range_avg"] * 0.88
        and current_candle["close_pos"] >= max(params["breakout_close_pos_min"] - 0.02, 0.58)
        and current_candle["body_ratio"] >= max(params["breakout_body_ratio_min"] - 0.03, 0.28)
        and context["volume_ratio"] >= max(params["breakout_volume_ratio_min"] - 0.06, 1.02)
        and current["volume"] >= max(context["prev_volume"] * 0.92, context["recent_volume_avg"] * 0.92)
        and market_state["adx"] >= max(params["breakout_adx_min"] - 1.0, params["intraday_adx_min"] + 0.8)
        and params["breakout_rsi_min"] <= market_state["rsi"] <= min(params["breakout_rsi_max"], 69.0)
        and market_state["histogram"] >= max(params["breakout_hist_min"], 3.5)
        and _flow_entry_ok(market_state, hourly, fourh, params, "long", strong=False)
        and context["hourly_fast_extension_pct"] <= max(atr_ratio * 0.94, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.08)
        and context["hourly_anchor_extension_pct"] <= max(atr_ratio * 1.36, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.18)
        and fourh["trend_spread_pct"] >= max(hourly["trend_spread_pct"] * 0.42, atr_ratio * 0.54)
        and fourh["ema_slow_slope_pct"] >= atr_ratio * 0.028
    )


def long_pullback_ok(context, market_state, params):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    breakout_high = context["breakout_high"]
    breakout_distance_pct = context["breakout_distance_pct"]
    breakout_high_penetration_pct = context["breakout_high_penetration_pct"]
    return (
        current["close"] >= breakout_high * (1.0 + params["breakout_buffer_pct"])
        and breakout_distance_pct >= atr_ratio * 0.02
        and breakout_distance_pct <= atr_ratio * 0.18
        and breakout_high_penetration_pct >= max(atr_ratio * 0.06, breakout_distance_pct * 0.65)
        and context["prev_breakout_distance_pct"] <= atr_ratio * 0.08
        and prev["low"] <= breakout_high * (1.0 + atr_ratio * 0.14)
        and prev["low"] >= market_state["ema_fast"] * (1.0 - atr_ratio * 0.44)
        and prev["close"] <= prev["high"] - context["prev_range"] * 0.16
        and current["close"] > max(prev["close"], breakout_high)
        and context["current_range"] >= max(context["prev_range"] * 0.96, context["recent_range_avg"] * 0.88)
        and context["current_candle"]["close_pos"] >= max(params["breakout_close_pos_min"] + 0.01, 0.62)
        and context["current_candle"]["body_ratio"] >= max(context["prev_candle"]["body_ratio"] * 0.96, 0.30)
        and current["volume"] >= max(context["prev_volume"] * 0.94, context["recent_volume_avg"] * 0.92)
        and context["volume_ratio"] >= max(params["breakout_volume_ratio_min"] - 0.08, 1.02)
        and _flow_entry_ok(market_state, hourly, fourh, params, "long", strong=False)
        and context["hourly_fast_extension_pct"] <= max(atr_ratio * 0.92, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.00)
        and context["hourly_anchor_extension_pct"] <= max(atr_ratio * 1.30, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.02)
        and fourh["trend_spread_pct"] >= max(hourly["trend_spread_pct"] * 0.40, atr_ratio * 0.52)
        and fourh["ema_slow_slope_pct"] >= atr_ratio * 0.028
    )


def long_trend_reaccel_ok(context, market_state, params):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    breakout_distance_pct = context["breakout_distance_pct"]
    breakout_high = context["breakout_high"]
    breakout_high_penetration_pct = context["breakout_high_penetration_pct"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "long")
    return (
        current["close"] >= breakout_high * (1.0 + params["breakout_buffer_pct"])
        and breakout_distance_pct >= atr_ratio * 0.14
        and breakout_distance_pct <= atr_ratio * 0.32
        and breakout_high_penetration_pct >= max(atr_ratio * 0.20, breakout_distance_pct * 1.06)
        and current["close"] > prev["high"]
        and context["current_range"] >= context["recent_range_avg"] * 1.08
        and context["current_candle"]["close_pos"] >= max(params["breakout_close_pos_min"] + 0.12, 0.74)
        and context["current_candle"]["body_ratio"] >= max(params["breakout_body_ratio_min"] + 0.10, 0.42)
        and context["volume_ratio"] >= max(params["breakout_volume_ratio_min"] + 0.08, 1.20)
        and current["volume"] >= max(context["prev_volume"] * 1.02, context["recent_volume_avg"] * 1.04)
        and market_state["adx"] >= max(params["breakout_adx_min"], params["intraday_adx_min"] + 1.8)
        and params["breakout_rsi_min"] <= market_state["rsi"] <= min(params["breakout_rsi_max"], 68.0)
        and market_state["histogram"] >= max(params["breakout_hist_min"] + 0.2, 4.2)
        and hourly["trend_spread_pct"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.54, atr_ratio * 0.96)
        and fourh["trend_spread_pct"] >= max(hourly["trend_spread_pct"] * 0.82, atr_ratio * 0.94)
        and fourh["ema_slow_slope_pct"] >= atr_ratio * 0.040
        and _flow_entry_ok(market_state, hourly, fourh, params, "long", strong=True)
        and flow_metrics["directional_bias"] >= 0.04
    )


def long_signal_path_ok(breakout_ok, pullback_ok, reaccel_ok):
    return breakout_ok or pullback_ok or reaccel_ok


def long_final_veto_clear(context, market_state, params, breakout_ok, pullback_ok, reaccel_ok):
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    breakout_distance_pct = context["breakout_distance_pct"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "long")
    mature_breakout_risk = (
        breakout_distance_pct >= atr_ratio * 0.18
        and context["hourly_fast_extension_pct"] >= max(atr_ratio * 0.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.96)
        and context["hourly_anchor_extension_pct"] >= max(atr_ratio * 1.32, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.12)
        and (
            fourh["trend_spread_pct"] < max(hourly["trend_spread_pct"] * 0.86, atr_ratio * 1.00)
            or fourh["ema_slow_slope_pct"] < atr_ratio * 0.040
            or fourh["adx"] < max(params["fourh_adx_min"], 13.5)
        )
    )
    stale_rediscovery_risk = (
        context["prev_breakout_reference_distance_pct"] >= atr_ratio * 0.08
        and breakout_distance_pct < max(context["prev_breakout_reference_distance_pct"] + atr_ratio * 0.04, atr_ratio * 0.18)
        and context["current_range"] < max(context["prev_range"] * 1.04, context["recent_range_avg"] * 1.04)
        and context["current"]["volume"] < max(context["prev_volume"] * 1.04, context["recent_volume_avg"] * 1.04)
    )
    weak_flow_chase = (
        flow_metrics["score"] < params["breakout_flow_score_min"]
        and flow_metrics["directional_bias"] < 0.04
        and not pullback_ok
        and breakout_distance_pct >= atr_ratio * 0.12
    )
    if mature_breakout_risk and not reaccel_ok:
        return False
    if stale_rediscovery_risk and not pullback_ok:
        return False
    if weak_flow_chase:
        return False
    return _trend_followthrough_long(market_state, context["breakout_high"], context["current"]["close"])


def short_outer_context_ok(context, market_state, params):
    short_state = _build_short_trend_state(context, market_state, params)
    return (
        short_state["intraday_bear"]
        and short_state["hourly_bear"]
        and short_state["fourh_bear"]
        and _trend_quality_short(market_state)
    )


def breakdown_ready(context, market_state, params):
    current = context["current"]
    prev = context["prev"]
    return (
        current["close"] <= context["breakdown_low"] * (1.0 - params["breakdown_buffer_pct"])
        and context["breakdown_distance_pct"] >= context["atr_ratio"] * 0.10
        and current["close"] < prev["low"]
        and context["current_candle"]["close_pos"] <= params["breakdown_close_pos_max"]
        and context["current_candle"]["body_ratio"] >= params["breakdown_body_ratio_min"]
        and context["volume_ratio"] >= params["breakdown_volume_ratio_min"]
        and current["volume"] >= context["prev_volume"] * 0.94
        and market_state["adx"] >= params["breakdown_adx_min"]
        and params["breakdown_rsi_min"] <= market_state["rsi"] <= params["breakdown_rsi_max"]
        and market_state["histogram"] <= params["breakdown_hist_max"]
    )


def short_breakdown_ok(context, market_state, params):
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "short")
    flow_ready = _flow_entry_ok(market_state, hourly, fourh, params, "short", strong=False)
    direct_breakdown = (
        context["current_candle"]["close_pos"] <= min(params["breakdown_close_pos_max"] + 0.01, 0.30)
        and context["current_candle"]["body_ratio"] >= max(params["breakdown_body_ratio_min"] - 0.01, 0.40)
        and context["current"]["volume"] >= max(context["prev_volume"] * 0.95, context["recent_volume_avg"] * 0.96)
        and flow_metrics["directional_bias"] >= 0.04
    )
    acceptance_breakdown = (
        context["breakdown_distance_pct"] <= atr_ratio * 0.22
        and context["prev_breakdown_distance_pct"] <= atr_ratio * 0.06
        and context["current"]["close"] < context["prev"]["close"]
        and context["current"]["low"] < context["prev"]["low"]
        and context["current_range"] >= max(context["prev_range"] * 0.96, context["recent_range_avg"] * 0.92)
        and context["current_candle"]["close_pos"] <= min(params["breakdown_close_pos_max"] + 0.03, 0.32)
        and context["current_candle"]["body_ratio"] >= max(params["breakdown_body_ratio_min"] - 0.03, 0.36)
        and context["current"]["volume"] >= max(context["prev_volume"] * 0.92, context["recent_volume_avg"] * 0.94)
        and flow_metrics["score"] >= 5
        and (
            flow_metrics["directional_bias"] >= 0.03
            or context["volume_ratio"] >= max(params["breakdown_volume_ratio_min"], 1.10)
        )
    )
    return (
        breakdown_ready(context, market_state, params)
        and context["breakdown_distance_pct"] <= atr_ratio * 0.30
        and context["breakdown_low_penetration_pct"] >= max(atr_ratio * 0.14, context["breakdown_distance_pct"] * 0.96)
        and flow_ready
        and (direct_breakdown or acceptance_breakdown)
    )


def short_bounce_fail_ok(context, market_state, params):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    return (
        breakdown_ready(context, market_state, params)
        and context["breakdown_distance_pct"] >= atr_ratio * 0.06
        and context["breakdown_distance_pct"] <= atr_ratio * 0.18
        and context["prev_breakdown_distance_pct"] <= atr_ratio * 0.05
        and prev["high"] >= context["breakdown_low"] * (1.0 - atr_ratio * 0.02)
        and prev["high"] <= hourly["ema_fast"] * (1.0 + atr_ratio * 0.36)
        and prev["close"] >= prev["low"] + context["prev_range"] * 0.30
        and current["close"] < prev["low"]
        and context["current_range"] >= max(context["prev_range"] * 1.02, context["recent_range_avg"] * 0.94)
        and context["current_candle"]["body_ratio"] >= max(context["prev_candle"]["body_ratio"] * 1.02, 0.40)
        and context["volume_ratio"] >= max(params["breakdown_volume_ratio_min"], 1.08)
        and _flow_entry_ok(market_state, hourly, fourh, params, "short", strong=False)
        and fourh["trend_spread_pct"] <= max(hourly["trend_spread_pct"] * 0.52, -atr_ratio * 0.58)
    )


def short_trend_reaccel_ok(context, market_state, params):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "short")
    return (
        breakdown_ready(context, market_state, params)
        and context["breakdown_distance_pct"] >= atr_ratio * 0.14
        and context["breakdown_distance_pct"] <= atr_ratio * 0.30
        and context["breakdown_low_penetration_pct"] >= max(atr_ratio * 0.20, context["breakdown_distance_pct"] * 1.10)
        and current["close"] < prev["low"]
        and context["current_range"] >= context["recent_range_avg"] * 1.06
        and context["current_candle"]["close_pos"] <= min(params["breakdown_close_pos_max"] - 0.04, 0.28)
        and context["current_candle"]["body_ratio"] >= max(params["breakdown_body_ratio_min"] + 0.06, 0.44)
        and context["volume_ratio"] >= max(params["breakdown_volume_ratio_min"] + 0.08, 1.18)
        and market_state["adx"] >= max(params["breakdown_adx_min"], params["intraday_adx_min"] + 1.2)
        and hourly["trend_spread_pct"] <= -max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.34, atr_ratio * 0.88)
        and fourh["trend_spread_pct"] <= -max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.86, atr_ratio * 0.80)
        and fourh["ema_slow_slope_pct"] <= -atr_ratio * 0.038
        and _flow_entry_ok(market_state, hourly, fourh, params, "short", strong=True)
        and flow_metrics["directional_bias"] >= 0.04
    )


def short_final_veto_clear(context, market_state, params, breakdown_ok, bounce_fail_ok, reaccel_ok):
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "short")
    exhausted_selloff = (
        context["breakdown_distance_pct"] >= atr_ratio * 0.20
        and context["hourly_fast_discount_pct"] >= max(atr_ratio * 0.98, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.22)
        and context["hourly_anchor_discount_pct"] >= max(atr_ratio * 1.58, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.72)
        and (
            fourh["trend_spread_pct"] > min(hourly["trend_spread_pct"] * 0.90, -atr_ratio * 1.02)
            or fourh["ema_slow_slope_pct"] > -atr_ratio * 0.040
            or fourh["adx"] < max(params["fourh_adx_min"], 13.8)
        )
    )
    stale_breakdown_risk = (
        context["prev_breakdown_reference_distance_pct"] >= atr_ratio * 0.08
        and context["breakdown_distance_pct"] < max(context["prev_breakdown_reference_distance_pct"] + atr_ratio * 0.04, atr_ratio * 0.18)
        and context["current_range"] < max(context["prev_range"] * 1.04, context["recent_range_avg"] * 1.04)
        and context["current"]["volume"] < max(context["prev_volume"] * 1.04, context["recent_volume_avg"] * 1.04)
    )
    weak_flow_dump = (
        flow_metrics["score"] < 5
        and flow_metrics["directional_bias"] < 0.04
        and not (breakdown_ok or bounce_fail_ok)
        and context["breakdown_distance_pct"] >= atr_ratio * 0.12
    )
    if exhausted_selloff and not reaccel_ok:
        return False
    if stale_breakdown_risk and not breakdown_ok:
        return False
    if weak_flow_dump:
        return False
    return _trend_followthrough_short(market_state, context["breakdown_low"], context["current"]["close"])


def _long_entry_signal(data, idx, positions, market_state):
    signal = strategy(data, idx, positions, market_state)
    return signal if signal == "long_breakout" else None


def _short_entry_signal(data, idx, positions, market_state):
    signal = strategy(data, idx, positions, market_state)
    return signal if signal == "short_breakdown" else None


def strategy(data, idx, positions, market_state):
    p = PARAMS
    if idx < p["min_history"]:
        return None

    context = _build_signal_context(data, idx, market_state, p)
    if context is None:
        return None
    if _is_sideways_regime(market_state):
        return None

    _record_funnel_pass("long", "sideways_pass")
    _record_funnel_pass("short", "sideways_pass")

    if long_outer_context_ok(context, market_state, p):
        _record_funnel_pass("long", "outer_context_pass")
        long_breakout_path = long_breakout_ok(context, market_state, p)
        long_pullback_path = long_pullback_ok(context, market_state, p)
        long_reaccel_path = long_trend_reaccel_ok(context, market_state, p)
        long_ownership_relay = (
            not long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path)
            and context["current"]["close"] >= context["breakout_high"] * (1.0 + p["breakout_buffer_pct"])
            and context["breakout_distance_pct"] >= context["atr_ratio"] * 0.04
            and context["current"]["close"] > context["prev"]["close"]
            and context["current_candle"]["close_pos"] >= max(p["breakout_close_pos_min"] - 0.02, 0.58)
            and context["current"]["volume"] >= max(context["prev_volume"] * 0.90, context["recent_volume_avg"] * 0.90)
            and _flow_entry_ok(
                market_state,
                context["hourly"],
                context["fourh"],
                p,
                "long",
                strong=False,
            )
        )
        if long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path) or long_ownership_relay:
            _record_funnel_pass("long", "path_pass")
            if long_final_veto_clear(
                context,
                market_state,
                p,
                long_breakout_path,
                long_pullback_path,
                long_reaccel_path,
            ):
                _record_funnel_pass("long", "final_veto_pass")
                return "long_breakout"

    if short_outer_context_ok(context, market_state, p):
        _record_funnel_pass("short", "outer_context_pass")
        short_breakdown_path = short_breakdown_ok(context, market_state, p)
        short_bounce_fail_path = short_bounce_fail_ok(context, market_state, p)
        short_reaccel_path = short_trend_reaccel_ok(context, market_state, p)
        if long_signal_path_ok(short_breakdown_path, short_bounce_fail_path, short_reaccel_path):
            _record_funnel_pass("short", "path_pass")
            if short_final_veto_clear(
                context,
                market_state,
                p,
                short_breakdown_path,
                short_bounce_fail_path,
                short_reaccel_path,
            ):
                _record_funnel_pass("short", "final_veto_pass")
                return "short_breakdown"

    return None
