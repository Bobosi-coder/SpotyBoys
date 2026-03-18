import os
import pandas as pd
import numpy as np
from tqdm import tqdm
import ast

# Configuration
DATA_DIR = "./data/processed"
SESSIONS_FILE = os.path.join(DATA_DIR, "filtered_sessions.csv")
EMBEDDINGS_FILE = os.path.join(DATA_DIR, "song_catalog_embeddings.csv")
OUTPUT_TRAINING = os.path.join(DATA_DIR, "training_dataset_v1.csv")

def engineer_features():
    print("🧠 Starting Stage 2: Session Feature Engineering...")
    
    if not os.path.exists(SESSIONS_FILE) or not os.path.exists(EMBEDDINGS_FILE):
        print("❌ Error: Missing input files in processed_data")
        return

    # Step 1: Load data
    print("📥 Loading sessions and embeddings...")
    sessions_df = pd.read_csv(SESSIONS_FILE)
    embeddings_df = pd.read_csv(EMBEDDINGS_FILE)
    
    # Convert embedding string back to list/numpy
    embeddings_df['embedding'] = embeddings_df['embedding'].apply(lambda x: np.array(ast.literal_eval(x), dtype=np.float32))
    
    # Step 2: Join Sessions with Embeddings
    print("🔗 Joining logs with embeddings...")
    df = pd.merge(sessions_df, embeddings_df, on='track_id', how='inner')
    
    # Step 3: Sort by session and position
    df = df.sort_values(['session_id', 'position'])
    
    # Step 4: Calculate rolling context embedding (Historical average within session)
    print("⏳ Calculating session context vectors...")
    
    def get_session_context(group):
        # group is already sorted by position
        embs = np.stack(group['embedding'].values)
        
        if len(embs) == 1:
            group['context_embedding'] = [np.zeros(embs.shape[1], dtype=np.float32).tolist()]
            return group
            
        # Cumulative sum of previous embeddings
        cum_sum = np.cumsum(embs, axis=0)
        counts = np.arange(1, len(embs) + 1).reshape(-1, 1)
        cum_mean = cum_sum / counts
        
        # Shift to avoid leakage (Predict track N using mean of 0..N-1)
        context = np.roll(cum_mean, 1, axis=0)
        context[0] = np.zeros(embs.shape[1])
        
        group['context_embedding'] = [c.tolist() for c in context]
        return group

    # Apply within each session
    print("Applying session transformations...")
    df = df.groupby('session_id').apply(get_session_context)
    
    # Step 5: Clean up and save
    print(f"💾 Saving final training dataset to {OUTPUT_TRAINING}...")
    output_df = df.drop(columns=['embedding'])
    output_df.to_csv(OUTPUT_TRAINING, index=False)
    print("🎉 Stage 2 Complete!")
    print(f"- Final Dataset Size: {len(output_df)} rows")

if __name__ == "__main__":
    engineer_features()
