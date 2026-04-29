champion_code_hash: e66af6f30f0b4e0610d3ccb68a67545c8ebea67cf6853b3dc2ba961a4d9eefa9

- 这版更像卡在“误判真实生效层”，不是单纯还缺一个新参数。优先改当前源码里确实会消费的 `long_outer_context_ok`、`long_final_veto_clear` 或已接线的 `EXIT_PARAMS`，不要再做只改参数表层却不接执行链的 no-op。
- 若继续看退出方向，先确认当前基底里哪条规则真的会迁移平仓集合；不要假设 `strategy()` 里存在独立的 long 持仓主退出链。
- 一旦再次出现 `exploration_blocked` 或 `behavioral_noop`，下一轮必须换 choke point，不要只换标签和措辞。
