# 激进版 MACD 当前状态

本文档只记录当前仓库主线的真实状态，不保留旧版说明。

## 当前主线

- 仓库：`test3`
- 研究器：`scripts/research_macd_aggressive_v2.py`
- 策略：`src/strategy_macd_aggressive.py`
- 回测器：`src/backtest_macd_aggressive.py`

旧版 `v1` 研究链路、外部仓库依赖脚本和旧配置链已经移除。

## 当前研究窗口

- `eval` 范围：`2023-07-01` ~ `2025-02-28`
- `validation` 范围：`2025-03-01` ~ `2026-03-31`
- `eval` 窗口长度：`28` 天
- `eval` 步长：`21` 天
- 当前窗口数：`29` 个 `eval` + `1` 个 `validation`

## 当前 Gate

- `eval` 大趋势段数 `>= 8`
- `validation` 大趋势段数 `>= 3`
- `eval` 趋势段命中率 `>= 35%`
- `validation` 趋势段命中率 `>= 25%`
- `eval` 趋势捕获分 `>= 0.10`
- `validation` 趋势捕获分 `>= 0.00`
- `eval - validation` 趋势捕获落差 `<= 0.45`
- 综合多头捕获分 `>= -0.10`
- 综合空头捕获分 `>= -0.10`
- 平均手续费拖累 `<= 8%`
- 严重过拟合风险直接淘汰

## 当前最优快照

来源：

- 当前策略源码按最新评分口径重新评估

`2026-04-15 22:37:12`（Asia/Shanghai）已按当前 `trend_capture_v1` 评分口径重算并写回 best state。

截至 `2026-04-15`，当前最佳基底：

- `eval_avg_return = 0.24%`
- `eval_median_return = -1.16%`
- `eval_p25_return = -9.75%`
- `validation_avg_return = 89.37%`
- `worst_drawdown = 34.93%`
- `avg_fee_drag = 3.67%`
- `total_trades = 475`
- `eval_trades = 336`
- `validation_trades = 139`
- `eval_trend_capture_score = 0.63`
- `validation_trend_capture_score = 0.28`
- `combined_trend_capture_score = 0.46`
- `combined_return_score = 1.38`
- `quality_score = 0.58`
- `promotion_score = 0.74`
- `eval_major_segment_count = 11`
- `validation_major_segment_count = 10`
- `major_segment_count = 22`
- `segment_hit_rate = 54.55%`
- `bull_capture_score = 0.33`
- `bear_capture_score = 0.67`
- `overfit_risk_score = 20`
- `overfit_top1_positive_share = 14.40%`
- `overfit_chain_positive_share = 14.40%`
- `overfit_coverage_ratio = 100%`
- `overfit_bull_bear_gap = 0.34`
- `eval_unique_trend_points = 3655`
- `eval_overlap_trend_points = 1246`
- `gate = 通过`

## 当前研究器增强

本次主线增强点已经落地：

- 主评分已经从 `Sortino` 切到 `trend_capture_v1`，核心目标改成抓大趋势，不再优化成“更平滑”。
- prompt 开头会先强调这是 `15m` 执行、`1h + 4h` 确认的 BTC 激进趋势策略，要求模型优先理解策略目标而不是盲目微调参数。
- 切段使用唯一 `4h` 时间轴，并在回测结果中直接输出市场路径与策略权益路径，避免为了评分重复加载市场数据。
- prompt 第一屏现在先给模型看“方向风险表”，按方向簇聚合近期失败、零增益、运行报错和最佳 `promotion_delta`。
- prompt 第一屏会在方向风险表之后追加“过拟合风险表”，把高风险轮次单独标出来，提醒模型谨慎参考。
- 方向簇标签分为 `OPEN / WARM / ACTIVE_WINNER / SATURATED / EXHAUSTED / RUNTIME_RISK`，用于提示模型哪些方向已经接近被试空。
- 候选必须输出 `closest_failed_cluster` 与 `novelty_proof`，先解释与最近失败方向的差异，再允许进入评估。
- 若最近连续 3 轮都只是低变化零增益微调，prompt 会触发“探索轮”，强制要求切换因子家族或编辑区域家族，避免继续在同簇里换说法。
- 每轮先跑 `smoke` 窗口，运行报错会在同一轮走 repair loop；修复次数耗尽才记成 `runtime_failed`。
- `heartbeat` 会带上当前阶段和窗口索引，方便排查是卡在 `smoke_test`、`full_eval` 还是 `candidate_repairing`。
- 压缩历史除了标签统计外，也会保留方向簇摘要，避免 20 轮压缩后丢失长期失败记忆。
- 提前淘汰改成了趋势捕获快照：窗口数足够、趋势段数足够且趋势捕获分和命中率都明显偏差时，才会提前结束这轮。
- 评估新增了过拟合风险诊断：会看单段正向贡献占比、同向连续段贡献占比、有效覆盖率、多空偏科和 eval/validation 落差；严重时直接 gate。

当前本地显式运行参数：

- `MACD_V2_EARLY_REJECT_WINDOWS=10`
- `MACD_V2_EARLY_REJECT_MIN_SEGMENTS=6`
- `MACD_V2_EARLY_REJECT_TREND_SCORE=-0.18`
- `MACD_V2_EARLY_REJECT_HIT_RATE=0.05`
- `MACD_V2_SMOKE_WINDOW_COUNT=3`
- `MACD_V2_MAX_REPAIR_ATTEMPTS=2`

## 当前已确认修复

最近已经修过这些关键口径问题：

- `TP1` 不再被当成独立 trade 统计
- 回测统计恢复为“整笔仓位”口径
- 入场滑点和初始止损现在共用同一基准价
- 加仓后会同步重算风险锚点
- 参数校验补上了关键关系约束
- 重叠 `eval` 窗口的主评分改为非重叠 OOS 主路径，窗口结果仅保留为稳定性诊断

## 当前关注点

当前基底在新口径下的主要问题已经更直白地暴露出来：

- 评估期大趋势段数量是够的，但真正“命中”的比例仍然很低
- 双向趋势捕获已经明显转正，当前 gate 可以通过，但 `eval` 和 `validation` 仍有不小落差
- 当前 best 的过拟合风险分是 `20`，属于“观察”而不是“高/严重”，说明还没有明显依赖单一行情，但后续仍应继续盯多样性

后续研究重点应该继续放在：

- 多头大趋势的到来识别和陪跑能力
- 掉头时的退出/反手机制
- 通过 prompt 和记忆让模型减少在单一近邻方向里打转，同时避免把高风险轮次当作模板复用
