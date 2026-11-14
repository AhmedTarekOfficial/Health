import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "ASTRA" / "website"
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))


class DeviceCapabilityTests(unittest.TestCase):
    def test_capability_discovery_marks_supported_metrics(self):
        from sensor_pipeline import DeviceCapabilityRegistry

        registry = DeviceCapabilityRegistry()
        caps = registry.discover(
            {
                "heart_rate": True,
                "spo2": True,
                "temperature": False,
                "activity": True,
            }
        )
        self.assertIn("heart_rate", caps["supported"])
        self.assertIn("activity", caps["supported"])
        self.assertIn("temperature", caps["missing"])

    def test_alert_engine_creates_structured_alert(self):
        from sensor_pipeline import AlertEngine

        engine = AlertEngine()
        alert = engine.evaluate({"heart_rate": 120, "spo2": 90, "quality": "good"})
        self.assertIsNotNone(alert)
        self.assertIn("severity", alert)
        self.assertIn("threshold", alert)

    def test_wearable_adapter_registry_handles_vendor_sources(self):
        from sensor_pipeline import WearableIntegrationManager

        manager = WearableIntegrationManager()
        adapter = manager.get_adapter("wear_os")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter["provider"], "wear_os")

        normalized = manager.normalize_payload(
            {
                "provider": "ble",
                "heart_rate": 72,
                "spo2": 98,
                "temperature": 36.8,
            }
        )
        self.assertEqual(normalized["provider"], "ble")
        self.assertEqual(normalized["metrics"]["heart_rate"], 72.0)


if __name__ == "__main__":
    unittest.main()
