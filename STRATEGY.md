# 激进版 MACD 策略说明

本文档只描述当前 `test3` 仓库正在运行的版本。

## 核心目标

这套策略的目标不是低波动，而是：

- 在明确趋势段里尽量早点上车
- 允许较高波动和回撤
- 通过过滤横盘、假突破、低质量延续，避免把仓位浪费在噪声里

## 当前有效信号

当前只有两个真实入场信号：

- `long_breakout`
- `short_breakdown`

未启用：

- `long_pullback`
- `short_bounce_fail`

## 当前决策框架

### 1. 多周期方向过滤

- `15m`：执行层
- `1h`：主趋势确认
- `4h`：大级别环境确认

只有低周期和高周期大体同向时，才允许继续往下看。

### 2. 横盘与弱趋势过滤

会综合看：

- `EMA spread`
- `EMA slope`
- `ADX`
- `Chop`
- `ATR ratio`

目的不是追求完美识别，而是尽量少在“没展开、方向弱、来回扫”的阶段激进开仓。

### 3. 入场质量过滤

突破 / 破位不是只看“创新高 / 新低”，还会看：

- `lookback` 窗口
- 成交量放大
- K 线实体质量
- 收盘位置
- `RSI`
- 高周期是否同步跟随

研究器近几轮重点优化的，也是 `short_breakdown` 里的“低质量追空”和“伪价格发现”问题。

## 当前执行与风控

默认执行参数要点：

- 杠杆：`14x`
- 单仓保证金占比：`17%`
- 单仓最小保证金：`5000`
- 单仓最大保证金：`30000`
- 最大并发仓位：`4`
- 最大加仓次数：`2`

退出链路包括：

- ATR 初始止损
- `TP1` 分批止盈
- 保本止损
- 追踪止损
- 趋势失效退出
- 时间退出
- 反向信号退出

## 回测口径

当前回测器：

- 用 `15m` K 线驱动策略
- 优先用 `1m` 数据模拟执行价
- 计入滑点、手续费、资金费
- 允许并发仓位和有限加仓

交易统计口径当前为“整笔仓位”：

- `TP1` 不再单独算成一笔 trade
- `trades / win_rate / avg_pnl_pct / avg_hold_bars / signal_stats.closed_trades`
  都按完整仓位统计

## 研究器边界

当前主线研究器是 `v2`：

- 文件：`scripts/research_macd_aggressive_v2.py`
- 允许模型直接改写策略源码
- 但会经过源码校验、参数边界校验和研究历史去重
- 评分核心是 `trend_capture_v4 + gate`

当前主评分口径：

- 先在唯一 `4h` 市场路径上识别 BTC 的大趋势段
- 当前切段已经放宽到更细的 `4h` 趋势段，约 `5%` 级别单边也算核心机会；按当前全段数据大约会识别出 `61` 段
- 每段拆成 `到来 / 陪跑 / 掉头`
- `trend_capture_score` 看的是：能否及时跟上、能否陪跑主趋势、能否在掉头时跑掉或反手
- `return_score` 看的是整条路径最终把资金放大了多少
- `quality_score = 0.70 * eval_trend_capture_score + 0.30 * eval_return_score`
- `promotion_score = 0.70 * validation_trend_capture_score + 0.30 * validation_return_score`
- `promotion_score` 只负责晋级，要求至少比当前 best 高 `0.02`
- `validation` 连续路径会再按时间顺序切成 `3` 个分块；分块波动过大、最差块过弱或负分块太多时，即使总分过线也不会刷新 best
- 全段连续收益和全段趋势分保留为诊断项，不再直接决定 best
- 爆仓和回撤保留为诊断项，但不再是主评分，也不再单独惩罚

当前研究器的研究记忆与修复链路：

- prompt 开头会先强调：这是一套 `15m` 执行、`1h + 4h` 确认的 BTC 激进趋势策略，目标是抓大行情，而不是做低波动收益平滑。
- prompt 第一屏先显示方向风险表，按方向簇聚合近期失败、零增益和运行报错。
- prompt 第一屏的“最近未压缩轮次表”现在会正确吃到当前评分口径的最新历史，不再被旧口径 compact 索引错切掉。
- 旧评分口径不会混进当前主表；它们只会在后面以“旧评分口径弱参考”出现，作为低优先级方向启发。
- 防重复探索主要靠 prompt 约束，不做系统层面的硬性方向封禁。
- 候选必须输出 `closest_failed_cluster` 与 `novelty_proof`，说明本轮为什么不是重复试错。
- 若最近连续 3 轮都属于低变化轮次，prompt 会强制把下一轮视为“探索轮”，优先切换因子家族或编辑区域家族。
- 每轮先跑少量 `smoke` 窗口；若代码运行报错，会在同一轮把错误回传给模型做 repair，而不是直接开始下一轮。
- 若 repair 次数耗尽，这轮会被记成 `runtime_failed`，同样进入 journal 和记忆压缩。
- `heartbeat` 会持续写出当前阶段和窗口索引，便于定位是卡在 `smoke`、`full_eval` 还是 repair。
- 提前淘汰已从旧的 Sortino 门槛改成“部分窗口趋势捕获快照”：只有当趋势段数量已足够且趋势捕获分、命中率都很差时，才会提前结束这轮。

参数校验除范围外，也会检查关键关系：

- `breakout_rsi_min <= breakout_rsi_max`
- `breakdown_rsi_min <= breakdown_rsi_max`
- `intraday_ema_fast < intraday_ema_slow`
- `hourly_ema_fast < hourly_ema_slow`
- `fourh_ema_fast < fourh_ema_slow`
- `macd_fast < macd_slow`

## 当前仓库定位

这是一个独立的 `test3` 主题仓库：

- 不依赖外部同类仓库
- 旧版 `v1` 链路已经移除
- 当前所有主线说明以 `README.md` 和 `docs/macd_aggressive_current_state.md` 为准
