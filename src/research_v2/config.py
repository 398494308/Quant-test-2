#!/usr/bin/env python3
"""研究器 v2 的运行配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# ==================== 环境加载 ====================

ENV_FILES = (
    "config/secrets.env",
    "config/research_v2.env",
    "config/research.env",
)


def _load_env_files(repo_root: Path) -> None:
    for relative_path in ENV_FILES:
        env_path = repo_root / relative_path
        if not env_path.exists():
            continue
        for line in env_path.read_text().splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


# ==================== 路径配置 ====================


@dataclass(frozen=True)
class ResearchPaths:
    repo_root: Path
    strategy_file: Path
    backtest_file: Path
    log_file: Path
    journal_file: Path
    heartbeat_file: Path
    best_state_file: Path
    best_strategy_file: Path
    stop_file: Path
    strategy_backup_file: Path


@dataclass(frozen=True)
class WindowConfig:
    eval_start_date: str
    eval_end_date: str
    eval_window_days: int
    eval_step_days: int
    validation_days: int


@dataclass(frozen=True)
class GateConfig:
    min_total_trades: int
    min_eval_trades: int
    min_validation_trades: int
    min_positive_ratio: float
    max_drawdown_pct: float
    max_liquidations: int
    min_validation_return: float
    max_eval_validation_gap: float
    max_fee_drag_pct: float


@dataclass(frozen=True)
class ResearchRuntimeConfig:
    paths: ResearchPaths
    windows: WindowConfig
    gates: GateConfig
    loop_interval_seconds: int
    provider_recovery_wait_seconds: int
    failure_cooldown_seconds: int
    prompt_max_output_tokens: int
    max_recent_journal_entries: int
    early_reject_after_windows: int
    early_reject_sortino_threshold: float


# ==================== 对外入口 ====================


def load_research_runtime_config(repo_root: Path) -> ResearchRuntimeConfig:
    _load_env_files(repo_root)

    paths = ResearchPaths(
        repo_root=repo_root,
        strategy_file=repo_root / "src/strategy_macd_aggressive.py",
        backtest_file=repo_root / "src/backtest_macd_aggressive.py",
        log_file=repo_root / "logs/macd_aggressive_research_v2.log",
        journal_file=repo_root / "state/research_macd_aggressive_v2_journal.jsonl",
        heartbeat_file=repo_root / "state/research_macd_aggressive_v2_heartbeat.json",
        best_state_file=repo_root / "state/research_macd_aggressive_v2_best.json",
        best_strategy_file=repo_root / "backups/strategy_macd_aggressive_v2_best.py",
        stop_file=repo_root / "state/research_macd_aggressive_v2.stop",
        strategy_backup_file=repo_root / "backups/strategy_macd_aggressive_v2_candidate.py",
    )

    windows = WindowConfig(
        eval_start_date=os.getenv("MACD_V2_EVAL_START_DATE", os.getenv("MACD_EVAL_START_DATE", "2025-09-01")),
        eval_end_date=os.getenv("MACD_V2_EVAL_END_DATE", os.getenv("MACD_EVAL_END_DATE", "2026-03-31")),
        eval_window_days=_env_int("MACD_V2_EVAL_WINDOW_DAYS", 28),
        eval_step_days=_env_int("MACD_V2_EVAL_STEP_DAYS", 21),
        validation_days=_env_int("MACD_V2_VALIDATION_DAYS", 28),
    )

    gates = GateConfig(
        min_total_trades=_env_int("MACD_V2_MIN_TOTAL_TRADES", 30),
        min_eval_trades=_env_int("MACD_V2_MIN_EVAL_TRADES", 24),
        min_validation_trades=_env_int("MACD_V2_MIN_VALIDATION_TRADES", 5),
        min_positive_ratio=_env_float("MACD_V2_MIN_POSITIVE_RATIO", 0.30),
        max_drawdown_pct=_env_float("MACD_V2_MAX_DRAWDOWN_PCT", 50.0),
        max_liquidations=_env_int("MACD_V2_MAX_LIQUIDATIONS", 0),
        min_validation_return=_env_float("MACD_V2_MIN_VALIDATION_RETURN", -10.0),
        max_eval_validation_gap=_env_float("MACD_V2_MAX_EVAL_VALIDATION_GAP", 30.0),
        max_fee_drag_pct=_env_float("MACD_V2_MAX_FEE_DRAG_PCT", 6.0),
    )

    return ResearchRuntimeConfig(
        paths=paths,
        windows=windows,
        gates=gates,
        loop_interval_seconds=_env_int("MACD_V2_LOOP_INTERVAL_SECONDS", _env_int("MACD_LOOP_INTERVAL_SECONDS", 120)),
        provider_recovery_wait_seconds=_env_int("MACD_V2_PROVIDER_RECOVERY_WAIT_SECONDS", 90),
        failure_cooldown_seconds=_env_int("MACD_V2_FAILURE_COOLDOWN_SECONDS", 60),
        prompt_max_output_tokens=_env_int("MACD_V2_PROMPT_MAX_OUTPUT_TOKENS", 12000),
        max_recent_journal_entries=_env_int("MACD_V2_MAX_RECENT_JOURNAL_ENTRIES", 12),
        early_reject_after_windows=_env_int("MACD_V2_EARLY_REJECT_WINDOWS", 15),
        early_reject_sortino_threshold=_env_float("MACD_V2_EARLY_REJECT_SORTINO", -1.0),
    )
