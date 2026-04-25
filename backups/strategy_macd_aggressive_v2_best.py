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
SIDEWAYS_RELEASE_RELAX = {
    "spread_floor_mult": 0.88,
    "slope_floor_mult": 0.90,
    "atr_ceiling_mult": 1.12,
    "chop_buffer": 2.5,
    "hard_sideways_atr_mult": 1.22,
    "hard_sideways_spread_mult": 1.18,
    "extreme_compression_atr_mult": 0.96,
    "extreme_compression_spread_mult": 0.94,
}

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
    "macd_fast": 8,
    "macd_signal": 6,
    "macd_slow": 20,
    "min_history": 260,
    "volume_lookback": 9,
}
# PARAMS_END


# EXIT_PARAMS_START
EXIT_PARAMS = {
    "break_even_activation_pct": 40.0,
    "break_even_buffer_pct": 0.35,
    "breakout_break_even_activation_pct": 60.0,
    "breakout_max_hold_bars": 384,
    "breakout_stop_atr_mult": 2.3,
    "breakout_tp1_close_fraction": 0.16,
    "breakout_tp1_pnl_pct": 80.0,
    "breakout_trailing_activation_pct": 122.7,
    "breakout_trailing_giveback_pct": 46.6,
    "dynamic_hold_adx_strong_threshold": 26.0,
    "dynamic_hold_adx_threshold": 16.0,
    "dynamic_hold_extension_bars": 96,
    "dynamic_hold_max_bars": 384,
    "entry_delay_minutes": 1,
    "execution_use_1m": 1,
    "funding_fee_enabled": 1,
    "leverage": 20,
    "max_concurrent_positions": 4,
    "max_hold_bars": 288,
    "okx_maker_fee_rate": 0.0002,
    "okx_taker_fee_rate": 0.0005,
    "position_fraction": 0.17,
    "position_size_max": 30000,
    "position_size_min": 5000,
    "pyramid_adx_min": 19.0,
    "pyramid_enabled": 1,
    "pyramid_max_times": 2,
    "pyramid_size_ratio": 0.28,
    "pyramid_trigger_pnl": 22.9,
    "regime_close_below_hourly_fast": 0,
    "regime_exit_confirm_bars": 1,
    "regime_exit_enabled": 1,
    "regime_hist_floor": -110.0,
    "regime_price_confirm_buffer_pct": 0.015,
    "short_breakdown_break_even_activation_pct": 25.7,
    "short_breakdown_max_hold_bars": 96,
    "short_breakdown_stop_atr_mult": 2.1,
    "short_breakdown_tp1_close_fraction": 0.22,
    "short_breakdown_tp1_pnl_pct": 31.4,
    "short_breakdown_trailing_activation_pct": 40.0,
    "short_breakdown_trailing_giveback_pct": 12.9,
    "slippage_pct": 0.0003,
    "stop_atr_mult": 3.1,
    "stop_max_loss_pct": 75.7,
    "tp1_close_fraction": 0.04,
    "tp1_pnl_pct": 65.7,
    "trading_fee_enabled": 1,
    "trailing_activation_pct": 124.3,
    "trailing_giveback_pct": 40.0,
}
# EXIT_PARAMS_END


ENTRY_SIGNAL_ALIASES = {
    "long_breakout": "long_pullback",
    "long_pullback": "long_pullback",
    "long_reaccel": "long_pullback",
    "long_relay": "long_pullback",
    "long_impulse": "long_pullback",
    "long_retest": "long_pullback",
    "short_breakdown": "short_breakdown",
    "short_bounce_fail": "short_breakdown",
    "short_reaccel": "short_breakdown",
    "short_impulse": "short_breakdown",
    "short_retest": "short_breakdown",
}
ENTRY_PATH_TAGS = {
    "long_breakout": "long_impulse",
    "long_pullback": "long_retest",
    "long_reaccel": "long_reaccel",
    "long_relay": "long_relay",
    "long_impulse": "long_impulse",
    "long_retest": "long_retest",
    "short_breakdown": "short_impulse",
    "short_bounce_fail": "short_retest",
    "short_reaccel": "short_reaccel",
    "short_impulse": "short_impulse",
    "short_retest": "short_retest",
}


FUNNEL_SIDES = ("long", "short")
FUNNEL_STAGES = ("sideways_pass", "outer_context_pass", "path_pass", "final_veto_pass")
_FUNNEL_DIAGNOSTICS = {}
LONG_PULLBACK_HOLD_TAGS = {"long_retest", "long_reaccel", "long_relay"}
LONG_PYRAMID_MAX_ADDS = 2


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


def normalize_entry_signal(signal, fallback_side=""):
    text = str(signal or "").strip()
    normalized = ENTRY_SIGNAL_ALIASES.get(text, "")
    if normalized:
        return normalized
    side = str(fallback_side or "").strip().lower()
    if not text:
        if side == "long":
            return "long_pullback"
        if side == "short":
            return "short_breakdown"
        return ""
    if text.startswith("long_"):
        return "long_pullback"
    if text.startswith("short_"):
        return "short_breakdown"
    return text


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


def _anchored_long_breakout_high(bar, atr_ratio):
    candle = _candle_metrics(bar)
    body_high = max(bar["open"], bar["close"])
    bar_range = _bar_range(bar)
    raw_high = bar["high"]
    wick_excess = max(raw_high - body_high, 0.0)
    upper_wick_ratio = wick_excess / bar_range if bar_range > 0.0 else 0.0
    body_ratio = candle["body_ratio"]
    close_pos = candle["close_pos"]
    strong_bullish_anchor = (
        bar["close"] >= bar["open"]
        and close_pos >= 0.66
        and body_ratio >= 0.26
        and upper_wick_ratio <= 0.36
    )
    if strong_bullish_anchor:
        allowed_wick_excess = max(
            body_high * atr_ratio * 0.34,
            bar_range * 0.20,
        )
    else:
        anchor_quality = min(
            1.0,
            max(0.0, (close_pos - 0.50) / 0.22) * 0.58
            + max(0.0, (body_ratio - 0.14) / 0.20) * 0.42,
        )
        wick_penalty = min(1.0, upper_wick_ratio / 0.30)
        weak_close_wick = upper_wick_ratio >= 0.30 and close_pos <= 0.70 and body_ratio <= 0.30
        stale_wick_anchor = upper_wick_ratio >= 0.42 and close_pos <= 0.74
        compressed_allowance = bar_range * (0.05 + 0.08 * anchor_quality)
        atr_allowance = body_high * atr_ratio * (0.10 + 0.14 * anchor_quality)
        allowed_wick_excess = max(
            min(compressed_allowance, atr_allowance) * (1.0 - 0.64 * wick_penalty),
            min(bar_range * 0.08, body_high * atr_ratio * 0.10),
        )
        if weak_close_wick:
            allowed_wick_excess = min(
                allowed_wick_excess,
                max(
                    min(bar_range * 0.04, body_high * atr_ratio * 0.07),
                    body_high * 0.00012,
                ),
            )
        if stale_wick_anchor:
            allowed_wick_excess = min(
                allowed_wick_excess,
                max(
                    min(bar_range * 0.03, body_high * atr_ratio * 0.05),
                    body_high * 0.00010,
                ),
            )
    return body_high + min(wick_excess, allowed_wick_excess)


def _effective_long_breakout_reference_bar(bar, atr_ratio):
    candle = _candle_metrics(bar)
    bar_range = _bar_range(bar)
    body_high = max(bar["open"], bar["close"])
    upper_wick = max(bar["high"] - body_high, 0.0)
    upper_wick_ratio = upper_wick / bar_range if bar_range > 0.0 else 0.0
    bullish_body = max(bar["close"] - bar["open"], 0.0)
    bullish_body_ratio = bullish_body / bar_range if bar_range > 0.0 else 0.0
    strong_close = candle["close_pos"] >= 0.72 and candle["body_ratio"] >= 0.26
    wick_cap = 0.34 if strong_close else 0.28
    wick_room = 0.24 if strong_close else 0.18
    return (
        bar["close"] >= bar["open"]
        and candle["close_pos"] >= 0.58
        and candle["body_ratio"] >= 0.20
        and bullish_body_ratio >= 0.14
        and bar_range >= max(bar["close"] * atr_ratio * 0.36, body_high * 0.0007)
        and upper_wick_ratio <= wick_cap
        and upper_wick <= max(body_high * atr_ratio * (0.20 if strong_close else 0.16), bar_range * wick_room)
        and upper_wick <= bullish_body + max(body_high * atr_ratio * 0.10, bar_range * 0.08)
    )


def _recent_long_breakout_prepared(data, start, end, reference_high, atr_ratio):
    score = 0
    probe_start = max(start, end - 2)
    close_gap = max(atr_ratio * 0.16, 0.0014)
    high_gap = max(atr_ratio * 0.08, 0.0009)
    for i in range(probe_start, end + 1):
        bar = data[i]
        candle = _candle_metrics(bar)
        anchored_high = _anchored_long_breakout_high(bar, atr_ratio)
        if bar["close"] >= reference_high * (1.0 - close_gap) and candle["close_pos"] >= 0.54:
            score += 1
        if bar["high"] >= reference_high * (1.0 - high_gap):
            score += 1
        if anchored_high >= reference_high * (1.0 - high_gap) and candle["body_ratio"] >= 0.18:
            score += 1
        if _effective_long_breakout_reference_bar(bar, atr_ratio):
            score += 1
    return score >= 4


def _long_breakout_reference_high(data, start, end, atr_ratio):
    reference_high = _anchored_long_breakout_high(data[start], atr_ratio)
    reference_idx = start
    recent_effective_high = 0.0
    recent_effective_idx = -1
    recent_prepared_high = 0.0
    recent_prepared_idx = -1
    window = end - start + 1
    recent_span = max(8, window // 2)
    recent_start = max(start, end - recent_span + 1)
    for i in range(start, end + 1):
        bar = data[i]
        anchored_high = _anchored_long_breakout_high(bar, atr_ratio)
        if anchored_high > reference_high:
            reference_high = anchored_high
            reference_idx = i
        if (
            i >= recent_start
            and _effective_long_breakout_reference_bar(bar, atr_ratio)
            and anchored_high >= recent_effective_high
        ):
            recent_effective_high = anchored_high
            recent_effective_idx = i
        if (
            i >= recent_start
            and bar["close"] >= bar["open"]
            and _candle_metrics(bar)["close_pos"] >= 0.54
            and _candle_metrics(bar)["body_ratio"] >= 0.16
            and anchored_high >= recent_prepared_high
        ):
            recent_prepared_high = anchored_high
            recent_prepared_idx = i

    candidate_high = recent_effective_high
    candidate_idx = recent_effective_idx
    if candidate_idx < 0 and recent_prepared_idx >= 0:
        candidate_high = recent_prepared_high
        candidate_idx = recent_prepared_idx

    if candidate_idx < 0:
        return reference_high
    if reference_idx >= recent_start:
        return max(reference_high, candidate_high)

    stale_gap_pct = (reference_high - candidate_high) / max(candidate_high, 1e-9)
    prepared_breakout = _recent_long_breakout_prepared(data, recent_start, end, candidate_high, atr_ratio)
    recent_close_acceptance = data[end]["close"] >= candidate_high * (1.0 - max(atr_ratio * 0.14, 0.0011))
    recent_high_acceptance = data[end]["high"] >= candidate_high * (1.0 - max(atr_ratio * 0.06, 0.0007))
    if prepared_breakout and stale_gap_pct <= max(atr_ratio * 1.48, 0.0058):
        return candidate_high
    if recent_close_acceptance and stale_gap_pct <= max(atr_ratio * 1.18, 0.0042):
        return candidate_high
    if recent_high_acceptance and stale_gap_pct <= max(atr_ratio * 0.92, 0.0032):
        return candidate_high
    return reference_high


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


def _position_side(position):
    signal = str((position or {}).get("entry_signal", "")).strip()
    if signal.startswith("short_"):
        return "short"
    if signal.startswith("long_"):
        return "long"
    return ""


def _long_pullback_hold_active(positions):
    if not positions:
        return False
    lead_position = positions[0]
    if _position_side(lead_position) != "long":
        return False
    path_tag = str(
        lead_position.get("entry_path_tag")
        or lead_position.get("entry_path_key")
        or lead_position.get("entry_signal")
        or ""
    ).strip()
    normalized_tag = ENTRY_PATH_TAGS.get(path_tag, path_tag)
    return normalized_tag in LONG_PULLBACK_HOLD_TAGS


def _count_positions_by_side(positions, side):
    if not positions:
        return 0
    return sum(1 for position in positions if _position_side(position) == side)


def _long_entry_addition_available(positions):
    long_positions = _count_positions_by_side(positions, "long")
    if long_positions <= 0:
        return True
    return long_positions < LONG_PYRAMID_MAX_ADDS + 1


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


def _flow_entry_ok(market_state, hourly, fourh, params, side=None, strong=False):
    entry_side = side if side in {"long", "short"} else "short"
    if entry_side == "long" and not strong:
        metrics = _flow_signal_metrics(market_state, hourly, fourh, params, entry_side)
        if (
            metrics["score"] >= max(params["breakout_flow_score_min"] - 1, 3)
            and metrics["participation_bias"] >= 0.017
            and metrics["directional_bias"] >= 0.0255
        ):
            return True
    return _flow_confirmation_ok(market_state, hourly, fourh, params, entry_side, strong=strong)


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
    atr_ratio = market_state["atr_ratio"]
    breakout_high = _long_breakout_reference_high(
        data,
        idx - params["breakout_lookback"],
        idx - 1,
        atr_ratio,
    )
    prev_breakout_high = _long_breakout_reference_high(
        data,
        idx - params["breakout_lookback"] - 1,
        idx - 2,
        atr_ratio,
    )
    breakout_reference_stale_gap_pct = max(
        (
            _window_max(data, idx - params["breakout_lookback"], idx - 1, "high")
            - breakout_high
        )
        / max(breakout_high, 1e-9),
        0.0,
    )
    breakdown_low = _window_min(data, idx - params["breakdown_lookback"], idx - 1, "low")
    prev_breakdown_low = _window_min(data, idx - params["breakdown_lookback"] - 1, idx - 2, "low")
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
        "breakout_reference_stale_gap_pct": breakout_reference_stale_gap_pct,
        "breakdown_low": breakdown_low,
        "prev_breakdown_low": prev_breakdown_low,
        "atr_ratio": atr_ratio,
        "breakout_distance_pct": (current["close"] - breakout_high) / max(breakout_high, 1e-9),
        "breakout_high_penetration_pct": max((current["high"] - breakout_high) / max(breakout_high, 1e-9), 0.0),
        "prev_breakout_distance_pct": max((prev["close"] - breakout_high) / max(breakout_high, 1e-9), 0.0),
        "prev_breakout_reference_distance_pct": max((prev["close"] - prev_breakout_high) / max(prev_breakout_high, 1e-9), 0.0),
        "prev_breakout_high_penetration_pct": max((prev["high"] - prev_breakout_high) / max(prev_breakout_high, 1e-9), 0.0),
        "breakout_reclaim_gap_pct": max((breakout_high - current["close"]) / max(breakout_high, 1e-9), 0.0),
        "breakout_reclaim_high_gap_pct": max((breakout_high - current["high"]) / max(breakout_high, 1e-9), 0.0),
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
    fourh_bull_base_core = (
        fourh["close"] > fourh["ema_slow"]
        and fourh["trend_spread_pct"] > 0.0
        and fourh["ema_slow_slope_pct"] >= 0.0
    )
    fourh_fast_support = fourh["close"] >= fourh["ema_fast"]
    fourh_macd_support = (
        fourh["macd_line"] > fourh["signal_line"]
        and fourh["adx"] >= max(params["fourh_adx_min"] - 1.0, 11.5)
    )
    fourh_bull_turn_core = (
        fourh["close"] > fourh["ema_slow"]
        and fourh["trend_spread_pct"] > 0.0
        and fourh["ema_slow_slope_pct"] >= 0.0
    )
    fourh_turn_fast_support = (
        fourh["close"] >= fourh["ema_fast"]
        and fourh["ema_fast"] >= fourh["ema_slow"]
    )
    fourh_turn_macd_support = (
        fourh["macd_line"] > fourh["signal_line"]
        and fourh["adx"] >= max(params["fourh_adx_min"] - 0.8, 11.8)
    )
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
            fourh_bull_base_core
            and (fourh_fast_support or fourh_macd_support)
        ),
        "fourh_bull_turn": (
            fourh_bull_turn_core
            and (fourh_turn_fast_support or fourh_turn_macd_support)
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


def _sideways_release_flags(market_state, positions=None):
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
    intraday_directional_spread = intraday["spread_pct"]
    intraday_directional_slope = intraday["slope_pct"]
    adx_soft = hourly["adx"] <= SIDEWAYS_MAX_HOURLY_ADX and fourh["adx"] <= SIDEWAYS_MAX_FOURH_ADX
    aligned_trend = hourly["trend_spread_pct"] * fourh["trend_spread_pct"] > 0.0
    long_pullback_hold = _long_pullback_hold_active(positions)
    relax = SIDEWAYS_RELEASE_RELAX

    def _safe_float(payload, key, default):
        if payload is None:
            return default
        try:
            value = float(payload.get(key, default))
        except (TypeError, ValueError, AttributeError):
            return default
        return value

    intraday_trade_ratio = _safe_float(market_state, "trade_count_ratio", 1.0)
    hourly_trade_ratio = _safe_float(hourly, "trade_count_ratio", 1.0)
    intraday_flow_imbalance = _safe_float(market_state, "flow_imbalance", 0.0)
    hourly_flow_imbalance = _safe_float(hourly, "flow_imbalance", 0.0)
    long_flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, PARAMS, "long")
    mild_long_pullback = (
        long_pullback_hold
        and hourly["trend_spread_pct"] > 0.0
        and fourh["trend_spread_pct"] > 0.0
        and intraday_directional_spread >= -max(atr_ratio * 0.08, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.22)
        and intraday_directional_slope >= -atr_ratio * 0.040
        and hourly["trend_spread_pct"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.84, atr_ratio * 0.46)
        and fourh["trend_spread_pct"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.46, atr_ratio * 0.38)
        and hourly["ema_slow_slope_pct"] >= -atr_ratio * 0.016
        and fourh["ema_slow_slope_pct"] >= -atr_ratio * 0.008
        and intraday_trade_ratio >= max(PARAMS["breakout_trade_count_ratio_min"] - 0.20, 0.84)
        and hourly_trade_ratio >= max(PARAMS["hourly_trade_count_ratio_min"] - 0.08, 0.68)
        and intraday_flow_imbalance >= -0.07
        and hourly_flow_imbalance >= -0.04
        and long_flow_metrics["directional_bias"] >= 0.0
    )
    convexity_release = (
        aligned_trend
        and intraday_spread >= atr_ratio * 0.26
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.10 * relax["spread_floor_mult"], atr_ratio * 0.66 * relax["spread_floor_mult"])
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.50 * relax["spread_floor_mult"], atr_ratio * 0.48 * relax["spread_floor_mult"])
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 0.98 * relax["atr_ceiling_mult"])
        and hourly_slope >= max(hourly_spread * 0.10 * relax["slope_floor_mult"], atr_ratio * 0.082 * relax["slope_floor_mult"])
        and fourh_slope >= max(fourh_spread * 0.18 * relax["slope_floor_mult"], atr_ratio * 0.028 * relax["slope_floor_mult"])
        and market_state["adx"] >= 13.5
        and hourly["adx"] >= 18.0
        and fourh["adx"] >= 12.0
    )
    trend_awakening = (
        aligned_trend
        and intraday_spread >= atr_ratio * 0.24
        and intraday_spread <= max(hourly_spread * 1.92, atr_ratio * 0.98 * relax["atr_ceiling_mult"])
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.92 * relax["spread_floor_mult"], atr_ratio * 0.54 * relax["spread_floor_mult"])
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.42 * relax["spread_floor_mult"], atr_ratio * 0.40 * relax["spread_floor_mult"])
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.08, atr_ratio * 0.90 * relax["atr_ceiling_mult"])
        and hourly_slope >= max(hourly_spread * 0.12 * relax["slope_floor_mult"], atr_ratio * 0.080 * relax["slope_floor_mult"])
        and fourh_slope >= max(fourh_spread * 0.20 * relax["slope_floor_mult"], atr_ratio * 0.028 * relax["slope_floor_mult"])
        and market_state["adx"] >= 13.0
        and hourly["adx"] >= 17.0
        and fourh["adx"] >= 11.8
        and intraday_chop < SIDEWAYS_HARD_INTRADAY_CHOP_MIN + relax["chop_buffer"]
        and hourly_chop < SIDEWAYS_HARD_HOURLY_CHOP_MIN + relax["chop_buffer"]
    )
    fresh_directional_expansion = (
        aligned_trend
        and intraday_spread >= atr_ratio * 0.22
        and intraday_spread <= max(hourly_spread * 1.35, atr_ratio * 0.92 * relax["atr_ceiling_mult"])
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.02 * relax["spread_floor_mult"], atr_ratio * 0.60 * relax["spread_floor_mult"])
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.56 * relax["spread_floor_mult"], atr_ratio * 0.52 * relax["spread_floor_mult"])
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.52, atr_ratio * 1.18 * relax["atr_ceiling_mult"])
        and hourly_slope >= max(hourly_spread * 0.13 * relax["slope_floor_mult"], atr_ratio * 0.084 * relax["slope_floor_mult"])
        and fourh_slope >= max(fourh_spread * 0.22 * relax["slope_floor_mult"], atr_ratio * 0.032 * relax["slope_floor_mult"])
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
            atr_ratio < SIDEWAYS_MIN_ATR_RATIO * relax["hard_sideways_atr_mult"]
            and hourly_spread < SIDEWAYS_MIN_HOURLY_SPREAD_PCT * relax["hard_sideways_spread_mult"]
            and fourh_spread < SIDEWAYS_MIN_FOURH_SPREAD_PCT * relax["hard_sideways_spread_mult"]
            and not (convexity_release or trend_awakening or fresh_directional_expansion)
        )
        or (
            hourly_spread < SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.92
            and fourh_spread < SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.92
            and hourly_slope < atr_ratio * 0.06
            and fourh_slope < atr_ratio * 0.03
        )
    )
    extreme_compression = (
        intraday_chop >= SIDEWAYS_HARD_INTRADAY_CHOP_MIN + 2.0
        and hourly_chop >= SIDEWAYS_HARD_HOURLY_CHOP_MIN + 2.0
        and atr_ratio < SIDEWAYS_MIN_ATR_RATIO * relax["extreme_compression_atr_mult"]
        and hourly_spread < SIDEWAYS_MIN_HOURLY_SPREAD_PCT * relax["extreme_compression_spread_mult"]
        and fourh_spread < SIDEWAYS_MIN_FOURH_SPREAD_PCT * relax["extreme_compression_spread_mult"]
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
        "mild_long_pullback": mild_long_pullback,
        "convexity_release": convexity_release,
        "trend_awakening": trend_awakening,
        "fresh_directional_expansion": fresh_directional_expansion,
        "exhausted_drift": exhausted_drift,
        "hard_sideways": hard_sideways,
        "extreme_compression": extreme_compression,
    }


def _is_sideways_regime(market_state, positions=None):
    intraday = _intraday_trend_metrics(market_state)
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    release_flags = _sideways_release_flags(market_state, positions=positions)
    intraday_spread = release_flags["intraday_spread"]
    hourly_spread = release_flags["hourly_spread"]
    fourh_spread = release_flags["fourh_spread"]
    hourly_slope = release_flags["hourly_slope"]
    fourh_slope = release_flags["fourh_slope"]
    intraday_chop = release_flags["intraday_chop"]
    hourly_chop = release_flags["hourly_chop"]
    atr_ratio = release_flags["atr_ratio"]
    adx_soft = release_flags["adx_soft"]
    mild_long_pullback = release_flags["mild_long_pullback"]
    convexity_release = release_flags["convexity_release"]
    trend_awakening = release_flags["trend_awakening"]
    fresh_directional_expansion = release_flags["fresh_directional_expansion"]
    exhausted_drift = release_flags["exhausted_drift"]
    hard_sideways = release_flags["hard_sideways"]
    extreme_compression = release_flags["extreme_compression"]
    if hard_sideways and not (mild_long_pullback and not extreme_compression):
        return True
    if exhausted_drift and not convexity_release and not mild_long_pullback:
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
    if weak_trend and (intraday_chop >= SIDEWAYS_INTRADAY_CHOP_MIN - 1.0 or adx_soft) and not mild_long_pullback:
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
    if bull_front_run and not mild_long_pullback:
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
    if mild_long_pullback and not extreme_compression:
        signals -= 1
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
    fourh_expansion_floor = (
        metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.58, atr_ratio * 0.56)
        and metrics["fourh_slope"] >= atr_ratio * 0.028
        and fourh["adx"] >= max(p["fourh_adx_min"] - 0.2, 12.8)
    )
    overextended_without_fourh = (
        hourly_fast_extension >= max(atr_ratio * 0.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.00)
        and hourly_anchor_extension >= max(atr_ratio * 1.32, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.10)
        and not fourh_expansion_floor
    )
    if overextended_without_fourh:
        return False
    return confirms >= 4


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
    macd_pressure_ok = (
        market_state["histogram"] < 0.0
        and market_state["macd_line"] < market_state["signal_line"]
        and hourly["macd_line"] < hourly["signal_line"]
        and fourh["macd_line"] < fourh["signal_line"]
    )
    overdiscounted_without_fourh = (
        hourly_fast_discount >= max(atr_ratio * 0.96, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.18)
        and hourly_anchor_discount >= max(atr_ratio * 1.52, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.62)
        and not fourh_participation_ok
    )
    if overdiscounted_without_fourh and not fresh_pressure_ok:
        return False
    return (
        confirms >= 4
        and macd_pressure_ok
        and (fourh_participation_ok or fresh_pressure_ok or hourly["adx"] >= p["hourly_adx_min"] + 2.0)
    )


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
    quality_ready = _trend_quality_long(market_state)

    confirms = 0
    if metrics["intraday_spread"] >= atr_ratio * 0.22:
        confirms += 1
    if metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.08, atr_ratio * 0.66):
        confirms += 1
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.58, atr_ratio * 0.56):
        confirms += 1
    if metrics["hourly_slope"] >= atr_ratio * 0.052 and metrics["fourh_slope"] >= atr_ratio * 0.018:
        confirms += 1
    if breakout_distance_pct >= -atr_ratio * 0.01:
        confirms += 1

    mild_continuation = (
        quality_ready
        and breakout_distance_pct >= -atr_ratio * 0.01
        and metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.02, atr_ratio * 0.62)
        and metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.54, atr_ratio * 0.50)
        and (
            metrics["hourly_slope"] >= atr_ratio * 0.044
            or metrics["fourh_slope"] >= atr_ratio * 0.020
            or metrics["intraday_spread"] >= atr_ratio * 0.26
        )
    )
    pullback_recovery = (
        quality_ready
        and breakout_distance_pct >= -atr_ratio * 0.01
        and breakout_distance_pct <= atr_ratio * 0.12
        and metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.92, atr_ratio * 0.54)
        and metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.48, atr_ratio * 0.46)
        and metrics["hourly_slope"] >= atr_ratio * 0.022
        and metrics["fourh_slope"] >= 0.0
    )

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
    if mild_continuation or pullback_recovery:
        return True
    return confirms >= 4


def _trend_followthrough_exit_long(market_state, trigger_price, current_close):
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "long")
    atr_ratio = metrics["atr_ratio"]
    breakout_distance_pct = (current_close - trigger_price) / max(trigger_price, 1e-9)
    hourly_fast_extension = (current_close - hourly["ema_fast"]) / max(current_close, 1e-9)
    hourly_anchor_extension = (current_close - hourly["ema_anchor"]) / max(current_close, 1e-9)

    confirms = 0
    if metrics["intraday_spread"] >= atr_ratio * 0.18:
        confirms += 1
    if metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.88, atr_ratio * 0.50):
        confirms += 1
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.44, atr_ratio * 0.42):
        confirms += 1
    if metrics["hourly_slope"] >= atr_ratio * 0.014:
        confirms += 1
    if breakout_distance_pct >= -atr_ratio * 0.04:
        confirms += 1

    reversal_pressure = (
        market_state["histogram"] <= 0.0
        and market_state["macd_line"] <= market_state["signal_line"]
        and hourly["close"] <= hourly["ema_fast"] * (1.0 + atr_ratio * 0.10)
        and hourly["ema_fast"] <= hourly["ema_anchor"] * (1.0 + atr_ratio * 0.05)
        and (
            breakout_distance_pct <= atr_ratio * 0.04
            or hourly_fast_extension <= atr_ratio * 0.06
            or metrics["hourly_slope"] <= 0.0
        )
    )
    support_lost = (
        current_close <= hourly["ema_fast"] * (1.0 + atr_ratio * 0.04)
        and (
            hourly_fast_extension <= atr_ratio * 0.03
            or hourly_anchor_extension <= atr_ratio * 0.10
            or breakout_distance_pct <= 0.0
        )
        and metrics["hourly_slope"] <= atr_ratio * 0.010
        and metrics["intraday_spread"] <= atr_ratio * 0.24
    )
    deeper_reversal = (
        breakout_distance_pct <= -atr_ratio * 0.02
        and hourly_fast_extension <= atr_ratio * 0.02
        and (
            metrics["hourly_slope"] <= 0.0
            or metrics["fourh_slope"] <= atr_ratio * 0.010
            or market_state["histogram"] <= -0.01
        )
    )
    if reversal_pressure or support_lost or deeper_reversal:
        return False
    return confirms >= 3


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


def _active_long_exit_followthrough_ok(positions, market_state, current_close):
    if not positions:
        return True
    lead_position = positions[0]
    trigger_price = float(lead_position.get("entry_price", current_close))
    return _trend_followthrough_exit_long(market_state, trigger_price, current_close)


def _long_durable_hold_active(market_state):
    fourh = market_state["four_hour"]
    if fourh is None:
        return False
    return (
        fourh["adx"] >= 22.0
        and fourh["trend_spread_pct"] > 0.0
        and fourh["close"] > fourh["ema_slow"]
    )


def long_outer_context_ok(context, market_state, params):
    long_state = _build_long_trend_state(context, market_state, params)
    context["long_outer_lane"] = ""
    reclaim_ready = (
        context["current"]["close"] > market_state["ema_fast"] > market_state["ema_slow"]
        and market_state["macd_line"] > market_state["signal_line"]
        and market_state["adx"] >= max(params["intraday_adx_min"] - 1.6, 11.5)
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
    context["long_reclaim_ready"] = reclaim_ready
    intraday_support_ready = long_state["intraday_bull"] or reclaim_ready
    hourly_support_ready = long_state["hourly_bull"]
    fourh_context_ready = long_state["fourh_bull_base"] or (
        context["fourh"]["close"] > context["fourh"]["ema_slow"]
        and context["fourh"]["ema_slow_slope_pct"] >= 0.0
        and context["fourh"]["trend_spread_pct"] >= -max(
            SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.42,
            context["atr_ratio"] * 0.18,
        )
    )
    base_ownership_ready = intraday_support_ready and (
        hourly_support_ready or fourh_context_ready
    )
    hourly_repair_ready = (
        context["hourly"]["close"] > context["hourly"]["ema_slow"]
        and context["hourly"]["ema_fast"] >= context["hourly"]["ema_slow"]
        and context["hourly"]["macd_line"] > context["hourly"]["signal_line"]
        and context["hourly"]["trend_spread_pct"] >= max(
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.46,
            context["atr_ratio"] * 0.26,
        )
        and context["hourly"]["ema_slow_slope_pct"] >= context["atr_ratio"] * 0.014
        and context["hourly"]["adx"] >= max(params["hourly_adx_min"] - 5.0, 14.0)
    )
    hourly_turn_repair_ready = (
        context["hourly"]["close"] > context["hourly"]["ema_slow"]
        and context["hourly"]["macd_line"] > context["hourly"]["signal_line"]
        and (
            context["hourly"]["ema_fast"] >= context["hourly"]["ema_slow"]
            or context["hourly"]["close"] >= context["hourly"]["ema_fast"]
        )
        and context["hourly"]["trend_spread_pct"] >= max(
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 0.40,
            context["atr_ratio"] * 0.22,
        )
        and context["hourly"]["ema_slow_slope_pct"] >= 0.0
        and context["hourly"]["adx"] >= max(params["hourly_adx_min"] - 6.0, 13.5)
    )
    late_mature_guard = (
        context["hourly_fast_extension_pct"] >= max(
            context["atr_ratio"] * 0.82,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.88,
        )
        and context["hourly_anchor_extension_pct"] >= max(
            context["atr_ratio"] * 1.24,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.96,
        )
        and context["breakout_distance_pct"] >= max(
            context["prev_breakout_distance_pct"] - context["atr_ratio"] * 0.01,
            context["atr_ratio"] * 0.10,
        )
        and (
            context["fourh"]["trend_spread_pct"] < max(
                context["hourly"]["trend_spread_pct"] * 0.90,
                context["atr_ratio"] * 0.98,
            )
            or context["fourh"]["ema_slow_slope_pct"] < max(
                context["fourh"]["trend_spread_pct"] * 0.10,
                context["atr_ratio"] * 0.038,
            )
        )
    )
    context["long_late_mature_guard"] = late_mature_guard
    mature_trend_lane = (
        base_ownership_ready
        and (hourly_support_ready or hourly_repair_ready)
        and not late_mature_guard
    )
    early_turn_outer_lane = (
        reclaim_ready
        and long_state["fourh_bull_turn"]
        and hourly_turn_repair_ready
        and not long_state["hourly_bull"]
        and not late_mature_guard
        and context["hourly_fast_extension_pct"] <= max(
            context["atr_ratio"] * 0.76,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.68,
        )
        and context["hourly_anchor_extension_pct"] <= max(
            context["atr_ratio"] * 1.06,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.56,
        )
    )
    if mature_trend_lane:
        context["long_outer_lane"] = "trend"
    elif early_turn_outer_lane:
        context["long_outer_lane"] = "early_turn"
    return mature_trend_lane or early_turn_outer_lane


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
        and market_state["adx"] >= max(params["breakout_adx_min"] - 2.3, params["intraday_adx_min"] + 0.3)
        and params["breakout_rsi_min"] <= market_state["rsi"] <= min(params["breakout_rsi_max"], 69.0)
        and market_state["histogram"] >= max(params["breakout_hist_min"], 3.5)
        and _flow_entry_ok(market_state, hourly, fourh, params, "long", strong=False)
        and context["hourly_fast_extension_pct"] <= max(atr_ratio * 0.94, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.08)
        and context["hourly_anchor_extension_pct"] <= max(atr_ratio * 1.36, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.18)
        and fourh["trend_spread_pct"] >= max(hourly["trend_spread_pct"] * 0.38, atr_ratio * 0.48)
        and fourh["ema_slow_slope_pct"] >= atr_ratio * 0.024
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
        and current["volume"] >= max(context["prev_volume"] * 0.92, context["recent_volume_avg"] * 0.90)
        and context["volume_ratio"] >= max(params["breakout_volume_ratio_min"] - 0.08, 1.02)
        and _flow_entry_ok(market_state, hourly, fourh, params, "long", strong=False)
        and context["hourly_fast_extension_pct"] <= max(atr_ratio * 0.96, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.08)
        and context["hourly_anchor_extension_pct"] <= max(atr_ratio * 1.36, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.12)
        and fourh["trend_spread_pct"] >= max(hourly["trend_spread_pct"] * 0.36, atr_ratio * 0.46)
        and fourh["ema_slow_slope_pct"] >= atr_ratio * 0.022
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


def _long_strong_trend_bypass(context, market_state, params):
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    metrics = _directional_trend_metrics(market_state, "long")
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "long")

    trend_confirms = 0
    if market_state["adx"] >= max(params["breakout_adx_min"] - 0.5, params["intraday_adx_min"] + 1.0):
        trend_confirms += 1
    if metrics["intraday_spread"] >= atr_ratio * 0.30:
        trend_confirms += 1
    if metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.28, atr_ratio * 0.82):
        trend_confirms += 1
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.82, atr_ratio * 0.78):
        trend_confirms += 1
    if metrics["hourly_slope"] >= atr_ratio * 0.078:
        trend_confirms += 1
    if metrics["fourh_slope"] >= atr_ratio * 0.032:
        trend_confirms += 1

    trend_score = trend_confirms / 6.0
    ema_bull_stack = (
        context["current"]["close"] > market_state["ema_fast"] > market_state["ema_slow"]
        and hourly["close"] > hourly["ema_fast"] > hourly["ema_slow"]
        and fourh["close"] > fourh["ema_fast"] > fourh["ema_slow"]
    )
    macd_above_zero = (
        market_state["macd_line"] > 0.0
        and market_state["signal_line"] > 0.0
        and hourly["macd_line"] > 0.0
        and hourly["signal_line"] > 0.0
    )
    return (
        _flow_confirmation_ok(market_state, hourly, fourh, params, "long", strong=True)
        and flow_metrics["directional_bias"] >= 0.04
        and trend_score > 0.75
        and _trend_quality_long(market_state)
        and ema_bull_stack
        and macd_above_zero
    )


def long_final_veto_clear(
    context,
    market_state,
    params,
    breakout_ok,
    pullback_ok,
    reaccel_ok,
    relay_ok=False,
    strong_trend_bypass=False,
):
    return True


def short_outer_context_ok(context, market_state, params):
    short_state = _build_short_trend_state(context, market_state, params)
    context["short_outer_lane"] = ""
    strict_trend_lane = (
        short_state["intraday_bear"]
        and short_state["hourly_bear"]
        and short_state["fourh_bear"]
        and _trend_quality_short(market_state)
    )
    extreme_bull_trend = (
        context["current"]["close"] > market_state["ema_fast"] > market_state["ema_slow"]
        and market_state["macd_line"] > market_state["signal_line"]
        and context["hourly"]["close"] > context["hourly"]["ema_fast"] > context["hourly"]["ema_slow"]
        and context["hourly"]["close"] > context["hourly"]["ema_anchor"]
        and context["hourly"]["macd_line"] > context["hourly"]["signal_line"]
        and context["hourly"]["trend_spread_pct"] > 0.0
        and context["hourly"]["ema_slow_slope_pct"] > 0.0
        and context["fourh"]["close"] > context["fourh"]["ema_fast"] > context["fourh"]["ema_slow"]
        and context["fourh"]["macd_line"] > context["fourh"]["signal_line"]
        and context["fourh"]["trend_spread_pct"] > 0.0
        and context["fourh"]["ema_slow_slope_pct"] > 0.0
    )
    breakdown_release_lane = (
        not strict_trend_lane
        and not extreme_bull_trend
        and context["current"]["close"] <= context["breakdown_low"] * (1.0 - params["breakdown_buffer_pct"])
        and context["breakdown_distance_pct"] >= context["atr_ratio"] * 0.06
        and context["current_candle"]["close_pos"] <= min(params["breakdown_close_pos_max"] + 0.03, 0.37)
        and context["current_candle"]["body_ratio"] >= max(params["breakdown_body_ratio_min"] - 0.05, 0.34)
    )
    if strict_trend_lane:
        context["short_outer_lane"] = "trend"
    elif breakdown_release_lane:
        context["short_outer_lane"] = "breakdown_release"
    return strict_trend_lane or breakdown_release_lane


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


def _short_breakdown_path_ok(context, market_state, params, require_breakdown_gate=True):
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "short")
    flow_ready = _flow_entry_ok(market_state, hourly, fourh, params, "short", strong=False)
    direct_breakdown = (
        context["prev_breakdown_distance_pct"] <= atr_ratio * 0.04
        and context["current_candle"]["close_pos"] <= min(params["breakdown_close_pos_max"] + 0.01, 0.30)
        and context["current_candle"]["body_ratio"] >= max(params["breakdown_body_ratio_min"] - 0.01, 0.40)
        and context["current"]["volume"] >= max(context["prev_volume"] * 0.98, context["recent_volume_avg"] * 1.00)
        and flow_metrics["score"] >= 5
        and flow_metrics["directional_bias"] >= 0.04
    )
    acceptance_breakdown = (
        context["breakdown_distance_pct"] <= atr_ratio * 0.22
        and context["prev_breakdown_distance_pct"] <= atr_ratio * 0.04
        and context["current"]["low"] < context["prev"]["low"]
        and context["current_range"] >= max(context["prev_range"] * 0.96, context["recent_range_avg"] * 0.92)
        and context["current_candle"]["close_pos"] <= min(params["breakdown_close_pos_max"] + 0.03, 0.32)
        and context["current_candle"]["body_ratio"] >= max(params["breakdown_body_ratio_min"] - 0.03, 0.36)
        and context["current"]["volume"] >= max(context["prev_volume"] * 0.95, context["recent_volume_avg"] * 0.98)
        and flow_metrics["score"] >= 5
        and (
            flow_metrics["directional_bias"] >= 0.04
            or context["volume_ratio"] >= max(params["breakdown_volume_ratio_min"], 1.10)
        )
    )
    return (
        (not require_breakdown_gate or breakdown_ready(context, market_state, params))
        and context["breakdown_distance_pct"] <= atr_ratio * 0.30
        and context["breakdown_low_penetration_pct"] >= max(atr_ratio * 0.14, context["breakdown_distance_pct"] * 0.96)
        and flow_ready
        and (direct_breakdown or acceptance_breakdown)
    )


def short_breakdown_ok(context, market_state, params):
    return _short_breakdown_path_ok(context, market_state, params, require_breakdown_gate=True)


def _short_bounce_fail_path_ok(context, market_state, params, require_breakdown_gate=True):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "short")
    prior_breakdown_hold = context["prev_breakdown_distance_pct"] >= atr_ratio * 0.05 or prev["low"] <= context["breakdown_low"] * (1.0 + atr_ratio * 0.04)
    return (
        (not require_breakdown_gate or breakdown_ready(context, market_state, params))
        and prior_breakdown_hold
        and context["breakdown_distance_pct"] >= atr_ratio * 0.07
        and context["breakdown_distance_pct"] <= atr_ratio * 0.18
        and prev["high"] >= context["breakdown_low"] * (1.0 + atr_ratio * 0.03)
        and prev["high"] <= hourly["ema_fast"] * (1.0 + atr_ratio * 0.36)
        and prev["close"] >= prev["low"] + context["prev_range"] * 0.36
        and prev["close"] >= context["breakdown_low"] * (1.0 + atr_ratio * 0.01)
        and current["close"] < prev["low"]
        and context["current_range"] >= max(context["prev_range"] * 1.02, context["recent_range_avg"] * 0.94)
        and context["current_candle"]["body_ratio"] >= max(context["prev_candle"]["body_ratio"] * 1.02, 0.40)
        and current["volume"] >= max(context["prev_volume"] * 1.02, context["recent_volume_avg"] * 1.04)
        and flow_metrics["score"] >= 5
        and flow_metrics["directional_bias"] >= 0.04
        and _flow_entry_ok(market_state, hourly, fourh, params, "short", strong=False)
        and fourh["trend_spread_pct"] <= max(hourly["trend_spread_pct"] * 0.52, -atr_ratio * 0.58)
    )


def short_bounce_fail_ok(context, market_state, params):
    return _short_bounce_fail_path_ok(context, market_state, params, require_breakdown_gate=True)


def short_trend_reaccel_ok(context, market_state, params):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    atr_ratio = context["atr_ratio"]
    flow_metrics = _flow_signal_metrics(market_state, hourly, fourh, params, "short")
    prior_breakdown_hold = context["prev_breakdown_distance_pct"] >= atr_ratio * 0.05 or prev["close"] <= context["breakdown_low"] * (1.0 + atr_ratio * 0.04)
    return (
        current["close"] <= context["breakdown_low"] * (1.0 - params["breakdown_buffer_pct"] * 0.3)
        and context["breakdown_distance_pct"] >= atr_ratio * 0.06
        and context["breakdown_distance_pct"] <= atr_ratio * 0.26
        and prior_breakdown_hold
        and current["low"] < prev["low"]
        and current["close"] < prev["close"]
        and context["current_range"] >= max(context["prev_range"] * 0.98, context["recent_range_avg"] * 0.94)
        and current["volume"] >= max(context["prev_volume"] * 0.94, context["recent_volume_avg"] * 0.92)
        and market_state["adx"] >= max(params["breakdown_adx_min"] - 1.2, params["intraday_adx_min"] + 0.6)
        and hourly["trend_spread_pct"] <= -max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.26, atr_ratio * 0.82)
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
    if stale_breakdown_risk and bounce_fail_ok and not reaccel_ok:
        return False
    if stale_breakdown_risk and not (breakdown_ok or reaccel_ok):
        return False
    if weak_flow_dump:
        return False
    return _trend_followthrough_short(market_state, context["breakdown_low"], context["current"]["close"])


def _long_entry_signal(data, idx, positions, market_state):
    signal = strategy(data, idx, positions, market_state)
    return signal if signal in {"long_breakout", "long_pullback"} else None


def _strategy_entry_context(data, idx, positions, market_state, params):
    if idx < params["min_history"]:
        return None
    context = _build_signal_context(data, idx, market_state, params)
    if context is None:
        return None
    if _is_sideways_regime(market_state, positions=positions):
        return None
    return context


def _short_entry_path_key(context, market_state, params, require_breakdown_gate=False):
    if _short_breakdown_path_ok(context, market_state, params, require_breakdown_gate=require_breakdown_gate):
        return "short_breakdown"
    if _short_bounce_fail_path_ok(context, market_state, params, require_breakdown_gate=require_breakdown_gate):
        return "short_bounce_fail"
    if short_trend_reaccel_ok(context, market_state, params):
        return "short_reaccel"
    return ""


def _short_entry_signal(data, idx, positions, market_state):
    p = PARAMS
    context = _strategy_entry_context(data, idx, positions, market_state, p)
    if context is None:
        return None

    path_key = _short_entry_path_key(context, market_state, p, require_breakdown_gate=False)
    if not path_key:
        return None
    return normalize_entry_signal(path_key, fallback_side="short") or None


def strategy_decision(data, idx, positions, market_state):
    p = PARAMS
    context = _strategy_entry_context(data, idx, positions, market_state, p)
    if context is None:
        return None

    _record_funnel_pass("long", "sideways_pass")
    _record_funnel_pass("short", "sideways_pass")

    if positions and _position_side(positions[0]) == "long":
        forced_short_path = _short_entry_path_key(context, market_state, p, require_breakdown_gate=False)
        long_exit_followthrough_ok = _active_long_exit_followthrough_ok(
            positions,
            market_state,
            context["current"]["close"],
        )
        if (
            forced_short_path
            and not long_exit_followthrough_ok
            and not _long_durable_hold_active(market_state)
        ):
            return {
                "entry_signal": "short_breakdown",
                "entry_side": "short",
                "entry_path_key": forced_short_path,
                "entry_path_tag": ENTRY_PATH_TAGS.get(forced_short_path, forced_short_path),
            }

    if long_outer_context_ok(context, market_state, p):
        _record_funnel_pass("long", "outer_context_pass")
        long_breakout_path = long_breakout_ok(context, market_state, p)
        long_pullback_path = long_pullback_ok(context, market_state, p)
        long_reaccel_path = long_trend_reaccel_ok(context, market_state, p)
        long_ownership_relay = (
            not long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path)
            and context["current"]["high"] > context["breakout_high"]
            and context["current"]["close"] >= context["breakout_high"] * (1.0 + p["breakout_buffer_pct"] * 0.4)
            and context["breakout_distance_pct"] >= context["atr_ratio"] * 0.02
            and context["current"]["close"] >= context["prev"]["close"]
            and context["current_candle"]["close_pos"] >= max(p["breakout_close_pos_min"] - 0.04, 0.54)
            and context["current"]["volume"] >= max(context["prev_volume"] * 0.86, context["recent_volume_avg"] * 0.88)
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
            strong_trend_bypass = _long_strong_trend_bypass(context, market_state, p)
            if long_final_veto_clear(
                context,
                market_state,
                p,
                long_breakout_path,
                long_pullback_path,
                long_reaccel_path,
                long_ownership_relay,
                strong_trend_bypass=strong_trend_bypass,
            ):
                _record_funnel_pass("long", "final_veto_pass")
                if not _long_entry_addition_available(positions):
                    return None
                path_key = "long_relay"
                if long_breakout_path:
                    path_key = "long_breakout"
                elif long_pullback_path:
                    path_key = "long_pullback"
                elif long_reaccel_path:
                    path_key = "long_reaccel"
                return {
                    "entry_signal": "long_pullback",
                    "entry_side": "long",
                    "entry_path_key": path_key,
                    "entry_path_tag": ENTRY_PATH_TAGS.get(path_key, path_key),
                }

    if short_outer_context_ok(context, market_state, p):
        _record_funnel_pass("short", "outer_context_pass")
        short_path_key = _short_entry_path_key(context, market_state, p, require_breakdown_gate=True)
        short_breakdown_path = short_path_key == "short_breakdown"
        short_bounce_fail_path = short_path_key == "short_bounce_fail"
        short_reaccel_path = short_path_key == "short_reaccel"
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
                return {
                    "entry_signal": "short_breakdown",
                    "entry_side": "short",
                    "entry_path_key": short_path_key,
                    "entry_path_tag": ENTRY_PATH_TAGS.get(short_path_key, short_path_key),
                }

    return None


def strategy(data, idx, positions, market_state):
    p = PARAMS
    context = _strategy_entry_context(data, idx, positions, market_state, p)
    if context is None:
        return None

    _record_funnel_pass("long", "sideways_pass")
    _record_funnel_pass("short", "sideways_pass")

    if positions and _position_side(positions[0]) == "long":
        forced_short_signal = _short_entry_signal(data, idx, positions, market_state)
        long_exit_followthrough_ok = _active_long_exit_followthrough_ok(
            positions,
            market_state,
            context["current"]["close"],
        )
        if (
            forced_short_signal
            and not long_exit_followthrough_ok
            and not _long_durable_hold_active(market_state)
        ):
            return forced_short_signal

    if long_outer_context_ok(context, market_state, p):
        _record_funnel_pass("long", "outer_context_pass")
        long_breakout_path = long_breakout_ok(context, market_state, p)
        long_pullback_path = long_pullback_ok(context, market_state, p)
        long_reaccel_path = long_trend_reaccel_ok(context, market_state, p)
        early_turn_outer_lane = context.get("long_outer_lane") == "early_turn"
        long_handoff_ready = (
            early_turn_outer_lane
            and not long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path)
            and context["long_reclaim_ready"]
            and context["current"]["high"] >= context["breakout_high"]
            and context["current"]["close"] >= context["breakout_high"] * (1.0 + p["breakout_buffer_pct"] * 0.15)
            and context["breakout_distance_pct"] >= 0.0
            and context["breakout_distance_pct"] <= context["atr_ratio"] * 0.08
            and context["current"]["close"] >= context["prev"]["close"]
            and context["current_candle"]["close_pos"] >= max(p["breakout_close_pos_min"] - 0.01, 0.59)
            and context["volume_ratio"] >= max(p["breakout_volume_ratio_min"] - 0.08, 1.00)
            and context["current"]["volume"] >= max(context["prev_volume"] * 0.94, context["recent_volume_avg"] * 0.94)
            and context["breakout_reference_stale_gap_pct"] <= max(context["atr_ratio"] * 0.42, 0.0030)
            and _flow_entry_ok(
                market_state,
                context["hourly"],
                context["fourh"],
                p,
                "long",
                strong=False,
            )
        )
        long_handoff_breakout = (
            long_handoff_ready
            and context["current"]["close"] >= context["breakout_high"] * (1.0 + p["breakout_buffer_pct"] * 0.70)
            and context["current"]["close"] > context["prev"]["high"]
            and context["current_range"] >= context["recent_range_avg"] * 0.86
            and context["current_candle"]["body_ratio"] >= max(p["breakout_body_ratio_min"] - 0.04, 0.27)
        )
        long_handoff_pullback = (
            long_handoff_ready
            and not long_handoff_breakout
            and context["prev"]["low"] <= context["breakout_high"] * (1.0 + context["atr_ratio"] * 0.12)
            and context["prev"]["close"] <= context["prev"]["high"] - context["prev_range"] * 0.14
            and context["current"]["close"] > max(
                context["prev"]["close"],
                context["breakout_high"] * (1.0 + p["breakout_buffer_pct"] * 0.45),
            )
            and context["prev_breakout_distance_pct"] <= context["atr_ratio"] * 0.06
            and context["current_range"] >= max(context["prev_range"] * 0.92, context["recent_range_avg"] * 0.84)
        )
        long_breakout_path = long_breakout_path or long_handoff_breakout
        long_pullback_path = long_pullback_path or long_handoff_pullback
        long_ownership_relay = (
            not early_turn_outer_lane
            and not long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path)
            and context["long_reclaim_ready"]
            and context["current"]["high"] >= context["breakout_high"]
            and context["current"]["close"] >= context["breakout_high"] * (1.0 + p["breakout_buffer_pct"] * 0.28)
            and context["breakout_distance_pct"] >= 0.0
            and context["breakout_distance_pct"] <= context["atr_ratio"] * 0.08
            and context["current"]["close"] >= context["prev"]["close"]
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
            strong_trend_bypass = _long_strong_trend_bypass(context, market_state, p)
            if long_final_veto_clear(
                context,
                market_state,
                p,
                long_breakout_path,
                long_pullback_path,
                long_reaccel_path,
                long_ownership_relay,
                strong_trend_bypass=strong_trend_bypass,
            ):
                _record_funnel_pass("long", "final_veto_pass")
                if not _long_entry_addition_available(positions):
                    return None
                path_key = "long_relay"
                if long_breakout_path:
                    path_key = "long_breakout"
                elif long_pullback_path:
                    path_key = "long_pullback"
                elif long_reaccel_path:
                    path_key = "long_reaccel"
                return normalize_entry_signal("long_pullback", fallback_side="long") or None

    if short_outer_context_ok(context, market_state, p):
        _record_funnel_pass("short", "outer_context_pass")
        short_path_key = _short_entry_path_key(context, market_state, p, require_breakdown_gate=True)
        short_breakdown_path = short_path_key == "short_breakdown"
        short_bounce_fail_path = short_path_key == "short_bounce_fail"
        short_reaccel_path = short_path_key == "short_reaccel"
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
                return normalize_entry_signal("short_breakdown", fallback_side="short") or None
    return None
