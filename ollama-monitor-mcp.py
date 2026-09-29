#!/usr/bin/env python3
"""Goose extension entry point. Always speaks MCP over stdin/stdout."""
from ollama_monitor.mcp import main


if __name__ == "__main__":
    main()
