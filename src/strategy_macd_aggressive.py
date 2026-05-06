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
ADX_PERIOD = 10
ADX_LOOKBACK_MULT = 3
LONG_PARTIAL_TAKE_PROFIT_PRICE_PCT = 0.05
LONG_PARTIAL_TAKE_PROFIT_CLOSE_FRACTION = 0.50
LONG_TRAILING_MULTIPLIER = 1.5
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


def _leveraged_pnl_pct_from_price_move(price_move_pct, leverage):
    return max(price_move_pct, 0.0) * max(float(leverage), 0.0) * 100.0

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
    "breakout_flow_score_min": 3,
    "breakout_flow_score_strong_min": 5,
    "breakout_hist_min": 4.0,
    "breakout_lookback": 28,
    "breakout_rsi_max": 69.0,
    "breakout_rsi_min": 50.0,
    "breakout_taker_buy_ratio_min": 0.50,
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
    "break_even_activation_pct": 39.0,
    "break_even_buffer_pct": 0.35,
    "breakout_break_even_activation_pct": 59.2,
    "breakout_break_even_buffer_pct": 0.30,
    "breakout_max_hold_bars": 384,
    "breakout_stop_atr_mult": 2.3,
    "breakout_tp1_close_fraction": 0.16,
    "breakout_tp1_pnl_pct": 80.0,
    "breakout_trailing_activation_pct": 95.0,
    "breakout_trailing_giveback_pct": 29.1,
    "dynamic_hold_adx_strong_threshold": 26.0,
    "dynamic_hold_adx_threshold": 16.0,
    "dynamic_hold_extension_bars": 96,
    "dynamic_hold_max_bars": 384,
    "entry_delay_minutes": 1,
    "execution_use_1m": 1,
    "funding_fee_enabled": 1,
    "leverage": 20,
    "long_breakout_stop_atr_mult": 5.90,
    "long_pullback_break_even_buffer_pct": 0.28,
    "long_pullback_stop_atr_mult": 5.90,
    "long_pullback_trailing_giveback_pct": 17.64,
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
    "regime_price_confirm_buffer_pct": 0.012,
    "short_breakdown_break_even_activation_pct": 25.7,
    "short_breakdown_max_hold_bars": 96,
    "short_breakdown_stop_atr_mult": 2.1,
    "short_breakdown_tp1_close_fraction": 0.22,
    "short_breakdown_tp1_pnl_pct": 31.4,
    "short_breakdown_trailing_activation_pct": 40.0,
    "short_breakdown_trailing_giveback_pct": 6.2307,
    "slippage_pct": 0.0003,
    "stop_atr_mult": 3.1,
    "stop_max_loss_pct": 75.7,
    "tp1_close_fraction": 0.50,
    "tp1_pnl_pct": 85.0,
    "trading_fee_enabled": 1,
    "trailing_activation_pct": 110.4,
    "trailing_giveback_pct": 13.44,
}
# EXIT_PARAMS_END


ENTRY_SIGNAL_ALIASES = {
    "long_breakout": "long_pullback",
    "long_pullback": "long_pullback",
    "long_reaccel": "long_pullback",
    "long_relay": "long_pullback",
    "long_impulse": "long_pullback",
    "long_reversal_sniper": "long_pullback",
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
    "long_reversal_sniper": "long_reversal_sniper",
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
LONG_PYRAMID_TRIGGER_RELAX_MULT = 0.65
LONG_PYRAMID_ADX_RELAX_MULT = 0.80
INTRADAY_BULL_EMA_PERIOD = 50
INTRADAY_BULL_ADX_MIN = 20.0
LONG_TIME_EXIT_MIN_HOLD_BARS = 48
LONG_TIME_EXIT_MAX_PRICE_MOVE_PCT = 0.02
VOLUME_CLIMAX_LOOKBACK = 20
VOLUME_CLIMAX_SPIKE_MULT = 3.0


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


def _confirm_count(*conditions):
    return sum(1 for condition in conditions if condition)


def _avg(data, start, end, key):
    total = 0.0
    count = 0
    for i in range(start, end + 1):
        total += data[i][key]
        count += 1
    return total / count if count else 0.0


def _ema(data, start, end, key, period):
    if period <= 1:
        return data[end][key]
    seed_start = max(start, end - period + 1)
    ema = _avg(data, seed_start, end, key)
    alpha = 2.0 / (period + 1.0)
    for i in range(seed_start + 1, end + 1):
        ema = data[i][key] * alpha + ema * (1.0 - alpha)
    return ema


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


def _aggregated_adx_bars(data, end_idx, step, period):
    bars_needed = max(period * ADX_LOOKBACK_MULT, period * 2 + 1)
    start_idx = max(0, end_idx - step * bars_needed + 1)
    bars = []
    chunk_start = start_idx
    while chunk_start <= end_idx:
        chunk_end = min(chunk_start + step - 1, end_idx)
        first_bar = data[chunk_start]
        high = first_bar["high"]
        low = first_bar["low"]
        for i in range(chunk_start + 1, chunk_end + 1):
            high = max(high, data[i]["high"])
            low = min(low, data[i]["low"])
        bars.append(
            {
                "high": high,
                "low": low,
                "close": data[chunk_end]["close"],
            }
        )
        chunk_start += step
    return bars


def _adx_from_bars(bars, period):
    if period <= 1 or len(bars) < period * 2:
        return None

    tr_values = []
    plus_dm_values = []
    minus_dm_values = []
    for i in range(1, len(bars)):
        current = bars[i]
        prev = bars[i - 1]
        up_move = current["high"] - prev["high"]
        down_move = prev["low"] - current["low"]
        plus_dm_values.append(up_move if up_move > down_move and up_move > 0.0 else 0.0)
        minus_dm_values.append(down_move if down_move > up_move and down_move > 0.0 else 0.0)
        tr_values.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - prev["close"]),
                abs(current["low"] - prev["close"]),
            )
        )

    if len(tr_values) < period:
        return None

    tr_smooth = sum(tr_values[:period])
    plus_dm_smooth = sum(plus_dm_values[:period])
    minus_dm_smooth = sum(minus_dm_values[:period])
    dx_values = []

    for i in range(period - 1, len(tr_values)):
        if i > period - 1:
            tr_smooth = tr_smooth - (tr_smooth / period) + tr_values[i]
            plus_dm_smooth = plus_dm_smooth - (plus_dm_smooth / period) + plus_dm_values[i]
            minus_dm_smooth = minus_dm_smooth - (minus_dm_smooth / period) + minus_dm_values[i]
        if tr_smooth <= 0.0:
            dx_values.append(0.0)
            continue
        plus_di = 100.0 * plus_dm_smooth / tr_smooth
        minus_di = 100.0 * minus_dm_smooth / tr_smooth
        di_sum = plus_di + minus_di
        dx_values.append(100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0.0 else 0.0)

    if len(dx_values) < period:
        return dx_values[-1] if dx_values else None

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = ((adx * (period - 1)) + dx) / period
    return adx


def _tuned_entry_market_state(data, idx, market_state):
    if not market_state:
        return market_state
    tuned_state = dict(market_state)
    intraday_adx = _adx_from_bars(_aggregated_adx_bars(data, idx, 1, ADX_PERIOD), ADX_PERIOD)
    if intraday_adx is not None:
        tuned_state["adx"] = intraday_adx
    for frame_key, step in (("hourly", 4), ("four_hour", 16)):
        frame = market_state.get(frame_key)
        if frame is None:
            continue
        tuned_frame = dict(frame)
        tuned_adx = _adx_from_bars(_aggregated_adx_bars(data, idx, step, ADX_PERIOD), ADX_PERIOD)
        if tuned_adx is not None:
            tuned_frame["adx"] = tuned_adx
        tuned_state[frame_key] = tuned_frame
    return tuned_state


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
    long_capacity = max(
        LONG_PYRAMID_MAX_ADDS + 1,
        int(EXIT_PARAMS.get("max_concurrent_positions", LONG_PYRAMID_MAX_ADDS + 1)),
    )
    return long_positions < long_capacity


def _long_position_pnl_pct(positions, current_close):
    if not positions or current_close <= 0.0:
        return 0.0
    total_size = 0.0
    weighted_entry = 0.0
    for position in positions:
        if _position_side(position) != "long":
            continue
        size = max(float(position.get("size", 0.0)), 0.0)
        entry_price = float(position.get("entry_price", 0.0))
        if size <= 0.0 or entry_price <= 0.0:
            continue
        total_size += size
        weighted_entry += entry_price * size
    if total_size <= 0.0:
        return 0.0
    avg_entry_price = weighted_entry / total_size
    price_move_pct = (current_close - avg_entry_price) / max(avg_entry_price, 1e-9)
    return _leveraged_pnl_pct_from_price_move(price_move_pct, EXIT_PARAMS.get("leverage", 0.0))


def _long_pyramid_relaxation_active(positions, current_close, market_state):
    if not positions or _count_positions_by_side(positions, "long") <= 0:
        return False
    adx_value = float((market_state or {}).get("adx", 0.0))
    relaxed_adx_min = float(EXIT_PARAMS.get("pyramid_adx_min", 0.0)) * LONG_PYRAMID_ADX_RELAX_MULT
    if adx_value < relaxed_adx_min:
        return False
    relaxed_trigger_pnl = float(EXIT_PARAMS.get("pyramid_trigger_pnl", 0.0)) * LONG_PYRAMID_TRIGGER_RELAX_MULT
    return _long_position_pnl_pct(positions, current_close) >= relaxed_trigger_pnl


def _long_time_exit_active(positions, current_close):
    if not positions or current_close <= 0.0:
        return False
    long_positions = [position for position in positions if _position_side(position) == "long"]
    if not long_positions:
        return False
    lead_position = long_positions[0]
    if int(lead_position.get("hold_bars", 0)) <= LONG_TIME_EXIT_MIN_HOLD_BARS:
        return False
    total_size = 0.0
    weighted_entry = 0.0
    for position in long_positions:
        size = max(float(position.get("size", 0.0)), 0.0)
        entry_price = float(position.get("entry_price", 0.0))
        if size <= 0.0 or entry_price <= 0.0:
            continue
        total_size += size
        weighted_entry += entry_price * size
    if total_size <= 0.0:
        return False
    avg_entry_price = weighted_entry / total_size
    price_move_pct = (current_close - avg_entry_price) / max(avg_entry_price, 1e-9)
    return price_move_pct < LONG_TIME_EXIT_MAX_PRICE_MOVE_PCT


def _volume_climax_exhaustion_side(data, idx, positions):
    stall_bars = 2
    if not positions or idx < max(VOLUME_CLIMAX_LOOKBACK, stall_bars):
        return ""
    lead_position = positions[0]
    side = _position_side(lead_position)
    if side not in {"long", "short"}:
        return ""
    hold_bars = max(int(lead_position.get("hold_bars", 0)), 0)
    if hold_bars < stall_bars:
        return ""
    volume_avg = max(_avg(data, idx - VOLUME_CLIMAX_LOOKBACK, idx - 1, "volume"), 1e-9)
    current_bar = data[idx]
    if current_bar["volume"] < volume_avg * VOLUME_CLIMAX_SPIKE_MULT:
        return ""
    entry_idx = max(0, idx - hold_bars)
    prior_extreme_end = idx - stall_bars
    if prior_extreme_end < entry_idx:
        return ""
    recent_extreme_start = idx - stall_bars + 1
    entry_price = float(lead_position.get("entry_price", 0.0))
    if side == "long":
        prior_extreme = _window_max(data, entry_idx, prior_extreme_end, "high")
        recent_extreme = _window_max(data, recent_extreme_start, idx, "high")
        if recent_extreme > prior_extreme:
            return ""
        if entry_price > 0.0 and current_bar["close"] <= entry_price:
            return ""
        return "long"
    prior_extreme = _window_min(data, entry_idx, prior_extreme_end, "low")
    recent_extreme = _window_min(data, recent_extreme_start, idx, "low")
    if recent_extreme < prior_extreme:
        return ""
    if entry_price > 0.0 and current_bar["close"] >= entry_price:
        return ""
    return "short"


def _flow_alignment_score(market_state, hourly, fourh, params, side):
    def _safe_float(payload, key, default):
        if payload is None:
            return default
        try:
            value = float(payload.get(key, default))
        except (TypeError, ValueError, AttributeError):
            return default
        return value

    intraday_buy_ratio = _safe_float(market_state, "taker_buy_ratio", 0.5)
    intraday_sell_ratio = _safe_float(market_state, "taker_sell_ratio", 0.5)
    intraday_imbalance = _safe_float(market_state, "flow_imbalance", 0.0)
    hourly_buy_ratio = _safe_float(hourly, "taker_buy_ratio", 0.5)
    hourly_sell_ratio = _safe_float(hourly, "taker_sell_ratio", 0.5)
    hourly_imbalance = _safe_float(hourly, "flow_imbalance", 0.0)
    fourh_buy_ratio = _safe_float(fourh, "taker_buy_ratio", 0.5)
    fourh_sell_ratio = _safe_float(fourh, "taker_sell_ratio", 0.5)
    fourh_imbalance = _safe_float(fourh, "flow_imbalance", 0.0)

    if side == "long":
        score = 0
        if intraday_buy_ratio >= params["breakout_taker_buy_ratio_min"]:
            score += 1
        if intraday_imbalance >= params["breakout_flow_imbalance_min"]:
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
    if intraday_sell_ratio >= 0.54:
        score += 1
    if intraday_imbalance <= -0.08:
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
            score >= 5
            and intraday_imbalance <= -0.06
            and hourly_imbalance <= 0.0
            and fourh_imbalance <= 0.0
            and hourly_sell_ratio >= 0.505
            and fourh_sell_ratio >= 0.50
        )
    return (
        score >= 4
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

    intraday_buy_ratio = _safe_float(market_state, "taker_buy_ratio", 0.5)
    intraday_sell_ratio = _safe_float(market_state, "taker_sell_ratio", 0.5)
    intraday_imbalance = _safe_float(market_state, "flow_imbalance", 0.0)
    hourly_buy_ratio = _safe_float(hourly, "taker_buy_ratio", 0.5)
    hourly_sell_ratio = _safe_float(hourly, "taker_sell_ratio", 0.5)
    hourly_imbalance = _safe_float(hourly, "flow_imbalance", 0.0)
    fourh_buy_ratio = _safe_float(fourh, "taker_buy_ratio", 0.5)
    fourh_sell_ratio = _safe_float(fourh, "taker_sell_ratio", 0.5)
    fourh_imbalance = _safe_float(fourh, "flow_imbalance", 0.0)

    if side == "long":
        directional_bias = (
            max(intraday_buy_ratio - params["breakout_taker_buy_ratio_min"], 0.0)
            + max(hourly_buy_ratio - params["hourly_taker_buy_ratio_min"], 0.0)
            + max(fourh_buy_ratio - params["fourh_taker_buy_ratio_min"], 0.0)
            + max(intraday_imbalance - params["breakout_flow_imbalance_min"], 0.0) * 2.0
            + max(hourly_imbalance - params["hourly_flow_confirmation_min"], 0.0)
            + max(fourh_imbalance - params["fourh_flow_confirmation_min"], 0.0)
        )
    else:
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
        "participation_bias": 0.0,
        "directional_bias": directional_bias,
    }


def _flow_entry_ok(market_state, hourly, fourh, params, side=None, strong=False):
    entry_side = side if side in {"long", "short"} else "short"
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
    reversal_reference_low = _window_min(data, idx - 20, idx - 1, "low")
    reversal_volume_avg = max(_avg(data, idx - 5, idx - 1, "volume"), 1e-9)

    return {
        "current": current,
        "prev": prev,
        "pre_prev": pre_prev,
        "hourly": hourly,
        "fourh": fourh,
        "intraday": intraday,
        "intraday_bull_ema": _ema(
            data,
            0,
            idx,
            "close",
            INTRADAY_BULL_EMA_PERIOD,
        ),
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
        "reversal_reference_low": reversal_reference_low,
        "reversal_volume_avg": reversal_volume_avg,
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
            market_state["adx"] > INTRADAY_BULL_ADX_MIN
            and current["close"] > context["intraday_bull_ema"]
        ),
        "hourly_bull": (
            hourly["close"] > hourly["ema_fast"] > hourly["ema_slow"]
            and hourly["close"] > hourly["ema_anchor"]
            and hourly["macd_line"] > hourly["signal_line"]
            and hourly["adx"] >= params["hourly_adx_min"]
            and hourly["trend_spread_pct"] > 0.0
            and hourly["ema_slow_slope_pct"] > 0.0
        ),
        "hourly_neutral": (
            hourly["close"] >= hourly["ema_slow"]
            and hourly["macd_line"] >= hourly["signal_line"]
            and hourly["trend_spread_pct"] >= 0.0
            and hourly["ema_slow_slope_pct"] >= 0.0
            and hourly["adx"] >= max(params["hourly_adx_min"] - 5.0, 14.0)
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
    release_flags = _sideways_release_flags(market_state, positions=positions)
    fast_decision = _sideways_fast_decision(release_flags)
    if fast_decision is not None:
        return fast_decision
    intraday = _intraday_trend_metrics(market_state)
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    if _sideways_directional_pause(intraday, hourly, fourh, release_flags):
        return True
    return _sideways_signal_score(release_flags) >= 3


def _sideways_fast_decision(release_flags):
    mild_long_pullback = bool(release_flags.get("mild_long_pullback", False))
    convexity_release = bool(release_flags.get("convexity_release", False))
    trend_awakening = bool(release_flags.get("trend_awakening", False))
    fresh_directional_expansion = bool(release_flags.get("fresh_directional_expansion", False))
    exhausted_drift = bool(release_flags.get("exhausted_drift", False))
    hard_sideways = bool(release_flags.get("hard_sideways", False))
    extreme_compression = bool(release_flags.get("extreme_compression", False))
    if hard_sideways and not (mild_long_pullback and not extreme_compression):
        return True
    if exhausted_drift and not convexity_release and not mild_long_pullback:
        return True
    if trend_awakening or fresh_directional_expansion:
        return False
    return None


def _sideways_directional_pause(intraday, hourly, fourh, release_flags):
    intraday_spread = release_flags["intraday_spread"]
    hourly_spread = release_flags["hourly_spread"]
    fourh_spread = release_flags["fourh_spread"]
    hourly_slope = release_flags["hourly_slope"]
    fourh_slope = release_flags["fourh_slope"]
    intraday_chop = release_flags["intraday_chop"]
    hourly_chop = release_flags["hourly_chop"]
    atr_ratio = release_flags["atr_ratio"]
    adx_soft = release_flags["adx_soft"]
    mild_long_pullback = bool(release_flags.get("mild_long_pullback", False))
    mixed_trend = (
        hourly["trend_spread_pct"] * fourh["trend_spread_pct"] <= 0.0
        and intraday_spread < max(atr_ratio * 0.55, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.15)
    )
    weak_trend = (
        hourly_spread < max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.20, atr_ratio * 0.72)
        and fourh_spread < max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.15, atr_ratio * 0.92)
        and hourly_slope < atr_ratio * 0.09
        and fourh_slope < atr_ratio * 0.045
    )
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
    return (
        (mixed_trend and (hourly_chop >= SIDEWAYS_HOURLY_CHOP_MIN - 1.0 or adx_soft))
        or (weak_trend and (intraday_chop >= SIDEWAYS_INTRADAY_CHOP_MIN - 1.0 or adx_soft) and not mild_long_pullback)
        or (bull_front_run and not mild_long_pullback)
    )


def _sideways_signal_score(release_flags):
    signals = _confirm_count(
        release_flags["intraday_chop"] >= SIDEWAYS_INTRADAY_CHOP_MIN
        and release_flags["hourly_chop"] >= SIDEWAYS_HOURLY_CHOP_MIN,
        release_flags["atr_ratio"] < SIDEWAYS_MIN_ATR_RATIO
        and release_flags["hourly_chop"] >= SIDEWAYS_HOURLY_CHOP_MIN - 1.0,
        release_flags["hourly_spread"] < SIDEWAYS_MIN_HOURLY_SPREAD_PCT
        and release_flags["fourh_spread"] < SIDEWAYS_MIN_FOURH_SPREAD_PCT,
        release_flags["intraday_spread"] < release_flags["atr_ratio"] * 0.28
        and release_flags["hourly_slope"] < release_flags["atr_ratio"] * 0.08
        and release_flags["fourh_slope"] < release_flags["atr_ratio"] * 0.04,
        bool(release_flags["adx_soft"]),
    )
    if release_flags["convexity_release"] or release_flags["fresh_directional_expansion"]:
        signals -= 2
    if release_flags["mild_long_pullback"] and not release_flags["extreme_compression"]:
        signals -= 1
    return signals


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


def _long_trend_quality_confirms(market_state, metrics, params):
    atr_ratio = metrics["atr_ratio"]
    return _confirm_count(
        market_state["adx"] >= max(params["intraday_adx_min"], 14.0),
        metrics["intraday_spread"] >= atr_ratio * 0.28,
        metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.20, atr_ratio * 0.74),
        metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.70, atr_ratio * 0.66),
        metrics["hourly_slope"] >= atr_ratio * 0.072 and metrics["fourh_slope"] >= atr_ratio * 0.028,
    )


def _long_overextended_without_fourh(hourly, fourh, metrics, atr_ratio, params):
    hourly_fast_extension = (hourly["close"] - hourly["ema_fast"]) / max(hourly["close"], 1e-9)
    hourly_anchor_extension = (hourly["close"] - hourly["ema_anchor"]) / max(hourly["close"], 1e-9)
    fourh_expansion_floor = (
        metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.58, atr_ratio * 0.56)
        and metrics["fourh_slope"] >= atr_ratio * 0.028
        and fourh["adx"] >= max(params["fourh_adx_min"] - 0.2, 12.8)
    )
    return (
        hourly_fast_extension >= max(atr_ratio * 0.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.00)
        and hourly_anchor_extension >= max(atr_ratio * 1.32, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.10)
        and not fourh_expansion_floor
    )


def _long_quality_gate(market_state, metrics, params, *, side):
    if side == "long":
        return _long_trend_quality_confirms(market_state, metrics, params)
    return _short_trend_quality_confirms(market_state, metrics, params)


def _long_support_flags(context, market_state, params, positions):
    long_state = _build_long_trend_state(context, market_state, params)
    reclaim_ready = _long_reclaim_ready(context, market_state, params)
    lane = _long_outer_context_lane(context, long_state, params, reclaim_ready)
    return long_state, reclaim_ready, lane


def _short_trend_quality_confirms(market_state, metrics, params):
    atr_ratio = metrics["atr_ratio"]
    return _confirm_count(
        market_state["adx"] >= max(params["intraday_adx_min"], 14.5),
        metrics["intraday_spread"] >= atr_ratio * 0.30,
        metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.24, atr_ratio * 0.78),
        metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.78, atr_ratio * 0.72),
        metrics["hourly_slope"] >= atr_ratio * 0.076 and metrics["fourh_slope"] >= atr_ratio * 0.032,
    )


def _short_trend_quality_support_flags(market_state, hourly, fourh, metrics, atr_ratio, params):
    hourly_fast_discount = (hourly["ema_fast"] - hourly["close"]) / max(hourly["close"], 1e-9)
    hourly_anchor_discount = (hourly["ema_anchor"] - hourly["close"]) / max(hourly["close"], 1e-9)
    fourh_participation_ok = (
        metrics["fourh_spread"] >= max(metrics["hourly_spread"] * 0.74, atr_ratio * 0.78)
        and metrics["fourh_slope"] >= atr_ratio * 0.032
        and fourh["adx"] >= max(params["fourh_adx_min"] - 0.2, 13.4)
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
    return fourh_participation_ok, fresh_pressure_ok, macd_pressure_ok, overdiscounted_without_fourh


def _trend_quality_long(market_state):
    p = PARAMS
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "long")
    atr_ratio = metrics["atr_ratio"]
    confirms = _long_trend_quality_confirms(market_state, metrics, p)
    if _long_overextended_without_fourh(hourly, fourh, metrics, atr_ratio, p):
        return False
    return confirms >= 4


def _fourh_trend_quality_long_score(market_state, params):
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "long")
    atr_ratio = metrics["atr_ratio"]

    confirms = 0
    if metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.70, atr_ratio * 0.66):
        confirms += 1
    if metrics["fourh_slope"] >= atr_ratio * 0.028:
        confirms += 1
    if fourh["adx"] >= max(params["fourh_adx_min"] - 0.2, 12.8):
        confirms += 1
    if fourh["close"] >= fourh["ema_fast"] or fourh["macd_line"] > fourh["signal_line"]:
        confirms += 1
    return confirms / 4.0


def _trend_quality_short(market_state):
    p = PARAMS
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "short")
    atr_ratio = metrics["atr_ratio"]
    confirms = _short_trend_quality_confirms(market_state, metrics, p)
    fourh_participation_ok, fresh_pressure_ok, macd_pressure_ok, overdiscounted_without_fourh = (
        _short_trend_quality_support_flags(market_state, hourly, fourh, metrics, atr_ratio, p)
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
    confirms = _confirm_count(
        metrics["intraday_spread"] >= atr_ratio * 0.22,
        metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.08, atr_ratio * 0.66),
        metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.58, atr_ratio * 0.56),
        metrics["hourly_slope"] >= atr_ratio * 0.052 and metrics["fourh_slope"] >= atr_ratio * 0.018,
        breakout_distance_pct >= -atr_ratio * 0.01,
    )

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
    long_stop_atr_mult = (
        EXIT_PARAMS["long_breakout_stop_atr_mult"] + EXIT_PARAMS["long_pullback_stop_atr_mult"]
    ) / 2.0
    long_exit_relax_mult = max(long_stop_atr_mult / max(EXIT_PARAMS["stop_atr_mult"], 1e-9), 1.0)
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
    if breakout_distance_pct >= -atr_ratio * (0.04 * long_exit_relax_mult):
        confirms += 1

    reversal_pressure = (
        market_state["histogram"] <= 0.0
        and market_state["macd_line"] <= market_state["signal_line"]
        and hourly["close"] <= hourly["ema_fast"] * (1.0 + atr_ratio * (0.10 * long_exit_relax_mult))
        and hourly["ema_fast"] <= hourly["ema_anchor"] * (1.0 + atr_ratio * (0.05 * long_exit_relax_mult))
        and (
            breakout_distance_pct <= atr_ratio * (0.04 * long_exit_relax_mult)
            or hourly_fast_extension <= atr_ratio * (0.06 * long_exit_relax_mult)
            or metrics["hourly_slope"] <= 0.0
        )
    )
    support_lost = (
        current_close <= hourly["ema_fast"] * (1.0 + atr_ratio * (0.04 * long_exit_relax_mult))
        and (
            hourly_fast_extension <= atr_ratio * (0.03 * long_exit_relax_mult)
            or hourly_anchor_extension <= atr_ratio * (0.10 * long_exit_relax_mult)
            or breakout_distance_pct <= -atr_ratio * (0.01 * max(long_exit_relax_mult - 1.0, 0.0))
        )
        and metrics["hourly_slope"] <= atr_ratio * 0.010
        and metrics["intraday_spread"] <= atr_ratio * (0.24 * long_exit_relax_mult)
    )
    deeper_reversal = (
        breakout_distance_pct <= -atr_ratio * (0.02 * long_exit_relax_mult)
        and hourly_fast_extension <= atr_ratio * (0.02 * long_exit_relax_mult)
        and (
            metrics["hourly_slope"] <= 0.0
            or metrics["fourh_slope"] <= atr_ratio * 0.010
            or market_state["histogram"] <= -0.01
        )
    )
    if reversal_pressure or support_lost or deeper_reversal:
        return False
    trend_cushion_active = (
        breakout_distance_pct >= atr_ratio * 0.05
        and metrics["hourly_slope"] >= atr_ratio * 0.010
        and metrics["fourh_slope"] >= 0.0
    )
    required_confirms = 2 if trend_cushion_active else 3
    return confirms >= required_confirms


def _trend_followthrough_short(market_state, trigger_price, current_close):
    hourly = market_state["hourly"]
    fourh = market_state["four_hour"]
    metrics = _directional_trend_metrics(market_state, "short")
    atr_ratio = metrics["atr_ratio"]
    breakdown_distance_pct = (trigger_price - current_close) / max(trigger_price, 1e-9)
    hourly_fast_discount = (hourly["ema_fast"] - current_close) / max(current_close, 1e-9)
    hourly_anchor_discount = (hourly["ema_anchor"] - current_close) / max(current_close, 1e-9)
    confirms = _confirm_count(
        metrics["intraday_spread"] >= atr_ratio * 0.28,
        metrics["hourly_spread"] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.24, atr_ratio * 0.80),
        metrics["fourh_spread"] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.74, atr_ratio * 0.70),
        metrics["hourly_slope"] >= atr_ratio * 0.078 and metrics["fourh_slope"] >= atr_ratio * 0.032,
        breakdown_distance_pct >= atr_ratio * 0.05,
    )

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


def _long_reclaim_ready(context, market_state, params):
    return (
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


def _long_outer_context_lane(context, long_state, params, reclaim_ready):
    intraday_support_ready = long_state["intraday_bull"] or reclaim_ready
    fourh_not_bear = not (
        context["fourh"]["close"] < context["fourh"]["ema_slow"]
        and context["fourh"]["trend_spread_pct"] < 0.0
        and context["fourh"]["ema_slow_slope_pct"] < 0.0
    )
    hourly_support_ready = long_state["hourly_bull"] or long_state["hourly_neutral"]
    fourh_context_ready = fourh_not_bear and (
        long_state["fourh_bull_base"] or long_state["fourh_bull_turn"]
    )
    base_ownership_ready = fourh_context_ready and _confirm_count(
        intraday_support_ready,
        long_state["hourly_bull"],
        long_state["fourh_bull_base"],
    ) >= 2
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
    if base_ownership_ready and (hourly_support_ready or hourly_repair_ready):
        return "trend"
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
        and reclaim_ready
        and long_state["fourh_bull_turn"]
        and not long_state["hourly_bull"]
        and context["hourly_fast_extension_pct"] <= max(
            context["atr_ratio"] * 0.76,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.68,
        )
        and context["hourly_anchor_extension_pct"] <= max(
            context["atr_ratio"] * 1.06,
            SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.56,
        )
    )
    if hourly_turn_repair_ready:
        return "early_turn"
    return ""


def _short_outer_context_lane(context, short_state, market_state, params):
    short_fourh_bear_gate = (
        short_state["fourh_bear"]
        or (
            context["fourh"]["trend_spread_pct"] <= -max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.68, context["atr_ratio"] * 0.58)
            and context["fourh"]["ema_slow_slope_pct"] <= -max(context["atr_ratio"] * 0.022, SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.18)
        )
    )
    strict_trend_lane = (
        short_state["intraday_bear"]
        and short_state["hourly_bear"]
        and short_fourh_bear_gate
        and _trend_quality_short(market_state)
    )
    if strict_trend_lane:
        return "trend"
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
        not extreme_bull_trend
        and short_fourh_bear_gate
        and context["current"]["close"] <= context["breakdown_low"] * (1.0 - params["breakdown_buffer_pct"])
        and context["breakdown_distance_pct"] >= context["atr_ratio"] * 0.06
        and context["current_candle"]["close_pos"] <= min(params["breakdown_close_pos_max"] + 0.03, 0.37)
        and context["current_candle"]["body_ratio"] >= max(params["breakdown_body_ratio_min"] - 0.05, 0.34)
    )
    if breakdown_release_lane:
        return "breakdown_release"
    return ""


def long_outer_context_ok(context, market_state, params):
    long_state = _build_long_trend_state(context, market_state, params)
    context["long_outer_lane"] = ""
    reclaim_ready = _long_reclaim_ready(context, market_state, params)
    context["long_reclaim_ready"] = reclaim_ready
    context["long_late_mature_guard"] = False
    context["long_outer_lane"] = _long_outer_context_lane(context, long_state, params, reclaim_ready)
    return bool(context["long_outer_lane"])


def long_breakout_ok(context, market_state, params):
    return _long_entry_path_core(context, market_state, params, mode="breakout")


def long_pullback_ok(context, market_state, params):
    return _long_entry_path_core(context, market_state, params, mode="pullback")


def long_trend_reaccel_ok(context, market_state, params):
    return _long_entry_path_core(context, market_state, params, mode="reaccel")


def _long_entry_path_core(context, market_state, params, *, mode):
    current = context["current"]
    prev = context["prev"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    current_candle = context["current_candle"]
    atr_ratio = context["atr_ratio"]
    breakout_distance_pct = context["breakout_distance_pct"]
    breakout_high = context["breakout_high"]
    breakout_high_penetration_pct = context["breakout_high_penetration_pct"]
    if mode == "breakout":
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
    if mode == "pullback":
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


def _long_reversal_sniper_ok(context):
    current = context.get("current")
    reversal_reference_low = context.get("reversal_reference_low")
    reversal_volume_avg = context.get("reversal_volume_avg")
    if not isinstance(current, dict) or reversal_reference_low is None or reversal_volume_avg is None:
        return False
    return (
        current["low"] < reversal_reference_low
        and current["close"] > current["open"]
        and current["close"] >= current["low"] * 1.015
        and current["volume"] >= reversal_volume_avg * 1.8
    )


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
    quality_relax_gate = 0.60
    fourh_quality_score = _fourh_trend_quality_long_score(market_state, params)
    long_quality_score = 0.65 * float(_trend_quality_long(market_state)) + 0.35 * fourh_quality_score
    high_quality_long = long_quality_score >= quality_relax_gate
    atr_ratio = context["atr_ratio"]
    hourly = context["hourly"]
    fourh = context["fourh"]
    rsi = market_state["rsi"]
    breakout_distance_pct = context["breakout_distance_pct"]
    hourly_fast_extension_pct = context["hourly_fast_extension_pct"]
    hourly_anchor_extension_pct = context["hourly_anchor_extension_pct"]
    sideways_regime = bool(context.get("sideways_regime", False))
    strict_rsi_cap = 72.0 if (breakout_ok or reaccel_ok) else 70.0
    relaxed_rsi_cap = strict_rsi_cap + 3.0
    strict_fast_extension_cap = max(atr_ratio * 1.02, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.22)
    relaxed_fast_extension_cap = max(atr_ratio * 1.20, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.58)
    strict_anchor_extension_cap = max(atr_ratio * 1.48, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.34)
    relaxed_anchor_extension_cap = max(atr_ratio * 1.72, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.72)
    strict_breakout_distance_cap = atr_ratio * 0.26
    relaxed_breakout_distance_cap = atr_ratio * 0.34
    mature_extension_clear = (
        breakout_distance_pct <= (relaxed_breakout_distance_cap if high_quality_long else strict_breakout_distance_cap)
        and hourly_fast_extension_pct <= (
            relaxed_fast_extension_cap if high_quality_long else strict_fast_extension_cap
        )
        and hourly_anchor_extension_pct <= (
            relaxed_anchor_extension_cap if high_quality_long else strict_anchor_extension_cap
        )
    )
    rsi_clear = rsi <= (relaxed_rsi_cap if high_quality_long else strict_rsi_cap)
    if strong_trend_bypass:
        return True
    if sideways_regime and not _flow_confirmation_ok(market_state, hourly, fourh, params, "long", strong=False):
        return False
    if high_quality_long:
        return rsi_clear and mature_extension_clear
    return rsi_clear and mature_extension_clear


def short_outer_context_ok(context, market_state, params):
    short_state = _build_short_trend_state(context, market_state, params)
    context["short_outer_lane"] = ""
    context["short_outer_lane"] = _short_outer_context_lane(context, short_state, market_state, params)
    return bool(context["short_outer_lane"])


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
    strong_fourh_bear = (
        fourh["trend_spread_pct"] <= -max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.06, atr_ratio * 0.68)
        and fourh["ema_slow_slope_pct"] <= -atr_ratio * 0.032
        and fourh["macd_line"] < fourh["signal_line"]
    )
    price_reaccel_ready = (
        current["close"] <= context["breakdown_low"] * (1.0 - params["breakdown_buffer_pct"] * 0.18)
        and context["breakdown_distance_pct"] >= atr_ratio * 0.04
        and context["breakdown_distance_pct"] <= atr_ratio * 0.30
        and current["low"] <= prev["low"] * (1.0 + atr_ratio * 0.04)
        and current["close"] <= prev["close"] * (1.0 + atr_ratio * 0.02)
    )
    range_reaccel_ready = (
        context["current_range"] >= max(context["prev_range"] * 0.92, context["recent_range_avg"] * 0.88)
    )
    volume_reaccel_ready = (
        current["volume"] >= max(context["prev_volume"] * 0.84, context["recent_volume_avg"] * 0.86)
    )
    adx_reaccel_ready = (
        market_state["adx"] >= max(params["breakdown_adx_min"] - 2.6, params["intraday_adx_min"])
    )
    hourly_reaccel_ready = (
        hourly["trend_spread_pct"] <= -max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.08, atr_ratio * 0.68)
    )
    flow_reaccel_ready = (
        _flow_entry_ok(market_state, hourly, fourh, params, "short", strong=not strong_fourh_bear)
        and flow_metrics["directional_bias"] >= (0.025 if strong_fourh_bear else 0.035)
    )
    return (
        price_reaccel_ready
        and prior_breakdown_hold
        and range_reaccel_ready
        and volume_reaccel_ready
        and adx_reaccel_ready
        and hourly_reaccel_ready
        and fourh["trend_spread_pct"] <= -max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.74, atr_ratio * 0.66)
        and fourh["ema_slow_slope_pct"] <= -atr_ratio * (0.032 if strong_fourh_bear else 0.036)
        and flow_reaccel_ready
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


def _strategy_exhaustion_short_signal(context, market_state, params, exhausted_side, sideways_regime, *, as_decision=False):
    if exhausted_side == "long" and not sideways_regime and short_outer_context_ok(context, market_state, params):
        _record_funnel_pass("short", "outer_context_pass")
        short_path_key = _short_entry_path_key(context, market_state, params, require_breakdown_gate=False)
        return _short_entry_result(
            context,
            market_state,
            params,
            short_path_key,
            as_decision=as_decision,
        )
    return None


def _strategy_long_signal(context, market_state, positions, params, exhausted_side, *, as_decision=False):
    sideways_regime = bool(context.get("sideways_regime", False))
    long_reversal_sniper = _long_reversal_sniper_ok(context)
    if long_reversal_sniper and not sideways_regime:
        _record_funnel_pass("long", "final_veto_pass")
        if not _long_entry_addition_available(positions):
            return None
        if as_decision:
            return {
                "entry_signal": "long_pullback",
                "entry_side": "long",
                "entry_path_key": "long_reversal_sniper",
                "entry_path_tag": ENTRY_PATH_TAGS.get("long_reversal_sniper", "long_reversal_sniper"),
            }
        return normalize_entry_signal("long_reversal_sniper", fallback_side="long") or None
    if exhausted_side == "long" or not long_outer_context_ok(context, market_state, params):
        return None

    _record_funnel_pass("long", "outer_context_pass")
    long_breakout_path, long_pullback_path, long_reaccel_path = _long_path_signals(
        context,
        market_state,
        params,
    )
    has_long_signal_path = long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path)
    if not has_long_signal_path:
        long_breakout_path, long_pullback_path = _long_handoff_paths(
            context,
            market_state,
            positions,
            params,
            long_breakout_path,
            long_pullback_path,
        )
        has_long_signal_path = long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path)

    long_ownership_relay = _long_ownership_relay(
        context,
        market_state,
        params,
        positions,
        has_long_signal_path,
    )
    return _long_entry_result(
        context,
        market_state,
        params,
        positions,
        long_breakout_path,
        long_pullback_path,
        long_reaccel_path,
        long_ownership_relay,
        as_decision=as_decision,
    )


def _long_path_signals(context, market_state, params):
    return (
        long_breakout_ok(context, market_state, params),
        long_pullback_ok(context, market_state, params),
        long_trend_reaccel_ok(context, market_state, params),
    )


def _long_handoff_paths(context, market_state, positions, params, long_breakout_path, long_pullback_path):
    long_pyramid_relax_active = _long_pyramid_relaxation_active(
        positions,
        context["current"]["close"],
        market_state,
    )
    handoff_buffer_mult = LONG_PYRAMID_TRIGGER_RELAX_MULT if long_pyramid_relax_active else 1.0
    handoff_close_pos_floor = max(
        params["breakout_close_pos_min"] - (0.05 if long_pyramid_relax_active else 0.01),
        0.56 if long_pyramid_relax_active else 0.59,
    )
    handoff_volume_floor = max(
        context["prev_volume"] * (0.90 if long_pyramid_relax_active else 0.94),
        context["recent_volume_avg"] * (0.90 if long_pyramid_relax_active else 0.94),
    )
    long_handoff_ready = (
        context.get("long_outer_lane") == "early_turn"
        and context["long_reclaim_ready"]
        and context["current"]["high"] >= context["breakout_high"]
        and context["current"]["close"] >= context["breakout_high"] * (1.0 + params["breakout_buffer_pct"] * 0.15 * handoff_buffer_mult)
        and context["breakout_distance_pct"] >= 0.0
        and context["breakout_distance_pct"] <= context["atr_ratio"] * 0.08
        and context["current"]["close"] >= context["prev"]["close"]
        and context["current_candle"]["close_pos"] >= handoff_close_pos_floor
        and context["volume_ratio"] >= max(params["breakout_volume_ratio_min"] - 0.08, 1.00)
        and context["current"]["volume"] >= handoff_volume_floor
        and context["breakout_reference_stale_gap_pct"] <= max(context["atr_ratio"] * 0.42, 0.0030)
        and _flow_entry_ok(
            market_state,
            context["hourly"],
            context["fourh"],
            params,
            "long",
            strong=False,
        )
    )
    long_handoff_breakout = (
        long_handoff_ready
        and context["current"]["close"] >= context["breakout_high"] * (1.0 + params["breakout_buffer_pct"] * 0.70)
        and context["current"]["close"] > context["prev"]["high"]
        and context["current_range"] >= context["recent_range_avg"] * 0.86
        and context["current_candle"]["body_ratio"] >= max(params["breakout_body_ratio_min"] - 0.04, 0.27)
    )
    long_handoff_pullback = (
        long_handoff_ready
        and not long_handoff_breakout
        and context["prev"]["low"] <= context["breakout_high"] * (1.0 + context["atr_ratio"] * 0.12)
        and context["prev"]["close"] <= context["prev"]["high"] - context["prev_range"] * 0.14
        and context["current"]["close"] > max(
            context["prev"]["close"],
            context["breakout_high"] * (1.0 + params["breakout_buffer_pct"] * 0.45),
        )
        and context["prev_breakout_distance_pct"] <= context["atr_ratio"] * 0.06
        and context["current_range"] >= max(context["prev_range"] * 0.92, context["recent_range_avg"] * 0.84)
    )
    return long_breakout_path or long_handoff_breakout, long_pullback_path or long_handoff_pullback


def _long_ownership_relay(context, market_state, params, positions, has_long_signal_path):
    if has_long_signal_path:
        return False
    long_pyramid_relax_active = _long_pyramid_relaxation_active(
        positions,
        context["current"]["close"],
        market_state,
    )
    relay_buffer_mult = LONG_PYRAMID_TRIGGER_RELAX_MULT if long_pyramid_relax_active else 1.0
    relay_close_pos_floor = max(
        params["breakout_close_pos_min"] - (0.06 if long_pyramid_relax_active else 0.02),
        0.56 if long_pyramid_relax_active else 0.58,
    )
    relay_volume_floor = max(
        context["prev_volume"] * (0.84 if long_pyramid_relax_active else 0.90),
        context["recent_volume_avg"] * (0.84 if long_pyramid_relax_active else 0.90),
    )
    long_quality_override = (
        0.65 * float(_trend_quality_long(market_state))
        + 0.35 * _fourh_trend_quality_long_score(market_state, params)
    ) >= 0.60
    long_ownership_relay = (
        context.get("long_outer_lane") != "early_turn"
        and context["long_reclaim_ready"]
        and context["current"]["high"] >= context["breakout_high"]
        and context["current"]["close"] >= context["breakout_high"] * (1.0 + params["breakout_buffer_pct"] * 0.28 * relay_buffer_mult)
        and context["breakout_distance_pct"] >= 0.0
        and context["breakout_distance_pct"] <= context["atr_ratio"] * 0.08
        and context["current"]["close"] >= context["prev"]["close"]
        and context["current_candle"]["close_pos"] >= relay_close_pos_floor
        and context["current"]["volume"] >= relay_volume_floor
        and _flow_entry_ok(
            market_state,
            context["hourly"],
            context["fourh"],
            params,
            "long",
            strong=False,
        )
    )
    if (
        long_quality_override
        and not long_ownership_relay
        and context["long_reclaim_ready"]
        and context["current"]["high"] >= context["breakout_high"]
        and context["current"]["close"] >= context["breakout_high"] * (1.0 + params["breakout_buffer_pct"])
    ):
        long_ownership_relay = True
    return long_ownership_relay


def _strategy_short_signal(context, market_state, params, exhausted_side, sideways_regime, *, as_decision=False):
    if exhausted_side != "short" and not sideways_regime and short_outer_context_ok(context, market_state, params):
        _record_funnel_pass("short", "outer_context_pass")
        short_path_key = _short_entry_path_key(context, market_state, params, require_breakdown_gate=True)
        return _short_entry_result(
            context,
            market_state,
            params,
            short_path_key,
            as_decision=as_decision,
        )
    return None


def _long_entry_signal(data, idx, positions, market_state):
    signal = strategy(data, idx, positions, market_state)
    return signal if signal in {"long_breakout", "long_pullback"} else None


def _strategy_entry_context(data, idx, positions, market_state, params, allow_sideways=False):
    if idx < params["min_history"]:
        return None
    context = _build_signal_context(data, idx, market_state, params)
    if context is None:
        return None
    sideways_regime = _is_sideways_regime(market_state, positions=positions)
    context["sideways_regime"] = sideways_regime
    if sideways_regime and not allow_sideways:
        return None
    return context


def _short_entry_path_key(context, market_state, params, require_breakdown_gate=False):
    if require_breakdown_gate:
        short_breakdown_path = short_breakdown_ok(context, market_state, params)
        short_bounce_fail_path = short_bounce_fail_ok(context, market_state, params)
    else:
        short_breakdown_path = _short_breakdown_path_ok(
            context,
            market_state,
            params,
            require_breakdown_gate=False,
        )
        short_bounce_fail_path = _short_bounce_fail_path_ok(
            context,
            market_state,
            params,
            require_breakdown_gate=False,
        )
    if short_breakdown_path:
        return "short_breakdown"
    if short_bounce_fail_path:
        return "short_bounce_fail"
    if short_trend_reaccel_ok(context, market_state, params):
        return "short_reaccel"
    return ""


def _long_entry_result(
    context,
    market_state,
    params,
    positions,
    long_breakout_path,
    long_pullback_path,
    long_reaccel_path,
    long_ownership_relay,
    *,
    as_decision,
):
    if not (long_signal_path_ok(long_breakout_path, long_pullback_path, long_reaccel_path) or long_ownership_relay):
        return None
    _record_funnel_pass("long", "path_pass")
    current = context.get("current")
    if isinstance(current, dict):
        real_body = max(abs(current["close"] - current["open"]), 1e-9)
        upper_wick = max(current["high"] - max(current["open"], current["close"]), 0.0)
        if (
            upper_wick / real_body > 2.0
            and current["volume"] > context["recent_volume_avg"] * 1.5
        ):
            return None
    try:
        strong_trend_bypass = _long_strong_trend_bypass(context, market_state, params)
    except (KeyError, TypeError):
        strong_trend_bypass = False
    if not long_final_veto_clear(
        context,
        market_state,
        params,
        long_breakout_path,
        long_pullback_path,
        long_reaccel_path,
        long_ownership_relay,
        strong_trend_bypass=strong_trend_bypass,
    ):
        return None
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

    if as_decision:
        return {
            "entry_signal": "long_pullback",
            "entry_side": "long",
            "entry_path_key": path_key,
            "entry_path_tag": ENTRY_PATH_TAGS.get(path_key, path_key),
        }
    return normalize_entry_signal("long_pullback", fallback_side="long") or None


def _short_entry_result(context, market_state, params, short_path_key, *, as_decision):
    if not short_path_key:
        return None
    short_breakdown_path = short_path_key == "short_breakdown"
    short_bounce_fail_path = short_path_key == "short_bounce_fail"
    short_reaccel_path = short_path_key == "short_reaccel"
    if not long_signal_path_ok(short_breakdown_path, short_bounce_fail_path, short_reaccel_path):
        return None
    _record_funnel_pass("short", "path_pass")
    if not short_final_veto_clear(
        context,
        market_state,
        params,
        short_breakdown_path,
        short_bounce_fail_path,
        short_reaccel_path,
    ):
        return None
    _record_funnel_pass("short", "final_veto_pass")
    if as_decision:
        return {
            "entry_signal": "short_breakdown",
            "entry_side": "short",
            "entry_path_key": short_path_key,
            "entry_path_tag": ENTRY_PATH_TAGS.get(short_path_key, short_path_key),
        }
    return normalize_entry_signal("short_breakdown", fallback_side="short") or None


def _short_entry_signal(data, idx, positions, market_state):
    p = PARAMS
    market_state = _tuned_entry_market_state(data, idx, market_state)
    context = _strategy_entry_context(data, idx, positions, market_state, p)
    if context is None:
        return None

    path_key = _short_entry_path_key(context, market_state, p, require_breakdown_gate=False)
    if not path_key:
        return None
    return normalize_entry_signal(path_key, fallback_side="short") or None


def strategy_decision(data, idx, positions, market_state):
    p = PARAMS
    market_state = _tuned_entry_market_state(data, idx, market_state)
    context = _strategy_entry_context(data, idx, positions, market_state, p, allow_sideways=True)
    if context is None:
        return None
    sideways_regime = bool(context.get("sideways_regime", False))
    exhausted_side = _volume_climax_exhaustion_side(data, idx, positions)

    _record_funnel_pass("long", "sideways_pass")
    _record_funnel_pass("short", "sideways_pass")

    decision = _strategy_exhaustion_short_signal(context, market_state, p, exhausted_side, sideways_regime, as_decision=True)
    if decision is not None:
        return decision
    decision = _strategy_long_signal(context, market_state, positions, p, exhausted_side, as_decision=True)
    if decision is not None:
        return decision
    return _strategy_short_signal(context, market_state, p, exhausted_side, sideways_regime, as_decision=True)


def strategy(data, idx, positions, market_state):
    p = PARAMS
    market_state = _tuned_entry_market_state(data, idx, market_state)
    context = _strategy_entry_context(data, idx, positions, market_state, p, allow_sideways=True)
    if context is None:
        return None
    sideways_regime = bool(context.get("sideways_regime", False))
    exhausted_side = _volume_climax_exhaustion_side(data, idx, positions)

    _record_funnel_pass("long", "sideways_pass")
    _record_funnel_pass("short", "sideways_pass")
    signal = _strategy_exhaustion_short_signal(context, market_state, p, exhausted_side, sideways_regime)
    if signal is not None:
        return signal
    signal = _strategy_long_signal(context, market_state, positions, p, exhausted_side)
    if signal is not None:
        return signal
    return _strategy_short_signal(context, market_state, p, exhausted_side, sideways_regime)
