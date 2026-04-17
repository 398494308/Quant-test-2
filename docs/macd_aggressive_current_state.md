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

- `eval` 大趋势段数 `>= 18`
- `validation` 大趋势段数 `>= 8`
- `eval` 趋势段命中率 `>= 40%`
- `validation` 趋势段命中率 `>= 35%`
- `eval` 趋势捕获分 `>= 0.10`
- `validation` 趋势捕获分 `>= 0.05`
- `eval - validation` 趋势捕获落差 `<= 0.60`
- `quality_score - promotion_score` 综合分落差 `<= 0.35`
- `validation` 连续路径按时间顺序切 `3` 块后：分块 `std <= 0.30`
- `validation` 最差分块 `>= -0.20`
- `validation` 负分块数量 `<= 1`
- 全段连续多头捕获分 `>= 0.00`
- 全段连续空头捕获分 `>= 0.00`
- 平均手续费拖累 `<= 8%`
- 严重过拟合风险直接淘汰

## 当前最优快照

来源：

- 当前策略源码按最新评分口径重新评估

`2026-04-17 11:39:31`（Asia/Shanghai）已按当前 `trend_capture_v4` 评分口径重算并写回 best state。

截至 `2026-04-17`，当前最佳基底：

- `eval_avg_return = 2.28%`
- `eval_median_return = 0.50%`
- `eval_p25_return = -9.00%`
- `validation_avg_return = 88.98%`
- `full_period_return_pct = 230.09%`
- `worst_drawdown = 34.17%`
- `avg_fee_drag = 3.33%`
- `total_trades = 432`
- `eval_trades = 313`
- `validation_trades = 119`
- `eval_trend_capture_score = 0.71`
- `validation_trend_capture_score = 0.18`
- `combined_trend_capture_score = 0.53`
- `combined_return_score = 1.72`
- `quality_score = 0.75`
- `promotion_score = 0.40`
- `promotion_gap = 0.35`
- `validation_block_score_mean = 0.23`
- `validation_block_score_std = 0.27`
- `validation_block_score_min = -0.15`
- `validation_block_fail_count = 1`
- `eval_major_segment_count = 33`
- `validation_major_segment_count = 21`
- `major_segment_count = 61`
- `segment_hit_rate = 45.90%`
- `bull_capture_score = 0.37`
- `bear_capture_score = 0.72`
- `overfit_risk_score = 20`
- `overfit_top1_positive_share = 8.32%`
- `overfit_chain_positive_share = 11.04%`
- `overfit_coverage_ratio = 83.33%`
- `overfit_bull_bear_gap = 0.36`
- `eval_unique_trend_points = 3655`
- `eval_overlap_trend_points = 1246`
- `gate = 通过`

## 当前研究器增强

本次主线增强点已经落地：

- 主评分已经从 `Sortino` 切到 `trend_capture_v4`，核心目标改成抓大趋势，不再优化成“更平滑”。
- 切段口径已经放宽到更细的 `4h` 趋势段，约 `5%` 级别单边也算核心机会；当前全段会识别出约 `61` 段。
- `promotion_score` 现在改成只看 `validation` 连续结果；全段连续收益和全段趋势分只保留为诊断项。
- `validation` 连续路径会再按时间顺序切成 `3` 个分块，晋级会额外检查分块均值、波动、最差块和负分块数量。
- Discord 主表的收益字段已统一为 `全段连续 / 评估连续 / 验证连续` 同类口径。
- 默认 funding 数据源切到 Binance funding，并强制检查 `price / funding` venue 一致和窗口覆盖完整。
- prompt 开头会先强调这是 `15m` 执行、`1h + 4h` 确认的 BTC 激进趋势策略，要求模型优先理解策略目标而不是盲目微调参数。
- 切段使用唯一 `4h` 时间轴，并在回测结果中直接输出市场路径与策略权益路径，避免为了评分重复加载市场数据。
- prompt 第一屏现在先给模型看“方向风险表”，按方向簇聚合近期失败、零增益、运行报错和最佳 `promotion_delta`。
- prompt 第一屏会在方向风险表之后追加“过拟合风险表”，把高风险轮次单独标出来，提醒模型谨慎参考。
- 方向簇标签分为 `OPEN / WARM / ACTIVE_WINNER / SATURATED / EXHAUSTED / RUNTIME_RISK`，用于提示模型哪些方向已经接近被试空。
- 方向记忆现在统一使用 `cluster_key`：优先采用稳定的失败簇名；如果模型写的是临时近邻名字，就回退到系统识别的广义方向簇，避免同一方向仅因 tag 改名就被拆成新簇。
- 候选必须输出 `closest_failed_cluster` 与 `novelty_proof`，先解释与最近失败方向的差异，再允许进入评估。
- 若最近连续 3 轮都只是低变化零增益微调，prompt 会触发“探索轮”，强制要求切换因子家族或编辑区域家族，避免继续在同簇里换说法。
- 每轮先跑 `smoke` 窗口，运行报错会在同一轮走 repair loop；修复次数耗尽才记成 `runtime_failed`。
- `smoke` 抽样现在默认会覆盖 `validation`；在 `MACD_V2_SMOKE_WINDOW_COUNT=3` 时，当前逻辑默认抽“早期 eval + validation + 中段 eval”。
- `heartbeat` 会带上当前阶段和窗口索引，方便排查是卡在 `smoke_test`、`full_eval` 还是 `candidate_repairing`。
- 压缩历史除了标签统计外，也会保留方向簇摘要，避免 20 轮压缩后丢失长期失败记忆。
- compact 历史现在会写入 `score_regime`，prompt 只会读取当前评分口径下的 compact 摘要；旧版未标记口径的 compact 轮次默认跳过，防止长期历史污染。
- 提前淘汰改成了趋势捕获快照：窗口数足够、趋势段数足够且趋势捕获分和命中率都明显偏差时，才会提前结束这轮。
- 评估新增了过拟合风险诊断：会看单段正向贡献占比、同向连续段贡献占比、有效覆盖率、多空偏科和 eval/validation 落差；严重时直接 gate。

当前本地显式运行参数：

- `MACD_V2_EARLY_REJECT_WINDOWS=10`
- `MACD_V2_EARLY_REJECT_MIN_SEGMENTS=8`
- `MACD_V2_EARLY_REJECT_TREND_SCORE=-0.18`
- `MACD_V2_EARLY_REJECT_HIT_RATE=0.08`
- `MACD_V2_SMOKE_WINDOW_COUNT=3`
- `MACD_V2_MAX_REPAIR_ATTEMPTS=2`

对应当前 smoke 选窗行为：

- `评估1 2023-07-01 ~ 2023-07-28`
- `验证1 2025-03-01 ~ 2026-03-31`
- `评估15 2024-04-20 ~ 2024-05-17`

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

- 在更细的 `61` 段口径下，当前策略并没有“段数不够”的问题，问题是很多机会没抓到
- 双向趋势捕获已经转正，当前 gate 可以通过，但 `validation` 趋势捕获分仍明显弱于 `eval`
- `promotion_gap = 0.35`，已经贴近当前上限，说明这个 best 仍然存在明显的“前强后弱”特征
- `validation` 三分块里已有 `1` 个负分块，说明验证期内部还不算平滑稳定
- 当前 best 的过拟合风险分是 `20`，属于“观察”而不是“高/严重”，说明还没有明显依赖单一行情，但后续仍应继续盯多样性

后续研究重点应该继续放在：

- 多头大趋势的到来识别和陪跑能力
- 掉头时的退出/反手机制
- 通过 prompt 和记忆让模型减少在单一近邻方向里打转，同时避免把高风险轮次当作模板复用
