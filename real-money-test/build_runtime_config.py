#!/usr/bin/env python3
"""Build a local freqtrade runtime config for test2 live testing."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
SRC_DIR = REPO_ROOT / "src"
DEFAULT_BASE_CONFIG = BASE_DIR / "config.base.json"
DEFAULT_SOURCE_CONFIG = REPO_ROOT.parent / "test1" / "user_data" / "config.json"
DEFAULT_OUTPUT = BASE_DIR / "runtime" / "config.runtime.json"
DEFAULT_USER_DATA_DIR = BASE_DIR / "user_data"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import backtest_macd_aggressive as backtest_module


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _deep_merge(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = deepcopy(value)
    return dst


def _copy_exchange_credentials(runtime_config: dict, source_config: dict) -> None:
    source_exchange = source_config.get("exchange", {})
    target_exchange = runtime_config.setdefault("exchange", {})
    required_keys = ("key", "secret", "password")
    missing = [key for key in required_keys if not source_exchange.get(key)]
    if missing:
        missing_text = ", ".join(missing)
        raise SystemExit(f"source config missing exchange fields: {missing_text}")
    for key in required_keys:
        target_exchange[key] = source_exchange[key]
    for key in ("ccxt_config", "ccxt_async_config"):
        if key in source_exchange:
            target_exchange[key] = deepcopy(source_exchange[key])


def build_runtime_config(mode: str, base_config_path: Path, source_config_path: Path, output_path: Path) -> Path:
    runtime_config = _load_json(base_config_path)
    source_config = _load_json(source_config_path)

    _copy_exchange_credentials(runtime_config, source_config)

    for key in ("entry_pricing", "exit_pricing", "unfilledtimeout", "telegram", "api_server", "internals"):
        if key in source_config:
            runtime_config[key] = _deep_merge(runtime_config.get(key, {}), source_config[key])

    runtime_dir = output_path.parent
    runtime_dir.mkdir(parents=True, exist_ok=True)
    DEFAULT_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DEFAULT_USER_DATA_DIR / "data").mkdir(parents=True, exist_ok=True)

    is_live = mode == "live"
    runtime_config["dry_run"] = not is_live
    if not is_live:
        runtime_config.setdefault("dry_run_wallet", 1000)
    else:
        runtime_config.pop("dry_run_wallet", None)

    runtime_config["db_url"] = f"sqlite:///{runtime_dir / ('tradesv3.live.sqlite' if is_live else 'tradesv3.dryrun.sqlite')}"
    runtime_config["datadir"] = str(DEFAULT_USER_DATA_DIR / "data")
    runtime_config["user_data_dir"] = str(DEFAULT_USER_DATA_DIR)
    runtime_config["bot_name"] = "macd-aggressive-live" if is_live else "macd-aggressive-dryrun"
    runtime_config["position_adjustment_enable"] = True
    runtime_config["max_entry_position_adjustment"] = max(
        0,
        int(backtest_module.EXIT_PARAMS.get("pyramid_max_times", 0)),
    )

    # test2 uses market orders for breakout-style execution, so keep the
    # price-side settings aligned even if test1 source config uses older defaults.
    order_types = runtime_config.get("order_types", {})
    if order_types.get("entry") == "market":
        runtime_config.setdefault("entry_pricing", {})["price_side"] = "other"
    if order_types.get("exit") == "market":
        runtime_config.setdefault("exit_pricing", {})["price_side"] = "other"

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(runtime_config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build freqtrade runtime config for test2 real-money-test")
    parser.add_argument("--mode", choices=("dry-run", "live"), default="dry-run")
    parser.add_argument("--base-config", type=Path, default=DEFAULT_BASE_CONFIG)
    parser.add_argument("--source-config", type=Path, default=DEFAULT_SOURCE_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = build_runtime_config(args.mode, args.base_config, args.source_config, args.output)
    print(output_path)


if __name__ == "__main__":
    main()
