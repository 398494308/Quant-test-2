#!/usr/bin/env python3
'''极致激进策略：严入场 + 慢止盈 + 慢止损 + 高杠杆。'''

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
 'breakout_adx_min': 21.5,
 'breakout_body_ratio_min': 0.3,
 'breakout_buffer_pct': 0.00015,
 'breakout_close_pos_min': 0.58,
 'breakout_hist_min': 4.0,
 'breakout_lookback': 28,
 'breakout_rsi_max': 69.0,
 'breakout_rsi_min': 50.0,
 'breakout_volume_ratio_min': 1.12,
 'fourh_adx_min': 12.5,
 'fourh_ema_fast': 10,
 'fourh_ema_slow': 34,
 'hourly_adx_min': 19.0,
 'hourly_ema_anchor': 85,
 'hourly_ema_fast': 12,
 'hourly_ema_slow': 50,
 'long_anchor_extension_atr_max': 1.02,
 'long_anchor_extension_spread_max': 2.44,
 'long_base_distance_atr_max': 0.12,
 'long_mid_extension_atr_min': 0.10,
 'long_true_discovery_adx_min': 15.0,
 'intraday_adx_min': 12.5,
  'intraday_ema_fast': 9,
  'intraday_ema_slow': 28,
 'launch_body_ratio_floor': 0.31,
 'launch_coil_preprev_range_max': 0.96,
 'launch_coil_prev_range_max': 0.92,
 'launch_range_ratio_min': 1.02,
 'launch_volume_ratio_floor': 0.96,
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


def _bar_is_valid(bar):
    return (
        bar['open'] > 0
        and bar['close'] > 0
        and bar['volume'] > 0
        and bar['high'] >= bar['low']
    )


def _bar_range(bar):
    return max(bar['high'] - bar['low'], bar['close'] * 1e-9)


def _candle_metrics(bar):
    open_price = bar['open']
    low = bar['low']
    close = bar['close']
    candle_range = _bar_range(bar)
    body = close - open_price
    return {
        'close_pos': (close - low) / candle_range,
        'body_ratio': abs(body) / candle_range,
    }


def _recent_window_stats(data, end_idx, window, price_floor):
    range_total = 0.0
    volume_total = 0.0
    body_ratio_total = 0.0
    for i in range(end_idx - window, end_idx):
        recent_bar = data[i]
        recent_range = _bar_range(recent_bar)
        body = recent_bar['close'] - recent_bar['open']
        range_total += recent_range
        volume_total += recent_bar['volume']
        body_ratio_total += abs(body) / recent_range
    return {
        'range_avg': max(range_total / window, price_floor * 1e-9),
        'volume_avg': max(volume_total / window, 1e-9),
        'body_ratio_avg': body_ratio_total / window,
    }


def _intraday_trend_metrics(market_state):
    ema_fast = market_state['ema_fast']
    ema_slow = market_state['ema_slow']
    prev_ema_slow = market_state['prev_ema_slow']
    trend_base = max(abs(ema_slow), 1e-9)
    return {
        'spread_pct': (ema_fast - ema_slow) / trend_base,
        'slope_pct': (ema_slow - prev_ema_slow) / trend_base,
    }


def _build_signal_context(data, idx, market_state, params):
    current = data[idx]
    prev = data[idx - 1]
    pre_prev = data[idx - 2]
    hourly = market_state['hourly']
    fourh = market_state['four_hour']
    if hourly is None or fourh is None:
        return None
    if not (_bar_is_valid(current) and _bar_is_valid(prev) and _bar_is_valid(pre_prev)):
        return None

    intraday = _intraday_trend_metrics(market_state)
    current_candle = _candle_metrics(current)
    prev_candle = _candle_metrics(prev)
    pre_prev_candle = _candle_metrics(pre_prev)
    avg_volume = max(_avg(data, idx - params['volume_lookback'] + 1, idx, 'volume'), 1e-9)
    breakout_high = _window_max(data, idx - params['breakout_lookback'], idx - 1, 'high')
    prev_breakout_high = _window_max(data, idx - params['breakout_lookback'] - 1, idx - 2, 'high')
    breakdown_low = _window_min(data, idx - params['breakdown_lookback'], idx - 1, 'low')
    prev_breakdown_low = _window_min(data, idx - params['breakdown_lookback'] - 1, idx - 2, 'low')
    atr_ratio = market_state['atr_ratio']
    recent_stats = _recent_window_stats(data, idx, 6, current['close'])

    return {
        'current': current,
        'prev': prev,
        'pre_prev': pre_prev,
        'hourly': hourly,
        'fourh': fourh,
        'intraday': intraday,
        'current_candle': current_candle,
        'prev_candle': prev_candle,
        'pre_prev_candle': pre_prev_candle,
        'volume_ratio': current['volume'] / avg_volume,
        'prev_volume': max(prev['volume'], 1e-9),
        'pre_prev_volume': max(pre_prev['volume'], 1e-9),
        'breakout_high': breakout_high,
        'prev_breakout_high': prev_breakout_high,
        'breakdown_low': breakdown_low,
        'prev_breakdown_low': prev_breakdown_low,
        'atr_ratio': atr_ratio,
        'breakout_distance_pct': (current['close'] - breakout_high) / max(breakout_high, 1e-9),
        'breakout_high_penetration_pct': max((current['high'] - breakout_high) / max(breakout_high, 1e-9), 0.0),
        'prev_breakout_distance_pct': max((prev['close'] - breakout_high) / max(breakout_high, 1e-9), 0.0),
        'prev_breakout_reference_distance_pct': max((prev['close'] - prev_breakout_high) / max(prev_breakout_high, 1e-9), 0.0),
        'prev_breakout_high_penetration_pct': max((prev['high'] - prev_breakout_high) / max(prev_breakout_high, 1e-9), 0.0),
        'breakdown_distance_pct': (breakdown_low - current['close']) / max(breakdown_low, 1e-9),
        'breakdown_low_penetration_pct': (breakdown_low - current['low']) / max(breakdown_low, 1e-9),
        'prev_breakdown_distance_pct': max((breakdown_low - prev['close']) / max(breakdown_low, 1e-9), 0.0),
        'prev_breakdown_reference_distance_pct': max((prev_breakdown_low - prev['close']) / max(prev_breakdown_low, 1e-9), 0.0),
        'prev_breakdown_low_penetration_pct': max((prev_breakdown_low - prev['low']) / max(prev_breakdown_low, 1e-9), 0.0),
        'hourly_fast_extension_pct': (current['close'] - hourly['ema_fast']) / max(current['close'], 1e-9),
        'hourly_anchor_extension_pct': (current['close'] - hourly['ema_anchor']) / max(current['close'], 1e-9),
        'hourly_fast_discount_pct': (hourly['ema_fast'] - current['close']) / max(current['close'], 1e-9),
        'hourly_anchor_discount_pct': (hourly['ema_anchor'] - current['close']) / max(current['close'], 1e-9),
        'current_range': _bar_range(current),
        'prev_range': _bar_range(prev),
        'prev_prev_range': _bar_range(pre_prev),
        'recent_range_avg': recent_stats['range_avg'],
        'recent_volume_avg': recent_stats['volume_avg'],
        'recent_body_ratio_avg': recent_stats['body_ratio_avg'],
    }


def _build_long_trend_state(context, market_state, params):
    current = context['current']
    hourly = context['hourly']
    fourh = context['fourh']
    return {
        'intraday_bull': (
            current['close'] > market_state['ema_fast'] > market_state['ema_slow']
            and market_state['adx'] >= params['intraday_adx_min']
            and market_state['macd_line'] > market_state['signal_line']
        ),
        'hourly_bull': (
            hourly['close'] > hourly['ema_fast'] > hourly['ema_slow']
            and hourly['close'] > hourly['ema_anchor']
            and hourly['macd_line'] > hourly['signal_line']
            and hourly['adx'] >= params['hourly_adx_min']
            and hourly['trend_spread_pct'] > 0.0
            and hourly['ema_slow_slope_pct'] > 0.0
        ),
        'fourh_bull': (
            fourh['close'] > fourh['ema_fast'] > fourh['ema_slow']
            and fourh['macd_line'] > fourh['signal_line']
            and fourh['adx'] >= params['fourh_adx_min']
            and fourh['trend_spread_pct'] > 0.0
            and fourh['ema_slow_slope_pct'] > 0.0
        ),
        'fourh_bull_base': (
            fourh['close'] > fourh['ema_slow']
            and fourh['close'] >= fourh['ema_fast']
            and fourh['macd_line'] > fourh['signal_line']
            and fourh['adx'] >= max(params['fourh_adx_min'] - 1.0, 11.5)
            and fourh['trend_spread_pct'] > 0.0
            and fourh['ema_slow_slope_pct'] > 0.0
        ),
        'fourh_bull_turn': (
            fourh['close'] > fourh['ema_slow']
            and fourh['macd_line'] > fourh['signal_line']
            and fourh['trend_spread_pct'] > 0.0
            and fourh['ema_slow_slope_pct'] > 0.0
            and fourh['adx'] >= max(params['fourh_adx_min'] - 0.8, 11.8)
        ),
    }


def _build_short_trend_state(context, market_state, params):
    current = context['current']
    hourly = context['hourly']
    fourh = context['fourh']
    return {
        'intraday_bear': (
            current['close'] < market_state['ema_fast'] < market_state['ema_slow']
            and market_state['adx'] >= params['intraday_adx_min']
            and market_state['macd_line'] < market_state['signal_line']
        ),
        'hourly_bear': (
            hourly['close'] < hourly['ema_fast'] < hourly['ema_slow']
            and hourly['close'] < hourly['ema_anchor']
            and hourly['macd_line'] < hourly['signal_line']
            and hourly['adx'] >= params['hourly_adx_min']
        ),
        'fourh_bear': (
            fourh['close'] < fourh['ema_slow']
            and fourh['adx'] >= params['fourh_adx_min']
        ),
        'fourh_bear_confirmed': (
            fourh['close'] < fourh['ema_fast'] < fourh['ema_slow']
            and fourh['macd_line'] < fourh['signal_line']
        ),
    }


def _is_sideways_regime(market_state):
    hourly = market_state['hourly']
    fourh = market_state['four_hour']
    intraday = _intraday_trend_metrics(market_state)
    intraday_chop = market_state['chop']
    hourly_chop = hourly['chop']
    atr_ratio = market_state['atr_ratio']
    intraday_spread = abs(intraday['spread_pct'])
    hourly_spread = abs(hourly['trend_spread_pct'])
    fourh_spread = abs(fourh['trend_spread_pct'])
    hourly_slope = abs(hourly['ema_slow_slope_pct'])
    fourh_slope = abs(fourh['ema_slow_slope_pct'])
    adx_soft = hourly['adx'] <= SIDEWAYS_MAX_HOURLY_ADX and fourh['adx'] <= SIDEWAYS_MAX_FOURH_ADX
    aligned_trend = hourly['trend_spread_pct'] * fourh['trend_spread_pct'] > 0.0
    convexity_release = (
        aligned_trend
        and intraday_spread >= atr_ratio * 0.26
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.10, atr_ratio * 0.66)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.50, atr_ratio * 0.48)
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 0.98)
        and hourly_slope >= max(hourly_spread * 0.10, atr_ratio * 0.082)
        and fourh_slope >= max(fourh_spread * 0.18, atr_ratio * 0.028)
        and market_state['adx'] >= 13.5
        and hourly['adx'] >= 18.0
        and fourh['adx'] >= 12.0
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
        and market_state['adx'] >= 13.0
        and hourly['adx'] >= 17.0
        and fourh['adx'] >= 11.8
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
        and market_state['adx'] >= 14.0
        and hourly['adx'] >= 18.5
        and fourh['adx'] >= 12.5
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
    if hard_sideways:
        return True
    if exhausted_drift and not convexity_release:
        return True
    if trend_awakening or fresh_directional_expansion:
        return False

    mixed_trend = (
        hourly['trend_spread_pct'] * fourh['trend_spread_pct'] <= 0.0
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
    if weak_trend and (intraday_chop >= SIDEWAYS_INTRADAY_CHOP_MIN - 1.0 or adx_soft) and not (convexity_release or trend_awakening or fresh_directional_expansion):
        return True

    bull_front_run = (
        hourly['trend_spread_pct'] > 0.0
        and fourh['trend_spread_pct'] > 0.0
        and intraday['spread_pct'] > max(hourly_spread * 1.85, atr_ratio * 1.00)
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


def _trend_quality_ok(market_state, side):
    hourly = market_state['hourly']
    fourh = market_state['four_hour']
    intraday = _intraday_trend_metrics(market_state)
    atr_ratio = market_state['atr_ratio']
    direction = -1.0 if side == 'short' else 1.0

    intraday_spread = direction * intraday['spread_pct']
    hourly_spread = direction * hourly['trend_spread_pct']
    fourh_spread = direction * fourh['trend_spread_pct']
    hourly_slope = direction * hourly['ema_slow_slope_pct']
    fourh_slope = direction * fourh['ema_slow_slope_pct']
    convex_turn_ok = (
        intraday_spread >= atr_ratio * 0.26
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.10, atr_ratio * 0.66)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.50, atr_ratio * 0.48)
        and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 0.98)
        and hourly_slope >= max(hourly_spread * 0.10, atr_ratio * 0.082)
        and fourh_slope >= max(fourh_spread * 0.18, atr_ratio * 0.028)
        and market_state['adx'] >= 13.5
        and hourly['adx'] >= 18.0
        and fourh['adx'] >= 12.0
    )

    confirms = 0
    if market_state['adx'] >= 14.0 and hourly['adx'] >= 18.0:
        confirms += 1
    if intraday_spread >= atr_ratio * 0.30:
        confirms += 1
    if hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.20, atr_ratio * 0.75):
        confirms += 1
    if fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.05, atr_ratio * 0.95):
        confirms += 1
    if hourly_slope >= atr_ratio * 0.07 and fourh_slope >= atr_ratio * 0.035:
        confirms += 1

    if side == 'long':
        hourly_fast_extension = (hourly['close'] - hourly['ema_fast']) / max(hourly['close'], 1e-9)
        hourly_anchor_extension = (hourly['close'] - hourly['ema_anchor']) / max(hourly['close'], 1e-9)
        long_flattening_chase = (
            hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.74, atr_ratio * 1.02)
            and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.26, atr_ratio * 1.06)
            and hourly_fast_extension >= max(atr_ratio * 0.86, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.96)
            and hourly_anchor_extension >= max(atr_ratio * 1.28, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.06)
            and hourly_slope < max(hourly_spread * 0.08, atr_ratio * 0.074)
            and fourh_slope < max(fourh_spread * 0.09, atr_ratio * 0.034)
            and intraday_spread < max(hourly_spread * 0.34, atr_ratio * 0.30)
        )
        if long_flattening_chase and not convex_turn_ok:
            return False

        long_fourh_ownership_ok = (
            fourh_spread >= max(hourly_spread * 0.82, atr_ratio * 0.94)
            and fourh_slope >= atr_ratio * 0.041
            and fourh['adx'] >= 13.8
        )
        long_early_turn_ok = (
            intraday_spread >= atr_ratio * 0.24
            and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.42, atr_ratio * 0.84)
            and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.64, atr_ratio * 0.60)
            and fourh_spread <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.34, atr_ratio * 1.08)
            and fourh_spread >= hourly_spread * 0.48
            and hourly_slope >= atr_ratio * 0.084
            and fourh_slope >= atr_ratio * 0.029
            and market_state['adx'] >= 13.8
            and hourly['adx'] >= 18.5
            and fourh['adx'] >= 12.4
            and hourly_fast_extension <= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.88)
            and hourly_anchor_extension <= max(atr_ratio * 1.18, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.86)
        )
        long_stretched_without_reaccel = (
            hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.66, atr_ratio * 1.00)
            and hourly_fast_extension >= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.90)
            and hourly_anchor_extension >= max(atr_ratio * 1.22, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.96)
            and (
                fourh_spread < max(hourly_spread * 0.80, atr_ratio * 0.92)
                or fourh_slope < atr_ratio * 0.040
                or fourh['adx'] < 13.8
            )
        )
        if long_stretched_without_reaccel and not long_fourh_ownership_ok:
            return False
        long_hourly_led_chase = (
            intraday_spread >= atr_ratio * 0.34
            and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.64, atr_ratio * 0.98)
            and hourly_slope >= atr_ratio * 0.092
            and (
                fourh_spread < max(hourly_spread * 0.80, atr_ratio * 0.94)
                or fourh_slope < atr_ratio * 0.040
                or fourh['adx'] < 13.8
            )
        )
        if long_hourly_led_chase and not long_early_turn_ok:
            return False

        long_mature_without_fourh = (
            hourly_fast_extension >= max(atr_ratio * 0.90, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.04)
            and hourly_anchor_extension >= max(atr_ratio * 1.34, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.18)
            and not long_fourh_ownership_ok
        )
        if long_mature_without_fourh:
            return False

        if long_fourh_ownership_ok:
            return confirms >= 4 or (confirms >= 3 and intraday_spread >= atr_ratio * 0.30)

        return (long_early_turn_ok or convex_turn_ok) and confirms >= 3

    if confirms < 3:
        return False

    hourly_fast_discount = (hourly['ema_fast'] - hourly['close']) / max(hourly['close'], 1e-9)
    hourly_anchor_discount = (hourly['ema_anchor'] - hourly['close']) / max(hourly['close'], 1e-9)
    short_flattening_selloff = (
        hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.70, atr_ratio * 1.00)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 1.04)
        and hourly_fast_discount >= max(atr_ratio * 0.92, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.08)
        and hourly_anchor_discount >= max(atr_ratio * 1.46, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.52)
        and hourly_slope < max(hourly_spread * 0.08, atr_ratio * 0.076)
        and fourh_slope < max(fourh_spread * 0.09, atr_ratio * 0.036)
        and intraday_spread < max(hourly_spread * 0.34, atr_ratio * 0.30)
    )
    if short_flattening_selloff and not convex_turn_ok:
        return False

    short_acceleration_exception = (
        intraday_spread >= atr_ratio * 0.44
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.90, atr_ratio * 1.10)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 1.04)
        and hourly_slope >= atr_ratio * 0.108
        and fourh_slope >= atr_ratio * 0.046
        and market_state['adx'] >= 16.5
        and hourly['adx'] >= 23.0
        and fourh['adx'] >= 16.5
    )

    short_fresh_pressure_exception = (
        intraday_spread >= atr_ratio * 0.40
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.86, atr_ratio * 1.08)
        and fourh_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.14, atr_ratio * 1.06)
        and hourly_slope >= atr_ratio * 0.100
        and fourh_slope >= atr_ratio * 0.047
        and market_state['adx'] >= 16.0
        and hourly['adx'] >= 22.0
        and fourh['adx'] >= 16.0
    )

    short_borderline_fourh_base = (
        intraday_spread >= atr_ratio * 0.36
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.62, atr_ratio * 0.98)
        and hourly_slope >= atr_ratio * 0.088
        and (
            fourh_spread < max(hourly_spread * 0.86, atr_ratio * 1.06)
            or fourh_slope < atr_ratio * 0.047
            or fourh['adx'] < 16.2
        )
    )
    if short_borderline_fourh_base and not short_acceleration_exception:
        return False

    short_mature_hourly_discount = (
        intraday_spread >= atr_ratio * 0.34
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.52, atr_ratio * 0.95)
        and hourly_slope >= atr_ratio * 0.082
        and hourly_fast_discount >= max(atr_ratio * 0.90, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.10)
        and hourly_anchor_discount >= max(atr_ratio * 1.42, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.35)
        and (
            fourh_spread < max(hourly_spread * 0.88, atr_ratio * 1.03)
            or fourh_slope < atr_ratio * 0.046
            or fourh['adx'] < 16.0
        )
    )
    if short_mature_hourly_discount and not short_acceleration_exception:
        return False

    short_discounted_stall = (
        hourly_fast_discount >= max(atr_ratio * 0.96, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.18)
        and hourly_anchor_discount >= max(atr_ratio * 1.52, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.62)
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.48, atr_ratio * 0.93)
        and hourly_slope >= atr_ratio * 0.078
        and (
            intraday_spread < max(hourly_spread * 0.31, atr_ratio * 0.31)
            or market_state['adx'] < 15.5
            or hourly['adx'] < 21.5
        )
        and (
            fourh_spread < max(hourly_spread * 0.92, atr_ratio * 1.04)
            or fourh_slope < atr_ratio * 0.047
            or fourh['adx'] < 16.0
        )
    )
    if short_discounted_stall and not (short_acceleration_exception or short_fresh_pressure_exception):
        return False

    short_fourh_participation_ok = (
        fourh_spread >= max(hourly_spread * 0.82, atr_ratio * 1.02)
        and fourh_slope >= atr_ratio * 0.045
        and fourh['adx'] >= 15.8
    )
    if short_fourh_participation_ok:
        short_discount_override = (
            hourly_fast_discount >= max(atr_ratio * 1.02, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.30)
            and hourly_anchor_discount >= max(atr_ratio * 1.60, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.80)
            and (
                fourh_spread < max(hourly_spread * 0.90, atr_ratio * 1.08)
                or fourh_slope < atr_ratio * 0.048
                or fourh['adx'] < 16.4
            )
        )
        if short_discount_override and not short_acceleration_exception:
            return False
        return True

    short_hourly_led_selloff = (
        intraday_spread >= atr_ratio * 0.34
        and hourly_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.55, atr_ratio * 0.96)
        and (
            fourh_spread < max(hourly_spread * 0.74, atr_ratio * 0.98)
            or fourh_slope < atr_ratio * 0.043
            or fourh['adx'] < 15.0
        )
    )
    if short_hourly_led_selloff:
        return False

    return short_acceleration_exception or short_fresh_pressure_exception


def _trend_followthrough_ok(market_state, side, trigger_price, current_close):
    hourly = market_state['hourly']
    fourh = market_state['four_hour']
    intraday = _intraday_trend_metrics(market_state)
    atr_ratio = market_state['atr_ratio']
    direction = -1.0 if side == 'short' else 1.0
    breakout_distance_pct = abs(current_close - trigger_price) / max(trigger_price, 1e-9)

    confirms = 0
    if direction * intraday['spread_pct'] >= atr_ratio * 0.30:
        confirms += 1
    if direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.35, atr_ratio * 0.85):
        confirms += 1
    if direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.10, atr_ratio * 1.05):
        confirms += 1
    if (
        direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.08
        and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.04
    ):
        confirms += 1
    if breakout_distance_pct >= atr_ratio * 0.35:
        confirms += 1

    if side == 'long':
        hourly_fast_extension = (current_close - hourly['ema_fast']) / max(current_close, 1e-9)
        hourly_anchor_extension = (current_close - hourly['ema_anchor']) / max(current_close, 1e-9)
        long_fourh_relay_strengthening = (
            direction * fourh['trend_spread_pct'] >= max(direction * hourly['trend_spread_pct'] * 0.58, atr_ratio * 0.74)
            and direction * fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.42, atr_ratio * 1.16)
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.034
            and fourh['adx'] >= 13.2
        )
        long_turn_handoff = (
            breakout_distance_pct <= atr_ratio * 0.16
            and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.60, atr_ratio * 0.95)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.76, atr_ratio * 0.70)
            and direction * fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.24, atr_ratio * 1.02)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.090
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.030
            and hourly['adx'] >= 20.0
            and fourh['adx'] >= 12.8
        )
        long_turn_extension_ok = (
            hourly_fast_extension <= max(atr_ratio * 0.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.08)
            and hourly_anchor_extension <= max(atr_ratio * 1.28, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.12)
        )
        long_fourh_ownership_ok = (
            direction * fourh['trend_spread_pct'] >= max(direction * hourly['trend_spread_pct'] * 0.86, atr_ratio * 1.00)
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.045
            and fourh['adx'] >= 14.4
        )
        long_early_acceptance_exception = (
            breakout_distance_pct <= atr_ratio * 0.10
            and direction * intraday['spread_pct'] >= atr_ratio * 0.28
            and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.48, atr_ratio * 0.90)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.74, atr_ratio * 0.68)
            and direction * fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.24, atr_ratio * 1.00)
            and direction * fourh['trend_spread_pct'] >= direction * hourly['trend_spread_pct'] * 0.54
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.088
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.031
            and hourly_fast_extension <= max(atr_ratio * 0.78, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.84)
            and hourly_anchor_extension <= max(atr_ratio * 1.10, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.70)
            and hourly['adx'] >= 19.0
            and fourh['adx'] >= 12.8
        )
        long_takeover_squeeze_exception = (
            breakout_distance_pct <= atr_ratio * 0.08
            and direction * intraday['spread_pct'] >= atr_ratio * 0.26
            and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.54, atr_ratio * 0.92)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.72, atr_ratio * 0.66)
            and direction * fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 0.96)
            and direction * fourh['trend_spread_pct'] >= direction * hourly['trend_spread_pct'] * 0.50
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.090
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.032
            and hourly_fast_extension <= max(atr_ratio * 0.72, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.70)
            and hourly_anchor_extension <= max(atr_ratio * 1.02, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.46)
            and hourly['adx'] >= 19.5
            and fourh['adx'] >= 13.0
        )
        long_compression_launch_exception = (
            breakout_distance_pct <= atr_ratio * 0.09
            and direction * intraday['spread_pct'] >= atr_ratio * 0.24
            and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.46, atr_ratio * 0.88)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.68, atr_ratio * 0.64)
            and direction * fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.12, atr_ratio * 0.94)
            and direction * fourh['trend_spread_pct'] >= max(direction * hourly['trend_spread_pct'] * 0.48, atr_ratio * 0.62)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.086
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.030
            and hourly_fast_extension <= max(atr_ratio * 0.74, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.72)
            and hourly_anchor_extension <= max(atr_ratio * 1.04, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.52)
            and hourly['adx'] >= 18.8
            and fourh['adx'] >= 12.6
        )
        long_continuation_exception = (
            direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.00, atr_ratio * 1.14)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.40, atr_ratio * 1.28)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.108
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.052
            and breakout_distance_pct >= atr_ratio * 0.28
            and hourly['adx'] >= 22.0
            and fourh['adx'] >= 15.0
        )
        long_base_release_exception = (
            breakout_distance_pct >= atr_ratio * 0.12
            and breakout_distance_pct <= atr_ratio * 0.24
            and direction * intraday['spread_pct'] >= atr_ratio * 0.34
            and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.72, atr_ratio * 1.00)
            and direction * fourh['trend_spread_pct'] >= max(direction * hourly['trend_spread_pct'] * 0.72, atr_ratio * 0.84)
            and direction * fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.36, atr_ratio * 1.18)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.096
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.038
            and hourly_fast_extension <= max(atr_ratio * 0.94, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.12)
            and hourly_anchor_extension <= max(atr_ratio * 1.36, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.24)
            and hourly['adx'] >= 20.5
            and fourh['adx'] >= 13.4
        )
        long_reset_release_exception = (
            breakout_distance_pct <= atr_ratio * 0.12
            and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.52, atr_ratio * 0.92)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.72, atr_ratio * 0.68)
            and direction * fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.24, atr_ratio * 1.00)
            and direction * fourh['trend_spread_pct'] >= max(direction * hourly['trend_spread_pct'] * 0.52, atr_ratio * 0.66)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.088
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.030
            and hourly_fast_extension <= max(atr_ratio * 0.76, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.82)
            and hourly_anchor_extension <= max(atr_ratio * 1.12, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.78)
            and hourly['adx'] >= 19.5
            and fourh['adx'] >= 12.6
        )
        long_ignition_followthrough_exception = (
            breakout_distance_pct <= atr_ratio * 0.16
            and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.56, atr_ratio * 0.94)
            and long_fourh_relay_strengthening
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.092
            and hourly_fast_extension <= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.92)
            and hourly_anchor_extension <= max(atr_ratio * 1.18, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.88)
            and hourly['adx'] >= 20.0
        )
        long_underparticipating_chase = (
            breakout_distance_pct >= atr_ratio * 0.12
            and hourly_fast_extension >= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.90)
            and hourly_anchor_extension >= max(atr_ratio * 1.24, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.00)
            and (
                direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.78, atr_ratio * 0.96)
                or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.043
                or fourh['adx'] < 14.0
            )
        )
        if long_underparticipating_chase and not (
            long_continuation_exception
            or long_base_release_exception
            or long_early_acceptance_exception
            or long_takeover_squeeze_exception
        ):
            return False

        long_reset_without_fourh = (
            breakout_distance_pct <= atr_ratio * 0.12
            and hourly_fast_extension <= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.92)
            and hourly_anchor_extension <= max(atr_ratio * 1.18, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.90)
            and (
                direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.50, atr_ratio * 0.66)
                or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.028
                or fourh['adx'] < 12.4
            )
        )
        if long_reset_without_fourh and not (long_continuation_exception or long_early_acceptance_exception or long_takeover_squeeze_exception):
            return False

        long_stale_handoff_without_relay = (
            breakout_distance_pct >= atr_ratio * 0.10
            and breakout_distance_pct <= atr_ratio * 0.22
            and hourly_fast_extension >= max(atr_ratio * 0.80, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.86)
            and hourly_anchor_extension >= max(atr_ratio * 1.20, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.94)
            and (
                direction * intraday['spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.68, atr_ratio * 0.36)
                or not long_fourh_relay_strengthening
            )
        )
        if long_stale_handoff_without_relay and not (long_continuation_exception or long_ignition_followthrough_exception or long_early_acceptance_exception or long_takeover_squeeze_exception):
            return False

        if long_turn_handoff:
            if not long_turn_extension_ok and not (long_continuation_exception or long_ignition_followthrough_exception):
                return False
            if (
                direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.54, atr_ratio * 0.70)
                or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.029
                or fourh['adx'] < 12.8
            ):
                return False
            required_confirms = 3 if breakout_distance_pct <= atr_ratio * 0.10 else 4
            if long_turn_extension_ok:
                confirms += 1
            if long_reset_release_exception or long_ignition_followthrough_exception or long_early_acceptance_exception or long_takeover_squeeze_exception:
                required_confirms = min(required_confirms, 3)
            return confirms >= required_confirms

        if long_reset_release_exception or long_takeover_squeeze_exception or long_compression_launch_exception:
            if hourly_fast_extension <= max(atr_ratio * 0.84, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.96):
                confirms += 1
            if (
                long_ignition_followthrough_exception
                or long_early_acceptance_exception
                or long_takeover_squeeze_exception
                or long_compression_launch_exception
            ):
                return confirms >= 3
            return confirms >= 4

        long_hourly_led_continuation = (
            breakout_distance_pct >= atr_ratio * 0.14
            and hourly_fast_extension >= max(atr_ratio * 0.84, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.92)
            and hourly_anchor_extension >= max(atr_ratio * 1.24, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.98)
            and not long_fourh_ownership_ok
        )
        if long_hourly_led_continuation and not (long_continuation_exception or long_base_release_exception):
            return False

        long_mature_without_fourh_drive = (
            breakout_distance_pct >= atr_ratio * 0.16
            and hourly_fast_extension >= max(atr_ratio * 0.90, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.02)
            and hourly_anchor_extension >= max(atr_ratio * 1.40, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.34)
            and (
                direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.92, atr_ratio * 1.14)
                or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.049
                or fourh['adx'] < 14.8
            )
        )
        if long_mature_without_fourh_drive and not (long_continuation_exception or long_base_release_exception):
            return False

        long_stale_continuation = (
            breakout_distance_pct >= atr_ratio * 0.18
            and breakout_distance_pct <= atr_ratio * 0.32
            and hourly_fast_extension >= max(atr_ratio * 0.86, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.96)
            and hourly_anchor_extension >= max(atr_ratio * 1.30, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.14)
            and (
                direction * intraday['spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.60, atr_ratio * 0.34)
                or direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.98, atr_ratio * 1.18)
                or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.050
                or fourh['adx'] < 15.0
            )
        )
        if long_stale_continuation and not (long_continuation_exception or long_base_release_exception):
            return False

        long_chase_risk = (
            breakout_distance_pct >= atr_ratio * 0.14
            and hourly_fast_extension >= max(atr_ratio * 0.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.94)
            and hourly_anchor_extension >= max(atr_ratio * 1.34, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.24)
            and (
                direction * hourly['trend_spread_pct'] < max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.86, atr_ratio * 1.06)
                or direction * fourh['trend_spread_pct'] < max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.34, atr_ratio * 1.22)
                or direction * hourly['ema_slow_slope_pct'] < atr_ratio * 0.100
                or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.050
            )
        )
        if long_chase_risk and not (long_continuation_exception or long_base_release_exception):
            return False

        long_fourh_underparticipation = (
            direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.88, atr_ratio * 1.10)
            or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.048
            or fourh['adx'] < 14.8
        )
        if long_fourh_underparticipation and not (long_continuation_exception or long_base_release_exception):
            return False

        if (
            hourly_fast_extension <= max(atr_ratio * 1.10, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.54)
            and hourly_anchor_extension <= max(atr_ratio * 1.66, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.92)
        ):
            confirms += 1

        required_confirms = 6 if breakout_distance_pct >= atr_ratio * 0.16 else 5
        if breakout_distance_pct >= atr_ratio * 0.22 and not long_continuation_exception:
            required_confirms += 1
        if long_continuation_exception or long_ignition_followthrough_exception:
            required_confirms -= 1
        if long_base_release_exception:
            required_confirms = min(required_confirms, 5)
        if (
            long_early_acceptance_exception
            or long_takeover_squeeze_exception
            or long_compression_launch_exception
        ) and breakout_distance_pct <= atr_ratio * 0.10:
            required_confirms = min(required_confirms, 4)
        return confirms >= required_confirms

    hourly_fast_discount = (hourly['ema_fast'] - current_close) / max(current_close, 1e-9)
    hourly_anchor_discount = (hourly['ema_anchor'] - current_close) / max(current_close, 1e-9)
    short_fresh_followthrough_exception = (
        direction * intraday['spread_pct'] >= atr_ratio * 0.40
        and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.88, atr_ratio * 1.08)
        and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 1.08)
        and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.104
        and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.048
        and breakout_distance_pct >= atr_ratio * 0.28
        and market_state['adx'] >= 16.0
        and hourly['adx'] >= 22.0
        and fourh['adx'] >= 16.0
    )

    short_discounted_followthrough_stall = (
        breakout_distance_pct >= atr_ratio * 0.18
        and hourly_fast_discount >= max(atr_ratio * 0.96, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.18)
        and hourly_anchor_discount >= max(atr_ratio * 1.54, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.68)
        and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.50, atr_ratio * 0.94)
        and (
            direction * intraday['spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.33, atr_ratio * 0.30)
            or market_state['adx'] < 15.5
        )
        and (
            direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.94, atr_ratio * 1.05)
            or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.047
            or fourh['adx'] < 16.0
        )
    )
    if short_discounted_followthrough_stall and not short_fresh_followthrough_exception:
        return False

    short_fourh_participation_gap = (
        breakout_distance_pct >= atr_ratio * 0.22
        and direction * intraday['spread_pct'] >= atr_ratio * 0.34
        and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.55, atr_ratio * 0.98)
        and (
            direction * fourh['trend_spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.62, atr_ratio * 0.96)
            or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.039
            or fourh['adx'] < 15.0
        )
        and hourly_fast_discount >= max(atr_ratio * 0.92, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.15)
        and hourly_anchor_discount >= max(atr_ratio * 1.48, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.55)
    )
    if short_fourh_participation_gap:
        short_broad_acceleration_exception = (
            direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.95, atr_ratio * 1.12)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.26, atr_ratio * 1.14)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.106
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.048
            and breakout_distance_pct >= atr_ratio * 0.34
            and fourh['adx'] >= 17.0
            and hourly['adx'] >= 23.0
        )
        if not short_broad_acceleration_exception:
            return False

    short_late_drift = (
        breakout_distance_pct >= atr_ratio * 0.32
        and hourly_fast_discount >= max(atr_ratio * 1.20, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.7)
        and hourly_anchor_discount >= max(atr_ratio * 1.90, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 4.4)
        and direction * hourly['ema_slow_slope_pct'] < atr_ratio * 0.095
        and direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.047
    )
    if short_late_drift:
        short_continuation_exception = (
            direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.75, atr_ratio * 1.05)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.28, atr_ratio * 1.18)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.10
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.05
            and breakout_distance_pct >= atr_ratio * 0.42
        )
        if not short_continuation_exception:
            return False

    short_chase_risk = (
        breakout_distance_pct >= atr_ratio * 0.24
        and hourly_fast_discount >= max(atr_ratio * 1.05, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.35)
        and hourly_anchor_discount >= max(atr_ratio * 1.68, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.95)
        and (
            direction * hourly['trend_spread_pct'] < max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.60, atr_ratio * 0.96)
            or direction * fourh['trend_spread_pct'] < max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.16, atr_ratio * 1.08)
            or direction * hourly['ema_slow_slope_pct'] < atr_ratio * 0.092
            or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.046
        )
    )
    if short_chase_risk:
        short_reacceleration_exception = (
            direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.88, atr_ratio * 1.08)
            and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.32, atr_ratio * 1.20)
            and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.106
            and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.053
            and breakout_distance_pct >= atr_ratio * 0.30
        )
        if not short_reacceleration_exception:
            return False

    short_discounted_no_reload = (
        breakout_distance_pct >= atr_ratio * 0.18
        and hourly_fast_discount >= max(atr_ratio * 0.94, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.14)
        and hourly_anchor_discount >= max(atr_ratio * 1.50, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.58)
        and direction * intraday['spread_pct'] < max(direction * hourly['trend_spread_pct'] * 0.36, atr_ratio * 0.34)
        and (
            direction * hourly['trend_spread_pct'] < max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.72, atr_ratio * 1.02)
            or direction * fourh['trend_spread_pct'] < max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.22, atr_ratio * 1.12)
            or direction * hourly['ema_slow_slope_pct'] < atr_ratio * 0.096
            or direction * fourh['ema_slow_slope_pct'] < atr_ratio * 0.046
            or market_state['adx'] < 16.2
            or hourly['adx'] < 22.0
            or fourh['adx'] < 15.8
        )
    )
    if short_discounted_no_reload and not short_fresh_followthrough_exception:
        return False

    short_reload_exception = (
        direction * intraday['spread_pct'] >= max(direction * hourly['trend_spread_pct'] * 0.42, atr_ratio * 0.38)
        and direction * hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.82, atr_ratio * 1.06)
        and direction * fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.24, atr_ratio * 1.14)
        and direction * hourly['ema_slow_slope_pct'] >= atr_ratio * 0.102
        and direction * fourh['ema_slow_slope_pct'] >= atr_ratio * 0.048
        and market_state['adx'] >= 16.0
        and hourly['adx'] >= 22.0
        and fourh['adx'] >= 16.0
    )

    short_extended_move = (
        breakout_distance_pct >= atr_ratio * 0.20
        and hourly_fast_discount >= max(atr_ratio * 0.98, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.22)
        and hourly_anchor_discount >= max(atr_ratio * 1.58, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.72)
    )
    short_mature_extension = (
        breakout_distance_pct >= atr_ratio * 0.18
        and hourly_fast_discount >= max(atr_ratio * 0.94, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.14)
        and hourly_anchor_discount >= max(atr_ratio * 1.50, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.58)
    )
    if short_mature_extension and not (short_fresh_followthrough_exception or short_reload_exception):
        required_confirms = 5
    else:
        required_confirms = 4 if short_extended_move else 3
    return confirms >= required_confirms


def strategy(data, idx, positions, market_state):
    p = PARAMS
    if idx < p['min_history']:
        return None

    context = _build_signal_context(data, idx, market_state, p)
    if context is None:
        return None

    if _is_sideways_regime(market_state):
        return None

    current, prev, pre_prev = context['current'], context['prev'], context['pre_prev']
    hourly, fourh, intraday = context['hourly'], context['fourh'], context['intraday']
    current_candle, prev_candle, pre_prev_candle = (
        context['current_candle'],
        context['prev_candle'],
        context['pre_prev_candle'],
    )
    volume_ratio, prev_volume, pre_prev_volume = (
        context['volume_ratio'],
        context['prev_volume'],
        context['pre_prev_volume'],
    )
    breakout_high, prev_breakout_high = context['breakout_high'], context['prev_breakout_high']
    breakdown_low, prev_breakdown_low = context['breakdown_low'], context['prev_breakdown_low']
    atr_ratio = context['atr_ratio']
    breakout_distance_pct, breakout_high_penetration_pct = (
        context['breakout_distance_pct'],
        context['breakout_high_penetration_pct'],
    )
    prev_breakout_distance_pct, prev_breakout_reference_distance_pct = (
        context['prev_breakout_distance_pct'],
        context['prev_breakout_reference_distance_pct'],
    )
    prev_breakout_high_penetration_pct = context['prev_breakout_high_penetration_pct']
    breakdown_distance_pct, breakdown_low_penetration_pct = (
        context['breakdown_distance_pct'],
        context['breakdown_low_penetration_pct'],
    )
    prev_breakdown_distance_pct, prev_breakdown_reference_distance_pct = (
        context['prev_breakdown_distance_pct'],
        context['prev_breakdown_reference_distance_pct'],
    )
    prev_breakdown_low_penetration_pct = context['prev_breakdown_low_penetration_pct']
    hourly_fast_extension_pct, hourly_anchor_extension_pct = (
        context['hourly_fast_extension_pct'],
        context['hourly_anchor_extension_pct'],
    )
    hourly_fast_discount_pct, hourly_anchor_discount_pct = (
        context['hourly_fast_discount_pct'],
        context['hourly_anchor_discount_pct'],
    )
    current_range, prev_range, prev_prev_range = (
        context['current_range'],
        context['prev_range'],
        context['prev_prev_range'],
    )
    recent_range_avg, recent_volume_avg, recent_body_ratio_avg = (
        context['recent_range_avg'],
        context['recent_volume_avg'],
        context['recent_body_ratio_avg'],
    )

    long_state = _build_long_trend_state(context, market_state, p)
    intraday_bull, hourly_bull, fourh_bull = (
        long_state['intraday_bull'],
        long_state['hourly_bull'],
        long_state['fourh_bull'],
    )
    fourh_bull_base, fourh_bull_turn = (
        long_state['fourh_bull_base'],
        long_state['fourh_bull_turn'],
    )

    if intraday_bull and hourly_bull and fourh_bull_base and _trend_quality_ok(market_state, 'long'):
        long_trend_expansion_ok = (
            hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.55, atr_ratio * 1.00)
            and fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.26, atr_ratio * 1.14)
            and hourly['ema_slow_slope_pct'] >= atr_ratio * 0.094
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.047
            and hourly['adx'] >= max(p['hourly_adx_min'], 20.0)
        )
        long_higher_tf_participation_ok = (
            hourly['trend_spread_pct'] >= intraday['spread_pct'] * 0.56
            and fourh['trend_spread_pct'] >= hourly['trend_spread_pct'] * 0.84
        )
        long_fourh_impulse_ok = (
            fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.36, atr_ratio * 1.22)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.050
            and fourh['adx'] >= max(p['fourh_adx_min'], 15.0)
            and fourh_bull
        )
        long_fourh_continuation_ok = (
            fourh['trend_spread_pct'] >= max(hourly['trend_spread_pct'] * 0.90, atr_ratio * 1.12)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.049
            and fourh['adx'] >= max(p['fourh_adx_min'], 15.0)
        )
        long_fourh_early_relay_ok = (
            fourh_bull_turn
            and hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.60, atr_ratio * 0.94)
            and fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 0.76, atr_ratio * 0.70)
            and fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.22, atr_ratio * 1.02)
            and fourh['trend_spread_pct'] >= hourly['trend_spread_pct'] * 0.52
            and fourh['trend_spread_pct'] <= hourly['trend_spread_pct'] * 0.88
            and hourly['ema_slow_slope_pct'] >= atr_ratio * 0.090
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.030
            and hourly['adx'] >= max(p['hourly_adx_min'], 20.0)
            and fourh['adx'] >= max(p['fourh_adx_min'] - 0.4, 12.8)
        )
        long_fourh_relay_strengthening = (
            fourh_bull_turn
            and fourh['trend_spread_pct'] >= max(hourly['trend_spread_pct'] * 0.56, atr_ratio * 0.72)
            and fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.40, atr_ratio * 1.16)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.034
            and fourh['adx'] >= max(p['fourh_adx_min'] - 0.2, 13.2)
        )
        long_fourh_ownership_takeover_ok = (
            fourh_bull_turn
            and fourh['trend_spread_pct'] >= max(hourly['trend_spread_pct'] * 0.54, atr_ratio * 0.70)
            and fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.26, atr_ratio * 1.04)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.031
            and fourh['adx'] >= max(p['fourh_adx_min'] - 0.4, 12.8)
        )
        long_absorption_exception = (
            hourly['trend_spread_pct'] >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.98, atr_ratio * 1.16)
            and fourh['trend_spread_pct'] >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.40, atr_ratio * 1.28)
            and hourly['ema_slow_slope_pct'] >= atr_ratio * 0.106
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.053
            and hourly['trend_spread_pct'] >= intraday['spread_pct'] * 0.54
            and fourh['trend_spread_pct'] >= hourly['trend_spread_pct'] * 0.84
            and volume_ratio >= max(p['breakout_volume_ratio_min'] + 0.18, 1.30)
            and market_state['adx'] >= max(p['breakout_adx_min'], p['intraday_adx_min'] + 2.5)
            and fourh['adx'] >= max(p['fourh_adx_min'], 15.0)
        )
        long_turn_arrival_breakout = (
            current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.05
            and breakout_distance_pct <= atr_ratio * (0.18 if long_fourh_relay_strengthening else 0.16)
            and breakout_high_penetration_pct >= max(atr_ratio * 0.09, breakout_distance_pct * 1.00)
            and current['close'] > prev['high']
            and current['close'] > prev['close']
            and current_range >= recent_range_avg * 0.94
            and current_candle['close_pos'] >= max(p['breakout_close_pos_min'] + 0.04, 0.62)
            and current_candle['body_ratio'] >= max(p['breakout_body_ratio_min'] + 0.01, 0.31)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 0.92, recent_body_ratio_avg * 0.96)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - (0.06 if long_fourh_relay_strengthening else 0.04), 1.06)
            and current['volume'] >= max(prev_volume * 0.94, recent_volume_avg * 0.96)
            and market_state['adx'] >= max(p['breakout_adx_min'] - 1.0, p['intraday_adx_min'] + 1.0)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 69.0)
            and market_state['histogram'] >= max(p['breakout_hist_min'] - 0.5, 3.5)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.04)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.28, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.08)
            and (
                prev['close'] <= breakout_high * (1.0 + p['breakout_buffer_pct'] * 1.6)
                or prev_breakout_reference_distance_pct < atr_ratio * 0.08
            )
        )
        long_reset_release_breakout = (
            current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.04
            and breakout_distance_pct <= atr_ratio * (0.15 if long_fourh_relay_strengthening else 0.14)
            and breakout_high_penetration_pct >= max(atr_ratio * 0.10, breakout_distance_pct * 1.04)
            and prev_breakout_distance_pct <= atr_ratio * 0.05
            and prev['close'] <= prev['high'] - prev_range * 0.36
            and prev['close'] <= prev['open']
            and prev_range <= recent_range_avg * 1.02
            and prev['volume'] <= max(pre_prev_volume * 1.06, recent_volume_avg * 1.02)
            and current['close'] > prev['high']
            and current['close'] > prev['close']
            and current_range >= max(prev_range * 1.06, recent_range_avg * 0.94)
            and current_candle['close_pos'] >= max(p['breakout_close_pos_min'] + 0.08, 0.70)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.08, recent_body_ratio_avg * 1.00, 0.34)
            and current['volume'] >= max(prev_volume * 1.02, recent_volume_avg * 0.98)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - 0.04, 1.08)
            and market_state['adx'] >= max(p['breakout_adx_min'] - 0.8, p['intraday_adx_min'] + 1.0)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 68.0)
            and market_state['histogram'] >= max(p['breakout_hist_min'] - 0.2, 3.8)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.78, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.84)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.14, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.82)
        )
        long_ignition_breakout = (
            current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.04
            and breakout_distance_pct <= atr_ratio * (0.14 if long_fourh_relay_strengthening else 0.12)
            and breakout_high_penetration_pct >= max(atr_ratio * 0.11, breakout_distance_pct * 1.06)
            and prev_breakout_distance_pct <= atr_ratio * 0.04
            and prev['close'] <= prev['high'] - prev_range * 0.32
            and prev['close'] >= breakout_high * (1.0 - atr_ratio * 0.10)
            and prev['low'] >= market_state['ema_fast'] * (1.0 - atr_ratio * 0.30)
            and prev_range <= recent_range_avg * 0.96
            and prev['volume'] <= max(pre_prev_volume * 1.02, recent_volume_avg * 0.98)
            and current['close'] > prev['high']
            and current['high'] > prev['high']
            and current_range >= max(prev_range * (1.10 if long_fourh_relay_strengthening else 1.14), recent_range_avg * 0.98)
            and current_candle['close_pos'] >= max(prev_candle['close_pos'] + 0.06, 0.70)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.12, recent_body_ratio_avg * 1.02, 0.35)
            and current['volume'] >= max(prev_volume * 1.04, recent_volume_avg * 1.00)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - 0.04, 1.08)
            and market_state['adx'] >= max(p['breakout_adx_min'] - 0.5, p['intraday_adx_min'] + 1.2)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 68.0)
            and market_state['histogram'] >= max(p['breakout_hist_min'] - 0.2, 3.8)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.80, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.88)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.16, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.84)
        )
        long_coiled_handoff_breakout = (
            current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.03
            and breakout_distance_pct <= atr_ratio * (0.13 if long_fourh_relay_strengthening else 0.11)
            and breakout_high_penetration_pct >= max(atr_ratio * 0.10, breakout_distance_pct * 1.08)
            and prev_breakout_distance_pct <= atr_ratio * 0.03
            and prev_range <= recent_range_avg * 0.90
            and prev_prev_range <= recent_range_avg * 0.94
            and prev['high'] >= breakout_high * (1.0 - atr_ratio * 0.08)
            and pre_prev['high'] >= breakout_high * (1.0 - atr_ratio * 0.12)
            and prev['low'] >= market_state['ema_fast'] * (1.0 - atr_ratio * 0.34)
            and pre_prev['low'] >= market_state['ema_fast'] * (1.0 - atr_ratio * 0.44)
            and prev['close'] <= prev['high'] - prev_range * 0.32
            and pre_prev['close'] <= pre_prev['high'] - prev_prev_range * 0.28
            and current['close'] > prev['high']
            and current['high'] > prev['high']
            and current_range >= max(prev_range * 1.12, recent_range_avg * 0.94)
            and current_candle['close_pos'] >= max(prev_candle['close_pos'] + 0.08, 0.72)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.12, pre_prev_candle['body_ratio'] * 1.10, recent_body_ratio_avg * 1.02, 0.34)
            and current['volume'] >= max(prev_volume * 1.04, pre_prev_volume * 1.02, recent_volume_avg * 0.98)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - 0.06, 1.06)
            and market_state['adx'] >= max(p['breakout_adx_min'] - 1.0, p['intraday_adx_min'] + 1.0)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 68.0)
            and market_state['histogram'] >= max(p['breakout_hist_min'] - 0.3, 3.6)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.74, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.76)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.08, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.62)
        )
        long_early_acceptance_breakout = (
            current['high'] >= breakout_high * (1.0 + p['breakout_buffer_pct'] * 1.6)
            and current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'] * 0.5)
            and breakout_distance_pct >= atr_ratio * 0.015
            and breakout_distance_pct <= atr_ratio * 0.09
            and breakout_high_penetration_pct >= max(atr_ratio * 0.10, breakout_distance_pct * 1.50)
            and prev_breakout_distance_pct <= atr_ratio * 0.03
            and prev['high'] >= breakout_high * (1.0 - atr_ratio * 0.06)
            and prev['low'] >= market_state['ema_fast'] * (1.0 - atr_ratio * 0.38)
            and prev_range <= recent_range_avg * 0.92
            and current['close'] > prev['close']
            and current['close'] >= current['open']
            and current_range >= max(prev_range * 1.02, recent_range_avg * 0.88)
            and current_candle['close_pos'] >= max(p['breakout_close_pos_min'] + 0.10, 0.72)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.04, recent_body_ratio_avg * 0.96, 0.28)
            and current['volume'] >= max(prev_volume * 0.98, recent_volume_avg * 0.94)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - 0.10, 1.02)
            and market_state['adx'] >= max(p['breakout_adx_min'] - 1.5, p['intraday_adx_min'] + 0.8)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 67.0)
            and market_state['histogram'] >= max(p['breakout_hist_min'] - 0.8, 3.2)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.74, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.74)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.06, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.58)
        )
        long_takeover_squeeze_breakout = (
            current['high'] >= breakout_high * (1.0 + p['breakout_buffer_pct'] * 1.5)
            and current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'] * 0.35)
            and breakout_distance_pct >= atr_ratio * 0.01
            and breakout_distance_pct <= atr_ratio * 0.075
            and breakout_high_penetration_pct >= max(atr_ratio * 0.09, breakout_distance_pct * 1.55)
            and prev_breakout_distance_pct <= atr_ratio * 0.025
            and prev['high'] >= breakout_high * (1.0 - atr_ratio * 0.06)
            and prev['low'] >= market_state['ema_fast'] * (1.0 - atr_ratio * 0.34)
            and prev_range <= recent_range_avg * 0.90
            and prev['close'] <= prev['high'] - prev_range * 0.24
            and current['close'] > prev['close']
            and current['high'] > prev['high']
            and current['close'] >= current['open']
            and current_range >= max(prev_range * 1.00, recent_range_avg * 0.86)
            and current_candle['close_pos'] >= max(p['breakout_close_pos_min'] + 0.10, 0.72)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.02, recent_body_ratio_avg * 0.94, 0.27)
            and current['volume'] >= max(prev_volume * 0.96, recent_volume_avg * 0.92)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - 0.12, 1.00)
            and market_state['adx'] >= max(p['breakout_adx_min'] - 1.8, p['intraday_adx_min'] + 0.6)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 66.5)
            and market_state['histogram'] >= max(p['breakout_hist_min'] - 1.0, 3.0)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.70, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.66)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.00, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.42)
            and long_fourh_ownership_takeover_ok
            and fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.18, atr_ratio * 0.96)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.032
            and fourh['adx'] >= max(p['fourh_adx_min'] - 0.2, 13.0)
        )
        prev_already_discovered_breakout = (
            prev['close'] >= prev_breakout_high * (1.0 + p['breakout_buffer_pct'])
            and prev_breakout_reference_distance_pct >= atr_ratio * 0.07
            and prev_breakout_high_penetration_pct >= max(atr_ratio * 0.12, prev_breakout_reference_distance_pct * 1.02)
            and prev_candle['close_pos'] >= max(p['breakout_close_pos_min'] - 0.02, 0.62)
            and prev_candle['body_ratio'] >= max(p['breakout_body_ratio_min'], 0.30)
            and prev['volume'] >= max(pre_prev_volume * 0.96, recent_volume_avg * 0.96)
        )
        long_compressed_reclaim_breakout = (
            prev_already_discovered_breakout
            and prev_breakout_reference_distance_pct >= atr_ratio * 0.07
            and prev_breakout_reference_distance_pct <= atr_ratio * 0.16
            and prev['low'] <= breakout_high * (1.0 + atr_ratio * 0.10)
            and prev['close'] <= prev['high'] - prev_range * 0.18
            and current['low'] <= breakout_high * (1.0 + atr_ratio * 0.06)
            and current['low'] >= breakout_high * (1.0 - atr_ratio * 0.10)
            and current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.05
            and breakout_distance_pct <= atr_ratio * 0.16
            and breakout_high_penetration_pct >= max(prev_breakout_high_penetration_pct + atr_ratio * 0.03, atr_ratio * 0.14)
            and current['close'] > prev['high']
            and current['high'] > prev['high']
            and current_range >= max(prev_range * 1.02, recent_range_avg * 0.96)
            and current_candle['close_pos'] >= max(prev_candle['close_pos'] + 0.05, 0.72)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.04, recent_body_ratio_avg * 1.00, 0.34)
            and current['volume'] >= max(prev_volume * 1.00, recent_volume_avg * 0.98)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - 0.06, 1.06)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.84, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.96)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.20, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.94)
            and (long_fourh_relay_strengthening or long_fourh_ownership_takeover_ok)
        )
        long_turn_incremental_breakout = (
            prev_already_discovered_breakout
            and prev_breakout_reference_distance_pct <= atr_ratio * 0.12
            and (
                prev['low'] <= breakout_high * (1.0 + atr_ratio * 0.10)
                or current['low'] <= breakout_high * (1.0 + atr_ratio * 0.06)
            )
            and prev['close'] <= prev['high'] - prev_range * 0.14
            and breakout_distance_pct >= max(prev_breakout_reference_distance_pct + atr_ratio * 0.05, atr_ratio * 0.12)
            and breakout_distance_pct <= atr_ratio * 0.18
            and breakout_high_penetration_pct >= max(prev_breakout_high_penetration_pct + atr_ratio * 0.04, atr_ratio * 0.16)
            and current['high'] > prev['high']
            and current['close'] > prev['close']
            and current_range >= max(prev_range * 1.06, recent_range_avg * 1.00)
            and current_candle['close_pos'] >= max(prev_candle['close_pos'], 0.70)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.05, recent_body_ratio_avg * 1.02, 0.35)
            and current['volume'] >= max(prev_volume * 1.06, recent_volume_avg * 1.04)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.92)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.18, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.88)
        )
        long_continuation_breakout = (
            current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.24
            and breakout_distance_pct <= atr_ratio * 0.34
            and breakout_high_penetration_pct >= max(atr_ratio * 0.28, breakout_distance_pct * 1.20)
            and current['close'] > prev['high']
            and current_range >= recent_range_avg * 1.12
            and current_candle['close_pos'] >= max(p['breakout_close_pos_min'] + 0.20, 0.78)
            and current_candle['body_ratio'] >= max(p['breakout_body_ratio_min'] + 0.14, 0.44)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.04, recent_body_ratio_avg * 1.06)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] + 0.26, 1.40)
            and current['volume'] >= max(prev_volume * 1.08, recent_volume_avg * 1.10)
            and market_state['adx'] >= max(p['breakout_adx_min'] + 0.8, p['intraday_adx_min'] + 2.4)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 67.0)
            and market_state['histogram'] >= max(p['breakout_hist_min'] + 1.6, 5.6)
            and fourh['trend_spread_pct'] >= max(hourly['trend_spread_pct'] * 0.96, atr_ratio * 1.18)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.051
            and fourh['adx'] >= max(p['fourh_adx_min'], 15.2)
        )
        long_base_release_breakout = (
            current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.12
            and breakout_distance_pct <= atr_ratio * 0.24
            and breakout_high_penetration_pct >= max(atr_ratio * 0.20, breakout_distance_pct * 1.10)
            and current['close'] > prev['high']
            and current['high'] > prev['high']
            and current_range >= max(prev_range * 1.06, recent_range_avg * 1.06)
            and current_candle['close_pos'] >= max(p['breakout_close_pos_min'] + 0.16, 0.74)
            and current_candle['body_ratio'] >= max(p['breakout_body_ratio_min'] + 0.08, prev_candle['body_ratio'] * 1.04, recent_body_ratio_avg * 1.04, 0.40)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] + 0.08, 1.20)
            and current['volume'] >= max(prev_volume * 1.02, recent_volume_avg * 1.04)
            and market_state['adx'] >= max(p['breakout_adx_min'], p['intraday_adx_min'] + 1.8)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 68.0)
            and market_state['histogram'] >= max(p['breakout_hist_min'] + 0.4, 4.4)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.94, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.12)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.36, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.24)
            and fourh['trend_spread_pct'] >= max(hourly['trend_spread_pct'] * 0.72, atr_ratio * 0.84)
            and fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.36, atr_ratio * 1.18)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.038
            and fourh['adx'] >= max(p['fourh_adx_min'] - 0.2, 13.4)
        )
        long_mature_breakout_risk = (
            breakout_distance_pct >= atr_ratio * 0.16
            and hourly_fast_extension_pct >= max(atr_ratio * 0.90, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.02)
            and hourly_anchor_extension_pct >= max(atr_ratio * 1.40, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.34)
            and (
                fourh['trend_spread_pct'] < max(hourly['trend_spread_pct'] * 0.92, atr_ratio * 1.14)
                or fourh['ema_slow_slope_pct'] < atr_ratio * 0.049
                or fourh['adx'] < max(p['fourh_adx_min'], 14.8)
            )
        )
        long_chase_breakout = (
            breakout_distance_pct >= atr_ratio * 0.12
            and hourly_fast_extension_pct >= max(atr_ratio * 0.84, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.88)
            and hourly_anchor_extension_pct >= max(atr_ratio * 1.26, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.02)
        )
        long_hourly_led_continuation_risk = (
            breakout_distance_pct >= atr_ratio * 0.12
            and hourly_fast_extension_pct >= max(atr_ratio * 0.82, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.86)
            and hourly_anchor_extension_pct >= max(atr_ratio * 1.22, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.92)
            and (
                fourh['trend_spread_pct'] < max(hourly['trend_spread_pct'] * 0.92, atr_ratio * 1.10)
                or fourh['ema_slow_slope_pct'] < atr_ratio * 0.048
                or fourh['adx'] < max(p['fourh_adx_min'], 14.8)
            )
        )
        long_reacceleration_ok = (
            current_candle['close_pos'] >= max(p['breakout_close_pos_min'] + 0.18, 0.78)
            and current_candle['body_ratio'] >= max(p['breakout_body_ratio_min'] + 0.12, 0.44)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.00, 0.36)
            and breakout_high_penetration_pct >= max(atr_ratio * 0.24, breakout_distance_pct * 1.10)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] + 0.20, 1.36)
            and current['close'] > prev['high']
            and current['volume'] >= prev_volume * 1.04
            and fourh['trend_spread_pct'] >= max(hourly['trend_spread_pct'] * 0.88, atr_ratio * 1.12)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.050
        )
        long_price_discovery_impulse_ok = (
            breakout_high_penetration_pct >= max(atr_ratio * 0.18, breakout_distance_pct * 1.06)
            and current_range >= max(prev_range * 1.05, recent_range_avg * 1.04)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.02, recent_body_ratio_avg * 1.04, 0.38)
            and current_candle['close_pos'] >= max(prev_candle['close_pos'], 0.72)
            and current['volume'] >= max(prev_volume * 1.02, recent_volume_avg * 1.02)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] + 0.02, 1.14)
        )
        long_local_launch_energy_ok = (
            current_range >= max(prev_range * 1.02, recent_range_avg * p['launch_range_ratio_min'])
            and current_candle['body_ratio'] >= max(recent_body_ratio_avg * 1.02, p['launch_body_ratio_floor'])
            and current['volume'] >= max(prev_volume * 0.98, recent_volume_avg * p['launch_volume_ratio_floor'])
        )
        long_two_bar_coil_ok = (
            prev_range <= recent_range_avg * p['launch_coil_prev_range_max']
            and prev_prev_range <= recent_range_avg * p['launch_coil_preprev_range_max']
            and prev['high'] >= breakout_high * (1.0 - atr_ratio * 0.10)
            and pre_prev['high'] >= breakout_high * (1.0 - atr_ratio * 0.14)
            and prev['low'] >= market_state['ema_fast'] * (1.0 - atr_ratio * 0.36)
            and pre_prev['low'] >= market_state['ema_fast'] * (1.0 - atr_ratio * 0.46)
        )
        long_compression_launch_breakout = (
            current['close'] >= breakout_high * (1.0 + p['breakout_buffer_pct'])
            and breakout_distance_pct >= atr_ratio * 0.02
            and breakout_distance_pct <= atr_ratio * 0.10
            and breakout_high_penetration_pct >= max(atr_ratio * 0.11, breakout_distance_pct * 1.28)
            and current['close'] > prev['high']
            and current['high'] > prev['high']
            and long_two_bar_coil_ok
            and long_local_launch_energy_ok
            and current_candle['close_pos'] >= max(prev_candle['close_pos'] + 0.06, 0.70)
            and volume_ratio >= max(p['breakout_volume_ratio_min'] - 0.12, 1.00)
            and market_state['adx'] >= max(p['breakout_adx_min'] - 1.8, p['intraday_adx_min'] + 0.8)
            and p['breakout_rsi_min'] <= market_state['rsi'] <= min(p['breakout_rsi_max'], 66.8)
            and market_state['histogram'] >= max(p['breakout_hist_min'] - 1.0, 3.0)
            and hourly_fast_extension_pct <= max(atr_ratio * 0.72, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.68)
            and hourly_anchor_extension_pct <= max(atr_ratio * 1.02, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.46)
        )
        long_continuation_reaccel_ok = (
            not prev_already_discovered_breakout
            or (
                breakout_distance_pct >= max(prev_breakout_reference_distance_pct + atr_ratio * 0.08, atr_ratio * 0.24)
                and breakout_high_penetration_pct >= max(prev_breakout_high_penetration_pct + atr_ratio * 0.06, atr_ratio * 0.26)
                and current_range >= max(prev_range * 1.10, recent_range_avg * 1.08)
                and current['volume'] >= max(prev_volume * 1.08, recent_volume_avg * 1.08)
                and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.06, 0.46)
            )
        )
        long_reload_without_expansion = (
            prev_already_discovered_breakout
            and breakout_distance_pct >= atr_ratio * 0.10
            and breakout_distance_pct < max(prev_breakout_reference_distance_pct + atr_ratio * 0.08, atr_ratio * 0.20)
            and breakout_high_penetration_pct < max(prev_breakout_high_penetration_pct + atr_ratio * 0.06, atr_ratio * 0.24)
            and current_range < max(prev_range * 1.08, recent_range_avg * 1.06)
            and current['volume'] < max(prev_volume * 1.08, recent_volume_avg * 1.08)
            and (
                fourh['trend_spread_pct'] < max(hourly['trend_spread_pct'] * 0.96, atr_ratio * 1.16)
                or fourh['ema_slow_slope_pct'] < atr_ratio * 0.050
                or fourh['adx'] < max(p['fourh_adx_min'], 15.0)
            )
            and not long_price_discovery_impulse_ok
        )
        long_stale_discovery_risk = (
            prev_already_discovered_breakout
            and (
                breakout_distance_pct < max(prev_breakout_reference_distance_pct + atr_ratio * 0.06, atr_ratio * 0.20)
                or prev_breakout_reference_distance_pct >= atr_ratio * 0.12
            )
            and breakout_high_penetration_pct < max(prev_breakout_high_penetration_pct + atr_ratio * 0.06, atr_ratio * 0.24)
            and current_range <= max(prev_range * 1.06, recent_range_avg * 1.08)
            and current['volume'] <= max(prev_volume * 1.08, recent_volume_avg * 1.12)
            and current_candle['body_ratio'] < max(prev_candle['body_ratio'] * 1.08, 0.48)
            and (
                fourh['trend_spread_pct'] < max(hourly['trend_spread_pct'] * 0.96, atr_ratio * 1.16)
                or fourh['ema_slow_slope_pct'] < atr_ratio * 0.050
                or fourh['adx'] < max(p['fourh_adx_min'], 15.0)
            )
            and not long_price_discovery_impulse_ok
        )
        long_multi_bar_discovery_risk = (
            prev_breakout_reference_distance_pct >= atr_ratio * 0.08
            and breakout_distance_pct <= max(prev_breakout_reference_distance_pct + atr_ratio * 0.05, atr_ratio * 0.18)
            and breakout_high_penetration_pct <= max(prev_breakout_high_penetration_pct + atr_ratio * 0.04, atr_ratio * 0.20)
            and current_range <= max(prev_range * 1.04, recent_range_avg * 1.06)
            and current['volume'] <= max(prev_volume * 1.06, recent_volume_avg * 1.08)
            and current_candle['body_ratio'] <= max(prev_candle['body_ratio'] * 1.06, 0.46)
            and (
                fourh['trend_spread_pct'] < max(hourly['trend_spread_pct'] * 0.98, atr_ratio * 1.16)
                or fourh['ema_slow_slope_pct'] < atr_ratio * 0.050
                or fourh['adx'] < max(p['fourh_adx_min'], 15.0)
            )
            and not long_price_discovery_impulse_ok
        )
        long_rediscovery_without_reset_risk = (
            prev_already_discovered_breakout
            and not long_compressed_reclaim_breakout
            and prev_breakout_reference_distance_pct >= atr_ratio * 0.07
            and breakout_distance_pct <= max(prev_breakout_reference_distance_pct + atr_ratio * 0.08, atr_ratio * 0.18)
            and breakout_high_penetration_pct <= max(prev_breakout_high_penetration_pct + atr_ratio * 0.05, atr_ratio * 0.22)
            and (
                prev['low'] > breakout_high * (1.0 + atr_ratio * 0.12)
                or current['low'] > breakout_high * (1.0 + atr_ratio * 0.10)
                or current_range <= max(prev_range * 1.04, recent_range_avg * 1.02)
                or current['volume'] <= max(prev_volume * 1.04, recent_volume_avg * 1.04)
            )
            and (
                fourh['trend_spread_pct'] < max(hourly['trend_spread_pct'] * 0.94, atr_ratio * 1.14)
                or fourh['ema_slow_slope_pct'] < atr_ratio * 0.049
                or fourh['adx'] < max(p['fourh_adx_min'], 14.8)
            )
            and not long_price_discovery_impulse_ok
        )
        long_low_energy_late_breakout_risk = (
            breakout_distance_pct >= atr_ratio * 0.08
            and not long_compression_launch_breakout
            and current_range < max(prev_range * 1.02, recent_range_avg * 1.02)
            and current['volume'] < max(prev_volume * 0.98, recent_volume_avg * 0.98)
            and current_candle['body_ratio'] < max(recent_body_ratio_avg * 1.04, 0.34)
        )
        long_anchor_reset_zone = (
            breakout_distance_pct <= atr_ratio * p['long_base_distance_atr_max']
            and hourly_fast_extension_pct <= max(atr_ratio * 0.72, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.70)
            and hourly_anchor_extension_pct <= max(
                atr_ratio * p['long_anchor_extension_atr_max'],
                SIDEWAYS_MIN_HOURLY_SPREAD_PCT * p['long_anchor_extension_spread_max'],
            )
            and fourh['trend_spread_pct'] <= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.22, atr_ratio * 1.00)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.030
            and fourh['adx'] >= max(p['fourh_adx_min'] - 0.2, 12.8)
        )
        long_true_discovery_zone = (
            fourh['trend_spread_pct'] >= max(hourly['trend_spread_pct'] * 0.94, atr_ratio * 1.14)
            and fourh['ema_slow_slope_pct'] >= atr_ratio * 0.049
            and fourh['adx'] >= max(p['long_true_discovery_adx_min'], p['fourh_adx_min'])
        )
        long_midstage_no_edge = (
            breakout_distance_pct >= atr_ratio * p['long_mid_extension_atr_min']
            and not long_anchor_reset_zone
            and not long_true_discovery_zone
            and (
                prev_already_discovered_breakout
                or hourly_fast_extension_pct >= max(atr_ratio * 0.78, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.82)
                or hourly_anchor_extension_pct >= max(atr_ratio * 1.14, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.74)
            )
        )
        long_arrival_acceptance_path_ok = long_fourh_ownership_takeover_ok and (long_early_acceptance_breakout or long_takeover_squeeze_breakout)
        long_reset_release_path_ok = long_anchor_reset_zone and long_fourh_early_relay_ok and (
            long_reset_release_breakout or long_ignition_breakout or long_coiled_handoff_breakout
        )
        long_turn_path_ok = long_anchor_reset_zone and long_fourh_early_relay_ok and (
            long_turn_arrival_breakout or long_turn_incremental_breakout or long_coiled_handoff_breakout
        )
        long_compression_launch_path_ok = (
            long_anchor_reset_zone
            and long_fourh_early_relay_ok
            and long_compression_launch_breakout
        )
        long_compressed_reclaim_path_ok = (
            long_anchor_reset_zone
            and (long_fourh_relay_strengthening or long_fourh_ownership_takeover_ok)
            and long_compressed_reclaim_breakout
        )
        long_continuation_path_ok = (
            long_trend_expansion_ok
            and long_higher_tf_participation_ok
            and long_fourh_impulse_ok
            and long_fourh_continuation_ok
            and long_true_discovery_zone
            and long_continuation_breakout
            and long_reacceleration_ok
            and long_price_discovery_impulse_ok
            and long_continuation_reaccel_ok
        )
        long_base_release_path_ok = (
            long_trend_expansion_ok
            and (long_higher_tf_participation_ok or long_fourh_relay_strengthening)
            and long_true_discovery_zone
            and long_base_release_breakout
            and (long_price_discovery_impulse_ok or long_fourh_relay_strengthening)
        )
        if (
            (long_arrival_acceptance_path_ok or long_reset_release_path_ok or long_turn_path_ok or long_compression_launch_path_ok or long_compressed_reclaim_path_ok or long_continuation_path_ok or long_base_release_path_ok or long_absorption_exception)
            and (not long_mature_breakout_risk or long_absorption_exception or long_base_release_path_ok)
            and (not long_chase_breakout or long_reacceleration_ok or long_price_discovery_impulse_ok or long_absorption_exception or long_reset_release_path_ok or long_arrival_acceptance_path_ok or long_compression_launch_path_ok or long_compressed_reclaim_path_ok or long_base_release_path_ok)
            and not long_hourly_led_continuation_risk
            and not long_reload_without_expansion
            and not long_stale_discovery_risk
            and (not long_multi_bar_discovery_risk or long_base_release_path_ok)
            and not long_rediscovery_without_reset_risk
            and not long_midstage_no_edge
            and (
                not long_low_energy_late_breakout_risk
                or long_arrival_acceptance_path_ok
                or long_reset_release_path_ok
                or long_compression_launch_path_ok
            )
            and _trend_followthrough_ok(market_state, 'long', breakout_high, current['close'])
        ):
            return 'long_breakout'

    short_state = _build_short_trend_state(context, market_state, p)
    intraday_bear, hourly_bear = short_state['intraday_bear'], short_state['hourly_bear']
    fourh_bear, fourh_bear_confirmed = short_state['fourh_bear'], short_state['fourh_bear_confirmed']

    if intraday_bear and hourly_bear and fourh_bear and _trend_quality_ok(market_state, 'short'):
        short_trend_expansion_ok = (
            hourly['trend_spread_pct'] <= -max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.28, atr_ratio * 0.82)
            and fourh['trend_spread_pct'] <= -max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.08, atr_ratio * 1.00)
            and hourly['ema_slow_slope_pct'] <= -atr_ratio * 0.075
            and fourh['ema_slow_slope_pct'] <= -atr_ratio * 0.038
        )
        intraday_bear_spread = max(-intraday['spread_pct'], 0.0)
        hourly_bear_spread = max(-hourly['trend_spread_pct'], 0.0)
        fourh_bear_spread = max(-fourh['trend_spread_pct'], 0.0)
        short_higher_tf_participation_ok = (
            hourly_bear_spread >= max(intraday_bear_spread * 0.42, atr_ratio * 0.72)
            and fourh_bear_spread >= max(hourly_bear_spread * 0.64, atr_ratio * 0.92)
        )
        short_participation_exception = (
            hourly_bear_spread >= max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.70, atr_ratio * 1.00)
            and fourh_bear_spread >= max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.30, atr_ratio * 1.16)
            and hourly['ema_slow_slope_pct'] <= -atr_ratio * 0.095
            and fourh['ema_slow_slope_pct'] <= -atr_ratio * 0.048
            and volume_ratio >= max(p['breakdown_volume_ratio_min'], 1.24)
            and market_state['adx'] >= max(p['breakdown_adx_min'], p['intraday_adx_min'] + 2.0)
        )
        short_fourh_structure_ok = (
            fourh_bear_confirmed
            or (
                fourh_bear_spread >= max(hourly_bear_spread * 0.74, atr_ratio * 1.02)
                and fourh['ema_slow_slope_pct'] <= -atr_ratio * 0.044
                and volume_ratio >= max(p['breakdown_volume_ratio_min'], 1.14)
                and breakdown_distance_pct >= atr_ratio * 0.20
            )
            or short_participation_exception
        )
        short_absorption_exception = (
            hourly['trend_spread_pct'] <= -max(SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.72, atr_ratio * 1.02)
            and fourh['trend_spread_pct'] <= -max(SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.24, atr_ratio * 1.15)
            and hourly['ema_slow_slope_pct'] <= -atr_ratio * 0.098
            and fourh['ema_slow_slope_pct'] <= -atr_ratio * 0.049
            and volume_ratio >= max(p['breakdown_volume_ratio_min'], 1.22)
            and market_state['adx'] >= max(p['breakdown_adx_min'], p['intraday_adx_min'] + 2.5)
        )
        short_structure_extension_ok = (
            not (
                breakdown_distance_pct >= atr_ratio * 0.32
                and hourly_fast_discount_pct >= max(atr_ratio * 1.18, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.7)
                and hourly_anchor_discount_pct >= max(atr_ratio * 1.88, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 4.4)
                and hourly['ema_slow_slope_pct'] > -atr_ratio * 0.082
                and fourh['ema_slow_slope_pct'] > -atr_ratio * 0.041
            )
            or short_absorption_exception
        )
        short_chase_breakdown = (
            breakdown_distance_pct >= atr_ratio * 0.22
            and hourly_fast_discount_pct >= max(atr_ratio * 1.00, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.28)
            and hourly_anchor_discount_pct >= max(atr_ratio * 1.62, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.88)
        )
        short_deep_discount = (
            breakdown_distance_pct >= atr_ratio * 0.18
            and hourly_fast_discount_pct >= max(atr_ratio * 0.92, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.10)
            and hourly_anchor_discount_pct >= max(atr_ratio * 1.48, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.60)
        )
        short_discounted_reacceleration_ok = (
            current_candle['close_pos'] <= min(p['breakdown_close_pos_max'] - 0.06, 0.28)
            and current_candle['body_ratio'] >= max(p['breakdown_body_ratio_min'] + 0.08, 0.47)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 0.96, 0.36)
            and volume_ratio >= max(p['breakdown_volume_ratio_min'] + 0.12, 1.22)
            and current['close'] < prev['low']
            and current['volume'] >= prev_volume * 0.96
            and fourh_bear_spread >= max(hourly_bear_spread * 0.72, atr_ratio * 0.98)
            and fourh['ema_slow_slope_pct'] <= -atr_ratio * 0.043
        )
        short_reacceleration_ok = (
            current_candle['close_pos'] <= min(p['breakdown_close_pos_max'] - 0.08, 0.24)
            and current_candle['body_ratio'] >= max(p['breakdown_body_ratio_min'] + 0.06, 0.46)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 0.90, 0.34)
            and volume_ratio >= max(p['breakdown_volume_ratio_min'] + 0.10, 1.20)
            and current['close'] < prev['close']
            and current['volume'] >= prev_volume * 0.92
        )
        short_fresh_discovery_ok = (
            (
                breakdown_distance_pct >= atr_ratio * 0.14
                and breakdown_low_penetration_pct >= max(atr_ratio * 0.24, breakdown_distance_pct * 1.28)
                and current_candle['close_pos'] <= min(p['breakdown_close_pos_max'] - 0.06, 0.28)
                and current_candle['body_ratio'] >= max(p['breakdown_body_ratio_min'] + 0.04, 0.43)
                and current['close'] < prev['low']
                and current['volume'] >= prev_volume * 0.96
            )
            or short_participation_exception
            or (
                fourh_bear_confirmed
                and fourh_bear_spread >= max(hourly_bear_spread * 0.78, atr_ratio * 1.00)
                and fourh['ema_slow_slope_pct'] <= -atr_ratio * 0.044
                and volume_ratio >= max(p['breakdown_volume_ratio_min'] + 0.14, 1.24)
                and current_candle['body_ratio'] >= max(p['breakdown_body_ratio_min'] + 0.06, 0.45)
                and current_candle['close_pos'] <= min(p['breakdown_close_pos_max'] - 0.06, 0.27)
            )
        )
        short_fresh_price_discovery_needed = (
            breakdown_distance_pct < atr_ratio * 0.18
            or breakdown_low_penetration_pct < max(atr_ratio * 0.26, breakdown_distance_pct * 1.30)
            or current_range < recent_range_avg * 1.04
            or current['volume'] < recent_volume_avg * 1.02
        )
        short_price_discovery_impulse_ok = (
            current_range >= recent_range_avg * 1.08
            and current_candle['body_ratio'] >= max(recent_body_ratio_avg * 1.05, p['breakdown_body_ratio_min'] + 0.03)
            and current_candle['close_pos'] <= min(p['breakdown_close_pos_max'] - 0.04, 0.30)
            and current['close'] <= current['low'] + current_range * 0.32
            and current['volume'] >= recent_volume_avg * 1.02
        )
        short_price_discovery_exception = (
            short_participation_exception
            or (
                fourh_bear_confirmed
                and fourh_bear_spread >= max(hourly_bear_spread * 0.78, atr_ratio * 1.00)
                and fourh['ema_slow_slope_pct'] <= -atr_ratio * 0.044
                and breakdown_distance_pct >= atr_ratio * 0.18
                and breakdown_low_penetration_pct >= max(atr_ratio * 0.26, breakdown_distance_pct * 1.30)
                and current_candle['body_ratio'] >= max(p['breakdown_body_ratio_min'] + 0.05, 0.44)
                and volume_ratio >= max(p['breakdown_volume_ratio_min'] + 0.12, 1.22)
            )
        )
        short_marginal_breakdown = (
            breakdown_distance_pct < atr_ratio * 0.16
            or breakdown_low_penetration_pct < atr_ratio * 0.22
        )
        short_extra_confirmation_needed = short_marginal_breakdown or short_deep_discount or not fourh_bear_confirmed
        short_penetration_confirmation_ok = (
            breakdown_distance_pct >= atr_ratio * 0.12
            and breakdown_low_penetration_pct >= max(atr_ratio * 0.18, breakdown_distance_pct * 1.20)
            and current_candle['close_pos'] <= min(p['breakdown_close_pos_max'] - 0.05, 0.30)
            and current_candle['body_ratio'] >= max(p['breakdown_body_ratio_min'] + 0.05, 0.45)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 0.94, 0.34)
            and volume_ratio >= max(p['breakdown_volume_ratio_min'] + 0.08, 1.18)
            and current['close'] < prev['low']
            and current['volume'] >= prev_volume * 0.94
        )
        short_relative_acceleration_needed = (
            not short_participation_exception
            and (
                not fourh_bear_confirmed
                or fourh_bear_spread < max(hourly_bear_spread * 0.82, atr_ratio * 1.00)
                or fourh['ema_slow_slope_pct'] > -atr_ratio * 0.046
            )
        )
        short_relative_acceleration_ok = (
            current_range >= max(prev_range * 1.05, recent_range_avg * 1.04)
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.02, recent_body_ratio_avg * 1.04, 0.44)
            and current_candle['close_pos'] <= min(prev_candle['close_pos'] + 0.02, 0.28)
            and breakdown_distance_pct >= max(prev_breakdown_distance_pct + atr_ratio * 0.04, atr_ratio * 0.14)
            and current['close'] <= current['low'] + current_range * 0.30
            and current['close'] < prev['close']
            and current['volume'] >= max(prev_volume * 0.98, recent_volume_avg * 1.02)
        )
        prev_already_discovered_breakdown = (
            prev['close'] <= prev_breakdown_low * (1.0 - p['breakdown_buffer_pct'])
            and prev_breakdown_reference_distance_pct >= atr_ratio * 0.11
            and prev_breakdown_low_penetration_pct >= max(atr_ratio * 0.20, prev_breakdown_reference_distance_pct * 1.15)
            and prev_candle['close_pos'] <= min(p['breakdown_close_pos_max'] + 0.02, 0.34)
            and prev_candle['body_ratio'] >= max(p['breakdown_body_ratio_min'], 0.36)
            and prev['volume'] >= max(pre_prev_volume * 0.96, recent_volume_avg * 0.98)
        )
        short_incremental_discovery_ok = (
            breakdown_distance_pct >= max(prev_breakdown_reference_distance_pct + atr_ratio * 0.05, atr_ratio * 0.16)
            and breakdown_low_penetration_pct >= max(prev_breakdown_low_penetration_pct + atr_ratio * 0.04, atr_ratio * 0.24)
            and current['low'] < prev['low']
            and current_range >= max(prev_range * 1.04, recent_range_avg * 1.05)
            and current_range >= prev_prev_range * 0.98
            and current_candle['body_ratio'] >= max(prev_candle['body_ratio'] * 1.02, pre_prev_candle['body_ratio'] * 0.98, recent_body_ratio_avg * 1.05, 0.44)
            and current_candle['close_pos'] <= min(prev_candle['close_pos'] + 0.02, 0.28)
            and current['close'] < prev['close']
            and current['volume'] >= max(prev_volume * 1.00, recent_volume_avg * 1.04)
        )
        short_handoff_discount_risk = (
            prev_already_discovered_breakdown
            and hourly_fast_discount_pct >= max(atr_ratio * 0.94, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 2.14)
            and hourly_anchor_discount_pct >= max(atr_ratio * 1.50, SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 3.58)
            and breakdown_distance_pct < max(prev_breakdown_reference_distance_pct + atr_ratio * 0.08, atr_ratio * 0.20)
            and breakdown_low_penetration_pct < max(prev_breakdown_low_penetration_pct + atr_ratio * 0.06, atr_ratio * 0.26)
        )
        short_stale_handoff_decay = (
            prev_already_discovered_breakdown
            and breakdown_distance_pct < max(prev_breakdown_reference_distance_pct + atr_ratio * 0.03, atr_ratio * 0.18)
            and breakdown_low_penetration_pct < max(prev_breakdown_low_penetration_pct + atr_ratio * 0.02, atr_ratio * 0.24)
            and current_range <= max(prev_range * 1.02, recent_range_avg * 1.03)
            and current['volume'] <= max(prev_volume * 1.04, recent_volume_avg * 1.08)
            and current_candle['body_ratio'] < max(prev_candle['body_ratio'] * 1.02, 0.48)
            and (
                not fourh_bear_confirmed
                or fourh_bear_spread < max(hourly_bear_spread * 0.78, atr_ratio * 1.00)
                or fourh['ema_slow_slope_pct'] > -atr_ratio * 0.045
            )
        )
        breakdown_ready = (
            current['close'] <= breakdown_low * (1.0 - p['breakdown_buffer_pct'])
            and breakdown_distance_pct >= atr_ratio * 0.10
            and current['close'] < prev['low']
            and current_candle['close_pos'] <= p['breakdown_close_pos_max']
            and current_candle['body_ratio'] >= p['breakdown_body_ratio_min']
            and volume_ratio >= p['breakdown_volume_ratio_min']
            and current['volume'] >= prev_volume * 0.94
            and market_state['adx'] >= p['breakdown_adx_min']
            and p['breakdown_rsi_min'] <= market_state['rsi'] <= p['breakdown_rsi_max']
            and market_state['histogram'] <= p['breakdown_hist_max']
        )
        short_discount_ok = (
            not short_deep_discount
            or fourh_bear_confirmed
            or short_participation_exception
            or short_discounted_reacceleration_ok
        )
        if (
            short_trend_expansion_ok
            and (short_higher_tf_participation_ok or short_participation_exception)
            and short_fourh_structure_ok
            and short_structure_extension_ok
            and short_discount_ok
            and short_fresh_discovery_ok
            and (not short_fresh_price_discovery_needed or short_price_discovery_impulse_ok or short_price_discovery_exception)
            and (not short_chase_breakdown or short_reacceleration_ok or short_absorption_exception)
            and (not short_extra_confirmation_needed or short_penetration_confirmation_ok or short_participation_exception)
            and (not short_relative_acceleration_needed or short_relative_acceleration_ok)
            and (not prev_already_discovered_breakdown or short_incremental_discovery_ok or short_participation_exception)
            and not short_handoff_discount_risk
            and not short_stale_handoff_decay
            and breakdown_ready
            and _trend_followthrough_ok(market_state, 'short', breakdown_low, current['close'])
        ):
            return 'short_breakdown'

    return None
