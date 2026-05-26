# `copper-net` Docker network

For CopperV2Arch runner containers and ws-relay on the same host:

```bash
docker network create copper-net
```

Then attach services (see `CopperV2Arch/copper-agents/compose.runner.yaml` and your ws-relay compose) with:

```yaml
networks:
  copper-net:
    external: true
```

Startup order: **Temporal → orchestrator-worker + subagents-worker → ws-relay**.

Set on ws-relay: `COPPER_TEMPORAL_ADDRESS=temporal:7233`, `COPPER_STREAM_SINK_PUBLIC_URL=http://agent:3000` (or the service name that exposes ws-relay), and the same `COPPER_STREAM_SINK_TOKEN` as workers.

## Redeploy target

Always redeploy the live Copper stack against:

```bash
export DOCKER_HOST=tcp://192.168.177.144:2375
```

## Which service to rebuild

- Copper runtime / streaming UX changes live in `agents/top_dog/src/ws-relay.ts` and `agents/top_dog/src/copper-client.ts`.
- Those changes only take effect after rebuilding `copper-ws-relay`.
- Rebuilding the website alone will not update Copper event translation, thinking messages, or final-answer synthesis.

Relay-only rebuild:

```bash
cd /Users/ethanmarreel/Downloads/kicad-9.0.7/mcp
DOCKER_HOST=tcp://192.168.177.144:2375 docker compose -f docker-compose.copper-edge.yaml up -d --build copper-ws-relay
```

Full edge rebuild:

```bash
cd /Users/ethanmarreel/Downloads/kicad-9.0.7/mcp
DOCKER_HOST=tcp://192.168.177.144:2375 docker compose -f docker-compose.copper-edge.yaml up -d --build
```
