import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = ROOT / "ASTRA" / "website"
if str(WEBSITE_DIR) not in sys.path:
    sys.path.insert(0, str(WEBSITE_DIR))

from app import app, smartwatch_state, smartwatch_lock


class OrbitNexusNewFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_models_status_reports_all_5_models(self):
        res = self.client.get("/api/models/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["count"], 5)
        for key in ["activity", "stress", "heart", "anomaly", "space_bio"]:
            self.assertIn(key, data["models"])
            self.assertEqual(data["models"][key]["status"], "online")

    def test_disease_detection_evaluates_hazards(self):
        res = self.client.get("/api/disease_detection")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "operational")
        self.assertEqual(data["count"], 6)
        disease_ids = {d["id"] for d in data["diseases"]}
        expected = {
            "cardio_deconditioning",
            "hypoxia_respiratory",
            "sans_neuro_ocular",
            "microgravity_osteopenia",
            "cosmic_radiation_dna",
            "dcs_hypothermia",
        }
        self.assertEqual(disease_ids, expected)

    def test_telemetry_live_includes_blood_water_and_temp(self):
        res = self.client.get("/api/telemetry/live")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("telemetry", data)
        telem = data["telemetry"]
        self.assertTrue("blood_water_percent" in telem or "blood_water" in telem)
        self.assertTrue("core_temp_c" in telem or "temperature" in telem)
        self.assertIn("disease_risks", data)
        self.assertEqual(len(data["disease_risks"]), 6)

    def test_smartwatch_toggle_and_mode_protection(self):
        # 1. Disconnect watch
        res_disconn = self.client.post("/api/smartwatch/status", json={"connected": False})
        self.assertEqual(res_disconn.status_code, 200)
        self.assertFalse(res_disconn.get_json()["smartwatch"]["connected"])

        # 2. Attempt switching to realtime mode while disconnected -> must be rejected
        res_mode_fail = self.client.post("/api/mode", json={"mode": "realtime"})
        self.assertEqual(res_mode_fail.status_code, 400)
        self.assertEqual(res_mode_fail.get_json()["error"], "smartwatch_disconnected")

        # 3. Reconnect watch
        res_conn = self.client.post("/api/smartwatch/status", json={"connected": True})
        self.assertEqual(res_conn.status_code, 200)
        self.assertTrue(res_conn.get_json()["smartwatch"]["connected"])

        # 4. Now switching to realtime succeeds
        res_mode_ok = self.client.post("/api/mode", json={"mode": "realtime"})
        self.assertEqual(res_mode_ok.status_code, 200)
        self.assertEqual(res_mode_ok.get_json()["mode"], "realtime")

        # Reset back to demo
        self.client.post("/api/mode", json={"mode": "demo"})


if __name__ == "__main__":
    unittest.main()
