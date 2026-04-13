#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests


CN_TZ = ZoneInfo("Asia/Shanghai")
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
RUNTIME_DIR = SCRIPT_DIR / "runtime"
CONFIG_PATH = RUNTIME_DIR / "config.runtime.json"
PID_FILE = RUNTIME_DIR / "freqtrade-dryrun.pid"
DB_PATH = RUNTIME_DIR / "tradesv3.dryrun.sqlite"
LOG_PATH = RUNTIME_DIR / "freqtrade-dryrun.log"
SNAPSHOT_PATH = RUNTIME_DIR / "daily-report-snapshots.json"
BASE_ENV_PATH = REPO_ROOT / "config" / "research.env"
LOCAL_ENV_PATH = SCRIPT_DIR / "report.env"
DISCORD_API_BASE = "https://discord.com/api/v10"
LOG_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}")


@dataclass
class BotStatus:
    running: bool
    pid: str = ""
    heartbeat_at: datetime | None = None
    last_log_at: datetime | None = None


def load_env(paths: Iterable[Path]) -> dict[str, str]:
    env: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def parse_log_timestamp(line: str) -> datetime | None:
    match = LOG_TIMESTAMP_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ)
    except ValueError:
        return None


def tail_lines(path: Path, limit: int = 200) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return lines[-limit:]


def pid_is_alive(pid: str) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def find_running_pid() -> str:
    if PID_FILE.exists():
        pid = PID_FILE.read_text(encoding="utf-8").strip()
        if pid_is_alive(pid):
            return pid
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    for line in result.stdout.splitlines():
        if "freqtrade trade" not in line or str(CONFIG_PATH) not in line:
            continue
        parts = line.strip().split(maxsplit=1)
        if parts:
            return parts[0]
    return ""


def read_bot_status() -> BotStatus:
    pid = find_running_pid()
    heartbeat_at = None
    last_log_at = None
    for line in reversed(tail_lines(LOG_PATH, limit=400)):
        ts = parse_log_timestamp(line)
        if ts and last_log_at is None:
            last_log_at = ts
        if "Bot heartbeat." in line and ts:
            heartbeat_at = ts
            break
    return BotStatus(running=bool(pid), pid=pid, heartbeat_at=heartbeat_at, last_log_at=last_log_at)


def format_age(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "-"
    delta = now - ts
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def read_runtime_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def open_db() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def read_summary(now: datetime) -> dict:
    summary = {
        "total_trades": 0,
        "open_trades": 0,
        "closed_trades": 0,
        "closed_pnl_abs": 0.0,
        "open_realized_pnl_abs": 0.0,
        "unrealized_pnl_abs": 0.0,
        "day_closed_trades": 0,
        "day_wins": 0,
        "day_pnl_abs": 0.0,
        "open_positions": [],
        "recent_closes": [],
    }
    conn = open_db()
    if conn is None:
        return summary

    now_utc = now.astimezone(ZoneInfo("UTC"))
    cutoff = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
          COUNT(*) AS total_trades,
          SUM(CASE WHEN is_open = 1 THEN 1 ELSE 0 END) AS open_trades,
          SUM(CASE WHEN is_open = 0 THEN 1 ELSE 0 END) AS closed_trades,
          SUM(CASE WHEN is_open = 0 THEN COALESCE(close_profit_abs, 0) ELSE 0 END) AS closed_pnl_abs,
          SUM(CASE WHEN is_open = 1 THEN COALESCE(realized_profit, 0) ELSE 0 END) AS open_realized_pnl_abs
        FROM trades
        """
    )
    row = cur.fetchone()
    if row is not None:
        summary["total_trades"] = row["total_trades"] or 0
        summary["open_trades"] = row["open_trades"] or 0
        summary["closed_trades"] = row["closed_trades"] or 0
        summary["closed_pnl_abs"] = float(row["closed_pnl_abs"] or 0.0)
        summary["open_realized_pnl_abs"] = float(row["open_realized_pnl_abs"] or 0.0)

    cur.execute(
        """
        SELECT
          COUNT(*) AS day_closed_trades,
          SUM(CASE WHEN COALESCE(close_profit_abs, 0) > 0 THEN 1 ELSE 0 END) AS day_wins,
          SUM(COALESCE(close_profit_abs, 0)) AS day_pnl_abs
        FROM trades
        WHERE is_open = 0
          AND close_date IS NOT NULL
          AND close_date >= ?
        """,
        (cutoff,),
    )
    row = cur.fetchone()
    if row is not None:
        summary["day_closed_trades"] = row["day_closed_trades"] or 0
        summary["day_wins"] = row["day_wins"] or 0
        summary["day_pnl_abs"] = float(row["day_pnl_abs"] or 0.0)

    cur.execute(
        """
        SELECT
          pair,
          is_short,
          leverage,
          open_rate,
          stake_amount,
          amount,
          contract_size,
          open_date,
          enter_tag,
          realized_profit
        FROM trades
        WHERE is_open = 1
        ORDER BY open_date ASC
        """
    )
    summary["open_positions"] = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT pair, is_short, close_date, close_profit_abs, exit_reason
        FROM trades
        WHERE is_open = 0
        ORDER BY close_date DESC
        LIMIT 3
        """
    )
    summary["recent_closes"] = [dict(row) for row in cur.fetchall()]

    prices = fetch_current_prices(summary["open_positions"])
    unrealized_pnl_abs = 0.0
    for pos in summary["open_positions"]:
        current_rate = prices.get(pos["pair"])
        pos["current_rate"] = current_rate
        pos["unrealized_pnl_abs"] = calc_unrealized_pnl_abs(pos, current_rate)
        unrealized_pnl_abs += float(pos["unrealized_pnl_abs"] or 0.0)
    summary["unrealized_pnl_abs"] = unrealized_pnl_abs
    conn.close()
    return summary


def format_side(is_short: int | bool | None) -> str:
    return "SHORT" if is_short else "LONG"


def pair_to_okx_inst_id(pair: str) -> str:
    base, quote_part = pair.split("/", 1)
    quote = quote_part.split(":", 1)[0]
    settle = quote_part.split(":", 1)[1] if ":" in quote_part else quote
    if quote == settle:
        return f"{base}-{quote}-SWAP"
    return f"{base}-{quote}-{settle}"


def fetch_current_prices(positions: list[dict]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for pos in positions:
        pair = pos["pair"]
        if pair in prices:
            continue
        inst_id = pair_to_okx_inst_id(pair)
        response = requests.get(
            f"https://www.okx.com/api/v5/market/ticker?instId={inst_id}",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        if not data:
            continue
        last = data[0].get("last")
        if last is None:
            continue
        prices[pair] = float(last)
    return prices


def calc_unrealized_pnl_abs(position: dict, current_rate: float | None) -> float:
    if current_rate is None:
        return 0.0
    amount = float(position.get("amount") or 0.0)
    open_rate = float(position.get("open_rate") or 0.0)
    if amount <= 0 or open_rate <= 0:
        return 0.0
    direction = -1.0 if position.get("is_short") else 1.0
    # For this OKX linear swap setup, `amount` is already the base-asset size.
    return direction * (current_rate - open_rate) * amount


def format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def format_abs(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}U"


def load_snapshots() -> list[dict]:
    if not SNAPSHOT_PATH.exists():
        return []
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def save_snapshots(snapshots: list[dict]) -> None:
    SNAPSHOT_PATH.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def previous_snapshot_for_date(snapshots: list[dict], report_date: str) -> dict | None:
    for snapshot in reversed(snapshots):
        if snapshot.get("date") != report_date:
            return snapshot
    return None


def upsert_snapshot(snapshots: list[dict], snapshot: dict) -> list[dict]:
    updated = [item for item in snapshots if item.get("date") != snapshot.get("date")]
    updated.append(snapshot)
    updated.sort(key=lambda item: item.get("date", ""))
    return updated[-30:]


def format_positions(positions: list[dict]) -> str:
    if not positions:
        return "空仓"
    rendered = []
    for pos in positions[:2]:
        current_rate = pos.get("current_rate")
        unrealized_pnl_abs = float(pos.get("unrealized_pnl_abs") or 0.0)
        pnl_pct = None
        stake_amount = float(pos.get("stake_amount") or 0.0)
        if stake_amount > 0:
            pnl_pct = unrealized_pnl_abs / stake_amount * 100.0
        rendered.append(
            (
                f"{pos['pair']} {format_side(pos.get('is_short'))} "
                f"x{int(pos.get('leverage') or 0)} "
                f"uPnL={format_abs(unrealized_pnl_abs)} "
                f"({format_pct(pnl_pct)})"
                + (f" now={float(current_rate):.2f}" if current_rate else "")
            )
        )
    if len(positions) > 2:
        rendered.append(f"...其余 {len(positions) - 2} 笔")
    return " | ".join(rendered)


def format_recent_closes(closes: list[dict]) -> str:
    if not closes:
        return "无"
    rendered = []
    for item in closes:
        close_date = str(item.get("close_date") or "")[:16]
        rendered.append(
            (
                f"{close_date} {item['pair']} {format_side(item.get('is_short'))} "
                f"{float(item.get('close_profit_abs') or 0):+.2f} "
                f"{item.get('exit_reason') or '-'}"
            )
        )
    return " | ".join(rendered)


def resolve_discord_channel_id(env: dict[str, str]) -> str:
    if env.get("DISCORD_CHANNEL_ID"):
        return env["DISCORD_CHANNEL_ID"]
    token = env.get("DISCORD_BOT_TOKEN", "")
    guild_id = env.get("DISCORD_GUILD_ID", "")
    channel_name = env.get("DISCORD_CHANNEL_NAME", "")
    if not token or not guild_id or not channel_name:
        return ""
    response = requests.get(
        f"{DISCORD_API_BASE}/guilds/{guild_id}/channels",
        headers={"Authorization": f"Bot {token}"},
        timeout=15,
    )
    response.raise_for_status()
    for channel in response.json():
        if channel.get("type") == 0 and channel.get("name") == channel_name:
            return channel.get("id", "")
    return ""


def send_discord(message: str, env: dict[str, str]) -> None:
    token = env.get("DISCORD_BOT_TOKEN", "")
    channel_id = resolve_discord_channel_id(env)
    if not token or not channel_id:
        raise RuntimeError("missing DISCORD_BOT_TOKEN or channel id")
    response = requests.post(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {token}"},
        json={"content": message},
        timeout=15,
    )
    response.raise_for_status()


def build_message(now: datetime, status: BotStatus, runtime_config: dict, summary: dict) -> str:
    initial_capital = float(runtime_config.get("dry_run_wallet") or 0.0)
    total_realized = float(summary["closed_pnl_abs"]) + float(summary["open_realized_pnl_abs"])
    equity = initial_capital + total_realized + float(summary["unrealized_pnl_abs"])
    cumulative_pct = None
    if initial_capital > 0:
        cumulative_pct = (equity / initial_capital - 1.0) * 100.0

    snapshots = load_snapshots()
    report_date = now.strftime("%Y-%m-%d")
    previous_snapshot = previous_snapshot_for_date(snapshots, report_date)
    day_delta_abs = None
    day_delta_pct = None
    if previous_snapshot is not None:
        previous_equity = float(previous_snapshot.get("equity") or 0.0)
        day_delta_abs = equity - previous_equity
        if previous_equity > 0:
            day_delta_pct = day_delta_abs / previous_equity * 100.0

    pairlist = runtime_config.get("exchange", {}).get("pair_whitelist", [])
    pair_text = ", ".join(pairlist) if pairlist else "-"
    heartbeat_age = format_age(status.heartbeat_at, now)
    position_usage = f"{summary['open_trades']}/{runtime_config.get('max_open_trades', '-')}"

    snapshot = {
        "date": report_date,
        "reported_at": now.isoformat(),
        "equity": equity,
        "initial_capital": initial_capital,
        "total_trades": summary["total_trades"],
        "open_trades": summary["open_trades"],
        "closed_trades": summary["closed_trades"],
        "total_realized": total_realized,
        "unrealized_pnl_abs": summary["unrealized_pnl_abs"],
    }
    save_snapshots(upsert_snapshot(snapshots, snapshot))

    lines = [
        f"【test2 Dry Run】{now.strftime('%Y-%m-%d %H:%M CST')}",
        (
            f"权益 {equity:.2f}U"
            f" | 昨日 {format_abs(day_delta_abs)} ({format_pct(day_delta_pct)})"
            f" | 累计 {format_pct(cumulative_pct)}"
        ),
        (
            f"交易 总{summary['total_trades']}"
            f" | 24h {summary['day_closed_trades']}"
            f" | 持仓 {position_usage}"
        ),
        (
            f"PnL 已实现 {format_abs(total_realized)}"
            f" | 未实现 {format_abs(summary['unrealized_pnl_abs'])}"
        ),
        (
            f"仓位 {format_positions(summary['open_positions'])}"
        ),
        (
            f"状态 {'RUNNING' if status.running else 'STOPPED'}"
            + (f" | hb {heartbeat_age}" if status.running else "")
            + f" | {pair_text} {runtime_config.get('timeframe', '-')}"
        ),
        f"最近 {format_recent_closes(summary['recent_closes'])}",
    ]
    return "\n".join(lines)


def main() -> int:
    env = load_env([BASE_ENV_PATH, LOCAL_ENV_PATH])
    now = datetime.now(CN_TZ)
    runtime_config = read_runtime_config()
    status = read_bot_status()
    summary = read_summary(now)
    message = build_message(now, status, runtime_config, summary)
    send_discord(message, env)
    print(message)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"daily report failed: {exc}", file=sys.stderr)
        raise
