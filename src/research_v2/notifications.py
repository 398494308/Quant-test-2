#!/usr/bin/env python3
"""研究器 v2 的 Discord 通知。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import requests

from research_v2.evaluation import EvaluationReport, overfit_reference_action, overfit_risk_level_from_score
from research_v2.strategy_code import StrategyCandidate


DISCORD_API_BASE = "https://discord.com/api/v10"
REGION_LABELS = {
    "PARAMS": "参数",
    "_is_sideways_regime": "横盘识别",
    "_trend_quality_ok": "趋势质量",
    "_trend_followthrough_ok": "跟随确认",
    "strategy": "入场逻辑",
}
TAG_LABELS = {
    "reduce_false_breakdown": "减少空头假破位",
    "reduce_false_breakout": "减少多头假突破",
    "breakdown_entry": "收紧空头入场",
    "breakout_entry": "收紧多头入场",
    "tighten_filter": "收紧过滤阈值",
    "fee_drag_control": "控制手续费拖累",
    "sideways_filter": "增强横盘过滤",
    "short_trend_quality": "收紧做空趋势质量",
    "short_followthrough": "收紧做空跟随确认",
    "fourh_base_filter": "强化4小时底座过滤",
    "hourly_stretch_guard": "过滤1小时过度拉伸",
    "fourh_confirmation": "强化4小时确认",
    "fourh_participation": "强化4小时参与度",
    "close_through_guard": "强化收盘穿透确认",
    "close_drive_filter": "过滤弱收盘破位",
    "fresh_impulse_filter": "强化新鲜动量过滤",
    "fresh_impulse_entry": "要求新鲜动量入场",
    "stale_step_filter": "过滤滞后补票",
    "wick_filter": "过滤长影线假信号",
    "extension_trap_filter": "过滤过度延伸陷阱",
    "distance_cap": "限制追价距离",
    "broad_participation_filter": "增强广泛参与过滤",
    "short_breakdown": "空头破位优化",
    "trigger_efficiency": "强化触发效率",
    "recoil_flush_filter": "过滤下刺回收",
    "hourly_discount_guard": "加入小时级折价保护",
    "short_entry_guard": "加入做空入场保护",
    "support_grind_filter": "过滤支撑位磨损破位",
    "compressed_bear_drift": "过滤压缩式空头漂移",
    "bear_front_run": "过滤空头前冲假信号",
    "discounted_marginal_expansion_guard": "过滤深度折价但扩张不足",
    "discounted_reexpansion_gap": "过滤折价后二次扩张不足",
    "thin_followthrough_guard": "过滤薄参与延续",
    "short_impulse_params": "收紧做空动量参数",
    "short_trigger_surface": "收紧做空触发表面",
    "stale_cascade_guard": "过滤空头二次追击",
}


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


def send_discord_message(message: str, config: DiscordConfig, attachments: list[Path] | None = None) -> None:
    channel_id = resolve_discord_channel_id(config)
    if not config.bot_token or not channel_id:
        raise RuntimeError("missing DISCORD_BOT_TOKEN or Discord channel id")
    if attachments:
        opened_files = []
        files = {}
        try:
            for index, path in enumerate(attachments):
                handle = path.open("rb")
                opened_files.append(handle)
                files[f"files[{index}]"] = (path.name, handle, "image/png")
            response = requests.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {config.bot_token}"},
                data={"payload_json": json.dumps({"content": message}, ensure_ascii=False)},
                files=files,
                timeout=30,
            )
        finally:
            for handle in opened_files:
                handle.close()
    else:
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


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _localize_tag(tag: str) -> str:
    if tag in TAG_LABELS:
        return TAG_LABELS[tag]
    tokens = [item for item in tag.split("_") if item]
    token_labels = {
        "short": "做空",
        "long": "做多",
        "breakdown": "破位",
        "breakout": "突破",
        "entry": "入场",
        "trend": "趋势",
        "quality": "质量",
        "followthrough": "跟随",
        "sideways": "横盘",
        "filter": "过滤",
        "guard": "保护",
        "fee": "手续费",
        "drag": "拖累",
        "control": "控制",
        "hourly": "1小时",
        "fourh": "4小时",
        "base": "底座",
        "fresh": "新鲜",
        "impulse": "动量",
        "close": "收盘",
        "through": "穿透",
        "support": "支撑",
        "compressed": "压缩",
        "bear": "空头",
        "recoil": "回抽",
        "trigger": "触发",
        "surface": "表面",
        "tighten": "收紧",
        "discounted": "深度折价",
        "expansion": "扩张",
        "reexpansion": "再扩张",
        "marginal": "边缘",
    }
    localized = [token_labels[token] for token in tokens if token in token_labels]
    if localized:
        return "/".join(localized)
    return "策略方向调整"


def _localized_tags_text(tags: tuple[str, ...] | list[str]) -> str:
    localized = [_localize_tag(tag) for tag in tags]
    return _single_line(" / ".join(localized), limit=180)


def _localized_regions_text(regions: tuple[str, ...] | list[str]) -> str:
    localized = [REGION_LABELS.get(region, region) for region in regions]
    return _single_line("、".join(localized), limit=120)


def _candidate_hypothesis_text(candidate: StrategyCandidate) -> str:
    if _contains_cjk(candidate.hypothesis):
        return _single_line(candidate.hypothesis, limit=220)
    return _single_line(
        f"本轮围绕“{_localized_tags_text(candidate.change_tags)}”做小步迭代，重点调整{_localized_regions_text(candidate.edited_regions)}。",
        limit=220,
    )


def _candidate_plan_text(candidate: StrategyCandidate) -> str:
    if _contains_cjk(candidate.change_plan):
        return _single_line(candidate.change_plan, limit=220)
    return _single_line(
        f"计划在{_localized_regions_text(candidate.edited_regions)}中继续收紧低质量信号，优先降低手续费拖累、无效交易和回撤。",
        limit=220,
    )


def _candidate_effect_text(candidate: StrategyCandidate) -> str:
    zh_effects = [_single_line(effect, limit=80) for effect in candidate.expected_effects if _contains_cjk(effect)]
    if zh_effects:
        return _single_line("；".join(zh_effects), limit=220)
    return "预期改善大趋势到来捕获、主趋势陪跑和掉头退出效率。"


def build_discord_summary_message(
    *,
    title: str,
    report: EvaluationReport,
    eval_window_count: int,
    validation_window_count: int,
    candidate: StrategyCandidate | None = None,
) -> str:
    metrics = report.metrics
    full_period_return_pct = float(metrics.get("full_period_return_pct", metrics.get("combined_path_return_pct", 0.0)))
    eval_path_return_pct = float(metrics.get("eval_path_return_pct", metrics.get("eval_avg_return", 0.0)))
    validation_path_return_pct = float(metrics.get("validation_path_return_pct", metrics.get("validation_avg_return", 0.0)))
    overfit_score = float(metrics.get("overfit_risk_score", 0.0))
    overfit_hard_fail = float(metrics.get("overfit_hard_fail", 0.0)) > 0.5
    overfit_level = "严重" if overfit_hard_fail else overfit_risk_level_from_score(overfit_score)
    window_text = f"{eval_window_count} 个评估窗口"
    if validation_window_count > 0:
        window_text += f" / {validation_window_count} 个验证窗口"
    rows = [
        ("窗口", window_text),
        ("全段连续收益", f"{full_period_return_pct:.2f}%"),
        ("评估连续收益", f"{eval_path_return_pct:.2f}%"),
        (
            "验证连续收益",
            f"{validation_path_return_pct:.2f}%"
            if validation_window_count > 0 else "-",
        ),
        ("评估窗口均值收益", f"{metrics['eval_avg_return']:.2f}%"),
        (
            "评/验趋势分",
            f"{metrics['eval_trend_capture_score']:.2f}"
            + (f" / {metrics['validation_trend_capture_score']:.2f}" if validation_window_count > 0 else ""),
        ),
        (
            "评/验命中率",
            f"{metrics['eval_segment_hit_rate']:.0%}"
            + (f" / {metrics['validation_segment_hit_rate']:.0%}" if validation_window_count > 0 else ""),
        ),
        (
            "评/验趋势段",
            f"{int(metrics['eval_major_segment_count'])}"
            + (f" / {int(metrics['validation_major_segment_count'])}" if validation_window_count > 0 else ""),
        ),
        (
            "评/验捕获落差",
            f"{metrics['capture_drop']:.2f}"
            if validation_window_count > 0 else "-",
        ),
        (
            "主晋级分落差",
            f"{metrics.get('promotion_gap', 0.0):.2f}"
            if validation_window_count > 0 else "-",
        ),
        ("评分(主/晋)", f"{metrics['quality_score']:.2f} / {metrics['promotion_score']:.2f}"),
        (
            "验证分块均值/std",
            f"{metrics.get('validation_block_score_mean', 0.0):.2f} / "
            f"{metrics.get('validation_block_score_std', 0.0):.2f}"
            if int(metrics.get("validation_block_count_used", 0)) > 0 else "-",
        ),
        (
            "验证最差块/负块数",
            f"{metrics.get('validation_block_score_min', 0.0):.2f} / "
            f"{int(metrics.get('validation_block_fail_count', 0.0))}"
            if int(metrics.get("validation_block_count_used", 0)) > 0 else "-",
        ),
        ("全段趋势/收益分", f"{metrics['combined_trend_capture_score']:.2f} / {metrics['combined_return_score']:.2f}"),
        ("全段到来/陪跑/掉头", f"{metrics['arrival_capture_score']:.2f} / {metrics['escort_capture_score']:.2f} / {metrics['turn_adaptation_score']:.2f}"),
        ("全段多/空捕获", f"{metrics['bull_capture_score']:.2f} / {metrics['bear_capture_score']:.2f}"),
        ("全段命中率/趋势段", f"{metrics['segment_hit_rate']:.0%} / {int(metrics['major_segment_count'])}"),
        ("过拟合风险", f"{overfit_level} / {overfit_score:.0f}"),
        (
            "集中度/覆盖率",
            f"{metrics.get('overfit_top1_positive_share', 0.0):.0%} / "
            f"{metrics.get('overfit_chain_positive_share', 0.0):.0%} / "
            f"{metrics.get('overfit_coverage_ratio', 1.0):.0%}",
        ),
        ("评估唯一路径", f"{int(metrics['eval_unique_trend_points'])} 个4h点"),
        ("最大回撤", f"{metrics['worst_drawdown']:.2f}%"),
        ("总交易", str(int(metrics["total_trades"]))),
        ("手续费拖累", f"{metrics['avg_fee_drag']:.2f}%"),
    ]

    parts = [
        f"**{title}**",
        "```text",
        _render_markdown_table(rows),
        "```",
        f"门禁：{_single_line(report.gate_reason, limit=280)}",
    ]
    if candidate is not None:
        parts.extend(
            [
                f"方向：{_localized_tags_text(candidate.change_tags)}",
                f"修改区域：{_localized_regions_text(candidate.edited_regions)}",
                f"假设：{_candidate_hypothesis_text(candidate)}",
                f"计划：{_candidate_plan_text(candidate)}",
                f"预期：{_candidate_effect_text(candidate)}",
                f"过拟合结论：{overfit_reference_action(overfit_score, overfit_hard_fail)}",
            ]
        )
    return "\n".join(parts)
