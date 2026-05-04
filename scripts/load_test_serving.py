#!/usr/bin/env python3
"""
load_test_serving.py — SpotyBoys serving load test for Q2.1 evidence.

Authenticates once, then spawns N concurrent workers that repeatedly call
/session/bootstrap and /recommendations/next for a configurable duration.
Prints a latency / error / fallback summary at the end.

Usage:
  python scripts/load_test_serving.py \
    --base-url http://129.114.25.25:8089 \
    --email 40305@navidrome.local \
    --password test123 \
    --users 20 \
    --duration-seconds 300
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

# ------------------------------------------------------------------ #
# Result collection
# ------------------------------------------------------------------ #

@dataclass
class RequestResult:
    endpoint: str
    status_code: int
    latency_ms: float
    fallback_level: str = "none"
    fallback_state: str = "healthy"
    degraded: bool = False
    error: Optional[str] = None
    timestamp: str = ""


@dataclass
class LoadTestStats:
    results: List[RequestResult] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, result: RequestResult) -> None:
        with self.lock:
            self.results.append(result)

    def summary(self) -> Dict[str, Any]:
        with self.lock:
            all_results = list(self.results)

        if not all_results:
            return {"total_requests": 0, "error": "no requests completed"}

        bootstrap_results = [r for r in all_results if r.endpoint == "bootstrap"]
        recommend_results = [r for r in all_results if r.endpoint == "recommend"]

        def _stats(results: List[RequestResult], label: str) -> Dict[str, Any]:
            if not results:
                return {"endpoint": label, "count": 0}
            latencies = [r.latency_ms for r in results]
            errors = [r for r in results if r.error or r.status_code >= 400]
            fallbacks = [r for r in results if r.fallback_level != "none" or r.fallback_state != "healthy"]
            degraded = [r for r in results if r.degraded]
            return {
                "endpoint": label,
                "count": len(results),
                "error_count": len(errors),
                "error_rate": round(len(errors) / len(results), 4),
                "fallback_count": len(fallbacks),
                "fallback_rate": round(len(fallbacks) / len(results), 4),
                "degraded_count": len(degraded),
                "latency_p50_ms": round(statistics.median(latencies), 2),
                "latency_p95_ms": round(_percentile(latencies, 95), 2),
                "latency_p99_ms": round(_percentile(latencies, 99), 2),
                "latency_avg_ms": round(statistics.mean(latencies), 2),
                "latency_min_ms": round(min(latencies), 2),
                "latency_max_ms": round(max(latencies), 2),
            }

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_requests": len(all_results),
            "total_errors": len([r for r in all_results if r.error or r.status_code >= 400]),
            "overall_error_rate": round(
                len([r for r in all_results if r.error or r.status_code >= 400]) / len(all_results), 4
            ),
            "bootstrap": _stats(bootstrap_results, "/session/bootstrap"),
            "recommend": _stats(recommend_results, "/recommendations/next"),
            "combined": _stats(all_results, "combined"),
        }


# ------------------------------------------------------------------ #
# Auth
# ------------------------------------------------------------------ #

def authenticate(base_url: str, email: str, password: str) -> requests.Session:
    session = requests.Session()
    resp = session.post(
        f"{base_url}/spotiboys/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"[ERROR] Login failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    token = data.get("token", "")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    print(f"[OK] Authenticated as {email} (user_id={data.get('user_id', '?')})")
    return session


# ------------------------------------------------------------------ #
# Worker
# ------------------------------------------------------------------ #

def worker(
    worker_id: int,
    base_url: str,
    cookie_jar: dict,
    token: str,
    duration_seconds: int,
    stats: LoadTestStats,
    stop_event: threading.Event,
) -> None:
    session = requests.Session()
    session.cookies.update(cookie_jar)
    if token:
        session.headers["Authorization"] = f"Bearer {token}"

    deadline = time.monotonic() + duration_seconds
    iteration = 0

    while time.monotonic() < deadline and not stop_event.is_set():
        iteration += 1

        # --- bootstrap ---
        try:
            t0 = time.perf_counter()
            resp = session.get(f"{base_url}/session/bootstrap", timeout=30)
            latency = (time.perf_counter() - t0) * 1000
            result = RequestResult(
                endpoint="bootstrap",
                status_code=resp.status_code,
                latency_ms=latency,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            if resp.status_code == 200:
                body = resp.json()
                result.fallback_level = str(body.get("fallback_level", "none"))
                result.fallback_state = str(body.get("fallback_state", "healthy"))
                result.degraded = bool(body.get("degraded", False))
                session_id = body.get("session_id", "")
                queue_revision = body.get("queue", {}).get("revision", 1)
            else:
                result.error = f"HTTP {resp.status_code}"
                session_id = ""
                queue_revision = 1
        except Exception as exc:
            result = RequestResult(
                endpoint="bootstrap",
                status_code=0,
                latency_ms=0,
                error=str(exc),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            session_id = ""
            queue_revision = 1
        stats.add(result)

        if stop_event.is_set():
            break

        # --- recommend ---
        if session_id:
            try:
                payload = {
                    "session_id": session_id,
                    "user_id": "",
                    "request_id": f"load_{worker_id}_{iteration}_{uuid.uuid4().hex[:8]}",
                    "queue_revision": queue_revision,
                }
                t0 = time.perf_counter()
                resp = session.post(
                    f"{base_url}/recommendations/next",
                    json=payload,
                    timeout=30,
                )
                latency = (time.perf_counter() - t0) * 1000
                result = RequestResult(
                    endpoint="recommend",
                    status_code=resp.status_code,
                    latency_ms=latency,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                if resp.status_code == 200:
                    body = resp.json()
                    result.fallback_level = str(body.get("fallback_level", "none"))
                    result.fallback_state = str(body.get("fallback_state", "healthy"))
                    result.degraded = bool(body.get("degraded", False))
                else:
                    result.error = f"HTTP {resp.status_code}"
            except Exception as exc:
                result = RequestResult(
                    endpoint="recommend",
                    status_code=0,
                    latency_ms=0,
                    error=str(exc),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            stats.add(result)

        # Small jitter to avoid perfect lock-step
        time.sleep(0.05)


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

def main() -> None:
    args = parse_args()
    print("=" * 60)
    print("  SpotyBoys Load Test")
    print("=" * 60)
    print(f"  Base URL:  {args.base_url}")
    print(f"  Users:     {args.users}")
    print(f"  Duration:  {args.duration_seconds}s")
    print(f"  Started:   {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Authenticate to get cookie + token
    auth_session = authenticate(args.base_url, args.email, args.password)
    cookie_jar = dict(auth_session.cookies)
    token = auth_session.headers.get("Authorization", "").removeprefix("Bearer ").strip()

    stats = LoadTestStats()
    stop_event = threading.Event()
    threads: List[threading.Thread] = []

    print(f"\n[START] Spawning {args.users} workers for {args.duration_seconds}s ...\n")
    t_start = time.monotonic()

    for i in range(args.users):
        t = threading.Thread(
            target=worker,
            args=(i, args.base_url, cookie_jar, token, args.duration_seconds, stats, stop_event),
            daemon=True,
        )
        threads.append(t)
        t.start()

    # Progress reporting
    try:
        while time.monotonic() - t_start < args.duration_seconds:
            time.sleep(10)
            elapsed = int(time.monotonic() - t_start)
            count = len(stats.results)
            errors = len([r for r in stats.results if r.error or r.status_code >= 400])
            print(f"  [{elapsed:>4}s] requests={count}  errors={errors}")
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Stopping workers ...")
        stop_event.set()

    stop_event.set()
    for t in threads:
        t.join(timeout=5)

    elapsed_total = time.monotonic() - t_start
    print(f"\n[DONE] Completed in {elapsed_total:.1f}s\n")

    # Print summary
    summary = stats.summary()
    print("=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    print(f"  Total requests:  {summary['total_requests']}")
    print(f"  Total errors:    {summary['total_errors']}")
    print(f"  Error rate:      {summary['overall_error_rate']:.2%}")
    print()

    for section in ["bootstrap", "recommend", "combined"]:
        s = summary.get(section, {})
        if not s or s.get("count", 0) == 0:
            continue
        print(f"  --- {s.get('endpoint', section)} ---")
        print(f"    Count:          {s['count']}")
        print(f"    Error rate:     {s['error_rate']:.2%}")
        print(f"    Fallback rate:  {s['fallback_rate']:.2%}")
        print(f"    Degraded count: {s['degraded_count']}")
        print(f"    Latency p50:    {s['latency_p50_ms']:.1f} ms")
        print(f"    Latency p95:    {s['latency_p95_ms']:.1f} ms")
        print(f"    Latency p99:    {s['latency_p99_ms']:.1f} ms")
        print(f"    Latency avg:    {s['latency_avg_ms']:.1f} ms")
        print(f"    Latency min:    {s['latency_min_ms']:.1f} ms")
        print(f"    Latency max:    {s['latency_max_ms']:.1f} ms")
        print()

    print("=" * 60)

    # Dump full JSON
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
        print(f"  JSON saved to: {args.output_json}")
    else:
        print("\n  Full JSON summary:")
        print(json.dumps(summary, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SpotyBoys serving load test for Q2.1 evidence.")
    parser.add_argument("--base-url", required=True, help="Base URL, e.g. http://129.114.25.25:8089")
    parser.add_argument("--email", default="40305@navidrome.local", help="Login email")
    parser.add_argument("--password", default="test123", help="Login password")
    parser.add_argument("--users", type=int, default=10, help="Number of concurrent workers")
    parser.add_argument("--duration-seconds", type=int, default=60, help="Test duration in seconds")
    parser.add_argument("--output-json", default=None, help="Optional path to save JSON summary")
    return parser.parse_args()


def _percentile(values: List[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = round((p / 100) * (len(sorted_values) - 1))
    return sorted_values[index]


if __name__ == "__main__":
    main()
