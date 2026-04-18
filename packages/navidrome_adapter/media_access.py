from __future__ import annotations

import io
import math
import wave
from typing import Tuple

from packages.db_access.repositories import DemoRepository
from packages.shared_contracts.schemas import PlayableTrackResponse


class MediaAccessService:
    def __init__(self, repository: DemoRepository) -> None:
        self.repository = repository

    def resolve_playable_track(self, track_id: str) -> PlayableTrackResponse:
        track = self.repository.get_playable_track(track_id)
        if not track:
            raise LookupError("track is not currently playable")
        return PlayableTrackResponse(
            track_id=track.track_id,
            is_playable=True,
            stream_policy="proxy",
            stream_path=f"/stream/{track.track_id}",
            expires_at=None,
        )

    def stream_bytes(self, track_id: str) -> Tuple[bytes, str]:
        track = self.repository.get_playable_track(track_id)
        if not track:
            raise LookupError("track is not currently playable")
        return build_demo_wav_bytes(track_id), "audio/wav"


def build_demo_wav_bytes(track_id: str) -> bytes:
    """Generate a tiny deterministic sine wave so demo playback has real bytes."""

    sample_rate = 8000
    duration_sec = 1
    frequency = 220 + (sum(ord(ch) for ch in track_id) % 330)
    frames = bytearray()
    for index in range(sample_rate * duration_sec):
        value = int(32767 * 0.22 * math.sin(2 * math.pi * frequency * (index / sample_rate)))
        frames.extend(value.to_bytes(2, byteorder="little", signed=True))

    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return output.getvalue()

