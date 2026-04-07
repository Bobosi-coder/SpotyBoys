# Data Team Presentation Runbook

This runbook is the shortest practical guide for presenting the current completed scope of the data-team work.

## Current Scope

The following parts are presentation-ready:

- high-level data design document
- object-storage versioning and manifests
- versioned ranker release
- data generator hitting hypothetical endpoints
- online feature computation path for one end-to-end request

The following part is intentionally excluded from the current presentation:

- batch pipeline that compiles versioned train/eval datasets from production data

## Requirement Status

| Requirement | Status | Evidence |
|---|---|---|
| High-level data design document | Done | `doc/data_team_initial_implement_plan.md` |
| Live object storage bucket | Mostly done | versioned prefixes + manifests in object storage |
| Reproducible ingest/transform pipeline | Mostly done | release scripts for item2vec / retriever / ranker |
| Data generator hitting endpoints | Done | generator-side + endpoint-side JSONL logs |
| Online feature computation path | Done | one-request demo output under `artifacts/online_feature_demo/` |
| Batch pipeline from production logs | Pending | not included in current presentation |

## Files To Show In The Presentation

### 1. Design and versioning

Show:

- `doc/data_team_initial_implement_plan.md`
- one `manifest.json` from object storage

Talk track:

- repositories used
- what data is stored in each repository
- how the releases are versioned
- how manifests capture lineage and transformation outputs

### 2. Release evidence

Show:

- object storage prefix for one successful release
- the corresponding `manifest.json`

Best example:

- `datasets/ranker/demo-v20260406-ranker/manifest.json`

Talk track:

- processed/features/datasets are published under immutable versioned prefixes
- manifests are stored both inside the release prefix and under a central manifest prefix

### 3. Generator evidence

Show:

- `artifacts/generator/<run_name>/run_summary.json`
- `artifacts/mock_service/recommend_logs.jsonl`
- `wc -l` output for the three endpoint-side logs

Talk track:

- the generator uses realistic seed contexts from `ranker_val.parquet`
- it repeatedly hits `/recommend`, `/impression`, and `/outcome`
- the mock service records endpoint-side logs

### 4. Online feature evidence

Show:

- `artifacts/online_feature_demo/<run_name>/request.json`
- `artifacts/online_feature_demo/<run_name>/feature_summary.json`
- `artifacts/online_feature_demo/<run_name>/response.json`

Talk track:

- one live request enters `/recommend`
- online features are computed from the request context
- the endpoint returns top-k recommendations

## Recommended Demo Order

### Step 1. Show the design document

Open:

- `doc/data_team_initial_implement_plan.md`

Highlight:

- object storage prefixes
- versioning rules
- manifest contract
- flow diagrams

### Step 2. Show release evidence

Show:

- bucket prefix
- `manifest.json`

Recommended talking point:

- "This demonstrates that our training-ready artifacts are versioned and trackable in object storage."

### Step 3. Show the generator workflow

Run:

```bash
bash scripts/run_mock_recommendation_server.sh
curl http://localhost:8001/health
bash scripts/run_ranker_seed_generator.sh --max-requests 25 --top-k 5
```

Then show:

```bash
wc -l artifacts/mock_service/recommend_logs.jsonl
wc -l artifacts/mock_service/impression_logs.jsonl
wc -l artifacts/mock_service/outcome_logs.jsonl
```

### Step 4. Show the online feature workflow

Run:

```bash
bash scripts/run_online_feature_demo.sh
```

Then show:

- `feature_summary.json`
- `response.json`

Recommended talking point:

- "This is our separate online feature artifact. It demonstrates a real-time inference path for a single request, distinct from the repeated-traffic generator."

## Files To Keep Handy During The Demo

- `doc/data_team_initial_implement_plan.md`
- `doc/generator_demo_script_draft.md`
- `doc/online_feature_pipeline_report_draft.md`
- `README.md`

## Suggested Final Message

If time is short, the safest summary is:

> We completed the data design, object-storage versioning, generator endpoint demo, and online feature inference demo. The remaining planned extension is the production-log-based batch training dataset compiler.
