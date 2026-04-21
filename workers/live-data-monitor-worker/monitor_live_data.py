from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from packages.config import load_config
from packages.db_access.postgres import PostgresRepository

# Drift thresholds — justified in comments next to each check below.
_SKIP_RATE_DRIFT_MAX        = 0.20   # >20 pp shift in skip rate suggests model degradation
_COMPLETION_RATE_DRIFT_MAX  = 0.20   # symmetric guard on positive signal
_LIKE_RATE_DRIFT_MAX        = 0.10   # likes are rarer so a smaller absolute shift matters
_REJECTION_RATE_MAX         = 0.05   # >5 % bad events at ingestion signals a client/pipeline bug
_DIVERSITY_TOP1_MAX         = 0.30   # one track taking >30 % of all impressions = popularity collapse
_DIVERSITY_UNIQUE_RATE_MIN  = 0.10   # <10 % unique tracks in impressions = catalog coverage collapse


def monitor_live_data(window_hours: int = 24) -> Path:
    config = load_config()
    repo = PostgresRepository(config.database_url)
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    with repo._connect() as conn:
        behavioral = _query_behavioral(conn, since)
        data_quality = _query_data_quality(conn, since)

    metrics = {**behavioral["metrics"], **data_quality["metrics"]}

    if behavioral["metrics"]["recent_impression_count"] == 0 \
            and behavioral["metrics"]["recent_playback_count"] == 0 \
            and behavioral["metrics"]["recent_feedback_count"] == 0:
        payload = {
            "stage": "live_monitoring",
            "status": "insufficient_data",
            "created_at": _now_iso(),
            "window_hours": window_hours,
            "metrics": metrics,
            "baseline": {"skip_rate": _baselines(config.object_storage_root)["skip_rate"]},
            "drift_checks": {},
            "notes": ["Not enough recent data to evaluate live quality or drift."],
        }
        return _write_report(config.object_storage_root, payload)

    # ── behavioral drift ──────────────────────────────────────────────────────
    bm = behavioral["metrics"]
    baselines = _baselines(config.object_storage_root)
    skip_rate_diff        = abs(bm["skip_rate"]       - baselines["skip_rate"])
    completion_rate_diff  = abs(bm["completion_rate"] - baselines["completion_rate"])
    like_rate_diff        = abs(bm["like_rate"]       - baselines["like_rate"])

    notes: list[str] = []
    status = "ok"

    if skip_rate_diff > _SKIP_RATE_DRIFT_MAX:
        status = "warning"
        notes.append(
            f"skip_rate drift {skip_rate_diff:.3f} exceeded {_SKIP_RATE_DRIFT_MAX} "
            "(model may be degrading or user population shifted)"
        )
    if completion_rate_diff > _COMPLETION_RATE_DRIFT_MAX:
        status = "warning"
        notes.append(
            f"completion_rate drift {completion_rate_diff:.3f} exceeded {_COMPLETION_RATE_DRIFT_MAX}"
        )
    if like_rate_diff > _LIKE_RATE_DRIFT_MAX:
        status = "warning"
        notes.append(
            f"like_rate drift {like_rate_diff:.3f} exceeded {_LIKE_RATE_DRIFT_MAX}"
        )

    # ── inference data quality checks ────────────────────────────────────────
    dq = data_quality["metrics"]

    rejection_rate = dq.get("ingestion_rejection_rate", 0.0)
    if rejection_rate > _REJECTION_RATE_MAX:
        status = "warning"
        notes.append(
            f"ingestion_rejection_rate={rejection_rate:.4f} exceeded {_REJECTION_RATE_MAX} "
            "(client is sending malformed events; check event-api logs)"
        )

    top1_share = dq.get("recommendation_top1_track_share", 0.0)
    if top1_share > _DIVERSITY_TOP1_MAX:
        status = "warning"
        notes.append(
            f"recommendation_top1_track_share={top1_share:.3f} exceeded {_DIVERSITY_TOP1_MAX} "
            "(model is collapsing recommendations to a single track)"
        )

    unique_rate = dq.get("recommendation_unique_track_rate", 1.0)
    if unique_rate < _DIVERSITY_UNIQUE_RATE_MIN:
        status = "warning"
        notes.append(
            f"recommendation_unique_track_rate={unique_rate:.3f} below {_DIVERSITY_UNIQUE_RATE_MIN} "
            "(catalog coverage collapsed; model over-concentrating on popular items)"
        )

    payload = {
        "stage": "live_monitoring",
        "status": status,
        "created_at": _now_iso(),
        "window_hours": window_hours,
        "metrics": metrics,
        "baseline": baselines,
        "drift_checks": {
            "skip_rate_abs_diff":       skip_rate_diff,
            "completion_rate_abs_diff": completion_rate_diff,
            "like_rate_abs_diff":       like_rate_diff,
        },
        "thresholds": {
            "skip_rate_drift_max":              _SKIP_RATE_DRIFT_MAX,
            "completion_rate_drift_max":        _COMPLETION_RATE_DRIFT_MAX,
            "like_rate_drift_max":              _LIKE_RATE_DRIFT_MAX,
            "ingestion_rejection_rate_max":     _REJECTION_RATE_MAX,
            "recommendation_top1_track_max":    _DIVERSITY_TOP1_MAX,
            "recommendation_unique_rate_min":   _DIVERSITY_UNIQUE_RATE_MIN,
        },
        "notes": notes,
    }
    return _write_report(config.object_storage_root, payload)


# ── behavioral metrics ────────────────────────────────────────────────────────

def _query_behavioral(conn, since: datetime) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM app.recommendation_impressions WHERE created_at >= %s",
            (since,),
        )
        impression_count = int(cur.fetchone()[0])

        cur.execute(
            "SELECT event_type, COUNT(*) FROM app.playback_events WHERE occurred_at >= %s GROUP BY event_type",
            (since,),
        )
        playback_counts = {str(et): int(c) for et, c in cur.fetchall()}

        cur.execute(
            "SELECT feedback_type, COUNT(*) FROM app.feedback_events WHERE occurred_at >= %s GROUP BY feedback_type",
            (since,),
        )
        feedback_counts = {str(ft): int(c) for ft, c in cur.fetchall()}

    playback_total  = sum(playback_counts.values())
    feedback_total  = sum(feedback_counts.values())
    completion_count = playback_counts.get("complete", 0)
    skip_count       = playback_counts.get("skip", 0)
    like_count       = feedback_counts.get("like", 0)
    dislike_count    = feedback_counts.get("dislike", 0)

    return {
        "metrics": {
            "recent_impression_count": impression_count,
            "recent_playback_count":   playback_total,
            "recent_feedback_count":   feedback_total,
            "completion_rate":  completion_count / max(playback_total, 1),
            "skip_rate":        skip_count       / max(playback_total, 1),
            "like_rate":        like_count       / max(impression_count, 1),
            "dislike_rate":     dislike_count    / max(impression_count, 1),
        }
    }


# ── inference data quality metrics ───────────────────────────────────────────

def _query_data_quality(conn, since: datetime) -> dict:
    """
    Measures data quality of the live inference stream itself:

    1. ingestion_rejection_rate
       Fraction of incoming events rejected by the ingestion quality gate
       (from app.ingestion_rejections).  A rising rate signals client bugs or
       upstream pipeline changes that are corrupting event payloads.

    2. recommendation_top1_track_share
       Share of all recommended track slots taken by the single most-recommended
       track in the window.  A value above 30 % indicates the model is
       collapsing toward a single popular item (popularity bias / diversity loss).

    3. recommendation_unique_track_rate
       Unique tracks recommended / total track slots recommended.
       Below 10 % means the recommendation catalog has effectively shrunk to a
       tiny set, which destroys personalization.
    """
    with conn.cursor() as cur:
        # ── ingestion rejection rate ──────────────────────────────────────────
        cur.execute(
            "SELECT COUNT(*) FROM app.ingestion_rejections WHERE created_at >= %s",
            (since,),
        )
        rejection_count = int(cur.fetchone()[0])

        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT impression_id FROM app.rendered_impressions WHERE received_at >= %s
                UNION ALL
                SELECT event_id FROM app.playback_events WHERE occurred_at >= %s
                UNION ALL
                SELECT event_id FROM app.feedback_events WHERE occurred_at >= %s
            ) t
            """,
            (since, since, since),
        )
        total_events = int(cur.fetchone()[0]) + rejection_count
        rejection_rate = rejection_count / max(total_events, 1)

        # ── recommendation diversity (from rendered_impressions.visible_items_json) ──
        cur.execute(
            """
            SELECT visible_items_json
            FROM app.rendered_impressions
            WHERE received_at >= %s
            """,
            (since,),
        )
        rows = cur.fetchall()

    total_slots = 0
    track_counts: dict[str, int] = {}
    for (visible_items_json,) in rows:
        if not visible_items_json:
            continue
        items = visible_items_json if isinstance(visible_items_json, list) else []
        for item in items:
            tid = item.get("track_id") if isinstance(item, dict) else None
            if tid:
                track_counts[tid] = track_counts.get(tid, 0) + 1
                total_slots += 1

    if total_slots == 0:
        top1_share        = 0.0
        unique_track_rate = 1.0
    else:
        top1_count        = max(track_counts.values()) if track_counts else 0
        top1_share        = top1_count / total_slots
        unique_track_rate = len(track_counts) / total_slots

    return {
        "metrics": {
            "ingestion_rejection_count":    rejection_count,
            "ingestion_rejection_rate":     rejection_rate,
            "recommendation_total_slots":   total_slots,
            "recommendation_unique_tracks": len(track_counts),
            "recommendation_top1_track_share":   top1_share,
            "recommendation_unique_track_rate":  unique_track_rate,
        }
    }


# ── baseline helpers ──────────────────────────────────────────────────────────

def _baselines(root: Path) -> dict:
    defaults = {
        "skip_rate":        float(os.environ.get("SPOTIBOYS_BASELINE_SKIP_RATE",        "0.20")),
        "completion_rate":  float(os.environ.get("SPOTIBOYS_BASELINE_COMPLETION_RATE",  "0.50")),
        "like_rate":        float(os.environ.get("SPOTIBOYS_BASELINE_LIKE_RATE",        "0.05")),
    }
    previous = sorted((root / "quality_reports" / "live").glob("*.json"))
    if previous:
        try:
            payload = json.loads(previous[-1].read_text(encoding="utf-8"))
            metrics = payload.get("metrics", {})
            for key in defaults:
                val = metrics.get(key)
                if val is not None:
                    defaults[key] = float(val)
        except Exception:
            pass
    return defaults


def _baseline_skip_rate(root: Path) -> float:
    return _baselines(root)["skip_rate"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_report(root: Path, payload: dict) -> Path:
    report_dir = root / "quality_reports" / "live"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"{stamp}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    import logging
    import time

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    logger.info("Live data monitor started (hourly loop)")
    while True:
        try:
            path = monitor_live_data()
            logger.info("Quality report written: %s", path)
        except Exception as exc:
            logger.error("Monitor cycle failed: %s", exc, exc_info=True)
        time.sleep(3600)
