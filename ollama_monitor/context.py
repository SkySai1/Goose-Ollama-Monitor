"""Runner context occupancy, preserving tolerant slot/log parsing from ollama-watch."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
import re
import os
import time
from .io import http_json, numeric, percent
from .processes import runner_processes


def _extract_n_decoded(value: Any) -> int:
    """Extract n_decoded from llama.cpp/Ollama slot variants.

    Upstream llama.cpp normally exposes next_token as an object, but some
    Ollama runner builds (notably speculative/MTP variants) may expose a list
    or another nested structure. Never assume .get() is available.
    """
    if isinstance(value, dict):
        direct = numeric(value.get("n_decoded"))
        if direct is not None:
            return max(0, direct)

        # Be tolerant of wrappers introduced by runner-specific builds.
        # Search nested JSON generically: speculative/MTP builds can add
        # additional wrapper keys around target/draft token state.
        candidates: list[int] = []
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                decoded = _extract_n_decoded(nested)
                if decoded >= 0:
                    candidates.append(decoded)
        return max(candidates) if candidates else -1

    if isinstance(value, list):
        # A speculative runner can expose several token-state objects.
        # They describe parallel/draft state, so summing them would overcount;
        # the largest n_decoded is the safest generation-progress value.
        candidates = [_extract_n_decoded(item) for item in value]
        candidates = [n for n in candidates if n >= 0]
        return max(candidates) if candidates else -1

    return -1


def _slot_used_tokens(slot: dict[str, Any]) -> int:
    """Best-effort extraction of occupied KV/context tokens from a slot."""
    for key in (
        "n_past",
        "n_cache_tokens",
        "tokens_cached",
        "n_tokens",
        "cache_n",
    ):
        value = numeric(slot.get(key))
        if value is not None and value >= 0:
            return value
    return -1



def parse_log(lines: list[str]) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "prompt_total": -1,
        "released_used": -1,
        "last_n_past": -1,
        "n_ctx": 0,
        "truncated": "unknown",
        "context_shifts": 0,
        "prompt_eval": -1,
        "prompt_tps": None,
        "gen_eval": -1,
        "gen_tps": None,
    }

    for line in lines:
        low = line.lower()
        if "context shift" in low:
            stats["context_shifts"] += 1

        if m := re.search(r"task\.n_tokens\s*[=:]\s*(\d+)", line):
            stats["prompt_total"] = int(m.group(1))
            # A new task invalidates the previous request's cached occupancy.
            stats["last_n_past"] = stats["released_used"] = -1
        if m := re.search(r"n_ctx_slot\s*[=:]\s*(\d+)", line):
            stats["n_ctx"] = int(m.group(1))
        if m := re.search(r'[" ]n_ctx[" ]*[:=]\s*(\d+)', line):
            stats["n_ctx"] = int(m.group(1))
        if m := re.search(r'n_past[" ]*[:=]\s*(\d+)', line):
            stats["last_n_past"] = int(m.group(1))

        if "slot released" in low or "slot release" in low:
            if m := re.search(r'n_cache_tokens[" ]*[:=]\s*(\d+)', line):
                stats["released_used"] = int(m.group(1))
                stats["last_n_past"] = stats["released_used"]
            elif m := re.search(r'n_past[" ]*[:=]\s*(\d+)', line):
                stats["released_used"] = int(m.group(1))
            if m := re.search(r'truncated[" ]*[:=]\s*(true|false)', line, re.I):
                stats["truncated"] = m.group(1).lower()

        if "prompt eval time" in low:
            if m := re.search(r"/\s*(\d+)\s+tokens", line):
                stats["prompt_eval"] = int(m.group(1))
            if m := re.search(r"([0-9]+(?:\.[0-9]+)?)\s+tokens per second", line):
                stats["prompt_tps"] = float(m.group(1))

        if "generation eval time" in low:
            if m := re.search(r"/\s*(\d+)\s+(?:runs|tokens)", line):
                stats["gen_eval"] = int(m.group(1))
            if m := re.search(r"([0-9]+(?:\.[0-9]+)?)\s+tokens per second", line):
                stats["gen_tps"] = float(m.group(1))
    return stats


class LogReader:
    """Read only new bounded log bytes; invalidate on rotation/truncation/model change.

    Historical unscoped logs cannot reliably be assigned to the current runner.
    Start at EOF on each identity change, accepting only subsequently observed data.
    """
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.identity = None
        self.file_id = None
        self.offset = 0
        self.lines = []
        self.observed_at = None
        self.pending = b""

    def read(self, identity):
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                file_id = (stat.st_dev, stat.st_ino)
                if (identity != self.identity or file_id != self.file_id or
                        stat.st_size < self.offset):
                    self.identity, self.file_id = identity, file_id
                    self.offset, self.lines, self.pending = stat.st_size, [], b""
                    self.observed_at = None
                stream.seek(max(self.offset, stat.st_size - 262144))
                data = stream.read(262144)
                self.offset = stream.tell()
            if data:
                parts = (self.pending + data).split(b"\n")
                self.pending = parts.pop()
                new = [line.decode("utf-8", errors="replace") for line in parts]
                self.lines = (self.lines + new)[-1200:]
                if any(re.search(r"n_past|n_cache_tokens|task\.n_tokens", line) for line in new):
                    self.observed_at = time.time()
        except OSError:
            self.lines, self.observed_at = [], None
        return parse_log(self.lines)


def read_slots(runner):
    data = http_json(f"http://127.0.0.1:{runner['port']}/slots")
    result = []
    for slot in data if isinstance(data, list) else []:
        if not isinstance(slot, dict):
            continue
        decoded = _extract_n_decoded(slot.get("next_token"))
        if decoded < 0:
            decoded = numeric(slot.get("n_decoded"))
        used = _slot_used_tokens(slot)
        result.append(dict(pid=runner["pid"], port=runner["port"], slot=slot.get("id"),
                           processing=slot.get("is_processing") is True,
                           max_tokens=numeric(slot.get("n_ctx")) or None,
                           used_tokens=used if used >= 0 else None,
                           generated_tokens=decoded if decoded is not None and decoded >= 0 else None))
    return result


def get_context_metrics(models, rows, log_reader):
    runners = runner_processes(rows)
    identity = (tuple(sorted((m["name"], m.get("digest") or "") for m in models or [])),
                tuple(sorted((r["pid"], r["command"]) for r in rows or [])))
    log = log_reader.read(identity)
    maximum = models[0].get("context_length") if models and len(models) == 1 else None
    diagnostics = {key: (None if isinstance(value, (int, float)) and value < 0 else value)
                   for key, value in log.items()}
    diagnostics["n_ctx"] = diagnostics["n_ctx"] or None
    result = dict(used_tokens=None, max_tokens=maximum, used_percent=None,
                  source="unavailable", estimated=False, processing=None,
                  runner_pid=None, slot=None, observed_at=None,
                  model_name=models[0]["name"] if models and len(models) == 1 else None,
                  slots=[], diagnostics=diagnostics,
                  recent_log=log_reader.lines[-18:])
    if not models:
        return result
    if runners:
        with ThreadPoolExecutor(max_workers=min(len(runners), 8)) as pool:
            slots = [s for group in pool.map(read_slots, runners) for s in group]
    else:
        slots = []
    result["slots"] = slots
    # Report one explicit slot: active first, then highest occupancy. Never add
    # independent contexts, or combine one model's capacity with another's usage.
    slot = max(slots, key=lambda s: (s["processing"], s["used_tokens"] is not None,
                                    s["used_tokens"] or 0), default=None)
    if slot:
        result.update(used_tokens=slot["used_tokens"],
                      max_tokens=slot["max_tokens"], processing=slot["processing"],
                      runner_pid=slot["pid"], slot=slot["slot"],
                      generated_tokens=slot["generated_tokens"])
        if slot["used_tokens"] is not None:
            result.update(source="runner /slots", observed_at=time.time())
    # API context is per model, not necessarily per parallel slot. Use it only
    # when the observed runner has exactly one slot.
    if slot and not result["max_tokens"] and len(slots) == 1 and len(runners) == 1:
        result["max_tokens"] = maximum
    fresh = log_reader.observed_at is not None and time.time() - log_reader.observed_at <= 30
    if (result["used_tokens"] is None and fresh and len(models) == 1 and
            len(runners) == 1 and len(slots) <= 1):
        # A last n_past observation is more recent than an earlier slot release.
        used = log["last_n_past"] if log["last_n_past"] >= 0 else log["released_used"]
        source = "recent log observation"
        if used < 0 and slot and slot["processing"] and log["prompt_total"] >= 0:
            if slot["generated_tokens"] is not None and not log["context_shifts"]:
                used = log["prompt_total"] + slot["generated_tokens"]
                source = "prompt + generated estimate"
        if used >= 0:
            result.update(used_tokens=used, source=source, estimated=True,
                          observed_at=log_reader.observed_at)
            result["max_tokens"] = result["max_tokens"] or log["n_ctx"] or maximum
    result["used_percent"] = percent(result["used_tokens"], result["max_tokens"])
    return result
