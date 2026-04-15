# Quant Test 3

这是一个独立的 `BTCUSDT` 永续合约激进趋势研究仓库。

当前主线只有一套：

- 策略文件：`src/strategy_macd_aggressive.py`
- 回测器：`src/backtest_macd_aggressive.py`
- 研究器：`scripts/research_macd_aggressive_v2.py`

仓库已经清理掉旧的 `v1` 研究链路，也不再依赖外部同类仓库的脚本、路径或配置。

## 当前做什么

研究器每轮会做这几件事：

1. 读取当前最优策略。
2. 让模型生成一个新候选。
3. 对候选跑整套 `eval + validation` 窗口回测。
4. 只有 `gate` 通过且 `promotion_score` 提升，才晋级为新的最优。
5. 把每轮结果写进 journal，供后续提示词复用。

当前评分口径：

- `quality_score` = `eval` 非重叠 OOS 主路径日收益的年化 Sortino
- `promotion_score` = `eval` 非重叠 OOS 主路径 + `validation` 合并后的年化 Sortino
- rolling `eval` 窗口继续保留，但只做稳定性诊断，不再重复加权同一天

当前窗口配置默认是：

- `eval` 起点：`2023-07-01`
- `eval` 终点：`2026-03-31`
- `eval` 窗口：`28` 天
- `eval` 步长：`21` 天
- `validation`：末尾 `396` 天

## 当前策略轮廓

当前只有两个有效入场信号：

- `long_breakout`
- `short_breakdown`

核心逻辑：

- `15m` 做执行
- `1h + 4h` 做趋势确认
- 横盘环境会尽量少做
- 开仓后带 ATR 初始止损、保本、TP1、移动止损、趋势失效退出、时间退出
- 允许有限次加仓

回测器当前已包含：

- `1m` 执行价近似
- 滑点
- 手续费
- 资金费
- 多并发仓位
- TP1 分批结算

交易统计口径已经修正为“整笔仓位”，不会再把 `TP1` 当成独立 trade 放大交易数。

## 当前最优快照

研究器运行时会把最新最优状态写到 `state/research_macd_aggressive_v2_best.json`。

截至 `2026-04-15`，按当前非重叠 OOS 评分口径重新评估，当前最佳快照为：

- `quality_score = 2.04`
- `promotion_score = 1.69`
- `eval_avg_return = 1.89%`
- `validation_avg_return = 10.84%`
- `worst_drawdown = 18.52%`
- `total_trades = 98`
- `eval_unique_days = 609`
- `eval_window_sortino_p25 = -1.43`
- `eval_window_sortino_worst = -4.91`
- `gate = 通过`

## 目录结构

```text
config/            研究配置、样板配置
data/              价格、情绪、资金费数据
docs/              当前状态说明
real-money-test/   freqtrade dry-run / live 壳子
scripts/           研究、分析、下载脚本
src/               策略、回测器、研究器模块
state/             运行状态、journal、最优快照
tests/             最小回归测试
```

## 配置文件

当前主要配置：

- `config/research_v2.env`
  研究窗口、gate、循环间隔
- `config/research_v2.env.example`
  `v2` 配置样板
- `config/secrets.env.example`
  Discord / OKX / 可选 Codex 覆盖项样板

`config/secrets.env` 现在是可选的：

- 只跑研究器且不发 Discord 时，可以没有它
- 需要 Discord 或 `real-money-test` 的 OKX 凭证时再补

## 快速开始

只做一次评估，不生成候选：

```bash
python3 scripts/research_macd_aggressive_v2.py --once --no-optimize
```

跑一轮研究：

```bash
python3 scripts/research_macd_aggressive_v2.py --once
```

持续运行研究器：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh start
tail -f logs/macd_aggressive_research_v2.out
```

看状态：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh status
```

看窗口分析：

```bash
python3 scripts/analyze_windows.py
```

检查 freqtrade 入场信号适配：

```bash
./.venv/bin/python scripts/freqtrade_compare.py
```

## real-money-test

`real-money-test/` 现在是本仓库内自洽的 freqtrade 壳子：

- 策略入口来自 `real-money-test/strategies/MacdAggressiveStrategy.py`
- 逻辑源仍是 `src/freqtrade_macd_aggressive.py`
- 运行配置由 `real-money-test/build_runtime_config.py` 本地生成
- OKX 凭证优先读取环境变量或 `config/secrets.env`

不再默认继承外部仓库配置。

## 已移除的旧链路

以下内容已经从仓库主线移除：

- `v1` 研究器脚本
- 旧版 `v1` 研究脚本
- 对外部仓库配置链的默认依赖
- 对外部路径的硬编码

如果你还看到旧概念，优先以这里和 `docs/macd_aggressive_current_state.md` 为准。
