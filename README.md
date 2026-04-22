# Quant AI Research

`OKX BTC-USDT-SWAP` 激进趋势策略研究仓库。

这个仓库当前只维护一条主线：同一套策略源码、同一套回测器、同一套研究器，以及一套配套的 `freqtrade` dry-run / live 外壳。

## 当前范围

- 策略源码：[src/strategy_macd_aggressive.py](src/strategy_macd_aggressive.py)
- 回测器：[src/backtest_macd_aggressive.py](src/backtest_macd_aggressive.py)
- 研究器：[scripts/research_macd_aggressive_v2.py](scripts/research_macd_aggressive_v2.py)
- 研究器管理脚本：[scripts/manage_research_macd_aggressive_v2.sh](scripts/manage_research_macd_aggressive_v2.sh)
- 数据下载脚本：[scripts/download_aggressive_data.py](scripts/download_aggressive_data.py)
- 实盘壳子说明：[real-money-test/README.md](real-money-test/README.md)

## 当前数据与评分

- 数据源已经统一到 `OKX`
- 事实层是 `15m` K 线
- `1h / 4h` 由 `15m` 聚合得到，只做趋势与环境确认
- 回测执行价优先使用 `1m`
- 当前评分口径是 `trend_capture_v6`
- `train`：`2023-07-01` 到 `2024-12-31`
- `val`：`2025-01-01` 到 `2025-12-31`
- `test`：`2026-01-01` 到 `2026-03-31`
- 新候选只有在 `gate` 通过且 `promotion_delta > 0.02` 时，才会替换当前 champion
- `test` 只在新 champion 时运行，不进入 prompt

## 当前 Champion 快照

以下快照来自 [state/research_macd_aggressive_v2_best.json](state/research_macd_aggressive_v2_best.json)，更新时间是 `2026-04-22 15:33 CST`：

| 项目 | 数值 |
| --- | --- |
| gate | 通过 |
| quality_score | 0.2723 |
| promotion_score | 0.3798 |
| train+val期间收益 | 20.85% |
| val期间收益 | 17.56% |
| val多/空捕获 | 0.37 / 0.51 |
| Sharpe(train / val) | 0.36 / 0.56 |
| train+val交易数量 | 254 |

当前 `train / val` 的 funding 覆盖率仍是 `0%`。原因不是研究器没接 funding，而是 OKX 公共 funding 历史拿不到对应旧窗口；缺失区间会按 `0 funding` 回测，并在评估里显式标注。

## 研究器怎么运行

1. 研究器围绕当前 champion 进入一个 stage，并在这个 stage 内复用同一个 `planner` session。
2. `planner` 只负责提出本轮假设和改动计划，不直接落码。
3. 短生命周期 `edit_worker` 只改策略文件；如果代码报错或复杂度超限，再由 `repair_worker` 做同轮修复。
4. 候选先过 `smoke`，再跑完整 `train walk-forward + val`。
5. `behavioral_noop`、重复结果盆地、failure wiki exact cut、空 diff、非法 brief 都会被挡下，不会静默混进有效研究结果。
6. `factor_admission` 采用 `5/7/10` 梯度提醒，复杂度采用“两档预警 + 绝对硬帽”；真正直接拒收的只剩绝对复杂度超帽。
7. 只有刷新 champion 时才跑隐藏 `test`，并清掉旧 stage 的 session 上下文。

## 常用命令

下载和重建本地 OKX 数据：

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

单轮运行一次研究器：

```bash
python3 scripts/research_macd_aggressive_v2.py --once
```

如果你只是想安全查看当前策略评估，不要直接跑 `--no-optimize`。评估当前 `src` 的安全方式见 [docs/macd_aggressive_current_state.md](docs/macd_aggressive_current_state.md)。

## 文档导航

- [STRATEGY.md](STRATEGY.md)
  解释这版策略在看什么、怎么开仓、怎么退出。
- [docs/macd_aggressive_current_state.md](docs/macd_aggressive_current_state.md)
  解释当前 champion、评分、gate、session、Discord 播报和运行目录。
- [real-money-test/README.md](real-money-test/README.md)
  解释 `freqtrade` dry-run / live 外壳如何接这套策略。

## 目录速览

```text
config/              研究器配置、凭证样板、人工方向卡
data/                OKX 价格、funding、指数数据
docs/                当前状态文档
logs/                研究器日志与模型调用日志
real-money-test/     freqtrade dry-run / live 外壳
scripts/             下载、研究、管理、分析脚本
src/                 策略、回测器、研究器依赖模块
state/               champion、journal、memory、heartbeat、session
tests/               研究器相关测试
```
