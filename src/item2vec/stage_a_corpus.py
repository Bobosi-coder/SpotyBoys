"""
Stage A — Build Item2Vec Training Corpus

Input:  data/raw/content/30music_parsed/session_tracks.csv  (~31M rows)
Output: data/processed/item2vec_corpus.parquet
        schema: session_id (int64), track_ids (list<int32>), length (int16)

Label filter: keep positive + neutral; drop skip + unknown.
Memory strategy: chunked read (500K rows), progressive dict aggregation.
"""
import logging
import os
import time
from collections import defaultdict

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

RAW_DIR    = "data/raw/content/30music_parsed"
OUT_DIR    = "data/processed"
CHUNK_SIZE = 500_000
MIN_SEQ    = 2
KEEP_LABELS = {"positive", "neutral"}

log = logging.getLogger("item2vec.stage_a")


def run(corpus_path: str = None) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    corpus_path = corpus_path or os.path.join(OUT_DIR, "item2vec_corpus.parquet")

    src = os.path.join(RAW_DIR, "session_tracks.csv")
    log.info(f"Stage A — building corpus from {src}")
    t0 = time.time()

    sessions: dict[int, list[tuple[int, int]]] = defaultdict(list)
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
        chunk["track_id"] = chunk["track_id"].astype("int32")
        chunk["position"] = pd.to_numeric(chunk["position"], errors="coerce").fillna(0).astype("int16")
        n_kept += len(chunk)

        for row in chunk.itertuples(index=False):
            sessions[int(row.session_id)].append((int(row.position), int(row.track_id)))

        if (n_raw // CHUNK_SIZE) % 10 == 0:
            log.info(f"  processed {n_raw:,} rows, kept {n_kept:,}...")

    log.info(f"Loaded {n_raw:,} rows, kept {n_kept:,} after label filter")
    log.info(f"Raw sessions: {len(sessions):,}")

    # Sort by position, extract track_id lists, filter short sessions
    records = []
    n_dropped_short = 0
    for sid, entries in sessions.items():
        entries.sort(key=lambda x: x[0])
        track_ids = [e[1] for e in entries]
        if len(track_ids) < MIN_SEQ:
            n_dropped_short += 1
            continue
        records.append({"session_id": sid, "track_ids": track_ids, "length": len(track_ids)})

    log.info(f"Sessions dropped (length < {MIN_SEQ}): {n_dropped_short:,}")
    log.info(f"Final sessions: {len(records):,}")

    n_tokens   = sum(r["length"] for r in records)
    n_vocab    = len({tid for r in records for tid in r["track_ids"]})
    log.info(f"Total tokens: {n_tokens:,} | Unique track_ids: {n_vocab:,}")

    # Write parquet with list column
    schema = pa.schema([
        pa.field("session_id", pa.int64()),
        pa.field("track_ids",  pa.list_(pa.int32())),
        pa.field("length",     pa.int16()),
    ])
    table = pa.Table.from_pylist(records, schema=schema)
    pq.write_table(table, corpus_path, compression="snappy")
    log.info(f"Wrote {corpus_path}  ({os.path.getsize(corpus_path)/1e6:.1f} MB)  elapsed {time.time()-t0:.0f}s")

    return {
        "n_raw_rows":       n_raw,
        "n_kept_rows":      n_kept,
        "n_sessions":       len(records),
        "n_dropped_short":  n_dropped_short,
        "n_tokens":         n_tokens,
        "n_vocab_candidates": n_vocab,
        "corpus_path":      corpus_path,
    }
