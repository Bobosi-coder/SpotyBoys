"""
Ranker Training Data Build

Generates ranker_{train,val}.parquet and neg_sample_weights.npy from offline artifacts.

Parquet schema (one row per context-candidate pair, 6 rows per context):
  context_id    int64    groups 6 rows (1 positive + 5 negatives)
  session_id    int64
  user_id       int64
  prefix_ids    list<int32>  len=20, left-padded with 0
  prefix_labels list<int8>   len=20, 0=pos 1=neu 2=skip 3=pad
  prefix_len    int16        actual prefix length before padding
  candidate_id  int32
  y             float32      1.0 (positive) / 0.5 (neutral) / 0.0 (skip or negative)
  weight        float32      1.0 / 0.3 (neutral) / 1.0
  is_positive   bool         True for the anchor row

Negatives: 3 hard (top C_sess neighbors of last prefix track) + 2 random (∝ pop_count^0.75)

Inputs:
  artifacts/item2vec/session_tracks_i2v.parquet
  artifacts/item2vec/item2vec_catalog.csv
  artifacts/retriever/split/split_{train,val}.npy
  artifacts/retriever/cooc/cooc_session.npz
  artifacts/retriever/popularity/pop_scores.csv

Outputs:
  artifacts/ranker/ranker_train.parquet
  artifacts/ranker/ranker_val.parquet
  artifacts/ranker/neg_sample_weights.npy

CLI:
  uv run python -m src.ranker.data.build
  uv run python -m src.ranker.data.build --max-train-sessions 50000
"""
import argparse
import logging
import os
import time

import mlflow
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import scipy.sparse as sp

I2V_DIR      = "artifacts/item2vec"
SPLIT_DIR    = "artifacts/retriever/split"
COOC_DIR     = "artifacts/retriever/cooc"
POP_DIR      = "artifacts/retriever/popularity"
OUT_DIR      = "artifacts/ranker"

L            = 20          # prefix length (left-padded)
N_HARD       = 3           # hard negatives per context
N_RAND       = 2           # random negatives per context
N_NEG        = N_HARD + N_RAND   # total negatives = 5
WRITE_BATCH  = 100_000     # rows per parquet write flush

LABEL_ENC = {"positive": 0, "neutral": 1, "skip": 2}   # unknown / other → 3 (pad)

log = logging.getLogger("ranker.data.build")

_SCHEMA = pa.schema([
    pa.field("context_id",    pa.int64()),
    pa.field("session_id",    pa.int64()),
    pa.field("user_id",       pa.int64()),
    pa.field("prefix_ids",    pa.list_(pa.int32())),
    pa.field("prefix_labels", pa.list_(pa.int8())),
    pa.field("prefix_len",    pa.int16()),
    pa.field("candidate_id",  pa.int32()),
    pa.field("y",             pa.float32()),
    pa.field("weight",        pa.float32()),
    pa.field("is_positive",   pa.bool_()),
])


# ── Shared helpers ────────────────────────────────────────────────────────────

def _build_tid2idx() -> tuple[dict, list, int]:
    """
    Deduplicated catalog → (tid2idx, r2t, N).
    Identical dedup logic to src/retriever/cooc/build.py so indices are consistent.
    """
    catalog_path = os.path.join(I2V_DIR, "item2vec_catalog.csv")
    raw_ids = pd.read_csv(catalog_path, usecols=["track_id"])["track_id"].tolist()
    seen: set = set()
    unique_ids: list = []
    for t in raw_ids:
        ti = int(t)
        if ti not in seen:
            seen.add(ti)
            unique_ids.append(ti)
    tid2idx = {t: i for i, t in enumerate(unique_ids)}
    r2t = unique_ids   # row_index → track_id
    N = len(tid2idx)
    log.info(f"  Catalog: {len(raw_ids):,} rows → {N:,} unique track_ids")
    return tid2idx, r2t, N


def _build_neg_weights(tid2idx: dict, r2t: list, N: int) -> np.ndarray:
    """
    Negative sampling weights aligned with tid2idx row order.
    weight[i] = pop_count[r2t[i]]^0.75 / sum(pop_count^0.75)
    Tracks absent from pop_scores.csv get pop_count=1.
    """
    pop_path = os.path.join(POP_DIR, "pop_scores.csv")
    pop_df   = pd.read_csv(pop_path, usecols=["track_id", "track_count"])
    pop_count = dict(
        zip(pop_df["track_id"].tolist(), pop_df["track_count"].tolist())
    )
    counts  = np.array([float(pop_count.get(r2t[i], 1)) for i in range(N)], dtype=np.float64)
    powered = counts ** 0.75
    return (powered / powered.sum()).astype(np.float32)


def _build_cooc_neighbors(C_sess: sp.csr_matrix, r2t: list) -> list[list]:
    """
    Pre-compute per-row neighbor list from C_sess sorted by co-occurrence weight.
    Returns a list of length N where neighbors[i] = [track_id, ...] (descending weight).
    Much faster than calling C_sess.getrow(i).tocoo() per context at build time.
    """
    log.info("  Pre-computing cooc neighbor lists...")
    N = C_sess.shape[0]
    neighbors: list[list] = [[] for _ in range(N)]
    csr = C_sess.tocsr()
    for i in range(N):
        start = csr.indptr[i]
        end   = csr.indptr[i + 1]
        if end == start:
            continue
        cols = csr.indices[start:end]
        data = csr.data[start:end]
        order = np.argsort(-data)
        neighbors[i] = [r2t[cols[j]] for j in order]
    log.info("  Cooc neighbor lists ready.")
    return neighbors


def _hard_negs(
    cooc_neighbors: list[list],
    last_idx: int,
    exclude: set,
) -> list:
    """Up to N_HARD hard negatives from pre-computed cooc_neighbors of last prefix track."""
    if last_idx < 0 or last_idx >= len(cooc_neighbors):
        return []
    hard = []
    for tid in cooc_neighbors[last_idx]:
        if tid not in exclude:
            hard.append(tid)
            exclude.add(tid)
            if len(hard) == N_HARD:
                break
    return hard


# Pool size for batch pre-sampling of random negatives
_RAND_POOL_SIZE = 2_000_000

class _RandNegSampler:
    """
    Efficient random negative sampler using a pre-sampled pool.
    Avoids calling rng.choice(N, p=weights) one-at-a-time (O(N) per call).
    """
    def __init__(self, rng: np.random.Generator, N: int, neg_weights: np.ndarray, r2t: list):
        self._rng       = rng
        self._N         = N
        self._weights   = neg_weights
        self._r2t       = r2t
        self._pool: np.ndarray = np.empty(0, dtype=np.int64)
        self._pos       = 0
        self._refill()

    def _refill(self) -> None:
        self._pool = self._rng.choice(self._N, size=_RAND_POOL_SIZE,
                                      replace=True, p=self._weights)
        self._pos  = 0

    def sample(self, exclude: set, n_needed: int) -> list:
        rand = []
        attempts = 0
        while len(rand) < n_needed:
            if self._pos >= len(self._pool):
                self._refill()
            idx = int(self._pool[self._pos])
            self._pos += 1
            tid = self._r2t[idx]
            if tid not in exclude:
                rand.append(tid)
                exclude.add(tid)
            attempts += 1
            if attempts > n_needed * 200:   # safety fallback
                break
        # Extremely rare fallback: sequential scan
        if len(rand) < n_needed:
            for i in range(self._N):
                if self._r2t[i] not in exclude:
                    rand.append(self._r2t[i])
                    exclude.add(self._r2t[i])
                    if len(rand) == n_needed:
                        break
        return rand


def _make_prefix(
    prefix_ids_raw: list,
    prefix_labs_raw: list,
) -> tuple[list, list, int]:
    """Left-pad prefix to length L; return (pid_padded, plab_padded, plen)."""
    plen = min(len(prefix_ids_raw), L)
    pid  = [0] * (L - plen) + [int(x) for x in prefix_ids_raw[-plen:]]
    plab = [3] * (L - plen) + [LABEL_ENC.get(x, 3) for x in prefix_labs_raw[-plen:]]
    return pid, plab, plen


def _append_context(
    batch: list,
    context_id: int,
    session_id: int,
    user_id: int,
    pid: list,
    plab: list,
    plen: int,
    anchor_tid: int,
    anchor_y: float,
    anchor_w: float,
    negs: list,
) -> None:
    """Append 6 rows (1 positive + 5 negatives) to the batch accumulator in-place."""
    shared = (context_id, session_id, user_id, pid, plab, plen)

    # Positive row
    batch[0].append(context_id)
    batch[1].append(session_id)
    batch[2].append(user_id)
    batch[3].append(pid)
    batch[4].append(plab)
    batch[5].append(plen)
    batch[6].append(int(anchor_tid))
    batch[7].append(float(anchor_y))
    batch[8].append(float(anchor_w))
    batch[9].append(True)

    # Negative rows
    for neg_tid in negs:
        batch[0].append(context_id)
        batch[1].append(session_id)
        batch[2].append(user_id)
        batch[3].append(pid)
        batch[4].append(plab)
        batch[5].append(plen)
        batch[6].append(int(neg_tid))
        batch[7].append(0.0)
        batch[8].append(1.0)
        batch[9].append(False)


def _flush(batch: list, writer: pq.ParquetWriter) -> int:
    """Write accumulated batch to parquet; clear batch; return row count written."""
    n = len(batch[0])
    writer.write_table(pa.table({
        "context_id":    pa.array(batch[0], type=pa.int64()),
        "session_id":    pa.array(batch[1], type=pa.int64()),
        "user_id":       pa.array(batch[2], type=pa.int64()),
        "prefix_ids":    pa.array(batch[3], type=pa.list_(pa.int32())),
        "prefix_labels": pa.array(batch[4], type=pa.list_(pa.int8())),
        "prefix_len":    pa.array(batch[5], type=pa.int16()),
        "candidate_id":  pa.array(batch[6], type=pa.int32()),
        "y":             pa.array(batch[7], type=pa.float32()),
        "weight":        pa.array(batch[8], type=pa.float32()),
        "is_positive":   pa.array(batch[9], type=pa.bool_()),
    }))
    for b in batch:
        b.clear()
    return n


# ── Main split builder ────────────────────────────────────────────────────────

def _build_split(
    split_name: str,
    split_set: set,
    tids: np.ndarray,
    sids: np.ndarray,
    uids: np.ndarray,
    labels: np.ndarray,
    tid2idx: dict,
    r2t: list,
    N: int,
    neg_weights: np.ndarray,
    C_sess: sp.csr_matrix,
    cooc_neighbors: list,
    rng: np.random.Generator,
    out_path: str,
    is_val: bool,
    max_sessions: int | None,
) -> dict:
    """
    Iterate sessions in split_set, generate training contexts, write parquet.

    Train mode: one context per position t = 0 .. N_seq-3, positive = tracks[t+1]
    Val mode:   one context per session,  positive = tracks[-1]
    """
    rand_sampler = _RandNegSampler(rng, N, neg_weights, r2t)

    boundaries = np.concatenate(
        ([0], np.where(np.diff(sids) != 0)[0] + 1, [len(sids)])
    )
    n_all = len(boundaries) - 1
    log.info(f"  Scanning {n_all:,} sessions in parquet for {split_name} split...")

    writer   = pq.ParquetWriter(out_path, _SCHEMA, compression="snappy")
    batch: list[list] = [[] for _ in range(10)]

    ctx_ctr = 0
    n_sess = 0
    n_rows = 0
    t_start = time.time()
    LOG_EVERY = 100_000   # log progress every N sessions processed

    for i in range(n_all):
        if max_sessions is not None and n_sess >= max_sessions:
            break

        start = int(boundaries[i])
        end   = int(boundaries[i + 1])
        sid   = int(sids[start])

        if sid not in split_set:
            continue

        n_sess += 1
        if n_sess % LOG_EVERY == 0:
            elapsed = time.time() - t_start
            log.info(
                f"  [{split_name}] {n_sess:,} sessions  {ctx_ctr:,} contexts  "
                f"{n_rows:,} rows  ({elapsed:.0f}s)"
            )

        uid   = int(uids[start])
        seq_t = tids[start:end].tolist()
        seq_l = labels[start:end].tolist()
        N_seq = len(seq_t)

        if is_val:
            # ── Val: one context per session, anchor = last track ─────────────
            if N_seq < 2:
                continue
            anchor_tid   = seq_t[-1]
            anchor_label = seq_l[-1]
            if anchor_label == "unknown":
                continue

            if anchor_label == "positive":
                y_val, w_val = 1.0, 1.0
            elif anchor_label == "neutral":
                y_val, w_val = 0.5, 0.3
            else:   # skip
                y_val, w_val = 0.0, 1.0

            pid, plab, plen = _make_prefix(seq_t[:-1], seq_l[:-1])

            last_prefix_idx = tid2idx.get(seq_t[-2], -1)
            exclude = {anchor_tid}
            hard = _hard_negs(cooc_neighbors, last_prefix_idx, exclude)
            rand = rand_sampler.sample(exclude, N_NEG - len(hard))
            negs = hard + rand

            _append_context(batch, ctx_ctr, sid, uid, pid, plab, plen,
                            anchor_tid, y_val, w_val, negs)
            ctx_ctr += 1

        else:
            # ── Train: one context per position t = 0 .. N_seq-3 ────────────
            if N_seq < 3:
                continue

            for t in range(N_seq - 2):
                anchor_tid   = seq_t[t + 1]
                anchor_label = seq_l[t + 1]
                if anchor_label == "unknown":
                    continue

                if anchor_label == "positive":
                    y_val, w_val = 1.0, 1.0
                elif anchor_label == "neutral":
                    y_val, w_val = 0.5, 0.3
                else:   # skip
                    y_val, w_val = 0.0, 1.0

                pid, plab, plen = _make_prefix(seq_t[:t + 1], seq_l[:t + 1])

                last_idx = tid2idx.get(seq_t[t], -1)
                exclude  = {anchor_tid}
                hard = _hard_negs(cooc_neighbors, last_idx, exclude)
                rand = rand_sampler.sample(exclude, N_NEG - len(hard))
                negs = hard + rand

                _append_context(batch, ctx_ctr, sid, uid, pid, plab, plen,
                                anchor_tid, y_val, w_val, negs)
                ctx_ctr += 1

                if len(batch[0]) >= WRITE_BATCH:
                    n_rows += _flush(batch, writer)

    if batch[0]:
        n_rows += _flush(batch, writer)
    writer.close()

    sz_mb = os.path.getsize(out_path) / 1e6
    log.info(
        f"  {split_name}: {n_sess:,} sessions → {ctx_ctr:,} contexts → "
        f"{n_rows:,} rows  ({sz_mb:.0f} MB)  → {out_path}"
    )
    return {"n_sessions": n_sess, "n_contexts": ctx_ctr, "n_rows": n_rows}


# ── Entry point ────────────────────────────────────────────────────────────────

def run(
    mlflow_experiment: str = "ranker-data-build",
    run_name: str = "data-build",
    max_train_sessions: int | None = None,
    max_val_sessions: int | None = None,
    seed: int = 42,
) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    t0  = time.time()
    rng = np.random.default_rng(seed)

    # ── Catalog & vocab ────────────────────────────────────────────────────
    log.info("Loading catalog...")
    tid2idx, r2t, N = _build_tid2idx()

    # ── Negative sample weights ────────────────────────────────────────────
    log.info("Building neg_sample_weights...")
    neg_weights = _build_neg_weights(tid2idx, r2t, N)
    nsw_path = os.path.join(OUT_DIR, "neg_sample_weights.npy")
    np.save(nsw_path, neg_weights)
    log.info(f"  Saved {nsw_path}")

    # ── Co-occurrence matrix ───────────────────────────────────────────────
    log.info("Loading C_sess...")
    C_sess = sp.load_npz(os.path.join(COOC_DIR, "cooc_session.npz"))
    log.info(f"  C_sess: {C_sess.shape}  nnz={C_sess.nnz:,}")
    cooc_neighbors = _build_cooc_neighbors(C_sess, r2t)

    # ── Split sets ─────────────────────────────────────────────────────────
    log.info("Loading split sets...")
    train_set = set(np.load(os.path.join(SPLIT_DIR, "split_train.npy")).tolist())
    val_set   = set(np.load(os.path.join(SPLIT_DIR, "split_val.npy")).tolist())
    log.info(f"  Train: {len(train_set):,}  Val: {len(val_set):,}")

    # ── Session data ───────────────────────────────────────────────────────
    log.info("Loading session_tracks_i2v.parquet...")
    table = pq.read_table(
        os.path.join(I2V_DIR, "session_tracks_i2v.parquet"),
        columns=["session_id", "user_id", "position", "track_id", "label"],
    )
    df = table.to_pandas()
    del table
    df["session_id"] = df["session_id"].astype("int64")
    df["user_id"]    = df["user_id"].astype("int64")
    df["track_id"]   = df["track_id"].astype("int32")
    df["position"]   = df["position"].astype("int32")
    df.sort_values(["session_id", "position"], inplace=True, ignore_index=True)
    log.info(f"  Session rows: {len(df):,}")

    sids   = df["session_id"].values
    uids   = df["user_id"].values
    tids   = df["track_id"].values
    labels = df["label"].values
    del df

    # ── Build train ────────────────────────────────────────────────────────
    log.info("── Building train data ──")
    train_m = _build_split(
        "train", train_set, tids, sids, uids, labels,
        tid2idx, r2t, N, neg_weights, C_sess, cooc_neighbors, rng,
        out_path=os.path.join(OUT_DIR, "ranker_train.parquet"),
        is_val=False, max_sessions=max_train_sessions,
    )

    # ── Build val ──────────────────────────────────────────────────────────
    log.info("── Building val data ──")
    val_m = _build_split(
        "val", val_set, tids, sids, uids, labels,
        tid2idx, r2t, N, neg_weights, C_sess, cooc_neighbors, rng,
        out_path=os.path.join(OUT_DIR, "ranker_val.parquet"),
        is_val=True, max_sessions=max_val_sessions,
    )

    elapsed = time.time() - t0
    log.info(f"Total elapsed: {elapsed:.0f}s")

    metrics = {
        "N_vocab":        N,
        "train_contexts": train_m["n_contexts"],
        "train_rows":     train_m["n_rows"],
        "val_contexts":   val_m["n_contexts"],
        "val_rows":       val_m["n_rows"],
    }

    # ── MLflow ─────────────────────────────────────────────────────────────
    mlflow.set_experiment(mlflow_experiment)
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params({
            "seed": seed, "L": L, "n_hard": N_HARD, "n_rand": N_RAND,
            "max_train_sessions": max_train_sessions or "all",
            "max_val_sessions":   max_val_sessions or "all",
        })
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(nsw_path)

    return metrics


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Build ranker training data")
    parser.add_argument("--max-train-sessions", type=int, default=None,
                        help="Subsample N training sessions (default: all)")
    parser.add_argument("--max-val-sessions",   type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    result = run(
        max_train_sessions=args.max_train_sessions,
        max_val_sessions=args.max_val_sessions,
        seed=args.seed,
    )
    for k, v in result.items():
        log.info(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")
