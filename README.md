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
2. 先把“方向风险表 + 过拟合风险表 + 历史压缩摘要 + 最近轮次表”喂给模型，再生成一个新候选。
3. 候选必须显式说明它最接近哪个失败方向簇，并给出 `novelty_proof` 证明这轮不是重复试错。
4. 先对候选跑少量 `smoke` 窗口；若运行报错，会在同一轮把错误回传给模型修复，而不是直接进入下一轮。
5. `smoke` 通过后再跑整套 `eval + validation` 窗口回测。
6. 只有 `gate` 通过、未触发严重过拟合淘汰且 `promotion_score` 提升，才晋级为新的最优。
7. 把每轮结果写进 journal，包含 `accepted / rejected / early_rejected / runtime_failed`，并按 20 轮做压缩记忆。

当前评分口径：

- `score_regime = trend_capture_v1`
- `quality_score = 0.70 * eval_trend_capture_score + 0.30 * eval_return_score`
- `promotion_score = 0.70 * combined_trend_capture_score + 0.30 * combined_return_score`
- `trend_capture_score` 不看“平滑度”，而是看大趋势的 `到来 / 陪跑 / 掉头`
- 切段用唯一 `4h` 市场路径，不重复加权重叠窗口里的同一时间点

当前窗口配置默认是：

- `eval` 起点：`2023-07-01`
- `eval` 终点：`2026-03-31`
- `eval` 窗口：`28` 天
- `eval` 步长：`21` 天
- `validation`：末尾 `396` 天

## 本次机制增强

- 防重复探索仍然走 prompt 约束，不做系统层面的硬禁令。
- prompt 开头会先明确说明：这是 `15m` 执行、`1h + 4h` 确认的 BTC 激进趋势策略，目标是抓大波段，而不是追求平滑收益。
- prompt 第一屏现在会显示“方向风险表”，按方向簇聚合最近同评分口径下的失败、零增益和运行报错。
- prompt 第一屏紧跟着会显示“过拟合风险表”，明确标出哪些轮次虽然分数不差，但更像依赖少数同类行情，应该降权参考。
- 模型必须输出 `closest_failed_cluster` 与 `novelty_proof`，先解释为什么不是继续围绕同一失败簇做近邻微调。
- 若最近连续 3 轮都属于低变化轮次，prompt 会强制进入“探索轮”，要求切换因子家族或编辑区域家族，而不是继续做近邻改写。
- 每轮先跑 `smoke` 窗口，运行报错会在同一轮进入 repair loop，最多按配置尝试修复，再决定是否记为 `runtime_failed`。
- `heartbeat` 会写出当前阶段和窗口进度，便于判断卡在 `smoke`、`full_eval` 还是修复。
- `2026-04-15` 已按当前 `trend_capture_v1` 评分口径重新初始化 best state，避免旧分数挡住本该通过的候选。
- 提前淘汰从旧的 Sortino 逻辑改成了部分窗口趋势捕获快照：趋势段够多且趋势捕获分、命中率都很差时，会提前结束该轮。
- 评估阶段现在会额外计算过拟合风险：若结果过度依赖单一正向趋势段、同向连续段，或有效命中覆盖率过低且明显多空偏科，会直接被 gate 掉。

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

截至 `2026-04-15 22:37:12`（Asia/Shanghai），按当前 `trend_capture_v1` 评分口径重新评估，当前最优快照为：

- `quality_score = 0.58`
- `promotion_score = 0.74`
- `eval_trend_capture_score = 0.63`
- `validation_trend_capture_score = 0.28`
- `combined_trend_capture_score = 0.46`
- `combined_return_score = 1.38`
- `eval_avg_return = 0.24%`
- `validation_avg_return = 89.37%`
- `worst_drawdown = 34.93%`
- `total_trades = 475`
- `eval_major_segment_count = 11`
- `validation_major_segment_count = 10`
- `segment_hit_rate = 54.55%`
- `bull_capture_score = 0.33`
- `bear_capture_score = 0.67`
- `overfit_risk_score = 20`
- `overfit_top1_positive_share = 14.40%`
- `overfit_coverage_ratio = 100%`
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

当前与运行保护直接相关的参数：

- `MACD_V2_EARLY_REJECT_WINDOWS`
- `MACD_V2_EARLY_REJECT_MIN_SEGMENTS`
- `MACD_V2_EARLY_REJECT_TREND_SCORE`
- `MACD_V2_EARLY_REJECT_HIT_RATE`
- `MACD_V2_SMOKE_WINDOW_COUNT`
- `MACD_V2_MAX_REPAIR_ATTEMPTS`

## 快速开始

只做一次评估，不生成候选：

```bash
python3 scripts/research_macd_aggressive_v2.py --once --no-optimize
```

按当前评分口径重算现有基底，并把它写回新的 best state：

```bash
PYTHONPATH=src .venv/bin/python scripts/research_macd_aggressive_v2.py --once --no-optimize --reset-best
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
