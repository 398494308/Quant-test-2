# Quant Test 3

这是一个独立的 `BTCUSDT` 永续合约激进趋势研究仓库。

当前主线只有一套：

- 策略文件：`src/strategy_macd_aggressive.py`
- 回测器：`src/backtest_macd_aggressive.py`
- 研究器：`scripts/research_macd_aggressive_v2.py`

仓库不再依赖旧的 `test1 / test2` 研究链路。

## 当前研究器做什么

研究器每轮会按下面的顺序执行：

1. 读取当前主参考策略。
2. 创建临时 workspace，把当前主参考策略复制到里面。
3. 把“当前诊断 + 方向风险表 + 方向冷却表 + 过拟合风险表 + 最近轮次摘要 + 压缩历史弱参考”喂给模型。
4. 模型只允许改 `src/strategy_macd_aggressive.py` 的可编辑区域，并在 workspace 里原地改文件。
5. 主进程校验候选只修改了允许区域。
6. 先做评估前硬约束检查；如果候选仍然是同簇低变化近邻，或命中锁簇，会在同一轮直接强制重生，而不是白跑评估。
7. 通过前置检查后，先跑少量 `smoke` 窗口；如果运行报错，会在同一轮进入 repair loop，而不是直接开始下一轮。
8. `smoke` 通过后，再跑整套 `development walk-forward + validation`。
9. 只有 `gate` 通过，且相对当前 `champion` 的 `promotion_delta` 至少高 `0.02`，才刷新当前主参考。
10. 如果当前还没有 `gate-passed champion`，而现有基线本身又没过 gate，那么第一条过 gate 的候选会直接晋升为新 `champion`。
11. 刷新 `champion` 之后，才会额外跑一次隐藏 `test`；它只做验收，不参与 `champion` 选择，也不会喂给模型。
12. 每轮结果都会写进 journal，包含 `accepted / rejected / duplicate_skipped / exploration_blocked / early_rejected / runtime_failed`。

## 当前评分口径

当前评分口径是 `trend_capture_v6`。

它分成三层：

- `development`
  `2023-07-01` 到 `2024-12-31`
  这里会生成滚动 walk-forward 窗口，用来检查稳定性。

- `validation`
  `2025-01-01` 到 `2025-12-31`
  这是模型可见的 holdout，也是唯一决定能不能刷新 `champion` 的主分。

- `test`
  `2026-01-01` 到 `2026-03-31`
  这是隐藏验收集，不参与调参，不进 prompt，只在新 `champion` 时播报。

单段评分还是：

- `period_score = 0.70 * trend_capture_score + 0.30 * return_score`

其中：

- `trend_capture_score`
  看三件事：到来时能不能及时跟上，主趋势中段能不能陪跑，掉头时能不能及时退出或反手。

- `return_score`
  看这条连续路径最终把资金放大了多少。

研究器里的两个主分现在是：

- `quality_score`
  开发期滚动窗口的均值分。

- `promotion_score`
  验证集单段连续分。

现在不会再把 hidden `test` 混进 `quality_score` 或 `promotion_score`，而且过拟合严重会直接触发 gate veto。

## 当前 gate

当前 gate 主要看这些：

- 开发期滚动均值分
- 开发期滚动中位分
- 开发期滚动波动
- 开发期盈利窗口占比
- 验证集趋势段数量
- 验证集命中率
- 验证集趋势捕获分
- 开发期和验证集的分数落差
- 验证集多头捕获
- 验证集空头捕获
- 验证集三分块稳健性
- 手续费拖累
- 验证集多空交易支持是否过弱

过拟合集中度诊断仍会继续计算，但不再只是提示项：

- 严重集中度会直接触发 gate 拒收
- 高风险轮次会在 journal 的过拟合风险表里持续降权

## Prompt 现在怎么组织

当前 prompt 的顺序是：

1. 策略目标
2. 思考框架
3. 当前诊断
4. 记忆使用规则
5. 历史研究记忆
6. 探索与防重复规则
7. 硬约束
8. 输出要求

现在的 prompt 有几个重要变化：

- 不再把整份策略源码塞进 prompt。
- memory rule 放在 journal 记忆前面，避免规则和记忆内容隔太远。
- 防重复约束只保留一份，不再在多个位置重复同一句规则。
- 模型可以看到 `validation` 的聚合诊断，但完全看不到 hidden `test`。
- prompt 会明确写出：只有 `gate` 通过且 `promotion_delta > 0.02` 才可能刷新当前 `champion`。
- `edited_regions` 现在只允许填 `1-3` 个，而且系统会用真实代码 diff / AST 派生的 `system signature` 复核，不再只信模型自报元信息。
- 最近轮次摘要拆成了“核心指标表 + 元信息摘要”，不再用超宽大表。

## 当前窗口配置

默认配置在 `config/research_v2.env`。

关键日期：

- `MACD_V2_DEVELOPMENT_START_DATE=2023-07-01`
- `MACD_V2_DEVELOPMENT_END_DATE=2024-12-31`
- `MACD_V2_VALIDATION_START_DATE=2025-01-01`
- `MACD_V2_VALIDATION_END_DATE=2025-12-31`
- `MACD_V2_TEST_START_DATE=2026-01-01`
- `MACD_V2_TEST_END_DATE=2026-03-31`

滚动窗口：

- `MACD_V2_EVAL_WINDOW_DAYS=28`
- `MACD_V2_EVAL_STEP_DAYS=21`

## 当前策略轮廓

当前策略仍然是：

- `15m` 是唯一事实源
- `1h + 4h` 只是由 `15m` 聚合出来的趋势确认层
- 横盘环境尽量少做；突破不只看总成交量，也会看主动买卖量、成交活跃度，以及 `1h/4h` 的 flow confirm
- 开仓后带 ATR 初始止损、保本、TP1、移动止损、趋势失效退出、时间退出
- 允许有限次加仓

数据下载脚本现在会先下载 `15m`，再由它派生 `1h` 和 `4h`。如果本地还是旧版 CSV，需要重新执行：

```bash
python3 scripts/download_aggressive_data.py
```

回测器当前已包含：

- `1m` 执行价近似
- 滑点
- 手续费
- 资金费
- 多并发仓位
- TP1 分批结算

交易统计口径已经是“整笔仓位”，不会再把 `TP1` 当成独立 trade。

研究器现在还带了新的防局部最优保护：

- 如果候选只是落在同一方向簇里的低变化近邻，系统会在评估前直接拦截
- 被拦截后不会立刻浪费下一轮，而是会在同一轮里强制重生候选
- 如果同一方向簇反复触发该问题，会进入短期冷却锁，默认是 `3 -> 6 -> 10` 轮递增
- 低变化近邻的判定不再主要靠 `change_tags / edited_regions` 自报，而会同时看真实 diff、参数族变化和 AST 派生的结构签名

## Discord 播报说明

Discord 主表现在优先显示收益相关，再显示验证集与选择期诊断。

核心字段的直白解释：

- `窗口`
  这轮一共跑了多少个开发窗口、多少个验证窗口。只有新 `champion` 才会额外显示隐藏测试窗口。

- `选择期连续收益`
  这是把 `development + validation` 这段时间真正连续跑 1 次后的总收益。

- `验证连续收益`
  这是把 `validation` 整段真正连续跑 1 次后的收益。

- `隐藏测试连续收益`
  只在新 `champion` 时出现。它不参与本次 `champion` 选择，只做最终验收。

- `开发窗口均值收益`
  所有开发窗口收益的平均值，只用于看训练期整体大概表现。

- `开发滚动分(均/中/std)`
  这是开发期滚动窗口分数的均值、中位数和波动。
  均值越高越好，中位数越高越好，`std` 越小越稳。

- `开发盈利窗占比`
  开发窗口里有多少比例是正分窗口。

- `验证晋级分`
  就是 `promotion_score`。这是比较候选与当前 `champion` 的主分，但前提仍然是先过 gate。

- `开发/验证分差`
  如果这个数很大，通常说明前面开发期很好，但验证集跟不上，泛化不足。

- `验证趋势/收益分`
  前一个看抓趋势的质量，后一个看资金放大的效果。

- `验证到来/陪跑/掉头`
  分别表示：上车速度、陪跑能力、掉头时的退出或反手能力。

- `验证多/空捕获`
  分别表示这套策略在验证期里对上涨段和下跌段抓得怎么样。

- `验证命中率/趋势段`
  前一个是抓到的大趋势比例，后一个是验证期一共识别出多少个趋势段。

- `验证多/空平仓数`
  看验证期是不是只靠单边方向偶然出结果。

- `验证分块均值/std`
  把验证期按时间顺序切成三块后，每块各算一个分。这里看平均值和块间波动。

- `验证最差块/负块数`
  最差那一块有多差，负分块有几个。

- `选择期趋势/收益分`
  这是 `development + validation` 连续跑一次后的趋势分和收益分，只做诊断，不直接决定 `champion`。

- `选择期集中度诊断`
  用来看结果是不是太依赖少数行情、同向链条过重或覆盖面太窄。

- `手续费拖累`
  手续费吃掉了多少本金比例。

## 运行状态

研究器会持续写 `state/research_macd_aggressive_v2_heartbeat.json`。

重点字段：

- `status`
  当前状态，比如 `model_waiting`、`iteration_running`、`candidate_repairing`、`new_champion`、`sleeping`。

- `phase`
  当前在哪个阶段，比如 `model_generate`、`model_repair`、`smoke_test`、`full_eval`、`selection_period_eval`、`hidden_test_eval`。

- `current_window`
  当前跑的是哪个窗口；连续回测会显示 `选择期连续` 或 `隐藏测试`。

- `elapsed_seconds / timeout_seconds`
  只在等模型返回时有意义，方便判断是 provider 慢，还是研究器卡在别的阶段。

## 常用命令

只评估当前策略：

```bash
python3 scripts/research_macd_aggressive_v2.py --no-optimize
```

只跑一轮研究：

```bash
python3 scripts/research_macd_aggressive_v2.py --once
```

持续运行研究器：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh start
```

查看状态：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh status
```

停止研究器：

```bash
bash scripts/manage_research_macd_aggressive_v2.sh stop
```

## 目录结构

```text
config/            研究配置、样板配置
data/              价格、情绪、资金费数据
docs/              当前状态说明
real-money-test/   freqtrade dry-run / live 壳子
scripts/           研究、分析、下载脚本
src/               策略、回测器、研究器模块
state/             运行状态、journal、主参考快照
tests/             最小回归测试
```
