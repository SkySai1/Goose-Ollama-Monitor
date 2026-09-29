"""Bounded, read-only operating system and HTTP operations."""
import json
import math
import os
import subprocess
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def run(command, timeout=0.8):
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, env={**os.environ, "LC_ALL": "C"})
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def http_json(url, timeout=0.6):
    try:
        # Local monitoring must not send requests through environment proxies.
        with build_opener(ProxyHandler({})).open(url, timeout=timeout) as response:
            return json.load(response)
    except (URLError, OSError, ValueError):
        return None


def numeric(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value) and value >= 0:
            return int(value)
    return None


def percent(used, total):
    return round(used / total * 100, 2) if used is not None and total else None
