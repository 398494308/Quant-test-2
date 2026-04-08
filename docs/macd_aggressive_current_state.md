# 激进版 MACD 当前设定

## 定位

- 面向高风险、高回报场景
- 目标是提升趋势段收益弹性，而不是做平滑曲线
- 接受较高回撤，但研究循环会限制极端坏解进入最优基底

## 信号结构

当前有效信号：

- `long_breakout` — 三周期共振 + 突破前高
- `short_breakdown` — 三周期共振 + 跌破前低

入场前置过滤：

- **横盘过滤**：通过多周期 choppiness、ATR ratio、EMA spread 和 slope 综合判断，横盘环境下不开仓
- **趋势确认**：breakout/breakdown 触发后还需通过 EMA spread、slope、突破距离等 5 项中至少 3 项确认，避免假突破

## 当前运行参数

- 杠杆：`14x`
- 最大并发：`5`
- 单仓保证金占比：`17%`
- 单仓最小保证金：`5000 USDT`
- 单仓最大保证金：`30000 USDT`

## 做多出场参数（long_breakout）

| 参数 | 值 | 说明 |
|------|-----|------|
| breakout_stop_atr_mult | 2.3 | ATR 止损倍数 |
| breakout_tp1_pnl_pct | 56.0% | 第一止盈触发门槛 |
| breakout_tp1_close_fraction | 0.16 | 第一止盈平仓比例 |
| breakout_trailing_activation_pct | 98.0% | 移动止盈激活门槛 |
| breakout_trailing_giveback_pct | 32.0% | 移动止盈允许回吐 |
| breakout_break_even_activation_pct | 42.0% | 保本激活门槛 |
| breakout_max_hold_bars | 384 | 最大持仓 K 线数 |

## 做空出场参数（short_breakdown）

| 参数 | 值 | 说明 |
|------|-----|------|
| short_breakdown_stop_atr_mult | 2.1 | ATR 止损倍数 |
| short_breakdown_tp1_pnl_pct | 22.0% | 第一止盈触发门槛 |
| short_breakdown_tp1_close_fraction | 0.22 | 第一止盈平仓比例 |
| short_breakdown_trailing_activation_pct | 34.0% | 移动止盈激活门槛 |
| short_breakdown_trailing_giveback_pct | 9.0% | 移动止盈允许回吐 |
| short_breakdown_break_even_activation_pct | 18.0% | 保本激活门槛 |
| short_breakdown_max_hold_bars | 96 | 最大持仓 K 线数 |

## 加仓机制

- 金字塔加仓：开启
- 最大加仓次数：3
- 加仓比例：当前仓位的 28%
- 加仓触发利润：10%
- 加仓 ADX 门槛：19.0

## 当前 13 个核心优化参数

入场 7 个：

- `macd_fast = 5`
- `macd_slow = 16`
- `macd_signal = 3`
- `hourly_adx_min = 15.2`
- `breakout_lookback = 22`
- `breakdown_lookback = 25`
- `breakout_rsi_max = 82.1`

出场 6 个：

- `leverage = 14`
- `position_fraction = 0.17`
- `breakout_stop_atr_mult = 2.3`
- `breakout_trailing_activation_pct = 98.0`
- `breakout_trailing_giveback_pct = 32.0`
- `short_breakdown_trailing_activation_pct = 34.0`

## 参数搜索分组

| 组名 | 包含参数 |
|------|---------|
| momentum_core | macd_fast, macd_slow, macd_signal |
| trend_timing | hourly_adx_min, breakout_lookback, breakdown_lookback, breakout_rsi_max |
| aggression_risk | leverage, position_fraction, breakout_stop_atr_mult |
| trailing_profit | breakout_trailing_activation_pct, breakout_trailing_giveback_pct, short_breakdown_trailing_activation_pct |

## 评分机制

当前评分已切到 Walk-Forward `eval + holdout` 双层结构：

- `selection_score` 只基于全部 `eval` 窗口表现
- `promotion_score` = `selection_score` 再叠加留出窗口回归惩罚
- 优化模型只看到 `eval` 摘要，`holdout` 只在外层 gate / 晋级时使用

惩罚项：

- **一致性惩罚**：`eval` 收益标准差 x 0.15
- **尾部惩罚**：最差 `eval` 窗口亏损超过 15% 时，超出部分 x 0.18
- **亏损窗口惩罚**：亏损 `eval` 窗口占比超过 55% 时触发
- **回撤惩罚**：回撤超过 55% 时，超出部分 x 0.22
- **爆仓惩罚**：每次爆仓扣 1.5 分
- **留出落差惩罚**：`eval_avg - holdout_avg` 为正时，差值 x 0.15
- **负留出惩罚**：`holdout_avg < 0` 时继续扣分

门禁条件：

- 总交易 >= 30，`eval` 交易 >= 20，留出交易 >= 8
- 最大回撤 <= 65%
- 爆仓 <= 10 次
- `eval` 均值 > 0
- `eval` 正收益窗口占比 >= 35%
- 杠杆 >= 14x
- 留出均值 >= 0
- `eval_avg - holdout_avg <= 28`
- `holdout_avg` 不得比历史最优留出均值低超过 6 个百分点

## 研究循环设置

- 每轮默认最多改动 `4` 个键
- 单键步长通常不超过当前值 `12%`
- 连续 `6` 轮通过但未刷新最优时，自动放宽到 `6` 个键和 `18%` 步长
- 重复检测只检查精确匹配，允许同方向不同幅度的尝试
- accepted / rejected 各保留最近 `16` 条精确签名记忆
- 最多重规划 `2` 次，仍重复则直接跳过本轮

## 评估窗口

- 评估范围：2025-09-01 ~ 2026-03-31
- 每块 20 天
- 除最后 2 块留出窗口外，其余全部作为 Walk-Forward `eval`
- 留出结果不直接暴露给优化模型，仅用于外层拦截与晋级判断
