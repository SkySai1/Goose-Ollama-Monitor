"""Disposable Goose stdio adapter; all readings come from the HTTP service."""
import argparse
import json
import sys
from .api import app_html
from .io import http_json

URI = "ui://ollama-monitor/dashboard"


def dispatch(method, params, port):
    origin = f"http://127.0.0.1:{port}"
    meta = {"ui": {"csp": {"connectDomains": [origin]}},
            "window": {"width": 880, "height": 940, "resizable": True}}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"resources": {}, "tools": {}},
                "serverInfo": {"name": "ollama-monitor", "version": "0.2.0"},
                "instructions": "Call open_monitor to display the Ollama Monitor MCP App. "
                                "The app reads metrics through monitor_read; no shell commands are needed."}
    if method == "ping":
        return {}
    if method == "resources/list":
        return {"resources": [{"uri": URI, "name": "ollama-monitor",
                               "description": "Local Ollama memory and context monitor",
                               "mimeType": "text/html;profile=mcp-app", "_meta": meta}]}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "resources/read":
        if params.get("uri") != URI:
            raise ValueError("Unknown resource")
        return {"contents": [{"uri": URI, "mimeType": "text/html;profile=mcp-app",
                              "text": app_html(port), "_meta": meta}]}
    if method == "tools/list":
        return {"tools": [{"name": "monitor_read", "description": "Read Ollama Monitor status and history",
                           "inputSchema": {"type": "object", "properties": {
                               "range": {"type": "string", "enum": ["1m", "15m", "1h"]}},
                               "additionalProperties": False},
                           "annotations": {"readOnlyHint": True, "openWorldHint": False},
                           "_meta": {"ui": {"resourceUri": URI, "visibility": ["app"]}}},
                          {"name": "open_monitor", "title": "Open Ollama Monitor",
                           "description": "Open the interactive Ollama Monitor app with four memory/context charts. "
                                          "Use this when the user asks to open or show the Ollama monitoring dashboard.",
                           "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                           "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                           "idempotentHint": True, "openWorldHint": False},
                           "_meta": {"ui": {"resourceUri": URI, "visibility": ["model", "app"]}}}]}
    if method == "tools/call":
        arguments = params.get("arguments") or {}
        if params.get("name") == "open_monitor":
            if arguments:
                raise ValueError("open_monitor takes no arguments")
            # The host resolves this resource via resources/read. The app can
            # render its reconnect state even when the monitoring service is down.
            return {"content": [{"type": "text", "text": "Ollama Monitor dashboard"}],
                    "_meta": {"ui": {"resourceUri": URI}}}
        range_name = arguments.get("range", "15m")
        if params.get("name") != "monitor_read" or range_name not in {"1m", "15m", "1h"}:
            raise ValueError("Unknown tool or range")
        status = http_json(origin + "/api/status", timeout=2)
        history = http_json(origin + "/api/history?range=" + range_name, timeout=2)
        if not status or not history:
            return {"isError": True, "content": [{"type": "text", "text": f"No data from {origin}. Check the monitoring service."}]}
        data = {"status": status, "history": history}
        return {"content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data}
    raise LookupError("Method not found")


def serve_stdio(port):
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or "id" not in request:
                continue
            result = dispatch(request.get("method"), request.get("params") or {}, port)
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as error:
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                        "error": {"code": -32601 if isinstance(error, LookupError) else -32602,
                                  "message": str(error)}}
        print(json.dumps(response), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Ollama Monitor MCP stdio adapter for Goose")
    parser.add_argument("--port", type=int, default=11436, help="existing monitoring service port")
    parser.add_argument("--install-goose-app", action="store_true", help="register a card in Goose Apps and exit")
    parser.add_argument("--extension-name", default="ollamamonitor", help="Goose extension key for the launch card")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.port == 11434:
        parser.error("choose a monitor port in 1..65535 other than Ollama's 11434")
    if args.install_goose_app:
        from .goose import install_launcher
        print(install_launcher(args.port, args.extension_name))
        return
    serve_stdio(args.port)
