import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from research_v2.config import load_research_runtime_config


class ResearchRuntimeConfigTest(unittest.TestCase):
    def test_load_research_runtime_config_reads_robustness_overrides_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "research_v2.env").write_text(
                "\n".join(
                        [
                        "MACD_V2_MIN_VALIDATION_CLOSED_TRADES=0",
                        "MACD_V2_PROMOTION_CAPTURE_WEIGHT=0.45",
                        "MACD_V2_PROMOTION_TIMED_RETURN_WEIGHT=0.30",
                        "MACD_V2_PROMOTION_SHARPE_FLOOR_WEIGHT=0.25",
                        "MACD_V2_PROMOTION_TRADE_ACTIVITY_PENALTY_WEIGHT=0.10",
                        "MACD_V2_TRADE_ACTIVITY_TRAIN_RANGE_LOW=270",
                        "MACD_V2_TRADE_ACTIVITY_TRAIN_RANGE_HIGH=360",
                        "MACD_V2_TRADE_ACTIVITY_VALIDATION_RANGE_LOW=180",
                        "MACD_V2_TRADE_ACTIVITY_VALIDATION_RANGE_HIGH=240",
                        "MACD_V2_ROBUSTNESS_PENALTY_CAP=0.31",
                        "MACD_V2_ROBUSTNESS_GAP_WARN_THRESHOLD=0.17",
                        "MACD_V2_ROBUSTNESS_GAP_FAIL_THRESHOLD=0.23",
                        "MACD_V2_ROBUSTNESS_GAP_WARN_PENALTY=0.04",
                        "MACD_V2_ROBUSTNESS_GAP_FAIL_PENALTY=0.09",
                        "MACD_V2_ROBUSTNESS_BLOCK_STD_WARN_THRESHOLD=0.19",
                        "MACD_V2_ROBUSTNESS_BLOCK_STD_FAIL_THRESHOLD=0.27",
                        "MACD_V2_ROBUSTNESS_BLOCK_STD_WARN_PENALTY=0.05",
                        "MACD_V2_ROBUSTNESS_BLOCK_STD_FAIL_PENALTY=0.08",
                        "MACD_V2_ROBUSTNESS_BLOCK_FLOOR_WARN_THRESHOLD=0.21",
                        "MACD_V2_ROBUSTNESS_BLOCK_FLOOR_FAIL_THRESHOLD=0.07",
                        "MACD_V2_ROBUSTNESS_BLOCK_FLOOR_WARN_PENALTY=0.04",
                        "MACD_V2_ROBUSTNESS_BLOCK_FLOOR_FAIL_PENALTY=0.09",
                        "MACD_V2_ROBUSTNESS_BLOCK_TAIL_WARN_THRESHOLD=0.11",
                        "MACD_V2_ROBUSTNESS_BLOCK_TAIL_FAIL_THRESHOLD=0.19",
                        "MACD_V2_ROBUSTNESS_BLOCK_TAIL_WARN_PENALTY=0.05",
                        "MACD_V2_ROBUSTNESS_BLOCK_TAIL_FAIL_PENALTY=0.11",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {}, clear=True):
                runtime = load_research_runtime_config(repo_root)

            self.assertEqual(runtime.gates.min_validation_closed_trades, 0)
            self.assertAlmostEqual(runtime.scoring.promotion_capture_weight, 0.45)
            self.assertAlmostEqual(runtime.scoring.promotion_timed_return_weight, 0.30)
            self.assertAlmostEqual(runtime.scoring.promotion_sharpe_floor_weight, 0.25)
            self.assertAlmostEqual(runtime.scoring.promotion_trade_activity_penalty_weight, 0.10)
            self.assertEqual(runtime.scoring.trade_activity_train_range_low, 270)
            self.assertEqual(runtime.scoring.trade_activity_train_range_high, 360)
            self.assertEqual(runtime.scoring.trade_activity_validation_range_low, 180)
            self.assertEqual(runtime.scoring.trade_activity_validation_range_high, 240)
            self.assertAlmostEqual(runtime.scoring.robustness_penalty_cap, 0.31)
            self.assertAlmostEqual(runtime.scoring.robustness_gap_warn_threshold, 0.17)
            self.assertAlmostEqual(runtime.scoring.robustness_gap_fail_threshold, 0.23)
            self.assertAlmostEqual(runtime.scoring.robustness_gap_warn_penalty, 0.04)
            self.assertAlmostEqual(runtime.scoring.robustness_gap_fail_penalty, 0.09)
            self.assertAlmostEqual(runtime.scoring.robustness_block_std_warn_threshold, 0.19)
            self.assertAlmostEqual(runtime.scoring.robustness_block_std_fail_threshold, 0.27)
            self.assertAlmostEqual(runtime.scoring.robustness_block_std_warn_penalty, 0.05)
            self.assertAlmostEqual(runtime.scoring.robustness_block_std_fail_penalty, 0.08)
            self.assertAlmostEqual(runtime.scoring.robustness_block_floor_warn_threshold, 0.21)
            self.assertAlmostEqual(runtime.scoring.robustness_block_floor_fail_threshold, 0.07)
            self.assertAlmostEqual(runtime.scoring.robustness_block_floor_warn_penalty, 0.04)
            self.assertAlmostEqual(runtime.scoring.robustness_block_floor_fail_penalty, 0.09)
            self.assertAlmostEqual(runtime.scoring.robustness_block_tail_warn_threshold, 0.11)
            self.assertAlmostEqual(runtime.scoring.robustness_block_tail_fail_threshold, 0.19)
            self.assertAlmostEqual(runtime.scoring.robustness_block_tail_warn_penalty, 0.05)
            self.assertAlmostEqual(runtime.scoring.robustness_block_tail_fail_penalty, 0.11)


if __name__ == "__main__":
    unittest.main()
