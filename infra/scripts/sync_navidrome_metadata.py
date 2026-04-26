from __future__ import annotations

import argparse
import csv
import hashlib
import json
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


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _artist_id(name: str) -> str:
    normalized = " ".join((name or "Unknown Artist").strip().lower().split())
    return "spb-artist-" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]


def _participants_json(artist_id: str, artist: str) -> str:
    participant = {"id": artist_id, "name": artist, "subRole": ""}
    return json.dumps(
        {
            "artist": [participant],
            "albumartist": [participant],
        },
        separators=(",", ":"),
    )


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


def _load_temp_metadata(conn: sqlite3.Connection, rows: list[dict[str, str]]) -> None:
    conn.execute("DROP TABLE IF EXISTS temp.spotiboys_metadata")
    conn.execute(
        """
        CREATE TEMP TABLE spotiboys_metadata (
            track_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            artist_id TEXT NOT NULL,
            participants TEXT NOT NULL,
            album TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO spotiboys_metadata (track_id, filename, title, artist, artist_id, participants, album)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            filename = excluded.filename,
            title = excluded.title,
            artist = excluded.artist,
            artist_id = excluded.artist_id,
            participants = excluded.participants,
            album = excluded.album
        """,
        [
            (
                row["track_id"],
                f"{row['track_id']}.mp3",
                row["title"],
                row["artist"],
                _artist_id(row["artist"]),
                _participants_json(_artist_id(row["artist"]), row["artist"]),
                row["album"],
            )
            for row in rows
        ],
    )
    conn.execute("CREATE INDEX temp.idx_spotiboys_metadata_filename ON spotiboys_metadata(filename)")
    conn.execute("CREATE INDEX temp.idx_spotiboys_metadata_artist_id ON spotiboys_metadata(artist_id)")


def _bulk_update_metadata(conn: sqlite3.Connection, table: str, columns: set[str]) -> int:
    column_sources = {
        "title": "title",
        "artist": "artist",
        "artist_id": "artist_id",
        "album": "album",
        "album_artist": "artist",
        "album_artist_id": "artist_id",
        "order_artist_name": "lower(artist)",
        "order_album_artist_name": "lower(artist)",
        "sort_artist_name": "artist",
        "sort_album_artist_name": "artist",
        "participants": "participants",
        "search_participants": "artist",
        "search_normalized": "title || ' ' || album || ' ' || artist || ' ' || artist",
        "full_text": "title || ' ' || album || ' ' || artist || ' ' || artist",
    }
    update_columns = [name for name in column_sources if name in columns]
    if not update_columns:
        return 0
    quoted_table = _quote_identifier(table)

    total_updated = 0
    matchers = [
        ("filename", "m.filename = {table}.path"),
        ("track_id", "m.track_id = {table}.title"),
    ]
    for _name, matcher in matchers:
        assignments = ", ".join(
            f"{_quote_identifier(column)} = ("
            f"SELECT {column_sources[column]} FROM spotiboys_metadata m "
            f"WHERE {matcher.format(table=quoted_table)} LIMIT 1"
            f")"
            for column in update_columns
        )
        sql = f"""
            UPDATE {quoted_table}
            SET {assignments}
            WHERE EXISTS (
                SELECT 1
                FROM spotiboys_metadata m
                WHERE {matcher.format(table=quoted_table)}
            )
        """
        cur = conn.execute(sql)
        total_updated += cur.rowcount if cur.rowcount > 0 else 0
    return total_updated


def _sync_artists(conn: sqlite3.Connection, media_table: str) -> int:
    tables = _existing_tables(conn)
    required = {"artist", "media_file_artists", "library_artist"}
    if not required.issubset(tables):
        print("warning: Navidrome artist relationship tables not found; skipping artist sync", flush=True)
        return 0

    quoted_media = _quote_identifier(media_table)
    print("upserting Navidrome artist rows", flush=True)
    conn.execute(
        """
        INSERT OR IGNORE INTO artist (
            id, name, full_text, order_artist_name, sort_artist_name, search_normalized, missing
        )
        SELECT
            artist_id,
            artist,
            artist,
            lower(artist),
            artist,
            artist,
            false
        FROM (
            SELECT artist_id, MIN(artist) AS artist
            FROM spotiboys_metadata
            WHERE artist <> ''
            GROUP BY artist_id
        )
        """
    )
    conn.execute(
        """
        UPDATE artist
        SET name = src.artist,
            full_text = src.artist,
            order_artist_name = lower(src.artist),
            sort_artist_name = src.artist,
            search_normalized = src.artist,
            missing = false,
            updated_at = current_time
        FROM (
            SELECT artist_id, MIN(artist) AS artist
            FROM spotiboys_metadata
            WHERE artist <> ''
            GROUP BY artist_id
        ) src
        WHERE artist.id = src.artist_id
        """
    )

    print("rewiring Navidrome media_file_artists rows", flush=True)
    conn.execute(
        f"""
        DELETE FROM media_file_artists
        WHERE role IN ('artist', 'albumartist')
          AND media_file_id IN (
            SELECT mf.id
            FROM {quoted_media} mf
            JOIN spotiboys_metadata m
              ON m.filename = mf.path OR m.track_id = mf.title
          )
        """
    )
    conn.execute(
        f"""
        INSERT OR IGNORE INTO media_file_artists (media_file_id, artist_id, role, sub_role)
        SELECT mf.id, m.artist_id, roles.role, ''
        FROM {quoted_media} mf
        JOIN spotiboys_metadata m
          ON m.filename = mf.path OR m.track_id = mf.title
        JOIN (
            SELECT 'artist' AS role
            UNION ALL
            SELECT 'albumartist' AS role
        ) roles
        """
    )
    relation_rows = int(conn.execute("SELECT changes()").fetchone()[0] or 0)

    print("upserting Navidrome library_artist rows", flush=True)
    conn.execute(
        """
        INSERT OR IGNORE INTO library_artist (library_id, artist_id, stats)
        SELECT DISTINCT 1, artist_id, '{}'
        FROM spotiboys_metadata
        WHERE artist_id <> ''
        """
    )
    print("refreshing Navidrome library_artist stats", flush=True)
    conn.execute("DROP TABLE IF EXISTS temp.spotiboys_artist_stats")
    conn.execute(
        f"""
        CREATE TEMP TABLE spotiboys_artist_stats AS
        WITH base AS (
            SELECT m.artist_id,
                   mf.library_id,
                   count(DISTINCT mf.album_id) AS album_count,
                   count(DISTINCT mf.id) AS media_count,
                   sum(mf.size) AS size
            FROM spotiboys_metadata m
            JOIN {quoted_media} mf
              ON m.filename = mf.path OR m.track_id = mf.title
            WHERE m.artist_id <> ''
            GROUP BY m.artist_id, mf.library_id
        )
        SELECT artist_id,
               library_id,
               json_object(
                   'albumartist', json_object('a', album_count, 'm', media_count, 's', coalesce(size, 0)),
                   'artist', json_object('a', album_count, 'm', media_count, 's', coalesce(size, 0)),
                   'total', json_object('a', album_count, 'm', media_count, 's', coalesce(size, 0)),
                   'maincredit', json_object('a', album_count, 'm', media_count, 's', coalesce(size, 0))
               ) AS stats
        FROM base
        """
    )
    conn.execute(
        "CREATE INDEX temp.idx_spotiboys_artist_stats "
        "ON spotiboys_artist_stats(artist_id, library_id)"
    )
    stats_rows = int(
        conn.execute("SELECT COUNT(*) FROM spotiboys_artist_stats").fetchone()[0] or 0
    )
    print(f"Navidrome library_artist stats rows prepared: {stats_rows}", flush=True)
    conn.execute(
        """
        UPDATE library_artist
        SET stats = coalesce((
            SELECT sas.stats
            FROM spotiboys_artist_stats sas
            WHERE sas.artist_id = library_artist.artist_id
              AND sas.library_id = library_artist.library_id
        ), '{}')
        WHERE library_artist.artist_id IN (SELECT artist_id FROM spotiboys_artist_stats)
        """
    )
    return relation_rows


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
        print(
            f"Navidrome scan ready; loading {len(rows)} manifest metadata rows",
            flush=True,
        )
        _load_temp_metadata(conn, rows)
        updated = 0
        for table in _candidate_tables(conn):
            columns = _table_columns(conn, table)
            if "path" not in columns:
                continue
            print(f"updating Navidrome metadata table={table}", flush=True)
            updated = _bulk_update_metadata(conn, table, columns)
            artist_relations = _sync_artists(conn, table)
            conn.commit()
            if updated == 0:
                raise RuntimeError(
                    f"found Navidrome media table {table} but no metadata rows matched manifest track ids"
                )
            print(f"Navidrome artist relationship rows updated: {artist_relations}", flush=True)
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
