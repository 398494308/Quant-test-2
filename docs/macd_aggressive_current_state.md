# MACD Aggressive Current State

## 当前主线

- 仓库：`test3`
- 研究器：`scripts/research_macd_aggressive_v2.py`
- 策略：`src/strategy_macd_aggressive.py`
- 回测器：`src/backtest_macd_aggressive.py`
- 当前评分口径：`trend_capture_v6`
- 当前事实源：`15m`
- `1h` / `4h` 只是由 `15m` 聚合出来的确认层
- 成交量维度除了总量，还包含 `trade_count`、`taker_buy_volume`、`taker_sell_volume`

## 当前时间切分

- `development`
  `2023-07-01` 到 `2024-12-31`

- `validation`
  `2025-01-01` 到 `2025-12-31`

- `test`
  `2026-01-01` 到 `2026-03-31`

当前默认是：

- `28` 天开发窗口
- `21` 天步长
- `development` 内做 walk-forward
- `validation` 和 `test` 都是单段连续窗口

## 当前评分结构

单段分数：

- `period_score = 0.70 * trend_capture_score + 0.30 * return_score`

研究器主分：

- `quality_score`
  开发期滚动窗口的均值分

- `promotion_score`
  验证集单段连续分；只有先过 gate，才会拿它和当前 `champion` 比较

隐藏验收：

- hidden `test` 只在新 `champion` 时评估
- hidden `test` 不参与 `quality_score`
- hidden `test` 不参与 `promotion_score`
- hidden `test` 不会进入 prompt 和记忆表

## 当前 gate

主 gate 现在重点看：

- 开发期滚动均值分
- 开发期滚动中位分
- 开发期滚动波动
- 开发期盈利窗口占比
- 验证集趋势段数
- 验证集命中率
- 验证集趋势捕获分
- 开发期与验证集的分差
- 验证集多头捕获
- 验证集空头捕获
- 验证分块稳健性
- 手续费拖累
- 验证期多空交易支持

过拟合集中度仍会诊断，但严重集中度会直接触发 gate veto；高风险轮次也会继续在 journal 里降权。

## 当前 prompt 结构

当前 prompt 顺序：

1. 策略目标
2. 思考框架
3. 当前诊断
4. 记忆使用规则
5. 历史研究记忆
6. 探索与防重复规则
7. 硬约束
8. 输出要求

当前 prompt 的关键点：

- 不再内嵌完整策略源码
- memory rule 与 journal 相邻
- validation 聚合诊断可见
- hidden test 完全不可见
- 最近轮次拆成“核心指标表 + 元信息摘要”
- journal 里新增 `方向冷却表（系统硬约束）`
- 防重复规则只保留一份，不再多处复写
- `edited_regions` 最多 `1-3` 个，系统会用真实 diff / AST 派生的 `system signature` 复核

## 当前 Discord 口径

Discord 现在优先播报：

- `选择期连续收益`
- `验证连续收益`
- 新 `champion` 时额外播报 `隐藏测试连续收益`

然后再播报：

- 开发滚动分
- 验证晋级分
- 开发/验证分差
- 验证到来/陪跑/掉头
- 验证多/空捕获
- 验证命中率/趋势段
- 验证多/空平仓数
- 验证三分块稳健性
- 选择期连续诊断

## 当前运行保护

- `smoke` 先跑少量窗口
- 候选报错时会在同一轮 repair
- 同簇低变化近邻会在评估前被系统拦截，不再白跑 `smoke/full eval`
- 被探索硬约束拦截后，会在同一轮里强制重生候选方向
- 同一方向簇再次触发该机制后，会进入短期冷却锁
- 冷却锁采用 `3 -> 6 -> 10` 轮递增
- 低变化近邻判定会同时看真实 diff、参数族变化和 AST 派生结构签名
- `duplicate source / duplicate hash / empty diff` 会写入 journal
- `exploration_blocked` 表示候选在评估前就被系统探索硬约束拒收
- heartbeat 会写出当前阶段和窗口名
- provider timeout 默认 `600s`

## 当前需要注意

- 这是一次新的评分 regime 切换，旧 `trend_capture_v4` 历史不会再作为主参考。
- 新 regime 下的 `champion / baseline` 会在研究器下一次初始化或下一轮运行后重新沉淀。
- 如果本地价格 CSV 还是旧格式，需要先重新运行 `python3 scripts/download_aggressive_data.py`，生成带 flow 列的新 `15m/1h/4h/1m` 数据。
- 如果要看现在的真实基线，请直接跑：

```bash
python3 scripts/research_macd_aggressive_v2.py --no-optimize
```
