# Generator Demo Script Draft

This draft is meant for presentation preparation and report writing. It focuses only on the current minimum implementation of the generator requirement.

## Requirement Addressed

This draft addresses the following requirement:

> Data generator that hits the (hypothetical) service endpoints with real or synthetic data.

The current implementation uses realistic seed contexts from `ranker_val.parquet` and sends repeated requests to a lightweight local mock recommendation service.

## What This Demo Shows

The demo is intended to show that:

1. the generator can load realistic session seeds from repository artifacts
2. it can repeatedly call hypothetical endpoints
3. the endpoints can record recommendation, impression, and outcome logs
4. the entire flow is runnable non-interactively from saved repository scripts

## Components Used

- `src/data_gen/generate_ranker_seed_traffic.py`
- `scripts/run_ranker_seed_generator.sh`
- `src/service/mock_recommendation_server.py`
- `scripts/run_mock_recommendation_server.sh`

## Demo Commands

### Terminal 1: Start the mock service

```bash
bash scripts/run_mock_recommendation_server.sh
```

### Terminal 2: Health check

```bash
curl http://localhost:8001/health
```

Expected response:

```json
{"status":"ok","time":"..."}
```

### Terminal 2: Run the generator

```bash
bash scripts/run_ranker_seed_generator.sh --max-requests 25 --top-k 5
```

### Terminal 2: Show endpoint-side evidence

```bash
ls artifacts/mock_service
wc -l artifacts/mock_service/recommend_logs.jsonl
wc -l artifacts/mock_service/impression_logs.jsonl
wc -l artifacts/mock_service/outcome_logs.jsonl
```

### Terminal 2: Show generator-side evidence

```bash
find artifacts/generator -maxdepth 2 -type f | sort
```

## Expected Successful Output

### Generator-side output

Under a run directory such as:

```text
artifacts/generator/20260406-232546/
```

Expected files:

- `recommend_requests.jsonl`
- `impression_logs.jsonl`
- `outcome_logs.jsonl`
- `run_summary.json`

### Endpoint-side output

Under:

```text
artifacts/mock_service/
```

Expected files:

- `recommend_logs.jsonl`
- `impression_logs.jsonl`
- `outcome_logs.jsonl`

For a successful run, each endpoint-side log should contain the same number of rows as `--max-requests`.

## Short Presentation Script

In this demo, we use realistic validation contexts from `ranker_val.parquet` as seeds and repeatedly call a hypothetical recommendation service.

First, we launch `run_mock_recommendation_server.sh`, which exposes `/recommend`, `/impression`, and `/outcome` locally. Then we run `run_ranker_seed_generator.sh`, which samples realistic session prefixes and candidate sets from `ranker_val.parquet` and sends them to the endpoints.

We verify success using two groups of outputs. The first group is the generator-side JSONL logs. The second group is the mock-service JSONL logs. If the endpoint-side log counts match the request count, we can confirm that the generator successfully hit the endpoints.

This implementation is not a full production deployment, but it does satisfy the requirement of reproducible hypothetical endpoint traffic generation from repository artifacts.

## Documentation Draft (English)

We implemented a lightweight production-like traffic generator that uses `ranker_val.parquet` as a realistic seed source. Instead of sampling fully synthetic sessions from scratch, the generator reads session prefixes, label histories, and candidate sets from the ranker validation parquet.

For each sampled context, the generator emits three request types:

- recommendation request
- impression log
- outcome log

The generator can send these payloads to hypothetical service endpoints and also saves generator-side JSONL artifacts for reproducibility. For the initial implementation demo, we ran the generator against a local mock service exposing:

- `POST /recommend`
- `POST /impression`
- `POST /outcome`
- `GET /health`

Successful evidence includes:

- a healthy endpoint response from `curl http://localhost:8001/health`
- successful generator completion for `25` requests
- generator-side logs under `artifacts/generator/<run_name>/`
- endpoint-side logs under `artifacts/mock_service/`

This demonstrates that the generator not only creates local payloads, but also successfully hits the hypothetical endpoints and produces endpoint-side outputs.

## Suggested Evidence for Slides

If slide space is limited, show these four things:

1. `curl http://localhost:8001/health`
2. `bash scripts/run_ranker_seed_generator.sh --max-requests 25 --top-k 5`
3. `cat artifacts/generator/<run_name>/run_summary.json`
4. `wc -l artifacts/mock_service/recommend_logs.jsonl`

## Current Scope

This draft intentionally covers only the generator requirement. It does not attempt to document the batch pipeline, and it does not replace the separate online feature pipeline report.
