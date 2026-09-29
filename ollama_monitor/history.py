"""Thread-safe in-memory repository; clients do not depend on the storage backend."""
from collections import deque
from copy import deepcopy
import math
import threading
import time

RANGES = {"1m": 60, "15m": 900, "1h": 3600}


def sample_from(snapshot):
    system, ollama, pressure, context = (snapshot[k] for k in
                                         ("system_memory", "ollama_memory", "memory_pressure", "context"))
    return dict(timestamp=snapshot["timestamp"],
                system_ram_percent=system["used_percent"], system_ram_bytes=system["used_bytes"],
                system_total_bytes=system["total_bytes"], swap_bytes=system["swap_used_bytes"],
                ollama_ram_bytes=ollama["used_bytes"], ollama_ram_percent=ollama["used_percent_of_system_ram"],
                memory_pressure_percent=pressure["pressure_percent"], memory_free_percent=pressure["free_percent"],
                context_percent=context["used_percent"], context_used_tokens=context["used_tokens"],
                context_max_tokens=context["max_tokens"], context_source=context["source"],
                context_runner_pid=context["runner_pid"], context_slot=context["slot"],
                context_model=context["model_name"])


class History:
    def __init__(self, interval=2):
        self.interval = interval
        self.samples = deque(maxlen=max(2000, math.ceil(3900 / interval) + 1))
        self.current = None
        self.lock = threading.Lock()

    def append(self, snapshot):
        with self.lock:
            self.current = deepcopy(snapshot)
            self.samples.append(sample_from(snapshot))

    def status(self):
        with self.lock:
            return deepcopy(self.current)

    def query(self, range_name, now=None):
        end = time.time() if now is None else now
        start = end - RANGES[range_name]
        with self.lock:
            samples = [dict(s) for s in self.samples if start <= s["timestamp"] <= end]
        # ~1800 points/hour is inexpensive on Canvas. Return all raw observations;
        # no decimation can hide peaks or turn null gaps into fabricated readings.
        return dict(range=range_name, start=start, end=end, interval_seconds=self.interval,
                    samples=samples)
