"""Ollama API access. Allocated context is never treated as occupied context."""
from .io import http_json, numeric


def get_loaded_models(host):
    data = http_json(f"{host}/api/ps")
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return None
    return [dict(name=m.get("name") or m.get("model") or "unknown",
                 digest=m.get("digest"), size_bytes=numeric(m.get("size")),
                 vram_bytes=numeric(m.get("size_vram")),
                 context_length=numeric(m.get("context_length")),
                 expires_at=m.get("expires_at"))
            for m in data["models"] if isinstance(m, dict)]


def get_ollama_status(host, models):
    version = http_json(f"{host}/api/version")
    return {"online": models is not None or isinstance(version, dict),
            "version": version.get("version") if isinstance(version, dict) else None,
            "models_available": models is not None}
