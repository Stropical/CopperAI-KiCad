#!/usr/bin/env python3
"""Live RL layout optimization pass against a running KiCad instance.

After the LLM places and wires components, this module:
1. Reads the current schematic state from KiCad MCP
2. Identifies poorly-placed components (alignment, spacing, crossings)
3. Proposes candidate moves for each (grid-aligned, toward peers, compact)
4. Selects the best move using the RL policy (or heuristic scoring)
5. Applies moves back to live KiCad via move_component

Usage:
    from schematic_gym.error_fixer.live_rl_pass import optimize_layout
    results = optimize_layout(kicad_url="http://127.0.0.1:8080/mcp", max_steps=10)
"""

from __future__ import annotations

import json
import math
import logging
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

GRID_MM = 0.635  # KiCad default schematic grid


# ---------------------------------------------------------------------------
# KiCad MCP client
# ---------------------------------------------------------------------------

def _call_kicad(url: str, tool: str, args: dict | None = None) -> Any:
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args or {}},
    }
    resp = requests.post(url, json=payload, timeout=30)
    result = resp.json()
    content = result.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"text": text}


# ---------------------------------------------------------------------------
# State reader — pull schematic state from live KiCad
# ---------------------------------------------------------------------------

@dataclass
class ComponentState:
    reference: str
    symbol: str
    value: str
    x: float
    y: float
    rotation: float
    pins: list[dict] = field(default_factory=list)
    is_power: bool = False
    bbox_w: float = 0.0
    bbox_h: float = 0.0


@dataclass
class SchematicState:
    components: list[ComponentState] = field(default_factory=list)
    grid_mm: float = GRID_MM


def read_schematic_state(kicad_url: str) -> SchematicState:
    """Read current schematic layout from live KiCad."""
    bounds = _call_kicad(kicad_url, "get_all_bounds")
    state = SchematicState()

    if not isinstance(bounds, list):
        return state

    for b in bounds:
        ref = b.get("reference", "")
        if not ref or ref.startswith("#"):
            continue

        # Get pins for this component.
        pins = _call_kicad(kicad_url, "get_component_pins", {"reference": ref})
        pin_list = pins if isinstance(pins, list) else []

        comp = ComponentState(
            reference=ref,
            symbol=b.get("symbol", ""),
            value=b.get("value", ""),
            x=b.get("cx_mm", 0.0),
            y=b.get("cy_mm", 0.0),
            rotation=0.0,
            pins=pin_list,
            is_power=ref.startswith("#") or ref.startswith("PWR"),
            bbox_w=b.get("width_mm", 0.0),
            bbox_h=b.get("height_mm", 0.0),
        )
        state.components.append(comp)

    return state


# ---------------------------------------------------------------------------
# Layout scorer — identify what needs fixing
# ---------------------------------------------------------------------------

def score_component_placement(
    comp: ComponentState,
    all_comps: list[ComponentState],
    grid_mm: float,
) -> dict[str, float]:
    """Score how well a component is placed (0=bad, 1=good).

    Accounts for grid alignment, peer alignment, spacing, and
    critically: bounding box overlap with other components.
    """
    scores = {}

    # Grid alignment.
    x_off = abs(comp.x % grid_mm)
    y_off = abs(comp.y % grid_mm)
    x_off = min(x_off, grid_mm - x_off)
    y_off = min(y_off, grid_mm - y_off)
    scores["grid"] = 1.0 - min((x_off + y_off) / grid_mm, 1.0)

    # Peer alignment — how many other components share X or Y.
    align_count = 0
    for other in all_comps:
        if other.reference == comp.reference:
            continue
        if abs(other.y - comp.y) < grid_mm * 2:
            align_count += 1
        elif abs(other.x - comp.x) < grid_mm * 2:
            align_count += 1
    scores["alignment"] = min(align_count / max(len(all_comps) - 1, 1), 1.0)

    # Spacing — distance to nearest neighbor.
    min_dist = float("inf")
    for other in all_comps:
        if other.reference == comp.reference:
            continue
        d = math.hypot(other.x - comp.x, other.y - comp.y)
        if d > 0:
            min_dist = min(min_dist, d)
    if min_dist < 3.0:
        scores["spacing"] = 0.2  # too close / overlapping
    elif min_dist > 40.0:
        scores["spacing"] = 0.3  # too far
    else:
        scores["spacing"] = 1.0

    # Overlap penalty — check if bounding boxes intersect.
    overlap_count = 0
    half_w = max(comp.bbox_w / 2, 2.0)
    half_h = max(comp.bbox_h / 2, 2.0)
    for other in all_comps:
        if other.reference == comp.reference:
            continue
        other_hw = max(other.bbox_w / 2, 2.0)
        other_hh = max(other.bbox_h / 2, 2.0)
        # AABB overlap test with 1mm margin.
        margin = 1.0
        if (abs(comp.x - other.x) < half_w + other_hw + margin and
                abs(comp.y - other.y) < half_h + other_hh + margin):
            overlap_count += 1
    scores["no_overlap"] = 1.0 if overlap_count == 0 else max(0.0, 1.0 - overlap_count * 0.4)

    scores["total"] = (
        0.15 * scores["grid"]
        + 0.25 * scores["alignment"]
        + 0.25 * scores["spacing"]
        + 0.35 * scores["no_overlap"]  # Overlap is the worst — highest weight
    )
    return scores


# ---------------------------------------------------------------------------
# Candidate move generation
# ---------------------------------------------------------------------------

@dataclass
class CandidateMove:
    label: str
    dx: float
    dy: float
    score: float = 0.0  # pre-computed heuristic score


def generate_candidates(
    comp: ComponentState,
    all_comps: list[ComponentState],
    grid_mm: float,
    max_candidates: int = 8,
) -> list[CandidateMove]:
    """Generate candidate moves for a component."""
    candidates: list[CandidateMove] = []

    def add(label: str, dx: float, dy: float) -> None:
        # Snap to grid.
        dx = round(dx / grid_mm) * grid_mm
        dy = round(dy / grid_mm) * grid_mm
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return
        # Deduplicate.
        for existing in candidates:
            if abs(existing.dx - dx) < 1e-6 and abs(existing.dy - dy) < 1e-6:
                return
        candidates.append(CandidateMove(label=label, dx=dx, dy=dy))

    peers = [c for c in all_comps if c.reference != comp.reference and not c.is_power]

    # Align to nearest peer's Y (horizontal alignment).
    for peer in sorted(peers, key=lambda p: math.hypot(p.x - comp.x, p.y - comp.y)):
        dy = peer.y - comp.y
        if 0.5 < abs(dy) < 20.0:
            add(f"align_y_{peer.reference}", 0.0, dy)
            break

    # Align to nearest peer's X (vertical alignment).
    for peer in sorted(peers, key=lambda p: math.hypot(p.x - comp.x, p.y - comp.y)):
        dx = peer.x - comp.x
        if 0.5 < abs(dx) < 20.0:
            add(f"align_x_{peer.reference}", dx, 0.0)
            break

    # Move toward centroid of connected components.
    if peers:
        cx = sum(p.x for p in peers) / len(peers)
        cy = sum(p.y for p in peers) / len(peers)
        dx = cx - comp.x
        dy = cy - comp.y
        mag = math.hypot(dx, dy)
        if mag > 1.0:
            scale = min(grid_mm * 3 / mag, 1.0)
            add("toward_centroid", dx * scale, dy * scale)

    # Compact — move 1-2 grid steps toward nearest peer.
    if peers:
        nearest = min(peers, key=lambda p: math.hypot(p.x - comp.x, p.y - comp.y))
        dx = nearest.x - comp.x
        dy = nearest.y - comp.y
        mag = math.hypot(dx, dy)
        if mag > grid_mm * 2:
            step = grid_mm * 2 / mag
            add("compact", dx * step, dy * step)

    # Grid snap — move to nearest grid point.
    snap_dx = round(comp.x / grid_mm) * grid_mm - comp.x
    snap_dy = round(comp.y / grid_mm) * grid_mm - comp.y
    if abs(snap_dx) > 0.01 or abs(snap_dy) > 0.01:
        add("grid_snap", snap_dx, snap_dy)

    # Small nudges in 4 directions.
    for label, dx, dy in [
        ("nudge_up", 0, -grid_mm * 2),
        ("nudge_down", 0, grid_mm * 2),
        ("nudge_left", -grid_mm * 2, 0),
        ("nudge_right", grid_mm * 2, 0),
    ]:
        add(label, dx, dy)

    # Noop is always an option.
    candidates.append(CandidateMove(label="noop", dx=0.0, dy=0.0))

    return candidates[:max_candidates]


# ---------------------------------------------------------------------------
# Move scorer — evaluate each candidate
# ---------------------------------------------------------------------------

def score_candidate(
    comp: ComponentState,
    move: CandidateMove,
    all_comps: list[ComponentState],
    grid_mm: float,
) -> float:
    """Score a candidate move by simulating it.

    Hard-penalizes moves that create bbox overlaps with other components.
    """
    if move.label == "noop":
        return score_component_placement(comp, all_comps, grid_mm)["total"]

    # Simulate the move.
    orig_x, orig_y = comp.x, comp.y
    comp.x += move.dx
    comp.y += move.dy
    result = score_component_placement(comp, all_comps, grid_mm)
    score = result["total"]

    # Hard penalty: if this move creates an overlap, score = 0.
    if result["no_overlap"] < 0.5:
        score = 0.0

    comp.x, comp.y = orig_x, orig_y
    return score


# ---------------------------------------------------------------------------
# Main optimization loop
# ---------------------------------------------------------------------------

@dataclass
class MoveResult:
    reference: str
    label: str
    dx: float
    dy: float
    score_before: float
    score_after: float
    applied: bool


def optimize_layout(
    kicad_url: str = "http://127.0.0.1:8080/mcp",
    max_steps: int = 10,
    min_improvement: float = 0.10,
    target_score: float = 0.85,
    dry_run: bool = False,
    verbose: bool = True,
    only_refs: list[str] | None = None,
) -> list[MoveResult]:
    """Run RL-style layout optimization on a live KiCad schematic.

    Uses move_chunk (NOT move_component) so wires move with components.

    1. Read current state from KiCad
    2. Score each component's placement
    3. For the worst-placed component, generate candidates
    4. Pick the best candidate (highest score)
    5. Apply via move_chunk (moves wires + labels too)
    6. Repeat until target_score reached or max_steps exhausted

    Parameters
    ----------
    only_refs : list[str] | None
        If set, only optimize these components. Useful for targeting
        newly placed components without touching the existing layout.

    Returns list of moves applied.
    """
    results: list[MoveResult] = []

    if verbose:
        print("=== RL Layout Optimization Pass ===\n")

    state = read_schematic_state(kicad_url)
    if not state.components:
        if verbose:
            print("No components found.")
        return results

    non_power = [c for c in state.components if not c.is_power]
    if only_refs:
        non_power = [c for c in non_power if c.reference in only_refs]
    if verbose:
        refs = [c.reference for c in non_power]
        print(f"Components to optimize: {len(non_power)} — {refs}")

    moved_refs: set[str] = set()

    for step in range(max_steps):
        # Score all components.
        comp_scores: list[tuple[ComponentState, float]] = []
        for comp in non_power:
            if comp.reference in moved_refs:
                continue
            s = score_component_placement(comp, non_power, state.grid_mm)
            comp_scores.append((comp, s["total"]))

        if not comp_scores:
            if verbose:
                print(f"\nStep {step}: All components scored or moved.")
            break

        # Find worst-placed component.
        comp_scores.sort(key=lambda x: x[1])
        worst_comp, worst_score = comp_scores[0]

        if worst_score >= target_score:
            if verbose:
                print(f"\nStep {step}: All components score >= {target_score:.1%}. Done.")
            break

        # Generate and score candidates.
        candidates = generate_candidates(worst_comp, non_power, state.grid_mm)
        for cand in candidates:
            cand.score = score_candidate(worst_comp, cand, non_power, state.grid_mm)

        # Pick best.
        candidates.sort(key=lambda c: -c.score)
        best = candidates[0]

        improvement = best.score - worst_score

        if verbose:
            print(f"\nStep {step}: {worst_comp.reference} score={worst_score:.3f}")
            print(f"  Best move: {best.label} dx={best.dx:+.2f} dy={best.dy:+.2f} → score={best.score:.3f} (Δ={improvement:+.3f})")

        if improvement < min_improvement and best.label != "noop":
            if verbose:
                print(f"  Improvement {improvement:.3f} < {min_improvement:.3f}, trying noop")
            # Mark as moved so we skip it next time.
            moved_refs.add(worst_comp.reference)
            results.append(MoveResult(
                reference=worst_comp.reference,
                label="skip", dx=0, dy=0,
                score_before=worst_score, score_after=worst_score,
                applied=False,
            ))
            continue

        if best.label == "noop":
            moved_refs.add(worst_comp.reference)
            results.append(MoveResult(
                reference=worst_comp.reference,
                label="noop", dx=0, dy=0,
                score_before=worst_score, score_after=worst_score,
                applied=False,
            ))
            continue

        # Apply the move using move_chunk (moves wires + labels with the component).
        # NEVER use move_component — it only moves the symbol body, leaving wires behind.
        new_x = worst_comp.x + best.dx
        new_y = worst_comp.y + best.dy

        if not dry_run:
            move_result = _call_kicad(kicad_url, "move_chunk", {
                "references": [worst_comp.reference],
                "dx_mm": round(best.dx, 3),
                "dy_mm": round(best.dy, 3),
            })
            result_text = json.dumps(move_result, default=str)
            success = "error" not in result_text.lower() or "moved" in result_text.lower()
            if verbose:
                print(f"  Applied: move_chunk({worst_comp.reference}, dx={best.dx:+.2f}, dy={best.dy:+.2f})")
                if not success:
                    print(f"  WARNING: {result_text[:150]}")
        else:
            success = True
            if verbose:
                print(f"  [DRY RUN] Would move_chunk {worst_comp.reference} by dx={best.dx:+.2f}, dy={best.dy:+.2f}")

        if success:
            worst_comp.x = new_x
            worst_comp.y = new_y

        moved_refs.add(worst_comp.reference)
        results.append(MoveResult(
            reference=worst_comp.reference,
            label=best.label,
            dx=best.dx, dy=best.dy,
            score_before=worst_score,
            score_after=best.score,
            applied=success and not dry_run,
        ))

    if verbose:
        print(f"\n=== Optimization Complete: {len([r for r in results if r.applied])} moves applied ===")
        for r in results:
            status = "MOVED" if r.applied else "SKIP"
            print(f"  [{status}] {r.reference}: {r.label} ({r.score_before:.3f} → {r.score_after:.3f})")

    return results


# ---------------------------------------------------------------------------
# Combined LLM + RL pipeline against live KiCad
# ---------------------------------------------------------------------------

def llm_then_rl(
    kicad_url: str,
    ollama_url: str,
    model: str,
    task_prompt: str,
    max_llm_turns: int = 15,
    max_rl_steps: int = 10,
    verbose: bool = True,
) -> dict:
    """Full pipeline: LLM fixes logic, then RL optimizes layout."""
    from .live_test import run_agent_loop, SYSTEM_PROMPT, print_final_state

    # Phase 1: LLM
    if verbose:
        print("=" * 60)
        print("PHASE 1: LLM Intent Resolution")
        print("=" * 60)

    from .env import ERCFixerEnv
    from .mcp_bridge import MCPBridge

    # We don't use the gym env here — we talk directly to KiCad.
    # But we need the MCPBridge tool definitions for the LLM.
    # Build a minimal env just for tool definitions.
    env = ERCFixerEnv(max_steps=100, observation_modes=["structured"])
    env.reset(seed=1)
    bridge = MCPBridge(env)

    # Get actual KiCad tools instead.
    tools_resp = requests.post(
        kicad_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        timeout=10,
    )
    kicad_tools = tools_resp.json().get("result", {}).get("tools", [])

    TOOL_NAMES = {
        "get_schematic_summary", "erc_check", "get_component_pins",
        "move_component", "rotate_component", "add_wire",
        "batch_connect", "get_all_bounds", "find_empty_space",
        "batch_search_components", "batch_get_component_data",
        "place_component", "delete_components_batch",
        "get_netlist", "net_diagnostics",
    }
    ollama_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
            },
        }
        for t in kicad_tools
        if t.get("name") in TOOL_NAMES
    ]

    # Run LLM loop against real KiCad.
    import time
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task_prompt},
    ]

    llm_actions = []
    for turn in range(max_llm_turns):
        resp = requests.post(
            f"{ollama_url}/api/chat",
            json={"model": model, "messages": messages, "tools": ollama_tools, "stream": False, "options": {"temperature": 0.1}},
            timeout=300,
        )
        msg = resp.json().get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        if verbose and content:
            print(f"  LLM: {content[:200]}")

        if "DONE" in (content or "").upper() and not tool_calls:
            break

        if tool_calls:
            messages.append(msg)
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                if verbose:
                    print(f"  TOOL: {name}({json.dumps(args, default=str)[:80]})")
                result = _call_kicad(kicad_url, name, args)
                messages.append({"role": "tool", "content": json.dumps(result, default=str)})
                llm_actions.append({"tool": name, "args": args})
        else:
            messages.append(msg)
            if not content:
                break

    # Phase 2: RL Layout Optimization
    if verbose:
        print()
        print("=" * 60)
        print("PHASE 2: RL Layout Optimization")
        print("=" * 60)

    # Only optimize components that the LLM placed/modified,
    # not the existing layout. Extract refs from LLM actions.
    new_refs = []
    for action in llm_actions:
        if action["tool"] == "place_component":
            ref = action["args"].get("reference", "")
            if ref:
                new_refs.append(ref)

    rl_results = optimize_layout(
        kicad_url=kicad_url,
        max_steps=max_rl_steps,
        verbose=verbose,
        only_refs=new_refs if new_refs else None,
    )

    # Final verification.
    if verbose:
        print()
        print("=" * 60)
        print("FINAL STATE")
        print("=" * 60)
    summary = _call_kicad(kicad_url, "get_schematic_summary")
    erc = _call_kicad(kicad_url, "erc_check")
    diag = _call_kicad(kicad_url, "net_diagnostics")

    if verbose:
        print(f"\n{summary.get('text', str(summary))[:400]}")
        erc_text = erc.get("text", json.dumps(erc, default=str)) if isinstance(erc, dict) else json.dumps(erc, default=str)
        print(f"\nERC: {erc_text[:200]}")
        issues = diag.get("issues", [])
        overlap = diag.get("summary", {}).get("overlapping_labels", 0)
        print(f"Net diagnostics: {len(issues)} issues, {overlap} overlapping labels")

    return {
        "llm_actions": len(llm_actions),
        "rl_moves": len([r for r in rl_results if r.applied]),
        "rl_results": rl_results,
    }


if __name__ == "__main__":
    import sys
    # Quick test: just run RL optimization on current schematic.
    results = optimize_layout(verbose=True)
    sys.exit(0 if results else 1)
