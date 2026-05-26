# What Makes Good Agents

Detailed research report based on `https://github.com/codeaashu/claude-code` (local clone reviewed at `claude-code/`).

---

## Scope and Method

This report is based on a second-pass review of:

- Top-level docs: `README.md`, `docs/architecture.md`, `docs/subsystems.md`, `docs/tools.md`, `docs/commands.md`, `docs/exploration-guide.md`
- Core implementation files:
  - `src/Tool.ts`
  - `src/tools.ts`
  - `src/QueryEngine.ts`
  - `src/utils/systemPrompt.ts`
  - `src/hooks/toolPermission/PermissionContext.ts`
  - `src/utils/shell/readOnlyCommandValidation.ts`
  - `src/tools/AgentTool/loadAgentsDir.ts`
  - `src/utils/tasks.ts`
  - `src/utils/workloadContext.ts`
  - `src/utils/teammateContext.ts`
  - `src/utils/swarm/teammatePromptAddendum.ts`
  - `src/utils/toolSchemaCache.ts`

The goal is not to reproduce their code, but to extract durable engineering patterns for building high-quality agents.

---

## Executive Summary

The strongest patterns in this repository are:

1. **A strict tool contract layer** with schema validation, permission checks, side-effect classification, and concurrency declarations.
2. **Centralized permission orchestration** that can combine static rules, runtime hooks, classifier decisions, and user approvals.
3. **A resilient turn loop** that tracks retries, budgets, denials, structured-output failures, and returns machine-readable outcomes.
4. **Prompt composition as a system** (default + mode + agent + append), not a monolithic string.
5. **Context isolation for concurrency** via AsyncLocalStorage to prevent cross-session/agent state leaks.
6. **Task-backed multi-agent orchestration** with locking and identity model for parallel work.
7. **Operational hardening**: schema caching, lazy loading, feature flags, strict defaults, and diagnostics.

If you adopt only a subset, prioritize:

- centralized tool governance,
- turn-level budget/retry/error accounting,
- explicit role tool-allowlists,
- context isolation under concurrency,
- and task-based subagent coordination.

---

## 1) Tooling Architecture: Why Their Tool Model Is Strong

### 1.1 Tools are first-class objects, not ad-hoc functions

From `src/Tool.ts`, each tool has a rich contract:

- input schema (`inputSchema`)
- capability description
- permission check entrypoint (`checkPermissions`)
- concurrency/read-only/destructive metadata (`isConcurrencySafe`, `isReadOnly`, `isDestructive`)
- optional input validation (`validateInput`)
- rendering hooks for UI and transcript behavior
- classifier serialization (`toAutoClassifierInput`)
- interrupt behavior semantics

This design makes tools auditable and composable.

### 1.2 Fail-safe defaults in `buildTool`

`buildTool` defines critical defaults:

- `isConcurrencySafe` defaults to `false`
- `isReadOnly` defaults to `false`
- `isDestructive` defaults to `false`
- `checkPermissions` defaults to allow (then global permission logic can still apply)

The important part is the posture: unknown capabilities are treated conservatively for concurrency and mutability. That reduces accidental unsafe parallelism.

### 1.3 Tool registration as policy surface

In `src/tools.ts`, tooling is assembled through:

- feature flags
- environment gates
- tool presets
- dynamic includes/excludes (including deferred tool discovery)

This is not only dependency wiring; it is operational policy. The toolset can be adapted by mode, environment, and maturity level without rewriting core execution.

---

## 2) Permission System: The Biggest Real-World Differentiator

### 2.1 Every tool call runs through a centralized gate

`src/hooks/toolPermission/PermissionContext.ts` orchestrates:

- rule matching,
- hook-based checks,
- optional classifier approval path,
- user prompt fallback,
- deny/abort behavior,
- and persistence of permission updates.

This is the right architecture for production agents: permissions are a core subsystem, not bolt-on prompts.

### 2.2 Multiple approval sources are unified

The code tracks decision provenance (hook/user/classifier). That supports:

- trustworthy audit logs,
- metrics by approval source,
- and safer policy evolution.

### 2.3 Read-only shell mode is deeply validated

`src/utils/shell/readOnlyCommandValidation.ts` goes beyond command-name allowlisting:

- command/subcommand-level maps,
- explicit allowed flags with argument typing,
- special callbacks for command-specific danger checks,
- parser edge-case hardening.

This is a major production lesson: shell safety fails at parser edges, not at obvious command strings.

---

## 3) Query Loop Engineering: What Reliability Looks Like

### 3.1 QueryEngine is a controlled state machine

`src/QueryEngine.ts` shows robust turn handling:

- wrapped tool permission tracking,
- structured output enforcement and retry counting,
- budget cutoffs,
- retry emission (`api_retry` events),
- comprehensive result envelopes with costs, usage, denials, and errors.

### 3.2 Cost and budget are first-class stop conditions

The loop checks `maxBudgetUsd` and returns explicit machine-readable errors on budget breach.

This is critical for production autonomy: good agents need financial safety rails, not only logic guards.

### 3.3 Structured-output failure modes are explicitly bounded

Structured output retries are counted and capped (`MAX_STRUCTURED_OUTPUT_RETRIES`). Infinite "format retry loops" are prevented at framework level.

### 3.4 Transcript durability concerns are engineered-in

The code includes eager flush behaviors for environments where process teardown can lose buffered writes. This is a subtle but real ops concern in distributed clients.

---

## 4) Prompt Engineering as Infrastructure (Not Prompt Art)

### 4.1 Prompt composition has explicit precedence

`src/utils/systemPrompt.ts` composes prompt layers:

- override prompt
- coordinator prompt
- agent prompt
- custom prompt
- default prompt
- append prompt

By making precedence explicit in code, behavior becomes testable and predictable.

### 4.2 Agent prompt behavior varies by mode deliberately

In proactive modes, agent instructions are appended rather than replacing defaults. This preserves core safety/identity scaffolding while layering domain behavior.

### 4.3 Prompt construction is tied to runtime context

Prompt parts depend on loaded tools, available MCP clients, and dynamic context. This avoids stale or impossible instructions.

---

## 5) Multi-Agent Design: Why Their Coordination Works

### 5.1 Team communication is explicit, not implicit

`teammatePromptAddendum.ts` enforces that teammates must use messaging tools to communicate. Plain text alone is insufficient in team contexts.

That avoids one of the biggest multi-agent failure modes: hidden assumptions about shared visibility.

### 5.2 Agent definitions are typed and configurable

`loadAgentsDir.ts` supports structured agent definitions:

- allowed/disallowed tools,
- model and effort,
- permission mode,
- required MCP servers,
- max turns,
- memory scope,
- isolation mode.

This turns "agent identity" into declarative configuration rather than ad-hoc prompts.

### 5.3 MCP requirements are agent-level availability constraints

Agents can declare required MCP servers; unavailable dependencies filter agent availability. This prevents launching "broken-by-design" agents.

### 5.4 Task model handles parallelism robustly

`src/utils/tasks.ts` adds:

- lock-based mutation safety,
- high-water mark task IDs,
- status lifecycle,
- team-aware task-list resolution,
- update signaling.

For real parallel agents, this data layer is essential.

---

## 6) Concurrency Isolation: The Most Underappreciated Requirement

### 6.1 AsyncLocalStorage prevents cross-turn leakage

Both `workloadContext.ts` and `teammateContext.ts` use AsyncLocalStorage to isolate context through async boundaries.

The code comments call out specific leakage patterns that happen with globals under detached async execution.

### 6.2 Context boundaries are always established

A notable detail: they intentionally call `.run()` even when workload is undefined, to reset inherited ambient context and prevent sticky leaks.

This is exactly the kind of correctness hardening that separates demo agents from production agents.

---

## 7) Performance and Stability Patterns

### 7.1 Session-level tool schema cache

`toolSchemaCache.ts` caches rendered tool schemas to avoid re-render churn and token cache busting during session drift (flags, MCP reconnect, dynamic prompts).

This is a strong pattern for lowering cost and preserving stable model behavior.

### 7.2 Lazy imports and feature-flag dead code elimination

Heavy or optional modules are gated by `bun:bundle` flags and lazy imports. Benefits:

- startup speed,
- lower memory,
- reduced attack surface in minimal builds.

### 7.3 Massive codebase, narrow execution

Even with many capabilities, runtime paths are constrained by environment flags and mode-specific registries. This keeps behavior deterministic in each deployment context.

---

## 8) Design Principles Derived from the Repository

### Principle A: "Tools are policy objects"

A tool should encode:

- what it does,
- what input is valid,
- whether it can run in parallel,
- whether it mutates state,
- and how approval is decided.

### Principle B: "Permission is runtime governance, not UI friction"

Permissions are an enforcement engine spanning policy rules, classifiers, hooks, and user intents.

### Principle C: "Loops need hard stop semantics"

Good agents stop deterministically when budgets, retries, turn limits, or structured-output thresholds are hit.

### Principle D: "Role and capability must be linked"

Planner/verifier/executor should not merely be prompt personas. They must map to actual allowed tool surfaces.

### Principle E: "Concurrency requires isolation primitives"

Without ALS-style scoping, background and parallel work will leak context and produce nondeterministic behavior.

### Principle F: "Observability should return structured outcomes"

Every turn should emit machine-readable envelopes including:

- cost,
- model usage,
- tool denials,
- stop reason,
- and classified error subtype.

---

## 9) Anti-Patterns This Repository Avoids

1. **Single giant prompt with no layering**
2. **Tool permission checks embedded inside random tool functions only**
3. **Global mutable context across parallel agents**
4. **No retry caps or budget caps**
5. **Shell safety based only on keyword matching**
6. **Agent personas without enforceable tool boundaries**
7. **No provenance for why a permission was granted**
8. **No structured output retry limits**
9. **No durability model for session transcripts**
10. **No feature flag boundaries for experimental systems**

---

## 10) Practical Blueprint for Building Good Agents

### 10.1 Minimal maturity model

#### Level 1: Baseline-safe

- Tool schemas validated
- Central permission dispatcher
- Read-only vs mutating distinction
- Retry with max attempts and timeout

#### Level 2: Production-safe

- Role-based tool allowlists
- Budget and turn ceilings
- Structured output retry cap
- Denial and error telemetry per turn
- Shell command parser/flag allowlists

#### Level 3: Scale-safe

- ALS context isolation for async/parallel agents
- Task graph + locking for multi-agent coordination
- Session-stable tool-schema caching
- Feature-flagged experimental capabilities

### 10.2 Recommended agent turn envelope

Capture these fields every turn:

- `session_id`, `turn_id`
- `role` (planner/executor/verifier/etc.)
- `tools_exposed`
- `tools_called` (name, duration, status)
- `permission_denials` (tool, reason/source)
- `retry_events` (attempt, delay, category)
- `usage` (prompt/completion tokens)
- `cost_usd`
- `stop_reason`
- `result_subtype` (success/error_max_budget/error_schema_retries/etc.)

### 10.3 Recommended policy layers

Order of evaluation:

1. Input schema validation
2. Tool-specific validation
3. Static permission rules
4. Hook/policy engine checks
5. Classifier (if enabled)
6. User approval fallback
7. Tool execution

This mirrors robust behavior in the analyzed codebase.

---

## 11) What This Means for Copper Agents

Even without changing architecture dramatically, the biggest wins are:

1. **Harden tool metadata**
   - ensure every tool declares mutability and concurrency safety
2. **Unify role-bound capability surfaces**
   - planner/verifier must be structurally read-only
3. **Promote permission pipeline**
   - explicit decision source and denial reporting
4. **Add loop stop reason taxonomy**
   - budget, retries, schema failures, and execution errors as distinct outcomes
5. **Add context isolation for parallel/long-running work**
   - ALS or equivalent request context propagation
6. **Adopt task-backed multi-agent orchestration**
   - lock-safe persistence over in-memory-only task state

---

## 12) Reliability Checklist (Can Be Used as Acceptance Criteria)

Use this as a go/no-go list for agent quality:

- [ ] Every tool has schema + mutability + concurrency declarations.
- [ ] Every tool invocation passes through one permission gateway.
- [ ] Permission outcomes include source (`rule`, `hook`, `classifier`, `user`).
- [ ] Planner/verifier cannot invoke mutating tools.
- [ ] Query loop has max-turn, max-budget, max-retry, max-schema-retry stops.
- [ ] Turn result is emitted as typed envelope with usage/cost/errors.
- [ ] Shell tool has argument-level safety checks, not name-only checks.
- [ ] Parallel agents have isolated runtime context (ALS-like).
- [ ] Task coordination is lock-safe and team-aware.
- [ ] Prompt is composable with deterministic precedence.
- [ ] Experimental systems are feature-flagged.
- [ ] Transcript/session persistence accounts for abrupt process termination.

---

## 13) Making Fast Agents (Latency and Cost)

“Fast” here means **fewer round-trips**, **smaller prompts**, **cheaper models where possible**, and **parallel work** where safe—not skipping safety checks.

### 13.1 Reduce tokens per turn

- **Narrow the tool surface** for each role. If the model only needs read tools for a step, do not expose write/MCP-heavy tools; smaller tool schemas mean fewer input tokens and less confusion.
- **Stabilize tool schema bytes** across a session (the Claude Code pattern: cache rendered schemas so mid-session churn does not re-bloat the system block every turn).
- **Avoid repeating large context** (e.g. full netlists, long file dumps) in every message; summarize once and refer by ID or path.
- **Compact or summarize** conversation history when approaching context limits (`/compact`-style behavior in their CLI); for your stack, equivalent is periodic summarization + dropping redundant tool outputs.

### 13.2 Use cheaper or faster models for the right subtasks

- **Routing**: classification, “is this a part-number query?”, or simple JSON shaping can run on smaller/faster models; synthesis and risky edits stay on stronger models.
- **Cap turns and budget** so a “fast” path cannot spin into dozens of API calls (same `maxTurns` / `maxBudgetUsd` ideas as in `QueryEngine`).

### 13.3 Parallelize only where contracts allow

- Tools marked **not concurrency-safe** must run sequentially; read-only, independent lookups (e.g. two unrelated part searches) can run in parallel if your orchestration supports it.
- **Do not** parallelize mutating schematic operations without explicit ordering rules (KiCad MCP and live editors are usually single-writer).

### 13.4 Cut latency at the integration layer

- **HTTP MCP vs stdio**: stdio avoids an extra network hop for local tools; remote tools add RTT—batch calls or use a single execution phase (e.g. “advisory then executor” handoff) instead of ping-ponging servers every tool call.
- **Warm dependencies**: build LayoutEngine / symbol servers once; avoid cold `npm install` on every agent start in production images.
- **Prefetch**: parallelize independent IO (policy read, auth, feature flags) on startup where possible (their `main.tsx` pattern).

### 13.5 Fast *and* safe

Speed without guardrails produces retries and incidents, which are slower overall. The winning pattern is **bounded loops + small toolsets + structured handoffs** so each step does one thing and finishes.

---

## 14) Sub-Agents With Specific Tasks (Copper / KiCad Shapes)

Specialized sub-agents work best when each has a **clear mission**, **allowed tools**, **inputs/outputs**, and **how it hands off** to the next role. Below is a concrete split aligned with **finding parts**, **designing blocks**, and **verifying a circuit**—matching how you already think about planner / executor / verifier, but with sharper boundaries.

### 14.1 Why specialize at all?

- **Part search** needs vendor APIs, alternates, and package sanity; it should not thrash the live schematic.
- **Block design** (subcircuits, hierarchical sheets, structured “blocks” in your LayoutEngine sense) needs IR/placement semantics and iteration; it is a different failure mode than “did we find a capacitor?”
- **Verification** should be **read-mostly**: ERC, net sanity, constraints, and cross-checks against intent—without mutating the design unless you explicitly allow “fix-up” tools.

Treating these as separate **agents or modes** (even if implemented as one process with role flags) keeps prompts short and reduces tool misuse.

### 14.2 Sub-agent A — Part finder (alternates, BOM, footprint sanity)

**Mission**: Given requirements (value, package, voltage, stock), return **part choices**, **footprint/library pointers**, and **alternates**—not arbitrary schematic edits.

**Typical tools**: Part-finder MCP / Digi-Key (or your `part-finder` service), read-only schematic snapshot if needed for context, **no** `place_component` unless you deliberately merge this role with layout.

**Outputs (handoff object)**:

- `primary_part`, `alternates[]`, `footprint_notes`, `risk_flags` (EOL, wrong voltage, etc.)

**Speed tips**: cache vendor queries; avoid re-fetching the same MPN; keep the model focused on a table-shaped answer, not prose.

### 14.3 Sub-agent B — Block / module designer (structure and intent)

**Mission**: Propose or refine **functional blocks**—power, regulation, signal conditioning, connectors—at the level of **netlist intent**, **hierarchy**, and **interface pins**, optionally coordinated with LayoutEngine IR (motifs, blocks, relayout targets).

**Typical tools**: KiCad MCP for structural edits *or* LayoutEngine for IR-first layout/export, plus your JSON/schematic pipeline as needed. **Not** the full part-vendor surface unless the block agent must pick specific ICs.

**Outputs**:

- `block_diagram` (logical),
- `interface` (ports, rails),
- `implementation_notes` (what still needs part-finder),
- optional **LayoutEngine patch** or relayout target for formatting passes.

**Handoff from A → B**: Part finder supplies approved parts; block designer wires them at a **reference/designator** level; unresolved parts become explicit TODOs back to A.

### 14.4 Sub-agent C — Circuit verifier (ERC, topology, requirements)

**Mission**: Answer “does this match the spec and pass checks?”—ERC, floating nets, rail presence, key connections, and **semantic** checks (e.g. feedback polarity, decoupling presence) as your tools allow.

**Typical tools**: `net_diagnostics`, `erc_check`, `get_schematic_state` / connectivity graph, read-only summarization. Optionally a **second pass** with a stronger model only when the first pass finds anomalies.

**Outputs**:

- `pass | fail | warn`,
- `findings[]` with severity,
- `suggested_next_agent` (`part_finder` | `block_designer` | `executor`)—so the orchestrator does not guess.

**Critical rule**: default verifier is **non-mutating**. If you allow auto-fix tools, put them behind a separate **“verifier-fix”** mode with explicit user or policy approval.

### 14.5 Orchestration pattern that stays fast

1. **Part finder** runs to completion (or timeout) with a compact result object.
2. **Block designer** consumes that object; does not re-query vendors unless a gap is listed.
3. **Verifier** runs on a stable snapshot; does not interleave with heavy vendor or layout loops.

This is the same **two-phase** discipline as “Copper advises → LayoutEngine executes”: **finish one role’s work**, then pass structured state forward.

### 14.6 Mapping to your stack (mental model)

| Role | Primary concern | Primary services |
|------|-----------------|------------------|
| Part finder | BOM, stock, alternates | Part-finder MCP, datasheets |
| Block designer | Structure, hierarchy, layout intent | KiCad MCP, LayoutEngine IR |
| Verifier | Correctness, ERC, requirements | KiCad diagnostics, read-only graph |

Shared **memory** (project `CLAUDE.md`-style conventions: rail names, design rules) keeps sub-agents aligned without pasting the whole repo into every prompt.

---

## Closing

The key insight from this repository is that "good agents" are less about one perfect system prompt and more about **systems engineering discipline**:

- contracts,
- policy pipelines,
- bounded loops,
- context isolation,
- and operational telemetry.

Those choices make autonomous behavior predictable, inspectable, and safe at scale.

