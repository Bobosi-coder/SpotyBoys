import requests
import base64
import io
import time
import numpy as np
from scipy.io import wavfile
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="PANN-Cnn14 Triton API Proxy")
# Expect Triton to be running on localhost:8000 when deployed on same instance
TRITON_URL = "http://localhost:8000/v2/models/pann/infer"

class AudioRequest(BaseModel):
    audio_b64: str

@app.post("/predict")
def predict(req: AudioRequest):
    try:
        audio_bytes = base64.b64decode(req.audio_b64)
        sample_rate, data = wavfile.read(io.BytesIO(audio_bytes))
        
        waveform = data.astype(np.float32) / 32768.0 if data.dtype == np.int16 else data.astype(np.float32)
        input_tensor = waveform[None, :] 
        
        # Prepare Triton Payload
        payload = {
            "inputs": [
                {
                    "name": "input",
                    "shape": list(input_tensor.shape),
                    "datatype": "FP32",
                    "data": input_tensor.flatten().tolist()
                }
            ]
        }
        
        start_time = time.time()
        resp = requests.post(TRITON_URL, json=payload)
        inference_time = time.time() - start_time
        
        if resp.status_code != 200:
            raise Exception(f"Triton Error: {resp.text}")
            
        result = resp.json()
        
        return {
            "status": "success",
            "inference_time_s": inference_time,
            "embedding_shape": result["outputs"][0]["shape"]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
