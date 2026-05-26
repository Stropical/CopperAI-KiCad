"""Smoke tests for reconstructed_objects → .kicad_sch export."""

from spatial_layout.export_kicad import reconstructed_objects_to_kicad_sch
from spatial_layout.infer import reconstruct_objects
from spatial_layout.token_model import (
    AnchorBlock,
    NET_GND,
    RelativePose,
    TokenObject,
)


def test_reconstructed_objects_to_kicad_sch_contains_symbol():
    pose = RelativePose("SIDE_UP", "DX_P1", "DY_N2", "DIST_NEAR", "ROT_0")
    obj = TokenObject(
        "ID_1",
        "TYPE_C",
        "ROLE_DECOUP",
        "TOPO_SHUNT",
        "ID_0",
        "25",
        pose,
        net_role=NET_GND,
    )
    block = AnchorBlock(
        "ID_0",
        "25",
        objects=[obj],
        page_grid_x="PAGE_X_P2",
        page_grid_y="PAGE_Y_N1",
    )
    block_tokens = block.to_tokens()
    tokens = ["BOS", *block_tokens, "EOS"]
    objects = reconstruct_objects(tokens)
    assert len(objects) >= 1
    sch = reconstructed_objects_to_kicad_sch(objects)
    assert "(kicad_sch" in sch
    assert "Device:C" in sch or "Device:R" in sch


def test_decode_dx_dy_offsets_sheet():
    objs = [
        {
            "ref": "ID_0",
            "type": "TYPE_R",
            "role": "ROLE_OTHER",
            "topology": "TOPO_INLINE",
            "anchor_ref": "ID_PAGE",
            "anchor_pin": "0",
            "pose": {
                "side": "DIR_E",
                "dx_bin": "DX_P2",
                "dy_bin": "DY_0",
                "dist": "DIST_NEAR",
                "rotation": "ROT_0",
                "mirror": "NO_MIRROR",
            },
        }
    ]
    sch = reconstructed_objects_to_kicad_sch(objs)
    assert "(kicad_sch" in sch
    assert "Device:R" in sch
    assert "R1" in sch



def test_page_grid_anchor_is_preserved_in_export():
    pose = RelativePose("DIR_E", "DX_P2", "DY_N1", "DIST_NEAR", "ROT_0")
    obj = TokenObject(
        "ID_1",
        "TYPE_R",
        "ROLE_OTHER",
        "TOPO_INLINE",
        "ID_PAGE",
        "0",
        pose,
    )
    block = AnchorBlock(
        "ID_PAGE",
        "0",
        objects=[obj],
        page_grid_x="PAGE_X_P2",
        page_grid_y="PAGE_Y_N1",
    )
    objects = reconstruct_objects(["BOS", *block.to_tokens(), "EOS"])

    assert objects[0]["page_grid_x"] == "PAGE_X_P2"
    assert objects[0]["page_grid_y"] == "PAGE_Y_N1"

    sch = reconstructed_objects_to_kicad_sch(objects, anchor_x_mm=80.0, anchor_y_mm=80.0)

    # PAGE_X_P2/PAGE_Y_N1 decode to (20, -10) mm, then DX_P2/DY_N1 add (+5.08, -2.54) mm.
    assert "25.08" in sch
    assert "-12.54" in sch
    assert "85.08" not in sch
    assert "67.46" not in sch
