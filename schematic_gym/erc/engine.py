"""ERC engine: run all checks and aggregate violations."""

from __future__ import annotations

from ..core.grid import GridSnap
from ..core.nets import Net
from ..core.project import ERCViolation, Sheet
from ..core.symbols import SymbolDef
from .checks import (
    check_dangling_label,
    check_dangling_wire,
    check_duplicate_reference,
    check_noconnect_connected,
    check_off_grid_endpoint,
    check_pin_not_connected,
    check_pin_not_driven,
    check_pin_to_pin_conflicts,
    check_power_pin_not_driven,
    check_unconnected_wire_endpoint,
)


def run_erc(
    sheet: Sheet,
    nets: list[Net],
    symbol_library: dict[str, SymbolDef],
    grid: GridSnap | None = None,
) -> list[ERCViolation]:
    """Run all ERC checks and return violations sorted by severity.

    Parameters
    ----------
    sheet:
        The schematic sheet to check.
    nets:
        Resolved nets (from connectivity analysis).
    symbol_library:
        Symbol definitions keyed by lib_id.
    grid:
        Grid snapping helper.  Defaults to 2.54 mm if not provided.

    Returns
    -------
    list[ERCViolation]
        All violations, sorted with errors first, then warnings.
    """
    if grid is None:
        grid = GridSnap(2.54)

    violations: list[ERCViolation] = []

    # Run each check and collect all violations.
    violations.extend(check_pin_not_connected(sheet, nets, symbol_library))
    violations.extend(check_pin_not_driven(nets))
    violations.extend(check_power_pin_not_driven(nets))
    violations.extend(check_duplicate_reference(sheet))
    violations.extend(check_dangling_wire(sheet, nets))
    violations.extend(check_unconnected_wire_endpoint(sheet, nets))
    violations.extend(check_off_grid_endpoint(sheet, nets, grid))
    violations.extend(check_noconnect_connected(sheet, nets))
    violations.extend(check_dangling_label(sheet, nets))
    violations.extend(check_pin_to_pin_conflicts(nets))

    # Sort: errors first (severity="error" < "warning" lexicographically).
    violations.sort(key=lambda v: (0 if v.severity == "error" else 1, v.check_type))

    return violations


def count_errors(violations: list[ERCViolation]) -> int:
    """Count violations with severity ``"error"``."""
    return sum(1 for v in violations if v.severity == "error")


def count_warnings(violations: list[ERCViolation]) -> int:
    """Count violations with severity ``"warning"``."""
    return sum(1 for v in violations if v.severity == "warning")
