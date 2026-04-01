# 激进版 MACD 策略实现计划

目录目标
- 所有激进版代码隔离在 `quant/test2`
- `test1` 保持原运行状态，不共享运行文件
- 数据继续读取 `test1/data`

已创建文件
- `strategy_macd_aggressive.py`
- `backtest_macd_aggressive.py`
- `research_macd_aggressive.py`
- `compare_strategies.py`
- `stress_test_aggressive.py`
- `param_sensitivity.py`
- `program_macd_aggressive.md`
- `macd_aggressive_current_state.md`
- `freqtrade.service.env.example`
- `openai_strategy_client.py`

实现要点
- 提高杠杆、并发仓位、单笔资金占比
- 延后止盈并拉宽 trailing
- 放宽 breakout / pullback / 1h 过滤
- 增加一次金字塔加仓能力
- 保留与保守版相近的接口，便于直接做月度对比

后续建议
1. 先跑 `compare_strategies.py` 看 10 个月分布。
2. 再跑 `stress_test_aggressive.py` 和 `param_sensitivity.py` 看风险上限。
3. 若需要自动调参，再启用 `research_macd_aggressive.py`。
