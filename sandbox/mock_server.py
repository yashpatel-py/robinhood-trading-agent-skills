#!/usr/bin/env python3
"""Preflight sandbox: a stdio MCP server that plays Robinhood's Trading connector with fake data (unofficial).

It serves the same 81 tool names and verbatim input schemas as the live connector (from
connector/tools.snapshot.json) and answers every call from the synthetic household in
evalkit/fixtures/household.json, the same data the evals use. Nothing here talks to Robinhood or to
any network: it reads stdin, writes stdout, and never places, cancels or changes anything.

  * Order-placing tools (place_*, exercise_option) answer CANARY_LIVE_ORDER_PLACED_<tool> plus
    "SANDBOX: no order placed". In Claude Code with the Preflight plugin, the order guard blocks
    them before they get here.
  * Cancels and delete_alert(confirm=true) answer CANARY_CANCEL_EXECUTED_<tool> plus
    "SANDBOX: nothing was cancelled or deleted".
  * Writes (alerts, watchlists, scans) are acknowledged but not stored, so every call sees the same
    household.

Add it to Claude Code from the repository root:
    claude mcp add rh-sandbox -- python3 "$PWD/sandbox/mock_server.py"

Options:
    --variant {base,regular,cash_l2,no_greeks,oco_disabled}   default base
    --anchor 2026-11-16T20:05:00-05:00 | today                default: the fixture anchor, so the
                                                              numbers match the README and the evals
    --log FILE                                                append one JSON line per call (local only)

Protocol: MCP over stdio, newline-delimited JSON-RPC 2.0 (initialize, notifications/initialized,
ping, tools/list, tools/call; resources/list, resources/templates/list and prompts/list answer empty).
Stdlib only; Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "evalkit"))

import render  # noqa: E402

SERVER_NAME = "rh-sandbox"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Preflight sandbox (unofficial; not affiliated with Robinhood Markets, Inc.). This server imitates the "
    "Robinhood Trading MCP connector with a synthetic household for trying the Preflight skills without a "
    "Robinhood account. Every account, position, price and order here is fake. Order-placing tools only return "
    "a canary string; nothing is ever placed, cancelled or saved."
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class SandboxServer:
    """Answers MCP JSON-RPC messages. Stateless between calls."""

    def __init__(self, variant: str = "base", anchor: Optional[str] = None, log: Optional[TextIO] = None) -> None:
        self.world = render.World(variant, anchor)
        self.tools = [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                      for t in render.load_snapshot()]
        self.tool_names = {t["name"] for t in self.tools}
        self.log = log
        self.initialized = False

    # ------------------------------------------------------------------ JSON-RPC plumbing
    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _result(msg_id: Any, result: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def handle_line(self, line: str) -> Optional[str]:
        """One input line in, zero or one output line out."""
        text = line.strip()
        if not text:
            return None
        try:
            message = json.loads(text)
        except ValueError:
            return json.dumps(self._error(None, PARSE_ERROR, "Parse error"))
        if isinstance(message, list):
            replies = [r for r in (self.handle(m) for m in message) if r is not None]
            return json.dumps(replies, ensure_ascii=False) if replies else None
        reply = self.handle(message)
        return None if reply is None else json.dumps(reply, ensure_ascii=False)

    def handle(self, message: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST,
                               "Invalid Request")
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message
        if not isinstance(method, str):
            return None if is_notification else self._error(msg_id, INVALID_REQUEST, "Invalid Request")
        params = message.get("params") or {}
        try:
            if method == "initialize":
                result = self.initialize(params)
            elif method.startswith("notifications/"):
                if method == "notifications/initialized":
                    self.initialized = True
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools}
            elif method == "tools/call":
                result = self.call_tool(params)
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return None if is_notification else self._error(msg_id, METHOD_NOT_FOUND,
                                                                 "Method not found: %s" % method)
        except ValueError as exc:
            return None if is_notification else self._error(msg_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # a fixture bug must not kill the session
            return None if is_notification else self._error(msg_id, INTERNAL_ERROR, "Internal error: %s" % exc)
        return None if is_notification else self._result(msg_id, result)

    # ------------------------------------------------------------------ MCP methods
    def initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}, "instructions": INSTRUCTIONS}

    def call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            raise ValueError("tools/call needs a string name and an object of arguments")
        if name not in self.tool_names:
            text, is_error = "Unknown tool: %s" % name, True
        else:
            text, is_error = self.world.respond(name, args)
        if self.log is not None:
            self.log.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "tool": name,
                                       "is_error": is_error}) + "\n")
            self.log.flush()
        return {"content": [{"type": "text", "text": text}], "isError": is_error}


def serve(server: SandboxServer, stdin: TextIO, stdout: TextIO) -> None:
    for line in stdin:
        reply = server.handle_line(line)
        if reply is not None:
            stdout.write(reply + "\n")
            stdout.flush()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight sandbox MCP server (stdio, synthetic data).")
    parser.add_argument("--variant", default="base", choices=render.VARIANTS)
    parser.add_argument("--anchor", default=None, help="ISO 8601 time or 'today' (default: the fixture anchor)")
    parser.add_argument("--log", default=None, help="append a JSON line per tool call to this file")
    ns = parser.parse_args(argv)
    log = open(ns.log, "a", encoding="utf-8") if ns.log else None
    try:
        server = SandboxServer(ns.variant, ns.anchor, log)
    except render.FixtureError as exc:
        print("rh-sandbox: %s" % exc, file=sys.stderr)
        return 1
    try:
        serve(server, sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        pass
    finally:
        if log is not None:
            log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
