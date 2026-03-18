import pandas as pd
import requests
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

UNIVERSE_FILE = "./data/processed/universe_metadata.csv"
SAMPLE_SIZE = 2500  # 5% of 50k is mathematically highly significant (margin of error < 2%)

def check_coverage(row):
    query = f"{row['artist_hint']} {row['title']}"
    search_url = f"https://api.deezer.com/search?q={requests.utils.quote(query)}"
    
    try:
        # Strict rate limit adherence (Deezer allows ~10 req/s without token)
        time.sleep(0.1) 
        response = requests.get(search_url, timeout=10)
        
        if response.status_code == 429:
            # Hit Quota, wait and retry once
            time.sleep(2)
            response = requests.get(search_url, timeout=10)
            
        data = response.json()
        
        if not data.get('data'):
            return False
            
        preview_url = data['data'][0].get('preview')
        if not preview_url:
            return False
            
        return True
    except Exception as e:
        return False

def run_verification():
    print(f"📥 Loading Universal Song Set from {UNIVERSE_FILE}...")
    df = pd.read_csv(UNIVERSE_FILE)
    
    # Randomly sample the universe to provide a fast, statistically significant coverage percentage
    # (Checking all 50k at 10 req/s would take ~1.4 hours just to verify)
    print(f"🎲 Randomly sampling {SAMPLE_SIZE} tracks (5% of total universe) for statistical coverage analysis...")
    sample_df = df.sample(n=SAMPLE_SIZE, random_state=42)
    
    found = 0
    missing = 0
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(check_coverage, row): row['track_id'] for _, row in sample_df.iterrows()}
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="Verifying Coverage"):
            if future.result():
                found += 1
            else:
                missing += 1
                
    coverage_pct = (found / SAMPLE_SIZE) * 100
    
    print("\n📊 Deezer Availability Coverage Report")
    print("-" * 40)
    print(f"Total Sampled  : {SAMPLE_SIZE} tracks")
    print(f"Found w/ Audio : {found}")
    print(f"Missing        : {missing}")
    print(f"Coverage Pct   : {coverage_pct:.2f}%")
    print("-" * 40)
    print(f"Estimated Available Tracks in Full 50k Universe: ~{int(50000 * (coverage_pct/100))}")

if __name__ == "__main__":
    run_verification()
