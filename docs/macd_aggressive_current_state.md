# MACD Aggressive Current State

这份文档只描述当前这条主线现在怎么跑，不再保留 `test1 / test2` 或旧版双状态设计的历史说明。

## 当前快照

运行时只有一个 active reference。最新快照在：

- [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)
- [backups/strategy_macd_aggressive_v2_best.py](../backups/strategy_macd_aggressive_v2_best.py)
- [state/research_macd_aggressive_v2_best.json](../state/research_macd_aggressive_v2_best.json)

当前关键指标：

| 项目 | 数值 |
| --- | --- |
| 当前角色 | champion |
| gate | 通过 |
| score regime | trend_capture_v6 |
| quality_score | 0.2607 |
| promotion_score | 0.3634 |
| train+val期间收益 | 16.60% |
| val期间收益 | 13.18% |
| val多/空捕获 | 0.37 / 0.51 |
| Sharpe(train / val) | 0.37 / 0.48 |
| train+val交易数量 | 262 |

说明：

- `state/research_macd_aggressive_v2_best.json` 里如果还带旧字段，例如 `working_base`，那是历史兼容残留；重启后会按新的单一 active reference 语义继续写。
- 当前运行状态以 [state/research_macd_aggressive_v2_heartbeat.json](../state/research_macd_aggressive_v2_heartbeat.json) 为准。

## 数据与窗口

当前默认数据源已经统一为 `OKX`。

研究与评估窗口：

- `train`：`2023-07-01` 到 `2024-12-31`
- `val`：`2025-01-01` 到 `2025-12-31`
- `test`：`2026-01-01` 到 `2026-03-31`

当前默认配置：

- `train` 滚动窗口长度：`28` 天
- 滚动步长：`21` 天
- `smoke` 窗口数：`5`
- 主循环等待：`10s`
- provider 恢复等待：`90s`

## 评分与晋升

单段分数：

`period_score = 0.70 * trend_capture_score + 0.30 * return_score`

研究器主要看两类分：

- `quality_score`
  `train` 滚动窗口的均值分
- `promotion_score`
  `val` 连续 holdout 的单段分

晋升规则：

- 先过 `gate`
- 再满足相对当前 active reference 的 `promotion_delta > 0.02`
- 只有刷新 champion 时才额外跑隐藏 `test`

`test` 只做验收，不参与 prompt，不参与当前轮次调参。

## 当前 Gate

当前真正参与 gate 的主条件是：

- `train` 均值分至少 `0.10`
- `train` 中位分至少 `0.00`
- `val` 命中率至少 `0.35`
- `val` 趋势捕获分至少 `0.05`
- `train / val` 分差不超过 `0.30`
- 手续费拖累不超过 `8%`
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

更直观的流程图见 [docs/agent_subagent_workflow.md](./agent_subagent_workflow.md)。

当前不再存在这些自动行为：

- 不再自动切 `factor_admission`
- 不再自动切 `compaction lane`
- 不再单独沉淀 `working_base`

## Prompt 与 Session

当前 prompt 分层：

1. 仓库级 [AGENTS.md](../AGENTS.md)
2. workspace 局部 `AGENTS.md`
3. planner runtime prompt
4. `wiki/reviewer_summary_card.md`
5. `wiki/latest_history_package.md`
6. `wiki/failure_wiki.md`
7. `wiki/duplicate_watchlist.md`
8. worker prompt

当前 planner 的顺序约束：

- 先看上一轮 `reviewer` 总结卡
- 先看结构化失败反馈和当前诊断
- 先复盘上一轮为什么失败、失败更像发生在哪一层交易路径
- 先决定这轮继续同方向还是转向，再写 draft brief
- 最后才写 `hypothesis / change_plan / novelty_proof`
- `novelty_proof` 现在用于先说明“上一版被什么证据否掉”，再说明“这轮为什么继续或转向”，最后才补“这次和旧 cut 的不同点”

当前 reviewer 的职责：

- reviewer 不负责提出新方向，只负责审稿
- reviewer 只能输出 `PASS` 或 `REVISE`
- 若 `REVISE`，必须指出当前 draft 仍落在哪个失败近邻，以及 planner 下一版至少要换哪一层
- 未通过 reviewer 的 brief 不会进入 `edit_worker`

当前 session 规则：

- `planner` 在同一个 stage 内复用同一个 session
- `reviewer / edit_worker / repair_worker / summary_worker` 都不复用 planner session
- session scope 只绑定当前 active reference 的 `code_hash + stage`
- 一旦手工重开 stage，或 champion 刷新，planner session 就会重置
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

推荐 SOP：

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
