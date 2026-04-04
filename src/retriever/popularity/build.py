"""
Popularity Score Construction

Computes pop_score = log(1 + track_count) from session_tracks_i2v.parquet,
where track_count is the total number of times a track appears across all
session rows (all labels).

Input:  artifacts/item2vec/session_tracks_i2v.parquet
Output: artifacts/retriever/popularity/pop_scores.csv
          columns: track_id, track_count, pop_score  (sorted by pop_score descending)
"""
import logging
import os
import time

import mlflow
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

I2V_DIR = "artifacts/item2vec"
OUT_DIR       = "artifacts/retriever/popularity"

log = logging.getLogger("retriever.popularity")


def run(
    mlflow_experiment: str = "retriever-popularity",
    run_name: str = "popularity",
) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()

    # ── Count track occurrences across all session rows ───────────────────────
    sess_path = os.path.join(I2V_DIR, "session_tracks_i2v.parquet")
    log.info(f"Loading track_id column from {sess_path}")
    track_ids = (
        pq.read_table(sess_path, columns=["track_id"])
        .column("track_id")
        .to_pylist()
    )
    log.info(f"  Total rows: {len(track_ids):,}")

    counts = pd.Series(track_ids, dtype="int64").value_counts()
    counts.name = "track_count"
    counts.index.name = "track_id"
    df = counts.reset_index()
    df["pop_score"] = np.log1p(df["track_count"].astype("float64"))
    df.sort_values("pop_score", ascending=False, inplace=True, ignore_index=True)

    out_path = os.path.join(OUT_DIR, "pop_scores.csv")
    df.to_csv(out_path, index=False)

    elapsed = time.time() - t0
    log.info(
        f"Pop scores saved → {out_path}  ({os.path.getsize(out_path)/1e6:.1f} MB)  "
        f"elapsed={elapsed:.1f}s"
    )
    log.info(f"  Unique tracks: {len(df):,}")
    log.info(f"  Top-5 by pop_score:")
    for _, row in df.head(5).iterrows():
        log.info(f"    track_id={int(row.track_id)}  count={int(row.track_count):,}  "
                 f"pop_score={row.pop_score:.4f}")

    metrics = {
        "n_unique_tracks": len(df),
        "max_pop_score":   round(float(df["pop_score"].max()), 4),
        "median_pop_score": round(float(df["pop_score"].median()), 4),
    }

    # ── MLflow ────────────────────────────────────────────────────────────────
    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_name=run_name):
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(out_path)

    return metrics


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    result = run()
    for k, v in result.items():
        log.info(f"  {k}: {v}")
