"""V2 Incremental constraint-based placer.

Places components one at a time, guided by semantic roles and net connectivity.
No hardcoded position templates — works for any block type.

Core principle: each new component is positioned so its connecting pin
aligns with the pin it shares a net with on an already-placed component,
offset by the role's semantic direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .placer import compute_pin_anchors, effective_size, snap_to_grid
from .schema import BlockSpec, Component, ComponentKind, Net, PinAnchor, Placement
from .templates import (
    BlockTemplate,
    CAP_TEMPLATE,
    RESISTOR_TEMPLATE,
    INDUCTOR_TEMPLATE,
    DIODE_TEMPLATE,
    BUCK_IC,
    LDO_IC,
    OPAMP_IC,
    ComponentTemplate,
    PinOffset,
)


# ── Semantic roles ──────────────────────────────────────────

class Role(str, Enum):
    anchor = "anchor"
    input_decoupling = "input_decoupling"
    output_decoupling = "output_decoupling"
    power_path = "power_path"
    feedback_upper = "feedback_upper"
    feedback_lower = "feedback_lower"
    enable = "enable"
    bootstrap = "bootstrap"
    bypass = "bypass"
    feedback_path = "feedback_path"
    grounding = "grounding"
    protection = "protection"
    generic = "generic"


@dataclass(frozen=True)
class RoleParams:
    dx: int   # preferred offset direction from shared pin
    dy: int
    distance: float  # offset distance in mm
    priority: int     # lower = placed earlier
    chain: str | None = None


ROLE_MAP: dict[ComponentKind, Role] = {
    ComponentKind.regulator_ic:     Role.anchor,
    ComponentKind.ldo_ic:           Role.anchor,
    ComponentKind.opamp_ic:         Role.anchor,
    ComponentKind.input_cap:        Role.input_decoupling,
    ComponentKind.output_cap:       Role.output_decoupling,
    ComponentKind.inductor:         Role.power_path,
    ComponentKind.fb_top:           Role.feedback_upper,
    ComponentKind.fb_bottom:        Role.feedback_lower,
    ComponentKind.enable_resistor:  Role.enable,
    ComponentKind.bootstrap_cap:    Role.bootstrap,
    ComponentKind.bypass_cap:       Role.bypass,
    ComponentKind.feedback_resistor: Role.feedback_path,
    ComponentKind.gain_resistor:    Role.grounding,
    ComponentKind.ground_resistor:  Role.grounding,
    ComponentKind.input_resistor:   Role.input_decoupling,
    ComponentKind.diode:            Role.protection,
    ComponentKind.generic:          Role.generic,
}

ROLE_PARAMS: dict[Role, RoleParams] = {
    Role.anchor:            RoleParams(0, 0, 0, 0),
    Role.power_path:        RoleParams(+1, 0, 8, 1),
    Role.input_decoupling:  RoleParams(-1, 0, 15, 2),
    Role.output_decoupling: RoleParams(+1, 0, 20, 3),
    Role.feedback_upper:    RoleParams(0, +1, 15, 4),
    Role.feedback_lower:    RoleParams(0, +1, 12, 5, chain="feedback_upper"),
    Role.bootstrap:         RoleParams(0, -1, 18, 6),
    Role.enable:            RoleParams(-1, +1, 15, 7),
    Role.bypass:            RoleParams(-1, -1, 12, 8),
    Role.feedback_path:     RoleParams(+1, -1, 20, 9),
    Role.grounding:         RoleParams(-1, +1, 20, 10),
    Role.protection:        RoleParams(+1, -1, 10, 11),
    Role.generic:           RoleParams(+1, 0, 20, 12),
}


# ── Component template registry ─────────────────────────────

GENERIC_CT = ComponentTemplate(
    width=7.62, height=7.62,
    pin_offsets={"1": PinOffset(3.81, -2.54), "2": PinOffset(3.81, 10.16)},
)

COMPONENT_REGISTRY: dict[ComponentKind, ComponentTemplate] = {
    ComponentKind.regulator_ic:     BUCK_IC,
    ComponentKind.ldo_ic:           LDO_IC,
    ComponentKind.opamp_ic:         OPAMP_IC,
    ComponentKind.input_cap:        CAP_TEMPLATE,
    ComponentKind.output_cap:       CAP_TEMPLATE,
    ComponentKind.inductor:         INDUCTOR_TEMPLATE,
    ComponentKind.fb_top:           RESISTOR_TEMPLATE,
    ComponentKind.fb_bottom:        RESISTOR_TEMPLATE,
    ComponentKind.enable_resistor:  RESISTOR_TEMPLATE,
    ComponentKind.bootstrap_cap:    CAP_TEMPLATE,
    ComponentKind.bypass_cap:       CAP_TEMPLATE,
    ComponentKind.feedback_resistor: RESISTOR_TEMPLATE,
    ComponentKind.gain_resistor:    RESISTOR_TEMPLATE,
    ComponentKind.ground_resistor:  RESISTOR_TEMPLATE,
    ComponentKind.input_resistor:   RESISTOR_TEMPLATE,
    ComponentKind.diode:            DIODE_TEMPLATE,
    ComponentKind.generic:          GENERIC_CT,
}


def _get_ct(kind: ComponentKind) -> ComponentTemplate:
    return COMPONENT_REGISTRY.get(kind, GENERIC_CT)


def _get_role(kind: ComponentKind) -> Role:
    return ROLE_MAP.get(kind, Role.generic)


# ── Connectivity analysis ────────────────────────────────────

@dataclass
class SharedPin:
    other_id: str
    other_pin: str
    my_pin: str
    net_name: str
    is_gnd: bool


def _build_pin_connections(spec: BlockSpec) -> dict[str, list[SharedPin]]:
    result: dict[str, list[SharedPin]] = {c.id: [] for c in spec.components}
    for net in spec.nets:
        members = [(m.split(".")[0], m.split(".")[1]) for m in net.members if "." in m]
        is_gnd = net.name.upper() == "GND"
        for i, (cid_a, pin_a) in enumerate(members):
            for j, (cid_b, pin_b) in enumerate(members):
                if i != j:
                    result[cid_a].append(SharedPin(
                        other_id=cid_b, other_pin=pin_b,
                        my_pin=pin_a, net_name=net.name, is_gnd=is_gnd,
                    ))
    return result


def _find_ic(spec: BlockSpec) -> Component:
    for c in spec.components:
        if c.kind.value.endswith("_ic"):
            return c
    return spec.components[0]


def _compute_order(spec: BlockSpec) -> list[Component]:
    ic = _find_ic(spec)
    others = [c for c in spec.components if c.id != ic.id]
    others.sort(key=lambda c: ROLE_PARAMS[_get_role(c.kind)].priority)
    return [ic] + others


# ── Pin-aware anchor finding ─────────────────────────────────

@dataclass
class AnchorInfo:
    pin_pos: tuple[float, float]
    my_pin: str | None
    anchor_comp: Component | None = None
    anchor_pin: str | None = None


def _find_shared_pin_position(
    comp: Component,
    placed_map: dict[str, tuple[Component, Placement]],
    pin_conns: dict[str, list[SharedPin]],
) -> AnchorInfo:
    """Find the actual pin position on a placed component that shares a net
    with this component. Prefers non-GND, non-VCC connections."""
    best_pos = None
    best_my_pin = None
    best_priority = 999
    best_other_comp = None
    best_other_pin = None

    for sp in pin_conns.get(comp.id, []):
        if sp.other_id not in placed_map:
            continue

        other_comp, other_pl = placed_map[sp.other_id]
        ct = _get_ct(other_comp.kind)
        po = ct.pin_offsets.get(sp.other_pin)
        if po is None:
            continue

        pin_x = other_pl.x + po.x
        pin_y = other_pl.y + po.y

        # Priority: IC signal pins > non-IC signal > power > GND
        # Always prefer anchoring to the IC for predictable layout
        is_ic = other_comp.kind.value.endswith("_ic")
        if sp.is_gnd:
            priority = 5
        elif sp.net_name.upper() in ("VCC", "VDD", "V+"):
            priority = 3 if is_ic else 4
        else:
            priority = 0 if is_ic else 1

        if priority < best_priority or best_pos is None:
            best_pos = (pin_x, pin_y)
            best_my_pin = sp.my_pin
            best_priority = priority
            best_other_comp = other_comp
            best_other_pin = sp.other_pin

    if best_pos is None:
        for _, (c, p) in placed_map.items():
            ew, eh = effective_size(p)
            return AnchorInfo(
                pin_pos=(p.x + ew / 2, p.y + eh / 2),
                my_pin=None,
            )

    return AnchorInfo(
        pin_pos=best_pos,
        my_pin=best_my_pin,
        anchor_comp=best_other_comp,
        anchor_pin=best_other_pin,
    )


# Op-amp signal pins — when a component anchors to one of these, we want
# pin-level Y alignment (dy=0) rather than the role's default vertical offset.
_OPAMP_SIGNAL_PINS = frozenset({"IN+", "IN-", "OUT"})


def _opamp_adjusted_params(
    role: Role,
    anchor_info: AnchorInfo,
    pin_conns: dict[str, list[SharedPin]] | None = None,
    comp_id: str | None = None,
) -> RoleParams:
    """Return role params adjusted for op-amp pin-level alignment.

    When a component anchors to an opamp signal pin (IN+, IN-, OUT), the
    default role dy offset would push it away from the pin's Y level, making
    resistors stack vertically instead of aligning with their respective pins.

    Rules:
    - input_decoupling connecting to IN+/IN-: keep at pin Y level, push LEFT.
    - grounding connecting to opamp via a signal net (not GND/VCC):
      treat like input — push LEFT at pin Y level.
    - grounding connecting to opamp via GND/VCC: hang BELOW the pin.
    - generic connecting to IN+/IN-: keep at pin Y level, push LEFT.
    - feedback_path is handled separately via _compute_opamp_feedback_pos.
    """
    base = ROLE_PARAMS[role]

    if anchor_info.anchor_comp is None:
        return base
    if anchor_info.anchor_comp.kind != ComponentKind.opamp_ic:
        return base
    if anchor_info.anchor_pin not in _OPAMP_SIGNAL_PINS:
        return base

    if role == Role.feedback_path:
        # Handled by _compute_opamp_feedback_pos — return base as fallback
        return base

    if role == Role.grounding:
        # Determine if this grounding component has a pin going to GND/power.
        # If it does, it should hang BELOW the opamp pin (e.g. ground_resistor
        # from IN+ to GND).  If all its nets are signal nets, it should sit
        # LEFT at pin Y level (e.g. gain_resistor feeding IN+ from a signal).
        has_gnd_pin = False
        if pin_conns is not None and comp_id is not None:
            for sp in pin_conns.get(comp_id, []):
                if sp.is_gnd or sp.net_name.upper() in ("VCC", "VDD", "V+"):
                    has_gnd_pin = True
                    break

        if has_gnd_pin:
            # Component bridges signal pin to power/GND — push LEFT and BELOW
            # the pin so it clears the IC body (which extends ~20mm below the
            # top).  Using dx=-1 keeps it left of the IC; dy=+1 gives downward
            # preference for overlap resolution.
            return RoleParams(dx=-1, dy=+1, distance=15, priority=base.priority)
        else:
            # Pure signal connection — push LEFT at pin Y level
            return RoleParams(dx=-1, dy=0, distance=base.distance, priority=base.priority)

    if role in (Role.input_decoupling, Role.generic):
        # Keep at the IC pin's Y level — only offset horizontally.
        return RoleParams(dx=base.dx, dy=0, distance=base.distance, priority=base.priority)

    return base


def _compute_opamp_feedback_pos(
    comp: Component,
    placed_map: dict[str, tuple[Component, Placement]],
    pin_conns: dict[str, list[SharedPin]],
    grid: float,
) -> tuple[float, float] | None:
    """Compute feedback resistor position ABOVE the opamp IC.

    The feedback resistor bridges OUT back to IN-.  Place it centered
    horizontally above the IC body, with enough clearance for wires.
    Returns (x, y) for the component top-left, or None if no opamp found.
    """
    ct = _get_ct(comp.kind)

    # Find the opamp IC this component connects to
    ic_comp = None
    ic_pl = None
    for sp in pin_conns.get(comp.id, []):
        if sp.other_id not in placed_map:
            continue
        other_comp, other_pl = placed_map[sp.other_id]
        if other_comp.kind == ComponentKind.opamp_ic:
            ic_comp = other_comp
            ic_pl = other_pl
            break

    if ic_comp is None or ic_pl is None:
        return None

    ic_ct = _get_ct(ic_comp.kind)

    # Center the feedback resistor horizontally above the IC.
    # IC center X = ic_pl.x + ic_ct.width / 2
    # Resistor pin X offset = 2.54 (both pins at same X)
    # So resistor body X = IC_center_X - pin_offset_x
    center_x = ic_pl.x + ic_ct.width / 2
    res_x = snap_to_grid(center_x - ct.pin_offsets["1"].x, grid)

    # Place above the IC with clearance.
    # IC top edge = ic_pl.y
    # Resistor pin 2 (bottom pin) at res_y + 17.78 should be near or above IC top.
    # Give 5mm clearance above the IC for wires.
    res_y = snap_to_grid(ic_pl.y - ct.height - 5.0, grid)

    return res_x, res_y


def _compute_candidate(
    comp: Component,
    anchor_pin_pos: tuple[float, float],
    my_pin_name: str | None,
    params: RoleParams,
    grid: float,
) -> tuple[float, float]:
    """Position component so its connecting pin is offset from the anchor pin
    by the role's direction x distance."""
    ct = _get_ct(comp.kind)
    ax, ay = anchor_pin_pos

    target_pin_x = ax + params.dx * params.distance
    target_pin_y = ay + params.dy * params.distance

    if my_pin_name is not None:
        po = ct.pin_offsets.get(my_pin_name)
        if po is not None:
            return snap_to_grid(target_pin_x - po.x, grid), snap_to_grid(target_pin_y - po.y, grid)

    return snap_to_grid(target_pin_x - ct.width / 2, grid), snap_to_grid(target_pin_y - ct.height / 2, grid)


# ── Overlap resolution ───────────────────────────────────────

OVERLAP_MARGIN = 5.0


def _boxes_overlap(p1: Placement, p2: Placement) -> bool:
    w1, h1 = effective_size(p1)
    w2, h2 = effective_size(p2)
    return not (
        p1.x + w1 + OVERLAP_MARGIN <= p2.x or
        p2.x + w2 + OVERLAP_MARGIN <= p1.x or
        p1.y + h1 + OVERLAP_MARGIN <= p2.y or
        p2.y + h2 + OVERLAP_MARGIN <= p1.y
    )


def _resolve_overlaps(
    new_pl: Placement,
    existing: list[Placement],
    params: RoleParams,
    grid: float,
) -> Placement:
    """Push new_pl away from overlaps using minimum displacement.
    Prefers the role's direction, but falls back to pushing along the
    axis with less overlap to avoid cascading pushes."""
    for iteration in range(25):
        found_overlap = False
        for ex in existing:
            if not _boxes_overlap(new_pl, ex):
                continue
            found_overlap = True

            w1, h1 = effective_size(new_pl)
            w2, h2 = effective_size(ex)

            # Compute clear positions for each direction
            clear_right = snap_to_grid(ex.x + w2 + OVERLAP_MARGIN, grid)
            clear_left = snap_to_grid(ex.x - w1 - OVERLAP_MARGIN, grid)
            clear_below = snap_to_grid(ex.y + h2 + OVERLAP_MARGIN, grid)
            clear_above = snap_to_grid(ex.y - h1 - OVERLAP_MARGIN, grid)

            # Score each option: prefer role direction, minimize displacement
            options = []
            if params.dx >= 0:
                options.append((abs(clear_right - new_pl.x), clear_right, new_pl.y, "R"))
            if params.dx <= 0:
                options.append((abs(clear_left - new_pl.x), clear_left, new_pl.y, "L"))
            if params.dy >= 0:
                options.append((abs(clear_below - new_pl.y), new_pl.x, clear_below, "D"))
            if params.dy <= 0:
                options.append((abs(clear_above - new_pl.y), new_pl.x, clear_above, "U"))

            # If no preferred direction, try all
            if not options:
                options = [
                    (abs(clear_right - new_pl.x), clear_right, new_pl.y, "R"),
                    (abs(clear_left - new_pl.x), clear_left, new_pl.y, "L"),
                    (abs(clear_below - new_pl.y), new_pl.x, clear_below, "D"),
                    (abs(clear_above - new_pl.y), new_pl.x, clear_above, "U"),
                ]

            # Pick minimum displacement
            options.sort(key=lambda o: o[0])
            _, new_x, new_y, _ = options[0]
            new_pl.x = new_x
            new_pl.y = new_y

            break  # re-check all overlaps after each push

        if not found_overlap:
            break

    return new_pl


# ── Main incremental placement loop ─────────────────────────

def incremental_place(
    spec: BlockSpec,
    origin_x: float = 40.0,
    origin_y: float = 40.0,
    grid: float = 2.54,
) -> list[Placement]:
    pin_conns = _build_pin_connections(spec)
    order = _compute_order(spec)

    placed: list[Placement] = []
    placed_map: dict[str, tuple[Component, Placement]] = {}
    chain_anchor: dict[str, Placement] = {}

    for comp in order:
        role = _get_role(comp.kind)
        params = ROLE_PARAMS[role]
        ct = _get_ct(comp.kind)
        # effective_params may be adjusted for opamp pin alignment
        effective_params = params

        if role == Role.anchor:
            pl = Placement(
                id=comp.id,
                x=snap_to_grid(origin_x, grid),
                y=snap_to_grid(origin_y, grid),
                rotation=0, width=ct.width, height=ct.height,
            )
        elif params.chain is not None and params.chain in chain_anchor:
            head_pl = chain_anchor[params.chain]
            _, head_eh = effective_size(head_pl)
            pl = Placement(
                id=comp.id,
                x=head_pl.x,
                y=snap_to_grid(head_pl.y + head_eh + params.distance, grid),
                rotation=0, width=ct.width, height=ct.height,
            )
        else:
            anchor_info = _find_shared_pin_position(comp, placed_map, pin_conns)
            effective_params = _opamp_adjusted_params(role, anchor_info, pin_conns, comp.id)

            # Special placement for opamp feedback resistor
            fb_pos = None
            if role == Role.feedback_path and anchor_info.anchor_comp is not None \
                    and anchor_info.anchor_comp.kind == ComponentKind.opamp_ic:
                fb_pos = _compute_opamp_feedback_pos(comp, placed_map, pin_conns, grid)

            if fb_pos is not None:
                cx, cy = fb_pos
                # Override effective_params for overlap resolution direction
                effective_params = RoleParams(dx=0, dy=-1, distance=15, priority=effective_params.priority)
            else:
                cx, cy = _compute_candidate(comp, anchor_info.pin_pos, anchor_info.my_pin, effective_params, grid)
            pl = Placement(id=comp.id, x=cx, y=cy, rotation=0, width=ct.width, height=ct.height)

        if role != Role.anchor:
            pl = _resolve_overlaps(pl, placed, effective_params, grid)

        placed.append(pl)
        placed_map[comp.id] = (comp, pl)

        if role == Role.feedback_upper:
            chain_anchor["feedback_upper"] = pl

    return placed


# ── Template synthesis ───────────────────────────────────────

def _compute_net_route_order(spec: BlockSpec) -> list[str]:
    gnd = []
    signal = []
    for net in spec.nets:
        if net.name.upper() == "GND":
            gnd.append(net.name)
        else:
            signal.append((len(net.members), net.name))
    signal.sort(reverse=True)
    return [n for _, n in signal] + gnd


def synthesize_template(spec: BlockSpec) -> BlockTemplate:
    ct = {}
    instance_ct = {}
    for comp in spec.components:
        tmpl = _get_ct(comp.kind)
        ct[comp.kind.value] = tmpl
        instance_ct[comp.id] = tmpl
    return BlockTemplate(
        name=spec.block_type,
        component_templates=ct,
        relative_positions={},
        component_instance_templates=instance_ct,
        net_route_order=_compute_net_route_order(spec),
        grid=2.54,
    )


def place(spec: BlockSpec) -> tuple[list[Placement], list[PinAnchor], BlockTemplate]:
    template = synthesize_template(spec)
    placements = incremental_place(spec)
    anchors = compute_pin_anchors(spec, placements, template)
    return placements, anchors, template
