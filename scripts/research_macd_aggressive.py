#!/usr/bin/env python3
"""激进版双向 BTC 合约策略自动研究循环。"""
import importlib
import json
import logging
import os
import pprint
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import backtest_macd_aggressive as backtest_module
import strategy_macd_aggressive as strategy_module
from openai_strategy_client import (
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    generate_strategy_code,
)

BASE_DIR = REPO_ROOT
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
DISCORD_CHANNEL_NAME = os.getenv("DISCORD_CHANNEL_NAME", "quant-highrisk")
STRATEGY_FILE = BASE_DIR / "src/strategy_macd_aggressive.py"
BACKUP_FILE = BASE_DIR / "backups/strategy_macd_aggressive_backup.py"
BACKTEST_FILE = BASE_DIR / "src/backtest_macd_aggressive.py"
BACKTEST_BACKUP_FILE = BASE_DIR / "backups/backtest_macd_aggressive_backup.py"
PROGRAM_FILE = BASE_DIR / "docs/program_macd_aggressive.md"
LOG_FILE = str(BASE_DIR / "logs/macd_aggressive_research.log")
MEMORY_FILE = BASE_DIR / "state/optimizer_memory_macd_aggressive.json"
LOOP_INTERVAL_SECONDS = int(os.getenv("MACD_LOOP_INTERVAL_SECONDS", "600"))
INTRADAY_FILE = str(BASE_DIR / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv")
HOURLY_FILE = str(BASE_DIR / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv")
WINDOWS = [
    ("test", "测试-2025-08", "2025-08-01", "2025-08-31"),
    ("test", "测试-2025-11", "2025-11-01", "2025-11-30"),
    ("test", "测试-2025-07", "2025-07-01", "2025-07-31"),
    ("test", "测试-2025-09", "2025-09-01", "2025-09-30"),
    ("test", "测试-2025-10", "2025-10-01", "2025-10-31"),
    ("test", "测试-2025-12", "2025-12-01", "2025-12-31"),
    ("main", "主-2026-01", "2026-01-01", "2026-01-31"),
    ("main", "主-2026-02", "2026-02-01", "2026-02-28"),
]
best_score = -999999.0
iteration = 0
strategy = strategy_module.strategy
backtest_macd_aggressive = backtest_module.backtest_macd_aggressive
latest_eval_summary = ""
last_params_snapshot = None
last_exit_snapshot = None
_resolved_discord_channel_id = CHANNEL_ID

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def _resolve_discord_channel_id():
    global _resolved_discord_channel_id
    if _resolved_discord_channel_id:
        return _resolved_discord_channel_id
    if not DISCORD_TOKEN or not DISCORD_GUILD_ID:
        return ""
    try:
        headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
        resp = requests.get(
            f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/channels",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        channels = resp.json()
        for channel in channels:
            if channel.get("type") == 0 and channel.get("name") == DISCORD_CHANNEL_NAME:
                _resolved_discord_channel_id = channel.get("id", "")
                return _resolved_discord_channel_id
    except Exception:
        return ""
    return ""


def send_discord(msg):
    if not DISCORD_TOKEN:
        return
    channel_id = _resolve_discord_channel_id()
    if not channel_id:
        return
    try:
        headers = {"Authorization": f"Bot {DISCORD_TOKEN}"}
        response = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers,
            json={"content": msg},
            timeout=10,
        )
        response.raise_for_status()
    except Exception:
        logging.exception("Discord 发送失败")


def log_info(message):
    print(message)
    logging.info(message)


def log_exception(message):
    print(message)
    logging.error("%s\n%s", message, traceback.format_exc())


def reload_strategy():
    global strategy, backtest_macd_aggressive
    importlib.reload(backtest_module)
    importlib.reload(strategy_module)
    strategy = strategy_module.strategy
    backtest_macd_aggressive = backtest_module.backtest_macd_aggressive


def run_all_tests():
    results = []
    for group, name, start_date, end_date in WINDOWS:
        result = backtest_macd_aggressive(
            strategy_func=strategy,
            intraday_file=INTRADAY_FILE,
            hourly_file=HOURLY_FILE,
            start_date=start_date,
            end_date=end_date,
            strategy_params=strategy_module.PARAMS,
            exit_params=backtest_module.EXIT_PARAMS,
        )
        results.append({"group": group, "name": name, "window": f"{start_date}~{end_date}", "result": result})
    return results


def _score_summary(results):
    main_scores = [item["result"]["score"] for item in results if item["group"] == "main"]
    test_scores = [item["result"]["score"] for item in results if item["group"] == "test"]
    main_avg = sum(main_scores) / len(main_scores) if main_scores else 0.0
    test_avg = sum(test_scores) / len(test_scores) if test_scores else 0.0
    worst_drawdown = max((item["result"]["max_drawdown"] for item in results), default=0.0)
    liquidation_rate = (
        sum(item["result"].get("liquidations", 0) for item in results)
        / max(1, sum(item["result"]["trades"] for item in results))
    )
    overall_avg = 0.60 * main_avg + 0.40 * test_avg - max(0.0, worst_drawdown - 18.0) * 0.10 - liquidation_rate * 4.0
    return main_avg, test_avg, overall_avg


def build_summary_message(title, results, main_avg, overall_avg):
    test_scores = [item["result"]["score"] for item in results if item["group"] == "test"]
    test_avg = sum(test_scores) / len(test_scores) if test_scores else 0.0
    total_trades = sum(item["result"]["trades"] for item in results)
    worst_drawdown = max((item["result"]["max_drawdown"] for item in results), default=0.0)
    pyramid_adds = sum(item["result"].get("pyramid_add_count", 0) for item in results)
    lines = [
        title,
        f"模型 {OPENAI_MODEL} / {OPENAI_REASONING_EFFORT}",
        f"主窗口 {main_avg:.4f} | 测试窗口 {test_avg:.4f} | 综合分 {overall_avg:.4f}",
        (
            f"{backtest_module.EXIT_PARAMS['leverage']}x | "
            f"并发 {backtest_module.EXIT_PARAMS['max_concurrent_positions']} | "
            f"首止盈 {backtest_module.EXIT_PARAMS['tp1_pnl_pct']:.1f}%"
        ),
        f"最差回撤 {worst_drawdown:.2f}% | 总交易数 {total_trades} | 加仓次数 {pyramid_adds}",
        "```text",
        "窗口       类型  交易数  收益率%  回撤%",
        "--------  ----  -----  -------  ------",
    ]
    for item in results:
        result = item["result"]
        lines.append(
            f"{item['name']:<8}  "
            f"{'主' if item['group'] == 'main' else '测':<4}  "
            f"{result['trades']:>5}  "
            f"{result['return']:>7.2f}  "
            f"{result['max_drawdown']:>6.2f}"
        )
    lines.extend(["```"])
    return "\n".join(lines)


def _memory_summary():
    if not MEMORY_FILE.exists():
        return "暂无历史方向记忆。"
    try:
        payload = json.loads(MEMORY_FILE.read_text())
    except Exception:
        return "历史记忆读取失败。"
    lines = []
    for title, key in [("最近有效方向", "accepted"), ("最近无效方向", "rejected")]:
        items = []
        seen = set()
        for item in reversed(payload.get(key, [])):
            signature = item.get("signature") or "|".join(item.get("changes", []))
            if signature in seen:
                continue
            seen.add(signature)
            items.append(item)
            if len(items) >= 4:
                break
        items.reverse()
        if not items:
            continue
        lines.append(title + ":")
        for item in items:
            lines.append(
                f"- overall={item['overall']:.4f}, gate={item['gate']}, changes={'; '.join(item['changes'])}"
            )
    return "\n".join(lines) if lines else "暂无历史方向记忆。"


def _change_signature(changes):
    if not changes:
        return "no_change"
    return "|".join(changes)


def optimize_strategy():
    with open(STRATEGY_FILE, "r") as f:
        current_strategy_code = f.read()
    with open(BACKTEST_FILE, "r") as f:
        current_backtest_code = f.read()
    with open(PROGRAM_FILE, "r") as f:
        instructions = f.read()

    prompt = f"""你是激进版 BTC 双向趋势策略参数优化 AI。

{instructions}

当前策略参数：
{json.dumps(strategy_module.PARAMS, ensure_ascii=False, indent=2, sort_keys=True)}

当前退出参数：
{json.dumps(backtest_module.EXIT_PARAMS, ensure_ascii=False, indent=2, sort_keys=True)}

最近一轮评估摘要：
{latest_eval_summary}

历史方向记忆：
{_memory_summary()}

请只输出一个完整 JSON 对象：
{{
  "strategy_params": {{...}},
  "exit_params": {{...}}
}}
"""

    payload = json.loads(
        generate_strategy_code(
            prompt=prompt,
            system_prompt=(
                "你是激进版 BTC 双向趋势策略参数优化 AI。"
                "只输出 strategy_params 和 exit_params 的 JSON。"
            ),
            max_output_tokens=2200,
        )
    )

    new_strategy_params = payload["strategy_params"]
    new_exit_params = payload["exit_params"]

    params_block = "# PARAMS_START\nPARAMS = " + pprint.pformat(new_strategy_params, sort_dicts=True) + "\n# PARAMS_END"
    exit_block = "# EXIT_PARAMS_START\nEXIT_PARAMS = " + pprint.pformat(new_exit_params, sort_dicts=True) + "\n# EXIT_PARAMS_END"

    updated_strategy_code = re.sub(
        r"# PARAMS_START\n.*?\n# PARAMS_END",
        params_block,
        current_strategy_code,
        count=1,
        flags=re.S,
    )
    updated_backtest_code = re.sub(
        r"# EXIT_PARAMS_START\n.*?\n# EXIT_PARAMS_END",
        exit_block,
        current_backtest_code,
        count=1,
        flags=re.S,
    )

    compile(updated_strategy_code, STRATEGY_FILE, "exec")
    compile(updated_backtest_code, BACKTEST_FILE, "exec")
    with open(STRATEGY_FILE, "w") as f:
        f.write(updated_strategy_code)
    with open(BACKTEST_FILE, "w") as f:
        f.write(updated_backtest_code)


def _append_memory(bucket, changes, overall, gate):
    if MEMORY_FILE.exists():
        memory = json.loads(MEMORY_FILE.read_text())
    else:
        memory = {"accepted": [], "rejected": []}
    signature = _change_signature(changes)
    entries = memory.setdefault(bucket, [])
    if any(item.get("signature") == signature for item in entries[-8:]):
        return
    entries.append({"overall": overall, "gate": gate, "changes": changes, "signature": signature})
    memory[bucket] = memory[bucket][-16:]
    MEMORY_FILE.write_text(json.dumps(memory, ensure_ascii=False, indent=2))


def initialize_best_score():
    global best_score, latest_eval_summary, last_params_snapshot, last_exit_snapshot
    reload_strategy()
    results = run_all_tests()
    main_avg, test_avg, overall_avg = _score_summary(results)
    best_score = overall_avg
    latest_eval_summary = build_summary_message("基准", results, main_avg, overall_avg)
    last_params_snapshot = dict(strategy_module.PARAMS)
    last_exit_snapshot = dict(backtest_module.EXIT_PARAMS)
    log_info(f"激进版初始基准: overall={overall_avg:.4f}, main={main_avg:.4f}, test={test_avg:.4f}")
    send_discord(latest_eval_summary)


def run_iteration(iteration_id, use_model_optimization=True):
    global best_score, latest_eval_summary, last_params_snapshot, last_exit_snapshot
    shutil.copy(STRATEGY_FILE, BACKUP_FILE)
    shutil.copy(BACKTEST_FILE, BACKTEST_BACKUP_FILE)

    try:
        if use_model_optimization:
            optimize_strategy()
        reload_strategy()
        results = run_all_tests()
        main_avg, test_avg, overall_avg = _score_summary(results)
    except Exception as exc:
        shutil.copy(BACKUP_FILE, STRATEGY_FILE)
        shutil.copy(BACKTEST_BACKUP_FILE, BACKTEST_FILE)
        reload_strategy()
        log_exception(f"❌ 激进版第 {iteration_id} 轮失败: {exc}")
        raise

    latest_eval_summary = build_summary_message("最近一轮", results, main_avg, overall_avg)
    current_params_snapshot = dict(strategy_module.PARAMS)
    current_exit_snapshot = dict(backtest_module.EXIT_PARAMS)
    changes = []
    for key, old_value in last_params_snapshot.items():
        new_value = current_params_snapshot.get(key)
        if old_value != new_value:
            changes.append(f"strategy.{key}:{old_value}->{new_value}")
    for key, old_value in last_exit_snapshot.items():
        new_value = current_exit_snapshot.get(key)
        if old_value != new_value:
            changes.append(f"exit.{key}:{old_value}->{new_value}")

    total_trades = sum(item["result"]["trades"] for item in results)
    worst_drawdown = max(item["result"]["max_drawdown"] for item in results)
    liquidations = sum(item["result"].get("liquidations", 0) for item in results)
    gate_passed = total_trades >= 45 and worst_drawdown <= 24.0 and liquidations <= 4
    gate_reason = "通过" if gate_passed else f"trade={total_trades}, dd={worst_drawdown:.2f}, liq={liquidations}"

    if gate_passed and overall_avg > best_score:
        best_score = overall_avg
        last_params_snapshot = current_params_snapshot
        last_exit_snapshot = current_exit_snapshot
        _append_memory("accepted", changes, overall_avg, gate_reason)
        msg = build_summary_message(f"🚀 新最优激进版策略 #{iteration_id}", results, main_avg, overall_avg)
        log_info(msg)
        send_discord(msg)
        return True

    _append_memory("rejected", changes, overall_avg, gate_reason)
    shutil.copy(BACKUP_FILE, STRATEGY_FILE)
    shutil.copy(BACKTEST_BACKUP_FILE, BACKTEST_FILE)
    reload_strategy()
    log_info(f"第 {iteration_id} 轮未保留: {gate_reason}")
    return False


def main():
    global iteration
    once = "--once" in sys.argv
    no_optimize = "--no-optimize" in sys.argv

    log_info("🚀 启动激进版 MACD 研究系统")
    log_info(f"日志文件: {LOG_FILE}")
    log_info(f"循环间隔: {LOOP_INTERVAL_SECONDS} 秒")
    initialize_best_score()
    if once and no_optimize:
        log_info(latest_eval_summary)
        return

    while True:
        iteration += 1
        log_info(f"\n=== 激进版第 {iteration} 轮 ===")
        try:
            run_iteration(iteration, use_model_optimization=not no_optimize)
        except Exception:
            cooldown_seconds = max(LOOP_INTERVAL_SECONDS, 600)
            log_info(f"本轮失败，冷却 {cooldown_seconds} 秒后重试")
            if once:
                break
            time.sleep(cooldown_seconds)
            continue

        if once:
            break
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
