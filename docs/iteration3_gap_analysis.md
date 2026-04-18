# SpotyBoys Iteration 3 Gap Analysis

## Audio Path Audit

Before iteration 3, local playback still sounded like beeps because the VM1 Compose stack used `SPOTIBOYS_MEDIA_MODE=fixture-file` and `infra/scripts/generate_fixture_music.py` generated WAV tones via `packages/navidrome_adapter/media_access.py::build_demo_wav_bytes`. Navidrome was present and scanned `/music`, but the recommendation API streamed local generated files rather than requiring the internal Navidrome stream API.

Iteration 3 changes the default local mode to `SPOTIBOYS_MEDIA_MODE=navidrome_fixture`. The tone generator remains only behind the explicit non-default `fixture_beep` test/debug mode.

## Navidrome Usage Audit

Before iteration 3:

- `docker-compose.yml` ran Navidrome and mounted a generated fixture library.
- `workers/catalog-sync-worker/sync_catalog.py` trusted static `nav_001` style IDs from `fixtures/demo_catalog.json`.
- `/stream/{track_id}` did not require a successful Navidrome Subsonic stream response.

After iteration 3:

- `infra/scripts/bootstrap_navidrome.py` creates or verifies the internal Subsonic user.
- `workers/catalog-sync-worker/sync_catalog.py` searches Navidrome through `/rest/search3.view` and writes actual Navidrome media IDs into `navidrome_track_mapping`.
- `packages/navidrome_adapter/media_access.py` proxies `/rest/stream.view` for `navidrome_fixture`, `navidrome_vm_library`, and `navidrome` modes.

## Serving-Path Mismatches Fixed

- Default local streaming no longer uses generated beep files.
- Local fixture media comes from real MP3 preview files under `data/raw/audio_previews`.
- Navidrome is in the local default stream path.
- Canonical IDs are reconciled to actual Navidrome IDs before the recommendation API starts.
- Missing/quarantined mappings still fail closed.
- Frontend continues to see only `/stream/{track_id}`.

## Remaining Risks

- Local real fixture audio depends on `data/raw/audio_previews` being present in the local checkout. VM mode does not depend on this path.
- The Navidrome reconciliation is intentionally simple: it searches by canonical `track_id` marker and then title. VM deployment should provide a stronger catalog/mapping source if filenames do not include canonical IDs.
- The local C3 ranker is a lightweight fixture ranker loaded from the approved `gru_ranker.pt` artifact path. It proves serving-path invocation and ordering influence, but production GRU tensor inference remains iteration 4 work.
