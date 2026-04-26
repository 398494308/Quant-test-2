# 当前 Champion 人工观察卡

说明：这张卡只给 planner 看，是软引导，不是硬限制。`champion_code_hash` 必须等于当前 champion；刷新新 champion 后主进程会自动忽略这张卡，直到人工更新 hash。

champion_code_hash: a63365ed9d1ccd7538032d19639ae37d5d3fe76451ede4b2d20bfa068f4488f0

## 人工观察

- 图形直觉上，当前 champion 的退出时间抓得不好，很多上涨后的利润被回吐，回撤偏大。
- 不要只继续研究“更早放多头”；下一步优先检查趋势失效、利润保护、追踪止损、sideways/regime exit、反向信号退出等退出相关机制。
- 目标不是把收益压平，而是在尽量保留 train/val 收益弹性的前提下，降低大回撤和 shadow test 回吐。
- 当前参考现象：validation 收益很强，但 shadow test 出现负收益与约 39% 回撤，说明 OOS 退出/保护质量需要重点复核。
