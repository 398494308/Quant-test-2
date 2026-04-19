#!/usr/bin/env python3
"""下载并合并激进版策略所需的 BTCUSDT 合约历史数据。"""
import csv
import time
from datetime import datetime
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data/price"
FUNDING_DIR = REPO_ROOT / "data/funding"
API_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_FUNDING_API_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
OKX_FUNDING_API_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
DATA_START_STR = "2023-01-01"
DATA_END_STR = "2026-04-01"
FILE_TAG = f"{DATA_START_STR.replace('-', '')}_{DATA_END_STR.replace('-', '')}"
PRICE_HEADER = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "taker_buy_volume",
    "taker_sell_volume",
]


def _download_klines(symbol, interval, start_str, end_str):
    start_ms = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_str, "%Y-%m-%d").timestamp() * 1000)
    rows = []
    current_start = start_ms

    while current_start < end_ms:
        response = requests.get(
            API_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1500,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            break
        rows.extend(data)
        current_start = int(data[-1][0]) + 1
        time.sleep(0.15)
    return rows


def _read_csv_rows(path):
    with path.open("r", newline="") as handle:
        return list(csv.DictReader(handle))


def _kline_to_row(row):
    volume = float(row[5])
    taker_buy_volume = float(row[9])
    taker_sell_volume = max(volume - taker_buy_volume, 0.0)
    return {
        "timestamp": int(row[0]),
        "open": row[1],
        "high": row[2],
        "low": row[3],
        "close": row[4],
        "volume": row[5],
        "trade_count": int(row[8]),
        "taker_buy_volume": row[9],
        "taker_sell_volume": f"{taker_sell_volume:.12f}".rstrip("0").rstrip(".") or "0",
    }


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(PRICE_HEADER)
        for row in rows:
            writer.writerow(
                [
                    row["timestamp"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    row["trade_count"],
                    row["taker_buy_volume"],
                    row["taker_sell_volume"],
                ]
            )


def _stream_download_rows(path, symbol, interval, start_str, end_str):
    start_ms = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_str, "%Y-%m-%d").timestamp() * 1000)
    current_start = start_ms
    written = 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(PRICE_HEADER)
        while current_start < end_ms:
            response = requests.get(
                API_URL,
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": current_start,
                    "endTime": end_ms,
                    "limit": 1500,
                },
                timeout=20,
            )
            response.raise_for_status()
            rows = response.json()
            if not rows:
                break
            for row in rows:
                normalized = _kline_to_row(row)
                writer.writerow(
                    [
                        normalized["timestamp"],
                        normalized["open"],
                        normalized["high"],
                        normalized["low"],
                        normalized["close"],
                        normalized["volume"],
                        normalized["trade_count"],
                        normalized["taker_buy_volume"],
                        normalized["taker_sell_volume"],
                    ]
                )
                written += 1
            current_start = int(rows[-1][0]) + 1
            time.sleep(0.18)
    print(f"{path}: {written} rows")


def _merge_rows(downloaded_rows, local_rows):
    merged = {}
    for row in downloaded_rows:
        normalized = _kline_to_row(row)
        merged[int(normalized["timestamp"])] = normalized
    for row in local_rows:
        merged[int(row["timestamp"])] = {
            "timestamp": int(row["timestamp"]),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "trade_count": int(float(row.get("trade_count", 0) or 0)),
            "taker_buy_volume": row.get("taker_buy_volume", "0"),
            "taker_sell_volume": row.get("taker_sell_volume", "0"),
        }
    return [merged[key] for key in sorted(merged)]


def build_dataset(interval, start_str=DATA_START_STR, end_str=DATA_END_STR):
    output_name = f"BTCUSDT_futures_{interval}_{FILE_TAG}.csv"
    output_path = DATA_DIR / output_name
    _stream_download_rows(output_path, "BTCUSDT", interval, start_str, end_str)


def _aggregate_rows(rows, bars_per_bucket):
    buckets = []
    current = []
    for row in rows:
        current.append(row)
        if len(current) != bars_per_bucket:
            continue
        volume = sum(float(item["volume"]) for item in current)
        taker_buy_volume = sum(float(item.get("taker_buy_volume", 0.0) or 0.0) for item in current)
        taker_sell_volume = sum(float(item.get("taker_sell_volume", 0.0) or 0.0) for item in current)
        buckets.append(
            {
                "timestamp": int(current[0]["timestamp"]),
                "open": current[0]["open"],
                "high": f"{max(float(item['high']) for item in current):.12f}".rstrip("0").rstrip("."),
                "low": f"{min(float(item['low']) for item in current):.12f}".rstrip("0").rstrip("."),
                "close": current[-1]["close"],
                "volume": f"{volume:.12f}".rstrip("0").rstrip(".") or "0",
                "trade_count": sum(int(float(item.get("trade_count", 0) or 0)) for item in current),
                "taker_buy_volume": f"{taker_buy_volume:.12f}".rstrip("0").rstrip(".") or "0",
                "taker_sell_volume": f"{taker_sell_volume:.12f}".rstrip("0").rstrip(".") or "0",
            }
        )
        current = []
    return buckets


def build_derived_dataset(source_interval, target_interval, *, start_str=DATA_START_STR, end_str=DATA_END_STR):
    source_name = f"BTCUSDT_futures_{source_interval}_{FILE_TAG}.csv"
    target_name = f"BTCUSDT_futures_{target_interval}_{FILE_TAG}.csv"
    source_path = DATA_DIR / source_name
    target_path = DATA_DIR / target_name
    rows = _read_csv_rows(source_path)
    bars_per_bucket = {"1h": 4, "4h": 16}.get(target_interval)
    if bars_per_bucket is None:
        raise ValueError(f"unsupported derived target interval: {target_interval}")
    _write_rows(target_path, _aggregate_rows(rows, bars_per_bucket))
    print(f"{target_path}: derived from {source_name}")


def download_okx_funding(inst_id="BTC-USDT-SWAP", start_str=DATA_START_STR, end_str=DATA_END_STR):
    start_ms = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_str, "%Y-%m-%d").timestamp() * 1000)
    rows_by_ts = {}
    cursor = None

    while True:
        params = {"instId": inst_id, "limit": 100}
        if cursor is not None:
            params["after"] = cursor
        response = requests.get(OKX_FUNDING_API_URL, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", [])
        if not data:
            break

        oldest_ts = None
        for item in data:
            timestamp = int(item["fundingTime"])
            oldest_ts = timestamp if oldest_ts is None else min(oldest_ts, timestamp)
            if start_ms <= timestamp <= end_ms:
                rows_by_ts[timestamp] = {
                    "timestamp": timestamp,
                    "funding_rate": item.get("realizedRate") or item.get("fundingRate") or "0",
                }
        if oldest_ts is None or oldest_ts <= start_ms:
            break
        cursor = oldest_ts
        time.sleep(0.18)

    output_path = FUNDING_DIR / f"OKX_BTC_USDT_SWAP_funding_{FILE_TAG}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "funding_rate"])
        for timestamp in sorted(rows_by_ts):
            writer.writerow([timestamp, rows_by_ts[timestamp]["funding_rate"]])
    print(f"{output_path}: {len(rows_by_ts)} rows")


def download_binance_funding(symbol="BTCUSDT", start_str=DATA_START_STR, end_str=DATA_END_STR):
    start_ms = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_str, "%Y-%m-%d").timestamp() * 1000)
    rows_by_ts = {}
    current_start = start_ms

    while current_start < end_ms:
        response = requests.get(
            BINANCE_FUNDING_API_URL,
            params={
                "symbol": symbol,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            break

        newest_ts = current_start
        for item in data:
            timestamp = int(item["fundingTime"])
            newest_ts = max(newest_ts, timestamp)
            if start_ms <= timestamp <= end_ms:
                rows_by_ts[timestamp] = {
                    "timestamp": timestamp,
                    "funding_rate": item.get("fundingRate") or "0",
                }

        if newest_ts <= current_start:
            break
        current_start = newest_ts + 1
        time.sleep(0.18)

    output_path = FUNDING_DIR / f"{symbol}_futures_funding_{FILE_TAG}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "funding_rate"])
        for timestamp in sorted(rows_by_ts):
            writer.writerow([timestamp, rows_by_ts[timestamp]["funding_rate"]])
    print(f"{output_path}: {len(rows_by_ts)} rows")


def main():
    build_dataset("15m")
    build_derived_dataset("15m", "1h")
    build_derived_dataset("15m", "4h")
    build_dataset("1m")
    download_binance_funding()


if __name__ == "__main__":
    main()
