"""
Serving Context — loads artifacts (real or mock) and provides
embedding lookups, candidate generation, and cross-feature computation.
"""
import numpy as np
import json, os, pickle

class ServingContext:
    def __init__(self, artifacts_dir=None, catalog_size=10000, d_emb=128):
        self.d_emb = d_emb
        if artifacts_dir and os.path.exists(os.path.join(artifacts_dir, "item2vec_128d.npy")):
            self._load_real(artifacts_dir)
        else:
            self._init_mock(catalog_size)

    # ---------- real artifacts ----------
    def _load_real(self, d):
        print(f"[context] Loading real artifacts from {d}")
        self.embeddings = np.load(os.path.join(d, "item2vec_128d.npy"))
        with open(os.path.join(d, "item2vec_track_to_row.json")) as f:
            self.track_to_row = {int(k): v for k, v in json.load(f).items()}
        self.track_ids = sorted(self.track_to_row.keys())
        with open(os.path.join(d, "user_centroids.pkl"), "rb") as f:
            raw = pickle.load(f)
            self.user_centroids = {}
            for uid, clist in raw.items():
                self.user_centroids[int(uid)] = [np.array(c[0], dtype=np.float32) for c in clist]
        print(f"[context] Catalog size: {len(self.track_ids)}, Users with centroids: {len(self.user_centroids)}")

    # ---------- mock artifacts ----------
    def _init_mock(self, n):
        print(f"[context] Generating mock artifacts (catalog={n})")
        emb = np.random.randn(n, self.d_emb).astype(np.float32)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        self.embeddings = emb / np.clip(norms, 1e-8, None)
        self.track_ids = list(range(n))
        self.track_to_row = {tid: i for i, tid in enumerate(self.track_ids)}
        self.user_centroids = {}
        for uid in range(200):
            k = np.random.choice([1, 2, 3])
            idxs = np.random.choice(n, k, replace=False)
            self.user_centroids[uid] = [self.embeddings[i].copy() for i in idxs]

    # ---------- public API ----------
    def get_embeddings(self, track_ids):
        out = []
        for tid in track_ids:
            if tid in self.track_to_row:
                out.append(self.embeddings[self.track_to_row[tid]])
            else:
                out.append(np.zeros(self.d_emb, dtype=np.float32))
        return np.array(out, dtype=np.float32)

    def get_user_centroid(self, user_id, session_mean):
        if user_id in self.user_centroids:
            centroids = self.user_centroids[user_id]
            best = min(centroids, key=lambda c: float(np.linalg.norm(c - session_mean)))
            return best.astype(np.float32)
        return session_mean.astype(np.float32)

    def get_candidates(self, n=200, exclude=None):
        exclude = set(exclude or [])
        pool = [t for t in self.track_ids if t not in exclude]
        if len(pool) > n:
            idxs = np.random.choice(len(pool), n, replace=False)
            cand_ids = [pool[i] for i in idxs]
        else:
            cand_ids = pool[:n]
        return cand_ids, self.get_embeddings(cand_ids)

    def compute_cross_features(self, cand_embs, last_emb, sess_mean, ulong):
        def _cos(a, b):
            dot = a @ b
            return dot / (np.linalg.norm(a, axis=1) * np.linalg.norm(b) + 1e-8)
        f1 = _cos(cand_embs, last_emb)
        f2 = _cos(cand_embs, sess_mean)
        f3 = _cos(cand_embs, ulong)
        return np.stack([f1, f2, f3], axis=1).astype(np.float32)
