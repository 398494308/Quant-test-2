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
3. `edit_worker / repair_worker` 是短生命周期 worker，只负责把方向落到 [src/strategy_macd_aggressive.py](src/strategy_macd_aggressive.py)。
4. 候选必须先形成真实源码 diff，再过 `smoke`，再跑完整 `train walk-forward + val`。
5. `behavioral_noop`、空 diff、重复源码、重复结果盆地、非法 brief 都会被挡下。
6. complexity 诊断仍会进入 journal、wiki 和 prompt，但不会再自动改研究车道，也不会单独沉淀一条 `working_base`。

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
