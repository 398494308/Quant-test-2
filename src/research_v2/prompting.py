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
            "core_factors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "thesis": {"type": "string"},
                        "current_signal": {"type": "string"},
                    },
                    "required": ["name", "thesis", "current_signal"],
                    "additionalProperties": False,
                },
                "minItems": 0,
                "maxItems": 4,
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
            "core_factors",
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

门禁规则（触碰即淘汰）：
- 总交易数 >= 30
- eval 交易数 >= 24
- 验证集交易数 >= 5
- eval 正收益窗口占比 >= 30%
- 最大回撤 <= 50%
- 爆仓次数 = 0
- 验证集平均收益 >= -10%
- eval 与验证集落差 <= 30
- 手续费拖累 <= 6%

评分方式：
- `quality_score` = eval 非重叠 OOS 主路径的年化 Sortino Ratio
- `promotion_score` = eval 非重叠 OOS 主路径 + 验证集日收益率合并后的年化 Sortino Ratio
- rolling eval 窗口只用于稳定性诊断，不会重复加权同一天
- Sortino 只惩罚下行波动，不惩罚向上的大波动
- 你看不到验证集的具体数字，但门禁会告诉你是否通过

硬约束：
- 只允许修改 `src/strategy_macd_aggressive.py`。
- 只允许改这些区域：{", ".join(EDITABLE_REGIONS)}。
- 保留 `PARAMS`、`strategy()`、`_is_sideways_regime()`、`_trend_quality_ok()`、`_trend_followthrough_ok()` 这些符号。
- 不要引入网络、文件、随机数、外部依赖。
- 每轮只做一个明确假设，最多改 1 到 3 个区域。
- 如果最近某个方向连续失败，不要继续重复。
- 最近研究表是动态的；如果你识别出具有跨轮次解释力、值得持续追踪的新核心因子/指标，可以附带 `core_factors`。
- 只有当该因子有明确依据，且足以影响后续多轮研究取舍时，才添加 `core_factors`；不要把一次性的局部现象包装成核心因子。

你要优先解决：
- 提高 Sortino（减少下行波动、提高收益）
- 横盘假突破导致的无效交易
- 手续费拖累过高
- 高回撤

输出要求：
- 只输出 JSON。
- `strategy_code` 字段里放完整的最新策略文件源码，不要 markdown。
- `change_tags` 用简短标签描述方向，比如 `sideways_filter`, `breakout_entry`, `tighten_filter`, `reduce_false_breakout`。
- `core_factors` 字段必须输出；如果当前没有足够强的新核心因子，就输出空数组 `[]`。如果填写具体因子，`name` 使用 ASCII snake_case，`thesis` 与 `current_signal` 必须使用简体中文。
- `hypothesis`、`change_plan`、`expected_effects` 必须使用简体中文。
- `candidate_id` 与 `change_tags` 保持 ASCII 标识符，避免中文变量名或空格标签。
"""
