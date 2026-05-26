"""Utilities for cleaning up orphaned wires, labels, and dangling items.

The #1 recurring problem after deleting components: orphaned wires and labels
that persist because GetItems IPC doesn't work in KiCad 9.0.7.  These tools
bypass that limitation by reading the .kicad_sch file directly via three new
C++ MCP endpoints:

    remove_all_dangling  - finds dangling wire_end positions via ERC, matches
                           them to wire UUIDs in .kicad_sch, deletes via DeleteItems
    remove_items_by_position - removes wires/labels near a specific (x,y) coord
    list_wires           - lists all wires with UUIDs and positions from .kicad_sch

Usage:
    cleanup = CleanupTools("http://127.0.0.1:8080/mcp")
    result = cleanup.remove_all_dangling()
    print(f"Cleaned {result.get('deleted', 0)} dangling wires")

    wires = cleanup.list_wires()
    print(f"Total wires on schematic: {len(wires)}")
"""

from __future__ import annotations

from typing import Any

from .placement_engine import _call


# Maximum number of dangling-removal passes before we give up.  Each pass may
# reveal new dangles that were previously hidden behind other wires.
_MAX_DANGLING_PASSES = 5


class CleanupTools:
    """High-level cleanup operations for orphaned schematic items."""

    def __init__(self, kicad_url: str = "http://127.0.0.1:8080/mcp"):
        self.url = kicad_url

    # ------------------------------------------------------------------
    # Primitive wrappers around the three new MCP tools
    # ------------------------------------------------------------------

    def remove_all_dangling(self) -> dict:
        """Remove all dangling wire endpoints.

        Calls the ``remove_all_dangling`` MCP tool which:
        1. Runs ERC to find dangling wire_end markers
        2. Parses the .kicad_sch file to match positions to wire UUIDs
        3. Deletes the matched wires via DeleteItems

        Returns ``{"deleted": N, "positions": [...]}`` on success, or the
        raw response dict on unexpected output.
        """
        result = _call(self.url, "remove_all_dangling")
        if isinstance(result, dict):
            return result
        # If the tool returned a list (e.g. list of positions), wrap it.
        if isinstance(result, list):
            return {"deleted": len(result), "positions": result}
        return {"deleted": 0, "_raw": result}

    def remove_items_near(
        self,
        x: float,
        y: float,
        tolerance: float = 1.0,
        wires: bool = True,
        labels: bool = True,
    ) -> dict:
        """Remove wires and/or labels near position *(x, y)*.

        Parameters
        ----------
        x, y : float
            Centre of the search area in mm.
        tolerance : float
            Radius (mm) around *(x, y)* to search.
        wires : bool
            Whether to remove wire segments within the tolerance.
        labels : bool
            Whether to remove labels within the tolerance.

        Returns dict with keys like ``{"removed": N, "items": [...]}``.
        """
        args: dict[str, Any] = {
            "x": round(x, 3),
            "y": round(y, 3),
            "tolerance": round(tolerance, 3),
            "wires": wires,
            "labels": labels,
        }
        result = _call(self.url, "remove_items_by_position", args)
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"removed": len(result), "items": result}
        return {"removed": 0, "_raw": result}

    def list_wires(self, bbox: tuple | None = None) -> list[dict]:
        """List all wires on the schematic.

        Each wire dict contains at least ``uuid``, ``x1``, ``y1``, ``x2``,
        ``y2`` (all in mm).

        Parameters
        ----------
        bbox : tuple, optional
            If provided as ``(min_x, min_y, max_x, max_y)`` in mm, only
            wires that overlap the bounding box are returned.

        Returns a list of wire dicts.
        """
        args: dict[str, Any] = {}
        if bbox is not None:
            args["min_x"] = round(bbox[0], 3)
            args["min_y"] = round(bbox[1], 3)
            args["max_x"] = round(bbox[2], 3)
            args["max_y"] = round(bbox[3], 3)

        result = _call(self.url, "list_wires", args if args else None)

        if isinstance(result, list):
            return result
        # Some MCP responses wrap the list inside a dict.
        if isinstance(result, dict):
            if "wires" in result and isinstance(result["wires"], list):
                return result["wires"]
            if "_text" in result:
                return []
        return []

    # ------------------------------------------------------------------
    # Higher-level workflows
    # ------------------------------------------------------------------

    def cleanup_after_delete(
        self,
        old_pin_positions: list[tuple[float, float]],
        tolerance: float = 1.5,
    ) -> dict:
        """Full cleanup after deleting a component.

        Call this right after removing a symbol.  It:
        1. Runs ``remove_all_dangling`` to catch obvious orphans.
        2. For each old pin position, calls ``remove_items_near`` to sweep
           up any wires or labels that were connected to the now-gone pins.
        3. Returns a summary of everything that was deleted.

        Parameters
        ----------
        old_pin_positions : list of (x, y) tuples
            The pin positions of the component *before* it was deleted.
            Obtain these via ``get_component_pins`` before the delete.
        tolerance : float
            Search radius around each pin position (mm).
        """
        summary: dict[str, Any] = {
            "dangling_deleted": 0,
            "positional_deleted": 0,
            "pin_positions_swept": len(old_pin_positions),
            "details": [],
        }

        # Pass 1: remove globally-detected dangles.
        dangling_result = self.remove_all_dangling()
        summary["dangling_deleted"] = dangling_result.get("deleted", 0)

        # Pass 2: sweep each old pin location.
        for px, py in old_pin_positions:
            pos_result = self.remove_items_near(
                x=px, y=py, tolerance=tolerance, wires=True, labels=True
            )
            removed = pos_result.get("removed", 0)
            summary["positional_deleted"] += removed
            if removed > 0:
                summary["details"].append({
                    "position": {"x": px, "y": py},
                    "removed": removed,
                })

        summary["total_deleted"] = (
            summary["dangling_deleted"] + summary["positional_deleted"]
        )
        return summary

    def cleanup_full(self) -> dict:
        """Run all cleanup operations.

        Call this after any messy operation (delete, move, paste, etc.).

        1. Repeatedly calls ``remove_all_dangling`` until no more dangles
           are found (up to ``_MAX_DANGLING_PASSES`` passes), since removing
           one wire can expose a new dangling endpoint on an adjacent wire.
        2. Runs ERC to check for remaining issues.
        3. Returns a summary report.
        """
        total_deleted = 0
        passes = 0
        pass_details: list[dict] = []

        for i in range(_MAX_DANGLING_PASSES):
            result = self.remove_all_dangling()
            deleted = result.get("deleted", 0)
            passes += 1
            pass_details.append({"pass": i + 1, "deleted": deleted})
            total_deleted += deleted
            if deleted == 0:
                break

        # Final ERC check.
        erc_result = _call(self.url, "erc_check")
        erc_errors = 0
        if isinstance(erc_result, list):
            erc_errors = len(erc_result)
        elif isinstance(erc_result, dict):
            erc_errors = erc_result.get("error_count", 0)

        return {
            "total_deleted": total_deleted,
            "passes": passes,
            "pass_details": pass_details,
            "erc_errors_remaining": erc_errors,
        }


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cleanup = CleanupTools("http://127.0.0.1:8080/mcp")

    result = cleanup.remove_all_dangling()
    print(f"Cleaned {result.get('deleted', 0)} dangling wires")

    wires = cleanup.list_wires()
    print(f"Total wires on schematic: {len(wires)}")

    full = cleanup.cleanup_full()
    print(f"Full cleanup: {full['total_deleted']} items removed in {full['passes']} pass(es), "
          f"{full['erc_errors_remaining']} ERC errors remaining")
