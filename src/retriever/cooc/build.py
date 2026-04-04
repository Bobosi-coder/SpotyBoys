"""
Co-occurrence Matrix Construction

Builds two sparse co-occurrence matrices:
  C_sess — session transition matrix (train sessions only, positive+neutral, adjacent pairs)
  C_pl   — playlist co-occurrence matrix (all playlists, pairs with |pos_i - pos_j| <= 5)

Inputs:
  artifacts/item2vec/session_tracks_i2v.parquet
  artifacts/item2vec/playlist_tracks_i2v.parquet
  artifacts/retriever/split/split_train.npy
  artifacts/item2vec/item2vec_catalog.csv

Outputs:
  artifacts/retriever/cooc/cooc_session.npz   SciPy CSR float32
  artifacts/retriever/cooc/cooc_playlist.npz  SciPy CSR float32

Memory strategy (VM-safe):
  - Filter parquet to required rows/columns before concat
  - Sort → extract numpy arrays → del DataFrame
  - Numpy diff boundary scan (no Python groupby accumulation)
  - Build COO arrays as int32, convert to CSR once at the end
"""
import logging
import os
import time

import mlflow
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import scipy.sparse as sp

I2V_DIR = "artifacts/item2vec"
SPLIT_DIR     = "artifacts/retriever/split"
OUT_DIR       = "artifacts/retriever/cooc"
KEEP_LABELS   = {"positive", "neutral"}
PL_MAX_DIST   = 5   # maximum |pos_i - pos_j| for playlist pairs

log = logging.getLogger("retriever.cooc")


def _build_csess(
    session_parquet: str,
    train_set: set,
    tid2idx: dict,
    N: int,
) -> sp.csr_matrix:
    """
    Session transition matrix C_sess.
    Counts adjacent (i_j, i_{j+1}) pairs in training sessions with pos/neutral labels.
    Memory-safe: numpy diff boundary approach, no groupby.
    """
    log.info("  Building C_sess — reading and filtering session_tracks...")
    t0 = time.time()

    n_raw = 0

    # Read parquet, filter label + train sessions — after filtering fits in RAM
    table = pq.read_table(
        session_parquet,
        columns=["session_id", "position", "track_id", "label"],
    )
    df = table.to_pandas()
    del table

    n_raw = len(df)
    df = df[df["label"].isin(KEEP_LABELS)]
    df = df[df["session_id"].isin(train_set)]
    df = df[["session_id", "position", "track_id"]].copy()
    df["session_id"] = df["session_id"].astype("int64")
    df["track_id"]   = df["track_id"].astype("int32")
    df["position"]   = df["position"].astype("int32")
    log.info(f"  Filtered to {len(df):,} rows (from {n_raw:,}) in {time.time()-t0:.1f}s")

    log.info("  Sorting...")
    df.sort_values(["session_id", "position"], inplace=True, ignore_index=True)

    sids = df["session_id"].values  # int64
    tids = df["track_id"].values    # int32
    del df

    # Numpy diff boundary scan
    boundaries = np.concatenate(([0], np.where(np.diff(sids) != 0)[0] + 1, [len(sids)]))
    n_sessions = len(boundaries) - 1
    log.info(f"  Processing {n_sessions:,} training sessions...")

    rows_list = []
    cols_list = []

    for i in range(n_sessions):
        start = boundaries[i]
        end   = boundaries[i + 1]
        seq   = tids[start:end]
        if len(seq) < 2:
            continue
        for a, b in zip(seq[:-1], seq[1:]):
            ra = tid2idx.get(int(a), -1)
            rb = tid2idx.get(int(b), -1)
            if ra >= 0 and rb >= 0:
                rows_list.append(ra)
                cols_list.append(rb)

    del sids, tids, boundaries

    rows_arr = np.array(rows_list, dtype=np.int32)
    cols_arr = np.array(cols_list, dtype=np.int32)
    data_arr = np.ones(len(rows_arr), dtype=np.float32)
    del rows_list, cols_list

    log.info(f"  COO pairs: {len(rows_arr):,}")
    C_sess = sp.coo_matrix(
        (data_arr, (rows_arr, cols_arr)), shape=(N, N), dtype=np.float32
    ).tocsr()
    del rows_arr, cols_arr, data_arr

    log.info(f"  C_sess nnz={C_sess.nnz:,}  shape={C_sess.shape}  "
             f"density={C_sess.nnz / (N * N):.2e}  elapsed={time.time()-t0:.0f}s")
    return C_sess


def _build_cpl(
    playlist_parquet: str,
    tid2idx: dict,
    N: int,
) -> sp.csr_matrix:
    """
    Playlist co-occurrence matrix C_pl.
    For all pairs (i,j) in the same playlist with |pos_i - pos_j| <= PL_MAX_DIST:
        C_pl[tid2idx[i], tid2idx[j]] += 1 / |pos_i - pos_j|
    Uses ALL playlists (train + val/test).
    Memory-safe: numpy diff boundary approach.
    """
    log.info("  Building C_pl — reading playlist_tracks...")
    t0 = time.time()

    table = pq.read_table(
        playlist_parquet,
        columns=["playlist_id", "position", "track_id"],
    )
    df = table.to_pandas()
    del table

    df["playlist_id"] = df["playlist_id"].astype("int64")
    df["track_id"]    = df["track_id"].astype("int32")
    df["position"]    = df["position"].astype("int32")
    log.info(f"  Loaded {len(df):,} playlist rows")

    df.sort_values(["playlist_id", "position"], inplace=True, ignore_index=True)

    plids = df["playlist_id"].values
    tids  = df["track_id"].values
    pos   = df["position"].values
    del df

    boundaries = np.concatenate(([0], np.where(np.diff(plids) != 0)[0] + 1, [len(plids)]))
    n_playlists = len(boundaries) - 1
    log.info(f"  Processing {n_playlists:,} playlists...")

    rows_list   = []
    cols_list   = []
    weights_list = []

    for i in range(n_playlists):
        start = boundaries[i]
        end   = boundaries[i + 1]
        seq_t = tids[start:end]
        seq_p = pos[start:end]
        M = len(seq_t)
        if M < 2:
            continue
        for a_idx in range(M):
            ra = tid2idx.get(int(seq_t[a_idx]), -1)
            if ra < 0:
                continue
            for b_idx in range(a_idx + 1, min(a_idx + PL_MAX_DIST + 1, M)):
                dist = int(seq_p[b_idx]) - int(seq_p[a_idx])
                if dist <= 0:
                    continue
                if dist > PL_MAX_DIST:
                    break
                rb = tid2idx.get(int(seq_t[b_idx]), -1)
                if rb < 0:
                    continue
                w = 1.0 / dist
                rows_list.append(ra)
                cols_list.append(rb)
                weights_list.append(w)
                # symmetric
                rows_list.append(rb)
                cols_list.append(ra)
                weights_list.append(w)

    del plids, tids, pos, boundaries

    rows_arr    = np.array(rows_list,    dtype=np.int32)
    cols_arr    = np.array(cols_list,    dtype=np.int32)
    weights_arr = np.array(weights_list, dtype=np.float32)
    del rows_list, cols_list, weights_list

    log.info(f"  COO pairs: {len(rows_arr):,}")
    C_pl = sp.coo_matrix(
        (weights_arr, (rows_arr, cols_arr)), shape=(N, N), dtype=np.float32
    ).tocsr()
    del rows_arr, cols_arr, weights_arr

    log.info(f"  C_pl nnz={C_pl.nnz:,}  shape={C_pl.shape}  "
             f"density={C_pl.nnz / (N * N):.2e}  elapsed={time.time()-t0:.0f}s")
    return C_pl


def run(
    mlflow_experiment: str = "retriever-cooc",
    run_name: str = "cooc",
) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    t_start = time.time()

    # ── Load artifacts ────────────────────────────────────────────────────────
    train_path = os.path.join(SPLIT_DIR, "split_train.npy")
    log.info(f"Loading train split from {train_path}")
    train_set = set(np.load(train_path).tolist())
    log.info(f"  Train sessions: {len(train_set):,}")

    catalog_path = os.path.join(I2V_DIR, "item2vec_catalog.csv")
    log.info(f"Loading catalog from {catalog_path}")
    # Deduplicate: item2vec_catalog.csv can have duplicate track_ids if tracks.csv has
    # duplicates (left-join in stage_b_train). Use first-occurrence order only so that
    # enumerate indices [0, N) match dict length N exactly.
    raw_ids = pd.read_csv(catalog_path, usecols=["track_id"])["track_id"].tolist()
    seen: set = set()
    unique_ids: list = []
    for t in raw_ids:
        ti = int(t)
        if ti not in seen:
            seen.add(ti)
            unique_ids.append(ti)
    tid2idx = {t: i for i, t in enumerate(unique_ids)}
    N = len(tid2idx)
    log.info(f"  Catalog rows: {len(raw_ids):,}  unique track_ids (N): {N:,}")

    # ── Build C_sess ──────────────────────────────────────────────────────────
    log.info("── Building C_sess ──")
    session_parquet  = os.path.join(I2V_DIR, "session_tracks_i2v.parquet")
    C_sess = _build_csess(session_parquet, train_set, tid2idx, N)
    sess_path = os.path.join(OUT_DIR, "cooc_session.npz")
    sp.save_npz(sess_path, C_sess)
    log.info(f"Saved {sess_path}  ({os.path.getsize(sess_path)/1e6:.1f} MB)")

    # ── Build C_pl ────────────────────────────────────────────────────────────
    log.info("── Building C_pl ──")
    playlist_parquet = os.path.join(I2V_DIR, "playlist_tracks_i2v.parquet")
    C_pl = _build_cpl(playlist_parquet, tid2idx, N)
    pl_path = os.path.join(OUT_DIR, "cooc_playlist.npz")
    sp.save_npz(pl_path, C_pl)
    log.info(f"Saved {pl_path}  ({os.path.getsize(pl_path)/1e6:.1f} MB)")

    elapsed = time.time() - t_start
    log.info(f"Co-occurrence build complete  {elapsed:.0f}s")

    metrics = {
        "N_vocab":       N,
        "csess_nnz":     int(C_sess.nnz),
        "cpl_nnz":       int(C_pl.nnz),
        "csess_density": round(C_sess.nnz / (N * N), 8),
        "cpl_density":   round(C_pl.nnz   / (N * N), 8),
    }

    # ── MLflow ────────────────────────────────────────────────────────────────
    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params({"pl_max_dist": PL_MAX_DIST, "keep_labels": "positive,neutral"})
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(sess_path)
        mlflow.log_artifact(pl_path)

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
