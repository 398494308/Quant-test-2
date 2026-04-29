#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research_v2.round_artifacts import (
    load_round_artifact_metadata,
    persist_round_artifact,
    update_round_artifact_test_payload,
)
from research_v2.strategy_code import source_hash


class RoundArtifactsTest(unittest.TestCase):
    def _entry(self, *, iteration: int, timestamp: str, candidate_id: str) -> dict[str, object]:
        return {
            "iteration": iteration,
            "timestamp": timestamp,
            "candidate_id": candidate_id,
            "outcome": "rejected",
            "stop_stage": "full_eval",
            "score_regime": "trend_capture_v11_piecewise_drawdown_penalty",
            "reference_role": "champion",
            "primary_direction": "long",
            "gate_reason": "未超过当前champion晋级分",
            "decision_reason": "未超过当前champion晋级分",
            "note": "",
            "promotion_score": 0.44,
            "quality_score": 0.51,
            "promotion_delta": -0.02,
            "reference_code_hash": "reference_hash",
            "change_tags": ["drawdown_control"],
            "edited_regions": ["strategy"],
            "system_changed_regions": ["strategy"],
            "diff_summary": ["adjust stop logic"],
            "metrics": {
                "capture_score": 0.62,
                "timed_return_score": 0.28,
                "drawdown_risk_score": 1.30,
                "drawdown_penalty_score": 0.36,
                "selection_max_drawdown": 27.5,
                "validation_sharpe_ratio": 1.12,
            },
        }

    def test_persist_round_artifact_deduplicates_source_pool(self) -> None:
        strategy_source = "def strategy():\n    return 'ok'\n"
        code_hash = source_hash(strategy_source)

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            artifacts_root = repo_root / "backups/research_v2_round_artifacts"
            context = {
                "windows": {"validation_start_date": "2025-01-01"},
                "gates": {"max_fee_drag_pct": 11.5},
                "scoring": {"promotion_capture_weight": 0.8},
                "data_fingerprints": {"intraday": {"path": "a.csv", "exists": True}},
                "engine_fingerprints": {"evaluation": {"path": "eval.py", "exists": True}},
            }

            first_dir = persist_round_artifact(
                artifacts_root,
                repo_root=repo_root,
                entry=self._entry(iteration=1, timestamp="2026-04-28T00:00:00+00:00", candidate_id="cand_a"),
                strategy_source=strategy_source,
                windows=context["windows"],
                gates=context["gates"],
                scoring=context["scoring"],
                data_fingerprints=context["data_fingerprints"],
                engine_fingerprints=context["engine_fingerprints"],
            )
            second_dir = persist_round_artifact(
                artifacts_root,
                repo_root=repo_root,
                entry=self._entry(iteration=2, timestamp="2026-04-28T00:01:00+00:00", candidate_id="cand_b"),
                strategy_source=strategy_source,
                windows=context["windows"],
                gates=context["gates"],
                scoring=context["scoring"],
                data_fingerprints=context["data_fingerprints"],
                engine_fingerprints=context["engine_fingerprints"],
            )

            source_files = list((artifacts_root / "sources" / code_hash[:2]).glob("*.py"))
            self.assertEqual(len(source_files), 1)
            self.assertTrue((first_dir / "strategy_macd_aggressive.py").exists())
            self.assertTrue((second_dir / "strategy_macd_aggressive.py").exists())

            metadata = json.loads((first_dir / "metadata.json").read_text())
            self.assertEqual(metadata["strategy"]["code_hash"], code_hash)
            self.assertEqual(metadata["summary_scores"]["promotion_score"], 0.44)
            self.assertEqual(metadata["score_components"]["drawdown_penalty_score"], 0.36)
            self.assertEqual(
                metadata["strategy"]["source_pool_path"],
                f"backups/research_v2_round_artifacts/sources/{code_hash[:2]}/{code_hash}.py",
            )

    def test_persist_round_artifact_falls_back_to_copy_when_hardlink_fails(self) -> None:
        strategy_source = "def strategy():\n    return 'copy'\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            artifacts_root = repo_root / "backups/research_v2_round_artifacts"
            with mock.patch("research_v2.round_artifacts.os.link", side_effect=OSError("cross-device")):
                round_dir = persist_round_artifact(
                    artifacts_root,
                    repo_root=repo_root,
                    entry=self._entry(iteration=3, timestamp="2026-04-28T00:02:00+00:00", candidate_id="cand_c"),
                    strategy_source=strategy_source,
                    windows={},
                    gates={},
                    scoring={},
                    data_fingerprints={},
                    engine_fingerprints={},
                )

            self.assertEqual((round_dir / "strategy_macd_aggressive.py").read_text(), strategy_source)

    def test_persist_round_artifact_records_champion_refs(self) -> None:
        strategy_source = "def strategy():\n    return 'champion'\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            artifacts_root = repo_root / "backups/research_v2_round_artifacts"
            champion_snapshot_dir = repo_root / "backups/champion_history/snapshot_a"
            champion_snapshot_dir.mkdir(parents=True, exist_ok=True)
            (champion_snapshot_dir / "validation.png").write_bytes(b"validation")
            chart_path = repo_root / "reports/research_v2_charts/validation.png"
            chart_path.parent.mkdir(parents=True, exist_ok=True)
            chart_path.write_bytes(b"chart")

            round_dir = persist_round_artifact(
                artifacts_root,
                repo_root=repo_root,
                entry=self._entry(iteration=4, timestamp="2026-04-28T00:03:00+00:00", candidate_id="cand_d"),
                strategy_source=strategy_source,
                windows={},
                gates={},
                scoring={},
                data_fingerprints={},
                engine_fingerprints={},
                test_metrics={"test_score": 0.91},
                champion_snapshot_dir=champion_snapshot_dir,
                chart_paths={"validation_chart": chart_path},
            )

            metadata = json.loads((round_dir / "metadata.json").read_text())
            self.assertEqual(metadata["test_metrics"]["test_score"], 0.91)
            self.assertEqual(
                metadata["champion_artifacts"]["snapshot_dir"],
                "backups/champion_history/snapshot_a",
            )
            self.assertEqual(
                metadata["champion_artifacts"]["validation_chart"],
                "reports/research_v2_charts/validation.png",
            )

    def test_load_round_artifact_metadata_normalizes_legacy_test_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            round_dir = Path(tmpdir) / "round"
            round_dir.mkdir(parents=True, exist_ok=True)
            (round_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "iteration": 7,
                        "candidate_id": "cand_legacy",
                        "shadow_test_metrics": {
                            "shadow_test_score": 0.42,
                            "shadow_test_sharpe_ratio": 1.11,
                        },
                    },
                    ensure_ascii=False,
                )
            )

            metadata = load_round_artifact_metadata(round_dir)
            self.assertEqual(metadata["test_metrics"]["test_score"], 0.42)
            self.assertEqual(metadata["test_metrics"]["test_sharpe_ratio"], 1.11)
            self.assertNotIn("shadow_test_metrics", metadata)

    def test_update_round_artifact_test_payload_writes_test_status(self) -> None:
        strategy_source = "def strategy():\n    return 'ok'\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            artifacts_root = repo_root / "backups/research_v2_round_artifacts"
            round_dir = persist_round_artifact(
                artifacts_root,
                repo_root=repo_root,
                entry=self._entry(iteration=5, timestamp="2026-04-28T00:05:00+00:00", candidate_id="cand_e"),
                strategy_source=strategy_source,
                windows={},
                gates={},
                scoring={},
                data_fingerprints={},
                engine_fingerprints={},
            )

            update_round_artifact_test_payload(
                round_dir,
                test_metrics={"test_score": 0.55, "test_sharpe_ratio": 0.88},
                test_evaluation={
                    "status": "completed",
                    "mode": "rejected_async",
                    "queued_at": "2026-04-28T00:05:01+00:00",
                    "completed_at": "2026-04-28T00:05:09+00:00",
                },
            )

            metadata = load_round_artifact_metadata(round_dir)
            self.assertEqual(metadata["test_metrics"]["test_score"], 0.55)
            self.assertEqual(metadata["test_metrics"]["test_sharpe_ratio"], 0.88)
            self.assertEqual(metadata["test_evaluation"]["status"], "completed")
            self.assertEqual(metadata["test_evaluation"]["mode"], "rejected_async")


if __name__ == "__main__":
    unittest.main()
