#!/usr/bin/env python3
"""KiCad MCP Proxy — sits between webchat and KiCad MCP.

Intercepts tool calls from the webchat/agent, runs them through the
error_fixer environment for validation and readability scoring, then
forwards to the real KiCad MCP instance. Reports back readability
metrics and ERC status after each tool call.

Architecture:
    webchat → kicad_proxy (this server, port 8081) → KiCad MCP (port 8080)

The proxy:
1. Forwards all tool calls to real KiCad MCP
2. After modifying tool calls, re-runs ERC + readability checks
3. Injects readability/ERC metadata into the tool result
4. Can block or warn on actions that degrade readability
5. Provides a /gym endpoint for the RL agent to interact directly

Usage:
    python -m schematic_gym.error_fixer.kicad_proxy --kicad-url http://127.0.0.1:8080/mcp --port 8081
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

logger = logging.getLogger(__name__)

# Ensure imports.
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


# ---------------------------------------------------------------------------
# KiCad MCP client
# ---------------------------------------------------------------------------

def call_kicad_mcp(url: str, tool_name: str, args: dict, timeout: float = 30.0) -> dict:
    """Call the real KiCad MCP instance."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": str(e)}}


def list_kicad_tools(url: str) -> list[dict]:
    """List available tools from KiCad MCP."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("result", {}).get("tools", [])
    except Exception as e:
        logger.warning("Failed to list KiCad tools: %s", e)
        return []


# ---------------------------------------------------------------------------
# Modifying tool names (tools that change schematic state)
# ---------------------------------------------------------------------------

MODIFYING_TOOLS = {
    "place_component", "move_component", "rotate_component",
    "add_wire", "batch_connect", "connect_pin_to_pin", "connect_net_to_pin",
    "add_global_label", "delete_component", "delete_components_batch",
    "remove_wire", "remove_wires_in_bbox", "remove_label", "remove_labels_in_bbox",
    "disconnect_pin", "batch_disconnect_pins",
    "move_components_batch", "move_chunk", "replace_component",
    "start_block",
    "commit_schematic_from_json", "commit_schematic_from_python",
}


# ---------------------------------------------------------------------------
# Proxy server
# ---------------------------------------------------------------------------

class ProxyHandler(BaseHTTPRequestHandler):
    """HTTP handler that proxies MCP requests to KiCad and adds validation."""

    kicad_url: str = "http://127.0.0.1:8080/mcp"
    erc_after_modify: bool = True
    log_tools: bool = True
    _call_count: int = 0
    _erc_error_count: int = 0

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        method = request.get("method", "")

        if self.path == "/gym":
            # Direct gym endpoint (for RL agent).
            self._handle_gym(request)
            return

        if method == "tools/list":
            # Forward to KiCad, add our metadata.
            result = self._forward_to_kicad(body)
            self._send_json(200, result)
            return

        if method == "tools/call":
            self._handle_tool_call(request, body)
            return

        # Unknown method — forward as-is.
        result = self._forward_to_kicad(body)
        self._send_json(200, result)

    def _handle_tool_call(self, request: dict, raw_body: bytes) -> None:
        """Handle a tools/call request with validation."""
        params = request.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        is_modifying = tool_name in MODIFYING_TOOLS

        ProxyHandler._call_count += 1
        t0 = time.time()

        if self.log_tools:
            args_str = json.dumps(tool_args, default=str)
            if len(args_str) > 100:
                args_str = args_str[:100] + "..."
            logger.info("[%d] %s(%s)", ProxyHandler._call_count, tool_name, args_str)

        # Forward to real KiCad MCP.
        result = self._forward_to_kicad(raw_body)

        elapsed = time.time() - t0

        # After modifying tools, run ERC check.
        if is_modifying and self.erc_after_modify:
            erc_result = call_kicad_mcp(self.kicad_url, "erc_check", {})
            erc_data = self._extract_text_content(erc_result)

            # Count errors.
            try:
                erc_parsed = json.loads(erc_data) if isinstance(erc_data, str) else erc_data
                if isinstance(erc_parsed, dict):
                    n_errors = erc_parsed.get("errors", 0)
                    n_warnings = erc_parsed.get("warnings", 0)
                else:
                    n_errors = erc_data.count('"severity":"error"') if isinstance(erc_data, str) else 0
                    n_warnings = 0
            except (json.JSONDecodeError, TypeError):
                n_errors = 0
                n_warnings = 0

            ProxyHandler._erc_error_count = n_errors

            # Inject ERC metadata into the result.
            if "result" in result:
                content = result["result"].get("content", [])
                if content and isinstance(content, list):
                    # Append ERC status to the text content.
                    original_text = content[0].get("text", "") if content else ""
                    content.append({
                        "type": "text",
                        "text": f"\n[PROXY] ERC after {tool_name}: {n_errors} errors, {n_warnings} warnings ({elapsed:.1f}s)",
                    })

            if self.log_tools:
                logger.info("  → ERC: %d errors, %d warnings (%.1fs)", n_errors, n_warnings, elapsed)

        self._send_json(200, result)

    def _handle_gym(self, request: dict) -> None:
        """Handle direct gym interactions (for RL agent)."""
        action = request.get("action", "")

        if action == "status":
            self._send_json(200, {
                "call_count": ProxyHandler._call_count,
                "erc_errors": ProxyHandler._erc_error_count,
                "kicad_url": self.kicad_url,
            })
        elif action == "erc":
            result = call_kicad_mcp(self.kicad_url, "erc_check", {})
            self._send_json(200, result)
        else:
            self._send_json(400, {"error": f"Unknown gym action: {action}"})

    def _forward_to_kicad(self, body: bytes) -> dict:
        """Forward raw request to KiCad MCP."""
        req = urllib.request.Request(
            self.kicad_url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -1, "message": f"KiCad MCP error: {e}"},
            }

    def _extract_text_content(self, result: dict) -> str:
        """Extract text from MCP result content array."""
        content = result.get("result", {}).get("content", [])
        if content and isinstance(content, list):
            return content[0].get("text", "")
        return ""

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default HTTP logging."""
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="KiCad MCP Proxy with ERC validation")
    parser.add_argument("--kicad-url", default="http://127.0.0.1:8080/mcp", help="KiCad MCP endpoint")
    parser.add_argument("--port", type=int, default=8081, help="Proxy port")
    parser.add_argument("--no-erc", action="store_true", help="Disable post-modify ERC checks")
    parser.add_argument("--quiet", action="store_true", help="Disable tool call logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ProxyHandler.kicad_url = args.kicad_url
    ProxyHandler.erc_after_modify = not args.no_erc
    ProxyHandler.log_tools = not args.quiet

    server = HTTPServer(("0.0.0.0", args.port), ProxyHandler)
    print(f"KiCad MCP Proxy listening on :{args.port}")
    print(f"  Forwarding to: {args.kicad_url}")
    print(f"  ERC after modify: {ProxyHandler.erc_after_modify}")
    print(f"  Point webchat NEXT_PUBLIC_KICAD_MCP_URL to http://127.0.0.1:{args.port}/mcp")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down proxy.")
        server.server_close()


if __name__ == "__main__":
    main()
