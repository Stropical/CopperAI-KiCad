"""Core data types for schematic data-pipeline generation and ranking jobs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from hashlib import sha256
from typing import Any, Dict, List, Mapping, Optional, Tuple

ISSUE_DISTANCE_INCREASE = "distance_increase"
ISSUE_ROTATION_AWKWARDNESS = "rotation_awkwardness"
ISSUE_ALIGNMENT_BREAK = "alignment_break"
ISSUE_SPREAD_GROUP = "spread_group"

ISSUE_TAGS: Tuple[str, ...] = (
    ISSUE_DISTANCE_INCREASE,
    ISSUE_ROTATION_AWKWARDNESS,
    ISSUE_ALIGNMENT_BREAK,
    ISSUE_SPREAD_GROUP,
)

ELECTRICAL_EDGE_TYPES: Tuple[str, ...] = (
    "symbol_has_pin",
    "pin_connected_to_wire",
    "wire_connected_to_junction",
    "label_attached_to_net",
)


def _to_primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {f.name: _to_primitive(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _to_primitive(v) for k, v in value.items()}
    if isinstance(value, set):
        return [_to_primitive(item) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [_to_primitive(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Serialize data into stable JSON form."""
    return json.dumps(_to_primitive(value), sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    """Return deterministic SHA256 hash over canonical JSON bytes."""
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ProjectMetadata:
    """Project-level provenance metadata."""

    project_id: str
    project_name: Optional[str] = None
    project_family: Optional[str] = None
    revision: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectMetadata":
        return cls(
            project_id=str(payload.get("project_id", "unknown_project")),
            project_name=_str_or_none(payload.get("project_name")),
            project_family=_str_or_none(payload.get("project_family")),
            revision=_str_or_none(payload.get("revision")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class SourceMetadata:
    """Source-corpus provenance metadata."""

    source_id: str
    source_type: str
    source_uri: Optional[str] = None
    snapshot_hash: Optional[str] = None
    commit_sha: Optional[str] = None
    generated_at_utc: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SourceMetadata":
        return cls(
            source_id=str(payload.get("source_id", "unknown_source")),
            source_type=str(payload.get("source_type", "unknown")),
            source_uri=_str_or_none(payload.get("source_uri")),
            snapshot_hash=_str_or_none(payload.get("snapshot_hash")),
            commit_sha=_str_or_none(payload.get("commit_sha")),
            generated_at_utc=_str_or_none(payload.get("generated_at_utc")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class SchematicNode:
    """Graph node with geometric placement fields."""

    node_id: str
    node_type: str
    x_mm: float
    y_mm: float
    width_mm: float = 0.0
    height_mm: float = 0.0
    rotation_deg: float = 0.0
    refdes: Optional[str] = None
    group_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SchematicNode":
        return cls(
            node_id=str(payload.get("node_id", payload.get("id", ""))),
            node_type=str(payload.get("node_type", payload.get("type", "symbol"))),
            x_mm=float(payload.get("x_mm", payload.get("x", 0.0))),
            y_mm=float(payload.get("y_mm", payload.get("y", 0.0))),
            width_mm=float(payload.get("width_mm", payload.get("w", 0.0))),
            height_mm=float(payload.get("height_mm", payload.get("h", 0.0))),
            rotation_deg=float(payload.get("rotation_deg", payload.get("rotation", 0.0))),
            refdes=_str_or_none(payload.get("refdes")),
            group_id=_str_or_none(payload.get("group_id")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class SchematicEdge:
    """Graph edge with optional electrical semantic flags."""

    edge_id: str
    src_node_id: str
    dst_node_id: str
    edge_type: str
    is_electrical: bool = True
    net_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SchematicEdge":
        src_node_id = str(payload.get("src_node_id", payload.get("src", payload.get("source", ""))))
        dst_node_id = str(payload.get("dst_node_id", payload.get("dst", payload.get("target", ""))))
        edge_type = str(payload.get("edge_type", payload.get("type", "near")))
        edge_id = str(payload.get("edge_id", f"{src_node_id}:{edge_type}:{dst_node_id}"))
        is_electrical = bool(payload.get("is_electrical", edge_type in ELECTRICAL_EDGE_TYPES))
        return cls(
            edge_id=edge_id,
            src_node_id=src_node_id,
            dst_node_id=dst_node_id,
            edge_type=edge_type,
            is_electrical=is_electrical,
            net_name=_str_or_none(payload.get("net_name")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class SchematicBlock:
    """Self-contained schematic block used by perturbation and ranking jobs."""

    block_id: str
    project_metadata: ProjectMetadata = field(
        default_factory=lambda: ProjectMetadata(project_id="unknown_project")
    )
    source_metadata: SourceMetadata = field(
        default_factory=lambda: SourceMetadata(source_id="unknown_source", source_type="unknown")
    )
    nodes: List[SchematicNode] = field(default_factory=list)
    edges: List[SchematicEdge] = field(default_factory=list)
    anchor_node_id: Optional[str] = None
    block_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    def node_index(self) -> Dict[str, SchematicNode]:
        return {node.node_id: node for node in self.nodes}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SchematicBlock":
        raw_project = payload.get("project_metadata", {})
        raw_source = payload.get("source_metadata", {})
        raw_nodes = payload.get("nodes", [])
        raw_edges = payload.get("edges", [])

        project = (
            raw_project
            if isinstance(raw_project, ProjectMetadata)
            else ProjectMetadata.from_dict(_as_mapping(raw_project))
        )
        source = (
            raw_source
            if isinstance(raw_source, SourceMetadata)
            else SourceMetadata.from_dict(_as_mapping(raw_source))
        )

        nodes = [node if isinstance(node, SchematicNode) else SchematicNode.from_dict(_as_mapping(node)) for node in raw_nodes]
        edges = [edge if isinstance(edge, SchematicEdge) else SchematicEdge.from_dict(_as_mapping(edge)) for edge in raw_edges]

        return cls(
            block_id=str(payload.get("block_id", "unknown_block")),
            project_metadata=project,
            source_metadata=source,
            nodes=nodes,
            edges=edges,
            anchor_node_id=_str_or_none(payload.get("anchor_node_id")),
            block_type=_str_or_none(payload.get("block_type")),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class RepairLabel:
    """Inverse edit target for repairing a perturbation."""

    action: str
    target_node_ids: List[str]
    anchor_node_id: Optional[str] = None
    delta_x_mm: float = 0.0
    delta_y_mm: float = 0.0
    delta_rotation_deg: float = 0.0
    reason: Optional[str] = None
    issue_tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RepairLabel":
        return cls(
            action=str(payload.get("action", "noop")),
            target_node_ids=[str(item) for item in payload.get("target_node_ids", [])],
            anchor_node_id=_str_or_none(payload.get("anchor_node_id")),
            delta_x_mm=float(payload.get("delta_x_mm", 0.0)),
            delta_y_mm=float(payload.get("delta_y_mm", 0.0)),
            delta_rotation_deg=float(payload.get("delta_rotation_deg", 0.0)),
            reason=_str_or_none(payload.get("reason")),
            issue_tags=[str(item) for item in payload.get("issue_tags", [])],
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class BlockPerturbation:
    """Materialized perturbation record and resulting block variant."""

    perturbation_id: str
    perturbation_type: str
    source_block_id: str
    perturbed_block: SchematicBlock
    repair_label: RepairLabel
    target_node_ids: List[str] = field(default_factory=list)
    issue_tags: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BlockPerturbation":
        raw_block = payload.get("perturbed_block", {})
        raw_repair = payload.get("repair_label", {})
        return cls(
            perturbation_id=str(payload.get("perturbation_id", "unknown_perturbation")),
            perturbation_type=str(payload.get("perturbation_type", "unknown")),
            source_block_id=str(payload.get("source_block_id", "unknown_block")),
            perturbed_block=(
                raw_block
                if isinstance(raw_block, SchematicBlock)
                else SchematicBlock.from_dict(_as_mapping(raw_block))
            ),
            repair_label=(
                raw_repair
                if isinstance(raw_repair, RepairLabel)
                else RepairLabel.from_dict(_as_mapping(raw_repair))
            ),
            target_node_ids=[str(item) for item in payload.get("target_node_ids", [])],
            issue_tags=[str(item) for item in payload.get("issue_tags", [])],
            parameters=dict(payload.get("parameters", {})),
            seed=int(payload.get("seed", 0)),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class RankingSample:
    """Pairwise ranking label from clean-vs-perturbed comparisons."""

    sample_id: str
    clean_block_id: str
    perturbed_block_id: str
    clean_score: float
    perturbed_score: float
    preferred_index: int
    pairwise_label: int
    issue_tags: List[str] = field(default_factory=list)
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RankingSample":
        return cls(
            sample_id=str(payload.get("sample_id", "unknown_ranking_sample")),
            clean_block_id=str(payload.get("clean_block_id", "unknown_block")),
            perturbed_block_id=str(payload.get("perturbed_block_id", "unknown_block")),
            clean_score=float(payload.get("clean_score", 0.0)),
            perturbed_score=float(payload.get("perturbed_score", 0.0)),
            preferred_index=int(payload.get("preferred_index", 0)),
            pairwise_label=int(payload.get("pairwise_label", 1)),
            issue_tags=[str(item) for item in payload.get("issue_tags", [])],
            reason=str(payload.get("reason", "")),
            metadata=dict(payload.get("metadata", {})),
        )


def clone_block(block: SchematicBlock) -> SchematicBlock:
    """Deep-copy a block via canonical serialization."""
    return SchematicBlock.from_dict(block.to_dict())


def electrical_connectivity_signature(block: SchematicBlock) -> Tuple[Tuple[str, str, str], ...]:
    """Build canonical signature of electrical connectivity for invariance checks."""
    triples: List[Tuple[str, str, str]] = []
    for edge in block.edges:
        if edge.is_electrical or edge.edge_type in ELECTRICAL_EDGE_TYPES:
            src, dst = sorted((edge.src_node_id, edge.dst_node_id))
            key = edge.net_name if edge.net_name else edge.edge_type
            triples.append((src, dst, key))
    triples.sort()
    return tuple(triples)


def block_fingerprint(block: SchematicBlock) -> str:
    """Deterministic hash for block content and geometry."""
    return stable_hash(block.to_dict())


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    as_str = str(value)
    if not as_str:
        return None
    return as_str


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


__all__ = [
    "ISSUE_DISTANCE_INCREASE",
    "ISSUE_ROTATION_AWKWARDNESS",
    "ISSUE_ALIGNMENT_BREAK",
    "ISSUE_SPREAD_GROUP",
    "ISSUE_TAGS",
    "ELECTRICAL_EDGE_TYPES",
    "ProjectMetadata",
    "SourceMetadata",
    "SchematicNode",
    "SchematicEdge",
    "SchematicBlock",
    "RepairLabel",
    "BlockPerturbation",
    "RankingSample",
    "canonical_json",
    "stable_hash",
    "clone_block",
    "electrical_connectivity_signature",
    "block_fingerprint",
]
