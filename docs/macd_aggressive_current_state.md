# MACD Aggressive Current State

这份文档只记录当前这条主线的实际状态，不展开旧方案和历史演进。

## 当前快照

最后核对时间：`2026-04-22 15:48 CST`

当前主参考和 champion 存在这里：

- [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)
- [backups/strategy_macd_aggressive_v2_best.py](../backups/strategy_macd_aggressive_v2_best.py)
- [state/research_macd_aggressive_v2_best.json](../state/research_macd_aggressive_v2_best.json)

当前 best 快照更新时间：`2026-04-22 15:33 CST`

| 项目 | 数值 |
| --- | --- |
| 当前角色 | champion |
| gate | 通过 |
| score regime | trend_capture_v6 |
| quality_score | 0.2723 |
| promotion_score | 0.3798 |
| train+val期间收益 | 20.85% |
| val期间收益 | 17.56% |
| val多/空捕获 | 0.37 / 0.51 |
| Sharpe(train / val) | 0.36 / 0.56 |
| train+val交易数量 | 254 |
| train funding 覆盖率 | 0% |
| val funding 覆盖率 | 0% |

当前研究器状态：

- 管理脚本状态：`running`
- 当前轮次：`1`
- 当前 phase：`selection_period_eval`
- 当前窗口：`train+val连续`

## 当前数据和窗口

当前默认数据源已经统一为 `OKX`。

研究与评估用到的时间窗口是：

- `train`
  `2023-07-01` 到 `2024-12-31`
- `val`
  `2025-01-01` 到 `2025-12-31`
- `test`
  `2026-01-01` 到 `2026-03-31`

当前默认窗口配置：

- `train` 滚动窗口长度：`28` 天
- 滚动步长：`21` 天
- `smoke` 窗口数：`5`
- 主循环等待：`10s`
- provider 恢复等待：`90s`

## 当前评分与晋升

单段分数仍是：

`period_score = 0.70 * trend_capture_score + 0.30 * return_score`

研究器核心看两件事：

- `quality_score`
  `train` 滚动窗口的均值分
- `promotion_score`
  `val` 单段连续分

当前晋升规则：

- 先过 `gate`
- 再满足 `promotion_delta > 0.02`
- 只有刷新 champion 时才额外跑隐藏 `test`

`test` 只做验收，不参与 prompt，不参与当前轮次的调参。

## 当前 gate

当前真正参与 gate 的主条件是：

- `train` 均值分至少 `0.10`
- `train` 中位分至少 `0.00`
- `val` 命中率至少 `0.35`
- `val` 趋势捕获分至少 `0.05`
- `train / val` 分差不超过 `0.30`
- 手续费拖累不超过 `8%`
- `val` 分成 `3` 块后，最差块至少 `-0.35`
- `val` 负块数量最多 `1`

同时还会继续做过拟合诊断。严重集中度会直接 veto，普通风险会保留在 journal 和摘要里。

## 当前研究器结构

当前研究器已经稳定到下面这套分工：

1. `planner`
   复用同一个 stage 级 session，只负责提出 round brief
2. `edit_worker`
   短生命周期 session，只负责改 [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)
3. `repair_worker`
   只在同轮修错时出现，不保留长历史
4. 主进程
   负责真实 diff 检查、smoke、完整评估、gate、journal、Discord、memory、failure wiki

当前 planner brief 已经统一成 text-only 契约，核心字段缺失会直接判 `generation_invalid`，不会再自动圆成“看似成功的一轮”。

## 当前 prompt 和 session 组织

现在的 prompt 结构是：

1. workspace 局部 `AGENTS.md`
2. [config/research_v2_operator_focus.md](../config/research_v2_operator_focus.md)
3. planner runtime prompt
4. `wiki/latest_history_package.md`
5. `wiki/failure_wiki.md`
6. worker prompt

当前 session 行为：

- 同一个 `baseline / champion stage` 内复用同一个 `planner` session
- `edit_worker / repair_worker` 不复用 planner session
- 如果 `factor_change_mode` 在 `default` 和 `factor_admission` 之间切换，会主动清掉旧 planner session
- 如果 champion 或 baseline 刷新，也会切新 stage 和新 session

当前 session 文件：

- [state/research_macd_aggressive_v2_session.json](../state/research_macd_aggressive_v2_session.json)
- [state/research_macd_aggressive_v2_agent_workspace/AGENTS.md](../state/research_macd_aggressive_v2_agent_workspace/AGENTS.md)

当前方向引导口径：

- 人工方向卡和 champion 缺陷提示现在只指定“主目标”，不再把多头弱直接收窄成固定补法。
- 弱侧是 `long` 时，研究器仍优先补多头，但要持续监控空头是否被破坏。
- 若某个多头补法已经在当前 stage 反复失败，下一轮应继续围绕补多头，但优先换机制层、换 choke point 或换最终路由。
- `factor_admission` 不再在短 stall 后立刻自动开启；当前节奏是连续 `5` 轮 stall 先提醒、`7` 轮强提醒、`10` 轮才强制切入，且单个 stage 最多连开 `4` 轮。

## 当前运行保护

当前有效的运行保护包括：

- 候选必须真的改出源码 diff
- round brief 缺关键字段会 fail fast
- `smoke` 先跑，再决定是否值得完整评估
- `smoke` 行为完全不变时会先同轮重生，连续不变才记 `behavioral_noop`
- 命中 failure wiki 的 exact cut 会在评估前被挡回
- 重复 source、重复 hash、空 diff、非法输出会隔离成技术空转，不让它们污染真正的研究记忆
- 复杂度现在采用两档预警加绝对硬帽：`warning_1` 提醒开始偏胖，`warning_2` 提醒优先先压缩，只有超过绝对复杂度帽才会直接拒收
- 复杂度硬帽仍会优先走同轮 repair，不再立刻浪费下一轮
- 当前 stage 内如果同一 `cluster + target + ordinary family` 的 rejected 反复出现且没有正向 delta，也会被视为研究停滞证据，用来触发换挡；这不是跨 stage 永久封锁。

## 当前目录与状态文件

当前最重要的目录和文件是：

- [state/research_macd_aggressive_v2_heartbeat.json](../state/research_macd_aggressive_v2_heartbeat.json)
  当前轮次、phase、窗口、更新时间
- [state/research_macd_aggressive_v2_journal.jsonl](../state/research_macd_aggressive_v2_journal.jsonl)
  每轮正式结果
- `state/research_macd_aggressive_v2_memory/`
  原始历史、压缩摘要、prompt 包
- [logs/macd_aggressive_research_v2.log](../logs/macd_aggressive_research_v2.log)
  研究器主日志
- [logs/macd_aggressive_research_v2_model_calls.jsonl](../logs/macd_aggressive_research_v2_model_calls.jsonl)
  模型调用大小、是否 resume、耗时和返回大小

## 当前 Discord 播报口径

现在 Discord 主表只保留这些字段：

- 数据范围
- 本轮窗口
- 因子模式
- train+val期间收益
- val期间收益
- 新 champion 时的 test期间收益
- `Sharpe(train / val / test)`
- train+val交易数量
- 新 champion 时的 test交易数量
- val多/空捕获
- train+val期间回撤/手续费拖累
- 新 champion 时的 test期间回撤/手续费拖累

## 当前已知事项

- 策略当前仍是“空头强于多头”的画像，长侧是更值得继续优化的部分。
- `train / val` funding 覆盖率目前还是 `0%`，这来自 OKX 公共 funding 历史本身的覆盖限制。
- 启动研究器时，系统会用当前 best 恢复 [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)。

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
