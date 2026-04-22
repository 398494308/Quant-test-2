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


def _env_text(name: str, default: str) -> str:
    return str(os.getenv(name, default)).strip()


# ==================== 路径配置 ====================


@dataclass(frozen=True)
class ResearchPaths:
    repo_root: Path
    strategy_file: Path
    backtest_file: Path
    log_file: Path
    model_call_log_file: Path
    journal_file: Path
    memory_dir: Path
    session_state_file: Path
    agent_workspace_dir: Path
    heartbeat_file: Path
    best_state_file: Path
    best_strategy_file: Path
    stop_file: Path
    strategy_backup_file: Path


@dataclass(frozen=True)
class WindowConfig:
    development_start_date: str
    development_end_date: str
    validation_start_date: str
    validation_end_date: str
    test_start_date: str
    test_end_date: str
    eval_window_days: int
    eval_step_days: int


@dataclass(frozen=True)
class GateConfig:
    min_development_mean_score: float
    min_development_median_score: float
    min_validation_hit_rate: float
    min_validation_trend_score: float
    max_dev_validation_gap: float
    min_validation_bull_capture: float
    min_validation_bear_capture: float
    max_fee_drag_pct: float
    validation_block_count: int = 3
    min_validation_block_floor: float = -0.20
    max_validation_block_failures: int = 1


@dataclass(frozen=True)
class ResearchRuntimeConfig:
    paths: ResearchPaths
    windows: WindowConfig
    gates: GateConfig
    base_factor_change_mode: str
    loop_interval_seconds: int
    provider_recovery_wait_seconds: int
    failure_cooldown_seconds: int
    prompt_max_output_tokens: int
    max_recent_journal_entries: int
    early_reject_after_windows: int
    early_reject_min_segments: int
    early_reject_trend_score_threshold: float
    early_reject_hit_rate_threshold: float
    smoke_window_count: int
    max_repair_attempts: int
    max_no_edit_repair_attempts: int
    max_exploration_regen_attempts: int
    max_consecutive_no_edit_failures_before_stop: int
    cluster_lock_rounds_stage1: int
    cluster_lock_rounds_stage2: int
    cluster_lock_rounds_stage3: int
    promotion_min_delta: float


# ==================== 对外入口 ====================


def load_research_runtime_config(repo_root: Path) -> ResearchRuntimeConfig:
    _load_env_files(repo_root)

    paths = ResearchPaths(
        repo_root=repo_root,
        strategy_file=repo_root / "src/strategy_macd_aggressive.py",
        backtest_file=repo_root / "src/backtest_macd_aggressive.py",
        log_file=repo_root / "logs/macd_aggressive_research_v2.log",
        model_call_log_file=repo_root / "logs/macd_aggressive_research_v2_model_calls.jsonl",
        journal_file=repo_root / "state/research_macd_aggressive_v2_journal.jsonl",
        memory_dir=repo_root / "state/research_macd_aggressive_v2_memory",
        session_state_file=repo_root / "state/research_macd_aggressive_v2_session.json",
        agent_workspace_dir=repo_root / "state/research_macd_aggressive_v2_agent_workspace",
        heartbeat_file=repo_root / "state/research_macd_aggressive_v2_heartbeat.json",
        best_state_file=repo_root / "state/research_macd_aggressive_v2_best.json",
        best_strategy_file=repo_root / "backups/strategy_macd_aggressive_v2_best.py",
        stop_file=repo_root / "state/research_macd_aggressive_v2.stop",
        strategy_backup_file=repo_root / "backups/strategy_macd_aggressive_v2_candidate.py",
    )

    windows = WindowConfig(
        development_start_date=os.getenv("MACD_V2_DEVELOPMENT_START_DATE", "2023-07-01"),
        development_end_date=os.getenv("MACD_V2_DEVELOPMENT_END_DATE", "2024-12-31"),
        validation_start_date=os.getenv("MACD_V2_VALIDATION_START_DATE", "2025-01-01"),
        validation_end_date=os.getenv("MACD_V2_VALIDATION_END_DATE", "2025-12-31"),
        test_start_date=os.getenv("MACD_V2_TEST_START_DATE", "2026-01-01"),
        test_end_date=os.getenv("MACD_V2_TEST_END_DATE", "2026-03-31"),
        eval_window_days=_env_int("MACD_V2_EVAL_WINDOW_DAYS", 28),
        eval_step_days=_env_int("MACD_V2_EVAL_STEP_DAYS", 21),
    )

    gates = GateConfig(
        min_development_mean_score=_env_float("MACD_V2_MIN_DEVELOPMENT_MEAN_SCORE", 0.10),
        min_development_median_score=_env_float("MACD_V2_MIN_DEVELOPMENT_MEDIAN_SCORE", 0.00),
        min_validation_hit_rate=_env_float("MACD_V2_MIN_VALIDATION_HIT_RATE", 0.35),
        min_validation_trend_score=_env_float("MACD_V2_MIN_VALIDATION_TREND_SCORE", 0.05),
        max_dev_validation_gap=_env_float("MACD_V2_MAX_DEV_VALIDATION_GAP", 0.30),
        min_validation_bull_capture=_env_float("MACD_V2_MIN_VALIDATION_BULL_CAPTURE", 0.00),
        min_validation_bear_capture=_env_float("MACD_V2_MIN_VALIDATION_BEAR_CAPTURE", 0.00),
        max_fee_drag_pct=_env_float("MACD_V2_MAX_FEE_DRAG_PCT", 8.0),
        validation_block_count=_env_int("MACD_V2_VALIDATION_BLOCK_COUNT", 3),
        min_validation_block_floor=_env_float("MACD_V2_MIN_VALIDATION_BLOCK_FLOOR", -0.35),
        max_validation_block_failures=_env_int("MACD_V2_MAX_VALIDATION_BLOCK_FAILURES", 1),
    )

    return ResearchRuntimeConfig(
        paths=paths,
        windows=windows,
        gates=gates,
        base_factor_change_mode=_env_text("MACD_V2_FACTOR_CHANGE_MODE", "default"),
        loop_interval_seconds=_env_int("MACD_V2_LOOP_INTERVAL_SECONDS", 120),
        provider_recovery_wait_seconds=_env_int("MACD_V2_PROVIDER_RECOVERY_WAIT_SECONDS", 90),
        failure_cooldown_seconds=_env_int("MACD_V2_FAILURE_COOLDOWN_SECONDS", 10),
        prompt_max_output_tokens=_env_int("MACD_V2_PROMPT_MAX_OUTPUT_TOKENS", 12000),
        max_recent_journal_entries=_env_int("MACD_V2_MAX_RECENT_JOURNAL_ENTRIES", 12),
        early_reject_after_windows=_env_int("MACD_V2_EARLY_REJECT_WINDOWS", 15),
        early_reject_min_segments=_env_int("MACD_V2_EARLY_REJECT_MIN_SEGMENTS", 4),
        early_reject_trend_score_threshold=_env_float("MACD_V2_EARLY_REJECT_TREND_SCORE", -0.10),
        early_reject_hit_rate_threshold=_env_float("MACD_V2_EARLY_REJECT_HIT_RATE", 0.15),
        smoke_window_count=_env_int("MACD_V2_SMOKE_WINDOW_COUNT", 3),
        max_repair_attempts=_env_int("MACD_V2_MAX_REPAIR_ATTEMPTS", 2),
        max_no_edit_repair_attempts=_env_int("MACD_V2_MAX_NO_EDIT_REPAIR_ATTEMPTS", 1),
        max_exploration_regen_attempts=_env_int("MACD_V2_MAX_EXPLORATION_REGEN_ATTEMPTS", 2),
        max_consecutive_no_edit_failures_before_stop=_env_int(
            "MACD_V2_MAX_CONSECUTIVE_NO_EDIT_FAILURES_BEFORE_STOP",
            3,
        ),
        cluster_lock_rounds_stage1=_env_int("MACD_V2_CLUSTER_LOCK_ROUNDS_STAGE1", 3),
        cluster_lock_rounds_stage2=_env_int("MACD_V2_CLUSTER_LOCK_ROUNDS_STAGE2", 6),
        cluster_lock_rounds_stage3=_env_int("MACD_V2_CLUSTER_LOCK_ROUNDS_STAGE3", 10),
        promotion_min_delta=_env_float("MACD_V2_PROMOTION_MIN_DELTA", 0.02),
    )
