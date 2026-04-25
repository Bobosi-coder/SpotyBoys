#!/usr/bin/env python3
"""
delta-export-worker — runs in a loop every 24h.

Steps:
  1. Sync loved_tracks from Navidrome (getStarred2)
  2. Sync user_playlists + playlist_tracks from Navidrome (getPlaylists / getPlaylist)
  3. Export 4 parquets to S3: session_tracks, session_meta, love, users, playlist_tracks
  4. Write delta_checkpoint watermark
  5. If new complete events >= threshold → trigger Airflow retrain_phase2
  6. Compute skip_rate over last 24h → update app.model_status (soft rollback flag)

Usage:
  python export_and_trigger.py           # runs in 24h loop
  python export_and_trigger.py --once    # run once and exit
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Add project root to path so packages are importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg2
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("delta-export")

# ------------------------------------------------------------------ #
# Config from environment
# ------------------------------------------------------------------ #
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/spotiboys")
NAVIDROME_BASE_URL = os.environ.get("NAVIDROME_BASE_URL", "http://navidrome:4533").rstrip("/")
NAVIDROME_USERNAME = os.environ.get("NAVIDROME_USERNAME", "spotiboys")
AIRFLOW_BASE_URL = os.environ.get("AIRFLOW_BASE_URL", "http://host.docker.internal:8080")
AIRFLOW_USERNAME = os.environ.get("AIRFLOW_USERNAME", "admin")
AIRFLOW_PASSWORD = os.environ.get("AIRFLOW_PASSWORD", "admin")
AWS_ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL", "https://chi.tacc.chameleoncloud.org:7480")
S3_NO_VERIFY_SSL = os.environ.get("S3_NO_VERIFY_SSL", "true").lower() in ("1", "true", "yes")
ARTIFACT_BUCKET = os.environ.get("ARTIFACT_BUCKET", "proj23-mlflow-artifacts")
RETRAIN_THRESHOLD = int(os.environ.get("SPOTIBOYS_RETRAIN_THRESHOLD", "1000"))
INTERVAL_SECONDS = int(os.environ.get("SPOTIBOYS_DELTA_INTERVAL_SECONDS", "86400"))


# ------------------------------------------------------------------ #
# S3 helpers
# ------------------------------------------------------------------ #

def _s3_client():
    import boto3
    import urllib3
    if S3_NO_VERIFY_SSL:
        urllib3.disable_warnings()
    return boto3.client(
        "s3",
        endpoint_url=AWS_ENDPOINT_URL,
        verify=not S3_NO_VERIFY_SSL,
    )


def _upload_parquet(buf: io.BytesIO, s3_key: str) -> None:
    client = _s3_client()
    buf.seek(0)
    client.upload_fileobj(buf, ARTIFACT_BUCKET, s3_key)
    log.info(f"Uploaded s3://{ARTIFACT_BUCKET}/{s3_key}")


# ------------------------------------------------------------------ #
# Navidrome Subsonic helpers
# ------------------------------------------------------------------ #

def _subsonic_get(path: str, params: Dict[str, Any], username: str = NAVIDROME_USERNAME) -> Dict[str, Any]:
    base_params = {
        "u": username,
        "v": "1.16.1",
        "c": "spotiboys-delta",
        "f": "json",
    }
    resp = requests.get(
        f"{NAVIDROME_BASE_URL}/rest/{path}",
        params={**base_params, **params},
        headers={"Remote-User": str(username)},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    subsonic_resp = data.get("subsonic-response", {})
    if subsonic_resp.get("status") != "ok":
        raise RuntimeError(f"Subsonic error: {subsonic_resp.get('error', data)}")
    return subsonic_resp


# ------------------------------------------------------------------ #
# Main export logic
# ------------------------------------------------------------------ #

def run_export() -> None:
    import pandas as pd

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    try:
        # ---- 1. Read last watermark ---- #
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_int_id_watermark, exported_at FROM app.delta_checkpoint ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            last_watermark = int(row[0]) if row else 0
            last_exported_at = row[1] if row else datetime(2000, 1, 1, tzinfo=timezone.utc)

        version = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log.info(f"Export version={version}  watermark={last_watermark}  last_exported_at={last_exported_at}")

        # ---- 2. Sync loved_tracks from Navidrome ---- #
        log.info("Syncing loved tracks from Navidrome...")
        try:
            _sync_loved_tracks(conn)
        except Exception as exc:
            log.warning(f"Loved track sync failed (non-fatal): {exc}")

        # ---- 3. Sync playlists from Navidrome ---- #
        log.info("Syncing playlists from Navidrome...")
        try:
            _sync_playlists(conn)
        except Exception as exc:
            log.warning(f"Playlist sync failed (non-fatal): {exc}")

        conn.commit()

        # ---- 4. Build parquets ---- #
        with conn.cursor() as cur:
            # session_tracks_addition.parquet
            cur.execute(
                """
                SELECT session_int_id AS session_id, user_int_id AS user_id,
                       position, track_id,
                       playratio::float AS playratio,
                       CASE WHEN playratio > 0.8 THEN 'positive'
                            WHEN playratio > 0.2 THEN 'neutral'
                            ELSE 'skip' END AS label
                FROM app.playback_events
                WHERE playratio IS NOT NULL
                  AND session_int_id > %s
                """,
                (last_watermark,),
            )
            session_tracks_rows = cur.fetchall()
            session_tracks_df = pd.DataFrame(
                session_tracks_rows,
                columns=["session_id", "user_id", "position", "track_id", "playratio", "label"],
            )

            # session_meta_addition.parquet
            cur.execute(
                """
                SELECT DISTINCT session_int_id AS session_id, user_int_id AS user_id
                FROM app.playback_events WHERE session_int_id > %s
                """,
                (last_watermark,),
            )
            session_meta_df = pd.DataFrame(cur.fetchall(), columns=["session_id", "user_id"])

            # love_addition.parquet
            cur.execute(
                "SELECT user_int_id AS user_id, track_id FROM app.loved_tracks WHERE loved_at > %s",
                (last_exported_at,),
            )
            love_df = pd.DataFrame(cur.fetchall(), columns=["user_id", "track_id"])

            # users_addition.parquet (online users only, not pre-seeded 30Music)
            cur.execute(
                "SELECT user_int_id AS user_id FROM app.users WHERE created_at > %s AND user_int_id >= 100000",
                (last_exported_at,),
            )
            users_df = pd.DataFrame(cur.fetchall(), columns=["user_id"])

            # playlist_tracks_addition.parquet
            cur.execute(
                """
                SELECT p.playlist_int_id AS playlist_id, pt.position, pt.track_id
                FROM app.playlist_tracks pt
                JOIN app.user_playlists p USING (playlist_int_id)
                WHERE p.synced_at > %s
                """,
                (last_exported_at,),
            )
            playlist_tracks_df = pd.DataFrame(cur.fetchall(), columns=["playlist_id", "position", "track_id"])

        # Find the highest session_int_id exported
        new_watermark = int(session_meta_df["session_id"].max()) if not session_meta_df.empty else last_watermark

        rows_exported = {
            "session_tracks": len(session_tracks_df),
            "session_meta": len(session_meta_df),
            "love": len(love_df),
            "users": len(users_df),
            "playlist_tracks": len(playlist_tracks_df),
        }
        log.info(f"Rows to export: {rows_exported}")

        # ---- 5. Upload to S3 ---- #
        prefix = f"session_event/delta/{version}"
        for df, filename in [
            (session_tracks_df, "session_tracks_addition.parquet"),
            (session_meta_df, "session_meta_addition.parquet"),
            (love_df, "love_addition.parquet"),
            (users_df, "users_addition.parquet"),
            (playlist_tracks_df, "playlist_tracks_addition.parquet"),
        ]:
            buf = io.BytesIO()
            df.to_parquet(buf, index=False)
            _upload_parquet(buf, f"{prefix}/{filename}")

        # ---- 6. Write checkpoint ---- #
        with conn.cursor() as cur:
            import json
            cur.execute(
                "INSERT INTO app.delta_checkpoint (version, session_int_id_watermark, rows_exported) VALUES (%s, %s, %s)",
                (version, new_watermark, json.dumps(rows_exported)),
            )
        conn.commit()

        # ---- 7. Trigger Airflow if threshold met ---- #
        new_complete_events = rows_exported["session_tracks"]
        if new_complete_events >= RETRAIN_THRESHOLD:
            log.info(f"Threshold met ({new_complete_events} >= {RETRAIN_THRESHOLD}), triggering Airflow...")
            try:
                _trigger_airflow(version)
            except Exception as exc:
                log.warning(f"Airflow trigger failed (non-fatal): {exc}")
        else:
            log.info(f"Threshold not met ({new_complete_events} < {RETRAIN_THRESHOLD}), skipping Airflow trigger")

        # ---- 8. Soft rollback check ---- #
        _update_model_status(conn)
        conn.commit()

        log.info(f"Export complete: version={version}")

    finally:
        conn.close()


def _get_active_users(conn):
    """Return (user_int_id,) tuples for users who have ever logged in via SpotyBoys."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT u.user_int_id
            FROM app.users u
            JOIN app.auth_sessions s ON s.user_id = u.user_id
            """
        )
        return [row[0] for row in cur.fetchall()]


def _sync_loved_tracks(conn) -> None:
    """Sync starred tracks from Navidrome → app.loved_tracks (active users only)."""
    active_users = _get_active_users(conn)
    log.info(f"Syncing loved tracks for {len(active_users)} active users")

    for user_int_id in active_users:
        try:
            nav_username = str(user_int_id)
            resp = _subsonic_get("getStarred2.view", {}, username=nav_username)
            starred = resp.get("starred2", {})
            songs = starred.get("song", [])
            with conn.cursor() as cur:
                for song in songs:
                    nav_id = song.get("id", "")
                    cur.execute(
                        "SELECT track_id FROM app.playable_tracks WHERE navidrome_track_id = %s",
                        (nav_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        track_id = int(row[0])
                        cur.execute(
                            """
                            INSERT INTO app.loved_tracks (user_int_id, track_id)
                            VALUES (%s, %s) ON CONFLICT DO NOTHING
                            """,
                            (user_int_id, track_id),
                        )
        except Exception as exc:
            log.debug(f"Failed to sync loved tracks for user {user_int_id}: {exc}")


def _sync_playlists(conn) -> None:
    """Sync playlists from Navidrome → app.user_playlists + app.playlist_tracks (active users only)."""
    active_users = _get_active_users(conn)
    log.info(f"Syncing playlists for {len(active_users)} active users")

    for user_int_id in active_users:
        try:
            nav_username = str(user_int_id)
            resp = _subsonic_get("getPlaylists.view", {}, username=nav_username)
            playlists = resp.get("playlists", {}).get("playlist", [])
            for pl in playlists:
                nav_pl_id = pl["id"]
                pl_name = pl.get("name", "")
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app.user_playlists (user_int_id, nav_playlist_id, name, synced_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (nav_playlist_id) DO UPDATE SET name = EXCLUDED.name, synced_at = NOW()
                        RETURNING playlist_int_id
                        """,
                        (user_int_id, nav_pl_id, pl_name),
                    )
                    playlist_int_id = cur.fetchone()[0]

                    detail = _subsonic_get("getPlaylist.view", {"id": nav_pl_id}, username=nav_username)
                    entries = detail.get("playlist", {}).get("entry", [])

                    cur.execute("DELETE FROM app.playlist_tracks WHERE playlist_int_id = %s", (playlist_int_id,))
                    for pos, entry in enumerate(entries):
                        nav_id = entry.get("id", "")
                        cur.execute(
                            "SELECT track_id FROM app.playable_tracks WHERE navidrome_track_id = %s",
                            (nav_id,),
                        )
                        row = cur.fetchone()
                        if row:
                            track_id = int(row[0])
                            cur.execute(
                                "INSERT INTO app.playlist_tracks (playlist_int_id, position, track_id) VALUES (%s, %s, %s)",
                                (playlist_int_id, pos, track_id),
                            )
        except Exception as exc:
            log.debug(f"Failed to sync playlists for user {user_int_id}: {exc}")


def _trigger_airflow(version: str) -> None:
    url = f"{AIRFLOW_BASE_URL}/api/v1/dags/retrain_phase2/dagRuns"
    payload = {
        "conf": {"delta_version": version},
        "logical_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }
    resp = requests.post(url, json=payload, auth=(AIRFLOW_USERNAME, AIRFLOW_PASSWORD), timeout=30)
    resp.raise_for_status()
    dag_run_id = resp.json().get("dag_run_id", "unknown")
    log.info(f"Airflow triggered: run_id={dag_run_id}")


def _update_model_status(conn) -> None:
    """Compute skip_rate over last 24h and update app.model_status."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE event_type = 'skip') AS skips,
                COUNT(*) AS total
            FROM app.playback_events
            WHERE playratio IS NOT NULL
              AND created_at > NOW() - INTERVAL '1 day'
            """
        )
        row = cur.fetchone()
        skips, total = (row[0] or 0), (row[1] or 0)

    if total < 50:
        return  # Not enough data to make a determination

    skip_rate = skips / total
    if skip_rate > 0.80:
        log.warning(f"High skip rate detected: {skip_rate:.2%} — marking model as degraded")
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.model_status (id, degraded, reason, updated_at)
                VALUES (1, TRUE, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET degraded = TRUE, reason = EXCLUDED.reason, updated_at = NOW()
                """,
                (f"skip_rate={skip_rate:.2f}",),
            )
    else:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.model_status (id, degraded, reason, updated_at)
                VALUES (1, FALSE, NULL, NOW())
                ON CONFLICT (id) DO UPDATE SET degraded = FALSE, reason = NULL, updated_at = NOW()
                """
            )


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit instead of looping")
    args = parser.parse_args()

    if args.once:
        log.info("Running single export...")
        run_export()
        return

    log.info(f"Starting delta export loop (interval={INTERVAL_SECONDS}s)...")
    while True:
        try:
            run_export()
        except Exception as exc:
            log.error(f"Export failed: {exc}", exc_info=True)
        log.info(f"Sleeping {INTERVAL_SECONDS}s until next export...")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
