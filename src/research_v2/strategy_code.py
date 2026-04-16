#!/usr/bin/env python3
"""策略源码的加载、校验与源码摘要。"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


# ==================== 数据结构 ====================


class StrategySourceError(RuntimeError):
    """候选策略源码不合法。"""


@dataclass(frozen=True)
class StrategyCoreFactor:
    name: str
    thesis: str
    current_signal: str


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_id: str
    hypothesis: str
    change_plan: str
    closest_failed_cluster: str
    novelty_proof: str
    change_tags: tuple[str, ...]
    edited_regions: tuple[str, ...]
    expected_effects: tuple[str, ...]
    core_factors: tuple[StrategyCoreFactor, ...]
    strategy_code: str


# ==================== 源码基础操作 ====================


PARAM_BLOCK_PATTERN = re.compile(r"# PARAMS_START\s*\nPARAMS = (.*?)\n# PARAMS_END", re.DOTALL)

# 参数硬性范围：防止参数漂移到无意义的区域
# 格式: key -> (最小值, 最大值)
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    # 入场 ADX 阈值
    "intraday_adx_min": (5, 50),
    "hourly_adx_min": (5, 50),
    "fourh_adx_min": (5, 50),
    "breakout_adx_min": (5, 50),
    "breakdown_adx_min": (5, 50),
    # lookback 周期
    "breakout_lookback": (3, 60),
    "breakdown_lookback": (3, 60),
    # RSI 范围
    "breakout_rsi_min": (10, 70),
    "breakout_rsi_max": (30, 95),
    "breakdown_rsi_min": (5, 70),
    "breakdown_rsi_max": (30, 90),
    # 成交量
    "breakout_volume_ratio_min": (0.5, 5.0),
    "breakdown_volume_ratio_min": (0.5, 5.0),
    # K 线形态
    "breakout_body_ratio_min": (0.1, 0.9),
    "breakdown_body_ratio_min": (0.1, 0.9),
    "breakout_close_pos_min": (0.1, 0.95),
    "breakdown_close_pos_max": (0.05, 0.9),
    # EMA 周期
    "intraday_ema_fast": (3, 30),
    "intraday_ema_slow": (10, 60),
    "hourly_ema_fast": (3, 30),
    "hourly_ema_slow": (10, 100),
    "fourh_ema_fast": (3, 30),
    "fourh_ema_slow": (10, 100),
    # MACD 参数
    "macd_fast": (5, 20),
    "macd_slow": (15, 40),
    "macd_signal": (3, 15),
    # 成交量回看
    "volume_lookback": (5, 40),
}

PARAM_RELATIONS: tuple[tuple[str, str, str], ...] = (
    ("breakout_rsi_min", "<=", "breakout_rsi_max"),
    ("breakdown_rsi_min", "<=", "breakdown_rsi_max"),
    ("intraday_ema_fast", "<", "intraday_ema_slow"),
    ("hourly_ema_fast", "<", "hourly_ema_slow"),
    ("fourh_ema_fast", "<", "fourh_ema_slow"),
    ("macd_fast", "<", "macd_slow"),
)

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


def _offset_for_line_column(line_offsets: list[int], lineno: int, column: int) -> int:
    return line_offsets[lineno - 1] + column


def _editable_spans(source: str, editable_regions: tuple[str, ...]) -> list[tuple[int, int, str]]:
    normalized = normalize_strategy_source(source)
    tree = ast.parse(normalized)
    line_offsets = [0]
    for line in normalized.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    spans: list[tuple[int, int, str]] = []
    if "PARAMS" in editable_regions:
        match = PARAM_BLOCK_PATTERN.search(normalized)
        if match is None:
            raise StrategySourceError("missing PARAMS block markers")
        spans.append((match.start(), match.end(), "PARAMS"))

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in editable_regions:
            continue
        start = _offset_for_line_column(line_offsets, node.lineno, node.col_offset)
        end_lineno = getattr(node, "end_lineno", node.lineno)
        end_col_offset = getattr(node, "end_col_offset", node.col_offset)
        end = _offset_for_line_column(line_offsets, end_lineno, end_col_offset)
        spans.append((start, end, node.name))

    spans.sort(key=lambda item: item[0])
    return spans


def _mask_editable_regions(source: str, editable_regions: tuple[str, ...]) -> str:
    normalized = normalize_strategy_source(source)
    parts: list[str] = []
    cursor = 0
    for start, end, region_name in _editable_spans(normalized, editable_regions):
        if start < cursor:
            raise StrategySourceError(f"overlapping editable regions around {region_name}")
        parts.append(normalized[cursor:start])
        parts.append(f"<<EDITABLE:{region_name}>>")
        cursor = end
    parts.append(normalized[cursor:])
    return "".join(parts)


def validate_editable_region_boundaries(
    base_source: str,
    candidate_source: str,
    editable_regions: tuple[str, ...],
) -> None:
    base_masked = _mask_editable_regions(base_source, editable_regions)
    candidate_masked = _mask_editable_regions(candidate_source, editable_regions)
    if base_masked != candidate_masked:
        raise StrategySourceError(
            "candidate modified content outside editable regions"
        )


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

    params = extract_params(normalized)
    for key, value in params.items():
        if key in PARAM_BOUNDS and isinstance(value, (int, float)) and not isinstance(value, bool):
            lo, hi = PARAM_BOUNDS[key]
            if value < lo or value > hi:
                raise StrategySourceError(
                    f"parameter {key}={value} out of bounds [{lo}, {hi}]"
                )

    for left_key, operator, right_key in PARAM_RELATIONS:
        left_value = params.get(left_key)
        right_value = params.get(right_key)
        if not isinstance(left_value, (int, float)) or isinstance(left_value, bool):
            continue
        if not isinstance(right_value, (int, float)) or isinstance(right_value, bool):
            continue
        if operator == "<=" and left_value > right_value:
            raise StrategySourceError(f"invalid parameter relation: {left_key}={left_value} > {right_key}={right_value}")
        if operator == "<" and left_value >= right_value:
            raise StrategySourceError(f"invalid parameter relation: {left_key}={left_value} >= {right_key}={right_value}")
