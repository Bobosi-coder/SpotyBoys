"""
Benchmark script — sends concurrent requests to any serving endpoint.
Measures p50/p95 latency, throughput, and error rate.

Usage:
  python benchmark_serving.py --url http://IP:PORT/predict -n 200 -c 10
  python benchmark_serving.py --url http://IP:PORT/predict -n 200 -c 50
"""
import argparse, time, json
import requests
import numpy as np
import concurrent.futures

def create_payload(n_tracks=5):
    """Create a realistic recommendation request."""
    return {
        "user_id": int(np.random.randint(0, 200)),
        "session_track_ids": [int(x) for x in np.random.randint(0, 10000, size=n_tracks)],
        "session_labels": [int(x) for x in np.random.choice([0, 0, 0, 1, 2], size=n_tracks)],
    }

def send_request(url, payload):
    start = time.time()
    try:
        resp = requests.post(url, json=payload, timeout=30)
        lat = time.time() - start
        if resp.status_code == 200:
            body = resp.json()
            return lat, True, body.get("inference_time_ms", 0)
        return lat, False, 0
    except Exception:
        return time.time() - start, False, 0

def benchmark(url, n, concurrency, session_len=5):
    print(f"{'='*60}")
    print(f"Benchmark: {url}")
    print(f"Requests: {n}, Concurrency: {concurrency}, Session: {session_len} tracks")
    print(f"{'='*60}")

    payload = create_payload(session_len)

    # Warmup
    for _ in range(3):
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass

    latencies = []
    infer_times = []
    errors = 0

    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(send_request, url, create_payload(session_len)) for _ in range(n)]
        for f in concurrent.futures.as_completed(futs):
            lat, ok, infer_ms = f.result()
            if ok:
                latencies.append(lat)
                infer_times.append(infer_ms)
            else:
                errors += 1
    total = time.time() - t_start

    if latencies:
        p50 = np.percentile(latencies, 50) * 1000
        p95 = np.percentile(latencies, 95) * 1000
        p99 = np.percentile(latencies, 99) * 1000
        throughput = len(latencies) / total
        avg_infer = np.mean(infer_times)
    else:
        p50 = p95 = p99 = throughput = avg_infer = 0

    print(f"\nResults:")
    print(f"  Successful: {len(latencies)}/{n}")
    print(f"  Errors:     {errors}")
    print(f"  Total time: {total:.2f}s")
    print(f"  Throughput: {throughput:.2f} req/s")
    print(f"  p50 latency:  {p50:.2f} ms")
    print(f"  p95 latency:  {p95:.2f} ms")
    print(f"  p99 latency:  {p99:.2f} ms")
    print(f"  Avg model inference: {avg_infer:.2f} ms")
    print(f"  Error rate: {errors/n*100:.1f}%")
    print(f"{'='*60}\n")

    return {
        "url": url, "n": n, "concurrency": concurrency,
        "p50_ms": round(p50, 2), "p95_ms": round(p95, 2), "p99_ms": round(p99, 2),
        "throughput": round(throughput, 2), "error_rate": round(errors/n*100, 1),
        "avg_infer_ms": round(avg_infer, 2),
    }

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("-n", "--num_requests", type=int, default=200)
    p.add_argument("-c", "--concurrency", type=int, default=10)
    p.add_argument("--session_len", type=int, default=6)
    p.add_argument("--save", default=None, help="Save JSON results to file")
    args = p.parse_args()

    result = benchmark(args.url, args.num_requests, args.concurrency, args.session_len)

    if args.save:
        with open(args.save, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to {args.save}")
