"""
Stage C — Validate Item2Vec Embeddings

Checks:
  1. Same-artist cosine similarity > random pairs
  2. Embedding norm statistics (mean, std, NaN/Inf)
  3. Top-5 nearest neighbours for 5 random tracks

Logs results to MLflow (continues the Stage B run via run_id).
"""
import logging
import os
import random
import time

import mlflow
import numpy as np
import pandas as pd
from gensim.models import KeyedVectors

RAW_DIR = "data/raw/content/30music_parsed"
OUT_DIR = "artifacts/item2vec"
MDL_DIR = "artifacts/item2vec"
LOG_DIR = "logs"

log = logging.getLogger("item2vec.stage_c")


def run(run_id: str = None, mlflow_experiment: str = "item2vec-training") -> dict:
    os.makedirs(LOG_DIR, exist_ok=True)

    model_path   = os.path.join(MDL_DIR, "item2vec_model.bin")
    catalog_path = os.path.join(OUT_DIR, "item2vec_catalog.csv")
    nbr_log_path = os.path.join(LOG_DIR, "item2vec_neighbors_sample.txt")

    log.info("Stage C — loading model for validation...")
    wv      = KeyedVectors.load(model_path)
    catalog = pd.read_csv(catalog_path, low_memory=False)
    catalog["track_id"] = catalog["track_id"].astype("int64")

    # ── 1. Norm statistics ────────────────────────────────────────────────────
    vecs     = wv.vectors
    norms    = np.linalg.norm(vecs, axis=1)
    n_nan    = int(np.isnan(vecs).sum())
    n_inf    = int(np.isinf(vecs).sum())
    norm_mean = float(norms.mean())
    norm_std  = float(norms.std())
    log.info(f"Norm stats: mean={norm_mean:.4f}, std={norm_std:.4f}, NaN={n_nan}, Inf={n_inf}")

    # ── 2. Same-artist cosine similarity ─────────────────────────────────────
    artist_groups = (
        catalog.dropna(subset=["artist_hint"])
        .groupby("artist_hint")["track_id"]
        .apply(list)
    )
    multi = artist_groups[artist_groups.apply(len) >= 2]

    same_sims, rand_sims = [], []
    n_attempts = 50

    if len(multi) >= 10:
        for _ in range(n_attempts):
            artist   = random.choice(multi.index.tolist())
            t1, t2   = random.sample(multi[artist], 2)
            k1, k2   = str(t1), str(t2)
            if k1 in wv and k2 in wv:
                same_sims.append(float(wv.similarity(k1, k2)))

        all_keys = list(wv.index_to_key)
        for _ in range(n_attempts):
            k1, k2 = random.sample(all_keys, 2)
            rand_sims.append(float(wv.similarity(k1, k2)))

    mean_same  = float(np.mean(same_sims)) if same_sims else float("nan")
    mean_rand  = float(np.mean(rand_sims)) if rand_sims else float("nan")
    passed     = mean_same > mean_rand

    level = logging.INFO if passed else logging.WARNING
    log.log(level,
        f"Same-artist cosine={mean_same:.4f}  random={mean_rand:.4f}  "
        f"{'PASS' if passed else 'FAIL'}"
    )

    # ── 3. Nearest neighbour samples ─────────────────────────────────────────
    sample_keys = random.sample(list(wv.index_to_key), min(5, len(wv)))
    lines = []
    for key in sample_keys:
        tid  = int(key)
        row  = catalog[catalog["track_id"] == tid]
        name = f"{row.iloc[0]['artist_hint']} — {row.iloc[0]['title']}" \
               if len(row) else f"track_id={tid}"
        neighbours = wv.most_similar(key, topn=5)
        lines.append(f"\n[{name}]")
        for nkey, score in neighbours:
            ntid = int(nkey)
            nrow = catalog[catalog["track_id"] == ntid]
            nname = f"{nrow.iloc[0]['artist_hint']} — {nrow.iloc[0]['title']}" \
                    if len(nrow) else f"track_id={ntid}"
            lines.append(f"  {score:.4f}  {nname}")

    with open(nbr_log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info(f"Neighbours sample → {nbr_log_path}")
    print("\n".join(lines))

    # ── MLflow logging ────────────────────────────────────────────────────────
    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics({
            "sanity_same_artist_cosine": round(mean_same, 4) if not np.isnan(mean_same) else -1,
            "sanity_random_cosine":      round(mean_rand, 4) if not np.isnan(mean_rand) else -1,
            "sanity_passed":             int(passed),
            "embed_norm_mean":           round(norm_mean, 4),
            "embed_norm_std":            round(norm_std, 4),
            "embed_nan_count":           n_nan,
        })
        mlflow.log_artifact(nbr_log_path)

    return {
        "sanity_passed":     passed,
        "mean_same_cosine":  mean_same,
        "mean_rand_cosine":  mean_rand,
        "norm_mean":         norm_mean,
        "norm_std":          norm_std,
    }
