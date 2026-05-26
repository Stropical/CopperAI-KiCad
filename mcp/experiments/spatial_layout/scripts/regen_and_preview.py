from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

_EXP_ROOT = Path(__file__).resolve().parents[2]
if str(_EXP_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXP_ROOT))


def _load_json(path: str | Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run graph-to-placement regen and refresh KiCanvas preview artifacts.")
    parser.add_argument("--ir-json", required=True, help="Input canonical IR JSON")
    parser.add_argument("--scope-refs", nargs="*", default=[], help="Refs to place")
    parser.add_argument("--locked-refs", nargs="*", default=[], help="Refs that must not move")
    parser.add_argument("--fixed-context-refs", nargs="*", default=[], help="Refs included as fixed graph context")
    parser.add_argument("--page-constraints-json", default=None, help="Optional page-constraints JSON file")
    parser.add_argument("--placement-json", default=None, help="Optional PlacementResult JSON to apply directly")
    parser.add_argument("--tokens-json", default=None, help="Optional placement token JSON to decode directly")
    parser.add_argument("--updated-ir-out", default=None, help="Where to write the updated IR JSON")
    parser.add_argument("--preview-out", default=None, help="Where to write the faithful preview .kicad_sch")
    args = parser.parse_args()

    from spatial_layout.faithful_export import apply_placement_result, apply_placement_tokens, write_faithful_kicad_sch
    from spatial_layout.regen import PlacementComponent, PlacementResult, build_placement_request, place_block
    from spatial_layout.verify import verify_placement

    ir = _load_json(args.ir_json)
    page_constraints = _load_json(args.page_constraints_json) if args.page_constraints_json else None
    preview_out = Path(args.preview_out) if args.preview_out else Path(__file__).resolve().parents[1] / "preview" / "preview_faithful.kicad_sch"
    updated_ir_out = Path(args.updated_ir_out) if args.updated_ir_out else preview_out.with_suffix(".ir.json")
    scope = {"refs": list(args.scope_refs), "block_id": None}
    request = build_placement_request(
        ir,
        scope,
        locked_refs=args.locked_refs,
        fixed_context_refs=args.fixed_context_refs,
        page_constraints=page_constraints,
    )

    if args.tokens_json:
        token_data = json.loads(Path(args.tokens_json).read_text(encoding="utf-8"))
        if not isinstance(token_data, list):
            raise SystemExit("--tokens-json must contain a JSON array")
        updated_ir = apply_placement_tokens(ir, [str(token) for token in token_data], scope_refs=args.scope_refs, locked_refs=args.locked_refs)
        placement = PlacementResult(components=[])
        verify = verify_placement(
            request=request,
            placement=placement,
            original_ir=ir,
            placed_ir=updated_ir,
        )
        write_faithful_kicad_sch(updated_ir, preview_out)
        result = {
            "placement": placement.to_dict(),
            "ir": updated_ir,
            "verify": verify,
            "preview_path": str(preview_out),
            "debug": {"prompt_tokens": [], "output_tokens": [str(token) for token in token_data], "changed_refs": sorted(args.scope_refs)},
        }
    elif args.placement_json:
        placement_blob = _load_json(args.placement_json)
        components = [
            PlacementComponent(**component)
            for component in placement_blob.get("components") or []
            if isinstance(component, dict)
        ]
        updated_ir = apply_placement_result(ir, PlacementResult(components=components), scope_refs=args.scope_refs, locked_refs=args.locked_refs)
        write_faithful_kicad_sch(updated_ir, preview_out)
        result = {
            "placement": PlacementResult(components=components).to_dict(),
            "ir": updated_ir,
            "verify": verify_placement(
                request=request,
                placement=PlacementResult(components=components),
                original_ir=ir,
                placed_ir=updated_ir,
            ),
            "preview_path": str(preview_out),
            "debug": {"prompt_tokens": [], "output_tokens": [], "changed_refs": sorted(args.scope_refs)},
        }
    else:
        result = place_block(
            ir,
            scope,
            locked_refs=args.locked_refs,
            fixed_context_refs=args.fixed_context_refs,
            page_constraints=page_constraints,
            preview_path=preview_out,
        )

    updated_ir_out.write_text(json.dumps(result["ir"], indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "preview_path": result["preview_path"],
        "updated_ir_path": str(updated_ir_out),
        "verify": result["verify"],
        "changed_refs": result["debug"]["changed_refs"],
        "changed_component_ids": result["debug"].get("changed_component_ids", []),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
