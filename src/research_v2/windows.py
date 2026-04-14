#!/usr/bin/env python3
"""研究窗口生成。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from research_v2.config import WindowConfig


# ==================== 数据结构 ====================


@dataclass(frozen=True)
class ResearchWindow:
    group: str
    label: str
    start_date: str
    end_date: str
    weight: float


# ==================== 窗口生成 ====================


def _parse_date(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d")


def build_research_windows(config: WindowConfig) -> list[ResearchWindow]:
    start_dt = _parse_date(config.eval_start_date)
    end_dt = _parse_date(config.eval_end_date)
    if end_dt < start_dt:
        raise ValueError(f"invalid evaluation range: {config.eval_start_date}..{config.eval_end_date}")
    if config.eval_window_days < 7:
        raise ValueError(f"eval_window_days too small: {config.eval_window_days}")
    if config.eval_step_days < 5:
        raise ValueError(f"eval_step_days too small: {config.eval_step_days}")
    if config.validation_days < 7:
        raise ValueError(f"validation_days too small: {config.validation_days}")

    validation_start = end_dt - timedelta(days=config.validation_days - 1)
    eval_last_end = validation_start - timedelta(days=1)
    if eval_last_end <= start_dt:
        raise ValueError("not enough room for eval windows before validation")

    eval_windows: list[ResearchWindow] = []
    cursor = start_dt
    window_index = 1
    while True:
        window_end = cursor + timedelta(days=config.eval_window_days - 1)
        if window_end > eval_last_end:
            break
        eval_windows.append(
            ResearchWindow(
                group="eval",
                label=f"评估{window_index}",
                start_date=cursor.strftime("%Y-%m-%d"),
                end_date=window_end.strftime("%Y-%m-%d"),
                weight=1.0,
            )
        )
        window_index += 1
        cursor += timedelta(days=config.eval_step_days)

    tail_start = eval_last_end - timedelta(days=config.eval_window_days - 1)
    if tail_start >= start_dt:
        tail_start_str = tail_start.strftime("%Y-%m-%d")
        tail_end_str = eval_last_end.strftime("%Y-%m-%d")
        if not eval_windows or (
            eval_windows[-1].start_date != tail_start_str
            or eval_windows[-1].end_date != tail_end_str
        ):
            eval_windows.append(
                ResearchWindow(
                    group="eval",
                    label=f"评估{window_index}",
                    start_date=tail_start_str,
                    end_date=tail_end_str,
                    weight=1.0,
                )
            )

    if len(eval_windows) < 4:
        raise ValueError(f"not enough eval windows: {len(eval_windows)}")

    validation_window = ResearchWindow(
        group="validation",
        label="验证1",
        start_date=validation_start.strftime("%Y-%m-%d"),
        end_date=end_dt.strftime("%Y-%m-%d"),
        weight=1.0,
    )
    return [*eval_windows, validation_window]
