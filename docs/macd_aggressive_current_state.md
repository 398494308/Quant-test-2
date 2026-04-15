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

- 总交易数 `>= 30`
- `eval` 交易数 `>= 24`
- `validation` 交易数 `>= 5`
- `eval` 正收益窗口占比 `>= 30%`
- 最大回撤 `<= 50%`
- 爆仓次数 `<= 0`
- `validation` 平均收益 `>= -10%`
- `eval - validation` 落差 `<= 30`
- 平均手续费拖累 `<= 6%`

## 当前最优快照

来源：

- 当前策略源码按最新评分口径重新评估

`2026-04-15 16:07:46`（Asia/Shanghai）已按当前 `non_overlapping_oos_v1` 评分口径重算并写回 best state。

截至 `2026-04-15`，当前最佳基底：

- `eval_avg_return = 1.89%`
- `eval_median_return = 0.00%`
- `eval_p25_return = -1.08%`
- `validation_avg_return = 10.84%`
- `worst_drawdown = 18.52%`
- `avg_fee_drag = 0.76%`
- `total_trades = 98`
- `eval_trades = 64`
- `validation_trades = 34`
- `daily_sharpe = 0.96`
- `daily_sortino = 2.04`
- `quality_score = 2.04`
- `promotion_score = 1.69`
- `eval_unique_days = 609`
- `eval_overlap_days = 203`
- `eval_overlap_points_dropped = 203`
- `eval_window_sortino_avg = 0.49`
- `eval_window_sortino_p25 = -1.43`
- `eval_window_sortino_worst = -4.91`
- `gate = 通过`

## 当前研究器增强

本次主线增强点已经落地：

- prompt 第一屏现在先给模型看“方向风险表”，按方向簇聚合近期失败、零增益、运行报错和最佳 `promotion_delta`。
- 方向簇标签分为 `OPEN / WARM / ACTIVE_WINNER / SATURATED / EXHAUSTED / RUNTIME_RISK`，用于提示模型哪些方向已经接近被试空。
- 候选必须输出 `closest_failed_cluster` 与 `novelty_proof`，先解释与最近失败方向的差异，再允许进入评估。
- 若最近连续 3 轮都只是低变化零增益微调，prompt 会触发“探索轮”，强制要求切换因子家族或编辑区域家族，避免继续在同簇里换说法。
- 每轮先跑 `smoke` 窗口，运行报错会在同一轮走 repair loop；修复次数耗尽才记成 `runtime_failed`。
- `heartbeat` 会带上当前阶段和窗口索引，方便排查是卡在 `smoke_test`、`full_eval` 还是 `candidate_repairing`。
- 压缩历史除了标签统计外，也会保留方向簇摘要，避免 20 轮压缩后丢失长期失败记忆。

当前本地显式运行参数：

- `MACD_V2_EARLY_REJECT_WINDOWS=15`
- `MACD_V2_EARLY_REJECT_SORTINO=-1.0`
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

当前策略还没有完全跳出 `short_breakdown` 的局部优化盆地。

也就是说：

- 已经能持续找到更优版本
- 但研究方向仍然主要集中在做空破位质量、价格发现效率、4H 跟随确认这几个邻近主题
- 主评分口径更干净后，窗口稳定性尾部仍偏弱，`eval_window_sortino_p25` 和最差窗口仍明显为负

如果后续要继续推进，重点不该是回到旧版参数扫，而是继续提升研究器的方向多样性与记忆表达质量。
