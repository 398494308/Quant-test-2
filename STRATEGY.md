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
- 评分核心是 `trend_capture_v6 + gate + overfit veto`

当前主评分口径：

- 先在唯一 `4h` 市场路径上识别 BTC 的大趋势段
- 当前切段已经放宽到更细的 `4h` 趋势段，约 `5%` 级别单边也算核心机会；按当前全段数据大约会识别出 `61` 段
- 每段拆成 `到来 / 陪跑 / 掉头`
- `trend_capture_score` 看的是：能否及时跟上、能否陪跑主趋势、能否在掉头时跑掉或反手
- `return_score` 看的是整条路径最终把资金放大了多少
- `quality_score = 0.70 * eval_trend_capture_score + 0.30 * eval_return_score`
- `promotion_score = 0.70 * validation_trend_capture_score + 0.30 * validation_return_score`
- `promotion_score` 只负责和当前 `champion` 比较，要求至少比当前 `champion` 高 `0.02`
- `validation` 连续路径会再按时间顺序切成 `3` 个分块；分块波动过大、最差块过弱或负分块太多时，即使总分过线也不会刷新 `champion`
- 全段连续收益和全段趋势分保留为诊断项，不再直接决定 `champion`
- 爆仓和回撤保留为诊断项，但不再是主评分，也不再单独惩罚
- 选择期过拟合集中度如果达到严重阈值，会直接触发 gate veto，不再只是提示项

当前研究器的研究记忆与修复链路：

- prompt 现在拆成 `system prompt + runtime prompt + history package` 三层。
- `system prompt` 只放稳定信息：项目目标、文件职责、编辑边界、输出规则。
- `runtime prompt` 只放本轮动态信息：当前诊断、当前口径 gate、本轮执行框架和行为无变化约束。
- `history package` 改成按当前主参考的 `stage` 来组织，而不是固定“最近 N 轮”。
- 当前 `stage` 的边界来自 `best_state` 里的 `reference_stage_started_at / reference_stage_iteration`；研究器重启后不会把当前 `baseline/champion` 阶段切乱。
- prompt 第一屏仍先显示方向风险表，按当前 `stage` 的方向簇聚合失败、零增益和运行报错。
- 若最近一段历史里同一方向簇占比过高且没有实质正 `delta`，prompt 会在方向风险表后追加“主簇过热”提示，默认要求下一轮优先跨簇。
- 当前 `stage` 只在 prompt 里展示最近有限条表格和元信息，避免长串 noop 淹没当前硬约束；完整 `stage` 与全量原始历史会单独落到 memory 目录。
- 旧评分口径不会混进当前主表；它们只会在后面以“旧评分口径弱参考”出现，作为低优先级方向启发。
- 老 `stage` 不会丢失，会被压缩成 `历史 stage 摘要`；同时还会生成当前评分口径的 `全局方向统计`，给模型看跨 stage 的长期模式。
- 每条 journal 都会同步归档到 `state/research_macd_aggressive_v2_memory/raw/full_history.jsonl` 和 `raw/rounds/*.json`。
- 防重复探索不再主要靠 prompt 自报；系统会结合真实 diff、参数族变化和 AST 派生结构签名做硬拦截。
- 候选必须输出 `closest_failed_cluster` 与 `novelty_proof`，说明本轮为什么不是重复试错。
- 如果仍想留在“主簇过热”的方向簇里，`novelty_proof` 必须同时说明不同交易路径、至少两个会明显变化的关键诊断，以及为什么这不是只换 tag 或轻微阈值。
- 若最近连续 3 轮都属于低变化轮次，prompt 会强制把下一轮视为“探索轮”，优先切换因子家族或编辑区域家族。
- ordinary family 现在只保留“至少改 `1` 个”的下限；`1-3` 仍是 prompt 里的默认软引导，但连续 `behavioral_noop` 后允许直接覆盖更多 family。系统只把真实 diff 派生出来的 changed regions 当成主依据。
- 研究器的 `behavioral_noop` 反馈会直接点名当前该优先检查的 choke point：长侧优先看 `long_outer_context_ok` 与 `long_final_veto_clear`，空侧优先看 `short_outer_context_ok` 与 `short_final_veto_clear`。
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
