"""轻量退出参数扫描。"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import json
import re
import sys
from pathlib import Path
from typing import Any

from .evaluation import period_score_from_result
from .strategy_code import extract_exit_params, normalize_strategy_source


ALLOWED_EXIT_RANGE_KEYS: dict[str, tuple[float, float]] = {
    "break_even_activation_pct": (0.0, 200.0),
    "break_even_buffer_pct": (0.0, 20.0),
    "breakout_break_even_activation_pct": (0.0, 240.0),
    "breakout_max_hold_bars": (12, 768),
    "breakout_stop_atr_mult": (0.5, 8.0),
    "breakout_tp1_close_fraction": (0.01, 0.80),
    "breakout_tp1_pnl_pct": (1.0, 300.0),
    "breakout_trailing_activation_pct": (1.0, 300.0),
    "breakout_trailing_giveback_pct": (1.0, 200.0),
    "dynamic_hold_adx_strong_threshold": (5.0, 60.0),
    "dynamic_hold_adx_threshold": (5.0, 60.0),
    "dynamic_hold_extension_bars": (0, 384),
    "dynamic_hold_max_bars": (24, 768),
    "max_hold_bars": (12, 768),
    "pyramid_adx_min": (5.0, 60.0),
    "pyramid_trigger_pnl": (1.0, 150.0),
    "regime_exit_confirm_bars": (1, 8),
    "regime_hist_floor": (-500.0, 100.0),
    "regime_price_confirm_buffer_pct": (0.0, 1.0),
    "short_breakdown_break_even_activation_pct": (0.0, 200.0),
    "short_breakdown_max_hold_bars": (12, 768),
    "short_breakdown_stop_atr_mult": (0.5, 8.0),
    "short_breakdown_tp1_close_fraction": (0.01, 0.80),
    "short_breakdown_tp1_pnl_pct": (1.0, 250.0),
    "short_breakdown_trailing_activation_pct": (1.0, 250.0),
    "short_breakdown_trailing_giveback_pct": (1.0, 150.0),
    "stop_atr_mult": (0.5, 8.0),
    "stop_max_loss_pct": (1.0, 200.0),
    "tp1_close_fraction": (0.01, 0.80),
    "tp1_pnl_pct": (1.0, 250.0),
    "trailing_activation_pct": (1.0, 300.0),
    "trailing_giveback_pct": (1.0, 200.0),
}

SCAN_KEY_PRIORITY: tuple[str, ...] = (
    "trailing_activation_pct",
    "trailing_giveback_pct",
    "breakout_trailing_activation_pct",
    "breakout_trailing_giveback_pct",
    "short_breakdown_trailing_activation_pct",
    "short_breakdown_trailing_giveback_pct",
    "stop_max_loss_pct",
    "stop_atr_mult",
    "breakout_stop_atr_mult",
    "short_breakdown_stop_atr_mult",
    "tp1_pnl_pct",
    "breakout_tp1_pnl_pct",
    "short_breakdown_tp1_pnl_pct",
    "break_even_activation_pct",
    "breakout_break_even_activation_pct",
    "short_breakdown_break_even_activation_pct",
    "max_hold_bars",
    "breakout_max_hold_bars",
    "short_breakdown_max_hold_bars",
    "pyramid_trigger_pnl",
    "pyramid_adx_min",
)

INTEGER_KEYS = {
    "breakout_max_hold_bars",
    "dynamic_hold_extension_bars",
    "dynamic_hold_max_bars",
    "max_hold_bars",
    "regime_exit_confirm_bars",
    "short_breakdown_max_hold_bars",
}


@dataclass(frozen=True)
class ExitRangeScanSpec:
    param: str
    values: tuple[float | int, ...]
    reason: str = ""


@dataclass(frozen=True)
class ExitRangeScanOutcome:
    enabled: bool
    applied: bool
    param: str = ""
    values: tuple[float | int, ...] = ()
    selected_value: float | int | None = None
    current_value: float | int | None = None
    reason: str = ""
    summary: tuple[dict[str, Any], ...] = ()
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "applied": self.applied,
            "param": self.param,
            "values": list(self.values),
            "selected_value": self.selected_value,
            "current_value": self.current_value,
            "reason": self.reason,
            "summary": list(self.summary),
            "skipped_reason": self.skipped_reason,
        }


@dataclass(frozen=True)
class PlateauProbeOutcome:
    enabled: bool
    param: str = ""
    values: tuple[float | int, ...] = ()
    current_value: float | int | None = None
    best_value: float | int | None = None
    center_period_score: float = 0.0
    best_period_score: float = 0.0
    center_gap: float = 0.0
    score_span: float = 0.0
    drawdown_span: float = 0.0
    current_is_best: bool = False
    reason: str = ""
    summary: tuple[dict[str, Any], ...] = ()
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "param": self.param,
            "values": list(self.values),
            "current_value": self.current_value,
            "best_value": self.best_value,
            "center_period_score": self.center_period_score,
            "best_period_score": self.best_period_score,
            "center_gap": self.center_gap,
            "score_span": self.score_span,
            "drawdown_span": self.drawdown_span,
            "current_is_best": self.current_is_best,
            "reason": self.reason,
            "summary": list(self.summary),
            "skipped_reason": self.skipped_reason,
        }


def _coerce_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def _clip_value(key: str, value: float | int) -> float | int:
    lo, hi = ALLOWED_EXIT_RANGE_KEYS[key]
    clipped = min(max(float(value), lo), hi)
    if key in INTEGER_KEYS:
        return int(round(clipped))
    return round(clipped, 6)


def _dedupe_values(key: str, values: list[float | int], max_values: int) -> tuple[float | int, ...]:
    output: list[float | int] = []
    for value in values:
        clipped = _clip_value(key, value)
        if clipped not in output:
            output.append(clipped)
        if len(output) >= max(1, max_values):
            break
    return tuple(output)


def _auto_values(key: str, current_value: float | int, max_values: int) -> tuple[float | int, ...]:
    current = float(current_value)
    if key in INTEGER_KEYS:
        step = max(1, int(round(abs(current) * 0.15)))
        values = [current - step, current, current + step]
    elif "adx" in key:
        values = [current - 3.0, current, current + 3.0]
    elif "fraction" in key:
        values = [current * 0.8, current, current * 1.2]
    elif "buffer" in key or "confirm_buffer" in key:
        values = [current * 0.7, current, current * 1.3]
    else:
        values = [current * 0.85, current, current * 1.15]
    return _dedupe_values(key, values, max_values)


def parse_exit_range_scan_payload(payload: Any, *, max_values: int) -> ExitRangeScanSpec | None:
    if not payload:
        return None
    raw: Any = payload
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() in {"none", "null", "无", "[]"}:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            parts = [part.strip() for part in re.split(r"[|；;]", text) if part.strip()]
            if len(parts) < 2:
                return None
            raw = {"param": parts[0], "values": re.split(r"[,，\s]+", parts[1]), "reason": parts[2] if len(parts) > 2 else ""}
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("param") or raw.get("key") or "").strip()
    if key.startswith("EXIT_PARAMS."):
        key = key.split(".", 1)[1]
    if key not in ALLOWED_EXIT_RANGE_KEYS:
        return None
    values_raw = raw.get("values", [])
    if isinstance(values_raw, str):
        values_iterable = re.split(r"[,，\s]+", values_raw.strip())
    elif isinstance(values_raw, (list, tuple)):
        values_iterable = values_raw
    else:
        values_iterable = []
    values: list[float | int] = []
    for item in values_iterable:
        number = _coerce_number(item)
        if number is not None:
            values.append(number)
    normalized = _dedupe_values(key, values, max_values)
    if len(normalized) < 2:
        return None
    return ExitRangeScanSpec(param=key, values=normalized, reason=str(raw.get("reason", "")).strip())


def infer_exit_range_scan_spec(
    base_source: str,
    candidate_source: str,
    explicit_payload: Any,
    *,
    max_values: int,
) -> ExitRangeScanSpec | None:
    explicit = parse_exit_range_scan_payload(explicit_payload, max_values=max_values)
    if explicit is not None:
        return explicit
    try:
        base_exit = extract_exit_params(normalize_strategy_source(base_source))
        candidate_exit = extract_exit_params(normalize_strategy_source(candidate_source))
    except Exception:
        return None
    changed = [
        key for key, value in candidate_exit.items()
        if key in ALLOWED_EXIT_RANGE_KEYS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and base_exit.get(key) != value
    ]
    if not changed:
        return None
    changed.sort(key=lambda key: SCAN_KEY_PRIORITY.index(key) if key in SCAN_KEY_PRIORITY else len(SCAN_KEY_PRIORITY))
    key = changed[0]
    current = _coerce_number(candidate_exit.get(key))
    if current is None:
        return None
    values = _auto_values(key, current, max_values)
    if len(values) < 2:
        return None
    return ExitRangeScanSpec(param=key, values=values, reason="系统根据本轮改动的退出参数自动生成 3 点轻量扫描")


def replace_exit_param_value(source: str, key: str, value: float | int) -> str:
    normalized = normalize_strategy_source(source)
    value_text = str(int(value)) if isinstance(value, int) else repr(float(value))
    pattern = re.compile(rf'("{re.escape(key)}"\s*:\s*)([-+]?\d+(?:\.\d+)?)(\s*,)')
    updated, count = pattern.subn(rf"\g<1>{value_text}\g<3>", normalized, count=1)
    if count != 1:
        raise ValueError(f"EXIT_PARAMS key not found or not numeric: {key}")
    return normalize_strategy_source(updated)


def _scan_value_worker(payload: dict[str, Any]) -> dict[str, Any]:
    repo_root = Path(payload["repo_root"])
    src_dir = repo_root / "src"
    sys.path.insert(0, str(src_dir))
    import backtest_macd_aggressive as backtest_module  # type: ignore
    import strategy_macd_aggressive as strategy_module  # type: ignore

    exit_params = dict(payload["exit_params"])
    windows = list(payload["windows"])
    gate_fee = float(payload["max_fee_drag_pct"])
    fee_mult = float(payload["max_fee_mult"])
    prepared_context = backtest_module.prepare_backtest_context(
        strategy_module.PARAMS,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        exit_params=exit_params,
    )
    results: list[dict[str, float]] = []
    stopped_early = False
    for index, window in enumerate(windows, start=1):
        result = backtest_module.backtest_macd_aggressive(
            strategy_func=strategy_module.strategy,
            intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
            hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
            start_date=window["start_date"],
            end_date=window["end_date"],
            strategy_params=strategy_module.PARAMS,
            exit_params=exit_params,
            include_diagnostics=False,
            prepared_context=prepared_context,
        )
        item = {
            "return": float(result.get("return", 0.0)),
            "max_drawdown": float(result.get("max_drawdown", 0.0)),
            "fee_drag_pct": float(result.get("fee_drag_pct", 0.0)),
            "score": float(result.get("score", 0.0)),
            "trades": float(result.get("trades", 0.0)),
        }
        results.append(item)
        if index >= 1 and item["fee_drag_pct"] > gate_fee * fee_mult and item["return"] <= 0.0:
            stopped_early = True
            break
    count = max(1, len(results))
    return {
        "value": payload["value"],
        "window_count": len(results),
        "mean_score": sum(item["score"] for item in results) / count,
        "mean_return": sum(item["return"] for item in results) / count,
        "max_drawdown": max((item["max_drawdown"] for item in results), default=0.0),
        "mean_fee_drag": sum(item["fee_drag_pct"] for item in results) / count,
        "total_trades": sum(item["trades"] for item in results),
        "stopped_early": stopped_early,
    }


def _probe_value_worker(payload: dict[str, Any]) -> dict[str, Any]:
    repo_root = Path(payload["repo_root"])
    src_dir = repo_root / "src"
    sys.path.insert(0, str(src_dir))
    import backtest_macd_aggressive as backtest_module  # type: ignore
    import strategy_macd_aggressive as strategy_module  # type: ignore

    exit_params = dict(payload["exit_params"])
    windows = list(payload["windows"])
    prepared_context = backtest_module.prepare_backtest_context(
        strategy_module.PARAMS,
        intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
        hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
        exit_params=exit_params,
    )
    results: list[dict[str, float]] = []
    for window in windows:
        result = backtest_module.backtest_macd_aggressive(
            strategy_func=strategy_module.strategy,
            intraday_file=backtest_module.DEFAULT_INTRADAY_FILE,
            hourly_file=backtest_module.DEFAULT_HOURLY_FILE,
            start_date=window["start_date"],
            end_date=window["end_date"],
            strategy_params=strategy_module.PARAMS,
            exit_params=exit_params,
            include_diagnostics=True,
            prepared_context=prepared_context,
        )
        results.append(
            {
                "period_score": float(period_score_from_result(result)),
                "return": float(result.get("return", 0.0)),
                "max_drawdown": float(result.get("max_drawdown", 0.0)),
                "fee_drag_pct": float(result.get("fee_drag_pct", 0.0)),
                "trades": float(result.get("trades", 0.0)),
            }
        )
    count = max(1, len(results))
    period_scores = [item["period_score"] for item in results]
    drawdowns = [item["max_drawdown"] for item in results]
    returns = [item["return"] for item in results]
    fees = [item["fee_drag_pct"] for item in results]
    return {
        "value": payload["value"],
        "window_count": len(results),
        "mean_period_score": sum(period_scores) / count,
        "mean_return": sum(returns) / count,
        "max_drawdown": max(drawdowns, default=0.0),
        "mean_fee_drag": sum(fees) / count,
        "total_trades": sum(item["trades"] for item in results),
        "window_period_scores": period_scores,
        "window_drawdowns": drawdowns,
    }


def run_exit_range_scan(
    *,
    repo_root: Path,
    spec: ExitRangeScanSpec,
    current_exit_params: dict[str, Any],
    windows: list[Any],
    max_fee_drag_pct: float,
    max_fee_mult: float,
    workers: int,
) -> ExitRangeScanOutcome:
    current_value = _coerce_number(current_exit_params.get(spec.param))
    if current_value is None:
        return ExitRangeScanOutcome(enabled=True, applied=False, param=spec.param, skipped_reason="当前参数不是数值")
    values = _dedupe_values(spec.param, list(spec.values), len(spec.values))
    if current_value not in values:
        values = _dedupe_values(spec.param, [*values, current_value], len(values) + 1)
    if len(values) < 2 or not windows:
        return ExitRangeScanOutcome(enabled=True, applied=False, param=spec.param, values=values, current_value=current_value, skipped_reason="扫描值或窗口不足")
    window_payload = [
        {"label": window.label, "start_date": window.start_date, "end_date": window.end_date}
        for window in windows
    ]
    tasks = []
    for value in values:
        exit_params = dict(current_exit_params)
        exit_params[spec.param] = value
        tasks.append(
            {
                "repo_root": str(repo_root),
                "exit_params": exit_params,
                "windows": window_payload,
                "value": value,
                "max_fee_drag_pct": max_fee_drag_pct,
                "max_fee_mult": max_fee_mult,
            }
        )
    max_workers = max(1, min(int(workers), len(tasks)))
    summaries: list[dict[str, Any]] = []
    if max_workers == 1:
        summaries = [_scan_value_worker(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_scan_value_worker, task): task for task in tasks}
            for future in as_completed(future_map):
                summaries.append(future.result())
    summaries.sort(key=lambda item: values.index(item["value"]))
    viable = [item for item in summaries if item["total_trades"] > 0]
    if not viable:
        viable = summaries
    best = max(
        viable,
        key=lambda item: (
            round(float(item["mean_score"]), 10),
            round(float(item["mean_return"]), 10),
            item["value"] == current_value,
            -round(float(item["max_drawdown"]), 10),
            -round(float(item["mean_fee_drag"]), 10),
        ),
    )
    selected = best["value"]
    return ExitRangeScanOutcome(
        enabled=True,
        applied=selected != current_value,
        param=spec.param,
        values=values,
        selected_value=selected,
        current_value=current_value,
        reason=spec.reason,
        summary=tuple(summaries),
    )


def run_plateau_probe(
    *,
    repo_root: Path,
    spec: ExitRangeScanSpec,
    current_exit_params: dict[str, Any],
    windows: list[Any],
    workers: int,
) -> PlateauProbeOutcome:
    current_value = _coerce_number(current_exit_params.get(spec.param))
    if current_value is None:
        return PlateauProbeOutcome(enabled=False, param=spec.param, skipped_reason="当前参数不是数值")
    values = _dedupe_values(spec.param, list(spec.values), len(spec.values))
    if current_value not in values:
        values = _dedupe_values(spec.param, [*values, current_value], len(values) + 1)
    if len(values) < 2 or not windows:
        return PlateauProbeOutcome(
            enabled=False,
            param=spec.param,
            values=values,
            current_value=current_value,
            skipped_reason="平台观察值或窗口不足",
        )

    window_payload = [
        {"label": window.label, "start_date": window.start_date, "end_date": window.end_date}
        for window in windows
    ]
    tasks = []
    for value in values:
        exit_params = dict(current_exit_params)
        exit_params[spec.param] = value
        tasks.append(
            {
                "repo_root": str(repo_root),
                "exit_params": exit_params,
                "windows": window_payload,
                "value": value,
            }
        )

    max_workers = max(1, min(int(workers), len(tasks)))
    summaries: list[dict[str, Any]] = []
    if max_workers == 1:
        summaries = [_probe_value_worker(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_probe_value_worker, task): task for task in tasks}
            for future in as_completed(future_map):
                summaries.append(future.result())
    summaries.sort(key=lambda item: values.index(item["value"]))

    viable = [item for item in summaries if item["total_trades"] > 0]
    if not viable:
        viable = summaries
    best = max(
        viable,
        key=lambda item: (
            round(float(item["mean_period_score"]), 10),
            round(float(item["mean_return"]), 10),
            item["value"] == current_value,
            -round(float(item["max_drawdown"]), 10),
            -round(float(item["mean_fee_drag"]), 10),
        ),
    )
    current_summary = next(
        (item for item in summaries if item["value"] == current_value),
        None,
    )
    if current_summary is None:
        return PlateauProbeOutcome(
            enabled=False,
            param=spec.param,
            values=values,
            current_value=current_value,
            skipped_reason="未找到当前中心值的观察结果",
        )

    score_values = [float(item["mean_period_score"]) for item in summaries]
    drawdown_values = [float(item["max_drawdown"]) for item in summaries]
    center_period_score = float(current_summary["mean_period_score"])
    best_period_score = float(best["mean_period_score"])
    return PlateauProbeOutcome(
        enabled=True,
        param=spec.param,
        values=values,
        current_value=current_value,
        best_value=best["value"],
        center_period_score=center_period_score,
        best_period_score=best_period_score,
        center_gap=max(0.0, best_period_score - center_period_score),
        score_span=max(score_values) - min(score_values) if score_values else 0.0,
        drawdown_span=max(drawdown_values) - min(drawdown_values) if drawdown_values else 0.0,
        current_is_best=best["value"] == current_value,
        reason=spec.reason,
        summary=tuple(summaries),
    )
