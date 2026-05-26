from spatial_layout.token_model import (
    AnchorBlock,
    BOS,
    EOS,
    NET_GND,
    RelativePose,
    TokenObject,
    TYPE_R,
    ROLE_PULLUP,
    TOPO_INLINE,
    DIR_E,
    ROT_0,
    NO_MIRROR,
    DIST_NEAR
)
from spatial_layout.infer import reconstruct_objects
from spatial_layout.export_kicad import reconstructed_objects_to_kicad_sch
from spatial_layout.layout_decode import decode_pose_bins_to_offset_mm

def test_pipeline_roundtrip():
    # 1. Start with a high-level representation
    pose = RelativePose(
        direction=DIR_E,
        dx_bin='DX_P2', # 2 * 2.54 = 5.08mm
        dy_bin='DY_N1', # -1 * 2.54 = -2.54mm
        dist=DIST_NEAR,
        rotation=ROT_0,
        mirror=NO_MIRROR
    )
    obj = TokenObject(
        ref='ID_1',
        type=TYPE_R,
        role=ROLE_PULLUP,
        topology=TOPO_INLINE,
        anchor_ref='ID_0',
        anchor_pin='1',
        pose=pose,
        value_bin='VAL_NICE',
        package_bin='PKG_SMD_MED',
        net_role=NET_GND
    )
    block = AnchorBlock(
        anchor_ref='ID_0',
        anchor_pin='1',
        page_grid_x='PAGE_X_0',
        page_grid_y='PAGE_Y_0',
        objects=[obj]
    )

    # 2. Convert to tokens
    tokens = ['BOS'] + block.to_tokens() + ['EOS']

    # 3. Reconstruct objects from tokens
    reconstructed = reconstruct_objects(tokens)
    assert len(reconstructed) == 1
    r_obj = reconstructed[0]

    assert r_obj['ref'] == 'ID_1'
    assert r_obj['type'] == TYPE_R
    assert r_obj['pose']['dx_bin'] == 'DX_P2'
    assert r_obj['pose']['dy_bin'] == 'DY_N1'

    # 4. Verify coordinate decoding
    dx_mm, dy_mm = decode_pose_bins_to_offset_mm(r_obj['pose'])
    assert abs(dx_mm - 5.08) < 1e-6
    assert abs(dy_mm - (-2.54)) < 1e-6

    # 5. Convert to KiCad Schematic
    sch = reconstructed_objects_to_kicad_sch(reconstructed, anchor_x_mm=100.0, anchor_y_mm=100.0)

    # Check that it contains the expected KiCad S-expression elements
    assert '(kicad_sch' in sch
    assert 'Device:R' in sch
    # Coordinates in KiCad are often in mm in the newer versions.
    # sch2py uses mm.
    # 100 + 5.08 = 105.08
    # 100 - 2.54 = 97.46
    assert '105.08' in sch
    assert '97.46' in sch

from spatial_layout.json_to_tokens import ir_to_tokens

def test_ir_to_kicad_roundtrip():
    # 1. Start with a minimal IR
    ir = {
        'components': [
            {
                'ref': 'U1',
                'lib_id': 'Package_DIP:ATmega328P',
                'x': 0.0, 'y': 0.0, 'rotation': 0.0,
                'pins': [{'num': '7', 'name': 'VCC', 'x': 0.0, 'y': 0.0, 'net': 'VCC'}]
            },
            {
                'ref': 'C1',
                'lib_id': 'Device:C_Small',
                'x': 2.54, 'y': 0.0, 'rotation': 0.0,
                'pins': [
                    {'num': '1', 'name': '1', 'x': 2.54, 'y': 0.0, 'net': 'VCC'},
                    {'num': '2', 'name': '2', 'x': 2.54, 'y': 2.54, 'net': 'GND'}
                ]
            }
        ]
    }

    # 2. IR -> Tokens
    tokens = ir_to_tokens(ir)
    assert 'BOS' in tokens
    assert 'EOS' in tokens

    # 3. Tokens -> Reconstructed Objects
    reconstructed = reconstruct_objects(tokens)
    # The IR has 2 components.
    # U1 is at (0,0), C1 is at (2.54, 0).
    # json_to_tokens will likely make one an anchor and the other relative.
    assert len(reconstructed) >= 1

    # 4. Reconstructed Objects -> KiCad Schematic
    sch = reconstructed_objects_to_kicad_sch(reconstructed)
    assert '(kicad_sch' in sch
    # Should find at least one of the symbol types (mapped to common symbols)
    # Device:C_Small -> TYPE_C -> Device:C
    # Package_DIP:ATmega328P -> TYPE_IC -> Amplifier_Operational:LM358 (as per _TYPE_TO_LIB_ID)
    assert 'Device:C' in sch or 'Amplifier_Operational:LM358' in sch
