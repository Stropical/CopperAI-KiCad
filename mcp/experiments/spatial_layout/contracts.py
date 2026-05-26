from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PageConstraints:
    anchor_xy_mm: Tuple[float, float] = (80.0, 80.0)
    page_bbox_mm: Optional[Tuple[float, float, float, float]] = None
    preferred_region: Optional[str] = None
    max_size_mm: Optional[Tuple[float, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalPin:
    num: str
    name: str = ""
    net: str = ""
    x: Optional[float] = None
    y: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalComponent:
    component_id: str
    ref: str
    lib_id: str
    value: str = ""
    footprint: str = ""
    x_mm: float = 0.0
    y_mm: float = 0.0
    rotation_deg: float = 0.0
    mirror: str = "none"
    pins: List[CanonicalPin] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "pins": [pin.to_dict() for pin in self.pins],
        }


@dataclass
class CanonicalNet:
    name: str
    pins: List[str] = field(default_factory=list)
    net_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanonicalBlockSpec:
    components: List[CanonicalComponent] = field(default_factory=list)
    nets: List[CanonicalNet] = field(default_factory=list)
    source: Optional[str] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "components": [component.to_dict() for component in self.components],
            "nets": [net.to_dict() for net in self.nets],
            "source": self.source,
            "name": self.name,
        }


@dataclass
class PlacementComponent:
    ref: str
    x_mm: float
    y_mm: float
    component_id: Optional[str] = None
    rotation_deg: int = 0
    mirror: str = "none"
    anchor_ref: Optional[str] = None
    anchor_component_id: Optional[str] = None
    anchor_pin: Optional[str] = None
    group_id: Optional[str] = None
    local_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlacementIssue:
    code: str
    message: str
    severity: str = "error"
    ref: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlacementResult:
    components: List[PlacementComponent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"components": [component.to_dict() for component in self.components]}


@dataclass
class PlacementRequest:
    ir: Dict[str, Any]
    scope: Dict[str, Any]
    block: Optional[CanonicalBlockSpec] = None
    scope_component_ids: List[str] = field(default_factory=list)
    locked_component_ids: List[str] = field(default_factory=list)
    fixed_context_component_ids: List[str] = field(default_factory=list)
    locked_refs: List[str] = field(default_factory=list)
    fixed_context_refs: List[str] = field(default_factory=list)
    page_constraints: PageConstraints = field(default_factory=PageConstraints)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["block"] = self.block.to_dict() if self.block else None
        out["page_constraints"] = self.page_constraints.to_dict()
        return out
