def run_all_stages(limit=None):
    from src.download_previews import run_download
    from src.pipeline_stage1_catalog import run_pipeline
    from src.pipeline_stage2_training import engineer_features

    print("="*60)
    print(f"🚀 STARTING FULL DATA ENGINEERING PIPELINE (Limit={limit if limit else 'ALL'})")
    print("="*60)

    # 1. Download Previews
    print("\n[1/3] Downloading Audio Previews...")
    run_download(limit=limit)
    
    # 2. Extract Catalog Embeddings
    print("\n[2/3] Extracting PANN Embeddings (Stage 1)...")
    run_pipeline(limit=limit)
    
    # 3. Session Feature Engineering
    print("\n[3/3] Engineering Session Contexts (Stage 2)...")
    engineer_features()
    
    print("\n" + "="*60)
    print("🎉 FULL PIPELINE COMPLETED SUCCESSFULLY!")
    print("="*60)

if __name__ == "__main__":
    import sys

    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
        
    run_all_stages(limit=limit)
