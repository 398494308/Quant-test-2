# real-money-test

这个目录专门放 `test2` 的实盘测试壳子，目标很简单：

- 继续用 `src/strategy_macd_aggressive.py` 和 `src/freqtrade_macd_aggressive.py` 作为策略逻辑来源
- 用 `freqtrade` 负责连 OKX、下单、持仓、日志、重启
- 复用 `test1` 现成的 OKX API 配置，不再重复手填一份

## 目录说明

- `config.base.json`
  `test2` 的基础 `freqtrade` 配置，不含敏感信息
- `build_runtime_config.py`
  从 `test1/user_data/config.json` 读取 OKX 的 `key/secret/password`，拼成 `test2` 可运行配置
- `strategies/MacdAggressiveStrategy.py`
  `freqtrade` 的策略入口，内部转到 `src/freqtrade_macd_aggressive.py`
- `start_dry_run.sh`
  启动纸面盘
- `start_live.sh`
  启动真金白银实盘，需要先显式确认风险
- `manage.sh`
  统一入口，直接用 `start / stop / restart / status / log`
- `status.sh`
  看当前有没有 `freqtrade` 进程在跑
- `systemd/freqtrade-test2-dryrun.service.example`
  systemd 服务示例

## 先做什么

先跑纸面盘，不要直接上实盘。

```bash
cd /home/ubuntu/quant/test2
bash real-money-test/manage.sh start
```

日志默认会写到：

```text
real-money-test/runtime/freqtrade-dryrun.log
```

实时看日志：

```bash
bash real-money-test/manage.sh log
```

看状态：

```bash
bash real-money-test/manage.sh status
```

停止：

```bash
bash real-money-test/manage.sh stop
```

## 真正实盘怎么启动

只有在你已经确认纸面盘表现、交易所权限、仓位限制都没问题之后，才执行：

```bash
cd /home/ubuntu/quant/test2
export I_UNDERSTAND_LIVE_RISK=YES
bash real-money-test/start_live.sh
```

## 这一步已经解决了什么

- `test2` 有了单独的实盘测试目录，不再和研究脚本混在一起
- `test1` 已有的 OKX 凭证可以直接复用
- `test2` 已经能直接调用本机现成的 `freqtrade` 环境
- futures / isolated / `BTC/USDT:USDT` 的基础配置已经单独整理好

## 这一步还没有完全解决什么

目前这个 `freqtrade` 适配层，已经对齐了下面这些关键执行逻辑：

- 动态仓位缩放
- ATR 初始止损
- 保本止损
- 基于峰值利润的 trailing
- 趋势失效退出
- 时间退出
- 反向信号退出
- `TP1` 分批止盈
- `pyramid` 有限次加仓

但它还没有完整复刻自研回测器里的整套执行细节。主要差异还在：

- 回测里的 1 分钟执行价与实盘真实成交之间的细微差异
- 回测里的滑点假设与真实盘口冲击差异
- 资金费、手续费和交易所返回字段在真实环境下的逐笔核对

所以这套现在的定位应该是：

- 可以开始跑纸面盘
- 可以准备小资金实盘
- 已经很接近可执行版本
- 但还不能把回测收益直接当成未来实盘收益

## 推荐推进顺序

1. 先跑 `dry-run`，确认能稳定拉起、能下模拟单、日志正常
2. 做 2 到 5 天的连续纸面盘观察，重点看开平仓、部分止盈、加仓、退出原因
3. 再做 very small size 的真实资金测试
4. 最后才考虑放大仓位
