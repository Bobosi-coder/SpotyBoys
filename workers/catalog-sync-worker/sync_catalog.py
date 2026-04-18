from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime


def sync_catalog() -> int:
    config = load_config()
    repository, _runtime = build_repository_and_runtime(config)
    payload = json.loads(Path(config.fixture_path).read_text(encoding="utf-8"))
    synced = 0
    for row in payload["tracks"]:
        navidrome_track_id = _resolve_navidrome_id(config, row) or row.get("navidrome_track_id")
        if not navidrome_track_id:
            continue
        availability = str(row.get("availability_status", "available"))
        repository.upsert_playable_mapping(
            str(row["track_id"]),
            str(navidrome_track_id),
            mapping_confidence=float(row.get("mapping_confidence", 1.0)),
            availability_status=availability,
            quarantine_reason=row.get("quarantine_reason"),
        )
        synced += 1
    return synced


def _resolve_navidrome_id(config, row: dict) -> str | None:
    if config.media_mode not in {"navidrome_fixture", "navidrome_vm_library", "navidrome"}:
        return None
    songs = _search_songs(config, str(row["track_id"]))
    if not songs:
        songs = _search_songs(config, str(row["title"]))
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
