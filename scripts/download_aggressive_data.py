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
API_URL = "https://fapi.binance.com/fapi/v1/klines"
LOCAL_FORWARD_15M = REPO_ROOT.parent / "test1/data/price/BTCUSDT_futures_15m_20250601_20260401.csv"
LOCAL_FORWARD_1H = REPO_ROOT.parent / "test1/data/price/BTCUSDT_futures_1h_20250601_20260401.csv"


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


def build_dataset(interval, local_forward_path, start_str="2024-06-01", bridge_end_str="2025-06-01"):
    downloaded = _download_klines("BTCUSDT", interval, start_str, bridge_end_str)
    local_rows = _read_csv_rows(local_forward_path)
    merged_rows = _merge_rows(downloaded, local_rows)
    output_name = f"BTCUSDT_futures_{interval}_20240601_20260401.csv"
    output_path = DATA_DIR / output_name
    _write_rows(output_path, merged_rows)
    print(f"{output_path}: {len(merged_rows)} rows")


def main():
    build_dataset("15m", LOCAL_FORWARD_15M)
    build_dataset("1h", LOCAL_FORWARD_1H)


if __name__ == "__main__":
    main()
