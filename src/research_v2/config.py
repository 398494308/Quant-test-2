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


def _env_int_tuple(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = str(os.getenv(name, ",".join(str(item) for item in default))).strip()
    if not raw:
        return tuple()
    values: list[int] = []
    for item in raw.replace("；", ",").replace(";", ",").split(","):
        text = item.strip()
        if not text:
            continue
        values.append(int(text))
    return tuple(dict.fromkeys(value for value in values if value > 0))


def _env_text(name: str, default: str) -> str:
    return str(os.getenv(name, default)).strip()


def _load_scoring_config(windows: "WindowConfig") -> "ScoringConfig":
    risk_window_days = _env_int("MACD_V2_RISK_WINDOW_DAYS", windows.eval_window_days)
    risk_window_step_days = _env_int(
        "MACD_V2_RISK_WINDOW_STEP_DAYS",
        max(1, risk_window_days // 4),
    )
    return ScoringConfig(
        promotion_capture_weight=_env_float("MACD_V2_PROMOTION_CAPTURE_WEIGHT", 0.45),
        promotion_timed_return_weight=_env_float("MACD_V2_PROMOTION_TIMED_RETURN_WEIGHT", 0.30),
        promotion_sharpe_floor_weight=_env_float("MACD_V2_PROMOTION_SHARPE_FLOOR_WEIGHT", 0.25),
        promotion_drawdown_base_weight=_env_float("MACD_V2_PROMOTION_DRAWDOWN_BASE_WEIGHT", 0.20),
        promotion_drawdown_knee=_env_float("MACD_V2_PROMOTION_DRAWDOWN_KNEE", 1.25),
        promotion_drawdown_excess_weight=_env_float("MACD_V2_PROMOTION_DRAWDOWN_EXCESS_WEIGHT", 1.00),
        robustness_penalty_cap=_env_float("MACD_V2_ROBUSTNESS_PENALTY_CAP", 0.25),
        robustness_gap_warn_threshold=_env_float("MACD_V2_ROBUSTNESS_GAP_WARN_THRESHOLD", 0.20),
        robustness_gap_fail_threshold=_env_float("MACD_V2_ROBUSTNESS_GAP_FAIL_THRESHOLD", 0.27),
        robustness_gap_warn_penalty=_env_float("MACD_V2_ROBUSTNESS_GAP_WARN_PENALTY", 0.05),
        robustness_gap_fail_penalty=_env_float("MACD_V2_ROBUSTNESS_GAP_FAIL_PENALTY", 0.10),
        robustness_block_std_warn_threshold=_env_float("MACD_V2_ROBUSTNESS_BLOCK_STD_WARN_THRESHOLD", 0.22),
        robustness_block_std_fail_threshold=_env_float("MACD_V2_ROBUSTNESS_BLOCK_STD_FAIL_THRESHOLD", 0.30),
        robustness_block_std_warn_penalty=_env_float("MACD_V2_ROBUSTNESS_BLOCK_STD_WARN_PENALTY", 0.03),
        robustness_block_std_fail_penalty=_env_float("MACD_V2_ROBUSTNESS_BLOCK_STD_FAIL_PENALTY", 0.06),
        robustness_block_floor_warn_threshold=_env_float("MACD_V2_ROBUSTNESS_BLOCK_FLOOR_WARN_THRESHOLD", 0.15),
        robustness_block_floor_fail_threshold=_env_float("MACD_V2_ROBUSTNESS_BLOCK_FLOOR_FAIL_THRESHOLD", 0.00),
        robustness_block_floor_warn_penalty=_env_float("MACD_V2_ROBUSTNESS_BLOCK_FLOOR_WARN_PENALTY", 0.03),
        robustness_block_floor_fail_penalty=_env_float("MACD_V2_ROBUSTNESS_BLOCK_FLOOR_FAIL_PENALTY", 0.06),
        robustness_block_tail_warn_threshold=_env_float("MACD_V2_ROBUSTNESS_BLOCK_TAIL_WARN_THRESHOLD", 0.12),
        robustness_block_tail_fail_threshold=_env_float("MACD_V2_ROBUSTNESS_BLOCK_TAIL_FAIL_THRESHOLD", 0.22),
        robustness_block_tail_warn_penalty=_env_float("MACD_V2_ROBUSTNESS_BLOCK_TAIL_WARN_PENALTY", 0.03),
        robustness_block_tail_fail_penalty=_env_float("MACD_V2_ROBUSTNESS_BLOCK_TAIL_FAIL_PENALTY", 0.06),
        robustness_block_fail_penalty_per_block=_env_float("MACD_V2_ROBUSTNESS_BLOCK_FAIL_PENALTY_PER_BLOCK", 0.03),
        robustness_block_fail_penalty_cap_count=_env_int("MACD_V2_ROBUSTNESS_BLOCK_FAIL_PENALTY_CAP_COUNT", 2),
        robustness_sharpe_gap_warn_threshold=_env_float("MACD_V2_ROBUSTNESS_SHARPE_GAP_WARN_THRESHOLD", 0.35),
        robustness_sharpe_gap_fail_threshold=_env_float("MACD_V2_ROBUSTNESS_SHARPE_GAP_FAIL_THRESHOLD", 0.60),
        robustness_sharpe_gap_warn_penalty=_env_float("MACD_V2_ROBUSTNESS_SHARPE_GAP_WARN_PENALTY", 0.03),
        robustness_sharpe_gap_fail_penalty=_env_float("MACD_V2_ROBUSTNESS_SHARPE_GAP_FAIL_PENALTY", 0.06),
        robustness_sharpe_floor_warn_threshold=_env_float("MACD_V2_ROBUSTNESS_SHARPE_FLOOR_WARN_THRESHOLD", 1.00),
        robustness_sharpe_floor_fail_threshold=_env_float("MACD_V2_ROBUSTNESS_SHARPE_FLOOR_FAIL_THRESHOLD", 0.75),
        robustness_sharpe_floor_warn_penalty=_env_float("MACD_V2_ROBUSTNESS_SHARPE_FLOOR_WARN_PENALTY", 0.03),
        robustness_sharpe_floor_fail_penalty=_env_float("MACD_V2_ROBUSTNESS_SHARPE_FLOOR_FAIL_PENALTY", 0.06),
        robustness_plateau_center_gap_warn_threshold=_env_float("MACD_V2_ROBUSTNESS_PLATEAU_CENTER_GAP_WARN_THRESHOLD", 0.05),
        robustness_plateau_center_gap_fail_threshold=_env_float("MACD_V2_ROBUSTNESS_PLATEAU_CENTER_GAP_FAIL_THRESHOLD", 0.10),
        robustness_plateau_center_gap_warn_penalty=_env_float("MACD_V2_ROBUSTNESS_PLATEAU_CENTER_GAP_WARN_PENALTY", 0.03),
        robustness_plateau_center_gap_fail_penalty=_env_float("MACD_V2_ROBUSTNESS_PLATEAU_CENTER_GAP_FAIL_PENALTY", 0.06),
        robustness_plateau_score_span_threshold=_env_float("MACD_V2_ROBUSTNESS_PLATEAU_SCORE_SPAN_THRESHOLD", 0.12),
        robustness_plateau_drawdown_span_threshold=_env_float("MACD_V2_ROBUSTNESS_PLATEAU_DRAWDOWN_SPAN_THRESHOLD", 8.0),
        robustness_plateau_extra_penalty=_env_float("MACD_V2_ROBUSTNESS_PLATEAU_EXTRA_PENALTY", 0.03),
        risk_window_days=max(1, risk_window_days),
        risk_window_step_days=max(1, risk_window_step_days),
        drawdown_risk_tail_quantile=_env_float("MACD_V2_DRAWDOWN_RISK_TAIL_QUANTILE", 0.75),
        drawdown_risk_tail_weight=_env_float("MACD_V2_DRAWDOWN_RISK_TAIL_WEIGHT", 0.60),
        drawdown_risk_scale_pct=max(0.1, _env_float("MACD_V2_DRAWDOWN_RISK_SCALE_PCT", 6.0)),
    )


# ==================== 路径配置 ====================


@dataclass(frozen=True)
class ResearchPaths:
    repo_root: Path
    strategy_file: Path
    backtest_file: Path
    operator_focus_file: Path
    champion_review_file: Path
    log_file: Path
    model_call_log_file: Path
    journal_file: Path
    memory_dir: Path
    session_state_file: Path
    agent_workspace_dir: Path
    heartbeat_file: Path
    best_state_file: Path
    best_strategy_file: Path
    champion_strategy_file: Path
    round_artifacts_dir: Path
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
    min_validation_closed_trades: int
    validation_block_count: int = 3
    min_validation_block_floor: float = -0.20
    max_validation_block_failures: int = 1


@dataclass(frozen=True)
class ScoringConfig:
    promotion_capture_weight: float = 0.45
    promotion_timed_return_weight: float = 0.30
    promotion_sharpe_floor_weight: float = 0.25
    promotion_drawdown_base_weight: float = 0.20
    promotion_drawdown_knee: float = 1.25
    promotion_drawdown_excess_weight: float = 1.00
    robustness_penalty_cap: float = 0.25
    robustness_gap_warn_threshold: float = 0.20
    robustness_gap_fail_threshold: float = 0.27
    robustness_gap_warn_penalty: float = 0.05
    robustness_gap_fail_penalty: float = 0.10
    robustness_block_std_warn_threshold: float = 0.22
    robustness_block_std_fail_threshold: float = 0.30
    robustness_block_std_warn_penalty: float = 0.03
    robustness_block_std_fail_penalty: float = 0.06
    robustness_block_floor_warn_threshold: float = 0.15
    robustness_block_floor_fail_threshold: float = 0.00
    robustness_block_floor_warn_penalty: float = 0.03
    robustness_block_floor_fail_penalty: float = 0.06
    robustness_block_tail_warn_threshold: float = 0.12
    robustness_block_tail_fail_threshold: float = 0.22
    robustness_block_tail_warn_penalty: float = 0.03
    robustness_block_tail_fail_penalty: float = 0.06
    robustness_block_fail_penalty_per_block: float = 0.03
    robustness_block_fail_penalty_cap_count: int = 2
    robustness_sharpe_gap_warn_threshold: float = 0.35
    robustness_sharpe_gap_fail_threshold: float = 0.60
    robustness_sharpe_gap_warn_penalty: float = 0.03
    robustness_sharpe_gap_fail_penalty: float = 0.06
    robustness_sharpe_floor_warn_threshold: float = 1.00
    robustness_sharpe_floor_fail_threshold: float = 0.75
    robustness_sharpe_floor_warn_penalty: float = 0.03
    robustness_sharpe_floor_fail_penalty: float = 0.06
    robustness_plateau_center_gap_warn_threshold: float = 0.05
    robustness_plateau_center_gap_fail_threshold: float = 0.10
    robustness_plateau_center_gap_warn_penalty: float = 0.03
    robustness_plateau_center_gap_fail_penalty: float = 0.06
    robustness_plateau_score_span_threshold: float = 0.12
    robustness_plateau_drawdown_span_threshold: float = 8.0
    robustness_plateau_extra_penalty: float = 0.03
    risk_window_days: int = 28
    risk_window_step_days: int = 7
    drawdown_risk_tail_quantile: float = 0.75
    drawdown_risk_tail_weight: float = 0.60
    drawdown_risk_scale_pct: float = 6.0


@dataclass(frozen=True)
class ResearchRuntimeConfig:
    paths: ResearchPaths
    windows: WindowConfig
    gates: GateConfig
    scoring: ScoringConfig
    promotion_accept_margin: float
    promotion_accept_quality_drop_margin: float
    loop_interval_seconds: int
    provider_recovery_wait_seconds: int
    failure_cooldown_seconds: int
    prompt_max_output_tokens: int
    max_recent_journal_entries: int
    early_reject_after_windows: int
    early_reject_milestones: tuple[int, ...]
    early_reject_min_segments: int
    early_reject_trend_score_threshold: float
    early_reject_hit_rate_threshold: float
    smoke_window_count: int
    exit_range_scan_enabled: bool
    exit_range_scan_max_values: int
    exit_range_scan_workers: int
    exit_range_scan_windows: int
    exit_range_scan_max_fee_mult: float
    plateau_probe_enabled: bool
    max_repair_attempts: int
    max_no_edit_repair_attempts: int
    max_exploration_regen_attempts: int
    max_consecutive_no_edit_failures_before_stop: int


# ==================== 对外入口 ====================


def load_research_runtime_config(repo_root: Path) -> ResearchRuntimeConfig:
    _load_env_files(repo_root)

    paths = ResearchPaths(
        repo_root=repo_root,
        strategy_file=repo_root / "src/strategy_macd_aggressive.py",
        backtest_file=repo_root / "src/backtest_macd_aggressive.py",
        operator_focus_file=repo_root / "config/research_v2_operator_focus.md",
        champion_review_file=repo_root / "config/research_v2_champion_review.md",
        log_file=repo_root / "logs/macd_aggressive_research_v2.log",
        model_call_log_file=repo_root / "logs/macd_aggressive_research_v2_model_calls.jsonl",
        journal_file=repo_root / "state/research_macd_aggressive_v2_journal.jsonl",
        memory_dir=repo_root / "state/research_macd_aggressive_v2_memory",
        session_state_file=repo_root / "state/research_macd_aggressive_v2_session.json",
        agent_workspace_dir=repo_root / "state/research_macd_aggressive_v2_agent_workspace",
        heartbeat_file=repo_root / "state/research_macd_aggressive_v2_heartbeat.json",
        best_state_file=repo_root / "state/research_macd_aggressive_v2_best.json",
        best_strategy_file=repo_root / "backups/strategy_macd_aggressive_v2_best.py",
        champion_strategy_file=repo_root / "backups/strategy_macd_aggressive_v2_champion.py",
        round_artifacts_dir=repo_root / "backups/research_v2_round_artifacts",
        stop_file=repo_root / "state/research_macd_aggressive_v2.stop",
        strategy_backup_file=repo_root / "backups/strategy_macd_aggressive_v2_candidate.py",
    )

    windows = WindowConfig(
        development_start_date=os.getenv("MACD_V2_DEVELOPMENT_START_DATE", "2023-07-01"),
        development_end_date=os.getenv("MACD_V2_DEVELOPMENT_END_DATE", "2024-12-31"),
        validation_start_date=os.getenv("MACD_V2_VALIDATION_START_DATE", "2025-01-01"),
        validation_end_date=os.getenv("MACD_V2_VALIDATION_END_DATE", "2025-12-31"),
        test_start_date=os.getenv("MACD_V2_TEST_START_DATE", "2026-01-01"),
        test_end_date=os.getenv("MACD_V2_TEST_END_DATE", "2026-04-20"),
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
        max_fee_drag_pct=_env_float("MACD_V2_MAX_FEE_DRAG_PCT", 11.5),
        min_validation_closed_trades=_env_int("MACD_V2_MIN_VALIDATION_CLOSED_TRADES", 180),
        validation_block_count=_env_int("MACD_V2_VALIDATION_BLOCK_COUNT", 3),
        min_validation_block_floor=_env_float("MACD_V2_MIN_VALIDATION_BLOCK_FLOOR", -0.35),
        max_validation_block_failures=_env_int("MACD_V2_MAX_VALIDATION_BLOCK_FAILURES", 1),
    )
    scoring = _load_scoring_config(windows)

    return ResearchRuntimeConfig(
        paths=paths,
        windows=windows,
        gates=gates,
        scoring=scoring,
        promotion_accept_margin=_env_float("MACD_V2_PROMOTION_ACCEPT_MARGIN", 0.02),
        promotion_accept_quality_drop_margin=_env_float(
            "MACD_V2_PROMOTION_ACCEPT_QUALITY_DROP_MARGIN",
            0.03,
        ),
        loop_interval_seconds=_env_int("MACD_V2_LOOP_INTERVAL_SECONDS", 10),
        provider_recovery_wait_seconds=_env_int("MACD_V2_PROVIDER_RECOVERY_WAIT_SECONDS", 90),
        failure_cooldown_seconds=_env_int("MACD_V2_FAILURE_COOLDOWN_SECONDS", 10),
        prompt_max_output_tokens=_env_int("MACD_V2_PROMPT_MAX_OUTPUT_TOKENS", 12000),
        max_recent_journal_entries=_env_int("MACD_V2_MAX_RECENT_JOURNAL_ENTRIES", 12),
        early_reject_after_windows=_env_int("MACD_V2_EARLY_REJECT_WINDOWS", 15),
        early_reject_milestones=_env_int_tuple("MACD_V2_EARLY_REJECT_MILESTONES", (10, 18, 26)),
        early_reject_min_segments=_env_int("MACD_V2_EARLY_REJECT_MIN_SEGMENTS", 4),
        early_reject_trend_score_threshold=_env_float("MACD_V2_EARLY_REJECT_TREND_SCORE", -0.10),
        early_reject_hit_rate_threshold=_env_float("MACD_V2_EARLY_REJECT_HIT_RATE", 0.15),
        smoke_window_count=_env_int("MACD_V2_SMOKE_WINDOW_COUNT", 5),
        exit_range_scan_enabled=_env_int("MACD_V2_EXIT_RANGE_SCAN_ENABLED", 1) > 0,
        exit_range_scan_max_values=_env_int("MACD_V2_EXIT_RANGE_SCAN_MAX_VALUES", 3),
        exit_range_scan_workers=_env_int("MACD_V2_EXIT_RANGE_SCAN_WORKERS", 2),
        exit_range_scan_windows=_env_int("MACD_V2_EXIT_RANGE_SCAN_WINDOWS", 3),
        exit_range_scan_max_fee_mult=_env_float("MACD_V2_EXIT_RANGE_SCAN_MAX_FEE_MULT", 1.5),
        plateau_probe_enabled=_env_int("MACD_V2_PLATEAU_PROBE_ENABLED", 1) > 0,
        max_repair_attempts=_env_int("MACD_V2_MAX_REPAIR_ATTEMPTS", 2),
        max_no_edit_repair_attempts=_env_int("MACD_V2_MAX_NO_EDIT_REPAIR_ATTEMPTS", 1),
        max_exploration_regen_attempts=_env_int("MACD_V2_MAX_EXPLORATION_REGEN_ATTEMPTS", 2),
        max_consecutive_no_edit_failures_before_stop=_env_int(
            "MACD_V2_MAX_CONSECUTIVE_NO_EDIT_FAILURES_BEFORE_STOP",
            3,
        ),
    )
