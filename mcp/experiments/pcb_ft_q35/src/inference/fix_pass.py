"""Fix placement pass: run model over multiple windows, aggregate edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

from src.inference.graph_loader import schematic_to_graph_tensors
from src.inference.run import load_model_and_tokenizer, run_one


def _is_noop_edit(edit: dict[str, Any]) -> bool:
    if not isinstance(edit, dict):
        return True
    if edit.get("_parse_error"):
        return True
    action = edit.get("action", edit.get("action_type", ""))
    if not action or str(action).lower() in ("noop", "none", ""):
        return True
    return False


def fix_placement_pass(
    schematic_state: Union[str, Path, dict[str, Any]],
    window_centers: Sequence[str],
    radius_mm: float,
    checkpoint_dir: Union[str, Path],
    task: str = "suggest_repair",
    max_edits: Optional[int] = None,
    max_new_tokens: int = 256,
    device: Optional[str] = None,
    include_critique_per_window: bool = False,
) -> dict[str, Any]:
    """Run a placement fix pass: one model call per window, aggregate edits.

    Args:
        schematic_state: Path to .kicad_sch file or dict with "nodes" and "edges"
            (or "schematic_path" to parse).
        window_centers: List of refdes (or node ids) to center each window on.
        radius_mm: Window radius in mm.
        checkpoint_dir: Path to saved Qwen35GraphModel checkpoint.
        task: Task to run per window (default "suggest_repair").
        max_edits: Optional cap on number of edits to return.
        max_new_tokens: Max tokens per window generation.
        device: Device for model (default auto).
        include_critique_per_window: If True, run critique per window and add
            "critique_per_window" to the result.

    Returns:
        {"edits": list[dict], "critique_per_window": list[dict] (optional)}.
        Each edit is executor-friendly (action, target_node_ids, anchor_node_id,
        delta_* when present). Parse errors are skipped (no edit added).
    """
    checkpoint_dir = Path(checkpoint_dir)
    model, tokenizer = load_model_and_tokenizer(checkpoint_dir, device=device)

    graph_tensors = schematic_to_graph_tensors(
        schematic_state,
        center_refs=list(window_centers),
        radius_mm=radius_mm,
    )
    if not graph_tensors:
        return {"edits": [], "critique_per_window": [] if include_critique_per_window else None}

    edits: List[dict[str, Any]] = []
    critique_per_window: List[dict[str, Any]] = []

    for graph_tensor in graph_tensors:
        out = run_one(
            graph_tensor=graph_tensor,
            task=task,
            tokenizer=tokenizer,
            model=model,
            max_new_tokens=max_new_tokens,
        )
        if include_critique_per_window:
            critique_per_window.append(out if task == "critique" else {})
        if out.get("_parse_error"):
            continue
        if task == "suggest_repair" or task == "tag_issues":
            if not _is_noop_edit(out):
                edits.append(out)
        elif task == "critique" and isinstance(out.get("issues"), list):
            pass
        else:
            if not _is_noop_edit(out):
                edits.append(out)

    if max_edits is not None and max_edits >= 0 and len(edits) > max_edits:
        edits = edits[:max_edits]

    result: dict[str, Any] = {"edits": edits}
    if include_critique_per_window:
        result["critique_per_window"] = critique_per_window
    return result
