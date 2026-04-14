# Quant Test 2

这是一个专门研究 `BTCUSDT` 永续合约激进趋势策略的独立仓库。当前默认主线已经切到 `v2` 研究器，重点不是“乱试参数”，而是让脚本按固定流程反复做下面这件事:

1. 拿当前最优策略当底稿。
2. 生成一个新候选。
3. 把新候选丢到多段历史里回测。
4. 只有在“赚钱、回撤、留出”这些核心条件都过线时，才允许它晋级。

一句大白话:

- 这套脚本本质上是在做“自动化策略面试”。
- `eval` 窗口像平时做题。
- `holdout` 窗口像最后没给答案的考试。

## 当前策略在干什么

策略本身不复杂，核心就是两种信号:

- `long_breakout`: 多周期一起看多时，追多突破
- `short_breakdown`: 多周期一起看空时，追空破位

它的判断方式可以粗暴理解成:

1. 先看 `15m`、`1h`、`4h` 三个周期是不是同方向。
2. 再看现在是不是横盘震荡，如果像在磨人，就直接不做。
3. 如果不是横盘，再看是不是刚突破前高/跌破前低，而且成交量、K 线实体、ADX、RSI 这些条件也要一起过线。
4. 开仓后不是死扛，会带 ATR 止损、保本、分批止盈、移动止盈、趋势失效退出、时间退出，还允许有限次加仓。

当前默认执行口径:

- 标的: `BTCUSDT` 永续合约
- 主执行周期: `15m`
- 趋势确认周期: `1h` + `4h`
- 执行价模拟: 优先用 `1m` 数据
- 手续费: 已计入
- 滑点: 已计入
- 资金费: 已计入
- 默认杠杆: `14x`
- 单仓保证金占比: `17%`
- 最大并发仓位: `4`
- 最大加仓次数: `2`

## 现在研究脚本到底怎么跑

当前默认主入口是:

- [scripts/research_macd_aggressive_v2.py](scripts/research_macd_aggressive_v2.py)

运行前提:

- 机器上要安装可用的 `codex` 命令。
- `~/.codex/config.toml` 和 `~/.codex/auth.json` 需要已经能正常跑 `codex exec`。

运行逻辑按顺序是这样的:

1. 启动时先读取 `config/research_v2.env`，把窗口、门槛、循环间隔这些配置装进运行时。
2. 然后加载“当前最优基底”。如果 `backups/strategy_macd_aggressive_v2_best.py` 存在，就优先拿它；否则拿当前的 [src/strategy_macd_aggressive.py](src/strategy_macd_aggressive.py)。
3. 评估时会把 `2025-09-01` 到 `2026-03-31` 切成 `9` 个重叠的 `eval` 窗口，再加 `1` 个最终 `holdout` 窗口。
4. 每个窗口都调用同一个回测器 [src/backtest_macd_aggressive.py](src/backtest_macd_aggressive.py)，用同一套进出场、费用、滑点和资金费规则算结果。
5. 如果是优化模式，研究器会调用本机 `codex exec` 生成候选策略；如果 Codex 暂时不可用或超时，本轮会延后，等下次继续。
6. 新候选先过源码校验，再检查是不是和最近试过的版本重复，避免反复撞墙。
7. 通过后，候选会跑完整套 `eval + holdout` 评估。
8. 只有同时满足两件事，才会被收成新的最优:
   - `gate_passed = true`
   - `promotion_score` 比当前最优更高
9. 不管通过还是失败，都会记进 `state/research_macd_aggressive_v2_journal.jsonl`，下次提示词会参考这份研究历史。

如果你只想看当前版本有多烂或多好，不想生成新候选，可以用:

```bash
python3 scripts/research_macd_aggressive_v2.py --once --no-optimize
```

## 当前评估窗口

数据范围：`2023-01-01` ~ `2026-03-31`（前 6 个月做指标预热）

按 60 / 40 切分：

- `eval`（29 个窗口）：`2023-07-01` ~ `2025-02-28`
  - 覆盖熊转牛、高位横盘、主升浪、深度回调、二次主升
  - 每个窗口 `28` 天，步长 `21` 天
- `validation`（1 个窗口）：`2025-03-01` ~ `2026-03-31`
  - 13 个月，AI 看不到具体数字
  - 占总 Sortino 计算的 40% 权重

提前淘汰：前 15 个 eval 窗口跑完后，如果 Sortino < -1.0，直接跳过剩余窗口。

## 当前 Gate 条件

候选想晋级，至少要过这些硬门槛:

- 总交易数 `>= 30`
- `eval` 交易数 `>= 24`
- `validation` 交易数 `>= 5`
- `eval` 正收益窗口占比 `>= 30%`
- 最大回撤 `<= 50%`
- 爆仓次数 `<= 0`
- `validation` 平均收益 `>= -10%`
- `eval - validation` 落差 `<= 30`
- 平均手续费拖累 `<= 6%`

以上全部可通过 `config/research_v2.env` 覆盖。

## 评分方式

评分已简化为**纯 Sortino Ratio**（年化）:

- `quality_score` = eval 窗口的年化 Sortino（只看平时练习的风险收益比）
- `promotion_score` = eval + validation 合并后的年化 Sortino

大白话:

- Sortino 只看"亏钱的时候波动有多大"，不惩罚赚大钱。
- validation 占 40% 的天数，如果验证集表现差，promotion_score 自然被拉低。
- 以前的 18 个系数加减公式已经全部去掉了。
- Gate 负责"淘汰明显不合格的"，Sortino 负责"在合格的里面挑最好的"。
- AI 看不到 validation 的具体数字，但能看到 gate 是否通过。

## 参数安全范围

策略的每个可调参数现在都有硬性上下界。AI 生成的候选只要参数超出范围就直接拒绝:

- ADX 阈值: `5` ~ `50`
- lookback 周期: `3` ~ `60`
- RSI 范围: 各自有合理上下界
- EMA 周期: 快线 `3` ~ `30`，慢线 `10` ~ `100`
- MACD 参数: `fast 5~20`，`slow 15~40`，`signal 3~15`
- 成交量比率: `0.5` ~ `5.0`

范围定义在 `src/research_v2/strategy_code.py` 的 `PARAM_BOUNDS` 字典里。

## 当前关键指标

当前数据范围已切换到 `2023-01 ~ 2026-03`，窗口从 10 个扩展到 30 个（29 eval + 1 validation）。

具体数字见 `state/research_macd_aggressive_v2_best.json`。研究循环每次产生新最优时会更新此文件。

## 关键指标是啥意思

如果你不懂量化，记下面这几个就够了:

- `eval_avg_return`: 平时多段练习题的平均成绩
- `holdout_avg_return`: 最后正式考试的成绩
- `max_drawdown`: 中途最多会疼到什么程度
- `avg_fee_drag`: 手续费和执行损耗吃掉了多少
- `profit_factor`: 赚的钱和亏的钱相比有没有明显优势
- `quality_score`: 不看最终考试时的内部评分
- `promotion_score`: 连最终考试一起算的总评分
- `gate`: 一票否决规则，只要踩线就不让晋级

## 主要脚本

当前最值得看的脚本:

- [scripts/research_macd_aggressive_v2.py](scripts/research_macd_aggressive_v2.py)
  当前主研究器，负责候选生成、评估、日志、晋级。
- [scripts/manage_research_macd_aggressive_v2.sh](scripts/manage_research_macd_aggressive_v2.sh)
  `start / stop / restart / status / reset` 统一入口。
- [scripts/run_research_macd_aggressive_v2.sh](scripts/run_research_macd_aggressive_v2.sh)
  带锁和自动拉起的 supervisor。
- [src/research_v2/evaluation.py](src/research_v2/evaluation.py)
  所有核心评分、惩罚和 Gate 都在这里。
- [src/research_v2/strategy_code.py](src/research_v2/strategy_code.py)
  负责候选源码校验、参数边界检查和 diff 摘要。
- [src/research_v2/journal.py](src/research_v2/journal.py)
  负责研究历史记录，避免同样的坑反复踩。

辅助脚本:

- [scripts/param_sensitivity.py](scripts/param_sensitivity.py)
  看单个参数变动对收益、回撤、交易次数的影响。
- [scripts/search_aggressive_params.py](scripts/search_aggressive_params.py)
  小范围暴力枚举参数组合。
- [scripts/analyze_windows.py](scripts/analyze_windows.py)
  看指定窗口的逐笔交易明细。
- [scripts/freqtrade_compare.py](scripts/freqtrade_compare.py)
  检查自研策略和 `freqtrade` 适配层在“原始入场信号”上是否对齐。
- [scripts/compare_strategies.py](scripts/compare_strategies.py)
  对比保守版和激进版的月度表现。

旧版脚本还保留着:

- [scripts/research_macd_aggressive.py](scripts/research_macd_aggressive.py)
  这是旧版 `v1` 研究器，现在不是默认主线。

## 数据与目录

```text
Quant-test-2/
├── config/            环境变量与运行配置
├── data/              价格、情绪、资金费数据
├── docs/              策略和当前状态说明
├── logs/              研究器日志
├── reports/           对比报表
├── state/             最优状态、心跳、研究日志
├── backups/           当前候选 / 当前最优策略备份
├── real-money-test/   freqtrade dry-run / live 壳子
├── scripts/           研究、分析、下载、对比脚本
└── src/               策略、回测器、OpenAI 客户端、v2 模块
```

关键数据文件:

- [data/price/BTCUSDT_futures_15m_20240601_20260401.csv](data/price/BTCUSDT_futures_15m_20240601_20260401.csv)
- [data/price/BTCUSDT_futures_1h_20240601_20260401.csv](data/price/BTCUSDT_futures_1h_20240601_20260401.csv)
- [data/price/BTCUSDT_futures_1m_20240601_20260401.csv](data/price/BTCUSDT_futures_1m_20240601_20260401.csv)
- [data/index/crypto_fear_greed_daily_20240601_20260401.csv](data/index/crypto_fear_greed_daily_20240601_20260401.csv)
- [data/funding/OKX_BTC_USDT_SWAP_funding_20240601_20260401.csv](data/funding/OKX_BTC_USDT_SWAP_funding_20240601_20260401.csv)

## 快速开始

只做一次评估，不生成新候选:

```bash
python3 scripts/research_macd_aggressive_v2.py --once --no-optimize
```

跑一轮优化:

```bash
python3 scripts/research_macd_aggressive_v2.py --once
```

持续运行 `v2` 研究循环:

```bash
bash scripts/manage_research_macd_aggressive_v2.sh start
tail -f logs/macd_aggressive_research_v2.out
```

看研究状态:

```bash
bash scripts/manage_research_macd_aggressive_v2.sh status
```

做入场信号一致性检查:

```bash
./.venv/bin/python scripts/freqtrade_compare.py
```

## 关于 AI 不稳定

当前代码不再直连第三方 OpenAI 兼容代理，也不做本地参数兜底:

- 候选生成由本机 `codex exec` 完成，模型默认使用 `gpt-5.4` + `medium`。
- 如果 Codex 超时或暂时不可用，这一轮会延后，按恢复等待时间再试。
- 所以 Codex 的可用性现在会直接影响研究推进速度。

## 实盘壳子

`real-money-test/` 是 `freqtrade` 的 dry-run / live 壳子，主要目的不是替代研究器，而是验证策略在真实执行环境里的行为。

补一句最重要的话:

- 回测器允许 `4` 个并发仓位。
- `real-money-test` 当前只跑单一交易对。
- 所以实盘壳子更像“单一净仓位 + 分批加减仓”，不能直接把 `4` 并发回测收益原样当成未来实盘收益。

更多说明见 [real-money-test/README.md](real-money-test/README.md)。

## 相关文档

- [STRATEGY.md](STRATEGY.md)
- [docs/program_macd_aggressive.md](docs/program_macd_aggressive.md)
- [docs/macd_aggressive_current_state.md](docs/macd_aggressive_current_state.md)

## GitHub

远端仓库:

```text
https://github.com/398494308/Quant-test-2.git
```
