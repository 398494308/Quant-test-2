# Quant Test 3

这是一个独立的 `OKX BTC-USDT-SWAP` 激进趋势研究仓库。

当前主线只有一套：

- 策略文件：`src/strategy_macd_aggressive.py`
- 回测器：`src/backtest_macd_aggressive.py`
- 研究器：`scripts/research_macd_aggressive_v2.py`

仓库不再依赖旧的 `test1 / test2` 研究链路。

## 最新基底状态

`2026-04-19` 已手工重做过一版新的 `baseline`，并已经持久化到：

- `src/strategy_macd_aggressive.py`
- `backups/strategy_macd_aggressive_v2_best.py`
- `state/research_macd_aggressive_v2_best.json`

`2026-04-20` 已按 `OKX` 默认数据重评一次当前基底，当前有效结果是：

- `gate=通过`
- `quality_score=0.32`
- `promotion_score=0.25`
- `train+val期间收益=139.48%`
- `val期间收益=10.06%`
- `val多/空捕获=0.17 / 0.41`
- `最大回撤/手续费拖累=34.47% / 2.80%`

注意：

- `2026-04-19` 之后，回测与研究默认数据已切到 `OKX`，旧的 Binance 口径评估值不再作为当前有效指标。
- OKX 公共 funding 历史目前拿不到 `train/val` 这整段，因此当前 `train/val` 评估的 funding 覆盖率是 `0%`；缺失区间按 `0 funding` 回测，并在评估摘要里明确标注。

## 当前研究器做什么

研究器每轮会按下面的顺序执行：

1. 读取当前主参考策略。
2. 进入阶段级持久 workspace：`state/research_macd_aggressive_v2_agent_workspace/`。这里有局部 `AGENTS.md`、可写策略副本、只读 `wiki/` 与 `memory/` 链接。
3. 同一 `baseline/champion stage` 内会复用同一个 Codex session；只有当 `champion` 或 `baseline` 刷新时，才会清掉旧 session 并新开一个。
4. 每轮开始先把当前主参考重新写回 workspace 里的 `src/strategy_macd_aggressive.py`，避免上一轮残留源码污染新一轮。
5. 动态 prompt 只补“当前诊断 + 本轮目标 + 本轮失败反馈”；稳定规则放进 workspace 局部 `AGENTS.md`，长历史放进 `wiki/latest_history_package.md` 与 `wiki/failure_wiki.md`。
6. 模型只允许改 `src/strategy_macd_aggressive.py` 的可编辑区域，并在 workspace 里原地改文件。
7. 主进程校验候选只修改了允许区域。
8. 先做评估前硬约束检查；如果候选命中 failure wiki 已封死的 exact cut、仍然是同簇低变化近邻、命中锁簇，或根本没有产出真实源码改动，会在同一轮直接强制重生，而不是白跑评估。
9. 通过前置检查后，先跑少量 `smoke` 窗口；如果运行报错，会在同一轮进入 repair loop，而不是直接开始下一轮。
10. `smoke` 通过后，主进程还会对比候选和当前参考在 smoke 窗口里的行为指纹；如果收益、交易数、信号统计、退出原因和交易摘要完全一致，不会立刻结束本轮，而是把 smoke 摘要回灌给模型，在同一轮里强制重生候选。
11. 只有连续重生后仍然无法改变 smoke 行为，才会正式记一次 `behavioral_noop`。
12. 只有 smoke 行为真的变了，才会继续跑整套 `train walk-forward + val`。
13. 只有 `gate` 通过，且相对当前 `champion` 的 `promotion_delta` 至少高 `0.02`，才刷新当前主参考。
14. 如果当前还没有 `gate-passed champion`，而现有基线本身又没过 gate，那么第一条过 gate 的候选会直接晋升为新 `champion`。
15. 刷新 `champion` 之后，研究器会清掉旧 session / workspace 上下文，并为下一个 stage 准备新 session。
16. 刷新 `champion` 之后，才会额外跑一次隐藏 `test`；它只做验收，不参与 `champion` 选择，也不会喂给模型。
17. 每轮结果都会写进 journal，包含 `accepted / rejected / duplicate_skipped / behavioral_noop / exploration_blocked / early_rejected / runtime_failed / generation_invalid`。
18. 如果候选交回了完整 JSON 但没有真实代码改动，研究器不会刷新 session；它会留在同一个 session 里继续重生，并把下一版任务压缩成“先确定单一策略方向，再把这个方向落到代码里”。

## 当前评分口径

当前评分口径是 `trend_capture_v6`。

它分成三层：

- `train`
  `2023-07-01` 到 `2024-12-31`
  这里会生成滚动 walk-forward 窗口，用来检查稳定性。

- `val`
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
  train 滚动窗口的均值分。

- `promotion_score`
  val 单段连续分。

现在不会再把 `test` 混进 `quality_score` 或 `promotion_score`，而且过拟合严重会直接触发 gate veto。

## 当前 gate

当前 gate 主要看这些：

- train 滚动均值分
- train 滚动中位分
- val 命中率
- val 趋势捕获分
- train 和 val 的分数落差
- val 多头捕获
- val 空头捕获
- val 三分块里的最差块和负块数量
- 手续费拖累

这些现在只做诊断，不再直接卡 gate：

- train 滚动波动
- train 盈利窗口占比
- val 趋势段数量
- val 分块波动
- val 多空交易支持是否偏弱

过拟合集中度诊断仍会继续计算，但不再只是提示项：

- 严重集中度会直接触发 gate 拒收
- 高风险轮次会在 journal 的过拟合风险表里持续降权

## Prompt 现在怎么组织

当前 prompt 现在拆成四层：

1. workspace 局部 `AGENTS.md`
2. runtime prompt
3. `wiki/latest_history_package.md`
4. `wiki/failure_wiki.md`

现在的 prompt 有几个重要变化：

- 不再把整份策略源码塞进 prompt。
- 稳定规则已经从每轮 system prompt 挪到 workspace 局部 `AGENTS.md`，只影响研究器自己的工作区，不会污染仓库根目录环境。
- runtime prompt 现在只放本轮动态信息，并显式写出“先目标 -> 再现状 -> 再 failure wiki / history package -> 再提出单一假设 -> 再回看 failure wiki 自检”的阅读顺序。
- `evaluation` 的 prompt 摘要不再平铺全部指标，而是先给 `gate` 主失败项、弱侧、`val` 漏斗堵点和当前风险/成本摘要。
- `history package` 不再先摆方向风险表，而是先给 `stage` 执行摘要和失败核聚合；相同 gate reason 会先折叠成同一个“坏盆地”。
- 失败信息新增成独立 `failure wiki`：一份 markdown 给模型读，一份 json 给系统 guard 读；同一 exact cut 连续失败后，会在评估前直接被挡回去重生。
- “未执行代码改动 / blocked / no_edit / no_change / sandbox_blocked” 这类占位结果现在被显式视为非法提交；辅助记忆缺失时也必须退化为直接修改当前策略源码。
- 防重复约束只保留一份，不再在多个位置重复同一句规则。
- 模型可以看到 `val` 的聚合诊断，但完全看不到 `test`。
- prompt 会明确写出：只有 `gate` 通过且 `promotion_delta > 0.02` 才可能刷新当前 `champion`。
- 如果候选在 smoke 窗口上的行为完全不变，系统会在同一轮回灌 smoke 摘要并强制重生，而不是直接白白结束整轮。
- 如果候选在源码校验阶段就因为复杂度预算或复杂度增量超限被拒，系统也会先做同轮 repair；修不动才把这类失败写进当前 `stage` 记忆。
- runtime prompt 会额外显示当前基底最紧张的 complexity headroom，让模型先看到哪个 family / function 已经贴边。
- `edited_regions` 不再硬卡 `1-3`，也不再要求“至少改到 1 个 ordinary family”；`strategy-only / PARAMS-only` 也允许进入后续流程，是否算有效探索改由真实 diff / smoke / 结果盆地重复 / complexity 共同决定。
- prompt 里的可编辑区域已经扩成真实存在的命名规则块，允许模型直接动 `sideways / flow / trend_quality / followthrough / long_entry / short_entry / strategy` 这些结构块。
- 最近轮次摘要仍保留“核心指标表 + 元信息摘要”，但在它前面新增了 `stage` 执行摘要和失败核聚合，逐轮表只作为附录。

## 当前窗口配置

默认配置在 `config/research_v2.env`。

关键日期：

- `MACD_V2_DEVELOPMENT_START_DATE=2023-07-01`
- `MACD_V2_DEVELOPMENT_END_DATE=2024-12-31`
- `MACD_V2_VALIDATION_START_DATE=2025-01-01`
- `MACD_V2_VALIDATION_END_DATE=2025-12-31`
- `MACD_V2_TEST_START_DATE=2026-01-01`
- `MACD_V2_TEST_END_DATE=2026-03-31`
- `MACD_V2_SMOKE_WINDOW_COUNT=5`

滚动窗口：

- `MACD_V2_EVAL_WINDOW_DAYS=28`
- `MACD_V2_EVAL_STEP_DAYS=21`

## 当前策略轮廓

当前策略仍然是：

- `15m` 是唯一事实源
- `1h + 4h` 只是由 `15m` 聚合出来的趋势确认层
- 横盘环境尽量少做；突破不只看总成交量，也会看基于 OKX K 线推导的方向流量代理、成交活跃度，以及 `1h/4h` 的 flow confirm
- 开仓后带 ATR 初始止损、保本、TP1、移动止损、趋势失效退出、时间退出
- 允许有限次加仓

数据下载脚本现在会直接下载 `OKX 15m / 1m`，再由 `15m` 派生 `1h / 4h`。如果本地还是旧版 CSV，需要重新执行：

```bash
python3 scripts/download_aggressive_data.py
```

说明：

- `15m/1m` 价格数据和 `funding` 都来自 `OKX`。
- 若 OKX 公共 funding 历史无法覆盖请求起点，脚本会打印提示；旧窗口会在回测里按 `0 funding` 继续运行，而不是直接报错中断研究器。

注意：

- 研究器启动时会把 `src/strategy_macd_aggressive.py` 从已保存 best 恢复回来。
- 所以如果你只是想看当前 `src` 的真实评估，不要直接跑 `python3 scripts/research_macd_aggressive_v2.py --no-optimize`。
- 安全做法是直接调用评估函数：

```bash
python3 - <<'PY'
from pathlib import Path
import sys, importlib
sys.path.insert(0, str(Path('.').resolve()))
sys.path.insert(0, str(Path('src').resolve()))

import strategy_macd_aggressive as sm
import scripts.research_macd_aggressive_v2 as rs

importlib.reload(sm)
rs.strategy_module = sm
report = rs.evaluate_current_strategy()
print(report.summary_text)
PY
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
- 连续 `behavioral_noop`、重复结果盆地和复杂度连撞都会进入 stall 上下文，后续若仍沿同簇近邻试错，会更快触发放宽或拦截并进入冷却锁
- 被拦截后不会立刻浪费下一轮，而是会在同一轮里强制重生候选
- 如果同一方向簇反复触发该问题，会进入短期冷却锁，默认是 `3 -> 6 -> 10` 轮递增
- 低变化近邻的判定不再主要靠 `closest_failed_cluster / change_tags / edited_regions` 自报，而会同时看真实 diff、参数族变化、AST 派生结构签名和结果盆地重复
- `smoke` 默认覆盖 `5` 个窗口，当前会取早 train / val / 中前段 train / 中段 train / 尾段 train
- `smoke` 现在还会比较行为指纹；如果候选和当前参考的 smoke 交易行为完全一致，会先在同一轮回灌 smoke 摘要并强制重生，只有连续重生后仍不变化才记 `behavioral_noop`
- `duplicate source / empty diff / generation_invalid` 这类“技术空转”会写入 raw history，但会从 failure wiki、方向风险表、结果盆地和过热统计里隔离出去，避免污染真正的失败记忆
- prompt 的最近轮次表现在只展示最近有限条，避免长串重复 noop 淹没当前硬约束；`behavioral_noop` 未跑完整评估的指标也不再伪装成 `0.00`
- 当前 stage 的 Codex session 会持久化到 `state/research_macd_aggressive_v2_session.json`
- 研究器独立工作区在 `state/research_macd_aggressive_v2_agent_workspace/`
- `reset_research_macd_aggressive_v2_state.sh` 现在会把 journal/memory 之外的 session 文件和 agent workspace 也一并归档

## Discord 播报说明

Discord 主表现在只保留最核心字段：

核心字段的直白解释：

- `数据范围`
  固定显示 train / val / test 各自用了哪段时间。

- `本轮窗口`
  显示这轮实际怎么评估：train 是滚动窗口，val 是连续窗口，test 只在新 `champion` 时才跑。

- `train+val期间收益`
  这是把 `train + val` 整段真正连续跑 1 次后的总收益。

- `val期间收益`
  这是把 `val` 整段真正连续跑 1 次后的收益。

- `test期间收益`
  只在新 `champion` 时出现。它不参与本次 `champion` 选择，只做最终验收。

- `train+val交易数量`
  这是 `train + val` 连续回测里的总交易数。

- `test交易数量`
  只在新 `champion` 时出现，表示 test 连续回测里的总交易数。

- `val多/空捕获`
  分别表示这套策略在 val 里对上涨段和下跌段抓得怎么样。

- `最大回撤/手续费拖累`
  前一个看这轮最糟时资金回撤有多深，后一个看手续费吃掉了多少本金比例。

## 运行状态

研究器会持续写 `state/research_macd_aggressive_v2_heartbeat.json`。

重点字段：

- `status`
  当前状态，比如 `model_waiting`、`iteration_running`、`candidate_repairing`、`new_champion`、`sleeping`。

- `behavioral_noop`
  候选能运行，但 smoke 交易行为和当前参考完全一致，系统会直接跳过完整评估。

- `phase`
  当前在哪个阶段，比如 `model_generate`、`model_repair`、`smoke_test`、`full_eval`、`selection_period_eval`、`hidden_test_eval`。

- `current_window`
  当前跑的是哪个窗口；连续回测会显示 `train+val连续` 或 `test连续`。

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

如果你刚手工换了当前基底，想让研究器忽略旧的已保存参考并从 `src/strategy_macd_aggressive.py` 重新初始化一次：

```bash
python3 scripts/research_macd_aggressive_v2.py --reset-champion --no-optimize
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
