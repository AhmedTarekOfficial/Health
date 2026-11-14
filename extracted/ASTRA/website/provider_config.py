import os
import random
import time
from typing import Any, Dict, List, Optional

DEFAULT_PROVIDER = "groq"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def _read_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def classify_provider_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "401" in message or "unauthorized" in message or "api key" in message or "authentication" in message:
        return "authentication_error"
    if "429" in message or "rate limit" in message:
        return "rate_limit"
    if "404" in message or "model" in message or "not found" in message:
        return "unavailable_model"
    if "503" in message or "timeout" in message or "temporarily unavailable" in message or "capacity" in message:
        return "temporary_capacity_error"
    return "provider_error"


def _get_groq_api_key() -> Optional[str]:
    raw = os.getenv("GROQ_API_KEY", "").strip()
    return raw or None


def _get_groq_chat_model() -> str:
    chosen = os.getenv("GROQ_CHAT_MODEL", "").strip()
    if chosen:
        return chosen
    return DEFAULT_GROQ_MODEL


def _resolve_groq_model(candidate_names: List[str], fallback: str = DEFAULT_GROQ_MODEL) -> str:
    preferred = [
        os.getenv("GROQ_CHAT_MODEL", "").strip(),
        "llama-3.3-70b-versatile",
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    ]
    for p in preferred:
        if p and p in candidate_names:
            return p
    for candidate in candidate_names:
        if candidate and ("llama" in candidate.lower() or "qwen" in candidate.lower() or "gpt" in candidate.lower()):
            return candidate
    return candidate_names[0] if candidate_names else fallback


def _build_model_candidates() -> List[str]:
    env_model = os.getenv("GROQ_CHAT_MODEL", "").strip()
    candidates = [
        env_model,
        "llama-3.3-70b-versatile",
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-120b",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    ]
    seen: List[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.append(item)
    return seen


def get_provider_settings() -> Dict[str, Any]:
    provider = (os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if provider not in {"groq", "gemini", "google"}:
        provider = DEFAULT_PROVIDER

    groq_key = _get_groq_api_key()
    model = _get_groq_chat_model()
    return {
        "provider": provider,
        "route": "groq" if provider == "groq" else provider,
        "api_key_present": bool(groq_key),
        "model": model,
        "fallback_provider": None,
        "timeout_seconds": int(os.getenv("LLM_TIMEOUT_SECONDS", "20")),
        "max_retries": int(os.getenv("LLM_MAX_RETRIES", "2")),
        "health_check_enabled": _read_bool_env("LLM_HEALTH_CHECK", True),
    }


def _sleep_with_backoff(attempt: int) -> None:
    base = 0.5
    jitter = random.uniform(0.0, 0.5)
    time.sleep(base * (2**max(0, attempt-1)) + jitter)


def invoke_groq_chat(messages: List[Dict[str, str]], *, model: Optional[str] = None, temperature: float = 0.6, max_tokens: int = 1024) -> Dict[str, Any]:
    from groq import Groq

    api_key = _get_groq_api_key()
    if not api_key:
        return {
            "status": "unavailable",
            "reply": "OrbitNexus AI is unavailable because no Groq API key is configured. Set GROQ_API_KEY and restart the service.",
            "source": "system",
            "provider": "groq",
            "error_type": "missing_api_key",
        }

    client = Groq(api_key=api_key, timeout=get_provider_settings()["timeout_seconds"])
    target_model = model or _get_groq_chat_model()
    available_models: List[str] = []
    try:
        available_models = [m.id for m in client.models.list().data]
    except Exception:
        available_models = []

    if available_models and target_model not in available_models:
        target_model = _resolve_groq_model(available_models, fallback=target_model)

    last_error: Optional[Exception] = None
    for attempt in range(1, get_provider_settings()["max_retries"] + 2):
        try:
            completion = client.chat.completions.create(
                model=target_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            reply = completion.choices[0].message.content if completion.choices else "No response returned."
            return {
                "status": "ok",
                "reply": reply,
                "source": target_model,
                "provider": "groq",
                "model_latency_ms": getattr(getattr(completion, "usage", None), "total_tokens", None),
            }
        except Exception as exc:  # pragma: no cover - network/provider behavior is environment-dependent
            last_error = exc
            if attempt > get_provider_settings()["max_retries"]:
                break
            _sleep_with_backoff(attempt)

    error_type = classify_provider_error(last_error) if last_error else "provider_error"
    return {
        "status": "error",
        "reply": (
            "OrbitNexus AI is temporarily unavailable. The configured Groq provider returned a retryable error "
            "and no safe fallback is active."
        ),
        "source": "system",
        "provider": "groq",
        "error_type": error_type,
    }


def get_llm_provider_status() -> Dict[str, Any]:
    settings = get_provider_settings()
    is_available = settings["provider"] == "groq" and bool(_get_groq_api_key())
    return {
        "provider": settings["provider"],
        "route": settings["route"],
        "configured": is_available,
        "api_key_present": bool(_get_groq_api_key()),
        "model": _get_groq_chat_model(),
        "health_check": settings["health_check_enabled"],
        "max_retries": settings["max_retries"],
        "timeout_seconds": settings["timeout_seconds"],
    }
