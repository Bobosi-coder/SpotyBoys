import os
import pandas as pd
import numpy as np
import faiss
import ast
import json

DATA_DIR = "./data/processed"
EMBEDDINGS_FILE = os.path.join(DATA_DIR, "song_catalog_embeddings.csv")
INDEX_FILE = os.path.join(DATA_DIR, "song_embeddings_faiss.index")
MAPPING_FILE = os.path.join(DATA_DIR, "faiss_track_id_mapping.json")

def build_vector_db():
    print(f"🔍 Starting Vector Database Construction...")
    
    if not os.path.exists(EMBEDDINGS_FILE):
        print(f"❌ Error: {EMBEDDINGS_FILE} not found. Skip DB creation.")
        return
        
    print("📥 Loading embeddings from catalog...")
    df = pd.read_csv(EMBEDDINGS_FILE)
    
    if len(df) == 0:
        print("⚠️ No embeddings found in catalog. Skipping FAISS.")
        return
        
    # Convert string representation of list back to numpy array
    print("🔄 Processing vectors...")
    embeddings = np.array([ast.literal_eval(x) for x in df['embedding'].values], dtype=np.float32)
    track_ids = df['track_id'].tolist()
    
    # 2048 dimensions for PANN-Cnn14
    dimension = embeddings.shape[1]
    
    # Create an Inner Product (Cosine Similarity) index since we want similar audio
    # First, l2-normalize the embeddings so Inner Product behaves exactly like Cosine Similarity
    faiss.normalize_L2(embeddings)
    
    print(f"🏗️ Building FAISS IndexFlatIP (Dimension: {dimension})...")
    index = faiss.IndexFlatIP(dimension)
    
    # Add vectors to the index
    index.add(embeddings)
    print(f"✅ Added {index.ntotal} vectors to the DB.")
    
    # Save the index to disk
    faiss.write_index(index, INDEX_FILE)
    
    # Save the mapping from FAISS internal ID (0 to N-1) to actual track_id
    mapping = {i: track_id for i, track_id in enumerate(track_ids)}
    with open(MAPPING_FILE, 'w') as f:
        json.dump(mapping, f)
        
    print(f"💾 Vector Database saved to:\n  - Index: {INDEX_FILE}\n  - Mapping: {MAPPING_FILE}")
    print("🎉 Stage 1C (Vector DB) Complete!")

if __name__ == "__main__":
    build_vector_db()
