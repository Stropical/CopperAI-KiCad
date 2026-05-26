"""Synthetic schematic perturbation job for layout-quality training data."""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..types import (
    BlockPerturbation,
    RepairLabel,
    SchematicBlock,
    SchematicNode,
    ISSUE_ALIGNMENT_BREAK,
    ISSUE_DISTANCE_INCREASE,
    ISSUE_ROTATION_AWKWARDNESS,
    ISSUE_SPREAD_GROUP,
    clone_block,
    electrical_connectivity_signature,
    stable_hash,
)

PerturbationResult = Tuple[SchematicBlock, RepairLabel, List[str], List[str], Dict[str, Any]]

DEFAULT_PERTURBATION_TYPES: Tuple[str, ...] = (
    ISSUE_DISTANCE_INCREASE,
    ISSUE_ROTATION_AWKWARDNESS,
    ISSUE_ALIGNMENT_BREAK,
    ISSUE_SPREAD_GROUP,
)


class PerturbationEngine:
    """Generates deterministic geometry-only perturbations for a block."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = int(seed)

    def generate(
        self,
        block: SchematicBlock,
        perturbation_types: Optional[Sequence[str]] = None,
        max_variants: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> List[BlockPerturbation]:
        """Generate perturbations while preserving electrical connectivity."""
        kinds = tuple(perturbation_types) if perturbation_types else DEFAULT_PERTURBATION_TYPES
        if max_variants is not None and max_variants < 0:
            raise ValueError("max_variants must be >= 0 when provided")

        active_seed = self.seed if seed is None else int(seed)
        rng = random.Random(active_seed)
        base_signature = electrical_connectivity_signature(block)

        handlers: Dict[str, Callable[[SchematicBlock, random.Random], Optional[PerturbationResult]]] = {
            ISSUE_DISTANCE_INCREASE: self._distance_increase,
            ISSUE_ROTATION_AWKWARDNESS: self._rotation_awkwardness,
            ISSUE_ALIGNMENT_BREAK: self._alignment_break,
            ISSUE_SPREAD_GROUP: self._spread_group,
        }

        records: List[BlockPerturbation] = []
        for kind in kinds:
            if max_variants is not None and len(records) >= max_variants:
                break
            handler = handlers.get(kind)
            if handler is None:
                continue
            result = handler(block, rng)
            if result is None:
                continue

            perturbed, repair, issue_tags, target_node_ids, parameters = result
            if electrical_connectivity_signature(perturbed) != base_signature:
                raise ValueError(f"perturbation '{kind}' changed electrical connectivity")

            record = self._build_record(
                source_block=block,
                perturbed_block=perturbed,
                perturbation_type=kind,
                repair_label=repair,
                issue_tags=issue_tags,
                target_node_ids=target_node_ids,
                parameters=parameters,
                seed=active_seed,
            )
            records.append(record)

        return records

    def _build_record(
        self,
        source_block: SchematicBlock,
        perturbed_block: SchematicBlock,
        perturbation_type: str,
        repair_label: RepairLabel,
        issue_tags: Sequence[str],
        target_node_ids: Sequence[str],
        parameters: Dict[str, Any],
        seed: int,
    ) -> BlockPerturbation:
        suffix = stable_hash(
            {
                "source": source_block.block_id,
                "type": perturbation_type,
                "targets": list(target_node_ids),
                "params": parameters,
            }
        )[:10]
        perturbed_block.block_id = f"{source_block.block_id}__{perturbation_type}_{suffix}"

        perturbation_id = "ptb_" + stable_hash(
            {
                "source": source_block.block_id,
                "perturbed": perturbed_block.block_id,
                "type": perturbation_type,
                "repair": repair_label.to_dict(),
                "tags": sorted(set(issue_tags)),
            }
        )[:14]

        return BlockPerturbation(
            perturbation_id=perturbation_id,
            perturbation_type=perturbation_type,
            source_block_id=source_block.block_id,
            perturbed_block=perturbed_block,
            repair_label=repair_label,
            issue_tags=sorted(set(issue_tags)),
            target_node_ids=sorted(set(str(node_id) for node_id in target_node_ids)),
            parameters=dict(parameters),
            seed=int(seed),
        )

    def _distance_increase(self, block: SchematicBlock, rng: random.Random) -> Optional[PerturbationResult]:
        perturbed = clone_block(block)
        candidates = self._placement_nodes(perturbed)
        if len(candidates) < 2:
            return None

        node_map = perturbed.node_index()
        anchor = self._choose_anchor(perturbed, candidates)
        target_candidates = [node for node in candidates if node.node_id != anchor.node_id]
        target = min(target_candidates, key=lambda node: self._distance(node, anchor))

        delta_mm = rng.uniform(4.0, 12.0)
        dx = target.x_mm - anchor.x_mm
        dy = target.y_mm - anchor.y_mm
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            angle = rng.uniform(0.0, 2.0 * math.pi)
            ux = math.cos(angle)
            uy = math.sin(angle)
        else:
            ux = dx / length
            uy = dy / length

        shift_x = ux * delta_mm
        shift_y = uy * delta_mm

        target_ref = node_map[target.node_id]
        target_ref.x_mm += shift_x
        target_ref.y_mm += shift_y

        repair = RepairLabel(
            action="move_symbol",
            target_node_ids=[target.node_id],
            anchor_node_id=anchor.node_id,
            delta_x_mm=-shift_x,
            delta_y_mm=-shift_y,
            reason="undo_distance_increase",
            issue_tags=[ISSUE_DISTANCE_INCREASE],
        )

        issue_tags = [ISSUE_DISTANCE_INCREASE, "decoupler_distance"]
        parameters = {
            "delta_mm": delta_mm,
            "shift_x_mm": shift_x,
            "shift_y_mm": shift_y,
            "anchor_node_id": anchor.node_id,
        }
        return perturbed, repair, issue_tags, [target.node_id], parameters

    def _rotation_awkwardness(
        self,
        block: SchematicBlock,
        rng: random.Random,
    ) -> Optional[PerturbationResult]:
        perturbed = clone_block(block)
        candidates = self._placement_nodes(perturbed)
        if not candidates:
            return None

        node_map = perturbed.node_index()
        connector_like = [
            node
            for node in candidates
            if "conn" in (node.refdes or "").lower() or "connector" in node.node_type.lower()
        ]
        target = connector_like[0] if connector_like else candidates[0]

        target_ref = node_map[target.node_id]
        old_rotation = target_ref.rotation_deg % 360.0
        nearest_cardinal = round(old_rotation / 90.0) * 90.0
        offset = rng.choice([30.0, 45.0, 60.0])
        new_rotation = (nearest_cardinal + offset) % 360.0
        if abs(new_rotation - old_rotation) < 1e-6:
            new_rotation = (nearest_cardinal - offset) % 360.0

        target_ref.rotation_deg = new_rotation
        inverse_delta = ((old_rotation - new_rotation + 540.0) % 360.0) - 180.0

        repair = RepairLabel(
            action="rotate_symbol",
            target_node_ids=[target.node_id],
            delta_rotation_deg=inverse_delta,
            reason="undo_rotation_awkwardness",
            issue_tags=[ISSUE_ROTATION_AWKWARDNESS],
        )

        issue_tags = [ISSUE_ROTATION_AWKWARDNESS, "connector_rotation"]
        parameters = {
            "old_rotation_deg": old_rotation,
            "new_rotation_deg": new_rotation,
            "nearest_cardinal_deg": nearest_cardinal % 360.0,
        }
        return perturbed, repair, issue_tags, [target.node_id], parameters

    def _alignment_break(self, block: SchematicBlock, rng: random.Random) -> Optional[PerturbationResult]:
        perturbed = clone_block(block)
        candidates = self._placement_nodes(perturbed)
        if len(candidates) < 2:
            return None

        node_map = perturbed.node_index()
        axis, anchor, target = self._find_alignment_pair(candidates)
        target_ref = node_map[target.node_id]

        delta_mm = rng.uniform(2.0, 6.0)
        sign = rng.choice([-1.0, 1.0])
        shift_x = 0.0
        shift_y = 0.0

        if axis == "x":
            shift_x = sign * delta_mm
            target_ref.x_mm += shift_x
        else:
            shift_y = sign * delta_mm
            target_ref.y_mm += shift_y

        repair = RepairLabel(
            action="align_symbol",
            target_node_ids=[target.node_id],
            anchor_node_id=anchor.node_id,
            delta_x_mm=-shift_x,
            delta_y_mm=-shift_y,
            reason="undo_alignment_break",
            issue_tags=[ISSUE_ALIGNMENT_BREAK],
        )

        issue_tags = [ISSUE_ALIGNMENT_BREAK, "alignment"]
        parameters = {
            "axis": axis,
            "delta_mm": delta_mm,
            "shift_x_mm": shift_x,
            "shift_y_mm": shift_y,
            "anchor_node_id": anchor.node_id,
        }
        return perturbed, repair, issue_tags, [target.node_id], parameters

    def _spread_group(self, block: SchematicBlock, rng: random.Random) -> Optional[PerturbationResult]:
        perturbed = clone_block(block)
        candidates = self._placement_nodes(perturbed)
        if len(candidates) < 3:
            return None

        node_map = perturbed.node_index()
        group = candidates
        cx = sum(node.x_mm for node in group) / len(group)
        cy = sum(node.y_mm for node in group) / len(group)
        scale = rng.uniform(1.3, 1.8)

        per_node_inverse: Dict[str, Dict[str, float]] = {}
        for node in group:
            target_ref = node_map[node.node_id]
            dx = target_ref.x_mm - cx
            dy = target_ref.y_mm - cy
            if abs(dx) + abs(dy) <= 1e-9:
                angle = rng.uniform(0.0, 2.0 * math.pi)
                dx = math.cos(angle)
                dy = math.sin(angle)

            shift_x = dx * (scale - 1.0)
            shift_y = dy * (scale - 1.0)
            target_ref.x_mm += shift_x
            target_ref.y_mm += shift_y
            per_node_inverse[node.node_id] = {
                "delta_x_mm": -shift_x,
                "delta_y_mm": -shift_y,
            }

        repair = RepairLabel(
            action="compact_group",
            target_node_ids=[node.node_id for node in group],
            delta_x_mm=0.0,
            delta_y_mm=0.0,
            reason="undo_spread_group",
            issue_tags=[ISSUE_SPREAD_GROUP],
            metadata={
                "inverse_scale": 1.0 / scale,
                "per_node_delta": per_node_inverse,
            },
        )

        issue_tags = [ISSUE_SPREAD_GROUP]
        parameters = {
            "scale": scale,
            "centroid_x_mm": cx,
            "centroid_y_mm": cy,
            "group_size": len(group),
        }
        target_ids = [node.node_id for node in group]
        return perturbed, repair, issue_tags, target_ids, parameters

    def _placement_nodes(self, block: SchematicBlock) -> List[SchematicNode]:
        preferred = [
            node
            for node in block.nodes
            if node.node_type not in {"pin", "wire_segment", "junction"}
        ]
        nodes = preferred if preferred else list(block.nodes)
        return sorted(nodes, key=lambda node: node.node_id)

    def _choose_anchor(self, block: SchematicBlock, nodes: Sequence[SchematicNode]) -> SchematicNode:
        node_map = block.node_index()
        if block.anchor_node_id and block.anchor_node_id in node_map:
            return node_map[block.anchor_node_id]
        return sorted(nodes, key=lambda node: node.node_id)[0]

    def _find_alignment_pair(
        self,
        nodes: Sequence[SchematicNode],
    ) -> Tuple[str, SchematicNode, SchematicNode]:
        tolerance = 0.75
        best: Optional[Tuple[str, float, SchematicNode, SchematicNode]] = None

        ordered = sorted(nodes, key=lambda node: node.node_id)
        for idx, left in enumerate(ordered):
            for right in ordered[idx + 1 :]:
                dx = abs(left.x_mm - right.x_mm)
                dy = abs(left.y_mm - right.y_mm)
                if dx <= tolerance:
                    candidate = ("x", dx, left, right)
                    if best is None or candidate[1] < best[1]:
                        best = candidate
                if dy <= tolerance:
                    candidate = ("y", dy, left, right)
                    if best is None or candidate[1] < best[1]:
                        best = candidate

        if best is not None:
            return best[0], best[2], best[3]

        if len(ordered) < 2:
            raise ValueError("alignment break requires at least two nodes")
        first, second = ordered[0], ordered[1]
        axis = "x" if abs(first.x_mm - second.x_mm) <= abs(first.y_mm - second.y_mm) else "y"
        return axis, first, second

    def _distance(self, a: SchematicNode, b: SchematicNode) -> float:
        return math.hypot(a.x_mm - b.x_mm, a.y_mm - b.y_mm)


def generate_layout_perturbations(
    block: SchematicBlock,
    seed: int = 0,
    perturbation_types: Optional[Sequence[str]] = None,
    max_variants: Optional[int] = None,
) -> List[BlockPerturbation]:
    """Convenience wrapper for deterministic perturbation generation."""
    engine = PerturbationEngine(seed=seed)
    return engine.generate(
        block=block,
        perturbation_types=perturbation_types,
        max_variants=max_variants,
        seed=seed,
    )


__all__ = [
    "DEFAULT_PERTURBATION_TYPES",
    "PerturbationEngine",
    "generate_layout_perturbations",
]
