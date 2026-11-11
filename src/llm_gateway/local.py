"""Local model server helpers that are not model calls (health check, installed tags)."""
from __future__ import annotations

import httpx

OLLAMA_URL = "http://localhost:11434"


class OllamaUnreachable(Exception):
    pass


def installed_ollama_models(base_url: str = OLLAMA_URL, timeout: float = 5.0) -> set[str]:
    """Tags known to the local Ollama server. Raises OllamaUnreachable when it does not answer."""
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=timeout)
        return {m["name"] for m in r.json().get("models", [])}
    except Exception as e:
        raise OllamaUnreachable(type(e).__name__) from e


def ollama_has(tag: str, installed: set[str]) -> bool:
    return tag in installed or f"{tag}:latest" in installed
