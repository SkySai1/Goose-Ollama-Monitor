"""Register a launch card for Goose versions whose Apps page filters to 'apps'."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from .api import app_html


def install_launcher(port=11436, extension="ollamamonitor"):
    from .mcp import URI
    root = os.getenv("GOOSE_PATH_ROOT", "")
    config = (Path(root) / "config" if root and Path(root).is_absolute() else
              Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "goose")
    cache = config / "mcp-apps-cache"
    cache.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"apps::{URI}".encode()).hexdigest()
    target = cache / f"apps_{digest}.json"
    # 'apps' makes the card visible in Goose 1.52. The FIRST server routes
    # resource reads and UI tool calls to our actual MCP extension, not Apps.
    card = dict(uri=URI, name="ollama-monitor", description="Local Ollama memory and context monitor",
                mimeType="text/html;profile=mcp-app", text=app_html(port),
                mcpServers=[extension, "apps"], width=880, height=940, resizable=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=cache, prefix=".ollama-card-", delete=False) as stream:
        json.dump(card, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    os.replace(temporary, target)
    return target
