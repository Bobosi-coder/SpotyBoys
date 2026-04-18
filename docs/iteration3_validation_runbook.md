# SpotyBoys Iteration 3 Validation Runbook

## Start Local VM1 Serving

```bash
bash infra/scripts/demo_up.sh compose
```

The product URL is:

```text
http://127.0.0.1:5173
```

## Seed Real Fixture Songs

```bash
python3 infra/scripts/generate_fixture_music.py \
  --fixture fixtures/demo_catalog.json \
  --output-dir .local/fixture_music \
  --source-root data/raw/audio_previews
```

The default command fails if real MP3 previews are missing. Use `--allow-beep-fallback` only for debug tests.

## Bootstrap And Reconcile Navidrome

Compose runs these automatically:

```bash
python3 infra/scripts/bootstrap_navidrome.py
python3 workers/catalog-sync-worker/sync_catalog.py
```

To force a rescan, recreate the Navidrome container or run:

```bash
docker compose -f docker-compose.yml restart navidrome
docker compose -f docker-compose.yml run --rm navidrome-bootstrap
docker compose -f docker-compose.yml run --rm catalog-sync-worker
```

## Verify Real Stream Path

```bash
bash infra/scripts/healthcheck_demo.sh
```

Then in the browser, click play at `http://127.0.0.1:5173`. The frontend receives `/stream/{track_id}` only. The backend resolves the canonical track to a reconciled Navidrome media ID and proxies internal Navidrome bytes.

## Switch To VM Library Mode

Set:

```bash
SPOTIBOYS_MEDIA_MODE=navidrome_vm_library
SPOTIBOYS_MUSIC_ROOT=/mnt/spotiboys/music
NAVIDROME_BASE_URL=http://navidrome:4533
SPOTIBOYS_SERVING_BUNDLE_PATH=/srv/spotiboys/Real_service/current
```

Then restart the VM1 Compose stack. No code changes are required.

## Failure Modes

- Stop Navidrome: `/stream/{track_id}` must fail closed.
- Request `/stream/trk_missing`: must return 404.
- Corrupt the serving manifest or add FAISS: readiness/validation must fail.
- Stop VM2 services: serving must continue.
