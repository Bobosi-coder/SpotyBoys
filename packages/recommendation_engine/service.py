from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Tuple

from packages.artifact_runtime import ServingBundle
from packages.db_access.repositories import DemoRepository, PlayableTrackRecord
from packages.db_access.runtime_state import InMemoryRuntimeState
from packages.recommendation_engine.pipeline import PipelineTrace, ServingRecommendationPipeline
from packages.shared_contracts.enums import BrowseSurfaceSlot, FallbackLevel
from packages.shared_contracts.schemas import (
    BrowseSurface,
    QueueItem,
    QueueUpdate,
    RecommendationRequest,
    RecommendationResponse,
    TrackItem,
    utc_now,
)


class RecommendationService:
    def __init__(
        self,
        repository: DemoRepository,
        runtime_state: InMemoryRuntimeState,
        serving_bundle: Optional[ServingBundle] = None,
        require_full_ml_pipeline: bool = False,
    ) -> None:
        self.repository = repository
        self.runtime_state = runtime_state
        self.serving_bundle = serving_bundle
        self.pipeline = (
            ServingRecommendationPipeline(serving_bundle, require_full_runtime=require_full_ml_pipeline)
            if serving_bundle
            else None
        )

    @property
    def model_version(self) -> str:
        if self.serving_bundle:
            return self.serving_bundle.model_version
        return "fixture-generated-fallback"

    @property
    def last_pipeline_trace(self) -> PipelineTrace | None:
        return self.pipeline.last_trace if self.pipeline else None

    def reload_serving_bundle(self, serving_bundle: ServingBundle, *, require_full_ml_pipeline: bool = False) -> None:
        self.serving_bundle = serving_bundle
        self.pipeline = ServingRecommendationPipeline(
            serving_bundle,
            require_full_runtime=require_full_ml_pipeline,
        )

    def build_bootstrap_surfaces(self, session_id: str, user_id: str) -> Tuple[BrowseSurface, List[QueueItem]]:
        request_id = f"req_bootstrap_{session_id}"
        impression_id = f"imp_bootstrap_{session_id}"
        return self._compose_surfaces(request_id, impression_id, session_id, user_id)

    def recommend_next(self, request: RecommendationRequest) -> RecommendationResponse:
        request_id = request.request_id or f"req_{uuid.uuid4().hex}"
        impression_id = f"imp_{uuid.uuid4().hex}"
        browse_surface, queue_items = self._compose_surfaces(request_id, impression_id, request.session_id, request.user_id)
        queue = self.runtime_state.set_queue(
            request.session_id,
            queue_items,
            fallback_level=FallbackLevel.NONE.value,
        )
        response = RecommendationResponse(
            request_id=request_id,
            impression_id=impression_id,
            model_version=self.model_version,
            fallback_level=FallbackLevel.NONE,
            browse_surface=browse_surface,
            queue=QueueUpdate(items=queue.items, revision=queue.revision),
        )
        self.repository.persist_recommendation_impression(
            impression_id,
            {
                "request_id": request_id,
                "session_id": request.session_id,
                "user_id": request.user_id,
                "model_version": self.model_version,
                "fallback_level": FallbackLevel.NONE.value,
                "browse_surface": response.browse_surface.dict(),
                "queue": response.queue.dict(),
                "created_at": utc_now().isoformat(),
            },
        )
        return response

    def _compose_surfaces(
        self,
        request_id: str,
        impression_id: str,
        session_id: str = "",
        user_id: str = "",
    ) -> Tuple[BrowseSurface, List[QueueItem]]:
        playable = self._rank_playable_tracks(
            self.repository.list_playable_tracks(),
            session_id=session_id,
            user_id=user_id,
        )
        featured = playable[:4]
        random_items = playable[4:14]
        queue_tracks = playable[:8]
        browse_surface = BrowseSurface(
            featured_items=[
                self._to_track_item(track, BrowseSurfaceSlot(f"featured_{idx}"))
                for idx, track in enumerate(featured, start=1)
            ],
            random_carousel_items=[
                self._to_track_item(track, BrowseSurfaceSlot(f"random_{idx}"))
                for idx, track in enumerate(random_items, start=1)
            ],
        )
        queue_items = [
            QueueItem(
                track_id=track.track_id,
                navidrome_track_id=track.navidrome_track_id,
                title=track.title,
                artist=track.artist,
                album=track.album,
                duration_sec=track.duration_sec,
                cover_art_url=track.cover_art_url,
                queue_position=idx,
                request_id=request_id,
                impression_id=impression_id,
            )
            for idx, track in enumerate(queue_tracks, start=1)
        ]
        return browse_surface, queue_items

    @staticmethod
    def _to_track_item(track: PlayableTrackRecord, slot: BrowseSurfaceSlot) -> TrackItem:
        return TrackItem(
            track_id=track.track_id,
            navidrome_track_id=track.navidrome_track_id,
            title=track.title,
            artist=track.artist,
            album=track.album,
            duration_sec=track.duration_sec,
            cover_art_url=track.cover_art_url,
            surface_slot=slot,
        )

    def _rank_playable_tracks(
        self,
        playable: List[PlayableTrackRecord],
        *,
        session_id: str = "",
        user_id: str = "",
    ) -> List[PlayableTrackRecord]:
        if self.pipeline:
            ranked = self.pipeline.recommend(
                playable,
                user_id=user_id or "user_demo",
                recent_track_ids=self.runtime_state.recent_track_ids(session_id) if session_id else [],
                disliked_track_ids=self.repository.list_disliked_track_ids(user_id) if user_id else [],
            )
            if ranked:
                return ranked
            return playable
        if not self.serving_bundle:
            return playable
        track_by_id: Dict[str, PlayableTrackRecord] = {track.track_id: track for track in playable}
        ranked: List[PlayableTrackRecord] = []
        seen = set()
        for track_id in self.serving_bundle.ranked_track_ids():
            track = track_by_id.get(track_id)
            if track:
                ranked.append(track)
                seen.add(track_id)
        ranked.extend(track for track in playable if track.track_id not in seen)
        return ranked
