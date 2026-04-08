# Quant Test 2

这是一个独立于 `test1` 的 BTCUSDT 永续合约高风险、高收益研究仓库，当前版本聚焦激进型 MACD 趋势策略的自动调参与回测验证。

当前实现特征：

- 标的：`BTCUSDT` 永续合约
- 数据区间：`2024-06-01` 到 `2026-04-01`
- 执行周期：15 分钟
- 趋势过滤：1 小时 + 4 小时
- 当前有效信号：`long_breakout`、`short_breakdown`
- 默认杠杆：`15x`
- 最大并发仓位：`5`
- 研究循环冷却：`60` 秒
- 评估切片：默认 `20` 天连续时间块，按时间顺序拆成 Walk-Forward `eval` 加最终 `holdout`

当前优化器只允许 AI 微调 12 个核心参数：

- 入场 6 个：`macd_fast`、`macd_slow`、`macd_signal`、`hourly_adx_min`、`breakout_lookback`、`breakdown_lookback`
- 出场 6 个：`leverage`、`position_fraction`、`stop_atr_mult`、`stop_max_loss_pct`、`tp1_pnl_pct`、`trailing_activation_pct`

更完整的策略说明见 [STRATEGY.md](STRATEGY.md)。

当前评估链路：

- `eval`：除最后 `2` 个留出块外，其余连续 `20` 天块全部参与 Walk-Forward 评分
- `holdout`：最后 `2` 个连续 `20` 天块，只用于外层晋级拦截
- 研究器提示词只看公开版 `eval` 摘要，不直接展示 `holdout` 指标

## 目录结构

```text
test2/
├── config/      环境变量和 Discord 配置
├── data/        价格数据和情绪数据
├── docs/        策略说明、计划、当前状态
├── backups/     研究循环写回前的备份文件
├── logs/        运行日志
├── reports/     回测和对比报告
├── scripts/     下载、回测、研究、搜索脚本
└── src/         核心策略、回测引擎、OpenAI 客户端
```

## 关键文件

- [src/strategy_macd_aggressive.py](src/strategy_macd_aggressive.py)
  入场信号逻辑
- [src/backtest_macd_aggressive.py](src/backtest_macd_aggressive.py)
  回测引擎、仓位控制、止盈止损、加仓和费用计算
- [scripts/research_macd_aggressive.py](scripts/research_macd_aggressive.py)
  自动研究循环、评分、Gate、重复方向拦截
- [src/openai_strategy_client.py](src/openai_strategy_client.py)
  OpenAI Responses API 客户端
- [docs/program_macd_aggressive.md](docs/program_macd_aggressive.md)
  提供给优化器的目标说明
- [docs/macd_aggressive_current_state.md](docs/macd_aggressive_current_state.md)
  当前运行设定快照

## 数据文件

- [data/price/BTCUSDT_futures_15m_20240601_20260401.csv](data/price/BTCUSDT_futures_15m_20240601_20260401.csv)
- [data/price/BTCUSDT_futures_1h_20240601_20260401.csv](data/price/BTCUSDT_futures_1h_20240601_20260401.csv)
- [data/price/BTCUSDT_futures_1m_20240601_20260401.csv](data/price/BTCUSDT_futures_1m_20240601_20260401.csv)
- [data/index/crypto_fear_greed_daily_20240601_20260401.csv](data/index/crypto_fear_greed_daily_20240601_20260401.csv)
- [data/funding/OKX_BTC_USDT_SWAP_funding_20240601_20260401.csv](data/funding/OKX_BTC_USDT_SWAP_funding_20240601_20260401.csv)

## 快速开始

在仓库根目录执行：

```bash
cd test2
python3 scripts/research_macd_aggressive.py --once --no-optimize
python3 scripts/research_macd_aggressive.py --once
python3 scripts/compare_strategies.py
```

持续运行研究循环：

```bash
bash scripts/manage_research_macd_aggressive.sh start
tail -f logs/macd_aggressive_research.log
```

## Discord

`test2` 使用独立覆盖配置：

- 频道名：`quant-highrisk`
- 频道 ID：`1488748862188552312`
- 配置文件：[config/research.env](config/research.env)

研究循环会优先读取本仓库的 `config/research.env`，如果上级目录存在 `test1/freqtrade.service.env`，也会把其中的通用密钥一起加载。

公开上传前至少应确认以下密钥没有被提交：

- `OPENAI_API_KEY`
- `DISCORD_BOT_TOKEN`
- `DISCORD_GUILD_ID`

## Git

目标仓库：

```text
https://github.com/398494308/Quant-test-2.git
```

当前目录已经整理成适合单独建仓和上传的结构。运行态文件如 `state/*.pid`、`state/*.lock`、日志和报表已加入忽略列表，适合在 `test2` 目录单独建仓上传。
