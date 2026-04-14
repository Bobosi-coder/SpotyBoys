"""
Post-training quantization of the ONNX GRU Ranker.
Model-level optimization: INT8 dynamic and static quantization.

Usage:
  python quantize_onnx.py [--input PATH] [--output PATH] [--mode dynamic|static]
"""
import argparse, os, sys
import numpy as np

def quantize_dynamic(input_path, output_path):
    from onnxruntime.quantization import quantize_dynamic, QuantType
    print(f"Dynamic INT8 quantization: {input_path} -> {output_path}")
    quantize_dynamic(
        input_path, output_path,
        weight_type=QuantType.QInt8,
    )
    orig = os.path.getsize(input_path) / 1024
    quant = os.path.getsize(output_path) / 1024
    print(f"  FP32 size: {orig:.1f} KB")
    print(f"  INT8 size: {quant:.1f} KB")
    print(f"  Reduction: {(1 - quant / orig) * 100:.1f}%")

def quantize_static(input_path, output_path, n_calibration=100):
    from onnxruntime.quantization import quantize_static, QuantType, CalibrationDataReader

    class GRUCalibrationReader(CalibrationDataReader):
        def __init__(self, n):
            self.data = []
            for _ in range(n):
                self.data.append({
                    "item_embs":      np.random.randn(1, 20, 128).astype(np.float32),
                    "labels":         np.zeros((1, 20), dtype=np.int64),
                    "u_long":         np.random.randn(1, 128).astype(np.float32),
                    "candidate_embs": np.random.randn(1, 200, 128).astype(np.float32),
                    "cross_features": np.random.randn(1, 200, 3).astype(np.float32),
                })
            self.idx = 0

        def get_next(self):
            if self.idx >= len(self.data):
                return None
            d = self.data[self.idx]
            self.idx += 1
            return d

    print(f"Static INT8 quantization ({n_calibration} calibration samples)")
    reader = GRUCalibrationReader(n_calibration)

    # Pre-process for static quantization
    from onnxruntime.quantization import preprocess
    preprocess_path = input_path.replace(".onnx", "_preprocess.onnx")
    preprocess.quant_pre_process(input_path, preprocess_path)

    quantize_static(
        preprocess_path, output_path,
        calibration_data_reader=reader,
        quant_format=3,  # QDQ format
        weight_type=QuantType.QInt8,
        activation_type=QuantType.QUInt8,
    )
    orig = os.path.getsize(input_path) / 1024
    quant = os.path.getsize(output_path) / 1024
    print(f"  FP32 size: {orig:.1f} KB, INT8 size: {quant:.1f} KB, Reduction: {(1 - quant/orig)*100:.1f}%")

    # Cleanup
    if os.path.exists(preprocess_path):
        os.remove(preprocess_path)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input",  default="models/gru_ranker.onnx")
    p.add_argument("--output", default=None)
    p.add_argument("--mode",   choices=["dynamic", "static"], default="dynamic")
    args = p.parse_args()

    if args.output is None:
        args.output = args.input.replace(".onnx", f"_{args.mode}_int8.onnx")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    if args.mode == "dynamic":
        quantize_dynamic(args.input, args.output)
    else:
        quantize_static(args.input, args.output)
