# SpotyBoys Iteration 3 Execution Plan

## Implementation Order

1. Audit generated/beep audio usage and Navidrome bypasses.
2. Make `navidrome_fixture` the default local media mode.
3. Generate local fixture music from real MP3 preview files, not synthetic WAV tones.
4. Bootstrap the internal Navidrome user before catalog reconciliation.
5. Reconcile canonical track IDs to actual Navidrome media IDs through the Subsonic API.
6. Proxy `/stream/{track_id}` through Navidrome for both local fixture and VM library modes.
7. Add C1/C2/C3/C4 model-stack verification and tests.
8. Validate through unit tests and Docker Compose health checks.

## Media Modes

- `navidrome_fixture`: default local mode. Uses tiny real local MP3 files mounted into Navidrome.
- `navidrome_vm_library`: VM mode. Uses the real VM1 mounted music library and the same proxy code path.
- `fixture_beep`: explicit non-default debug/test mode only.

## Env Contract

- `SPOTIBOYS_MEDIA_MODE=navidrome_fixture`
- `SPOTIBOYS_MUSIC_ROOT=/music`
- `NAVIDROME_BASE_URL=http://navidrome:4533`
- `NAVIDROME_USERNAME=spotiboys`
- `NAVIDROME_PASSWORD=spotiboys`
- `SPOTIBOYS_SERVING_BUNDLE_PATH=/app/fixtures/serving_bundle/Real_service/demo-fixture-v1`

VM mode changes only the mounted music root, Navidrome credentials, and serving bundle path.

## Acceptance Criteria

- Local default mode plays real MP3-derived audio through Navidrome.
- `/stream/{track_id}` fails if Navidrome is unavailable.
- Recommendations are playable-only.
- C2 retrieval, C3 ranker, and C4 policy all execute in the recommendation path.
- C1 remains offline artifact input, not online recomputation.
