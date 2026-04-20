from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from packages.config import load_config
from packages.db_access.postgres import PostgresRepository


def monitor_live_data(window_hours: int = 24) -> Path:
    config = load_config()
    repo = PostgresRepository(config.database_url)
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM app.recommendation_impressions
                WHERE created_at >= %s
                """,
                (since,),
            )
            impression_count = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT event_type, COUNT(*)
                FROM app.playback_events
                WHERE occurred_at >= %s
                GROUP BY event_type
                """,
                (since,),
            )
            playback_counts = {str(event_type): int(count) for event_type, count in cur.fetchall()}

            cur.execute(
                """
                SELECT feedback_type, COUNT(*)
                FROM app.feedback_events
                WHERE occurred_at >= %s
                GROUP BY feedback_type
                """,
                (since,),
            )
            feedback_counts = {str(feedback_type): int(count) for feedback_type, count in cur.fetchall()}

    playback_total = sum(playback_counts.values())
    feedback_total = sum(feedback_counts.values())
    completion_count = playback_counts.get("complete", 0)
    skip_count = playback_counts.get("skip", 0)
    like_count = feedback_counts.get("like", 0)
    dislike_count = feedback_counts.get("dislike", 0)

    if impression_count == 0 and playback_total == 0 and feedback_total == 0:
        payload = {
            "stage": "live_monitoring",
            "status": "insufficient_data",
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "window_hours": window_hours,
            "metrics": {
                "recent_impression_count": impression_count,
                "recent_playback_count": playback_total,
                "recent_feedback_count": feedback_total,
                "completion_rate": 0.0,
                "skip_rate": 0.0,
                "like_rate": 0.0,
                "dislike_rate": 0.0,
            },
            "baseline": {"skip_rate": _baseline_skip_rate(config.object_storage_root)},
            "drift_checks": {},
            "notes": ["Not enough recent data to evaluate live quality or drift."],
        }
        return _write_report(config.object_storage_root, payload)

    skip_rate = skip_count / max(playback_total, 1)
    baseline_skip_rate = _baseline_skip_rate(config.object_storage_root)
    skip_rate_abs_diff = abs(skip_rate - baseline_skip_rate)

    notes: list[str] = []
    status = "ok"
    if skip_rate_abs_diff > 0.20:
        status = "warning"
        notes.append("skip_rate drift exceeded threshold 0.20")

    payload = {
        "stage": "live_monitoring",
        "status": status,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "window_hours": window_hours,
        "metrics": {
            "recent_impression_count": impression_count,
            "recent_playback_count": playback_total,
            "recent_feedback_count": feedback_total,
            "completion_rate": completion_count / max(playback_total, 1),
            "skip_rate": skip_rate,
            "like_rate": like_count / max(impression_count, 1),
            "dislike_rate": dislike_count / max(impression_count, 1),
        },
        "baseline": {"skip_rate": baseline_skip_rate},
        "drift_checks": {"skip_rate_abs_diff": skip_rate_abs_diff},
        "notes": notes,
    }
    return _write_report(config.object_storage_root, payload)


def _baseline_skip_rate(root: Path) -> float:
    previous = sorted((root / "quality_reports" / "live").glob("*.json"))
    if previous:
        try:
            payload = json.loads(previous[-1].read_text(encoding="utf-8"))
            metrics = payload.get("metrics", {})
            value = metrics.get("skip_rate")
            if value is not None:
                return float(value)
        except Exception:
            pass
    return float(os.environ.get("SPOTIBOYS_BASELINE_SKIP_RATE", "0.20"))


def _write_report(root: Path, payload: dict) -> Path:
    report_dir = root / "quality_reports" / "live"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"{stamp}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(monitor_live_data())
