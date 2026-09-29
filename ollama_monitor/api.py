"""Loopback-only HTTP server. No commands or mutable API endpoints."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .history import RANGES

APP_PATH = Path(__file__).resolve().parent.parent / "app" / "ollama-monitor.html"


def app_html(port):
    return APP_PATH.read_text().replace("http://127.0.0.1:11436", f"http://127.0.0.1:{port}")


def make_server(history, port=11436):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def trusted(self):
            if self.headers.get("Host", "") not in {f"127.0.0.1:{self.server.server_port}",
                                                     f"localhost:{self.server.server_port}"}:
                return False
            origin = self.headers.get("Origin")
            if origin is None or origin == "null":
                return True
            try:
                parsed = urlsplit(origin)
                return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
            except ValueError:
                return False

        def respond(self, code, data, content_type="application/json; charset=utf-8"):
            payload = (json.dumps(data, allow_nan=False).encode() if isinstance(data, (dict, list))
                       else data.encode())
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            origin = self.headers.get("Origin")
            if origin and self.trusted():
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if not self.trusted():
                self.respond(403, {"error": "Local requests only"})
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/api/status":
                status = history.status()
                self.respond(200 if status else 503, status or {"error": "Collector warming up"})
            elif parsed.path == "/api/history":
                range_name = parse_qs(parsed.query).get("range", ["15m"])[0]
                if range_name not in RANGES:
                    self.respond(400, {"error": "range must be 1m, 15m or 1h"})
                    return
                self.respond(200, history.query(range_name))
            elif parsed.path in {"/", "/ollama-monitor.html"}:
                self.respond(200, app_html(self.server.server_port), "text/html; charset=utf-8")
            else:
                self.respond(404, {"error": "Not found"})

        def do_OPTIONS(self):
            if not self.trusted():
                self.respond(403, {"error": "Local requests only"})
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "null"))
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
