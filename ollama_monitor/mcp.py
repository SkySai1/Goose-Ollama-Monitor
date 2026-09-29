"""Disposable Goose stdio adapter; all readings come from the HTTP service."""
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
                "serverInfo": {"name": "ollama-monitor", "version": "0.1.0"}}
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
                           "_meta": {"ui": {"resourceUri": URI}}}]}
    if method == "tools/call":
        arguments = params.get("arguments") or {}
        range_name = arguments.get("range", "15m")
        if params.get("name") != "monitor_read" or range_name not in {"1m", "15m", "1h"}:
            raise ValueError("Unknown tool or range")
        status = http_json(origin + "/api/status", timeout=2)
        history = http_json(origin + "/api/history?range=" + range_name, timeout=2)
        if not status or not history:
            return {"isError": True, "content": [{"type": "text", "text": "Monitoring backend unavailable"}]}
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
