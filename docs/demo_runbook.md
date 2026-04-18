# SpotiBoys Demo Runbook

## Fast Local Demo

Start the dependency-light same-origin demo gateway:

```bash
bash infra/scripts/demo_up.sh
```

Open:

```text
http://127.0.0.1:5173/?gateway=1
```

Health check:

```bash
bash infra/scripts/healthcheck_demo.sh
```

Stop:

```bash
bash infra/scripts/demo_down.sh
```

## Container Demo Shape

Primary demo path:

```bash
bash infra/scripts/demo_up.sh compose
```

URLs:

- frontend and same-origin API/media proxy: `http://127.0.0.1:5173/`
- proxy health: `http://127.0.0.1:5173/health`
- recommendation API health through proxy: `http://127.0.0.1:5173/recommendation-health`
- event API health through proxy: `http://127.0.0.1:5173/event-health`

Stop:

```bash
bash infra/scripts/demo_down.sh compose
```

## Fixture Tracks

Known playable fixtures:

- `trk_001`
- `trk_002`
- `trk_003`
- `trk_004`

Fail-closed fixtures:

- `trk_missing`
- `trk_quarantined`

## Stream Checks

Mapped stream:

```text
http://127.0.0.1:5173/stream/trk_001
```

Unmapped stream should return 404:

```text
http://127.0.0.1:5173/stream/trk_missing
```

## Current Limitations

- Docker Compose is the production-faithful demo path and uses Postgres plus Redis internally.
- The fast local gateway remains available for fallback development only and uses deterministic in-memory fixture data.
- Real Navidrome is represented by the media adapter boundary and deterministic WAV bytes.
- Recommender internals are fixture-backed behind the playable-only contract.
