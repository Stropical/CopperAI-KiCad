import importlib
from pathlib import Path

import pytest


torch = pytest.importorskip("torch")
nn = torch.nn


class _DummyDecoder(nn.Module):
    def __init__(self, hidden_size=1024, vocab_size=128):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, hidden_size)
        self.lm_head = nn.Linear(hidden_size, vocab_size)
        self.config = type("Cfg", (), {"hidden_size": hidden_size})()
        self.last_generate_inputs_embeds = None
        self.last_generate_attention_mask = None

    def get_input_embeddings(self):
        return self.embed

    def forward(self, input_ids=None, inputs_embeds=None, attention_mask=None, labels=None, **kwargs):
        if inputs_embeds is None:
            assert input_ids is not None
            inputs_embeds = self.embed(input_ids)
        logits = self.lm_head(inputs_embeds)
        out = {"logits": logits}
        if labels is not None:
            out["loss"] = logits.float().mean() * 0 + torch.tensor(0.0, device=logits.device)
        return out

    def generate(self, inputs_embeds=None, attention_mask=None, max_new_tokens=4, **kwargs):
        self.last_generate_inputs_embeds = inputs_embeds
        self.last_generate_attention_mask = attention_mask
        bsz = inputs_embeds.size(0)
        return torch.zeros(bsz, max_new_tokens, dtype=torch.long, device=inputs_embeds.device)


def _import_module():
    for name in ("src.models.qwen35_graph_model", "models.qwen35_graph_model", "qwen35_graph_model"):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            continue
    raise AssertionError("Could not import qwen35_graph_model module")


def _sample_graph(batch_size=2, num_nodes=5, node_dim=32, num_edges=6, edge_dim=16):
    node_features = torch.randn(batch_size, num_nodes, node_dim)
    edge_index = torch.randint(0, num_nodes, (batch_size, 2, num_edges))
    edge_features = torch.randn(batch_size, num_edges, edge_dim)
    node_type_ids = torch.randint(0, 8, (batch_size, num_nodes))
    edge_type_ids = torch.randint(0, 8, (batch_size, num_edges))
    node_mask = torch.ones(batch_size, num_nodes, dtype=torch.bool)
    return {
        "node_features": node_features,
        "edge_index": edge_index,
        "edge_features": edge_features,
        "node_type_ids": node_type_ids,
        "edge_type_ids": edge_type_ids,
        "node_mask": node_mask,
    }


def test_qwen35_graph_forward_and_generate():
    mod = _import_module()
    model_cls = mod.Qwen35GraphModel

    decoder = _DummyDecoder(hidden_size=1024)
    model = model_cls(decoder=decoder)

    graph_batch = _sample_graph(batch_size=2)
    input_ids = torch.randint(0, 100, (2, 7))
    attention_mask = torch.ones_like(input_ids)

    out = model(graph_batch=graph_batch, input_ids=input_ids, attention_mask=attention_mask)
    assert isinstance(out, dict)
    assert "logits" in out
    assert out["logits"].shape[0] == 2
    assert out["logits"].shape[1] == graph_batch["node_features"].size(1) + input_ids.size(1)

    generated = model.generate(
        graph_batch=graph_batch,
        input_ids=input_ids,
        attention_mask=attention_mask,
        max_new_tokens=3,
    )
    assert generated.shape == (2, 3)
    assert decoder.last_generate_inputs_embeds is not None
    assert decoder.last_generate_attention_mask is not None


def test_qwen35_graph_save_pretrained(tmp_path: Path):
    mod = _import_module()
    model_cls = mod.Qwen35GraphModel

    decoder = _DummyDecoder(hidden_size=1024)
    model = model_cls(decoder=decoder)

    out_dir = tmp_path / "graph_model"
    model.save_pretrained(out_dir)

    assert (out_dir / "graph_encoder.pt").exists()
    assert (out_dir / "graph_projector.pt").exists()
    assert (out_dir / "graph_model_config.json").exists()
