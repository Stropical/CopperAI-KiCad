# Spatial Layout Bridge Contract

This document defines the Python JSON request/response shape for the graph-to-placement bridge and keeps it aligned with the existing `relayout_target` response structure in `mcp/LayoutEngine/web/src/agent-interface/types.ts`.

## Request

```json
{
  "ir": { "...": "canonical schematic IR" },
  "scope": {
    "refs": ["U1", "C1", "C2"],
    "component_ids": ["cmp_u1", "cmp_c1", "cmp_c2"],
    "block_id": null
  },
  "locked_refs": ["U1"],
  "locked_component_ids": ["cmp_u1"],
  "fixed_context_refs": ["J1"],
  "fixed_context_component_ids": ["cmp_j1"],
  "page_constraints": {
    "anchor_xy_mm": [80.0, 80.0],
    "page_bbox_mm": [0.0, 0.0, 200.0, 150.0],
    "preferred_region": "left",
    "max_size_mm": [60.0, 40.0]
  }
}
```

## Response

```json
{
  "placement": {
    "components": [
      {
        "ref": "C1",
        "component_id": "cmp_c1",
        "local_id": "ID_1",
        "x_mm": 42.0,
        "y_mm": 18.5,
        "rotation_deg": 90.0,
        "mirror": "none",
        "anchor_ref": "U1",
        "anchor_component_id": "cmp_u1",
        "anchor_pin": "5",
        "group_id": "ID_0"
      }
    ]
  },
  "ir": { "...": "updated schematic IR" },
  "verify": {
    "ok": true,
    "issues": []
  },
  "preview_path": "mcp/experiments/spatial_layout/preview/preview_faithful.kicad_sch",
  "debug": {
    "prompt_tokens": ["BOS", "STRUCT_START", "...", "SEP", "PLACE_START"],
    "output_tokens": ["BOS", "...", "EOS"],
    "changed_refs": ["C1", "C2"]
  }
}
```

## Alignment With `relayout_target`

The bridge keeps the same top-level semantics as `RelayoutTargetResponse` even though the payload is placement-centric:

- `placement.components[*]` is the spatial equivalent of `placements`.
- `placement.components[*].component_id` is the authoritative component identity.
- `debug.changed_refs` maps to `changedComponentIds`.
- `debug.changed_component_ids` should be treated as the identity-safe equivalent if present.
- `scope.refs` is the request-side equivalent of `impactedComponentIds`.
- `scope.component_ids` is the preferred request-side identity key.
- `verify.ok` and `verify.issues` cover the same post-layout validation role as `verify_layout`.
- `debug` is where token-level diagnostics live so callers do not need token grammar for normal operation.

## Constraints

- Callers must treat `placement` as the stable artifact.
- Token streams are debug-only.
- No English instructions should be forwarded into the model. The model input is the machine token stream in `debug.prompt_tokens`.
- Persistence must keep `component_id` in the canonical IR or sidecar across calls; `ref` is not authoritative identity.
