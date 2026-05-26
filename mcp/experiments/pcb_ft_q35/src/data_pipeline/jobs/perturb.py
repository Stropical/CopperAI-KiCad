"""Record-based wrapper for job 4: synthetic perturbation + repair labels."""

from __future__ import annotations

from typing import Any, Sequence

from ..types import ProjectMetadata, SchematicBlock, SchematicEdge, SchematicNode, SourceMetadata
from .perturbation_engine import PerturbationEngine


def _as_block(record: dict[str, Any]) -> SchematicBlock:
    if "block" in record and isinstance(record["block"], dict):
        return SchematicBlock.from_dict(record["block"])
    if "normalized_graph" in record and isinstance(record["normalized_graph"], dict):
        return SchematicBlock.from_dict(record["normalized_graph"])
    if "nodes" in record and "edges" in record:
        return _block_from_extracted_record(record)
    return SchematicBlock.from_dict(record)


def _block_from_extracted_record(record: dict[str, Any]) -> SchematicBlock:
    nodes_raw = record.get("nodes", [])
    edges_raw = record.get("edges", [])

    nodes: list[SchematicNode] = []
    for raw in nodes_raw:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("node_id", raw.get("id", "")))
        node_type = str(raw.get("node_type", raw.get("type", "unknown")))
        x = raw.get("x_mm", raw.get("x", 0.0))
        y = raw.get("y_mm", raw.get("y", 0.0))
        nodes.append(
            SchematicNode(
                node_id=node_id,
                node_type=node_type,
                x_mm=float(x),
                y_mm=float(y),
                rotation_deg=float(raw.get("rotation_deg", 0.0)),
                refdes=(str(raw.get("label")) if raw.get("label") else None),
                metadata={
                    "symbol_class": raw.get("symbol_class"),
                    "attrs": raw.get("attrs", {}),
                },
            )
        )

    edges: list[SchematicEdge] = []
    for raw in edges_raw:
        if not isinstance(raw, dict):
            continue
        src = str(raw.get("src", raw.get("src_node_id", "")))
        dst = str(raw.get("dst", raw.get("dst_node_id", "")))
        edge_type = str(raw.get("edge_type", raw.get("type", "unknown")))
        edges.append(
            SchematicEdge(
                edge_id=str(raw.get("edge_id", f"{src}:{edge_type}:{dst}")),
                src_node_id=src,
                dst_node_id=dst,
                edge_type=edge_type,
                is_electrical=edge_type
                in {
                    "symbol_has_pin",
                    "pin_connected_to_wire",
                    "wire_connected_to_junction",
                    "label_attached_to_net",
                    "wire_segment",
                },
                metadata={"attrs": raw.get("attrs", {})},
            )
        )

    return SchematicBlock(
        block_id=str(record.get("block_id", "unknown_block")),
        project_metadata=ProjectMetadata(
            project_id=str(record.get("project_id", "unknown_project")),
            project_name=str(record.get("project_root", "")) or None,
        ),
        source_metadata=SourceMetadata(
            source_id=str(record.get("schematic_path", "unknown_source")),
            source_type="kicad_schematic",
            source_uri=str(record.get("schematic_path", "")) or None,
        ),
        nodes=nodes,
        edges=edges,
        anchor_node_id=(str(record.get("anchor_node_id")) if record.get("anchor_node_id") else None),
        block_type=(str(record.get("block_type")) if record.get("block_type") else None),
        metadata={
            "family_id": record.get("family_id"),
            "canonical_signature": record.get("canonical_signature"),
            "traceability": record.get("traceability", {}),
        },
    )


def _positions(block: SchematicBlock) -> dict[str, dict[str, float]]:
    return {
        node.node_id: {
            "x_mm": float(node.x_mm),
            "y_mm": float(node.y_mm),
            "rotation_deg": float(node.rotation_deg),
        }
        for node in block.nodes
    }


def _parse_perturbation_types(raw: Any) -> Sequence[str] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        items = [item.strip() for item in raw.split(",")]
        return [item for item in items if item]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if str(item).strip()]
    return None


def run(
    records: list[dict[str, Any]],
    seed: int = 0,
    max_variants: int | None = None,
    perturbation_types: Any = None,
) -> list[dict[str, Any]]:
    """Create perturbed variants with inverse repair labels."""
    engine = PerturbationEngine(seed=seed)
    kinds = _parse_perturbation_types(perturbation_types)

    output: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        source_block = _as_block(record)
        perturbations = engine.generate(
            block=source_block,
            perturbation_types=kinds,
            max_variants=max_variants,
            seed=seed,
        )

        for perturbation in perturbations:
            output.append(
                {
                    "sample_type": "perturbation",
                    "block_id": perturbation.perturbed_block.block_id,
                    "family_id": source_block.metadata.get("family_id"),
                    "source_project": source_block.project_metadata.project_id,
                    "source_block_id": source_block.block_id,
                    "normalized_graph": perturbation.perturbed_block.to_dict(),
                    "original_positions": _positions(source_block),
                    "score": None,
                    "issue_tags": perturbation.issue_tags,
                    "perturbation_history": [perturbation.perturbation_type],
                    "repair_label": perturbation.repair_label.to_dict(),
                    "perturbation": perturbation.to_dict(),
                    "source_block": source_block.to_dict(),
                    "perturbed_block": perturbation.perturbed_block.to_dict(),
                }
            )

    output.sort(key=lambda item: (str(item.get("family_id", "")), str(item.get("block_id", ""))))
    return output


__all__ = ["run"]
