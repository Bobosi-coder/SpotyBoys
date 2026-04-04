"""
Stage B — Train Item2Vec (Word2Vec Skip-gram on session sequences)

Input:  artifacts/item2vec/item2vec_corpus.parquet
Output:
  artifacts/item2vec/item2vec_model.bin          gensim KeyedVectors
  artifacts/item2vec/item2vec_128d.npy           float32 (vocab_size, vector_size)
  artifacts/item2vec/item2vec_track_to_row.json
  artifacts/item2vec/item2vec_catalog.csv        (track_id, artist_hint, title)

MLflow experiment: item2vec-training
"""
import json
import logging
import os
import time

import mlflow
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from gensim.models import Word2Vec

RAW_DIR = "data/raw/content/30music_parsed"
OUT_DIR = "artifacts/item2vec"
MDL_DIR = "artifacts/item2vec"

log = logging.getLogger("item2vec.stage_b")


class CorpusIterator:
    """Streams list-of-track_id sequences from parquet without loading all into RAM."""

    def __init__(self, corpus_path: str):
        self.corpus_path = corpus_path
        self._len = None

    def __iter__(self):
        table = pq.read_table(self.corpus_path, columns=["track_ids"])
        for batch in table.to_batches(max_chunksize=10_000):
            col = batch.column("track_ids")
            for seq in col.to_pylist():
                yield [str(t) for t in seq]

    def __len__(self):
        if self._len is None:
            self._len = pq.read_metadata(self.corpus_path).num_rows
        return self._len


def run(
    corpus_path:    str  = None,
    vector_size:    int  = 128,
    window:         int  = 10,
    min_count:      int  = 5,
    negative:       int  = 15,
    epochs:         int  = 10,
    workers:        int  = 8,
    mlflow_experiment: str = "item2vec-training",
    run_name:       str  = "item2vec-run",
    run_id:         str  = None,   # pass to continue an existing MLflow run
) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(MDL_DIR, exist_ok=True)

    corpus_path  = corpus_path  or os.path.join(OUT_DIR, "item2vec_corpus.parquet")
    model_path   = os.path.join(MDL_DIR, "item2vec_model.bin")
    npy_path     = os.path.join(OUT_DIR, f"item2vec_{vector_size}d.npy")
    row_map_path = os.path.join(OUT_DIR, "item2vec_track_to_row.json")
    catalog_path = os.path.join(OUT_DIR, "item2vec_catalog.csv")

    corpus     = CorpusIterator(corpus_path)
    n_sessions = len(corpus)
    # quick token count from parquet metadata column stats not available cheaply;
    # compute from the length column instead
    length_col = pq.read_table(corpus_path, columns=["length"])["length"].to_pylist()
    n_tokens   = sum(length_col)
    log.info(f"Corpus: {n_sessions:,} sessions, {n_tokens:,} tokens")

    mlflow.set_experiment(mlflow_experiment)
    ctx = mlflow.start_run(run_id=run_id, run_name=run_name) if run_id is None \
          else mlflow.start_run(run_id=run_id)

    with ctx as active_run:
        mlflow.log_params({
            "vector_size":    vector_size,
            "window":         window,
            "min_count":      min_count,
            "negative":       negative,
            "epochs":         epochs,
            "workers":        workers,
            "sg":             1,
            "ns_exponent":    0.75,
            "corpus_sessions": n_sessions,
            "corpus_tokens":  n_tokens,
        })

        log.info("Training Word2Vec (Skip-gram)...")
        t0 = time.time()
        model = Word2Vec(
            sentences=corpus,
            vector_size=vector_size,
            window=window,
            min_count=min_count,
            negative=negative,
            ns_exponent=0.75,
            epochs=epochs,
            workers=workers,
            sg=1,
            seed=42,
        )
        train_time = time.time() - t0
        log.info(f"Training done in {train_time:.0f}s | vocab size: {len(model.wv):,}")

        # Export embedding matrix + row map
        vocab_keys   = list(model.wv.index_to_key)        # list of str track_ids
        track_to_row = {k: i for i, k in enumerate(vocab_keys)}
        matrix       = model.wv.vectors.astype("float32")  # (vocab_size, vector_size)

        np.save(npy_path, matrix)
        with open(row_map_path, "w") as f:
            json.dump(track_to_row, f)
        model.wv.save(model_path)
        log.info(f"Saved model → {model_path}")
        log.info(f"Saved matrix → {npy_path}  shape={matrix.shape}")

        # Build item2vec_catalog.csv: join vocab track_ids with tracks.csv metadata
        vocab_int = [int(k) for k in vocab_keys]
        tracks_df = pd.read_csv(
            os.path.join(RAW_DIR, "tracks.csv"),
            usecols=["track_id", "artist_hint", "title"],
            low_memory=False,
        )
        tracks_df["track_id"] = pd.to_numeric(tracks_df["track_id"], errors="coerce")
        tracks_df = tracks_df.dropna(subset=["track_id"])
        tracks_df["track_id"] = tracks_df["track_id"].astype("int64")
        vocab_df  = pd.DataFrame({"track_id": vocab_int})
        catalog   = vocab_df.merge(tracks_df, on="track_id", how="left")
        catalog.to_csv(catalog_path, index=False)
        log.info(f"Saved catalog → {catalog_path}  ({len(catalog):,} rows)")

        # Coverage vs total unique tracks in tracks.csv
        total_tracks     = len(tracks_df["track_id"].unique())
        coverage_pct     = 100.0 * len(vocab_int) / total_tracks
        del tracks_df, vocab_df, catalog, matrix, vocab_keys, track_to_row

        mlflow.log_metrics({
            "vocab_size":          len(model.wv),
            "catalog_coverage_pct": round(coverage_pct, 2),
            "training_time_sec":   round(train_time, 1),
        })
        mlflow.log_artifact(model_path)
        mlflow.log_artifact(npy_path)
        mlflow.log_artifact(catalog_path)

        log.info(f"Coverage vs tracks.csv: {coverage_pct:.1f}%  ({len(vocab_int):,} / {total_tracks:,})")

        active_run_id = active_run.info.run_id

    return {
        "run_id":         active_run_id,
        "vocab_size":     len(model.wv),
        "coverage_pct":   coverage_pct,
        "train_time_sec": train_time,
        "model_path":     model_path,
        "npy_path":       npy_path,
        "catalog_path":   catalog_path,
    }
