from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from packages.monitoring.rollups import build_serving_rollup, evaluate_rollback


class MonitoringRollupTests(unittest.TestCase):
    def test_rollup_computes_model_operational_and_feedback_metrics(self) -> None:
        now = datetime.now(timezone.utc)
        inputs = {
            "request_metrics": [
                {
                    "endpoint": "recommendations.next",
                    "status": "success",
                    "latency_ms": 100,
                    "fallback_level": "none",
                },
                {
                    "endpoint": "recommendations.next",
                    "status": "error",
                    "latency_ms": 500,
                    "fallback_level": "catalog-safe-fallback",
                },
                {"endpoint": "stream.proxy", "status": "error", "latency_ms": 40},
            ],
            "recommendation_impressions": [
                {
                    "session_id": "sess_1",
                    "queue": {
                        "items": [
                            {"track_id": "1", "artist": "Artist A"},
                            {"track_id": "2", "artist": "Artist A"},
                            {"track_id": "3", "artist": "Artist B"},
                        ]
                    },
                }
            ],
            "rendered_impressions": [{"impression_id": "imp_1"}],
            "playback_events": [
                {"event_type": "playback_start"},
                {"event_type": "skip"},
                {"event_type": "complete"},
            ],
            "feedback_events": [{"feedback_type": "dislike"}],
        }
        rollup = build_serving_rollup(
            inputs,
            window_name="1h",
            window_start=now - timedelta(hours=1),
            window_end=now,
            model_version="model_v1",
        )

        self.assertEqual(rollup["request_count"], 3)
        self.assertEqual(rollup["recommendation_request_count"], 2)
        self.assertEqual(rollup["stream_request_count"], 1)
        self.assertEqual(rollup["unique_track_count"], 3)
        self.assertEqual(rollup["unique_artist_count"], 2)
        self.assertAlmostEqual(rollup["top_artist_share"], 2 / 3)
        self.assertAlmostEqual(rollup["fallback_rate"], 0.5)
        self.assertEqual(rollup["playback_start_count"], 1)
        self.assertEqual(rollup["completion_rate"], 1.0)
        self.assertEqual(rollup["sample_status"], "insufficient_sample")

    def test_rollback_requires_minimum_sample_before_action(self) -> None:
        rollup = {
            "model_version": "model_v2",
            "recommendation_request_count": 1,
            "stream_request_count": 1,
            "playback_start_count": 1,
            "fallback_rate": 1.0,
            "stream_failure_rate": 1.0,
            "dislike_rate": 1.0,
            "top_artist_share": 1.0,
            "repeat_violation_count": 1,
            "metrics": {
                "operational": {"recommendation_error_count": 1},
                "model_output": {"recommended_item_count": 1},
            },
        }

        decision = evaluate_rollback(rollup, active_model={"model_version": "model_v2"})

        self.assertEqual(decision["decision"], "no_action_insufficient_sample")
        self.assertIn("fallback_rate", decision["metrics"]["insufficient_sample"])

    def test_rollback_recommends_action_after_guarded_breach(self) -> None:
        rollup = {
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
        }

        decision = evaluate_rollback(
            rollup,
            active_model={"model_version": "model_v2"},
            previous_model={"model_version": "model_v1"},
        )

        self.assertEqual(decision["decision"], "rollback_recommended")
        self.assertIn("fallback_rate", decision["metrics"]["breaches"])


if __name__ == "__main__":
    unittest.main()
