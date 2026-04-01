# Quant Test 2

这是一个独立于 `test1` 的 BTCUSDT 永续合约双向策略研究仓库，专门用于高风险、高收益版本的手动优化和自动探索。

当前策略特征：

- 标的：`BTCUSDT` 永续合约
- 数据：`2024-06-01` 到 `2026-04-01`
- 周期：15 分钟执行，1 小时和 4 小时过滤
- 方向：双向
- 信号：
  - `long_breakout`
  - `long_pullback`
  - `short_breakdown`
  - `short_bounce_fail`
- 默认杠杆：`18x`

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

- [src/strategy_macd_aggressive.py](/home/ubuntu/quant/test2/src/strategy_macd_aggressive.py)
  双向策略信号逻辑
- [src/backtest_macd_aggressive.py](/home/ubuntu/quant/test2/src/backtest_macd_aggressive.py)
  双向回测引擎、分信号风控、杠杆与加仓
- [scripts/research_macd_aggressive.py](/home/ubuntu/quant/test2/scripts/research_macd_aggressive.py)
  自动研究循环
- [scripts/compare_strategies.py](/home/ubuntu/quant/test2/scripts/compare_strategies.py)
  与 `test1` 保守版逐月对比
- [scripts/search_aggressive_params.py](/home/ubuntu/quant/test2/scripts/search_aggressive_params.py)
  小范围参数搜索

## 数据文件

- [data/price/BTCUSDT_futures_15m_20240601_20260401.csv](/home/ubuntu/quant/test2/data/price/BTCUSDT_futures_15m_20240601_20260401.csv)
- [data/price/BTCUSDT_futures_1h_20240601_20260401.csv](/home/ubuntu/quant/test2/data/price/BTCUSDT_futures_1h_20240601_20260401.csv)
- [data/index/crypto_fear_greed_daily_20240601_20260401.csv](/home/ubuntu/quant/test2/data/index/crypto_fear_greed_daily_20240601_20260401.csv)

## 快速开始

在仓库根目录执行：

```bash
cd /home/ubuntu/quant/test2
python3 scripts/compare_strategies.py
python3 scripts/stress_test_aggressive.py
python3 scripts/param_sensitivity.py
python3 scripts/research_macd_aggressive.py --once --no-optimize
```

## Discord

`test2` 使用独立覆盖配置：

- 频道名：`quant-highrisk`
- 频道 ID：`1488748862188552312`
- 配置文件：[config/research.env](/home/ubuntu/quant/test2/config/research.env)

研究循环会先继承 `test1/freqtrade.service.env` 的通用密钥，再用 `test2/config/research.env` 覆盖频道和轮询参数。

## Git

目标仓库：

```text
https://github.com/398494308/Quant-test-2.git
```

当前目录已经整理成适合单独建仓和上传的结构。建议在 `test2` 目录内单独初始化 Git，不与 `test1` 混在一起。
