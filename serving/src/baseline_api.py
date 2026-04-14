"""
Option 1 — Baseline: FastAPI + PyTorch eager mode
Serves the full C2→C3→C4 recommendation pipeline.
No graph compilation, no batching, single worker.
"""
import os, sys, time
import torch
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import load_or_create_model
from context import ServingContext

app = FastAPI(title="GRU Ranker — Baseline (PyTorch)")

MODEL_PATH = os.getenv("MODEL_PATH", None)
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", None)
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_VERSION = os.getenv("MODEL_VERSION", "gru_ranker_untrained_v3")
MAX_SEQ_LEN = 20
NUM_CANDIDATES = 200

model = None
ctx = None

class RecommendRequest(BaseModel):
    user_id: int = 0
    session_track_ids: List[int]
    session_labels: List[int]

class TrackScore(BaseModel):
    track_id: int
    score: float

class RecommendResponse(BaseModel):
    recommendations: List[TrackScore]
    model_version: str
    inference_time_ms: float
    total_time_ms: float

@app.on_event("startup")
def startup():
    global model, ctx
    model = load_or_create_model(MODEL_PATH, DEVICE)
    ctx = ServingContext(ARTIFACTS_DIR)

@app.post("/predict", response_model=RecommendResponse)
def predict(req: RecommendRequest):
    t0 = time.time()
    try:
        ids = req.session_track_ids[-MAX_SEQ_LEN:]
        labels = req.session_labels[-MAX_SEQ_LEN:]
        sess_embs = ctx.get_embeddings(ids)
        sess_mean = sess_embs.mean(axis=0)
        last_emb = sess_embs[-1]
        ulong = ctx.get_user_centroid(req.user_id, sess_mean)
        cand_ids, cand_embs = ctx.get_candidates(NUM_CANDIDATES, exclude=ids)
        cross = ctx.compute_cross_features(cand_embs, last_emb, sess_mean, ulong)

        # Pad session to MAX_SEQ_LEN
        pad_len = MAX_SEQ_LEN - len(ids)
        if pad_len > 0:
            sess_embs = np.pad(sess_embs, ((pad_len, 0), (0, 0)), mode='constant')
            labels = [3] * pad_len + labels  # 3 = pad

        item_t  = torch.tensor(sess_embs[None], dtype=torch.float32, device=DEVICE)
        label_t = torch.tensor([labels], dtype=torch.long, device=DEVICE)
        u_t     = torch.tensor(ulong[None], dtype=torch.float32, device=DEVICE)
        cand_t  = torch.tensor(cand_embs[None], dtype=torch.float32, device=DEVICE)
        cross_t = torch.tensor(cross[None], dtype=torch.float32, device=DEVICE)

        t_infer = time.time()
        with torch.inference_mode():
            scores = model(item_t, label_t, u_t, cand_t, cross_t)[0]
        inference_ms = (time.time() - t_infer) * 1000

        top_idx = torch.argsort(scores, descending=True)[:5].cpu().numpy()
        recs = [TrackScore(track_id=int(cand_ids[i]), score=float(scores[i]))
                for i in top_idx]

        return RecommendResponse(
            recommendations=recs,
            model_version=MODEL_VERSION,
            inference_time_ms=round(inference_ms, 2),
            total_time_ms=round((time.time() - t0) * 1000, 2),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
