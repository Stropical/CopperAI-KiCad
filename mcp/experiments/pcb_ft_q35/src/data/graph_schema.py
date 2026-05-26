"""Data schema and deterministic feature bucketing for schematic graph windows."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from enum import Enum
from math import floor, isfinite
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class NodeType(str, Enum):
    """Canonical heterogeneous node types for local-window graphs."""

    SYMBOL = "symbol"
    PIN = "pin"
    JUNCTION = "junction"
    NET_LABEL = "net_label"
    POWER_SYMBOL = "power_symbol"
    WIRE_SEGMENT = "wire_segment"
    BLOCK = "block"


class EdgeType(str, Enum):
    """Canonical heterogeneous edge types for local-window graphs."""

    SYMBOL_HAS_PIN = "symbol_has_pin"
    PIN_CONNECTED_TO_WIRE = "pin_connected_to_wire"
    WIRE_CONNECTED_TO_JUNCTION = "wire_connected_to_junction"
    LABEL_ATTACHED_TO_NET = "label_attached_to_net"
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    ABOVE = "above"
    BELOW = "below"
    NEAR = "near"
    INLINE_WITH = "inline_with"
    SAME_BLOCK = "same_block"


NODE_TYPE_VALUES: Tuple[str, ...] = tuple(member.value for member in NodeType)
EDGE_TYPE_VALUES: Tuple[str, ...] = tuple(member.value for member in EdgeType)
NODE_TYPE_TO_ID: Dict[str, int] = {name: idx for idx, name in enumerate(NODE_TYPE_VALUES)}
EDGE_TYPE_TO_ID: Dict[str, int] = {name: idx for idx, name in enumerate(EDGE_TYPE_VALUES)}


# Deterministic default bucketing constants.
GEOMETRY_BUCKET_SIZE_MM = 1.0
GEOMETRY_MIN_BUCKET = -256
GEOMETRY_MAX_BUCKET = 255
DISTANCE_BUCKET_BOUNDS_MM: Tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)
ANGLE_BUCKET_COUNT = 16


@dataclass(slots=True)
class NodeRecord:
    """Single graph node record for a local schematic window."""

    node_id: int
    node_type: str
    x_mm: float
    y_mm: float
    width_mm: float = 0.0
    height_mm: float = 0.0
    rotation_deg: float = 0.0
    refdes: Optional[str] = None
    text: Optional[str] = None
    symbol_class: Optional[str] = None
    electrical_role: Optional[str] = None
    pin_side: Optional[str] = None
    pin_order: Optional[int] = None
    window_id: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EdgeRecord:
    """Single typed relation between two nodes in a local window."""

    src_node_id: int
    dst_node_id: int
    edge_type: str
    distance_mm: Optional[float] = None
    angle_deg: Optional[float] = None
    is_orthogonal: Optional[bool] = None
    net_class: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WindowMetadata:
    """Window-level metadata used for dataset and traceability fields."""

    sample_id: str
    center_ref: str
    radius_mm: float
    page_index: Optional[int] = None
    source: Optional[str] = None
    quality_score: Optional[float] = None
    dataset_version: Optional[str] = None
    task_type: Optional[str] = None
    globals: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphWindowSample:
    """Complete local-window graph sample."""

    metadata: WindowMetadata
    nodes: List[NodeRecord]
    edges: List[EdgeRecord]
    target: Optional[Mapping[str, Any]] = None


def bucketize_geometry_mm(
    value_mm: float,
    bucket_size_mm: float = GEOMETRY_BUCKET_SIZE_MM,
    min_bucket: int = GEOMETRY_MIN_BUCKET,
    max_bucket: int = GEOMETRY_MAX_BUCKET,
) -> int:
    """Bucketize a geometry scalar (x/y/w/h) into a clamped integer bucket."""
    if bucket_size_mm <= 0:
        raise ValueError("bucket_size_mm must be > 0")
    if not isfinite(value_mm):
        return 0
    raw_bucket = int(floor(value_mm / bucket_size_mm))
    return max(min_bucket, min(max_bucket, raw_bucket))


def bucketize_distance_mm(
    distance_mm: float,
    bounds_mm: Sequence[float] = DISTANCE_BUCKET_BOUNDS_MM,
) -> int:
    """Bucketize distance into interval index using monotonic upper bounds."""
    if not bounds_mm:
        raise ValueError("bounds_mm must not be empty")
    if not isfinite(distance_mm):
        return len(bounds_mm)
    bounded = max(0.0, distance_mm)
    return bisect_left(tuple(bounds_mm), bounded)


def bucketize_angle_deg(angle_deg: float, num_buckets: int = ANGLE_BUCKET_COUNT) -> int:
    """Bucketize an angle in degrees into [0, num_buckets)."""
    if num_buckets <= 0:
        raise ValueError("num_buckets must be > 0")
    if not isfinite(angle_deg):
        return 0
    normalized = angle_deg % 360.0
    bucket_width = 360.0 / float(num_buckets)
    bucket = int(normalized // bucket_width)
    return min(bucket, num_buckets - 1)


def bucketize_rotation_deg(rotation_deg: float) -> int:
    """Map rotation to one of four 90-degree buckets."""
    if not isfinite(rotation_deg):
        return 0
    normalized = rotation_deg % 360.0
    return int((normalized + 45.0) // 90.0) % 4


def validate_sample(sample: GraphWindowSample) -> List[str]:
    """Validate core schema constraints and return a list of human-readable errors."""
    errors: List[str] = []
    if not isinstance(sample, GraphWindowSample):
        return ["sample must be GraphWindowSample"]

    metadata = sample.metadata
    if not metadata.sample_id:
        errors.append("metadata.sample_id must be non-empty")
    if not metadata.center_ref:
        errors.append("metadata.center_ref must be non-empty")
    if metadata.radius_mm <= 0:
        errors.append("metadata.radius_mm must be > 0")
    if metadata.quality_score is not None and not (0.0 <= metadata.quality_score <= 1.0):
        errors.append("metadata.quality_score must be in [0, 1]")

    seen_ids = set()
    has_center_ref = False
    for idx, node in enumerate(sample.nodes):
        if node.node_id in seen_ids:
            errors.append(f"nodes[{idx}] duplicate node_id={node.node_id}")
        seen_ids.add(node.node_id)

        if node.node_type not in NODE_TYPE_TO_ID:
            errors.append(f"nodes[{idx}] invalid node_type='{node.node_type}'")

        if node.refdes == metadata.center_ref:
            has_center_ref = True

        for field_name, value in (
            ("x_mm", node.x_mm),
            ("y_mm", node.y_mm),
            ("width_mm", node.width_mm),
            ("height_mm", node.height_mm),
            ("rotation_deg", node.rotation_deg),
        ):
            if not isfinite(value):
                errors.append(f"nodes[{idx}].{field_name} must be finite")

        if node.width_mm < 0:
            errors.append(f"nodes[{idx}].width_mm must be >= 0")
        if node.height_mm < 0:
            errors.append(f"nodes[{idx}].height_mm must be >= 0")

    if sample.nodes and metadata.center_ref and not has_center_ref:
        errors.append("metadata.center_ref not present in any node.refdes")

    for idx, edge in enumerate(sample.edges):
        if edge.edge_type not in EDGE_TYPE_TO_ID:
            errors.append(f"edges[{idx}] invalid edge_type='{edge.edge_type}'")
        if edge.src_node_id not in seen_ids:
            errors.append(f"edges[{idx}] src_node_id={edge.src_node_id} not in nodes")
        if edge.dst_node_id not in seen_ids:
            errors.append(f"edges[{idx}] dst_node_id={edge.dst_node_id} not in nodes")

        if edge.distance_mm is not None and not isfinite(edge.distance_mm):
            errors.append(f"edges[{idx}].distance_mm must be finite when provided")
        if edge.angle_deg is not None and not isfinite(edge.angle_deg):
            errors.append(f"edges[{idx}].angle_deg must be finite when provided")

    if sample.target is not None and not isinstance(sample.target, Mapping):
        errors.append("target must be a mapping when provided")

    return errors
