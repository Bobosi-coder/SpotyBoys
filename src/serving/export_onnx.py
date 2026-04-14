import torch
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline_stage1_catalog import Cnn14Standalone

def main():
    SR = 32000
    print("Initializing Un-trained Cnn14Standalone...")
    model = Cnn14Standalone(SR, 1024, 320, 64, 50, 14000, 527)
    model.eval()

    # Create dummy input: batch_size=1, length=32000*5 (5 seconds)
    dummy_input = torch.randn(1, SR * 5, dtype=torch.float32)

    onnx_path = "models/Cnn14.onnx"
    os.makedirs(os.path.dirname(onnx_path), exist_ok=True)
    
    print(f"Exporting PyTorch model to ONNX format at {onnx_path}...")
    torch.onnx.export(
        model, 
        dummy_input, 
        onnx_path, 
        export_params=True, 
        opset_version=14, 
        do_constant_folding=True, 
        input_names=['input'], 
        output_names=['embedding'], 
        dynamic_axes={'input': {0: 'batch_size', 1: 'audio_length'}, 
                      'embedding': {0: 'batch_size'}}
    )
    print("Export successful.")

if __name__ == "__main__":
    main()
