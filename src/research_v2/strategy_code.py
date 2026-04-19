#!/usr/bin/env python3
"""策略源码的加载、校验与源码摘要。"""
from __future__ import annotations

import ast
import hashlib
import json
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
    "breakout_trade_count_ratio_min": (0.5, 4.0),
    "breakdown_trade_count_ratio_min": (0.5, 4.0),
    "breakout_taker_buy_ratio_min": (0.45, 0.95),
    "breakdown_taker_sell_ratio_min": (0.45, 0.95),
    "breakout_flow_imbalance_min": (-0.30, 0.90),
    "breakdown_flow_imbalance_max": (-0.90, 0.30),
    "hourly_flow_confirmation_min": (0.0, 0.40),
    "fourh_flow_confirmation_min": (0.0, 0.40),
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
    "flow_lookback": (3, 40),
}

PARAM_RELATIONS: tuple[tuple[str, str, str], ...] = (
    ("breakout_rsi_min", "<=", "breakout_rsi_max"),
    ("breakdown_rsi_min", "<=", "breakdown_rsi_max"),
    ("intraday_ema_fast", "<", "intraday_ema_slow"),
    ("hourly_ema_fast", "<", "hourly_ema_slow"),
    ("fourh_ema_fast", "<", "fourh_ema_slow"),
    ("macd_fast", "<", "macd_slow"),
)

STRUCTURAL_LITERAL_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,}")

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


def _editable_region_source_map(source: str, editable_regions: tuple[str, ...]) -> dict[str, str]:
    normalized = normalize_strategy_source(source)
    return {
        region_name: normalized[start:end]
        for start, end, region_name in _editable_spans(normalized, editable_regions)
    }


def changed_editable_regions(
    base_source: str,
    candidate_source: str,
    editable_regions: tuple[str, ...],
) -> tuple[str, ...]:
    base_regions = _editable_region_source_map(base_source, editable_regions)
    candidate_regions = _editable_region_source_map(candidate_source, editable_regions)
    changed = [
        region_name
        for region_name in editable_regions
        if base_regions.get(region_name, "") != candidate_regions.get(region_name, "")
    ]
    return tuple(changed)


def _function_node_map(source: str, region_names: tuple[str, ...]) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(normalize_strategy_source(source))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in region_names
    }


def _param_keys_changed(base_source: str, candidate_source: str) -> tuple[str, ...]:
    base_params = extract_params(base_source)
    candidate_params = extract_params(candidate_source)
    keys = sorted(set(base_params) | set(candidate_params))
    changed = [
        key
        for key in keys
        if base_params.get(key) != candidate_params.get(key)
    ]
    return tuple(changed)


def param_family_for_key(param_key: str) -> str:
    key = str(param_key).strip().lower()
    if not key:
        return "unknown"
    if any(token in key for token in ("trade_count", "taker", "flow")):
        return "flow"
    for prefix in (
        "breakout",
        "breakdown",
        "intraday",
        "hourly",
        "fourh",
        "launch",
        "long",
        "short",
        "macd",
    ):
        if key.startswith(prefix + "_") or key == prefix:
            return prefix
    return key.split("_", 1)[0]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _node_string_literals(node: ast.AST) -> set[str]:
    literals: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            text = child.value.strip()
            if STRUCTURAL_LITERAL_PATTERN.fullmatch(text):
                literals.add(text)
    return literals


def _called_helper_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            call_name = _call_name(child.func).strip()
            if call_name:
                names.add(call_name)
    return names


def build_system_edit_signature(
    base_source: str,
    candidate_source: str,
    editable_regions: tuple[str, ...],
) -> dict[str, object]:
    changed_regions = changed_editable_regions(base_source, candidate_source, editable_regions)
    changed_param_keys = _param_keys_changed(base_source, candidate_source)
    param_families = tuple(
        sorted({param_family_for_key(key) for key in changed_param_keys})
    )

    function_names = tuple(
        region_name
        for region_name in changed_regions
        if region_name != "PARAMS"
    )
    function_nodes = _function_node_map(candidate_source, function_names)

    changed_literals: set[str] = set()
    helper_calls: set[str] = set()
    for function_name in function_names:
        node = function_nodes.get(function_name)
        if node is None:
            continue
        changed_literals.update(_node_string_literals(node))
        helper_calls.update(_called_helper_names(node))

    structural_tokens = tuple(
        sorted(
            {
                *changed_regions,
                *param_families,
                *changed_literals,
                *helper_calls,
            }
        )
    )
    signature_payload = {
        "changed_regions": list(changed_regions),
        "changed_param_keys": list(changed_param_keys),
        "param_families": list(param_families),
        "changed_literals": sorted(changed_literals),
        "helper_calls": sorted(helper_calls),
    }
    return {
        "changed_regions": changed_regions,
        "changed_param_keys": changed_param_keys,
        "param_families": param_families,
        "changed_literals": tuple(sorted(changed_literals)),
        "helper_calls": tuple(sorted(helper_calls)),
        "structural_tokens": structural_tokens,
        "signature_hash": hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


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
