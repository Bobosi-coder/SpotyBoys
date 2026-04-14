#!/bin/bash
# Export and optimize the GRU Ranker model for all serving options.
# Run this ONCE before starting any serving container.
# Can run locally or on Chameleon.
set -e

echo "=== Step 1: Export PyTorch -> ONNX (FP32) ==="
cd "$(dirname "$0")/.."
python src/export_onnx.py --output models/gru_ranker.onnx

echo ""
echo "=== Step 2: Dynamic INT8 Quantization ==="
python src/quantize_onnx.py --input models/gru_ranker.onnx --mode dynamic

echo ""
echo "=== Step 3: Static INT8 Quantization ==="
python src/quantize_onnx.py --input models/gru_ranker.onnx --mode static

echo ""
echo "=== Step 4: Copy ONNX to Triton model_repository ==="
cp models/gru_ranker.onnx model_repository/gru_ranker/1/model.onnx
echo "Copied to model_repository/gru_ranker/1/model.onnx"

echo ""
echo "=== All models ready ==="
ls -lh models/
echo ""
echo "Available models:"
echo "  models/gru_ranker.onnx              (FP32 - baseline ONNX)"
echo "  models/gru_ranker_dynamic_int8.onnx (INT8 dynamic quantization)"
echo "  models/gru_ranker_static_int8.onnx  (INT8 static quantization)"
