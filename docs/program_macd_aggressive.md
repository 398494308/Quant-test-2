# 激进版 MACD 策略优化目标

## 交易框架
- 标的：BTCUSDT 永续合约
- 杠杆基准：14x
- 执行周期：15分钟
- 趋势过滤周期：1小时 + 4小时
- 当前有效信号是 `long_breakout` 与 `short_breakdown`
- 默认最多 4 个并发仓，弱趋势时会自动降到 2-3 个
- 单仓目标保证金占账户约 17%，弱趋势时会自动缩仓
- 单仓保证金下限 5000 USDT
- 单仓保证金上限 30000 USDT

## 策略目标
- 目标不是平滑，而是提高月度爆发力
- 接受更高回撤和一定爆仓风险
- 尽量抓住大级别趋势段，让利润跑得更久
- breakout 负责抓趋势启动
- breakdown 负责抓空头加速段

## 允许的激进方向
- 微调 breakout / breakdown 的节奏参数，不重写整套信号结构
- 优先优化入场质量、趋势门槛与加仓节奏，不再优先微调 MACD 细枝末节
- 允许适度放宽或收紧 1h / 4h 趋势过滤，但不要把趋势约束直接拆掉
- trailing 允许优化，但不要为了单个窗口收益把利润保护放得过松
- 保持较高杠杆和较高仓位利用率
- 允许金字塔加仓，但弱趋势环境应减少并发、延后加仓或直接停加仓

## 本轮可调参数范围

研究循环当前只允许修改 13 个核心参数：

- 入场 7 个：
  - `hourly_adx_min`
  - `breakout_adx_min`
  - `breakdown_adx_min`
  - `breakout_lookback`
  - `breakdown_lookback`
  - `breakout_rsi_max`
  - `breakout_volume_ratio_min`
- 出场 6 个：
  - `leverage`
  - `position_fraction`
  - `breakout_stop_atr_mult`
  - `breakout_trailing_activation_pct`
  - `breakout_trailing_giveback_pct`
  - `pyramid_trigger_pnl`

其余参数默认不动。

## 验证要求
- 评估窗口按连续时间块顺序切分，块长取当前 `MACD_EVAL_CHUNK_DAYS`
- 除最后 `2` 块留出窗口外，其余窗口统一作为 Walk-Forward `eval` 窗口
- 默认评估日期范围是 `2025-09-01` 到 `2026-03-31`
- 选优先看全部 `eval` 窗口的综合表现，不再拆成训练 / 验证两层
- 留出窗口只用于外层晋级拦截，不作为优化提示的一部分
- 同时记录：
  - 最大回撤
  - 爆仓次数
  - 窗口收益分布
  - 手续费拖累
- 不要求像保守版那样压低波动，但不能长期靠少数异常窗口撑分

## 约束
- 优先改参数，不要重写整体框架
- 每轮默认只做局部微调，不要同时改太多键
- 不要把交易数压到过低
- 不要在低 ADX / 高 chop / 低 spread 的环境里继续维持满仓激进开火
- 不要为了单月暴冲而完全牺牲后续月份
- 保持激进风格，不要把它改成低杠杆、低波动、保守止盈版本
- 杠杆尽量维持在 14x 以上
- `breakout_tp1_pnl_pct` 尽量维持在 42% 以上
- 评估窗口正收益占比需维持在合理区间，不接受大多数窗口亏损只靠少数窗口暴赚撑分
