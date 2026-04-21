#!/usr/bin/env python3
"""研究器 v2 的 prompt 与 schema。"""
from __future__ import annotations

from typing import Any

from research_v2.journal import ORDINARY_REGION_FAMILIES
from research_v2.strategy_code import (
    COMPLEXITY_ABSOLUTE_BUDGETS,
    COMPLEXITY_FAMILY_ABSOLUTE_BUDGETS,
    COMPLEXITY_DEFAULT_GROWTH_LIMITS,
    REQUIRED_FUNCTIONS,
    factor_change_mode_label,
    factor_change_mode_prompt_hint,
)

# ==================== 编辑边界 ====================


EDITABLE_REGIONS = (
    "PARAMS",
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
    "long_outer_context_ok",
    "long_breakout_ok",
    "long_pullback_ok",
    "long_trend_reaccel_ok",
    "long_signal_path_ok",
    "long_final_veto_clear",
    "short_outer_context_ok",
    "breakdown_ready",
    "short_breakdown_ok",
    "short_bounce_fail_ok",
    "short_trend_reaccel_ok",
    "short_final_veto_clear",
    "strategy",
)


def _required_symbol_text() -> str:
    function_symbols = "、".join(f"`{function_name}()`" for function_name in REQUIRED_FUNCTIONS)
    return f"`PARAMS`、{function_symbols}"


def _ordinary_family_text() -> str:
    return " / ".join(f"`{family_name}`" for family_name in ORDINARY_REGION_FAMILIES)


def _complexity_budget_text() -> str:
    absolute_lines = ["复杂度硬上限（超了直接拒收）:"]
    for function_name in ("_is_sideways_regime", "_trend_quality_ok", "_trend_followthrough_ok", "strategy"):
        limits = COMPLEXITY_ABSOLUTE_BUDGETS[function_name]
        absolute_lines.append(
            f"- `{function_name}`: lines <= {limits['lines']} / bool_ops <= {limits['bool_ops']} / ifs <= {limits['ifs']}"
        )
    absolute_lines.append("决策链 family 硬上限（禁止把复杂度搬家到 long_* / short_* / final_veto 等 helper）:")
    for family_name in ("sideways_family", "trend_quality_family", "long_path_chain", "short_path_chain"):
        limits = COMPLEXITY_FAMILY_ABSOLUTE_BUDGETS[family_name]
        absolute_lines.append(
            f"- `{family_name}`: lines <= {limits['lines']} / bool_ops <= {limits['bool_ops']} / ifs <= {limits['ifs']}"
        )

    growth = COMPLEXITY_DEFAULT_GROWTH_LIMITS
    absolute_lines.append("默认模式单轮增量上限（相对当前基底，超了直接拒收）:")
    absolute_lines.append(
        f"- 任一监控函数: lines <= +{growth['lines']} / bool_ops <= +{growth['bool_ops']} / ifs <= +{growth['ifs']}"
    )
    absolute_lines.append(
        f"- 任一决策链 family: lines <= +{growth['lines']} / bool_ops <= +{growth['bool_ops']} / ifs <= +{growth['ifs']}"
    )
    absolute_lines.append("- 若想新增条件，必须同步删旧条件、合并旧分支，优先保持净复杂度不增长。")
    return "\n".join(absolute_lines)


def _bootstrap_journal_excerpt(journal_summary: str, *, max_lines: int = 28, max_chars: int = 2800) -> str:
    if not journal_summary.strip():
        return ""
    lines: list[str] = []
    total_chars = 0
    for raw_line in journal_summary.splitlines():
        line = raw_line.rstrip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        projected = total_chars + len(line) + 1
        if len(lines) >= max_lines or projected > max_chars:
            break
        lines.append(line)
        total_chars = projected
    return "\n".join(lines).strip()


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


def build_strategy_agents_instructions(*, factor_change_mode: str = "default") -> str:
    complexity_budget_text = _complexity_budget_text()
    return f"""你是 BTC 永续合约激进趋势策略研究员。

项目目标：
- 用 OKX 数据研究一套高弹性趋势捕获策略；允许较大波动，但不能靠日期特判、路径硬编码或伪优化刷分。
- `15m` 是唯一事实源，`1h + 4h` 只是由 `15m` 聚合的确认层；突破/跌破除了成交量，也要结合方向流量代理与成交活跃度。
- 目标不是做平滑净值，而是更早跟上 BTC 的主要上涨/下跌，并在趋势失效时更快退出或反手。

持久 session 规则：
- 这是一个跨多轮复用的研究 session。不要假设主进程会每轮重复灌完整历史；你需要主动利用本地只读记忆文件。
- 每轮开始先读 `wiki/failure_wiki.md` 的概览；新 session 首轮还必须先读 `wiki/latest_history_package.md` 的摘要段。
- 形成单一因果假设后，必须回看一次 `wiki/failure_wiki.md`；若命中相同或高度相似的失败 cut，应在本轮内改写假设，不要硬提交旧方向。
- `memory/raw/rounds/` 与 `memory/raw/full_history.jsonl` 保留未压缩原始历史；需要深挖时去看，但优先先读 wiki 摘要与失败聚合。

工作区文件职责：
- `src/strategy_macd_aggressive.py`：唯一允许修改的策略文件。
- `src/backtest_macd_aggressive.py`：回测、成交路径与指标口径定义，只读参考。
- `wiki/failure_wiki.md`：当前评分口径下的失败 wiki；先看它，避免重走已知坏 cut。
- `wiki/latest_history_package.md`：当前 stage 历史摘要包；先看前部执行摘要与失败核，再决定是否下钻表格。
- `memory/`：研究记忆归档，只读参考；其中 `raw/` 保留原始历史，`summaries/` 保留压缩摘要。
- `data/`：OKX K 线 / funding / 成交流量代理等数据，只读参考。

工作方式：
- 先阅读并直接修改 `src/strategy_macd_aggressive.py`，必要时再看回测实现或数据文件，不要靠猜测写策略。
- 每轮只验证一个可证伪假设；改动要能映射到真实交易路径变化，而不是只制造源码 diff。
- 优先保持代码结构化、规则块命名清晰、阈值集中、因果链可解释。
- 默认先做删减、合并、替换，再考虑新增条件；如果一个新条件只覆盖很窄的历史片段，优先删旧条件或改旧阈值，不要继续叠分叉。
- 不要把同一侧 path 拆成多个近似微变体；一个 path 没有明显新增交易路径时，应视为失败假设，而不是继续微调同一区间。
- 允许只改 `strategy()` / `PARAMS` / 少量新 helper 做结构化重排，但前提是你能明确说明这会改变最终信号集合、退出集合或真实交易路径；否则不要为了造 diff 去动它们。
- 明确要求：不要“堆屎”。很多你想到的过滤、例外、path 或 veto，当前策略里往往已经以别的名字存在；新增前先检查现有规则块、阈值和最终放行链是否已经表达了同一因果。
- 若现有脚本里已经有近似逻辑，不要换个名字再写一份重复条件；优先删旧、并旧、改旧，禁止把同一因果链在不同 helper / path / veto 里重复实现。
- 明确允许结构性删减轮：`remove_dead_gate`、`merge_veto`、`widen_outer_context` 都是合法 change_tags；这类轮次的目标是减少死分支、提高 reachability。
- {factor_change_mode_prompt_hint(factor_change_mode)}
{complexity_budget_text}

输出与协作规则：
- 只输出 JSON，不要解释，不要 markdown，不要贴源码。
- 主进程会直接读取你改好的 `src/strategy_macd_aggressive.py`。
- 除 `candidate_id` 与 `change_tags` 外，其余说明字段必须使用简体中文。

编辑边界：
- 只允许改这些区域：{", ".join(EDITABLE_REGIONS)}。
- 必须保留这些符号，不允许删除、改名或合并回旧结构：{_required_symbol_text()}。
- `strategy()` 与 `PARAMS` 属于特殊区域；除它们外，普通 family 为 {_ordinary_family_text()}。
- 不要引入网络、文件写入、随机数、外部依赖，也不要做无关重构、批量改名或大面积格式化。
- 不要 hard code 针对单个日期、窗口、行情段或历史结果表的特判。
"""


def build_strategy_system_prompt(*, factor_change_mode: str = "default") -> str:
    return f"""遵守当前工作区本地 `AGENTS.md`，它是本 session 的长期规则。

当前因子模式：{factor_change_mode_label(factor_change_mode)}
- {factor_change_mode_prompt_hint(factor_change_mode)}

你在一个持久研究 session 中工作；当前用户提示只补充本轮目标、诊断和失败反馈。
只返回符合 schema 的 JSON，不要输出 markdown、解释或源码。
除非当前提示明确允许，否则不要创建、修改或删除 `src/strategy_macd_aggressive.py` 之外的文件。
"""


def _safe_metric(metrics: dict[str, Any] | None, key: str) -> float:
    if not isinstance(metrics, dict):
        return 0.0
    try:
        return float(metrics.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _side_bias_guidance(reference_metrics: dict[str, Any] | None) -> str:
    bull = _safe_metric(reference_metrics, "validation_bull_capture_score")
    bear = _safe_metric(reference_metrics, "validation_bear_capture_score")
    hit_rate = _safe_metric(reference_metrics, "validation_segment_hit_rate")
    gap = bear - bull

    if bear >= 0.20 and gap >= 0.12:
        return (
            "多空强化偏置（软引导，不是硬限制）:\n"
            f"- 当前主参考 val 多头/空头捕获 = {bull:.2f} / {bear:.2f}，命中率 = {hit_rate:.0%}。\n"
            "- 这说明空头已有可用基础，但多头仍明显偏弱，继续把主要探索预算投入空头，边际收益大概率更低。\n"
            "- 本轮默认优先考虑 `long` 或 `mixed` 假设：先补多头的到来、接力、二次启动、陪跑，而不是继续抛光空头。\n"
            "- 这不是禁止修改空头；只有当某个 mixed 假设能在基本不破坏空头的前提下补多头，或空头出现新的硬伤时，才值得继续动 short。\n"
            "- 若选择 mixed 方案，`expected_effects` 的第一优先级应是改善多头捕获/命中率，第二优先级才是维持空头不明显恶化。"
        )

    if bull < 0.05 <= bear:
        return (
            "多空强化偏置（软引导，不是硬限制）:\n"
            f"- 当前主参考 val 多头/空头捕获 = {bull:.2f} / {bear:.2f}。\n"
            "- 当前更值得优先探索的是多头侧，因为空头至少已经过线，而多头仍接近或低于门槛。\n"
            "- 默认优先做能补多头捕获的 `long` 或 `mixed` 假设，但不要为了补多头而粗暴破坏空头主框架。"
        )

    return ""


def build_strategy_research_prompt(
    *,
    evaluation_summary: str,
    journal_summary: str,
    previous_best_score: float,
    reference_metrics: dict[str, Any] | None = None,
    score_regime: str = "trend_capture_v6",
    promotion_min_delta: float = 0.02,
    factor_change_mode: str = "default",
    current_complexity_headroom_text: str = "",
    session_mode: str = "resume",
    history_package_path: str = "wiki/latest_history_package.md",
    failure_wiki_path: str = "wiki/failure_wiki.md",
) -> str:
    side_bias_guidance = _side_bias_guidance(reference_metrics)
    side_bias_block = f"\n{side_bias_guidance}\n" if side_bias_guidance else ""
    complexity_headroom_block = (
        f"\n{current_complexity_headroom_text}\n"
        if current_complexity_headroom_text.strip()
        else "\n"
    )
    bootstrap_excerpt = _bootstrap_journal_excerpt(journal_summary)
    bootstrap_block = ""
    if session_mode == "bootstrap":
        bootstrap_block = (
            "\n新 session 启动补充（完整版本见 "
            f"`{history_package_path}`）：\n"
            f"{bootstrap_excerpt or '请先读取本地历史包摘要。'}\n"
        )
    session_label = "stage_bootstrap" if session_mode == "bootstrap" else "stage_resume"
    return f"""当前回合任务：
- session 状态：`{session_label}`
- 围绕一个可证伪假设，直接修改 `src/strategy_macd_aggressive.py`。
- 本轮目标是改变真实交易路径，不是只制造源码 diff；若 smoke 行为完全不变，会被系统按 `behavioral_noop` 拒收。
- 当前评分口径是 `{score_regime}`；只有在 `gate` 通过，且相对当前 champion 的有效 `promotion_delta > {promotion_min_delta:.2f}` 时，候选才有资格刷新当前主参考。
- `train` 看滚动窗口均值/中位数，`val` 看连续 holdout 的 `promotion_score`，`test` 是隐藏验收集。

当前 champion 参考晋级分：{previous_best_score:.2f}
{side_bias_block}
本轮本地只读记忆文件：
- 失败 wiki：`{failure_wiki_path}`
- 历史摘要包：`{history_package_path}`
- 完整原始历史：`memory/raw/rounds/` 与 `memory/raw/full_history.jsonl`
{bootstrap_block}

本轮阅读顺序（必须执行）：
1. 先看“刷新条件与本轮目标”，确认本轮不是为了造 diff，而是为了改变真实交易路径。
2. 再看“当前诊断”，先定位当前 gate 主失败项、弱侧和 val 漏斗堵点。
3. 再看 `{failure_wiki_path}` 与 `{history_package_path}` 的前部摘要；相同失败核或 exact cut 只算一个已知坏盆地，不要逐条重读。
4. 基于上面三步只提出一个单一因果假设，并明确它预计会新增、删除或迁移哪类真实交易。
5. 形成假设后，必须回看一次 `{failure_wiki_path}` 做自检；若命中相同或高度相似的失败 cut，先在本轮内改写假设再提交。
6. 若仍落在同方向簇或同 ordinary family，必须证明这次改的是不同 choke point、不同最终放行链，或不同的真实交易路径层级；`strategy-only` 也可以，但必须说清它改变了哪一层最终路由。

当前因子模式：{factor_change_mode_label(factor_change_mode)}

{evaluation_summary}

本轮执行框架：
- 先判断最大短板是在多头、空头、到来、陪跑还是掉头；再提出单一因果假设。
- 如果目标是补早段 long / short，先看外层总闸门：长侧先看 `long_outer_context_ok`，空侧先看 `short_outer_context_ok`。
- 新增 path 不等于新增交易；必须继续检查最终合流与否决链。长侧重点看 `long_signal_path_ok -> long_final_veto_clear -> _trend_followthrough_long()`，空侧重点看 `breakdown_ready -> short_final_veto_clear -> _trend_followthrough_short()`。
- 如果主要改 `_trend_followthrough_ok()`、`_trend_quality_ok()` 或 `_flow_confirmation_ok()`，必须确认现有 `strategy()` 路径会触达；否则优先改 `strategy()`。
- 若最近连续出现 `behavioral_noop` 或结果盆地重复，本轮默认必须放大步长：优先切不同方向簇，或切不同 choke point / 最终放行链；不要只换措辞、tag 或近邻阈值。
- 若漏斗诊断显示一侧长期 0 交易、outer_context 几乎全死，或 path 能过但 final_veto 基本全死，优先做结构性删减轮：`remove_dead_gate` / `merge_veto` / `widen_outer_context`。
- 默认模式下有复杂度预算：如果你想加一条新条件，必须同步删掉或合并旧条件，避免 `strategy()` 和关键 helper 再次膨胀。
- 若 headroom 显示某 family 的 `bool_ops` 已经剩余 `0`，或 `lines` 剩余不超过 `8`，默认把它视为饱和区；除非本轮 `change_plan` 明确先删旧条件，否则不要再把主改动落到该 family。
- 若你的主要假设是最终路由或最终 veto 错配，允许只改 `strategy()` 或少量结构化 helper；但必须在 `change_plan` 里写清楚它会新增、删除或迁移哪类真实交易。
{complexity_headroom_block}

当前口径的 gate / 评分提醒：
- val 趋势段命中率 >= 35%
- val 趋势捕获分 >= 0.05
- train 与 val 分数落差 <= 0.30
- val 多头捕获 >= 0.00，val 空头捕获 >= 0.00
- val 会再切成 3 个连续时间分块：最差分块 >= -0.35，负分块最多 1 个
- 手续费拖累 <= 8%
- train+val 严重集中度过拟合会直接淘汰

输出要求：
- 只输出 JSON。
- `change_plan` 与 `novelty_proof` 必须明确写出预计会新增、删除或移动哪类实际交易，不要只写“提高确认质量”。
- `change_tags` 用简短 ASCII 标签；`candidate_id` 也保持 ASCII。
- `closest_failed_cluster` 必须填写最接近的最近失败方向簇；若确实是新方向，也要写出最接近的旧簇名。
- `core_factors` 字段必须输出；它只用于描述本轮复用或删减的现有决策因子，不是新增因子的申请通道。没有就输出空数组 `[]`。
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
- 这是同一个持久 session；若先前上下文丢失，以本提示、`AGENTS.md`、`wiki/failure_wiki.md` 和当前源码为准
- 当前工作区里的 `src/strategy_macd_aggressive.py` 已经是刚才失败的候选版本
- 你必须直接在该文件上原地修复
- 不要把源码贴回 JSON；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件

运行错误：
{error_message}

修复规则：
- 这是同一轮 repair，不要换研究主题，不要大幅改 hypothesis。
- 优先做最小必要修复，先保证代码可运行。
- 若报错是复杂度预算或复杂度增量超限，必须先删旧条件或合并旧分支，让触发报错的 function/family 回到预算内。
- 不要把复杂度从一个函数搬到同 family 的别的 helper；这类“搬家”仍会因为 family 预算被系统拒收。
- 默认模式允许少量新 helper/常量做结构化抽离，但它们必须服务于删旧、并旧和降复杂度，不能借机复制旧因果链。
- 若报错涉及缺失 helper / 缺失命名规则块，优先恢复缺失的原函数定义，保留拆分后的结构，不要把多个 helper 合并回旧函数。
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
    feedback_note: str = "",
) -> str:
    locked_cluster_text = "；".join(locked_clusters) if locked_clusters else "无"
    feedback_block = f"\n附加反馈（本次必须处理）:\n{feedback_note}\n" if feedback_note else "\n"
    return f"""你正在同一轮里重生候选方向，不是开始新一轮研究。

这是第 {regeneration_attempt} 次同轮重生。目标是：保持本轮要解决的策略短板大方向不变，但必须绕开系统刚刚拒收的近邻方向，改成一个可进入评估的新方向；如果上一版被 `behavioral_noop` 拒收，默认说明上一版局部因果链已失效，可以直接重写局部 hypothesis / change_plan。

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
{feedback_block}

工作区说明：
- 这是同一个持久 session；若先前上下文丢失，以本提示、`AGENTS.md`、`wiki/failure_wiki.md` 和当前源码为准
- 当前工作区里的 `src/strategy_macd_aggressive.py` 仍然是刚才被系统拒收的候选版本
- 你必须直接在该文件上继续修改，产出一个新的候选方向
- 不要把源码贴回 JSON；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件

重生规则：
- 这不是代码修错；不要只做微小阈值近邻调整然后原样留在被拒簇。
- 若 `blocked_cluster` 正处于系统冷却中，本轮禁止继续使用该簇。
- 优先切到不同方向簇。
- 若确实留在同簇，至少切换普通 family、最终放行链或 long-short target 中的一项；`strategy-only` 结构性改路由是允许的，但必须明确说明为什么这次会触达不同交易路径。
- 不要只换 tag、只换措辞、或只补一两条很像的条件。
- 若上一版是 `behavioral_noop`，不要沿用原 hypothesis / change_plan 只换表述；应默认把那条局部路径假设视为已被证伪，并改成新的可触达交易路径。
- 重生后的候选必须预计改变 smoke 窗口实际交易路径；如果上一版只是 helper / followthrough 变化但没有触发新交易，优先改 `strategy()` 的最终入场路径。
- 如果附加反馈显示 smoke 窗口的交易数、收益和信号统计完全没变，默认说明你上一版改动没有触达真实交易路径；这次必须优先改能改变最终信号集合或退出集合的规则块，而不是继续只拨不会触发的细阈值。
- 在提交重生版之前，回看一次 `wiki/failure_wiki.md`；如果你这次仍命中相同或高度相似的失败 cut，必须继续改写，不要原样交回。
- 对长侧优先检查 `long_outer_context_ok` 与 `long_final_veto_clear`；对空侧优先检查 `short_outer_context_ok` 与 `short_final_veto_clear`。若这些总闸门不动，新增 path 很可能仍是死分支。
- 不要把“新增一个 `xxx_path_ok`”误当成一定会新增交易；只有它真正穿过最终 veto / followthrough，smoke 行为才会改变。
- 若附加反馈显示 `outer_context` 或 `final_veto` 是主要堵点，允许直接做结构性删减轮：`remove_dead_gate`、`merge_veto`、`widen_outer_context`。
- 仍然只允许修改 `src/strategy_macd_aggressive.py` 可编辑区域。
- 不要引入网络、文件、随机数、外部依赖。
- 代码仍必须保持简洁、结构化、可读。

输出要求：
- 仍然只输出 JSON。
- `novelty_proof` 必须明确说明这次相对刚才被拒方向，具体换了哪一个方向簇/普通 family/long-short target/核心因子家族。
"""
