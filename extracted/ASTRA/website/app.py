"""
OrbitNexus – Autonomous Astronaut Health Monitoring System
==========================================================
Flask + Flask-SocketIO backend for the OrbitNexus dashboard.
Serves the UI, streams simulated telemetry via WebSockets,
exposes ML inference endpoints, and provides an LLM assistant
powered by Groq (Llama-3.3-70b).
"""

import os
import time
import math
import threading
from datetime import datetime, timezone

import numpy as np
from flask import Flask, render_template, request, jsonify, send_from_directory

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"))
    load_dotenv()
except ImportError:
    pass
from flask_socketio import SocketIO, emit
from flask_cors import CORS

from ai_orchestrator import AIOrchestrator
from astra_engine import AstraIntelligenceEngine
from trusted_web_rag import TrustedWebRAG, build_search_query
from memory_manager import MemoryManager
from provider_config import get_llm_provider_status, invoke_groq_chat
from security_manager import SecurityManager
from sensor_pipeline import SensorSimulator, SensorNormalizer, DeviceCapabilityRegistry, AlertEngine, WearableIntegrationManager
from telemetry_snapshot import (
    LIVE_REFRESH_SECONDS,
    alert_category_updates_from_predictions,
    extract_readings_from_packet,
    resolve_observation_time,
    snapshot_store,
)
from voice_assistant import VoiceAssistant

# ── Online Learning imports ──────────────────────────────────────────────────
from sklearn.linear_model import SGDClassifier
from collections import deque

# ─────────────────────────────────────────────────────────────────────────────
# App Initialisation
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24).hex()
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Load ML engine ───────────────────────────────────────────────────────────
engine = AstraIntelligenceEngine()
sensor_simulator = SensorSimulator()
capability_registry = DeviceCapabilityRegistry()
alert_engine = AlertEngine()
wearable_manager = WearableIntegrationManager()
memory_manager = MemoryManager()
medical_rag = TrustedWebRAG()
voice_assistant = VoiceAssistant()
security_manager = SecurityManager()
orchestrator = AIOrchestrator()

# ── Provider configuration ──────────────────────────────────────────────────
provider_status = get_llm_provider_status()
provider_name = provider_status["provider"]
groq_client = None
active_groq_model = provider_status["model"]

try:
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if api_key and provider_name == "groq":
        groq_client = Groq(api_key=api_key, timeout=provider_status["timeout_seconds"])
        print(f"[GROQ] Connected! Active AI Model: {active_groq_model}")
    else:
        print("[LLM] Provider is configured as groq but no API key is present; chat requests will stay offline.")
except ImportError:
    print("⚠ groq package not installed – /api/chat will be unavailable.")

SYSTEM_PROMPT = (
    "You are OrbitNexus AI, the Autonomous Space Therapeutics & Health Intelligence System "
    "aboard the International Space Station. You monitor astronaut health telemetry in real-time "
    "and explain space-health research. Use plain language, explain unfamiliar terms, "
    "prioritize crew safety, and provide clear structured answers. "
    "Use the recent health memory context when relevant and maintain continuity across sessions. "
    "Answer only what was asked and reply in the user's language. "
    "Never open a reply with a telemetry or status recap, and never volunteer current vital signs, "
    "ML predictions, mission status or warnings unless the user explicitly asks about them or they are "
    "essential to a safe answer. Telemetry context is reference data, not a status report to repeat. "
    "Use the [Approved Web Reference Context] for medical and research information beyond telemetry. "
    "Cite supplied source IDs such as [S1] when using their evidence. Never invent references, "
    "diagnoses, drug doses, or procedures that those sources do not support. Explain when the "
    "selected research pages do not contain the requested treatment guidance, and suggest consulting "
    "the mission medical team or an applicable approved onboard protocol. "
    "Clearly separate measured crew data from reference knowledge and simulation. "
    "Keep replies concise: prefer a short direct answer over a long structured report. "
    "Use short paragraphs and simple numbered steps when explaining actions. "
    "For Arabic replies, explain technical terms in Arabic first and include the English term "
    "in parentheses only when useful. Keep values and units together. Avoid Markdown tables "
    "and long mixed-language sentences; use simple paragraphs or lists instead."
)
chat_history: list[dict] = []  # last 10 user+assistant turns
chat_lock = threading.Lock()

# ── Mission Clock ────────────────────────────────────────────────────────────
MISSION_START = time.time()

def mission_elapsed() -> str:
    """Return MET as 'Xd HH:MM:SS'."""
    elapsed = int(time.time() - MISSION_START)
    days, rem = divmod(elapsed, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"

# ── Canonical telemetry snapshot (dashboard + chat share this store) ─────────
# Legacy names kept as thin accessors so older helpers/tests can still import them.
telemetry_lock = snapshot_store._lock  # noqa: SLF001 — shared RLock for callers


def _telemetry_flat() -> dict:
    """Deep-copied flat view of the current snapshot (empty dict if none)."""
    return snapshot_store.build_api_telemetry_view()


# ── Simulation Control ───────────────────────────────────────────────────────
simulation_active = threading.Event()
simulation_thread = None
_sim_tick = 0
_sim_tick_lock = threading.Lock()


def _next_sim_tick() -> int:
    """Single shared tick counter so REST and Socket.IO do not diverge."""
    global _sim_tick
    with _sim_tick_lock:
        tick = _sim_tick
        _sim_tick += 1
        return tick


def _commit_simulated_observation(vitals_display: dict, predictions: dict) -> dict:
    """Write one simulated observation into the canonical store and return it."""
    return snapshot_store.commit_complete(
        readings=vitals_display,
        predictions=predictions,
        source="simulation",
        alert_category_updates=alert_category_updates_from_predictions(predictions),
        mission_elapsed=mission_elapsed(),
    )


def ensure_live_snapshot() -> dict:
    """
    Return the canonical live snapshot for REST polling.

    Generation strategy (one coherent stream):
    - If the Socket.IO simulation loop is already active, only read the store
      (do not generate a second independent stream).
    - Otherwise REST polling drives simulation: generate when empty or when the
      current snapshot is older than LIVE_REFRESH_SECONDS, then persist it so
      /api/chat sees the same data the dashboard displays.
    """
    current = snapshot_store.get_current()
    age = snapshot_store.current_age_seconds()

    if simulation_active.is_set():
        if current is not None:
            return current
        # Race at loop start: seed one observation into the shared store.
        packet, vitals_display = _generate_telemetry(_next_sim_tick())
        predictions = engine.process_astronaut_telemetry(packet)
        return _commit_simulated_observation(vitals_display, predictions)

    needs_refresh = current is None or age is None or age >= LIVE_REFRESH_SECONDS
    if not needs_refresh:
        return current

    packet, vitals_display = _generate_telemetry(_next_sim_tick())
    predictions = engine.process_astronaut_telemetry(packet)
    return _commit_simulated_observation(vitals_display, predictions)

# ── Online Learner ───────────────────────────────────────────────────────────
online_learner = SGDClassifier(loss="log_loss", random_state=42, warm_start=False)
ol_fitted = False            # True after first partial_fit call
ol_n_updates = 0
ol_accuracy_window: deque = deque(maxlen=50)  # rolling window for drift detection
ol_accuracy_history: list[float] = []
ol_lock = threading.Lock()

# ── Smartwatch & Mode Management ─────────────────────────────────────────────
smartwatch_state = {
    "connected": True,
    "device_name": "OrbitWatch-01 (Medical BLE Suite)",
    "device_id": "BLE-MED-ARES-77",
    "protocol": "Bluetooth Low Energy 5.3 / Wear OS Health Services",
    "sample_rate_hz": 125,
    "battery_percent": 94,
    "signal_quality_sqi": 0.98,
    "sensors_active": [
        "PPG-dual",
        "ECG-single-lead",
        "IMU-9DoF",
        "GSR-EDA",
        "HeatFlux-Temp",
        "Bioimpedance-Hydration"
    ],
    "last_sync": datetime.now(timezone.utc).isoformat()
}
smartwatch_lock = threading.Lock()
current_mode = "demo"  # "demo" or "realtime"
mode_lock = threading.Lock()


def evaluate_disease_risks(telemetry_ctx: dict) -> list[dict]:
    hr = float(telemetry_ctx.get("heart_rate_bpm", 75.0))
    hrv = float(telemetry_ctx.get("hrv_rmssd", 48.0))
    spo2 = float(telemetry_ctx.get("spo2_percent", 98.5))
    resp = float(telemetry_ctx.get("respiration_rate", 16.0))
    temp = float(telemetry_ctx.get("core_temp_c", 36.8))
    blood_water = float(telemetry_ctx.get("blood_water_percent", 62.4))
    sbp = float(telemetry_ctx.get("systolic_bp", 120.0))
    dbp = float(telemetry_ctx.get("diastolic_bp", 78.0))
    activity = telemetry_ctx.get("activity", "Resting")
    if isinstance(activity, dict):
        activity = activity.get("activity_name", "Resting")

    # 1. Cardiovascular Deconditioning & Spaceflight Arrhythmia
    cv_risk = "Nominal"
    cv_score = 12
    if hr > 100 or hrv < 25 or sbp > 140:
        cv_risk = "Elevated"
        cv_score = 68
    elif hr > 88 or hrv < 35:
        cv_risk = "Mild Strain"
        cv_score = 38

    # 2. Hypoxia & Acute Respiratory Distress
    hypoxia_risk = "Nominal"
    hypoxia_score = 8
    if spo2 < 93.0 or resp > 24.0:
        hypoxia_risk = "Critical"
        hypoxia_score = 92
    elif spo2 < 96.0 or resp > 20.0:
        hypoxia_risk = "Caution"
        hypoxia_score = 45

    # 3. SANS (Spaceflight-Associated Neuro-Ocular Syndrome)
    sans_risk = "Monitored"
    sans_score = 24
    if blood_water > 64.0 and sbp > 130:
        sans_risk = "Elevated ICP"
        sans_score = 54

    # 4. Microgravity Osteopenia & Demineralization
    osteo_risk = "Active Countermeasure Required"
    osteo_score = 42
    if "exercise" in str(activity).lower():
        osteo_risk = "Countermeasure Active (ARED Loading)"
        osteo_score = 20

    # 5. Cosmic Radiation Induced DNA Damage
    rad_risk = "Nominal (Within ALARA Limits)"
    rad_score = 18

    # 6. Acute Decompression Sickness (DCS) & EVA Hypothermia
    dcs_risk = "Nominal (P_suit 4.3 psi Safe)"
    dcs_score = 10
    if temp < 36.0:
        dcs_risk = "Mild Hypothermia Watch"
        dcs_score = 48

    return [
        {
            "id": "cardio_deconditioning",
            "name": "Cardiovascular Deconditioning & Arrhythmia",
            "category": "Cardiovascular",
            "risk_level": cv_risk,
            "risk_score": cv_score,
            "biomarkers": f"HR: {hr:.1f} bpm | HRV: {hrv:.1f} ms | BP: {int(sbp)}/{int(dbp)}",
            "pathology": "Fluid cephalad shift (~2L) reduces lower-body preload, inducing left-ventricular remodeling and orthostatic intolerance.",
            "protocol": "Daily 30-min ARED ergometer cycle; LBNP (Lower Body Negative Pressure) countermeasure 20 mmHg."
        },
        {
            "id": "hypoxia_respiratory",
            "name": "Hypoxia & Ventilation/Perfusion Mismatch",
            "category": "Respiratory",
            "risk_level": hypoxia_risk,
            "risk_score": hypoxia_score,
            "biomarkers": f"SpO2: {spo2:.1f}% | Resp: {resp:.1f} brpm",
            "pathology": "Absence of hydrostatic pleural gradients creates regional V/Q mismatch. Cabin PO2 drop risks arterial desaturation.",
            "protocol": "Automatic cabin atmosphere scrubber enrichment; supplemental O2 delivery mask on standby."
        },
        {
            "id": "sans_neuro_ocular",
            "name": "SANS (Spaceflight-Associated Neuro-Ocular Syndrome)",
            "category": "Neuro-Ophthalmology",
            "risk_level": sans_risk,
            "risk_score": sans_score,
            "biomarkers": f"Blood Water: {blood_water:.1f}% | SBP: {int(sbp)} mmHg",
            "pathology": "Cephalad venous engorgement elevates chronic intracranial pressure (ICP), causing posterior globe flattening and optic disc edema.",
            "protocol": "Chibis LBNP suit sessions; optical coherence tomography (OCT) funduscopy twice weekly."
        },
        {
            "id": "microgravity_osteopenia",
            "name": "Microgravity Osteopenia & Demineralization",
            "category": "Musculoskeletal",
            "risk_level": osteo_risk,
            "risk_score": osteo_score,
            "biomarkers": f"Activity: {activity} | Hydration: {blood_water:.1f}%",
            "pathology": "Unloading of skeletal weight-bearing trabeculae causes bone mineral density loss of 1.0-1.5%/month, increasing renal calculi risk.",
            "protocol": "Daily ARED heavy axial loading (squats/deadlifts) + bisphosphonates + 2.5L daily hydration target."
        },
        {
            "id": "cosmic_radiation_dna",
            "name": "Cosmic Radiation DNA Damage & Genomic Stress",
            "category": "Radiobiology",
            "risk_level": rad_risk,
            "risk_score": rad_score,
            "biomarkers": "NASA GeneLab OSD-178 Transcriptomic Signature (100% Adaptation Index)",
            "pathology": "High-Z and high-energy (HZE) cosmic ions induce clustered double-strand DNA breaks and oxidative stress.",
            "protocol": "Crew quarters water-wall shielding; antioxidant dietary regimen; real-time active dosimeter tracking."
        },
        {
            "id": "dcs_hypothermia",
            "name": "Acute Decompression Sickness (DCS) & Hypothermia",
            "category": "EVA Life Support",
            "risk_level": dcs_risk,
            "risk_score": dcs_score,
            "biomarkers": f"Core Temp: {temp:.2f}°C | Skin Temp: {telemetry_ctx.get('skin_temp_c', 33.5):.1f}°C",
            "pathology": "Rapid cabin-to-suit depressurization can trigger inert N2 gas phase bubble formation in periarticular tissues.",
            "protocol": "100% O2 pre-breathe campout protocol prior to airlock depressurization; EMU thermal garment regulation."
        }
    ]

# ═════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/src/<path:filename>")
def serve_src(filename):
    src_dir = os.path.join(os.path.dirname(__file__), "src")
    return send_from_directory(src_dir, filename)


@app.route("/api/smartwatch/status", methods=["GET", "POST"])
def api_smartwatch_status():
    global smartwatch_state
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        with smartwatch_lock:
            if "connected" in data:
                smartwatch_state["connected"] = bool(data["connected"])
            smartwatch_state["last_sync"] = datetime.now(timezone.utc).isoformat()
            if not smartwatch_state["connected"]:
                smartwatch_state["battery_percent"] = 0
                smartwatch_state["signal_quality_sqi"] = 0.0
            else:
                smartwatch_state["battery_percent"] = 94
                smartwatch_state["signal_quality_sqi"] = 0.98
        return jsonify({"status": "updated", "smartwatch": smartwatch_state})
    with smartwatch_lock:
        return jsonify(smartwatch_state)


@app.route("/api/mode", methods=["GET", "POST"])
def api_mode():
    global current_mode
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        req_mode = data.get("mode", "demo").lower()
        if req_mode not in {"demo", "realtime"}:
            return jsonify({"error": "Invalid mode. Must be 'demo' or 'realtime'"}), 400

        with smartwatch_lock:
            sw_connected = smartwatch_state["connected"]

        if req_mode == "realtime" and not sw_connected:
            return jsonify({
                "status": "error",
                "error": "smartwatch_disconnected",
                "message": "No smartwatch detected! OrbitWatch-01 BLE stream unavailable. Returning to demo mode.",
                "mode": "demo"
            }), 400

        with mode_lock:
            current_mode = req_mode
        return jsonify({"status": "ok", "mode": current_mode})
    with mode_lock:
        return jsonify({"mode": current_mode})


@app.route("/api/disease_detection", methods=["GET"])
def api_disease_detection():
    try:
        ctx = _telemetry_flat()
        risks = evaluate_disease_risks(ctx)
        return jsonify({
            "status": "operational",
            "evaluator": "OrbitNexus Aerospace Clinical Decision Engine",
            "mission_elapsed_time": mission_elapsed(),
            "diseases": risks,
            "count": len(risks),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/models/status", methods=["GET"])
def api_models_status():
    loaded = list(engine.models.keys())
    return jsonify({
        "status": "operational",
        "loaded_models": loaded,
        "count": len(loaded),
        "models": {
            "activity": {
                "name": "Activity Recognition",
                "dataset": "PAMAP2 (12 Physical Activities, 3 IMUs)",
                "type": "Random Forest Classifier (GPU/Scikit)",
                "accuracy": "99.62%",
                "status": "online" if "activity" in engine.models else "offline",
                "features_expected": 22
            },
            "stress": {
                "name": "Cognitive Stress & Autonomic State",
                "dataset": "WESAD (Multimodal Wearable Stress)",
                "type": "XGBoost Classifier",
                "accuracy": "89.67%",
                "status": "online" if "stress" in engine.models else "offline",
                "features_expected": 5
            },
            "heart": {
                "name": "PPG Continuous Heart Rate Estimator",
                "dataset": "BIDMC Pulse Oximeter Waveform",
                "type": "Random Forest Regressor",
                "mae": "1.10 BPM",
                "status": "online" if "heart" in engine.models else "offline",
                "features_expected": 9
            },
            "anomaly": {
                "name": "Physiological Anomaly & Hypoxia Detector",
                "dataset": "ISS Simulation Biosensor Suite",
                "type": "Isolation Forest (Unsupervised)",
                "metric": "Contamination 0.05",
                "status": "online" if "anomaly" in engine.models else "offline",
                "features_expected": 5
            },
            "space_bio": {
                "name": "NASA Spaceflight Adaptation Genomics",
                "dataset": "NASA GeneLab OSD-178 (Spaceflight vs Ground)",
                "type": "Support Vector Classifier / Random Forest",
                "accuracy": "100.0%",
                "status": "online" if "space_bio" in engine.models else "offline",
                "features_expected": 50
            }
        }
    })


@app.route("/api/telemetry/live", methods=["GET"])
def api_telemetry_live():
    # Persist into the canonical store so /api/chat can use the same snapshot.
    snap = ensure_live_snapshot()
    ctx = snapshot_store.build_api_telemetry_view(snap)
    risks = evaluate_disease_risks(ctx)
    return jsonify({
        "snapshot_id": snap.get("snapshot_id"),
        "version": snap.get("version"),
        "observed_at": snap.get("observed_at"),
        "received_at": snap.get("received_at"),
        "source": snap.get("source"),
        "telemetry": ctx,
        "mission_assessment": snap.get("mission_assessment"),
        "disease_risks": risks,
        "smartwatch": smartwatch_state,
        "mode": current_mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/telemetry/demo", methods=["GET", "POST"])
def api_telemetry_demo():
    """
    Publish the Trial Demo fixture as a server-owned snapshot.

    Dashboard Demo mode must bind to this snapshot so chat discusses the same
    readings the UI displays (never a leftover live snapshot).
    """
    snap = snapshot_store.commit_demo_snapshot(mission_elapsed=mission_elapsed())
    ctx = snapshot_store.build_api_telemetry_view(snap)
    risks = evaluate_disease_risks(ctx)
    return jsonify({
        "snapshot_id": snap.get("snapshot_id"),
        "version": snap.get("version"),
        "observed_at": snap.get("observed_at"),
        "received_at": snap.get("received_at"),
        "source": snap.get("source"),
        "telemetry": ctx,
        "mission_assessment": snap.get("mission_assessment"),
        "disease_risks": risks,
        "mode": "demo",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/status", methods=["GET"])
def api_status():
    try:
        loaded = list(engine.models.keys())
        return jsonify({
            "status": "operational",
            "mission_elapsed_time": mission_elapsed(),
            "uptime_seconds": round(time.time() - MISSION_START, 1),
            "loaded_models": loaded,
            "models_count": len(loaded),
            "simulation_active": simulation_active.is_set(),
            "online_learner_updates": ol_n_updates,
            "provider": get_llm_provider_status(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/provider_status", methods=["GET"])
def provider_status_route():
    return jsonify(get_llm_provider_status())


@app.route("/api/system/overview", methods=["GET"])
def api_system_overview():
    try:
        snapshot = sensor_simulator.generate_snapshot()
        return jsonify({
            "system_status": "operational",
            "features": [
                "telemetry monitoring",
                "AI risk analysis",
                "device capability discovery",
                "wearable integration abstraction",
                "alert engine",
                "memory retention",
                "voice interaction",
                "security session controls",
                "dashboard orchestration",
            ],
            "device_id": snapshot["device_id"],
            "device_name": snapshot["device_name"],
            "provider": get_llm_provider_status(),
            "capabilities": capability_registry.discover({
                "heart_rate": True,
                "spo2": True,
                "temperature": True,
                "respiration_rate": True,
                "activity": True,
                "sleep": False,
                "blood_pressure": False,
            }),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "system_status": "degraded"}), 500


@app.before_request
def _security_guard():
    if request.method == "OPTIONS":
        return None
    if request.path.startswith("/static") or request.path.startswith("/src"):
        return None
    if request.path in {
        "/", "/api/status", "/api/provider_status", "/api/sensors/overview",
        "/api/sensors/simulate", "/api/devices/capabilities", "/api/devices/integrations",
        "/api/alerts/active", "/api/memory/summary", "/api/voice/status",
        "/api/security/status", "/api/orchestrate/summary", "/api/disease_detection",
        "/api/smartwatch/status", "/api/mode", "/api/models/status", "/api/telemetry/live",
        "/api/online_learn/stats"
    }:
        security_manager.audit_event("allowlisted_request", {"path": request.path, "method": request.method})
        return None

    token = request.headers.get("X-Session-Token")
    session_id = request.headers.get("X-Session-ID", "default-session")
    if token:
        if not security_manager.verify_session_token(token, session_id):
            return jsonify({"error": "invalid session token"}), 401
        security_manager.audit_event("verified_session", {"path": request.path, "session_id": session_id})
        return None

    security_manager.audit_event("anonymous_api_access", {"path": request.path, "method": request.method})
    return None


@app.route("/api/security/status", methods=["GET"])
def api_security_status():
    return jsonify(security_manager.get_security_status())


@app.route("/api/memory/summary", methods=["GET"])
def api_memory_summary():
    return jsonify(memory_manager.get_summary())


@app.route("/api/voice/status", methods=["GET"])
def api_voice_status():
    return jsonify({
        "voice_enabled": voice_assistant.voice_enabled,
        "message": "Browser voice input/output is enabled when supported by the client device.",
    })


@app.route("/api/orchestrate/summary", methods=["GET"])
def api_orchestrate_summary():
    snapshot = sensor_simulator.generate_snapshot()
    alert = alert_engine.evaluate({
        "heart_rate": snapshot["heart_rate"]["value"],
        "spo2": snapshot["spo2"]["value"],
        "respiration_rate": snapshot["respiration_rate"]["value"],
        "temperature": snapshot["temperature"]["value"],
        "quality": "good",
    })
    summary = orchestrator.build_summary(
        telemetry={
            "heart_rate_bpm": snapshot["heart_rate"]["value"],
            "spo2_percent": snapshot["spo2"]["value"],
            "respiration_rate": snapshot["respiration_rate"]["value"],
        },
        alerts=[alert] if alert else [],
        memory=memory_manager.get_recent_context(limit=5),
        capabilities=["heart_rate", "spo2", "temperature", "respiration_rate"],
    )
    return jsonify(summary)


@app.route("/api/sensors/overview", methods=["GET"])
def api_sensor_overview():
    snapshot = sensor_simulator.generate_snapshot()
    return jsonify({
        "device_id": snapshot["device_id"],
        "device_name": snapshot["device_name"],
        "source": snapshot["source"],
        "status": snapshot["status"],
        "capabilities": snapshot["capabilities"],
        "readings": {k: SensorNormalizer.normalize(v) for k, v in snapshot.items() if k in {"heart_rate", "spo2", "temperature", "respiration_rate", "hrv", "skin_temp"}},
        "timestamp": snapshot["timestamp"],
    })


@app.route("/api/sensors/simulate", methods=["POST"])
def api_sensor_simulate():
    snapshot = sensor_simulator.generate_snapshot()
    return jsonify({
        "status": "ok",
        "device_id": snapshot["device_id"],
        "snapshot": snapshot,
    })


@app.route("/api/devices/capabilities", methods=["GET"])
def api_device_capabilities():
    snapshot = sensor_simulator.generate_snapshot()
    capabilities = {
        "device_id": snapshot["device_id"],
        "device_name": snapshot["device_name"],
        "capabilities": capability_registry.discover({
            "heart_rate": True,
            "hrv": True,
            "spo2": True,
            "respiration_rate": True,
            "temperature": True,
            "skin_temp": True,
            "activity": True,
            "sleep": False,
            "blood_pressure": False,
            "ecg": False,
        }),
        "integrations": wearable_manager.list_adapters(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify(capabilities)


@app.route("/api/devices/integrations", methods=["GET"])
def api_device_integrations():
    return jsonify({
        "providers": wearable_manager.list_adapters(),
        "selected_provider": "wear_os",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/alerts/active", methods=["GET"])
def api_active_alerts():
    snapshot = sensor_simulator.generate_snapshot()
    metrics = {
        "heart_rate": snapshot["heart_rate"]["value"],
        "spo2": snapshot["spo2"]["value"],
        "respiration_rate": snapshot["respiration_rate"]["value"],
        "temperature": snapshot["temperature"]["value"],
        "quality": "good",
    }
    alert = alert_engine.evaluate(metrics)
    payload = {
        "device_id": snapshot["device_id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "alerts": [alert] if alert else [],
    }
    return jsonify(payload)


@app.route("/api/telemetry", methods=["POST"])
def api_telemetry():
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Empty or invalid JSON body"}), 400

        received_at = datetime.now(timezone.utc).isoformat()
        time_result = resolve_observation_time(
            data.get("observed_at"),
            received_at=received_at,
        )
        if not time_result["ok"]:
            return jsonify({
                "error": time_result["error"],
                "validation": time_result,
            }), 400

        results = engine.process_astronaut_telemetry(data)
        readings = extract_readings_from_packet(data)
        # Category-aware alerts from successful model outputs only — never infer
        # an all-clear merely because feature keys were present.
        category_updates = alert_category_updates_from_predictions(results)
        snap = snapshot_store.commit_partial(
            readings=readings,
            predictions=results,
            source=str(data.get("source") or "api_post"),
            observed_at=time_result["observed_at"],
            received_at=time_result["received_at"],
            observation_time_basis=time_result["basis"],
            alert_category_updates=category_updates,
            mission_elapsed=mission_elapsed(),
            replace_predictions=False,
        )

        return jsonify({
            "snapshot_id": snap.get("snapshot_id"),
            "version": snap.get("version"),
            "observed_at": snap.get("observed_at"),
            "received_at": snap.get("received_at"),
            "observation_time_basis": snap.get("observation_time_basis"),
            "observation_time_warning": time_result.get("warning"),
            "source": snap.get("source"),
            "readings": snap.get("readings") or {},
            # Canonical merged predictions (outdated category updates omitted).
            "predictions": snap.get("predictions") or {},
            "alerts": snap.get("alerts"),
            "mission_assessment": snap.get("mission_assessment"),
            "ingestion": snap.get("ingestion") or {},
            "mission_elapsed_time": mission_elapsed(),
            "timestamp": received_at,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        body = request.get_json(force=True)
        user_msg = (body or {}).get("message", "").strip()
        if not user_msg:
            return jsonify({"error": "message field is required"}), 400

        # Client may send the displayed snapshot_id; server resolves trusted data.
        # Arbitrary client-provided vital values are ignored.
        requested_snapshot_id = (body or {}).get("snapshot_id")
        resolution = snapshot_store.resolve_for_chat(requested_snapshot_id)
        telem_context = snapshot_store.build_llm_context(resolution)
        memory_context = memory_manager.build_context_prompt(limit=5)

        # Retrieve outside the chat lock. Explicit follow-ups reuse the last
        # user topic; short new questions do not inherit generated medical claims.
        with chat_lock:
            rag_query = build_search_query(user_msg, chat_history)
        retrieval = medical_rag.retrieve(rag_query)
        rag_context = retrieval["context"]

        with chat_lock:
            chat_history.append({"role": "user", "content": user_msg})
            if len(chat_history) > 20:
                del chat_history[:2]
            messages = [
                {
                    "role": "system",
                    "content": (
                        SYSTEM_PROMPT
                        + "\n\n"
                        + memory_context
                        + "\n"
                        + rag_context
                        + "\n"
                        + telem_context
                    ),
                },
                *chat_history,
            ]

        result = invoke_groq_chat(
            messages,
            model=active_groq_model,
            temperature=0.6,
            max_tokens=1024,
        )
        if result.get("status") in {"error", "unavailable"}:
            fallback_reply = result.get("reply", "OrbitNexus AI is unavailable.")
            return jsonify({
                "reply": fallback_reply,
                "source": result.get("source", "system"),
                "status": result.get("status"),
                "error_type": result.get("error_type"),
                "voice": voice_assistant.build_reply(fallback_reply),
                "snapshot_id": resolution.get("used_id"),
                "snapshot_selection": resolution.get("selection"),
                "knowledge_sources": retrieval["sources"],
                "knowledge_status": retrieval["status"],
            })

        reply = result["reply"]
        memory_manager.add_event("chat", user_msg, {
            "reply": reply,
            "source": result.get("source", active_groq_model),
            "snapshot_id": resolution.get("used_id"),
        })
        with chat_lock:
            chat_history.append({"role": "assistant", "content": reply})

        return jsonify({
            "reply": reply,
            "source": result.get("source", active_groq_model),
            "status": result["status"],
            "voice": voice_assistant.build_reply(reply),
            "snapshot_id": resolution.get("used_id"),
            "snapshot_selection": resolution.get("selection"),
            "snapshot_note": resolution.get("note"),
            "knowledge_sources": retrieval["sources"],
            "knowledge_status": retrieval["status"],
        })
    except Exception as e:
        return jsonify({"error": str(e), "source": "system"}), 500


@app.route("/api/online_learn", methods=["POST"])
def api_online_learn():
    """Accept a labeled data point and update the online SGDClassifier."""
    global ol_fitted, ol_n_updates
    try:
        body = request.get_json(force=True)
        # Support both {"features": [...], "label": 0} and {"heart_rate": 75, "spo2": 98, ...}
        if "features" in body and isinstance(body["features"], list):
            features = body["features"]
        else:
            # Derive 5 features: [heart_rate, hrv_rmssd, eda, skin_temp, respiration_rate]
            hr = float(body.get("heart_rate", 75.0))
            temp = float(body.get("temperature", 36.8))
            rr = float(body.get("respiration_rate", 16.0))
            features = [hr, 50.0 - (hr - 70.0) * 0.4, 2.0 + (hr - 70.0) * 0.04, temp - 3.2, rr]

        raw_label = body.get("label", 0)
        label_map = {"relaxed": 0, "baseline": 0, "rest": 0, "moderate": 1, "stressed": 1, "stress": 1, "recovery": 2}
        if isinstance(raw_label, str):
            label = label_map.get(raw_label.lower().strip(), 0)
        else:
            label = int(raw_label)

        X = np.array(features, dtype=float).reshape(1, -1)
        y = np.array([label])

        with ol_lock:
            if not ol_fitted:
                online_learner.partial_fit(X, y, classes=np.array([0, 1, 2]))
                ol_fitted = True
            else:
                online_learner.partial_fit(X, y)

            # Track accuracy on this sample (self-evaluation)
            pred = int(online_learner.predict(X)[0])
            correct = int(pred == label)
            ol_accuracy_window.append(correct)
            ol_n_updates += 1

            rolling_acc = sum(ol_accuracy_window) / len(ol_accuracy_window)
            ol_accuracy_history.append(rolling_acc)

        return jsonify({
            "status": "updated",
            "n_updates": ol_n_updates,
            "latest_prediction": pred,
            "correct": bool(correct),
            "rolling_accuracy": round(rolling_acc, 4),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/online_learn/stats", methods=["GET"])
def api_online_learn_stats():
    try:
        with ol_lock:
            window_list = list(ol_accuracy_window)
            rolling_acc = (sum(window_list) / len(window_list)) if window_list else None
            # Concept drift detection: compare first and second half of window
            drift_detected = False
            if len(window_list) >= 20:
                mid = len(window_list) // 2
                first_half = sum(window_list[:mid]) / mid
                second_half = sum(window_list[mid:]) / (len(window_list) - mid)
                if first_half - second_half > 0.15:
                    drift_detected = True

            return jsonify({
                "n_updates": ol_n_updates,
                "fitted": ol_fitted,
                "rolling_accuracy": round(rolling_acc, 4) if rolling_acc is not None else None,
                "window_size": len(window_list),
                "concept_drift_detected": drift_detected,
                "accuracy_history_last_20": [round(a, 4) for a in ol_accuracy_history[-20:]],
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sensors/overview", methods=["GET"])
def api_sensors_overview():
    try:
        ctx = _telemetry_flat()

        sensors = {
            "mission_elapsed_time": mission_elapsed(),
            "status": "nominal",
            "sensors": {
                "ppg_pulse_oximeter": {
                    "type": "Optical Photoplethysmography (PPG)",
                    "target": "Radial / Digital Artery",
                    "sample_rate_hz": 125,
                    "metrics": {
                        "heart_rate_bpm": ctx.get("heart_rate_bpm", 75.0),
                        "spo2_percent": ctx.get("spo2_percent", 98.5),
                        "signal_quality_index": 0.96,
                    },
                    "status": "online",
                },
                "imu_wearable_suite": {
                    "type": "9-DoF Inertial Measurement Unit",
                    "locations": ["wrist", "chest", "ankle"],
                    "sample_rate_hz": 100,
                    "metrics": {
                        "current_activity": ctx.get("activity", {}).get("activity_name", "Resting") if isinstance(ctx.get("activity"), dict) else ctx.get("activity", "Resting"),
                        "activity_confidence": 0.996,
                    },
                    "status": "online",
                },
                "electrodermal_galvanic": {
                    "type": "Galvanic Skin Response (GSR/EDA)",
                    "target": "Palmar / Thenar Eminence",
                    "sample_rate_hz": 4,
                    "metrics": {
                        "eda_microsiemens": ctx.get("eda_microsiemens", 2.2),
                        "hrv_rmssd_ms": ctx.get("hrv_rmssd", 48.0),
                        "stress_state": ctx.get("stress", {}).get("stress_state", "Relaxed") if isinstance(ctx.get("stress"), dict) else ctx.get("stress", "Relaxed"),
                    },
                    "status": "online",
                },
                "respiratory_effort": {
                    "type": "Impedance Pneumography / Plethysmography",
                    "target": "Transthoracic",
                    "sample_rate_hz": 125,
                    "metrics": {
                        "respiration_rate_brpm": ctx.get("respiration_rate", 16.0),
                        "apnea_detected": False,
                    },
                    "status": "online",
                },
                "core_temperature": {
                    "type": "Double-sensor heat flux thermometry",
                    "target": "Temporal / Pre-auricular",
                    "metrics": {
                        "core_temp_c": ctx.get("core_temp_c", 36.8),
                        "skin_temp_c": ctx.get("skin_temp_c", 33.5),
                    },
                    "status": "online",
                },
            },
        }
        return jsonify(sensors)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET EVENTS
# ═════════════════════════════════════════════════════════════════════════════

@socketio.on("connect")
def handle_connect():
    global simulation_thread
    print(f"[WS] Client connected: {request.sid}")
    emit("server_message", {
        "msg": "Connected to OrbitNexus Ground Control",
        "met": mission_elapsed(),
    })
    # Automatically start simulation stream for the connected dashboard
    if not simulation_active.is_set():
        simulation_active.set()
        simulation_thread = socketio.start_background_task(target=_telemetry_loop)
        print("[SIM] Auto-started telemetry simulation stream.")


@socketio.on("disconnect")
def handle_disconnect():
    print(f"[WS] Client disconnected: {request.sid}")


@socketio.on("start_simulation")
def handle_start_simulation(_data=None):
    global simulation_thread
    if not simulation_active.is_set():
        simulation_active.set()
        simulation_thread = socketio.start_background_task(target=_telemetry_loop)
        emit("server_message", {"msg": "Telemetry simulation STARTED", "met": mission_elapsed()})
        print("[SIM] Simulation started.")


@socketio.on("stop_simulation")
def handle_stop_simulation(_data=None):
    if simulation_active.is_set():
        simulation_active.clear()
        emit("server_message", {"msg": "Telemetry simulation STOPPED", "met": mission_elapsed()})
        print("[SIM] Simulation stopped.")


# ═════════════════════════════════════════════════════════════════════════════
#  TELEMETRY SIMULATION LOOP
# ═════════════════════════════════════════════════════════════════════════════

def _generate_telemetry(tick: int) -> dict:
    """
    Produce a realistic simulated astronaut telemetry packet.
    Signals have circadian-like cycles, breathing artefacts, and stochastic noise.
    """
    t = tick * 2.0  # seconds elapsed in simulation

    # ── Heart rate: sinusoidal base + slow drift + noise (65-85 BPM) ─────
    hr_base = 75.0
    hr_circadian = 5.0 * math.sin(2 * math.pi * t / 600)    # 10-min slow cycle
    hr_resp = 1.5 * math.sin(2 * math.pi * t / 4)            # respiratory sinus arrhythmia
    hr_noise = np.random.normal(0, 0.8)
    heart_rate = np.clip(hr_base + hr_circadian + hr_resp + hr_noise, 55, 110)

    # ── SpO2 (97-100%) ──────────────────────────────────────────────────
    spo2 = np.clip(98.5 + 0.5 * math.sin(2 * math.pi * t / 300) + np.random.normal(0, 0.3), 94, 100)

    # ── Respiration rate (12-20 breaths/min) ─────────────────────────────
    resp_rate = np.clip(16.0 + 2.0 * math.sin(2 * math.pi * t / 480) + np.random.normal(0, 0.5), 10, 24)

    # ── Core temperature (36.4-37.2 °C) ─────────────────────────────────
    core_temp = np.clip(36.8 + 0.2 * math.sin(2 * math.pi * t / 900) + np.random.normal(0, 0.05), 36.0, 37.5)

    # ── Blood pressure ───────────────────────────────────────────────────
    systolic = np.clip(120 + 5 * math.sin(2 * math.pi * t / 500) + np.random.normal(0, 2), 100, 145)
    diastolic = np.clip(78 + 3 * math.sin(2 * math.pi * t / 500) + np.random.normal(0, 1.5), 60, 95)

    # ── Stress cycling (EDA / skin conductance) ──────────────────────────
    eda = np.clip(2.0 + 1.5 * math.sin(2 * math.pi * t / 360) + np.random.normal(0, 0.3), 0.5, 6.0)

    # ── Skin temperature ────────────────────────────────────────────────
    skin_temp = np.clip(33.5 + 0.5 * math.sin(2 * math.pi * t / 700) + np.random.normal(0, 0.2), 32.0, 35.5)

    # ── HRV (RMSSD) ─────────────────────────────────────────────────────
    hrv_rmssd = np.clip(45.0 + 10 * math.sin(2 * math.pi * t / 400) + np.random.normal(0, 3), 15, 80)

    # ── Blood water / hydration percentage (~62.4% nominal) ─────────────
    blood_water = np.clip(62.4 + 0.6 * math.sin(2 * math.pi * t / 800) + np.random.normal(0, 0.15), 58.0, 66.0)

    # ── Wrist / Chest / Ankle IMU (22 features) ─────────────────────────
    def _imu_block(temp_base, g_offset=9.8):
        temp = temp_base + np.random.normal(0, 0.3)
        acc = [np.random.normal(0, 0.3), g_offset + np.random.normal(0, 0.2), np.random.normal(0, 0.3)]
        gyro = [np.random.normal(0, 0.05) for _ in range(3)]
        return [temp] + acc + gyro

    imu_features = [float(heart_rate)] + _imu_block(30.5) + _imu_block(33.2) + _imu_block(31.0)

    # ── PPG waveform statistics (9 features) ─────────────────────────────
    ppg_stats = [
        0.45 + np.random.normal(0, 0.02),
        0.12 + np.random.normal(0, 0.01),
        0.15 + np.random.normal(0, 0.01),
        0.85 + np.random.normal(0, 0.03),
        0.70 + np.random.normal(0, 0.02),
        0.48 + np.random.normal(0, 0.02),
        0.35 + np.random.normal(0, 0.02),
        0.05 + np.random.normal(0, 0.01),
        0.20 + np.random.normal(0, 0.01),
    ]

    # ── Gene expression placeholder (50 features) ───────────────────────
    genomics = list(100.0 + np.random.normal(0, 5, 50))

    # ── Compose engine-compatible telemetry packet ───────────────────────
    telemetry_packet = {
        "imu": [float(x) for x in imu_features],
        "stress_physio": [float(heart_rate), float(hrv_rmssd), float(eda), float(skin_temp), float(resp_rate)],
        "ppg_stats": [float(x) for x in ppg_stats],
        "vitals": [float(heart_rate), float(spo2), float(resp_rate), float(systolic), float(core_temp)],
        "genomics": [float(x) for x in genomics],
    }

    display_vitals = {
        "heart_rate_bpm": round(float(heart_rate), 1),
        "spo2_percent": round(float(spo2), 1),
        "respiration_rate": round(float(resp_rate), 1),
        "core_temp_c": round(float(core_temp), 2),
        "blood_water_percent": round(float(blood_water), 1),
        "systolic_bp": round(float(systolic), 0),
        "diastolic_bp": round(float(diastolic), 0),
        "eda_microsiemens": round(float(eda), 2),
        "skin_temp_c": round(float(skin_temp), 1),
        "hrv_rmssd": round(float(hrv_rmssd), 1),
    }

    return telemetry_packet, display_vitals


def _telemetry_loop():
    """Background thread – emits telemetry every 2 s while simulation is active."""
    while simulation_active.is_set():
        try:
            tick = _next_sim_tick()
            packet, vitals_display = _generate_telemetry(tick)
            predictions = engine.process_astronaut_telemetry(packet)
            snap = _commit_simulated_observation(vitals_display, predictions)
            alerts = (snap.get("alerts") or {}).get("items") or []

            payload = {
                # Direct top-level bindings for dashboard UI
                "heart_rate": vitals_display["heart_rate_bpm"],
                "spo2": vitals_display["spo2_percent"],
                "respiration_rate": vitals_display["respiration_rate"],
                "temperature": vitals_display["core_temp_c"],
                "blood_water": vitals_display["blood_water_percent"],
                "blood_pressure": f"{int(vitals_display['systolic_bp'])}/{int(vitals_display['diastolic_bp'])}",
                "activity": predictions.get("activity", {}).get("activity_name", "Resting"),
                "activity_id": predictions.get("activity", {}).get("activity_id", 1),
                "stress": predictions.get("stress", {}).get("stress_state", "Baseline / Relaxed"),
                "stress_level_id": predictions.get("stress", {}).get("stress_level_id", 0),
                "anomaly": predictions.get("anomaly_detection", {}).get("anomaly_flag", False),
                "anomaly_score": predictions.get("anomaly_detection", {}).get("anomaly_score", 0.0),
                "space_bio": predictions.get("space_bio", {}).get("biological_state", "Earth Ground Control"),
                # Nested structures
                "snapshot_id": snap.get("snapshot_id"),
                "vitals": vitals_display,
                "predictions": predictions,
                "disease_risks": evaluate_disease_risks(vitals_display),
                "alerts": alerts,
                "mission_elapsed_time": mission_elapsed(),
                "timestamp": snap.get("observed_at") or datetime.now(timezone.utc).isoformat(),
                "tick": tick,
            }

            socketio.emit("telemetry_update", payload)

        except Exception as e:
            print(f"[SIM] Error in telemetry loop: {e}")

        socketio.sleep(2)

    print("[SIM] Telemetry loop exited.")


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 65)
    print("  OrbitNexus – Autonomous Astronaut Health Monitoring System")
    print(f"  Mission clock started at {datetime.now(timezone.utc).isoformat()}")
    print(f"  Models loaded: {list(engine.models.keys())}")
    print(f"  OrbitNexus AI (Groq): {'READY' if groq_client else 'OFFLINE (set GROQ_API_KEY and LLM_PROVIDER=groq)'}")
    print("  Listening on: http://localhost:5000")
    print("=" * 65)
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
