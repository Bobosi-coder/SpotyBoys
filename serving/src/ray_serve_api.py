"""
Option 4 (BONUS) — Ray Serve: autoscaling replicas + @serve.batch
System-level optimization: replica scaling + application-level batching.
"""
import os, sys, time
import torch
import numpy as np
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ray import serve

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import load_or_create_model
from context import ServingContext

app = FastAPI(title="GRU Ranker — Ray Serve")

MAX_SEQ_LEN = 20
NUM_CANDIDATES = 200

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

@serve.deployment(
    num_replicas=2,
    ray_actor_options={"num_cpus": 1},
)
@serve.ingress(app)
class GRURankerDeployment:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        ckpt = os.getenv("MODEL_PATH", None)
        self.model = load_or_create_model(ckpt, self.device)
        self.ctx = ServingContext(os.getenv("ARTIFACTS_DIR", None))
        self.version = os.getenv("MODEL_VERSION", "gru_ranker_ray_serve")

    @serve.batch(max_batch_size=8, batch_wait_timeout_s=0.05)
    async def score_batch(self, batch_inputs: List[dict]) -> List[np.ndarray]:
        """Batch-score multiple requests on the GRU model."""
        B = len(batch_inputs)
        item_stack  = np.stack([b["item_embs"] for b in batch_inputs])
        label_stack = np.stack([b["labels"]    for b in batch_inputs])
        u_stack     = np.stack([b["u_long"]    for b in batch_inputs])
        cand_stack  = np.stack([b["cand_embs"] for b in batch_inputs])
        cross_stack = np.stack([b["cross"]     for b in batch_inputs])

        with torch.inference_mode():
            scores = self.model(
                torch.tensor(item_stack,  dtype=torch.float32, device=self.device),
                torch.tensor(label_stack, dtype=torch.long,    device=self.device),
                torch.tensor(u_stack,     dtype=torch.float32, device=self.device),
                torch.tensor(cand_stack,  dtype=torch.float32, device=self.device),
                torch.tensor(cross_stack, dtype=torch.float32, device=self.device),
            ).cpu().numpy()

        return [scores[i] for i in range(B)]

    @app.post("/predict", response_model=RecommendResponse)
    async def predict(self, req: RecommendRequest):
        t0 = time.time()
        try:
            ids = req.session_track_ids[-MAX_SEQ_LEN:]
            labels = req.session_labels[-MAX_SEQ_LEN:]
            sess_embs = self.ctx.get_embeddings(ids)
            sess_mean = sess_embs.mean(axis=0)
            last_emb = sess_embs[-1]
            ulong = self.ctx.get_user_centroid(req.user_id, sess_mean)
            cand_ids, cand_embs = self.ctx.get_candidates(NUM_CANDIDATES, exclude=ids)
            cross = self.ctx.compute_cross_features(cand_embs, last_emb, sess_mean, ulong)

            pad_len = MAX_SEQ_LEN - len(ids)
            if pad_len > 0:
                sess_embs = np.pad(sess_embs, ((pad_len, 0), (0, 0)), mode='constant')
                labels = [3] * pad_len + labels

            inp = {
                "item_embs": sess_embs.astype(np.float32),
                "labels":    np.array(labels, dtype=np.int64),
                "u_long":    ulong.astype(np.float32),
                "cand_embs": cand_embs.astype(np.float32),
                "cross":     cross.astype(np.float32),
            }

            t_infer = time.time()
            scores = await self.score_batch(inp)
            inference_ms = (time.time() - t_infer) * 1000

            top_idx = np.argsort(scores)[::-1][:5]
            recs = [TrackScore(track_id=int(cand_ids[i]), score=float(scores[i]))
                    for i in top_idx]

            return RecommendResponse(
                recommendations=recs,
                model_version=self.version,
                inference_time_ms=round(inference_ms, 2),
                total_time_ms=round((time.time() - t0) * 1000, 2),
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

gru_ranker_app = GRURankerDeployment.bind()
