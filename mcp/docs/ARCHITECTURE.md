# Copper AI — WebSocket Agent Stack Architecture

## Overview

The agent runs in Docker containers behind a load balancer. KiCad (with its MCP server) runs on the **client machine**. Communication happens over WebSocket — tool calls are **proxied** through the client's web view.

## Network Topology

```
┌─────────────────────────────────────────────────────────────────┐
│  Client Machine                                                 │
│                                                                 │
│  ┌──────────┐    HTTP POST     ┌──────────────────────┐        │
│  │ KiCad    │◄────────────────►│ KiCad MCP Server     │        │
│  │ App      │                  │ 127.0.0.1:8080/mcp   │        │
│  └──────────┘                  └──────────────────────┘        │
│       │                               ▲                         │
│       │ embeds                        │ HTTP (local)            │
│       ▼                               │                         │
│  ┌──────────────────┐                 │                         │
│  │ KiCad WebView    │─────────────────┘                         │
│  │ (WS Client)      │                                           │
│  └────────┬─────────┘                                           │
│           │ ws://                                                │
└───────────┼─────────────────────────────────────────────────────┘
            │
            │  WebSocket (TCP)
            ▼
┌───────────────────────────────────────────────────────────────────┐
│  Docker Stack                                                     │
│                                                                   │
│  ┌────────────────────────┐                                       │
│  │ Nginx Load Balancer    │  :9090 (host-mapped)                  │
│  │ ip_hash sticky sessions│                                       │
│  └────┬───────┬───────┬───┘                                       │
│       │       │       │      upstream (round-robin w/ sticky)     │
│       ▼       ▼       ▼                                           │
│  ┌────────┐┌────────┐┌────────┐                                   │
│  │agent-1 ││agent-2 ││agent-N │  :3000 (internal)                 │
│  │ws-relay││ws-relay││ws-relay│                                   │
│  └───┬────┘└───┬────┘└───┬────┘                                   │
│      │         │         │       Docker DNS                       │
│      └─────────┴─────────┘                                        │
│               │                                                   │
│               ▼                                                   │
│  ┌─────────────────────┐                                          │
│  │ json-mcp            │  :8082 (internal, shared sessions vol)  │
│  │ schematic.json sync  │                                          │
│  └─────────────────────┘                                          │
│                                                                   │
│  Network: agent-net (bridge)                                      │
└───────────────────────────────────────────────────────────────────┘
```

## Services

| Service | Image | Port | Replicas | Purpose |
|---------|-------|------|----------|---------|
| `loadbalancer` | `nginx:alpine` | `9090` (host) | 1 | WebSocket load balancer with sticky sessions |
| `agent` | `Dockerfile.ws` | `3000` (internal) | 3 (configurable) | Runs Codex SDK, streams events over WS |
| `json-mcp` | `agents/json_mcp/Dockerfile` | `8082` (internal) | 1 | Schematic JSON sync (export_schematic_to_json, etc.); shares `agent-sessions` volume |

## Tool Call Routing

There are two classes of MCP servers:

### 1. Client-Proxied MCP (e.g. `kicad`)

The KiCad MCP server runs on the client machine. The agent **cannot** reach it directly. Tool calls are serialized as JSON, sent over the WebSocket to the client WebView, which executes them locally and returns the result.

```
Agent (Docker)  ──ws──►  WebView (Client)  ──http──►  KiCad MCP (127.0.0.1:8080)
       ◄──ws──                    ◄──http──
```

### 2. Networked MCP (e.g. `json-mcp`)

Runs inside the Docker stack. The relay calls it for schematic.json sync (export_schematic_to_json after KiCad edits). Shares the `agent-sessions` volume with the agent so session workspaces are visible to both.

```
Agent (Docker)  ──http──►  json-mcp:8082 (Docker, same volume)
       │
       └──ws──►  WebView (tool results / sync)
```

## Scaling

```bash
# Default: 3 agent replicas
docker compose up

# Custom scale
docker compose up --scale agent=5

# Or modify deploy.replicas in docker-compose.yml
```

The Nginx load balancer uses `ip_hash` so a single client IP always routes to the same agent instance for the duration of a session.

## Connecting the chat view

The Copper AI chat (website) talks to the Docker stack over WebSocket. Point the website at the **load balancer** on the host:

- **Local Docker**: In the website `.env` set `NEXT_PUBLIC_RUNNER_URL=ws://localhost:9090` (or `http://localhost:9090`; the client normalizes to `ws://`).
- **Remote host**: Use your hostname or IP, e.g. `NEXT_PUBLIC_RUNNER_URL=wss://your-host.example.com` if TLS is in front of the load balancer.

After `docker compose up`, the chat connects to port 9090; the load balancer forwards to an agent replica. Agent output is filtered so the chat shows clean responses (no CLI noise). To confirm the relay is up: open `http://localhost:9090/` in a browser (plain-text message) or `http://localhost:9090/health` (JSON).
