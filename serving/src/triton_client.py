"""
Option 3 — Triton Inference Server proxy client.
System-level optimization: dynamic batching via Triton.
The actual Triton server runs NVIDIA's container with the model_repository.
This FastAPI service acts as a thin proxy that preprocesses requests.
"""
import os, sys, time
import numpy as np
import requests as http_requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from context import ServingContext

app = FastAPI(title="GRU Ranker — Triton Client Proxy")

TRITON_URL = os.getenv("TRITON_URL", "http://localhost:8000/v2/models/gru_ranker/infer")
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", None)
MODEL_VERSION = os.getenv("MODEL_VERSION", "gru_ranker_triton")
MAX_SEQ_LEN = 20
NUM_CANDIDATES = 200

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
    global ctx
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

        # Pad
        pad_len = MAX_SEQ_LEN - len(ids)
        if pad_len > 0:
            sess_embs = np.pad(sess_embs, ((pad_len, 0), (0, 0)), mode='constant')
            labels = [3] * pad_len + labels

        payload = {
            "inputs": [
                {"name": "item_embs",       "shape": [1, MAX_SEQ_LEN, 128], "datatype": "FP32",
                 "data": sess_embs.flatten().tolist()},
                {"name": "labels",          "shape": [1, MAX_SEQ_LEN],      "datatype": "INT64",
                 "data": labels},
                {"name": "u_long",          "shape": [1, 128],              "datatype": "FP32",
                 "data": ulong.flatten().tolist()},
                {"name": "candidate_embs",  "shape": [1, NUM_CANDIDATES, 128], "datatype": "FP32",
                 "data": cand_embs.flatten().tolist()},
                {"name": "cross_features",  "shape": [1, NUM_CANDIDATES, 3],   "datatype": "FP32",
                 "data": cross.flatten().tolist()},
            ]
        }

        t_infer = time.time()
        resp = http_requests.post(TRITON_URL, json=payload, timeout=10)
        inference_ms = (time.time() - t_infer) * 1000

        if resp.status_code != 200:
            raise Exception(f"Triton error {resp.status_code}: {resp.text[:300]}")

        scores = np.array(resp.json()["outputs"][0]["data"], dtype=np.float32)
        top_idx = np.argsort(scores)[::-1][:5]
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
    uvicorn.run(app, host="0.0.0.0", port=8002)
