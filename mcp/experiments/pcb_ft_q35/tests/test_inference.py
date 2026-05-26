"""Tests for inference module: pack_graph_batch, run_one, fix_placement_pass, schematic_to_graph_tensors."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def test_pack_graph_batch_shape():
    """pack_graph_batch produces correct tensor shapes from geometry-format graph_tensor."""
    from src.inference.run import pack_graph_batch

    # Geometry job format: node_features list of [node_type_id, symbol_class_id, x_q, y_q, rot_q]
    graph_tensor = {
        "node_features": [[1, 0, 0, 0, 0], [1, 2, 5, 3, 0]],
        "edge_index": [[0, 1], [1, 0]],
        "edge_features": [[1], [4]],
    }
    batch = pack_graph_batch([graph_tensor])
    assert batch["node_features"].shape[0] == 1
    assert batch["node_features"].shape[1] == 2
    assert batch["node_features"].shape[2] == 5
    assert batch["edge_index"].shape == (1, 2, 2)
    assert batch["node_mask"].shape == (1, 2)
    assert batch["node_mask"][0, 0].item() is True
    assert batch["node_mask"][0, 1].item() is True


def test_schematic_to_graph_tensors_in_memory():
    """schematic_to_graph_tensors accepts in-memory nodes/edges and returns list of graph_tensors."""
    from src.inference import schematic_to_graph_tensors

    nodes = [
        {"node_id": "U1", "node_type": "symbol", "x_mm": 0, "y_mm": 0, "refdes": "U1"},
        {"node_id": "C1", "node_type": "symbol", "x_mm": 10, "y_mm": 5, "refdes": "C1"},
    ]
    edges = [{"src": "U1", "dst": "C1", "edge_type": "near"}]
    tensors = schematic_to_graph_tensors(
        {"nodes": nodes, "edges": edges},
        center_refs=["U1"],
        radius_mm=50,
    )
    assert len(tensors) == 1
    assert "node_features" in tensors[0]
    assert "edge_index" in tensors[0]
    assert "edge_features" in tensors[0]
    assert len(tensors[0]["node_features"]) == 2
    assert len(tensors[0]["node_features"][0]) >= 4


def test_fix_placement_pass_in_memory_requires_checkpoint():
    """fix_placement_pass with in-memory graph fails fast on missing checkpoint."""
    from src.inference import fix_placement_pass

    nodes = [
        {"node_id": "U1", "node_type": "symbol", "x_mm": 0, "y_mm": 0, "refdes": "U1"},
    ]
    edges = []
    with pytest.raises((RuntimeError, FileNotFoundError, ValueError, OSError)):
        fix_placement_pass(
            schematic_state={"nodes": nodes, "edges": edges},
            window_centers=["U1"],
            radius_mm=35,
            checkpoint_dir="/nonexistent/checkpoint/path",
        )


def _has_checkpoint():
    d = os.environ.get("CHECKPOINT_DIR", "")
    return bool(d) and Path(d).joinpath("graph_encoder.pt").exists()


@pytest.mark.skipif(
    not _has_checkpoint(),
    reason="Set CHECKPOINT_DIR to a saved checkpoint to run end-to-end inference test",
)
def test_fix_placement_pass_with_real_checkpoint():
    """Run fix_placement_pass with a real checkpoint (when CHECKPOINT_DIR is set)."""
    from pathlib import Path
    from src.inference import fix_placement_pass

    checkpoint_dir = Path(os.environ["CHECKPOINT_DIR"])
    result = fix_placement_pass(
        schematic_state={
            "nodes": [
                {"node_id": "U1", "node_type": "symbol", "x_mm": 0, "y_mm": 0, "refdes": "U1"},
                {"node_id": "C1", "node_type": "symbol", "x_mm": 15, "y_mm": 5, "refdes": "C1"},
            ],
            "edges": [{"src": "U1", "dst": "C1", "edge_type": "near"}],
        },
        window_centers=["U1"],
        radius_mm=35,
        checkpoint_dir=checkpoint_dir,
        task="suggest_repair",
        max_edits=5,
        device="auto",
    )
    assert "edits" in result
    assert isinstance(result["edits"], list)


def test_run_one_returns_dict_with_dummy_model(tmp_path: Path):
    """run_one returns a dict (parsed JSON or error wrapper) when given model and tokenizer."""
    torch = pytest.importorskip("torch")
    from src.inference.run import run_one
    from src.models.graph_encoder import SchematicGraphEncoder
    from src.models.projector import GraphToQwenProjector

    # Build minimal "decoder" that has get_input_embeddings and generate
    class _MinimalDecoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = torch.nn.Embedding(1000, 1024)
            self.lm_head = torch.nn.Linear(1024, 1000)
            self.config = type("C", (), {"hidden_size": 1024})()

        def get_input_embeddings(self):
            return self.embed

        def generate(self, inputs_embeds=None, attention_mask=None, max_new_tokens=8, **kwargs):
            b, s, _ = inputs_embeds.shape
            return torch.zeros(b, max_new_tokens, dtype=torch.long)

    from src.models.qwen35_graph_model import Qwen35GraphModel

    decoder = _MinimalDecoder()
    enc = SchematicGraphEncoder(node_feature_dim=5, edge_feature_dim=1, hidden_dim=384, num_layers=2)
    proj = GraphToQwenProjector(in_dim=384, out_dim=1024, hidden_dim=1024)
    model = Qwen35GraphModel(decoder=decoder, graph_encoder=enc, projector=proj)

    class _MinimalTokenizer:
        def encode(self, text, add_special_tokens=False):
            return [1, 2, 3, 4, 5]
        def decode(self, ids, skip_special_tokens=True):
            return '{"action":"noop"}'
        bos_token_id = 0
        eos_token_id = 1
        pad_token_id = 1

    graph_tensor = {
        "node_features": [[1, 0, 0, 0, 0], [1, 2, 5, 3, 0]],
        "edge_index": [[0, 1], [1, 0]],
        "edge_features": [[1], [4]],
    }
    out = run_one(
        graph_tensor=graph_tensor,
        task="suggest_repair",
        tokenizer=_MinimalTokenizer(),
        model=model,
        max_new_tokens=16,
        device=torch.device("cpu"),
    )
    assert isinstance(out, dict)
    assert "_parse_error" in out or "action" in out or "edits" in out
