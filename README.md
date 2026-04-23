# Quant AI Research

`OKX BTC-USDT-SWAP` 激进趋势策略研究仓库。这里的 `SWAP` 指 OKX 的 `USDT` 本位永续合约。

这个仓库当前只维护一条主线：同一套策略源码、同一套回测器、同一套研究器，以及一套后续可接 `OKX` 自动交易的实盘外壳。

## 当前范围

- 策略源码：[src/strategy_macd_aggressive.py](src/strategy_macd_aggressive.py)
- 回测器：[src/backtest_macd_aggressive.py](src/backtest_macd_aggressive.py)
- 研究器：[scripts/research_macd_aggressive_v2.py](scripts/research_macd_aggressive_v2.py)
- 管理脚本：[scripts/manage_research_macd_aggressive_v2.sh](scripts/manage_research_macd_aggressive_v2.sh)
- stage 重开脚本：[scripts/reset_research_macd_aggressive_v2_stage.sh](scripts/reset_research_macd_aggressive_v2_stage.sh)
- 数据下载脚本：[scripts/download_aggressive_data.py](scripts/download_aggressive_data.py)
- 实盘外壳说明：[real-money-test/README.md](real-money-test/README.md)

## 数据与评分

- 数据源：`OKX`
- 事实层：`15m`
- `1h / 4h` 由 `15m` 聚合得到，只做趋势和环境确认
- 回测执行价优先使用 `1m`
- 当前评分口径：`trend_capture_v6`

时间窗口：

- `train`：`2023-07-01` 到 `2024-12-31`
- `val`：`2025-01-01` 到 `2025-12-31`
- `test`：`2026-01-01` 到 `2026-03-31`

晋升规则：

- 候选必须先过 `gate`
- 再满足相对当前 active reference 的 `promotion_delta > 0.02`
- `test` 只在新 champion 时运行，不进入 prompt
- 复杂度信息现在只做只读诊断，不再自动触发压缩任务，也不再自动切换 factor mode

## 当前 Active Reference

最新快照以 [state/research_macd_aggressive_v2_best.json](state/research_macd_aggressive_v2_best.json) 为准。

当前关键指标：

| 项目 | 数值 |
| --- | --- |
| gate | 通过 |
| quality_score | 0.2607 |
| promotion_score | 0.3634 |
| train+val期间收益 | 16.60% |
| val期间收益 | 13.18% |
| val多/空捕获 | 0.37 / 0.51 |
| Sharpe(train / val) | 0.37 / 0.48 |
| train+val交易数量 | 262 |

当前 `train / val` 的 funding 覆盖率仍是 `0%`。这不是研究器没接 funding，而是公开可得的 OKX 历史 funding 覆盖不到当前旧窗口；缺失区间按 `0 funding` 回测，并在评估里保留口径一致性。

## 研究器运行方式

1. 运行时只维护一个 active reference。
   在还没有 gate-passed 版本时，它是 `baseline`；一旦出现 gate-passed 版本，它就是 `champion`。
2. `planner` 用持久 session，只负责提出 round brief；但顺序上先看结构化失败反馈，先判断上一轮为什么失败、这轮该继续还是转向，再写 brief。
3. `reviewer` 是每轮全新的短生命周期审稿 worker。它只审 planner 的 draft brief，结论只有 `PASS` 或 `REVISE`；若打回，planner 必须先吸收 reviewer 打回信息，再重写 brief。
4. `edit_worker / repair_worker` 是短生命周期 worker，只负责把 reviewer 放行后的方向落到 [src/strategy_macd_aggressive.py](src/strategy_macd_aggressive.py)。
5. 候选必须先形成真实源码 diff，再过 `smoke`，再跑完整 `train walk-forward + val`。
6. `behavioral_noop`、空 diff、重复源码、重复结果盆地、非法 brief、reviewer 连续打回都会被挡下。
7. complexity 诊断仍会进入 journal、wiki 和 prompt，但不会再自动改研究车道，也不会单独沉淀一条 `working_base`。

## Agent / Subagent 工作流

下面这张图按“竖着看”的方式画，`planner` 的持久主 session 在中轴；其余都是围绕它工作的短生命周期 subagent。

```mermaid
flowchart TB
    A[主进程开始第 N 轮] --> B[主 session / planner<br/>读取 reviewer 卡、history package、failure wiki、duplicate watchlist]
    B --> C[planner 产出 draft round brief]

    C --> D[reviewer 子会话<br/>短生命周期审稿]
    D --> E{PASS or REVISE}

    E -- REVISE --> F[planner 吸收 reviewer 打回信息<br/>重写 draft round brief]
    F --> D

    E -- PASS --> G[edit_worker 子会话<br/>把通过审稿的 brief 落到策略源码]
    G --> H{源码校验 / no-edit / 运行报错?}

    H -- 修错 --> I[repair_worker 子会话<br/>只修当前轮技术问题]
    I --> H

    H -- 通过 --> J[主进程执行真实 diff 检查<br/>smoke / full eval / gate]
    J --> K[summary_worker 子会话<br/>按最终真实 diff 回写候选摘要]
    K --> L[主进程写 journal / wiki / reviewer 卡 / heartbeat]
    L --> M[更新当前 stage 记忆]
    M --> B
```

当前这套工作流的意思是：

- `planner` 仍然负责研究方向，但它先给出的是 `draft brief`，不是直接进入落码的最终指令。
- `reviewer` 不是第二个 planner。它不能替 `planner` 发明新方向，只能判断这份 draft 当前值不值得试。
- 如果 `reviewer=REVISE`，本轮不会进入 `edit_worker`。`planner` 必须先吸收打回理由，再重写 draft。
- 如果 `reviewer=PASS`，才会进入 `edit_worker` 落码。
- `repair_worker` 只在同轮技术修错时出现，不参与研究方向判断。
- `summary_worker` 只根据最终真实 diff 回写候选摘要，避免“原 brief”和“最终代码”错位。
- 每轮结束后，主进程会把结果写回 `journal / wiki / reviewer_summary_card`。下一轮 `planner` 会先读这些前台记忆，再继续研究。

更完整的说明见 [docs/agent_subagent_workflow.md](docs/agent_subagent_workflow.md)。

## 手工瘦身 SOP

当前复杂度不再由系统自动压缩。推荐人工 SOP：

1. 停掉研究器。
2. 手工瘦身当前策略，或手工替换 active reference。
3. 执行 [scripts/reset_research_macd_aggressive_v2_stage.sh](scripts/reset_research_macd_aggressive_v2_stage.sh)。
   这个脚本会保留 `memory/raw/*`，但清空 front memory、session、workspace 和当前 stage journal。
4. 重新启动研究器，进入新 stage。

## 常用命令

下载或重建本地 OKX 数据：

```bash
python3 scripts/download_aggressive_data.py
```

启动研究器：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh start
```

查看状态：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh status
```

停止研究器：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh stop
```

重开一个新 stage：

```bash
bash scripts/reset_research_macd_aggressive_v2_stage.sh
```

单轮运行一次研究器：

```bash
python3 scripts/research_macd_aggressive_v2.py --once
```

## 文档导航

- [STRATEGY.md](STRATEGY.md)
  用非工程语言解释当前策略在看什么、怎么开仓、怎么退出。
- [docs/macd_aggressive_current_state.md](docs/macd_aggressive_current_state.md)
  解释当前评分、gate、session、memory、Discord 播报和运行目录。
- [docs/agent_subagent_workflow.md](docs/agent_subagent_workflow.md)
  专门解释 `planner / reviewer / edit_worker / repair_worker / summary_worker / 主进程` 之间怎么配合。
- [real-money-test/README.md](real-money-test/README.md)
  解释 `freqtrade` dry-run / live 外壳如何接这套策略。

## 目录速览

```text
config/              研究器配置、凭证样板、人工方向卡
data/                OKX 价格、funding、指数数据
docs/                当前状态文档
logs/                研究器日志与模型调用日志
real-money-test/     freqtrade dry-run / live 外壳
scripts/             下载、研究、管理、stage reset 脚本
src/                 策略、回测器、研究器依赖模块
state/               active reference 状态、journal、memory、heartbeat、session
tests/               研究器相关测试
```
