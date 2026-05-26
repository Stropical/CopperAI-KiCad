"""Constraint-based schematic placement & routing engine.

No ML — pure geometry, graph algorithms, and constraint satisfaction.
Reads state from live KiCad MCP, computes optimal positions and wire routes.

Usage:
    engine = PlacementEngine("http://127.0.0.1:8080/mcp")
    state = engine.read_state()

    # Place a new component optimally
    result = engine.place_and_wire(
        library="Device", symbol="D_TVS", reference="D2", value="SMBJ18A",
        connections=[("1", "D1", "1", "VIN_RAW"), ("2", "#PWR03", "1", "GND")],
    )

    # Move a component and re-wire
    result = engine.move_and_reroute("D1", new_x=105.0, new_y=90.0)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Pin:
    ref: str
    number: str
    x: float
    y: float
    orientation: float = 0.0  # degrees: 0=right, 90=up, 180=left, 270=down


@dataclass
class Component:
    ref: str
    x: float
    y: float
    w: float
    h: float
    rotation: float = 0.0
    is_power: bool = False
    pins: list[Pin] = field(default_factory=list)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) with 1mm margin."""
        hw, hh = self.w / 2 + 1.0, self.h / 2 + 1.0
        return (self.x - hw, self.y - hh, self.x + hw, self.y + hh)


@dataclass
class Net:
    name: str
    pins: list[tuple[str, str]]  # [(ref, pin_number), ...]


@dataclass
class WireSegment:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class SchematicState:
    components: list[Component] = field(default_factory=list)
    nets: list[Net] = field(default_factory=list)
    grid: float = 0.635  # KiCad default schematic grid


def _bboxes_overlap(a: tuple, b: tuple) -> bool:
    """AABB overlap test. Each bbox is (min_x, min_y, max_x, max_y)."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _segment_intersects_bbox(x1, y1, x2, y2, bbox) -> bool:
    """Check if a horizontal or vertical wire segment passes through a bbox."""
    bx1, by1, bx2, by2 = bbox
    # Horizontal segment
    if abs(y1 - y2) < 0.01:
        sx, ex = min(x1, x2), max(x1, x2)
        return by1 < y1 < by2 and sx < bx2 and ex > bx1
    # Vertical segment
    if abs(x1 - x2) < 0.01:
        sy, ey = min(y1, y2), max(y1, y2)
        return bx1 < x1 < bx2 and sy < by2 and ey > by1
    return False


# ---------------------------------------------------------------------------
# KiCad MCP client
# ---------------------------------------------------------------------------

def _call(url: str, tool: str, args: dict | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": args or {}}}
    resp = requests.post(url, json=payload, timeout=30)
    content = resp.json().get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"_text": text}


# ---------------------------------------------------------------------------
# Placement Engine
# ---------------------------------------------------------------------------

class PlacementEngine:
    def __init__(self, kicad_url: str = "http://127.0.0.1:8080/mcp"):
        self.url = kicad_url

    # === State reading ===

    def read_state(self) -> SchematicState:
        """Read full schematic state from live KiCad."""
        state = SchematicState()

        # Components + bounding boxes.
        bounds = _call(self.url, "get_all_bounds")
        if not isinstance(bounds, list):
            return state

        for b in bounds:
            ref = b.get("reference", "")
            if not ref:
                continue
            comp = Component(
                ref=ref, x=b.get("cx_mm", 0), y=b.get("cy_mm", 0),
                w=b.get("width_mm", 5), h=b.get("height_mm", 5),
                is_power=ref.startswith("#") or ref.startswith("PWR"),
            )
            # Get pin positions.
            pins_data = _call(self.url, "get_component_pins", {"reference": ref})
            if isinstance(pins_data, list):
                for p in pins_data:
                    comp.pins.append(Pin(
                        ref=ref, number=p.get("pin_number", ""),
                        x=p.get("x_mm", 0), y=p.get("y_mm", 0),
                        orientation=p.get("orientation", 0),
                    ))
            state.components.append(comp)

        # Netlist.
        netlist = _call(self.url, "get_netlist")
        if isinstance(netlist, list):
            for net in netlist:
                name = net.get("name", "")
                pins = [(p.get("ref", ""), p.get("pin", "")) for p in net.get("pins", [])]
                state.nets.append(Net(name=name, pins=pins))

        return state

    # === Placement solver ===

    def find_optimal_position(
        self,
        comp_w: float,
        comp_h: float,
        connections: list[tuple[str, str, str, str]],  # [(my_pin, target_ref, target_pin, net)]
        state: SchematicState,
        component_role: str = "passive",  # passive, ic, connector, semiconductor
    ) -> tuple[float, float, int]:
        """Find best (x, y, rotation) for a new component.

        Uses empirically-derived rules from analysis of 1344 real schematics:
        - Connected passives go on the SAME ROW (92% of real schematics)
        - Typical connected distance: 15mm (passives), 8.5mm (passive-semiconductor)
        - Bypass caps: below and left of IC (dx=-16mm, dy=+10mm)
        - Connectors: on schematic edges (73%)
        - VCC top, GND bottom
        """
        grid = state.grid

        # Find all target pin positions and their parent components.
        target_pins: list[Pin] = []
        target_comps: list[Component] = []
        for my_pin, tgt_ref, tgt_pin, net in connections:
            for comp in state.components:
                if comp.ref == tgt_ref:
                    for pin in comp.pins:
                        if pin.number == tgt_pin:
                            target_pins.append(pin)
                            target_comps.append(comp)

        if not target_pins:
            empty = _call(self.url, "find_empty_space", {"width_mm": comp_w + 4, "height_mm": comp_h + 4})
            if isinstance(empty, dict) and empty.get("found"):
                return (empty["x_mm"], empty["y_mm"], 0)
            return (100.0, 100.0, 0)

        # --- Rule-based seed positions ---
        # Find the PRIMARY connection (first non-power pin).
        primary_pin = None
        primary_comp = None
        for pin, comp in zip(target_pins, target_comps):
            if not comp.is_power:
                primary_pin = pin
                primary_comp = comp
                break
        if primary_pin is None:
            primary_pin = target_pins[0]
            primary_comp = target_comps[0]

        # Determine ideal spacing based on component types.
        # From analysis: passive-passive median=15mm, passive-semiconductor=8.5mm
        # Special case: bypass caps near IC power pins should be VERY close (5mm).
        is_bypass_cap = (component_role == "passive" and
                         any(c.ref.startswith("U") or c.ref.startswith("IC") for c in target_comps if not c.is_power))
        is_inline = (len(target_comps) >= 2 and
                     all(not c.is_power for c in target_comps))  # component sits between two others

        if is_bypass_cap:
            ideal_spacing = 5.0   # bypass caps RIGHT NEXT to IC pin
        elif component_role == "passive":
            ideal_spacing = 12.7  # 5 grid units
        elif component_role == "semiconductor":
            ideal_spacing = 10.0
        elif component_role == "connector":
            ideal_spacing = 15.0
        else:
            ideal_spacing = 12.7

        # Generate seed positions based on rules:
        seeds: list[tuple[float, float, int, str]] = []  # (x, y, rotation, reason)

        # Rule 1: SAME ROW as primary connection (92% of real schematics).
        # Place to the right (signal flow) at ideal_spacing.
        seeds.append((primary_pin.x + ideal_spacing, primary_pin.y, 0, "same_row_right"))
        seeds.append((primary_pin.x - ideal_spacing, primary_pin.y, 0, "same_row_left"))

        # Rule 2: SAME ROW, rotated 90° (for vertical components like caps).
        seeds.append((primary_pin.x + ideal_spacing, primary_pin.y, 90, "same_row_right_rot90"))
        seeds.append((primary_pin.x - ideal_spacing, primary_pin.y, 90, "same_row_left_rot90"))

        # Rule 3: BELOW primary (for bypass caps near ICs: dy=+10mm from analysis).
        seeds.append((primary_pin.x, primary_pin.y + 10.0, 0, "below"))
        seeds.append((primary_pin.x, primary_pin.y + 10.0, 90, "below_rot90"))

        # Rule 4: ABOVE primary.
        seeds.append((primary_pin.x, primary_pin.y - 10.0, 0, "above"))
        seeds.append((primary_pin.x, primary_pin.y - 10.0, 90, "above_rot90"))

        # Rule 5: Near each connected pin directly (minimize wire length).
        for pin in target_pins:
            for offset in [(ideal_spacing, 0), (-ideal_spacing, 0), (0, ideal_spacing), (0, -ideal_spacing)]:
                seeds.append((pin.x + offset[0], pin.y + offset[1], 0, f"near_{pin.ref}"))

        # Rule 6: INLINE between two non-power connections (e.g., series resistor).
        non_power_pins = [p for p, c in zip(target_pins, target_comps) if not c.is_power]
        if len(non_power_pins) >= 2:
            mid_x = sum(p.x for p in non_power_pins) / len(non_power_pins)
            mid_y = sum(p.y for p in non_power_pins) / len(non_power_pins)
            seeds.append((mid_x, mid_y, 0, "inline_midpoint"))
            seeds.append((mid_x, mid_y, 90, "inline_midpoint_rot90"))
            # Also try slight offsets from midpoint.
            seeds.append((mid_x, mid_y - ideal_spacing, 0, "inline_above"))
            seeds.append((mid_x, mid_y + ideal_spacing, 0, "inline_below"))

        # Rule 7: For bypass caps, RIGHT NEXT to IC pin (above or below, very close).
        if is_bypass_cap:
            for pin, comp in zip(target_pins, target_comps):
                if comp.ref.startswith("U") or comp.ref.startswith("IC"):
                    # Directly below the IC power pin
                    seeds.append((pin.x, pin.y + 5.0, 90, "bypass_below_pin"))
                    seeds.append((pin.x, pin.y - 5.0, 90, "bypass_above_pin"))
                    seeds.append((pin.x + 5.0, pin.y, 0, "bypass_right_pin"))
                    seeds.append((pin.x - 5.0, pin.y, 0, "bypass_left_pin"))

        # Expand each seed into a grid of fine candidates.
        candidates: list[tuple[float, float, int, float]] = []

        for sx, sy, rot, reason in seeds:
            rw = comp_w if rot == 0 else comp_h
            rh = comp_h if rot == 0 else comp_w

            for fine_dx in range(-3, 4):
                for fine_dy in range(-3, 4):
                    x = round((sx + fine_dx * grid * 2) / grid) * grid
                    y = round((sy + fine_dy * grid * 2) / grid) * grid

                    # Hard constraint: no overlaps (3mm margin for better spacing).
                    new_bbox = (x - rw/2 - 3.0, y - rh/2 - 3.0, x + rw/2 + 3.0, y + rh/2 + 3.0)
                    overlaps = any(_bboxes_overlap(new_bbox, c.bbox) for c in state.components)
                    if overlaps:
                        continue

                    # --- SCORING (lower = better) ---
                    score = 0.0

                    # S1: Wire length — weight SIGNAL connections heavily, POWER connections lightly.
                    # Power pins (GND, VCC) just need a short vertical drop, so they
                    # shouldn't pull placement left/right. Signal pins determine position.
                    wire_length = 0
                    for pin, comp in zip(target_pins, target_comps):
                        dist = abs(pin.x - x) + abs(pin.y - y)
                        if comp.is_power:
                            wire_length += dist * 0.2  # power: low weight (just needs vertical drop)
                        else:
                            wire_length += dist * 1.5  # signal: high weight (determines position)
                    score += wire_length

                    # S2: Row alignment with connected pins.
                    # From analysis: 92% of connected passives share Y ±5mm.
                    # Check alignment with ALL non-power target pins, not just primary.
                    for tp in target_pins:
                        tp_comp = next((c for c in target_comps if c.ref == tp.ref), None)
                        if tp_comp and tp_comp.is_power:
                            continue
                        y_diff = abs(y - tp.y)
                        if y_diff < 2.0:
                            score -= 25.0  # very strong reward for exact same row
                        elif y_diff < 5.0:
                            score -= 10.0  # good for close row

                    # S3: Proximity to primary (want ~ideal_spacing, not too close/far).
                    dist_to_primary = math.hypot(x - primary_pin.x, y - primary_pin.y)
                    if dist_to_primary < 5.0:
                        score += 15.0  # too close
                    elif dist_to_primary > 40.0:
                        score += 10.0  # too far
                    else:
                        # Sweet spot: near ideal_spacing
                        spacing_err = abs(dist_to_primary - ideal_spacing)
                        score += spacing_err * 0.5

                    # S4: Signal flow — prefer placing to the right of input connections
                    # and to the left of output connections.
                    # Simple: reward positions that maintain left-to-right ordering.
                    for tgt_comp in target_comps:
                        if not tgt_comp.is_power:
                            if x > tgt_comp.x:
                                score -= 2.0  # downstream = good for signal flow

                    # S5: Alignment with other components on the same net.
                    for net in state.nets:
                        refs_on_net = set(r for r, p in net.pins)
                        for tgt_comp in target_comps:
                            if tgt_comp.ref in refs_on_net:
                                # Other components on this net — prefer aligning Y with them.
                                for other in state.components:
                                    if other.ref in refs_on_net and other.ref != tgt_comp.ref:
                                        if abs(y - other.y) < 3.0:
                                            score -= 3.0  # same row as net peer

                    # S6: Bypass cap bonus — strong pull toward IC power pin (within 5mm).
                    if is_bypass_cap:
                        for pin, comp in zip(target_pins, target_comps):
                            if comp.ref.startswith("U") or comp.ref.startswith("IC"):
                                d = math.hypot(x - pin.x, y - pin.y)
                                if d < 5.0:
                                    score -= 50.0  # very strong pull
                                elif d < 10.0:
                                    score -= 20.0

                    # S7: TVS/protection diodes — same column as their protected signal pin.
                    if component_role == "semiconductor":
                        signal_pin = next((p for p, c in zip(target_pins, target_comps) if not c.is_power), None)
                        if signal_pin:
                            if abs(x - signal_pin.x) < grid * 2:
                                score -= 30.0  # strong same-column bonus

                    # S8: Inline series components — same row at midpoint between two non-power targets.
                    if is_inline and len(non_power_pins) >= 2:
                        mid_y = sum(p.y for p in non_power_pins) / len(non_power_pins)
                        if abs(y - mid_y) < grid * 2:
                            score -= 25.0  # strong row alignment bonus

                    # S9: Connectors prefer schematic edges (73% of connectors in analysis).
                    if component_role == "connector":
                        xs = [c.x for c in state.components if not c.is_power]
                        if xs:
                            x_min, x_max = min(xs), max(xs)
                            x_range = max(x_max - x_min, 1)
                            edge_pct = min(abs(x - x_min), abs(x - x_max)) / x_range
                            if edge_pct < 0.15:
                                score -= 15.0  # on edge = good for connectors

                    candidates.append((x, y, rot, score))

        if not candidates:
            empty = _call(self.url, "find_empty_space", {
                "width_mm": comp_w + 6, "height_mm": comp_h + 6,
                "near_x": primary_pin.x, "near_y": primary_pin.y,
            })
            if isinstance(empty, dict) and empty.get("found"):
                return (empty["x_mm"], empty["y_mm"], 0)
            return (primary_pin.x + 15, primary_pin.y, 0)

        candidates.sort(key=lambda c: c[3])
        best = candidates[0]
        return (best[0], best[1], best[2])

    # === Wire routing ===

    def route_wire(
        self,
        pin_a: Pin,
        pin_b: Pin,
        state: SchematicState,
    ) -> list[WireSegment]:
        """Route a Manhattan wire between two pins, avoiding component bounding boxes.

        Returns list of wire segments (horizontal/vertical only).
        """
        ax, ay = pin_a.x, pin_a.y
        bx, by = pin_b.x, pin_b.y

        # Collect all component bboxes (except the two endpoints' components).
        obstacles = []
        for comp in state.components:
            if comp.ref in (pin_a.ref, pin_b.ref):
                continue
            if comp.is_power:
                continue
            obstacles.append(comp.bbox)

        # Try L-path: horizontal first, then vertical.
        l_path_hv = [
            WireSegment(ax, ay, bx, ay),  # horizontal
            WireSegment(bx, ay, bx, by),  # vertical
        ]
        hv_blocked = any(
            _segment_intersects_bbox(s.x1, s.y1, s.x2, s.y2, obs)
            for s in l_path_hv for obs in obstacles
        )

        # Try L-path: vertical first, then horizontal.
        l_path_vh = [
            WireSegment(ax, ay, ax, by),  # vertical
            WireSegment(ax, by, bx, by),  # horizontal
        ]
        vh_blocked = any(
            _segment_intersects_bbox(s.x1, s.y1, s.x2, s.y2, obs)
            for s in l_path_vh for obs in obstacles
        )

        # Straight line (if aligned).
        if abs(ax - bx) < 0.01 or abs(ay - by) < 0.01:
            straight = [WireSegment(ax, ay, bx, by)]
            s_blocked = any(
                _segment_intersects_bbox(s.x1, s.y1, s.x2, s.y2, obs)
                for s in straight for obs in obstacles
            )
            if not s_blocked:
                return straight

        if not hv_blocked:
            # Filter out zero-length segments.
            return [s for s in l_path_hv if abs(s.x1-s.x2) > 0.01 or abs(s.y1-s.y2) > 0.01]

        if not vh_blocked:
            return [s for s in l_path_vh if abs(s.x1-s.x2) > 0.01 or abs(s.y1-s.y2) > 0.01]

        # Both L-paths blocked — try U-path (detour around obstacles).
        # Find a clear Y-coordinate above or below both pins.
        grid = state.grid
        for offset in range(1, 20):
            for direction in [1, -1]:
                detour_y = round((min(ay, by) + direction * offset * grid * 4) / grid) * grid
                u_path = [
                    WireSegment(ax, ay, ax, detour_y),
                    WireSegment(ax, detour_y, bx, detour_y),
                    WireSegment(bx, detour_y, bx, by),
                ]
                u_blocked = any(
                    _segment_intersects_bbox(s.x1, s.y1, s.x2, s.y2, obs)
                    for s in u_path for obs in obstacles
                )
                if not u_blocked:
                    return [s for s in u_path if abs(s.x1-s.x2) > 0.01 or abs(s.y1-s.y2) > 0.01]

        # Fallback: just do L-path H-V even if blocked (better than nothing).
        return [s for s in l_path_hv if abs(s.x1-s.x2) > 0.01 or abs(s.y1-s.y2) > 0.01]

    # === Place and wire ===

    def place_and_wire(
        self,
        library: str,
        symbol: str,
        reference: str,
        value: str,
        connections: list[tuple[str, str, str, str]],  # [(my_pin, target_ref, target_pin, net_name)]
        state: SchematicState | None = None,
        dry_run: bool = False,
        component_role: str = "passive",  # passive, ic, connector, semiconductor
    ) -> dict:
        """Place a new component at the optimal position and wire all connections.

        Returns dict with placement position and wiring results.
        """
        if state is None:
            state = self.read_state()

        # Get component dimensions.
        comp_data = _call(self.url, "batch_get_component_data", {
            "components": [{"library": library, "symbol": symbol}]
        })
        comp_w, comp_h = 10.0, 5.0  # defaults
        if isinstance(comp_data, dict) and "_text" in comp_data:
            try:
                parsed = json.loads(comp_data["_text"])
                if isinstance(parsed, list) and parsed:
                    comp_w = parsed[0].get("width_mm", 10)
                    comp_h = parsed[0].get("height_mm", 5)
            except (json.JSONDecodeError, TypeError):
                pass

        # Find optimal position.
        x, y, rotation = self.find_optimal_position(comp_w, comp_h, connections, state, component_role=component_role)

        result = {
            "reference": reference,
            "position": {"x": round(x, 2), "y": round(y, 2), "rotation": rotation},
            "connections_planned": len(connections),
            "wires": [],
        }

        if dry_run:
            result["dry_run"] = True
            print(f"[DRY RUN] Would place {reference} ({value}) at ({x:.1f}, {y:.1f}) rot={rotation}")
            return result

        # Place the component.
        place_result = _call(self.url, "place_component", {
            "library": library, "symbol": symbol, "reference": reference,
            "value": value, "x": round(x, 2), "y": round(y, 2), "rotation": rotation,
        })
        result["place_result"] = place_result.get("_text", str(place_result))[:100]

        # Re-read state to get the new pin positions.
        state = self.read_state()
        new_comp = next((c for c in state.components if c.ref == reference), None)
        if new_comp is None:
            result["error"] = f"Component {reference} not found after placement"
            return result

        # Wire each connection.
        wired = 0
        for my_pin_num, tgt_ref, tgt_pin_num, net_name in connections:
            # Find my pin.
            my_pin = next((p for p in new_comp.pins if p.number == my_pin_num), None)
            # Find target pin.
            tgt_comp = next((c for c in state.components if c.ref == tgt_ref), None)
            tgt_pin = None
            if tgt_comp:
                tgt_pin = next((p for p in tgt_comp.pins if p.number == tgt_pin_num), None)

            if my_pin is None or tgt_pin is None:
                result["wires"].append({
                    "from": f"{reference}.{my_pin_num}",
                    "to": f"{tgt_ref}.{tgt_pin_num}",
                    "error": "Pin not found",
                })
                continue

            # Route the wire.
            segments = self.route_wire(my_pin, tgt_pin, state)

            # Apply via add_wire.
            wire_segments = [{"x1": s.x1, "y1": s.y1, "x2": s.x2, "y2": s.y2} for s in segments]
            if wire_segments:
                wire_result = _call(self.url, "add_wire", {"segments": wire_segments})
                wired += 1

            result["wires"].append({
                "from": f"{reference}.{my_pin_num}",
                "to": f"{tgt_ref}.{tgt_pin_num}",
                "net": net_name,
                "segments": len(segments),
                "total_length": sum(abs(s.x2-s.x1) + abs(s.y2-s.y1) for s in segments),
            })

        result["wired"] = wired

        # Verify.
        erc = _call(self.url, "erc_check")
        if isinstance(erc, list):
            result["dangling_after"] = len(erc)
        elif isinstance(erc, dict):
            result["erc"] = erc.get("_text", "")[:100]

        return result

    # === Move and re-route ===

    def move_and_reroute(
        self,
        reference: str,
        new_x: float,
        new_y: float,
        state: SchematicState | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Move a component and re-wire all its connections.

        1. Find all pins on this component and their connected peers
        2. Disconnect old wires at old pin positions
        3. Move component to new position
        4. Re-route wires from new pin positions to peers
        """
        if state is None:
            state = self.read_state()

        comp = next((c for c in state.components if c.ref == reference), None)
        if comp is None:
            return {"error": f"Component {reference} not found"}

        old_x, old_y = comp.x, comp.y

        # Find all connected pins from netlist.
        peer_connections: list[tuple[str, str, str, str]] = []  # (my_pin, peer_ref, peer_pin, net)
        for net in state.nets:
            my_pins_on_net = [(r, p) for r, p in net.pins if r == reference]
            peer_pins_on_net = [(r, p) for r, p in net.pins if r != reference]
            for my_ref, my_pin in my_pins_on_net:
                for peer_ref, peer_pin in peer_pins_on_net:
                    peer_connections.append((my_pin, peer_ref, peer_pin, net.name))

        result = {
            "reference": reference,
            "old_position": {"x": old_x, "y": old_y},
            "new_position": {"x": new_x, "y": new_y},
            "connections_to_reroute": len(peer_connections),
        }

        if dry_run:
            result["dry_run"] = True
            print(f"[DRY RUN] Would move {reference} ({old_x:.1f},{old_y:.1f}) -> ({new_x:.1f},{new_y:.1f})")
            print(f"  Would re-route {len(peer_connections)} connections")
            for c in peer_connections:
                print(f"    {reference}.{c[0]} -> {c[1]}.{c[2]} (net: {c[3]})")
            return result

        # Disconnect old wires from this component's pins.
        for pin in comp.pins:
            _call(self.url, "disconnect_pin", {
                "reference": reference, "pin_number": pin.number,
            })

        # Move the component (simple move, no chunk).
        _call(self.url, "move_component", {
            "reference": reference,
            "x_mm": round(new_x, 2),
            "y_mm": round(new_y, 2),
        })

        # Re-read state for new pin positions.
        state = self.read_state()
        comp = next((c for c in state.components if c.ref == reference), None)
        if comp is None:
            result["error"] = "Component lost after move"
            return result

        # Re-route each connection.
        wired = 0
        result["wires"] = []
        for my_pin_num, peer_ref, peer_pin_num, net_name in peer_connections:
            my_pin = next((p for p in comp.pins if p.number == my_pin_num), None)
            peer_comp = next((c for c in state.components if c.ref == peer_ref), None)
            peer_pin = None
            if peer_comp:
                peer_pin = next((p for p in peer_comp.pins if p.number == peer_pin_num), None)

            if my_pin is None or peer_pin is None:
                result["wires"].append({"error": f"Pin not found: {reference}.{my_pin_num} -> {peer_ref}.{peer_pin_num}"})
                continue

            segments = self.route_wire(my_pin, peer_pin, state)
            wire_segs = [{"x1": s.x1, "y1": s.y1, "x2": s.x2, "y2": s.y2} for s in segments]
            if wire_segs:
                _call(self.url, "add_wire", {"segments": wire_segs})
                wired += 1

            result["wires"].append({
                "from": f"{reference}.{my_pin_num}", "to": f"{peer_ref}.{peer_pin_num}",
                "net": net_name, "segments": len(segments),
            })

        result["wired"] = wired

        # Verify.
        erc = _call(self.url, "erc_check")
        if isinstance(erc, list):
            result["dangling_after"] = len(erc)

        return result


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    engine = PlacementEngine()
    state = engine.read_state()
    print(f"Components: {len(state.components)}")
    for c in state.components:
        if not c.is_power:
            print(f"  {c.ref:8s} @ ({c.x:.1f}, {c.y:.1f}) {c.w:.0f}x{c.h:.0f}mm  pins={len(c.pins)}")
    print(f"Nets: {len(state.nets)}")
    for n in state.nets[:5]:
        print(f"  {n.name}: {n.pins}")
