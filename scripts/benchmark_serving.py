import argparse
import base64
import time
import requests
import numpy as np
from scipy.io import wavfile
import io
import concurrent.futures

def create_dummy_wav_b64(seconds=5, sr=32000):
    dummy_audio = np.random.randn(sr * seconds).astype(np.float32)
    dummy_audio_int16 = (dummy_audio * 32767).astype(np.int16)
    buffer = io.BytesIO()
    wavfile.write(buffer, sr, dummy_audio_int16)
    return base64.b64encode(buffer.getvalue()).decode('utf-8')

def send_request(url, payload):
    start = time.time()
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            return time.time() - start, True
        else:
            return 0, False
    except Exception as e:
        return 0, False

def benchmark(url, num_requests=100, concurrency=10):
    print(f"Benchmarking {url} with {num_requests} requests (concurrency: {concurrency})...")
    payload = {"audio_b64": create_dummy_wav_b64()}
    
    # Warmup
    for _ in range(2):
        try:
            requests.post(url, json=payload)
        except:
            pass
            
    latencies = []
    errors = 0
    
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(send_request, url, payload) for _ in range(num_requests)]
        for future in concurrent.futures.as_completed(futures):
            lat, success = future.result()
            if success:
                latencies.append(lat)
            else:
                errors += 1
                
    total_time = time.time() - start_time
    
    if latencies:
        p50 = np.percentile(latencies, 50)
        p95 = np.percentile(latencies, 95)
        throughput = len(latencies) / total_time
    else:
        p50 = p95 = throughput = 0
        
    print("-" * 40)
    print(f"Results for {url}:")
    print(f"Total Time: {total_time:.2f}s")
    print(f"Throughput: {throughput:.2f} req/s")
    print(f"p50 Latency: {p50*1000:.2f} ms")
    print(f"p95 Latency: {p95*1000:.2f} ms")
    print(f"Errors: {errors}")
    print("-" * 40)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, required=True, help="API endpoint URL")
    parser.add_argument("-n", "--num_requests", type=int, default=100)
    parser.add_argument("-c", "--concurrency", type=int, default=10)
    args = parser.parse_args()
    benchmark(args.url, args.num_requests, args.concurrency)
