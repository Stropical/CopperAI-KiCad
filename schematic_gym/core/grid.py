"""Grid snapping, 2D transforms, and bounding box utilities for SchematicGym."""

from __future__ import annotations

import math
from dataclasses import dataclass


class GridSnap:
    """Snap coordinates to a regular grid (default 2.54 mm / 100 mil)."""

    def __init__(self, grid_size: float = 2.54) -> None:
        if grid_size <= 0:
            raise ValueError(f"grid_size must be positive, got {grid_size}")
        self.grid_size = grid_size

    def snap(self, value: float) -> float:
        """Round *value* to the nearest grid point."""
        return round(value / self.grid_size) * self.grid_size

    def snap_point(self, x: float, y: float) -> tuple[float, float]:
        """Snap both coordinates to the grid."""
        return self.snap(x), self.snap(y)

    def is_on_grid(self, value: float) -> bool:
        """Return True if *value* is within floating-point tolerance of a grid point."""
        remainder = abs(math.remainder(value, self.grid_size))
        return remainder < 1e-6


@dataclass(frozen=True, slots=True)
class Transform2D:
    """2x2 integer rotation/mirror matrix.

    The transform encodes rotation and mirroring of symbol-local
    coordinates into world coordinates:

        world_x = x1 * local_x + y1 * local_y
        world_y = x2 * local_x + y2 * local_y

    (Translation by the instance position is applied by the caller.)
    """

    x1: int
    y1: int
    x2: int
    y2: int

    # -- core operations --------------------------------------------------

    def apply(self, local_x: float, local_y: float) -> tuple[float, float]:
        """Map a local-coordinate point through this transform."""
        return (
            self.x1 * local_x + self.y1 * local_y,
            self.x2 * local_x + self.y2 * local_y,
        )

    # -- factory methods ---------------------------------------------------

    @classmethod
    def from_rotation(cls, angle: int) -> Transform2D:
        """Create a transform for *angle* degrees (0, 90, 180, 270).

        The matrices include the Y-axis flip required to convert from
        KiCad symbol-library coordinates (Y-up) to schematic world
        coordinates (Y-down).  Derived from kicanvas analysis.
        """
        _ROTATION_TABLE: dict[int, tuple[int, int, int, int]] = {
            0: (1, 0, 0, -1),
            90: (0, -1, -1, 0),
            180: (-1, 0, 0, 1),
            270: (0, 1, 1, 0),
        }
        try:
            x1, y1, x2, y2 = _ROTATION_TABLE[angle]
        except KeyError:
            raise ValueError(
                f"angle must be one of 0, 90, 180, 270 -- got {angle}"
            ) from None
        return cls(x1=x1, y1=y1, x2=x2, y2=y2)

    def with_mirror(self, mirror_x: bool = False, mirror_y: bool = False) -> Transform2D:
        """Return a new transform with optional axis mirroring applied.

        KiCad/kicanvas convention:
        - mirror_x (vertical flip, KiCad ``mirror "x"``): negate x2, y2
        - mirror_y (horizontal flip, KiCad ``mirror "y"``): negate x1, y1
        """
        x1, y1, x2, y2 = self.x1, self.y1, self.x2, self.y2
        if mirror_x:
            x2 = -x2
            y2 = -y2
        if mirror_y:
            x1 = -x1
            y1 = -y1
        return Transform2D(x1=x1, y1=y1, x2=x2, y2=y2)


@dataclass(slots=True)
class BBox:
    """Axis-aligned bounding box in millimetres."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.min_x > self.max_x:
            self.min_x, self.max_x = self.max_x, self.min_x
        if self.min_y > self.max_y:
            self.min_y, self.max_y = self.max_y, self.min_y

    # -- properties --------------------------------------------------------

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    # -- query methods -----------------------------------------------------

    def center(self) -> tuple[float, float]:
        """Return the centre point of the box."""
        return (
            (self.min_x + self.max_x) / 2.0,
            (self.min_y + self.max_y) / 2.0,
        )

    def contains(self, x: float, y: float) -> bool:
        """True if *(x, y)* lies inside or on the boundary."""
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y

    def overlaps(self, other: BBox) -> bool:
        """True if this box overlaps *other* (inclusive of touching edges)."""
        return (
            self.min_x <= other.max_x
            and self.max_x >= other.min_x
            and self.min_y <= other.max_y
            and self.max_y >= other.min_y
        )

    def expand(self, margin: float) -> BBox:
        """Return a new box expanded by *margin* on every side."""
        return BBox(
            min_x=self.min_x - margin,
            min_y=self.min_y - margin,
            max_x=self.max_x + margin,
            max_y=self.max_y + margin,
        )
