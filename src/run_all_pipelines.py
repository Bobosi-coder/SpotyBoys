import sys
import subprocess
import os

def run_all_stages(limit=None):
    print("="*60)
    print(f"🚀 STARTING FULL DATA ENGINEERING PIPELINE (Limit={limit if limit else 'ALL'})")
    print("="*60)
    
    limit_args = [str(limit)] if limit is not None else []
    
    # 1. Download Previews
    print("\n[1/3] Downloading Audio Previews...")
    subprocess.run([sys.executable, "src/download_previews.py"] + limit_args, check=True)
    
    # 2. Extract Catalog Embeddings
    print("\n[2/3] Extracting PANN Embeddings (Stage 1)...")
    subprocess.run([sys.executable, "src/pipeline_stage1_catalog.py"] + limit_args, check=True)
    
    # 3. Session Feature Engineering
    print("\n[3/4] Engineering Session Contexts (Stage 2)...")
    subprocess.run([sys.executable, "src/pipeline_stage2_training.py"], check=True)
    
    # 4. Vector Database
    print("\n[4/4] Building FAISS Vector Database...")
    subprocess.run([sys.executable, "src/build_vector_db.py"], check=True)
    
    print("\n" + "="*60)
    print("🎉 FULL PIPELINE COMPLETED SUCCESSFULLY!")
    print("="*60)

if __name__ == "__main__":
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
        
    run_all_stages(limit=limit)
