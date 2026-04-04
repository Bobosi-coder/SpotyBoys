"""
Stage A — Build Item2Vec Training Corpus

Input:  data/raw/content/30music_parsed/session_tracks.csv  (~31M rows)
Output: data/processed/item2vec_corpus.parquet
        schema: session_id (int64), track_ids (list<int32>), length (int16)

Label filter: keep positive + neutral; drop skip + unknown.

Memory strategy:
  1. Read CSV in 500K-row chunks, filter immediately, keep only 3 int columns.
  2. Concat filtered chunks into one compact DataFrame (~600 MB vs ~1.8 GB dict).
  3. Sort by (session_id, position), then extract two numpy arrays and del DataFrame.
  4. Stream through numpy arrays to find group boundaries — no Python dict, no records list.
  5. Write to parquet in rolling batches of 50K sessions; each batch is tiny.
  Peak memory: ~1.2 GB (during pandas sort), well within 2.9 GB.
"""
import logging
import os
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

RAW_DIR     = "data/raw/content/30music_parsed"
OUT_DIR     = "data/processed"
CHUNK_SIZE  = 500_000
MIN_SEQ     = 2
WRITE_BATCH = 50_000
KEEP_LABELS = {"positive", "neutral"}

log = logging.getLogger("item2vec.stage_a")


def run(corpus_path: str = None) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    corpus_path = corpus_path or os.path.join(OUT_DIR, "item2vec_corpus.parquet")

    src = os.path.join(RAW_DIR, "session_tracks.csv")
    log.info(f"Stage A — building corpus from {src}")
    t0 = time.time()

    # ── Step 1: chunked read, filter immediately, keep only 3 int columns ──
    filtered_chunks = []
    n_raw = n_kept = 0

    for chunk in pd.read_csv(
        src,
        usecols=["session_id", "position", "track_id", "label"],
        chunksize=CHUNK_SIZE,
        low_memory=False,
    ):
        n_raw += len(chunk)
        chunk = chunk[chunk["label"].isin(KEEP_LABELS)]
        chunk = chunk.dropna(subset=["track_id"])
        chunk = chunk[["session_id", "position", "track_id"]].copy()
        chunk["session_id"] = chunk["session_id"].astype("int64")
        chunk["track_id"]   = chunk["track_id"].astype("int32")
        chunk["position"]   = pd.to_numeric(chunk["position"], errors="coerce") \
                                .fillna(0).astype("int16")
        n_kept += len(chunk)
        filtered_chunks.append(chunk)

        if (n_raw // CHUNK_SIZE) % 10 == 0:
            log.info(f"  read {n_raw:,} rows, kept {n_kept:,}...")

    log.info(f"Loaded {n_raw:,} rows → kept {n_kept:,} after label filter")

    # ── Step 2: concat into one compact DataFrame ──
    df = pd.concat(filtered_chunks, ignore_index=True)
    del filtered_chunks   # free chunk list immediately

    log.info(f"Sorting {len(df):,} rows by (session_id, position)...")
    df.sort_values(["session_id", "position"], inplace=True, ignore_index=True)

    # ── Step 3: extract numpy arrays, del DataFrame ──
    sids = df["session_id"].values   # int64 array
    tids = df["track_id"].values     # int32 array
    del df                           # free DataFrame — arrays are independent copies

    # ── Step 4: find group boundaries via numpy diff ──
    # Since sids is sorted, groups are contiguous
    boundaries = np.concatenate(([0], np.where(np.diff(sids) != 0)[0] + 1, [len(sids)]))
    n_raw_sessions = len(boundaries) - 1
    log.info(f"Raw sessions: {n_raw_sessions:,}")

    # ── Step 5: stream through groups, write parquet in rolling batches ──
    schema = pa.schema([
        pa.field("session_id", pa.int64()),
        pa.field("track_ids",  pa.list_(pa.int32())),
        pa.field("length",     pa.int16()),
    ])

    writer = pq.ParquetWriter(corpus_path, schema, compression="snappy")

    batch_sids    = []
    batch_tids    = []
    batch_lengths = []
    n_sessions = n_dropped_short = n_tokens = 0

    for i in range(n_raw_sessions):
        start = boundaries[i]
        end   = boundaries[i + 1]
        seq   = tids[start:end].tolist()

        if len(seq) < MIN_SEQ:
            n_dropped_short += 1
            continue

        batch_sids.append(int(sids[start]))
        batch_tids.append(seq)
        batch_lengths.append(len(seq))
        n_tokens  += len(seq)
        n_sessions += 1

        if len(batch_sids) >= WRITE_BATCH:
            writer.write_table(pa.table({
                "session_id": pa.array(batch_sids,    type=pa.int64()),
                "track_ids":  pa.array(batch_tids,    type=pa.list_(pa.int32())),
                "length":     pa.array(batch_lengths, type=pa.int16()),
            }))
            batch_sids.clear()
            batch_tids.clear()
            batch_lengths.clear()

    # flush remaining
    if batch_sids:
        writer.write_table(pa.table({
            "session_id": pa.array(batch_sids,    type=pa.int64()),
            "track_ids":  pa.array(batch_tids,    type=pa.list_(pa.int32())),
            "length":     pa.array(batch_lengths, type=pa.int16()),
        }))
    writer.close()

    del sids, tids, boundaries

    # vocab count from the written file (no extra in-memory set)
    n_vocab = int(pq.read_table(corpus_path, columns=["track_ids"])
                  .column("track_ids")
                  .flatten()
                  .unique()
                  .to_pylist().__len__())

    log.info(f"Sessions dropped (length < {MIN_SEQ}): {n_dropped_short:,}")
    log.info(f"Final sessions: {n_sessions:,} | Total tokens: {n_tokens:,} | "
             f"Unique track_ids: {n_vocab:,}")
    log.info(f"Wrote {corpus_path}  ({os.path.getsize(corpus_path)/1e6:.1f} MB)  "
             f"elapsed {time.time()-t0:.0f}s")

    return {
        "n_raw_rows":         n_raw,
        "n_kept_rows":        n_kept,
        "n_sessions":         n_sessions,
        "n_dropped_short":    n_dropped_short,
        "n_tokens":           n_tokens,
        "n_vocab_candidates": n_vocab,
        "corpus_path":        corpus_path,
    }
