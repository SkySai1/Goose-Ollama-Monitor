"""One monitoring core shared by HTTP, CLI and history."""
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from .context import LogReader, get_context_metrics
from .history import History
from .memory import get_system_memory_metrics, get_memory_pressure_metrics
from .ollama import get_loaded_models, get_ollama_status
from .processes import get_processes, get_ollama_memory_metrics


class Monitor:
    def __init__(self, host="http://127.0.0.1:11434", interval=2, log_path="~/.ollama/logs/server.log"):
        self.host, self.interval = host, interval
        self.history = History(interval)
        self.log_reader = LogReader(log_path)
        self.stop_event = threading.Event()
        self.thread = None

    def build_snapshot(self):
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            system_job = pool.submit(get_system_memory_metrics)
            pressure_job = pool.submit(get_memory_pressure_metrics)
            processes_job = pool.submit(get_processes)
            models_job = pool.submit(get_loaded_models, self.host)
            models = models_job.result()
            status_job = pool.submit(get_ollama_status, self.host, models)
            system, pressure, rows = system_job.result(), pressure_job.result(), processes_job.result()
            context = get_context_metrics(models, rows, self.log_reader)
            ollama = status_job.result()
        return dict(timestamp=time.time(), ollama=ollama,
                    model=models[0] if models and len(models) == 1 else None,
                    models=models, system_memory=system, memory_pressure=pressure,
                    ollama_memory=get_ollama_memory_metrics(rows, system["total_bytes"]),
                    context=context, processes=rows,
                    collector=dict(interval_seconds=self.interval,
                                   duration_seconds=round(time.monotonic() - started, 3)))

    def _loop(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            try:
                self.history.append(self.build_snapshot())
            except Exception:
                # Keep the service alive; the UI detects the stale timestamp.
                logging.exception("Snapshot failed")
            deadline += self.interval
            now = time.monotonic()
            if deadline < now:
                deadline = now
            self.stop_event.wait(max(0, deadline - now))

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="ollama-collector", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
