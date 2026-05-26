"""Individual ERC check functions for SchematicGym.

Each public function returns ``list[ERCViolation]``.  Functions accept the
resolved ``sheet``, ``nets``, ``symbol_library``, and (optionally) a
``grid`` helper.  They are designed to be called from :func:`engine.run_erc`.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from itertools import combinations

from ..core.grid import GridSnap
from ..core.labels import GlobalLabel, NetLabel, PowerSymbol
from ..core.nets import Net
from ..core.project import ERCViolation, Sheet
from ..core.symbols import PinType, SymbolDef
from .pin_matrix import (
    DRIVEN_PIN_TYPES,
    DRIVING_PIN_TYPES,
    POWER_DRIVING_PIN_TYPES,
    check_pin_pair,
)

# Floating-point tolerance for coordinate comparisons (mm).
_EPS = 1e-4


def _pos_eq(a: float, b: float) -> bool:
    """Return *True* if two coordinates are equal within tolerance."""
    return abs(a - b) < _EPS


def _pos_match(x1: float, y1: float, x2: float, y2: float) -> bool:
    """Return *True* if two points are coincident within tolerance."""
    return _pos_eq(x1, x2) and _pos_eq(y1, y2)


# ---------------------------------------------------------------------------
# Helper: collect every wire endpoint and occupied coordinate
# ---------------------------------------------------------------------------

def _wire_endpoints(sheet: Sheet) -> set[tuple[float, float]]:
    """Return the set of all wire endpoint coordinates on *sheet*."""
    eps = set()
    for w in sheet.wires:
        eps.add((round(w.x1, 4), round(w.y1, 4)))
        eps.add((round(w.x2, 4), round(w.y2, 4)))
    return eps


def _all_connected_positions(sheet: Sheet, nets: list[Net]) -> set[tuple[float, float]]:
    """Return every position that is electrically 'occupied'.

    Includes pin positions, wire endpoints, junction positions, and label
    positions.
    """
    positions: set[tuple[float, float]] = set()

    # Wire endpoints.
    for w in sheet.wires:
        positions.add((round(w.x1, 4), round(w.y1, 4)))
        positions.add((round(w.x2, 4), round(w.y2, 4)))

    # Junction positions.
    for j in sheet.junctions:
        positions.add((round(j.x, 4), round(j.y, 4)))

    # Pin positions (from nets -- these are world-resolved).
    for net in nets:
        for pin in net.pins:
            positions.add((round(pin.world_x, 4), round(pin.world_y, 4)))

    # Label positions.
    for lbl in sheet.labels:
        positions.add((round(lbl.x, 4), round(lbl.y, 4)))
    for gl in sheet.global_labels:
        positions.add((round(gl.x, 4), round(gl.y, 4)))
    for ps in sheet.power_symbols:
        positions.add((round(ps.x, 4), round(ps.y, 4)))

    return positions


def _point_on_wire_interior(
    px: float, py: float, sheet: Sheet, exclude_wire_id: str | None = None
) -> bool:
    """Return *True* if (px, py) lies on the **strict interior** of any wire.

    The strict interior excludes the wire's own endpoints.  This prevents
    a wire endpoint from matching itself.  Optionally exclude a specific
    wire by *exclude_wire_id*.
    """
    for w in sheet.wires:
        if exclude_wire_id is not None and w.wire_id == exclude_wire_id:
            continue
        if w.is_horizontal and _pos_eq(py, w.y1):
            lo = min(w.x1, w.x2)
            hi = max(w.x1, w.x2)
            # Strict interior: exclude the endpoints themselves.
            if lo + _EPS < px < hi - _EPS:
                return True
        elif w.is_vertical and _pos_eq(px, w.x1):
            lo = min(w.y1, w.y2)
            hi = max(w.y1, w.y2)
            if lo + _EPS < py < hi - _EPS:
                return True
    return False


# ---------------------------------------------------------------------------
# Check: PIN_NOT_CONNECTED
# ---------------------------------------------------------------------------

def check_pin_not_connected(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
) -> list[ERCViolation]:
    """Detect pins with no net assignment and no no-connect marker.

    A pin is considered unconnected if it does not appear in any net's pin
    list.  Pins of type ``NO_CONNECT`` are exempt.
    """
    # Collect all pin IDs that are assigned to a net.
    connected_pins: set[tuple[str, str]] = set()  # (instance_id, pin_number)
    for net in nets:
        for pin in net.pins:
            connected_pins.add((pin.instance_id, pin.number))

    violations: list[ERCViolation] = []

    for inst in sheet.instances:
        sym_def = symbol_library.get(inst.symbol_id)
        if sym_def is None:
            continue
        for pin in inst.get_pins(sym_def):
            if pin.electrical_type == PinType.NO_CONNECT:
                continue
            if (pin.instance_id, pin.number) not in connected_pins:
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="PIN_NOT_CONNECTED",
                        severity="error",
                        message=(
                            f"Pin {pin.number} ({pin.name}) of {inst.reference} "
                            f"is not connected"
                        ),
                        location_x=pin.world_x,
                        location_y=pin.world_y,
                        items=[inst.instance_id, pin.number],
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Check: PIN_NOT_DRIVEN
# ---------------------------------------------------------------------------

def check_pin_not_driven(nets: list[Net]) -> list[ERCViolation]:
    """Detect nets that have input/driven pins but no driving pin.

    A net needs a driver if it contains any pin in ``DRIVEN_PIN_TYPES``.
    A driver is any pin in ``DRIVING_PIN_TYPES``.
    """
    violations: list[ERCViolation] = []

    for net in nets:
        has_driven = False
        has_driver = False

        for pin in net.pins:
            if pin.electrical_type in DRIVEN_PIN_TYPES:
                has_driven = True
            if pin.electrical_type in DRIVING_PIN_TYPES:
                has_driver = True

        if has_driven and not has_driver:
            # Report at the location of the first driven pin.
            driven_pin = next(
                p for p in net.pins if p.electrical_type in DRIVEN_PIN_TYPES
            )
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="PIN_NOT_DRIVEN",
                    severity="error",
                    message=f"Net '{net.name}' has input pins but no driver",
                    location_x=driven_pin.world_x,
                    location_y=driven_pin.world_y,
                    items=[net.net_id],
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Check: POWER_PIN_NOT_DRIVEN
# ---------------------------------------------------------------------------

def check_power_pin_not_driven(nets: list[Net]) -> list[ERCViolation]:
    """Detect power nets (has POWER_IN) without a POWER_OUT driver."""
    violations: list[ERCViolation] = []

    for net in nets:
        has_power_in = any(
            p.electrical_type == PinType.POWER_IN for p in net.pins
        )
        has_power_out = any(
            p.electrical_type == PinType.POWER_OUT for p in net.pins
        )

        if has_power_in and not has_power_out:
            power_pin = next(
                p for p in net.pins if p.electrical_type == PinType.POWER_IN
            )
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="POWER_PIN_NOT_DRIVEN",
                    severity="error",
                    message=(
                        f"Power net '{net.name}' has power_in pins but no "
                        f"power_out driver"
                    ),
                    location_x=power_pin.world_x,
                    location_y=power_pin.world_y,
                    items=[net.net_id],
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Check: DUPLICATE_REFERENCE
# ---------------------------------------------------------------------------

def check_duplicate_reference(sheet: Sheet) -> list[ERCViolation]:
    """Detect two symbol instances with the same reference designator."""
    ref_map: dict[str, list] = defaultdict(list)
    for inst in sheet.instances:
        if inst.reference:
            ref_map[inst.reference].append(inst)

    violations: list[ERCViolation] = []
    for ref, instances in ref_map.items():
        if len(instances) > 1:
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="DUPLICATE_REFERENCE",
                    severity="error",
                    message=(
                        f"Duplicate reference designator '{ref}' on "
                        f"{len(instances)} instances"
                    ),
                    location_x=instances[0].x,
                    location_y=instances[0].y,
                    items=[inst.instance_id for inst in instances],
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Check: DANGLING_WIRE
# ---------------------------------------------------------------------------

def check_dangling_wire(sheet: Sheet, nets: list[Net]) -> list[ERCViolation]:
    """Detect wire endpoints not touching any pin, junction, or other wire.

    A wire endpoint is *dangling* if it does not coincide with any pin
    position, junction, other wire endpoint, or lie on the interior of
    another wire.
    """
    # Build position sets.
    pin_positions: set[tuple[float, float]] = set()
    for net in nets:
        for pin in net.pins:
            pin_positions.add((round(pin.world_x, 4), round(pin.world_y, 4)))

    junction_positions: set[tuple[float, float]] = set()
    for j in sheet.junctions:
        junction_positions.add((round(j.x, 4), round(j.y, 4)))

    label_positions: set[tuple[float, float]] = set()
    for lbl in sheet.labels:
        label_positions.add((round(lbl.x, 4), round(lbl.y, 4)))
    for gl in sheet.global_labels:
        label_positions.add((round(gl.x, 4), round(gl.y, 4)))
    for ps in sheet.power_symbols:
        label_positions.add((round(ps.x, 4), round(ps.y, 4)))

    # Count how many wire endpoints share each coordinate.
    endpoint_count: dict[tuple[float, float], int] = defaultdict(int)
    for w in sheet.wires:
        endpoint_count[(round(w.x1, 4), round(w.y1, 4))] += 1
        endpoint_count[(round(w.x2, 4), round(w.y2, 4))] += 1

    violations: list[ERCViolation] = []

    for w in sheet.wires:
        for ex, ey in [(round(w.x1, 4), round(w.y1, 4)),
                       (round(w.x2, 4), round(w.y2, 4))]:
            pt = (ex, ey)
            # Connected if: on a pin, on a junction, on a label,
            # shared with another wire endpoint (count >= 2), or on
            # the interior of another wire.
            if pt in pin_positions:
                continue
            if pt in junction_positions:
                continue
            if pt in label_positions:
                continue
            if endpoint_count.get(pt, 0) >= 2:
                continue
            if _point_on_wire_interior(ex, ey, sheet, exclude_wire_id=w.wire_id):
                continue

            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="DANGLING_WIRE",
                    severity="warning",
                    message=(
                        f"Wire endpoint at ({ex:.2f}, {ey:.2f}) is not "
                        f"connected to anything"
                    ),
                    location_x=ex,
                    location_y=ey,
                    items=[w.wire_id],
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Check: UNCONNECTED_WIRE_ENDPOINT
# ---------------------------------------------------------------------------

def check_unconnected_wire_endpoint(
    sheet: Sheet, nets: list[Net]
) -> list[ERCViolation]:
    """Detect wire endpoints touching nothing.

    This is similar to ``check_dangling_wire`` but focuses specifically on
    endpoints that are completely isolated (no other electrical object at
    that coordinate at all).
    """
    occupied = _all_connected_positions(sheet, nets)
    wire_ep = _wire_endpoints(sheet)

    violations: list[ERCViolation] = []
    seen: set[tuple[float, float]] = set()

    for w in sheet.wires:
        for ex, ey in [(round(w.x1, 4), round(w.y1, 4)),
                       (round(w.x2, 4), round(w.y2, 4))]:
            pt = (ex, ey)
            if pt in seen:
                continue
            seen.add(pt)

            # Count wire endpoints at this point (including this wire).
            ep_count = sum(
                1 for w2 in sheet.wires
                for wp in [(round(w2.x1, 4), round(w2.y1, 4)),
                           (round(w2.x2, 4), round(w2.y2, 4))]
                if wp == pt
            )

            # If this endpoint is only from one wire (this one) and there is
            # nothing else here, it is unconnected.
            if ep_count <= 1:
                # Check if there is a pin, junction, or label at this point.
                has_pin = any(
                    _pos_match(pin.world_x, pin.world_y, ex, ey)
                    for net in nets
                    for pin in net.pins
                )
                has_junction = any(
                    _pos_match(j.x, j.y, ex, ey) for j in sheet.junctions
                )
                has_label = (
                    any(_pos_match(lbl.x, lbl.y, ex, ey) for lbl in sheet.labels)
                    or any(_pos_match(gl.x, gl.y, ex, ey) for gl in sheet.global_labels)
                    or any(_pos_match(ps.x, ps.y, ex, ey) for ps in sheet.power_symbols)
                )

                if not has_pin and not has_junction and not has_label:
                    if not _point_on_wire_interior(ex, ey, sheet, exclude_wire_id=w.wire_id):
                        violations.append(
                            ERCViolation(
                                violation_id=str(uuid.uuid4()),
                                check_type="UNCONNECTED_WIRE_ENDPOINT",
                                severity="warning",
                                message=(
                                    f"Wire endpoint at ({ex:.2f}, {ey:.2f}) "
                                    f"touches nothing"
                                ),
                                location_x=ex,
                                location_y=ey,
                                items=[w.wire_id],
                            )
                        )

    return violations


# ---------------------------------------------------------------------------
# Check: OFF_GRID_ENDPOINT
# ---------------------------------------------------------------------------

def check_off_grid_endpoint(
    sheet: Sheet,
    nets: list[Net],
    grid: GridSnap | None = None,
) -> list[ERCViolation]:
    """Detect pins and wire endpoints not aligned to the grid."""
    if grid is None:
        grid = GridSnap(2.54)

    violations: list[ERCViolation] = []
    seen: set[tuple[float, float]] = set()

    # Check pin positions.
    for net in nets:
        for pin in net.pins:
            pt = (round(pin.world_x, 4), round(pin.world_y, 4))
            if pt in seen:
                continue
            seen.add(pt)
            if not grid.is_on_grid(pin.world_x) or not grid.is_on_grid(pin.world_y):
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="OFF_GRID_ENDPOINT",
                        severity="warning",
                        message=(
                            f"Pin at ({pin.world_x:.4f}, {pin.world_y:.4f}) "
                            f"is off the {grid.grid_size}mm grid"
                        ),
                        location_x=pin.world_x,
                        location_y=pin.world_y,
                        items=[pin.instance_id, pin.number],
                    )
                )

    # Check wire endpoints.
    for w in sheet.wires:
        for wx, wy in [(w.x1, w.y1), (w.x2, w.y2)]:
            pt = (round(wx, 4), round(wy, 4))
            if pt in seen:
                continue
            seen.add(pt)
            if not grid.is_on_grid(wx) or not grid.is_on_grid(wy):
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="OFF_GRID_ENDPOINT",
                        severity="warning",
                        message=(
                            f"Wire endpoint at ({wx:.4f}, {wy:.4f}) "
                            f"is off the {grid.grid_size}mm grid"
                        ),
                        location_x=wx,
                        location_y=wy,
                        items=[w.wire_id],
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Check: NOCONNECT_CONNECTED (placeholder for v1)
# ---------------------------------------------------------------------------

def check_noconnect_connected(
    sheet: Sheet, nets: list[Net]
) -> list[ERCViolation]:
    """Placeholder: no-connect on a pin that is actually connected.

    Not implemented in v1 (no explicit NoConnect marker type).
    Returns an empty list.
    """
    return []


# ---------------------------------------------------------------------------
# Check: DANGLING_LABEL
# ---------------------------------------------------------------------------

def check_dangling_label(sheet: Sheet, nets: list[Net]) -> list[ERCViolation]:
    """Detect labels whose position doesn't connect to any wire or pin."""
    # Build set of all electrically occupied positions (pins + wire endpoints
    # + junctions).
    occupied: set[tuple[float, float]] = set()
    for w in sheet.wires:
        occupied.add((round(w.x1, 4), round(w.y1, 4)))
        occupied.add((round(w.x2, 4), round(w.y2, 4)))
    for j in sheet.junctions:
        occupied.add((round(j.x, 4), round(j.y, 4)))
    for net in nets:
        for pin in net.pins:
            occupied.add((round(pin.world_x, 4), round(pin.world_y, 4)))

    violations: list[ERCViolation] = []

    # Check NetLabels.
    for lbl in sheet.labels:
        pt = (round(lbl.x, 4), round(lbl.y, 4))
        if pt not in occupied and not _point_on_wire_interior(lbl.x, lbl.y, sheet):
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="DANGLING_LABEL",
                    severity="error",
                    message=(
                        f"Label '{lbl.name}' at ({lbl.x:.2f}, {lbl.y:.2f}) "
                        f"is not connected to any wire or pin"
                    ),
                    location_x=lbl.x,
                    location_y=lbl.y,
                    items=[lbl.label_id],
                )
            )

    # Check GlobalLabels.
    for gl in sheet.global_labels:
        pt = (round(gl.x, 4), round(gl.y, 4))
        if pt not in occupied and not _point_on_wire_interior(gl.x, gl.y, sheet):
            violations.append(
                ERCViolation(
                    violation_id=str(uuid.uuid4()),
                    check_type="DANGLING_LABEL",
                    severity="error",
                    message=(
                        f"Global label '{gl.name}' at ({gl.x:.2f}, {gl.y:.2f}) "
                        f"is not connected to any wire or pin"
                    ),
                    location_x=gl.x,
                    location_y=gl.y,
                    items=[gl.label_id],
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Check: PIN_TO_PIN_CONFLICTS
# ---------------------------------------------------------------------------

def check_pin_to_pin_conflicts(nets: list[Net]) -> list[ERCViolation]:
    """For each net, check all pin pairs against the pin compatibility matrix."""
    violations: list[ERCViolation] = []

    for net in nets:
        if len(net.pins) < 2:
            continue

        for pin_a, pin_b in combinations(net.pins, 2):
            result = check_pin_pair(pin_a.electrical_type, pin_b.electrical_type)

            if result == "ERR":
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="PIN_TO_PIN_ERROR",
                        severity="error",
                        message=(
                            f"Incompatible pin types on net '{net.name}': "
                            f"{pin_a.electrical_type.value} ({pin_a.instance_id}:"
                            f"{pin_a.number}) vs "
                            f"{pin_b.electrical_type.value} ({pin_b.instance_id}:"
                            f"{pin_b.number})"
                        ),
                        location_x=pin_a.world_x,
                        location_y=pin_a.world_y,
                        items=[
                            pin_a.instance_id, pin_a.number,
                            pin_b.instance_id, pin_b.number,
                        ],
                    )
                )
            elif result == "WAR":
                violations.append(
                    ERCViolation(
                        violation_id=str(uuid.uuid4()),
                        check_type="PIN_TO_PIN_WARNING",
                        severity="warning",
                        message=(
                            f"Pin type warning on net '{net.name}': "
                            f"{pin_a.electrical_type.value} ({pin_a.instance_id}:"
                            f"{pin_a.number}) vs "
                            f"{pin_b.electrical_type.value} ({pin_b.instance_id}:"
                            f"{pin_b.number})"
                        ),
                        location_x=pin_a.world_x,
                        location_y=pin_a.world_y,
                        items=[
                            pin_a.instance_id, pin_a.number,
                            pin_b.instance_id, pin_b.number,
                        ],
                    )
                )

    return violations
