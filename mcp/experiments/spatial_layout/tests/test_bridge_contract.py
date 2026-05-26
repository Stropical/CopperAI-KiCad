from __future__ import annotations

import json

from spatial_layout.contracts import PlacementComponent, PlacementResult
from spatial_layout.faithful_export import apply_placement_result
from spatial_layout.regen import build_placement_request, build_regen_prompt, decode_placement_result


def _bridge_ir() -> dict:
    return {
        "name": "bridge-contract",
        "components": [
            {
                "ref": "U1",
                "component_id": "cmp-u1",
                "lib_id": "Package_DIP:ATmega328P",
                "value": "MCU",
                "footprint": "Package_DIP:ATmega328P",
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
                "component_id": "cmp-c1",
                "lib_id": "Device:C_Small",
                "value": "100n",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
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
                "component_id": "cmp-r1",
                "lib_id": "Device:R_Small",
                "value": "10k",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
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


def test_local_id_binding_is_deterministic_for_a_stable_component_order() -> None:
    ir = _bridge_ir()

    first = build_regen_prompt(build_placement_request(ir, {"refs": ["C1", "U1"], "block_id": None}))
    second = build_regen_prompt(build_placement_request(ir, {"refs": ["U1", "C1"], "block_id": None}))

    assert first["ref_to_local_id"] == second["ref_to_local_id"]
    assert first["local_to_ref"] == second["local_to_ref"]
    assert set(first["ref_to_local_id"]) == {"U1", "C1", "R1"}
    assert set(first["local_to_ref"].values()) >= {"U1", "C1", "R1"}
    assert len(set(first["target_local_ids"])) == 2
    assert set(first["target_component_ids"]) == {"cmp-c1", "cmp-u1"}
    assert set(second["target_component_ids"]) == {"cmp-c1", "cmp-u1"}


def test_request_and_result_json_roundtrip_preserve_component_identity_fields() -> None:
    ir = _bridge_ir()
    request = build_placement_request(ir, {"refs": ["C1"], "block_id": None})
    request_blob = json.loads(json.dumps(request.to_dict()))
    assert request_blob["ir"]["components"][1]["component_id"] == "cmp-c1"
    assert request_blob["scope"]["refs"] == ["C1"]

    result = PlacementResult(
        components=[
            PlacementComponent(
                ref="C1",
                local_id="ID_1",
                x_mm=42.0,
                y_mm=18.5,
                rotation_deg=90,
                anchor_ref="ID_0",
                anchor_pin="1",
            )
        ]
    )
    result_blob = json.loads(json.dumps(result.to_dict()))
    assert result_blob["components"][0]["local_id"] == "ID_1"
    assert "component_id" in result_blob["components"][0]


def test_apply_and_decode_keep_metadata_when_geometry_changes() -> None:
    ir = _bridge_ir()
    request = build_placement_request(ir, {"refs": ["C1"], "block_id": None})
    prompt = build_regen_prompt(request)
    placement = decode_placement_result(
        prompt["full_tokens"],
        local_to_ref=prompt["local_to_ref"],
        page_constraints=request.page_constraints,
    )

    moved = next(component for component in placement.components if component.ref == "C1")
    moved.x_mm += 5.0
    moved.y_mm += 7.5
    moved.rotation_deg = 180

    updated = apply_placement_result(ir, PlacementResult(components=[moved]), scope_refs=["C1"])
    component = next(comp for comp in updated["components"] if comp["ref"] == "C1")

    assert component["component_id"] == "cmp-c1"
    assert component["lib_id"] == "Device:C_Small"
    assert component["value"] == "100n"
    assert component["footprint"] == "Capacitor_SMD:C_0603_1608Metric"
    assert component["x"] != 16.0 or component["y"] != 10.0
