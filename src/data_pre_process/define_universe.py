import os
import pandas as pd
from tqdm import tqdm

# Configuration
DATA_DIR = "./data/raw/content/30music_parsed"
EVENTS_FILE = os.path.join(DATA_DIR, "events.csv")
TRACKS_FILE = os.path.join(DATA_DIR, "tracks.csv")
SESSIONS_FILE = os.path.join(DATA_DIR, "session_tracks.csv")
SESSION_META_FILE = os.path.join(DATA_DIR, "session_meta.csv")

# Selection Strategy
TOP_N = 50000
OUTPUT_DIR = "./data/processed"

def define_universe():
    print(f"🚀 Starting Universe Definition (Top {TOP_N} tracks)...")
    
    if not os.path.exists(EVENTS_FILE):
        print(f"❌ Error: {EVENTS_FILE} not found. Please ensure CSVs are in {DATA_DIR}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Step 1: Count Track Frequencies from Events
    print("📋 Phase 1: Counting track frequencies in events...")
    chunk_size = 500000
    track_counts = pd.Series(dtype=int)
    
    # Verify column name for track_id
    sample = pd.read_csv(EVENTS_FILE, nrows=5)
    t_id_col = 'track_id' if 'track_id' in sample.columns else sample.columns[1]
    print(f"Using column '{t_id_col}' for track identification.")

    reader = pd.read_csv(EVENTS_FILE, chunksize=chunk_size)
    for chunk in tqdm(reader, desc="Parsing events"):
        track_counts = track_counts.add(chunk[t_id_col].value_counts(), fill_value=0)

    # Step 2: Select Top N
    top_tracks = track_counts.sort_values(ascending=False).head(TOP_N).index.tolist()
    top_tracks_set = set(top_tracks)
    print(f"✅ Selected top {len(top_tracks)} unique track IDs.")

    # Step 3: Filter Metadata (Universe Tracks)
    print("🧹 Phase 2: Generating Clean Universe Metadata...")
    # Load tracks and deduplicate
    tracks_df = pd.read_csv(TRACKS_FILE)
    print(f"Raw Tracks: {len(tracks_df)}")
    tracks_df = tracks_df.drop_duplicates(subset='track_id')
    print(f"Unique Tracks Source: {len(tracks_df)}")
    
    # Filter only top tracks
    universe_metadata = tracks_df[tracks_df['track_id'].isin(top_tracks_set)]
    universe_metadata.to_csv(os.path.join(OUTPUT_DIR, "universe_metadata.csv"), index=False)
    
    # Step 4: Filter Sessions
    # print("🧹 Phase 3: Filtering session logs to the Universal set...")
    # session_reader = pd.read_csv(SESSIONS_FILE, chunksize=chunk_size)
    
    # # Create the output file and write header (Overwrite existing)
    # out_session_file = os.path.join(OUTPUT_DIR, "filtered_sessions.csv")
    # if os.path.exists(out_session_file): os.remove(out_session_file)
    
    # first_chunk = True
    # for chunk in tqdm(session_reader, desc="Filtering sessions"):
    #     filtered_chunk = chunk[chunk['track_id'].isin(top_tracks_set)]
    #     if not filtered_chunk.empty:
    #         filtered_chunk.to_csv(out_session_file, mode='a', index=False, header=first_chunk)
    #         first_chunk = False
            
    # print("🎉 Universe Synchronization Complete!")
    # print(f"- Universe Size: {len(universe_metadata)} unique tracks")
    # print(f"- Processed Data stored in: {OUTPUT_DIR}")

if __name__ == "__main__":
    define_universe()
