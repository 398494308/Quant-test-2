# MACD Aggressive Current State

这份文档只描述当前这条主线现在怎么跑，不再保留 `test1 / test2` 或旧版双状态设计的历史说明。

## 当前快照

运行时只有一个 active reference。最新快照在：

- [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)
- [backups/strategy_macd_aggressive_v2_best.py](../backups/strategy_macd_aggressive_v2_best.py)
- [state/research_macd_aggressive_v2_best.json](../state/research_macd_aggressive_v2_best.json)

历史 `champion` 快照现在会额外保存在：

- `backups/champion_history/<timestamp>_i<iteration>_<candidate_id>_<codehash>/`

每个快照目录至少会带：

- `strategy_macd_aggressive.py`
- `metadata.json`
- 若当轮图表生成成功，还会带 `selection.png` 与 `validation.png`

当前关键指标：

| 项目 | 数值 |
| --- | --- |
| 当前角色 | champion |
| 当前 champion hash | e66af6f30f0b4e0610d3ccb68a67545c8ebea67cf6853b3dc2ba961a4d9eefa9 |
| 当前 reference stage 起点轮次 | 3 |
| gate | 通过 |
| score regime（保存态 / 仓库默认） | trend_capture_v12_robustness_plateau_penalty / trend_capture_v12_robustness_plateau_penalty |
| quality_score（train连续趋势分） | 0.8144 |
| promotion_score（保存态） | 0.7798 |
| capture_score / timed_return_score / turn_protection_score | 0.6697 / 2.3792 / 0.6744 |
| drawdown_risk_score / drawdown_penalty_score（保存态） | 1.1593 / 0.2319 |
| train/val连续抓取分 | 0.8144 / 0.5250 |
| train+val期间收益 | 1263.42% |
| val期间收益 | 285.39% |
| train+val多/空捕获 | 0.32 / 0.28 |
| Sharpe(train+val / val) | 1.42 / 1.89 |
| test收益 / Sharpe | -2.64% / 0.33 |
| train+val交易数量 | 256 |

当前轮次留档除了 `journal` 与 `memory/raw` 外，还额外维护一条最小可复现链路：

- `backups/research_v2_round_artifacts/sources/`：按 `code_hash` 去重保存策略源码
- `backups/research_v2_round_artifacts/rounds/`：每轮一个目录，保留策略快照、关键评分、`test` 关键指标、`test` 异步状态，以及窗口/评分配置和数据/引擎指纹
- 新 champion 轮次会在这份最小归档里额外引用 `champion_history` 和图表路径；`test` 结果只做留档，不会回灌给研究 prompt

说明：

- 上表按当前 [state/research_macd_aggressive_v2_best.json](../state/research_macd_aggressive_v2_best.json) 的最近保存态整理；若你刚切到 `v12` 但还没重启研究器，`promotion_score` 与 `robustness_penalty_score` 会在下一次启动时按新口径重算。
- 仓库默认评分已经切到 `trend_capture_v12_robustness_plateau_penalty`；研究器启动时会先从已保存 champion 重算新口径，再继续后续轮次。
- 新口径下 `drawdown_risk_score` 仍是固定窗口 `Ulcer` 风格风险分；`promotion_score` 在分段回撤惩罚之外，又额外接了一层轻量鲁棒性软惩罚。
- 当前人工方向已经从“继续补多头收益”切到“保护已有收益、减少利润回吐和深回撤”；长期软引导在 [config/research_v2_operator_focus.md](../config/research_v2_operator_focus.md)，当前 champion 人工观察卡在 [config/research_v2_champion_review.md](../config/research_v2_champion_review.md) 中已写回，并且只对当前 hash 生效。
- `state/research_macd_aggressive_v2_best.json` 里如果还带旧字段，例如 `working_base`，那只是历史兼容读取入口；新状态写回只使用单一 active reference 语义。
- 若只切换 `score_regime` 后重启，研究器现在会优先从 `backups/strategy_macd_aggressive_v2_best.py` 载入已保存 champion，并按新评分口径重算；不会再误用工作区里的当前候选文件做启动基线。
- 当前运行状态以 [state/research_macd_aggressive_v2_heartbeat.json](../state/research_macd_aggressive_v2_heartbeat.json) 为准；本文更新时 heartbeat 显示 `iteration 5`、phase=`model_regenerate_edit_worker`，仍在围绕这版 champion 做后续重生尝试。
- `real-money-test/` 这条执行壳子现在默认转为 `OKX Demo Trading`：策略必须先冻结为固定副本，`demo` 只认 `OKX_DEMO_*` 凭证，旧 `dry-run` 代码保留但不再默认使用，播报也切到 `demo` 卡口径。
- 如果你想把 `demo` 账户里的更大余额压到固定测试规模，当前壳子支持通过 `OKX_DEMO_AVAILABLE_CAPITAL` 给 `freqtrade` 注入单 bot 资金上限；例如 `1000` 表示只按 `1000 USDT` 规模运行。

## 数据与窗口

当前默认数据源已经统一为 `OKX`。

研究与评估窗口：

- `train`：`2023-07-01` 到 `2024-12-31`
- `val`：`2025-01-01` 到 `2025-12-31`
- `test`：`2026-01-01` 到 `2026-04-20`

当前默认配置：

- `train` 滚动窗口长度：`28` 天
- 滚动步长：`21` 天
- `smoke` 窗口数：`5`
- 主循环等待：`10s`
- `test`：新 champion 同步运行；已完成完整评估但未保留的候选也会后台异步运行，只保留关键指标，不生成图片，也不参与晋升
- provider 恢复等待：`90s`

## 评分与晋升

原始单段分数：

`period_score = 0.70 * trend_capture_score + 0.30 * return_score`

研究器主要看五类分：

- `quality_score`
  `train` 连续路径上的趋势抓取分
- `capture_score`
  `train/val` 连续趋势抓取分按 `5:5` 平均后的主分
- `timed_return_score`
  `train/val` 按日收益路径年化分按 `5:5` 平均后的补充分
- `drawdown_risk_score`
  `train/val` 固定窗口回撤风险分按 `5:5` 平均后的原始风险指标；窗口内用 `Ulcer` 风格回撤深度与持续时间衡量利润回吐压力
- `drawdown_penalty_score`
  由 `drawdown_risk_score` 映射出的分段惩罚项；先做基础扣分，超过拐点后按更陡斜率追加扣分
- `robustness_penalty_score`
  轻量鲁棒性软惩罚，只看 `train/val` 落差、`val` 分块稳定性，以及退出参数邻域在 `val` 3 段上的平台形态；总扣分封顶 `0.25`
- `turn_protection_score`
  `train/val` 趋势掉头窗口保护分按 `5:5` 平均后的诊断分，不再直接进入晋级主公式
- `promotion_score`
  最终晋级分。以 `capture_score` 为主，加少量 `timed_return_score`，再减去 `drawdown_penalty_score` 与 `robustness_penalty_score`

当前默认公式：

`capture_score = 0.50 * train_capture_score + 0.50 * val_capture_score`

`timed_return_score = 0.50 * train_timed_return_score + 0.50 * val_timed_return_score`

`drawdown_risk_score = 0.50 * train_drawdown_risk_score + 0.50 * val_drawdown_risk_score`

`turn_protection_score = 0.50 * train_turn_protection_score + 0.50 * val_turn_protection_score`

`drawdown_penalty_score = 0.20 * drawdown_risk_score + 1.00 * max(drawdown_risk_score - 1.25, 0.0)`

`promotion_score = 0.80 * capture_score + 0.20 * timed_return_score - drawdown_penalty_score - robustness_penalty_score`

`drawdown_risk_score` 直接复用现有 `train/val` 日收益路径，不新增回测。两侧都按固定 `28` 天窗口滚动切分，再对每个窗口计算 `Ulcer` 风格回撤值，最后用 `median + P75` 的加权聚合成风险分。这样 `train` 的滚动窗口路径和 `val` 的整年路径会落到同一时间单位上比较，也不会被一次单日极端点完全主导。

`drawdown_penalty_score` 在此基础上再做一层分段映射：低于 `1.25` 时只按基础权重扣分，超过 `1.25` 后每增加一单位风险都按更陡斜率继续扣分。目的不是把所有回撤都打死，而是显著压低“收益爆炸但回撤也很深”的候选，让研究器更偏向收益仍强、但利润回吐更可控的方案。

`robustness_penalty_score` 不引入新的硬 gate。它只作为软降权层，主要压低三类“看起来能赢、但泛化味道差”的候选：`train/val` 落差偏大、`val` 内部分块明显不稳、以及退出参数只在一个很尖的邻域点上有效。若本轮没有触发退出参数平台观察，则 `plateau` 相关部分记为 `0`。

`turn_protection_score` 仍复用现有主要趋势段，只在相邻趋势方向反转时切掉头保护窗口，衡量窗口内策略权益从运行高点到后续低点的最大回吐。它现在保留给诊断和人工复盘，不再直接进入晋级主公式。

晋升规则：

- 先过 `gate`
- 再满足 `promotion_score` 高于当前 active reference
- 刷新 champion 时会同步跑 `test`
- 已完成完整评估但未保留的候选也会后台异步补跑 `test`
- reject / duplicate_skipped 的 `test` 结果只进 round artifact、通知和人工观察，不参与 prompt、不参与晋升

`test` 只做验收，不参与 prompt，不参与当前轮次调参。

## 当前 Gate

当前真正参与 gate 的主条件是：

- `train` 均值分至少 `0.10`
- `train` 中位分至少 `0.00`
- `val` 命中率至少 `0.35`
- `val` 趋势捕获分至少 `0.05`
- `train / val` 连续趋势抓取分差不超过 `0.30`
- 手续费拖累不超过 `11.5%`
- `val` 分成 `3` 块后，最差块至少 `-0.35`
- `val` 负块数量最多 `1`

另外仍保留过拟合诊断。严重集中度会直接 veto，普通风险会进入 journal 和历史摘要。

## 当前研究器结构

当前主结构是：

1. `planner`
   复用当前 stage 的持久 session，只负责 draft round brief
2. `reviewer`
   每轮全新 short-lived 审稿 worker，只负责判定当前 draft brief 是 `PASS` 还是 `REVISE`
3. `edit_worker`
   短生命周期 worker，只负责修改 [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)
4. `repair_worker`
   只在同轮修错时出现，不保留长历史
5. `summary_worker`
   只根据最终真实 diff 回写最终候选元信息
6. 主进程
   负责 reviewer gating、真实 diff 检查、smoke、完整评估、gate、journal、Discord、memory 和 stage/session 管理

为了把大文件切薄但不改主流程，当前又补了 5 个实现层模块：

- `src/research_v2/reference_state.py`
- `src/research_v2/champion_artifacts.py`
- `src/research_v2/round_artifacts.py`
- `src/research_v2/backtest_window_runtime.py`
- `src/research_v2/evaluation_summary.py`
- `src/research_v2/journal_prompt_builder.py`

更直观的流程图见 [docs/agent_subagent_workflow.md](./agent_subagent_workflow.md)。

当前不再存在这些自动行为：

- 不再自动切 `factor_admission`
- 不再自动切 `compaction lane`
- 不再单独沉淀 `working_base`

## Prompt 与 Session

当前 prompt 分层：

1. 仓库级 [AGENTS.md](../AGENTS.md)
2. workspace 局部 `AGENTS.md`，只放共享长期规则
3. planner system prompt
4. reviewer system prompt
5. edit / repair / summary 各自的 system prompt
6. planner runtime prompt
7. `config/research_v2_operator_focus.md`
8. `config/research_v2_champion_review.md`
9. `wiki/reviewer_summary_card.md`
10. `wiki/direction_board.md`
11. `wiki/latest_history_package.md`
12. `wiki/failure_wiki.md`
13. `wiki/duplicate_watchlist.md`

其中第 6 层现在只保留精简前台记忆：

- 当前 stage 执行摘要
- 失败核聚合
- 方向风险/过热簇
- 最近轮次元信息

不再把全量表格反复塞进 planner 主上下文。

当前 planner 的顺序约束：

- 先看当前 champion 人工观察卡；只有 `champion_code_hash` 命中时，这张卡才会进入 prompt。
- 再看上一轮 `reviewer` 总结卡，先判断上一轮为什么失败。
- 再看 `direction_board / duplicate_watchlist / failure_wiki`，确认当前主方向是否已经高热，以及这次是不是仍在同一热区横移。
- planner 读取 wiki 时先看顶部摘要；只有方向高热、证据冲突或需要确认失败层时，才下钻更长表格。
- 再看当前诊断，定位失败更像发生在 `outer_context / path / final_veto / routing / followthrough / exit / unknown` 的哪一层。
- 先决定这轮继续同方向还是转向，再写 draft brief。
- 最后才写 `hypothesis / change_plan / novelty_proof`。
- `novelty_proof` 现在用于先说明“上一版被什么证据否掉”，再说明“这轮为什么继续或转向”，最后才补“这次换了哪一层真实触达路径或关键规则链”。

当前 edit_worker 约束：

- worker 仍然只改 [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)
- 整份策略文件都允许修改，但改动必须克制、结构准确、添加有必要
- 策略文件现在同时包含 `PARAMS` 和 `EXIT_PARAMS`；worker 可以调部分退出参数，但不能改固定的杠杆和仓位风险参数
- 对单个连续型 `EXIT_PARAMS`，planner 可选给 `exit_range_scan`；主进程最多扫 3 个值、3 个轻量窗口、2 worker 预筛，只把最佳值送入完整评估
- context cache 只跟数据准备相关的退出开关绑定，避免普通退出参数变化导致重复加载/聚合数据
- early reject 只在 `10 / 18 / 26` 三个 eval milestone 做连续 snapshot；早停同时看趋势分、综合期段分和命中率
- behavioral no-op 除成交指纹外，还会看 `outer_context / path / final_veto / filled_entries` 的关键漏斗变化
- worker 会收到当前 gate、最弱维度和 val 多/空捕获/命中率的紧凑诊断，但不会收到完整历史包，也不重新做 planner 研究
- 单轮改动预算只作为参考，不是硬 gate；超出小 diff 范围时必须服务于打通真实路径或删除旧冗余
- 真正会进入 diff / smoke / full eval 的，是最终源码里的真实落地改动

当前 reviewer 的职责：

- reviewer 不负责提出新方向，只负责审稿
- reviewer 只能输出 `PASS` 或 `REVISE`
- reviewer 会把 `direction_board` 当成高优先级证据，但它只在命中高热方向时检查“有没有结构性差异”，不会把方向永久封死
- reviewer 在 `PASS` 前会检查 draft 是否说明预计新增、删除或迁移哪类真实交易；如果完全没有交易路径变化说明，会打回让 planner 补清楚
- 若 `REVISE`，必须指出当前 draft 仍落在哪个失败近邻，以及 planner 下一版至少要换哪一层
- 未通过 reviewer 的 brief 不会进入 `edit_worker`


人工观察卡：

- `config/research_v2_champion_review.md` 只给 planner 看，是短人工直觉，不是硬 gate。
- 内容应短尽短；适合写一句当前 champion 的图形直觉。
- 卡内 `champion_code_hash` 必须命中当前 champion；刷新新 champion 后主进程自动忽略旧卡，避免旧直觉污染新阶段。

当前 session 规则：

- `planner` 在同一个 stage 内复用同一个 session
- `reviewer / edit_worker / repair_worker / summary_worker` 都不复用 planner session
- session scope 只绑定当前 active reference 的 `code_hash + stage`
- 一旦手工重开 stage，或 champion 刷新，planner session 就会重置
- `wiki/reviewer_summary_card.md` 只保留当前轮最后一次 reviewer 判定；如果同轮先 `REVISE` 后重写再 `PASS`，最终卡记录最后一次 `PASS`
- 如果本轮没有刷新 champion，主进程会写回 `journal / wiki / reviewer_summary_card / direction_board`，然后沿用当前 stage / planner session 进入下一轮
- 当前同一轮允许出现 `planner -> reviewer -> planner 重写 -> reviewer` 的短链，但只有 planner 复用持久 session

## 复杂度与手工瘦身

复杂度现在只保留为只读诊断，不再自动驱动流程。

系统仍会记录：

- 每轮 diff 的复杂度变化
- 当前 active reference 哪些 function / family 最紧
- 哪些地方有明显膨胀风险

但它不会再：

- 自动拒绝“只是偏胖”的候选
- 自动触发压缩任务
- 自动切另一种因子准入模式
- 把复杂度提示反复塞进 planner / reviewer prompt
- 把复杂度状态发到 Discord 作为流程提示

手工瘦身 SOP：

1. 停掉研究器。
2. 手工瘦身或手工替换 active reference。
3. 执行 [scripts/reset_research_macd_aggressive_v2_stage.sh](../scripts/reset_research_macd_aggressive_v2_stage.sh)。
4. 重新启动研究器，进入新 stage。

这个脚本会保留 `memory/raw/*`，但会清空：

- 当前 stage journal
- prompt 摘要
- wiki 前台文件
- summaries
- session 状态
- agent workspace
- heartbeat

## 当前运行保护

当前有效保护包括：

- reviewer 审稿不通过时，本轮不会进入落码
- 候选必须真的改出源码 diff
- planner brief 缺关键字段会 fail fast
- `smoke` 行为完全不变会记为 `behavioral_noop`
- 重复 source、重复 hash、空 diff、非法输出会作为技术空转隔离
- failure wiki 现在主要作为历史记忆与风险提示，不再做 exact-cut 硬拦截
- complexity 诊断会被记录，但不再自动驱动流程
- 连续 no-edit 到阈值时，研究器会自动停机，避免空耗算力

## 当前目录与状态文件

最常看的文件：

- [state/research_macd_aggressive_v2_heartbeat.json](../state/research_macd_aggressive_v2_heartbeat.json)
  当前轮次、phase、窗口、更新时间
- [state/research_macd_aggressive_v2_journal.jsonl](../state/research_macd_aggressive_v2_journal.jsonl)
  当前 stage 的正式轮次结果
- `state/research_macd_aggressive_v2_memory/raw/`
  全量未压缩历史
- `state/research_macd_aggressive_v2_memory/wiki/`
  当前 stage 的 reviewer 卡、重复/失败摘要
- [logs/macd_aggressive_research_v2.log](../logs/macd_aggressive_research_v2.log)
  研究器主日志
- [logs/macd_aggressive_research_v2_model_calls.jsonl](../logs/macd_aggressive_research_v2_model_calls.jsonl)
  模型调用大小、resume 情况、耗时和返回大小

## 当前 Discord 播报

当前主表只保留这些字段：

- 数据范围
- 本轮窗口
- train+val期间收益
- val期间收益
- 新 champion 时的 test期间收益
- `Sharpe(train / val / test)`
- train+val交易数量
- 新 champion 时的 test交易数量
- val多/空捕获
- train+val期间回撤/手续费拖累
- 新 champion 时的 test期间回撤/手续费拖累

另外：

- 普通启动仍会发送 `📌 已加载 champion 参考` 播报。
- 真正刷新 champion 时，仍会发送 `🚀 新 champion` 播报
- 如果进程随后因承接这个新 champion 而重启，启动时那条 `📌 已加载 champion 参考` 不再重复播报

## 安全评估当前策略

如果你只是想安全评估当前 `src`，不要直接跑 `--no-optimize`。请用下面这段：

```bash
python3 - <<'PY'
from pathlib import Path
import importlib
import sys

sys.path.insert(0, str(Path('.').resolve()))
sys.path.insert(0, str(Path('src').resolve()))

import strategy_macd_aggressive as sm
import scripts.research_macd_aggressive_v2 as rs

importlib.reload(sm)
rs.strategy_module = sm
report = rs.evaluate_current_strategy()
print(report.summary_text)
PY
```
