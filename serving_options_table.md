| Option | Endpoint URL | Model version | Code version | Hardware | p50/p95 latency | Throughput | Error rate | Concurrency tested | Compute instance type | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| baseline_http | http://127.0.0.1:8000/predict | un-trained CNN14 | TBD | CPU | TBD | TBD | TBD | 10 | compute_icelake | FastAPI basic |
| onnx_cpu | http://127.0.0.1:8001/predict | un-trained ONNX | TBD | CPU | TBD | TBD | TBD | 10 | compute_icelake | ONNX Runtime CPU |
| triton_batching | http://127.0.0.1:8002/predict | un-trained ONNX | TBD | CPU | TBD | TBD | TBD | 10 | compute_icelake | Triton Dynamic Batching |
| ray_serve | http://127.0.0.1:8003/predict | un-trained CNN14 | TBD | CPU | TBD | TBD | TBD | 10 | compute_icelake | Ray Serve Bonus |
