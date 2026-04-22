#!/usr/bin/env python3
"""策略源码的加载、校验与源码摘要。"""
from __future__ import annotations

import ast
import builtins
import hashlib
import json
import math
import re
import symtable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
FACTOR_CHANGE_MODES = frozenset({"default", "factor_admission"})
DEFAULT_MODE_MAX_NEW_TOP_LEVEL_CONSTANTS = 2
DEFAULT_MODE_MAX_NEW_TOP_LEVEL_HELPERS = 2

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
REQUIRED_FUNCTIONS: tuple[str, ...] = (
    "_sideways_release_flags",
    "_is_sideways_regime",
    "_flow_signal_metrics",
    "_flow_confirmation_ok",
    "_flow_entry_ok",
    "_trend_quality_long",
    "_trend_quality_short",
    "_trend_quality_ok",
    "_trend_followthrough_long",
    "_trend_followthrough_short",
    "_trend_followthrough_ok",
    "_long_entry_signal",
    "_short_entry_signal",
    "strategy",
)
REQUIRED_FUNCTION_SET = frozenset(REQUIRED_FUNCTIONS)
COMPLEXITY_MONITORED_FUNCTIONS: tuple[str, ...] = (
    "_is_sideways_regime",
    "_trend_quality_ok",
    "_trend_followthrough_ok",
    "strategy",
)
COMPLEXITY_MONITORED_FAMILIES: dict[str, tuple[str, ...]] = {
    "sideways_family": (
        "_sideways_release_flags",
        "_is_sideways_regime",
    ),
    "flow_family": (
        "_flow_signal_metrics",
        "_flow_confirmation_ok",
        "_flow_entry_ok",
    ),
    "trend_quality_family": (
        "_trend_quality_long",
        "_trend_quality_short",
        "_trend_quality_ok",
        "long_outer_context_ok",
        "short_outer_context_ok",
    ),
    "long_path_chain": (
        "long_breakout_ok",
        "long_pullback_ok",
        "long_trend_reaccel_ok",
        "long_signal_path_ok",
        "long_final_veto_clear",
        "_trend_followthrough_long",
    ),
    "short_path_chain": (
        "breakdown_ready",
        "short_breakdown_ok",
        "short_bounce_fail_ok",
        "short_trend_reaccel_ok",
        "short_final_veto_clear",
        "_trend_followthrough_short",
    ),
}
COMPLEXITY_ABSOLUTE_BUDGETS: dict[str, dict[str, int]] = {
    "_is_sideways_regime": {"lines": 90, "bool_ops": 32, "ifs": 14},
    "_trend_quality_ok": {"lines": 90, "bool_ops": 32, "ifs": 14},
    "_trend_followthrough_ok": {"lines": 90, "bool_ops": 36, "ifs": 12},
    "strategy": {"lines": 360, "bool_ops": 180, "ifs": 12},
}
COMPLEXITY_FAMILY_ABSOLUTE_BUDGETS: dict[str, dict[str, int]] = {
    "sideways_family": {"lines": 200, "bool_ops": 88, "ifs": 16},
    "flow_family": {"lines": 140, "bool_ops": 30, "ifs": 10},
    "trend_quality_family": {"lines": 150, "bool_ops": 50, "ifs": 18},
    "long_path_chain": {"lines": 200, "bool_ops": 88, "ifs": 12},
    "short_path_chain": {"lines": 176, "bool_ops": 80, "ifs": 12},
}
COMPLEXITY_DEFAULT_GROWTH_LIMITS: dict[str, int] = {
    "lines": 40,
    "bool_ops": 20,
    "ifs": 4,
}
COMPLEXITY_FAMILY_DEFAULT_GROWTH_LIMITS: dict[str, int] = {
    "lines": 40,
    "bool_ops": 20,
    "ifs": 4,
}
COMPLEXITY_HEADROOM_WARNING_THRESHOLDS: dict[str, dict[str, int]] = {
    "warning_1": {"lines": 20, "bool_ops": 8, "ifs": 3},
    "warning_2": {"lines": 8, "bool_ops": 2, "ifs": 1},
}
COMPLEXITY_GROWTH_WARNING_RATIOS: dict[str, float] = {
    "warning_1": 0.60,
    "warning_2": 0.85,
}
COMPLEXITY_SNAPSHOT_FUNCTIONS = frozenset(
    {
        *COMPLEXITY_MONITORED_FUNCTIONS,
        *(
            function_name
            for family_functions in COMPLEXITY_MONITORED_FAMILIES.values()
            for function_name in family_functions
        ),
    }
)


def normalize_factor_change_mode(mode: str | None) -> str:
    normalized = str(mode or "default").strip().lower().replace("-", "_")
    if normalized not in FACTOR_CHANGE_MODES:
        raise StrategySourceError(
            f"unsupported factor change mode: {mode}; expected one of {sorted(FACTOR_CHANGE_MODES)}"
        )
    return normalized


def factor_change_mode_label(mode: str | None) -> str:
    normalized = normalize_factor_change_mode(mode)
    if normalized == "factor_admission":
        return "因子准入模式"
    return "默认模式"


def factor_change_mode_prompt_hint(mode: str | None) -> str:
    normalized = normalize_factor_change_mode(mode)
    if normalized == "factor_admission":
        return (
            "当前因子模式：因子准入模式。仅当现有规则无法表达你的假设时，才允许新增少量参数或局部规则；"
            "新增后必须同时删减旧复杂度，避免净复杂度继续膨胀。"
        )
    return (
        "当前因子模式：默认模式。禁止新增 `PARAMS` 键；"
        f"允许最多新增 {DEFAULT_MODE_MAX_NEW_TOP_LEVEL_CONSTANTS} 个顶层常量和 "
        f"{DEFAULT_MODE_MAX_NEW_TOP_LEVEL_HELPERS} 个顶层 helper，用于结构化抽离现有逻辑；"
        "不允许借这个口子堆新因子或复制旧规则。"
    )


def complexity_pressure_label(level: str) -> str:
    normalized = str(level or "normal").strip().lower()
    if normalized == "warning_1":
        return "warning_1（开始拥挤，优先删旧合并）"
    if normalized == "warning_2":
        return "warning_2（接近危险区，优先先压缩再新增）"
    if normalized == "hard_cap":
        return "hard_cap（超出绝对复杂度帽，直接拒收）"
    return "normal（空间正常）"


def complexity_growth_warning_thresholds() -> dict[str, dict[str, int]]:
    thresholds: dict[str, dict[str, int]] = {}
    for level, ratio in COMPLEXITY_GROWTH_WARNING_RATIOS.items():
        thresholds[level] = {
            metric_name: max(1, int(math.ceil(limit * ratio)))
            for metric_name, limit in COMPLEXITY_DEFAULT_GROWTH_LIMITS.items()
        }
    return thresholds

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


def _rebuild_source_from_regions(
    base_source: str,
    region_sources: dict[str, str],
    editable_regions: tuple[str, ...],
) -> str:
    normalized_base = normalize_strategy_source(base_source)
    parts: list[str] = []
    cursor = 0
    for start, end, region_name in _editable_spans(normalized_base, editable_regions):
        parts.append(normalized_base[cursor:start])
        parts.append(region_sources.get(region_name, normalized_base[start:end]))
        cursor = end
    parts.append(normalized_base[cursor:])
    return normalize_strategy_source("".join(parts))


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


def missing_required_functions(source: str) -> tuple[str, ...]:
    normalized = normalize_strategy_source(source)
    try:
        tree = ast.parse(normalized)
    except SyntaxError as exc:
        raise StrategySourceError(f"strategy source has syntax error: line {exc.lineno} {exc.msg}") from exc
    function_names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    return tuple(sorted(REQUIRED_FUNCTION_SET - function_names))


def repair_missing_required_functions(
    base_source: str,
    candidate_source: str,
    editable_regions: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    normalized_candidate = normalize_strategy_source(candidate_source)
    missing = missing_required_functions(normalized_candidate)
    repairable_missing = tuple(
        function_name
        for function_name in missing
        if function_name in editable_regions
    )
    if not repairable_missing:
        return normalized_candidate, ()

    candidate_regions = _editable_region_source_map(normalized_candidate, editable_regions)
    repaired_source = _rebuild_source_from_regions(
        base_source,
        candidate_regions,
        editable_regions,
    )
    return repaired_source, repairable_missing


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


def _iter_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for child in target.elts:
            names.extend(_iter_target_names(child))
        return names
    if isinstance(target, ast.Starred):
        return _iter_target_names(target.value)
    return []


def _module_defined_names(tree: ast.Module) -> set[str]:
    names = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_iter_target_names(target))
            continue
        if isinstance(node, ast.AnnAssign):
            names.update(_iter_target_names(node.target))
            continue
    return names


def _top_level_function_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _top_level_constant_names(tree: ast.Module) -> set[str]:
    constants: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for name in _iter_target_names(target):
                if name.isupper() and name != "PARAMS":
                    constants.add(name)
    return constants


def _function_complexity_metrics(node: ast.FunctionDef) -> dict[str, int]:
    bool_ops = 0
    ifs = 0
    compares = 0
    for child in ast.walk(node):
        if isinstance(child, ast.BoolOp):
            bool_ops += max(len(child.values) - 1, 1)
        elif isinstance(child, ast.If):
            ifs += 1
        elif isinstance(child, ast.Compare):
            compares += len(child.ops)
    end_lineno = getattr(node, "end_lineno", node.lineno)
    return {
        "lines": end_lineno - node.lineno + 1,
        "bool_ops": bool_ops,
        "ifs": ifs,
        "compares": compares,
    }


def build_strategy_complexity_snapshot(source: str) -> dict[str, dict[str, int]]:
    tree = ast.parse(normalize_strategy_source(source))
    snapshot: dict[str, dict[str, int]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in COMPLEXITY_SNAPSHOT_FUNCTIONS:
            continue
        snapshot[node.name] = _function_complexity_metrics(node)
    return snapshot


def _family_complexity_metrics(
    snapshot: dict[str, dict[str, int]],
    family_name: str,
) -> dict[str, int]:
    aggregate = {
        "lines": 0,
        "bool_ops": 0,
        "ifs": 0,
        "compares": 0,
    }
    for function_name in COMPLEXITY_MONITORED_FAMILIES.get(family_name, ()):
        function_metrics = snapshot.get(function_name, {})
        for metric_name in aggregate:
            aggregate[metric_name] += int(function_metrics.get(metric_name, 0))
    return aggregate


def build_strategy_complexity_delta(base_source: str, candidate_source: str) -> dict[str, object]:
    base_snapshot = build_strategy_complexity_snapshot(base_source)
    candidate_snapshot = build_strategy_complexity_snapshot(candidate_source)
    functions: dict[str, dict[str, int]] = {}
    families: dict[str, dict[str, int]] = {}
    flags: list[str] = []
    summary_parts: list[str] = []

    for function_name in COMPLEXITY_MONITORED_FUNCTIONS:
        base_metrics = base_snapshot.get(function_name, {})
        candidate_metrics = candidate_snapshot.get(function_name, {})
        if not candidate_metrics:
            continue
        delta_metrics = {
            "base_lines": int(base_metrics.get("lines", 0)),
            "candidate_lines": int(candidate_metrics.get("lines", 0)),
            "delta_lines": int(candidate_metrics.get("lines", 0) - base_metrics.get("lines", 0)),
            "base_bool_ops": int(base_metrics.get("bool_ops", 0)),
            "candidate_bool_ops": int(candidate_metrics.get("bool_ops", 0)),
            "delta_bool_ops": int(candidate_metrics.get("bool_ops", 0) - base_metrics.get("bool_ops", 0)),
            "base_ifs": int(base_metrics.get("ifs", 0)),
            "candidate_ifs": int(candidate_metrics.get("ifs", 0)),
            "delta_ifs": int(candidate_metrics.get("ifs", 0) - base_metrics.get("ifs", 0)),
        }
        functions[function_name] = delta_metrics
        if (
            delta_metrics["delta_lines"] > 0
            or delta_metrics["delta_bool_ops"] > 0
            or delta_metrics["delta_ifs"] > 0
        ):
            summary_parts.append(
                f"{function_name}:L{delta_metrics['delta_lines']:+d}/B{delta_metrics['delta_bool_ops']:+d}/I{delta_metrics['delta_ifs']:+d}"
            )
        absolute_budget = COMPLEXITY_ABSOLUTE_BUDGETS.get(function_name, {})
        for metric_name, limit in absolute_budget.items():
            candidate_value = int(candidate_metrics.get(metric_name, 0))
            if candidate_value > int(limit):
                flags.append(f"{function_name}.{metric_name}>{limit}")

    for family_name in COMPLEXITY_MONITORED_FAMILIES:
        base_metrics = _family_complexity_metrics(base_snapshot, family_name)
        candidate_metrics = _family_complexity_metrics(candidate_snapshot, family_name)
        delta_metrics = {
            "base_lines": int(base_metrics.get("lines", 0)),
            "candidate_lines": int(candidate_metrics.get("lines", 0)),
            "delta_lines": int(candidate_metrics.get("lines", 0) - base_metrics.get("lines", 0)),
            "base_bool_ops": int(base_metrics.get("bool_ops", 0)),
            "candidate_bool_ops": int(candidate_metrics.get("bool_ops", 0)),
            "delta_bool_ops": int(candidate_metrics.get("bool_ops", 0) - base_metrics.get("bool_ops", 0)),
            "base_ifs": int(base_metrics.get("ifs", 0)),
            "candidate_ifs": int(candidate_metrics.get("ifs", 0)),
            "delta_ifs": int(candidate_metrics.get("ifs", 0) - base_metrics.get("ifs", 0)),
        }
        families[family_name] = delta_metrics
        if (
            delta_metrics["delta_lines"] > 0
            or delta_metrics["delta_bool_ops"] > 0
            or delta_metrics["delta_ifs"] > 0
        ):
            summary_parts.append(
                f"{family_name}:L{delta_metrics['delta_lines']:+d}/B{delta_metrics['delta_bool_ops']:+d}/I{delta_metrics['delta_ifs']:+d}"
            )
        absolute_budget = COMPLEXITY_FAMILY_ABSOLUTE_BUDGETS.get(family_name, {})
        for metric_name, limit in absolute_budget.items():
            candidate_value = int(candidate_metrics.get(metric_name, 0))
            if candidate_value > int(limit):
                flags.append(f"{family_name}.{metric_name}>{limit}")

    return {
        "functions": functions,
        "families": families,
        "summary": " | ".join(summary_parts) if summary_parts else "complexity_flat",
        "flags": tuple(flags),
        "bloat_flag": bool(flags),
    }


def build_strategy_complexity_headroom(source: str) -> dict[str, dict[str, dict[str, int]]]:
    snapshot = build_strategy_complexity_snapshot(source)
    functions: dict[str, dict[str, int]] = {}
    families: dict[str, dict[str, int]] = {}

    for function_name, limits in COMPLEXITY_ABSOLUTE_BUDGETS.items():
        used_metrics = snapshot.get(function_name, {})
        functions[function_name] = {
            "used_lines": int(used_metrics.get("lines", 0)),
            "remaining_lines": int(limits["lines"]) - int(used_metrics.get("lines", 0)),
            "used_bool_ops": int(used_metrics.get("bool_ops", 0)),
            "remaining_bool_ops": int(limits["bool_ops"]) - int(used_metrics.get("bool_ops", 0)),
            "used_ifs": int(used_metrics.get("ifs", 0)),
            "remaining_ifs": int(limits["ifs"]) - int(used_metrics.get("ifs", 0)),
        }

    for family_name, limits in COMPLEXITY_FAMILY_ABSOLUTE_BUDGETS.items():
        used_metrics = _family_complexity_metrics(snapshot, family_name)
        families[family_name] = {
            "used_lines": int(used_metrics.get("lines", 0)),
            "remaining_lines": int(limits["lines"]) - int(used_metrics.get("lines", 0)),
            "used_bool_ops": int(used_metrics.get("bool_ops", 0)),
            "remaining_bool_ops": int(limits["bool_ops"]) - int(used_metrics.get("bool_ops", 0)),
            "used_ifs": int(used_metrics.get("ifs", 0)),
            "remaining_ifs": int(limits["ifs"]) - int(used_metrics.get("ifs", 0)),
        }

    return {
        "functions": functions,
        "families": families,
    }


def build_strategy_complexity_pressure(
    source: str,
    *,
    base_source: str | None = None,
) -> dict[str, Any]:
    headroom = build_strategy_complexity_headroom(source)
    headroom_level = "normal"
    headroom_items: list[str] = []

    def _update_level(current_level: str, next_level: str) -> str:
        order = {"normal": 0, "warning_1": 1, "warning_2": 2, "hard_cap": 3}
        return next_level if order.get(next_level, 0) > order.get(current_level, 0) else current_level

    for group_name, group_metrics in (
        ("family", headroom["families"]),
        ("function", headroom["functions"]),
    ):
        for item_name, metrics in group_metrics.items():
            for metric_name in ("lines", "bool_ops", "ifs"):
                remaining_value = int(metrics.get(f"remaining_{metric_name}", 0))
                if remaining_value < 0:
                    headroom_level = "hard_cap"
                    headroom_items.append(f"{group_name} `{item_name}` 的 {metric_name} 已超帽 {abs(remaining_value)}")
                    continue
                if remaining_value <= COMPLEXITY_HEADROOM_WARNING_THRESHOLDS["warning_2"][metric_name]:
                    headroom_level = _update_level(headroom_level, "warning_2")
                    headroom_items.append(f"{group_name} `{item_name}` 的 {metric_name} 余量仅剩 {remaining_value}")
                    continue
                if remaining_value <= COMPLEXITY_HEADROOM_WARNING_THRESHOLDS["warning_1"][metric_name]:
                    headroom_level = _update_level(headroom_level, "warning_1")
                    headroom_items.append(f"{group_name} `{item_name}` 的 {metric_name} 余量仅剩 {remaining_value}")

    growth_level = "normal"
    growth_items: list[str] = []
    if base_source is not None:
        complexity_delta = build_strategy_complexity_delta(base_source, source)
        growth_thresholds = complexity_growth_warning_thresholds()
        for group_name, delta_group in (
            ("family", complexity_delta["families"]),
            ("function", complexity_delta["functions"]),
        ):
            for item_name, metrics in delta_group.items():
                for metric_name in ("lines", "bool_ops", "ifs"):
                    delta_value = int(metrics.get(f"delta_{metric_name}", 0))
                    if delta_value <= 0:
                        continue
                    if delta_value >= growth_thresholds["warning_2"][metric_name]:
                        growth_level = _update_level(growth_level, "warning_2")
                        growth_items.append(f"{group_name} `{item_name}` 的 {metric_name} 单轮增加 {delta_value}")
                        continue
                    if delta_value >= growth_thresholds["warning_1"][metric_name]:
                        growth_level = _update_level(growth_level, "warning_1")
                        growth_items.append(f"{group_name} `{item_name}` 的 {metric_name} 单轮增加 {delta_value}")

    level = _update_level(headroom_level, growth_level)
    summary_parts = [f"overall={level}", f"headroom={headroom_level}", f"growth={growth_level}"]
    if headroom_items:
        summary_parts.append(f"headroom_risk={'；'.join(headroom_items[:3])}")
    if growth_items:
        summary_parts.append(f"growth_risk={'；'.join(growth_items[:3])}")
    return {
        "level": level,
        "headroom_level": headroom_level,
        "growth_level": growth_level,
        "headroom_items": tuple(headroom_items),
        "growth_items": tuple(growth_items),
        "summary": " | ".join(summary_parts),
    }


def format_strategy_complexity_headroom(source: str, *, limit: int = 4) -> str:
    headroom = build_strategy_complexity_headroom(source)
    pressure = build_strategy_complexity_pressure(source)

    def _risk_tuple(item: tuple[str, dict[str, int]]) -> tuple[int, int, int, str]:
        name, metrics = item
        return (
            int(metrics["remaining_bool_ops"]),
            int(metrics["remaining_lines"]),
            int(metrics["remaining_ifs"]),
            name,
        )

    family_items = sorted(headroom["families"].items(), key=_risk_tuple)[: max(1, limit)]
    function_items = sorted(headroom["functions"].items(), key=_risk_tuple)[: max(1, limit)]
    lines = [
        f"当前基底复杂度状态：{complexity_pressure_label(str(pressure.get('level', 'normal')))}",
        "- warning_1 只是提醒开始偏胖；warning_2 表示下一步应优先压缩；只有 hard_cap 才会直接拒收。",
        "当前基底复杂度余量（剩余越小越容易再次撞复杂度）:",
    ]

    for name, metrics in family_items:
        lines.append(
            f"- family `{name}`: lines 剩 {metrics['remaining_lines']}, "
            f"bool_ops 剩 {metrics['remaining_bool_ops']}, ifs 剩 {metrics['remaining_ifs']}"
        )

    for name, metrics in function_items:
        lines.append(
            f"- function `{name}`: lines 剩 {metrics['remaining_lines']}, "
            f"bool_ops 剩 {metrics['remaining_bool_ops']}, ifs 剩 {metrics['remaining_ifs']}"
        )

    if pressure["headroom_items"]:
        lines.append(f"- 当前最紧张位置：{'；'.join(pressure['headroom_items'][:3])}")
    lines.append("- 若要继续改最紧张的 family/function，先删旧条件或合并旧分支，再考虑新增判断。")
    return "\n".join(lines)


def _validate_complexity_budget(
    source: str,
    *,
    tree: ast.Module,
    base_source: str | None,
    factor_change_mode: str,
) -> None:
    node_map = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    for function_name, limits in COMPLEXITY_ABSOLUTE_BUDGETS.items():
        node = node_map.get(function_name)
        if node is None:
            continue
        metrics = _function_complexity_metrics(node)
        for metric_name, limit in limits.items():
            value = int(metrics.get(metric_name, 0))
            if value > int(limit):
                raise StrategySourceError(
                    f"complexity budget exceeded: {function_name}.{metric_name}={value} > {limit}"
                )

    candidate_snapshot = build_strategy_complexity_snapshot(source)
    for family_name, limits in COMPLEXITY_FAMILY_ABSOLUTE_BUDGETS.items():
        metrics = _family_complexity_metrics(candidate_snapshot, family_name)
        for metric_name, limit in limits.items():
            value = int(metrics.get(metric_name, 0))
            if value > int(limit):
                raise StrategySourceError(
                    f"complexity family budget exceeded: {family_name}.{metric_name}={value} > {limit}"
                )

    if base_source is None or normalize_factor_change_mode(factor_change_mode) != "default":
        return
    # 默认模式仍会记录单轮复杂度增量，但这里只保留绝对复杂度硬帽拒收。
    # 软增量限制改为 prompt/journal 预警，避免把可尝试但偏胖的候选过早拦死。
    _ = build_strategy_complexity_delta(base_source, source)


def _undefined_function_reference_errors(source: str, tree: ast.Module) -> list[str]:
    module_names = _module_defined_names(tree)
    root_table = symtable.symtable(source, "strategy_candidate", "exec")
    errors: list[str] = []

    def _walk(table: symtable.SymbolTable, parents: tuple[str, ...]) -> None:
        current_path = parents
        if table.get_type() == "function":
            current_path = parents + (table.get_name(),)
            for symbol in table.get_symbols():
                if not symbol.is_referenced():
                    continue
                if symbol.is_parameter() or symbol.is_local() or symbol.is_imported():
                    continue
                if symbol.is_free() or symbol.is_nonlocal():
                    continue
                name = symbol.get_name()
                if name in module_names:
                    continue
                function_path = ".".join(current_path)
                errors.append(
                    f"function {function_path} references undefined name '{name}'"
                )
        for child in table.get_children():
            _walk(child, current_path)

    _walk(root_table, ())
    return errors


def _validate_factor_change_policy(
    source: str,
    *,
    tree: ast.Module,
    base_source: str | None,
    factor_change_mode: str,
) -> None:
    if base_source is None:
        return
    normalized_mode = normalize_factor_change_mode(factor_change_mode)
    if normalized_mode != "default":
        return

    base_tree = ast.parse(normalize_strategy_source(base_source))
    base_params = extract_params(base_source)
    candidate_params = extract_params(source)

    new_param_keys = sorted(set(candidate_params) - set(base_params))
    if new_param_keys:
        raise StrategySourceError(
            "factor mode default forbids new PARAMS keys: "
            + ", ".join(new_param_keys[:8])
        )

    new_constant_names = sorted(_top_level_constant_names(tree) - _top_level_constant_names(base_tree))
    invalid_constant_names = [name for name in new_constant_names if name.upper() != name]
    if invalid_constant_names:
        raise StrategySourceError(
            "default mode new top-level constants must use UPPER_CASE names: "
            + ", ".join(invalid_constant_names[:8])
        )
    if len(new_constant_names) > DEFAULT_MODE_MAX_NEW_TOP_LEVEL_CONSTANTS:
        raise StrategySourceError(
            "default mode allows at most "
            f"{DEFAULT_MODE_MAX_NEW_TOP_LEVEL_CONSTANTS} new top-level constants: "
            + ", ".join(new_constant_names[:8])
        )

    new_function_names = sorted(_top_level_function_names(tree) - _top_level_function_names(base_tree))
    invalid_function_names = [name for name in new_function_names if not name.startswith("_")]
    if invalid_function_names:
        raise StrategySourceError(
            "default mode new top-level helpers must use private helper names: "
            + ", ".join(invalid_function_names[:8])
        )
    if len(new_function_names) > DEFAULT_MODE_MAX_NEW_TOP_LEVEL_HELPERS:
        raise StrategySourceError(
            "default mode allows at most "
            f"{DEFAULT_MODE_MAX_NEW_TOP_LEVEL_HELPERS} new top-level helpers: "
            + ", ".join(new_function_names[:8])
        )


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


def validate_strategy_source(
    source: str,
    *,
    base_source: str | None = None,
    factor_change_mode: str = "default",
) -> None:
    normalized = normalize_strategy_source(source)
    missing_functions = missing_required_functions(normalized)
    if missing_functions:
        raise StrategySourceError(f"missing required functions: {sorted(missing_functions)}")

    tree = ast.parse(normalized)
    undefined_reference_errors = _undefined_function_reference_errors(normalized, tree)
    if undefined_reference_errors:
        raise StrategySourceError(undefined_reference_errors[0])
    _validate_factor_change_policy(
        normalized,
        tree=tree,
        base_source=base_source,
        factor_change_mode=factor_change_mode,
    )
    _validate_complexity_budget(
        normalized,
        tree=tree,
        base_source=base_source,
        factor_change_mode=factor_change_mode,
    )

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
