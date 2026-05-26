#!/usr/bin/env python3
"""Neural network RL layout pass against live KiCad.

Loads the trained MultiStepPlacementPolicy and runs it against
a live KiCad instance by:
1. Reading schematic state from KiCad MCP into a gym-compatible Sheet
2. Building the same observation vectors the model was trained on
3. Running the policy to select candidate moves
4. Applying moves via move_chunk (preserves wires)

Usage:
    from schematic_gym.error_fixer.live_nn_pass import nn_optimize_layout
    results = nn_optimize_layout(model_path="path/to/policy_final.pt")
"""

from __future__ import annotations

import json
import math
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import requests
import torch

logger = logging.getLogger(__name__)

GRID_MM = 2.54
KICAD_GRID_MM = 0.635


# ---------------------------------------------------------------------------
# KiCad MCP helpers
# ---------------------------------------------------------------------------

def _call_kicad(url: str, tool: str, args: dict | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": args or {}}}
    resp = requests.post(url, json=payload, timeout=30)
    result = resp.json()
    content = result.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"text": text}


# ---------------------------------------------------------------------------
# Build gym-compatible state from live KiCad
# ---------------------------------------------------------------------------

def _read_kicad_state(kicad_url: str) -> dict:
    """Read schematic state from live KiCad into a structured dict."""
    bounds = _call_kicad(kicad_url, "get_all_bounds")
    if not isinstance(bounds, list):
        bounds = []

    components = []
    for b in bounds:
        ref = b.get("reference", "")
        if not ref:
            continue
        pins = _call_kicad(kicad_url, "get_component_pins", {"reference": ref})
        pin_list = pins if isinstance(pins, list) else []
        is_power = ref.startswith("#") or ref.startswith("PWR")
        components.append({
            "reference": ref,
            "x": b.get("cx_mm", 0.0),
            "y": b.get("cy_mm", 0.0),
            "width": b.get("width_mm", 5.0),
            "height": b.get("height_mm", 5.0),
            "rotation": 0,
            "is_power": is_power,
            "pins": pin_list,
            "value": b.get("value", ""),
            "symbol": b.get("symbol", ""),
        })

    return {"components": components, "grid_mm": KICAD_GRID_MM}


def _build_state_vec(state: dict, step: int = 0, max_steps: int = 10, moved_count: int = 0) -> np.ndarray:
    """Build a state vector matching MultiStepFixerEnv's format.

    The trained model expects 2892 dims = 2890 (structured obs) + 2 (step, moved).
    The structured obs is: scalars + instance arrays + pin arrays + wire arrays + erc arrays + readability.
    """
    MAX_INSTANCES = 64
    MAX_PINS = 256
    MAX_WIRES = 256
    MAX_ERC = 64

    comps = [c for c in state["components"] if not c["is_power"]]
    all_comps = state["components"]

    # Scalars (20 values to match training).
    scalars = np.zeros(20, dtype=np.float32)
    scalars[0] = len(all_comps)  # num_instances
    scalars[1] = 0  # num_wires (we don't read wires from KiCad)
    scalars[2] = 0  # num_erc_errors
    scalars[3] = 0  # num_erc_warnings
    scalars[4] = step  # step_num
    scalars[5] = max_steps - step  # steps_remaining

    # Instance positions (64 × 2).
    inst_pos = np.zeros((MAX_INSTANCES, 2), dtype=np.float32)
    inst_rot = np.zeros(MAX_INSTANCES, dtype=np.float32)
    inst_mask = np.zeros(MAX_INSTANCES, dtype=np.float32)
    for i, c in enumerate(all_comps[:MAX_INSTANCES]):
        inst_pos[i] = [c["x"], c["y"]]
        inst_rot[i] = c.get("rotation", 0) / 360.0
        inst_mask[i] = 1.0

    # Pin positions (256 × 2).
    pin_pos = np.zeros((MAX_PINS, 2), dtype=np.float32)
    pin_conn = np.zeros(MAX_PINS, dtype=np.float32)
    pin_mask = np.zeros(MAX_PINS, dtype=np.float32)
    pin_idx = 0
    for c in all_comps:
        for p in c.get("pins", []):
            if pin_idx >= MAX_PINS:
                break
            pin_pos[pin_idx] = [p.get("x_mm", 0), p.get("y_mm", 0)]
            pin_conn[pin_idx] = 1.0  # assume connected (we don't track this from KiCad)
            pin_mask[pin_idx] = 1.0
            pin_idx += 1

    # Wire endpoints (256 × 4) — zeros since we don't read wires.
    wire_ep = np.zeros((MAX_WIRES, 4), dtype=np.float32)
    wire_mask = np.zeros(MAX_WIRES, dtype=np.float32)

    # ERC features (64 × 4) — zeros.
    erc_feat = np.zeros((MAX_ERC, 4), dtype=np.float32)
    erc_mask = np.zeros(MAX_ERC, dtype=np.float32)

    # Readability scores (4 scalars).
    read_scores = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)

    # Flatten everything into one vector.
    parts = [
        scalars,
        inst_pos.flatten(), inst_rot, inst_mask,
        pin_pos.flatten(), pin_conn, pin_mask,
        wire_ep.flatten(), wire_mask,
        erc_feat.flatten(), erc_mask,
        read_scores,
    ]
    base_vec = np.concatenate(parts)

    # Pad or truncate to exactly 2890 (the base dim from training).
    target_base = 2890
    if len(base_vec) < target_base:
        base_vec = np.pad(base_vec, (0, target_base - len(base_vec)))
    elif len(base_vec) > target_base:
        base_vec = base_vec[:target_base]

    # Append 2 RL-specific features.
    extra = np.array([
        step / max(max_steps, 1),
        moved_count / max(len(all_comps), 1),
    ], dtype=np.float32)

    return np.concatenate([base_vec, extra])


# ---------------------------------------------------------------------------
# Candidate generation (matches MultiStepFixerEnv)
# ---------------------------------------------------------------------------

@dataclass
class LiveCandidate:
    label: str
    reference: str
    dx: float
    dy: float
    features: np.ndarray


def _generate_live_candidates(
    target: dict,
    all_comps: list[dict],
    grid_mm: float,
    max_candidates: int = 8,
) -> list[LiveCandidate]:
    """Generate candidate moves for a component, matching the training format."""
    candidates: list[LiveCandidate] = []
    seen: set[tuple[float, float]] = set()
    ref = target["reference"]
    tx, ty = target["x"], target["y"]

    peers = [c for c in all_comps if c["reference"] != ref and not c["is_power"]]

    def add(label: str, dx: float, dy: float) -> None:
        # Snap to KiCad grid.
        dx = round(dx / grid_mm) * grid_mm
        dy = round(dy / grid_mm) * grid_mm
        key = (round(dx, 3), round(dy, 3))
        if key in seen or (abs(dx) < 0.01 and abs(dy) < 0.01):
            return
        seen.add(key)
        candidates.append(LiveCandidate(label=label, reference=ref, dx=dx, dy=dy,
                                         features=np.zeros(24, dtype=np.float32)))

    # Align to nearest peers.
    for peer in sorted(peers, key=lambda p: math.hypot(p["x"] - tx, p["y"] - ty)):
        dy = peer["y"] - ty
        if 0.5 < abs(dy) < 30.0:
            add(f"align_y_{peer['reference']}", 0.0, dy)
            break
    for peer in sorted(peers, key=lambda p: math.hypot(p["x"] - tx, p["y"] - ty)):
        dx = peer["x"] - tx
        if 0.5 < abs(dx) < 30.0:
            add(f"align_x_{peer['reference']}", dx, 0.0)
            break

    # Move toward centroid of peers.
    if peers:
        cx = sum(p["x"] for p in peers) / len(peers)
        cy = sum(p["y"] for p in peers) / len(peers)
        dx, dy = cx - tx, cy - ty
        mag = math.hypot(dx, dy)
        if mag > 1.0:
            scale = min(grid_mm * 4 / mag, 1.0)
            add("toward_centroid", dx * scale, dy * scale)

    # Compact toward nearest peer.
    if peers:
        nearest = min(peers, key=lambda p: math.hypot(p["x"] - tx, p["y"] - ty))
        dx, dy = nearest["x"] - tx, nearest["y"] - ty
        mag = math.hypot(dx, dy)
        if mag > grid_mm * 3:
            step = grid_mm * 3 / mag
            add("compact", dx * step, dy * step)

    # Spacing moves — push away if too close.
    if peers:
        nearest = min(peers, key=lambda p: math.hypot(p["x"] - tx, p["y"] - ty))
        dist = math.hypot(nearest["x"] - tx, nearest["y"] - ty)
        if dist < 8.0:  # too close, push away
            dx = tx - nearest["x"]
            dy = ty - nearest["y"]
            mag = math.hypot(dx, dy)
            if mag > 0.1:
                add("space_out", dx / mag * grid_mm * 3, dy / mag * grid_mm * 3)

    # Grid nudges (larger — 3-5 grid steps).
    for label, dx, dy in [
        ("nudge_up", 0, -grid_mm * 4),
        ("nudge_down", 0, grid_mm * 4),
        ("nudge_left", -grid_mm * 4, 0),
        ("nudge_right", grid_mm * 4, 0),
    ]:
        add(label, dx, dy)

    # Noop always last.
    candidates.append(LiveCandidate(label="noop", reference=ref, dx=0.0, dy=0.0,
                                     features=np.zeros(24, dtype=np.float32)))

    # Build feature vectors.
    sheet_w = max(c["x"] for c in all_comps) - min(c["x"] for c in all_comps) + 50
    sheet_h = max(c["y"] for c in all_comps) - min(c["y"] for c in all_comps) + 50

    for cand in candidates[:max_candidates]:
        feat = cand.features
        feat[0] = 1.0 if cand.label == "noop" else 0.0
        feat[1] = cand.dx / max(sheet_w, 1)
        feat[2] = cand.dy / max(sheet_h, 1)
        feat[3] = math.hypot(cand.dx, cand.dy) / max(sheet_w, sheet_h, 1)
        feat[5] = tx / max(sheet_w, 1)
        feat[6] = ty / max(sheet_h, 1)
        feat[9] = 0.5  # current readability placeholder
        feat[10] = 1.0  # is_selected
        feat[14] = 1.0 if "align" in cand.label or "compact" in cand.label else 0.0
        feat[15] = 1.0 if "nudge" in cand.label or "space" in cand.label else 0.0
        # Role flags.
        rp = ref[0].upper() if ref else ""
        feat[16] = 1.0 if rp == "C" else 0.0  # decoupler
        feat[17] = 1.0 if rp in ("J", "P") else 0.0  # connector
        feat[18] = 1.0 if rp in ("R", "L") else 0.0  # passive
        feat[19] = 1.0 if rp in ("U", "D") else 0.0  # ic/diode
        # Edge proximity after move.
        new_x = max(0, tx + cand.dx)
        new_y = max(0, ty + cand.dy)
        edge_band = max(min(sheet_w, sheet_h) * 0.15, 5.0)
        min_edge = min(new_x, new_y, sheet_w - new_x, sheet_h - new_y)
        feat[20] = max(0, min(1, 1 - min_edge / edge_band))

    return candidates[:max_candidates]


# ---------------------------------------------------------------------------
# Overlap checker
# ---------------------------------------------------------------------------

def _would_overlap(target: dict, dx: float, dy: float, all_comps: list[dict], margin: float = 3.0) -> bool:
    """Check if moving target by (dx,dy) would overlap any other component."""
    new_x = target["x"] + dx
    new_y = target["y"] + dy
    tw = max(target.get("width", 5), 3) / 2 + margin
    th = max(target.get("height", 5), 3) / 2 + margin

    for other in all_comps:
        if other["reference"] == target["reference"]:
            continue
        if other["is_power"]:
            continue
        ow = max(other.get("width", 5), 3) / 2
        oh = max(other.get("height", 5), 3) / 2
        if abs(new_x - other["x"]) < tw + ow and abs(new_y - other["y"]) < th + oh:
            return True
    return False


# ---------------------------------------------------------------------------
# Main: Neural network layout optimization
# ---------------------------------------------------------------------------

@dataclass
class NNMoveResult:
    reference: str
    label: str
    dx: float
    dy: float
    policy_confidence: float
    applied: bool


def nn_optimize_layout(
    kicad_url: str = "http://127.0.0.1:8080/mcp",
    model_path: str = "schematic_gym/renders/rl_training_v4/policy_final.pt",
    max_steps: int = 10,
    only_refs: list[str] | None = None,
    verbose: bool = True,
    dry_run: bool = False,
    device: str = "cpu",
) -> list[NNMoveResult]:
    """Run the trained neural network policy on a live KiCad schematic.

    Parameters
    ----------
    model_path : path to policy_final.pt
    only_refs : if set, only optimize these components
    """
    results: list[NNMoveResult] = []

    # Load model.
    from .rl_policy import MultiStepPlacementPolicy

    checkpoint = torch.load(model_path, weights_only=False, map_location=device)
    state_dim = checkpoint["state_dim"]
    candidate_dim = checkpoint["candidate_dim"]

    policy = MultiStepPlacementPolicy(
        state_dim=state_dim,
        candidate_dim=candidate_dim,
        hidden_dim=256,
        max_steps=max_steps,
    )
    policy.load_state_dict(checkpoint["model_state_dict"])
    policy.eval()
    policy.to(device)

    if verbose:
        total_params = sum(p.numel() for p in policy.parameters())
        print(f"Loaded policy: {total_params:,} params from {model_path}")
        print(f"  Trained for {checkpoint['episode']} episodes")

    # Read state from KiCad.
    state = _read_kicad_state(kicad_url)
    all_comps = state["components"]
    non_power = [c for c in all_comps if not c["is_power"]]

    if only_refs:
        targets = [c for c in non_power if c["reference"] in only_refs]
    else:
        targets = list(non_power)

    if verbose:
        print(f"Components to optimize: {[c['reference'] for c in targets]}")
        print(f"Total components: {len(all_comps)} ({len(non_power)} non-power)")

    moved: set[str] = set()

    for step in range(max_steps):
        # Find worst-placed component not yet moved.
        remaining = [c for c in targets if c["reference"] not in moved]
        if not remaining:
            if verbose:
                print(f"\nStep {step}: All target components moved.")
            break

        # Simple scoring to pick which component to move next.
        def _score(c: dict) -> float:
            # Lower = worse placement = should move first.
            align_count = sum(1 for p in non_power
                              if p["reference"] != c["reference"]
                              and (abs(p["y"] - c["y"]) < KICAD_GRID_MM * 3
                                   or abs(p["x"] - c["x"]) < KICAD_GRID_MM * 3))
            nearest_dist = min((math.hypot(p["x"] - c["x"], p["y"] - c["y"])
                                for p in non_power if p["reference"] != c["reference"]),
                               default=100)
            return align_count / max(len(non_power), 1) + (1 if nearest_dist > 5 else 0)

        remaining.sort(key=_score)
        target = remaining[0]

        # Generate candidates.
        candidates = _generate_live_candidates(target, all_comps, KICAD_GRID_MM)

        # Build observation tensors.
        state_vec = _build_state_vec(state, step=step, max_steps=max_steps, moved_count=len(moved))
        sv_tensor = torch.tensor(state_vec, dtype=torch.float32).unsqueeze(0).to(device)
        step_tensor = torch.tensor([step / max_steps], dtype=torch.float32).to(device)

        cand_features = np.zeros((8, candidate_dim), dtype=np.float32)
        cand_mask = np.zeros(8, dtype=bool)
        for i, c in enumerate(candidates[:8]):
            cand_features[i] = c.features
            cand_mask[i] = True
            # Filter: reject moves that create overlaps.
            if c.label != "noop" and _would_overlap(target, c.dx, c.dy, all_comps):
                cand_mask[i] = False

        cf_tensor = torch.tensor(cand_features, dtype=torch.float32).unsqueeze(0).to(device)
        cm_tensor = torch.tensor(cand_mask, dtype=torch.bool).unsqueeze(0).to(device)

        # Run policy.
        with torch.no_grad():
            action, log_prob, value, entropy = policy.sample_action(
                sv_tensor, step_tensor, cf_tensor, cm_tensor)

        action_idx = action.item()
        chosen = candidates[min(action_idx, len(candidates) - 1)]
        confidence = torch.exp(log_prob).item()

        if verbose:
            print(f"\nStep {step}: {target['reference']} @ ({target['x']:.1f}, {target['y']:.1f})")
            print(f"  Policy chose: {chosen.label} (dx={chosen.dx:+.2f}, dy={chosen.dy:+.2f}) confidence={confidence:.2f}")
            for i, c in enumerate(candidates[:8]):
                marker = " <<<" if i == action_idx else ""
                valid = "OK" if (i < len(candidates) and cand_mask[i]) else "BLOCKED"
                print(f"    [{i}] {c.label:20s} dx={c.dx:+6.2f} dy={c.dy:+6.2f} [{valid}]{marker}")

        if chosen.label == "noop":
            moved.add(target["reference"])
            results.append(NNMoveResult(target["reference"], "noop", 0, 0, confidence, False))
            continue

        # Apply move via move_chunk.
        if not dry_run:
            move_result = _call_kicad(kicad_url, "move_chunk", {
                "references": [target["reference"]],
                "dx_mm": round(chosen.dx, 3),
                "dy_mm": round(chosen.dy, 3),
            })
            if verbose:
                print(f"  Applied: move_chunk({target['reference']}, dx={chosen.dx:+.2f}, dy={chosen.dy:+.2f})")

            # Update local state.
            target["x"] += chosen.dx
            target["y"] += chosen.dy
        else:
            if verbose:
                print(f"  [DRY RUN] Would move {target['reference']} by ({chosen.dx:+.2f}, {chosen.dy:+.2f})")

        moved.add(target["reference"])
        results.append(NNMoveResult(target["reference"], chosen.label, chosen.dx, chosen.dy, confidence, not dry_run))

    if verbose:
        applied = [r for r in results if r.applied]
        print(f"\n=== NN Optimization: {len(applied)} moves applied ===")
        for r in results:
            status = "MOVED" if r.applied else "SKIP"
            print(f"  [{status}] {r.reference}: {r.label} (dx={r.dx:+.2f}, dy={r.dy:+.2f}) conf={r.policy_confidence:.2f}")

    return results


if __name__ == "__main__":
    results = nn_optimize_layout(verbose=True, dry_run=True)
