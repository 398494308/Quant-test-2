#!/usr/bin/env python3
"""研究器 v2 的 Discord 通知。"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from research_v2.evaluation import EvaluationReport
from research_v2.strategy_code import StrategyCandidate


DISCORD_API_BASE = "https://discord.com/api/v10"


@dataclass(frozen=True)
class DiscordConfig:
    bot_token: str
    channel_id: str
    guild_id: str
    channel_name: str

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and (self.channel_id or (self.guild_id and self.channel_name)))


def load_discord_config() -> DiscordConfig:
    return DiscordConfig(
        bot_token=os.getenv("DISCORD_BOT_TOKEN", "").strip(),
        channel_id=os.getenv("DISCORD_CHANNEL_ID", "").strip(),
        guild_id=os.getenv("DISCORD_GUILD_ID", "").strip(),
        channel_name=os.getenv("DISCORD_CHANNEL_NAME", "quant-highrisk").strip(),
    )


def resolve_discord_channel_id(config: DiscordConfig) -> str:
    if config.channel_id:
        return config.channel_id
    if not config.bot_token or not config.guild_id or not config.channel_name:
        return ""
    response = requests.get(
        f"{DISCORD_API_BASE}/guilds/{config.guild_id}/channels",
        headers={"Authorization": f"Bot {config.bot_token}"},
        timeout=15,
    )
    response.raise_for_status()
    for channel in response.json():
        if channel.get("type") == 0 and channel.get("name") == config.channel_name:
            return str(channel.get("id") or "")
    return ""


def send_discord_message(message: str, config: DiscordConfig) -> None:
    channel_id = resolve_discord_channel_id(config)
    if not config.bot_token or not channel_id:
        raise RuntimeError("missing DISCORD_BOT_TOKEN or Discord channel id")
    response = requests.post(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {config.bot_token}"},
        json={"content": message},
        timeout=15,
    )
    response.raise_for_status()


def _render_markdown_table(rows: list[tuple[str, str]]) -> str:
    lines = [
        "| 项目 | 数值 |",
        "| --- | --- |",
    ]
    for label, value in rows:
        lines.append(f"| {label} | {value} |")
    return "\n".join(lines)


def _single_line(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def build_discord_summary_message(
    *,
    title: str,
    report: EvaluationReport,
    eval_window_count: int,
    validation_window_count: int,
    candidate: StrategyCandidate | None = None,
) -> str:
    metrics = report.metrics
    window_text = f"{eval_window_count}评估"
    if validation_window_count > 0:
        window_text += f" / {validation_window_count}验证"
    rows = [
        ("窗口", window_text),
        (
            "收益",
            f"{metrics['eval_avg_return']:.2f}%"
            + (f" / {metrics['validation_avg_return']:.2f}%" if validation_window_count > 0 else ""),
        ),
        ("评分", f"{metrics['quality_score']:.2f} / {metrics['promotion_score']:.2f}"),
        ("最大回撤", f"{metrics['worst_drawdown']:.2f}%"),
        ("正收益窗", f"{metrics['eval_positive_ratio']:.0%}"),
        ("总交易", str(int(metrics["total_trades"]))),
        ("手续费拖累", f"{metrics['avg_fee_drag']:.2f}%"),
    ]

    parts = [
        f"**{title}**",
        "```text",
        _render_markdown_table(rows),
        "```",
        f"Gate: {_single_line(report.gate_reason, limit=280)}",
    ]
    if candidate is not None:
        parts.extend(
            [
                f"假设: {_single_line(candidate.hypothesis)}",
                f"标签: {_single_line(', '.join(candidate.change_tags), limit=120)}",
                f"计划: {_single_line(candidate.change_plan, limit=220)}",
            ]
        )
    return "\n".join(parts)
