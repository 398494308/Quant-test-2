#!/usr/bin/env python3
"""对比保守版和激进版 MACD 策略的月度表现。"""
import argparse
import calendar
import csv
import statistics
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
TEST1_DIR = REPO_ROOT.parent / "test1"
if TEST1_DIR.exists():
    sys.path.insert(0, str(TEST1_DIR))
sys.path.insert(0, str(SRC_DIR))

try:
    import backtest_macd as conservative_backtest  # noqa: E402
    import strategy_macd as conservative_strategy  # noqa: E402
    HAS_CONSERVATIVE = True
except ImportError:
    HAS_CONSERVATIVE = False
from backtest_macd_aggressive import backtest_macd_aggressive  # noqa: E402
import backtest_macd_aggressive as aggressive_backtest  # noqa: E402
import strategy_macd_aggressive as aggressive_strategy  # noqa: E402

INTRADAY_FILE = TEST1_DIR / "data/price/BTCUSDT_futures_15m_20250601_20260401.csv"
HOURLY_FILE = TEST1_DIR / "data/price/BTCUSDT_futures_1h_20250601_20260401.csv"
AGGRESSIVE_INTRADAY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_15m_20240601_20260401.csv"
AGGRESSIVE_HOURLY_FILE = REPO_ROOT / "data/price/BTCUSDT_futures_1h_20240601_20260401.csv"


def month_windows():
    months = []
    for year, month in [
        (2025, 6),
        (2025, 7),
        (2025, 8),
        (2025, 9),
        (2025, 10),
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
    ]:
        last_day = calendar.monthrange(year, month)[1]
        months.append((f"{year}-{month:02d}", f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last_day:02d}"))
    return months


def annualized_return(monthly_returns):
    if not monthly_returns:
        return 0.0
    avg = statistics.mean(monthly_returns)
    return avg * 12.0


def sharpe_ratio(monthly_returns):
    if len(monthly_returns) < 2:
        return 0.0
    std = statistics.pstdev(monthly_returns)
    if std <= 1e-9:
        return 0.0
    return statistics.mean(monthly_returns) / std * (12 ** 0.5)


def sortino_ratio(monthly_returns):
    downside = [value for value in monthly_returns if value < 0]
    if not downside:
        return 0.0
    downside_std = statistics.pstdev(downside)
    if downside_std <= 1e-9:
        return 0.0
    return statistics.mean(monthly_returns) / downside_std * (12 ** 0.5)


def calmar_ratio(monthly_returns, max_drawdown):
    if max_drawdown <= 1e-9:
        return 0.0
    return annualized_return(monthly_returns) / max_drawdown


def run_comparison():
    rows = []
    conservative_monthly = []
    aggressive_monthly = []
    max_conservative_dd = 0.0
    max_aggressive_dd = 0.0

    for label, start_date, end_date in month_windows():
        conservative = conservative_backtest.backtest_macd(
            strategy_func=conservative_strategy.strategy,
            intraday_file=str(INTRADAY_FILE),
            hourly_file=str(HOURLY_FILE),
            start_date=start_date,
            end_date=end_date,
            strategy_params=conservative_strategy.PARAMS,
            exit_params=conservative_backtest.EXIT_PARAMS,
        )
        aggressive = backtest_macd_aggressive(
            strategy_func=aggressive_strategy.strategy,
            intraday_file=str(AGGRESSIVE_INTRADAY_FILE),
            hourly_file=str(AGGRESSIVE_HOURLY_FILE),
            start_date=start_date,
            end_date=end_date,
            strategy_params=aggressive_strategy.PARAMS,
            exit_params=aggressive_backtest.EXIT_PARAMS,
        )
        conservative_monthly.append(conservative["return"])
        aggressive_monthly.append(aggressive["return"])
        max_conservative_dd = max(max_conservative_dd, conservative["max_drawdown"])
        max_aggressive_dd = max(max_aggressive_dd, aggressive["max_drawdown"])
        rows.append(
            {
                "month": label,
                "conservative_return": conservative["return"],
                "aggressive_return": aggressive["return"],
                "conservative_drawdown": conservative["max_drawdown"],
                "aggressive_drawdown": aggressive["max_drawdown"],
                "conservative_trades": conservative["trades"],
                "aggressive_trades": aggressive["trades"],
                "conservative_win_rate": conservative["win_rate"],
                "aggressive_win_rate": aggressive["win_rate"],
            }
        )

    summary = {
        "conservative": {
            "annualized_return": annualized_return(conservative_monthly),
            "sharpe": sharpe_ratio(conservative_monthly),
            "sortino": sortino_ratio(conservative_monthly),
            "calmar": calmar_ratio(conservative_monthly, max_conservative_dd),
            "best_month": max(conservative_monthly),
            "worst_month": min(conservative_monthly),
        },
        "aggressive": {
            "annualized_return": annualized_return(aggressive_monthly),
            "sharpe": sharpe_ratio(aggressive_monthly),
            "sortino": sortino_ratio(aggressive_monthly),
            "calmar": calmar_ratio(aggressive_monthly, max_aggressive_dd),
            "best_month": max(aggressive_monthly),
            "worst_month": min(aggressive_monthly),
        },
    }
    return rows, summary


def print_report(rows, summary):
    print("月份    | 保守收益 | 激进收益 | 保守回撤 | 激进回撤 | 保守交易 | 激进交易")
    for row in rows:
        print(
            f"{row['month']} | "
            f"{row['conservative_return']:>7.2f}% | "
            f"{row['aggressive_return']:>7.2f}% | "
            f"{row['conservative_drawdown']:>7.2f}% | "
            f"{row['aggressive_drawdown']:>7.2f}% | "
            f"{row['conservative_trades']:>4} | "
            f"{row['aggressive_trades']:>4}"
        )
    print()
    for name in ("conservative", "aggressive"):
        stats = summary[name]
        print(
            f"{name}: annualized={stats['annualized_return']:.2f}%, "
            f"sharpe={stats['sharpe']:.2f}, sortino={stats['sortino']:.2f}, "
            f"calmar={stats['calmar']:.2f}, best={stats['best_month']:.2f}%, "
            f"worst={stats['worst_month']:.2f}%"
        )


def write_csv_report(rows, output_path):
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(REPO_ROOT / "reports/compare_strategies_report.csv"))
    args = parser.parse_args()

    rows, summary = run_comparison()
    print_report(rows, summary)
    write_csv_report(rows, args.csv)
    print(f"\nCSV 已写入: {args.csv}")


if __name__ == "__main__":
    main()
