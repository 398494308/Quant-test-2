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
        ],
        "additionalProperties": False,
    }


# ==================== Prompt 组装 ====================


def build_strategy_research_prompt(
    *,
    evaluation_summary: str,
    journal_summary: str,
    previous_best_score: float,
) -> str:
    return f"""你是 BTC 合约激进趋势策略研究员。

策略目标：
- `15m` 是唯一事实源，`1h + 4h` 只是由 `15m` 聚合的趋势确认层；突破判断除了总成交量，也要看主动买卖量和成交活跃度。
- 目标不是做平滑收益，而是尽快抓到 BTC 的大上涨或大下跌，尽量陪跑主趋势，并在掉头时退出或反手。
- 允许较大波动、较大回撤，甚至允许个别阶段爆仓；不要为了“看起来更平滑”而牺牲大趋势捕获能力。
- 当前核心机会已经放宽到更细的 `4h` 趋势段，约 `5%` 级别的单边也算应当抓住的有效趋势。

你的任务不是机械拨参数，而是围绕一个可证伪的假设，修改 `src/strategy_macd_aggressive.py`，提升这套策略对 BTC 大趋势的捕获能力。

当前最优晋级分：{previous_best_score:.2f}

思考框架：
- 先看方向风险表，确认哪些方向簇仍然可用，哪些已经过热或接近耗尽。
- 再看当前验证集短板是什么：到来、陪跑、掉头、多头捕获还是空头捕获。
- 围绕最大短板提出一个因果明确的假设：因为 X，所以修改 Y，预期 Z 会变化。
- 只改最少的区域验证这个假设，避免顺手重写无关逻辑。
- 在输出 JSON 前，先在内部确认：当前主方向、最大短板、本轮因果链条、以及与最近失败轮次的核心差异。

当前诊断：
{evaluation_summary}

记忆使用规则：
- 当前评分口径 `trend_capture_v5` 的近期轮次、方向风险表和过拟合风险表，是本轮唯一主参考。
- 如果历史记忆里出现“旧评分口径弱参考”，只能把它当成因子家族或方向假设的弱启发。
- 禁止把旧口径的分数、gate 通过/失败结论或旧 best，直接当成当前口径下仍然有效的证据。

历史研究记忆：
{journal_summary}

工作区说明：
- 当前工作区里已经有目标文件 `src/strategy_macd_aggressive.py`
- 你必须先阅读并直接修改这个文件，再输出最终 JSON
- 不要把源码贴回 JSON；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件

评分方式：
- 当前评分口径是 `trend_capture_v5`。
- `development` 使用滚动 walk-forward 窗口，看均值、中位数、波动和盈利窗口占比。
- `validation` 是单段连续 holdout，`promotion_score` 只看这里。
- `test` 是隐藏验收集，你完全看不到，也不会参与本轮调参。
- `trend_capture_score` 看三件事：到来阶段是否及时跟上、主趋势中段是否陪跑、掉头时是否及时退出或反手。
- `return_score` 看连续路径最终把资金放大了多少。
- `quality_score` 是开发期滚动窗口的均值分；`promotion_score` 是验证集连续分。
- 验证集还会检查 `3` 个连续时间分块、开发期与验证集的分差、以及验证期多空交易支持是否过弱。
- 选择期连续结果只做诊断，不直接决定能否刷新最优。

关键门禁（触碰即淘汰）：
- development 滚动均值分不能太低
- development 滚动中位分不能太低
- development 滚动波动不能太大
- development 盈利窗口占比不能太低
- validation 大趋势段数 >= 18
- validation 趋势段命中率 >= 40%
- validation 趋势捕获分 >= 0.10
- development 与 validation 的分数落差 <= 0.20
- validation 多头捕获 >= 0.00
- validation 空头捕获 >= 0.00
- validation 连续路径会再按时间顺序切成 3 个分块：分块 std <= 0.30、最差分块 >= -0.20、负分块最多 1 个
- 手续费拖累 <= 8%

探索与防重复规则：
- 先读方向风险表；若某方向簇被标为 `SATURATED` / `EXHAUSTED` / `RUNTIME_RISK`，默认不要继续把它当主方向。
- 再读 `方向冷却表（系统硬约束）`；若某方向簇处于 `COOLING`，本轮系统不会接受该簇，继续沿用只会被评估前拦截。
- 若出现 `主簇过热（必须先读）`，默认必须切到不同方向簇；只有当你能明确举证“这次会改变不同交易路径”时，才允许留在热簇。
- 若出现 `探索触发（必须执行）`，本轮必须满足以下至少一项：切换方向簇 / 切换 edited region family / 切换 long-short target。
- 探索轮允许结果变差，但不允许只换 tag、只换措辞、只拨轻微阈值，或只在同一 edited region family 里做近邻微调。
- 若系统提示“同簇低变化近邻已被拒收”，下一次必须优先切到不同方向簇；若确实留在同簇，至少同时切换 edited region family 与 long-short target 或核心因子家族。
- 若仍借鉴高风险轮次或留在热簇，`novelty_proof` 必须明确说明：本轮与最近失败方向的差异、会改变哪类交易路径、以及至少两个预计会明显变化的关键诊断。

硬约束：
- 只允许修改 `src/strategy_macd_aggressive.py`。
- 只允许改这些区域：{", ".join(EDITABLE_REGIONS)}。
- 保留 `PARAMS`、`strategy()`、`_is_sideways_regime()`、`_trend_quality_ok()`、`_trend_followthrough_ok()` 这些符号。
- 不要删除、改名或只改一半仍被下游条件复用的共享中间变量；若重构某个布尔变量或上下文变量，必须同步更新全部引用，禁止留下未定义局部变量。
- 不要引入网络、文件、随机数、外部依赖。
- 每轮只做一个明确假设，最多改 1 到 3 个区域。
- 不要为了显得“有改动”而重写无关逻辑、批量改名、做大面积格式化，或新增与本轮假设无关的分支。
- 禁止 hard code 针对单个日期、单个窗口、单段行情、固定价格路径或历史结果表做特判。
- 代码必须保持简洁、结构化、可读；优先最小必要改动，避免重复条件、冗余 helper、臃肿嵌套和一次性补丁式写法。
- 最近研究表是动态的；如果你识别出具有跨轮次解释力、值得持续追踪的新核心因子/指标，可以附带 `core_factors`。
- 只有当该因子有明确依据，且足以影响后续多轮研究取舍时，才添加 `core_factors`；不要把一次性的局部现象包装成核心因子。

你要优先解决：
- 提高大趋势到来阶段的上车能力
- 提高主趋势中段的陪跑能力
- 在趋势掉头时更早退出，必要时更快反手
- 少把仓位浪费在横盘假突破、弱延续和手续费噪声上

输出要求：
- 只输出 JSON。
- `change_tags` 用简短标签描述方向，比如 `sideways_filter`, `breakout_entry`, `tighten_filter`, `reduce_false_breakout`。
- `closest_failed_cluster` 必须填写你认为最接近的最近失败方向簇；如果确实是新方向，也要写出最接近的旧簇名。
- `novelty_proof` 必须使用简体中文，明确说明“本轮与最近失败方向的差异、预计会改变的交易路径、为什么不属于重复试错”。
- `core_factors` 字段必须输出；如果当前没有足够强的新核心因子，就输出空数组 `[]`。如果填写具体因子，`name` 使用 ASCII snake_case，`thesis` 与 `current_signal` 必须使用简体中文。
- `hypothesis`、`change_plan`、`expected_effects` 必须使用简体中文。
- `candidate_id` 与 `change_tags` 保持 ASCII 标识符，避免中文变量名或空格标签。
"""


def build_strategy_runtime_repair_prompt(
    *,
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

工作区说明：
- 当前工作区里的 `src/strategy_macd_aggressive.py` 已经是刚才失败的候选版本
- 你必须直接在该文件上原地修复
- 不要把源码贴回 JSON；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件

运行错误：
{error_message}

修复规则：
- 这是同一轮 repair，不要换研究主题，不要大幅改 hypothesis。
- 优先做最小必要修复，先保证代码可运行。
- 若报错涉及 `UnboundLocalError` / `NameError` / 条件变量缺失，优先恢复缺失变量定义，或把该变量的所有引用同步替换到新的等价变量；禁止只修一处而留下半残引用。
- 除非原标签明显不准确，否则尽量保持 `change_tags`、`edited_regions`、`closest_failed_cluster` 不变。
- 只允许修改 `src/strategy_macd_aggressive.py` 可编辑区域。
- 不要引入网络、文件、随机数、外部依赖。
- 不要趁修复机会重写无关逻辑，也不要 hard code 针对单个窗口或单段行情的特判。
- 修复后的代码仍必须保持简洁、结构化、可读，避免补丁式堆条件。

输出要求：
- 仍然只输出 JSON。
- `novelty_proof` 可以在原说明基础上补一句“本次修复仅修正运行错误，不改变主假设”。
"""


def build_strategy_exploration_repair_prompt(
    *,
    candidate_id: str,
    hypothesis: str,
    change_plan: str,
    change_tags: tuple[str, ...],
    edited_regions: tuple[str, ...],
    expected_effects: tuple[str, ...],
    closest_failed_cluster: str,
    novelty_proof: str,
    block_kind: str,
    blocked_cluster: str,
    blocked_reason: str,
    locked_clusters: tuple[str, ...],
    regeneration_attempt: int,
) -> str:
    locked_cluster_text = "；".join(locked_clusters) if locked_clusters else "无"
    return f"""你正在同一轮里重生候选方向，不是开始新一轮研究。

这是第 {regeneration_attempt} 次同轮重生。目标是：保持本轮总目标不变，但必须绕开系统刚刚拒收的近邻方向，改成一个可进入评估的新方向。

原候选元信息：
- candidate_id: {candidate_id}
- hypothesis: {hypothesis}
- change_plan: {change_plan}
- change_tags: {", ".join(change_tags)}
- edited_regions: {", ".join(edited_regions)}
- expected_effects: {"；".join(expected_effects)}
- closest_failed_cluster: {closest_failed_cluster}
- novelty_proof: {novelty_proof}

系统拒收原因：
- block_kind: {block_kind}
- blocked_cluster: {blocked_cluster}
- blocked_reason: {blocked_reason}
- 当前处于冷却锁定的方向簇: {locked_cluster_text}

工作区说明：
- 当前工作区里的 `src/strategy_macd_aggressive.py` 仍然是刚才被系统拒收的候选版本
- 你必须直接在该文件上继续修改，产出一个新的候选方向
- 不要把源码贴回 JSON；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件

重生规则：
- 这不是代码修错；不要只做微小阈值近邻调整然后原样留在被拒簇。
- 若 `blocked_cluster` 正处于系统冷却中，本轮禁止继续使用该簇。
- 优先切到不同方向簇。
- 若确实留在同簇，至少同时切换 edited region family，并切换 long-short target 或核心因子家族；否则系统仍会拒收。
- 不要只换 tag、只换措辞、或只补一两条很像的条件。
- 仍然只允许修改 `src/strategy_macd_aggressive.py` 可编辑区域。
- 不要引入网络、文件、随机数、外部依赖。
- 代码仍必须保持简洁、结构化、可读。

输出要求：
- 仍然只输出 JSON。
- `novelty_proof` 必须明确说明这次相对刚才被拒方向，具体换了哪一个方向簇/edited region family/long-short target/核心因子家族。
"""
