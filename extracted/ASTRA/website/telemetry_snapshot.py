"""
Canonical telemetry snapshot store for OrbitNexus.

Dashboard (GET /api/telemetry/live, /api/telemetry/demo) and chatbot (POST /api/chat)
share one versioned snapshot. Partial updates never silently present outdated
fields as fresh measurements, and fetching a snapshot does not refresh
observation times.

Snapshot selection policy for chat
----------------------------------
1. If the client sends snapshot_id and that ID is known, use that snapshot.
2. If the requested ID is unknown/absent but a current snapshot exists, use the
   current snapshot and report the substitution explicitly (never silently).
3. If no snapshot exists, report that current measurements are unavailable.
   Do not invent normal-looking fallback readings.

Alert update policy (category-aware)
------------------------------------
Alert categories (ANOMALY, STRESS) are tracked independently.
A partial update adds/updates alerts only for categories whose models returned
a successful result. Categories not reassessed keep their prior state.
A successful clear for one category does not clear unrelated categories.
Model errors / missing outputs are distinct from a successful clear.
Overall status "clear" requires every alert category to be successfully clear;
partial coverage is reported as "partial", never as a silent all-clear.

Alert model success contract
----------------------------
- Stress: requires an explicit boolean ``alert`` field.
- Anomaly: requires an explicit boolean ``anomaly_flag`` field.
- True → active; False → successful clear.
- Missing flag, wrong type, non-dict output, or ``error`` key → failed assessment
  (not a clear). Empty ``{}`` does not mean clear.
- Absent prediction key → category not reassessed.

Chronological protection (readings + alerts + linked predictions)
-----------------------------------------------------------------
Per-field / per-category observed_at ordering:
- Older readings do not overwrite newer readings.
- Older alert assessments do not replace newer category state (active/clear/failed).
- When an outdated alert assessment is rejected, its linked prediction key is
  not merged either (keeps readings/predictions/alerts consistent).
- Equal timestamps: last-write-wins (incoming accepted) — deterministic.
- Packet receipt time never refreshes an accepted assessment timestamp.

Category ordering timestamp (per category, parsed — never lexicographic)
-----------------------------------------------------------------------
Each alert category tracks three distinct concepts:
- status + item + assessed_at: result and time of the last SUCCESSFUL assessment.
- last_evaluation + last_evaluation_at (+ last_error): the latest attempt, which
  may have failed while the previous status/assessed_at is preserved.
- ordering timestamp: max(assessed_at, last_evaluation_at) as parsed instants.
An incoming update is rejected when strictly older than that ordering timestamp.
A newer failed evaluation therefore protects the category from a delayed packet
even though the retained alert keeps an earlier assessed_at. Ordering is
category-specific: rejecting STRESS never blocks an eligible ANOMALY update.

Mission health summary policy
-----------------------------
mission_health_summary is NEVER taken from an incoming packet as-is. It is
recomputed from the accepted canonical assessment state after alert merging and
prediction acceptance, so it cannot contradict a retained alert or a rejected
prediction. Derivation levels:
- alert / caution: a retained active alert (with any failed evaluation noted).
- indeterminate: the latest evaluation failed and no alert is retained.
- partial: coverage is incomplete (categories never assessed).
- unknown: nothing has ever been assessed.
- clear: every category was successfully assessed clear.
Only the clear level asserts readiness; an incoming packet's own summary is
recorded as ignored when it disagrees and never reaches the LLM context.

Observation time policy
-----------------------
- observed_at: when the measurement was taken (client-supplied when valid).
- received_at: when the server received the packet.
- Missing observed_at → receipt time is used with basis "receipt_time_assumed"
  (uncertainty is explicit; not silently claimed as a known measurement time).
- Invalid / implausibly future timestamps → validation failure (not stored).
- Naive ISO timestamps are interpreted as UTC.
- Out-of-order: an older observation does not overwrite a newer field value.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


# Readings older than this are marked stale for LLM / API freshness metadata.
DEFAULT_STALE_AFTER_SECONDS = 15.0

# REST live polling may generate a new simulated observation when the current
# snapshot is at least this old AND the WebSocket simulation loop is not already
# driving updates (single coherent generation stream).
LIVE_REFRESH_SECONDS = 2.0

# Reject observation timestamps more than this far in the future.
MAX_FUTURE_SKEW_SECONDS = 120.0

READING_SPECS: dict[str, dict[str, str]] = {
    "heart_rate_bpm": {"unit": "bpm", "label": "Heart Rate"},
    "spo2_percent": {"unit": "%", "label": "SpO2"},
    "respiration_rate": {"unit": "brpm", "label": "Respiration Rate"},
    "core_temp_c": {"unit": "°C", "label": "Core Temperature"},
    "blood_water_percent": {"unit": "%", "label": "Blood Water / Hydration"},
    "systolic_bp": {"unit": "mmHg", "label": "Systolic BP"},
    "diastolic_bp": {"unit": "mmHg", "label": "Diastolic BP"},
    "eda_microsiemens": {"unit": "µS", "label": "EDA"},
    "skin_temp_c": {"unit": "°C", "label": "Skin Temperature"},
    "hrv_rmssd": {"unit": "ms", "label": "HRV RMSSD"},
}

PREDICTION_KEYS = (
    "activity",
    "stress",
    "heart_rate",
    "anomaly_detection",
    "space_bio",
    "mission_health_summary",
)

# Engine feature-packet keys (legacy helper; alert coverage uses model outputs).
COMPLETE_ASSESSMENT_FEATURE_KEYS = ("imu", "stress_physio", "ppg_stats", "vitals", "genomics")

# Alert categories with independent lifecycle tracking.
ALERT_CATEGORIES = ("ANOMALY", "STRESS")

# Prediction keys whose freshness is tied to the matching alert category.
CATEGORY_PREDICTION_KEYS: dict[str, tuple[str, ...]] = {
    "ANOMALY": ("anomaly_detection",),
    "STRESS": ("stress",),
}

# Predictions never taken from an incoming packet as-is: the store derives them
# from the accepted assessment state (see derive_mission_health_summary).
DERIVED_PREDICTION_KEYS: tuple[str, ...] = ("mission_health_summary",)

# Summary variants. The clear string is unchanged for dashboard/demo
# compatibility; the others make failure / incomplete coverage explicit.
MISSION_SUMMARY_CLEAR = "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)"
MISSION_SUMMARY_ANOMALY = "ALERT: Physiological Anomaly Detected"
MISSION_SUMMARY_STRESS = "CAUTION: Elevated Cognitive/Physical Stress"
MISSION_SUMMARY_UNKNOWN = (
    "UNKNOWN: No alert assessment available — health status cannot be confirmed"
)

# Demo fixtures aligned with src/modules/dashboard/data.js baseline UI values.
DEMO_READINGS: dict[str, Any] = {
    "heart_rate_bpm": 72.0,
    "spo2_percent": 98.6,
    "respiration_rate": 16.2,
    "core_temp_c": 36.85,
    "blood_water_percent": 62.4,
    "systolic_bp": 118.0,
    "diastolic_bp": 76.0,
    "eda_microsiemens": 2.15,
    "skin_temp_c": 33.5,
    "hrv_rmssd": 48.5,
}

DEMO_PREDICTIONS: dict[str, Any] = {
    "activity": {
        "activity_id": 1,
        "activity_name": "Resting / Nominal Metabolic IVA",
        "status": "nominal",
    },
    "stress": {
        "stress_level_id": 0,
        "stress_state": "Autonomic Equilibrium (EDA: 2.15 µS)",
        "alert": False,
    },
    "heart_rate": {
        "estimated_heart_rate_bpm": 72.4,
        "status": "normal",
    },
    "anomaly_detection": {
        "is_nominal": True,
        "anomaly_flag": False,
        "anomaly_score": 0.142,
        "severity": "NOMINAL",
    },
    "space_bio": {
        "spaceflight_adaptation_signature": True,
        "biological_state": "Active Orbital Adaptation Signature",
    },
    "mission_health_summary": "ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)",
}


def utc_now_iso(now: Optional[float] = None) -> str:
    ts = time.time() if now is None else now
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _parse_iso(ts: Optional[str]) -> Optional[float]:
    """Parse ISO-8601; naive datetimes are treated as UTC."""
    if not ts:
        return None
    try:
        text = str(ts).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _to_utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def age_seconds(observed_at: Optional[str], now: Optional[float] = None) -> Optional[float]:
    ts = _parse_iso(observed_at)
    if ts is None:
        return None
    return max(0.0, (now if now is not None else time.time()) - ts)


def resolve_observation_time(
    raw_observed_at: Optional[str],
    *,
    received_at: Optional[str] = None,
    now: Optional[float] = None,
    max_future_skew_seconds: float = MAX_FUTURE_SKEW_SECONDS,
) -> dict:
    """
    Validate / normalize an incoming observation timestamp.

    Returns a dict with ok, observed_at, received_at, basis, error, warning.
    """
    now_ts = time.time() if now is None else now
    received = received_at or utc_now_iso(now_ts)

    if raw_observed_at is None or str(raw_observed_at).strip() == "":
        return {
            "ok": True,
            "observed_at": received,
            "received_at": received,
            "basis": "receipt_time_assumed",
            "error": None,
            "warning": (
                "No observation timestamp supplied; using server receipt time. "
                "Measurement time is uncertain and must not be treated as a known "
                "device observation instant."
            ),
        }

    parsed = _parse_iso(raw_observed_at)
    if parsed is None:
        return {
            "ok": False,
            "observed_at": None,
            "received_at": received,
            "basis": "invalid",
            "error": "Invalid observed_at; expected an ISO-8601 timestamp.",
            "warning": None,
        }

    if parsed - now_ts > max_future_skew_seconds:
        return {
            "ok": False,
            "observed_at": None,
            "received_at": received,
            "basis": "invalid",
            "error": (
                f"observed_at is implausibly in the future "
                f"(>{max_future_skew_seconds:.0f}s ahead of server time)."
            ),
            "warning": None,
        }

    return {
        "ok": True,
        "observed_at": _to_utc_iso(parsed),
        "received_at": received,
        "basis": "client_supplied",
        "error": None,
        "warning": None,
    }


def extract_readings_from_packet(data: dict) -> dict[str, Any]:
    """Pull display readings from a POST body or engine feature packet."""
    readings: dict[str, Any] = {}
    if not isinstance(data, dict):
        return readings

    for key in READING_SPECS:
        if key in data and data[key] is not None:
            readings[key] = data[key]

    nested = data.get("vitals")
    if isinstance(nested, dict):
        for key in READING_SPECS:
            if key in nested and nested[key] is not None:
                readings.setdefault(key, nested[key])
    elif isinstance(nested, list) and len(nested) >= 5:
        readings.setdefault("heart_rate_bpm", nested[0])
        readings.setdefault("spo2_percent", nested[1])
        readings.setdefault("respiration_rate", nested[2])
        readings.setdefault("systolic_bp", nested[3])
        readings.setdefault("core_temp_c", nested[4])

    stress = data.get("stress_physio")
    if isinstance(stress, list) and len(stress) >= 5:
        readings.setdefault("heart_rate_bpm", stress[0])
        readings.setdefault("hrv_rmssd", stress[1])
        readings.setdefault("eda_microsiemens", stress[2])
        readings.setdefault("skin_temp_c", stress[3])
        readings.setdefault("respiration_rate", stress[4])

    return readings


def is_complete_assessment_packet(data: dict) -> bool:
    """True when the packet carries a full feature set for all five models."""
    if not isinstance(data, dict):
        return False
    return all(k in data for k in COMPLETE_ASSESSMENT_FEATURE_KEYS)


def alerts_from_predictions(predictions: dict) -> list[dict]:
    """Legacy helper: active alert items derived from successful predictions."""
    updates = alert_category_updates_from_predictions(predictions)
    items = []
    for cat in ALERT_CATEGORIES:
        upd = updates.get(cat)
        if upd and upd.get("outcome") == "active" and upd.get("item"):
            items.append(upd["item"])
    return items


def _failed_category_update(detail: str) -> dict:
    return {"outcome": "failed", "item": None, "detail": detail}


def _require_explicit_bool(container: dict, field: str) -> tuple[Optional[bool], Optional[str]]:
    """
    Return (value, error). Only native bool counts as a valid assessment flag.
    Missing / wrong-type values are errors — never coerced via truthiness.
    """
    if field not in container:
        return None, f"missing required boolean field '{field}'"
    value = container[field]
    if isinstance(value, bool):
        return value, None
    return None, f"'{field}' must be a boolean (got {type(value).__name__})"


def alert_category_updates_from_predictions(predictions: dict) -> dict[str, dict]:
    """
    Build per-category alert updates from model outputs.

    A category is included only when its prediction key is present.
    Success requires an explicit boolean flag (see module policy).
    {"error": ...} / malformed / empty → outcome "failed" (not a clear).
    """
    updates: dict[str, dict] = {}
    if not isinstance(predictions, dict):
        return updates

    if "anomaly_detection" in predictions:
        anomaly = predictions.get("anomaly_detection")
        if not isinstance(anomaly, dict):
            updates["ANOMALY"] = _failed_category_update(
                "anomaly_detection output is not an object"
            )
        elif "error" in anomaly:
            updates["ANOMALY"] = _failed_category_update(
                str(anomaly.get("error") or "anomaly evaluation failed")
            )
        else:
            flag, err = _require_explicit_bool(anomaly, "anomaly_flag")
            if err:
                updates["ANOMALY"] = _failed_category_update(err)
            elif flag:
                severity = anomaly.get("severity", "ELEVATED")
                updates["ANOMALY"] = {
                    "outcome": "active",
                    "item": {
                        "type": "ANOMALY",
                        "severity": severity,
                        "message": (
                            f"Physiological anomaly detected – severity: {severity}"
                        ),
                    },
                }
            else:
                updates["ANOMALY"] = {"outcome": "clear", "item": None}

    if "stress" in predictions:
        stress = predictions.get("stress")
        if not isinstance(stress, dict):
            updates["STRESS"] = _failed_category_update(
                "stress output is not an object"
            )
        elif "error" in stress:
            updates["STRESS"] = _failed_category_update(
                str(stress.get("error") or "stress evaluation failed")
            )
        else:
            flag, err = _require_explicit_bool(stress, "alert")
            if err:
                updates["STRESS"] = _failed_category_update(err)
            elif flag:
                updates["STRESS"] = {
                    "outcome": "active",
                    "item": {
                        "type": "STRESS",
                        "severity": "CAUTION",
                        "message": "Acute stress / high cognitive load detected",
                    },
                }
            else:
                updates["STRESS"] = {"outcome": "clear", "item": None}

    return updates


def _empty_category_state() -> dict:
    return {
        "status": "unknown",
        "item": None,
        "assessed_at": None,
        "last_evaluation": None,
        "last_evaluation_at": None,
    }


def _empty_alerts_state() -> dict:
    categories = {cat: _empty_category_state() for cat in ALERT_CATEGORIES}
    return {
        # unknown | clear | active | partial
        "status": "unknown",
        "items": [],
        "categories": categories,
        "coverage": {
            "assessed": [],
            "not_assessed": list(ALERT_CATEGORIES),
            "failed": [],
        },
        # Latest successful assessment across categories (display only).
        "assessed_at": None,
        # Latest evaluation attempt (successful OR failed) across categories.
        "last_evaluation_at": None,
    }


def _alerts_from_legacy_list(alerts: Optional[list], assessed_at: str) -> dict:
    """Convert a flat alert list + complete flag into category state (all assessed)."""
    state = _empty_alerts_state()
    active_types = {
        (a.get("type") or "").upper()
        for a in (alerts or [])
        if isinstance(a, dict)
    }
    items_by_type = {
        (a.get("type") or "").upper(): a
        for a in (alerts or [])
        if isinstance(a, dict)
    }
    for cat in ALERT_CATEGORIES:
        if cat in active_types:
            state["categories"][cat] = {
                "status": "active",
                "item": items_by_type.get(cat),
                "assessed_at": assessed_at,
                "last_evaluation": "active",
                "last_evaluation_at": assessed_at,
            }
        else:
            state["categories"][cat] = {
                "status": "clear",
                "item": None,
                "assessed_at": assessed_at,
                "last_evaluation": "clear",
                "last_evaluation_at": assessed_at,
            }
    return _finalize_alert_state(state)


def _finalize_alert_state(state: dict) -> dict:
    items = []
    assessed = []
    not_assessed = []
    failed = []
    latest = None
    latest_eval = None

    for cat in ALERT_CATEGORIES:
        cat_state = state["categories"].setdefault(cat, _empty_category_state())
        if cat_state.get("last_evaluation") == "failed":
            failed.append(cat)
        if cat_state.get("status") == "active" and cat_state.get("item"):
            items.append(cat_state["item"])
            assessed.append(cat)
        elif cat_state.get("status") == "clear":
            assessed.append(cat)
        else:
            not_assessed.append(cat)
        at = cat_state.get("assessed_at") or cat_state.get("last_evaluation_at")
        if at and (latest is None or (_parse_iso(at) or 0) > (_parse_iso(latest) or 0)):
            latest = at
        eval_at = cat_state.get("last_evaluation_at")
        if eval_at and (
            latest_eval is None
            or (_parse_iso(eval_at) or 0) > (_parse_iso(latest_eval) or 0)
        ):
            latest_eval = eval_at

    if items:
        overall = "active"
    elif assessed and not not_assessed and not failed:
        # Every category successfully clear → overall clear.
        overall = "clear"
    elif assessed:
        # Some categories assessed; others unknown/failed → not an all-clear.
        overall = "partial"
    else:
        overall = "unknown"

    state["status"] = overall
    state["items"] = items
    state["coverage"] = {
        "assessed": assessed,
        "not_assessed": not_assessed,
        "failed": failed,
    }
    state["assessed_at"] = latest
    state["last_evaluation_at"] = latest_eval
    return state


def _category_assessment_ts(cat_state: Optional[dict]) -> Optional[float]:
    """
    Ordering timestamp for one alert category.

    This is the latest *applicable* evaluation instant: the later of the last
    successful assessment (``assessed_at``) and the latest evaluation attempt
    (``last_evaluation_at``), compared as parsed instants — never as strings.
    A failed attempt updates ``last_evaluation_at`` while the retained alert
    keeps its earlier ``assessed_at``; using the max means that newer failure
    still prevents an older packet from rewriting or clearing the category.
    """
    if not isinstance(cat_state, dict):
        return None
    candidates = (
        _parse_iso(cat_state.get("assessed_at")),
        _parse_iso(cat_state.get("last_evaluation_at")),
    )
    valid = [ts for ts in candidates if ts is not None]
    return max(valid) if valid else None


def derive_mission_health_summary(alert_state: Optional[dict]) -> dict:
    """
    Aggregate the accepted assessment state into one canonical health summary.

    The result distinguishes active alerts, failed/incomplete evaluations and
    missing coverage from a genuinely successful all-clear, so an outgoing
    packet's optimistic summary can never contradict retained state.
    """
    state = (
        alert_state
        if isinstance(alert_state, dict) and alert_state
        else _empty_alerts_state()
    )
    categories = state.get("categories") or {}

    active: list[str] = []
    failed: list[str] = []
    not_assessed: list[str] = []
    evaluated: list[str] = []

    for cat in ALERT_CATEGORIES:
        c = categories.get(cat) or _empty_category_state()
        status = c.get("status")
        if status == "active":
            active.append(cat)
        elif status != "clear":
            not_assessed.append(cat)
        if c.get("last_evaluation") == "failed":
            failed.append(cat)
        if c.get("last_evaluation") in {"active", "clear", "failed"}:
            evaluated.append(cat)

    if active:
        parts = []
        if "ANOMALY" in active:
            parts.append(MISSION_SUMMARY_ANOMALY)
        if "STRESS" in active:
            parts.append(MISSION_SUMMARY_STRESS)
        level = "alert" if "ANOMALY" in active else "caution"
        summary = " | ".join(parts)
        if failed:
            summary += (
                f" [latest evaluation failed for: {', '.join(failed)} — "
                "retained alert state from the last successful assessment]"
            )
        if not_assessed:
            summary += f" [not assessed: {', '.join(not_assessed)}]"
    elif failed:
        level = "indeterminate"
        summary = (
            f"INDETERMINATE: Alert evaluation failed for {', '.join(failed)} — "
            "no confirmed all-clear"
        )
        if not_assessed:
            summary += f" [not assessed: {', '.join(not_assessed)}]"
    elif not_assessed and not evaluated:
        level = "unknown"
        summary = MISSION_SUMMARY_UNKNOWN
    elif not_assessed:
        level = "partial"
        summary = (
            "PARTIAL: Alert assessment incomplete — not assessed: "
            f"{', '.join(not_assessed)}; no overall readiness claim"
        )
    else:
        level = "clear"
        summary = MISSION_SUMMARY_CLEAR

    return {
        "summary": summary,
        "level": level,
        "active_categories": active,
        "failed_categories": failed,
        "not_assessed_categories": not_assessed,
        "evaluated_categories": evaluated,
        "assessed_at": state.get("assessed_at"),
        "last_evaluation_at": state.get("last_evaluation_at"),
        "basis": "derived_from_accepted_assessment_state",
    }


def _apply_derived_summary(
    predictions: Optional[dict],
    alert_state: Optional[dict],
) -> tuple[dict, dict]:
    """Replace any packet-supplied mission summary with the derived one."""
    preds = dict(predictions or {})
    preds.pop("mission_health_summary", None)
    assessment = derive_mission_health_summary(alert_state)
    preds["mission_health_summary"] = assessment["summary"]
    return preds, assessment


def _summary_ingestion(
    incoming_summary: Optional[str], mission_assessment: dict
) -> dict:
    """Ingestion metadata describing how the mission summary was produced."""
    return {
        "mission_health_summary_source": "derived_from_accepted_state",
        "incoming_summary_present": incoming_summary is not None,
        # The packet's own summary was not authoritative for this snapshot.
        "incoming_summary_ignored": (
            incoming_summary is not None
            and incoming_summary != mission_assessment.get("summary")
        ),
    }


def merge_alert_category_updates(
    previous: Optional[dict],
    category_updates: dict[str, dict],
    assessed_at: str,
) -> tuple[dict, list[str]]:
    """
    Merge successful/failed per-category evaluations into alert state.

    Ordering is per category and uses that category's ordering timestamp
    (_category_assessment_ts): the later of its successful assessment time and
    its latest evaluation attempt, failed attempts included. An incoming
    assessment strictly older than that timestamp is skipped, preserving the
    alert state, the successful assessment time, the latest evaluation status
    and time, and any error details from the newer evaluation.
    Equal timestamps: last-write-wins (incoming kept).
    Returns (alert_state, skipped_outdated_categories).
    """
    state = copy.deepcopy(previous) if previous else _empty_alerts_state()
    if "categories" not in state:
        # Upgrade legacy alert blobs that lack categories.
        upgraded = _empty_alerts_state()
        for item in state.get("items") or []:
            if isinstance(item, dict) and item.get("type"):
                cat = str(item["type"]).upper()
                if cat in upgraded["categories"]:
                    upgraded["categories"][cat] = {
                        "status": "active",
                        "item": item,
                        "assessed_at": state.get("assessed_at"),
                        "last_evaluation": "active",
                        "last_evaluation_at": state.get("assessed_at"),
                    }
        if state.get("status") == "clear":
            for cat in ALERT_CATEGORIES:
                upgraded["categories"][cat] = {
                    "status": "clear",
                    "item": None,
                    "assessed_at": state.get("assessed_at"),
                    "last_evaluation": "clear",
                    "last_evaluation_at": state.get("assessed_at"),
                }
        state = upgraded

    skipped: list[str] = []
    incoming_ts = _parse_iso(assessed_at)

    for cat, update in (category_updates or {}).items():
        cat = str(cat).upper()
        if cat not in ALERT_CATEGORIES:
            continue
        outcome = update.get("outcome")
        prior = state["categories"].get(cat) or _empty_category_state()
        prior_ts = _category_assessment_ts(prior)
        if (
            prior_ts is not None
            and incoming_ts is not None
            and incoming_ts < prior_ts
        ):
            # Outdated assessment — keep newer state (including failure metadata).
            skipped.append(cat)
            continue

        if outcome == "failed":
            # Preserve prior clear/active; failed ≠ successful clear.
            state["categories"][cat] = {
                **prior,
                "last_evaluation": "failed",
                "last_evaluation_at": assessed_at,
                "last_error": update.get("detail"),
            }
        elif outcome == "clear":
            state["categories"][cat] = {
                "status": "clear",
                "item": None,
                "assessed_at": assessed_at,
                "last_evaluation": "clear",
                "last_evaluation_at": assessed_at,
            }
        elif outcome == "active":
            state["categories"][cat] = {
                "status": "active",
                "item": update.get("item"),
                "assessed_at": assessed_at,
                "last_evaluation": "active",
                "last_evaluation_at": assessed_at,
            }

    return _finalize_alert_state(state), skipped


class TelemetrySnapshotStore:
    """Thread-safe canonical telemetry snapshot for dashboard + chat."""

    def __init__(self, stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS):
        self._lock = threading.RLock()
        self._current: Optional[dict] = None
        self._history: dict[str, dict] = {}
        self._history_order: list[str] = []
        self._max_history = 48
        self.stale_after_seconds = float(stale_after_seconds)
        self._version = 0

    def clear(self) -> None:
        with self._lock:
            self._current = None
            self._history.clear()
            self._history_order.clear()
            self._version = 0

    def get_current(self) -> Optional[dict]:
        with self._lock:
            if self._current is None:
                return None
            return self._annotate(copy.deepcopy(self._current))

    def get_by_id(self, snapshot_id: str) -> Optional[dict]:
        with self._lock:
            snap = self._history.get(snapshot_id)
            if snap is None and self._current and self._current.get("snapshot_id") == snapshot_id:
                snap = self._current
            if snap is None:
                return None
            return self._annotate(copy.deepcopy(snap))

    def current_age_seconds(self) -> Optional[float]:
        with self._lock:
            if not self._current:
                return None
            return age_seconds(self._current.get("observed_at"))

    def commit_demo_snapshot(self, *, mission_elapsed: Optional[str] = None) -> dict:
        """Publish the canonical Trial Demo fixture snapshot (source=demo)."""
        return self.commit_complete(
            readings=dict(DEMO_READINGS),
            predictions=dict(DEMO_PREDICTIONS),
            source="demo",
            alert_category_updates=alert_category_updates_from_predictions(DEMO_PREDICTIONS),
            mission_elapsed=mission_elapsed,
        )

    def commit_complete(
        self,
        *,
        readings: dict[str, Any],
        predictions: Optional[dict] = None,
        source: str = "simulation",
        observed_at: Optional[str] = None,
        received_at: Optional[str] = None,
        observation_time_basis: str = "receipt_time_assumed",
        alerts: Optional[list] = None,
        alerts_complete: bool = True,
        alert_category_updates: Optional[dict] = None,
        mission_elapsed: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> dict:
        """
        Replace readings with the provided set (complete observation).

        When alert_category_updates is provided, those drive alert state.
        Else when alerts_complete is True, the flat alerts list is treated as a
        full reassessment of every alert category (empty list → all clear).
        """
        received = received_at or utc_now_iso()
        observed = observed_at or received
        with self._lock:
            reading_entries = {}
            for key, value in (readings or {}).items():
                if key not in READING_SPECS or value is None:
                    continue
                reading_entries[key] = self._make_reading(
                    key,
                    value,
                    observed_at=observed,
                    received_at=received,
                    observation_time_basis=observation_time_basis,
                )

            if alert_category_updates is not None:
                alert_state, _skipped_alerts = merge_alert_category_updates(
                    None, alert_category_updates, observed
                )
            elif alerts_complete:
                alert_state = _alerts_from_legacy_list(alerts, observed)
            else:
                alert_state = _empty_alerts_state()

            # The packet's own summary is never trusted verbatim: recompute it
            # from the accepted complete assessment state.
            incoming_summary = (predictions or {}).get("mission_health_summary")
            merged_predictions, mission_assessment = _apply_derived_summary(
                predictions, alert_state
            )

            snap = self._build_snapshot(
                readings=reading_entries,
                predictions=merged_predictions,
                source=source,
                observed_at=observed,
                received_at=received,
                observation_time_basis=observation_time_basis,
                alerts=alert_state,
                mission_elapsed=mission_elapsed,
                extra=extra,
                skipped_readings=[],
                mission_assessment=mission_assessment,
                summary_ingestion=_summary_ingestion(
                    incoming_summary, mission_assessment
                ),
            )
            return self._publish(snap)

    def commit_partial(
        self,
        *,
        readings: Optional[dict[str, Any]] = None,
        predictions: Optional[dict] = None,
        source: str = "api_post",
        observed_at: Optional[str] = None,
        received_at: Optional[str] = None,
        observation_time_basis: str = "receipt_time_assumed",
        alerts: Optional[list] = None,
        alerts_complete: bool = False,
        alert_category_updates: Optional[dict] = None,
        mission_elapsed: Optional[str] = None,
        replace_predictions: bool = False,
        extra: Optional[dict] = None,
    ) -> dict:
        """
        Merge a partial packet.

        Only provided reading keys get a new value and observed_at. Other readings
        retain their prior values and timestamps (and may be marked stale).
        An older observation for a field does not overwrite a newer one.
        Alert assessments and their linked predictions follow the same ordering.
        """
        received = received_at or utc_now_iso()
        observed = observed_at or received
        with self._lock:
            prev = copy.deepcopy(self._current) if self._current else None
            reading_entries = {}
            if prev:
                reading_entries = copy.deepcopy(prev.get("readings") or {})

            skipped_readings: list[str] = []
            for key, value in (readings or {}).items():
                if key not in READING_SPECS or value is None:
                    continue
                prior = reading_entries.get(key)
                if prior:
                    prior_ts = _parse_iso(prior.get("observed_at"))
                    new_ts = _parse_iso(observed)
                    if (
                        prior_ts is not None
                        and new_ts is not None
                        and new_ts < prior_ts
                    ):
                        # Out-of-order: keep the newer measurement.
                        skipped_readings.append(key)
                        continue
                reading_entries[key] = self._make_reading(
                    key,
                    value,
                    observed_at=observed,
                    received_at=received,
                    observation_time_basis=observation_time_basis,
                )

            prev_alerts = (prev or {}).get("alerts")
            skipped_alert_categories: list[str] = []
            if alert_category_updates is not None:
                alert_state, skipped_alert_categories = merge_alert_category_updates(
                    prev_alerts, alert_category_updates, observed
                )
            elif alerts_complete:
                alert_state = _alerts_from_legacy_list(alerts, observed)
            else:
                alert_state = (
                    copy.deepcopy(prev_alerts)
                    if prev_alerts
                    else _empty_alerts_state()
                )
                if prev_alerts and "categories" not in alert_state:
                    alert_state, skipped_alert_categories = merge_alert_category_updates(
                        prev_alerts, {}, observed
                    )

            # Predictions: skip keys linked to outdated alert assessments so a
            # delayed clear cannot contradict a newer active alert (and vice versa).
            blocked_pred_keys: set[str] = set()
            for cat in skipped_alert_categories:
                blocked_pred_keys.update(CATEGORY_PREDICTION_KEYS.get(cat, ()))

            skipped_predictions: list[str] = []
            if replace_predictions:
                pred = dict(predictions or {})
                for key in list(pred.keys()):
                    if key in blocked_pred_keys:
                        skipped_predictions.append(key)
                        pred.pop(key, None)
                    elif key in DERIVED_PREDICTION_KEYS:
                        # Derived from accepted state below, never from the packet.
                        pred.pop(key, None)
                # Restore blocked keys from previous snapshot when replacing.
                if prev and blocked_pred_keys:
                    for key in blocked_pred_keys:
                        if key in (prev.get("predictions") or {}):
                            pred[key] = copy.deepcopy(prev["predictions"][key])
            else:
                pred = copy.deepcopy((prev or {}).get("predictions") or {})
                if predictions:
                    for key, value in predictions.items():
                        if key in blocked_pred_keys:
                            skipped_predictions.append(key)
                            continue
                        if key in DERIVED_PREDICTION_KEYS:
                            # Derived from the accepted state, not the packet.
                            continue
                        pred[key] = value

            incoming_summary = (
                predictions.get("mission_health_summary")
                if isinstance(predictions, dict)
                else None
            )
            pred, mission_assessment = _apply_derived_summary(pred, alert_state)
            summary_ingestion = _summary_ingestion(
                incoming_summary, mission_assessment
            )

            snap = self._build_snapshot(
                readings=reading_entries,
                predictions=pred,
                source=source,
                observed_at=observed,
                received_at=received,
                observation_time_basis=observation_time_basis,
                alerts=alert_state,
                mission_elapsed=mission_elapsed or (prev or {}).get("mission_elapsed_time"),
                extra=extra,
                skipped_readings=skipped_readings,
                skipped_alert_categories=skipped_alert_categories,
                skipped_predictions=skipped_predictions,
                mission_assessment=mission_assessment,
                summary_ingestion=summary_ingestion,
            )
            if prev and not readings and not predictions and alert_category_updates is None:
                snap["observed_at"] = prev.get("observed_at", observed)
            return self._publish(snap)

    def resolve_for_chat(self, requested_id: Optional[str] = None) -> dict:
        """Apply the documented snapshot-selection policy for /api/chat."""
        with self._lock:
            current = self._annotate(copy.deepcopy(self._current)) if self._current else None
            requested = (requested_id or "").strip() or None

            if current is None:
                return {
                    "snapshot": None,
                    "selection": "missing",
                    "requested_id": requested,
                    "used_id": None,
                    "note": (
                        "No telemetry snapshot is available. Current measurements and "
                        "ML predictions are unavailable. Do not invent readings or "
                        "infer good health from missing data."
                    ),
                }

            if requested and requested == current.get("snapshot_id"):
                return {
                    "snapshot": current,
                    "selection": "exact",
                    "requested_id": requested,
                    "used_id": current.get("snapshot_id"),
                    "note": "Using the snapshot currently displayed on the dashboard.",
                }

            if requested and requested in self._history:
                hist = self._annotate(copy.deepcopy(self._history[requested]))
                return {
                    "snapshot": hist,
                    "selection": "exact_historical",
                    "requested_id": requested,
                    "used_id": hist.get("snapshot_id"),
                    "note": (
                        f"Using requested historical snapshot {requested}. "
                        f"A newer current snapshot exists: {current.get('snapshot_id')}."
                    ),
                }

            if requested:
                return {
                    "snapshot": current,
                    "selection": "current_substituted",
                    "requested_id": requested,
                    "used_id": current.get("snapshot_id"),
                    "note": (
                        f"Requested snapshot_id {requested} was not found. "
                        f"Using newer/current snapshot {current.get('snapshot_id')} instead."
                    ),
                }

            return {
                "snapshot": current,
                "selection": "current",
                "requested_id": None,
                "used_id": current.get("snapshot_id"),
                "note": "No snapshot_id provided; using the latest server snapshot.",
            }

    def build_api_telemetry_view(self, snapshot: Optional[dict] = None) -> dict:
        """Flatten a snapshot for existing dashboard consumers."""
        snap = snapshot if snapshot is not None else self.get_current()
        if not snap:
            return {}

        flat: dict[str, Any] = {
            "snapshot_id": snap.get("snapshot_id"),
            "version": snap.get("version"),
            "observed_at": snap.get("observed_at"),
            "received_at": snap.get("received_at"),
            "observation_time_basis": snap.get("observation_time_basis"),
            "source": snap.get("source"),
            "met": snap.get("mission_elapsed_time"),
            "mission_elapsed_time": snap.get("mission_elapsed_time"),
            "readings": snap.get("readings") or {},
            "predictions": snap.get("predictions") or {},
            "alerts": snap.get("alerts") or _empty_alerts_state(),
            "active_alerts": (snap.get("alerts") or {}).get("items") or [],
            "mission_assessment": snap.get("mission_assessment")
            or derive_mission_health_summary(snap.get("alerts")),
            "availability": snap.get("availability") or {},
            "ingestion": snap.get("ingestion") or {},
        }

        for key, entry in (snap.get("readings") or {}).items():
            if isinstance(entry, dict) and "value" in entry:
                flat[key] = entry["value"]

        for key, value in (snap.get("predictions") or {}).items():
            flat[key] = value

        return flat

    def build_llm_context(self, resolution: dict) -> str:
        """Structured telemetry block for the LLM system prompt."""
        lines = [
            "",
            "[OrbitNexus Telemetry Context]",
            f"Selection policy result: {resolution.get('selection')}",
            f"Note: {resolution.get('note')}",
        ]
        snap = resolution.get("snapshot")
        if not snap:
            lines.append(
                "DATA AVAILABILITY: MISSING — no current readings or predictions. "
                "Do not claim the astronaut is healthy based on absent data."
            )
            return "\n".join(lines)

        availability = snap.get("availability") or {}
        source = snap.get("source") or "unknown"
        simulated = source in {"simulation", "demo", "simulated"}
        basis = snap.get("observation_time_basis") or "unknown"
        lines.extend([
            f"Snapshot ID: {snap.get('snapshot_id')}",
            f"Version: {snap.get('version')}",
            f"Observed at (measurement time): {snap.get('observed_at')}",
            f"Received at (server ingest): {snap.get('received_at')}",
            f"Observation time basis: {basis}",
            f"Source: {source}" + (" (SIMULATED DATA — not a live device)" if simulated else ""),
            f"Mission elapsed time: {snap.get('mission_elapsed_time') or 'N/A'}",
            f"Freshness: {availability.get('freshness', 'unknown')} "
            f"(age_seconds={availability.get('age_seconds')})",
        ])

        if basis == "receipt_time_assumed":
            lines.append(
                "WARNING: Measurement time was not supplied by the device. "
                "observed_at reflects server receipt time and is uncertain."
            )
        if availability.get("freshness") == "stale":
            lines.append(
                "WARNING: This snapshot is STALE based on observed_at. "
                "Treat values as outdated; do not present them as current without stating the age."
            )
        if simulated:
            lines.append(
                "IMPORTANT: All readings below are SIMULATED for demonstration. "
                "State that clearly when discussing them."
            )

        readings = snap.get("readings") or {}
        lines.append("Readings:")
        if not readings:
            lines.append(
                "  (none) — current measurements unavailable. "
                "Do not insert fallback vital signs."
            )
        else:
            for key, spec in READING_SPECS.items():
                entry = readings.get(key)
                if not entry:
                    lines.append(f"  - {spec['label']} ({key}): UNAVAILABLE")
                    continue
                stale_flag = " STALE" if entry.get("stale") else ""
                lines.append(
                    f"  - {spec['label']} ({key}): {entry.get('value')} {entry.get('unit', '')}"
                    f" | observed_at={entry.get('observed_at')}"
                    f" | received_at={entry.get('received_at')}"
                    f" | basis={entry.get('observation_time_basis')}{stale_flag}"
                )

        predictions = snap.get("predictions") or {}
        lines.append("ML predictions:")
        if not predictions:
            lines.append("  (none) — predictions unavailable for this snapshot.")
        else:
            for key in PREDICTION_KEYS:
                if key in predictions and key not in DERIVED_PREDICTION_KEYS:
                    lines.append(f"  - {key}: {predictions[key]}")
            for key, value in predictions.items():
                if key not in PREDICTION_KEYS:
                    lines.append(f"  - {key}: {value}")

        alerts = snap.get("alerts") or _empty_alerts_state()
        status = alerts.get("status", "unknown")
        coverage = alerts.get("coverage") or {}
        lines.append(f"Alerts status: {status}")
        lines.append(
            f"Alert coverage: assessed={coverage.get('assessed', [])} "
            f"not_assessed={coverage.get('not_assessed', [])} "
            f"failed={coverage.get('failed', [])}"
        )
        if status == "unknown":
            lines.append(
                "  Alert assessment incomplete/unknown — do not assume an all-clear."
            )
        elif status == "partial":
            lines.append(
                "  Only some alert categories were successfully assessed. "
                "Do NOT treat this as an overall all-clear; unassessed or failed "
                "categories may still hide hazards. Category assessment times are "
                "historical for untouched categories."
            )
        elif status == "clear":
            lines.append(
                "  Every alert category was successfully assessed clear "
                f"(assessed_at={alerts.get('assessed_at')})."
            )
        else:
            for item in alerts.get("items") or []:
                lines.append(f"  - {item}")

        categories = alerts.get("categories") or {}
        if categories:
            lines.append("Alert categories:")
            for cat in ALERT_CATEGORIES:
                c = categories.get(cat) or {}
                line = (
                    f"  - {cat}: status={c.get('status')} "
                    f"assessed_at={c.get('assessed_at')} "
                    f"last_evaluation={c.get('last_evaluation')} "
                    f"last_evaluation_at={c.get('last_evaluation_at')}"
                )
                if c.get("last_error"):
                    line += f" last_error={c.get('last_error')}"
                lines.append(line)

        mission = snap.get("mission_assessment") or derive_mission_health_summary(alerts)
        level = mission.get("level")
        lines.append("Mission health summary:")
        lines.append(
            "  (derived from the accepted assessment state above — not from the "
            "last incoming packet)"
        )
        lines.append(f"  Summary: {mission.get('summary')}")
        lines.append(f"  Assessment level: {level}")
        lines.append(
            f"  Active alert categories: {mission.get('active_categories', [])} | "
            f"failed latest evaluation: {mission.get('failed_categories', [])} | "
            f"not assessed: {mission.get('not_assessed_categories', [])}"
        )
        if level in {"indeterminate", "partial", "unknown"}:
            lines.append(
                "  WARNING: The assessment is incomplete. Do not present this as "
                "an all-clear or claim overall readiness."
            )
        if mission.get("failed_categories"):
            lines.append(
                "  WARNING: The latest evaluation failed for "
                f"{mission.get('failed_categories')}. Any retained alert reflects "
                "the last successful assessment, not a fresh result."
            )

        return "\n".join(lines)

    # ── internals ────────────────────────────────────────────────────────────

    def _make_reading(
        self,
        key: str,
        value: Any,
        *,
        observed_at: str,
        received_at: str,
        observation_time_basis: str,
    ) -> dict:
        spec = READING_SPECS[key]
        return {
            "value": value,
            "unit": spec["unit"],
            "label": spec["label"],
            "observed_at": observed_at,
            "received_at": received_at,
            "observation_time_basis": observation_time_basis,
            "stale": False,
        }

    def _build_snapshot(
        self,
        *,
        readings: dict,
        predictions: dict,
        source: str,
        observed_at: str,
        received_at: str,
        observation_time_basis: str,
        alerts: dict,
        mission_elapsed: Optional[str],
        extra: Optional[dict],
        skipped_readings: Optional[list] = None,
        skipped_alert_categories: Optional[list] = None,
        skipped_predictions: Optional[list] = None,
        mission_assessment: Optional[dict] = None,
        summary_ingestion: Optional[dict] = None,
    ) -> dict:
        self._version += 1
        ingestion = {
            "skipped_outdated_readings": list(skipped_readings or []),
            "skipped_outdated_alert_categories": list(skipped_alert_categories or []),
            "skipped_outdated_predictions": list(skipped_predictions or []),
        }
        if summary_ingestion:
            ingestion.update(summary_ingestion)
        assessment = mission_assessment or derive_mission_health_summary(alerts)
        merged_predictions = dict(predictions or {})
        merged_predictions["mission_health_summary"] = assessment["summary"]
        return {
            "snapshot_id": f"snap-{uuid.uuid4().hex[:12]}",
            "version": self._version,
            "observed_at": observed_at,
            "received_at": received_at,
            "observation_time_basis": observation_time_basis,
            "source": source,
            "mission_elapsed_time": mission_elapsed,
            "readings": readings,
            "predictions": merged_predictions,
            "alerts": alerts,
            "mission_assessment": assessment,
            "extra": extra or {},
            "ingestion": ingestion,
        }

    def _annotate(self, snap: dict, now: Optional[float] = None) -> dict:
        """Compute freshness flags without mutating stored observation times."""
        now = now if now is not None else time.time()
        observed_at = snap.get("observed_at")
        age = age_seconds(observed_at, now)
        freshness = "missing"
        if snap.get("readings") or snap.get("predictions"):
            if age is None:
                freshness = "unknown"
            elif age > self.stale_after_seconds:
                freshness = "stale"
            else:
                freshness = "fresh"

        readings = snap.get("readings") or {}
        for entry in readings.values():
            if not isinstance(entry, dict):
                continue
            r_age = age_seconds(entry.get("observed_at"), now)
            entry["stale"] = bool(r_age is not None and r_age > self.stale_after_seconds)
            entry["age_seconds"] = None if r_age is None else round(r_age, 3)

        snap["availability"] = {
            "has_readings": bool(readings),
            "has_predictions": bool(snap.get("predictions")),
            "freshness": freshness,
            "age_seconds": None if age is None else round(age, 3),
            "stale_after_seconds": self.stale_after_seconds,
            "observation_time_basis": snap.get("observation_time_basis"),
        }
        return snap

    def _publish(self, snap: dict) -> dict:
        annotated = self._annotate(copy.deepcopy(snap))
        self._current = copy.deepcopy(snap)
        sid = snap["snapshot_id"]
        self._history[sid] = copy.deepcopy(snap)
        self._history_order.append(sid)
        while len(self._history_order) > self._max_history:
            old = self._history_order.pop(0)
            if old != self._current.get("snapshot_id"):
                self._history.pop(old, None)
        return annotated


# Process-wide singleton used by the Flask app.
snapshot_store = TelemetrySnapshotStore()
