"""
Export GRU Ranker from PyTorch to ONNX format.
Model-level optimization: graph compilation with constant folding.
Run locally or on Chameleon BEFORE benchmarking the ONNX/Triton options.

Usage:
  python export_onnx.py [--checkpoint PATH] [--output PATH]
"""
import argparse, os, sys
import torch
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import load_or_create_model

MAX_SEQ_LEN = 20
NUM_CANDIDATES = 200
D_EMB = 128

def export(checkpoint_path=None, output_path="models/gru_ranker.onnx"):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    model = load_or_create_model(checkpoint_path, device="cpu")

    # Dummy inputs matching serving contract
    item_embs   = torch.randn(1, MAX_SEQ_LEN, D_EMB)
    labels      = torch.zeros(1, MAX_SEQ_LEN, dtype=torch.long)
    u_long      = torch.randn(1, D_EMB)
    cand_embs   = torch.randn(1, NUM_CANDIDATES, D_EMB)
    cross_feat  = torch.randn(1, NUM_CANDIDATES, 3)

    print(f"Exporting to {output_path} ...")

    # Build export kwargs — use legacy TorchScript exporter (dynamo=False)
    # for compatibility with onnxruntime quantizer.
    export_kwargs = dict(
        export_params=True,
        opset_version=int(os.getenv("ONNX_OPSET", "18")),
        do_constant_folding=True,
        input_names=["item_embs", "labels", "u_long", "candidate_embs", "cross_features"],
        output_names=["scores"],
        dynamic_axes={
            "item_embs":       {0: "batch"},
            "labels":          {0: "batch"},
            "u_long":          {0: "batch"},
            "candidate_embs":  {0: "batch"},
            "cross_features":  {0: "batch"},
            "scores":          {0: "batch"},
        },
    )

    # PyTorch 2.5+ defaults to dynamo exporter; force legacy for quantizer compat
    if torch.__version__ >= "2.5":
        export_kwargs["dynamo"] = False

    torch.onnx.export(
        model,
        (item_embs, labels, u_long, cand_embs, cross_feat),
        output_path,
        **export_kwargs,
    )

    fsize = os.path.getsize(output_path) / 1024
    print(f"ONNX model size: {fsize:.1f} KB")

    # Verify (optional — needs onnxruntime)
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(output_path, providers=["CPUExecutionProvider"])
        out = sess.run(None, {
            "item_embs":       item_embs.numpy(),
            "labels":          labels.numpy(),
            "u_long":          u_long.numpy(),
            "candidate_embs":  cand_embs.numpy(),
            "cross_features":  cross_feat.numpy(),
        })
        print(f"Verification OK — output shape: {out[0].shape}")
    except ImportError:
        print("(onnxruntime not installed — skipping verification)")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", default=None)
    p.add_argument("--output", default="models/gru_ranker.onnx")
    args = p.parse_args()
    export(args.checkpoint, args.output)
