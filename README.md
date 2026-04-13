# Quant Test 2

这是一个专门研究 `BTCUSDT` 永续合约激进趋势策略的独立仓库。当前默认主线已经切到 `v2` 研究器，重点不是“乱试参数”，而是让脚本按固定流程反复做下面这件事:

1. 拿当前最优策略当底稿。
2. 生成一个新候选。
3. 把新候选丢到多段历史里回测。
4. 只有在“赚钱、回撤、留出、压力测试”都过线时，才允许它晋级。

一句大白话:

- 这套脚本本质上是在做“自动化策略面试”。
- `eval` 窗口像平时做题。
- `holdout` 窗口像最后没给答案的考试。
- `stress` 像故意把手续费、滑点、延迟调坏，看看它会不会当场露馅。

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

运行逻辑按顺序是这样的:

1. 启动时先读取 `config/research_v2.env`，把窗口、门槛、循环间隔这些配置装进运行时。
2. 然后加载“当前最优基底”。如果 `backups/strategy_macd_aggressive_v2_best.py` 存在，就优先拿它；否则拿当前的 [src/strategy_macd_aggressive.py](src/strategy_macd_aggressive.py)。
3. 评估时会把 `2025-09-01` 到 `2026-03-31` 切成 `9` 个重叠的 `eval` 窗口，再加 `1` 个最终 `holdout` 窗口。
4. 每个窗口都调用同一个回测器 [src/backtest_macd_aggressive.py](src/backtest_macd_aggressive.py)，用同一套进出场、费用、滑点和资金费规则算结果。
5. 如果是优化模式，研究器会生成一个候选策略。优先走 OpenAI；如果模型没输出、暂时不稳，或者你强制兜底，它会自动退回“本地小步改参数”，不会把整轮卡死。
6. 新候选先过源码校验，再检查是不是和最近试过的版本重复，避免反复撞墙。
7. 通过后，候选会跑完整套 `eval + holdout + stress` 评估。
8. 只有同时满足两件事，才会被收成新的最优:
   - `gate_passed = true`
   - `promotion_score` 比当前最优更高
9. 不管通过还是失败，都会记进 `state/research_macd_aggressive_v2_journal.jsonl`，下次提示词和本地兜底都会参考这份研究历史。

如果你只想看当前版本有多烂或多好，不想生成新候选，可以用:

```bash
python3 scripts/research_macd_aggressive_v2.py --once --no-optimize
```

## 当前评估窗口

当前代码和配置实际生成的窗口是:

- `评估1`: `2025-09-01` ~ `2025-09-28`
- `评估2`: `2025-09-22` ~ `2025-10-19`
- `评估3`: `2025-10-13` ~ `2025-11-09`
- `评估4`: `2025-11-03` ~ `2025-11-30`
- `评估5`: `2025-11-24` ~ `2025-12-21`
- `评估6`: `2025-12-15` ~ `2026-01-11`
- `评估7`: `2026-01-05` ~ `2026-02-01`
- `评估8`: `2026-01-26` ~ `2026-02-22`
- `评估9`: `2026-02-04` ~ `2026-03-03`
- `留出1`: `2026-03-04` ~ `2026-03-31`

切窗参数:

- 每个 `eval` 窗口长度: `28` 天
- 相邻 `eval` 窗口步长: `21` 天
- `holdout` 长度: `28` 天

## 当前 Gate 条件

候选想晋级，至少要过这些硬门槛:

- 总交易数 `>= 30`
- `eval` 交易数 `>= 24`
- `holdout` 交易数 `>= 8`
- `eval` 正收益窗口占比 `>= 40%`
- 最大回撤 `<= 45%`
- 爆仓次数 `<= 0`
- `holdout` 平均收益 `>= 0%`
- `eval - holdout` 落差 `<= 22`
- 平均手续费拖累 `<= 6%`
- 压力测试最差收益 `>= -8%`

大白话:

- 不是说“某一段很猛”就行。
- 还得证明它不是只会挑简单题做。
- 还得证明交易成本一变坏，它不会立刻散架。

## 当前关键指标

以下数字来自本地运行态文件 `state/research_macd_aggressive_v2_best.json`，更新时间是 `2026-04-13`:

- `weighted_eval_return = 3.70%`
  意思是 9 段平时考试里，按后面窗口稍微更重要的权重算，平均下来赚了 `3.70%`。
- `eval_median_return = 1.37%`
  意思是“典型窗口”大概只赚一点，不是暴利型稳定赚钱。
- `eval_p25_return = -10.03%`
  意思是倒霉一点的窗口，亏损会到 `10%` 左右。
- `holdout_avg_return = -15.50%`
  意思是最后那段没拿去喂优化器的真正考试，结果是亏的，而且亏得不轻。
- `worst_drawdown = 31.67%`
  意思是资金从高点到低点，最深回撤大约三成。
- `avg_fee_drag = 4.44%`
  意思是光手续费和执行摩擦，就吃掉了大约 `4.44%` 的收益。
- `total_trades = 240`
  意思是样本量不算太少，不是只靠几笔单子蒙出来的。
- `daily_sortino / daily_sharpe = 0.49 / 0.23`
  意思是风险收益比不算强，只能说勉强有点正值。
- `profit_factor = 1.16`
  意思是盈利单总利润只比亏损单总亏损高一点点，还不够硬。
- `stress_avg_return = 1.23%`
  意思是压力测试平均还没死透。
- `stress_worst_return = -18.47%`
  意思是压力测试里最差那次还是亏得比较难看。
- `quality_score = 0.52`
  可以理解成“平时表现分”，刚刚站上正数，但不高。
- `promotion_score = -8.62`
  可以理解成“综合晋级分”。把留出和压力测试算进去以后，还是负分。
- `gate_passed = false`
  当前最优基底没有晋级资格。
- `gate_reason = 留出收益不足(-15.50%)；压力测试过弱(-18.47%)`
  这就是它现在没过线的直接原因。

一句结论:

- 现在这版策略不是“完全不能跑”。
- 但它还远远没到“我可以放心说这版过关了”的程度。
- 真正拖后腿的是最后留出窗口和压力测试，不是平时窗口完全赚不到钱。

## 关键指标是啥意思

如果你不懂量化，记下面这几个就够了:

- `weighted_eval_return`: 平时多段练习题的综合成绩
- `holdout_avg_return`: 最后正式考试的成绩
- `max_drawdown`: 中途最多会疼到什么程度
- `avg_fee_drag`: 手续费和执行损耗吃掉了多少
- `profit_factor`: 赚的钱和亏的钱相比有没有明显优势
- `quality_score`: 不看最终考试时的内部评分
- `promotion_score`: 连最终考试和压力测试一起算的总评分
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
  负责候选源码校验、去重和本地参数兜底。
- [src/research_v2/journal.py](src/research_v2/journal.py)
  负责研究历史记录，避免同样的坑反复踩。

辅助脚本:

- [scripts/stress_test_aggressive.py](scripts/stress_test_aggressive.py)
  做闪崩、平盘、高杠杆等压力测试。
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

做压力测试:

```bash
python3 scripts/stress_test_aggressive.py
```

## 关于 AI 不稳定

当前代码已经内置兜底逻辑:

- OpenAI 正常可用时，优先让模型给完整候选策略源码。
- 如果模型空输出、暂时不稳，或者你显式开启强制兜底，研究器会自动切到本地小步改参数。
- 所以 AI 不稳定是“会影响效率”的问题，不是“整个研究流程彻底不能跑”的问题。

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
