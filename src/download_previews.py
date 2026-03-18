import os
import pandas as pd
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Configuration
UNIVERSE_FILE = "./data/processed/universe_metadata.csv"
OUTPUT_DIR = os.getenv("AUDIO_PREVIEWS_DIR", "./data/raw/audio_previews")
MAX_WORKERS = 5 # Modest concurrency to avoid API rate limits

# Create output directory
os.makedirs(OUTPUT_DIR, exist_ok=True)

def download_track(row):
    artist = row['artist_hint']
    title = row['title']
    track_id = row['track_id']
    
    query = f"{artist} {title}"
    output_path = os.path.join(OUTPUT_DIR, f"{track_id}.mp3")
    
    # Skip if already downloaded
    if os.path.exists(output_path):
        return True
        
    try:
        # Search Deezer
        search_url = f"https://api.deezer.com/search?q={requests.utils.quote(query)}"
        response = requests.get(search_url, timeout=10).json()
        
        if not response.get('data'):
            return False
            
        preview_url = response['data'][0]['preview']
        if not preview_url:
            return False
            
        # Download the preview
        r = requests.get(preview_url, stream=True, timeout=10)
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        return False

def run_download(limit=None):
    if not os.path.exists(UNIVERSE_FILE):
        print(f"❌ Error: {UNIVERSE_FILE} not found.")
        return
        
    print(f"📥 Loading Universe from {UNIVERSE_FILE}...")
    df = pd.read_csv(UNIVERSE_FILE)
    
    if limit:
        df = df.head(limit)
        print(f"⚠️ Limiting to first {limit} tracks for estimation test.")
    else:
        print(f"🚀 Preparing to download all {len(df)} tracks.")
        
    print(f"📂 Saving MP3s to {OUTPUT_DIR}/")
    
    success_count = 0
    start_time = time.time()
    
    # Use ThreadPoolExecutor to download in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        futures = {executor.submit(download_track, row): row['track_id'] for _, row in df.iterrows()}
        
        # Process results as they complete with a progress bar
        for future in tqdm(as_completed(futures), total=len(futures), desc="Downloading"):
            if future.result():
                success_count += 1
                
    elapsed_time = time.time() - start_time
    print("\n🎉 Download Batch Complete!")
    print(f"✅ Successfully downloaded: {success_count}/{len(df)} tracks")
    print(f"⏱️ Time taken: {elapsed_time:.2f} seconds")
    
    if len(df) > 0:
        avg_speed = elapsed_time / len(df)
        print(f"⚡ Average speed: {avg_speed:.2f} seconds/track")
        if limit and limit < 50000:
            est_total_hours = (avg_speed * 50000) / 3600
            print(f"📊 Estimated time for the full 50,000 track universe: ~{est_total_hours:.1f} hours.")

if __name__ == "__main__":
    import sys
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    # You can change the limit here to run a smaller test first to estimate total time
    run_download(limit=limit)
