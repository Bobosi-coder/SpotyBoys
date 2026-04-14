#!/bin/bash
# Run ALL benchmarks on Chameleon and collect results.
# Execute this AFTER all serving containers are running.
#
# Usage: bash scripts/run_all_benchmarks.sh <CHAMELEON_IP>

set -e
IP=${1:-"127.0.0.1"}
OUT_DIR="results"
mkdir -p $OUT_DIR

echo "=== Benchmarking all serving options on $IP ==="

# --- Option 1: Baseline (port 8000) ---
echo "[1/6] Baseline PyTorch — concurrency 10"
python scripts/benchmark_serving.py --url http://$IP:8000/predict -n 200 -c 10 \
  --save $OUT_DIR/baseline_c10.json

echo "[1b/6] Baseline PyTorch — concurrency 1 (single-user latency)"
python scripts/benchmark_serving.py --url http://$IP:8000/predict -n 50 -c 1 \
  --save $OUT_DIR/baseline_c1.json

# --- Option 2: ONNX FP32 (port 8001) ---
echo "[2/6] ONNX FP32 — concurrency 10"
python scripts/benchmark_serving.py --url http://$IP:8001/predict -n 200 -c 10 \
  --save $OUT_DIR/onnx_fp32_c10.json

# --- Option 3: ONNX INT8 (port 8001, swap model) ---
echo "[3/6] ONNX INT8 — concurrency 10"
python scripts/benchmark_serving.py --url http://$IP:8001/predict -n 200 -c 10 \
  --save $OUT_DIR/onnx_int8_c10.json

# --- Option 4: Triton (port 8002) ---
echo "[4/6] Triton dynamic batching — concurrency 10"
python scripts/benchmark_serving.py --url http://$IP:8002/predict -n 200 -c 10 \
  --save $OUT_DIR/triton_c10.json

echo "[4b/6] Triton — concurrency 50 (stress test)"
python scripts/benchmark_serving.py --url http://$IP:8002/predict -n 500 -c 50 \
  --save $OUT_DIR/triton_c50.json

# --- Option 5: Ray Serve (port 8003) ---
echo "[5/6] Ray Serve — concurrency 10"
python scripts/benchmark_serving.py --url http://$IP:8003/predict -n 200 -c 10 \
  --save $OUT_DIR/ray_c10.json

echo "[5b/6] Ray Serve — concurrency 50"
python scripts/benchmark_serving.py --url http://$IP:8003/predict -n 500 -c 50 \
  --save $OUT_DIR/ray_c50.json

# --- Resource usage snapshot ---
echo "[6/6] Capturing resource usage..."
echo "CPU info:" > $OUT_DIR/system_info.txt
nproc >> $OUT_DIR/system_info.txt 2>/dev/null || true
cat /proc/cpuinfo | head -20 >> $OUT_DIR/system_info.txt 2>/dev/null || true
echo "Memory:" >> $OUT_DIR/system_info.txt
free -m >> $OUT_DIR/system_info.txt 2>/dev/null || true

echo ""
echo "=== All benchmarks complete ==="
echo "Results saved in: $OUT_DIR/"
ls -la $OUT_DIR/
