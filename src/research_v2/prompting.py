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
            "closest_failed_cluster": {"type": "string"},
            "novelty_proof": {"type": "string"},
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
            "closest_failed_cluster",
            "novelty_proof",
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

这套策略不是低波动理财策略，而是：
- `15m` 执行，`1h + 4h` 做趋势确认。
- 目标是尽快抓到 BTC 的大上涨或大下跌，尽量陪跑主趋势，并在掉头时退出或反手。
- 允许较大波动、较大回撤，甚至允许个别阶段爆仓；不要为了“看起来更平滑”而牺牲大趋势捕获能力。

你的目标不是机械拨参数，而是围绕一个清晰假设，修改 `src/strategy_macd_aggressive.py`，提升这套策略对大趋势的捕获能力。

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
- eval 大趋势段数 >= 8
- validation 大趋势段数 >= 3
- eval 趋势段命中率 >= 35%
- validation 趋势段命中率 >= 25%
- eval 趋势捕获分 >= 0.10
- validation 趋势捕获分 >= 0.00
- eval 与 validation 的趋势捕获落差 <= 0.45
- 综合多头捕获分 >= -0.10
- 综合空头捕获分 >= -0.10
- 手续费拖累 <= 8%
- 严重过拟合风险会直接淘汰；如果结果主要依赖少数同类行情、单一同向链或明显多空偏科，即使分数高也不能通过

评分方式：
- 主评分口径是 `trend_capture_v1`
- 先在唯一时间轴上识别 BTC 的大趋势段，再把每段拆成“到来 / 陪跑 / 掉头”三部分
- `trend_capture_score` 衡量：是否及时跟上、是否陪跑主趋势、是否在掉头时跑掉或反手
- `return_score` 衡量：整段路径最后把资金放大了多少
- `quality_score` = `0.70 * eval_trend_capture_score + 0.30 * eval_return_score`
- `promotion_score` = `0.70 * combined_trend_capture_score + 0.30 * combined_return_score`
- 爆仓和回撤现在是诊断项，不是主评分，也不是单独惩罚项
- 你看不到 validation 的逐项细节，但 gate 会告诉你是否通过

硬约束：
- 只允许修改 `src/strategy_macd_aggressive.py`。
- 只允许改这些区域：{", ".join(EDITABLE_REGIONS)}。
- 保留 `PARAMS`、`strategy()`、`_is_sideways_regime()`、`_trend_quality_ok()`、`_trend_followthrough_ok()` 这些符号。
- 不要引入网络、文件、随机数、外部依赖。
- 每轮只做一个明确假设，最多改 1 到 3 个区域。
- 你必须先阅读最前面的方向风险表。
- 你还必须阅读紧随其后的过拟合风险表。
- 不要把带有 `SATURATED` / `EXHAUSTED` 标签的方向簇作为本轮主方向。
- 不要把在过拟合风险表里被标为 `高` / `严重` 的轮次当作可直接复用的成功模板。
- 如果你仍要借鉴这些高风险轮次，必须在 `novelty_proof` 里说明：这次如何打破它对少数行情、单一同向链或单边方向的依赖。
- 如果你仍选择接近该方向簇，必须在 `novelty_proof` 里明确说明这次为什么不是重复探索、会改变哪类交易路径、为什么不应再是零增益。
- 如果最近多轮都零增益，优先切换方向簇或切换 edited region family，而不是继续围绕同一失败簇做近邻微调。
- 如果历史记忆里出现 `探索触发（必须执行）`，你必须把本轮当作探索轮，而不是继续做同簇微调。
- 探索轮优先切换核心因子家族或 edited region family；允许结果变差，但不允许只做措辞替换、标签换名或近邻改写。
- 探索轮应尽量让 `segment_hit_rate`、`bull_capture_score`、`bear_capture_score`、`avg_fee_drag`、`total_trades` 这类关键诊断至少两项出现明显变化。
- 最近研究表是动态的；如果你识别出具有跨轮次解释力、值得持续追踪的新核心因子/指标，可以附带 `core_factors`。
- 只有当该因子有明确依据，且足以影响后续多轮研究取舍时，才添加 `core_factors`；不要把一次性的局部现象包装成核心因子。

你要优先解决：
- 提高大趋势到来阶段的上车能力
- 提高主趋势中段的陪跑能力
- 在趋势掉头时更早退出，必要时更快反手
- 少把仓位浪费在横盘假突破、弱延续和手续费噪声上

输出要求：
- 只输出 JSON。
- `strategy_code` 字段里放完整的最新策略文件源码，不要 markdown。
- `change_tags` 用简短标签描述方向，比如 `sideways_filter`, `breakout_entry`, `tighten_filter`, `reduce_false_breakout`。
- `closest_failed_cluster` 必须填写你认为最接近的最近失败方向簇；如果确实是新方向，也要写出最接近的旧簇名。
- `novelty_proof` 必须使用简体中文，明确说明“本轮与最近失败方向的差异、预计会改变的交易路径、为什么不属于重复试错”。
- `core_factors` 字段必须输出；如果当前没有足够强的新核心因子，就输出空数组 `[]`。如果填写具体因子，`name` 使用 ASCII snake_case，`thesis` 与 `current_signal` 必须使用简体中文。
- `hypothesis`、`change_plan`、`expected_effects` 必须使用简体中文。
- `candidate_id` 与 `change_tags` 保持 ASCII 标识符，避免中文变量名或空格标签。
"""


def build_strategy_runtime_repair_prompt(
    *,
    strategy_source: str,
    failed_candidate_code: str,
    candidate_id: str,
    hypothesis: str,
    change_plan: str,
    change_tags: tuple[str, ...],
    edited_regions: tuple[str, ...],
    expected_effects: tuple[str, ...],
    closest_failed_cluster: str,
    novelty_proof: str,
    error_message: str,
    repair_attempt: int,
) -> str:
    return f"""你正在修复同一轮候选代码，不是开始新一轮研究。

这是第 {repair_attempt} 次修复尝试。目标是：保持原研究方向基本不变，优先修复运行错误，让代码先通过 smoke test，再进入完整评估。

原候选元信息：
- candidate_id: {candidate_id}
- hypothesis: {hypothesis}
- change_plan: {change_plan}
- change_tags: {", ".join(change_tags)}
- edited_regions: {", ".join(edited_regions)}
- expected_effects: {"；".join(expected_effects)}
- closest_failed_cluster: {closest_failed_cluster}
- novelty_proof: {novelty_proof}

当前基底策略源码：
```python
{strategy_source}
```

刚才失败的候选源码：
```python
{failed_candidate_code}
```

运行错误：
{error_message}

修复规则：
- 这是同一轮 repair，不要换研究主题，不要大幅改 hypothesis。
- 优先做最小必要修复，先保证代码可运行。
- 除非原标签明显不准确，否则尽量保持 `change_tags`、`edited_regions`、`closest_failed_cluster` 不变。
- 只允许修改 `src/strategy_macd_aggressive.py` 可编辑区域。
- 不要引入网络、文件、随机数、外部依赖。

输出要求：
- 仍然只输出 JSON。
- `strategy_code` 提供完整策略源码。
- `novelty_proof` 可以在原说明基础上补一句“本次修复仅修正运行错误，不改变主假设”。
"""
