from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from freqtrade_macd_aggressive import MacdAggressiveStrategy as BaseMacdAggressiveStrategy


class MacdAggressiveStrategy(BaseMacdAggressiveStrategy):
    startup_candle_count = 320
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True
    position_adjustment_enable = True
