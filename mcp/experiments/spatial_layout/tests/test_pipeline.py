import pytest
import torch
from spatial_layout import dataset as dataset_mod
from spatial_layout.token_model import (
    AnchorBlock,
    BOS,
    EOS,
    NET_GND,
    NET_ROLE,
    OBJ_END,
    OBJ_START,
    RelativePose,
    TokenObject,
)
from spatial_layout.train import _geometry_validity_loss
from spatial_layout.grammar import parse_token_stream, validate_sequence
from spatial_layout.json_to_tokens import classify_topology, ir_to_tokens, normalize_kind

def test_token_roundtrip():
    pose = RelativePose("SIDE_UP", "DX_P1", "DY_N2", "DIST_NEAR", "ROT_0")
    obj = TokenObject("ID_1", "TYPE_C", "ROLE_DECOUP", "TOPO_SHUNT", "ID_0", "25", pose, net_role=NET_GND)
    tokens = obj.to_tokens()
    
    assert tokens[0] == OBJ_START
    assert tokens[-1] == OBJ_END
    
    block = AnchorBlock("ID_0", "25", objects=[obj], page_grid_x="PAGE_X_P2", page_grid_y="PAGE_Y_N1")
    block_tokens = block.to_tokens()

    assert validate_sequence(["BOS", *block_tokens, "EOS"])
    
    parsed = parse_token_stream(block_tokens)
    assert len(parsed) == 1
    assert parsed[0].anchor_ref == "ID_0"
    assert parsed[0].page_grid_x == "PAGE_X_P2"
    assert parsed[0].page_grid_y == "PAGE_Y_N1"
    assert parsed[0].objects[0].ref == "ID_1"
    assert parsed[0].objects[0].pose.side == "SIDE_UP"
    assert parsed[0].objects[0].net_role == NET_GND
    assert NET_ROLE in tokens


def test_validate_sequence_requires_full_structure():
    valid = ["BOS", "ANCHOR_BLOCK", "REF", "ID_0", "PIN", "25", "PAGE_GRID_X", "PAGE_X_0", "PAGE_GRID_Y", "PAGE_Y_0"] + TokenObject(
        "ID_1",
        "TYPE_C",
        "ROLE_DECOUP",
        "TOPO_SHUNT",
        "ID_0",
        "25",
        RelativePose("SIDE_UP", "DX_P1", "DY_N2", "DIST_NEAR", "ROT_0"),
        net_role=NET_GND,
    ).to_tokens() + ["ANCHOR_BLOCK_END", "EOS"]
    assert validate_sequence(valid)

    invalid = ["BOS", "ANCHOR_BLOCK", "REF", "U9", "PIN", "25", "OBJ_START", "ROLE", "ROLE_OTHER", "OBJ_END", "ANCHOR_BLOCK_END", "EOS"]
    assert not validate_sequence(invalid)

def test_normalization():
    assert normalize_kind("Device:C_Small") == "TYPE_C"
    assert normalize_kind("Device:R_Small") == "TYPE_R"
    assert normalize_kind("Transistor_FET:Q_PMOS_GSD") == "TYPE_FET"

def test_topology():
    comp = {
        "lib_id": "Device:C_Small",
        "pins": [{"net": "+5V"}, {"net": "GND"}]
    }
    assert classify_topology(comp) == "TOPO_SHUNT"

def test_ir_to_tokens_basic():
    ir = {
        "components": [
            {
                "ref": "U1",
                "lib_id": "Package_DIP:ATmega328P",
                "x": 0, "y": 0, "rotation": 0,
                "pins": [{"num": "7", "name": "VCC", "x": 0, "y": 0, "net": "VCC"}]
            },
            {
                "ref": "C1",
                "lib_id": "Device:C_Small",
                "x": 2.54, "y": 0, "rotation": 0,
                "pins": [
                    {"num": "1", "name": "1", "x": 2.54, "y": 0, "net": "VCC"},
                    {"num": "2", "name": "2", "x": 2.54, "y": 2.54, "net": "GND"}
                ]
            }
        ]
    }
    tokens = ir_to_tokens(ir)
    assert BOS in tokens
    assert EOS in tokens
    assert "REF" in tokens
    assert "ID_0" in tokens
    assert "ID_1" in tokens
    assert "PAGE_GRID_X" in tokens
    assert "PAGE_GRID_Y" in tokens
    assert "U1" not in tokens
    assert "C1" not in tokens


def test_legacy_side_tokens_parse():
    tokens = [
        "BOS",
        "ANCHOR_BLOCK",
        "REF",
        "U1",
        "PIN",
        "1",
        "OBJ_START",
        "TYPE",
        "TYPE_R",
        "ROLE",
        "ROLE_PULLUP",
        "REF",
        "R1",
        "ANCHOR_REF",
        "U1",
        "ANCHOR_PIN",
        "1",
        "SIDE_LEFT",
        "DX_N1",
        "DY_0",
        "DIST_NEAR",
        "TOPO",
        "TOPO_INLINE",
        "ROT_0",
        "NO_MIRROR",
        "OBJ_END",
        "ANCHOR_BLOCK_END",
        "EOS",
    ]
    parsed = parse_token_stream(tokens)
    assert parsed[0].objects[0].pose.side == "SIDE_LEFT"


def test_discover_data_files_prefers_parquet(tmp_path):
    token_file = tmp_path / "sample.tokens.json"
    parquet_dir = tmp_path / "parquet"
    parquet_dir.mkdir()
    parquet_file = parquet_dir / "part-00000.parquet"
    token_file.write_text('["BOS", "EOS"]')
    parquet_file.write_bytes(b"parquet")

    files = dataset_mod.discover_data_files(str(tmp_path))
    assert files == [str(parquet_file)]


def test_dataset_reads_parquet_rows(monkeypatch, tmp_path):
    parquet_file = tmp_path / "sample.parquet"
    parquet_file.write_bytes(b"parquet")

    monkeypatch.setattr(
        dataset_mod,
        "_load_parquet_rows",
        lambda path: [
            {
                "source_path": "sample.sch",
                "tokens": ["BOS", "ANCHOR_BLOCK", "REF", "U1", "PIN", "1", "ANCHOR_BLOCK_END", "EOS"],
                "ir_context_json": '{"components": [{"ref": "U1", "x": 1, "y": 2}]}',
            }
        ],
    )

    ds = dataset_mod.SpatialLayoutDataset([str(parquet_file)], max_len=16)
    assert len(ds) == 1
    x, y, ir_ctx = ds[0]
    assert x.shape[0] == 15
    assert y.shape[0] == 15
    assert ir_ctx is not None
    assert ir_ctx["components"][0]["ref"] == "U1"


def test_dataset_exposes_spatial_context_top_level(monkeypatch, tmp_path):
    parquet_file = tmp_path / "sample.parquet"
    parquet_file.write_bytes(b"parquet")

    monkeypatch.setattr(
        dataset_mod,
        "_load_parquet_rows",
        lambda path: [
            {
                "source_path": "sample.sch",
                "tokens": [
                    "BOS",
                    "ANCHOR_BLOCK",
                    "REF",
                    "U1",
                    "PIN",
                    "1",
                    "OBJ_START",
                    "TYPE",
                    "TYPE_C",
                    "ROLE",
                    "ROLE_DECOUP",
                    "REF",
                    "C1",
                    "ANCHOR_REF",
                    "U1",
                    "ANCHOR_PIN",
                    "1",
                    "SIDE_LEFT",
                    "DX_0",
                    "DY_0",
                    "DIST_NEAR",
                    "TOPO",
                    "TOPO_SHUNT",
                    "ROT_0",
                    "NO_MIRROR",
                    "OBJ_END",
                    "ANCHOR_BLOCK_END",
                    "EOS",
                ],
                "ir_context_json": '{"components": [{"ref": "U1", "x": 1, "y": 2}]}',
            }
        ],
    )

    ds = dataset_mod.SpatialLayoutDataset([str(parquet_file)], max_len=32)
    _, _, ir_ctx = ds[0]
    assert ir_ctx is not None
    assert ir_ctx["relation_context"] is not None
    assert ir_ctx["blocks"] is not None
    assert ir_ctx["token_positions"] is not None


def test_dataset_uses_cached_relation_context(monkeypatch, tmp_path):
    parquet_file = tmp_path / "sample.parquet"
    parquet_file.write_bytes(b"parquet")

    monkeypatch.setattr(
        dataset_mod,
        "_load_parquet_rows",
        lambda path: [
            {
                "source_path": "sample.sch",
                "tokens": [
                    "BOS",
                    "ANCHOR_BLOCK",
                    "REF",
                    "U1",
                    "PIN",
                    "1",
                    "ANCHOR_BLOCK_END",
                    "EOS",
                ],
                "ir_context_json": '{"components": [{"ref": "U1", "x": 1, "y": 2}]}',
                "relation_context_json": '{"blocks": [{"anchor_xy": [1, 2], "objects": []}], "token_positions": []}',
            }
        ],
    )
    monkeypatch.setattr(
        dataset_mod,
        "_build_relation_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not rebuild")),
    )

    ds = dataset_mod.SpatialLayoutDataset([str(parquet_file)], max_len=16)
    _, _, ir_ctx = ds[0]
    assert ir_ctx is not None
    assert ir_ctx["relation_context"]["blocks"][0]["anchor_xy"] == [1, 2]


def test_geometry_validity_loss_penalizes_overlap():
    hidden = torch.tensor(
        [[[0.0, 0.0], [0.35, 0.0], [1.5, 1.5]]],
        dtype=torch.float32,
    )
    relation_context = [
        {
            "blocks": [
                {
                    "anchor_xy": [0.0, 0.0],
                    "objects": [
                        {
                            "xy": [0.0, 0.0],
                            "type": "TYPE_C",
                            "package_bin": "PKG_SMD_SMALL",
                            "role": "ROLE_DECOUP",
                        },
                        {
                            "xy": [0.5, 0.0],
                            "type": "TYPE_C",
                            "package_bin": "PKG_SMD_SMALL",
                            "role": "ROLE_DECOUP",
                        },
                    ],
                }
            ],
            "token_positions": [
                {"block_index": 0, "object_index": 0},
                {"block_index": 0, "object_index": 1},
                {"block_index": 0, "object_index": 1},
            ],
        }
    ]

    align_loss, relation_loss, overlap_loss = _geometry_validity_loss(
        hidden,
        relation_context,
        torch.nn.Identity(),
        coord_scale=1.0,
    )

    assert align_loss.item() >= 0.0
    assert relation_loss.item() >= 0.0
    assert overlap_loss.item() > 0.0


def _single_block_tokens(
    side: str = "SIDE_UP",
    dx: str = "DX_P1",
    dy: str = "DY_N2",
    rot: str = "ROT_0",
    mirror: str = "NO_MIRROR",
):
    return [
        "BOS",
        "ANCHOR_BLOCK",
        "REF",
        "ID_0",
        "PIN",
        "1",
        "PAGE_GRID_X",
        "PAGE_X_0",
        "PAGE_GRID_Y",
        "PAGE_Y_0",
        "OBJ_START",
        "TYPE",
        "TYPE_C",
        "ROLE",
        "ROLE_DECOUP",
        "REF",
        "ID_1",
        "ANCHOR_REF",
        "ID_0",
        "ANCHOR_PIN",
        "1",
        side,
        dx,
        dy,
        "DIST_NEAR",
        "TOPO",
        "TOPO_SHUNT",
        rot,
        mirror,
        "OBJ_END",
        "ANCHOR_BLOCK_END",
        "EOS",
    ]


def _two_block_tokens():
    block_a = [
        "ANCHOR_BLOCK",
        "REF",
        "ID_0",
        "PIN",
        "1",
        "PAGE_GRID_X",
        "PAGE_X_0",
        "PAGE_GRID_Y",
        "PAGE_Y_0",
        "ANCHOR_BLOCK_END",
    ]
    block_b = [
        "ANCHOR_BLOCK",
        "REF",
        "ID_2",
        "PIN",
        "2",
        "PAGE_GRID_X",
        "PAGE_X_P1",
        "PAGE_GRID_Y",
        "PAGE_Y_N1",
        "ANCHOR_BLOCK_END",
    ]
    return ["BOS", *block_a, *block_b, "EOS"]


def test_layout_rotation_updates_pose_tokens_consistently():
    tokens = _single_block_tokens(side="SIDE_UP", dx="DX_P1", dy="DY_N2", rot="ROT_90")
    augmented = dataset_mod._apply_layout_augmentation(tokens, rotation_k=1)

    assert augmented[21] == "SIDE_RIGHT"
    assert augmented[22] == "DX_N2"
    assert augmented[23] == "DY_N1"
    assert augmented[27] == "ROT_180"
    assert validate_sequence(augmented)


def test_layout_mirror_updates_mirror_and_signed_bins_consistently():
    tokens = _single_block_tokens(
        side="SIDE_LEFT",
        dx="DX_N3",
        dy="DY_P2",
        rot="ROT_0",
        mirror="NO_MIRROR",
    )
    augmented = dataset_mod._apply_layout_augmentation(tokens, mirror_axis="Y")

    assert augmented[21] == "SIDE_RIGHT"
    assert augmented[22] == "DX_P3"
    assert augmented[23] == "DY_P2"
    assert augmented[28] == "MIRROR_Y"
    assert validate_sequence(augmented)


def test_layout_block_shuffle_reorders_anchor_blocks():
    class ReverseRng:
        def shuffle(self, values):
            values.reverse()

    tokens = _two_block_tokens()
    augmented = dataset_mod._apply_layout_augmentation(tokens, shuffle_blocks=True, rng=ReverseRng())

    assert augmented != tokens
    assert validate_sequence(augmented)
    assert augmented[3] == "ID_2"


def test_train_augmentation_applies_only_to_train_split(monkeypatch, tmp_path):
    parquet_file = tmp_path / "sample.parquet"
    parquet_file.write_bytes(b"parquet")
    base_tokens = _single_block_tokens(rot="ROT_0")

    monkeypatch.setattr(
        dataset_mod,
        "_load_parquet_rows",
        lambda path: [
            {"tokens": list(base_tokens), "ir_context_json": '{"components": []}'},
            {"tokens": list(base_tokens), "ir_context_json": '{"components": []}'},
            {"tokens": list(base_tokens), "ir_context_json": '{"components": []}'},
            {"tokens": list(base_tokens), "ir_context_json": '{"components": []}'},
        ],
    )
    monkeypatch.setattr(
        dataset_mod,
        "_augment_token_sequence",
        lambda tokens: dataset_mod._apply_layout_augmentation(tokens, rotation_k=1),
    )

    train_loader, val_loader, vocab = dataset_mod.create_dataloaders(
        str(tmp_path),
        batch_size=1,
        train_split=0.5,
        seed=11,
        max_len=64,
        train_augmentation=True,
    )
    itos = {i: t for t, i in vocab.items()}
    pad_idx = vocab["<PAD>"]

    train_x, _, _ = next(iter(train_loader))
    val_x, _, _ = next(iter(val_loader))

    train_tokens = [itos[i] for i in train_x[0].tolist() if i != pad_idx]
    val_tokens = [itos[i] for i in val_x[0].tolist() if i != pad_idx]

    assert "ROT_90" in train_tokens
    assert "ROT_90" not in val_tokens
    assert "ROT_0" in val_tokens


def test_create_dataloaders_dataset_fraction(monkeypatch, tmp_path):
    parquet_file = tmp_path / "sample.parquet"
    parquet_file.write_bytes(b"parquet")
    base_tokens = _single_block_tokens(rot="ROT_0")

    monkeypatch.setattr(
        dataset_mod,
        "_load_parquet_rows",
        lambda path: [
            {"tokens": list(base_tokens), "ir_context_json": "{}"},
            {"tokens": list(base_tokens), "ir_context_json": "{}"},
            {"tokens": list(base_tokens), "ir_context_json": "{}"},
            {"tokens": list(base_tokens), "ir_context_json": "{}"},
        ],
    )

    train_loader, val_loader, _vocab = dataset_mod.create_dataloaders(
        str(tmp_path),
        batch_size=1,
        train_split=0.5,
        seed=0,
        max_len=64,
        dataset_fraction=0.5,
    )
    assert len(train_loader.dataset) == 1
    assert len(val_loader.dataset) == 1
