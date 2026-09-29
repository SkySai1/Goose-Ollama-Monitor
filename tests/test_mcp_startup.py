"""Exercise the actual extension process without closing stdin to elicit a reply."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class McpStartupTests(unittest.TestCase):
    def exchange(self, process, request):
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.flush()
        deadline = time.monotonic() + 3
        response = b""
        while b"\n" not in response:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                self.fail("MCP did not reply while stdin remained open")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                self.fail("MCP process exited before replying")
            response += chunk
        self.assertEqual(response.count(b"\n"), 1, "stdout must contain only the JSON-RPC response")
        reply = json.loads(response)
        self.assertEqual(reply["id"], request["id"])
        self.assertIn("result", reply)
        self.assertIsNone(process.poll(), "Extension must stay alive between requests")
        return reply["result"]

    def check_startup(self, script, *args):
        # Goose can start extensions with an unrelated working directory.
        with tempfile.TemporaryDirectory() as cwd:
            process = subprocess.Popen([sys.executable, str(ROOT / script), *args],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, cwd=cwd)
            try:
                result = self.exchange(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "goose", "version": "test"}}})
                self.assertIn("resources", result["capabilities"])
                process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                process.stdin.flush()
                result = self.exchange(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                self.assertEqual(result["tools"][0]["name"], "monitor_read")
                result = self.exchange(process, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
                self.assertEqual(result["resources"][0]["uri"], "ui://ollama-monitor/dashboard")
                self.exchange(process, {"jsonrpc": "2.0", "id": 4, "method": "ping"})
                result = self.exchange(process, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                                "params": {"name": "open_monitor", "arguments": {}}})
                self.assertEqual(result["_meta"]["ui"]["resourceUri"], "ui://ollama-monitor/dashboard")
                result = self.exchange(process, {"jsonrpc": "2.0", "id": 6, "method": "resources/read",
                                                "params": {"uri": "ui://ollama-monitor/dashboard"}})
                self.assertIn("ensureBridge", result["contents"][0]["text"])
                self.assertTrue(result["contents"][0]["_meta"]["window"]["resizable"])
                process.stdin.close()
                self.assertEqual(process.wait(timeout=3), 0)
                self.assertEqual(process.stderr.read(), b"")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()

    def test_dedicated_entry_needs_no_mode_flag_or_backend(self):
        self.check_startup("ollama-monitor-mcp.py")

    def test_existing_explicit_mcp_mode_still_works(self):
        self.check_startup("ollama-watch.py", "--mcp")
