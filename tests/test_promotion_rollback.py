from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import importlib.util
from pathlib import Path

from packages.db_access.repositories import DemoRepository, PlayableTrackRecord
from packages.monitoring.rollups import evaluate_rollback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROMOTION_SPEC = importlib.util.spec_from_file_location(
    "promotion_gate",
    PROJECT_ROOT / "jobs" / "promotion-gate" / "promotion_gate.py",
)
_PROMOTION_MODULE = importlib.util.module_from_spec(_PROMOTION_SPEC)
assert _PROMOTION_SPEC.loader is not None
_PROMOTION_SPEC.loader.exec_module(_PROMOTION_MODULE)
evaluate_promotion = _PROMOTION_MODULE.evaluate_promotion


class PromotionRollbackTests(unittest.TestCase):
    def test_promotion_gate_approves_non_regressing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate"
            shutil.copytree(PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1", candidate)
            eval_path = Path(tmp) / "eval.json"
            eval_path.write_text(
                json.dumps(
                    {
                        "candidate": {"recall_at_20": 0.501, "mrr_at_20": 0.201},
                        "baseline": {"recall_at_20": 0.500, "mrr_at_20": 0.200},
                    }
                ),
                encoding="utf-8",
            )

            decision = evaluate_promotion(candidate, eval_path)

            self.assertEqual(decision["decision"], "approve")
            self.assertTrue((candidate / "promotion_decision.json").exists())

    def test_promotion_gate_blocks_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate"
            shutil.copytree(PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1", candidate)
            (candidate / "pop_scores.csv").unlink()
            eval_path = Path(tmp) / "eval.json"
            eval_path.write_text(
                json.dumps(
                    {
                        "candidate": {"recall_at_20": 0.501, "mrr_at_20": 0.201},
                        "baseline": {"recall_at_20": 0.500, "mrr_at_20": 0.200},
                    }
                ),
                encoding="utf-8",
            )

            decision = evaluate_promotion(candidate, eval_path)

            self.assertEqual(decision["decision"], "block")
            self.assertIn("bundle validation failed", decision["reason"])

    def test_demo_repository_records_monitoring_decisions(self) -> None:
        repo = DemoRepository([PlayableTrackRecord("1", "One", "A", "", 1, "", True, "nav1")])
        repo.register_active_model_version("model_v1", "bundle_v1", "/bundle_v1/manifest.json")
        repo.register_active_model_version("model_v2", "bundle_v2", "/bundle_v2/manifest.json")
        active = repo.get_active_model_version()
        previous = repo.get_previous_good_model_version()

        self.assertEqual(active["model_version"], "model_v2")
        self.assertEqual(previous["model_version"], "model_v1")

        decision = evaluate_rollback(
            {
                "model_version": "model_v2",
                "recommendation_request_count": 60,
                "stream_request_count": 30,
                "playback_start_count": 30,
                "fallback_rate": 0.30,
                "stream_failure_rate": 0.0,
                "dislike_rate": 0.0,
                "top_artist_share": 0.10,
                "repeat_violation_count": 0,
                "metrics": {
                    "operational": {"recommendation_error_count": 0},
                    "model_output": {"recommended_item_count": 60},
                },
            },
            active_model=active,
            previous_model=previous,
        )
        repo.record_model_trigger_decision(decision)

        self.assertEqual(repo.latest_model_trigger_decision("rollback")["decision"], "rollback_recommended")


if __name__ == "__main__":
    unittest.main()
