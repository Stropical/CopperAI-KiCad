from __future__ import annotations

from pathlib import Path

from spatial_layout.contracts import PageConstraints, PlacementComponent, PlacementResult
from spatial_layout.faithful_export import apply_placement_result
from spatial_layout.json_to_tokens import structure_tokens
from spatial_layout.regen import (
    build_placement_request,
    build_regen_prompt,
    decode_placement_result,
    place_block,
)
from spatial_layout.verify import verify_placement


def _toy_ir():
    return {
        "name": "toy",
        "components": [
            {
                "ref": "U1",
                "lib_id": "Package_DIP:ATmega328P",
                "value": "MCU",
                "x": 10.0,
                "y": 10.0,
                "rotation": 0.0,
                "pins": [
                    {"num": "1", "x": 8.0, "y": 10.0, "net": "SIG"},
                    {"num": "2", "x": 10.0, "y": 12.0, "net": "VCC"},
                ],
            },
            {
                "ref": "C1",
                "lib_id": "Device:C_Small",
                "value": "100n",
                "x": 16.0,
                "y": 10.0,
                "rotation": 0.0,
                "pins": [
                    {"num": "1", "x": 15.0, "y": 10.0, "net": "VCC"},
                    {"num": "2", "x": 17.0, "y": 10.0, "net": "GND"},
                ],
            },
            {
                "ref": "R1",
                "lib_id": "Device:R_Small",
                "value": "10k",
                "x": 20.0,
                "y": 10.0,
                "rotation": 0.0,
                "pins": [
                    {"num": "1", "x": 19.0, "y": 10.0, "net": "SIG"},
                    {"num": "2", "x": 21.0, "y": 10.0, "net": "VCC"},
                ],
            },
        ],
        "nets": [
            {"name": "SIG", "pins": ["U1:1", "R1:1"]},
            {"name": "VCC", "pins": ["U1:2", "C1:1", "R1:2"]},
            {"name": "GND", "pins": ["C1:2"]},
        ],
    }


def test_structure_serialization_is_deterministic_and_covers_all_net_pins():
    ir = _toy_ir()
    tokens_a = structure_tokens(ir)
    tokens_b = structure_tokens(ir)
    assert tokens_a == tokens_b

    members = []
    idx = 0
    while idx < len(tokens_a):
        if tokens_a[idx] == "MEMBER":
            members.append((tokens_a[idx + 2], tokens_a[idx + 4]))
        idx += 1
    assert len(members) == 6
    assert len(set(members)) == 6


def test_regen_prompt_has_expected_shape():
    request = build_placement_request(_toy_ir(), {"refs": ["C1"], "block_id": None})
    prompt = build_regen_prompt(request)
    tokens = prompt["prompt_tokens"]
    assert tokens[0] == "BOS"
    assert tokens[1] == "STRUCT_START"
    assert tokens[-2:] == ["SEP", "PLACE_START"]


def test_decode_placement_result_is_stable():
    request = build_placement_request(_toy_ir(), {"refs": ["C1"], "block_id": None})
    prompt = build_regen_prompt(request)
    result = decode_placement_result(
        prompt["full_tokens"],
        local_to_ref=prompt["local_to_ref"],
        local_to_component_id=prompt["local_to_component_id"],
        component_id_to_ref=prompt["component_id_to_ref"],
        page_constraints=request.page_constraints,
    )
    refs = [component.ref for component in result.components]
    assert set(refs) == {"C1", "R1", "U1"}
    assert all(component.component_id for component in result.components)
    assert result.components[0].rotation_deg in {0, 90, 180, 270}


def test_apply_respects_scope_and_locks():
    ir = _toy_ir()
    placement = PlacementResult(
        components=[
            PlacementComponent(ref="C1", x_mm=50.0, y_mm=50.0),
            PlacementComponent(ref="R1", x_mm=60.0, y_mm=60.0),
        ]
    )
    out = apply_placement_result(ir, placement, scope_refs=["C1"], locked_refs=["C1"])
    comps = {comp["ref"]: comp for comp in out["components"]}
    assert comps["C1"]["x"] == 16.0
    assert comps["R1"]["x"] == 20.0


def test_apply_rotation_and_mirror_updates_pin_geometry():
    ir = {
        "components": [
            {
                "ref": "R1",
                "lib_id": "Device:R",
                "x": 10.0,
                "y": 10.0,
                "rotation": 0.0,
                "pins": [{"num": "1", "x": 12.0, "y": 10.0, "net": "SIG"}],
            }
        ],
        "nets": [{"name": "SIG", "pins": ["R1:1"]}],
    }
    rotated = apply_placement_result(
        ir,
        PlacementResult(components=[PlacementComponent(ref="R1", x_mm=20.0, y_mm=20.0, rotation_deg=90)]),
    )
    pin = rotated["components"][0]["pins"][0]
    assert round(pin["x"], 3) == 20.0
    assert round(pin["y"], 3) == 22.0

    mirrored = apply_placement_result(
        ir,
        PlacementResult(components=[PlacementComponent(ref="R1", x_mm=20.0, y_mm=20.0, mirror="x")]),
    )
    pin = mirrored["components"][0]["pins"][0]
    assert round(pin["x"], 3) == 18.0
    assert round(pin["y"], 3) == 20.0


def test_verify_rejects_overlap():
    ir = _toy_ir()
    request = build_placement_request(ir, {"refs": ["C1", "R1"], "block_id": None})
    placement = PlacementResult(
        components=[
            PlacementComponent(ref="C1", x_mm=30.0, y_mm=30.0),
            PlacementComponent(ref="R1", x_mm=30.0, y_mm=30.0),
        ]
    )
    placed_ir = apply_placement_result(ir, placement, scope_refs=["C1", "R1"])
    verify = verify_placement(request=request, placement=placement, original_ir=ir, placed_ir=placed_ir)
    assert verify["ok"] is False
    assert any(issue["code"] == "component_overlap" for issue in verify["issues"])


def test_full_pipeline_writes_preview_artifact(tmp_path: Path):
    preview_path = tmp_path / "preview_faithful.kicad_sch"
    result = place_block(
        _toy_ir(),
        {"refs": ["C1", "R1"], "block_id": None},
        page_constraints=PageConstraints(anchor_xy_mm=(80.0, 80.0)),
        preview_path=preview_path,
    )
    assert result["verify"]["ok"] is True
    assert preview_path.exists()
    text = preview_path.read_text(encoding="utf-8")
    assert "(kicad_sch" in text
    assert "Device:C" in text or "Device:R" in text


def test_agent_harness_uses_machine_tokens_only(tmp_path: Path):
    ir = _toy_ir()

    def agent_call():
        return place_block(
            ir,
            {"refs": ["C1"], "block_id": None},
            preview_path=tmp_path / "agent_preview.kicad_sch",
        )

    first = agent_call()
    second = agent_call()
    assert first["placement"] == second["placement"]
    assert first["verify"] == second["verify"]
    assert all(isinstance(token, str) and " " not in token for token in first["debug"]["prompt_tokens"])
