"""
RankerDataset — PyTorch Dataset for GRU Ranker training

Loads ranker_{train,val}.parquet, item2vec embeddings, and user centroids.
Each __getitem__ returns all 6 rows (1 positive + 5 negatives) for one context,
with prefix tensors converted from left-padded (storage) to right-padded (GRU input).

Collate function expands per-context tensors to (B*6, ...) for batched model forward.

Memory note: parquet is loaded fully into RAM.
Use --max-train-sessions in data/build.py to create a smaller dev set.
"""
import json
import logging
import pickle

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

log = logging.getLogger("ranker.dataset")

L_PREFIX = 20   # must match data/build.py


class RankerDataset(Dataset):
    """
    Args:
        parquet_path:    path to ranker_train.parquet or ranker_val.parquet
        emb_path:        path to item2vec_128d.npy
        t2r_path:        path to item2vec_track_to_row.json
        centroids_path:  path to user_centroids.pkl
    """

    def __init__(
        self,
        parquet_path: str,
        emb_path: str,
        t2r_path: str,
        centroids_path: str,
    ):
        log.info(f"Loading parquet: {parquet_path}")
        df = pd.read_parquet(parquet_path)
        n_rows = len(df)
        assert n_rows % 6 == 0, f"Expected rows divisible by 6, got {n_rows}"
        self.n_contexts = n_rows // 6

        # Scalar columns → numpy arrays
        self.user_ids     = df["user_id"].values.astype(np.int64)     # (n_rows,)
        self.prefix_len   = df["prefix_len"].values.astype(np.int32)  # (n_rows,)
        self.candidate_id = df["candidate_id"].values.astype(np.int32)
        self.y            = df["y"].values.astype(np.float32)
        self.weight       = df["weight"].values.astype(np.float32)
        self.is_positive  = df["is_positive"].values.astype(bool)

        # List columns → 2D numpy arrays
        log.info("  Converting list columns to numpy arrays...")
        self.prefix_ids    = np.array(df["prefix_ids"].tolist(),    dtype=np.int32)  # (n_rows, 20)
        self.prefix_labels = np.array(df["prefix_labels"].tolist(), dtype=np.int8)   # (n_rows, 20)
        del df
        log.info(f"  Loaded {self.n_contexts:,} contexts ({n_rows:,} rows)")

        # Item2Vec embeddings
        log.info(f"Loading embeddings: {emb_path}")
        self.emb = np.load(emb_path).astype(np.float32)   # (N_vocab, 128)
        log.info(f"  Embedding shape: {self.emb.shape}")

        # track_id → row mapping
        with open(t2r_path) as f:
            t2r_raw = json.load(f)
        self.t2r: dict[int, int] = {int(k): v for k, v in t2r_raw.items()}

        # User centroids for u_long
        with open(centroids_path, "rb") as f:
            self.user_centroids: dict[int, list] = pickle.load(f)
        log.info(f"  User centroids: {len(self.user_centroids):,} users")

    def __len__(self) -> int:
        return self.n_contexts

    def _get_ulong(self, user_id: int, prefix_ids_right: np.ndarray, prefix_len: int) -> np.ndarray:
        """
        Return the user centroid nearest to the current session mean embedding.
        prefix_ids_right is right-padded (real tokens in positions 0..prefix_len-1).
        Returns zeros (128,) for cold users or empty prefix.
        """
        centroids = self.user_centroids.get(int(user_id))
        if not centroids:
            return np.zeros(128, dtype=np.float32)

        rows = [self.t2r[int(tid)] for tid in prefix_ids_right[:prefix_len]
                if int(tid) in self.t2r]
        if not rows:
            return np.array(centroids[0][0], dtype=np.float32)

        sess_mean = self.emb[rows].mean(axis=0)   # (128,)
        best_c, best_d = None, float("inf")
        for cvec, _ in centroids:
            c = np.array(cvec, dtype=np.float32)
            d = float(np.sum((sess_mean - c) ** 2))
            if d < best_d:
                best_d, best_c = d, c
        return best_c

    def __getitem__(self, idx: int) -> dict:
        """
        Returns all tensors needed for one context (6 candidates).

        item_embs  (20, 128)  right-padded prefix embeddings
        labels     (20,)      int64 right-padded prefix labels
        prefix_len ()         int64 scalar
        u_long     (128,)     long-term preference (zeros for cold users)
        cand_embs  (6, 128)   one per candidate
        y          (6,)       float32 targets
        weight     (6,)       float32 sample weights
        is_positive (6,)      bool
        """
        row0 = idx * 6

        # Shared context fields (same for all 6 rows)
        user_id    = int(self.user_ids[row0])
        prefix_len = int(self.prefix_len[row0])

        # Convert left-padded storage format to right-padded for GRU
        pid_left  = self.prefix_ids[row0]      # (20,) left-padded, pad=0
        plab_left = self.prefix_labels[row0]   # (20,) left-padded, pad=3

        pid_right  = np.zeros(L_PREFIX, dtype=np.int32)
        plab_right = np.full(L_PREFIX, 3, dtype=np.int8)
        if prefix_len > 0:
            real = pid_left[-prefix_len:]            # last `prefix_len` elements
            pid_right[:prefix_len]  = real
            plab_right[:prefix_len] = plab_left[-prefix_len:]

        # Prefix embeddings (right-padded; pad positions stay zero)
        item_embs = np.zeros((L_PREFIX, 128), dtype=np.float32)
        for pos in range(prefix_len):
            tid = int(pid_right[pos])
            row = self.t2r.get(tid)
            if row is not None:
                item_embs[pos] = self.emb[row]

        # u_long: nearest centroid to session mean
        u_long = self._get_ulong(user_id, pid_right, prefix_len)

        # Per-candidate embeddings (6 candidates)
        cand_embs = np.zeros((6, 128), dtype=np.float32)
        for k in range(6):
            cid = int(self.candidate_id[row0 + k])
            row = self.t2r.get(cid)
            if row is not None:
                cand_embs[k] = self.emb[row]

        return {
            "item_embs":   torch.from_numpy(item_embs),
            "labels":      torch.from_numpy(plab_right.astype(np.int64)),
            "prefix_len":  torch.tensor(prefix_len, dtype=torch.int64),
            "u_long":      torch.from_numpy(u_long),
            "cand_embs":   torch.from_numpy(cand_embs),                          # (6, 128)
            "y":           torch.from_numpy(self.y[row0:row0 + 6].copy()),
            "weight":      torch.from_numpy(self.weight[row0:row0 + 6].copy()),
            "is_positive": torch.from_numpy(self.is_positive[row0:row0 + 6].copy()),
        }


def collate_fn(batch: list[dict]) -> dict:
    """
    Collate B context dicts into a single batched dict.

    Per-context tensors (item_embs, labels, prefix_len, u_long) are repeated 6×
    to align with the 6 candidates per context, yielding shape (B*6, ...).
    This lets the model process all B*6 (context, candidate) pairs in one forward pass.
    """
    B = len(batch)

    item_embs  = torch.stack([b["item_embs"]  for b in batch])   # (B, 20, 128)
    labels     = torch.stack([b["labels"]     for b in batch])   # (B, 20)
    prefix_len = torch.stack([b["prefix_len"] for b in batch])   # (B,)
    u_long     = torch.stack([b["u_long"]     for b in batch])   # (B, 128)
    cand_embs  = torch.stack([b["cand_embs"]  for b in batch])   # (B, 6, 128)
    y          = torch.stack([b["y"]          for b in batch])   # (B, 6)
    weight     = torch.stack([b["weight"]     for b in batch])   # (B, 6)
    is_pos     = torch.stack([b["is_positive"]for b in batch])   # (B, 6)

    # Expand context tensors: each context repeats 6× for its candidates
    item_embs_exp  = item_embs.unsqueeze(1).expand(-1, 6, -1, -1).reshape(B * 6, L_PREFIX, 128)
    labels_exp     = labels.unsqueeze(1).expand(-1, 6, -1).reshape(B * 6, L_PREFIX)
    prefix_len_exp = prefix_len.unsqueeze(1).expand(-1, 6).reshape(B * 6)
    u_long_exp     = u_long.unsqueeze(1).expand(-1, 6, -1).reshape(B * 6, 128)

    return {
        "item_embs":   item_embs_exp,             # (B*6, 20, 128)
        "labels":      labels_exp,                # (B*6, 20)
        "prefix_len":  prefix_len_exp,            # (B*6,)
        "u_long":      u_long_exp,                # (B*6, 128)
        "cand_emb":    cand_embs.reshape(B*6, 128),  # (B*6, 128)
        "y":           y.reshape(B * 6),          # (B*6,)
        "weight":      weight.reshape(B * 6),     # (B*6,)
        "is_positive": is_pos.reshape(B * 6),     # (B*6,)
    }
