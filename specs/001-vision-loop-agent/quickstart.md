# Quickstart: Vision Loop Sub-Agent

**Branch**: `001-vision-loop-agent`

## Prerequisites

1. **KiCad** running with IPC API enabled and a schematic open
2. **KiCad MCP server** running at `http://127.0.0.1:8080/mcp`
3. **Ollama** running with models pulled:
   ```bash
   ollama pull llava:13b
   ollama pull llama3.1:8b
   ```
4. **Python** >= 3.10

## Install

```bash
cd mcp/agents/executor
pip install -e .
```

## Run the agent

```bash
# Start the server (LangServe playground + A2A endpoint)
python server.py

# Server runs at http://localhost:8000
# Playground: http://localhost:8000/agent/playground/
# A2A Agent Card: http://localhost:8000/.well-known/agent.json
```

## Test with a simple circuit

```bash
curl -X POST http://localhost:8000/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "circuit_definition": {
        "components": [
          {"library": "Device", "symbol": "R", "reference": "R1", "value": "10k"},
          {"library": "Device", "symbol": "LED", "reference": "D1"}
        ],
        "nets": [
          {"name": "SIG", "connections": [{"reference": "R1", "pin": "2"}, {"reference": "D1", "pin": "1"}]},
          {"name": "GND", "connections": [{"reference": "D1", "pin": "2"}]}
        ]
      }
    },
    "config": {"configurable": {"thread_id": "test-001"}}
  }'
```

## Test with A2A protocol

```bash
# Discover agent
curl http://localhost:8000/.well-known/agent.json

# Submit task
curl -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "{\"components\":[...],\"nets\":[...]}"}],
        "messageId": "msg-001"
      }
    }
  }'
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_VISION_MODEL` | `llava:13b` | Vision model name |
| `OLLAMA_THINKING_MODEL` | `llama3.1:8b` | Thinking/tool-calling model |
| `MCP_ENDPOINT` | `http://127.0.0.1:8080/mcp` | KiCad MCP server URL |
| `MAX_ITERATIONS` | `10` | Maximum place-wire-verify cycles |
| `AGENT_PORT` | `8000` | HTTP server port |
| `LANGSMITH_API_KEY` | — | Optional: enables LangSmith tracing |
| `LANGSMITH_PROJECT` | `vision-loop-agent` | LangSmith project name |
