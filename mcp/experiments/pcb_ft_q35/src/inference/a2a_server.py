"""Minimal A2A HTTP server: agent card + task endpoint that calls fix_placement_pass.

Run with: python -m src.inference.a2a_server --checkpoint DIR [--port 8000] [--host 0.0.0.0]

Reference: https://geminicli.com/docs/core/remote-agents/
Parent configures this server's URL as agent_card_url in .gemini/agents/*.md (kind: remote).
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


def _load_model_once(checkpoint_dir: Path):
    from src.inference import fix_placement_pass, load_model_and_tokenizer
    model, tokenizer = load_model_and_tokenizer(checkpoint_dir, device=None)
    return model, tokenizer


class A2AHandler(BaseHTTPRequestHandler):
    model = None
    tokenizer = None
    checkpoint_dir = None

    def _agent_card(self) -> dict[str, Any]:
        return {
            "name": "schematic-fix-pass",
            "description": "Schematic placement and rotation fix pass. Accepts schematic state and window centers; returns list of edits (action, target_node_ids, deltas).",
            "version": "0.1.0",
            "endpoints": {
                "task": "/task",
            },
            "capabilities": ["task"],
        }

    def _do_agent_card(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self._agent_card(), indent=2).encode("utf-8"))

    def _do_task(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"Missing request body"}')
            return
        try:
            body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
        schematic_path = payload.get("schematic_path", payload.get("schematic_state"))
        window_centers = payload.get("window_centers", payload.get("centers", []))
        if isinstance(window_centers, str):
            window_centers = [c.strip() for c in window_centers.split(",") if c.strip()]
        radius_mm = float(payload.get("radius_mm", payload.get("radius", 35.0)))
        max_edits = payload.get("max_edits")
        if schematic_path is None or not window_centers:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": "schematic_path and window_centers required"}).encode("utf-8")
            )
            return
        try:
            from src.inference import fix_placement_pass
            result = fix_placement_pass(
                schematic_state=schematic_path,
                window_centers=window_centers,
                radius_mm=radius_mm,
                checkpoint_dir=self.checkpoint_dir,
                task="suggest_repair",
                max_edits=max_edits,
            )
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result, indent=2).encode("utf-8"))

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/agent-card", "/.well-known/agent.json"):
            self._do_agent_card()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/task":
            self._do_task()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[A2A] {args[0]}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A2A server for schematic fix pass.")
    parser.add_argument("--checkpoint", type=str, required=True, help="Path to model checkpoint")
    parser.add_argument("--port", type=int, default=8000, help="Port (default 8000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    args = parser.parse_args()
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_dir():
        print(f"Checkpoint dir not found: {checkpoint}", file=sys.stderr)
        return 1
    A2AHandler.checkpoint_dir = checkpoint
    server = HTTPServer((args.host, args.port), A2AHandler)
    print(f"Agent card: http://{args.host}:{args.port}/.well-known/agent.json", file=sys.stderr)
    print(f"Task endpoint: POST http://{args.host}:{args.port}/task", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
