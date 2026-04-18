from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from packages.config import load_config
from packages.db_access.postgres import PostgresRepository


def export_delta(version: str | None = None) -> Path:
    config = load_config()
    repo = PostgresRepository(config.database_url)
    stamp = version or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = config.object_storage_root / "proj23-mlflow-artifacts" / "session_event" / "delta" / stamp
    output.mkdir(parents=True, exist_ok=True)
    row_counts = {
        "session_tracks.parquet": _export_csv(repo, output / "session_tracks.parquet", "playback_events"),
        "session_meta.parquet": _export_csv(repo, output / "session_meta.parquet", "recommendation_impressions"),
        "love.parquet": _export_csv(repo, output / "love.parquet", "feedback_events"),
    }
    manifest = {
        "version": stamp,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "output_files": ["session_tracks.parquet", "session_meta.parquet", "love.parquet", "manifest.json"],
        "row_counts": row_counts,
        "source": "vm1-postgres-parser",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output


def _export_csv(repo: PostgresRepository, path: Path, table: str) -> int:
    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM app.{table} ORDER BY 1")
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    print(export_delta())
