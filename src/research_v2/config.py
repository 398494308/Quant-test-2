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


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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
    holdout_days: int


@dataclass(frozen=True)
class GateConfig:
    min_total_trades: int
    min_eval_trades: int
    min_holdout_trades: int
    min_positive_ratio: float
    max_drawdown_pct: float
    max_liquidations: int
    min_holdout_return: float
    max_eval_holdout_gap: float
    max_fee_drag_pct: float
    min_stress_return: float


@dataclass(frozen=True)
class StressScenario:
    name: str
    label: str
    fee_multiplier: float
    slippage_multiplier: float
    entry_delay_delta: int


@dataclass(frozen=True)
class ScoringConfig:
    """评分函数系数。

    每个系数控制评分公式中某一项的权重或阈值。
    如果你觉得某个惩罚太重或太轻，直接在 env 里覆盖对应的环境变量即可。

    命名规则：
    - *_weight: 乘法权重，越大该项对总分影响越大
    - *_threshold: 超过这个值才开始惩罚
    - *_clamp_*: 截断上下界，防止极端值主导
    """
    # ---- 收益项权重（加分） ----
    # 加权评估收益的权重，体现"近期窗口更重要"
    weighted_return_weight: float
    # 中位数收益的权重，体现"典型表现"
    median_return_weight: float
    # P25 收益的权重，体现"较差情况下的表现"
    p25_return_weight: float
    # 留出窗口收益的权重
    holdout_return_weight: float
    # Sortino 指标的乘数（下行风险调整后收益）
    sortino_weight: float
    # Sharpe 指标的乘数（整体风险调整后收益）
    sharpe_weight: float
    # 盈亏比（profit_factor - 1）的乘数
    profit_factor_weight: float
    # Sortino/Sharpe 截断范围
    risk_ratio_clamp_low: float
    risk_ratio_clamp_high: float
    # 盈亏比截断范围
    profit_factor_clamp_low: float
    profit_factor_clamp_high: float

    # ---- 惩罚项（扣分） ----
    # 回撤惩罚：超过 threshold 后每多 1% 扣多少分
    drawdown_threshold: float
    drawdown_weight: float
    # 手续费惩罚：超过 threshold 后每多 1% 扣多少分
    fee_drag_threshold: float
    fee_drag_weight: float
    # 爆仓惩罚：每次爆仓扣多少分
    liquidation_weight: float
    # 尾部风险惩罚：最差窗口亏损超过 threshold 后每多 1% 扣多少分
    tail_loss_threshold: float
    tail_loss_weight: float
    # 一致性惩罚：窗口标准差超过 threshold 后每多 1% 扣多少分
    consistency_threshold: float
    consistency_weight: float
    # 留出差值惩罚：eval 与 holdout 落差的乘数
    holdout_gap_weight: float
    # 压力测试惩罚：最差压力收益低于 threshold 后每多 1% 扣多少分
    stress_loss_threshold: float
    stress_loss_weight: float


@dataclass(frozen=True)
class ResearchRuntimeConfig:
    paths: ResearchPaths
    windows: WindowConfig
    gates: GateConfig
    scoring: ScoringConfig
    stress_enabled: bool
    stress_scenarios: tuple[StressScenario, ...]
    loop_interval_seconds: int
    provider_recovery_wait_seconds: int
    failure_cooldown_seconds: int
    local_fallback_enabled: bool
    force_local_fallback: bool
    provider_empty_output_fallback_seconds: int
    local_param_mutation_attempts: int
    prompt_max_output_tokens: int
    max_recent_journal_entries: int


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
        holdout_days=_env_int("MACD_V2_HOLDOUT_DAYS", 28),
    )

    gates = GateConfig(
        min_total_trades=_env_int("MACD_V2_MIN_TOTAL_TRADES", 30),
        min_eval_trades=_env_int("MACD_V2_MIN_EVAL_TRADES", 24),
        min_holdout_trades=_env_int("MACD_V2_MIN_HOLDOUT_TRADES", 8),
        min_positive_ratio=_env_float("MACD_V2_MIN_POSITIVE_RATIO", 0.40),
        max_drawdown_pct=_env_float("MACD_V2_MAX_DRAWDOWN_PCT", 45.0),
        max_liquidations=_env_int("MACD_V2_MAX_LIQUIDATIONS", 0),
        min_holdout_return=_env_float("MACD_V2_MIN_HOLDOUT_RETURN", 0.0),
        max_eval_holdout_gap=_env_float("MACD_V2_MAX_EVAL_HOLDOUT_GAP", 22.0),
        max_fee_drag_pct=_env_float("MACD_V2_MAX_FEE_DRAG_PCT", 6.0),
        min_stress_return=_env_float("MACD_V2_MIN_STRESS_RETURN", -8.0),
    )

    stress_scenarios = (
        StressScenario(
            name="fee_slippage_plus",
            label="手续费/滑点上调",
            fee_multiplier=_env_float("MACD_V2_STRESS_FEE_MULTIPLIER", 1.25),
            slippage_multiplier=_env_float("MACD_V2_STRESS_SLIPPAGE_MULTIPLIER", 1.5),
            entry_delay_delta=_env_int("MACD_V2_STRESS_ENTRY_DELAY_DELTA", 1),
        ),
    )

    scoring = ScoringConfig(
        # 收益项
        weighted_return_weight=_env_float("MACD_V2_SCORE_WEIGHTED_RETURN_W", 0.42),
        median_return_weight=_env_float("MACD_V2_SCORE_MEDIAN_RETURN_W", 0.24),
        p25_return_weight=_env_float("MACD_V2_SCORE_P25_RETURN_W", 0.20),
        holdout_return_weight=_env_float("MACD_V2_SCORE_HOLDOUT_RETURN_W", 0.18),
        sortino_weight=_env_float("MACD_V2_SCORE_SORTINO_W", 4.0),
        sharpe_weight=_env_float("MACD_V2_SCORE_SHARPE_W", 2.0),
        profit_factor_weight=_env_float("MACD_V2_SCORE_PROFIT_FACTOR_W", 3.0),
        risk_ratio_clamp_low=_env_float("MACD_V2_SCORE_RISK_CLAMP_LOW", -2.5),
        risk_ratio_clamp_high=_env_float("MACD_V2_SCORE_RISK_CLAMP_HIGH", 5.0),
        profit_factor_clamp_low=_env_float("MACD_V2_SCORE_PF_CLAMP_LOW", -1.0),
        profit_factor_clamp_high=_env_float("MACD_V2_SCORE_PF_CLAMP_HIGH", 2.5),
        # 惩罚项
        drawdown_threshold=_env_float("MACD_V2_SCORE_DD_THRESHOLD", 32.0),
        drawdown_weight=_env_float("MACD_V2_SCORE_DD_W", 0.45),
        fee_drag_threshold=_env_float("MACD_V2_SCORE_FEE_THRESHOLD", 4.0),
        fee_drag_weight=_env_float("MACD_V2_SCORE_FEE_W", 1.60),
        liquidation_weight=_env_float("MACD_V2_SCORE_LIQ_W", 8.0),
        tail_loss_threshold=_env_float("MACD_V2_SCORE_TAIL_THRESHOLD", 10.0),
        tail_loss_weight=_env_float("MACD_V2_SCORE_TAIL_W", 0.28),
        consistency_threshold=_env_float("MACD_V2_SCORE_CONSISTENCY_THRESHOLD", 16.0),
        consistency_weight=_env_float("MACD_V2_SCORE_CONSISTENCY_W", 0.25),
        holdout_gap_weight=_env_float("MACD_V2_SCORE_HOLDOUT_GAP_W", 0.18),
        stress_loss_threshold=_env_float("MACD_V2_SCORE_STRESS_THRESHOLD", 4.0),
        stress_loss_weight=_env_float("MACD_V2_SCORE_STRESS_W", 0.20),
    )

    return ResearchRuntimeConfig(
        paths=paths,
        windows=windows,
        gates=gates,
        scoring=scoring,
        stress_enabled=_env_flag("MACD_V2_STRESS_ENABLED", True),
        stress_scenarios=stress_scenarios,
        loop_interval_seconds=_env_int("MACD_V2_LOOP_INTERVAL_SECONDS", _env_int("MACD_LOOP_INTERVAL_SECONDS", 120)),
        provider_recovery_wait_seconds=_env_int("MACD_V2_PROVIDER_RECOVERY_WAIT_SECONDS", 90),
        failure_cooldown_seconds=_env_int("MACD_V2_FAILURE_COOLDOWN_SECONDS", 60),
        local_fallback_enabled=_env_flag("MACD_V2_LOCAL_FALLBACK_ENABLED", True),
        force_local_fallback=_env_flag("MACD_V2_FORCE_LOCAL_FALLBACK", False),
        provider_empty_output_fallback_seconds=_env_int("MACD_V2_PROVIDER_EMPTY_OUTPUT_FALLBACK_SECONDS", 1800),
        local_param_mutation_attempts=_env_int("MACD_V2_LOCAL_PARAM_MUTATION_ATTEMPTS", 12),
        prompt_max_output_tokens=_env_int("MACD_V2_PROMPT_MAX_OUTPUT_TOKENS", 12000),
        max_recent_journal_entries=_env_int("MACD_V2_MAX_RECENT_JOURNAL_ENTRIES", 12),
    )
