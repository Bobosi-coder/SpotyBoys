from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

_DEFAULT_DAG_ID = "retrain_phase2"


def trigger_airflow_retrain(dag_id: str = _DEFAULT_DAG_ID) -> Dict[str, Any]:
    """
    POST a dagRun to the Airflow REST API to kick off retraining.

    Reads AIRFLOW_BASE_URL, AIRFLOW_USERNAME, AIRFLOW_PASSWORD from env.
    Raises requests.HTTPError on non-2xx response.
    """
    base_url = os.environ.get("AIRFLOW_BASE_URL", "http://localhost:8080")
    username = os.environ.get("AIRFLOW_USERNAME", "admin")
    password = os.environ.get("AIRFLOW_PASSWORD", "admin")

    url = f"{base_url}/api/v1/dags/{dag_id}/dagRuns"
    payload: Dict[str, Any] = {
        "conf": {},
        "logical_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    }

    logger.info(f"Triggering Airflow DAG '{dag_id}' at {base_url}")
    resp = requests.post(url, json=payload, auth=(username, password), timeout=30)
    resp.raise_for_status()

    data = resp.json()
    run_id = data.get("dag_run_id", "unknown")
    logger.info(f"Airflow DAG '{dag_id}' triggered: run_id={run_id}")
    return data
