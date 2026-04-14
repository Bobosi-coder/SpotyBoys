import os
import pandas as pd
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import base64

# Configuration
UNIVERSE_FILE = "./data/processed/universe_metadata.csv"
OUTPUT_FILE = "./data/raw/spotify_audio_features.csv"
MAX_WORKERS = 10 

# Ensure you have set your Spotify Developer credentials in your environment variables!
CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID", "your_client_id_here")
CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET", "your_client_secret_here")

def get_spotify_token():
    auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
    auth_bytes = auth_string.encode("utf-8")
    auth_base64 = str(base64.b64encode(auth_bytes), "utf-8")
    
    url = "https://accounts.spotify.com/api/token"
    headers = {
        "Authorization": f"Basic {auth_base64}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {"grant_type": "client_credentials"}
    
    response = requests.post(url, headers=headers, data=data)
    if response.status_code != 200:
        raise Exception(f"Failed to get token: {response.text}")
    return response.json()["access_token"]

def search_spotify_track(row, token):
    artist = str(row['artist_hint'])
    title = str(row['title'])
    track_id = row['track_id']
    
    # Clean up names for better search matching
    query = f"track:{title} artist:{artist}"
    url = f"https://api.spotify.com/v1/search?q={requests.utils.quote(query)}&type=track&limit=1"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        response = requests.get(url, headers=headers, timeout=5).json()
        items = response.get('tracks', {}).get('items', [])
        if items:
            return {'track_id': track_id, 'spotify_id': items[0]['id']}
        return {'track_id': track_id, 'spotify_id': None}
    except Exception as e:
        return {'track_id': track_id, 'spotify_id': None}

def fetch_audio_features(spotify_ids, token):
    valid_ids = [s for s in spotify_ids if s is not None]
    if not valid_ids:
        return []
        
    ids_str = ",".join(valid_ids)
    url = f"https://api.spotify.com/v1/audio-features?ids={ids_str}"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10).json()
        return response.get('audio_features', [])
    except Exception:
        return []

def run_pipeline(limit=None):
    if not os.path.exists(UNIVERSE_FILE):
        print(f"❌ Error: {UNIVERSE_FILE} not found.")
        return
        
    df = pd.read_csv(UNIVERSE_FILE)
    if limit:
        df = df.head(limit)
        print(f"⚠️ Limiting to {limit} tracks for test.")
        
    try:
        token = get_spotify_token()
    except Exception as e:
        print(f"❌ Spotify Authentication Failed! Did you set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET?\n{e}")
        return

    print("🔍 Step 1: Searching for Spotify IDs in parallel...")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(search_spotify_track, row, token) for _, row in df.iterrows()]
        for future in tqdm(as_completed(futures), total=len(futures)):
            results.append(future.result())
            
    track_to_spotify = {r['track_id']: r['spotify_id'] for r in results}
    
    print("\n🎧 Step 2: Fetching Audio Features in batches of 100...")
    features_list = []
    
    # Spotify allows 100 IDs per request for audio features
    all_spotify_ids = [sid for sid in track_to_spotify.values() if sid]
    for i in tqdm(range(0, len(all_spotify_ids), 100)):
        batch_ids = all_spotify_ids[i:i+100]
        batch_features = fetch_audio_features(batch_ids, token)
        features_list.extend([f for f in batch_features if f])
        
    # Map back to original track_ids
    spotify_to_features = {f['id']: f for f in features_list}
    
    final_output = []
    for track_id, sid in track_to_spotify.items():
        if sid and sid in spotify_to_features:
            f = spotify_to_features[sid]
            final_output.append({
                'track_id': track_id,
                'spotify_id': sid,
                'acousticness': f.get('acousticness'),
                'danceability': f.get('danceability'),
                'energy': f.get('energy'),
                'instrumentalness': f.get('instrumentalness'),
                'liveness': f.get('liveness'),
                'loudness': f.get('loudness'),
                'speechiness': f.get('speechiness'),
                'valence': f.get('valence'),
                'tempo': f.get('tempo'),
                'key': f.get('key'),
                'mode': f.get('mode')
            })
            
    out_df = pd.DataFrame(final_output)
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    out_df.to_csv(OUTPUT_FILE, index=False)
    print(f"\n✅ Complete! Written {len(out_df)} records to {OUTPUT_FILE}")
    print("🚀 You can now proceed to Component 1 to attach these to your Item2Vec embeddings!")

if __name__ == "__main__":
    import sys
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    run_pipeline(limit=limit)
