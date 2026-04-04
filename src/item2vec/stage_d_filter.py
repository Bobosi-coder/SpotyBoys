"""
Stage D — Filter Interaction Tables to Item2Vec Vocabulary

Reads directly from data/raw/content/30music_parsed/.
Uses item2vec_catalog.csv (vocab track_ids) as the filter set.

Outputs (data/processed/):
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
OUT_DIR       = "data/processed"
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
    metrics = {}

    # ── D1  session_tracks ────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D1 session_tracks ──")
    keep = ["session_id", "user_id", "position", "track_id", "playratio", "label"]
    st   = pd.read_csv(os.path.join(RAW_DIR, "session_tracks.csv"), usecols=keep, low_memory=False)
    n0   = len(st)

    st = st.drop_duplicates(subset=["session_id", "position"], keep="first")
    n1 = len(st)
    log.info(f"  dedup (session_id, position): {n0:,} → {n1:,}")

    st["track_id"] = pd.to_numeric(st["track_id"], errors="coerce")
    st = st[st["track_id"].isin(vocab_ids)]
    n2 = len(st)
    log.info(f"  filter track_id ∈ vocab: → {n2:,}")

    st = st[st["label"] != "unknown"]
    n3 = len(st)
    log.info(f"  remove label=unknown: → {n3:,}")

    st["playratio"] = pd.to_numeric(st["playratio"], errors="coerce")
    n_clipped = int((st["playratio"] > PLAYRATIO_CAP).sum())
    st["playratio"] = st["playratio"].clip(upper=PLAYRATIO_CAP)
    log.info(f"  clip playratio@{PLAYRATIO_CAP}: {n_clipped:,} rows clipped")

    valid_sessions = st.groupby("session_id").size()
    valid_sessions = valid_sessions[valid_sessions >= MIN_SEQ].index
    st = st[st["session_id"].isin(valid_sessions)]
    n4 = len(st)
    log.info(f"  drop sessions len<{MIN_SEQ}: → {n4:,}")

    st = st.sort_values(["session_id", "position"]).reset_index(drop=True)
    st.to_parquet(os.path.join(OUT_DIR, "session_tracks_i2v.parquet"), index=False)
    log.info(f"  wrote session_tracks_i2v.parquet  {n4:,} rows  {time.time()-t0:.0f}s")
    metrics.update({"st_raw": n0, "st_after_dedup": n1, "st_after_vocab": n2,
                    "st_after_label": n3, "st_final": n4})

    # ── D2  session_meta ──────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D2 session_meta ──")
    sm = pd.read_csv(os.path.join(RAW_DIR, "session_meta.csv"),
                     usecols=["session_id", "user_id"], low_memory=False)
    sm = sm.drop_duplicates(subset="session_id", keep="first")
    surviving = set(st["session_id"].unique())
    sm = sm[sm["session_id"].isin(surviving)]
    sm.to_parquet(os.path.join(OUT_DIR, "session_meta_i2v.parquet"), index=False)
    log.info(f"  wrote session_meta_i2v.parquet  {len(sm):,} rows  {time.time()-t0:.0f}s")
    metrics["sm_final"] = len(sm)

    # ── D3  playlist_tracks ───────────────────────────────────────────────────
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
    log.info(f"  {pt_n0:,} → {pt_n1:,} (vocab filter) → {pt_n2:,} (min-len)  {time.time()-t0:.0f}s")
    metrics.update({"pt_raw": pt_n0, "pt_after_vocab": pt_n1, "pt_final": pt_n2})

    # ── D4  playlist_meta ─────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D4 playlist_meta ──")
    pm = pd.read_csv(os.path.join(RAW_DIR, "playlist_meta.csv"),
                     usecols=["playlist_id", "user_id"], low_memory=False)
    pm = pm.drop_duplicates(subset="playlist_id", keep="first")
    surviving_pl = set(pt["playlist_id"].unique())
    pm = pm[pm["playlist_id"].isin(surviving_pl)]
    pm.to_parquet(os.path.join(OUT_DIR, "playlist_meta_i2v.parquet"), index=False)
    log.info(f"  wrote playlist_meta_i2v.parquet  {len(pm):,} rows  {time.time()-t0:.0f}s")
    metrics["pm_final"] = len(pm)

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

    # ── D6  users ─────────────────────────────────────────────────────────────
    t0 = time.time()
    log.info("── D6 users ──")
    us = pd.read_csv(os.path.join(RAW_DIR, "users.csv"),
                     usecols=["user_id"], low_memory=False)
    us = us.drop_duplicates(subset="user_id", keep="first")
    us.to_parquet(os.path.join(OUT_DIR, "users_filtered_i2v.parquet"), index=False)
    log.info(f"  wrote users_filtered_i2v.parquet  {len(us):,} rows  {time.time()-t0:.0f}s")
    metrics["us_final"] = len(us)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.time() - stage_start
    log.info(f"Stage D complete  {elapsed:.0f}s")
    log.info(f"  session_tracks_i2v : {metrics['st_final']:>10,}")
    log.info(f"  session_meta_i2v   : {metrics['sm_final']:>10,}")
    log.info(f"  playlist_tracks_i2v: {metrics['pt_final']:>10,}")
    log.info(f"  playlist_meta_i2v  : {metrics['pm_final']:>10,}")
    log.info(f"  love_filtered_i2v  : {metrics['lv_final']:>10,}")
    log.info(f"  users_filtered_i2v : {metrics['us_final']:>10,}")

    # MLflow
    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics({f"filter_{k}": v for k, v in metrics.items()})

    return metrics
