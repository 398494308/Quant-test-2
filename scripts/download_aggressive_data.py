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
OKX_FUNDING_API_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
DATA_START_STR = "2023-01-01"
DATA_END_STR = "2026-04-01"
FILE_TAG = f"{DATA_START_STR.replace('-', '')}_{DATA_END_STR.replace('-', '')}"


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


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for row in rows:
            writer.writerow([row["timestamp"], row["open"], row["high"], row["low"], row["close"], row["volume"]])


def _stream_download_rows(path, symbol, interval, start_str, end_str):
    start_ms = int(datetime.strptime(start_str, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_str, "%Y-%m-%d").timestamp() * 1000)
    current_start = start_ms
    written = 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
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
                writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5]])
                written += 1
            current_start = int(rows[-1][0]) + 1
            time.sleep(0.18)
    print(f"{path}: {written} rows")


def _merge_rows(downloaded_rows, local_rows):
    merged = {}
    for row in downloaded_rows:
        merged[int(row[0])] = {
            "timestamp": int(row[0]),
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5],
        }
    for row in local_rows:
        merged[int(row["timestamp"])] = {
            "timestamp": int(row["timestamp"]),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
    return [merged[key] for key in sorted(merged)]


def build_dataset(interval, start_str=DATA_START_STR, end_str=DATA_END_STR):
    output_name = f"BTCUSDT_futures_{interval}_{FILE_TAG}.csv"
    output_path = DATA_DIR / output_name
    _stream_download_rows(output_path, "BTCUSDT", interval, start_str, end_str)


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


def main():
    build_dataset("15m")
    build_dataset("1h")
    build_dataset("1m")
    download_okx_funding()


if __name__ == "__main__":
    main()
