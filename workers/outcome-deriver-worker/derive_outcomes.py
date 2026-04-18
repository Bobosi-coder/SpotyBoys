from __future__ import annotations

from packages.config import load_config
from packages.db_access.postgres import PostgresRepository


def derive_outcomes() -> int:
    config = load_config()
    repo = PostgresRepository(config.database_url)
    with repo._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app.recommendation_outcomes
                    (outcome_id, request_id, impression_id, session_id, user_id, track_id, derived_label)
                SELECT DISTINCT
                    'out_' || p.event_id,
                    p.request_id,
                    p.impression_id,
                    p.session_id,
                    p.user_id,
                    p.track_id,
                    CASE
                        WHEN p.event_type = 'complete' THEN 'completed'
                        WHEN p.event_type = 'skip' AND p.playback_ms < 30000 THEN 'early-skip'
                        WHEN p.event_type = 'playback_start' THEN 'selected'
                        ELSE p.event_type
                    END
                FROM app.playback_events p
                WHERE p.event_type IN ('playback_start', 'skip', 'complete')
                ON CONFLICT (outcome_id) DO NOTHING
                """
            )
            return int(cur.rowcount)


if __name__ == "__main__":
    print(f"derived {derive_outcomes()} recommendation outcomes")
