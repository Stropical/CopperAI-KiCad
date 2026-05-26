# Schematic Agent Workflow Contract

This document defines the required execution order and JSON outputs for schematic-agent runs.

## Required Stage Order
Every run MUST execute these stages in order, with no skipping and no reordering:
1. `preflight`
2. `plan`
3. `implementation`
4. `validation`
5. `report`

If any stage fails, stop the run and emit a `report` with failure details.

## Global Enforcement Rules
- Each stage MUST produce a valid JSON object.
- A stage cannot start until the previous stage output is valid.
- `implementation` MUST only execute items declared in `plan`.
- `validation` MUST verify all planned outcomes.
- `report` MUST summarize outcomes, deviations, and residual risks.

## 1) Preflight (Required JSON Schema)
Preflight captures scope, constraints, and anti-patterns before changes.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Preflight",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "stage",
    "task_summary",
    "constraints",
    "rules_used",
    "bad_patterns_to_avoid",
    "success_criteria"
  ],
  "properties": {
    "stage": { "const": "preflight" },
    "task_summary": { "type": "string", "minLength": 1 },
    "constraints": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 },
      "minItems": 1
    },
    "rules_used": {
      "type": "array",
      "description": "Concrete rules the run will enforce.",
      "items": { "type": "string", "minLength": 1 },
      "minItems": 1
    },
    "bad_patterns_to_avoid": {
      "type": "array",
      "description": "Known failure patterns the run must avoid.",
      "items": { "type": "string", "minLength": 1 },
      "minItems": 1
    },
    "success_criteria": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 },
      "minItems": 1
    },
    "assumptions": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 }
    }
  }
}
```

## 2) Plan
Plan MUST be derived from preflight and include:
- Ordered action steps.
- Target files/tools per step.
- Validation intent per step.

Minimum required shape:
```json
{
  "stage": "plan",
  "steps": [
    {
      "id": "step-1",
      "action": "...",
      "targets": ["..."],
      "expected_result": "..."
    }
  ]
}
```

## 3) Implementation
Implementation MUST:
- Execute only planned steps.
- Record actual edits/actions and any deviations.

Minimum required shape:
```json
{
  "stage": "implementation",
  "completed_steps": ["step-1"],
  "deviations": ["..."]
}
```

## 4) Validation
Validation MUST:
- Check each success criterion from preflight.
- Confirm plan steps were completed as intended.
- Mark each check `pass` or `fail`.

Minimum required shape:
```json
{
  "stage": "validation",
  "checks": [
    {
      "name": "...",
      "status": "pass",
      "evidence": "..."
    }
  ]
}
```

## 5) Report (Required Postflight JSON Schema)
Postflight report is the final authoritative run artifact.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PostflightReport",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "stage",
    "status",
    "summary",
    "files_changed",
    "checks_passed",
    "checks_failed",
    "deviations",
    "next_actions"
  ],
  "properties": {
    "stage": { "const": "report" },
    "status": { "type": "string", "enum": ["success", "partial", "failed"] },
    "summary": { "type": "string", "minLength": 1 },
    "files_changed": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 }
    },
    "checks_passed": { "type": "integer", "minimum": 0 },
    "checks_failed": { "type": "integer", "minimum": 0 },
    "deviations": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 }
    },
    "risks_remaining": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 }
    },
    "next_actions": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 }
    }
  }
}
```

## Compliance Checklist
A run is compliant only if all are true:
- Preflight JSON validates and includes non-empty `rules_used` and `bad_patterns_to_avoid`.
- Plan exists and is ordered.
- Implementation maps to planned step IDs.
- Validation includes explicit pass/fail checks with evidence.
- Postflight report JSON validates.
