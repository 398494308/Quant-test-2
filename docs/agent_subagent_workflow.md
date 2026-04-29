# 研究器 SOP

这份文档是当前 GitHub 内的主 SOP，描述研究器从启动、出方案、落码、评估、刷新 champion 到人工介入的完整流程。

## 一图看懂

```mermaid
flowchart TB
    A[启动研究器] --> B[加载 active reference<br/>baseline 或 champion]
    B --> C[准备当前 stage 记忆<br/>journal / wiki / direction_board]
    C --> D[检查人工卡<br/>operator_focus 长期软引导<br/>champion_review hash 命中才生效]

    D --> E[planner 持久 session<br/>读人工卡 / reviewer 卡 / 方向账本 / 前台记忆]
    E --> F[planner 输出 draft round brief]
    F --> G[reviewer fresh session 审稿]
    G --> H{PASS?}

    H -- 否 --> I[planner 吸收打回理由<br/>同轮重写 draft]
    I --> G

    H -- 是 --> J[edit_worker 落码<br/>只改策略文件]
    J --> K{技术校验通过?}
    K -- 否 --> L[repair_worker 修技术错误<br/>不改研究主题]
    L --> K

    K -- 是 --> M[主进程判卷<br/>diff / smoke / behavioral_noop]
    M --> N[可选 exit_range_scan<br/>单参数 3 点轻量预筛]
    N --> O[full eval<br/>train walk-forward + val]
    O --> P[可选 plateau_probe<br/>只读观察 val 3 段平台]
    P --> Q[gate + promotion_score]
    Q --> R[summary_worker<br/>按真实 diff 回写摘要]
    R --> S{刷新 champion?}

    S -- 否 --> T[写回 journal / wiki<br/>reviewer_summary_card / direction_board]
    T --> E

    S -- 是 --> U[更新 best/champion/策略快照]
    U --> V[只读 test 验收<br/>2026-01-01 到 2026-04-20]
    V --> W[生成图表 / Discord 播报 / champion_history 归档]
    W --> X[重置 stage 和 planner session]
    X --> Y[旧 champion_review 自动失效<br/>除非人工更新 hash]
    Y --> E
```

## 核心原则

- 研究器只维护一个 active reference：没有合格版本时是 `baseline`，有合格版本后是 `champion`。
- `planner` 负责想方向，不直接写代码；`reviewer` 负责拦坏方向，不替 planner 发明方向。
- `edit_worker` 只把 reviewer 放行后的方向落到 `src/strategy_macd_aggressive.py`。
- 主进程负责判卷，不负责想策略。
- `test` 对新 champion 同步运行；对已完成完整评估但未保留的候选会后台异步补跑，只做只读留档，不参与晋升，也不进入普通调参循环。
- 人工卡都是软引导，不是硬 gate；当前 champion 人工观察卡必须 hash 命中才会给 planner 看。

## 当前数据与评分口径

- 标的：`BTC-USDT-SWAP`，策略按 `20x` 合约研究。
- 事实层：`15m`；`1h / 4h` 由 `15m` 聚合，只做确认层。
- 执行层：优先使用 `1m` 回测成交。
- 评分口径：`trend_capture_v12_robustness_plateau_penalty`。
- `train`：`2023-07-01` 到 `2024-12-31`。
- `val`：`2025-01-01` 到 `2025-12-31`。
- `test`：`2026-01-01` 到 `2026-04-20`。
- 晋升条件：先过 `gate`，再要求 `promotion_score` 高于当前 active reference。
- `promotion_score` 以连续趋势抓取主分与按日收益年化补分 `8:2` 为主体，再减去分段回撤惩罚和轻量鲁棒性软惩罚。
- 鲁棒性软惩罚只看 `train/val` 落差、`val` 分块稳定性，以及退出参数邻域在 `val` 3 段上的平台形态。
- `test` 只做只读观察；reject / duplicate_skipped 的异步 `test` 只进留档，不进 prompt、不进晋升。

## 每一轮怎么跑

1. 主进程读取当前 active reference、窗口配置和 stage 记忆。
2. 若 `config/research_v2_champion_review.md` 的 `champion_code_hash` 命中当前 champion，注入这张人工观察卡；否则忽略。
3. `planner` 读取人工卡、上一轮 reviewer 卡、方向账本和前台记忆，写一个单一假设的 draft brief。
4. `reviewer` 审稿，只输出 `PASS` 或 `REVISE`。
5. 若 `REVISE`，planner 必须吸收打回理由，同轮重写；连续打回则本轮停止。
6. 若 `PASS`，`edit_worker` 把方向落到策略源码。
7. 若出现 no-edit、语法错误、缺 helper、校验失败等技术问题，`repair_worker` 只修技术错误。
8. 主进程检查真实 diff、重复源码、smoke 行为和关键漏斗变化。
9. 如果 brief 指定单个连续型 `EXIT_PARAMS` 的 `exit_range_scan`，主进程最多扫 3 个值，只做轻量预筛。
10. 主进程跑完整 `train walk-forward + val`；若本轮动了允许观察的退出参数，再额外做一次只读 `plateau_probe`，只看 `val` 3 段平台，不改代码、不改基底。
11. 主进程执行 gate 与 promotion 判断。
12. `summary_worker` 按最终真实 diff 回写候选摘要。
13. 没有刷新 champion：写回 `journal / wiki / reviewer_summary_card / direction_board`；若该轮已完成 full eval，则后台异步补跑 `test` 关键指标留档，然后进入下一轮。
14. 刷新 champion：更新策略快照，同步跑 `test`，生成图表和 Discord 播报，归档 `champion_history`，然后重置 stage 与 planner session。

## 各角色职责

### planner

- 唯一持久 session。
- 负责提出研究方向和单一可证伪假设。
- 必须先读当前人工卡、reviewer 卡、direction board 和前台记忆。
- 如果 reviewer 打回，必须先吸收打回理由再重写。

### reviewer

- 每轮 fresh session。
- 只审 planner 的 draft 是否值得落码。
- 重点检查是否旧失败近邻、是否只换标签、是否说明真实交易路径变化。
- 不写代码，不替 planner 提新方案。

### edit_worker

- 只接收 reviewer 放行后的 brief。
- 只改 `src/strategy_macd_aggressive.py`。
- `PARAMS` 和开放的 `EXIT_PARAMS` 都可调整。
- 杠杆、仓位比例、单仓上下限、并发数和加仓规模保持固定。

### repair_worker

- 只处理同轮技术错误。
- 不改研究主题，不重新想方向。

### summary_worker

- 只根据最终源码 diff 和最终候选代码写摘要。
- 用来避免“planner 原本想改什么”和“代码实际改了什么”错位。

### 主进程

- 负责调度、校验、评估、gate、归档、播报和记忆回写。
- 不替 planner 想策略。

## 人工介入 SOP

### 临时给 planner 一句直觉

编辑：`config/research_v2_champion_review.md`

要求：

- 内容应短尽短。
- 必须保留 `champion_code_hash`。
- 只写针对当前 champion 的观察。
- 新 champion 后 hash 不匹配，旧卡会自动失效。

当前示例：

```text
champion_code_hash: <当前 champion hash>

直觉看了一下图片，觉得现在的问题在退出方向，应该想办法保住收益。
```

### 长期方向偏好

编辑：`config/research_v2_operator_focus.md`

用途：长期软引导，例如优先方向、降权方向、默认动作。它不绑定 champion hash，不会自动失效。

### 手工瘦身或替换 active reference

1. 停掉研究器：`bash scripts/manage_research_macd_aggressive_v2.sh stop`
2. 手工修改策略或替换 active reference。
3. 重开 stage：`bash scripts/reset_research_macd_aggressive_v2_stage.sh`
4. 启动研究器：`bash scripts/manage_research_macd_aggressive_v2.sh start`
5. 跟状态：`bash scripts/manage_research_macd_aggressive_v2.sh status`

补充约束：

- 研究器是由 `scripts/run_research_macd_aggressive_v2.sh` 这个 supervisor 循环拉起；不要只停内部 python 进程，否则 supervisor 会自动重启。
- 如果这次改的是当前 active reference 本体，除了 `src/strategy_macd_aggressive.py`，还要同步本机上的 `backups/strategy_macd_aggressive_v2_best.py` 与 `backups/strategy_macd_aggressive_v2_champion.py`，因为启动时主进程会先从 `best` 快照装载基底；这两个快照现在只作本地运行态文件，不再提交到 GitHub。

## 常用命令

```bash
# 启动
bash scripts/manage_research_macd_aggressive_v2.sh start

# 查看状态
bash scripts/manage_research_macd_aggressive_v2.sh status

# 停止
bash scripts/manage_research_macd_aggressive_v2.sh stop

# 重开 stage
bash scripts/reset_research_macd_aggressive_v2_stage.sh

# 单轮运行
python3 scripts/research_macd_aggressive_v2.py --once

# 重建 OKX 数据
python3 scripts/download_aggressive_data.py
```

## 运行产物

- `state/research_macd_aggressive_v2_best.json`：当前 best/champion 状态。
- `backups/strategy_macd_aggressive_v2_best.py`：当前 best 策略快照，仅保留在本机运行环境。
- `backups/strategy_macd_aggressive_v2_champion.py`：当前 champion 策略快照，仅保留在本机运行环境。
- `backups/strategy_macd_aggressive_v2_candidate.py`：运行中的候选快照，不应随手提交。
- `backups/champion_history/`：每次新 champion 的独立归档。
- `backups/research_v2_round_artifacts/`：每轮最小可复现归档；源码按 `code_hash` 去重，accepted 轮次会额外引用 champion 图表/快照；rejected full eval 轮次会异步补写 `test` 关键指标。
- `reports/research_v2_charts/`：selection / validation 图表。
- `logs/macd_aggressive_research_v2.log`：主日志。
- `logs/macd_aggressive_research_v2_model_calls.jsonl`：模型调用日志。
- `state/research_macd_aggressive_v2_memory/wiki/`：前台记忆、方向账本、失败 wiki、reviewer 卡。
- `src/research_v2/reference_state.py`：reference 状态读写
- `src/research_v2/champion_artifacts.py`：champion 快照归档
- `src/research_v2/round_artifacts.py`：每轮最小可复现归档
- `src/research_v2/backtest_window_runtime.py`：回测窗口运行态
- `src/research_v2/evaluation_summary.py`：评分汇总组装
- `src/research_v2/journal_prompt_builder.py`：journal prompt 组装

## 提交代码时的注意事项

- 研究器运行中会持续改写候选和策略文件。
- `backups/strategy_macd_aggressive_v2_best.py` 与 `backups/strategy_macd_aggressive_v2_champion.py` 现在不再入库；需要分享时单独发送文件，不要重新加入 Git 跟踪。
- 只想提交当前 champion 时，先停研究器，再排除 `backups/strategy_macd_aggressive_v2_candidate.py`。
- 文档、配置或流程改动完成后，要同步更新文档并推送 git。
- 不要把运行中的候选误当成稳定 champion 提交。
