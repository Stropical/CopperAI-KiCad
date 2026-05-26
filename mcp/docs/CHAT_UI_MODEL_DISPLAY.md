# Showing the running model in the chat UI

The relay sends a **`run_started`** message when a run begins (right after `status: "running_agent"`). Use it to show which model and mode are running.

## Message shape

```json
{
  "type": "run_started",
  "model": "gemini-3-flash",
  "command": "start:pipeline",
  "runLabel": "Plan + Execute"
}
```

- **`model`** — Model ID (e.g. `gemini-3-flash`). Show as "Model: …" in the UI.
- **`command`** — Command that started the run (`start:pipeline`, `start:exec`, `start:verify`, etc.).
- **`runLabel`** — Short label: `"Plan + Execute"`, `"Execute + Verify"`, or the agent name (e.g. `"Verifier Agent"`).

## Example: React/Next.js

In your WebSocket message handler:

```ts
if (msg.type === "run_started") {
  setRunInfo({
    model: msg.model,
    runLabel: msg.runLabel,
  });
}
```

In the chat header or above the message list:

```tsx
{runInfo && (
  <div className="text-xs text-muted-foreground">
    {runInfo.runLabel} · Model: {runInfo.model}
  </div>
)}
```

Clear `runInfo` when you receive `done` or `agent_complete` (or when starting a new prompt), so the next run can show its own model.

## Protocol reference

See [WS_PROTOCOL.md](./WS_PROTOCOL.md) for the full `run_started` spec and other server messages.
