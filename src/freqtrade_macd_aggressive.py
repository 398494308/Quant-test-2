"""Freqtrade IStrategy 适配层：尽量复用主策略的参数与入场逻辑。

这层的目标不是复刻自研回测器的全部出场细节，而是：
1. 复用主策略的单一参数源，避免第二份参数长期漂移。
2. 为 freqtrade / 对比工具提供尽量一致的入场信号形态。
"""

from __future__ import annotations

from datetime import datetime

try:
    from freqtrade.strategy import (
        IStrategy,
        Trade,
        informative,
        stoploss_from_absolute,
        timeframe_to_minutes,
    )
except ImportError:  # pragma: no cover - 允许在无 freqtrade 环境下被对比脚本导入
    class IStrategy:
        pass

    class Trade:
        pass

    def informative(_timeframe):
        def decorator(func):
            return func

        return decorator

    def stoploss_from_absolute(stop_rate, current_rate, is_short=False, leverage=1.0):
        if current_rate == 0:
            return 1.0
        stoploss = 1 - (stop_rate / current_rate)
        if is_short:
            stoploss = -stoploss
        return max(stoploss, 0.0) * leverage

    def timeframe_to_minutes(timeframe):
        if timeframe.endswith("m"):
            return int(timeframe[:-1])
        if timeframe.endswith("h"):
            return int(timeframe[:-1]) * 60
        raise ValueError(f"unsupported timeframe: {timeframe}")

try:
    import talib.abstract as ta
except ImportError:  # pragma: no cover - 对比脚本会显式跳过
    ta = None

import numpy as np
import pandas as pd
from pandas import DataFrame

import backtest_macd_aggressive as backtest_module
import strategy_macd_aggressive as core_strategy


P = core_strategy.PARAMS
E = backtest_module.EXIT_PARAMS


def _require_talib():
    if ta is None:  # pragma: no cover - 运行期依赖检查
        raise ImportError("TA-Lib is required for freqtrade_macd_aggressive")


def _choppiness(dataframe: DataFrame, length: int = 14) -> pd.Series:
    prev_close = dataframe["close"].shift(1)
    true_range = pd.concat(
        [
            (dataframe["high"] - dataframe["low"]).abs(),
            (dataframe["high"] - prev_close).abs(),
            (dataframe["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr_sum = true_range.rolling(length).sum()
    high_window = dataframe["high"].rolling(length).max()
    low_window = dataframe["low"].rolling(length).min()
    price_range = (high_window - low_window).clip(lower=1e-9)
    return 100.0 * np.log10((tr_sum / price_range).clip(lower=1e-9)) / np.log10(length)


def _apply_trend_columns(dataframe: DataFrame, ema_fast: int, ema_slow: int, ema_anchor: int | None = None) -> DataFrame:
    _require_talib()
    frame = dataframe.copy()
    frame["ema_fast"] = ta.EMA(frame, timeperiod=ema_fast)
    frame["ema_slow"] = ta.EMA(frame, timeperiod=ema_slow)
    if ema_anchor is not None:
        frame["ema_anchor"] = ta.EMA(frame, timeperiod=ema_anchor)
    trend_base = frame["ema_slow"].abs().clip(lower=1e-9)
    frame["trend_spread_pct"] = (frame["ema_fast"] - frame["ema_slow"]) / trend_base
    frame["ema_slow_slope_pct"] = (frame["ema_slow"] - frame["ema_slow"].shift(1)) / trend_base
    frame["adx"] = ta.ADX(frame, timeperiod=14)
    frame["chop"] = _choppiness(frame, 14)
    return frame


def _apply_intraday_indicators(dataframe: DataFrame) -> DataFrame:
    frame = _apply_trend_columns(dataframe, P["intraday_ema_fast"], P["intraday_ema_slow"])
    macd = ta.MACD(frame, fastperiod=P["macd_fast"], slowperiod=P["macd_slow"], signalperiod=P["macd_signal"])
    frame["macd_line"] = macd["macd"]
    frame["macd_signal_line"] = macd["macdsignal"]
    frame["histogram"] = macd["macdhist"]
    frame["atr"] = ta.ATR(frame, timeperiod=14)
    frame["atr_ratio"] = frame["atr"] / frame["close"].clip(lower=1e-9)
    frame["rsi"] = ta.RSI(frame, timeperiod=14)
    frame["breakout_high"] = frame["high"].rolling(window=P["breakout_lookback"]).max().shift(1)
    frame["breakdown_low"] = frame["low"].rolling(window=P["breakdown_lookback"]).min().shift(1)
    frame["avg_volume"] = frame["volume"].rolling(window=P["volume_lookback"]).mean()
    frame["volume_ratio"] = frame["volume"] / frame["avg_volume"].clip(lower=1e-9)
    candle_range = (frame["high"] - frame["low"]).clip(lower=1e-9)
    body = frame["close"] - frame["open"]
    frame["body_ratio"] = body.abs() / candle_range
    frame["close_pos"] = (frame["close"] - frame["low"]) / candle_range
    return frame


def _apply_hourly_indicators(dataframe: DataFrame) -> DataFrame:
    frame = _apply_trend_columns(
        dataframe,
        P["hourly_ema_fast"],
        P["hourly_ema_slow"],
        ema_anchor=P["hourly_ema_anchor"],
    )
    macd = ta.MACD(frame, fastperiod=P["macd_fast"], slowperiod=P["macd_slow"], signalperiod=P["macd_signal"])
    frame["macd_line"] = macd["macd"]
    frame["macd_signal"] = macd["macdsignal"]
    frame["histogram"] = macd["macdhist"]
    return frame


def _apply_fourh_indicators(dataframe: DataFrame) -> DataFrame:
    return _apply_trend_columns(dataframe, P["fourh_ema_fast"], P["fourh_ema_slow"])


def _rename_informative(frame: DataFrame, suffix: str, columns: list[str]) -> DataFrame:
    renamed = frame[columns].copy()
    mapping = {column: f"{column}_{suffix}" for column in columns if column != "timestamp"}
    return renamed.rename(columns=mapping)


def _sideways_mask(dataframe: DataFrame) -> pd.Series:
    intraday_spread = dataframe["trend_spread_pct"].abs()
    hourly_spread = dataframe["trend_spread_pct_1h"].abs()
    fourh_spread = dataframe["trend_spread_pct_4h"].abs()
    hourly_slope = dataframe["ema_slow_slope_pct_1h"].abs()
    fourh_slope = dataframe["ema_slow_slope_pct_4h"].abs()
    atr_ratio = dataframe["atr_ratio"]

    signals = (
        ((dataframe["chop"] >= core_strategy.SIDEWAYS_INTRADAY_CHOP_MIN) & (dataframe["chop_1h"] >= core_strategy.SIDEWAYS_HOURLY_CHOP_MIN)).astype(int)
        + ((atr_ratio < core_strategy.SIDEWAYS_MIN_ATR_RATIO) & (dataframe["chop_1h"] >= core_strategy.SIDEWAYS_HOURLY_CHOP_MIN - 1.0)).astype(int)
        + ((hourly_spread < core_strategy.SIDEWAYS_MIN_HOURLY_SPREAD_PCT) & (fourh_spread < core_strategy.SIDEWAYS_MIN_FOURH_SPREAD_PCT)).astype(int)
        + (
            (intraday_spread < atr_ratio * 0.28)
            & (hourly_slope < atr_ratio * 0.08)
            & (fourh_slope < atr_ratio * 0.04)
        ).astype(int)
    )
    return signals >= 2


def _followthrough_mask(dataframe: DataFrame, side: str, trigger_col: str) -> pd.Series:
    direction = -1.0 if side == "short" else 1.0
    atr_ratio = dataframe["atr_ratio"]
    trigger_price = dataframe[trigger_col].clip(lower=1e-9)
    breakout_distance_pct = (dataframe["close"] - trigger_price).abs() / trigger_price

    confirms = (
        (direction * dataframe["trend_spread_pct"] >= atr_ratio * 0.30).astype(int)
        + (direction * dataframe["trend_spread_pct_1h"] >= np.maximum(core_strategy.SIDEWAYS_MIN_HOURLY_SPREAD_PCT * 1.35, atr_ratio * 0.85)).astype(int)
        + (direction * dataframe["trend_spread_pct_4h"] >= np.maximum(core_strategy.SIDEWAYS_MIN_FOURH_SPREAD_PCT * 1.10, atr_ratio * 1.05)).astype(int)
        + (
            (direction * dataframe["ema_slow_slope_pct_1h"] >= atr_ratio * 0.08)
            & (direction * dataframe["ema_slow_slope_pct_4h"] >= atr_ratio * 0.04)
        ).astype(int)
        + (breakout_distance_pct >= atr_ratio * 0.35).astype(int)
    )
    return confirms >= 3


def apply_entry_logic(dataframe: DataFrame) -> DataFrame:
    frame = dataframe.copy()
    frame["enter_long"] = 0
    frame["enter_short"] = 0
    frame["enter_tag"] = None

    sideways = _sideways_mask(frame)

    intraday_bull = (
        (frame["close"] > frame["ema_fast"])
        & (frame["ema_fast"] > frame["ema_slow"])
        & (frame["adx"] >= P["intraday_adx_min"])
        & (frame["macd_line"] > frame["macd_signal_line"])
    )
    hourly_bull = (
        (frame["close_1h"] > frame["ema_fast_1h"])
        & (frame["ema_fast_1h"] > frame["ema_slow_1h"])
        & (frame["close_1h"] > frame["ema_anchor_1h"])
        & (frame["macd_line_1h"] > frame["macd_signal_1h"])
        & (frame["adx_1h"] >= P["hourly_adx_min"])
    )
    fourh_bull = (
        (frame["close_4h"] > frame["ema_fast_4h"])
        & (frame["ema_fast_4h"] > frame["ema_slow_4h"])
        & (frame["adx_4h"] >= P["fourh_adx_min"])
    )
    breakout_ready = (
        (frame["close"] >= frame["breakout_high"] * (1.0 + P["breakout_buffer_pct"]))
        & (frame["close_pos"] >= P["breakout_close_pos_min"])
        & (frame["body_ratio"] >= P["breakout_body_ratio_min"])
        & (frame["volume_ratio"] >= P["breakout_volume_ratio_min"])
        & (frame["adx"] >= P["breakout_adx_min"])
        & (frame["rsi"] >= P["breakout_rsi_min"])
        & (frame["rsi"] <= P["breakout_rsi_max"])
        & (frame["histogram"] >= P["breakout_hist_min"])
    )
    breakout_followthrough = _followthrough_mask(frame, "long", "breakout_high")

    intraday_bear = (
        (frame["close"] < frame["ema_fast"])
        & (frame["ema_fast"] < frame["ema_slow"])
        & (frame["adx"] >= P["intraday_adx_min"])
        & (frame["macd_line"] < frame["macd_signal_line"])
    )
    hourly_bear = (
        (frame["close_1h"] < frame["ema_fast_1h"])
        & (frame["ema_fast_1h"] < frame["ema_slow_1h"])
        & (frame["close_1h"] < frame["ema_anchor_1h"])
        & (frame["macd_line_1h"] < frame["macd_signal_1h"])
        & (frame["adx_1h"] >= P["hourly_adx_min"])
    )
    fourh_bear = (
        (frame["close_4h"] < frame["ema_slow_4h"])
        & (frame["adx_4h"] >= P["fourh_adx_min"])
    )
    breakdown_ready = (
        (frame["close"] <= frame["breakdown_low"] * (1.0 - P["breakdown_buffer_pct"]))
        & (frame["close_pos"] <= P["breakdown_close_pos_max"])
        & (frame["body_ratio"] >= P["breakdown_body_ratio_min"])
        & (frame["volume_ratio"] >= P["breakdown_volume_ratio_min"])
        & (frame["adx"] >= P["breakdown_adx_min"])
        & (frame["rsi"] >= P["breakdown_rsi_min"])
        & (frame["rsi"] <= P["breakdown_rsi_max"])
        & (frame["histogram"] <= P["breakdown_hist_max"])
    )
    breakdown_followthrough = _followthrough_mask(frame, "short", "breakdown_low")

    long_mask = intraday_bull & hourly_bull & fourh_bull & breakout_ready & breakout_followthrough & (~sideways)
    short_mask = intraday_bear & hourly_bear & fourh_bear & breakdown_ready & breakdown_followthrough & (~sideways)

    frame.loc[long_mask, "enter_long"] = 1
    frame.loc[long_mask, "enter_tag"] = "long_breakout"
    frame.loc[short_mask, "enter_short"] = 1
    frame.loc[short_mask, "enter_tag"] = "short_breakdown"
    return frame


def build_signal_frame(df_15m: DataFrame, df_1h: DataFrame, df_4h: DataFrame) -> DataFrame:
    intraday = _apply_intraday_indicators(df_15m.sort_values("timestamp").reset_index(drop=True))
    hourly = _apply_hourly_indicators(df_1h.sort_values("timestamp").reset_index(drop=True))
    fourh = _apply_fourh_indicators(df_4h.sort_values("timestamp").reset_index(drop=True))

    merged = pd.merge_asof(
        intraday,
        _rename_informative(
            hourly,
            "1h",
            [
                "timestamp",
                "close",
                "ema_fast",
                "ema_slow",
                "ema_anchor",
                "macd_line",
                "macd_signal",
                "histogram",
                "adx",
                "trend_spread_pct",
                "ema_slow_slope_pct",
                "chop",
            ],
        ),
        on="timestamp",
        direction="backward",
    )
    merged = pd.merge_asof(
        merged,
        _rename_informative(
            fourh,
            "4h",
            ["timestamp", "close", "ema_fast", "ema_slow", "adx", "trend_spread_pct", "ema_slow_slope_pct"],
        ),
        on="timestamp",
        direction="backward",
    )
    return apply_entry_logic(merged)


def _trade_entry_tag(trade: Trade) -> str:
    entry_tag = getattr(trade, "enter_tag", None) or getattr(trade, "buy_tag", None)
    if entry_tag in {"long_breakout", "short_breakdown"}:
        return entry_tag
    return "short_breakdown" if getattr(trade, "is_short", False) else "long_breakout"


def _trade_side(trade: Trade) -> str:
    return "short" if getattr(trade, "is_short", False) else "long"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _row_to_market_state(row: pd.Series, prev_row: pd.Series | None = None) -> dict:
    prev = prev_row if prev_row is not None else row
    return {
        "hourly": {
            "close": _safe_float(row.get("close_1h")),
            "ema_fast": _safe_float(row.get("ema_fast_1h")),
            "ema_slow": _safe_float(row.get("ema_slow_1h")),
            "histogram": _safe_float(row.get("histogram_1h")),
            "macd_line": _safe_float(row.get("macd_line_1h")),
            "signal_line": _safe_float(row.get("macd_signal_1h")),
            "adx": _safe_float(row.get("adx_1h")),
            "trend_spread_pct": _safe_float(row.get("trend_spread_pct_1h")),
            "ema_slow_slope_pct": _safe_float(row.get("ema_slow_slope_pct_1h")),
            "chop": _safe_float(row.get("chop_1h")),
        },
        "prev_hourly": {
            "close": _safe_float(prev.get("close_1h")),
            "ema_fast": _safe_float(prev.get("ema_fast_1h")),
            "ema_slow": _safe_float(prev.get("ema_slow_1h")),
            "histogram": _safe_float(prev.get("histogram_1h")),
            "macd_line": _safe_float(prev.get("macd_line_1h")),
            "signal_line": _safe_float(prev.get("macd_signal_1h")),
            "adx": _safe_float(prev.get("adx_1h")),
            "trend_spread_pct": _safe_float(prev.get("trend_spread_pct_1h")),
            "ema_slow_slope_pct": _safe_float(prev.get("ema_slow_slope_pct_1h")),
            "chop": _safe_float(prev.get("chop_1h")),
        },
        "four_hour": {
            "close": _safe_float(row.get("close_4h")),
            "ema_fast": _safe_float(row.get("ema_fast_4h")),
            "ema_slow": _safe_float(row.get("ema_slow_4h")),
            "adx": _safe_float(row.get("adx_4h")),
            "trend_spread_pct": _safe_float(row.get("trend_spread_pct_4h")),
            "ema_slow_slope_pct": _safe_float(row.get("ema_slow_slope_pct_4h")),
        },
        "ema_fast": _safe_float(row.get("ema_fast")),
        "ema_slow": _safe_float(row.get("ema_slow")),
        "prev_ema_fast": _safe_float(prev.get("ema_fast")),
        "prev_ema_slow": _safe_float(prev.get("ema_slow")),
        "adx": _safe_float(row.get("adx")),
        "atr": _safe_float(row.get("atr")),
        "atr_ratio": _safe_float(row.get("atr_ratio")),
        "rsi": _safe_float(row.get("rsi")),
        "chop": _safe_float(row.get("chop")),
        "macd_line": _safe_float(row.get("macd_line")),
        "signal_line": _safe_float(row.get("macd_signal_line")),
        "histogram": _safe_float(row.get("histogram")),
        "prev_histogram": _safe_float(prev.get("histogram")),
    }


class MacdAggressiveStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    minimal_roi = {"0": 999}
    stoploss = -E["stop_max_loss_pct"] / 100.0 / max(E["leverage"], 1)
    leverage_value = E["leverage"]
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    use_custom_stoploss = True
    position_adjustment_enable = True
    max_entry_position_adjustment = max(0, int(E.get("pyramid_max_times", 0)))

    macd_fast = P["macd_fast"]
    macd_slow = P["macd_slow"]
    macd_signal = P["macd_signal"]
    intraday_ema_fast = P["intraday_ema_fast"]
    intraday_ema_slow = P["intraday_ema_slow"]
    hourly_ema_fast = P["hourly_ema_fast"]
    hourly_ema_slow = P["hourly_ema_slow"]
    hourly_ema_anchor = P["hourly_ema_anchor"]
    fourh_ema_fast = P["fourh_ema_fast"]
    fourh_ema_slow = P["fourh_ema_slow"]
    intraday_adx_min = P["intraday_adx_min"]
    hourly_adx_min = P["hourly_adx_min"]
    fourh_adx_min = P["fourh_adx_min"]
    breakout_lookback = P["breakout_lookback"]
    breakdown_lookback = P["breakdown_lookback"]
    breakout_rsi_min = P["breakout_rsi_min"]
    breakout_rsi_max = P["breakout_rsi_max"]
    breakdown_rsi_min = P["breakdown_rsi_min"]
    breakdown_rsi_max = P["breakdown_rsi_max"]
    breakout_adx_min = P["breakout_adx_min"]
    breakdown_adx_min = P["breakdown_adx_min"]
    breakout_volume_ratio_min = P["breakout_volume_ratio_min"]
    breakdown_volume_ratio_min = P["breakdown_volume_ratio_min"]
    breakout_body_ratio_min = P["breakout_body_ratio_min"]
    breakdown_body_ratio_min = P["breakdown_body_ratio_min"]
    breakout_close_pos_min = P["breakout_close_pos_min"]
    breakdown_close_pos_max = P["breakdown_close_pos_max"]
    breakout_hist_min = P["breakout_hist_min"]
    breakdown_hist_max = P["breakdown_hist_max"]
    breakout_buffer_pct = P["breakout_buffer_pct"]
    breakdown_buffer_pct = P["breakdown_buffer_pct"]
    volume_lookback = P["volume_lookback"]
    min_history = P["min_history"]

    trailing_stop = False

    def _get_pair_context(self, pair: str, current_time: datetime) -> tuple[pd.Series | None, pd.Series | None]:
        if getattr(self, "dp", None) is None:
            return None, None
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            return None, None
        frame = dataframe
        if "date" in frame.columns:
            eligible = frame.loc[frame["date"] <= current_time]
            if not eligible.empty:
                frame = eligible
        row = frame.iloc[-1]
        prev_row = frame.iloc[-2] if len(frame) >= 2 else row
        return row, prev_row

    def _entry_signal_from_trade(self, trade: Trade) -> str:
        return _trade_entry_tag(trade)

    def _sync_trade_runtime_state(self, trade: Trade, current_profit: float, current_rate: float) -> tuple[float, float]:
        peak_profit = max(_safe_float(trade.get_custom_data("peak_profit", current_profit), current_profit), current_profit)
        favorable_rate = _safe_float(trade.get_custom_data("favorable_rate", trade.open_rate), trade.open_rate)
        if getattr(trade, "is_short", False):
            favorable_rate = min(favorable_rate, current_rate)
        else:
            favorable_rate = max(favorable_rate, current_rate)
        trade.set_custom_data("peak_profit", peak_profit)
        trade.set_custom_data("favorable_rate", favorable_rate)
        return peak_profit, favorable_rate

    def _hold_bars(self, trade: Trade, current_time: datetime) -> int:
        delta_minutes = max(0.0, (current_time - trade.open_date_utc).total_seconds() / 60.0)
        return int(delta_minutes // timeframe_to_minutes(self.timeframe))

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return min(self.leverage_value, max_leverage)

    def informative_pairs(self):
        return [
            ("BTC/USDT:USDT", "1h"),
            ("BTC/USDT:USDT", "4h"),
        ]

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return _apply_hourly_indicators(dataframe)

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return _apply_fourh_indicators(dataframe)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return _apply_intraday_indicators(dataframe)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return apply_entry_logic(dataframe)

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, "exit_long"] = 0
        dataframe.loc[:, "exit_short"] = 0
        return dataframe

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        row, prev_row = self._get_pair_context(pair, current_time)
        if row is None:
            return proposed_stake
        market_state = _row_to_market_state(row, prev_row)
        risk_profile = backtest_module._market_risk_profile(market_state, E)
        target_stake = min(proposed_stake, max_stake) * risk_profile["position_fraction_scale"]
        if min_stake is not None:
            target_stake = max(target_stake, min_stake)
        return min(target_stake, max_stake)

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        row, prev_row = self._get_pair_context(pair, current_time)
        if row is None:
            return None

        market_state = _row_to_market_state(row, prev_row)
        atr_value = market_state["atr"]
        if atr_value <= 0:
            return None

        entry_signal = self._entry_signal_from_trade(trade)
        leverage = float(getattr(trade, "leverage", 0.0) or self.leverage_value)
        side = _trade_side(trade)
        peak_profit, favorable_rate = self._sync_trade_runtime_state(trade, current_profit, current_rate)
        peak_profit_pct = peak_profit * 100.0

        stop_mult = float(backtest_module._exit_value(E, {"entry_signal": entry_signal}, "stop_atr_mult"))
        hard_loss_pct = float(E["stop_max_loss_pct"]) / leverage / 100.0
        if side == "short":
            atr_stop = trade.open_rate + atr_value * stop_mult
            hard_stop = trade.open_rate * (1.0 + hard_loss_pct)
            stop_price = min(atr_stop, hard_stop)
        else:
            atr_stop = trade.open_rate - atr_value * stop_mult
            hard_stop = trade.open_rate * (1.0 - hard_loss_pct)
            stop_price = max(atr_stop, hard_stop)

        break_even_activation_pct = float(backtest_module._exit_value(E, {"entry_signal": entry_signal}, "break_even_activation_pct"))
        if peak_profit_pct >= break_even_activation_pct:
            break_even_buffer = float(E["break_even_buffer_pct"]) / 100.0
            if side == "short":
                stop_price = min(stop_price, trade.open_rate * (1.0 - break_even_buffer))
            else:
                stop_price = max(stop_price, trade.open_rate * (1.0 + break_even_buffer))

        trailing_activation_pct = float(backtest_module._exit_value(E, {"entry_signal": entry_signal}, "trailing_activation_pct"))
        trailing_giveback_pct = float(backtest_module._exit_value(E, {"entry_signal": entry_signal}, "trailing_giveback_pct"))
        if peak_profit_pct >= trailing_activation_pct:
            trailing_gap = trailing_giveback_pct / leverage / 100.0
            if side == "short":
                stop_price = min(stop_price, favorable_rate * (1.0 + trailing_gap))
            else:
                stop_price = max(stop_price, favorable_rate * (1.0 - trailing_gap))

        return stoploss_from_absolute(
            stop_price,
            current_rate=current_rate,
            is_short=getattr(trade, "is_short", False),
            leverage=leverage,
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | bool | None:
        row, prev_row = self._get_pair_context(pair, current_time)
        if row is None:
            return None

        entry_signal = self._entry_signal_from_trade(trade)
        market_state = _row_to_market_state(row, prev_row)
        pseudo_position = {"entry_signal": entry_signal}
        close_pnl_pct = current_profit * 100.0
        trailing_activation_pct = float(backtest_module._exit_value(E, pseudo_position, "trailing_activation_pct"))

        if int(E.get("regime_exit_enabled", 0)) > 0:
            regime_broken = backtest_module._confirmed_regime_break(
                pseudo_position,
                E,
                {"close": _safe_float(row.get("close"))},
                {"close": _safe_float(prev_row.get("close"))} if prev_row is not None else None,
                market_state,
            )
            if regime_broken and close_pnl_pct < trailing_activation_pct:
                return "regime_break"

        side = _trade_side(trade)
        if side == "long" and int(_safe_float(row.get("enter_short"))) > 0:
            return "reverse_signal"
        if side == "short" and int(_safe_float(row.get("enter_long"))) > 0:
            return "reverse_signal"

        hold_limit = backtest_module._resolve_hold_limit(pseudo_position, E, market_state, close_pnl_pct)
        if self._hold_bars(trade, current_time) >= hold_limit:
            return "time_exit"

        return None

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None | tuple[float | None, str | None]:
        row, prev_row = self._get_pair_context(trade.pair, current_time)
        if row is None:
            return None

        entry_signal = self._entry_signal_from_trade(trade)
        pseudo_position = {"entry_signal": entry_signal}
        market_state = _row_to_market_state(row, prev_row)
        close_pnl_pct = current_entry_profit * 100.0

        tp1_done = bool(trade.get_custom_data("tp1_done", False))
        tp1_pnl_pct = float(backtest_module._exit_value(E, pseudo_position, "tp1_pnl_pct"))
        tp1_close_fraction = float(backtest_module._exit_value(E, pseudo_position, "tp1_close_fraction"))
        if (
            not tp1_done
            and tp1_close_fraction > 0.0
            and current_exit_profit * 100.0 >= tp1_pnl_pct
            and trade.stake_amount > 0.0
        ):
            return (-trade.stake_amount * tp1_close_fraction, "tp1")

        risk_profile = backtest_module._market_risk_profile(market_state, E)
        pyramids_done = max(0, int(getattr(trade, "nr_of_successful_entries", 1)) - 1)
        side = _trade_side(trade)
        pyramid_allowed = (
            risk_profile["allow_pyramid"]
            and int(E.get("pyramid_enabled", 0)) > 0
            and pyramids_done < int(E.get("pyramid_max_times", 0))
            and entry_signal in {"long_breakout", "short_breakdown"}
            and close_pnl_pct >= float(E.get("pyramid_trigger_pnl", 20.0))
            and market_state["adx"] >= float(E.get("pyramid_adx_min", 30.0))
            and (
                market_state["macd_line"] > market_state["signal_line"]
                if side == "long"
                else market_state["macd_line"] < market_state["signal_line"]
            )
            and (
                market_state["hourly"]["close"] > market_state["hourly"]["ema_fast"]
                if side == "long"
                else market_state["hourly"]["close"] < market_state["hourly"]["ema_fast"]
            )
        )
        if not pyramid_allowed:
            return None

        add_ratio = float(E.get("pyramid_size_ratio", 0.5))
        additional_stake = min(max_stake, trade.stake_amount * add_ratio)
        if min_stake is not None and additional_stake < min_stake:
            return None
        if additional_stake <= 0.0:
            return None
        return (additional_stake, "pyramid")

    def order_filled(
        self,
        pair: str,
        trade: Trade,
        order,
        current_time: datetime,
        **kwargs,
    ) -> None:
        tag = getattr(order, "ft_order_tag", "") or ""
        side = getattr(order, "ft_order_side", "")
        if side == getattr(trade, "entry_side", ""):
            # Initialize runtime state on first fill and keep peak/favorable metrics
            # aligned with the live trade object after additional entries.
            if trade.get_custom_data("peak_profit", None) is None:
                trade.set_custom_data("peak_profit", 0.0)
            if trade.get_custom_data("favorable_rate", None) is None:
                trade.set_custom_data("favorable_rate", trade.open_rate)
        elif side == getattr(trade, "exit_side", "") and tag == "tp1":
            trade.set_custom_data("tp1_done", True)
