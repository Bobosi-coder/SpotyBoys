from __future__ import annotations

import io
import json
import math
import urllib.parse
import urllib.request
import wave
from typing import Optional, Tuple

from packages.config import AppConfig
from packages.db_access.repositories import DemoRepository
from packages.shared_contracts.schemas import PlayableTrackResponse


class MediaAccessService:
    def __init__(self, repository: DemoRepository, config: Optional[AppConfig] = None) -> None:
        self.repository = repository
        self.config = config

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
        if not self.config or self.config.media_mode == "fixture_beep":
            return build_demo_wav_bytes(track_id), "audio/wav"
        if self.config.media_mode == "fixture-file":
            path = self.config.music_root / f"{track.navidrome_track_id}.wav"
            if not path.exists():
                raise LookupError("mapped fixture media file is missing")
            return path.read_bytes(), "audio/wav"
        if self.config.media_mode in {"navidrome", "navidrome_fixture", "navidrome_vm_library"}:
            return self._stream_from_navidrome(str(track.navidrome_track_id))
        raise LookupError(f"unsupported media mode: {self.config.media_mode}")

    def _stream_from_navidrome(self, navidrome_track_id: str) -> Tuple[bytes, str]:
        assert self.config is not None
        query = {
            "u": self.config.navidrome_username,
            "v": "1.16.1",
            "c": "spotiboys",
            "f": "json",
            "id": navidrome_track_id,
        }
        url = f"{self.config.navidrome_base_url}/rest/stream.view?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={"Remote-User": str(self.config.navidrome_username)},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                content_type = response.headers.get_content_type() or "application/octet-stream"
                payload = response.read()
                if content_type == "application/json":
                    body = json.loads(payload.decode("utf-8"))
                    if body.get("subsonic-response", {}).get("status") != "ok":
                        raise LookupError("Navidrome stream request was rejected")
                return payload, content_type
        except Exception as exc:
            raise LookupError("Navidrome stream is unavailable") from exc


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
