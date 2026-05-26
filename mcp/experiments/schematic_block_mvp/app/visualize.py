"""Live pygame visualization of the V3 physics-based placement.

Usage:
    python -m app.visualize app/examples/circuit_buck_5v_3v3.json
    python -m app.visualize app/examples/complex_usb_pd.json --mode simultaneous

Modes:
    incremental: original one-component-at-a-time placer
    simultaneous: all components move at once with live keepout resizing

Shows rotation, spring physics, wire collisions, and grid snapping.
Auto-loops. SPACE=next, R=reset, ESC=quit.
"""

from __future__ import annotations

import json
import math
import argparse
import sys
import time

import pygame

from .placer_v3 import (
    COLLISION_MARGIN, DT, GLOBAL_DAMPING, ANGULAR_DAMPING, MAX_STEPS,
    ROLE_PHYSICS, SETTLED_FRAMES, VELOCITY_THRESHOLD, ANGULAR_VEL_THRESHOLD,
    Body, Spring, Vec2, _rotate_vec,
    _build_pin_connections, _build_springs, _compute_order, _create_wire_bodies,
    _find_ic, _get_ct, _get_ct_for_component, _get_role, _resolve_aabb, _snap_angle,
)
from .placer import effective_size, snap_to_grid, _rotate_pin, compute_pin_anchors
from .router import route_nets
from .schema import BlockSpec, Placement, RouteSegment


# ── Display ──────────────────────────────────────────────────

SCALE = 5.0
OFFSET_X = 100
OFFSET_Y = 140
FPS = 60
STEPS_PER_FRAME = 4
AUTO_ADVANCE_DELAY = 0.6
SIM_COMPONENT_MARGIN = 4.0
SIM_WIRE_MARGIN = 2.5
SIM_CENTERING_K = 0.18
SIM_SIZE_LERP = 0.12
SIM_SETTLE_FRAMES = 18

BG = (24, 26, 30)
GRID_COL = (36, 38, 42)
STATIC_FILL = (50, 60, 80)
STATIC_BORDER = (90, 110, 150)
DYN_BORDER = (80, 220, 130)
DYN_FILL = (40, 120, 70)
SPRING_COL = (255, 200, 60)
TARGET_COL = (255, 90, 70)
PIN_COL = (255, 80, 80)
WIRE_COL = (60, 60, 75)
WIRE_BORDER = (80, 80, 100)
SNAP_COL = (120, 255, 160)
BIAS_COL = (100, 180, 255)
ROUTE_COL = (200, 200, 220)
ROUTE_GND_COL = (120, 140, 160)
ROUTE_POWER_COL = (220, 160, 100)
ROUTE_DOT = (240, 240, 250)

# Net name → color for routed wires
def _net_color(name: str) -> tuple:
    n = name.upper()
    if n == "GND":
        return ROUTE_GND_COL
    if any(k in n for k in ("VIN", "VOUT", "VCC", "VDD", "V+")):
        return ROUTE_POWER_COL
    return ROUTE_COL


def mm2px(x: float, y: float) -> tuple[int, int]:
    return int(x * SCALE + OFFSET_X), int(y * SCALE + OFFSET_Y)


def draw_rotated_rect(surf, cx, cy, hw, hh, angle, fill, border, width=2):
    """Draw a rotated rectangle."""
    corners = []
    for sx, sy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
        lx, ly = sx * hw, sy * hh
        c, s = math.cos(angle), math.sin(angle)
        rx = lx * c - ly * s + cx
        ry = lx * s + ly * c + cy
        corners.append((int(rx), int(ry)))

    # Fill
    if fill:
        pygame.draw.polygon(surf, fill, corners)
    # Border
    pygame.draw.polygon(surf, border, corners, width)


def draw_grid(surf, w, h):
    step = 2.54 * SCALE
    x = OFFSET_X % step
    while x < w:
        pygame.draw.line(surf, GRID_COL, (int(x), 0), (int(x), h))
        x += step
    y = OFFSET_Y % step
    while y < h:
        pygame.draw.line(surf, GRID_COL, (0, int(y)), (w, int(y)))
        y += step


def draw_spring(surf, pin_world, target):
    px1, py1 = mm2px(pin_world.x, pin_world.y)
    px2, py2 = mm2px(target.x, target.y)
    pygame.draw.line(surf, SPRING_COL, (px1, py1), (px2, py2), 1)
    pygame.draw.circle(surf, TARGET_COL, (px2, py2), 4)
    pygame.draw.circle(surf, PIN_COL, (px1, py1), 3)


def draw_bias_arrow(surf, body, bias):
    if bias.length() < 0.001:
        return
    cx, cy = mm2px(body.position.x, body.position.y)
    n = bias.normalized()
    ex, ey = int(cx + n.x * 18), int(cy + n.y * 18)
    pygame.draw.line(surf, BIAS_COL, (cx, cy), (ex, ey), 2)
    a = math.atan2(n.y, n.x)
    for da in [2.5, -2.5]:
        hx, hy = int(ex - 7 * math.cos(a + da)), int(ey - 7 * math.sin(a + da))
        pygame.draw.line(surf, BIAS_COL, (ex, ey), (hx, hy), 2)


def draw_overlay(surf, font, lines):
    if font is None:
        return
    y = 12
    for line in lines:
        text = font.render(line, True, (220, 226, 236))
        surf.blit(text, (12, y))
        y += 18


def _resolve_dynamic_pair(a: Body, b: Body) -> None:
    ahw, ahh = a.effective_half_size()
    bhw, bhh = b.effective_half_size()
    dx = a.position.x - b.position.x
    dy = a.position.y - b.position.y
    overlap_x = (ahw + bhw) - abs(dx)
    overlap_y = (ahh + bhh) - abs(dy)

    if overlap_x <= 0 or overlap_y <= 0:
        return

    if overlap_x < overlap_y:
        push = overlap_x / 2
        sign = 1.0 if dx >= 0 else -1.0
        a.position.x += sign * push
        b.position.x -= sign * push
        a.velocity.x = 0.0
        b.velocity.x = 0.0
    else:
        push = overlap_y / 2
        sign = 1.0 if dy >= 0 else -1.0
        a.position.y += sign * push
        b.position.y -= sign * push
        a.velocity.y = 0.0
        b.velocity.y = 0.0


def _sim_keepout_margin(comp, ct, pin_conns) -> float:
    conn_count = len(pin_conns.get(comp.id, []))
    pin_pressure = max(0, len(comp.pins) - 2) * 0.35
    conn_pressure = min(7.0, conn_count * 0.45)
    generic_boost = 2.0 if comp.kind.value == "generic" else 0.0
    return SIM_COMPONENT_MARGIN + pin_pressure + conn_pressure + generic_boost


def _build_sim_links(spec: BlockSpec, pin_conns: dict[str, list]) -> list[tuple[str, str, str, str, float]]:
    links: list[tuple[str, str, str, str, float]] = []
    seen: set[tuple[str, str, str, str]] = set()
    comp_map = {c.id: c for c in spec.components}
    for comp in spec.components:
        for sp in pin_conns.get(comp.id, []):
            key = tuple(sorted((f"{comp.id}.{sp.my_pin}", f"{sp.other_id}.{sp.other_pin}")))
            if key in seen:
                continue
            seen.add(key)
            other = comp_map.get(sp.other_id)
            if other is None:
                continue
            stiffness = 0.9 if sp.is_gnd else (1.8 if sp.net_name.upper() in ("VIN", "VOUT", "VCC", "VDD", "VBUS") else 1.35)
            links.append((comp.id, sp.my_pin, sp.other_id, sp.other_pin, stiffness))
    return links


def _build_live_wire_bodies(
    body_by_id: dict[str, Body],
    ct_by_id: dict[str, tuple],
    links: list[tuple[str, str, str, str, float]],
) -> list[tuple[str, str, Body]]:
    wires: list[tuple[str, str, Body]] = []
    for a_id, a_pin, b_id, b_pin, _ in links:
        body_a = body_by_id[a_id]
        body_b = body_by_id[b_id]
        ct_a = ct_by_id[a_id]
        ct_b = ct_by_id[b_id]
        po_a = ct_a.pin_offsets.get(a_pin)
        po_b = ct_b.pin_offsets.get(b_pin)
        if po_a is None or po_b is None:
            continue

        a_local = Vec2(po_a.x - ct_a.width / 2, po_a.y - ct_a.height / 2)
        b_local = Vec2(po_b.x - ct_b.width / 2, po_b.y - ct_b.height / 2)
        p1 = body_a.position + _rotate_vec(a_local, body_a.angle)
        p2 = body_b.position + _rotate_vec(b_local, body_b.angle)
        mid = Vec2(p2.x, p1.y)

        if abs(p1.x - mid.x) > 0.5:
            cx = (p1.x + mid.x) / 2
            hw = abs(p1.x - mid.x) / 2 + SIM_WIRE_MARGIN
            wires.append((a_id, b_id, Body(f"wire_{a_id}_{b_id}_h", Vec2(cx, p1.y),
                                           half_w=hw, half_h=SIM_WIRE_MARGIN, is_static=True, is_wire=True)))
        if abs(p1.y - p2.y) > 0.5:
            cy = (p1.y + p2.y) / 2
            hh = abs(p1.y - p2.y) / 2 + SIM_WIRE_MARGIN
            wires.append((a_id, b_id, Body(f"wire_{a_id}_{b_id}_v", Vec2(mid.x, cy),
                                           half_w=SIM_WIRE_MARGIN, half_h=hh, is_static=True, is_wire=True)))
    return wires


def run_simultaneous_visualization(spec: BlockSpec):
    pygame.init()
    W, H = 1180, 820
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption(f"V3 Physics Simultaneous — {spec.block_type}")
    clock = pygame.time.Clock()
    font = None

    pin_conns = _build_pin_connections(spec)
    order = _compute_order(spec)
    links = _build_sim_links(spec, pin_conns)

    body_by_id: dict[str, Body] = {}
    comp_by_id = {comp.id: comp for comp in order}
    ct_by_id = {comp.id: _get_ct_for_component(comp) for comp in order}
    keepout_by_id = {comp.id: _sim_keepout_margin(comp, ct_by_id[comp.id], pin_conns) for comp in order}

    for idx, comp in enumerate(order):
        ct = ct_by_id[comp.id]
        role = _get_role(comp.kind)
        bp = ROLE_PHYSICS[role]
        row = idx // 4
        col = idx % 4
        start_x = 36 + col * 32 + bp.dx * 10
        start_y = 30 + row * 28 + bp.dy * 10
        margin = keepout_by_id[comp.id]
        body_by_id[comp.id] = Body(
            comp.id,
            Vec2(start_x + ct.width / 2, start_y + ct.height / 2),
            half_w=ct.width / 2 + margin,
            half_h=ct.height / 2 + margin,
        )

    primary = _find_ic(spec).id
    routed_wires: list[RouteSegment] = []
    routing_progress = 0
    sim_step = 0
    settled = 0
    phase = "simulating"
    wait_start = 0.0
    grid = 2.54

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_r:
                    return run_simultaneous_visualization(spec)

        if phase == "simulating":
            max_motion = 0.0
            for _ in range(STEPS_PER_FRAME):
                for comp in order:
                    body = body_by_id[comp.id]
                    ct = ct_by_id[comp.id]
                    target_margin = keepout_by_id[comp.id]
                    target_hw = ct.width / 2 + target_margin
                    target_hh = ct.height / 2 + target_margin
                    body.half_w += (target_hw - body.half_w) * SIM_SIZE_LERP
                    body.half_h += (target_hh - body.half_h) * SIM_SIZE_LERP

                for comp in order:
                    body = body_by_id[comp.id]
                    role = _get_role(comp.kind)
                    bp = ROLE_PHYSICS[role]
                    ct = ct_by_id[comp.id]

                    fx = bp.dx * bp.magnitude
                    fy = bp.dy * bp.magnitude
                    torque = 0.0

                    if comp.id == primary:
                        fx += (40.0 + ct.width / 2 - body.position.x) * SIM_CENTERING_K
                        fy += (40.0 + ct.height / 2 - body.position.y) * SIM_CENTERING_K

                    for a_id, a_pin, b_id, b_pin, stiffness in links:
                        if comp.id not in (a_id, b_id):
                            continue
                        my_pin = a_pin if comp.id == a_id else b_pin
                        other_id = b_id if comp.id == a_id else a_id
                        other_pin = b_pin if comp.id == a_id else a_pin
                        other_body = body_by_id[other_id]
                        other_ct = ct_by_id[other_id]
                        my_po = ct.pin_offsets.get(my_pin)
                        other_po = other_ct.pin_offsets.get(other_pin)
                        if my_po is None or other_po is None:
                            continue
                        my_anchor = Vec2(my_po.x - ct.width / 2, my_po.y - ct.height / 2)
                        other_anchor = Vec2(other_po.x - other_ct.width / 2, other_po.y - other_ct.height / 2)
                        my_world = body.position + _rotate_vec(my_anchor, body.angle)
                        other_world = other_body.position + _rotate_vec(other_anchor, other_body.angle)
                        delta = other_world - my_world
                        force_x = delta.x * stiffness
                        force_y = delta.y * stiffness
                        fx += force_x
                        fy += force_y
                        torque += my_anchor.x * force_y - my_anchor.y * force_x

                    body.velocity.x += fx * DT
                    body.velocity.y += fy * DT
                    body.velocity.x *= GLOBAL_DAMPING
                    body.velocity.y *= GLOBAL_DAMPING
                    body.position.x += body.velocity.x * DT
                    body.position.y += body.velocity.y * DT

                    nearest_90 = round(body.angle / (math.pi / 2)) * (math.pi / 2)
                    torque += -(body.angle - nearest_90) * 2.5
                    inertia = (body.half_w ** 2 + body.half_h ** 2) * 0.5
                    if inertia > 0:
                        body.angular_vel += (torque / inertia) * DT * 0.18
                    body.angular_vel *= ANGULAR_DAMPING
                    body.angle += body.angular_vel * DT

                for i in range(len(order)):
                    a = body_by_id[order[i].id]
                    for j in range(i + 1, len(order)):
                        b = body_by_id[order[j].id]
                        _resolve_dynamic_pair(a, b)

                live_wires = _build_live_wire_bodies(body_by_id, ct_by_id, links)
                for comp in order:
                    body = body_by_id[comp.id]
                    for a_id, b_id, wire in live_wires:
                        if comp.id in (a_id, b_id):
                            continue
                        _resolve_aabb(body, wire)

                for body in body_by_id.values():
                    motion = body.velocity.length() + abs(body.angular_vel) * 3.0
                    max_motion = max(max_motion, motion)

                sim_step += 1
                if max_motion < VELOCITY_THRESHOLD * 2:
                    settled += 1
                else:
                    settled = 0

                if settled >= SIM_SETTLE_FRAMES or sim_step >= MAX_STEPS * 2:
                    placements: list[Placement] = []
                    for comp in order:
                        body = body_by_id[comp.id]
                        ct = ct_by_id[comp.id]
                        rot_deg = _snap_angle(body.angle)
                        body.angle = math.radians(rot_deg)
                        if rot_deg in (90, 270):
                            ew, eh = ct.height, ct.width
                        else:
                            ew, eh = ct.width, ct.height
                        snapped_x = snap_to_grid(body.position.x - ew / 2, grid)
                        snapped_y = snap_to_grid(body.position.y - eh / 2, grid)
                        placements.append(Placement(
                            id=comp.id,
                            x=snapped_x,
                            y=snapped_y,
                            rotation=rot_deg,
                            width=ct.width,
                            height=ct.height,
                        ))

                    from .placer_v3 import synthesize_template
                    template = synthesize_template(spec)
                    anchors = compute_pin_anchors(spec, placements, template)
                    routed_wires = route_nets(spec, placements, anchors, template)
                    routing_progress = 0
                    phase = "routing"
                    wait_start = time.time()
                    break

        elif phase == "routing":
            elapsed = time.time() - wait_start
            total_segs = len(routed_wires)
            routing_progress = min(total_segs, int(elapsed * 15))
            if routing_progress >= total_segs:
                phase = "done"

        screen.fill(BG)
        draw_grid(screen, W, H)

        live_wires = _build_live_wire_bodies(body_by_id, ct_by_id, links) if phase == "simulating" else []
        for _, _, wire in live_wires:
            ehw, ehh = wire.effective_half_size()
            px, py = mm2px(wire.position.x - ehw, wire.position.y - ehh)
            pw, ph = int(ehw * 2 * SCALE), int(ehh * 2 * SCALE)
            s = pygame.Surface((max(1, pw), max(1, ph)), pygame.SRCALPHA)
            s.fill((*WIRE_COL, 45))
            screen.blit(s, (px, py))

        for comp in order:
            body = body_by_id[comp.id]
            ct = ct_by_id[comp.id]
            px, py = mm2px(body.position.x, body.position.y)
            hw_px = (ct.width / 2) * SCALE
            hh_px = (ct.height / 2) * SCALE
            draw_rotated_rect(screen, px, py, hw_px, hh_px, body.angle, STATIC_FILL, STATIC_BORDER)
            khw_px = body.half_w * SCALE
            khh_px = body.half_h * SCALE
            draw_rotated_rect(screen, px, py, khw_px, khh_px, body.angle, None, DYN_BORDER, width=1)
            role = _get_role(comp.kind)
            bp = ROLE_PHYSICS[role]
            draw_bias_arrow(screen, body, Vec2(bp.dx * bp.magnitude, bp.dy * bp.magnitude))

        for a_id, a_pin, b_id, b_pin, _ in links:
            body_a = body_by_id[a_id]
            body_b = body_by_id[b_id]
            ct_a = ct_by_id[a_id]
            ct_b = ct_by_id[b_id]
            po_a = ct_a.pin_offsets.get(a_pin)
            po_b = ct_b.pin_offsets.get(b_pin)
            if po_a is None or po_b is None:
                continue
            a_local = Vec2(po_a.x - ct_a.width / 2, po_a.y - ct_a.height / 2)
            b_local = Vec2(po_b.x - ct_b.width / 2, po_b.y - ct_b.height / 2)
            p1 = body_a.position + _rotate_vec(a_local, body_a.angle)
            p2 = body_b.position + _rotate_vec(b_local, body_b.angle)
            draw_spring(screen, p1, p2)

        for route in routed_wires[:routing_progress]:
            pts = route.points
            if len(pts) < 2:
                continue
            col = _net_color(route.net)
            for j in range(len(pts) - 1):
                p1, p2 = pts[j], pts[j + 1]
                x1, y1 = mm2px(p1.x, p1.y)
                x2, y2 = mm2px(p2.x, p2.y)
                pygame.draw.line(screen, col, (x1, y1), (x2, y2), 2)
            for pt in pts:
                px, py = mm2px(pt.x, pt.y)
                pygame.draw.circle(screen, ROUTE_DOT, (px, py), 2)

        draw_overlay(screen, font, [
            "mode: simultaneous",
            "all components move together",
            "thin boxes = dynamic keepout, gray bands = wire keepout",
            f"phase: {phase}",
            f"step: {sim_step}",
            f"links: {len(links)}",
        ])

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


def run_visualization(spec: BlockSpec):
    pygame.init()
    W, H = 960, 740
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption(f"V3 Physics — {spec.block_type}")
    clock = pygame.time.Clock()
    font = None

    pin_conns = _build_pin_connections(spec)
    order = _compute_order(spec)

    # State
    placed: list[Placement] = []
    placed_map: dict[str, tuple] = {}
    static_bodies: list[Body] = []
    ct_map: dict[str, tuple] = {}
    routed_wires: list[RouteSegment] = []  # final routed wires drawn after all placed
    routing_progress = 0  # for animated wire drawing
    comp_idx = 0
    current_body: Body | None = None
    current_springs: list[Spring] = []
    current_bias = Vec2()
    cur_ct_w = cur_ct_h = 0.0
    cur_id = ""
    sim_step = 0
    settled_count = 0
    phase = "placing"  # placing, settling, snapping, waiting, done
    wait_start = 0.0
    snap_start = 0.0
    snap_from = Vec2()
    snap_to_pos = Vec2()
    snap_from_angle = 0.0
    snap_to_angle = 0.0
    SNAP_DURATION = 0.4  # seconds

    margin_half = COLLISION_MARGIN / 2
    grid = 2.54

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_SPACE and phase == "waiting":
                    phase = "placing"
                elif ev.key == pygame.K_r:
                    comp_idx = 0; placed.clear(); placed_map.clear()
                    static_bodies.clear(); ct_map.clear()
                    current_body = None; phase = "placing"

        # ── State machine ──
        if phase == "placing" and comp_idx < len(order):
            comp = order[comp_idx]
            role = _get_role(comp.kind)
            bp = ROLE_PHYSICS[role]
            ct = _get_ct_for_component(comp)
            hw = ct.width / 2 + margin_half
            hh = ct.height / 2 + margin_half
            cur_ct_w, cur_ct_h = ct.width, ct.height
            cur_id = comp.id

            if role.value == "anchor":
                cx = snap_to_grid(40.0, grid) + ct.width / 2
                cy = snap_to_grid(40.0, grid) + ct.height / 2
                body = Body(comp.id, Vec2(cx, cy), half_w=hw, half_h=hh, is_static=True)
                static_bodies.append(body)
                ct_map[comp.id] = (ct.width, ct.height)
                pl = Placement(id=comp.id, x=snap_to_grid(40.0, grid), y=snap_to_grid(40.0, grid),
                               rotation=0, width=ct.width, height=ct.height)
                placed.append(pl); placed_map[comp.id] = (comp, pl)
                comp_idx += 1
                continue

            current_springs = _build_springs(comp, placed_map, pin_conns)
            if current_springs:
                tk = sum(s.stiffness for s in current_springs)
                cx = sum(s.target_world.x * s.stiffness for s in current_springs) / tk
                cy = sum(s.target_world.y * s.stiffness for s in current_springs) / tk
                cx += bp.dx * (COLLISION_MARGIN + ct.width / 2)
                cy += bp.dy * (COLLISION_MARGIN + ct.height / 2)
            else:
                cx = 40 + ct.width / 2 + bp.dx * 25
                cy = 40 + ct.height / 2 + bp.dy * 25

            current_body = Body(comp.id, Vec2(cx, cy), half_w=hw, half_h=hh)
            current_bias = Vec2(bp.dx * bp.magnitude, bp.dy * bp.magnitude)
            sim_step = 0; settled_count = 0
            phase = "settling"

        elif phase == "settling" and current_body is not None:
            for _ in range(STEPS_PER_FRAME):
                fx, fy = current_bias.x, current_bias.y
                torque = 0.0
                for sp in current_springs:
                    ra = _rotate_vec(sp.anchor_local, current_body.angle)
                    pw = current_body.position + ra
                    d = sp.target_world - pw
                    force_x, force_y = d.x * sp.stiffness, d.y * sp.stiffness
                    fx += force_x; fy += force_y
                    torque += ra.x * force_y - ra.y * force_x
                    dl = d.length()
                    if dl > 1e-9:
                        proj = (current_body.velocity.x * d.x + current_body.velocity.y * d.y) / (dl * dl)
                        current_body.velocity.x -= d.x * proj * sp.damping * DT
                        current_body.velocity.y -= d.y * proj * sp.damping * DT

                current_body.velocity.x += fx * DT; current_body.velocity.y += fy * DT
                current_body.velocity.x *= GLOBAL_DAMPING; current_body.velocity.y *= GLOBAL_DAMPING
                current_body.position.x += current_body.velocity.x * DT
                current_body.position.y += current_body.velocity.y * DT

                inertia = (current_body.half_w ** 2 + current_body.half_h ** 2) * 0.5
                if inertia > 0:
                    current_body.angular_vel += (torque / inertia) * DT * 0.3
                current_body.angular_vel *= ANGULAR_DAMPING
                current_body.angle += current_body.angular_vel * DT

                for sb in static_bodies:
                    _resolve_aabb(current_body, sb)

                sim_step += 1
                lin_ok = current_body.velocity.length() < VELOCITY_THRESHOLD
                ang_ok = abs(current_body.angular_vel) < ANGULAR_VEL_THRESHOLD
                if lin_ok and ang_ok:
                    settled_count += 1
                else:
                    settled_count = 0

                if settled_count >= SETTLED_FRAMES or sim_step >= MAX_STEPS:
                    # Transition to snap animation
                    snap_from = Vec2(current_body.position.x, current_body.position.y)
                    snap_from_angle = current_body.angle
                    rot_deg = _snap_angle(current_body.angle)
                    if rot_deg in (90, 270):
                        ew, eh = cur_ct_h, cur_ct_w
                    else:
                        ew, eh = cur_ct_w, cur_ct_h
                    sx = snap_to_grid(current_body.position.x - ew / 2, grid) + ew / 2
                    sy = snap_to_grid(current_body.position.y - eh / 2, grid) + eh / 2
                    snap_to_pos = Vec2(sx, sy)
                    snap_to_angle = math.radians(rot_deg)
                    snap_start = time.time()
                    phase = "snapping"
                    break

        elif phase == "snapping" and current_body is not None:
            t = min(1.0, (time.time() - snap_start) / SNAP_DURATION)
            # Smooth ease-out
            t = 1.0 - (1.0 - t) ** 3
            current_body.position.x = snap_from.x + (snap_to_pos.x - snap_from.x) * t
            current_body.position.y = snap_from.y + (snap_to_pos.y - snap_from.y) * t
            current_body.angle = snap_from_angle + (snap_to_angle - snap_from_angle) * t

            if t >= 1.0:
                # Freeze
                comp = order[comp_idx]
                ct = _get_ct_for_component(comp)
                rot_deg = _snap_angle(snap_to_angle)
                if rot_deg in (90, 270):
                    ew, eh = ct.height, ct.width
                else:
                    ew, eh = ct.width, ct.height
                snapped_x = snap_to_grid(snap_to_pos.x - ew / 2, grid)
                snapped_y = snap_to_grid(snap_to_pos.y - eh / 2, grid)

                pl = Placement(id=comp.id, x=snapped_x, y=snapped_y,
                               rotation=rot_deg, width=ct.width, height=ct.height)

                # Post-snap overlap fix
                for _ in range(20):
                    fixed = False
                    for other in placed:
                        ow2, oh2 = effective_size(other)
                        pw2, ph2 = effective_size(pl)
                        gx = max(other.x - (pl.x + pw2), pl.x - (other.x + ow2))
                        gy = max(other.y - (pl.y + ph2), pl.y - (other.y + oh2))
                        if gx >= 2.0 or gy >= 2.0:
                            continue
                        bp2 = ROLE_PHYSICS[_get_role(comp.kind)]
                        px = bp2.dx if bp2.dx != 0 else (1 if pl.x >= other.x else -1)
                        py = bp2.dy if bp2.dy != 0 else (1 if pl.y >= other.y else -1)
                        if abs(px) >= abs(py):
                            if px > 0: pl.x = snap_to_grid(other.x + ow2 + COLLISION_MARGIN, grid)
                            else: pl.x = snap_to_grid(other.x - pw2 - COLLISION_MARGIN, grid)
                        else:
                            if py > 0: pl.y = snap_to_grid(other.y + oh2 + COLLISION_MARGIN, grid)
                            else: pl.y = snap_to_grid(other.y - ph2 - COLLISION_MARGIN, grid)
                        fixed = True; break
                    if not fixed: break

                placed.append(pl); placed_map[comp.id] = (comp, pl)
                ct_map[comp.id] = (ct.width, ct.height)

                frozen = Body(comp.id, Vec2(pl.x + ew / 2, pl.y + eh / 2),
                              half_w=ew / 2 + margin_half, half_h=eh / 2 + margin_half,
                              is_static=True, angle=math.radians(rot_deg))
                static_bodies.append(frozen)

                # Add wire collision bodies
                wires = _create_wire_bodies(comp, pl, placed_map, pin_conns)
                static_bodies.extend(wires)

                current_body = None; comp_idx += 1
                phase = "waiting"; wait_start = time.time()

        elif phase == "waiting":
            if time.time() - wait_start > AUTO_ADVANCE_DELAY:
                phase = "placing"

        elif phase == "placing" and comp_idx >= len(order):
            # Run the actual router to get final wires
            from .placer_v3 import synthesize_template
            template = synthesize_template(spec)
            anchors = compute_pin_anchors(spec, placed, template)
            routed_wires = route_nets(spec, placed, anchors, template)
            routing_progress = 0
            phase = "routing"
            wait_start = time.time()

        elif phase == "routing":
            # Animate wires appearing one segment at a time
            elapsed = time.time() - wait_start
            total_segs = len(routed_wires)
            routing_progress = min(total_segs, int(elapsed * 15))  # ~15 segments per second
            if routing_progress >= total_segs:
                phase = "done"
                wait_start = time.time()

        elif phase == "done":
            if time.time() - wait_start > 3.0:
                comp_idx = 0; placed.clear(); placed_map.clear()
                static_bodies.clear(); ct_map.clear()
                routed_wires.clear(); routing_progress = 0
                current_body = None; phase = "placing"

        # ── Render ──
        screen.fill(BG)
        draw_grid(screen, W, H)

        # Wire bodies (render before components)
        for sb in static_bodies:
            if sb.is_wire:
                ehw, ehh = sb.effective_half_size()
                px, py = mm2px(sb.position.x - ehw, sb.position.y - ehh)
                pw, ph = int(ehw * 2 * SCALE), int(ehh * 2 * SCALE)
                s = pygame.Surface((max(1, pw), max(1, ph)), pygame.SRCALPHA)
                s.fill((*WIRE_COL, 60))
                screen.blit(s, (px, py))

        # Static component bodies
        for sb in static_bodies:
            if sb.is_wire:
                continue
            cw, ch = ct_map.get(sb.comp_id, (10, 10))
            px, py = mm2px(sb.position.x, sb.position.y)
            hw_px = cw / 2 * SCALE
            hh_px = ch / 2 * SCALE
            draw_rotated_rect(screen, px, py, hw_px, hh_px, sb.angle,
                              STATIC_FILL, STATIC_BORDER)

        # Springs
        if current_body is not None:
            for sp in current_springs:
                ra = _rotate_vec(sp.anchor_local, current_body.angle)
                pw = current_body.position + ra
                draw_spring(screen, pw, sp.target_world)

            draw_bias_arrow(screen, current_body, current_bias)

            # Dynamic body (rotated)
            px, py = mm2px(current_body.position.x, current_body.position.y)
            hw_px = cur_ct_w / 2 * SCALE
            hh_px = cur_ct_h / 2 * SCALE
            border = SNAP_COL if phase == "snapping" else DYN_BORDER
            draw_rotated_rect(screen, px, py, hw_px, hh_px, current_body.angle,
                              DYN_FILL, border)

        # Routed wires (drawn after routing phase starts)
        for i, route in enumerate(routed_wires[:routing_progress]):
            pts = route.points
            if len(pts) < 2:
                continue
            col = _net_color(route.net)
            for j in range(len(pts) - 1):
                p1, p2 = pts[j], pts[j + 1]
                x1, y1 = mm2px(p1.x, p1.y)
                x2, y2 = mm2px(p2.x, p2.y)
                pygame.draw.line(screen, col, (x1, y1), (x2, y2), 2)
            # Draw dots at segment endpoints
            for pt in pts:
                px, py = mm2px(pt.x, pt.y)
                pygame.draw.circle(screen, ROUTE_DOT, (px, py), 2)

        # Phase indicator bar
        if phase == "routing":
            bar_col = ROUTE_COL
        elif phase == "snapping":
            bar_col = SNAP_COL
        elif phase == "settling":
            bar_col = DYN_BORDER
        else:
            bar_col = STATIC_BORDER
        pygame.draw.rect(screen, bar_col, (0, H - 4, W, 4))
        draw_overlay(screen, font, [
            "mode: incremental",
            f"phase: {phase}",
            f"component: {cur_id or '-'}",
            "SPACE advances pause, R resets",
        ])

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="Visualize schematic block placement physics")
    parser.add_argument("input", help="Path to circuit JSON")
    parser.add_argument("--mode", choices=["incremental", "simultaneous"], default="incremental")
    args = parser.parse_args()

    with open(args.input) as f:
        spec = BlockSpec(**json.load(f))
    if args.mode == "simultaneous":
        run_simultaneous_visualization(spec)
    else:
        run_visualization(spec)


if __name__ == "__main__":
    main()
