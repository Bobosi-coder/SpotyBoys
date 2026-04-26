from __future__ import annotations

import csv
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime
from packages.db_access.repositories import PlayableTrackRecord


AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}


def _navidrome_request(config, url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Remote-User": str(config.navidrome_username)})


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
    missing_required = 0
    quarantine_rows = 0
    duplicate_track_ids = 0
    mapping_failures = 0

    mapped_track_map: dict[str, str] = {}
    if hasattr(repository, "get_mapped_track_map"):
        mapped_track_map = repository.get_mapped_track_map()
    elif hasattr(repository, "get_mapped_track_ids"):
        mapped_track_map = {str(track_id): "" for track_id in repository.get_mapped_track_ids()}
    already_mapped = set(mapped_track_map)
    skipped = len([r for r in rows if str(r["track_id"]) in already_mapped])
    duplicate_track_ids = _count_duplicate_track_ids(rows)
    missing_required = _count_missing_required_rows(rows)
    quarantine_rows = sum(1 for row in rows if row.get("quarantine_reason"))
    print(
        f"catalog sync starting: total={total} limit={limit or 'none'} "
        f"already_mapped={len(already_mapped)} will_skip={skipped}",
        flush=True,
    )

    # Bulk-fetch all Navidrome songs once (~220 paginated API calls for 110k tracks)
    # instead of one search3.view call per track (would be 110k calls / ~6 hours).
    navidrome_map: dict[str, str] = {}
    if config.media_mode in {"navidrome_fixture", "navidrome_vm_library", "navidrome"}:
        print("pre-fetching all Navidrome songs in bulk ...", flush=True)
        navidrome_map = _fetch_all_navidrome_songs(config)
    refresh_metadata = os.environ.get("SPOTIBOYS_REFRESH_CATALOG_METADATA", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    for index, row in enumerate(rows, start=1):
        track_id = str(row["track_id"])

        if hasattr(repository, "upsert_playable_track") and (refresh_metadata or track_id not in already_mapped):
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

        navidrome_track_id = navidrome_map.get(track_id) or row.get("navidrome_track_id")

        # Skip only when the current Navidrome scan still uses the same song id.
        # Navidrome can regenerate opaque ids after its DB is rebuilt; in that
        # case stale Postgres mappings make recommendation playback fail.
        if (
            track_id in already_mapped
            and mapped_track_map.get(track_id)
            and navidrome_track_id
            and mapped_track_map.get(track_id) == str(navidrome_track_id)
        ):
            if index == 1 or index % progress_every == 0 or index == total:
                _print_progress(index, total, synced, started_at)
            continue

        if not navidrome_track_id:
            if track_id in already_mapped:
                if index == 1 or index % progress_every == 0 or index == total:
                    _print_progress(index, total, synced, started_at)
                continue
            mapping_failures += 1
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
    report = _build_ingestion_quality_report(
        total_rows=total,
        missing_required=missing_required,
        duplicate_track_ids=duplicate_track_ids,
        mapped_rows=synced + skipped,
        quarantine_rows=quarantine_rows,
    )
    report_path = _write_quality_report(config.object_storage_root, "ingestion", report)
    print(f"catalog sync quality report written to {report_path}", flush=True)
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


def _count_missing_required_rows(rows: list[dict]) -> int:
    required = ("track_id", "title", "artist")
    missing = 0
    for row in rows:
        if any(not str(row.get(key, "")).strip() for key in required):
            missing += 1
    return missing


def _count_duplicate_track_ids(rows: list[dict]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for row in rows:
        track_id = str(row.get("track_id", "")).strip()
        if not track_id:
            continue
        if track_id in seen:
            duplicates += 1
            continue
        seen.add(track_id)
    return duplicates


def _build_ingestion_quality_report(
    *,
    total_rows: int,
    missing_required: int,
    duplicate_track_ids: int,
    mapped_rows: int,
    quarantine_rows: int,
) -> dict:
    total = max(total_rows, 1)
    metrics = {
        "total_rows": total_rows,
        "missing_required_rate": missing_required / total,
        "duplicate_track_id_rate": duplicate_track_ids / total,
        "mapping_success_rate": mapped_rows / total,
        "quarantine_rate": quarantine_rows / total,
    }
    thresholds = {
        "missing_required_rate_max": 0.01,
        "duplicate_track_id_rate_max": 0.01,
        "mapping_success_rate_min": 0.90,
        "quarantine_rate_max": 0.10,
    }
    notes: list[str] = []
    if metrics["missing_required_rate"] > thresholds["missing_required_rate_max"]:
        notes.append("missing_required_rate exceeded threshold")
    if metrics["duplicate_track_id_rate"] > thresholds["duplicate_track_id_rate_max"]:
        notes.append("duplicate_track_id_rate exceeded threshold")
    if metrics["mapping_success_rate"] < thresholds["mapping_success_rate_min"]:
        notes.append("mapping_success_rate fell below threshold")
    if metrics["quarantine_rate"] > thresholds["quarantine_rate_max"]:
        notes.append("quarantine_rate exceeded threshold")
    return {
        "stage": "ingestion",
        "status": "fail" if notes else "ok",
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "metrics": metrics,
        "thresholds": thresholds,
        "notes": notes,
    }


def _write_quality_report(root: Path, stage: str, payload: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_dir = root / "quality_reports" / stage
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{stamp}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


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


def _fetch_all_navidrome_songs(config) -> dict[str, str]:
    """
    Paginates through every song in Navidrome using search3.view with an empty query.
    Returns {track_id_stem: navidrome_song_id}.

    For a 110k-track library this is ~220 HTTP calls instead of 110k.
    Songs are indexed by path stem (e.g. "/music/1977186.mp3" → "1977186").
    """
    page_size = 500
    offset = 0
    mapping: dict[str, str] = {}
    base_params = {
        "u": config.navidrome_username,
        "v": "1.16.1",
        "c": "spotiboys",
        "f": "json",
        "query": "",
        "songCount": page_size,
        "albumCount": 0,
        "artistCount": 0,
    }
    while True:
        params = {**base_params, "songOffset": offset}
        url = f"{config.navidrome_base_url}/rest/search3.view?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(_navidrome_request(config, url), timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            print(f"warning: Navidrome bulk fetch failed at offset {offset}: {exc}", flush=True)
            break
        body = payload.get("subsonic-response", {})
        if body.get("status") != "ok":
            print(f"warning: Navidrome returned non-ok status at offset {offset}", flush=True)
            break
        songs = list(body.get("searchResult3", {}).get("song", []) or [])
        if not songs:
            break
        for song in songs:
            song_id = song.get("id")
            if not song_id:
                continue
            # Index by file path stem (primary: "/music/1977186.mp3" → "1977186")
            path = str(song.get("path") or "")
            stem = Path(path).stem if path else ""
            if stem:
                mapping[stem] = str(song_id)
            # Also index by title when it's a numeric track_id stored as title
            title = str(song.get("title") or "")
            if title.isdigit():
                mapping.setdefault(title, str(song_id))
        offset += len(songs)
        if offset % 10000 == 0:
            print(f"  bulk fetch progress: {offset} songs indexed ...", flush=True)
        if len(songs) < page_size:
            break
    print(f"Navidrome bulk fetch complete: {len(mapping)} songs indexed", flush=True)
    return mapping


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
        "v": "1.16.1",
        "c": "spotiboys",
        "f": "json",
        "query": query_text,
    }
    url = f"{config.navidrome_base_url}/rest/search3.view?{urllib.parse.urlencode(query)}"
    try:
        with urllib.request.urlopen(_navidrome_request(config, url), timeout=10) as response:
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
