from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from packages.config import load_config
from packages.db_access.postgres import PostgresRepository

logger = logging.getLogger(__name__)

BUCKET = os.environ.get("ARTIFACT_BUCKET", "proj23-mlflow-artifacts")
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT")


def _s3_client():
    import boto3
    from botocore.config import Config

    verify = os.environ.get("S3_NO_VERIFY_SSL", "true").lower() not in {"1", "true", "yes"}
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        verify=verify,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )


def _upload_delta_to_s3(local_dir: Path, stamp: str) -> None:
    client = _s3_client()
    prefix = f"session_event/delta/{stamp}/"
    for file in local_dir.iterdir():
        if file.is_file():
            key = prefix + file.name
            logger.info(f"Uploading {file.name} → s3://{BUCKET}/{key}")
            client.upload_file(str(file), BUCKET, key)
    logger.info(f"S3 upload complete: s3://{BUCKET}/{prefix}")


def export_delta(version: str | None = None) -> Path:
    """
    Export delta parquet files compatible with VM2 merge_delta.py.

    Produces 4 files under session_event/delta/{VERSION}/:
      session_tracks_addition.parquet  — schema matching session_tracks_i2v snapshot
      session_meta_addition.parquet    — schema matching session_meta_i2v snapshot
      love_addition.parquet            — schema matching love_i2v snapshot
      users_addition.parquet           — schema matching users_i2v snapshot

    ID mapping:
      track_id   → CAST(app.playable_tracks.track_id AS BIGINT)  (30Music int stored as TEXT)
      user_id    → app.users.user_int_id    (BIGSERIAL, offset 100,000 above snapshot max)
      session_id → app.sessions.session_int_id (BIGSERIAL, offset 3,000,000 above snapshot max)

    Label derivation (playratio = playback_ms / duration_sec*1000):
      > 0.8  → positive
      > 0.2  → neutral
      ≤ 0.2  → skip
    """
    config = load_config()
    repo = PostgresRepository(config.database_url)

    stamp = version or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output = (
        config.object_storage_root
        / "proj23-mlflow-artifacts"
        / "session_event"
        / "delta"
        / stamp
    )

    repo.record_delta_export_start(stamp)

    try:
        output.mkdir(parents=True, exist_ok=True)

        checkpoint = repo.get_checkpoint_session_int_id()

        row_counts = {
            "session_tracks_addition.parquet": _export_session_tracks(
                repo, output / "session_tracks_addition.parquet", checkpoint
            ),
            "session_meta_addition.parquet": _export_session_meta(
                repo, output / "session_meta_addition.parquet", checkpoint
            ),
            "love_addition.parquet": _export_love(
                repo, output / "love_addition.parquet", checkpoint
            ),
            "users_addition.parquet": _export_users(
                repo, output / "users_addition.parquet"
            ),
        }

        last_int_id = repo.get_max_session_int_id()

        manifest = {
            "version": stamp,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "output_files": list(row_counts.keys()) + ["manifest.json"],
            "row_counts": row_counts,
            "source": "vm1-postgres-parser",
            "checkpoint": {
                "last_exported_session_int_id": last_int_id,
                "previous_checkpoint_session_int_id": checkpoint,
            },
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        repo.record_delta_export_success(stamp, last_int_id, row_counts)

        _upload_delta_to_s3(output, stamp)

        return output

    except Exception as exc:
        repo.record_delta_export_failure(stamp, str(exc))
        raise RuntimeError(f"Delta export failed: {exc}") from exc


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_parquet(path: Path, columns: List[str], rows: List[Tuple[Any, ...]]) -> int:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "pyarrow is required for delta export; install pyarrow or use the Docker image"
        ) from exc

    payload: Dict[str, List[Any]] = {
        col: [row[i] for row in rows] for i, col in enumerate(columns)
    }
    pq.write_table(pa.Table.from_pydict(payload), path)
    return len(rows)


def _export_session_tracks(
    repo: PostgresRepository, path: Path, checkpoint: Optional[int]
) -> int:
    checkpoint_val = checkpoint or 0
    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH terminal AS (
                    SELECT DISTINCT ON (pe.session_id, pe.track_id)
                        pe.session_id,
                        pe.user_id,
                        pe.track_id,
                        pe.playback_ms,
                        pe.occurred_at
                    FROM app.playback_events pe
                    JOIN app.sessions s ON s.session_id = pe.session_id
                    WHERE pe.event_type IN ('skip', 'complete')
                      AND s.session_int_id > %s
                    ORDER BY pe.session_id, pe.track_id, pe.occurred_at DESC
                ),
                positioned AS (
                    SELECT
                        t.*,
                        (ROW_NUMBER() OVER (
                            PARTITION BY t.session_id ORDER BY t.occurred_at
                        ) - 1)::BIGINT AS position
                    FROM terminal t
                )
                SELECT
                    s.session_int_id                                              AS session_id,
                    u.user_int_id                                                 AS user_id,
                    p.position,
                    CAST(p.track_id AS BIGINT)                                    AS track_id,
                    LEAST(1.0, p.playback_ms::float
                          / NULLIF(pt.duration_sec * 1000.0, 0))                 AS playratio
                FROM positioned p
                JOIN app.sessions s ON s.session_id = p.session_id
                JOIN app.users u ON u.user_id = p.user_id
                JOIN app.playable_tracks pt ON pt.track_id = p.track_id
                WHERE s.session_int_id IS NOT NULL
                  AND u.user_int_id IS NOT NULL
                ORDER BY s.session_int_id, p.position
                """,
                (checkpoint_val,),
            )
            raw = cur.fetchall()

    rows: List[Tuple[Any, ...]] = []
    for session_id, user_id, position, track_id, playratio in raw:
        ratio = playratio if playratio is not None else 0.0
        label = "positive" if ratio > 0.8 else ("neutral" if ratio > 0.2 else "skip")
        rows.append((session_id, user_id, position, track_id, ratio, label))

    return _write_parquet(
        path,
        ["session_id", "user_id", "position", "track_id", "playratio", "label"],
        rows,
    )


def _export_session_meta(
    repo: PostgresRepository, path: Path, checkpoint: Optional[int]
) -> int:
    checkpoint_val = checkpoint or 0
    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT s.session_int_id, u.user_int_id
                FROM app.sessions s
                JOIN app.users u ON u.user_id = s.user_id
                WHERE s.session_int_id > %s
                  AND s.session_int_id IS NOT NULL
                  AND u.user_int_id IS NOT NULL
                ORDER BY s.session_int_id
                """,
                (checkpoint_val,),
            )
            rows = cur.fetchall()
    return _write_parquet(path, ["session_id", "user_id"], rows)


def _export_love(
    repo: PostgresRepository, path: Path, checkpoint: Optional[int]
) -> int:
    checkpoint_val = checkpoint or 0
    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT u.user_int_id, CAST(fe.track_id AS BIGINT)
                FROM app.feedback_events fe
                JOIN app.users u ON u.user_id = fe.user_id
                JOIN app.sessions s ON s.session_id = fe.session_id
                WHERE fe.feedback_type = 'like'
                  AND s.session_int_id > %s
                  AND u.user_int_id IS NOT NULL
                ORDER BY u.user_int_id
                """,
                (checkpoint_val,),
            )
            rows = cur.fetchall()
    return _write_parquet(path, ["user_id", "track_id"], rows)


def _export_users(repo: PostgresRepository, path: Path) -> int:
    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_int_id
                FROM app.users
                WHERE user_int_id IS NOT NULL
                ORDER BY user_int_id
                """
            )
            rows = cur.fetchall()
    return _write_parquet(path, ["user_id"], rows)


if __name__ == "__main__":
    print(export_delta())
