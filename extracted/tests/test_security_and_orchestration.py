import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "ASTRA" / "website"
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))


class SecurityAndOrchestrationTests(unittest.TestCase):
    def test_security_manager_generates_and_validates_tokens(self):
        from security_manager import SecurityManager

        manager = SecurityManager(secret_key="test-secret")
        token = manager.generate_session_token("astronaut-01")
        self.assertTrue(manager.verify_session_token(token, "astronaut-01"))
        self.assertFalse(manager.verify_session_token(token, "astronaut-02"))

    def test_orchestrator_builds_risk_summary(self):
        from ai_orchestrator import AIOrchestrator

        orchestrator = AIOrchestrator()
        summary = orchestrator.build_summary(
            telemetry={"heart_rate_bpm": 125, "spo2_percent": 91, "respiration_rate": 26},
            alerts=[{"type": "spo2", "severity": "critical"}],
            memory=[{"type": "heart_rate", "summary": "recent elevated heart rate"}],
            capabilities=["heart_rate", "spo2"],
        )
        self.assertIn("risk_level", summary)
        self.assertIn("recommendations", summary)
        self.assertEqual(summary["risk_level"], "high")

    def test_system_overview_route_reports_integrated_capabilities(self):
        from app import app

        client = app.test_client()
        response = client.get("/api/system/overview")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("system_status", payload)
        self.assertIn("features", payload)
        self.assertTrue(any("ai" in item.lower() for item in payload["features"]))


if __name__ == "__main__":
    unittest.main()
