"""V3 Physics-based placer with rotation.

Places components using a 2D Verlet physics simulation with springs,
AABB collision, and angular dynamics. Springs create both linear force
(pulling pins together) and torque (rotating the component so pins face
their targets). After settling, rotation snaps to the nearest 90 degrees.

Wire segments from already-routed nets become thin static collision bodies,
preventing subsequent components from being placed on top of wires.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .placer import compute_pin_anchors, effective_size, snap_to_grid
from .schema import BlockSpec, Component, ComponentKind, PinAnchor, Placement
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


# ── Vec2 ─────────────────────────────────────────────────────

@dataclass
class Vec2:
    x: float = 0.0
    y: float = 0.0

    def __add__(self, o: Vec2) -> Vec2:
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o: Vec2) -> Vec2:
        return Vec2(self.x - o.x, self.y - o.y)

    def __mul__(self, s: float) -> Vec2:
        return Vec2(self.x * s, self.y * s)

    def __rmul__(self, s: float) -> Vec2:
        return self.__mul__(s)

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y)

    def normalized(self) -> Vec2:
        ln = self.length()
        if ln < 1e-9:
            return Vec2(0, 0)
        return Vec2(self.x / ln, self.y / ln)


def _rotate_vec(v: Vec2, angle: float) -> Vec2:
    c, s = math.cos(angle), math.sin(angle)
    return Vec2(v.x * c - v.y * s, v.x * s + v.y * c)


# ── Physics primitives ───────────────────────────────────────

@dataclass
class Body:
    comp_id: str
    position: Vec2
    velocity: Vec2 = field(default_factory=Vec2)
    half_w: float = 0.0      # base half-width (unrotated)
    half_h: float = 0.0      # base half-height (unrotated)
    is_static: bool = False
    angle: float = 0.0       # radians
    angular_vel: float = 0.0
    is_wire: bool = False     # thin wire collision body

    def effective_half_size(self) -> tuple[float, float]:
        """AABB half-size accounting for rotation."""
        c, s = abs(math.cos(self.angle)), abs(math.sin(self.angle))
        return (self.half_w * c + self.half_h * s,
                self.half_w * s + self.half_h * c)


@dataclass
class Spring:
    anchor_local: Vec2      # pin offset from body center (unrotated)
    target_world: Vec2
    stiffness: float = 3.0
    damping: float = 0.8


# ── Simulation constants ────────────────────────────────────

DT = 0.02
MAX_STEPS = 800
GLOBAL_DAMPING = 0.92
ANGULAR_DAMPING = 0.88
VELOCITY_THRESHOLD = 0.01
ANGULAR_VEL_THRESHOLD = 0.005
SETTLED_FRAMES = 10
COLLISION_MARGIN = 7.0


# ── Semantic roles ───────────────────────────────────────────

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


@dataclass(frozen=True)
class RoleBias:
    dx: float
    dy: float
    magnitude: float
    priority: int


ROLE_PHYSICS: dict[Role, RoleBias] = {
    Role.anchor:            RoleBias(0, 0, 0, 0),
    Role.power_path:        RoleBias(+1, 0, 0.4, 1),
    Role.input_decoupling:  RoleBias(-1, 0, 0.3, 2),
    Role.output_decoupling: RoleBias(+1, 0, 0.3, 3),
    Role.feedback_upper:    RoleBias(+0.3, +1, 0.25, 4),
    Role.feedback_lower:    RoleBias(0, +1, 0.2, 5),
    Role.bootstrap:         RoleBias(0, -1, 0.3, 6),
    Role.enable:            RoleBias(-1, +0.5, 0.25, 7),
    Role.bypass:            RoleBias(-0.5, -1, 0.25, 8),
    Role.feedback_path:     RoleBias(0, -1, 0.35, 9),
    Role.grounding:         RoleBias(-0.7, +1, 0.25, 10),
    Role.protection:        RoleBias(+0.5, -1, 0.2, 11),
    Role.generic:           RoleBias(+1, 0, 0.1, 12),
}


# ── Component registry ──────────────────────────────────────

GENERIC_CT = ComponentTemplate(
    width=7.62, height=7.62,
    pin_offsets={"1": PinOffset(3.81, -2.54), "2": PinOffset(3.81, 10.16)},
)

COMPONENT_REGISTRY: dict[ComponentKind, ComponentTemplate] = {
    ComponentKind.regulator_ic: BUCK_IC, ComponentKind.ldo_ic: LDO_IC,
    ComponentKind.opamp_ic: OPAMP_IC, ComponentKind.input_cap: CAP_TEMPLATE,
    ComponentKind.output_cap: CAP_TEMPLATE, ComponentKind.inductor: INDUCTOR_TEMPLATE,
    ComponentKind.fb_top: RESISTOR_TEMPLATE, ComponentKind.fb_bottom: RESISTOR_TEMPLATE,
    ComponentKind.enable_resistor: RESISTOR_TEMPLATE, ComponentKind.bootstrap_cap: CAP_TEMPLATE,
    ComponentKind.bypass_cap: CAP_TEMPLATE, ComponentKind.feedback_resistor: RESISTOR_TEMPLATE,
    ComponentKind.gain_resistor: RESISTOR_TEMPLATE, ComponentKind.ground_resistor: RESISTOR_TEMPLATE,
    ComponentKind.input_resistor: RESISTOR_TEMPLATE, ComponentKind.diode: DIODE_TEMPLATE,
    ComponentKind.generic: GENERIC_CT,
}


def _get_ct(kind: ComponentKind) -> ComponentTemplate:
    return COMPONENT_REGISTRY.get(kind, GENERIC_CT)


# Cache for dynamically generated IC templates
_dynamic_ct_cache: dict[str, ComponentTemplate] = {}


def _get_ct_for_component(comp) -> ComponentTemplate:
    """Get or create a ComponentTemplate for a specific component.

    For ICs with many pins, dynamically generates a properly-sized template
    with pins distributed along left/right edges. For passives, returns the
    standard template.
    """
    ct = _get_ct(comp.kind)

    if comp.kind == ComponentKind.generic and comp.pins:
        cache_key = f"{comp.id}_{'_'.join(comp.pins)}"
        if cache_key in _dynamic_ct_cache:
            return _dynamic_ct_cache[cache_key]

        pin_spacing = 5.08
        stub_len = 5.08
        body_w = 7.62
        body_h = max(10.16, len(comp.pins) * pin_spacing)
        start_y = body_h / 2 - (len(comp.pins) - 1) * pin_spacing / 2

        offsets = {}
        for i, pin_name in enumerate(comp.pins):
            offsets[pin_name] = PinOffset(body_w + stub_len, start_y + i * pin_spacing)

        new_ct = ComponentTemplate(width=body_w, height=body_h, pin_offsets=offsets)
        _dynamic_ct_cache[cache_key] = new_ct
        return new_ct

    # Only generate dynamic templates for IC-type components with many pins
    if not comp.kind.value.endswith("_ic") or len(comp.pins) <= 7:
        return ct

    cache_key = f"{comp.id}_{len(comp.pins)}"
    if cache_key in _dynamic_ct_cache:
        return _dynamic_ct_cache[cache_key]

    # Determine IC size based on pin count
    n_pins = len(comp.pins)
    pin_spacing = 5.08  # mm between pins (2 grid units)
    stub_len = 5.08     # pin stub extends outside body
    body_w = 25.40      # wider for more pin names

    # Classify pins: power/ground on top/bottom, signals on left/right
    left_pins = []
    right_pins = []
    top_pins = []
    bottom_pins = []

    for pin_name in comp.pins:
        pn = pin_name.upper()
        if pn.startswith("VDD") or pn.startswith("VCC") or pn.startswith("AVDD") or pn.startswith("DVDD") or pn == "V+":
            top_pins.append(pin_name)
        elif pn.startswith("VSS") or pn.startswith("GND") or pn.startswith("AGND") or pn.startswith("DGND") or pn == "V-" or pn.startswith("VSSA"):
            bottom_pins.append(pin_name)
        elif pn.startswith("P") and len(pn) >= 3 and pn[1] in "ABCDEFGH" and pn[2:].isdigit():
            # GPIO pins (PA0, PB1, etc) go on right
            right_pins.append(pin_name)
        elif any(pn.startswith(p) for p in ["OUT", "SW", "FB", "PGOOD", "SDOUT", "BCLK", "LRCLK", "SDIN", "MCLK"]):
            right_pins.append(pin_name)
        else:
            left_pins.append(pin_name)

    # Compute body height based on max pins on one side
    max_side = max(len(left_pins), len(right_pins), 1)
    body_h = max(max_side * pin_spacing + pin_spacing, 20.32)

    # Build pin offsets
    offsets = {}

    # Left pins
    for i, pin_name in enumerate(left_pins):
        y = pin_spacing + i * pin_spacing
        offsets[pin_name] = PinOffset(-stub_len, y)

    # Right pins
    for i, pin_name in enumerate(right_pins):
        y = pin_spacing + i * pin_spacing
        offsets[pin_name] = PinOffset(body_w + stub_len, y)

    # Top pins (power)
    top_start_x = body_w / 2 - (len(top_pins) - 1) * pin_spacing / 2 if top_pins else body_w / 2
    for i, pin_name in enumerate(top_pins):
        x = top_start_x + i * pin_spacing
        offsets[pin_name] = PinOffset(x, -stub_len)

    # Bottom pins (ground)
    bot_start_x = body_w / 2 - (len(bottom_pins) - 1) * pin_spacing / 2 if bottom_pins else body_w / 2
    for i, pin_name in enumerate(bottom_pins):
        x = bot_start_x + i * pin_spacing
        offsets[pin_name] = PinOffset(x, body_h + stub_len)

    new_ct = ComponentTemplate(width=body_w, height=body_h, pin_offsets=offsets)
    _dynamic_ct_cache[cache_key] = new_ct
    return new_ct


def _get_role(kind: ComponentKind) -> Role:
    return ROLE_MAP.get(kind, Role.generic)


# ── Connectivity ─────────────────────────────────────────────

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
        for i, (a, pa) in enumerate(members):
            for j, (b, pb) in enumerate(members):
                if i != j:
                    result[a].append(SharedPin(b, pb, pa, net.name, is_gnd))
    return result


def _find_ic(spec: BlockSpec) -> Component:
    for c in spec.components:
        if c.kind.value.endswith("_ic"):
            return c
    return spec.components[0]


def _compute_order(spec: BlockSpec) -> list[Component]:
    ic = _find_ic(spec)
    others = [c for c in spec.components if c.id != ic.id]
    others.sort(key=lambda c: ROLE_PHYSICS[_get_role(c.kind)].priority)
    return [ic] + others


# ── Spring construction ──────────────────────────────────────

def _classify_stiffness(net_name: str, is_gnd: bool, partner_is_ic: bool) -> float:
    base = 0.5 if is_gnd else (1.5 if net_name.upper() in ("VCC", "VDD", "V+", "VIN", "VOUT") else 3.0)
    return base * (1.5 if partner_is_ic else 1.0)


def _pin_center_offset(ct: ComponentTemplate, pin_name: str) -> Vec2:
    po = ct.pin_offsets.get(pin_name)
    if po is None:
        return Vec2(0, 0)
    return Vec2(po.x - ct.width / 2, po.y - ct.height / 2)


def _build_springs(comp, placed_map, pin_conns) -> list[Spring]:
    ct = _get_ct_for_component(comp)
    springs = []
    for sp in pin_conns.get(comp.id, []):
        if sp.other_id not in placed_map:
            continue
        other_comp, other_pl = placed_map[sp.other_id]
        other_ct = _get_ct(other_comp.kind)
        other_po = other_ct.pin_offsets.get(sp.other_pin)
        if other_po is None:
            continue
        # Compute target accounting for partner's rotation
        from .placer import _rotate_pin
        rpx, rpy = _rotate_pin(other_po.x, other_po.y, other_ct.width, other_ct.height, other_pl.rotation)
        target = Vec2(other_pl.x + rpx, other_pl.y + rpy)
        anchor = _pin_center_offset(ct, sp.my_pin)
        stiffness = _classify_stiffness(sp.net_name, sp.is_gnd, other_comp.kind.value.endswith("_ic"))
        springs.append(Spring(anchor_local=anchor, target_world=target, stiffness=stiffness))
    return springs


# ── Wire segment bodies ─────────────────────────────────────

WIRE_THICKNESS = 2.0  # mm half-thickness for wire collision bodies


def _create_wire_bodies(
    comp: Component,
    pl: Placement,
    placed_map: dict,
    pin_conns: dict,
) -> list[Body]:
    """Create thin static bodies for wire segments between this component's
    pins and already-placed partner pins. Simple straight-line connections."""
    ct = _get_ct_for_component(comp)
    from .placer import _rotate_pin
    bodies = []

    seen_pairs: set[tuple[str, str]] = set()
    for sp in pin_conns.get(comp.id, []):
        if sp.other_id not in placed_map:
            continue
        pair_key = (min(comp.id, sp.other_id), max(comp.id, sp.other_id))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        other_comp, other_pl = placed_map[sp.other_id]
        other_ct = _get_ct(other_comp.kind)
        other_po = other_ct.pin_offsets.get(sp.other_pin)
        my_po = ct.pin_offsets.get(sp.my_pin)
        if other_po is None or my_po is None:
            continue

        rpx1, rpy1 = _rotate_pin(my_po.x, my_po.y, ct.width, ct.height, pl.rotation)
        px1 = pl.x + rpx1
        py1 = pl.y + rpy1

        rpx2, rpy2 = _rotate_pin(other_po.x, other_po.y, other_ct.width, other_ct.height, other_pl.rotation)
        px2 = other_pl.x + rpx2
        py2 = other_pl.y + rpy2

        # Create an L-shaped wire: horizontal then vertical
        mid_x, mid_y = px2, py1
        # Horizontal segment
        if abs(px1 - mid_x) > 1.0:
            cx = (px1 + mid_x) / 2
            hw = abs(px1 - mid_x) / 2 + WIRE_THICKNESS
            bodies.append(Body(f"wire_{comp.id}_{sp.other_id}_h", Vec2(cx, py1),
                               half_w=hw, half_h=WIRE_THICKNESS, is_static=True, is_wire=True))
        # Vertical segment
        if abs(py1 - py2) > 1.0:
            cy = (py1 + py2) / 2
            hh = abs(py1 - py2) / 2 + WIRE_THICKNESS
            bodies.append(Body(f"wire_{comp.id}_{sp.other_id}_v", Vec2(mid_x, cy),
                               half_w=WIRE_THICKNESS, half_h=hh, is_static=True, is_wire=True))

    return bodies


# ── Physics simulation ───────────────────────────────────────

def _resolve_aabb(dynamic: Body, static: Body) -> None:
    dhw, dhh = dynamic.effective_half_size()
    shw, shh = static.effective_half_size()

    dx = dynamic.position.x - static.position.x
    dy = dynamic.position.y - static.position.y
    overlap_x = (dhw + shw) - abs(dx)
    overlap_y = (dhh + shh) - abs(dy)

    if overlap_x <= 0 or overlap_y <= 0:
        return

    if overlap_x < overlap_y:
        sign = 1.0 if dx > 0 else -1.0
        dynamic.position.x += sign * overlap_x
        dynamic.velocity.x = 0.0
    else:
        sign = 1.0 if dy > 0 else -1.0
        dynamic.position.y += sign * overlap_y
        dynamic.velocity.y = 0.0


def _snap_angle(angle: float) -> int:
    """Snap radians to nearest 90° and return degrees (0, 90, 180, 270)."""
    deg = math.degrees(angle) % 360
    snapped = round(deg / 90) * 90
    return int(snapped) % 360


def _simulate(body: Body, springs: list[Spring], statics: list[Body], bias: Vec2) -> None:
    settled_count = 0
    for _ in range(MAX_STEPS):
        fx, fy = bias.x, bias.y
        torque = 0.0

        for sp in springs:
            rotated_anchor = _rotate_vec(sp.anchor_local, body.angle)
            pin_world = body.position + rotated_anchor
            delta = sp.target_world - pin_world
            force_x = delta.x * sp.stiffness
            force_y = delta.y * sp.stiffness
            fx += force_x
            fy += force_y
            # Torque = r × F (2D cross product)
            torque += rotated_anchor.x * force_y - rotated_anchor.y * force_x

            dl = delta.length()
            if dl > 1e-9:
                proj = (body.velocity.x * delta.x + body.velocity.y * delta.y) / (dl * dl)
                body.velocity.x -= delta.x * proj * sp.damping * DT
                body.velocity.y -= delta.y * proj * sp.damping * DT

        # Linear integration
        body.velocity.x += fx * DT
        body.velocity.y += fy * DT
        body.velocity.x *= GLOBAL_DAMPING
        body.velocity.y *= GLOBAL_DAMPING
        body.position.x += body.velocity.x * DT
        body.position.y += body.velocity.y * DT

        # Angular integration with restoring torque toward nearest 90°.
        # This prevents components from drifting past the snap boundary
        # unless spring forces are very strong and asymmetric.
        nearest_90 = round(body.angle / (math.pi / 2)) * (math.pi / 2)
        restore_torque = -(body.angle - nearest_90) * 4.0
        torque += restore_torque

        inertia = (body.half_w ** 2 + body.half_h ** 2) * 0.5
        if inertia > 0:
            body.angular_vel += (torque / inertia) * DT * 0.2
        body.angular_vel *= ANGULAR_DAMPING
        body.angle += body.angular_vel * DT

        # Collision
        for static in statics:
            _resolve_aabb(body, static)

        # Convergence
        linear_settled = body.velocity.length() < VELOCITY_THRESHOLD
        angular_settled = abs(body.angular_vel) < ANGULAR_VEL_THRESHOLD
        if linear_settled and angular_settled:
            settled_count += 1
            if settled_count >= SETTLED_FRAMES:
                break
        else:
            settled_count = 0


# ── Main placement loop ─────────────────────────────────────

def incremental_place(
    spec: BlockSpec,
    origin_x: float = 40.0,
    origin_y: float = 40.0,
    grid: float = 2.54,
) -> list[Placement]:
    pin_conns = _build_pin_connections(spec)
    order = _compute_order(spec)

    placed: list[Placement] = []
    placed_map: dict[str, tuple] = {}
    static_bodies: list[Body] = []

    margin_half = COLLISION_MARGIN / 2

    for comp in order:
        role = _get_role(comp.kind)
        bias_params = ROLE_PHYSICS[role]
        ct = _get_ct_for_component(comp)
        hw = ct.width / 2 + margin_half
        hh = ct.height / 2 + margin_half

        if role == Role.anchor:
            cx = snap_to_grid(origin_x, grid) + ct.width / 2
            cy = snap_to_grid(origin_y, grid) + ct.height / 2
            body = Body(comp.id, Vec2(cx, cy), half_w=hw, half_h=hh, is_static=True)
            static_bodies.append(body)
            pl = Placement(id=comp.id, x=snap_to_grid(origin_x, grid),
                           y=snap_to_grid(origin_y, grid),
                           rotation=0, width=ct.width, height=ct.height)
            placed.append(pl)
            placed_map[comp.id] = (comp, pl)
            continue

        springs = _build_springs(comp, placed_map, pin_conns)

        # Spawn at spring centroid + bias
        if springs:
            total_k = sum(s.stiffness for s in springs)
            cx = sum(s.target_world.x * s.stiffness for s in springs) / total_k
            cy = sum(s.target_world.y * s.stiffness for s in springs) / total_k
            cx += bias_params.dx * (COLLISION_MARGIN + ct.width / 2)
            cy += bias_params.dy * (COLLISION_MARGIN + ct.height / 2)
        else:
            cx = origin_x + ct.width / 2 + bias_params.dx * 25
            cy = origin_y + ct.height / 2 + bias_params.dy * 25

        body = Body(comp.id, Vec2(cx, cy), half_w=hw, half_h=hh)
        bias_force = Vec2(bias_params.dx * bias_params.magnitude,
                          bias_params.dy * bias_params.magnitude)
        _simulate(body, springs, static_bodies, bias_force)

        # Snap angle to nearest 90°
        rotation_deg = _snap_angle(body.angle)

        # Snap position to grid (convert center to top-left, accounting for rotation)
        if rotation_deg in (90, 270):
            ew, eh = ct.height, ct.width
        else:
            ew, eh = ct.width, ct.height
        snapped_x = snap_to_grid(body.position.x - ew / 2, grid)
        snapped_y = snap_to_grid(body.position.y - eh / 2, grid)

        pl = Placement(id=comp.id, x=snapped_x, y=snapped_y,
                        rotation=rotation_deg, width=ct.width, height=ct.height)

        # Post-snap overlap guarantee
        for _ in range(20):
            any_fix = False
            for other_pl in placed:
                ow, oh = effective_size(other_pl)
                pw, ph = effective_size(pl)
                gap_x = max(other_pl.x - (pl.x + pw), pl.x - (other_pl.x + ow))
                gap_y = max(other_pl.y - (pl.y + ph), pl.y - (other_pl.y + oh))
                if gap_x >= 2.0 or gap_y >= 2.0:
                    continue
                push_x = bias_params.dx if bias_params.dx != 0 else (1 if pl.x >= other_pl.x else -1)
                push_y = bias_params.dy if bias_params.dy != 0 else (1 if pl.y >= other_pl.y else -1)
                if abs(push_x) >= abs(push_y):
                    if push_x > 0:
                        pl.x = snap_to_grid(other_pl.x + ow + COLLISION_MARGIN, grid)
                    else:
                        pl.x = snap_to_grid(other_pl.x - pw - COLLISION_MARGIN, grid)
                else:
                    if push_y > 0:
                        pl.y = snap_to_grid(other_pl.y + oh + COLLISION_MARGIN, grid)
                    else:
                        pl.y = snap_to_grid(other_pl.y - ph - COLLISION_MARGIN, grid)
                any_fix = True
                break
            if not any_fix:
                break

        # Freeze component
        frozen = Body(comp.id,
                      Vec2(pl.x + ew / 2, pl.y + eh / 2),
                      half_w=ew / 2 + margin_half, half_h=eh / 2 + margin_half,
                      is_static=True, angle=math.radians(rotation_deg))
        static_bodies.append(frozen)
        placed.append(pl)
        placed_map[comp.id] = (comp, pl)

        # Create wire segment collision bodies
        wire_bodies = _create_wire_bodies(comp, pl, placed_map, pin_conns)
        static_bodies.extend(wire_bodies)

    return placed


# ── Template synthesis ───────────────────────────────────────

def _compute_net_route_order(spec: BlockSpec) -> list[str]:
    gnd, signal = [], []
    for net in spec.nets:
        (gnd if net.name.upper() == "GND" else signal).append(
            (len(net.members), net.name) if net.name.upper() != "GND" else net.name)
    if isinstance(gnd[0] if gnd else None, str):
        signal_sorted = sorted([s for s in signal], key=lambda x: -x[0])
        return [n for _, n in signal_sorted] + gnd
    return [n.name for n in spec.nets]


def synthesize_template(spec: BlockSpec) -> BlockTemplate:
    ct = {}
    instance_ct = {}
    for comp in spec.components:
        tmpl = _get_ct_for_component(comp)
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
