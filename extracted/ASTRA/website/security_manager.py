from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Dict, List, Optional


class SecurityManager:
    """Minimal security controls for session verification and request auditing."""

    def __init__(self, secret_key: Optional[str] = None, max_requests_per_minute: int = 60):
        self.secret_key = (secret_key or os.getenv("ORBITNEXUS_SECRET_KEY") or "orbitnexus-local-dev-secret").encode("utf-8")
        self.max_requests_per_minute = max_requests_per_minute
        self.audit_log: List[Dict[str, Any]] = []
        self.rate_limit_window: Dict[str, int] = {}

    def generate_session_token(self, session_id: str) -> str:
        payload = f"{session_id}:{int(time.time() // 60)}:{self.secret_key.decode('utf-8')}"
        return hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify_session_token(self, token: str, session_id: str) -> bool:
        if not token or not session_id:
            return False
        expected = self.generate_session_token(session_id)
        return hmac.compare_digest(token, expected)

    def audit_event(self, event_type: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        entry = {
            "type": event_type,
            "timestamp": int(time.time()),
            "details": details or {},
        }
        self.audit_log.insert(0, entry)
        if len(self.audit_log) > 25:
            self.audit_log.pop()
        return entry

    def check_rate_limit(self, identifier: str, max_requests: Optional[int] = None) -> bool:
        window = int(time.time() // 60)
        bucket_key = f"{identifier}:{window}"
        current = self.rate_limit_window.get(bucket_key, 0) + 1
        self.rate_limit_window[bucket_key] = current
        limit = max_requests if max_requests is not None else self.max_requests_per_minute
        return current <= limit

    def get_security_status(self) -> Dict[str, Any]:
        return {
            "security_enabled": True,
            "rate_limit_per_minute": self.max_requests_per_minute,
            "audit_count": len(self.audit_log),
            "latest_events": self.audit_log[:5],
        }
