from __future__ import annotations

import json
from pathlib import Path

from packages.navidrome_adapter.media_access import build_demo_wav_bytes


def generate_fixture_music(fixture_path: str | Path, output_dir: str | Path) -> None:
    payload = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    for row in payload["tracks"]:
        navidrome_track_id = row.get("navidrome_track_id")
        if not navidrome_track_id or row.get("availability_status") != "available":
            continue
        artist = _safe(str(row["artist"]))
        album = _safe(str(row.get("album", "Fixture Album")))
        title = _safe(str(row["title"]))
        folder = root / artist / album
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{title}__{navidrome_track_id}.wav").write_bytes(build_demo_wav_bytes(str(row["track_id"])))
        # Also write a flat ID-addressable copy for fast fixture-file mode.
        (root / f"{navidrome_track_id}.wav").write_bytes(build_demo_wav_bytes(str(row["track_id"])))


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {" ", "-", "_"} else "_" for ch in value).strip() or "unknown"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate the tiny local SpotyBoys fixture music library.")
    parser.add_argument("--fixture", default="fixtures/demo_catalog.json")
    parser.add_argument("--output-dir", default=".local/fixture_music")
    args = parser.parse_args()
    generate_fixture_music(args.fixture, args.output_dir)
    print(f"generated fixture music in {args.output_dir}")
