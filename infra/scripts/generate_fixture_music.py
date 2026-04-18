from __future__ import annotations

import json
import shutil
from pathlib import Path

from packages.navidrome_adapter.media_access import build_demo_wav_bytes


DEFAULT_SOURCE_ROOT = Path("data/raw/audio_previews")


def generate_fixture_music(
    fixture_path: str | Path,
    output_dir: str | Path,
    source_root: str | Path = DEFAULT_SOURCE_ROOT,
    *,
    allow_beep_fallback: bool = False,
) -> None:
    payload = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    source_files = sorted(Path(source_root).glob("*.mp3"))
    playable_rows = [
        row
        for row in payload["tracks"]
        if row.get("navidrome_track_id") and row.get("availability_status") == "available"
    ]
    if len(source_files) < len(playable_rows) and not allow_beep_fallback:
        raise RuntimeError(
            f"Need at least {len(playable_rows)} real fixture mp3 files under {source_root}; "
            "set --allow-beep-fallback only for non-default tests."
        )
    for index, row in enumerate(playable_rows):
        navidrome_track_id = row.get("navidrome_track_id")
        artist = _safe(str(row["artist"]))
        album = _safe(str(row.get("album", "Fixture Album")))
        title = _safe(str(row["title"]))
        folder = root / artist / album
        folder.mkdir(parents=True, exist_ok=True)
        if index < len(source_files):
            target = folder / f"{row['track_id']}__{title}__{navidrome_track_id}.mp3"
            shutil.copyfile(source_files[index], target)
        else:
            target = folder / f"{row['track_id']}__{title}__{navidrome_track_id}.wav"
            target.write_bytes(build_demo_wav_bytes(str(row["track_id"])))


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {" ", "-", "_"} else "_" for ch in value).strip() or "unknown"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate the tiny local SpotyBoys fixture music library.")
    parser.add_argument("--fixture", default="fixtures/demo_catalog.json")
    parser.add_argument("--output-dir", default=".local/fixture_music")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--allow-beep-fallback", action="store_true")
    args = parser.parse_args()
    generate_fixture_music(args.fixture, args.output_dir, args.source_root, allow_beep_fallback=args.allow_beep_fallback)
    print(f"generated fixture music in {args.output_dir}")
