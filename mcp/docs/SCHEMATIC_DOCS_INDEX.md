# Schematic Docs Index

Use this folder as the canonical guidance bundle for schematic agents.

## Recommended Read Order

1. `docs/SCHEMATIC_SOURCE_CATALOG.md`
2. `docs/SCHEMATIC_STYLE_GUIDE.md`
3. `docs/SCHEMATIC_GOOD_BAD_EXAMPLES.md`
4. `docs/SCHEMATIC_AGENT_WORKFLOW.md`
5. `docs/SCHEMATIC_EXAMPLES_TEMPLATE.md`

## Runtime Prompt Injection (Recommended)

At run start, inject:

1. A short system instruction:
   - "You must follow Schematic Style Guide rules and workflow contract."
2. A compact subset of rules:
   - 8-15 most relevant MUST/NEVER lines for the current task.
3. 1-2 contrastive examples from `SCHEMATIC_GOOD_BAD_EXAMPLES.md`.

Do not rely on the model discovering files itself; inject the relevant excerpts into the prompt.

## Enforcement

- Require preflight JSON from `SCHEMATIC_AGENT_WORKFLOW.md`.
- Block modifying tool calls until preflight is valid.
- Require postflight report JSON before task completion.

