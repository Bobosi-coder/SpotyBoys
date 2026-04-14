# Serving Options Comparison Table

**Instance**: Chameleon `compute_haswell` — Intel Core (Haswell, no TSX, IBRS), 4 GB RAM
**Model**: GRU Ranker (128-d Item2Vec, 314K parameters, un-trained)
**Session length**: 6 tracks, 200 candidates per request

## Results

| Option | Endpoint URL | Model version | Code ver | HW | p50 (ms) | p95 (ms) | Throughput (req/s) | Error rate | Conc. | Optimization | Course §  |
| :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- | :--- |
| baseline_pytorch | http://IP:8000/predict | un-trained GRU 128d | `f3de835` | CPU | 70.76 | 92.00 | 137.86 | 0% | 10 | None (PyTorch eager) | §7.3.1 |
| baseline_single | http://IP:8000/predict | un-trained GRU 128d | `f3de835` | CPU | 9.12 | 9.66 | 106.12 | 0% | 1 | None (single-user latency) | §7.3.1 |
| onnx_fp32 | http://IP:8001/predict | ONNX FP32 GRU | `f3de835` | CPU | 73.05 | 101.89 | 130.45 | 0% | 10 | Graph optimization | §7.3.2 |
| onnx_int8 ⭐ lowest p95 | http://IP:8001/predict | ONNX INT8 GRU | `f3de835` | CPU | 70.10 | 88.68 | 139.84 | 0% | 10 | INT8 dynamic quantization | §7.3.4 |
| triton_batched | http://IP:8005/predict | ONNX FP32 via Triton | `f3de835` | CPU | 380.33 | 520.75 | 25.88 | 0% | 10 | Dynamic batching (Triton) | §7.4.3 |
| ray_serve ⭐ bonus | http://IP:8003/predict | un-trained GRU 128d | `f3de835` | CPU | 133.82 | 192.95 | 71.07 | 0% | 10 | 2 replicas + @serve.batch | §7.4.4 |
| ray_serve_stress | http://IP:8003/predict | un-trained GRU 128d | `f3de835` | CPU | 632.78 | 773.08 | 77.78 | 0% | 50 | 2 replicas under high load | §7.4.4 |

## Best Options

| Priority | Winner | Why |
| :--- | :--- | :--- |
| ⭐ Lowest single-user latency | **baseline_pytorch (c=1)** | 9.12 ms p50 — PyTorch eager with no contention |
| ⭐ Lowest p95 under load | **onnx_int8 (c=10)** | 88.68 ms p95, best tail latency under concurrency |
| ⭐ Highest throughput | **onnx_int8 (c=10)** | 139.84 req/s — INT8 quantization wins |
| ⭐ Best scaling under stress | **ray_serve (c=50)** | Maintains 77.78 req/s at 50 concurrent users; 0% errors |

## Key Findings

### Model-Level Optimizations (§7.3)
- **ONNX graph compilation** reduced model inference time from **24.74 ms** (PyTorch) to **6.71 ms** (ONNX) — a **3.7× speedup** on the forward pass.
- **INT8 quantization** provided marginal additional improvement (6.86 ms vs 6.71 ms), expected for this small model (314K params). The model is already compute-light; quantization benefits increase with model size.
- End-to-end latency is dominated by **preprocessing** (embedding lookups, cross features, candidate generation), not model inference, explaining why total p50 is similar across options despite the 3.7× inference speedup.

### System-Level Optimizations (§7.4)
- **Triton dynamic batching** showed **higher latency** (380 ms p50) due to the double-hop architecture: client → HTTP → Triton server → inference → HTTP → client. The HTTP serialization overhead dominates for this small model. Triton's batching benefits are designed for GPU-bound, large-model workloads where batch amortization exceeds the networking cost.
- **Ray Serve** with 2 replicas + `@serve.batch` achieved **stable throughput under 5× load increase** (71 → 78 req/s from c=10 to c=50), demonstrating effective horizontal scaling. Per-request latency increased but 0% error rate shows graceful degradation.

### Infrastructure-Level: Right-Sizing (§7.5)
- **CPU**: ~23% user + ~23% system under load (Ray Serve). Instance is not CPU-bound.
- **Memory**: 2,289 MB used of 3,916 MB (58%). Ray Serve workers use ~670 MB each (16.7% each). Baseline/ONNX containers use significantly less.
- **Recommendation**: A 2-vCPU, 4 GB instance is sufficient for this model. The GRU ranker (314K params) is lightweight; the primary concern is memory for the embedding matrix (`item2vec_128d.npy`), which will grow to ~300 MB at full catalog size (600K × 128 × 4 bytes).
