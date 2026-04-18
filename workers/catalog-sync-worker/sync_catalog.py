from __future__ import annotations

import json
from pathlib import Path

from packages.config import load_config
from packages.db_access.factory import build_repository_and_runtime


def sync_catalog() -> int:
    config = load_config()
    repository, _runtime = build_repository_and_runtime(config)
    payload = json.loads(Path(config.fixture_path).read_text(encoding="utf-8"))
    synced = 0
    for row in payload["tracks"]:
        navidrome_track_id = row.get("navidrome_track_id")
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


if __name__ == "__main__":
    count = sync_catalog()
    print(f"synced {count} canonical tracks into playable Navidrome mappings")
