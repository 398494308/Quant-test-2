# 激进版 MACD 当前状态

本文档只记录当前代码和当前状态文件真正对应的版本，不沿用旧说明。

## 当前定位

- 目标: 找到一版能在趋势段吃到弹性、但不会在留出和压力测试里立刻崩掉的激进趋势策略
- 当前主线: `v2` 研究器
- 当前默认策略文件: [src/strategy_macd_aggressive.py](../src/strategy_macd_aggressive.py)
- 当前默认回测器: [src/backtest_macd_aggressive.py](../src/backtest_macd_aggressive.py)

## 当前信号结构

当前只有两个有效入场信号:

- `long_breakout`
- `short_breakdown`

入场前会做三层过滤:

1. `15m`、`1h`、`4h` 三周期同方向
2. 横盘过滤不过就不做
3. 突破/破位后还要看量能、K 线质量、ADX、RSI、跟随确认

## 当前默认执行参数

- 杠杆: `14x`
- 单仓保证金占比: `0.17`
- 单仓最小保证金: `5000`
- 单仓最大保证金: `30000`
- 最大并发仓位: `4`
- 最大持仓: `288` 根
- `long_breakout` 最大持仓: `384` 根
- `short_breakdown` 最大持仓: `96` 根
- 是否允许金字塔加仓: `是`
- 最大加仓次数: `2`
- 加仓比例: 当前仓位的 `28%`
- 加仓触发收益: `16%`

## 当前研究窗口

- 评估范围: `2025-09-01` ~ `2026-03-31`
- `eval` 窗口长度: `28` 天
- `eval` 步长: `21` 天
- `holdout` 长度: `28` 天
- 当前实际窗口数: `9` 个 `eval` + `1` 个 `holdout`

当前 `holdout`:

- `2026-03-04` ~ `2026-03-31`

## 当前 Gate

- 总交易数 `>= 30`
- `eval` 交易数 `>= 24`
- `holdout` 交易数 `>= 8`
- `eval` 正收益窗口占比 `>= 40%`
- 最大回撤 `<= 45%`
- 爆仓次数 `<= 0`
- `holdout` 平均收益 `>= 0%`
- `eval-holdout` 落差 `<= 22`
- 平均手续费拖累 `<= 6%`
- 压力测试最差收益 `>= -8%`

## 当前最优基底指标

来源:

- 本地运行态文件 `state/research_macd_aggressive_v2_best.json`

截至 `2026-04-13`，当前最优基底是:

- `weighted_eval_return = 3.70%`
- `eval_median_return = 1.37%`
- `eval_p25_return = -10.03%`
- `holdout_avg_return = -15.50%`
- `worst_drawdown = 31.67%`
- `avg_fee_drag = 4.44%`
- `total_trades = 240`
- `daily_sharpe = 0.23`
- `daily_sortino = 0.49`
- `profit_factor = 1.16`
- `stress_avg_return = 1.23%`
- `stress_worst_return = -18.47%`
- `quality_score = 0.52`
- `promotion_score = -8.62`
- `gate_passed = false`

当前不过线的直接原因:

- 留出收益不足 `(-15.50%)`
- 压力测试过弱 `(-18.47%)`

## 当前结论

- 这版基底在一部分 `eval` 窗口里可以赚钱。
- 但它还没有证明自己能扛住最后的留出窗口。
- 也没有证明自己在更差的手续费、滑点和延迟条件下足够稳。
- 所以它目前还是“可继续研究的底稿”，不是“已经确认过关的版本”。
