from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class MemoryManager:
    """Lightweight persistent memory for recent health events and reasoning context."""

    def __init__(self, max_events: int = 25):
        self.max_events = max_events
        self.events: List[Dict[str, Any]] = []

    def add_event(self, event_type: str, summary: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        entry = {
            "type": str(event_type),
            "summary": str(summary),
            "metadata": dict(metadata or {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.events.insert(0, entry)
        if len(self.events) > self.max_events:
            self.events.pop()
        return dict(entry)

    def get_recent_context(self, limit: int = 5) -> List[Dict[str, Any]]:
        return [dict(event) for event in self.events[:limit]]

    def build_context_prompt(self, limit: int = 5) -> str:
        if not self.events:
            return "No prior health memory recorded."

        lines = ["Recent health memory:"]
        for event in self.events[:limit]:
            ts = event["timestamp"][:19]
            lines.append(f"- {ts} | {event['type']}: {event['summary']}")
        return "\n".join(lines)

    def get_summary(self) -> Dict[str, Any]:
        return {
            "count": len(self.events),
            "recent": self.get_recent_context(limit=5),
            "summary": self.build_context_prompt(limit=5),
        }
