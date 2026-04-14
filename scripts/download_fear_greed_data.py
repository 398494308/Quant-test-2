#!/usr/bin/env python3
"""下载 Fear & Greed Index 历史数据到 test2。"""
import csv
from datetime import UTC, datetime
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
OUTPUT_FILE = REPO_ROOT / "data/index/crypto_fear_greed_daily_20230101_20260401.csv"
API_URL = "https://api.alternative.me/fng/"


def _to_timestamp_ms(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def main(start_date="2023-01-01", end_date="2026-04-01"):
    response = requests.get(API_URL, params={"limit": 0, "format": "json"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    rows = []
    start_ts = _to_timestamp_ms(start_date)
    end_ts = _to_timestamp_ms(end_date)

    for item in payload.get("data", []):
        ts_ms = int(item["timestamp"]) * 1000
        if ts_ms < start_ts or ts_ms > end_ts:
            continue
        rows.append(
            {
                "timestamp": ts_ms,
                "value": int(item["value"]),
                "classification": item["value_classification"],
            }
        )

    rows.sort(key=lambda row: row["timestamp"])
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "value", "classification"])
        for row in rows:
            writer.writerow([row["timestamp"], row["value"], row["classification"]])
    print(f"{OUTPUT_FILE}: {len(rows)} rows")


if __name__ == "__main__":
    main()
