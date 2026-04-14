"""
Option 2 — ONNX Runtime: FastAPI + ONNX Runtime CPUExecutionProvider
Model-level optimization: compiled graph, constant folding, operator fusion.
Optionally uses INT8 quantized model.
"""
import os, sys, time
import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from context import ServingContext

app = FastAPI(title="GRU Ranker — ONNX Runtime")

ONNX_PATH = os.getenv("ONNX_MODEL_PATH", "models/gru_ranker.onnx")
ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", None)
MODEL_VERSION = os.getenv("MODEL_VERSION", "gru_ranker_onnx_fp32")
MAX_SEQ_LEN = 20
NUM_CANDIDATES = 200

sess = None
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
    global sess, ctx
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = 4
    print(f"[onnx] Loading ONNX model from {ONNX_PATH}")
    sess = ort.InferenceSession(ONNX_PATH, so, providers=["CPUExecutionProvider"])
    ctx = ServingContext(ARTIFACTS_DIR)
    print("[onnx] Ready")

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

        feeds = {
            "item_embs":       sess_embs[None].astype(np.float32),
            "labels":          np.array([labels], dtype=np.int64),
            "u_long":          ulong[None].astype(np.float32),
            "candidate_embs":  cand_embs[None].astype(np.float32),
            "cross_features":  cross[None].astype(np.float32),
        }

        t_infer = time.time()
        scores = sess.run(["scores"], feeds)[0][0]
        inference_ms = (time.time() - t_infer) * 1000

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
    uvicorn.run(app, host="0.0.0.0", port=8001)
