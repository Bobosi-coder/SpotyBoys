"""
Split — Random Session-Level Data Split

Input:  artifacts/item2vec/session_meta_i2v.parquet
Output: artifacts/retriever/split/split_train.npy
        artifacts/retriever/split/split_val.npy
        artifacts/retriever/split/split_test.npy

Split ratio: 75 / 12.5 / 12.5 (train / val / test), random shuffle seed=42.
"""
import logging
import os
import time

import mlflow
import numpy as np
import pyarrow.parquet as pq

I2V_DIR = "artifacts/item2vec"
OUT_DIR       = "artifacts/retriever/split"
SEED          = 42
TRAIN_FRAC    = 0.75
VAL_FRAC      = 0.125   # test gets the rest

log = logging.getLogger("retriever.split")


def run(
    mlflow_experiment: str = "retriever-split",
    run_name: str = "split",
) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()

    # ── Read unique session_ids ──────────────────────────────────────────────
    meta_path = os.path.join(I2V_DIR, "session_meta_i2v.parquet")
    log.info(f"Reading session_ids from {meta_path}")
    session_ids = (
        pq.read_table(meta_path, columns=["session_id"])
        .column("session_id")
        .to_pylist()
    )
    session_ids = np.array(session_ids, dtype=np.int64)
    n_total = len(session_ids)
    log.info(f"Total sessions: {n_total:,}")

    # ── Shuffle and split ────────────────────────────────────────────────────
    rng = np.random.default_rng(SEED)
    rng.shuffle(session_ids)

    n_train = int(n_total * TRAIN_FRAC)
    n_val   = int(n_total * VAL_FRAC)
    # test gets the remainder (avoids rounding drift)
    split_train = session_ids[:n_train]
    split_val   = session_ids[n_train : n_train + n_val]
    split_test  = session_ids[n_train + n_val :]

    # ── Verify no overlap ────────────────────────────────────────────────────
    assert np.intersect1d(split_train, split_val).size  == 0, "train/val overlap"
    assert np.intersect1d(split_train, split_test).size == 0, "train/test overlap"
    assert np.intersect1d(split_val,   split_test).size == 0, "val/test overlap"
    assert len(split_train) + len(split_val) + len(split_test) == n_total

    # ── Save ─────────────────────────────────────────────────────────────────
    np.save(os.path.join(OUT_DIR, "split_train.npy"), split_train)
    np.save(os.path.join(OUT_DIR, "split_val.npy"),   split_val)
    np.save(os.path.join(OUT_DIR, "split_test.npy"),  split_test)

    elapsed = time.time() - t0
    log.info(
        f"Split complete in {elapsed:.1f}s  "
        f"train={len(split_train):,}  val={len(split_val):,}  test={len(split_test):,}"
    )

    # ── MLflow ───────────────────────────────────────────────────────────────
    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params({"seed": SEED, "train_frac": TRAIN_FRAC, "val_frac": VAL_FRAC})
        mlflow.log_metrics({
            "n_total":  n_total,
            "n_train":  len(split_train),
            "n_val":    len(split_val),
            "n_test":   len(split_test),
        })

    return {
        "n_total":  n_total,
        "n_train":  len(split_train),
        "n_val":    len(split_val),
        "n_test":   len(split_test),
        "out_dir":  OUT_DIR,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    result = run()
    for k, v in result.items():
        log.info(f"  {k}: {v}")
