# Serving Monitoring Submission Evidence

This document is the submission-facing evidence guide for the Serving team member requirement:

> Monitoring: The serving role must monitor the behavior of the deployed model over time, including model output, operational metrics, and user feedback. This team member is responsible for the triggers that promote a model, or roll back model versions. These triggers should be well-justified given the overall context.

## Architecture Boundary

- VM1 serving owns live monitoring, online rollups, serving-side anomaly detection, and rollback recommendations.
- VM2 retraining owns offline evaluation and actual promotion approval.
- Object storage is the handoff layer. VM1 consumes only manifest-confirmed `Real_service/{version}` bundles.
- PostgreSQL remains durable truth for raw impressions, playback events, feedback, model versions, derived monitoring rollups, and decision logs.
- Monitoring rollups are derived/rebuildable. They do not replace raw event truth.

## Generate Monitoring Rollups

```bash
export COMPOSE_PROJECT_NAME=spotiboys_vm1_demo
export SPOTIBOYS_FRONTEND_PORT=8089

docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  run --rm serving-monitor-worker
```

Expected output includes JSON with `5m` and `1h` rollups:

- `model_output.unique_track_count`
- `model_output.unique_artist_count`
- `model_output.top_artist_share`
- `operational.request_count`
- `operational.recommendation_error_count`
- `operational.stream_failure_count`
- `feedback.playback_start_count`
- `feedback.skip_count`
- `feedback.complete_count`
- `feedback.dislike_count`

## Show Monitoring Summary Endpoint

Authenticate through the app, then:

```bash
curl -b /tmp/spotiboys_cookie.txt \
  http://127.0.0.1:8089/monitoring/summary | python -m json.tool
```

Expected output:

- `active_model`
- `serving_bundle_version`
- `latest_5m_rollup`
- `latest_1h_rollup`
- `latest_promotion_decision`
- `latest_rollback_decision`

This proves the serving stack exposes the deployed model version and monitoring state.

## Query Rollup Tables

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  exec postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
    select window_name, model_version, request_count, fallback_rate,
           stream_failure_rate, completion_rate, dislike_rate,
           top_artist_share, sample_status, created_at
    from app.serving_metric_rollups
    order by created_at desc
    limit 5;
  "'
```

This proves monitoring is time-windowed and persisted, not just logs.

## Promotion Gate Evidence

VM2 owns this decision.

```bash
python jobs/promotion-gate/promotion_gate.py \
  --candidate fixtures/serving_bundle/Real_service/demo-fixture-v1 \
  --eval fixtures/promotion_good.json
```

Expected approved decision:

```json
{
  "decision_type": "promotion",
  "decision": "approve",
  "owner": "VM2_retraining"
}
```

To demonstrate a blocked promotion, remove/copy a bundle with a missing required artifact or provide metrics that regress by more than 2 percent. The expected decision is `block`.

## Rollback Check Evidence

VM1 owns this decision.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  run --rm rollback-check-worker
```

Expected small-sample behavior:

```json
{
  "decision_type": "rollback",
  "decision": "no_action_insufficient_sample"
}
```

If seeded rollups breach guarded thresholds, expected output is:

```json
{
  "decision_type": "rollback",
  "decision": "rollback_recommended"
}
```

Automatic rollback is disabled by default. To stage a rollback marker intentionally:

```bash
SPOTIBOYS_AUTO_ROLLBACK=true \
docker compose \
  -f docker-compose.yml \
  -f docker-compose.vm-library.yml \
  run --rm rollback-check-worker
```

The worker writes `restart_required.json` only when guarded thresholds breach and a previous known-good model is registered.

## Threshold Justification

| metric | minimum sample | threshold | action | rationale |
|---|---:|---:|---|---|
| recommendation error rate | 20 recommendation requests | > 5% | rollback recommended | catches broken serving without reacting to one failed request |
| fallback rate | 50 recommendation requests | > 25% | rollback recommended | indicates model/catalog/runtime degradation |
| stream failure rate | 20 stream attempts | > 5% | rollback recommended or ops warning | playback failure directly harms UX |
| completion-rate drop | 20 active and 20 baseline starts | > 30% relative drop | rollback recommended | avoids noisy decisions from tiny sessions |
| dislike rate | 20 feedback/playback interactions | > 15% | rollback recommended | captures strong negative feedback |
| top artist share | 50 recommended items | > 60% | rollback or policy warning | detects collapsed recommendation diversity |
| repeat violations | 20 recommendation requests | > 0 | rollback recommended | no-repeat is a serving invariant |

These thresholds are intentionally simple and course-project appropriate. They produce auditable decisions without adding enterprise monitoring infrastructure.

## Demo Story

1. Show the app running and the active model in `/recommendation-ready`.
2. Generate recommendations and playback/feedback events.
3. Run `serving-monitor-worker`.
4. Show `/monitoring/summary`.
5. Query `app.serving_metric_rollups`.
6. Run the VM2 promotion gate with approve and block examples.
7. Run the VM1 rollback checker with small-sample and seeded-breach examples.
8. Explain the ownership split: VM2 approves promotion; VM1 monitors live serving and recommends or executes rollback.
