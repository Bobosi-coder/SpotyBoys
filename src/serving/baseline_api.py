import base64
import io
import time
import torch
import uvicorn
import numpy as np
from scipy.io import wavfile
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import sys
import os
# Ensure we can import from src directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_stage1_catalog import Cnn14Standalone

app = FastAPI(title="PANN-Cnn14 Baseline API")

SR = 32000
DEVICE = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')

print(f"Loading un-trained PANN-Cnn14 model on {DEVICE}...")
model = Cnn14Standalone(SR, 1024, 320, 64, 50, 14000, 527)
model.to(DEVICE).eval()
print("Model loaded.")

class AudioRequest(BaseModel):
    audio_b64: str

@app.post("/predict")
def predict(req: AudioRequest):
    try:
        audio_bytes = base64.b64decode(req.audio_b64)
        sample_rate, data = wavfile.read(io.BytesIO(audio_bytes))
        
        # Resample logic omitted for baseline speed context, assume 32kHz
        waveform = data.astype(np.float32) / 32768.0 if data.dtype == np.int16 else data.astype(np.float32)
        
        with torch.no_grad():
            input_tensor = torch.from_numpy(waveform[None, :]).to(DEVICE)
            
            start_time = time.time()
            embedding = model(input_tensor)
            inference_time = time.time() - start_time
            
        return {
            "status": "success",
            "inference_time_s": inference_time,
            "embedding_shape": list(embedding.shape)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
