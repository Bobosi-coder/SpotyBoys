from __future__ import annotations

import csv
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.db_access.repositories import PlayableTrackRecord


AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}


def sync_catalog() -> int:
    config = load_config()
    repository, _runtime = build_repository_and_runtime(config)
    rows = _catalog_rows(config)
    limit = int(os.environ.get("SPOTIBOYS_CATALOG_SYNC_LIMIT", "0") or "0")
    if limit > 0:
        rows = rows[:limit]
    total = len(rows)
    progress_every = max(1, int(os.environ.get("SPOTIBOYS_CATALOG_PROGRESS_EVERY", "500") or "500"))
    started_at = time.monotonic()
    synced = 0

    already_mapped: set = set()
    if hasattr(repository, "get_mapped_track_ids"):
        already_mapped = repository.get_mapped_track_ids()
    skipped = len([r for r in rows if str(r["track_id"]) in already_mapped])
    print(
        f"catalog sync starting: total={total} limit={limit or 'none'} "
        f"already_mapped={len(already_mapped)} will_skip={skipped}",
        flush=True,
    )

    for index, row in enumerate(rows, start=1):
        track_id = str(row["track_id"])

        # Skip tracks already mapped — avoids Navidrome API calls on restarts.
        if track_id in already_mapped:
            if index == 1 or index % progress_every == 0 or index == total:
                _print_progress(index, total, synced, started_at)
            continue

        if hasattr(repository, "upsert_playable_track"):
            repository.upsert_playable_track(
                PlayableTrackRecord(
                    track_id=track_id,
                    title=str(row["title"]),
                    artist=str(row.get("artist") or "Unknown Artist"),
                    album=str(row.get("album") or ""),
                    duration_sec=int(float(row.get("duration_sec") or 30)),
                    cover_art_url=str(row.get("cover_art_url") or f"/covers/{track_id}"),
                    is_playable=True,
                    navidrome_track_id=row.get("navidrome_track_id"),
                    availability_status=str(row.get("availability_status", "available")),
                    quarantine_reason=row.get("quarantine_reason"),
                )
            )
        navidrome_track_id = _resolve_navidrome_id(config, row) or row.get("navidrome_track_id")
        if not navidrome_track_id:
            if index == 1 or index % progress_every == 0 or index == total:
                _print_progress(index, total, synced, started_at)
            continue
        availability = str(row.get("availability_status", "available"))
        repository.upsert_playable_mapping(
            track_id,
            str(navidrome_track_id),
            mapping_confidence=float(row.get("mapping_confidence", 1.0)),
            availability_status=availability,
            quarantine_reason=row.get("quarantine_reason"),
        )
        synced += 1
        if index == 1 or index % progress_every == 0 or index == total:
            _print_progress(index, total, synced, started_at)
    return synced


def _print_progress(processed: int, total: int, synced: int, started_at: float) -> None:
    elapsed = max(0.001, time.monotonic() - started_at)
    rate = processed / elapsed
    remaining = max(0, total - processed)
    eta_seconds = int(remaining / rate) if rate > 0 else 0
    percent = (processed * 100.0 / total) if total else 100.0
    print(
        "catalog sync progress: "
        f"processed={processed}/{total} "
        f"percent={percent:.2f}% "
        f"synced={synced} "
        f"rate={rate:.1f}/s "
        f"elapsed={_format_seconds(int(elapsed))} "
        f"eta={_format_seconds(eta_seconds)}",
        flush=True,
    )


def _format_seconds(seconds: int) -> str:
    minutes, sec = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def _catalog_rows(config) -> list[dict]:
    if config.media_mode == "navidrome_vm_library":
        rows = _rows_from_music_manifest(config.music_root / "manifest.csv")
        if rows:
            return rows
        return _rows_from_audio_files(config.music_root)
    payload = json.loads(Path(config.fixture_path).read_text(encoding="utf-8"))
    return list(payload["tracks"])


def _rows_from_music_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            track_id = _first(raw, "track_id", "id", "song_id", "item_id", "track", "tid")
            file_value = _first(raw, "path", "file", "filename", "filepath", "relative_path")
            if not track_id and file_value:
                track_id = Path(file_value).stem
            if not track_id:
                continue
            title = _first(raw, "title", "track_title", "song", "name") or str(track_id)
            artist = _first(raw, "artist", "artist_name", "creator") or "Unknown Artist"
            album = _first(raw, "album", "album_name") or ""
            duration = _first(raw, "duration_sec", "duration", "length_sec", "seconds") or 0
            rows.append(
                {
                    "track_id": str(track_id),
                    "title": str(title),
                    "artist": str(artist),
                    "album": str(album),
                    "duration_sec": duration,
                    "cover_art_url": f"/covers/{track_id}",
                    "availability_status": "available",
                    "search_terms": [str(track_id), str(title), str(file_value or "")],
                }
            )
    return rows


def _rows_from_audio_files(root: Path) -> list[dict]:
    if not root.exists():
        return []
    rows: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        track_id = path.stem
        rows.append(
            {
                "track_id": track_id,
                "title": track_id,
                "artist": "Unknown Artist",
                "album": "",
                "duration_sec": 0,
                "cover_art_url": f"/covers/{track_id}",
                "availability_status": "available",
                "search_terms": [track_id, path.name],
            }
        )
    return rows


def _first(row: dict, *names: str) -> str | None:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _resolve_navidrome_id(config, row: dict) -> str | None:
    if config.media_mode not in {"navidrome_fixture", "navidrome_vm_library", "navidrome"}:
        return None
    songs: list[dict] = []
    for term in [str(row["track_id"]), str(row.get("title", "")), *list(row.get("search_terms", []))]:
        if not term:
            continue
        songs = _search_songs(config, term)
        if songs:
            break
    marker = str(row["track_id"]).lower()
    for song in songs:
        haystack = " ".join(str(song.get(key, "")) for key in ["id", "title", "path", "album", "artist"]).lower()
        if marker in haystack:
            return str(song["id"])
    return str(songs[0]["id"]) if songs else None


def _search_songs(config, query_text: str) -> list[dict]:
    query = {
        "u": config.navidrome_username,
        "p": config.navidrome_password,
        "v": "1.16.1",
        "c": "spotiboys",
        "f": "json",
        "query": query_text,
    }
    url = f"{config.navidrome_base_url}/rest/search3.view?{urllib.parse.urlencode(query)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    body = payload.get("subsonic-response", {})
    if body.get("status") != "ok":
        return []
    return list(body.get("searchResult3", {}).get("song", []) or [])


if __name__ == "__main__":
    count = sync_catalog()
    print(f"synced {count} canonical tracks into playable Navidrome mappings")
