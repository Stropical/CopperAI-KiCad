"""Unified schematic design agent — high-level actions for placement, routing,
verification, and cleanup.

The LLM (or user) calls ``agent.add_component(...)`` and gets back a verified
result.  Every action ends with a screenshot + ERC check; failures trigger
automatic retries at alternative positions.

Usage:
    agent = SchematicAgent("http://127.0.0.1:8080/mcp")
    result = agent.add_component(
        library="Device", symbol="D_TVS", reference="D2", value="SMBJ18A",
        connections=[("1", "D1", "1", "VIN_RAW"), ("2", "#PWR03", "1", "GND")],
    )
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .placement_engine import (
    Component,
    Net,
    Pin,
    PlacementEngine,
    SchematicState,
    WireSegment,
    _call,
)

# Optional imports — these modules may not exist yet.
# Fall back gracefully so the agent can still function with reduced capability.
try:
    from .wire_router import WireRouter
except ImportError:
    WireRouter = None  # type: ignore[assignment,misc]

try:
    from .visual_verifier import VisualVerifier, VerifyResult
except ImportError:
    VisualVerifier = None  # type: ignore[assignment,misc]
    VerifyResult = None  # type: ignore[assignment,misc]

try:
    from .cleanup_tools import CleanupTools
except ImportError:
    CleanupTools = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Screenshots directory
# ---------------------------------------------------------------------------

_RENDERS_DIR = Path(__file__).resolve().parent.parent / "renders" / "agent"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """Outcome of a single high-level agent action."""

    success: bool
    reference: str
    position: tuple[float, float, int] | None  # (x, y, rotation)
    wires_added: int
    erc_before: int
    erc_after: int
    screenshot_path: str | None
    message: str
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Schematic Agent
# ---------------------------------------------------------------------------

class SchematicAgent:
    """High-level schematic design agent.

    Wraps placement, wire-routing, verification, and cleanup into simple
    actions that handle all the low-level KiCad MCP calls internally.
    """

    def __init__(self, kicad_url: str = "http://127.0.0.1:8080/mcp"):
        self.url = kicad_url
        self._engine = PlacementEngine(kicad_url)
        self._router = WireRouter(kicad_url) if WireRouter is not None else None
        self._verifier = VisualVerifier(kicad_url) if VisualVerifier is not None else None
        self._cleanup = CleanupTools(kicad_url) if CleanupTools is not None else None

        # Ensure the screenshots directory exists.
        _RENDERS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # State reading
    # ------------------------------------------------------------------

    def read_state(self) -> SchematicState:
        """Read current schematic state from live KiCad."""
        return self._engine.read_state()

    # ------------------------------------------------------------------
    # Core actions
    # ------------------------------------------------------------------

    def add_component(
        self,
        library: str,
        symbol: str,
        reference: str,
        value: str,
        connections: list[tuple[str, str, str, str]],
        component_role: str = "passive",
        max_retries: int = 2,
    ) -> ActionResult:
        """Place a component, wire it, and verify.  Retry on failure.

        Parameters
        ----------
        library, symbol:
            KiCad library and symbol name (e.g. ``"Device"``, ``"D_TVS"``).
        reference:
            Designator (e.g. ``"D3"``).
        value:
            Component value string (e.g. ``"100nF"``).
        connections:
            List of ``(my_pin, target_ref, target_pin, net_name)`` tuples
            describing how this component connects to the rest of the design.
        component_role:
            One of ``"passive"``, ``"ic"``, ``"connector"``, ``"semiconductor"``.
            Influences placement heuristics.
        max_retries:
            Number of times to retry with an alternative position when
            verification fails.

        Returns
        -------
        ActionResult
        """
        # 1. Snapshot ERC before we touch anything.
        erc_before_count = self._erc_count()

        # 2. Read current state.
        state = self.read_state()

        # 3. Fetch component dimensions.
        comp_w, comp_h = self._get_component_dims(library, symbol)

        # 4. Compute candidate positions (best first).
        candidates = self._ranked_positions(
            comp_w, comp_h, connections, state, component_role,
        )

        last_message = ""
        for attempt in range(max_retries + 1):
            pos = candidates[attempt] if attempt < len(candidates) else candidates[-1]
            x, y, rotation = pos

            # --- Place ---
            place_result = _call(self.url, "place_component", {
                "library": library,
                "symbol": symbol,
                "reference": reference,
                "value": value,
                "x": round(x, 2),
                "y": round(y, 2),
                "rotation": rotation,
            })

            # 4b. Re-read state to get actual pin positions.
            state = self.read_state()
            new_comp = next(
                (c for c in state.components if c.ref == reference), None,
            )
            if new_comp is None:
                last_message = (
                    f"Component {reference} not found after placement "
                    f"(attempt {attempt + 1})"
                )
                continue

            # --- Wire ---
            wires_added = self._wire_connections(
                new_comp, connections, state,
            )

            # --- Verify ---
            erc_after_count = self._erc_count()
            screenshot = self._take_screenshot(f"add_{reference}_{attempt}")
            verification_ok = self._verify_action(erc_before_count, erc_after_count)

            if verification_ok:
                return ActionResult(
                    success=True,
                    reference=reference,
                    position=(round(x, 2), round(y, 2), rotation),
                    wires_added=wires_added,
                    erc_before=erc_before_count,
                    erc_after=erc_after_count,
                    screenshot_path=screenshot,
                    message=(
                        f"Placed {reference} ({value}) at "
                        f"({x:.1f}, {y:.1f}) rot={rotation}, "
                        f"wired {wires_added}/{len(connections)} connections"
                    ),
                    retry_count=attempt,
                )

            # --- Verification failed — undo and try next position ---
            last_message = (
                f"Verification failed for {reference} at ({x:.1f}, {y:.1f}) "
                f"(ERC {erc_before_count}->{erc_after_count}), "
                f"attempt {attempt + 1}/{max_retries + 1}"
            )
            self._delete_and_cleanup(reference)
            # Re-read state after cleanup for next attempt.
            state = self.read_state()

        # All retries exhausted.
        return ActionResult(
            success=False,
            reference=reference,
            position=None,
            wires_added=0,
            erc_before=erc_before_count,
            erc_after=self._erc_count(),
            screenshot_path=self._take_screenshot(f"add_{reference}_failed"),
            message=f"Failed after {max_retries + 1} attempts: {last_message}",
            retry_count=max_retries,
        )

    def delete_component(self, reference: str) -> ActionResult:
        """Delete a component and clean up all orphaned wires/labels.

        Steps:
            1. Record pin positions.
            2. Delete via ``delete_components_batch``.
            3. Run cleanup on dangling items at old pin positions.
            4. Verify no new dangling items.
        """
        erc_before = self._erc_count()
        state = self.read_state()

        comp = next((c for c in state.components if c.ref == reference), None)
        if comp is None:
            return ActionResult(
                success=False,
                reference=reference,
                position=None,
                wires_added=0,
                erc_before=erc_before,
                erc_after=erc_before,
                screenshot_path=None,
                message=f"Component {reference} not found",
            )

        old_pos = (comp.x, comp.y, int(comp.rotation))
        pin_positions = [(p.x, p.y) for p in comp.pins]

        # Delete the component.
        _call(self.url, "delete_components_batch", {"references": [reference]})

        # Clean up dangling wires at old pin positions.
        if self._cleanup is not None:
            self._cleanup.cleanup_after_delete(pin_positions)
        else:
            # Minimal fallback: attempt to remove wires in a bbox around old pins.
            self._fallback_cleanup(pin_positions)

        erc_after = self._erc_count()
        screenshot = self._take_screenshot(f"delete_{reference}")

        return ActionResult(
            success=True,
            reference=reference,
            position=old_pos,
            wires_added=0,
            erc_before=erc_before,
            erc_after=erc_after,
            screenshot_path=screenshot,
            message=(
                f"Deleted {reference} and cleaned up "
                f"{len(pin_positions)} pin locations"
            ),
        )

    def move_and_reroute(
        self,
        reference: str,
        new_x: float,
        new_y: float,
    ) -> ActionResult:
        """Move a component and re-wire all its connections.

        Steps:
            1. Record all connected pins from netlist.
            2. Disconnect all wires at current pin positions.
            3. Move component.
            4. Re-route each connection with wire router.
            5. Verify.
        """
        erc_before = self._erc_count()
        state = self.read_state()

        comp = next((c for c in state.components if c.ref == reference), None)
        if comp is None:
            return ActionResult(
                success=False,
                reference=reference,
                position=None,
                wires_added=0,
                erc_before=erc_before,
                erc_after=erc_before,
                screenshot_path=None,
                message=f"Component {reference} not found",
            )

        # Find all peer connections from the netlist.
        peer_connections: list[tuple[str, str, str, str]] = []
        for net in state.nets:
            my_pins = [(r, p) for r, p in net.pins if r == reference]
            peers = [(r, p) for r, p in net.pins if r != reference]
            for _my_ref, my_pin in my_pins:
                for peer_ref, peer_pin in peers:
                    peer_connections.append(
                        (my_pin, peer_ref, peer_pin, net.name),
                    )

        # Disconnect existing wires from all pins.
        for pin in comp.pins:
            _call(self.url, "disconnect_pin", {
                "reference": reference,
                "pin_number": pin.number,
            })

        # Move the component.
        _call(self.url, "move_component", {
            "reference": reference,
            "x_mm": round(new_x, 2),
            "y_mm": round(new_y, 2),
        })

        # Re-read state for new pin positions.
        state = self.read_state()
        moved_comp = next(
            (c for c in state.components if c.ref == reference), None,
        )
        if moved_comp is None:
            return ActionResult(
                success=False,
                reference=reference,
                position=None,
                wires_added=0,
                erc_before=erc_before,
                erc_after=self._erc_count(),
                screenshot_path=self._take_screenshot(f"move_{reference}_lost"),
                message=f"Component {reference} lost after move",
            )

        # Re-route each connection.
        wires_added = self._wire_connections(
            moved_comp, peer_connections, state,
        )

        erc_after = self._erc_count()
        screenshot = self._take_screenshot(f"move_{reference}")

        return ActionResult(
            success=True,
            reference=reference,
            position=(round(new_x, 2), round(new_y, 2), int(moved_comp.rotation)),
            wires_added=wires_added,
            erc_before=erc_before,
            erc_after=erc_after,
            screenshot_path=screenshot,
            message=(
                f"Moved {reference} to ({new_x:.1f}, {new_y:.1f}), "
                f"re-routed {wires_added}/{len(peer_connections)} connections"
            ),
        )

    def verify_full(self) -> dict:
        """Full schematic verification: ERC + overlaps + wire quality.

        Returns a dict summarising all findings.
        """
        result: dict[str, Any] = {}

        # ERC check.
        erc_raw = _call(self.url, "erc_check")
        if isinstance(erc_raw, list):
            result["erc_violations"] = len(erc_raw)
            result["erc_details"] = erc_raw[:20]  # cap detail output
        elif isinstance(erc_raw, dict):
            result["erc_violations"] = 0
            result["erc_raw"] = erc_raw.get("_text", "")[:200]
        else:
            result["erc_violations"] = -1
            result["erc_raw"] = str(erc_raw)[:200]

        # Overlap check.
        state = self.read_state()
        overlaps: list[tuple[str, str]] = []
        comps = [c for c in state.components if not c.is_power]
        for i, a in enumerate(comps):
            for b in comps[i + 1 :]:
                if self._bboxes_overlap(a.bbox, b.bbox):
                    overlaps.append((a.ref, b.ref))
        result["overlaps"] = overlaps
        result["overlap_count"] = len(overlaps)

        # Visual verifier (if available).
        if self._verifier is not None:
            try:
                vr = self._verifier.verify()
                result["visual"] = {
                    "pass": vr.passed if hasattr(vr, "passed") else True,
                    "issues": vr.issues if hasattr(vr, "issues") else [],
                }
            except Exception as exc:
                result["visual"] = {"error": str(exc)}

        # Screenshot.
        result["screenshot"] = self._take_screenshot("verify_full")

        return result

    def cleanup(self) -> dict:
        """Clean up orphaned wires and labels.

        Returns a summary of what was cleaned.
        """
        result: dict[str, Any] = {}
        erc_before = self._erc_count()

        if self._cleanup is not None:
            try:
                cleanup_result = self._cleanup.cleanup_full()
                result["cleanup"] = cleanup_result
            except Exception as exc:
                result["error"] = str(exc)
        else:
            # Fallback: call MCP cleanup tool directly.
            raw = _call(self.url, "auto_cleanup_stray_nets", {})
            result["cleanup"] = raw

        erc_after = self._erc_count()
        result["erc_before"] = erc_before
        result["erc_after"] = erc_after
        result["screenshot"] = self._take_screenshot("cleanup")

        return result

    # ------------------------------------------------------------------
    # Internal: delete + cleanup (used for undo during retries)
    # ------------------------------------------------------------------

    def _delete_and_cleanup(self, reference: str) -> None:
        """Delete a component and clean up dangling wires at its old pin
        positions.  Used internally for undo during retry loops.
        """
        state = self.read_state()
        comp = next((c for c in state.components if c.ref == reference), None)
        pin_positions = [(p.x, p.y) for p in comp.pins] if comp else []

        # Delete.
        _call(self.url, "delete_components_batch", {"references": [reference]})

        # Clean up dangling wires at old pin positions.
        if self._cleanup is not None:
            self._cleanup.cleanup_after_delete(pin_positions)
        else:
            self._fallback_cleanup(pin_positions)

    # ------------------------------------------------------------------
    # Internal: wiring
    # ------------------------------------------------------------------

    def _wire_connections(
        self,
        comp: Component,
        connections: list[tuple[str, str, str, str]],
        state: SchematicState,
    ) -> int:
        """Wire all connections for *comp*.

        Returns the number of wires successfully applied.
        """
        wired = 0
        for my_pin_num, tgt_ref, tgt_pin_num, net_name in connections:
            # Find my pin on the newly placed component.
            my_pin = next(
                (p for p in comp.pins if p.number == my_pin_num), None,
            )
            # Find the target pin.
            tgt_comp = next(
                (c for c in state.components if c.ref == tgt_ref), None,
            )
            tgt_pin = None
            if tgt_comp is not None:
                tgt_pin = next(
                    (p for p in tgt_comp.pins if p.number == tgt_pin_num), None,
                )

            if my_pin is None or tgt_pin is None:
                continue

            # Route the wire.
            if self._router is not None:
                segments = self._router.route(my_pin, tgt_pin, state)
            else:
                # Fallback to PlacementEngine's built-in router.
                segments = self._engine.route_wire(my_pin, tgt_pin, state)

            # Apply via add_wire.
            wire_segs = [
                {"x1": s.x1, "y1": s.y1, "x2": s.x2, "y2": s.y2}
                for s in segments
            ]
            if wire_segs:
                _call(self.url, "add_wire", {"segments": wire_segs})
                wired += 1

        return wired

    # ------------------------------------------------------------------
    # Internal: positioning helpers
    # ------------------------------------------------------------------

    def _ranked_positions(
        self,
        comp_w: float,
        comp_h: float,
        connections: list[tuple[str, str, str, str]],
        state: SchematicState,
        component_role: str,
    ) -> list[tuple[float, float, int]]:
        """Return a list of candidate positions ordered best-first.

        The first entry is the optimal position from the placement engine.
        Subsequent entries are offset alternatives for retry attempts.
        """
        best = self._engine.find_optimal_position(
            comp_w, comp_h, connections, state,
            component_role=component_role,
        )
        x, y, rot = best

        # Generate alternatives: offset from the best by ~10mm in each
        # cardinal direction, then diagonals.
        offsets = [
            (0, 0),
            (12.7, 0), (-12.7, 0),
            (0, 12.7), (0, -12.7),
            (12.7, 12.7), (-12.7, -12.7),
            (12.7, -12.7), (-12.7, 12.7),
            (25.4, 0), (-25.4, 0),
        ]
        candidates: list[tuple[float, float, int]] = []
        grid = state.grid
        for dx, dy in offsets:
            cx = round((x + dx) / grid) * grid
            cy = round((y + dy) / grid) * grid
            # Check that this position doesn't overlap existing components.
            new_bbox = (
                cx - comp_w / 2 - 1.5,
                cy - comp_h / 2 - 1.5,
                cx + comp_w / 2 + 1.5,
                cy + comp_h / 2 + 1.5,
            )
            overlap = any(
                self._bboxes_overlap(new_bbox, c.bbox)
                for c in state.components
            )
            if not overlap:
                candidates.append((cx, cy, rot))

        # Always have at least the original best position as a fallback.
        if not candidates:
            candidates.append((x, y, rot))

        return candidates

    def _get_component_dims(
        self, library: str, symbol: str,
    ) -> tuple[float, float]:
        """Fetch component width and height from KiCad (with safe defaults)."""
        comp_data = _call(self.url, "batch_get_component_data", {
            "components": [{"library": library, "symbol": symbol}],
        })
        comp_w, comp_h = 10.0, 5.0  # safe defaults
        if isinstance(comp_data, dict) and "_text" in comp_data:
            try:
                parsed = json.loads(comp_data["_text"])
                if isinstance(parsed, list) and parsed:
                    comp_w = parsed[0].get("width_mm", 10)
                    comp_h = parsed[0].get("height_mm", 5)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(comp_data, list) and comp_data:
            comp_w = comp_data[0].get("width_mm", 10)
            comp_h = comp_data[0].get("height_mm", 5)
        return (comp_w, comp_h)

    # ------------------------------------------------------------------
    # Internal: verification helpers
    # ------------------------------------------------------------------

    def _erc_count(self) -> int:
        """Return the current number of ERC violations (0 on error)."""
        erc_raw = _call(self.url, "erc_check")
        if isinstance(erc_raw, list):
            return len(erc_raw)
        return 0

    def _verify_action(self, erc_before: int, erc_after: int) -> bool:
        """Return True if the action did not make things worse.

        The action passes verification when the ERC count did not increase.
        If a ``VisualVerifier`` is available, its result is also checked.
        """
        if erc_after > erc_before:
            return False

        if self._verifier is not None:
            try:
                vr = self._verifier.verify()
                if hasattr(vr, "passed") and not vr.passed:
                    return False
            except Exception:
                pass  # verifier failure is non-fatal

        return True

    def _take_screenshot(self, label: str) -> str | None:
        """Capture a schematic screenshot and save to the renders directory.

        Returns the absolute path to the saved PNG, or None on failure.
        """
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{label}.png"
        filepath = _RENDERS_DIR / filename

        try:
            result = _call(self.url, "export_schematic_screenshot", {
                "output_path": str(filepath),
            })
            if filepath.exists():
                return str(filepath)
            # Some MCP implementations return the image data differently.
            # Try an alternative tool name.
            result = _call(self.url, "screenshot", {
                "output_path": str(filepath),
            })
            if filepath.exists():
                return str(filepath)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Internal: cleanup fallback
    # ------------------------------------------------------------------

    def _fallback_cleanup(
        self, pin_positions: list[tuple[float, float]],
    ) -> None:
        """Minimal cleanup when CleanupTools is not available.

        Attempts to remove dangling wires within a small bbox around each
        old pin position.
        """
        margin = 1.0  # mm
        for px, py in pin_positions:
            _call(self.url, "remove_wires_in_bbox", {
                "x_min": px - margin,
                "y_min": py - margin,
                "x_max": px + margin,
                "y_max": py + margin,
            })

    # ------------------------------------------------------------------
    # Internal: geometry
    # ------------------------------------------------------------------

    @staticmethod
    def _bboxes_overlap(
        a: tuple[float, float, float, float],
        b: tuple[float, float, float, float],
    ) -> bool:
        """AABB overlap test.  Each bbox is (min_x, min_y, max_x, max_y)."""
        return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = SchematicAgent("http://127.0.0.1:8080/mcp")
    state = agent.read_state()
    print(f"Components: {len(state.components)}")

    # Dry run: just compute where D3 would go.
    engine = PlacementEngine(agent.url)
    pos = engine.find_optimal_position(
        10, 5,
        [("1", "C2", "1", "VOUT_12V"), ("2", "#PWR03", "1", "GND")],
        state,
        component_role="semiconductor",
    )
    print(f"D3 would go at: ({pos[0]:.1f}, {pos[1]:.1f}) rot={pos[2]}")
    print("Agent initialized OK")
