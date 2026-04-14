import base64
import io
import time
import uvicorn
import numpy as np
import onnxruntime as ort
from scipy.io import wavfile
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="PANN-Cnn14 ONNX API")

print("Loading ONNX model from models/Cnn14.onnx...")
sess = ort.InferenceSession("models/Cnn14.onnx", providers=['CPUExecutionProvider'])
print("Model loaded successfully.")

class AudioRequest(BaseModel):
    audio_b64: str

@app.post("/predict")
def predict(req: AudioRequest):
    try:
        audio_bytes = base64.b64decode(req.audio_b64)
        sample_rate, data = wavfile.read(io.BytesIO(audio_bytes))
        
        waveform = data.astype(np.float32) / 32768.0 if data.dtype == np.int16 else data.astype(np.float32)
        input_tensor = waveform[None, :]
        
        start_time = time.time()
        embedding = sess.run(['embedding'], {'input': input_tensor})[0]
        inference_time = time.time() - start_time
            
        return {
            "status": "success",
            "inference_time_s": inference_time,
            "embedding_shape": list(embedding.shape)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
