#!/usr/bin/env python3
"""研究器 v2 的 prompt 与返回格式。"""
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


# ==================== 返回格式 ====================


def build_candidate_response_format_instructions() -> str:
    return """- 只返回一个纯文本候选摘要，不要 JSON，不要 markdown，不要贴源码。
- 按以下字段顺序逐行输出，字段名保持英文小写并使用英文冒号：
  candidate_id:
  hypothesis:
  change_plan:
  change_tags:
  expected_effects:
  closest_failed_cluster:
  novelty_proof:
  core_factors:
- `change_tags` 用逗号分隔短 ASCII 标签。
- `expected_effects` 用 `||` 分隔 1-5 条预期影响。
- `core_factors` 没有就写 `none`；有则写成 `name | thesis | current_signal || ...`。
- 不要输出 `edited_regions`；系统会按真实 diff、可编辑区域边界和源码签名自动判定改动区域。"""


def build_edit_completion_instructions() -> str:
    return """- 本阶段唯一完成条件：直接修改 `src/strategy_macd_aggressive.py`。
- 调用结束时该文件必须与当前基底不同；若文件 hash 未变化，本次回复会被主进程直接丢弃。
- 完成编辑后只回复 `EDIT_DONE`。
- 不要输出 hypothesis、change_plan、JSON、markdown 或源码；这些会在落码成功后由下一阶段单独收集。"""


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

角色规则：
- `planner` 使用持久 session，负责研究方向、失败原因记忆和本轮 round brief。
- `edit_worker` / `repair_worker` 是短生命周期 worker，只负责把已确定方向落到 `src/strategy_macd_aggressive.py`，或修技术错误；它们不需要重扫全量历史。

持久 session 规则（只对 `planner` 生效）：
- 这是一个跨多轮复用的研究 session。不要假设主进程会每轮重复灌完整历史；你需要主动利用本地只读记忆文件。
- 每轮开始优先快速扫一遍 `wiki/duplicate_watchlist.md`；它只列最近最容易重复提交的少量源码指纹。
- 再读 `wiki/failure_wiki.md` 的概览；新 session 首轮优先再读 `wiki/latest_history_package.md` 的摘要段。
- 形成单一因果假设后，必须回看一次 `wiki/failure_wiki.md`；若命中相同或高度相似的失败 cut，优先在本轮内改写假设，不要无意义重走旧方向。
- `memory/raw/rounds/` 与 `memory/raw/full_history.jsonl` 保留未压缩原始历史；需要深挖时去看，但优先先读 wiki 摘要与失败聚合。
- 若 `wiki/` 或 `memory/` 里的辅助记忆暂时不可用，这不是本轮 `no_edit` 的合法理由；此时应退化为直接读取并修改 `src/strategy_macd_aggressive.py`。

工作区文件职责：
- `src/strategy_macd_aggressive.py`：唯一允许修改的策略文件。
- `src/backtest_macd_aggressive.py`：回测、成交路径与指标口径定义，只读参考。
- `config/research_v2_operator_focus.md`：人工方向卡，只是软引导，不是系统硬限制。
- `wiki/duplicate_watchlist.md`：最近高频重复源码黑名单摘要；先扫它，避免把同一份补丁再交一次。
- `wiki/failure_wiki.md`：当前评分口径下的失败 wiki；先看它，避免重走已知坏 cut。
- `wiki/latest_history_package.md`：当前 stage 历史摘要包；先看前部执行摘要与失败核，再决定是否下钻表格。
- `wiki/last_rejected_snapshot.md`：最近一次被系统判错的候选摘要、失败原因与 diff 提示；重生轮优先看它。
- `wiki/last_rejected_candidate.py`：最近一次被系统判错的完整候选代码，只读反例参考；不要把它当成本轮改动基底。
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
- `planner` 只返回纯文本字段摘要，不要 JSON，不要解释，不要 markdown，不要贴源码。
- `edit_worker` / `repair_worker` 完成编辑后只回复 `EDIT_DONE`。
- 主进程会直接读取工作区里改好的 `src/strategy_macd_aggressive.py`。
- 除 `candidate_id` 与 `change_tags` 外，其余说明字段必须使用简体中文。
- 不允许把 `blocked` / `no_edit` / `no_change` / `sandbox_blocked` / “未执行代码改动” 这类占位说明当成合法提交结果。

编辑边界：
- 只允许改这些区域：{", ".join(EDITABLE_REGIONS)}。
- 你不需要再自报 `edited_regions`；系统会根据真实 diff 自动归类 region / family，并据此做重复探索与边界校验。
- 边界判定只看两件事：是否真的改到了 `src/strategy_macd_aggressive.py` 的可编辑区域，以及是否越界；说明文本只用于帮助主进程理解你的意图。
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
只返回约定字段的纯文本候选摘要，不要输出 JSON、markdown、解释或源码。
除非当前提示明确允许，否则不要创建、修改或删除 `src/strategy_macd_aggressive.py` 之外的文件。
若辅助记忆文件暂时不可用，也必须继续基于当前 `src/strategy_macd_aggressive.py` 做真实改动；禁止把“未读到文件”当成合法终止条件。
"""


def build_strategy_worker_system_prompt(
    *,
    factor_change_mode: str = "default",
    worker_kind: str = "edit_worker",
) -> str:
    return f"""遵守当前工作区本地 `AGENTS.md`，它是当前工作区的长期规则。

当前因子模式：{factor_change_mode_label(factor_change_mode)}
- {factor_change_mode_prompt_hint(factor_change_mode)}

你当前是短生命周期 `{worker_kind}`，不是持久研究 planner。
- 不要重新做全量历史研究，不要重新定义本轮方向。
- 只根据当前提示里的 round brief 或 repair 指令，直接修改 `src/strategy_macd_aggressive.py`。
- 除非当前提示明确允许，否则不要创建、修改或删除其他文件。
- 完成编辑后只回复 `EDIT_DONE`。
- 禁止输出 JSON、markdown、解释、计划或源码。
- 若辅助记忆文件暂时不可用，也必须继续基于当前 `src/strategy_macd_aggressive.py` 做真实改动；禁止把“未读到文件”当成合法终止条件。
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


def _champion_focus_hint(reference_metrics: dict[str, Any] | None) -> str:
    bull = _safe_metric(reference_metrics, "validation_bull_capture_score")
    bear = _safe_metric(reference_metrics, "validation_bear_capture_score")
    arrival = _safe_metric(reference_metrics, "validation_arrival_capture_score")
    escort = _safe_metric(reference_metrics, "validation_escort_capture_score")
    turn = _safe_metric(reference_metrics, "validation_turn_adaptation_score")

    signals: list[str] = []
    if bear - bull >= 0.05:
        signals.append(f"多头捕获弱于空头（val {bull:.2f}/{bear:.2f}）")
    if escort + 0.08 < arrival and escort + 0.08 < turn:
        signals.append(f"陪跑能力明显弱于到来/掉头（val {arrival:.2f}/{escort:.2f}/{turn:.2f}）")

    if not signals:
        return ""

    return (
        "当前 champion 缺陷（软诊断，不是硬限制）: "
        + "；".join(signals)
        + "。优先尝试能补多头延续持有或减少过早出清的假设；若其他方向更能改善 gate，可以跳出这条提示。"
    )


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
    operator_focus_text: str = "",
    operator_focus_path: str = "config/research_v2_operator_focus.md",
    history_package_path: str = "wiki/latest_history_package.md",
    failure_wiki_path: str = "wiki/failure_wiki.md",
    duplicate_watchlist_path: str = "wiki/duplicate_watchlist.md",
) -> str:
    side_bias_guidance = _side_bias_guidance(reference_metrics)
    side_bias_block = f"\n{side_bias_guidance}\n" if side_bias_guidance else ""
    champion_focus_hint = _champion_focus_hint(reference_metrics)
    champion_focus_block = f"{champion_focus_hint}\n" if champion_focus_hint else ""
    operator_focus_block = ""
    if operator_focus_text.strip():
        operator_focus_block = (
            "人工方向卡（软引导，不是硬限制，优先服从当前有效诊断）:\n"
            f"- 文件: `{operator_focus_path}`\n"
            f"{operator_focus_text.strip()}\n"
        )
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
- 围绕一个可证伪假设，先产出一个简洁 round brief，交给后续 edit worker 落码。
- 本轮目标是改变真实交易路径，不是只制造源码 diff；若后续落码后的 smoke 行为完全不变，会被系统按 `behavioral_noop` 拒收。
- 当前评分口径是 `{score_regime}`；只有在 `gate` 通过，且相对当前 champion 的有效 `promotion_delta > {promotion_min_delta:.2f}` 时，候选才有资格刷新当前主参考。
- `train` 看滚动窗口均值/中位数，`val` 看连续 holdout 的 `promotion_score`，`test` 是隐藏验收集。

当前 champion 参考晋级分：{previous_best_score:.2f}
{side_bias_block}
{champion_focus_block}
{operator_focus_block}
本轮本地只读记忆文件：
- 重复黑名单：`{duplicate_watchlist_path}`
- 失败 wiki：`{failure_wiki_path}`
- 历史摘要包：`{history_package_path}`
- 完整原始历史：`memory/raw/rounds/` 与 `memory/raw/full_history.jsonl`
{bootstrap_block}

本轮阅读顺序（必须执行）：
1. 先看“刷新条件与本轮目标”，确认本轮不是为了造 diff，而是为了改变真实交易路径。
2. 再看“当前诊断”，先定位当前 gate 主失败项、弱侧和 val 漏斗堵点。
3. 快速扫一遍 `{duplicate_watchlist_path}`；如果你的假设与其中某条在 cluster / changed_regions / target 上高度相似，先改写，不要再交同一份补丁。
4. 再看 `{failure_wiki_path}` 与 `{history_package_path}` 的前部摘要；相同失败核或 exact cut 只算一个已知坏盆地，不要逐条重读。若这些辅助文件暂不可用，直接改为核对当前源码继续改，不要停在 no-edit。
5. 基于上面四步只提出一个单一因果假设，并明确它预计会新增、删除或迁移哪类真实交易。
6. 形成假设后，必须回看一次 `{duplicate_watchlist_path}` 与 `{failure_wiki_path}` 做自检；若命中相同或高度相似的重复补丁/失败 cut，优先在本轮内改写假设再提交。
7. 若仍落在同方向簇或同 ordinary family，必须证明这次改的是不同 choke point、不同最终放行链，或不同的真实交易路径层级；`strategy-only` 也可以，但必须说清它改变了哪一层最终路由。

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
- 读不到 `{duplicate_watchlist_path}`、`{failure_wiki_path}` 或 `{history_package_path}` 不是合法 no-edit 理由；当前源码仍是硬事实源，必须继续改代码。
{complexity_headroom_block}

当前口径的 gate / 评分提醒：
- val 趋势段命中率 >= 35%
- val 趋势捕获分 >= 0.05
- train 与 val 分数落差 <= 0.30
- val 多头捕获 >= 0.00，val 空头捕获 >= 0.00
- val 会再切成 3 个连续时间分块：最差分块 >= -0.35，负分块最多 1 个
- 手续费拖累 <= 8%
- train+val 严重集中度过拟合会直接淘汰

本轮硬完成条件：
- 当前阶段不要直接编辑文件；只输出 round brief，供后续 worker 落码。
- round brief 输出要求：
{build_candidate_response_format_instructions()}
- `change_plan` 必须具体到你希望 worker 改哪条规则块、阈值或最终放行链。
- `novelty_proof` 必须写清这次为什么不属于 failure wiki 里已经失败过的旧 cut。
- 不允许把“未执行代码改动”“blocked”“no_edit”“no_change”这类占位回复当成完成。
- 如果辅助记忆缺失，就直接基于当前源码做单假设判断，不要停在解释阶段。
"""


def build_strategy_edit_worker_prompt(
    *,
    candidate_id: str,
    hypothesis: str,
    change_plan: str,
    change_tags: tuple[str, ...],
    expected_effects: tuple[str, ...],
    closest_failed_cluster: str,
    novelty_proof: str,
    current_complexity_headroom_text: str = "",
) -> str:
    complexity_block = (
        f"\n当前复杂度余量提醒：\n{current_complexity_headroom_text}\n"
        if current_complexity_headroom_text.strip()
        else "\n"
    )
    return f"""你现在负责把已经确定的 round brief 直接落到代码。

本轮 round brief：
- candidate_id: {candidate_id or "-"}
- hypothesis: {hypothesis or "-"}
- change_plan: {change_plan or "-"}
- change_tags: {", ".join(change_tags) or "-"}
- expected_effects: {"；".join(expected_effects) or "-"}
- closest_failed_cluster: {closest_failed_cluster or "-"}
- novelty_proof: {novelty_proof or "-"}

当前要求：
- 只修改 `src/strategy_macd_aggressive.py`。
- 先读取当前源码，再按上面的 brief 落一版真实代码改动。
- 优先改已经存在的命名规则块、阈值和最终放行链；不要为了造 diff 新写一套近似逻辑。
- 如果你判断 brief 指向的 choke point 根本不在当前代码路径上，应在同一主题内改成能真实触达交易路径的实现，但不要改写研究方向本身。
- 保持代码结构化，不要做无关重构、大面积格式化或复制已有因果链。
- 只要完成真实落码并保存文件，就回复 `EDIT_DONE`。不要输出解释、计划、JSON、markdown 或源码。
- 如果辅助记忆文件读不到，不是合法 no-edit 理由；直接以当前 `src/strategy_macd_aggressive.py` 为事实源落码。
{complexity_block}
当前阶段唯一完成条件：
{build_edit_completion_instructions()}
"""


def build_strategy_no_edit_repair_prompt(
    *,
    no_edit_attempt: int,
    error_message: str,
    last_response_text: str = "",
    task_summary: str = "",
) -> str:
    last_response_excerpt = " ".join(str(last_response_text or "").split())
    if len(last_response_excerpt) > 280:
        last_response_excerpt = last_response_excerpt[:280] + "..."
    last_response_block = (
        f"\n你上一条回复摘录（已被丢弃，仅供你自查）:\n{last_response_excerpt}\n"
        if last_response_excerpt
        else "\n"
    )
    task_summary_block = (
        f"\n本轮 round brief（重新提醒你的唯一任务）:\n{task_summary}\n"
        if task_summary.strip()
        else "\n"
    )
    return f"""你上一条回复已被主进程直接丢弃。

这是第 {no_edit_attempt} 次 no-edit 修复，不是新一轮研究。

硬失败原因：
- `src/strategy_macd_aggressive.py` 的文件 hash 与调用前完全相同。
- 这说明你刚才没有把改动真正落到源码。
- 解释、计划、标签、JSON 或摘要都不能算完成；只有文件本身发生真实变化才算完成。
{last_response_block}
{task_summary_block}
当前要求：
- 现在只做一件事：直接修改 `src/strategy_macd_aggressive.py`。
- 调用结束时该文件必须与当前基底不同。
- 完成后只回复 `EDIT_DONE`。
- 不要输出 JSON、markdown、解释、计划或源码。
- 如果这次仍然没有真实源码改动，本轮会直接失败；连续多轮失败会自动停掉研究器。

当前 no-edit 错误：
{error_message}
"""


def build_strategy_candidate_summary_prompt(
    *,
    diff_summary: list[str],
    changed_regions: tuple[str, ...],
) -> str:
    diff_text = "\n".join(f"- {line}" for line in diff_summary[:12]) or "- 无摘要"
    changed_regions_text = ", ".join(changed_regions) if changed_regions else "-"
    return f"""代码修改已完成。现在进入摘要阶段。

注意：
- 不要再修改任何文件。
- 不要继续编辑 `src/strategy_macd_aggressive.py`；主进程现在只需要你总结已经落地的改动。
- 如果你发现代码与原计划不一致，也不要继续改代码，直接按当前实际代码总结。

系统检测到的真实改动区域：
- {changed_regions_text}

当前 diff 摘要：
{diff_text}

输出要求：
{build_candidate_response_format_instructions()}
- 只描述当前已经落到代码里的改动，不要描述“本来想改但没改成”的内容。
- `change_plan` 与 `novelty_proof` 必须明确写出已经新增、删除或迁移了哪类实际交易。
- `closest_failed_cluster` 必须写最接近的旧失败方向簇。
- `core_factors` 没有就输出 `none`。
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

这是第 {repair_attempt} 次修复尝试。目标是：保持原研究方向基本不变，优先修复源码/运行错误，让代码先通过源码校验与 smoke test，再进入完整评估。

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
- 不要把源码贴回回复文本；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件
- 即使辅助记忆文件暂不可用，也必须继续基于当前源码修复，不允许返回 no-edit 占位结果

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
- 完成编辑后只回复 `EDIT_DONE`。
- 不要输出 JSON、markdown、解释、计划或源码。
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
    hot_cluster_text = "；".join(locked_clusters) if locked_clusters else "无"
    feedback_block = f"\n附加反馈（本次必须处理）:\n{feedback_note}\n" if feedback_note else "\n"
    return f"""你正在同一轮里重生候选方向，不是开始新一轮研究。

这是第 {regeneration_attempt} 次同轮重生。目标是：保持本轮要解决的策略短板大方向不变，但必须绕开刚被验证失败的近邻方向，改成一个可进入评估的新方向；如果上一版被 `behavioral_noop` 拒收，默认说明上一版局部因果链已失效，可以直接重写局部 hypothesis / change_plan。

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
- 近期高频失败/过热方向（软提示）: {hot_cluster_text}
{feedback_block}

工作区说明：
- 主进程已把工作区里的 `src/strategy_macd_aggressive.py` 重置为当前正确基底（当前主参考），避免你在已知坏版本上继续叠改
- 刚才被系统拒收的候选版本只作为反例参考，不再是这次改动的基底
- `wiki/duplicate_watchlist.md` 只列最近最容易重复提交的少量源码指纹；提交前先快速核对，避免把同一份补丁再交一次
- `wiki/last_rejected_snapshot.md` 记录了上一版为什么被判错；`wiki/last_rejected_candidate.py` 保留了上一版完整代码，都是只读参考
- 你必须从当前正确基底重新修改该文件，产出一个新的候选方向
- 不要把源码贴回回复文本；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件
- 不允许把“未读到 wiki/history”“环境阻塞”“no_edit”“未执行代码改动”当成合法重生结果；必须继续改出真实 diff

重生规则：
- 这不是代码修错；不要只做微小阈值近邻调整然后原样留在被拒簇。
- 优先切到不同方向簇。
- 若确实留在同簇，必须明确切换外层 choke point、最终放行链、目标侧或核心规则块中的至少一项；`strategy-only` 结构性改路由是允许的，但必须明确说明为什么这次会触达不同交易路径。
- 不要只换 tag、只换措辞、或只补一两条很像的条件。
- 若上一版是 `behavioral_noop`，不要沿用原 hypothesis / change_plan 只换表述；应默认把那条局部路径假设视为已被证伪，并改成新的可触达交易路径。
- 重生后的候选必须预计改变 smoke 窗口实际交易路径；如果上一版只是 helper / followthrough 变化但没有触发新交易，优先改 `strategy()` 的最终入场路径。
- 如果附加反馈显示 smoke 窗口的交易数、收益和信号统计完全没变，默认说明你上一版改动没有触达真实交易路径；这次必须优先改能改变最终信号集合或退出集合的规则块，而不是继续只拨不会触发的细阈值。
- 若你认为上一版里某个局部思路仍有价值，必须在当前正确基底上重新实现；不要默认沿用上一版残留代码。
- 在动手前先快速核对 `wiki/last_rejected_snapshot.md`，必要时再看 `wiki/last_rejected_candidate.py`；目标是理解“上一版为什么错”，不是继续在错题代码上补丁。
- 在提交重生版之前，先快速核对 `wiki/duplicate_watchlist.md`，再回看一次 `wiki/failure_wiki.md`；如果你这次仍命中相同或高度相似的重复补丁/失败 cut，优先继续改写，不要无意义原样交回。
- 对长侧优先检查 `long_outer_context_ok` 与 `long_final_veto_clear`；对空侧优先检查 `short_outer_context_ok` 与 `short_final_veto_clear`。若这些总闸门不动，新增 path 很可能仍是死分支。
- 不要把“新增一个 `xxx_path_ok`”误当成一定会新增交易；只有它真正穿过最终 veto / followthrough，smoke 行为才会改变。
- 若附加反馈显示 `outer_context` 或 `final_veto` 是主要堵点，允许直接做结构性删减轮：`remove_dead_gate`、`merge_veto`、`widen_outer_context`。
- 若附加反馈显示你上一版没有真实 diff，本轮第一优先级是产出真实源码改动，而不是解释为什么没改。
- 仍然只允许修改 `src/strategy_macd_aggressive.py` 可编辑区域。
- 不要引入网络、文件、随机数、外部依赖。
- 代码仍必须保持简洁、结构化、可读。

输出要求：
- 返回格式仍然使用同一组纯文本字段，不要 JSON。
- `novelty_proof` 必须明确说明这次相对刚才被拒方向，具体换了哪一个 choke point / 最终放行链 / 目标侧 / 关键规则块。
"""
