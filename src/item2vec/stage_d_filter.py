"""
Stage D — Filter Interaction Tables to Item2Vec Vocabulary

Reads directly from data/raw/content/30music_parsed/.
Uses item2vec_catalog.csv (vocab track_ids) as the filter set.

Memory strategy for session_tracks (31M rows):
  Read in 500K-row chunks, apply all row-level filters per chunk,
  concat only the passing rows (~22M → fits in ~800 MB vs 2.5 GB full load).
  Session-level min-length filter runs on the already-small result.

Outputs (artifacts/item2vec/):
  session_tracks_i2v.parquet
  session_meta_i2v.parquet
  playlist_tracks_i2v.parquet
  playlist_meta_i2v.parquet
  love_filtered_i2v.parquet
  users_filtered_i2v.parquet
"""
import logging
import os
import time

import mlflow
import pandas as pd

RAW_DIR       = "data/raw/content/30music_parsed"
OUT_DIR       = "artifacts/item2vec"
CHUNK_SIZE    = 500_000
PLAYRATIO_CAP = 5.0
MIN_SEQ       = 2

log = logging.getLogger("item2vec.stage_d")


def run(
    catalog_path:      str = None,
    mlflow_experiment: str = "item2vec-training",
    run_id:            str = None,
) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    catalog_path = catalog_path or os.path.join(OUT_DIR, "item2vec_catalog.csv")

    vocab_ids = set(
        pd.read_csv(catalog_path, usecols=["track_id"])["track_id"].astype("int64")
    )
    log.info(f"Stage D — vocab size: {len(vocab_ids):,} track_ids")
    stage_start = time.time()
    metrics     = {}

    # ── D1  session_tracks ────────────────────────────────────────────────────
    # Memory fix: chunked read + row-level filters per chunk.
    # Only concat the already-filtered rows (~22M) instead of loading all 31M.
    t0 = time.time()
    log.info("── D1 session_tracks ──")

    keep = ["session_id", "user_id", "position", "track_id", "playratio", "label"]
    kept_chunks = []
    n0 = n_clipped = 0

    for chunk in pd.read_csv(
        os.path.join(RAW_DIR, "session_tracks.csv"),
        usecols=keep,
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        n0 += len(chunk)

        # row-level filters — applied before accumulation
        chunk["track_id"] = pd.to_numeric(chunk["track_id"], errors="coerce")
        chunk = chunk[chunk["track_id"].isin(vocab_ids)]
        chunk = chunk[chunk["label"] != "unknown"]
        chunk["playratio"] = pd.to_numeric(chunk["playratio"], errors="coerce")
        n_clipped += int((chunk["playratio"] > PLAYRATIO_CAP).sum())
        chunk["playratio"] = chunk["playratio"].clip(upper=PLAYRATIO_CAP)

        kept_chunks.append(chunk)

    log.info(f"  raw rows: {n0:,}")

    st = pd.concat(kept_chunks, ignore_index=True)
    del kept_chunks   # free immediately

    # dedup on the small result (data inspection showed 0 cross-chunk dupes,
    # but kept for correctness)
    n1 = len(st)
    st = st.drop_duplicates(subset=["session_id", "position"], keep="first")
    n2 = len(st)
    log.info(f"  after vocab+label filter: {n1:,} | after dedup: {n2:,} | "
             f"playratio clipped: {n_clipped:,}")

    # session-level min-length filter
    valid_sessions = st.groupby("session_id").size()
    valid_sessions = valid_sessions[valid_sessions >= MIN_SEQ].index
    st = st[st["session_id"].isin(valid_sessions)]
    n3 = len(st)
    log.info(f"  after drop sessions len<{MIN_SEQ}: {n3:,}")

    st = st.sort_values(["session_id", "position"]).reset_index(drop=True)
    st.to_parquet(os.path.join(OUT_DIR, "session_tracks_i2v.parquet"), index=False)
    log.info(f"  wrote session_tracks_i2v.parquet  {n3:,} rows  {time.time()-t0:.0f}s")
    metrics.update({"st_raw": n0, "st_after_filter": n2, "st_final": n3})

    # ── D2  session_meta ──────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D2 session_meta ──")
    surviving_sessions = set(st["session_id"].unique())
    del st   # no longer needed

    sm = pd.read_csv(os.path.join(RAW_DIR, "session_meta.csv"),
                     usecols=["session_id", "user_id"], low_memory=False)
    sm = sm.drop_duplicates(subset="session_id", keep="first")
    sm = sm[sm["session_id"].isin(surviving_sessions)]
    sm.to_parquet(os.path.join(OUT_DIR, "session_meta_i2v.parquet"), index=False)
    log.info(f"  wrote session_meta_i2v.parquet  {len(sm):,} rows  {time.time()-t0:.0f}s")
    metrics["sm_final"] = len(sm)
    del sm

    # ── D3  playlist_tracks ───────────────────────────────────────────────────
    # playlist_tracks.csv is only 1.6M rows — safe to load in full
    t0 = time.time()
    log.info("── D3 playlist_tracks ──")
    pt = pd.read_csv(os.path.join(RAW_DIR, "playlist_tracks.csv"),
                     usecols=["playlist_id", "user_id", "position", "track_id"],
                     low_memory=False)
    pt_n0 = len(pt)
    pt = pt.drop_duplicates(subset=["playlist_id", "track_id"], keep="first")
    pt["track_id"] = pd.to_numeric(pt["track_id"], errors="coerce")
    pt = pt[pt["track_id"].isin(vocab_ids)]
    pt_n1 = len(pt)
    valid_pl = pt.groupby("playlist_id").size()
    valid_pl = valid_pl[valid_pl >= MIN_SEQ].index
    pt = pt[pt["playlist_id"].isin(valid_pl)]
    pt_n2 = len(pt)
    pt.to_parquet(os.path.join(OUT_DIR, "playlist_tracks_i2v.parquet"), index=False)
    log.info(f"  {pt_n0:,} → {pt_n1:,} (vocab) → {pt_n2:,} (min-len)  {time.time()-t0:.0f}s")
    metrics.update({"pt_raw": pt_n0, "pt_after_vocab": pt_n1, "pt_final": pt_n2})

    # ── D4  playlist_meta ─────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D4 playlist_meta ──")
    surviving_pl = set(pt["playlist_id"].unique())
    del pt

    pm = pd.read_csv(os.path.join(RAW_DIR, "playlist_meta.csv"),
                     usecols=["playlist_id", "user_id"], low_memory=False)
    pm = pm.drop_duplicates(subset="playlist_id", keep="first")
    pm = pm[pm["playlist_id"].isin(surviving_pl)]
    pm.to_parquet(os.path.join(OUT_DIR, "playlist_meta_i2v.parquet"), index=False)
    log.info(f"  wrote playlist_meta_i2v.parquet  {len(pm):,} rows  {time.time()-t0:.0f}s")
    metrics["pm_final"] = len(pm)
    del pm

    # ── D5  love ──────────────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D5 love ──")
    lv = pd.read_csv(os.path.join(RAW_DIR, "love.csv"),
                     usecols=["user_id", "track_id"], low_memory=False)
    lv = lv.drop_duplicates(subset=["user_id", "track_id"], keep="first")
    lv["track_id"] = pd.to_numeric(lv["track_id"], errors="coerce")
    lv = lv[lv["track_id"].isin(vocab_ids)]
    lv.to_parquet(os.path.join(OUT_DIR, "love_filtered_i2v.parquet"), index=False)
    log.info(f"  wrote love_filtered_i2v.parquet  {len(lv):,} rows  {time.time()-t0:.0f}s")
    metrics["lv_final"] = len(lv)
    del lv

    # ── D6  users ─────────────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D6 users ──")
    us = pd.read_csv(os.path.join(RAW_DIR, "users.csv"),
                     usecols=["user_id"], low_memory=False)
    us = us.drop_duplicates(subset="user_id", keep="first")
    us.to_parquet(os.path.join(OUT_DIR, "users_filtered_i2v.parquet"), index=False)
    log.info(f"  wrote users_filtered_i2v.parquet  {len(us):,} rows  {time.time()-t0:.0f}s")
    metrics["us_final"] = len(us)
    del us

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.time() - stage_start
    log.info(f"Stage D complete  {elapsed:.0f}s")
    for k, v in metrics.items():
        log.info(f"  {k}: {v:,}")

    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics({f"filter_{k}": v for k, v in metrics.items()})

    return metrics
