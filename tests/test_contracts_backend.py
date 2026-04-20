from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from packages.artifact_runtime import ServingBundle
from packages.auth import (
    hash_password,
    hash_session_token,
    new_session_id,
    new_session_token,
    new_user_id,
    session_expires_at,
    verify_password,
)
from packages.config import AppConfig
from packages.db_access.demo_bootstrap import reset_demo_components
from packages.navidrome_adapter import MediaAccessService
from packages.recommendation_engine import RecommendationService
from packages.shared_contracts.enums import BrowseSurfaceSlot, PlaybackEventType
from packages.shared_contracts.manifests import (
    validate_delta_manifest,
    validate_serving_bundle_manifest,
)
from packages.shared_contracts.schemas import (
    AuthResponse,
    AuthUser,
    BootstrapResponse,
    DegradedState,
    FeedbackEventRequest,
    ImpressionEventRequest,
    PlaybackEventRequest,
    QueueState,
    RecommendationRequest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository, self.runtime = reset_demo_components()
        self.bundle = ServingBundle.load(PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1")
        self.recommender = RecommendationService(self.repository, self.runtime, self.bundle)
        self.media = MediaAccessService(self.repository)

    def test_bootstrap_contract_shape_and_queue_default_closed(self) -> None:
        browse_surface, queue_items = self.recommender.build_bootstrap_surfaces("sess_test", "user_test")
        queue = self.runtime.set_queue("sess_test", queue_items)
        payload = BootstrapResponse(
            session_id="sess_test",
            user_id="user_test",
            auth_state="authenticated",
            browse_surface=browse_surface,
            queue=QueueState(items=queue.items, revision=queue.revision, drawer_default_open=False),
            current_track=None,
            degraded=DegradedState(),
        )

        self.assertEqual(payload.auth_state, "authenticated")
        self.assertFalse(payload.queue.drawer_default_open)
        self.assertLessEqual(len(payload.browse_surface.featured_items), 4)
        self.assertLessEqual(len(payload.browse_surface.random_carousel_items), 10)
        self.assertGreater(len(payload.queue.items), 0)

    def test_recommendations_are_playable_only_and_impression_is_persisted(self) -> None:
        response = self.recommender.recommend_next(
            RecommendationRequest(session_id="sess_test", user_id="user_test")
        )
        returned_ids = [item.track_id for item in response.queue.items]

        self.assertIn(response.impression_id, self.repository.recommendation_impressions)
        self.assertLessEqual(len(response.browse_surface.featured_items), 4)
        self.assertLessEqual(len(response.browse_surface.random_carousel_items), 10)
        self.assertNotIn("trk_missing", returned_ids)
        self.assertNotIn("trk_quarantined", returned_ids)
        for track_id in returned_ids:
            self.assertIsNotNone(self.repository.get_playable_track(track_id))
        self.assertEqual(response.model_version, "demo-fixture-v1")
        self.assertNotEqual(response.browse_surface.featured_items[0].track_id, "trk_004")

    def test_event_idempotency(self) -> None:
        impression = ImpressionEventRequest(
            impression_id="imp_once",
            request_id="req_once",
            session_id="sess_test",
            user_id="user_test",
            visible_items=[{"track_id": "trk_001", "surface_slot": BrowseSurfaceSlot.FEATURED_1}],
            surface="browse_surface",
            rendered_at=datetime.now(timezone.utc),
        )
        self.assertTrue(self.runtime.remember_once("idem:impression", impression.impression_id))
        self.repository.persist_rendered_impression(impression.impression_id, impression.dict())
        self.assertFalse(self.runtime.remember_once("idem:impression", impression.impression_id))

        playback = PlaybackEventRequest(
            event_id="evt_once",
            event_type=PlaybackEventType.PLAYBACK_START,
            session_id="sess_test",
            user_id="user_test",
            track_id="trk_001",
            request_id="req_once",
            impression_id="imp_once",
            position_ms=0,
            playback_ms=0,
            occurred_at=datetime.now(timezone.utc),
            client_event_seq=1,
        )
        self.assertTrue(self.runtime.remember_once("idem:playback", playback.event_id))
        self.repository.persist_playback_event(playback.event_id, playback.dict())
        self.assertFalse(self.runtime.remember_once("idem:playback", playback.event_id))
        self.assertEqual(len(self.repository.playback_events), 1)

    def test_stream_proxy_fail_closed(self) -> None:
        payload, media_type = self.media.stream_bytes("trk_001")
        self.assertEqual(media_type, "audio/wav")
        self.assertGreater(len(payload), 100)

        with self.assertRaises(LookupError):
            self.media.stream_bytes("trk_missing")
        with self.assertRaises(LookupError):
            self.media.resolve_playable_track("trk_quarantined")

    def test_fixture_beep_is_non_default_debug_mode(self) -> None:
        from infra.scripts.generate_fixture_music import generate_fixture_music

        output = PROJECT_ROOT / ".local" / "test_fixture_music"
        generate_fixture_music(
            PROJECT_ROOT / "fixtures" / "demo_catalog.json",
            output,
            PROJECT_ROOT / "does-not-exist",
            allow_beep_fallback=True,
        )
        media = MediaAccessService(
            self.repository,
            AppConfig(
                runtime_mode="fixture",
                database_url="",
                redis_url="",
                fixture_path=PROJECT_ROOT / "fixtures" / "demo_catalog.json",
                session_id="sess_test",
                user_id="user_test",
                media_mode="fixture_beep",
                music_root=output,
                navidrome_base_url="http://navidrome:4533",
                navidrome_username="spotiboys",
                navidrome_password="spotiboys",
                navidrome_token="",
                navidrome_salt="",
                serving_bundle_path=PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1",
                object_storage_root=PROJECT_ROOT / ".local" / "object_storage",
                object_storage_endpoint="file://.local/object_storage",
                mlflow_tracking_uri="http://mlflow:5000",
                require_full_ml_pipeline=False,
            ),
        )
        payload, media_type = media.stream_bytes("trk_001")
        self.assertEqual(media_type, "audio/wav")
        self.assertGreater(len(payload), 100)

    def test_navidrome_unavailable_fails_closed(self) -> None:
        media = MediaAccessService(
            self.repository,
            AppConfig(
                runtime_mode="fixture",
                database_url="",
                redis_url="",
                fixture_path=PROJECT_ROOT / "fixtures" / "demo_catalog.json",
                session_id="sess_test",
                user_id="user_test",
                media_mode="navidrome_fixture",
                music_root=PROJECT_ROOT / ".local" / "test_fixture_music",
                navidrome_base_url="http://127.0.0.1:9",
                navidrome_username="spotiboys",
                navidrome_password="spotiboys",
                navidrome_token="",
                navidrome_salt="",
                serving_bundle_path=PROJECT_ROOT / "fixtures" / "serving_bundle" / "Real_service" / "demo-fixture-v1",
                object_storage_root=PROJECT_ROOT / ".local" / "object_storage",
                object_storage_endpoint="file://.local/object_storage",
                mlflow_tracking_uri="http://mlflow:5000",
                require_full_ml_pipeline=False,
            ),
        )
        with self.assertRaises(LookupError):
            media.stream_bytes("trk_001")

    def test_model_stack_c2_c3_c4_all_affect_recommendation_path(self) -> None:
        response = self.recommender.recommend_next(
            RecommendationRequest(session_id="sess_model", user_id="user_demo", seed_track_ids=["trk_010"])
        )
        trace = self.recommender.last_pipeline_trace
        self.assertIsNotNone(trace)
        assert trace is not None
        self.assertGreater(trace.c2_candidate_count, 0)
        self.assertIn("cooc_session", trace.c2_sources)
        self.assertIn("popularity", trace.c2_sources)
        self.assertTrue(trace.c3_ranker_invoked)
        self.assertTrue(trace.c4_policy_invoked)
        self.assertNotEqual([item.track_id for item in response.queue.items][:4], ["trk_004", "trk_001", "trk_007", "trk_002"])
        for item in response.queue.items:
            self.assertIsNotNone(self.repository.get_playable_track(item.track_id))

    def test_full_ml_pipeline_requirement_fails_without_real_runtime(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            RecommendationService(
                self.repository,
                self.runtime,
                self.bundle,
                require_full_ml_pipeline=True,
            )
        self.assertIn("Full C1-C4 ML pipeline is required", str(raised.exception))

    def test_policy_reranking_removes_recent_tracks(self) -> None:
        first = self.recommender.recommend_next(RecommendationRequest(session_id="sess_recent", user_id="user_demo"))
        self.runtime.set_queue("sess_recent", first.queue.items[:2])
        second = self.recommender.recommend_next(RecommendationRequest(session_id="sess_recent", user_id="user_demo"))
        removed = self.recommender.last_pipeline_trace.c4_removed_track_ids  # type: ignore[union-attr]
        self.assertIn(first.queue.items[0].track_id, removed)
        self.assertNotIn(first.queue.items[0].track_id, [item.track_id for item in second.queue.items])

    def test_dislike_feedback_changes_future_policy_filtering(self) -> None:
        first = self.recommender.recommend_next(RecommendationRequest(session_id="sess_dislike", user_id="user_demo"))
        disliked_track = first.queue.items[0]
        feedback = FeedbackEventRequest(
            event_id="evt_dislike_once",
            feedback_type="dislike",
            session_id="sess_dislike",
            user_id="user_demo",
            track_id=disliked_track.track_id,
            request_id=disliked_track.request_id,
            impression_id=disliked_track.impression_id,
            occurred_at=datetime.now(timezone.utc),
        )
        self.repository.persist_feedback_event(feedback.event_id, feedback.dict())

        second = self.recommender.recommend_next(RecommendationRequest(session_id="sess_dislike_next", user_id="user_demo"))
        self.assertNotIn(disliked_track.track_id, [item.track_id for item in second.queue.items])
        trace = self.recommender.last_pipeline_trace
        self.assertIsNotNone(trace)
        assert trace is not None
        self.assertIn(disliked_track.track_id, trace.c4_removed_track_ids)

    def test_auth_session_repository_contract(self) -> None:
        password_hash = hash_password("spotiboys-password")
        self.assertTrue(verify_password("spotiboys-password", password_hash))
        self.assertFalse(verify_password("wrong-password", password_hash))
        user = self.repository.create_user(
            new_user_id(),
            "listener@example.com",
            password_hash,
            "Listener",
        )
        token = new_session_token()
        auth_session = self.repository.create_auth_session(
            token_hash=hash_session_token(token),
            user_id=user["user_id"],
            session_id=new_session_id(),
            expires_at=session_expires_at(),
        )
        payload = AuthResponse(
            user=AuthUser(
                user_id=auth_session.user_id,
                email=auth_session.email,
                display_name=auth_session.display_name,
            ),
            session_id=auth_session.session_id,
        )
        self.assertEqual(payload.user.email, "listener@example.com")
        self.assertIsNotNone(self.repository.get_auth_session(hash_session_token(token)))
        self.assertTrue(self.repository.revoke_auth_session(hash_session_token(token)))
        self.assertIsNone(self.repository.get_auth_session(hash_session_token(token)))

    def test_manifest_validators(self) -> None:
        serving_manifest = json.loads((PROJECT_ROOT / "fixtures" / "serving_bundle_manifest.json").read_text())
        validate_serving_bundle_manifest(serving_manifest)

        bad_manifest = dict(serving_manifest)
        bad_manifest["artifacts"] = list(serving_manifest["artifacts"]) + ["track_index.faiss"]
        with self.assertRaises(ValueError):
            validate_serving_bundle_manifest(bad_manifest)

        delta_manifest = json.loads((PROJECT_ROOT / "fixtures" / "delta_manifest.json").read_text())
        validate_delta_manifest(delta_manifest)


if __name__ == "__main__":
    unittest.main()
