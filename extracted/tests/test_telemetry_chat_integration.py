"""
Dashboard ↔ chat telemetry integration tests.

Mocks the LLM provider and model engine so data-flow can be verified without
paid API calls or loading model pickle files for inference.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = ROOT / "ASTRA" / "website"
if str(WEBSITE_DIR) not in sys.path:
    sys.path.insert(0, str(WEBSITE_DIR))

from telemetry_snapshot import (  # noqa: E402
    DEMO_READINGS,
    TelemetrySnapshotStore,
    alert_category_updates_from_predictions,
    alerts_from_predictions,
    extract_readings_from_packet,
    merge_alert_category_updates,
    resolve_observation_time,
    snapshot_store,
)

MOCK_PREDICTIONS = {
    "activity": {"activity_id": 1, "activity_name": "Resting", "status": "nominal"},
    "stress": {
        "stress_level_id": 0,
        "stress_state": "Baseline / Relaxed",
        "alert": False,
    },
    "heart_rate": {"estimated_heart_rate_bpm": 74.0, "status": "normal"},
    "anomaly_detection": {
        "is_nominal": True,
        "anomaly_flag": False,
        "anomaly_score": 0.12,
        "severity": "NOMINAL",
    },
    "space_bio": {
        "spaceflight_adaptation_signature": True,
        "biological_state": "Orbital Spaceflight Adaptation State",
    },
    "mission_health_summary": "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)",
}


def _install_mock_engine_before_app_import():
    """
    Prevent app import from loading model pickles (xgboost/sklearn artifacts).
    Safe no-op if app was already imported by another test module.
    """
    if "app" in sys.modules:
        return
    mock_engine = MagicMock()
    mock_engine.models = {
        "activity": object(),
        "stress": object(),
        "heart": object(),
        "anomaly": object(),
        "space_bio": object(),
    }
    mock_engine.process_astronaut_telemetry.return_value = dict(MOCK_PREDICTIONS)
    # Patch the class used inside app.py at import time.
    import astra_engine as _astra_engine  # noqa: WPS433

    _astra_engine.AstraIntelligenceEngine = MagicMock(return_value=mock_engine)
    # Telemetry tests must not fetch public reference pages or depend on network.
    import trusted_web_rag
    mock_rag = MagicMock()
    mock_rag.retrieve.return_value = {
        "context": "[Approved Web Reference Context] No reference data in this test.",
        "sources": [],
        "status": {"state": "unavailable", "mode": "approved_web_pages"},
    }
    # Limit the constructor patch to app import; other retrieval tests keep
    # access to the real class even when all suites run in one process.
    import importlib
    with patch.object(trusted_web_rag, "TrustedWebRAG", return_value=mock_rag):
        importlib.import_module("app")


def _sample_packet(**overrides):
    packet = {
        "imu": [75.0] + [0.0] * 21,
        "stress_physio": [72.0, 52.0, 1.8, 34.1, 14.0],
        "ppg_stats": [0.45, 0.12, 0.15, 0.85, 0.70, 0.48, 0.35, 0.05, 0.20],
        "vitals": [74.0, 98.5, 14.0, 118.0, 36.6],
        "genomics": [100.0] * 50,
    }
    packet.update(overrides)
    return packet


class TelemetrySnapshotStoreUnitTests(unittest.TestCase):
    def setUp(self):
        self.store = TelemetrySnapshotStore(stale_after_seconds=2.0)

    def test_partial_update_does_not_refresh_untouched_timestamps(self):
        first = self.store.commit_complete(
            readings={"heart_rate_bpm": 80, "spo2_percent": 98.0},
            predictions={"activity": {"activity_name": "Resting"}},
            source="simulation",
            observed_at="2020-01-01T00:00:00+00:00",
            alerts=[],
            alerts_complete=True,
        )
        hr_ts = first["readings"]["heart_rate_bpm"]["observed_at"]

        second = self.store.commit_partial(
            readings={"spo2_percent": 97.0},
            source="api_post",
            observed_at="2020-01-01T00:00:10+00:00",
        )
        self.assertEqual(second["readings"]["heart_rate_bpm"]["value"], 80)
        self.assertEqual(second["readings"]["heart_rate_bpm"]["observed_at"], hr_ts)
        self.assertEqual(second["readings"]["spo2_percent"]["value"], 97.0)
        self.assertEqual(
            second["readings"]["spo2_percent"]["observed_at"],
            "2020-01-01T00:00:10+00:00",
        )
        # Heart rate keeps the old observation time — not silently presented as fresh.
        self.assertNotEqual(
            second["readings"]["heart_rate_bpm"]["observed_at"],
            second["readings"]["spo2_percent"]["observed_at"],
        )

    def test_complete_alert_free_assessment_clears_prior_alert(self):
        self.store.commit_complete(
            readings={"heart_rate_bpm": 110},
            predictions={},
            source="simulation",
            alerts=[{"type": "ANOMALY", "severity": "CRITICAL", "message": "prior"}],
            alerts_complete=True,
        )
        cleared = self.store.commit_complete(
            readings={"heart_rate_bpm": 72},
            predictions={"anomaly_detection": {"anomaly_flag": False}},
            source="simulation",
            alerts=[],
            alerts_complete=True,
        )
        self.assertEqual(cleared["alerts"]["status"], "clear")
        self.assertEqual(cleared["alerts"]["items"], [])

    def test_incomplete_assessment_keeps_unknown_distinct_from_clear(self):
        self.store.commit_complete(
            readings={"heart_rate_bpm": 90},
            predictions={},
            source="api_post",
            alerts=[{"type": "STRESS", "severity": "CAUTION", "message": "stress"}],
            alerts_complete=True,
        )
        partial = self.store.commit_partial(
            readings={"spo2_percent": 97.0},
            predictions={"heart_rate": {"estimated_heart_rate_bpm": 70}},
            source="api_post",
            alerts_complete=False,
        )
        self.assertEqual(partial["alerts"]["status"], "active")
        self.assertEqual(len(partial["alerts"]["items"]), 1)

        empty = TelemetrySnapshotStore()
        unknown = empty.commit_partial(
            readings={"heart_rate_bpm": 70},
            alerts_complete=False,
        )
        self.assertEqual(unknown["alerts"]["status"], "unknown")

    def test_stale_marking_does_not_refresh_timestamps_on_read(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        self.store.commit_complete(
            readings={"heart_rate_bpm": 77},
            predictions={},
            source="simulation",
            observed_at=old,
            alerts_complete=True,
            alerts=[],
        )
        snap = self.store.get_current()
        self.assertEqual(snap["availability"]["freshness"], "stale")
        self.assertTrue(snap["readings"]["heart_rate_bpm"]["stale"])
        self.assertEqual(snap["readings"]["heart_rate_bpm"]["observed_at"], old)
        # Second fetch must not bump timestamps.
        again = self.store.get_current()
        self.assertEqual(again["observed_at"], old)
        self.assertEqual(again["readings"]["heart_rate_bpm"]["observed_at"], old)

    def test_snapshot_selection_policy(self):
        first = self.store.commit_complete(
            readings={"heart_rate_bpm": 70},
            predictions={"mission_health_summary": "A"},
            source="simulation",
            alerts_complete=True,
            alerts=[],
        )
        second = self.store.commit_complete(
            readings={"heart_rate_bpm": 90},
            predictions={"mission_health_summary": "B"},
            source="simulation",
            alerts_complete=True,
            alerts=[],
        )
        exact = self.store.resolve_for_chat(first["snapshot_id"])
        self.assertEqual(exact["selection"], "exact_historical")
        self.assertEqual(exact["snapshot"]["readings"]["heart_rate_bpm"]["value"], 70)

        substituted = self.store.resolve_for_chat("snap-does-not-exist")
        self.assertEqual(substituted["selection"], "current_substituted")
        self.assertEqual(substituted["used_id"], second["snapshot_id"])
        self.assertIn("not found", substituted["note"].lower())

        missing_store = TelemetrySnapshotStore()
        missing = missing_store.resolve_for_chat(None)
        self.assertEqual(missing["selection"], "missing")
        self.assertIsNone(missing["snapshot"])


class TelemetryChatIntegrationTests(unittest.TestCase):
    """REST-only workflow: live → chat without Socket.IO."""

    @classmethod
    def setUpClass(cls):
        # Import app once with a mocked engine so pickles are not required.
        _install_mock_engine_before_app_import()
        import app as app_module

        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    def setUp(self):
        snapshot_store.clear()
        # Ensure REST path owns generation (no Socket.IO loop).
        self.app_module.simulation_active.clear()
        with self.app_module.chat_lock:
            self.app_module.chat_history.clear()

        self._engine_patch = patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value=dict(MOCK_PREDICTIONS),
        )
        self._llm_patch = patch.object(
            self.app_module,
            "invoke_groq_chat",
            side_effect=self._capture_llm,
        )
        self._gen_patch = patch.object(
            self.app_module,
            "_generate_telemetry",
            side_effect=self._fake_generate,
        )
        self.captured_messages = None
        self._engine_patch.start()
        self._llm_patch.start()
        self._gen_patch.start()

    def tearDown(self):
        self._engine_patch.stop()
        self._llm_patch.stop()
        self._gen_patch.stop()
        snapshot_store.clear()
        self.app_module.simulation_active.clear()

    def _fake_generate(self, tick: int):
        packet = _sample_packet()
        vitals = {
            "heart_rate_bpm": 123.0,  # distinctive value for the bug reproduction
            "spo2_percent": 97.5,
            "respiration_rate": 15.0,
            "core_temp_c": 36.7,
            "blood_water_percent": 62.1,
            "systolic_bp": 118.0,
            "diastolic_bp": 76.0,
            "eda_microsiemens": 2.1,
            "skin_temp_c": 33.4,
            "hrv_rmssd": 44.0,
        }
        return packet, vitals

    def _capture_llm(self, messages, **kwargs):
        self.captured_messages = messages
        return {
            "reply": "Acknowledged telemetry context.",
            "source": "mock-llm",
            "status": "ok",
        }

    def _system_prompt(self) -> str:
        self.assertIsNotNone(self.captured_messages)
        return self.captured_messages[0]["content"]

    def test_fresh_start_live_then_chat_shares_snapshot(self):
        """Fresh start → GET /api/telemetry/live → POST /api/chat shares readings."""
        live = self.client.get("/api/telemetry/live")
        self.assertEqual(live.status_code, 200)
        live_body = live.get_json()
        self.assertEqual(live_body["telemetry"]["heart_rate_bpm"], 123.0)
        self.assertIn("activity", live_body["telemetry"])
        snap_id = live_body["snapshot_id"]
        self.assertTrue(snap_id)

        chat = self.client.post(
            "/api/chat",
            json={"message": "How is my heart rate?", "snapshot_id": snap_id},
        )
        self.assertEqual(chat.status_code, 200)
        chat_body = chat.get_json()
        self.assertEqual(chat_body["snapshot_id"], snap_id)
        self.assertEqual(chat_body["snapshot_selection"], "exact")

        prompt = self._system_prompt()
        self.assertIn(snap_id, prompt)
        self.assertIn("123.0", prompt)
        self.assertIn("heart_rate_bpm", prompt)
        self.assertIn("SIMULATED", prompt)
        self.assertIn("activity", prompt)
        self.assertIn("Resting", prompt)

    def test_second_snapshot_selection_policy(self):
        first = self.client.get("/api/telemetry/live").get_json()
        first_id = first["snapshot_id"]

        # Force a newer snapshot without waiting for LIVE_REFRESH_SECONDS.
        snapshot_store.commit_complete(
            readings={"heart_rate_bpm": 99.0, "spo2_percent": 96.0},
            predictions={"mission_health_summary": "SECOND"},
            source="simulation",
            alerts_complete=True,
            alerts=[],
            mission_elapsed="0d 00:00:01",
        )
        second = snapshot_store.get_current()
        second_id = second["snapshot_id"]
        self.assertNotEqual(first_id, second_id)

        # Exact historical request still resolves the first snapshot.
        chat_hist = self.client.post(
            "/api/chat",
            json={"message": "Use the first reading", "snapshot_id": first_id},
        )
        self.assertEqual(chat_hist.get_json()["snapshot_selection"], "exact_historical")
        self.assertIn("123.0", self._system_prompt())

        # Unknown id → current substituted, made explicit.
        chat_sub = self.client.post(
            "/api/chat",
            json={"message": "Now?", "snapshot_id": "snap-missing"},
        )
        body = chat_sub.get_json()
        self.assertEqual(body["snapshot_selection"], "current_substituted")
        self.assertEqual(body["snapshot_id"], second_id)
        self.assertIn("not found", body["snapshot_note"].lower())
        self.assertIn("99.0", self._system_prompt())

    def test_chat_before_any_readings_reports_missing(self):
        chat = self.client.post("/api/chat", json={"message": "Status?"})
        self.assertEqual(chat.status_code, 200)
        body = chat.get_json()
        self.assertEqual(body["snapshot_selection"], "missing")
        self.assertIsNone(body["snapshot_id"])
        prompt = self._system_prompt()
        self.assertIn("MISSING", prompt)
        self.assertIn("unavailable", prompt.lower())
        self.assertNotIn("heart_rate_bpm): 75", prompt)
        self.assertNotIn("heart_rate_bpm): 74", prompt)

    def test_old_readings_marked_stale_without_timestamp_refresh(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        snap = snapshot_store.commit_complete(
            readings={"heart_rate_bpm": 88.0, "spo2_percent": 97.0},
            predictions=dict(MOCK_PREDICTIONS),
            source="simulation",
            observed_at=old,
            alerts_complete=True,
            alerts=[],
        )
        chat = self.client.post(
            "/api/chat",
            json={"message": "Are vitals current?", "snapshot_id": snap["snapshot_id"]},
        )
        self.assertEqual(chat.status_code, 200)
        prompt = self._system_prompt()
        self.assertIn("STALE", prompt)
        self.assertIn(old, prompt)
        # Store must still hold the original observation time.
        current = snapshot_store.get_current()
        self.assertEqual(current["observed_at"], old)
        self.assertEqual(current["readings"]["heart_rate_bpm"]["observed_at"], old)

    def test_post_telemetry_preserves_readings_and_predictions(self):
        packet = _sample_packet()
        packet["heart_rate_bpm"] = 111.0
        packet["spo2_percent"] = 95.5
        res = self.client.post("/api/telemetry", json=packet)
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertIn("snapshot_id", body)
        self.assertEqual(body["readings"]["heart_rate_bpm"]["value"], 111.0)
        self.assertEqual(body["predictions"]["activity"]["activity_name"], "Resting")

        flat = snapshot_store.build_api_telemetry_view()
        self.assertEqual(flat["heart_rate_bpm"], 111.0)
        self.assertEqual(flat["spo2_percent"], 95.5)
        self.assertEqual(flat["activity"]["activity_name"], "Resting")

    def test_complete_alert_free_assessment_clears_earlier_alert(self):
        snapshot_store.commit_complete(
            readings={"heart_rate_bpm": 120},
            predictions={},
            source="simulation",
            alerts=[{"type": "ANOMALY", "severity": "CRITICAL", "message": "old"}],
            alerts_complete=True,
        )
        packet = _sample_packet()
        res = self.client.post("/api/telemetry", json=packet)
        self.assertEqual(res.status_code, 200)
        alerts = res.get_json()["alerts"]
        self.assertEqual(alerts["status"], "clear")
        self.assertEqual(alerts["items"], [])

    def test_partial_update_cannot_present_outdated_fields_as_fresh(self):
        old = "2021-06-01T12:00:00+00:00"
        snapshot_store.commit_complete(
            readings={"heart_rate_bpm": 70.0, "spo2_percent": 99.0},
            predictions={"activity": {"activity_name": "OldActivity"}},
            source="simulation",
            observed_at=old,
            alerts_complete=True,
            alerts=[],
        )
        # Partial POST: only SpO2 (incomplete features → not a full assessment).
        res = self.client.post(
            "/api/telemetry",
            json={"spo2_percent": 94.0},
        )
        self.assertEqual(res.status_code, 200)
        snap = snapshot_store.get_current()
        self.assertEqual(snap["readings"]["heart_rate_bpm"]["value"], 70.0)
        self.assertEqual(snap["readings"]["heart_rate_bpm"]["observed_at"], old)
        self.assertEqual(snap["readings"]["spo2_percent"]["value"], 94.0)
        self.assertNotEqual(snap["readings"]["spo2_percent"]["observed_at"], old)
        # Incomplete packet must not invent a new all-clear; prior alert state is preserved.
        self.assertEqual(snap["alerts"]["status"], "clear")
        # Heart rate must not look newly observed just because SpO2 was updated.
        self.assertLess(
            datetime.fromisoformat(snap["readings"]["heart_rate_bpm"]["observed_at"]),
            datetime.fromisoformat(snap["readings"]["spo2_percent"]["observed_at"]),
        )

    def test_workflow_works_without_socketio_connection(self):
        self.assertFalse(self.app_module.simulation_active.is_set())
        live = self.client.get("/api/telemetry/live")
        self.assertEqual(live.status_code, 200)
        snap_id = live.get_json()["snapshot_id"]
        chat = self.client.post(
            "/api/chat",
            json={"message": "Summarize vitals", "snapshot_id": snap_id},
        )
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat.get_json()["snapshot_selection"], "exact")
        self.assertIn("123.0", self._system_prompt())
        self.assertFalse(self.app_module.simulation_active.is_set())

    def test_live_reuses_fresh_snapshot_instead_of_parallel_stream(self):
        first = self.client.get("/api/telemetry/live").get_json()
        second = self.client.get("/api/telemetry/live").get_json()
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["observed_at"], second["observed_at"])

    def test_extract_readings_helper(self):
        readings = extract_readings_from_packet(_sample_packet(heart_rate_bpm=101))
        self.assertEqual(readings["heart_rate_bpm"], 101)
        self.assertEqual(readings["spo2_percent"], 98.5)

    def test_alerts_from_predictions(self):
        preds = {
            "anomaly_detection": {"anomaly_flag": True, "severity": "CRITICAL"},
            "stress": {"alert": True},
        }
        alerts = alerts_from_predictions(preds)
        self.assertEqual(len(alerts), 2)

    # ── PROBLEM 1: Demo snapshot consistency ─────────────────────────────────

    def test_demo_then_chat_uses_matching_demo_readings(self):
        """Initial Demo display and chat use matching readings (server-owned)."""
        demo = self.client.get("/api/telemetry/demo")
        self.assertEqual(demo.status_code, 200)
        body = demo.get_json()
        snap_id = body["snapshot_id"]
        self.assertTrue(snap_id)
        self.assertEqual(body["source"], "demo")
        self.assertEqual(
            body["telemetry"]["heart_rate_bpm"],
            DEMO_READINGS["heart_rate_bpm"],
        )

        chat = self.client.post(
            "/api/chat",
            json={"message": "How is my heart rate?", "snapshot_id": snap_id},
        )
        self.assertEqual(chat.status_code, 200)
        chat_body = chat.get_json()
        self.assertEqual(chat_body["snapshot_id"], snap_id)
        self.assertEqual(chat_body["snapshot_selection"], "exact")
        prompt = self._system_prompt()
        self.assertIn(snap_id, prompt)
        self.assertIn(str(DEMO_READINGS["heart_rate_bpm"]), prompt)
        self.assertIn("SIMULATED", prompt)
        self.assertIn("demo", prompt.lower())

    def test_realtime_then_demo_then_chat_selects_demo_snapshot(self):
        """Real-Time → Demo → Chat selects the Demo snapshot, not leftover live."""
        live = self.client.get("/api/telemetry/live").get_json()
        live_id = live["snapshot_id"]
        self.assertEqual(live["telemetry"]["heart_rate_bpm"], 123.0)

        demo = self.client.get("/api/telemetry/demo").get_json()
        demo_id = demo["snapshot_id"]
        self.assertNotEqual(live_id, demo_id)
        self.assertEqual(demo["telemetry"]["heart_rate_bpm"], DEMO_READINGS["heart_rate_bpm"])

        chat = self.client.post(
            "/api/chat",
            json={"message": "How is my heart rate?", "snapshot_id": demo_id},
        )
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat.get_json()["snapshot_id"], demo_id)
        prompt = self._system_prompt()
        self.assertIn(str(DEMO_READINGS["heart_rate_bpm"]), prompt)
        self.assertNotIn("123.0", prompt)
        # Stored demo snapshot still matches the values chat received.
        stored = snapshot_store.get_by_id(demo_id)
        self.assertEqual(
            stored["readings"]["heart_rate_bpm"]["value"],
            DEMO_READINGS["heart_rate_bpm"],
        )

    # ── PROBLEM 2: Partial alert updates ─────────────────────────────────────

    def test_stress_only_positive_creates_active_stress_alert(self):
        """Stress-only positive result creates an active stress alert."""
        self._engine_patch.stop()
        stress_only = {
            "stress": {
                "stress_level_id": 2,
                "stress_state": "High Load",
                "alert": True,
            },
            "mission_health_summary": "CAUTION: Elevated Cognitive/Physical Stress",
        }
        with patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value=stress_only,
        ):
            res = self.client.post(
                "/api/telemetry",
                json={"stress_physio": [110.0, 20.0, 6.0, 34.0, 18.0]},
            )
        self._engine_patch.start()
        self.assertEqual(res.status_code, 200)
        alerts = res.get_json()["alerts"]
        self.assertEqual(alerts["status"], "active")
        self.assertEqual(len(alerts["items"]), 1)
        self.assertEqual(alerts["items"][0]["type"], "STRESS")
        self.assertIn("STRESS", alerts["coverage"]["assessed"])
        self.assertIn("ANOMALY", alerts["coverage"]["not_assessed"])

    def test_stress_only_clear_clears_previous_stress_alert(self):
        # Seed STRESS active while leaving ANOMALY unknown (not a full clear).
        snapshot_store.commit_partial(
            readings={"heart_rate_bpm": 100},
            source="simulation",
            alert_category_updates={
                "STRESS": {
                    "outcome": "active",
                    "item": {
                        "type": "STRESS",
                        "severity": "CAUTION",
                        "message": "prior stress",
                    },
                }
            },
        )
        self._engine_patch.stop()
        with patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value={"stress": {"alert": False, "stress_state": "Relaxed"}},
        ):
            res = self.client.post(
                "/api/telemetry",
                json={"stress_physio": [70.0, 50.0, 1.5, 33.0, 14.0]},
            )
        self._engine_patch.start()
        self.assertEqual(res.status_code, 200)
        alerts = res.get_json()["alerts"]
        self.assertEqual(alerts["categories"]["STRESS"]["status"], "clear")
        # Anomaly was never successfully assessed — must not become an all-clear.
        self.assertEqual(alerts["categories"]["ANOMALY"]["status"], "unknown")
        self.assertNotEqual(alerts["status"], "clear")
        self.assertEqual(alerts["status"], "partial")

    def test_stress_only_update_preserves_unrelated_anomaly_alert(self):
        snapshot_store.commit_complete(
            readings={"heart_rate_bpm": 100},
            predictions={},
            source="simulation",
            alerts=[{"type": "ANOMALY", "severity": "CRITICAL", "message": "prior anomaly"}],
            alerts_complete=True,
        )
        self._engine_patch.stop()
        with patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value={"stress": {"alert": False, "stress_state": "Relaxed"}},
        ):
            res = self.client.post(
                "/api/telemetry",
                json={"stress_physio": [70.0, 50.0, 1.5, 33.0, 14.0]},
            )
        self._engine_patch.start()
        alerts = res.get_json()["alerts"]
        self.assertEqual(alerts["status"], "active")
        self.assertEqual(alerts["categories"]["ANOMALY"]["status"], "active")
        self.assertEqual(alerts["categories"]["STRESS"]["status"], "clear")
        types = {i["type"] for i in alerts["items"]}
        self.assertEqual(types, {"ANOMALY"})

    def test_failed_model_output_does_not_produce_all_clear(self):
        updates = alert_category_updates_from_predictions(
            {"anomaly_detection": {"error": "model missing"}, "stress": {"alert": False}}
        )
        state, _skipped = merge_alert_category_updates(
            None, updates, "2020-01-01T00:00:00+00:00"
        )
        self.assertEqual(state["categories"]["STRESS"]["status"], "clear")
        self.assertEqual(state["categories"]["ANOMALY"]["status"], "unknown")
        self.assertEqual(state["categories"]["ANOMALY"]["last_evaluation"], "failed")
        self.assertNotEqual(state["status"], "clear")
        self.assertIn("ANOMALY", state["coverage"]["failed"])

    def test_successful_complete_assessment_clears_previous_alerts_via_api(self):
        snapshot_store.commit_complete(
            readings={"heart_rate_bpm": 120},
            predictions={},
            source="simulation",
            alerts=[
                {"type": "ANOMALY", "severity": "CRITICAL", "message": "old"},
                {"type": "STRESS", "severity": "CAUTION", "message": "old stress"},
            ],
            alerts_complete=True,
        )
        res = self.client.post("/api/telemetry", json=_sample_packet())
        self.assertEqual(res.status_code, 200)
        alerts = res.get_json()["alerts"]
        self.assertEqual(alerts["status"], "clear")
        self.assertEqual(alerts["items"], [])
        self.assertEqual(alerts["categories"]["ANOMALY"]["status"], "clear")
        self.assertEqual(alerts["categories"]["STRESS"]["status"], "clear")

    # ── PROBLEM 3: Observation timestamps ────────────────────────────────────

    def test_two_minute_old_observation_remains_stale_with_current_received_at(self):
        old_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
        old = old_dt.isoformat()
        before = datetime.now(timezone.utc)
        res = self.client.post(
            "/api/telemetry",
            json={**_sample_packet(), "observed_at": old, "heart_rate_bpm": 88.0},
        )
        after = datetime.now(timezone.utc)
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        stored_obs = datetime.fromisoformat(body["observed_at"].replace("Z", "+00:00"))
        self.assertEqual(stored_obs, old_dt)
        received = datetime.fromisoformat(body["received_at"].replace("Z", "+00:00"))
        self.assertGreaterEqual(received, before)
        self.assertLessEqual(received, after)
        self.assertNotEqual(body["observed_at"], body["received_at"])
        self.assertEqual(body["observation_time_basis"], "client_supplied")

        snap = snapshot_store.get_current()
        self.assertEqual(snap["availability"]["freshness"], "stale")
        self.assertTrue(snap["readings"]["heart_rate_bpm"]["stale"])
        self.assertEqual(snap["readings"]["heart_rate_bpm"]["observed_at"], body["observed_at"])

        # Fetch/read must not refresh observation timestamps.
        again = snapshot_store.get_current()
        self.assertEqual(again["observed_at"], body["observed_at"])
        self.assertEqual(again["readings"]["heart_rate_bpm"]["observed_at"], body["observed_at"])

    def test_invalid_observed_at_returns_validation_error(self):
        res = self.client.post(
            "/api/telemetry",
            json={**_sample_packet(), "observed_at": "not-a-timestamp"},
        )
        self.assertEqual(res.status_code, 400)
        body = res.get_json()
        self.assertIn("error", body)
        self.assertEqual(body["validation"]["basis"], "invalid")
        self.assertIsNone(snapshot_store.get_current())

    def test_implausible_future_observed_at_rejected(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        res = self.client.post(
            "/api/telemetry",
            json={**_sample_packet(), "observed_at": future},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("future", res.get_json()["error"].lower())

    def test_missing_observed_at_uses_receipt_time_with_explicit_basis(self):
        res = self.client.post("/api/telemetry", json=_sample_packet())
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["observation_time_basis"], "receipt_time_assumed")
        self.assertIsNotNone(body.get("observation_time_warning"))
        self.assertIn("uncertain", body["observation_time_warning"].lower())
        self.assertEqual(body["observed_at"], body["received_at"])

    def test_older_incoming_measurement_does_not_replace_newer(self):
        newer = "2022-01-01T12:00:10+00:00"
        older = "2022-01-01T12:00:00+00:00"
        snapshot_store.commit_complete(
            readings={"heart_rate_bpm": 90.0, "spo2_percent": 98.0},
            predictions={},
            source="simulation",
            observed_at=newer,
            alerts_complete=True,
            alerts=[],
        )
        second = snapshot_store.commit_partial(
            readings={"heart_rate_bpm": 70.0, "spo2_percent": 95.0},
            source="api_post",
            observed_at=older,
        )
        self.assertEqual(second["readings"]["heart_rate_bpm"]["value"], 90.0)
        self.assertEqual(second["readings"]["heart_rate_bpm"]["observed_at"], newer)
        self.assertIn("heart_rate_bpm", second["ingestion"]["skipped_outdated_readings"])
        self.assertIn("spo2_percent", second["ingestion"]["skipped_outdated_readings"])

    def test_resolve_observation_time_naive_iso_is_utc(self):
        result = resolve_observation_time(
            "2022-06-01T10:00:00",
            received_at="2022-06-01T10:00:05+00:00",
            now=datetime(2022, 6, 1, 10, 0, 5, tzinfo=timezone.utc).timestamp(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["basis"], "client_supplied")
        self.assertTrue(result["observed_at"].endswith("+00:00") or result["observed_at"].endswith("Z"))


class AlertCategoryMergeUnitTests(unittest.TestCase):
    def test_stress_only_positive_on_empty_store(self):
        updates = alert_category_updates_from_predictions({"stress": {"alert": True}})
        state, skipped = merge_alert_category_updates(
            None, updates, "2020-01-01T00:00:00+00:00"
        )
        self.assertEqual(skipped, [])
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["items"][0]["type"], "STRESS")
        self.assertEqual(state["categories"]["ANOMALY"]["status"], "unknown")

    def test_stress_clear_preserves_anomaly_active(self):
        prior, _ = merge_alert_category_updates(
            None,
            {
                "ANOMALY": {
                    "outcome": "active",
                    "item": {"type": "ANOMALY", "severity": "CRITICAL", "message": "a"},
                },
                "STRESS": {
                    "outcome": "active",
                    "item": {"type": "STRESS", "severity": "CAUTION", "message": "s"},
                },
            },
            "2020-01-01T00:00:00+00:00",
        )
        cleared, skipped = merge_alert_category_updates(
            prior,
            {"STRESS": {"outcome": "clear", "item": None}},
            "2020-01-01T00:01:00+00:00",
        )
        self.assertEqual(skipped, [])
        self.assertEqual(cleared["categories"]["STRESS"]["status"], "clear")
        self.assertEqual(cleared["categories"]["ANOMALY"]["status"], "active")
        self.assertEqual(cleared["status"], "active")

    def test_empty_dicts_do_not_produce_clear_assessments(self):
        updates = alert_category_updates_from_predictions(
            {"stress": {}, "anomaly_detection": {}}
        )
        self.assertEqual(updates["STRESS"]["outcome"], "failed")
        self.assertEqual(updates["ANOMALY"]["outcome"], "failed")
        state, _ = merge_alert_category_updates(
            None, updates, "2020-01-01T00:00:00+00:00"
        )
        self.assertNotEqual(state["status"], "clear")
        self.assertEqual(state["categories"]["STRESS"]["status"], "unknown")
        self.assertEqual(state["categories"]["ANOMALY"]["status"], "unknown")

    def test_missing_flag_fields_are_not_false(self):
        updates = alert_category_updates_from_predictions(
            {
                "stress": {"stress_state": "unknown"},
                "anomaly_detection": {"severity": "NOMINAL"},
            }
        )
        self.assertEqual(updates["STRESS"]["outcome"], "failed")
        self.assertEqual(updates["ANOMALY"]["outcome"], "failed")

    def test_non_dict_outputs_do_not_raise(self):
        updates = alert_category_updates_from_predictions(
            {"stress": "unavailable", "anomaly_detection": ["bad"]}
        )
        self.assertEqual(updates["STRESS"]["outcome"], "failed")
        self.assertEqual(updates["ANOMALY"]["outcome"], "failed")

    def test_invalid_flag_types_are_failed(self):
        updates = alert_category_updates_from_predictions(
            {
                "stress": {"alert": "false"},
                "anomaly_detection": {"anomaly_flag": 0},
            }
        )
        self.assertEqual(updates["STRESS"]["outcome"], "failed")
        self.assertEqual(updates["ANOMALY"]["outcome"], "failed")

    def test_explicit_false_clears_only_evaluated_category(self):
        prior, _ = merge_alert_category_updates(
            None,
            {
                "STRESS": {
                    "outcome": "active",
                    "item": {"type": "STRESS", "severity": "CAUTION", "message": "s"},
                },
                "ANOMALY": {
                    "outcome": "active",
                    "item": {"type": "ANOMALY", "severity": "CRITICAL", "message": "a"},
                },
            },
            "2020-01-01T00:00:00+00:00",
        )
        updates = alert_category_updates_from_predictions({"stress": {"alert": False}})
        state, _ = merge_alert_category_updates(
            prior, updates, "2020-01-01T00:01:00+00:00"
        )
        self.assertEqual(state["categories"]["STRESS"]["status"], "clear")
        self.assertEqual(state["categories"]["ANOMALY"]["status"], "active")

    def test_failed_evaluation_preserves_existing_alert(self):
        prior, _ = merge_alert_category_updates(
            None,
            {
                "STRESS": {
                    "outcome": "active",
                    "item": {"type": "STRESS", "severity": "CAUTION", "message": "s"},
                }
            },
            "2020-01-01T00:00:00+00:00",
        )
        updates = alert_category_updates_from_predictions({"stress": {}})
        state, _ = merge_alert_category_updates(
            prior, updates, "2020-01-01T00:01:00+00:00"
        )
        self.assertEqual(state["categories"]["STRESS"]["status"], "active")
        self.assertEqual(state["categories"]["STRESS"]["last_evaluation"], "failed")
        self.assertEqual(state["status"], "active")

    def test_api_non_dict_stress_does_not_http_500(self):
        # Import app path via TelemetryChatIntegrationTests patterns is heavy;
        # exercise the pure parser contract that api_telemetry relies on.
        updates = alert_category_updates_from_predictions({"stress": "unavailable"})
        self.assertEqual(updates["STRESS"]["outcome"], "failed")


class DelayedAlertOrderingTests(unittest.TestCase):
    def setUp(self):
        self.store = TelemetrySnapshotStore(stale_after_seconds=2.0)

    def test_new_active_then_old_clear_keeps_active(self):
        newer = "2022-01-01T12:02:00+00:00"
        older = "2022-01-01T12:00:00+00:00"
        first = self.store.commit_partial(
            readings={"heart_rate_bpm": 120.0},
            predictions={"stress": {"alert": True, "stress_state": "High"}},
            observed_at=newer,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": True}}
            ),
            source="api_post",
        )
        assessed = first["alerts"]["categories"]["STRESS"]["assessed_at"]
        second = self.store.commit_partial(
            readings={"heart_rate_bpm": 70.0},
            predictions={"stress": {"alert": False, "stress_state": "Relaxed"}},
            observed_at=older,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": False}}
            ),
            source="api_post",
        )
        self.assertEqual(second["readings"]["heart_rate_bpm"]["value"], 120.0)
        self.assertEqual(second["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertEqual(second["alerts"]["categories"]["STRESS"]["assessed_at"], assessed)
        self.assertTrue(second["predictions"]["stress"].get("alert") is True)
        self.assertIn("STRESS", second["ingestion"]["skipped_outdated_alert_categories"])
        self.assertIn("stress", second["ingestion"]["skipped_outdated_predictions"])

    def test_new_clear_then_old_active_keeps_clear(self):
        newer = "2022-01-01T12:02:00+00:00"
        older = "2022-01-01T12:00:00+00:00"
        self.store.commit_partial(
            readings={"heart_rate_bpm": 70.0},
            predictions={"stress": {"alert": False}},
            observed_at=newer,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": False}}
            ),
        )
        second = self.store.commit_partial(
            readings={"heart_rate_bpm": 120.0},
            predictions={"stress": {"alert": True}},
            observed_at=older,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": True}}
            ),
        )
        self.assertEqual(second["alerts"]["categories"]["STRESS"]["status"], "clear")
        self.assertTrue(second["predictions"]["stress"].get("alert") is False)

    def test_new_success_then_old_failure_keeps_metadata(self):
        newer = "2022-01-01T12:02:00+00:00"
        older = "2022-01-01T12:00:00+00:00"
        first = self.store.commit_partial(
            readings={"heart_rate_bpm": 80.0},
            predictions={"stress": {"alert": False}},
            observed_at=newer,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": False}}
            ),
        )
        second = self.store.commit_partial(
            readings={"heart_rate_bpm": 80.0},
            predictions={"stress": {"error": "timeout"}},
            observed_at=older,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"error": "timeout"}}
            ),
        )
        self.assertEqual(second["alerts"]["categories"]["STRESS"]["status"], "clear")
        self.assertEqual(
            second["alerts"]["categories"]["STRESS"]["last_evaluation"], "clear"
        )
        self.assertEqual(
            second["alerts"]["categories"]["STRESS"]["assessed_at"],
            first["alerts"]["categories"]["STRESS"]["assessed_at"],
        )
        self.assertIn("STRESS", second["ingestion"]["skipped_outdated_alert_categories"])

    def test_outdated_stress_does_not_affect_newer_anomaly(self):
        anomaly_time = "2022-01-01T12:05:00+00:00"
        stress_old = "2022-01-01T12:00:00+00:00"
        self.store.commit_partial(
            readings={"heart_rate_bpm": 90.0},
            predictions={"anomaly_detection": {"anomaly_flag": True}},
            observed_at=anomaly_time,
            alert_category_updates=alert_category_updates_from_predictions(
                {"anomaly_detection": {"anomaly_flag": True}}
            ),
        )
        # Category with no newer assessment can still accept a valid update.
        second = self.store.commit_partial(
            readings={"hrv_rmssd": 40.0},
            predictions={"stress": {"alert": True}},
            observed_at=stress_old,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": True}}
            ),
        )
        self.assertEqual(second["alerts"]["categories"]["ANOMALY"]["status"], "active")
        self.assertEqual(second["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertEqual(second["ingestion"]["skipped_outdated_alert_categories"], [])

    def test_equal_timestamps_last_write_wins(self):
        ts = "2022-01-01T12:00:00+00:00"
        self.store.commit_partial(
            readings={"heart_rate_bpm": 100.0},
            predictions={"stress": {"alert": True}},
            observed_at=ts,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": True}}
            ),
        )
        second = self.store.commit_partial(
            readings={"heart_rate_bpm": 70.0},
            predictions={"stress": {"alert": False}},
            observed_at=ts,
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": False}}
            ),
        )
        self.assertEqual(second["alerts"]["categories"]["STRESS"]["status"], "clear")
        self.assertEqual(second["readings"]["heart_rate_bpm"]["value"], 70.0)
        self.assertEqual(second["ingestion"]["skipped_outdated_alert_categories"], [])


class DelayedAlertApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_mock_engine_before_app_import()
        import app as app_module

        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    def setUp(self):
        snapshot_store.clear()
        self.app_module.simulation_active.clear()
        with self.app_module.chat_lock:
            self.app_module.chat_history.clear()
        self.captured_messages = None
        self._llm_patch = patch.object(
            self.app_module,
            "invoke_groq_chat",
            side_effect=self._capture_llm,
        )
        self._llm_patch.start()

    def tearDown(self):
        self._llm_patch.stop()
        snapshot_store.clear()

    def _capture_llm(self, messages, **kwargs):
        self.captured_messages = messages
        return {"reply": "ok", "source": "mock-llm", "status": "ok"}

    def test_api_recent_alert_not_cleared_by_older_packet(self):
        now = datetime.now(timezone.utc)
        newer = now.isoformat()
        older = (now - timedelta(minutes=2)).isoformat()

        with patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value={"stress": {"alert": True, "stress_state": "High"}},
        ):
            first = self.client.post(
                "/api/telemetry",
                json={
                    "stress_physio": [120.0, 20.0, 6.0, 34.0, 18.0],
                    "heart_rate_bpm": 120.0,
                    "observed_at": newer,
                },
            )
        self.assertEqual(first.status_code, 200)
        first_body = first.get_json()
        self.assertEqual(first_body["alerts"]["categories"]["STRESS"]["status"], "active")
        assessed = first_body["alerts"]["categories"]["STRESS"]["assessed_at"]

        with patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value={"stress": {"alert": False, "stress_state": "Relaxed"}},
        ):
            second = self.client.post(
                "/api/telemetry",
                json={
                    "stress_physio": [70.0, 50.0, 1.5, 33.0, 14.0],
                    "heart_rate_bpm": 70.0,
                    "observed_at": older,
                },
            )
        self.assertEqual(second.status_code, 200)
        body = second.get_json()
        self.assertEqual(body["readings"]["heart_rate_bpm"]["value"], 120.0)
        self.assertEqual(body["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertEqual(body["alerts"]["categories"]["STRESS"]["assessed_at"], assessed)
        self.assertTrue(body["predictions"]["stress"].get("alert") is True)
        self.assertIn("STRESS", body["ingestion"]["skipped_outdated_alert_categories"])

        snap_id = body["snapshot_id"]
        chat = self.client.post(
            "/api/chat",
            json={"message": "Any stress alerts?", "snapshot_id": snap_id},
        )
        self.assertEqual(chat.status_code, 200)
        prompt = self.captured_messages[0]["content"]
        self.assertIn("STRESS", prompt)
        self.assertIn("active", prompt)
        self.assertIn("120", prompt)

    def test_api_malformed_stress_does_not_http_500(self):
        with patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value={"stress": "unavailable"},
        ):
            res = self.client.post(
                "/api/telemetry",
                json={"stress_physio": [70.0, 50.0, 1.5, 33.0, 14.0]},
            )
        self.assertEqual(res.status_code, 200)
        alerts = res.get_json()["alerts"]
        self.assertEqual(alerts["categories"]["STRESS"]["last_evaluation"], "failed")
        self.assertNotEqual(alerts["status"], "clear")


class CategoryOrderingTimestampRegressionTests(unittest.TestCase):
    """ISSUE 1: a newer failed evaluation must still order later packets."""

    def setUp(self):
        self.store = TelemetrySnapshotStore(stale_after_seconds=2.0)

    def _post(self, observed_at, predictions, readings=None):
        return self.store.commit_partial(
            readings=readings or {"heart_rate_bpm": 72.0},
            predictions=predictions,
            source="api_post",
            observed_at=observed_at,
            alert_category_updates=alert_category_updates_from_predictions(predictions),
        )

    def test_earlier_active_then_newer_failure_then_delayed_clear(self):
        active = self._post(
            "2022-01-01T12:00:00+00:00", {"stress": {"alert": True}}
        )
        self.assertEqual(active["alerts"]["categories"]["STRESS"]["status"], "active")

        failed = self._post(
            "2022-01-01T12:02:00+00:00", {"stress": {"error": "inference timeout"}}
        )
        stress = failed["alerts"]["categories"]["STRESS"]
        self.assertEqual(stress["status"], "active")
        self.assertEqual(stress["last_evaluation"], "failed")
        self.assertEqual(stress["last_evaluation_at"], "2022-01-01T12:02:00+00:00")
        self.assertEqual(stress["assessed_at"], "2022-01-01T12:00:00+00:00")

        delayed = self._post(
            "2022-01-01T12:01:00+00:00", {"stress": {"alert": False}}
        )
        stress = delayed["alerts"]["categories"]["STRESS"]
        self.assertEqual(stress["status"], "active")
        self.assertEqual(stress["last_evaluation"], "failed")
        self.assertEqual(stress["last_evaluation_at"], "2022-01-01T12:02:00+00:00")
        self.assertEqual(stress["assessed_at"], "2022-01-01T12:00:00+00:00")
        self.assertIn("inference timeout", str(stress.get("last_error")))
        # The delayed clear prediction must not be accepted.
        self.assertNotIn("alert", delayed["predictions"]["stress"])
        self.assertNotEqual(delayed["predictions"]["stress"].get("alert"), False)
        self.assertIn(
            "STRESS", delayed["ingestion"]["skipped_outdated_alert_categories"]
        )
        self.assertIn("stress", delayed["ingestion"]["skipped_outdated_predictions"])

    def test_earlier_clear_then_newer_failure_then_delayed_active(self):
        self._post("2022-01-01T12:00:00+00:00", {"stress": {"alert": False}})
        self._post(
            "2022-01-01T12:02:00+00:00", {"stress": {"error": "inference timeout"}}
        )
        delayed = self._post(
            "2022-01-01T12:01:00+00:00", {"stress": {"alert": True}}
        )
        stress = delayed["alerts"]["categories"]["STRESS"]
        self.assertEqual(stress["status"], "clear")
        self.assertEqual(stress["last_evaluation"], "failed")
        self.assertEqual(stress["last_evaluation_at"], "2022-01-01T12:02:00+00:00")
        self.assertNotEqual(delayed["predictions"]["stress"].get("alert"), True)
        self.assertIn(
            "STRESS", delayed["ingestion"]["skipped_outdated_alert_categories"]
        )

    def test_older_failure_does_not_overwrite_newer_error_metadata(self):
        self._post("2022-01-01T12:00:00+00:00", {"stress": {"alert": True}})
        self._post(
            "2022-01-01T12:02:00+00:00", {"stress": {"error": "newer timeout"}}
        )
        delayed = self._post(
            "2022-01-01T12:01:00+00:00", {"stress": {"error": "older timeout"}}
        )
        stress = delayed["alerts"]["categories"]["STRESS"]
        self.assertEqual(stress["last_evaluation"], "failed")
        self.assertEqual(stress["last_evaluation_at"], "2022-01-01T12:02:00+00:00")
        self.assertIn("newer timeout", str(stress.get("last_error")))
        self.assertNotIn("older timeout", str(stress.get("last_error")))
        self.assertEqual(stress["status"], "active")

    def test_valid_recovery_after_failed_evaluation_is_accepted(self):
        self._post("2022-01-01T12:00:00+00:00", {"stress": {"alert": True}})
        self._post(
            "2022-01-01T12:02:00+00:00", {"stress": {"error": "inference timeout"}}
        )
        recovered = self._post(
            "2022-01-01T12:03:00+00:00", {"stress": {"alert": False}}
        )
        stress = recovered["alerts"]["categories"]["STRESS"]
        self.assertEqual(stress["status"], "clear")
        self.assertEqual(stress["last_evaluation"], "clear")
        self.assertEqual(stress["assessed_at"], "2022-01-01T12:03:00+00:00")
        self.assertIsNone(stress.get("last_error"))
        self.assertEqual(
            recovered["ingestion"]["skipped_outdated_alert_categories"], []
        )

    def test_outdated_category_does_not_block_other_category(self):
        self._post(
            "2022-01-01T12:00:00+00:00",
            {"anomaly_detection": {"anomaly_flag": False}},
        )
        self._post(
            "2022-01-01T12:06:00+00:00", {"stress": {"error": "inference timeout"}}
        )
        mixed = self._post(
            "2022-01-01T12:04:00+00:00",
            {
                "stress": {"alert": False},
                "anomaly_detection": {"anomaly_flag": True},
            },
        )
        self.assertEqual(mixed["alerts"]["categories"]["ANOMALY"]["status"], "active")
        self.assertEqual(mixed["alerts"]["categories"]["STRESS"]["status"], "unknown")
        self.assertEqual(
            mixed["alerts"]["categories"]["STRESS"]["last_evaluation"], "failed"
        )
        self.assertEqual(
            mixed["ingestion"]["skipped_outdated_alert_categories"], ["STRESS"]
        )
        self.assertTrue(
            mixed["predictions"]["anomaly_detection"]["anomaly_flag"] is True
        )

    def test_equal_timestamps_still_last_write_wins_after_failure(self):
        self._post("2022-01-01T12:00:00+00:00", {"stress": {"alert": True}})
        self._post(
            "2022-01-01T12:02:00+00:00", {"stress": {"error": "inference timeout"}}
        )
        same_ts = self._post(
            "2022-01-01T12:02:00+00:00", {"stress": {"alert": False}}
        )
        self.assertEqual(
            same_ts["alerts"]["categories"]["STRESS"]["status"], "clear"
        )
        self.assertEqual(
            same_ts["ingestion"]["skipped_outdated_alert_categories"], []
        )


class DerivedMissionSummaryRegressionTests(unittest.TestCase):
    """ISSUE 2: the summary must describe the accepted canonical state."""

    CLEAR = "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)"

    def setUp(self):
        self.store = TelemetrySnapshotStore(stale_after_seconds=2.0)

    def _post(self, observed_at, predictions, readings=None):
        return self.store.commit_partial(
            readings=readings or {"heart_rate_bpm": 72.0},
            predictions=predictions,
            source="api_post",
            observed_at=observed_at,
            alert_category_updates=alert_category_updates_from_predictions(predictions),
        )

    def test_new_active_stress_then_old_clear_packet_keeps_alert_summary(self):
        self._post(
            "2022-01-01T12:02:00+00:00",
            {
                "stress": {"alert": True, "stress_state": "High"},
                "mission_health_summary": "CAUTION: Elevated Cognitive/Physical Stress",
            },
        )
        delayed = self._post(
            "2022-01-01T12:00:00+00:00",
            {
                "stress": {"alert": False, "stress_state": "Relaxed"},
                "mission_health_summary": self.CLEAR,
            },
        )
        self.assertEqual(delayed["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertTrue(delayed["predictions"]["stress"]["alert"])
        summary = delayed["predictions"]["mission_health_summary"]
        self.assertIn("CAUTION", summary)
        self.assertNotIn("ALL SYSTEMS NOMINAL", summary)
        self.assertEqual(delayed["mission_assessment"]["level"], "caution")
        self.assertIn("STRESS", delayed["mission_assessment"]["active_categories"])
        self.assertTrue(delayed["ingestion"]["incoming_summary_ignored"])
        self.assertEqual(
            delayed["ingestion"]["mission_health_summary_source"],
            "derived_from_accepted_state",
        )

    def test_new_clear_then_old_active_packet_keeps_clear_summary(self):
        self._post(
            "2022-01-01T12:03:00+00:00",
            {
                "stress": {"alert": False},
                "anomaly_detection": {"anomaly_flag": False, "severity": "NOMINAL"},
                "mission_health_summary": self.CLEAR,
            },
        )
        delayed = self._post(
            "2022-01-01T12:00:00+00:00",
            {
                "stress": {"alert": True},
                "anomaly_detection": {"anomaly_flag": True, "severity": "CRITICAL"},
                "mission_health_summary": "ALERT: Physiological Anomaly Detected",
            },
        )
        self.assertEqual(delayed["alerts"]["categories"]["STRESS"]["status"], "clear")
        self.assertEqual(delayed["alerts"]["categories"]["ANOMALY"]["status"], "clear")
        self.assertEqual(delayed["predictions"]["mission_health_summary"], self.CLEAR)
        self.assertEqual(delayed["mission_assessment"]["level"], "clear")

    def test_mixed_update_summary_matches_final_accepted_state(self):
        self._post(
            "2022-01-01T12:00:00+00:00",
            {"stress": {"alert": False}, "anomaly_detection": {"anomaly_flag": False}},
        )
        self._post("2022-01-01T12:05:00+00:00", {"stress": {"alert": True}})
        mixed = self._post(
            "2022-01-01T12:04:00+00:00",
            {"stress": {"alert": False}, "anomaly_detection": {"anomaly_flag": False}},
        )
        self.assertEqual(mixed["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertEqual(mixed["alerts"]["categories"]["ANOMALY"]["status"], "clear")
        self.assertIn(
            "STRESS", mixed["ingestion"]["skipped_outdated_alert_categories"]
        )
        self.assertNotIn(
            "ANOMALY", mixed["ingestion"]["skipped_outdated_alert_categories"]
        )
        summary = mixed["predictions"]["mission_health_summary"]
        self.assertIn("CAUTION", summary)
        self.assertNotIn("ALL SYSTEMS NOMINAL", summary)
        self.assertEqual(mixed["mission_assessment"]["level"], "caution")

    def test_failed_latest_evaluation_is_not_an_all_clear(self):
        self._post(
            "2022-01-01T12:00:00+00:00",
            {"stress": {"alert": True}, "anomaly_detection": {"anomaly_flag": False}},
        )
        failed = self._post(
            "2022-01-01T12:02:00+00:00",
            {
                "stress": {"error": "inference timeout"},
                "mission_health_summary": self.CLEAR,
            },
        )
        summary = failed["predictions"]["mission_health_summary"]
        self.assertIn("CAUTION", summary)
        self.assertIn("failed", summary.lower())
        self.assertNotIn("ALL SYSTEMS NOMINAL", summary)
        self.assertEqual(failed["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertEqual(
            failed["alerts"]["categories"]["STRESS"]["last_evaluation"], "failed"
        )
        self.assertEqual(
            failed["mission_assessment"]["failed_categories"], ["STRESS"]
        )

    def test_failed_evaluation_after_clear_is_indeterminate(self):
        self._post(
            "2022-01-01T12:00:00+00:00",
            {"stress": {"alert": False}, "anomaly_detection": {"anomaly_flag": False}},
        )
        failed = self._post(
            "2022-01-01T12:02:00+00:00", {"stress": {"error": "inference timeout"}}
        )
        self.assertEqual(failed["mission_assessment"]["level"], "indeterminate")
        summary = failed["predictions"]["mission_health_summary"]
        self.assertIn("INDETERMINATE", summary)
        self.assertNotIn("ALL SYSTEMS NOMINAL", summary)

    def test_partial_coverage_does_not_claim_readiness(self):
        none_assessed = self.store.commit_partial(
            readings={"heart_rate_bpm": 70.0},
            source="api_post",
            observed_at="2022-01-01T12:00:00+00:00",
        )
        self.assertEqual(none_assessed["mission_assessment"]["level"], "unknown")
        self.assertNotIn(
            "ALL SYSTEMS NOMINAL",
            none_assessed["predictions"]["mission_health_summary"],
        )

        partial = self._post(
            "2022-01-01T12:01:00+00:00", {"stress": {"alert": False}}
        )
        self.assertEqual(partial["mission_assessment"]["level"], "partial")
        self.assertIn(
            "ANOMALY", partial["mission_assessment"]["not_assessed_categories"]
        )
        self.assertNotIn(
            "ALL SYSTEMS NOMINAL", partial["predictions"]["mission_health_summary"]
        )

    def test_complete_successful_clear_produces_documented_summary(self):
        clear = self._post(
            "2022-01-01T12:00:00+00:00",
            {"stress": {"alert": False}, "anomaly_detection": {"anomaly_flag": False}},
        )
        self.assertEqual(clear["mission_assessment"]["level"], "clear")
        self.assertEqual(clear["predictions"]["mission_health_summary"], self.CLEAR)
        self.assertEqual(clear["mission_assessment"]["failed_categories"], [])
        self.assertEqual(clear["mission_assessment"]["not_assessed_categories"], [])

    def test_incoming_summary_cannot_contradict_accepted_state_directly(self):
        """A packet's own summary is ignored even without an ordering conflict."""
        snap = self.store.commit_partial(
            readings={"heart_rate_bpm": 80.0},
            predictions={
                "stress": {"alert": True},
                "anomaly_detection": {"anomaly_flag": True},
                "mission_health_summary": "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)",
            },
            source="api_post",
            observed_at="2022-01-01T12:00:00+00:00",
            alert_category_updates=alert_category_updates_from_predictions(
                {"stress": {"alert": True}, "anomaly_detection": {"anomaly_flag": True}}
            ),
        )
        summary = snap["predictions"]["mission_health_summary"]
        self.assertIn("ALERT", summary)
        self.assertNotIn("ALL SYSTEMS NOMINAL", summary)
        self.assertTrue(snap["ingestion"]["incoming_summary_ignored"])


class MissionSummaryConsistencyApiTests(unittest.TestCase):
    """Issue 1 + 2 through POST /api/telemetry and POST /api/chat."""

    @classmethod
    def setUpClass(cls):
        _install_mock_engine_before_app_import()
        import app as app_module

        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    def setUp(self):
        snapshot_store.clear()
        self.app_module.simulation_active.clear()
        with self.app_module.chat_lock:
            self.app_module.chat_history.clear()
        self.captured_messages = None
        self._llm_patch = patch.object(
            self.app_module,
            "invoke_groq_chat",
            side_effect=self._capture_llm,
        )
        self._llm_patch.start()

    def tearDown(self):
        self._llm_patch.stop()
        snapshot_store.clear()

    def _capture_llm(self, messages, **kwargs):
        self.captured_messages = messages
        return {"reply": "ok", "source": "mock-llm", "status": "ok"}

    def _post_telemetry(self, predictions, observed_at, extra=None):
        body = {
            "stress_physio": [72.0, 52.0, 1.8, 34.1, 14.0],
            "heart_rate_bpm": 72.0,
            "observed_at": observed_at,
        }
        body.update(extra or {})
        with patch.object(
            self.app_module.engine,
            "process_astronaut_telemetry",
            return_value=predictions,
        ):
            res = self.client.post("/api/telemetry", json=body)
        self.assertEqual(res.status_code, 200)
        return res.get_json()

    def test_delayed_clear_packet_cannot_rewrite_alert_or_summary(self):
        now = datetime.now(timezone.utc)
        newer = now.isoformat()
        older = (now - timedelta(minutes=2)).isoformat()

        first = self._post_telemetry(
            {
                "stress": {"stress_level_id": 1, "stress_state": "High", "alert": True},
                "anomaly_detection": {"anomaly_flag": False, "severity": "NOMINAL"},
                "mission_health_summary": "CAUTION: Elevated Cognitive/Physical Stress",
            },
            newer,
            extra={"stress_physio": [120.0, 20.0, 6.0, 34.0, 18.0]},
        )
        self.assertEqual(first["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertIn("CAUTION", first["predictions"]["mission_health_summary"])

        delayed = self._post_telemetry(
            {
                "stress": {"stress_level_id": 0, "stress_state": "Relaxed", "alert": False},
                "anomaly_detection": {"anomaly_flag": False, "severity": "NOMINAL"},
                "mission_health_summary": "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)",
            },
            older,
        )
        self.assertEqual(delayed["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertTrue(delayed["predictions"]["stress"]["alert"])
        self.assertIn("STRESS", delayed["ingestion"]["skipped_outdated_alert_categories"])
        self.assertIn("stress", delayed["ingestion"]["skipped_outdated_predictions"])
        self.assertTrue(delayed["ingestion"]["incoming_summary_ignored"])
        self.assertIn("CAUTION", delayed["predictions"]["mission_health_summary"])
        self.assertNotIn(
            "ALL SYSTEMS NOMINAL", delayed["predictions"]["mission_health_summary"]
        )
        self.assertEqual(delayed["mission_assessment"]["level"], "caution")

        chat = self.client.post(
            "/api/chat",
            json={"message": "Any stress alerts?", "snapshot_id": delayed["snapshot_id"]},
        )
        self.assertEqual(chat.status_code, 200)
        prompt = self.captured_messages[0]["content"]
        self.assertIn("CAUTION: Elevated Cognitive/Physical Stress", prompt)
        self.assertIn("Assessment level: caution", prompt)
        self.assertIn("Active alert categories: ['STRESS']", prompt)
        self.assertNotIn("ALL SYSTEMS NOMINAL", prompt)

        stored = snapshot_store.get_by_id(delayed["snapshot_id"])
        self.assertEqual(
            stored["predictions"]["mission_health_summary"],
            delayed["predictions"]["mission_health_summary"],
        )
        flat = snapshot_store.build_api_telemetry_view(stored)
        self.assertEqual(
            flat["mission_health_summary"],
            delayed["predictions"]["mission_health_summary"],
        )
        self.assertEqual(flat["mission_assessment"]["level"], "caution")

    def test_failed_evaluation_is_reported_to_chat_as_uncertain(self):
        now = datetime.now(timezone.utc)
        active_ts = (now - timedelta(minutes=3)).isoformat()
        failed_ts = (now - timedelta(minutes=1)).isoformat()

        self._post_telemetry(
            {
                "stress": {"alert": True, "stress_state": "High"},
                "anomaly_detection": {"anomaly_flag": False, "severity": "NOMINAL"},
                "mission_health_summary": "CAUTION: Elevated Cognitive/Physical Stress",
            },
            active_ts,
        )
        failed = self._post_telemetry(
            {
                "stress": {"error": "inference timeout"},
                "mission_health_summary": "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)",
            },
            failed_ts,
        )
        summary = failed["predictions"]["mission_health_summary"]
        self.assertIn("CAUTION", summary)
        self.assertIn("failed", summary.lower())
        self.assertNotIn("ALL SYSTEMS NOMINAL", summary)
        self.assertEqual(failed["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertEqual(
            failed["alerts"]["categories"]["STRESS"]["last_evaluation"], "failed"
        )

        chat = self.client.post(
            "/api/chat",
            json={"message": "Status?", "snapshot_id": failed["snapshot_id"]},
        )
        self.assertEqual(chat.status_code, 200)
        prompt = self.captured_messages[0]["content"]
        self.assertIn("last_evaluation=failed", prompt)
        self.assertIn("inference timeout", prompt)
        self.assertIn("WARNING: The latest evaluation failed", prompt)
        self.assertNotIn("ALL SYSTEMS NOMINAL", prompt)

    def test_mixed_packet_summary_reflects_final_category_states(self):
        now = datetime.now(timezone.utc)
        stale_ts = (now - timedelta(minutes=5)).isoformat()
        anomaly_ts = (now - timedelta(minutes=2)).isoformat()
        stress_ts = now.isoformat()

        self._post_telemetry(
            {
                "stress": {"alert": False},
                "anomaly_detection": {"anomaly_flag": False, "severity": "NOMINAL"},
                "mission_health_summary": "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)",
            },
            stale_ts,
        )
        # Stress-only update: ANOMALY keeps its older accepted assessment.
        self._post_telemetry(
            {
                "stress": {"alert": True},
                "mission_health_summary": "CAUTION: Elevated Cognitive/Physical Stress",
            },
            stress_ts,
        )
        # Anomaly is newer than its last accepted assessment; stress is older.
        mixed = self._post_telemetry(
            {
                "stress": {"alert": False},
                "anomaly_detection": {"anomaly_flag": True, "severity": "CRITICAL"},
                "mission_health_summary": "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)",
            },
            anomaly_ts,
        )
        self.assertEqual(mixed["alerts"]["categories"]["STRESS"]["status"], "active")
        self.assertEqual(mixed["alerts"]["categories"]["ANOMALY"]["status"], "active")
        self.assertEqual(
            mixed["ingestion"]["skipped_outdated_alert_categories"], ["STRESS"]
        )
        summary = mixed["predictions"]["mission_health_summary"]
        self.assertIn("ALERT: Physiological Anomaly Detected", summary)
        self.assertIn("CAUTION: Elevated Cognitive/Physical Stress", summary)
        self.assertNotIn("ALL SYSTEMS NOMINAL", summary)
        self.assertEqual(mixed["mission_assessment"]["level"], "alert")
        self.assertEqual(
            sorted(mixed["mission_assessment"]["active_categories"]),
            ["ANOMALY", "STRESS"],
        )


if __name__ == "__main__":
    unittest.main()
