#!/usr/bin/env python3
"""
Seed pre-existing 30Music users into app.users.

Source: s3://proj23-mlflow-artifacts/artifacts/item2vec/users_filtered_i2v.parquet
  Column: user_id (int64)

All accounts are created with the fixed password "test123" so any account can be
manually accessed as {uid}@navidrome.local / test123.

Run once after first docker compose up. Safe to re-run (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations

import io
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
import psycopg2.extras

from packages.auth import hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed-users")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/spotiboys")
AWS_ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL", "https://chi.tacc.chameleoncloud.org:7480")
S3_NO_VERIFY_SSL = os.environ.get("S3_NO_VERIFY_SSL", "true").lower() in ("1", "true", "yes")
ARTIFACT_BUCKET = os.environ.get("ARTIFACT_BUCKET", "proj23-mlflow-artifacts")
USERS_PARQUET_KEY = "artifacts/item2vec/users_filtered_i2v.parquet"
FIXED_PASSWORD = "test123"
BATCH_SIZE = 1000


def download_parquet() -> "pd.DataFrame":
    import boto3
    import pandas as pd
    import urllib3

    if S3_NO_VERIFY_SSL:
        urllib3.disable_warnings()

    s3 = boto3.client(
        "s3",
        endpoint_url=AWS_ENDPOINT_URL,
        verify=not S3_NO_VERIFY_SSL,
    )
    log.info(f"Downloading s3://{ARTIFACT_BUCKET}/{USERS_PARQUET_KEY} ...")
    buf = io.BytesIO()
    s3.download_fileobj(ARTIFACT_BUCKET, USERS_PARQUET_KEY, buf)
    buf.seek(0)
    df = pd.read_parquet(buf)
    log.info(f"Downloaded {len(df)} rows")
    return df


def seed_users(df: "pd.DataFrame") -> None:
    password_hash = hash_password(FIXED_PASSWORD)
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM app.users WHERE user_int_id < 100000")
            existing = cur.fetchone()[0]
        log.info(f"Existing pre-seeded users: {existing}")

        rows = [
            (
                f"user_{uid}",           # user_id
                int(uid),                # user_int_id
                f"{uid}@navidrome.local",# email
                password_hash,           # password_hash
                f"30Music User {uid}",   # display_name
            )
            for uid in df["user_id"].astype(int)
        ]

        inserted = 0
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            psycopg2.extras.execute_values(
                conn.cursor(),
                """
                INSERT INTO app.users (user_id, user_int_id, email, password_hash, display_name)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                batch,
            )
            inserted += len(batch)
            if inserted % 10000 == 0 or i + BATCH_SIZE >= len(rows):
                conn.commit()
                log.info(f"Processed {inserted}/{len(rows)} users ...")

        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM app.users WHERE user_int_id < 100000")
            final = cur.fetchone()[0]
        log.info(f"Seeding complete. Pre-seeded users in DB: {final}")

    finally:
        conn.close()


def main() -> None:
    df = download_parquet()
    seed_users(df)


if __name__ == "__main__":
    main()
