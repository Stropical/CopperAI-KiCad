# Specification: Qwen Schematic V1

## 1. Objective

Build a local-window schematic reasoning model by replacing Qwen3.5-0.8B's image pathway with a graph encoder:

`schematic_graph -> graph_encoder -> projector(1024) -> Qwen decoder`

Primary value: structured critique and edit guidance for schematic layout quality.

## 2. Scope

### In scope (V1)

- Local window tasks only.
- Strict JSON outputs only.
- Task types:
  - candidate ranking (`best` among variants),
  - critique (`ok` + issue list),
  - one-step edit suggestion (`action`, `target`, `relation`).

### Out of scope (V1)

- Full-sheet generation from scratch.
- Open-ended prose explanations as default output.
- PCB routing optimization.

## 3. Base Model Contract

- Base checkpoint: `Qwen/Qwen3.5-0.8B`.
- Decoder interface target: `hidden_size = 1024`.
- Decoder initially frozen except optional late-stage LoRA on upper layers.

## 4. Input Representation

Each training/eval sample is a local schematic window transformed into a heterogeneous graph.

### Node types

- `symbol`
- `pin`
- `junction`
- `net_label`
- `power_symbol`
- `wire_segment`
- `block` (optional)

### Edge types

- `symbol_has_pin`
- `pin_connected_to_wire`
- `wire_connected_to_junction`
- `label_attached_to_net`
- `left_of`
- `right_of`
- `above`
- `below`
- `near`
- `inline_with`
- `same_block`

### Node features

- type embedding
- geometry buckets (`x`, `y`, `w`, `h`)
- rotation bucket
- local window ID
- graph degree bucket
- symbol class embedding
- electrical role embedding (if known)
- pin-side and pin-order embeddings
- text embedding (`refdes`, label text)

### Edge features

- relation type embedding
- distance bucket
- angle bucket
- orthogonality bit
- optional net-class embedding

## 5. Encoder + Projector

### Graph encoder

- Architecture: graph transformer.
- Depth: 6 layers (configurable 4-8).
- Hidden size: 384.
- Attention: multi-head with learned relation bias.
- Optional global virtual node.

### Token selection

- Output candidates: symbols, critical pins, labels, optional global tokens.
- Token budget:
  - small window: 64
  - normal: 128
  - hard cap: 256

### Projector

- `384 -> 1024` MLP.
- Default: `Linear -> GELU -> Linear -> LayerNorm`.

## 6. Decoder Injection Strategy

### Architecture choice (V1)

- Replace image-token path with graph-token path.

### Operational fallback

- If replacement is intrusive in first sprint, prepend graph tokens before task text while preserving decoder behavior.

## 7. Training Plan

### Stage 1: Alignment pretraining

- Trainable: graph encoder + projector.
- Frozen: full decoder.
- Tasks: graph-to-JSON factual summaries and relation checks.

### Stage 2: Instruction tuning

- Trainable: graph encoder + projector + optional LoRA on top decoder blocks.
- Tasks: ranking, critique, edit suggestion.
- Output schema enforced with JSON validation.

### Stage 3: Optional optimization

- Add heuristic reward-style reranking or preference tuning.
- Prioritize crossings, decoupler proximity, and readability.

## 8. Losses

- 70% JSON next-token generation loss.
- 20% ranking loss.
- 10% auxiliary losses (relation/role/geometric classification).

## 9. Output Schemas

### Critique

```json
{
  "ok": false,
  "issues": [
    {"target": "C3", "reason": "too_far_from_U1_VDD"}
  ]
}
```

### Ranking

```json
{"best": 2, "reason": "fewest_crossings"}
```

### Edit

```json
{
  "action": "move_symbol",
  "target": "C3",
  "anchor": "U1.23",
  "relation": "near_right"
}
```

### A2A sub-agent edit output

When this system is used as an A2A remote sub-agent for a placement/wire fix pass, it returns a **list of edits** in an executor-friendly schema aligned with the pipeline’s `RepairLabel` (see `src/data_pipeline/types.py`). Each edit object has:

| Field | Type | Description |
|-------|------|-------------|
| `action` | string | One of: `move_symbol`, `rotate_symbol`, `align_symbol`, `compact_group`. Future: `reroute_wire`, `move_net_label`. |
| `target_node_ids` | list of string | Refdes or node ids to modify (e.g. `["C3"]`). |
| `anchor_node_id` | string or null | Optional; for move_symbol/align_symbol, the reference node (e.g. `"U1"` or pin `"U1.23"`). |
| `delta_x_mm` | number | Translation in mm (apply to target). |
| `delta_y_mm` | number | Translation in mm (apply to target). |
| `delta_rotation_deg` | number | Rotation delta in degrees (apply to target). |
| `reason` | string or null | Optional human-readable reason (e.g. `"undo_distance_increase"`). |
| `issue_tags` | list of string | Optional issue taxonomy (e.g. `["decoupler_distance"]`). |

Top-level response shape:

```json
{
  "edits": [
    {
      "action": "move_symbol",
      "target_node_ids": ["C3"],
      "anchor_node_id": "U1",
      "delta_x_mm": -2.0,
      "delta_y_mm": 0.0,
      "delta_rotation_deg": 0.0,
      "reason": "undo_distance_increase",
      "issue_tags": ["decoupler_distance"]
    }
  ]
}
```

If the model outputs relation-based form (`target`, `anchor`, `relation`) instead of deltas, the inference layer may return that as-is (executor resolves relation) or adapt it to this schema when possible.

## 10. Acceptance Criteria (V1)

- End-to-end forward pass with graph tokens in decoder path.
- Stable training run for at least 50k steps on local-window tasks.
- On held-out validation:
  - ranking accuracy >= 0.70 on 3-way candidate sets,
  - critique issue F1 >= 0.65 for targeted rule set,
  - valid JSON rate >= 0.99.
- Inference latency acceptable for tooling integration (target <= 1.5x baseline decoder-only prompt latency for 128 graph tokens).

## 11. Risks and Mitigations

- Token mismatch -> strict Stage-1 alignment with frozen decoder.
- Graph noise explosion -> simplify wire chains, keep local windows.
- Weak labels -> start with narrow, automatically scored rules.
- Capacity ceiling at 0.8B -> keep tasks local and constrained in V1.
