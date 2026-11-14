from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union


@dataclass
class SensorReading:
    metric: str
    value: float
    unit: str
    source: str = "simulator"
    device_id: str = "watch-01"
    provider: str = "simulator"
    quality_flags: Optional[List[str]] = None
    sampling_frequency_hz: float = 1.0
    model_version: str = "sim-v1"
    timestamp: Optional[str] = None
    status: str = "measured"

    def __post_init__(self):
        if self.quality_flags is None:
            self.quality_flags = ["ok"]
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class SensorNormalizer:
    @staticmethod
    def normalize(reading: Union[SensorReading, Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(reading, dict):
            metric = str(reading.get("metric") or reading.get("name") or "unknown")
            value = float(reading.get("value", 0.0))
            unit = str(reading.get("unit") or "")
            source = str(reading.get("source") or "simulator")
            device_id = str(reading.get("device_id") or "device-01")
            provider = str(reading.get("provider") or source)
            quality_flags = reading.get("quality_flags") or ["ok"]
            sample_rate = float(reading.get("sampling_frequency_hz") or 1.0)
            model_version = str(reading.get("model_version") or "unknown")
            timestamp = reading.get("timestamp") or datetime.now(timezone.utc).isoformat()
            status = str(reading.get("status") or "measured")
        else:
            metric = reading.metric
            value = reading.value
            unit = reading.unit
            source = reading.source
            device_id = reading.device_id
            provider = reading.provider
            quality_flags = reading.quality_flags or ["ok"]
            sample_rate = reading.sampling_frequency_hz
            model_version = reading.model_version
            timestamp = reading.timestamp or datetime.now(timezone.utc).isoformat()
            status = reading.status or "measured"

        return {
            "metric": metric,
            "value": float(value),
            "unit": unit,
            "source": source,
            "provider": provider,
            "device_id": device_id,
            "status": status,
            "quality_flags": list(quality_flags),
            "sampling_frequency_hz": float(sample_rate),
            "model_version": model_version,
            "timestamp": timestamp,
        }


class DeviceCapabilityRegistry:
    DEFAULT_CAPABILITIES = {
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
    }

    def discover(self, capabilities: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
        source = capabilities or self.DEFAULT_CAPABILITIES
        supported = [key for key, enabled in source.items() if bool(enabled)]
        missing = [key for key, enabled in source.items() if not bool(enabled)]
        return {"supported": supported, "missing": missing}


class AlertEngine:
    DEFAULT_THRESHOLDS = {
        "heart_rate": {"high": 120, "low": 45},
        "spo2": {"low": 92},
        "respiration_rate": {"high": 28, "low": 8},
        "temperature": {"high": 38.5, "low": 35.5},
    }

    def evaluate(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        heart_rate = float(metrics.get("heart_rate", 0.0) or 0.0)
        spo2 = float(metrics.get("spo2", 100.0) or 100.0)
        rr = float(metrics.get("respiration_rate", 16.0) or 16.0)
        temp = float(metrics.get("temperature", 36.8) or 36.8)
        quality = str(metrics.get("quality", "good")).lower()

        if quality == "poor":
            return {
                "type": "sensor_quality",
                "severity": "warning",
                "message": "Sensor quality is degraded; live readings may be unreliable.",
                "threshold": "quality=poor",
            }

        if heart_rate > self.DEFAULT_THRESHOLDS["heart_rate"]["high"]:
            return {
                "type": "heart_rate",
                "severity": "warning",
                "message": "Heart rate is elevated beyond the configured threshold.",
                "threshold": self.DEFAULT_THRESHOLDS["heart_rate"]["high"],
                "value": heart_rate,
            }
        if spo2 < self.DEFAULT_THRESHOLDS["spo2"]["low"]:
            return {
                "type": "spo2",
                "severity": "critical",
                "message": "Blood oxygen is below the configured low threshold.",
                "threshold": self.DEFAULT_THRESHOLDS["spo2"]["low"],
                "value": spo2,
            }
        if rr > self.DEFAULT_THRESHOLDS["respiration_rate"]["high"] or rr < self.DEFAULT_THRESHOLDS["respiration_rate"]["low"]:
            return {
                "type": "respiration_rate",
                "severity": "warning",
                "message": "Respiratory rate is outside the configured safe range.",
                "threshold": {"low": self.DEFAULT_THRESHOLDS["respiration_rate"]["low"], "high": self.DEFAULT_THRESHOLDS["respiration_rate"]["high"]},
                "value": rr,
            }
        if temp > self.DEFAULT_THRESHOLDS["temperature"]["high"] or temp < self.DEFAULT_THRESHOLDS["temperature"]["low"]:
            return {
                "type": "temperature",
                "severity": "warning",
                "message": "Temperature is outside the configured safe range.",
                "threshold": {"low": self.DEFAULT_THRESHOLDS["temperature"]["low"], "high": self.DEFAULT_THRESHOLDS["temperature"]["high"]},
                "value": temp,
            }
        return None


class WearableIntegrationManager:
    """Normalize provider-specific wearable payloads into a canonical format."""

    DEFAULT_ADAPTERS = {
        "ble": {
            "provider": "ble",
            "name": "Bluetooth LE",
            "connection": "bluetooth",
            "supported_metrics": ["heart_rate", "spo2", "temperature", "activity"],
        },
        "wear_os": {
            "provider": "wear_os",
            "name": "Wear OS",
            "connection": "google_play_services",
            "supported_metrics": ["heart_rate", "spo2", "respiration_rate", "activity", "sleep"],
        },
        "health_connect": {
            "provider": "health_connect",
            "name": "Google Health Connect",
            "connection": "android_api",
            "supported_metrics": ["heart_rate", "spo2", "sleep", "activity", "blood_pressure"],
        },
        "healthkit": {
            "provider": "healthkit",
            "name": "Apple HealthKit",
            "connection": "apple_health",
            "supported_metrics": ["heart_rate", "spo2", "activity", "temperature", "sleep"],
        },
    }

    def __init__(self, adapters: Optional[Dict[str, Dict[str, Any]]] = None):
        self.adapters = adapters or self.DEFAULT_ADAPTERS.copy()

    def get_adapter(self, provider: str) -> Optional[Dict[str, Any]]:
        provider_key = str(provider or "").strip().lower()
        return self.adapters.get(provider_key)

    def list_adapters(self) -> List[Dict[str, Any]]:
        return [dict(adapter) for adapter in self.adapters.values()]

    def normalize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        provider = str(payload.get("provider") or payload.get("source") or "unknown").lower()
        adapter = self.get_adapter(provider)
        metrics = {}
        for key in ["heart_rate", "spo2", "temperature", "respiration_rate", "sleep", "activity", "blood_pressure"]:
            if key in payload:
                value = payload[key]
                if isinstance(value, (int, float)):
                    metrics[key] = float(value)
                else:
                    try:
                        metrics[key] = float(value)
                    except (TypeError, ValueError):
                        metrics[key] = value

        normalized = {
            "provider": provider,
            "device_id": str(payload.get("device_id") or payload.get("serial") or "unknown-device"),
            "adapter": adapter.copy() if adapter else {"provider": provider, "name": provider},
            "metrics": metrics,
            "timestamp": payload.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        }
        return normalized


class SensorSimulator:
    def __init__(self, device_id: str = "orbital-watch-01"):
        self.device_id = device_id

    def _base_values(self) -> Dict[str, float]:
        now = datetime.now(timezone.utc)
        epoch = now.timestamp()
        sin_t = math.sin(epoch / 1000.0)
        return {
            "heart_rate": 72.0 + sin_t * 5.0 + random.uniform(-1.0, 1.0),
            "spo2": 98.0 + math.sin(epoch / 200.0) * 0.8,
            "temperature": 36.8 + math.sin(epoch / 400.0) * 0.25,
            "respiration_rate": 16.0 + math.sin(epoch / 220.0) * 2.0,
            "hrv": 55.0 + math.cos(epoch / 260.0) * 10.0,
            "skin_temp": 33.9 + math.sin(epoch / 180.0) * 0.35,
        }

    def generate_snapshot(self) -> Dict[str, Any]:
        values = self._base_values()
        ts = datetime.now(timezone.utc).isoformat()
        snapshot = {
            "device_id": self.device_id,
            "device_name": "Orbital Watch Simulator",
            "source": "simulator",
            "provider": "simulator",
            "status": "simulated",
            "capabilities": [
                "heart_rate",
                "spo2",
                "temperature",
                "respiration_rate",
                "hrv",
                "skin_temp",
                "activity",
            ],
            "timestamp": ts,
            "heart_rate": {
                "metric": "heart_rate",
                "value": round(values["heart_rate"], 1),
                "unit": "bpm",
                "status": "measured",
                "device_id": self.device_id,
                "source": "simulator",
                "provider": "simulator",
                "quality_flags": ["ok"],
                "timestamp": ts,
            },
            "spo2": {
                "metric": "spo2",
                "value": round(values["spo2"], 1),
                "unit": "%",
                "status": "measured",
                "device_id": self.device_id,
                "source": "simulator",
                "provider": "simulator",
                "quality_flags": ["ok"],
                "timestamp": ts,
            },
            "temperature": {
                "metric": "temperature",
                "value": round(values["temperature"], 2),
                "unit": "C",
                "status": "measured",
                "device_id": self.device_id,
                "source": "simulator",
                "provider": "simulator",
                "quality_flags": ["ok"],
                "timestamp": ts,
            },
            "respiration_rate": {
                "metric": "respiration_rate",
                "value": round(values["respiration_rate"], 1),
                "unit": "brpm",
                "status": "measured",
                "device_id": self.device_id,
                "source": "simulator",
                "provider": "simulator",
                "quality_flags": ["ok"],
                "timestamp": ts,
            },
            "hrv": {
                "metric": "hrv",
                "value": round(values["hrv"], 1),
                "unit": "ms",
                "status": "measured",
                "device_id": self.device_id,
                "source": "simulator",
                "provider": "simulator",
                "quality_flags": ["ok"],
                "timestamp": ts,
            },
            "skin_temp": {
                "metric": "skin_temp",
                "value": round(values["skin_temp"], 2),
                "unit": "C",
                "status": "measured",
                "device_id": self.device_id,
                "source": "simulator",
                "provider": "simulator",
                "quality_flags": ["ok"],
                "timestamp": ts,
            },
        }
        return snapshot
