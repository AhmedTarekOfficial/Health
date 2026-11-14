from __future__ import annotations

from typing import Any, Dict, List, Optional


class AIOrchestrator:
    """High-level orchestration for telemetry, memory, alerts, and capability context."""

    def build_summary(
        self,
        telemetry: Optional[Dict[str, Any]] = None,
        alerts: Optional[List[Dict[str, Any]]] = None,
        memory: Optional[List[Dict[str, Any]]] = None,
        capabilities: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        telemetry = telemetry or {}
        alerts = alerts or []
        memory = memory or []
        capabilities = capabilities or []

        hr = float(telemetry.get("heart_rate_bpm", telemetry.get("heart_rate", 70.0)) or 70.0)
        spo2 = float(telemetry.get("spo2_percent", telemetry.get("spo2", 98.0)) or 98.0)
        rr = float(telemetry.get("respiration_rate", telemetry.get("rr", 16.0)) or 16.0)

        critical = any((alert.get("severity") or "").lower() == "critical" for alert in alerts)
        warning = any((alert.get("severity") or "").lower() == "warning" for alert in alerts)
        if critical or spo2 < 90 or hr > 120 or rr > 26:
            risk_level = "high"
        elif warning or spo2 < 95 or hr > 100:
            risk_level = "medium"
        else:
            risk_level = "low"

        recommendations = [
            "Continue monitoring the astronaut’s primary vitals and compare against historical trends.",
        ]
        if critical or spo2 < 92:
            recommendations.append("Escalate to the onboard medical workflow and pause high-intensity activity immediately.")
        if hr > 100:
            recommendations.append("Assess fatigue, exertion, and hydration before any further operational tasking.")
        if not capabilities:
            recommendations.append("Verify the connected device capability registry before relying on additional sensor metrics.")
        if memory:
            recommendations.append("Use recent memory context to compare the current clinical signal against prior recovery patterns.")

        summary = (
            f"Current telemetry shows heart rate {hr:.1f} bpm, SpO2 {spo2:.1f}%, and respiration {rr:.1f} br/min. "
            f"The device capability set includes {len(capabilities)} active metrics. "
            f"Recent memory context and active alerts indicate a {risk_level}-risk posture."
        )

        return {
            "risk_level": risk_level,
            "summary": summary,
            "recommendations": recommendations,
            "alerts": alerts,
            "memory_count": len(memory),
            "capability_count": len(capabilities),
        }
