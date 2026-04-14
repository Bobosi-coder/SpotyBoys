# Serving Deliverables — Complete Guide

## Quick Reference: What Goes Where

| What to do | Where to run | Why |
|:---|:---|:---|
| Export ONNX + quantize models | ✅ **Local** (or Chameleon) | Generates model artifacts, no GPU needed |
| Build Docker images | ✅ **Chameleon** | Must match Chameleon arch (x86_64) |
| Run serving containers | ✅ **Chameleon only** | Rubric: "all experiments on Chameleon" |
| Run benchmarks & collect numbers | ✅ **Chameleon only** | Must be inside containers on compute instance |
| Record demo video | ✅ **Chameleon** | Screen-record the benchmark running |
| Fill serving_options_table.md | ✅ Local (after Chameleon) | Copy numbers from benchmark output |

---

## Folder Structure

```
serving/
├── src/                              # All serving source code
│   ├── model.py                      # GRU Ranker definition (128-d, v3 spec)
│   ├── context.py                    # Artifact loader (real or mock mode)
│   ├── baseline_api.py               # Option 1: FastAPI + PyTorch eager
│   ├── onnx_api.py                   # Option 2: FastAPI + ONNX Runtime
│   ├── export_onnx.py                # Script: PyTorch → ONNX export
│   ├── quantize_onnx.py              # Script: ONNX → INT8 quantization
│   ├── triton_client.py              # Option 3: Triton client proxy
│   └── ray_serve_api.py              # Option 4: Ray Serve (BONUS)
├── dockerfiles/                      # One Dockerfile per option
│   ├── Dockerfile.baseline
│   ├── Dockerfile.onnx
│   ├── Dockerfile.triton
│   └── Dockerfile.ray
├── model_repository/                 # Triton Inference Server config
│   └── gru_ranker/
│       ├── config.pbtxt              # Dynamic batching config
│       └── 1/                        # ONNX model goes here
├── scripts/                          # Benchmark + helper scripts
│   ├── benchmark_serving.py          # Load testing (p50/p95/throughput)
│   ├── export_and_optimize.sh        # One-shot: export + quantize
│   └── run_all_benchmarks.sh         # Run ALL benchmarks, save results
├── requirements.txt                  # Python dependencies
└── serving_options_table.md          # Final grading table (fill after benchmarks)
```

---

## What Each File Does

### Source Code (`src/`)

| File | Role | Course concept |
|:---|:---|:---|
| `model.py` | Defines SessionEncoder (2-layer GRU, 128-d) + ScoringHead (387→256→64→1) + combined GRURanker. Matches v3 PDF pages 19-21 exactly. | §7.3.1 Model choice |
| `context.py` | Loads Item2Vec embeddings, user centroids, and provides embedding lookups + cross-feature computation. In mock mode, generates random embeddings so you can benchmark without trained artifacts. | §7.4.2 Feature retrieval |
| `baseline_api.py` | **Option 1 — Baseline.** FastAPI + PyTorch eager mode. Unoptimized reference. Loads model at startup, runs full C2→C3→C4 pipeline per request. | §7.4.1 Prediction service |
| `onnx_api.py` | **Option 2 — ONNX.** FastAPI + ONNX Runtime. Same API, but model runs via ONNX Runtime with graph optimization (constant folding, operator fusion, dropout elimination). | §7.3.2 Graph optimization |
| `export_onnx.py` | Converts PyTorch GRURanker to ONNX format with `do_constant_folding=True`. Creates `models/gru_ranker.onnx`. | §7.3.2 Compiling the graph |
| `quantize_onnx.py` | Post-training quantization: dynamic INT8 (weight-only) and static INT8 (weights + activations with calibration). Creates `*_int8.onnx` models. | §7.3.4 Quantization |
| `triton_client.py` | **Option 3 — Triton.** FastAPI proxy that preprocesses requests and forwards to Triton Inference Server for dynamic batching. Triton accumulates concurrent requests into efficient batches. | §7.4.3 Dynamic batching |
| `ray_serve_api.py` | **Option 4 — Ray Serve (BONUS).** Uses `@serve.batch` for application-level batching and `num_replicas=2` for horizontal scaling. Not in lab → qualifies for bonus credit. | §7.4.4 Scale with replicas |

### Dockerfiles

| File | Serves | Port | Base image |
|:---|:---|:---|:---|
| `Dockerfile.baseline` | baseline_api.py | 8000 | python:3.10-slim |
| `Dockerfile.onnx` | onnx_api.py | 8001 | python:3.10-slim |
| `Dockerfile.triton` | triton_client.py | 8002 | python:3.10-slim |
| `Dockerfile.ray` | ray_serve_api.py | 8000 | rayproject/ray:2.9.0 |

### Scripts

| File | Purpose | When to run |
|:---|:---|:---|
| `export_and_optimize.sh` | Exports PyTorch→ONNX, then runs dynamic + static INT8 quantization, copies model to Triton directory | **Once**, before starting ONNX/Triton containers |
| `benchmark_serving.py` | Sends concurrent requests, measures p50/p95/p99 latency, throughput, error rate | **Per endpoint**, after each container is running |
| `run_all_benchmarks.sh` | Runs benchmark against ALL endpoints and saves JSON results | **Once**, after ALL containers are running |

---

## Step-by-Step Execution Guide

### STEP 1 — Export Model Artifacts (Local or Chameleon)

```bash
cd serving

# Install dependencies
pip install -r requirements.txt

# Export ONNX + quantize (creates models/ directory)
bash scripts/export_and_optimize.sh
```

This creates:
- `models/gru_ranker.onnx` — FP32 ONNX (graph optimized)
- `models/gru_ranker_dynamic_int8.onnx` — INT8 dynamic quantization
- `models/gru_ranker_static_int8.onnx` — INT8 static quantization
- `model_repository/gru_ranker/1/model.onnx` — copy for Triton

### STEP 2 — Transfer to Chameleon

```bash
# From your local machine:
scp -r serving/ cc@<CHAMELEON_IP>:~/serving/
```

### STEP 3 — Build Docker Images (on Chameleon)

```bash
cd ~/serving

# Option 1: Baseline
docker build -t serving-baseline -f dockerfiles/Dockerfile.baseline .

# Option 2: ONNX
docker build -t serving-onnx -f dockerfiles/Dockerfile.onnx .

# Option 3: Triton client proxy
docker build -t serving-triton-client -f dockerfiles/Dockerfile.triton .

# Option 4: Ray Serve (BONUS)
docker build -t serving-ray -f dockerfiles/Dockerfile.ray .
```

### STEP 4 — Run Containers (on Chameleon)

Run each option one at a time (or in separate terminals):

```bash
# --- Option 1: Baseline (port 8000) ---
docker run --rm -p 8000:8000 serving-baseline

# --- Option 2: ONNX FP32 (port 8001) ---
docker run --rm -p 8001:8001 serving-onnx

# --- Option 2b: ONNX INT8 (port 8001, swap model) ---
docker run --rm -p 8001:8001 \
  -e ONNX_MODEL_PATH=/app/models/gru_ranker_dynamic_int8.onnx \
  -e MODEL_VERSION=gru_ranker_onnx_int8 \
  -v $(pwd)/models:/app/models \
  serving-onnx

# --- Option 3: Triton ---
# First start Triton Inference Server:
docker run --rm -p 8000:8000 -p 8001:8001 -p 8002:8002 \
  -v $(pwd)/model_repository:/models \
  nvcr.io/nvidia/tritonserver:23.10-py3 \
  tritonserver --model-repository=/models

# Then start the client proxy (different terminal):
docker run --rm -p 8002:8002 --network host serving-triton-client

# --- Option 4: Ray Serve (BONUS, port 8003) ---
docker run --rm -p 8003:8000 serving-ray
```

### STEP 5 — Run Benchmarks (on Chameleon)

```bash
# Install benchmark dependencies
pip install requests numpy

# Run ALL benchmarks at once:
bash scripts/run_all_benchmarks.sh 127.0.0.1

# Or run individually:
python scripts/benchmark_serving.py --url http://127.0.0.1:8000/predict -n 200 -c 10
python scripts/benchmark_serving.py --url http://127.0.0.1:8001/predict -n 200 -c 10
```

### STEP 6 — Capture Right-Sizing Data (on Chameleon)

While a container is running under load:
```bash
# In a separate terminal, while benchmark is running:
top -bn1 | head -20 > results/top_during_load.txt
free -m >> results/top_during_load.txt
cat /proc/cpuinfo | grep "model name" | head -1 >> results/top_during_load.txt
```

### STEP 7 — Record Demo Video (on Chameleon)

1. Start your best serving option container
2. Start a screen recording
3. Send a request: `curl -X POST http://127.0.0.1:8000/predict -H "Content-Type: application/json" -d '{"user_id": 42, "session_track_ids": [100, 200, 300], "session_labels": [0, 0, 1]}'`
4. Show the response (top-5 recommendations + latency)
5. Run a quick benchmark burst
6. Stop recording, speed up to 2-3x

### STEP 8 — Fill the Table

Copy the p50/p95/throughput numbers from the benchmark JSON files in `results/` into `serving_options_table.md`. Mark the best options with ⭐.

---

## Optimization Mapping to Course Concepts

| Optimization Level | What we did | Course section | Expected effect |
|:---|:---|:---|:---|
| **Model-level** | ONNX graph compilation (constant folding, dropout elimination, operator fusion) | §7.3.2 | Lower latency from fewer ops and reduced memory traffic |
| **Model-level** | INT8 quantization (dynamic + static) | §7.3.4 | ~2-4x smaller model, potentially faster on CPU with INT8 paths |
| **System-level** | Triton dynamic batching (preferred sizes 4/8/16, 5ms queue delay) | §7.4.3 | Higher throughput under concurrent load |
| **System-level** | Ray Serve replicas (num_replicas=2) + @serve.batch | §7.4.4 | Better concurrency handling via horizontal scaling |
| **Infrastructure** | CPU instance comparison (run on different Chameleon flavors if available) | §7.5.2 | Shows hardware selection tradeoffs |
| **Infrastructure** | Right-sizing analysis (measure CPU% and memory under load) | §7.5.4 | Proves you understand capacity planning |

---

## Checklist Before Submission

- [ ] ONNX models exported (`models/gru_ranker.onnx` + INT8 variants)
- [ ] All 4 Docker containers build and run on Chameleon
- [ ] Benchmark results collected for ALL options (JSON files in `results/`)
- [ ] `serving_options_table.md` filled with real numbers, best options marked
- [ ] Right-sizing note: CPU/memory usage documented
- [ ] Demo video recorded (best option running on Chameleon)
- [ ] Git commit SHA recorded in the table
- [ ] Everything in the `serving/` folder

---

## When Your Teammate Delivers the Trained Model

Once you receive `gru_ranker.pt`:

```bash
# Re-export with trained weights
python src/export_onnx.py --checkpoint path/to/gru_ranker.pt --output models/gru_ranker.onnx

# Re-quantize
python src/quantize_onnx.py --input models/gru_ranker.onnx --mode dynamic

# Run baseline with trained model
docker run --rm -p 8000:8000 -e MODEL_PATH=/app/models/gru_ranker.pt \
  -v $(pwd)/models:/app/models serving-baseline
```

You can then also evaluate **task quality metrics** (HR@5, NDCG@5) on the quantized vs FP32 model to check for quality degradation — this is the "accuracy-aware" evaluation from §7.3.4.
