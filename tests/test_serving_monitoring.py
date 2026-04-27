from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from packages.artifact_runtime import ServingBundle
from packages.db_access.repositories import DemoRepository, PlayableTrackRecord
from packages.db_access.runtime_state import InMemoryRuntimeState
from packages.monitoring.rollups import compute_rollup, evaluate_rollback
from packages.recommendation_engine import RecommendationService
from packages.recommendation_engine.serving_state import FallbackState, decide_serving_state
from packages.shared_contracts.schemas import RecommendationRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ServingMonitoringTests(unittest.TestCase):
    def _tracks(self, count: int = 6) -> list[PlayableTrackRecord]:
        return [
            PlayableTrackRecord(
                str(index),
                f"Track {index}",
                f"Artist {index % 3}",
                "",
                30,
                "",
                True,
                f"nav_{index}",
            )
            for index in range(1, count + 1)
        ]

    def test_state_machine_handles_pipeline_error(self) -> None:
        decision = decide_serving_state(
            serving_bundle_available=True,
            playable_count=20,
            ranked_count=0,
            returned_count=18,
            pipeline_error=RuntimeError("boom"),
        )
        self.assertEqual(decision.state, FallbackState.MODEL_UNAVAILABLE)
        self.assertTrue(decision.pipeline_error)
        self.assertEqual(decision.reason, "pipeline_error")

    def test_state_machine_handles_catalog_insufficient(self) -> None:
        decision = decide_serving_state(
            serving_bundle_available=True,
            playable_count=1,
            ranked_count=1,
            returned_count=1,
        )
        self.assertEqual(decision.state, FallbackState.CATALOG_INSUFFICIENT)
        self.assertEqual(decision.reason, "catalog_insufficient")

    def test_recommendation_pipeline_exception_returns_playable_fallback_and_metric(self) -> None:
        repo = DemoRepository(self._tracks(8))
        runtime = InMemoryRuntimeState()
        bundle = ServingBundle.load(PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1")
        service = RecommendationService(repo, runtime, bundle)

        def fail_recommend(*_args, **_kwargs):
            raise RuntimeError("forced pipeline failure")

        assert service.pipeline is not None
        service.pipeline.recommend = fail_recommend  # type: ignore[method-assign]
        response = service.recommend_next(RecommendationRequest(session_id="sess_fail", user_id="user_demo"))

        self.assertEqual(response.fallback_state, "model_unavailable")
        self.assertTrue(response.degraded)
        self.assertGreater(len(response.queue.items), 0)
        for item in response.queue.items:
            self.assertIsNotNone(repo.get_playable_track(item.track_id))
        self.assertEqual(len(repo.serving_request_metrics), 1)
        metric = next(iter(repo.serving_request_metrics.values()))
        self.assertTrue(metric["pipeline_error"])
        self.assertEqual(metric["fallback_state"], "model_unavailable")

    def test_manual_degraded_mode_changes_serving_behavior(self) -> None:
        repo = DemoRepository(self._tracks(8))
        repo.upsert_model_status(True, "manual_degraded", "fallback_only")
        runtime = InMemoryRuntimeState()
        bundle = ServingBundle.load(PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1")
        service = RecommendationService(repo, runtime, bundle)
        response = service.recommend_next(RecommendationRequest(session_id="sess_degraded", user_id="user_demo"))

        self.assertEqual(response.fallback_state, "fallback_only")
        self.assertEqual(response.degraded_reason, "manual_degraded")
        self.assertEqual(response.degraded_action, "fallback_only")

    def test_degraded_fallback_metric_does_not_reuse_previous_candidate_count(self) -> None:
        repo = DemoRepository(self._tracks(8))
        runtime = InMemoryRuntimeState()
        bundle = ServingBundle.load(PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1")
        service = RecommendationService(repo, runtime, bundle)

        first = service.recommend_next(RecommendationRequest(session_id="sess_normal", user_id="user_demo"))
        self.assertEqual(first.fallback_state, "healthy")
        first_metric = list(repo.serving_request_metrics.values())[-1]
        self.assertGreater(first_metric["candidate_count"], 0)

        repo.upsert_model_status(True, "manual_degraded", "fallback_only")
        second = service.recommend_next(RecommendationRequest(session_id="sess_degraded_metric", user_id="user_demo"))

        self.assertEqual(second.fallback_state, "fallback_only")
        second_metric = list(repo.serving_request_metrics.values())[-1]
        self.assertEqual(second_metric["candidate_count"], 0)

    def test_rollup_and_rollback_thresholds(self) -> None:
        now = datetime.now(timezone.utc)
        inputs = {
            "request_metrics": [
                {
                    "model_version": "m1",
                    "fallback_level": "catalog-safe-fallback" if index < 8 else "none",
                    "fallback_state": "model_unavailable" if index < 8 else "healthy",
                    "total_latency_ms": 25,
                    "returned_count": 10,
                    "pipeline_error": index < 2,
                    "created_at": (now - timedelta(minutes=1)).isoformat(),
                }
                for index in range(30)
            ],
            "playback_events": [],
            "feedback_events": [],
        }
        rollup = compute_rollup(
            window_name="5m",
            window_start=now - timedelta(minutes=5),
            window_end=now,
            inputs=inputs,
            minimum_sample_size=20,
        )
        self.assertEqual(rollup["sample_status"], "ok")
        decision = evaluate_rollback(
            rollup,
            active_model={"model_version": "m1"},
            previous_model={"model_version": "m0"},
        )
        self.assertEqual(decision["decision"], "recommend_rollback")
        self.assertEqual(decision["reason"], "high_error_rate")

    def test_insufficient_sample_does_not_recommend_rollback(self) -> None:
        decision = evaluate_rollback(
            {"model_version": "m1", "request_count": 2, "fallback_rate": 1.0},
            active_model={"model_version": "m1"},
            previous_model={"model_version": "m0"},
        )
        self.assertEqual(decision["decision"], "no_action")
        self.assertEqual(decision["reason"], "insufficient_sample")


if __name__ == "__main__":
    unittest.main()
