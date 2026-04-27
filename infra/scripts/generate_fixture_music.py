from __future__ import annotations

import json
import math
import wave
from pathlib import Path


def generate_fixture_music(
    catalog_path: str | Path,
    output_dir: str | Path,
    source_root: str | Path,
    *,
    allow_beep_fallback: bool = False,
) -> None:
    payload = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    output = Path(output_dir)
    source = Path(source_root)
    output.mkdir(parents=True, exist_ok=True)
    for row in payload.get("tracks", []):
        track_id = str(row["track_id"])
        target = output / f"{track_id}.wav"
        source_file = source / f"{track_id}.wav"
        if source_file.exists():
            target.write_bytes(source_file.read_bytes())
            continue
        if not allow_beep_fallback:
            raise FileNotFoundError(f"missing source audio for {track_id}")
        _write_beep(target, frequency=320 + (abs(hash(track_id)) % 300))


def _write_beep(path: Path, *, frequency: int) -> None:
    sample_rate = 8000
    duration_seconds = 1
    frames = bytearray()
    for index in range(sample_rate * duration_seconds):
        value = int(16000 * math.sin(2 * math.pi * frequency * index / sample_rate))
        frames.extend(int(value).to_bytes(2, byteorder="little", signed=True))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
