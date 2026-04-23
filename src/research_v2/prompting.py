#!/usr/bin/env python3
"""研究器 v2 的 prompt 与返回格式。"""
from __future__ import annotations

from typing import Any

from research_v2.journal import ORDINARY_REGION_FAMILIES
from research_v2.strategy_code import (
    REQUIRED_FUNCTIONS,
    REQUIRED_TOP_LEVEL_CONSTANTS,
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
    constant_symbols = "、".join(f"`{name}`" for name in REQUIRED_TOP_LEVEL_CONSTANTS)
    function_symbols = "、".join(f"`{function_name}()`" for function_name in REQUIRED_FUNCTIONS)
    return f"`PARAMS`、{constant_symbols}、{function_symbols}"


def _ordinary_family_text() -> str:
    return " / ".join(f"`{family_name}`" for family_name in ORDINARY_REGION_FAMILIES)


def _complexity_budget_text() -> str:
    return (
        "复杂度信息只做只读诊断，不是本轮的自动分流或自动拦截规则。\n"
        "- 如果某块明显偏胖，优先删旧、并旧、改旧，不要把复杂度换个名字搬家。\n"
        "- 新增条件前先检查当前源码里是否已经有近似逻辑；若有，优先直接改旧逻辑。"
    )


def _bootstrap_journal_excerpt(journal_summary: str, *, max_lines: int = 20, max_chars: int = 1800) -> str:
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
- `novelty_proof` 先写最近结构化失败证据否掉了什么，再写本轮为什么继续或转向，最后再补这次相对旧 cut 的不同点。
- `core_factors` 没有就写 `none`；有则写成 `name | thesis | current_signal || ...`。
- 不要输出 `edited_regions`；系统会按真实 diff、可编辑区域边界和源码签名自动判定改动区域。"""


def build_reviewer_response_format_instructions() -> str:
    return """- 只返回一个纯文本 reviewer 审稿结果，不要 JSON，不要 markdown，不要贴源码。
- 按以下字段顺序逐行输出，字段名保持英文小写并使用英文冒号：
  verdict:
  reviewer_summary:
  rejection_type:
  matched_evidence:
  must_change:
  why_not_new:
- `verdict` 只能写 `PASS` 或 `REVISE`。
- 如果 `verdict=PASS`，`rejection_type` / `matched_evidence` / `must_change` / `why_not_new` 统一写 `none`。
- 如果 `verdict=REVISE`，必须明确说明当前 draft 为什么仍落在旧失败近邻，以及 planner 下一版至少要换哪一层。
- reviewer 只能审稿，不能替 planner 发明新方向，也不要给出代码级修改清单。"""


def build_edit_completion_instructions() -> str:
    return """- 本阶段唯一完成条件：直接修改 `src/strategy_macd_aggressive.py`。
- 调用结束时该文件必须与当前基底不同；若文件 hash 未变化，本次回复会被主进程直接丢弃。
- 完成编辑后只回复 `EDIT_DONE`。
- 不要输出 hypothesis、change_plan、JSON、markdown 或源码；这些会在落码成功后由下一阶段单独收集。"""


def _text_only_output_contract() -> str:
    return "只返回约定字段的纯文本候选摘要，不要输出 JSON、markdown、解释或源码。"


def _single_strategy_file_scope_rule() -> str:
    return "除非当前提示明确允许，否则不要创建、修改或删除 `src/strategy_macd_aggressive.py` 之外的文件。"


def _missing_memory_is_not_no_edit_rule() -> str:
    return "若辅助记忆文件暂时不可用，也必须继续基于当前 `src/strategy_macd_aggressive.py` 做真实改动；禁止把“未读到文件”当成合法终止条件。"


# ==================== Prompt 组装 ====================


def build_strategy_agents_instructions(*, factor_change_mode: str = "default") -> str:
    complexity_budget_text = _complexity_budget_text()
    return f"""你是 BTC 永续合约激进趋势策略研究员。

项目目标：
- 用 OKX 数据研究一套高弹性趋势捕获策略；允许较大波动，但不能靠日期特判、路径硬编码或伪优化刷分。
- `15m` 是唯一事实源，`1h + 4h` 只是由 `15m` 聚合的确认层；突破/跌破除了成交量，也要结合方向流量代理与成交活跃度。
- 目标不是做平滑净值，而是更早跟上 BTC 的主要上涨/下跌，并在趋势失效时更快退出或反手。

角色规则：
- `planner` 使用持久 session，负责研究方向、失败原因记忆和本轮 draft round brief。
- `reviewer` 是短生命周期审稿 worker，只负责判定 planner 的 draft brief 当前是否值得尝试；它只能 `PASS` 或 `REVISE`，不能替 planner 发明新方向。
- `edit_worker` / `repair_worker` 是短生命周期 worker，只负责把已确定方向落到 `src/strategy_macd_aggressive.py`，或修技术错误；它们不需要重扫全量历史。

持久 session 规则（只对 `planner` 生效）：
- 这是一个跨多轮复用的研究 session。不要假设主进程会每轮重复灌完整历史；你需要主动利用本地只读记忆文件。
- 每轮开始优先快速扫一遍 `wiki/reviewer_summary_card.md`；它记录上一轮 reviewer 为什么放行或打回。
- 每轮开始优先快速扫一遍 `wiki/current_reference_denylist.md`；它只约束当前 active reference 下已经被反复证伪的调法模式，不是全局因子黑名单。
- 每轮开始优先快速扫一遍 `wiki/duplicate_watchlist.md`；它只列最近最容易重复提交的少量源码指纹。
- 再读 `wiki/failure_wiki.md` 的概览；新 session 首轮优先再读 `wiki/latest_history_package.md` 的摘要段。
- 先复盘，再方案：先判断最近结构化失败证据说明上一轮为什么错、错在交易路径哪一层，再决定本轮继续还是转向；不要一上来替当前方向辩护。
- 形成单一因果假设后，必须回看一次 `wiki/failure_wiki.md`；若命中相同或高度相似的失败 cut，优先在本轮内改写假设，不要无意义重走旧方向。
- `memory/raw/rounds/` 与 `memory/raw/full_history.jsonl` 保留未压缩原始历史；需要深挖时去看，但优先先读 wiki 摘要与失败聚合。
- 若 `wiki/` 或 `memory/` 里的辅助记忆暂时不可用，这不是本轮 `no_edit` 的合法理由；此时应退化为直接读取并修改 `src/strategy_macd_aggressive.py`。

工作区文件职责：
- `src/strategy_macd_aggressive.py`：唯一允许修改的策略文件。
- `src/backtest_macd_aggressive.py`：回测、成交路径与指标口径定义，只读参考。
- `config/research_v2_operator_focus.md`：人工方向卡，只是软引导，不是系统硬限制。
- `wiki/reviewer_summary_card.md`：上一轮 reviewer 审稿卡；若它明确打回某条近邻方向，planner 下一版必须先吸收这张卡里的打回理由。
- `wiki/current_reference_denylist.md`：当前 active reference 作用域下的调法 denylist；若命中 `HARD_BAN`，默认转向；若命中 `PROOF_REQUIRED`，必须先证明这次换了不同 choke point、最终放行链或真实交易路径层级。
- `wiki/duplicate_watchlist.md`：最近高频重复源码黑名单摘要；先扫它，避免把同一份补丁再交一次。
- `wiki/failure_wiki.md`：当前评分口径下的失败 wiki；先看它，避免重走已知坏 cut。
- `wiki/latest_history_package.md`：当前 stage 历史摘要包；先看前部执行摘要与失败核，再决定是否下钻表格。
- `wiki/last_rejected_snapshot.md`：最近一次被系统判错的候选摘要、失败原因与 diff 提示；重生轮优先看它。
- `wiki/last_rejected_candidate.py`：最近一次被系统判错的完整候选代码，只读反例参考；不要把它当成本轮改动基底。
- `memory/`：研究记忆归档，只读参考；其中 `raw/` 保留原始历史，`summaries/` 保留压缩摘要。
- `data/`：OKX K 线 / funding / 成交流量代理等数据，只读参考。

工作方式：
- 先想再写，先看历史再下手。
- 不要 hard code，不要堆屎。
- `planner` 先出 draft brief，再交给 `reviewer` 审稿；若 reviewer 打回，本轮必须先吸收打回理由，再重写 draft。
- 先阅读并直接修改 `src/strategy_macd_aggressive.py`，必要时再看回测实现或数据文件，不要靠猜测写策略。
- 最近结构化失败证据优先级高于 `weak side` 或 champion 缺陷提示；先复盘失败点，再决定是否继续同方向。
- 每轮只验证一个可证伪假设；改动要能映射到真实交易路径变化，而不是只制造源码 diff。
- 优先保持代码结构化、规则块命名清晰、阈值集中、因果链可解释。
- 默认先做删减、合并、替换，再考虑新增条件；如果一个新条件只覆盖很窄的历史片段，优先删旧条件或改旧阈值，不要继续叠分叉。
- 不要把同一侧 path 拆成多个近似微变体；一个 path 没有明显新增交易路径时，应视为失败假设，而不是继续微调同一区间。
- 允许只改 `strategy()` / `PARAMS` / 少量新 helper 做结构化重排，但前提是你能明确说明这会改变最终信号集合、退出集合或真实交易路径；否则不要为了造 diff 去动它们。
- 明确要求：不要“堆屎”。很多你想到的过滤、例外、path 或 veto，当前策略里往往已经以别的名字存在；新增前先检查现有规则块、阈值和最终放行链是否已经表达了同一因果。
- 若现有脚本里已经有近似逻辑，不要换个名字再写一份重复条件；优先删旧、并旧、改旧，禁止把同一因果链在不同 helper / path / veto 里重复实现。
- 明确允许结构性删减轮：`remove_dead_gate`、`merge_veto`、`widen_outer_context` 都是合法 change_tags；这类轮次的目标是减少死分支、提高 reachability。
{complexity_budget_text}

输出与协作规则：
- `planner` 只返回纯文本字段摘要，不要 JSON，不要解释，不要 markdown，不要贴源码。
- `reviewer` 只返回 `PASS/REVISE` 审稿卡；它只能判当前 draft 是否值得继续，不能改写方向本身。
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

你在一个持久研究 session 中工作；当前用户提示只补充本轮目标、诊断和失败反馈。
如果你当前是 `planner`，默认先复盘最近结构化失败证据，再决定 round brief；不要先替当前方向辩护。
{_text_only_output_contract()}
{_single_strategy_file_scope_rule()}
{_missing_memory_is_not_no_edit_rule()}
"""


def build_strategy_worker_system_prompt(
    *,
    factor_change_mode: str = "default",
    worker_kind: str = "edit_worker",
) -> str:
    return f"""遵守当前工作区本地 `AGENTS.md`，它是当前工作区的长期规则。

你当前是短生命周期 `{worker_kind}`，不是持久研究 planner。
- 不要重新做全量历史研究，不要重新定义本轮方向。
- 只根据当前提示里的 round brief 或 repair 指令，直接修改 `src/strategy_macd_aggressive.py`。
- {_single_strategy_file_scope_rule()}
- 完成编辑后只回复 `EDIT_DONE`。
- 禁止输出 JSON、markdown、解释、计划或源码。
- {_missing_memory_is_not_no_edit_rule()}
"""


def build_strategy_summary_worker_system_prompt(*, factor_change_mode: str = "default") -> str:
    return f"""遵守当前工作区本地 `AGENTS.md`，它是当前工作区的长期规则。

你当前是短生命周期 `summary_worker`，不是持久研究 planner，也不是 edit worker。
- 不要修改任何文件，不要开始新一轮研究。
- 只根据当前提示里的真实 diff、真实改动区域和当前 `src/strategy_macd_aggressive.py`，回写最终候选摘要。
- 输出仍然使用纯文本字段契约，不要输出 JSON、markdown、解释或源码。
- 若原 round brief 和最终代码不一致，以最终代码为准修正文案。
"""


def build_strategy_reviewer_system_prompt(*, factor_change_mode: str = "default") -> str:
    return f"""遵守当前工作区本地 `AGENTS.md`，它是当前工作区的长期规则。

你当前是短生命周期 `reviewer`，不是持久研究 planner，也不是 edit worker。
- 你的职责只有一个：审稿 planner 刚写出的 draft round brief，判断它当前是否值得继续进入落码。
- 你不能替 planner 发明新方向，不能改写 hypothesis，也不能给出代码级改法。
- 你只能做两种结论：`PASS` 或 `REVISE`。
- 若 `REVISE`，必须明确说明它为什么仍落在旧失败近邻，并指出 planner 下一版至少要换哪一层；不要写成抽象空话。
- 若 `PASS`，说明这版为什么已经避开最近重复失败核即可。
- 不要修改任何文件，不要开始新一轮研究，不要输出 JSON、markdown、解释或源码。
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
            "- 本轮默认优先考虑 `long` 或 `mixed` 假设：优先修多头，但不要在进入思考前就预设根因一定在 `outer_context`、`final_veto` 或某个固定 helper。\n"
            "- 这不是禁止修改空头；只有当某个 mixed 假设能在基本不破坏空头的前提下补多头，或空头出现新的硬伤时，才值得继续动 short。\n"
            "- 若选择 mixed 方案，`expected_effects` 的第一优先级应是改善多头捕获/命中率，第二优先级才是维持空头不明显恶化。\n"
            "- 默认先做“目标导向”判断：多头问题更像卡在到来、陪跑、过早出清、还是最终路由错配；先定目标，再定具体 choke point。"
        )

    if bull < 0.05 <= bear:
        return (
            "多空强化偏置（软引导，不是硬限制）:\n"
            f"- 当前主参考 val 多头/空头捕获 = {bull:.2f} / {bear:.2f}。\n"
            "- 当前更值得优先探索的是多头侧，因为空头至少已经过线，而多头仍接近或低于门槛。\n"
            "- 默认优先做能补多头捕获的 `long` 或 `mixed` 假设，但不要为了补多头而粗暴破坏空头主框架。\n"
            "- 先判断多头是卡在 arrival / escort / turn / routing 哪一段，再决定改哪一层规则；不要把“多头弱”直接翻译成“继续 widen outer_context”。"
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
        + "。优先尝试能补多头延续持有、提高到来质量或减少过早出清的假设；若其他方向更能改善 gate，可以跳出这条提示。"
    )


def build_strategy_research_prompt(
    *,
    evaluation_summary: str,
    journal_summary: str,
    previous_best_score: float,
    reference_metrics: dict[str, Any] | None = None,
    benchmark_label: str = "champion",
    current_base_role: str = "champion",
    score_regime: str = "trend_capture_v6",
    promotion_min_delta: float = 0.02,
    factor_change_mode: str = "default",
    factor_mode_status_text: str = "",
    iteration_lane: str = "research",
    iteration_lane_status_text: str = "",
    current_complexity_headroom_text: str = "",
    session_mode: str = "resume",
    operator_focus_text: str = "",
    operator_focus_path: str = "config/research_v2_operator_focus.md",
    reviewer_summary_text: str = "",
    reviewer_summary_path: str = "wiki/reviewer_summary_card.md",
    current_reference_denylist_path: str = "wiki/current_reference_denylist.md",
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
    iteration_lane_block = (
        f"本轮任务类型：`{iteration_lane}`\n- {iteration_lane_status_text.strip()}\n"
        if iteration_lane_status_text.strip()
        else ""
    )
    complexity_headroom_block = (
        f"\n{current_complexity_headroom_text}\n"
        if current_complexity_headroom_text.strip()
        else "\n"
    )
    bootstrap_excerpt = _bootstrap_journal_excerpt(journal_summary, max_lines=16, max_chars=1400)
    bootstrap_block = ""
    if session_mode == "bootstrap":
        bootstrap_block = (
            "\n新 session 启动补充（完整版本见 "
            f"`{history_package_path}`）：\n"
            f"{bootstrap_excerpt or '请先读取本地历史包摘要。'}\n"
        )
    reviewer_summary_excerpt = _bootstrap_journal_excerpt(
        reviewer_summary_text,
        max_lines=8,
        max_chars=900,
    )
    reviewer_summary_block = (
        "上一轮 reviewer 总结卡（高优先级，先看）:\n"
        f"- 文件: `{reviewer_summary_path}`\n"
        f"{reviewer_summary_excerpt or '- 暂无 reviewer 卡；本轮按当前诊断自行判断。'}\n"
    )
    session_label = "stage_bootstrap" if session_mode == "bootstrap" else "stage_resume"
    return f"""当前回合任务：
- session 状态：`{session_label}`
- 先复盘最近一条最强结构化失败证据，再决定本轮继续还是转向；不要先替当前方向辩护。
- 围绕一个可证伪假设，先产出一个简洁 round brief，交给后续 edit worker 落码。
- 本轮目标是改变真实交易路径，不是只制造源码 diff；若后续落码后的 smoke 行为完全不变，会被系统按 `behavioral_noop` 拒收。
- 当前评分口径是 `{score_regime}`；只有在 `gate` 通过，且相对当前 {benchmark_label} 的有效 `promotion_delta > {promotion_min_delta:.2f}` 时，候选才有资格刷新当前 active reference。
- `train` 看滚动窗口均值/中位数，`val` 看连续 holdout 的 `promotion_score`，`test` 只做只读观察，不参与晋升。

当前工作基底角色：`{current_base_role}`
当前 {benchmark_label} 参考晋级分：{previous_best_score:.2f}
{side_bias_block}
{champion_focus_block}
{operator_focus_block}
{reviewer_summary_block}
本轮本地只读记忆文件：
- reviewer 总结卡：`{reviewer_summary_path}`
- 当前 reference denylist：`{current_reference_denylist_path}`
- 重复黑名单：`{duplicate_watchlist_path}`
- 失败 wiki：`{failure_wiki_path}`
- 历史摘要包：`{history_package_path}`
- 完整原始历史：`memory/raw/rounds/` 与 `memory/raw/full_history.jsonl`
{bootstrap_block}

本轮阅读顺序（必须执行）：
1. 先看“刷新条件与本轮目标”，确认本轮不是为了造 diff，而是为了改变真实交易路径。
2. 再看上一轮 `reviewer` 总结卡；如果它明确把上一条近邻方向打回，本轮 draft brief 必须先吸收这张卡里的打回理由，再决定新的方向。
3. 快速扫一遍 `{current_reference_denylist_path}`；若命中 `HARD_BAN`，默认转向；若命中 `PROOF_REQUIRED`，先明确写出这次换了哪个 choke point、最终放行链或真实交易路径层级。
4. 再看“当前诊断”，先定位当前 gate 主失败项、弱侧和 val 漏斗堵点。
5. 快速扫一遍 `{duplicate_watchlist_path}`；如果你的假设与其中某条在 cluster / changed_regions / target 上高度相似，先改写，不要再交同一份补丁。
6. 再看 `{failure_wiki_path}` 与 `{history_package_path}` 的前部摘要；相同失败核或 exact cut 只算一个已知坏盆地，不要逐条重读。若这些辅助文件暂不可用，直接改为核对当前源码继续改，不要停在 no-edit。
7. 先用最近一条结构化失败证据做复盘：上一轮为什么失败、失败更像发生在 `outer_context / path / final_veto / routing / followthrough / exit / unknown` 的哪一层。
8. 再决定本轮继续还是转向；如果你写不清这次相对上一轮到底换了哪个 choke point / 最终放行链 / 目标侧，默认转向，而不是继续在原方向上自辩。
9. 最后才提出一个单一因果假设，并明确它预计会新增、删除或迁移哪类真实交易。
10. 形成假设后，必须回看一次 `{current_reference_denylist_path}`、`{duplicate_watchlist_path}` 与 `{failure_wiki_path}` 做自检；若命中相同或高度相似的重复补丁/失败 cut，优先在本轮内改写假设再提交。
11. 若仍落在同方向簇或同 ordinary family，必须证明这次改的是不同 choke point、不同最终放行链，或不同的真实交易路径层级；`strategy-only` 也可以，但必须说清它改变了哪一层最终路由。
{iteration_lane_block}

{evaluation_summary}

本轮执行框架：
- 先看最近结构化失败反馈与当前诊断，先判断上一轮假设为什么没突破；再决定继续同方向还是转向。
- 再判断最大短板是在多头、空头、到来、陪跑还是掉头；最后才提出单一因果假设。
- 先确定“目标侧 + 目标环节”：是补 long arrival、long escort、long turn、short stability，还是修最终 routing；先定目标，再决定具体改哪个 choke point。
- 如果目标是补早段 long / short，可以检查外层总闸门：长侧先看 `long_outer_context_ok`，空侧先看 `short_outer_context_ok`；但不要把这句话理解成“默认继续 widen outer_context”。
- 新增 path 不等于新增交易；必须继续检查最终合流与否决链。长侧重点看 `long_signal_path_ok -> long_final_veto_clear -> _trend_followthrough_long()`，空侧重点看 `breakdown_ready -> short_final_veto_clear -> _trend_followthrough_short()`。
- 如果主要改 `_trend_followthrough_ok()`、`_trend_quality_ok()` 或 `_flow_confirmation_ok()`，必须确认现有 `strategy()` 路径会触达；否则优先改 `strategy()`。
- 若最近连续出现 `behavioral_noop` 或结果盆地重复，本轮默认必须放大步长：优先切不同方向簇，或切不同 choke point / 最终放行链；不要只换措辞、tag 或近邻阈值。
- 若最近失败已经明确说明某条局部假设没有触达真实行为层，默认先把那条局部假设视为已被证伪；只有你能明确指出新的 choke point 时，才值得继续留在同方向。
- 若漏斗诊断显示一侧长期 0 交易、outer_context 几乎全死，或 path 能过但 final_veto 基本全死，可以考虑结构性删减轮：`remove_dead_gate` / `merge_veto` / `widen_outer_context`；但只有在它仍是当前最可能改变真实交易路径的根因时才这样做。
- 如果复杂度诊断显示某块已经很紧，优先先删旧、并旧、改旧，再考虑新增判断。
- 若你的主要假设是最终路由或最终 veto 错配，允许只改 `strategy()` 或少量结构化 helper；但必须在 `change_plan` 里写清楚它会新增、删除或迁移哪类真实交易。
- 如果当前 stage 明确显示某个簇或某种补法已经过热，不要只因为“弱侧还是 long”就继续留在同一路线；优先切到能修 long 但机制不同的方案。
- 读不到 `{current_reference_denylist_path}`、`{duplicate_watchlist_path}`、`{failure_wiki_path}` 或 `{history_package_path}` 不是合法 no-edit 理由；当前源码仍是硬事实源，必须继续改代码。
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
- 这份 round brief 只是 `draft`；主进程还会交给 `reviewer` 审稿。若 reviewer 打回，本轮必须先吸收其反馈再重写，不允许绕过审稿直接进入落码。
- `change_plan` 必须具体到你希望 worker 改哪条规则块、阈值或最终放行链。
- `novelty_proof` 不是自我辩护。先写上一版被什么结构化证据否掉，再写本轮为什么继续或转向，最后再写这次为什么不属于 failure wiki 里已经失败过的旧 cut。
- 不允许把“未执行代码改动”“blocked”“no_edit”“no_change”这类占位回复当成完成。
- 如果辅助记忆缺失，就直接基于当前源码做单假设判断，不要停在解释阶段。
"""


def build_strategy_reviewer_prompt(
    *,
    evaluation_summary: str,
    journal_summary: str,
    round_brief_text: str,
    reviewer_summary_path: str = "wiki/reviewer_summary_card.md",
    current_reference_denylist_path: str = "wiki/current_reference_denylist.md",
    history_package_path: str = "wiki/latest_history_package.md",
    failure_wiki_path: str = "wiki/failure_wiki.md",
    duplicate_watchlist_path: str = "wiki/duplicate_watchlist.md",
    last_rejected_snapshot_path: str = "wiki/last_rejected_snapshot.md",
) -> str:
    evidence_excerpt = _bootstrap_journal_excerpt(
        journal_summary,
        max_lines=14,
        max_chars=1600,
    )
    evaluation_excerpt = _bootstrap_journal_excerpt(
        evaluation_summary,
        max_lines=8,
        max_chars=900,
    )
    return f"""当前任务：你要审稿 planner 刚写出的 draft round brief，判断它当前是否值得进入落码。

你的职责边界：
- 你不是 planner，不要替它发明新方向。
- 你不是 edit worker，不要提出代码级修改步骤。
- 你只能做两种结论：`PASS` 或 `REVISE`。
- 若 `REVISE`，重点说清“为什么当前 draft 仍是旧失败近邻”以及“下一版至少要换哪一层”；不要写成泛泛而谈的建议。

当前 draft round brief：
{round_brief_text}

高信号证据包（先看这些，再决定）：
- 当前诊断摘录：
{evaluation_excerpt or '- 无'}

- 当前 stage 摘录：
{evidence_excerpt or '- 无'}

本地只读证据文件：
- 历史摘要包：`{history_package_path}`
- 当前 reference denylist：`{current_reference_denylist_path}`
- 失败 wiki：`{failure_wiki_path}`
- 重复黑名单：`{duplicate_watchlist_path}`
- 最近一次被系统判错的快照：`{last_rejected_snapshot_path}`
- 上一轮 reviewer 总结卡：`{reviewer_summary_path}`

审稿要求：
1. 先判断这份 draft 是不是仍落在最近重复失败核、过热方向簇或 duplicate watchlist 的近邻。
2. 若它命中 `{current_reference_denylist_path}` 里的 `HARD_BAN`，且 draft 没有明确证明目标侧、最终放行链和真实交易路径层级都不同，应判 `REVISE`。
3. 若它命中 `PROOF_REQUIRED`，但 `novelty_proof` 仍说不清新的 choke point、最终放行链或真实交易路径层级，也应判 `REVISE`。
4. 如果它仍是近邻，但只是换了措辞、tag 或局部阈值，应判 `REVISE`。
5. 如果它虽然仍以 `long` 为主目标，但已经换了机制层、关键 choke point 或真实交易路径层级，可以判 `PASS`。
6. `REVISE` 时不要替 planner 写新方案；只指出必须改变的层级，例如 `机制层`、`最终放行链`、`changed_regions`、`目标侧` 或 `真实交易路径层级`。
7. 如果你判断它属于“有行为影响但同盆地”的饱和近邻，应明确写出来；这类情况不等于完全无效，但也不应直接进入落码。
8. 若证据不足，优先回看本地只读证据文件；不要因为 draft 自己写了 `novelty_proof` 就直接放行。

输出要求：
{build_reviewer_response_format_instructions()}
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
    iteration_lane: str = "research",
    iteration_lane_status_text: str = "",
    current_complexity_headroom_text: str = "",
) -> str:
    iteration_lane_block = (
        f"\n本轮任务类型：`{iteration_lane}`\n- {iteration_lane_status_text.strip()}\n"
        if iteration_lane_status_text.strip()
        else "\n"
    )
    complexity_block = (
        f"\n当前复杂度诊断（只读）：\n{current_complexity_headroom_text}\n"
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
{iteration_lane_block}
{complexity_block}
当前阶段唯一完成条件：
{build_edit_completion_instructions()}
"""


def build_strategy_candidate_summary_prompt(
    *,
    candidate_id: str,
    hypothesis: str,
    change_plan: str,
    change_tags: tuple[str, ...],
    expected_effects: tuple[str, ...],
    closest_failed_cluster: str,
    novelty_proof: str,
    edited_regions: tuple[str, ...],
    region_families: tuple[str, ...],
    diff_summary: tuple[str, ...],
) -> str:
    diff_text = "\n".join(diff_summary) if diff_summary else "- 无"
    return f"""你现在负责根据最终落地代码回写候选元信息，不是开始新一轮研究，也不是继续改代码。

原始 round brief：
- candidate_id: {candidate_id or "-"}
- hypothesis: {hypothesis or "-"}
- change_plan: {change_plan or "-"}
- change_tags: {", ".join(change_tags) or "-"}
- expected_effects: {"；".join(expected_effects) or "-"}
- closest_failed_cluster: {closest_failed_cluster or "-"}
- novelty_proof: {novelty_proof or "-"}

系统检测到的最终真实改动：
- edited_regions: {", ".join(edited_regions) or "-"}
- ordinary/特殊 family: {", ".join(region_families) or "-"}

当前最终代码 diff 摘要：
{diff_text}

回写规则：
- 以当前工作区里的最终 `src/strategy_macd_aggressive.py` 和上面的真实 diff 为准。
- 如果原 round brief 与最终代码一致，尽量保持 `candidate_id`、`change_tags`、`closest_failed_cluster` 稳定。
- 如果 worker / repair 实际落码偏离了原 brief，必须修正 `hypothesis`、`change_plan`、`expected_effects`、`novelty_proof`，不要硬沿用旧表述。
- `change_tags` 应反映最终代码真正修改的机制；不要保留已经没有落到最终代码里的旧标签。
- 不要输出 `edited_regions`；系统会继续以真实 diff 为准。
- 不要把“原本想改什么”写进结果，只描述最终已经落到代码里的东西。

输出要求：
{build_candidate_response_format_instructions()}
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


def build_strategy_round_brief_repair_prompt(
    *,
    retry_attempt: int,
    invalid_reason: str,
    missing_fields: tuple[str, ...],
    raw_response_excerpt: str,
) -> str:
    missing_text = ", ".join(missing_fields) if missing_fields else "-"
    excerpt_text = raw_response_excerpt or "-"
    return f"""上一条 planner round brief 无效，这是第 {retry_attempt} 次同轮补正。

任务没有改变：继续当前这一轮的研究方向，但必须把输出修正成合法 round brief。

本次无效原因：
- {invalid_reason}
- 缺失或无效的核心字段: {missing_text}
- 上一条回复摘录: {excerpt_text}

补正规则：
- 不要输出随笔、自然段解释、JSON 或 markdown。
- 必须输出完整字段头，并确保 `hypothesis`、`change_plan`、`novelty_proof`、`change_tags` 非空。
- `candidate_id` 可以保留原值或重写；`closest_failed_cluster` 仍可留空让系统回填。
- `expected_effects` 与 `core_factors` 可以为空，但如果填写，必须和本轮方向一致。
- 不要把“未读到文件”“blocked”“no_edit”“未执行代码改动”当成 round brief 内容。

输出要求：
{build_candidate_response_format_instructions()}
"""


def build_strategy_reviewer_repair_prompt(
    *,
    retry_attempt: int,
    invalid_reason: str,
    raw_response_excerpt: str,
) -> str:
    excerpt_text = raw_response_excerpt or "-"
    return f"""上一条 reviewer 审稿结果无效，这是第 {retry_attempt} 次同轮补正。

你的任务没有改变：继续审稿当前 planner draft brief，但必须把输出修正成合法 reviewer 结果。

本次无效原因：
- {invalid_reason}
- 上一条回复摘录: {excerpt_text}

补正规则：
- 不要输出随笔、自然段解释、JSON 或 markdown。
- `verdict` 只能写 `PASS` 或 `REVISE`。
- `reviewer_summary` 必须非空。
- 若 `verdict=PASS`，其余字段统一写 `none`。
- 若 `verdict=REVISE`，必须把 `rejection_type`、`matched_evidence`、`must_change`、`why_not_new` 写完整。
- reviewer 只能审稿，不要替 planner 生成新方向。

输出要求：
{build_reviewer_response_format_instructions()}
"""


def build_strategy_reviewer_revise_prompt(
    *,
    round_brief_text: str,
    reviewer_verdict: str,
    reviewer_summary: str,
    rejection_type: str,
    matched_evidence: str,
    must_change: str,
    why_not_new: str,
) -> str:
    return f"""你上一条 draft round brief 没有通过 reviewer 审稿，这不是开始新一轮研究。

当前目标不变：仍然是围绕本轮主要短板产出一个可执行 draft brief。
但 reviewer 已经判定你刚才那版当前不值得进入落码，所以你必须先吸收 reviewer 的打回信息，再重写 draft；不要原样续写，也不要替上一版辩护。

上一版 draft round brief：
{round_brief_text}

reviewer 审稿结果：
- verdict: {reviewer_verdict}
- reviewer_summary: {reviewer_summary}
- rejection_type: {rejection_type}
- matched_evidence: {matched_evidence}
- must_change: {must_change}
- why_not_new: {why_not_new}

重写规则：
- reviewer 不负责替你发明新方向；新 draft 仍由你自己提出。
- 但 reviewer 已经明确指出上一版为什么仍是旧失败近邻，下一版必须真实吸收这些信息。
- 如果上一版被判为 `saturated_same_basin` 或近邻重复，下一版至少要换掉 reviewer 指定的层级；不要只换措辞、tag、局部阈值或同义 gate。
- 如果你仍想保持 `long` 作为主目标，可以继续保持；但不能继续停留在 reviewer 明确打回的同一条 `long` 子路线。
- 输出仍然是同一份 draft round brief，供 reviewer 重新审稿；不要输出解释、JSON 或 markdown。

输出要求：
{build_candidate_response_format_instructions()}
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
- 若报错明确指向某块代码过胖或结构失控，优先删旧条件或合并旧分支，再继续保留原研究方向。
- 不要把复杂度从一个函数搬到同 family 的别的 helper；这类“搬家”通常只是在换名字，没有解决问题。
- 允许少量新 helper/常量做结构化抽离，但它们必须服务于删旧、并旧和提高清晰度，不能借机复制旧因果链。
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
先根据 block 原因和附加反馈复盘上一版为什么失败，再决定继续同方向还是转向；不要先替上一版辩护。

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
- `wiki/reviewer_summary_card.md` 记录了上一轮 reviewer 为什么放行或打回；先看它，再看更细的失败快照
- `wiki/duplicate_watchlist.md` 只列最近最容易重复提交的少量源码指纹；提交前先快速核对，避免把同一份补丁再交一次
- `wiki/last_rejected_snapshot.md` 记录了上一版为什么被判错；`wiki/last_rejected_candidate.py` 保留了上一版完整代码，都是只读参考
- 你必须从当前正确基底重新修改该文件，产出一个新的候选方向
- 不要把源码贴回回复文本；主进程会直接读取你改好的文件
- 除 `src/strategy_macd_aggressive.py` 外，不要创建、修改或删除其他文件
- 不允许把“未读到 wiki/history”“环境阻塞”“no_edit”“未执行代码改动”当成合法重生结果；必须继续改出真实 diff

重生规则：
- 这不是代码修错；不要只做微小阈值近邻调整然后原样留在被拒簇。
- 先复盘上一版错在目标层、choke point 还是步长；先决定继续还是转向，再写新方案。
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
- 在动手前先快速核对 `wiki/reviewer_summary_card.md`；若 reviewer 已明确打回某条近邻方向，本轮不要原样续写。
- 仍然只允许修改 `src/strategy_macd_aggressive.py` 可编辑区域。
- 不要引入网络、文件、随机数、外部依赖。
- 代码仍必须保持简洁、结构化、可读。

输出要求：
- 返回格式仍然使用同一组纯文本字段，不要 JSON。
- `novelty_proof` 先写上一版被什么证据否掉，再写本轮为什么继续或转向，最后再明确说明这次相对刚才被拒方向，具体换了哪一个 choke point / 最终放行链 / 目标侧 / 关键规则块。
"""
