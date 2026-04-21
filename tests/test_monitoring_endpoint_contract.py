from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = (PROJECT_ROOT / "apps" / "recommendation-api" / "spotiboys_recommendation_api" / "main.py").read_text(
    encoding="utf-8"
)


class MonitoringEndpointContractTests(unittest.TestCase):
    def test_monitoring_summary_endpoint_exposes_rollups_and_decisions(self) -> None:
        self.assertIn('@app.get("/monitoring/summary")', MAIN_PY)
        self.assertIn('latest_serving_metric_rollup("5m")', MAIN_PY)
        self.assertIn('latest_serving_metric_rollup("1h")', MAIN_PY)
        self.assertIn('latest_model_trigger_decision("promotion")', MAIN_PY)
        self.assertIn('latest_model_trigger_decision("rollback")', MAIN_PY)


if __name__ == "__main__":
    unittest.main()
