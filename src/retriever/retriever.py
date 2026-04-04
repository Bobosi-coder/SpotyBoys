"""
MultiRecallRetriever — C2 Serving Class

Loads all offline artifacts and implements three recall branches:

  Branch 1 (up to 100): Co-occurrence
    Recency-weighted sum of C_sess + C_pl row scores for recent session tracks.
    w_j = exp(-0.3 * (t - j)), L=10 most recent tracks.

  Branch 2 (up to 80): Preference NN
    Per-user K-Means centroids; for each centroid k_i = floor(80 * n_k / sum_n)
    nearest neighbours by numpy dot product (no FAISS needed at 746K × 128d).

  Branch 3 (up to 20): Popularity fallback
    Pre-sorted pop_scores.csv; fills remaining slots for cold/new users.

Candidate merge:
  - Dedup by track_id, keep max score if a track appears in multiple branches.
  - Cap at 200 candidates.
  - Pad to >= 50 from popularity list if pool is thin.

get_ulong():
  Returns the centroid nearest to the current session mean — used by C3 GRU ranker.
"""
import json
import logging
import math
import os
import pickle

import numpy as np
import pandas as pd
import scipy.sparse as sp

log = logging.getLogger("retriever")

# Serving constants
RECENCY_DECAY  = 0.3   # w_j = exp(-RECENCY_DECAY * steps_from_end)
COOC_WINDOW    = 10    # use last N session tracks for co-occurrence lookup
N_COOC         = 100   # max candidates from Branch 1
N_PREF         = 80    # max candidates from Branch 2
N_POP          = 20    # max candidates from Branch 3
MAX_CANDIDATES = 200
MIN_CANDIDATES = 50    # pad to this if pool is thin


class MultiRecallRetriever:
    """
    Load once at startup; call retrieve() per request.

    Args:
        artifacts_dir:  root of retriever artifacts (default: artifacts/retriever)
        processed_dir:  item2vec outputs dir (default: artifacts/item2vec)
    """

    def __init__(
        self,
        artifacts_dir: str = "artifacts/retriever",
        processed_dir: str = "artifacts/item2vec",
    ):
        self._load_artifacts(artifacts_dir, processed_dir)

    # ── Load ─────────────────────────────────────────────────────────────────

    def _load_artifacts(self, artifacts_dir: str, processed_dir: str) -> None:
        log.info("Loading MultiRecallRetriever artifacts...")

        # Item2Vec embeddings
        emb_path = os.path.join(processed_dir, "item2vec_128d.npy")
        t2r_path = os.path.join(processed_dir, "item2vec_track_to_row.json")
        self.emb = np.load(emb_path).astype("float32")   # (N, 128)
        with open(t2r_path) as f:
            t2r_raw = json.load(f)
        # t2r_raw keys are str; we keep int keys for fast int lookup
        self.t2r: dict[int, int] = {int(k): v for k, v in t2r_raw.items()}
        self.r2t: list[int] = [0] * len(self.t2r)
        for tid, row in self.t2r.items():
            self.r2t[row] = tid
        log.info(f"  Embeddings: {self.emb.shape}")

        # Co-occurrence matrices
        c_sess_path = os.path.join(artifacts_dir, "cooc", "cooc_session.npz")
        c_pl_path   = os.path.join(artifacts_dir, "cooc", "cooc_playlist.npz")
        self.C_sess: sp.csr_matrix = sp.load_npz(c_sess_path)
        self.C_pl:   sp.csr_matrix = sp.load_npz(c_pl_path)
        log.info(f"  C_sess: {self.C_sess.shape}  nnz={self.C_sess.nnz:,}")
        log.info(f"  C_pl:   {self.C_pl.shape}    nnz={self.C_pl.nnz:,}")

        # User centroids
        centroids_path = os.path.join(artifacts_dir, "pref_nn", "user_centroids.pkl")
        with open(centroids_path, "rb") as f:
            self.user_centroids: dict[int, list] = pickle.load(f)
        log.info(f"  User centroids: {len(self.user_centroids):,} users")

        # Popularity
        pop_path = os.path.join(artifacts_dir, "popularity", "pop_scores.csv")
        pop_df   = pd.read_csv(pop_path, usecols=["track_id", "pop_score"])
        self.pop_top_all: list[int] = pop_df["track_id"].tolist()   # sorted desc by pop_score
        self.pop_scores:  dict[int, float] = dict(
            zip(pop_df["track_id"].tolist(), pop_df["pop_score"].tolist())
        )
        log.info(f"  Pop scores: {len(self.pop_top_all):,} tracks loaded")

        log.info("MultiRecallRetriever ready.")

    # ── Public API ────────────────────────────────────────────────────────────

    def retrieve(
        self,
        user_id: int,
        session_track_ids: list[int],
        session_labels: list[str],
        n_cooc: int = N_COOC,
        n_pref: int = N_PREF,
        n_pop:  int = N_POP,
    ) -> list[tuple[int, float]]:
        """
        Retrieve up to MAX_CANDIDATES (track_id, score) tuples.

        Args:
            user_id:           user integer ID
            session_track_ids: ordered list of track_ids in current session
            session_labels:    parallel list of labels ("positive","neutral","skip","unknown")

        Returns:
            List of (track_id, score) sorted by score descending, max MAX_CANDIDATES items.
        """
        candidates: dict[int, float] = {}

        # ── Branch 1: Co-occurrence ──────────────────────────────────────────
        cooc_cands = self._cooc_branch(session_track_ids, n_cooc)
        for tid, score in cooc_cands:
            candidates[tid] = max(candidates.get(tid, 0.0), score)

        # ── Branch 2: Preference NN ──────────────────────────────────────────
        pref_cands = self._pref_nn_branch(user_id, session_track_ids, n_pref)
        for tid, score in pref_cands:
            if tid not in candidates:
                candidates[tid] = score
            else:
                candidates[tid] = max(candidates[tid], score)

        # ── Branch 3: Popularity fallback ────────────────────────────────────
        pop_added = 0
        for tid in self.pop_top_all:
            if pop_added >= n_pop:
                break
            if tid not in candidates:
                candidates[tid] = self.pop_scores.get(tid, 0.0)
                pop_added += 1

        # ── Merge and cap ────────────────────────────────────────────────────
        result = sorted(candidates.items(), key=lambda x: -x[1])[:MAX_CANDIDATES]

        # Pad to MIN_CANDIDATES if pool is thin
        if len(result) < MIN_CANDIDATES:
            result_set = {tid for tid, _ in result}
            for tid in self.pop_top_all:
                if len(result) >= MIN_CANDIDATES:
                    break
                if tid not in result_set:
                    result.append((tid, self.pop_scores.get(tid, 0.0)))
                    result_set.add(tid)

        return result

    def get_ulong(
        self,
        user_id: int,
        session_track_ids: list[int],
    ) -> np.ndarray | None:
        """
        Return the user's centroid nearest to the current session's mean embedding.
        Used by C3 GRU ranker as the long-term preference vector u_long.

        Returns None if the user has no centroids (cold user).
        """
        centroids_data = self.user_centroids.get(user_id)
        if not centroids_data:
            return None

        # Compute session mean embedding
        rows = [self.t2r[tid] for tid in session_track_ids if tid in self.t2r]
        if not rows:
            # Fall back to first (largest) centroid
            return np.array(centroids_data[0][0], dtype="float32")

        session_mean = self.emb[rows].mean(axis=0)  # (128,)

        # Find centroid nearest to session mean
        best_centroid = None
        best_dist = float("inf")
        for centroid_vec, _ in centroids_data:
            c = np.array(centroid_vec, dtype="float32")
            dist = float(np.sum((session_mean - c) ** 2))
            if dist < best_dist:
                best_dist = dist
                best_centroid = c

        return best_centroid

    # ── Branch implementations ────────────────────────────────────────────────

    def _cooc_branch(
        self,
        session_track_ids: list[int],
        n_cooc: int,
    ) -> list[tuple[int, float]]:
        """
        Recency-weighted co-occurrence scoring.
        Uses last COOC_WINDOW tracks; w_j = exp(-RECENCY_DECAY * steps_from_end).
        score(c) = sum_j w_j * (C_sess[r_j, r_c] + C_pl[r_j, r_c])
        """
        recent = session_track_ids[-COOC_WINDOW:]
        n      = len(recent)
        if n == 0:
            return []

        score_acc: dict[int, float] = {}

        for step_from_end, tid in enumerate(reversed(recent)):
            row_idx = self.t2r.get(tid)
            if row_idx is None:
                continue
            w = math.exp(-RECENCY_DECAY * step_from_end)

            # Sparse row lookup: get (col_indices, values) for both matrices
            sess_row = self.C_sess.getrow(row_idx)
            pl_row   = self.C_pl.getrow(row_idx)

            # Sum the two rows
            combined = sess_row + pl_row
            cx = combined.tocoo()
            for col, val in zip(cx.col, cx.data):
                cand_tid = self.r2t[col]
                score_acc[cand_tid] = score_acc.get(cand_tid, 0.0) + w * float(val)

        if not score_acc:
            return []

        top = sorted(score_acc.items(), key=lambda x: -x[1])[:n_cooc]
        return top

    def _pref_nn_branch(
        self,
        user_id: int,
        session_track_ids: list[int],
        n_pref: int,
    ) -> list[tuple[int, float]]:
        """
        Proportional centroid nearest-neighbour retrieval.
        For each centroid c_k with weight n_k / sum_n:
            k_i = floor(n_pref * n_k / sum_n)  candidates to retrieve
            scores = emb @ c_k  (dot product similarity)
        """
        centroids_data = self.user_centroids.get(user_id)
        if not centroids_data:
            return []

        total_size = sum(sz for _, sz in centroids_data)
        if total_size == 0:
            return []

        result: dict[int, float] = {}

        for centroid_vec, cluster_size in centroids_data:
            k_i = math.floor(n_pref * cluster_size / total_size)
            if k_i < 1:
                k_i = 1

            c = np.array(centroid_vec, dtype="float32")
            scores = self.emb @ c   # (N,) dot products

            # Top k_i by score (use argpartition for efficiency)
            if k_i >= len(scores):
                top_rows = np.argsort(scores)[::-1]
            else:
                top_rows = np.argpartition(scores, -k_i)[-k_i:]
                top_rows = top_rows[np.argsort(scores[top_rows])[::-1]]

            for row in top_rows:
                tid = self.r2t[int(row)]
                s   = float(scores[row])
                if tid not in result or s > result[tid]:
                    result[tid] = s

        top = sorted(result.items(), key=lambda x: -x[1])[:n_pref]
        return top
