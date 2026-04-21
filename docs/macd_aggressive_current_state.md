# MACD Aggressive Current State

## 当前主线

- 仓库：`test3`
- 研究器：`scripts/research_macd_aggressive_v2.py`
- 策略：`src/strategy_macd_aggressive.py`
- 回测器：`src/backtest_macd_aggressive.py`
- 当前评分口径：`trend_capture_v6`
- 当前事实源：`15m`
- `1h` / `4h` 只是由 `15m` 聚合出来的确认层
- 默认交易所数据源：`OKX`
- 成交量维度除了总量，还会从 OKX K 线推导方向流量代理和成交活跃度
- 当前因子模式：`default`

## 最新手工基底

- 更新时间：`2026-04-20`
- 当前已持久化到：
  - `src/strategy_macd_aggressive.py`
  - `backups/strategy_macd_aggressive_v2_best.py`
  - `state/research_macd_aggressive_v2_best.json`
- `2026-04-20` 已完成一轮真正的“重新拆基底”：
  - `PARAMS` 收到一版核心阈值集合，删除了 `launch_*` 和一组只服务于局部微路径的 `long_*` 专用参数
  - `strategy()` 从超长条件链改成命名规则块编排
  - long 侧现在拆成 `long_outer_context_ok / long_breakout_ok / long_pullback_ok / long_trend_reaccel_ok / long_final_veto_clear`
  - short 侧现在拆成 `short_outer_context_ok / breakdown_ready / short_breakdown_ok / short_bounce_fail_ok / short_trend_reaccel_ok / short_final_veto_clear`
  - `_trend_quality_ok()` 和 `_trend_followthrough_ok()` 现在只保留分发职责，具体逻辑下沉到 long/short 侧 helper
  - `src/strategy_macd_aggressive.py` 和 `backups/strategy_macd_aggressive_v2_best.py` 当前已同步，且都能通过源码校验
- 当前这版 clean 基底还没有重新跑完整 `train/val/test` 评估；旧基底的历史分数不能再直接视为这版新结构的当前结果。
- 研究器已先手动停掉，避免后台进程把旧 champion 再次覆盖到 `src/strategy_macd_aggressive.py`。
- 当前 `train/val` funding 覆盖率是 `0%`。原因不是研究器没开 funding，而是 OKX 公共 funding 历史拿不到这段旧窗口；缺失区间当前按 `0 funding` 回测，并在评估摘要里直接显示覆盖率。

这版新基底的手工方向不是“继续堆更多 long”，而是：

- 先把策略从“堆条件”拉回到“少数清晰路径”
- 保留 volume / trade_count / taker buy-sell / flow imbalance 这些真实在用的数据
- 把无用的策略接口噪音拿掉；`sentiment` 不再传进策略 `market_state`
- 用更干净的基底重新起跑，让研究器围绕可解释的规则块继续找解

## 当前时间切分

- `train`
  `2023-07-01` 到 `2024-12-31`

- `val`
  `2025-01-01` 到 `2025-12-31`

- `test`
  `2026-01-01` 到 `2026-03-31`

当前默认是：

- `28` 天 train 窗口
- `21` 天步长
- `train` 内做 walk-forward
- `val` 和 `test` 都是单段连续窗口

## 当前评分结构

单段分数：

- `period_score = 0.70 * trend_capture_score + 0.30 * return_score`

研究器主分：

- `quality_score`
  train 滚动窗口的均值分

- `promotion_score`
  val 单段连续分；只有先过 gate，才会拿它和当前 `champion` 比较

test 验收：

- `test` 只在新 `champion` 时评估
- `test` 不参与 `quality_score`
- `test` 不参与 `promotion_score`
- `test` 不会进入 prompt 和记忆表

## 当前 gate

主 gate 现在重点看：

- train 滚动均值分
- train 滚动中位分
- val 命中率
- val 趋势捕获分
- train 与 val 的分差
- val 多头捕获
- val 空头捕获
- val 分块最差块和负分块数量
- 手续费拖累

这些现在只做诊断，不再直接卡 gate：

- train 滚动波动
- train 盈利窗口占比
- val 趋势段数
- val 分块波动
- val 多空交易支持

过拟合集中度仍会诊断，但只有严重集中度会直接触发 gate veto；高风险轮次会继续在 journal 里降权。

## 当前 prompt 结构

当前 prompt / session 已改成 `4` 层：

1. workspace 局部 `AGENTS.md`
   - 只放稳定项目上下文：项目目标、文件职责、允许修改边界、输出规则、复杂度预算、持久 session 规则
2. `runtime prompt`
   - 只放本轮动态信息，但顺序改成：刷新条件与目标 -> 本轮阅读顺序 -> 当前诊断摘要 -> 本轮执行框架
3. `history package`
   - 当前 `stage` 执行摘要
   - 当前 `stage` 失败核聚合
   - 当前 `stage` 风险/冷却/过热/过拟合附录
   - 历史 `stage` 压缩摘要
   - 当前评分口径全局统计表
   - 旧评分口径弱参考
4. `failure wiki`
   - markdown 给模型读
   - json 给系统 guard 读

当前 prompt 的关键点：

- 不再内嵌完整策略源码
- 稳定规则已经从每轮 system prompt 挪到 workspace 局部 `AGENTS.md`，只作用于 `state/research_macd_aggressive_v2_agent_workspace/`
- 当前 `baseline/champion stage` 内会复用同一个 Codex session；只有在刷新 `champion` 或重建 `baseline` 时，才会清掉旧 session 并新开一个
- `runtime prompt` 不再重复塞大量稳定约束，注意力更集中在本轮目标、当前诊断和真实 choke point
- `runtime prompt` 明确要求先看刷新条件和当前诊断，再看 `wiki/latest_history_package.md` 与 `wiki/failure_wiki.md` 的前部摘要
- 形成假设后必须回看一次 `failure wiki` 自检；命中相同或高度相似的失败 cut 时，要求在同一轮内改写
- 默认因子模式下，prompt 明确禁止新增 `PARAMS` 键；顶层常量和顶层 helper 只允许少量新增，用来做结构化抽离，不能借机堆新因子
- `history package` 不再按“最近 N 轮”裁切，而是按“自当前 `baseline/champion` 激活以来”的 `current stage`
- 相同 gate reason 不再逐轮重复广播，而是会先折叠成“失败核”；重复失败被视为同一个坏盆地
- 新增 `failure wiki`：一份 markdown 给模型查阅，一份 json 给系统 guard 做 exact cut 拦截；同一 cut 连续失败后，会在评估前先挡回去重生
- prompt/history 不再展示“最近动态核心因子 / 全局高频核心因子”，只保留方向簇、失败标签、改动区域和真实结果；原始 `core_factors` 仍保留在 raw archive
- 当多空捕获明显失衡时，prompt 会追加“软偏置”提示，优先把探索预算投向更弱的一侧，而不是硬性锁死只看单边
- `test` 完全不可见
- `runtime prompt` 现在会附带当前基底最紧张的 complexity headroom，明确显示哪个 family / function 还剩多少 `lines / bool_ops / ifs`
- 当前 `stage` 只在 prompt 中展示最近有限条表格和元信息，但它们现在后置到摘要之后；完整 `stage` 已另存到 memory 目录
- journal 里新增 `方向冷却表（系统硬约束）`
- 防重复规则只保留一份，不再多处复写
- 如果候选在 smoke 窗口上的行为完全不变，系统会在同一轮回灌 smoke 摘要并强制重生
- 如果候选在源码校验阶段就因为复杂度预算或复杂度增量超限被拒，系统也会先做同轮 repair；仍修不动才把这轮记进 journal 和记忆包
- ordinary family 不再有硬数量下限；`strategy-only / PARAMS-only` 现在允许进入后续流程，是否算有效探索改由真实 diff、smoke/noop、结果盆地重复和复杂度预算共同决定
- `behavioral_noop` 回灌现在会明确指出候选真实改动区域、普通 family、目标侧，以及当前该优先看的外层 choke point：`long_outer_context_ok / long_final_veto_clear / short_outer_context_ok / short_final_veto_clear`
- prompt 里的可编辑区域已切到真实存在的命名规则块，能直接改 `sideways / flow / trend_quality / followthrough / long_entry / short_entry / strategy`

当前基底的复杂度余量也已重新留白：

- `trend_quality_family` 现在约为 `116 / 130 lines`、`36 / 42 bool_ops`
- 也就是大约还剩 `14 lines`、`6 bool_ops`
- 这次瘦身只删减了已有门槛，没有改动整体函数结构和命名规则块

## 当前 Discord 口径

Discord 现在只保留：

- `数据范围`
- `本轮窗口`
- `因子模式`
- `train+val期间收益`
- `val期间收益`
- 新 `champion` 时额外播报 `test期间收益`
- `Sharpe(train / val / test)`
- `train+val交易数量`
- 新 `champion` 时额外播报 `test交易数量`
- `val多/空捕获`
- `最大回撤/手续费拖累`

## 当前运行保护

- `smoke` 默认先跑 `5` 个窗口，当前会取早 train / val / 中前段 train / 中段 train / 尾段 train
- `smoke` 通过后，还会比对候选和当前参考在 smoke 窗口里的行为指纹
- 如果收益、交易数、信号统计、退出原因和交易摘要完全一致，不会立刻结束本轮，而是把 smoke 摘要回灌给模型，在同一轮强制重生候选
- 只有连续重生后仍然无法改变 smoke 行为，才会正式记一次 `behavioral_noop`
- 每条 journal 现在都会同步写入 `state/research_macd_aggressive_v2_memory/raw/`
- memory 目录里会额外维护：
  - `raw/full_history.jsonl`
  - `raw/rounds/*.json`
  - `summaries/current_stage_rounds.json`
  - `summaries/past_stage_summaries.json`
  - `summaries/all_time_tables.json`
  - `prompt/latest_history_package.md`
- 失败 wiki 会额外维护：
  - `wiki/failure_wiki.md`
  - `wiki/failure_wiki_index.json`
- 当前 stage session 会额外维护：
  - `state/research_macd_aggressive_v2_session.json`
  - `state/research_macd_aggressive_v2_agent_workspace/`
- `best_state` 现在会持久化 `reference_stage_started_at/reference_stage_iteration`，重启后不会把当前 stage 切乱
- 当前基底策略里，研究器被显式引导优先检查外层总闸门和最终 veto 链，而不是继续把注意力浪费在未触达真实出单层的局部 helper 上
- 候选报错时会在同一轮 repair
- 如果候选命中 failure wiki 已封死的 exact cut，会在完整评估前直接被拦回去同轮重生
- 同簇低变化近邻会在评估前被系统拦截，不再白跑 `smoke/full eval`
- 连续 `behavioral_noop`、重复结果盆地和复杂度连撞都会被当成 stall，后续若继续沿同簇近邻试错，会更早触发放宽或拦截
- 被探索硬约束拦截后，会在同一轮里强制重生候选方向
- 同一方向簇再次触发该机制后，会进入短期冷却锁
- 冷却锁采用 `3 -> 6 -> 10` 轮递增
- 低变化近邻判定会同时看真实 diff、参数族变化和 AST 派生结构签名，不再优先相信模型自报的最近失败簇
- `duplicate source / duplicate hash / empty diff / behavioral_noop / duplicate result basin` 都会写入 journal
- `exploration_blocked` 表示候选在评估前就被系统探索硬约束拒收
- prompt 最近轮次表只展示最近有限条，避免长串重复 noop 淹没当前硬约束；`behavioral_noop` 未跑完整评估的指标也不再显示成伪 `0.00`
- heartbeat 会写出当前阶段和窗口名
- provider timeout 默认 `600s`

## 当前需要注意

- 这是一次新的评分 regime 切换，旧 `trend_capture_v4` 历史不会再作为主参考。
- 新 regime 下的 `champion / baseline` 会在研究器下一次初始化或下一轮运行后重新沉淀。
- 如果本地价格 CSV 还是旧格式，需要先重新运行 `python3 scripts/download_aggressive_data.py`，生成新的 `OKX 15m/1h/4h/1m` 数据。
- OKX 公共 funding 历史当前只能覆盖较近日期；如果请求窗口更早，下载脚本会保留可得 funding，并由回测器把缺口按 `0 funding` 继续跑，不再因为 funding 文件覆盖不足而整轮失败。
- `scripts/research_macd_aggressive_v2.py` 的启动路径会把 `src/strategy_macd_aggressive.py` 从已保存 best 恢复回来。
- 所以如果只是想安全评估当前 `src`，不要直接跑 `--no-optimize`，请用下面这段：

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

- 本轮已在 `2026-04-19` 做过一次彻底历史清理：
  - `state/research_macd_aggressive_v2_journal.jsonl` 已清空
  - `state/research_macd_aggressive_v2_journal.compact.json` 已清空
  - `scripts/reset_research_macd_aggressive_v2_state.sh` 现在也会把 `state/research_macd_aggressive_v2_memory/`、`state/research_macd_aggressive_v2_session.json`、`state/research_macd_aggressive_v2_agent_workspace/` 一并归档
  - 原文件已按时间戳归档
