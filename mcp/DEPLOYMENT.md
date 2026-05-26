# Deployment

## Live targets

- Docker host: `tcp://192.168.177.144:2375`
- Public website: `https://my-v0-project.copperai.workers.dev/chat`
- Public agent tunnel base: `https://api.vcryptfinancial.com/agent`
- Public agent websocket: `wss://api.vcryptfinancial.com/agent/ws`

## Rule

- The `website` Docker container is for local/dev only.
- The live website is deployed from `mcp/website` with Cloudflare Workers.
- Copper runtime / relay changes are deployed to Docker.

## Website deploy

From:

`/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp/website`

Use:

```bash
npm run deploy:cloudflare
```

Required website env:

```env
NEXT_PUBLIC_BACKEND_BASE_URL=https://api.vcryptfinancial.com/agent
NEXT_PUBLIC_RUNNER_URL=wss://api.vcryptfinancial.com/agent/ws
```

Notes:

- `NEXT_PUBLIC_BACKEND_BASE_URL` is the preferred setting. The chat app derives:
  - websocket: `/ws`
  - MCP: `/mcp`
  - session manager: `/session_manager`
- `NEXT_PUBLIC_RUNNER_URL` is kept explicit so the deployed app is pinned to the tunnel websocket.

## Docker redeploy

From:

`/Users/ethanmarreel/Downloads/kicad-9.0.7/mcp`

Use:

```bash
DOCKER_HOST=tcp://192.168.177.144:2375 docker compose -f docker-compose.copper-edge.yaml up -d --build copper-ws-relay
```

Do not deploy the live website with Docker.

## Remove accidental live website container

If the website was accidentally started on Docker:

```bash
DOCKER_HOST=tcp://192.168.177.144:2375 docker rm -f copperedge-website-1
```

## Runtime split

- `agents/top_dog/src/ws-relay.ts` and `agents/top_dog/src/copper-client.ts` control live chat streaming, thinking updates, and final answers.
- `website/` controls the Cloudflare-hosted frontend.
- Rebuilding the website does not update relay behavior.
- Rebuilding the relay does not deploy frontend changes.
