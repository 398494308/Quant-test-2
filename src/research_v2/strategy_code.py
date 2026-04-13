#!/usr/bin/env python3
"""策略源码的加载、校验与本地兜底改写。"""
from __future__ import annotations

import ast
import hashlib
import pprint
import random
import re
from dataclasses import dataclass
from pathlib import Path


# ==================== 数据结构 ====================


class StrategySourceError(RuntimeError):
    """候选策略源码不合法。"""


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_id: str
    hypothesis: str
    change_plan: str
    change_tags: tuple[str, ...]
    edited_regions: tuple[str, ...]
    expected_effects: tuple[str, ...]
    strategy_code: str


# ==================== 源码基础操作 ====================


PARAM_BLOCK_PATTERN = re.compile(r"# PARAMS_START\s*\nPARAMS = (.*?)\n# PARAMS_END", re.DOTALL)

LOCAL_PARAM_GROUPS: dict[str, tuple[str, ...]] = {
    "breakout_entry": (
        "breakout_adx_min",
        "breakout_lookback",
        "breakout_rsi_min",
        "breakout_rsi_max",
        "breakout_volume_ratio_min",
        "breakout_body_ratio_min",
        "breakout_close_pos_min",
    ),
    "breakdown_entry": (
        "breakdown_adx_min",
        "breakdown_lookback",
        "breakdown_rsi_min",
        "breakdown_rsi_max",
        "breakdown_volume_ratio_min",
        "breakdown_body_ratio_min",
        "breakdown_close_pos_max",
    ),
    "trend_confirmation": (
        "intraday_adx_min",
        "hourly_adx_min",
        "fourh_adx_min",
        "hourly_ema_fast",
        "hourly_ema_slow",
        "fourh_ema_fast",
        "fourh_ema_slow",
    ),
    "timing_core": (
        "macd_fast",
        "macd_slow",
        "macd_signal",
        "intraday_ema_fast",
        "intraday_ema_slow",
        "volume_lookback",
    ),
}


def load_strategy_source(path: Path) -> str:
    return path.read_text()


def normalize_strategy_source(source: str) -> str:
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def write_strategy_source(path: Path, source: str) -> None:
    path.write_text(normalize_strategy_source(source))


def source_hash(source: str) -> str:
    return hashlib.sha256(normalize_strategy_source(source).encode("utf-8")).hexdigest()


def extract_params(source: str) -> dict[str, object]:
    match = PARAM_BLOCK_PATTERN.search(source)
    if match is None:
        raise StrategySourceError("missing PARAMS block markers")
    try:
        params = ast.literal_eval(match.group(1))
    except Exception as exc:
        raise StrategySourceError(f"failed to parse PARAMS block: {exc}") from exc
    if not isinstance(params, dict):
        raise StrategySourceError("PARAMS block is not a dict")
    return params


def replace_params(source: str, params: dict[str, object]) -> str:
    replacement = "# PARAMS_START\nPARAMS = " + pprint.pformat(params, sort_dicts=True) + "\n# PARAMS_END"
    updated, count = PARAM_BLOCK_PATTERN.subn(replacement, source, count=1)
    if count != 1:
        raise StrategySourceError("failed to replace PARAMS block")
    return normalize_strategy_source(updated)


def build_diff_summary(old_source: str, new_source: str, limit: int = 24) -> list[str]:
    import difflib

    lines = list(
        difflib.unified_diff(
            normalize_strategy_source(old_source).splitlines(),
            normalize_strategy_source(new_source).splitlines(),
            lineterm="",
        )
    )
    filtered = [line for line in lines if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    return filtered[:limit]


def validate_strategy_source(source: str) -> None:
    normalized = normalize_strategy_source(source)
    try:
        tree = ast.parse(normalized)
    except SyntaxError as exc:
        raise StrategySourceError(f"strategy source has syntax error: line {exc.lineno} {exc.msg}") from exc

    function_names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    required_functions = {"strategy", "_is_sideways_regime", "_trend_quality_ok", "_trend_followthrough_ok"}
    missing_functions = required_functions - function_names
    if missing_functions:
        raise StrategySourceError(f"missing required functions: {sorted(missing_functions)}")

    if not any(isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "PARAMS" for target in node.targets) for node in tree.body):
        raise StrategySourceError("missing top-level PARAMS assignment")

    banned_import_modules = {"requests", "subprocess", "socket", "asyncio"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in banned_import_modules:
                    raise StrategySourceError(f"banned import in strategy source: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".", 1)[0]
            if module in banned_import_modules:
                raise StrategySourceError(f"banned import in strategy source: {module}")

    extract_params(normalized)


# ==================== 本地兜底候选 ====================


def _numeric_step(value: object, rng: random.Random) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int) and not isinstance(value, bool):
        step = max(1, round(abs(value) * 0.12))
        direction = rng.choice((-1, 1))
        return max(1, value + direction * rng.randint(1, step))
    if isinstance(value, float):
        step = max(0.0001, abs(value) * 0.10)
        direction = rng.choice((-1.0, 1.0))
        candidate = value + direction * step * rng.uniform(0.4, 1.0)
        if value > 0:
            candidate = max(value * 0.55, candidate)
        return round(candidate, 10)
    return value


def build_local_param_candidate(
    *,
    base_source: str,
    seed: int,
    attempts: int,
) -> StrategyCandidate:
    params = extract_params(base_source)
    groups = list(LOCAL_PARAM_GROUPS.items())
    for offset in range(attempts):
        rng = random.Random(seed + offset)
        group_name, keys = rng.choice(groups)
        selected_keys = [key for key in rng.sample(keys, min(len(keys), rng.randint(2, min(4, len(keys))))) if key in params]
        if not selected_keys:
            continue
        updated_params = dict(params)
        changed_keys = []
        for key in selected_keys:
            current = updated_params[key]
            candidate = _numeric_step(current, rng)
            if candidate == current:
                continue
            updated_params[key] = candidate
            changed_keys.append(key)
        if not changed_keys:
            continue
        new_source = replace_params(base_source, updated_params)
        validate_strategy_source(new_source)
        return StrategyCandidate(
            candidate_id=f"local-{seed + offset}",
            hypothesis=f"本地兜底：围绕 {group_name} 做小步探索",
            change_plan=f"仅调整 PARAMS 中的 {', '.join(changed_keys)}",
            change_tags=(group_name, "local_param_fallback"),
            edited_regions=("PARAMS", "strategy"),
            expected_effects=("在不改结构的前提下测试新的入场阈值组合",),
            strategy_code=new_source,
        )
    raise StrategySourceError("failed to build local fallback candidate")

