import base64
import io
import time
import torch
import numpy as np
from scipy.io import wavfile
from typing import Dict, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from ray import serve

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_stage1_catalog import Cnn14Standalone

app = FastAPI(title="PANN-Cnn14 Ray Serve API")

class AudioRequest(BaseModel):
    audio_b64: str

@serve.deployment(num_replicas=2, ray_actor_options={"num_cpus": 1})
@serve.ingress(app)
class PannModelDeployment:
    def __init__(self):
        self.SR = 32000
        self.DEVICE = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
        print(f"Loading model on {self.DEVICE}...")
        self.model = Cnn14Standalone(self.SR, 1024, 320, 64, 50, 14000, 527)
        self.model.to(self.DEVICE).eval()
        print("Model loaded.")

    @serve.batch(max_batch_size=8, batch_wait_timeout_s=0.1)
    async def process_batch(self, inputs: List[np.ndarray]) -> List[List[float]]:
        # Stack into batch tensor
        batch_tensor = torch.from_numpy(np.vstack(inputs)).to(self.DEVICE)
        with torch.no_grad():
            embeddings = self.model(batch_tensor)
        
        return embeddings.cpu().numpy().tolist()

    @app.post("/predict")
    async def predict(self, req: AudioRequest):
        try:
            audio_bytes = base64.b64decode(req.audio_b64)
            sample_rate, data = wavfile.read(io.BytesIO(audio_bytes))
            
            waveform = data.astype(np.float32) / 32768.0 if data.dtype == np.int16 else data.astype(np.float32)
            input_tensor = waveform[None, :] 
            
            start_time = time.time()
            embedding = await self.process_batch(input_tensor)
            inference_time = time.time() - start_time
            
            return {
                "status": "success",
                "inference_time_s": inference_time,
                "embedding_shape": [1, len(embedding)]
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

pann_app = PannModelDeployment.bind()
