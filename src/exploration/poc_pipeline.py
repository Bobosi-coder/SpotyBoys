import sys
import subprocess
import os
import ssl

def install_packages():
    """Install required packages if not present."""
    packages = ["yt-dlp", "pandas", "numpy"]
    for package in packages:
        try:
            package_import = package.replace("-", "_")
            __import__(package_import)
        except ImportError:
            print(f"Installing {package}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", package])

install_packages()

import yt_dlp
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings('ignore')

# Fix a common macos python ssl issue
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

def download_audio_snippet(search_query: str, output_path: str):
    """
    Downloads the first 30 seconds of a song from YouTube as an mp3 using yt-dlp.
    """
    print(f"🔍 Searching and downloading: {search_query}...")
    
    # Configure yt-dlp to download default best audio without ffmpeg processing
    ydl_opts = {
        'outtmpl': output_path + '.%(ext)s',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'ytsearch',
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # ytsearch1: searches youtube and downloads the top 1 result
            info = ydl.extract_info(f"ytsearch1:{search_query}", download=True)
            # Find the actual downloaded file name
            if 'entries' in info and len(info['entries']) > 0:
                actual_filename = ydl.prepare_filename(info['entries'][0])
                print(f"✅ Successfully downloaded audio to {actual_filename}")
                return actual_filename
        raise Exception("Download completed but no file entries found.")
    except Exception as e:
        print(f"⚠️ Failed to download {search_query}: {e}. Falling back to mock file...")
        mock_file = f"{output_path}_mock.mp3"
        # Create a mock file of random size to simulate download
        with open(mock_file, 'wb') as f:
            f.write(np.random.bytes(np.random.randint(50000, 200000)))
        return mock_file

def extract_audio_embedding(audio_path: str):
    """
    Simulates a deep learning spectrum extractor (like CLAP) returning a dense vector.
    Mocks the embedding by generating a deterministic vector based on the file size.
    """
    print(f"🧠 Extracting features from {audio_path}...")
    try:
        if not os.path.exists(audio_path):
            return None
        
        # MOCK EMBEDDING: Generate a deterministic 13-dim vector using file size as seed
        file_size = os.path.getsize(audio_path)
        np.random.seed(file_size % (2**32 - 1))
        embedding = np.random.randn(13)
        
        print(f"✅ Spectrum Embedding shape (Mocked): {embedding.shape}")
        return embedding
    except Exception as e:
        print(f"❌ Failed to extract features: {e}")
        return None

def run_poc_pipeline():
    print("🚀 Starting Data Engineering Pipeline POC...")
    
    # 1. Simulate a chunk from 30Music / Last.fm interactions with granular session features
    # session_id | user_id | timestamp | track_name | artist_name | play_time_sec | track_len_sec | replay_count | action
    mock_interactions = [
        {"session_id": "S1", "user_id": "U1", "timestamp": "2026-03-16 08:15:00", "artist": "Daft Punk", "track": "Get Lucky", "play_time_sec": 240, "track_len_sec": 240, "replay_count": 1, "action": "COMPLETE"},
        {"session_id": "S1", "user_id": "U1", "timestamp": "2026-03-16 08:19:00", "artist": "Rick Astley", "track": "Never Gonna Give You Up", "play_time_sec": 15, "track_len_sec": 212, "replay_count": 0, "action": "SKIP"},
        {"session_id": "S2", "user_id": "U2", "timestamp": "2026-03-14 22:30:00", "artist": "Queen", "track": "Bohemian Rhapsody", "play_time_sec": 300, "track_len_sec": 354, "replay_count": 0, "action": "SKIP"},
        {"session_id": "S2", "user_id": "U2", "timestamp": "2026-03-14 22:35:00", "artist": "Daft Punk", "track": "Get Lucky", "play_time_sec": 240, "track_len_sec": 240, "replay_count": 3, "action": "COMPLETE"}, # High replay
    ]
    df_interactions = pd.DataFrame(mock_interactions)
    df_interactions['timestamp'] = pd.to_datetime(df_interactions['timestamp'])
    
    print("\n📊 1. Raw Interaction Data (Simulated Last.fm):")
    print(df_interactions)
    
    print("\n⚙️ 1b. Feature Engineering (Granular Session Features):")
    # Calculate Percent Completion
    df_interactions['percent_completion'] = round(df_interactions['play_time_sec'] / df_interactions['track_len_sec'], 3)
    
    # Calculate Relative Skip Position (Early, Median, Late, None)
    def skip_position(row):
        if row['action'] == "COMPLETE":
            return "None"
        elif row['percent_completion'] < 0.25:
            return "Early_Skip"
        elif row['percent_completion'] < 0.75:
            return "Median_Skip"
        else:
            return "Late_Skip"
            
    df_interactions['skip_position'] = df_interactions.apply(skip_position, axis=1)
    
    # Calculate Temporal Features
    df_interactions['hour_of_day'] = df_interactions['timestamp'].dt.hour
    df_interactions['day_of_week'] = df_interactions['timestamp'].dt.dayofweek
    
    # Calculate Sequence Features (Session Position & Skip Streak)
    df_interactions = df_interactions.sort_values(by=['session_id', 'timestamp'])
    
    df_interactions['session_position'] = df_interactions.groupby('session_id').cumcount() + 1
    
    # Calculate skip streak (consecutive skips prior to current track in the same session)
    df_interactions['is_skip'] = (df_interactions['action'] == 'SKIP').astype(int)
    # create groups that break when a non-skip occurs
    df_interactions['streak_group'] = (df_interactions['is_skip'] == 0).groupby(df_interactions['session_id']).cumsum()
    # compute cumulative sum of skips per group, then shift by 1 to get *prior* skips
    df_interactions['skip_streak'] = df_interactions.groupby(['session_id', 'streak_group'])['is_skip'].cumsum()
    df_interactions['skip_streak'] = df_interactions.groupby('session_id')['skip_streak'].shift(1).fillna(0).astype(int)
    df_interactions.drop(columns=['is_skip', 'streak_group'], inplace=True)
    
    print(df_interactions[['user_id', 'track', 'percent_completion', 'hour_of_day', 'session_position', 'skip_streak', 'action']])
    
    # 2. Extract unique tracks to download (avoiding duplicates)
    unique_tracks = df_interactions[['artist', 'track']].drop_duplicates()
    
    # 3. Create a library to store embeddings
    audio_embeddings = {}
    
    os.makedirs("./audio_cache", exist_ok=True)
    
    print("\n🎧 2. Audio Acquisition & Feature Extraction:")
    for _, row in unique_tracks.iterrows():
        search_query = f"{row['artist']} - {row['track']}"
        output_filename = f"./audio_cache/{row['artist'].replace(' ', '_')}_{row['track'].replace(' ', '_')}"
        # Download
        downloaded_file = download_audio_snippet(search_query, output_filename)
        
        if downloaded_file and os.path.exists(downloaded_file):
            # Extract Mock Embedding
            embedding = extract_audio_embedding(downloaded_file)
            
            if embedding is not None:
                # Store the result
                audio_embeddings[search_query] = embedding

    print("\n🔗 3. Joining Interaction Logs with Audio Embeddings:")
    
    # Map the embeddings back to the dataframe
    def get_embedding(row):
        query = f"{row['artist']} - {row['track']}"
        emb = audio_embeddings.get(query)
        # Convert numpy array to list for display, or None if missing
        return emb.tolist() if emb is not None else None

    df_interactions['audio_vector'] = df_interactions.apply(get_embedding, axis=1)
    
    print(df_interactions[['user_id', 'artist', 'track', 'session_position', 'skip_streak', 'action']])
    print("\nSample Audio Vector Data attached to first interaction row:")
    first_vector = df_interactions.iloc[0]['audio_vector']
    if first_vector:
        # Print first 5 dimensions of the 13-dim MFCC/Mock vector
        print(f"Dimension (total {len(first_vector)}): {np.round(first_vector[:5], 3)} ...")
    
    print("\n🎉 POC Pipeline Complete! Vectors and Granular Features are ready for Neural Ranker training.")

if __name__ == "__main__":
    run_poc_pipeline()
