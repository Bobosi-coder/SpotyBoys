# SpotyBoys Iteration 2 Validation Runbook

## Start VM1 Local Serving

```bash
bash infra/scripts/demo_up.sh compose
```

Product URL:

```text
http://127.0.0.1:5173
```

## Seed And Reconcile

```bash
bash infra/scripts/seed_demo_data.sh
```

This generates fixture audio and runs the catalog-sync worker against the seeded canonical catalog.

## Health Check

```bash
bash infra/scripts/healthcheck_demo.sh
```

The health check validates:

- proxy health,
- recommendation API health/readiness,
- event API health,
- bootstrap contract,
- playable-track resolution,
- mapped stream success,
- unmapped stream fail-closed,
- recommendation caps,
- event idempotency.

## Validate Bundle Readiness

```bash
python3 infra/scripts/validate_serving_bundle.py fixtures/serving_bundle/Real_service/demo-fixture-v1/manifest.json
```

Readiness must reject any FAISS or `.index` runtime artifact.

## Validate Parser Export

```bash
PYTHONPATH=. python3 workers/parser-export-worker/export_delta.py
python3 infra/scripts/validate_delta_manifest.py .local/object_storage/proj23-mlflow-artifacts/session_event/delta/*/manifest.json
```

The parser export command reads VM1 PostgreSQL tables and writes the object-storage delta contract.

## Start Full VM1 Plus VM2 Mimic

```bash
docker compose -f docker-compose.full.yml up --build -d
```

VM2 services exist only for retraining handoff validation. Serving must continue if VM2 is stopped.

## Switch To VM Library Mode

Set these values in the VM environment:

```bash
SPOTIBOYS_MEDIA_MODE=navidrome
SPOTIBOYS_MUSIC_ROOT=/mnt/spotiboys/music
NAVIDROME_BASE_URL=http://navidrome:4533
SPOTIBOYS_SERVING_BUNDLE_PATH=/srv/spotiboys/Real_service/current
```

Then restart the VM1 Compose stack. No frontend code changes are required.

## Failure-Mode Checks

- Stop Redis: recommendations should remain playable-only and report runtime degradation if queue state resets.
- Stop Navidrome in `navidrome` mode: `/stream/{track_id}` should fail with media unavailable behavior, not fake audio.
- Request `/stream/trk_missing`: must return 404.
- Add a FAISS artifact to the serving manifest: validation must fail.
