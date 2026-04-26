from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import time
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
    candidates = [name for name in preferred if name in tables]
    for table in sorted(tables):
        if table in candidates:
            continue
        columns = _table_columns(conn, table)
        if "path" in columns and ("title" in columns or "id" in columns):
            candidates.append(table)
    return candidates


def _find_navidrome_db(root: Path = Path("/data")) -> Path | None:
    candidates = [
        DEFAULT_DB,
        root / "navidrome.db",
        root / "db" / "navidrome.db",
    ]
    if root.exists():
        candidates.extend(sorted(root.rglob("*.db")))
        candidates.extend(sorted(root.rglob("*.sqlite")))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _wait_for_db(db_path: Path, timeout_seconds: int) -> Path | None:
    deadline = time.monotonic() + max(0, timeout_seconds)
    root = db_path.parent if db_path.parent.exists() else Path("/data")
    while True:
        if db_path.exists():
            return db_path
        found = _find_navidrome_db(root)
        if found:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(2)


def _media_table(conn: sqlite3.Connection) -> tuple[str, set[str]] | None:
    for table in _candidate_tables(conn):
        columns = _table_columns(conn, table)
        if "path" in columns:
            return table, columns
    return None


def _media_count(conn: sqlite3.Connection) -> tuple[str, int] | None:
    media = _media_table(conn)
    if not media:
        return None
    table, _columns = media
    count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return table, count


def _wait_for_scan_rows(
    db_path: Path,
    *,
    min_rows: int,
    timeout_seconds: int,
    poll_seconds: int = 10,
) -> None:
    deadline = time.monotonic() + max(0, timeout_seconds)
    last_count: int | None = None
    while True:
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("PRAGMA busy_timeout = 30000")
                result = _media_count(conn)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            result = None
            print(f"waiting for Navidrome scan: sqlite not ready yet: {exc}", flush=True)

        if result:
            table, count = result
            if count != last_count:
                print(
                    f"waiting for Navidrome scan: table={table} rows={count} target={min_rows}",
                    flush=True,
                )
                last_count = count
            if count >= min_rows:
                return

        if time.monotonic() >= deadline:
            current = last_count if last_count is not None else 0
            raise TimeoutError(
                f"Navidrome scan did not reach {min_rows} rows before timeout; current rows={current}"
            )
        time.sleep(max(1, poll_seconds))


def sync_metadata(
    db_path: Path,
    manifest_path: Path,
    *,
    wait_seconds: int = 300,
    scan_wait_seconds: int = 1800,
    min_ready_ratio: float = 0.9,
) -> int:
    db_path = _wait_for_db(db_path, wait_seconds) or db_path
    if not db_path.exists():
        raise FileNotFoundError(f"Navidrome DB not found under {db_path.parent}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"music manifest not found: {manifest_path}")

    rows = _manifest_rows(manifest_path)
    if not rows:
        return 0
    min_rows = max(1, int(len(rows) * min_ready_ratio))
    _wait_for_scan_rows(db_path, min_rows=min_rows, timeout_seconds=scan_wait_seconds)

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
            sql = f"UPDATE {table} SET {assignments} WHERE path = ? OR path LIKE ? OR title = ?"
            for row in rows:
                values = [row[name] for name in set_columns]
                filename = f"{row['track_id']}.mp3"
                cur = conn.execute(sql, [*values, filename, f"%/{filename}", row["track_id"]])
                updated += cur.rowcount if cur.rowcount > 0 else 0
            conn.commit()
            if updated == 0:
                raise RuntimeError(
                    f"found Navidrome media table {table} but no metadata rows matched manifest track ids"
                )
            return updated
    finally:
        conn.close()
    print("warning: could not find a Navidrome media table with path/title metadata columns", flush=True)
    return 0


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--wait-seconds", type=int, default=int(os.environ.get("SPOTIBOYS_NAVIDROME_DB_WAIT_SECONDS", "300")))
    parser.add_argument(
        "--scan-wait-seconds",
        type=int,
        default=int(os.environ.get("SPOTIBOYS_NAVIDROME_SCAN_WAIT_SECONDS", "1800")),
    )
    parser.add_argument(
        "--min-ready-ratio",
        type=float,
        default=float(os.environ.get("SPOTIBOYS_NAVIDROME_SCAN_READY_RATIO", "0.9")),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    updated = sync_metadata(
        args.db,
        args.manifest,
        wait_seconds=args.wait_seconds,
        scan_wait_seconds=args.scan_wait_seconds,
        min_ready_ratio=args.min_ready_ratio,
    )
    print(f"Navidrome metadata rows updated: {updated}", flush=True)


if __name__ == "__main__":
    main()
