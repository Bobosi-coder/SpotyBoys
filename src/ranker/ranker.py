"""
GRURankerInference — C3 Serving Class

Loads gru_ranker.pt + config, item2vec embeddings, and user centroids at startup.
Scores all C2 candidates for the current (user, session) in one batched forward pass.

Usage:
    ranker = GRURankerInference()
    ranked = ranker.score(user_id, session_track_ids, session_labels, candidates)
    # ranked: list of (track_id, score) sorted descending
"""
import json
import logging
import math
import os
import pickle

import numpy as np
import torch

from src.ranker.model import GRURanker

OUT_DIR      = "artifacts/ranker"
I2V_DIR      = "artifacts/item2vec"
RETRIEVER_DIR = "artifacts/retriever"

L_PREFIX  = 20
LABEL_ENC = {"positive": 0, "neutral": 1, "skip": 2}   # unknown → 3

log = logging.getLogger("ranker.inference")


class GRURankerInference:
    """
    Load once at startup; call score() per request.

    Args:
        artifacts_dir: root of ranker artifacts (default: artifacts/ranker)
        i2v_dir:       item2vec outputs dir (default: artifacts/item2vec)
        retriever_dir: retriever artifacts dir (default: artifacts/retriever)
    """

    def __init__(
        self,
        artifacts_dir: str = OUT_DIR,
        i2v_dir:       str = I2V_DIR,
        retriever_dir: str = RETRIEVER_DIR,
    ):
        self._load_artifacts(artifacts_dir, i2v_dir, retriever_dir)

    def _load_artifacts(
        self,
        artifacts_dir: str,
        i2v_dir: str,
        retriever_dir: str,
    ) -> None:
        log.info("Loading GRURankerInference artifacts...")

        # Model config + weights
        cfg_path  = os.path.join(artifacts_dir, "gru_ranker_config.json")
        ckpt_path = os.path.join(artifacts_dir, "gru_ranker.pt")
        with open(cfg_path) as f:
            cfg = json.load(f)
        self.model = GRURanker(
            d_emb=cfg["d_emb"],
            n_layers=cfg["n_layers"],
            dropout=cfg.get("dropout", 0.1),
        )
        self.model.load_state_dict(
            torch.load(ckpt_path, map_location="cpu", weights_only=True)
        )
        self.model.eval()
        log.info(f"  Model loaded from {ckpt_path}")

        # Device (inference CPU is fine; override with .to(device) if GPU available)
        self.device = torch.device("cpu")

        # Item2Vec embeddings
        emb_path = os.path.join(i2v_dir, "item2vec_128d.npy")
        t2r_path = os.path.join(i2v_dir, "item2vec_track_to_row.json")
        self.emb = np.load(emb_path).astype("float32")   # (N, 128)
        with open(t2r_path) as f:
            t2r_raw = json.load(f)
        self.t2r: dict[int, int] = {int(k): v for k, v in t2r_raw.items()}
        log.info(f"  Embeddings: {self.emb.shape}")

        # User centroids
        centroids_path = os.path.join(retriever_dir, "pref_nn", "user_centroids.pkl")
        with open(centroids_path, "rb") as f:
            self.user_centroids: dict[int, list] = pickle.load(f)
        log.info(f"  User centroids: {len(self.user_centroids):,} users")

        log.info("GRURankerInference ready.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def score(
        self,
        user_id:           int,
        session_track_ids: list[int],
        session_labels:    list[str],
        candidates:        list[int],
    ) -> list[tuple[int, float]]:
        """
        Score all candidates for the current session context.

        Args:
            user_id:           integer user id
            session_track_ids: ordered track_ids in the current session (prefix)
            session_labels:    parallel labels ("positive","neutral","skip","unknown")
            candidates:        list of candidate track_ids from C2 (up to 200)

        Returns:
            List of (track_id, score) sorted descending.
        """
        if not candidates:
            return []

        B = len(candidates)

        # ── Build prefix tensors (right-padded) ─────────────────────────────
        plen = min(len(session_track_ids), L_PREFIX)
        pid_right  = np.zeros(L_PREFIX, dtype=np.int32)
        plab_right = np.full(L_PREFIX, 3, dtype=np.int8)
        if plen > 0:
            pid_right[:plen]  = [int(t) for t in session_track_ids[-plen:]]
            plab_right[:plen] = [LABEL_ENC.get(l, 3)
                                  for l in session_labels[-plen:]]

        # Prefix embeddings
        item_embs_np = np.zeros((L_PREFIX, 128), dtype=np.float32)
        for pos in range(plen):
            row = self.t2r.get(int(pid_right[pos]))
            if row is not None:
                item_embs_np[pos] = self.emb[row]

        # ── u_long ─────────────────────────────────────────────────────────
        u_long_np = self._get_ulong(user_id, pid_right, plen)

        # ── Batch tensors: repeat context B× for B candidates ───────────────
        item_embs_t = (torch.from_numpy(item_embs_np)
                       .unsqueeze(0).expand(B, -1, -1))          # (B, 20, 128)
        labels_t    = (torch.from_numpy(plab_right.astype(np.int64))
                       .unsqueeze(0).expand(B, -1))              # (B, 20)
        prefix_len_t = torch.full((B,), plen, dtype=torch.int64)  # (B,)
        u_long_t    = (torch.from_numpy(u_long_np)
                       .unsqueeze(0).expand(B, -1))              # (B, 128)

        # Candidate embeddings
        cand_embs_np = np.zeros((B, 128), dtype=np.float32)
        for i, cid in enumerate(candidates):
            row = self.t2r.get(int(cid))
            if row is not None:
                cand_embs_np[i] = self.emb[row]
        cand_emb_t = torch.from_numpy(cand_embs_np)             # (B, 128)

        # ── Forward pass ────────────────────────────────────────────────────
        with torch.no_grad():
            logits = self.model(
                item_embs_t.to(self.device),
                labels_t.to(self.device),
                prefix_len_t.to(self.device),
                u_long_t.to(self.device),
                cand_emb_t.to(self.device),
            )   # (B,)

        scores = logits.cpu().numpy().tolist()
        result = sorted(zip(candidates, scores), key=lambda x: -x[1])
        return result

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_ulong(
        self,
        user_id: int,
        pid_right: np.ndarray,
        plen: int,
    ) -> np.ndarray:
        """Nearest centroid to session mean; zeros for cold users."""
        centroids = self.user_centroids.get(int(user_id))
        if not centroids:
            return np.zeros(128, dtype=np.float32)

        rows = [self.t2r[int(tid)] for tid in pid_right[:plen] if int(tid) in self.t2r]
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
