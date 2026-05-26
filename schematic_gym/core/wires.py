"""Wire segments and junctions for SchematicGym."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field


@dataclass(slots=True)
class WireSegment:
    """An orthogonal (horizontal or vertical) wire on a schematic sheet.

    Invariants enforced in ``__post_init__``:
    - The segment must be axis-aligned (horizontal *or* vertical).
    - The segment must not be zero-length.
    """

    wire_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sheet_id: str = "default"
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    net_id: str | None = None

    def __post_init__(self) -> None:
        # Tolerance for floating-point grid alignment comparisons.
        eps = 1e-6

        is_h = abs(self.y1 - self.y2) < eps
        is_v = abs(self.x1 - self.x2) < eps

        if not (is_h or is_v):
            raise ValueError(
                f"Wire must be horizontal or vertical. "
                f"Got ({self.x1}, {self.y1}) -> ({self.x2}, {self.y2})"
            )

        # Zero-length check.
        if abs(self.x1 - self.x2) < eps and abs(self.y1 - self.y2) < eps:
            raise ValueError(
                f"Wire must not be zero-length. "
                f"Got ({self.x1}, {self.y1}) -> ({self.x2}, {self.y2})"
            )

    # -- derived properties ------------------------------------------------

    @property
    def is_horizontal(self) -> bool:
        return abs(self.y1 - self.y2) < 1e-6

    @property
    def is_vertical(self) -> bool:
        return abs(self.x1 - self.x2) < 1e-6

    @property
    def endpoints(self) -> list[tuple[float, float]]:
        """Return the two endpoints as ``[(x1, y1), (x2, y2)]``."""
        return [(self.x1, self.y1), (self.x2, self.y2)]

    @property
    def length(self) -> float:
        """Manhattan length of this segment (always positive)."""
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


@dataclass(slots=True)
class Junction:
    """An explicit junction dot where three or more wires meet."""

    junction_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sheet_id: str = "default"
    x: float = 0.0
    y: float = 0.0
