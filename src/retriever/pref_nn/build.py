"""
User Centroid Construction (Preference NN offline build)

For each user: collect Item2Vec embeddings of loved tracks (×3) and recent
positive-label session events (capped at 300), then run K-Means to produce
per-user taste centroids.

K thresholds (data-driven from actual distribution of 44K users):
  K=1  if combined track count < 100   (~18% of users)
  K=2  if 100 <= combined count <= 300  (~32% of users)
  K=3  if combined count > 300          (~50% of users)

Inputs:
  artifacts/item2vec/love_filtered_i2v.parquet
  artifacts/item2vec/session_tracks_i2v.parquet
  artifacts/item2vec/item2vec_128d.npy
  artifacts/item2vec/item2vec_track_to_row.json

Output:
  artifacts/retriever/pref_nn/user_centroids.pkl
    Format: {user_id (int): [(centroid_128d (list), cluster_size (int)), ...]}
"""
import json
import logging
import os
import pickle
import time

import mlflow
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.cluster import KMeans

I2V_DIR        = "artifacts/item2vec"
OUT_DIR        = "artifacts/retriever/pref_nn"
POS_CAP        = 300    # max recent positive events per user
K1_THRESHOLD   = 100    # combined < 100  → K=1
K2_THRESHOLD   = 300    # combined <= 300 → K=2, else K=3
LOVE_WEIGHT    = 3      # love tracks counted this many times

log = logging.getLogger("retriever.pref_nn")


def run(
    mlflow_experiment: str = "retriever-pref-nn",
    run_name: str = "pref-nn",
) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    t_start = time.time()

    # ── Load embeddings ───────────────────────────────────────────────────────
    emb_path = os.path.join(I2V_DIR, "item2vec_128d.npy")
    t2r_path = os.path.join(I2V_DIR, "item2vec_track_to_row.json")
    log.info(f"Loading embeddings from {emb_path}")
    emb = np.load(emb_path).astype("float32")      # (N, 128)
    log.info(f"  Embedding matrix shape: {emb.shape}")

    log.info(f"Loading track_to_row from {t2r_path}")
    with open(t2r_path) as f:
        t2r = json.load(f)   # str(track_id) -> row_index

    # ── Load love data ────────────────────────────────────────────────────────
    love_path = os.path.join(I2V_DIR, "love_filtered_i2v.parquet")
    log.info(f"Loading love data from {love_path}")
    love = pq.read_table(love_path, columns=["user_id", "track_id"]).to_pandas()
    love["user_id"]  = love["user_id"].astype("int64")
    love["track_id"] = love["track_id"].astype("int64")
    log.info(f"  Love rows: {len(love):,}")

    # ── Load positive session events, cap at 300 per user ────────────────────
    sess_path = os.path.join(I2V_DIR, "session_tracks_i2v.parquet")
    log.info(f"Loading positive session events from {sess_path}")
    sess = pq.read_table(
        sess_path,
        columns=["user_id", "session_id", "position", "track_id", "label"],
    ).to_pandas()

    sess = sess[sess["label"] == "positive"].copy()
    sess["user_id"]  = sess["user_id"].astype("int64")
    sess["track_id"] = sess["track_id"].astype("int64")
    sess.sort_values(["user_id", "session_id", "position"], inplace=True, ignore_index=True)

    # Cap at most recent POS_CAP events per user
    pos_capped = sess.groupby("user_id")["track_id"].apply(
        lambda s: s.iloc[-POS_CAP:].tolist()
    )
    del sess
    log.info(f"  Users with positive events: {len(pos_capped):,}")

    # ── Build per-user centroid ───────────────────────────────────────────────
    # Iterate over union of love users + positive users
    all_users = set(love["user_id"].unique()) | set(pos_capped.index)
    log.info(f"Total users to process: {len(all_users):,}")

    # Group love by user_id for fast lookup
    love_by_user = love.groupby("user_id")["track_id"].apply(list)
    del love

    user_centroids = {}
    k_counts = {1: 0, 2: 0, 3: 0}
    n_skipped = 0

    for user_id in all_users:
        love_ids = love_by_user.get(user_id, [])
        pos_ids  = pos_capped.get(user_id, [])

        # love tracks weighted ×LOVE_WEIGHT
        track_ids = list(love_ids) * LOVE_WEIGHT + list(pos_ids)

        # Look up embeddings, skip tracks not in vocab
        emb_rows = [emb[t2r[str(t)]] for t in track_ids if str(t) in t2r]

        if len(emb_rows) < 2:
            n_skipped += 1
            continue

        embs = np.array(emb_rows, dtype="float32")
        combined_count = len(embs)

        if combined_count < K1_THRESHOLD:
            K = 1
        elif combined_count <= K2_THRESHOLD:
            K = 2
        else:
            K = 3

        km = KMeans(n_clusters=K, random_state=42, n_init=10).fit(embs)
        user_centroids[int(user_id)] = list(
            zip(km.cluster_centers_.tolist(), np.bincount(km.labels_).tolist())
        )
        k_counts[K] += 1

    del love_by_user, pos_capped

    # ── Save ──────────────────────────────────────────────────────────────────
    out_path = os.path.join(OUT_DIR, "user_centroids.pkl")
    with open(out_path, "wb") as f:
        pickle.dump(user_centroids, f, protocol=4)

    elapsed = time.time() - t_start
    n_users_with = len(user_centroids)
    total_users  = len(all_users)
    coverage_pct = 100.0 * n_users_with / total_users if total_users > 0 else 0.0

    log.info(
        f"User centroids saved → {out_path}  "
        f"({os.path.getsize(out_path)/1e6:.1f} MB)  elapsed={elapsed:.0f}s"
    )
    log.info(
        f"Users with centroids: {n_users_with:,} / {total_users:,} ({coverage_pct:.1f}%)  "
        f"skipped (< 2 embeddings): {n_skipped:,}"
    )
    log.info(f"K distribution: K=1: {k_counts[1]:,}  K=2: {k_counts[2]:,}  K=3: {k_counts[3]:,}")

    metrics = {
        "n_users_total":         total_users,
        "n_users_with_centroids": n_users_with,
        "n_skipped":             n_skipped,
        "coverage_pct":          round(coverage_pct, 2),
        "k1_count":              k_counts[1],
        "k2_count":              k_counts[2],
        "k3_count":              k_counts[3],
    }

    # ── MLflow ────────────────────────────────────────────────────────────────
    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params({
            "pos_cap":       POS_CAP,
            "love_weight":   LOVE_WEIGHT,
            "k1_threshold":  K1_THRESHOLD,
            "k2_threshold":  K2_THRESHOLD,
        })
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
