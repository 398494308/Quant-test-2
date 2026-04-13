#!/usr/bin/env python3
"""研究器 v2 的 prompt 与 schema。"""
from __future__ import annotations

from typing import Any


# ==================== 编辑边界 ====================


EDITABLE_REGIONS = (
    "PARAMS",
    "_is_sideways_regime",
    "_trend_quality_ok",
    "_trend_followthrough_ok",
    "strategy",
)


# ==================== 结构化输出 ====================


def build_candidate_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "candidate_id": {"type": "string"},
            "hypothesis": {"type": "string"},
            "change_plan": {"type": "string"},
            "change_tags": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 6,
            },
            "edited_regions": {
                "type": "array",
                "items": {"type": "string", "enum": list(EDITABLE_REGIONS)},
                "minItems": 1,
                "maxItems": len(EDITABLE_REGIONS),
            },
            "expected_effects": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
            },
            "strategy_code": {"type": "string"},
        },
        "required": [
            "candidate_id",
            "hypothesis",
            "change_plan",
            "change_tags",
            "edited_regions",
            "expected_effects",
            "strategy_code",
        ],
        "additionalProperties": False,
    }


# ==================== Prompt 组装 ====================


def build_strategy_research_prompt(
    *,
    strategy_source: str,
    evaluation_summary: str,
    journal_summary: str,
    previous_best_score: float,
) -> str:
    return f"""你是 BTC 合约激进趋势策略研究员。

你的目标不是机械拨参数，而是围绕一个清晰假设，修改 `src/strategy_macd_aggressive.py`，让评估质量分与晋级分提升。

当前最优晋级分：{previous_best_score:.2f}

当前评估诊断：
{evaluation_summary}

历史研究记忆：
{journal_summary}

当前策略源码：
```python
{strategy_source}
```

硬约束：
- 只允许修改 `src/strategy_macd_aggressive.py`。
- 只允许改这些区域：{", ".join(EDITABLE_REGIONS)}。
- 保留 `PARAMS`、`strategy()`、`_is_sideways_regime()`、`_trend_quality_ok()`、`_trend_followthrough_ok()` 这些符号。
- 不要引入网络、文件、随机数、外部依赖。
- 不要为了刷单窗收益而牺牲留出和压力测试。
- 每轮只做一个明确假设，最多改 1 到 3 个区域。
- 如果最近某个方向连续失败，不要继续重复。

你要优先解决：
- 留出收益偏弱
- 横盘假突破
- 手续费拖累过高
- 高回撤而非高质量收益

输出要求：
- 只输出 JSON。
- `strategy_code` 字段里放完整的最新策略文件源码，不要 markdown。
- `change_tags` 用简短标签描述方向，比如 `sideways_filter`, `breakout_entry`, `tighten_filter`, `reduce_false_breakout`。
"""
