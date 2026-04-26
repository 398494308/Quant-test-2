#!/usr/bin/env python3
"""Centralized market data paths and OKX-derived flow proxies."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PRICE_DIR = REPO_ROOT / "data" / "price"
FUNDING_DIR = REPO_ROOT / "data" / "funding"
INDEX_DIR = REPO_ROOT / "data" / "index"

DEFAULT_VENUE = "okx"
DEFAULT_INSTRUMENT_ID = "BTC-USDT-SWAP"
DATA_START_STR = "2023-01-01"
DATA_END_STR = "2026-04-21"
SENTIMENT_FILE_NAME = "crypto_fear_greed_daily_20230101_20260420.csv"
FILE_TAG = f"{DATA_START_STR.replace('-', '')}_{DATA_END_STR.replace('-', '')}"

PRICE_HEADER = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_sell_volume",
    "flow_metric_source",
]


@dataclass(frozen=True)
class DefaultMarketDataPaths:
    intraday_15m: Path
    hourly_1h: Path
    fourh_4h: Path
    execution_1m: Path
    funding: Path
    sentiment: Path


def _normalized_instrument_tag(inst_id: str = DEFAULT_INSTRUMENT_ID) -> str:
    return inst_id.strip().lower().replace("-", "_")


def price_filename(
    interval: str,
    *,
    start_str: str = DATA_START_STR,
    end_str: str = DATA_END_STR,
    venue: str = DEFAULT_VENUE,
    inst_id: str = DEFAULT_INSTRUMENT_ID,
) -> str:
    file_tag = f"{start_str.replace('-', '')}_{end_str.replace('-', '')}"
    return f"{venue}_{_normalized_instrument_tag(inst_id)}_{interval}_{file_tag}.csv"


def funding_filename(
    *,
    start_str: str = DATA_START_STR,
    end_str: str = DATA_END_STR,
    venue: str = DEFAULT_VENUE,
    inst_id: str = DEFAULT_INSTRUMENT_ID,
) -> str:
    file_tag = f"{start_str.replace('-', '')}_{end_str.replace('-', '')}"
    return f"{venue}_{_normalized_instrument_tag(inst_id)}_funding_{file_tag}.csv"


def default_market_data_paths(
    *,
    start_str: str = DATA_START_STR,
    end_str: str = DATA_END_STR,
) -> DefaultMarketDataPaths:
    return DefaultMarketDataPaths(
        intraday_15m=PRICE_DIR / price_filename("15m", start_str=start_str, end_str=end_str),
        hourly_1h=PRICE_DIR / price_filename("1h", start_str=start_str, end_str=end_str),
        fourh_4h=PRICE_DIR / price_filename("4h", start_str=start_str, end_str=end_str),
        execution_1m=PRICE_DIR / price_filename("1m", start_str=start_str, end_str=end_str),
        funding=FUNDING_DIR / funding_filename(start_str=start_str, end_str=end_str),
        sentiment=INDEX_DIR / SENTIMENT_FILE_NAME,
    )


def okx_quote_volume_fallback(volume: float, close_price: float) -> float:
    return max(float(volume), 0.0) * max(float(close_price), 0.0)


def okx_flow_proxy(
    *,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
    quote_volume: float,
) -> dict[str, float | str]:
    """Infer directional flow proxies from OKX candle shape.

    OKX public candles expose traded volume and quote turnover, but not raw trade
    count or taker-side splits. We derive a stable directional proxy so the
    existing flow-sensitive strategy can keep working on an OKX-only data stack.
    """
    candle_range = max(float(high_price) - float(low_price), abs(float(close_price)) * 1e-9)
    close_pos = (float(close_price) - float(low_price)) / candle_range
    body_bias = (float(close_price) - float(open_price)) / candle_range
    close_bias = (close_pos - 0.5) * 2.0

    directional_bias = body_bias * 0.60 + close_bias * 0.40
    directional_bias = max(-1.0, min(1.0, directional_bias))
    buy_share = 0.5 + directional_bias * 0.32
    buy_share = min(max(buy_share, 0.08), 0.92)

    volume_value = max(float(volume), 0.0)
    quote_volume_value = max(float(quote_volume), okx_quote_volume_fallback(volume_value, close_price))
    taker_buy_volume = volume_value * buy_share

    return {
        "trade_count": quote_volume_value,
        "taker_buy_volume": taker_buy_volume,
        "taker_sell_volume": max(volume_value - taker_buy_volume, 0.0),
        "flow_metric_source": "okx_candle_proxy",
    }

