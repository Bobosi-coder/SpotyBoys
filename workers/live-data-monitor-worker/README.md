# Live Data Monitor Worker

Minimum data-role worker for the Apr 20 milestone.

Purpose:

- monitor live inference/event data quality in production
- compute one simple drift signal
- write a JSON report for later review or automation

Inputs:

- `app.recommendation_impressions`
- `app.playback_events`
- `app.feedback_events`

Window:

- default: last 24 hours

Metrics:

- `recent_impression_count`
- `recent_playback_count`
- `recent_feedback_count`
- `completion_rate`
- `skip_rate`
- `like_rate`
- `dislike_rate`

Drift check:

- compares current `skip_rate` against a baseline skip rate
- baseline comes from the latest previous live report when available
- fallback baseline comes from `SPOTIBOYS_BASELINE_SKIP_RATE`
- warning threshold: absolute difference greater than `0.20`

Output:

- JSON report under:
  - `<object_storage_root>/quality_reports/live/<timestamp>.json`

Statuses:

- `ok`
- `warning`
- `insufficient_data`

Run manually:

```bash
python workers/live-data-monitor-worker/monitor_live_data.py
```
