"""Tests for IR-faithful KiCad export and token placement application."""

from spatial_layout.faithful_export import apply_placement_tokens, ir_to_kicad_sch
from spatial_layout.json_to_tokens import ir_to_tokens


def test_ir_to_kicad_contains_real_lib_id():
    ir = {
        "name": "t",
        "components": [
            {
                "ref": "R1",
                "lib_id": "Device:R",
                "value": "10k",
                "x": 100.0,
                "y": 100.0,
                "rotation": 0.0,
                "pins": [
                    {"num": "1", "name": "~", "x": 100.0, "y": 103.81, "net": "A"},
                    {"num": "2", "name": "~", "x": 100.0, "y": 96.19, "net": "GND"},
                ],
            }
        ],
        "nets": [{"name": "GND", "pins": ["R1:2"]}],
    }
    text = ir_to_kicad_sch(ir, emit_gnd_power_symbols=False)
    assert "(kicad_sch" in text
    assert "Device:R" in text
    assert "R1" in text


def test_apply_placement_tokens_matches_object_positions_mm():
    from spatial_layout.export_kicad import object_positions_mm
    from spatial_layout.infer import reconstruct_objects

    ir = {
        "components": [
            {
                "ref": "R1",
                "lib_id": "Device:R",
                "value": "10k",
                "x": 80.0,
                "y": 80.0,
                "rotation": 0.0,
                "pins": [{"num": "1", "x": 80.0, "y": 83.81, "net": "A"}],
            }
        ],
        "nets": [],
    }
    tokens = ir_to_tokens(ir)
    objs = reconstruct_objects(tokens)
    want = {lid: (x, y) for lid, x, y in object_positions_mm(objs, anchor_x_mm=80.0, anchor_y_mm=80.0)}
    out = apply_placement_tokens({"components": list(ir["components"]), "nets": []}, tokens, anchor_x_mm=80.0, anchor_y_mm=80.0)
    c0 = out["components"][0]
    exp_x, exp_y = want["ID_0"]
    assert abs(float(c0["x"]) - exp_x) < 1e-3
    assert abs(float(c0["y"]) - exp_y) < 1e-3
