import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "ASTRA" / "website"
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))


class SensorPipelineTests(unittest.TestCase):
    def test_sensor_reading_normalizes_and_marks_status(self):
        from sensor_pipeline import SensorReading, SensorNormalizer

        reading = SensorReading(
            metric="heart_rate",
            value=72.3,
            unit="bpm",
            source="simulator",
            device_id="watch-01",
            provider="simulator",
            quality_flags=["ok"],
            sampling_frequency_hz=1.0,
            model_version="sim-v1",
        )

        normalized = SensorNormalizer.normalize(reading)
        self.assertEqual(normalized["metric"], "heart_rate")
        self.assertEqual(normalized["unit"], "bpm")
        self.assertEqual(normalized["status"], "measured")
        self.assertIn("device_id", normalized)
        self.assertTrue(normalized["timestamp"])

    def test_simulator_generates_sensor_snapshot(self):
        from sensor_pipeline import SensorSimulator

        snapshot = SensorSimulator().generate_snapshot()
        self.assertIn("heart_rate", snapshot)
        self.assertIn("spo2", snapshot)
        self.assertIn("temperature", snapshot)
        self.assertIn("device_id", snapshot)
        self.assertIn("status", snapshot)


if __name__ == "__main__":
    unittest.main()
