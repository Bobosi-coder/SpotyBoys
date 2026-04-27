# Data Requirements Minimum Implementation Plan

This document defines the smallest reasonable implementation scope for the data-role requirements in the Apr 20 milestone, and breaks the work into concrete step-by-step tasks.

Audience:

- data-role owner
- teammates reviewing what is and is not included

Team assumption:

- 3-person team

Goal:

- satisfy the three data requirements with the minimum amount of new code
- avoid building a large data platform
- produce something that is easy to explain in the final demo and report

---

## 1. What You Need To Implement

The data-role requirements ask for three things:

1. data quality evaluation at ingestion from external data sources
2. data quality evaluation when compiling retraining sets
3. live inference data quality and drift monitoring in production

The minimum viable interpretation is:

- add one ingestion quality report
- add one retraining-set quality report plus one simple quality gate
- add one live monitoring worker that computes a few online data-quality and drift metrics

That is enough to demonstrate:

- you evaluate data at 3 different points
- you have measurable thresholds
- the system can detect bad data automatically

---

## 2. Exact Scope

### In Scope

- add ingestion quality metrics to `workers/catalog-sync-worker/sync_catalog.py`
- save an ingestion quality report as JSON
- add retraining data quality metrics to `workers/parser-export-worker/export_delta.py`
- save a retraining quality report as JSON
- block retraining trigger if retraining data quality fails
- add one live-monitoring worker for recent production inference/event data
- save one live monitoring report as JSON
- implement 1 simple drift check
- document the metrics, thresholds, and actions

### Out of Scope

Do not do these unless there is extra time:

- no Grafana / Prometheus / full monitoring stack
- no ML-based drift detector
- no dashboards
- no email / Slack alert integration
- no new object storage protocol design
- no large schema redesign
- no feature store
- no Kubernetes-only data infra
- no fairness/explainability expansion inside the data task

---

## 3. Files You Should Touch

### Required edits

- `workers/catalog-sync-worker/sync_catalog.py`
- `workers/parser-export-worker/export_delta.py`
- `workers/delta-trigger-worker/trigger_delta_export.py`

### Required new files

- `workers/live-data-monitor-worker/monitor_live_data.py`
- `workers/live-data-monitor-worker/README.md`
- `docs/data_requirements_minimum_plan.md`

### Optional but useful

- `docker-compose.yml`
  - only if you want to wire the live monitor as a service/profile

---

## 4. Minimum Metrics To Implement

Keep the metrics small and easy to explain.

### A. Ingestion quality metrics

Implement only these:

- `total_rows`
- `missing_required_rate`
- `duplicate_track_id_rate`
- `mapping_success_rate`
- `quarantine_rate`

Required fields:

- `track_id`
- `title`
- `artist`

Recommended thresholds:

- `missing_required_rate <= 0.01`
- `duplicate_track_id_rate <= 0.01`
- `mapping_success_rate >= 0.90`
- `quarantine_rate <= 0.10`

Output:

- JSON report saved locally under object-storage-style path
- example path:
  - `.local/object_storage/quality_reports/ingestion/<timestamp>.json`
  - or `/object-storage/quality_reports/ingestion/<timestamp>.json` inside containers

Result:

- if thresholds fail, mark report status as `fail`
- ingestion worker may still complete successfully
- do not overcomplicate this with hard pipeline stops unless the team wants that

Why this is enough:

- it proves ingestion is being evaluated
- it gives clear quality metrics with thresholds
- it is easy to demonstrate in a report

### B. Retraining-set quality metrics

Implement only these:

- `session_tracks_rows`
- `session_meta_rows`
- `love_rows`
- `users_rows`
- `positive_label_rate`
- `neutral_label_rate`
- `skip_label_rate`
- `unique_user_count`
- `unique_track_count`

Recommended thresholds:

- `session_tracks_rows > 0`
- `session_meta_rows > 0`
- `users_rows > 0`
- `positive_label_rate >= 0.05`
- `skip_label_rate <= 0.95`

Output:

- JSON report stored next to the delta export
- example:
  - `session_event/delta/<version>/quality_report.json`

Result:

- if thresholds fail, mark report status as `fail`
- `trigger_delta_export.py` must not trigger Airflow retraining when status is `fail`

Why this is enough:

- it proves the retraining dataset is evaluated
- it adds an actual quality gate
- it is the strongest single improvement for the data-role rubric

### C. Live inference quality and drift metrics

Implement only these:

- `recent_impression_count`
- `recent_playback_count`
- `recent_feedback_count`
- `completion_rate`
- `skip_rate`
- `like_rate`
- `dislike_rate`

For drift, implement only one simple check:

- compare current `skip_rate` against a baseline `skip_rate`

Simple baseline choice:

- hardcode a baseline in the worker for now
- or read the latest previous monitoring report if available

Recommended drift rule:

- warning if `abs(current_skip_rate - baseline_skip_rate) > 0.20`

Output:

- JSON report saved under:
  - `quality_reports/live/<timestamp>.json`

Result:

- set report status:
  - `ok`
  - `warning`
  - `fail`

Minimum logic:

- if no data exists yet, output `status = "insufficient_data"`

Why this is enough:

- it satisfies both live quality monitoring and drift monitoring
- it stays extremely lightweight

---

## 5. Step-By-Step Implementation Plan

Follow these steps in order.

### Step 1. Add ingestion quality reporting

Target file:

- `workers/catalog-sync-worker/sync_catalog.py`

What to add:

- count total input rows
- count rows missing any required field
- count duplicate `track_id` values
- count successfully mapped rows
- count quarantined rows
- calculate rates
- write a JSON report file

Implementation notes:

- keep the report generation inside the worker
- use a small helper function like:
  - `_build_ingestion_quality_report(rows, synced, ...)`
  - `_write_quality_report(path, payload)`

Definition of success:

- worker still syncs catalog
- report file is always written
- report contains thresholds and final `status`

### Step 2. Add retraining dataset quality reporting

Target file:

- `workers/parser-export-worker/export_delta.py`

What to add:

- after parquet files are written, inspect the exported dataset
- compute row counts
- compute label distribution from `session_tracks_addition.parquet`
- compute unique users and tracks
- apply simple thresholds
- write `quality_report.json`

Implementation notes:

- reuse `pyarrow` or simple parquet reads
- keep this validator close to the export logic
- add a helper like:
  - `validate_exported_delta_quality(output_dir) -> dict`

Definition of success:

- every delta export creates a `quality_report.json`
- report contains `status`
- report clearly says whether retraining should continue

### Step 3. Gate the retraining trigger on the quality report

Target file:

- `workers/delta-trigger-worker/trigger_delta_export.py`

What to add:

- after `export_delta()` completes, read `quality_report.json`
- if report status is `fail`, log the reason and do not call Airflow
- only trigger Airflow when report status is `ok`

Definition of success:

- bad data can stop retraining automatically
- the logic is easy to explain in the final report

### Step 4. Add live inference quality/drift monitoring

Target files:

- `workers/live-data-monitor-worker/monitor_live_data.py`
- `workers/live-data-monitor-worker/README.md`

What to build:

- one simple Python worker
- it queries recent rows from:
  - `app.recommendation_impressions`
  - `app.playback_events`
  - `app.feedback_events`
- it computes:
  - counts
  - completion rate
  - skip rate
  - like rate
  - dislike rate
- it compares current skip rate to a baseline
- it writes one JSON report

Implementation notes:

- use a recent time window like last 24 hours
- keep baseline logic simple
- if there is not enough data, return `insufficient_data`

Definition of success:

- worker can be run manually
- it always produces a report
- it contains at least one drift result

### Step 5. Add a tiny runbook note

Target:

- `workers/live-data-monitor-worker/README.md`
- optionally `README.md` or another docs file later

What to document:

- which metrics are checked at each of the 3 points
- thresholds
- what happens on failure/warning

Definition of success:

- your teammate or TA can read one file and understand your data-quality story

---

## 6. Suggested Data Structures

To keep everything consistent, use a simple JSON structure like this.

```json
{
  "stage": "ingestion",
  "status": "ok",
  "created_at": "2026-04-20T12:00:00Z",
  "metrics": {
    "total_rows": 1000,
    "missing_required_rate": 0.002,
    "duplicate_track_id_rate": 0.0,
    "mapping_success_rate": 0.97,
    "quarantine_rate": 0.01
  },
  "thresholds": {
    "missing_required_rate_max": 0.01,
    "duplicate_track_id_rate_max": 0.01,
    "mapping_success_rate_min": 0.90,
    "quarantine_rate_max": 0.10
  },
  "notes": []
}
```

For retraining:

```json
{
  "stage": "retraining_dataset",
  "status": "ok",
  "created_at": "2026-04-20T12:00:00Z",
  "metrics": {
    "session_tracks_rows": 1200,
    "session_meta_rows": 300,
    "love_rows": 40,
    "users_rows": 280,
    "positive_label_rate": 0.21,
    "neutral_label_rate": 0.33,
    "skip_label_rate": 0.46,
    "unique_user_count": 280,
    "unique_track_count": 500
  },
  "thresholds": {
    "session_tracks_rows_min": 1,
    "session_meta_rows_min": 1,
    "users_rows_min": 1,
    "positive_label_rate_min": 0.05,
    "skip_label_rate_max": 0.95
  },
  "notes": []
}
```

For live monitoring:

```json
{
  "stage": "live_monitoring",
  "status": "warning",
  "created_at": "2026-04-20T12:00:00Z",
  "window_hours": 24,
  "metrics": {
    "recent_impression_count": 300,
    "recent_playback_count": 220,
    "recent_feedback_count": 15,
    "completion_rate": 0.42,
    "skip_rate": 0.48,
    "like_rate": 0.03,
    "dislike_rate": 0.02
  },
  "baseline": {
    "skip_rate": 0.20
  },
  "drift_checks": {
    "skip_rate_abs_diff": 0.28
  },
  "notes": [
    "skip_rate drift exceeded threshold 0.20"
  ]
}
```

---

## 7. Exact Definition Of Done

You are done when all of these are true:

### Requirement 1: Ingestion quality

- `sync_catalog.py` writes an ingestion quality JSON report
- the report includes metrics, thresholds, and status

### Requirement 2: Retraining-set quality

- `export_delta.py` writes a retraining quality JSON report
- `trigger_delta_export.py` blocks Airflow retraining if the report status is `fail`

### Requirement 3: Live inference quality + drift

- `monitor_live_data.py` exists
- it reads recent production event data from Postgres
- it writes a live monitoring report
- it includes at least one drift check

### Documentation

- `workers/live-data-monitor-worker/README.md` explains the metrics, thresholds, and actions

---

## 8. What You Should Say In The Demo Or Report

Use this framing:

1. At ingestion, we evaluate incoming catalog quality using missing-field rate, duplicate rate, mapping success rate, and quarantine rate.
2. When generating retraining data, we evaluate dataset size and label distribution, and retraining is blocked if the exported data fails quality thresholds.
3. In production, we monitor recent inference/event data quality and track simple drift using skip-rate deviation from baseline.

That is a strong and concise data-role story.

---

## 9. Recommended Implementation Order

If time is tight, do the work in this exact order:

1. retraining quality report + gate
2. ingestion quality report
3. live monitoring worker
4. README / documentation cleanup

Reason:

- the retraining gate gives the strongest rubric value first
- ingestion is quick to add
- live monitoring is required, but can stay very simple

---

## 10. What Not To Get Stuck On

Avoid spending time on:

- perfect statistical drift methods
- dashboards
- storing reports in a fancy database schema
- very detailed anomaly detection
- alert integrations
- major refactors

The requirement is about showing that the system evaluates data quality at 3 points, not about building a full data platform.
