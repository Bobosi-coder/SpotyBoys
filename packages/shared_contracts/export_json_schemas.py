from __future__ import annotations

import json
from pathlib import Path

from .schemas import (
    BootstrapResponse,
    FeedbackEventRequest,
    ImpressionEventRequest,
    PlayableTrackResponse,
    PlaybackEventRequest,
    RecommendationRequest,
    RecommendationResponse,
)

SCHEMAS = {
    "bootstrap_response": BootstrapResponse,
    "recommendation_request": RecommendationRequest,
    "recommendation_response": RecommendationResponse,
    "impression_event_request": ImpressionEventRequest,
    "playback_event_request": PlaybackEventRequest,
    "feedback_event_request": FeedbackEventRequest,
    "playable_track_response": PlayableTrackResponse,
}


def export_schemas(output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        path = destination / f"{name}.schema.json"
        path.write_text(json.dumps(model.schema(), indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    export_schemas(Path("packages/shared_contracts/json_schema"))
