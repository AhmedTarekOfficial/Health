"""
ASTRA Intelligence Engine – Real-Time Astronaut Telemetry Inference API
========================================================================
Loads the 5 trained models and provides unified real-time health telemetry:
  1. Activity Recognition (PAMAP2)
  2. Cognitive Stress State (WESAD / Physio)
  3. Real-Time Heart Rate from PPG (BIDMC)
  4. Physiological Anomaly Detection (Nominal vs Alert)
  5. NASA OSDR Spaceflight Adaptation Response (OSD-178)
"""

import os
import json
import joblib
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "models")


class AstraIntelligenceEngine:
    def __init__(self, models_path=MODELS_DIR):
        self.models_path = models_path
        self.models = {}
        self.metadata = {}
        self.load_models()

    def load_models(self):
        """Loads all 5 trained models into memory using joblib."""
        model_defs = {
            "activity": ("activity_model", "activity.pkl", "activity_meta.json"),
            "stress": ("stress_model", "stress.pkl", "stress_meta.json"),
            "heart": ("heart_model", "heart.pkl", "heart_meta.json"),
            "anomaly": ("anomaly_model", "anomaly.pkl", "anomaly_meta.json"),
            "space_bio": ("space_bio_model", "space_bio.pkl", "space_bio_meta.json"),
        }

        for key, (folder, pkl_name, meta_name) in model_defs.items():
            pkl_path = os.path.join(self.models_path, folder, pkl_name)
            meta_path = os.path.join(self.models_path, folder, meta_name)

            if os.path.exists(pkl_path):
                self.models[key] = joblib.load(pkl_path)
                print(f"✓ Loaded {key.upper()} model from {pkl_name}")
            else:
                print(f"⚠ Warning: {pkl_path} not found.")

            if os.path.exists(meta_path):
                with open(meta_path, "r") as f:
                    self.metadata[key] = json.load(f)

    def predict_activity(self, imu_features: list) -> dict:
        """Predicts astronaut current physical activity."""
        model = self.models.get("activity")
        if not model:
            return {"error": "Activity model not loaded"}
        X = np.array(imu_features).reshape(1, -1)
        pred_id = int(model.predict(X)[0])
        class_map = self.metadata.get("activity", {}).get("classes", {})
        activity_name = class_map.get(str(pred_id), class_map.get(pred_id, f"Activity #{pred_id}"))
        return {
            "activity_id": pred_id,
            "activity_name": activity_name,
            "status": "nominal"
        }

    def predict_stress(self, physio_features: list) -> dict:
        """Predicts astronaut stress / cognitive load."""
        model = self.models.get("stress")
        if not model:
            return {"error": "Stress model not loaded"}
        X = np.array(physio_features).reshape(1, -1)
        pred_id = int(model.predict(X)[0])
        labels = {0: "Baseline / Relaxed", 1: "Acute Stress / High Cognitive Load", 2: "Recovery / Rest"}
        return {
            "stress_level_id": pred_id,
            "stress_state": labels.get(pred_id, "Unknown"),
            "alert": bool(pred_id == 1)
        }

    def predict_heart_rate(self, ppg_waveform_stats: list) -> dict:
        """Estimates continuous heart rate from PPG pulse wave."""
        model = self.models.get("heart")
        if not model:
            return {"error": "Heart model not loaded"}
        X = np.array(ppg_waveform_stats).reshape(1, -1)
        estimated_hr = float(model.predict(X)[0])
        return {
            "estimated_heart_rate_bpm": round(estimated_hr, 1),
            "status": "normal" if 55 <= estimated_hr <= 100 else "warning"
        }

    def detect_anomaly(self, vitals: list) -> dict:
        """Detects physiological anomaly (hypoxia, cardiac stress, hypothermia)."""
        model = self.models.get("anomaly")
        if not model:
            return {"error": "Anomaly model not loaded"}
        X = np.array(vitals).reshape(1, -1)
        pred = int(model.predict(X)[0])  # 1 = Normal, -1 = Anomaly
        score = float(model.decision_function(X)[0]) if hasattr(model, "decision_function") else 0.0
        return {
            "is_nominal": bool(pred == 1),
            "anomaly_flag": bool(pred == -1),
            "anomaly_score": round(score, 4),
            "severity": "CRITICAL" if pred == -1 and score < -0.1 else ("ELEVATED" if pred == -1 else "NOMINAL")
        }

    def predict_space_bio_response(self, gene_expression: list) -> dict:
        """Assesses spaceflight biological adaptation state."""
        model = self.models.get("space_bio")
        if not model:
            return {"error": "Space bio model not loaded"}
        X = np.array(gene_expression).reshape(1, -1)
        pred = int(model.predict(X)[0])
        return {
            "spaceflight_adaptation_signature": bool(pred == 1),
            "biological_state": "Orbital Spaceflight Adaptation State" if pred == 1 else "Earth Ground Control"
        }

    def process_astronaut_telemetry(self, telemetry: dict) -> dict:
        """Unified Real-Time Telemetry Pipeline for ASTRA Dashboard."""
        results = {}
        if "imu" in telemetry:
            results["activity"] = self.predict_activity(telemetry["imu"])
        if "stress_physio" in telemetry:
            results["stress"] = self.predict_stress(telemetry["stress_physio"])
        if "ppg_stats" in telemetry:
            results["heart_rate"] = self.predict_heart_rate(telemetry["ppg_stats"])
        if "vitals" in telemetry:
            results["anomaly_detection"] = self.detect_anomaly(telemetry["vitals"])
        if "genomics" in telemetry:
            results["space_bio"] = self.predict_space_bio_response(telemetry["genomics"])

        # Overall astronaut mission health assessment
        is_anomaly = results.get("anomaly_detection", {}).get("anomaly_flag", False)
        is_stressed = results.get("stress", {}).get("alert", False)
        
        if is_anomaly:
            mission_status = "ALERT: Physiological Anomaly Detected"
        elif is_stressed:
            mission_status = "CAUTION: Elevated Cognitive/Physical Stress"
        else:
            mission_status = "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)"

        results["mission_health_summary"] = mission_status
        return results


# ── Simple Verification Test ──
if __name__ == "__main__":
    engine = AstraIntelligenceEngine()
    print("\nSimulating Real-Time Astronaut Telemetry Packet...")

    sample_packet = {
        # 22 IMU features (Heart rate, temps, 3D acc, 3D gyro)
        "imu": [75.0, 30.5, 2.1, 8.5, 3.4, 0.0, 0.05, -0.01, 33.2, 0.1, 9.8, 0.2, 0.0, 0.0, 0.0, 31.0, 0.0, 9.8, 0.1, 0.0, 0.0, 0.0],
        # HR, HRV RMSSD, EDA, Skin Temp, Respiration Rate
        "stress_physio": [72.0, 52.0, 1.8, 34.1, 14.0],
        # 9 PPG/RESP waveform statistics
        "ppg_stats": [0.45, 0.12, 0.15, 0.85, 0.70, 0.48, 0.35, 0.05, 0.20],
        # HR, SpO2, Resp, Systolic BP, Temp
        "vitals": [74.0, 98.5, 14.0, 118.0, 36.6],
        # Top 50 spaceflight biomarkers
        "genomics": [100.0] * 50
    }

    report = engine.process_astronaut_telemetry(sample_packet)
    print("\nTelemetry Assessment Result:")
    print(json.dumps(report, indent=2))
