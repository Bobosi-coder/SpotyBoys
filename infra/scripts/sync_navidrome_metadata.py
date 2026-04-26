from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from typing import Iterable


DEFAULT_DB = Path("/data/navidrome.db")
DEFAULT_MANIFEST = Path("/music/manifest.csv")


def _first(row: dict, *names: str) -> str:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _manifest_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows: list[dict[str, str]] = []
        for raw in csv.DictReader(handle):
            track_id = _first(raw, "track_id", "id", "song_id", "item_id", "track", "tid")
            file_value = _first(raw, "path", "file", "filename", "filepath", "relative_path")
            if not track_id and file_value:
                track_id = Path(file_value).stem
            if not track_id:
                continue
            rows.append(
                {
                    "track_id": track_id,
                    "title": _first(raw, "title", "track_title", "song", "name") or track_id,
                    "artist": _first(raw, "artist", "artist_name", "creator") or "Unknown Artist",
                    "album": _first(raw, "album", "album_name") or "",
                }
            )
    return rows


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _candidate_tables(conn: sqlite3.Connection) -> list[str]:
    tables = _existing_tables(conn)
    preferred = ["media_file", "media_files"]
    return [name for name in preferred if name in tables]


def _find_navidrome_db(root: Path = Path("/data")) -> Path | None:
    candidates = [
        DEFAULT_DB,
        root / "navidrome.db",
        root / "db" / "navidrome.db",
    ]
    candidates.extend(sorted(root.rglob("*.db")) if root.exists() else [])
    candidates.extend(sorted(root.rglob("*.sqlite")) if root.exists() else [])
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def sync_metadata(db_path: Path, manifest_path: Path) -> int:
    if not db_path.exists():
        found = _find_navidrome_db(db_path.parent if db_path.parent.exists() else Path("/data"))
        if found is None:
            print(f"warning: Navidrome DB not found: {db_path}; skipping metadata sync", flush=True)
            return 0
        db_path = found
    if not manifest_path.exists():
        print(f"warning: music manifest not found: {manifest_path}; skipping metadata sync", flush=True)
        return 0

    rows = _manifest_rows(manifest_path)
    if not rows:
        return 0

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        updated = 0
        for table in _candidate_tables(conn):
            columns = _table_columns(conn, table)
            if "path" not in columns:
                continue
            set_columns = [name for name in ("title", "artist", "album") if name in columns]
            if not set_columns:
                continue
            assignments = ", ".join(f"{name} = ?" for name in set_columns)
            sql = f"UPDATE {table} SET {assignments} WHERE path LIKE ? OR title = ?"
            for row in rows:
                values = [row[name] for name in set_columns]
                cur = conn.execute(sql, [*values, f"%/{row['track_id']}.%", row["track_id"]])
                updated += cur.rowcount if cur.rowcount > 0 else 0
            conn.commit()
            return updated
    finally:
        conn.close()
    print("warning: could not find a Navidrome media table with path/title metadata columns", flush=True)
    return 0


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(list(argv) if argv is not None else None)
    updated = sync_metadata(args.db, args.manifest)
    print(f"Navidrome metadata rows updated: {updated}", flush=True)


if __name__ == "__main__":
    main()
