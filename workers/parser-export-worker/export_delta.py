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

        quality_report = validate_exported_delta_quality(output)
        (output / "quality_report.json").write_text(
            json.dumps(quality_report, indent=2) + "\n",
            encoding="utf-8",
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


def validate_exported_delta_quality(output_dir: Path) -> Dict[str, Any]:
    """Evaluate the quality of a compiled retraining delta before it is uploaded.

    Set SPOTIBOYS_QG_ENABLED=false to bypass all checks (demo / emergency use).
    The report is still written with status="disabled" so the audit trail is intact.

    Status levels
    -------------
    ok   — all gates pass; retraining proceeds.
    warn — soft thresholds breached; retraining proceeds but notes are logged.
    fail — hard gates failed; delta-trigger-worker will NOT start retraining.

    Threshold justification
    -----------------------
    All thresholds are overridable via environment variables (see below).
    Defaults are intentionally low for demo / early-stage runs.
    Set the env vars to production-recommended values before full deployment.

    session_tracks_rows >= SPOTIBOYS_QG_SESSION_TRACKS_MIN  (default 5, prod 100)
        Minimum viable batch.  In production, below 100 rows the GRU gradient
        updates are too noisy; default is 5 to allow demo runs.

    unique_user_count >= SPOTIBOYS_QG_UNIQUE_USERS_MIN  (default 2, prod 10)
        Personalized retrieval requires signal from multiple users.  In
        production, fewer than 10 users risks overfitting to individuals.

    unique_track_count >= SPOTIBOYS_QG_UNIQUE_TRACKS_MIN  (default 3, prod 50)
        Item2Vec and co-occurrence embeddings need catalog coverage.

    null_rate_ids == 0  (SPOTIBOYS_QG_NULL_RATE_MAX, default 0.0 — not relaxed)
        Null session_id / user_id / track_id corrupts the ID→embedding lookup;
        this threshold is kept at 0 regardless of environment.

    positive_label_rate >= SPOTIBOYS_QG_POSITIVE_LABEL_RATE_MIN  (default 0.01, prod 0.05)
        Positive signal must be present for cross-entropy to converge.

    skip_label_rate <= SPOTIBOYS_QG_SKIP_LABEL_RATE_MAX  (default 0.99, prod 0.95)
        Dataset must not be almost entirely negative labels.

    playratio_out_of_range_rate == 0  (SPOTIBOYS_QG_PLAYRATIO_OOR_MAX, default 0.0)
        Values outside [0, 1] indicate an upstream computation bug.

    duplicate_row_rate <= SPOTIBOYS_QG_DUPLICATE_RATE_WARN  (default 0.50, prod 0.10)
        Soft warning only; retraining still proceeds.
    """
    if os.environ.get("SPOTIBOYS_QG_ENABLED", "true").lower() in {"0", "false", "no"}:
        logger.warning("Quality gate DISABLED (SPOTIBOYS_QG_ENABLED=false); skipping all checks.")
        return {
            "stage": "retraining_dataset",
            "status": "disabled",
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "metrics": {},
            "thresholds": {},
            "notes": ["Quality gate bypassed via SPOTIBOYS_QG_ENABLED=false."],
        }

    import pyarrow.parquet as pq

    # ── load tables ──────────────────────────────────────────────────────────
    st_table = pq.read_table(output_dir / "session_tracks_addition.parquet")
    sm_table = pq.read_table(output_dir / "session_meta_addition.parquet")
    love_table = pq.read_table(output_dir / "love_addition.parquet")
    users_table = pq.read_table(output_dir / "users_addition.parquet")

    # ── schema presence ───────────────────────────────────────────────────────
    st_cols = set(st_table.column_names)
    REQUIRED_ST_COLS = {"session_id", "user_id", "track_id", "playratio", "label"}
    missing_cols = REQUIRED_ST_COLS - st_cols

    # ── label distribution ────────────────────────────────────────────────────
    label_counts: Dict[str, int] = {"positive": 0, "neutral": 0, "skip": 0}
    labels = st_table.column("label").to_pylist() if "label" in st_cols else []
    for lbl in labels:
        key = str(lbl)
        if key in label_counts:
            label_counts[key] += 1

    st_rows = st_table.num_rows
    denom = max(st_rows, 1)

    # ── null rates on critical ID columns ────────────────────────────────────
    def _null_rate(table: Any, col: str) -> float:
        if col not in table.column_names:
            return 1.0
        vals = table.column(col).to_pylist()
        return sum(1 for v in vals if v is None) / max(len(vals), 1)

    null_session_id = _null_rate(st_table, "session_id")
    null_user_id    = _null_rate(st_table, "user_id")
    null_track_id   = _null_rate(st_table, "track_id")

    # ── playratio range ───────────────────────────────────────────────────────
    playratios = st_table.column("playratio").to_pylist() if "playratio" in st_cols else []
    out_of_range = sum(1 for v in playratios if v is not None and not (0.0 <= float(v) <= 1.0))
    playratio_out_of_range_rate = out_of_range / denom

    # ── diversity ────────────────────────────────────────────────────────────
    user_ids  = st_table.column("user_id").to_pylist()  if "user_id"  in st_cols else []
    track_ids = st_table.column("track_id").to_pylist() if "track_id" in st_cols else []
    unique_users  = len(set(u for u in user_ids  if u is not None))
    unique_tracks = len(set(t for t in track_ids if t is not None))

    # ── duplicate (session_id, track_id) pairs ────────────────────────────────
    if "session_id" in st_cols and "track_id" in st_cols:
        sid_list = st_table.column("session_id").to_pylist()
        pairs = list(zip(sid_list, track_ids))
        duplicate_rows = st_rows - len(set(pairs))
        duplicate_row_rate = duplicate_rows / denom
    else:
        duplicate_rows = 0
        duplicate_row_rate = 0.0

    metrics = {
        "session_tracks_rows":       st_rows,
        "session_meta_rows":         sm_table.num_rows,
        "love_rows":                 love_table.num_rows,
        "users_rows":                users_table.num_rows,
        "positive_label_rate":       label_counts["positive"] / denom,
        "neutral_label_rate":        label_counts["neutral"]  / denom,
        "skip_label_rate":           label_counts["skip"]     / denom,
        "unique_user_count":         unique_users,
        "unique_track_count":        unique_tracks,
        "null_rate_session_id":      null_session_id,
        "null_rate_user_id":         null_user_id,
        "null_rate_track_id":        null_track_id,
        "playratio_out_of_range_rate": playratio_out_of_range_rate,
        "duplicate_row_rate":        duplicate_row_rate,
        "missing_required_columns":  sorted(missing_cols),
    }

    # Thresholds — all overridable via environment variables so that demo /
    # staging environments can use lower values without touching production logic.
    #
    # Production-recommended values are shown in the comments.
    # Default values are intentionally low to allow demo and early-stage runs.
    # Set env vars (e.g. SPOTIBOYS_QG_SESSION_TRACKS_MIN=100) for production.
    thresholds = {
        "session_tracks_rows_min":         int(os.environ.get("SPOTIBOYS_QG_SESSION_TRACKS_MIN",         "5")),
        "unique_user_count_min":           int(os.environ.get("SPOTIBOYS_QG_UNIQUE_USERS_MIN",           "2")),
        "unique_track_count_min":          int(os.environ.get("SPOTIBOYS_QG_UNIQUE_TRACKS_MIN",          "3")),
        "null_rate_ids_max":               float(os.environ.get("SPOTIBOYS_QG_NULL_RATE_MAX",            "0.0")),
        "positive_label_rate_min":         float(os.environ.get("SPOTIBOYS_QG_POSITIVE_LABEL_RATE_MIN",  "0.01")),
        "skip_label_rate_max":             float(os.environ.get("SPOTIBOYS_QG_SKIP_LABEL_RATE_MAX",      "0.99")),
        "playratio_out_of_range_rate_max": float(os.environ.get("SPOTIBOYS_QG_PLAYRATIO_OOR_MAX",        "0.0")),
        "duplicate_row_rate_warn":         float(os.environ.get("SPOTIBOYS_QG_DUPLICATE_RATE_WARN",      "0.50")),
    }

    fail_notes: List[str] = []
    warn_notes: List[str] = []

    if missing_cols:
        fail_notes.append(f"missing required columns in session_tracks: {sorted(missing_cols)}")
    if st_rows < thresholds["session_tracks_rows_min"]:
        fail_notes.append(
            f"session_tracks_rows={st_rows} < {thresholds['session_tracks_rows_min']} "
            "(minimum viable training batch)"
        )
    if unique_users < thresholds["unique_user_count_min"]:
        fail_notes.append(
            f"unique_user_count={unique_users} < {thresholds['unique_user_count_min']} "
            "(too few users for personalized training)"
        )
    if unique_tracks < thresholds["unique_track_count_min"]:
        fail_notes.append(
            f"unique_track_count={unique_tracks} < {thresholds['unique_track_count_min']} "
            "(too few tracks for meaningful item embeddings)"
        )
    if null_session_id > thresholds["null_rate_ids_max"]:
        fail_notes.append(f"null_rate_session_id={null_session_id:.4f} (must be 0; pipeline bug)")
    if null_user_id > thresholds["null_rate_ids_max"]:
        fail_notes.append(f"null_rate_user_id={null_user_id:.4f} (must be 0; pipeline bug)")
    if null_track_id > thresholds["null_rate_ids_max"]:
        fail_notes.append(f"null_rate_track_id={null_track_id:.4f} (must be 0; pipeline bug)")
    if metrics["positive_label_rate"] < thresholds["positive_label_rate_min"]:
        fail_notes.append(
            f"positive_label_rate={metrics['positive_label_rate']:.4f} < "
            f"{thresholds['positive_label_rate_min']} (positive signal too sparse)"
        )
    if metrics["skip_label_rate"] > thresholds["skip_label_rate_max"]:
        fail_notes.append(
            f"skip_label_rate={metrics['skip_label_rate']:.4f} > "
            f"{thresholds['skip_label_rate_max']} (dataset dominated by negatives)"
        )
    if playratio_out_of_range_rate > thresholds["playratio_out_of_range_rate_max"]:
        fail_notes.append(
            f"playratio_out_of_range_rate={playratio_out_of_range_rate:.4f} "
            "(values outside [0,1] indicate upstream bug)"
        )
    if duplicate_row_rate > thresholds["duplicate_row_rate_warn"]:
        warn_notes.append(
            f"duplicate_row_rate={duplicate_row_rate:.4f} > "
            f"{thresholds['duplicate_row_rate_warn']} (checkpoint cursor may be drifting)"
        )

    if fail_notes:
        status = "fail"
    elif warn_notes:
        status = "warn"
    else:
        status = "ok"

    return {
        "stage": "retraining_dataset",
        "status": status,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "metrics": metrics,
        "thresholds": thresholds,
        "notes": fail_notes + warn_notes,
    }


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
