#!/usr/bin/env python3
"""Ollama Monitor entry point; stdlib only, Python 3.10+."""
import argparse
import json
import math
import os
import signal
import sys
import threading
from urllib.parse import urlsplit
from ollama_monitor.api import make_server
from ollama_monitor.collector import Monitor


def cli(snapshot):
    if not snapshot:
        return
    def number(value, suffix=""):
        return "unavailable" if value is None else f"{value:,.1f}{suffix}"
    def gb(value):
        return number(value / 1024**3 if value is not None else None, " GiB")
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    s, o, p, c = (snapshot[k] for k in ("system_memory", "ollama_memory", "memory_pressure", "context"))
    print("OLLAMA MONITOR  |", "ONLINE" if snapshot["ollama"]["online"] else "OFFLINE",
          "| version", snapshot["ollama"]["version"] or "unknown")
    print("Models:", ", ".join(m["name"] for m in snapshot["models"] or []) or "none / unavailable")
    print("System RAM:", gb(s["used_bytes"]), "/", gb(s["total_bytes"]), number(s["used_percent"], "%"))
    print("Ollama RSS:", gb(o["used_bytes"]), "CPU:", number(o["cpu_percent"], "%"))
    print("Memory pressure (derived):", number(p["pressure_percent"], "%"), "Swap:", gb(s["swap_used_bytes"]))
    print("Context:", c["used_tokens"], "/", c["max_tokens"], number(c["used_percent"], "%"), c["source"])
    for row in snapshot["processes"] or []:
        print(f"  PID {row['pid']:>6}  {gb(row['rss_bytes']):>12}  CPU {row['cpu']:6.1f}%  {row['exe']}")
    d = c["diagnostics"]
    print("Recent prompt:", d["prompt_total"], "Generation:", d["gen_eval"], "tokens",
          "Speed:", d["gen_tps"], "tok/s", "Context shifts:", d["context_shifts"])
    for line in c["recent_log"]:
        print(line)
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_interval", type=float, nargs="?", help="legacy sampling interval")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--port", type=int, default=11436)
    parser.add_argument("--host", default=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--log", default=os.getenv("OLLAMA_LOG", "~/.ollama/logs/server.log"))
    parser.add_argument("--server", action="store_true", help="run HTTP service (default)")
    parser.add_argument("--cli", action="store_true", help="display shared snapshots alongside HTTP service")
    parser.add_argument("--once", action="store_true", help="print one JSON snapshot and exit")
    parser.add_argument("--mcp", action="store_true", help="Goose stdio adapter; uses an existing HTTP service")
    args = parser.parse_args()
    interval = args.legacy_interval if args.legacy_interval is not None else args.interval
    if not math.isfinite(interval) or interval < 0.5:
        parser.error("interval must be finite and at least 0.5 seconds")
    if not 1 <= args.port <= 65535 or args.port == 11434:
        parser.error("choose a port in 1..65535 other than Ollama's 11434")
    host = args.host if "://" in args.host else "http://" + args.host
    try:
        parsed = urlsplit(host)
        valid = (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                 and not parsed.username and not parsed.password and parsed.path in {"", "/"}
                 and not parsed.query and not parsed.fragment and parsed.port != args.port)
    except ValueError:
        valid = False
    if not valid:
        parser.error("Ollama host must be a local HTTP origin, separate from the monitor port")
    if args.mcp:
        from ollama_monitor.mcp import serve_stdio
        serve_stdio(args.port)
        return
    monitor = Monitor(host.rstrip("/"), interval, args.log)
    if args.once:
        print(json.dumps(monitor.build_snapshot(), indent=2, allow_nan=False))
        return
    try:
        server = make_server(monitor.history, args.port)
    except OSError as error:
        parser.exit(1, f"Cannot bind 127.0.0.1:{args.port}: {error}. Choose --port.\n")
    stopped = threading.Event()
    def stop(*_):
        stopped.set()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    monitor.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Ollama Monitor: http://127.0.0.1:{args.port}  ({interval:g}s sampling)", file=sys.stderr)
    try:
        while not stopped.wait(interval):
            if args.cli or args.legacy_interval is not None:
                cli(monitor.history.status())
    finally:
        server.shutdown()
        server.server_close()
        monitor.stop()


if __name__ == "__main__":
    main()
