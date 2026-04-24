# DeepSeek Planner Experiment

这份 clone 用于做 `planner-only` 的 DeepSeek 对照实验。

## 当前接法

- `planner`：走 DeepSeek 官方兼容 API
- `reviewer / edit_worker / repair_worker / summary_worker`：继续走原来的 `codex exec`
- `base_url`：`https://api.deepseek.com`
- `model`：`deepseek-v4-pro`
- `thinking`：`enabled`
- `reasoning_effort`：`max`

## 当前观察

截至 `2026-04-24` 的这一轮对照实验，当前观察是：

- `GPT` 更适合固定框架、规则严密、执行链稳定的角色，例如 `reviewer / edit_worker / repair_worker / summary_worker`
- `DeepSeek` 在发散找方向、提出新假设、快速换研究层级这类 `planner` 任务里，当前表现更好

这只是当前仓库、当前评分口径和当前实验流程下的结论，不把它外推成所有任务的一般结论。

## 关键说明

### 1. 只换 planner

通过 `config/secrets.env` 中的 `MACD_V2_PLANNER_PROVIDER=deepseek` 控制。

只有 `session_kind=planner` 时才会切到 DeepSeek，其余角色不变。

### 2. AGENTS 规则仍然生效

原来的 `Codex CLI` 会天然读取工作区里的 `AGENTS.md`。

DeepSeek API 不会自动读取本地文件，所以实验接法里会把工作区 `AGENTS.md`
的全文显式注入到 `planner` 的 system prompt 中，确保原有 `apply on planner`
的规则继续成立。

### 3. planner 仍有持久 session

DeepSeek 没有直接复用 Codex 的 session 机制。

这个实验里，`planner` 的多轮上下文会写到工作区本地历史文件：

- `state/research_macd_aggressive_v2_agent_workspace/.deepseek_planner_session_*.json`

它仍然受当前 `active reference + stage` 作用域约束；stage 重开或 champion 刷新时，
本地 planner session 也会跟着重置。

### 4. planner reasoning 会额外落本地 trace

为了方便回看每一轮 `planner` 是否真的换了想法、有没有吸收 `reviewer` 的反馈，
实验接法会额外写一份 append-only trace：

- `state/research_macd_aggressive_v2_agent_workspace/.deepseek_planner_trace_*.jsonl`

每一行记录一轮 planner 调用，包含：

- 当前轮收到的 `prompt`
- DeepSeek 返回的最终 `assistant_content`
- DeepSeek 返回的 `assistant_reasoning_content`

这个 trace 只用于观测，不会把旧的 `reasoning_content` 再喂回模型，因此不会改变原有研究流程。

## 推荐启动前动作

因为这是从主仓拷出来的实验 clone，建议首次启动前先做一次 stage reset：

```bash
bash scripts/reset_research_macd_aggressive_v2_stage.sh
```

这样可以保留当前参考点，但清掉从主仓复制过来的 live session、wiki 前台记忆和候选残留，
避免 A/B 实验互相污染。
