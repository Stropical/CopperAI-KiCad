"""Single-window inference: load model, run generate, parse JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import torch

from src.models.qwen35_graph_model import Qwen35GraphModel


def _ensure_long_2d_edge_index(raw_edge_index: Any) -> torch.Tensor:
    if not raw_edge_index:
        return torch.zeros((2, 0), dtype=torch.long)
    edge_index = torch.tensor(raw_edge_index, dtype=torch.long)
    if edge_index.ndim != 2:
        raise ValueError("edge_index must be 2D")
    if edge_index.shape[1] == 2:
        edge_index = edge_index.transpose(0, 1)
    if edge_index.shape[0] != 2:
        raise ValueError("edge_index must resolve to shape [2, E]")
    return edge_index


def pack_graph_batch(graph_tensors: list[dict[str, Any]], device: Optional[torch.device] = None) -> dict[str, torch.Tensor]:
    """Pack a list of graph_tensor dicts into a single batch dict (same format as GeometryCollator._pack_graph)."""
    node_features_list: list[torch.Tensor] = []
    edge_index_list: list[torch.Tensor] = []
    edge_features_list: list[torch.Tensor] = []
    max_nodes = 0
    max_edges = 0
    node_dim = 0
    edge_dim = 1

    for graph in graph_tensors:
        nf = torch.tensor(graph.get("node_features", []), dtype=torch.float32)
        if nf.ndim != 2:
            raise ValueError("node_features must be a rank-2 array")
        ei = _ensure_long_2d_edge_index(graph.get("edge_index", []))
        ef_raw = graph.get("edge_features", [])
        ef = torch.tensor(ef_raw, dtype=torch.float32)
        if ef.ndim == 1:
            ef = ef.unsqueeze(-1)
        if ef.ndim != 2:
            raise ValueError("edge_features must be rank-2 (or rank-1)")
        if ef.shape[0] != ei.shape[1]:
            ef = torch.zeros(ei.shape[1], max(1, ef.shape[1] if ef.numel() else 1), dtype=torch.float32)
        node_features_list.append(nf)
        edge_index_list.append(ei)
        edge_features_list.append(ef)
        max_nodes = max(max_nodes, int(nf.shape[0]))
        max_edges = max(max_edges, int(ei.shape[1]))
        node_dim = max(node_dim, int(nf.shape[1]))
        edge_dim = max(edge_dim, int(ef.shape[1]))

    batch_size = len(graph_tensors)
    node_features = torch.zeros(batch_size, max_nodes, node_dim, dtype=torch.float32)
    node_type_ids = torch.zeros(batch_size, max_nodes, dtype=torch.long)
    node_mask = torch.zeros(batch_size, max_nodes, dtype=torch.bool)
    edge_index = torch.full((batch_size, 2, max_edges), -1, dtype=torch.long)
    edge_features = torch.zeros(batch_size, max_edges, edge_dim, dtype=torch.float32)
    edge_type_ids = torch.zeros(batch_size, max_edges, dtype=torch.long)

    for b in range(batch_size):
        nf = node_features_list[b]
        ei = edge_index_list[b]
        ef = edge_features_list[b]
        n, e = nf.shape[0], ei.shape[1]
        node_features[b, :n, : nf.shape[1]] = nf
        node_mask[b, :n] = True
        node_type_ids[b, :n] = nf[:, 0].long().clamp_min(0)
        edge_index[b, :, :e] = ei
        edge_features[b, :e, : ef.shape[1]] = ef
        edge_type_ids[b, :e] = ef[:, 0].long().clamp_min(0)

    out = {
        "node_features": node_features,
        "edge_index": edge_index,
        "edge_features": edge_features,
        "node_type_ids": node_type_ids,
        "edge_type_ids": edge_type_ids,
        "node_mask": node_mask,
    }
    if device is not None:
        out = {k: v.to(device) for k, v in out.items()}
    return out


def run_one(
    graph_tensor: dict[str, Any],
    task: str,
    tokenizer: Any,
    model: Qwen35GraphModel,
    max_new_tokens: int = 256,
    device: Optional[torch.device] = None,
) -> dict[str, Any]:
    """Run model on a single graph window and return parsed JSON.

    Args:
        graph_tensor: One graph_tensor dict (node_features, edge_index, edge_features).
        task: Task name, e.g. "critique", "suggest_repair", "rank_candidates".
        tokenizer: Model tokenizer.
        model: Loaded Qwen35GraphModel.
        max_new_tokens: Max tokens to generate.
        device: Device for inference (default: model device).

    Returns:
        Parsed JSON dict (e.g. {"action": "...", "target_node_ids": [...]} for suggest_repair).
        On parse error returns {"_parse_error": true, "raw": "<text>"}.
    """
    if device is None:
        device = next(model.parameters(), torch.tensor(0)).device
    model.eval()

    payload = {"task": task, "input": {}}
    prompt_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    prompt_prefix = f"<task>\n{prompt_text}\n<answer>\n"
    prompt_ids = tokenizer.encode(prompt_prefix, add_special_tokens=False)
    bos = getattr(tokenizer, "bos_token_id", None)
    if isinstance(bos, int) and bos >= 0:
        prompt_ids = [bos] + prompt_ids
    input_ids = torch.tensor([prompt_ids], dtype=torch.long, device=device)
    attention_mask = torch.ones_like(input_ids, dtype=torch.long, device=device)

    graph_batch = pack_graph_batch([graph_tensor], device=device)

    with torch.no_grad():
        out_ids = model.generate(
            graph_batch=graph_batch,
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=getattr(tokenizer, "pad_token_id", None) or getattr(tokenizer, "eos_token_id", 0),
        )

    if out_ids.dim() == 2:
        out_ids = out_ids[0]
    new_tokens = out_ids[input_ids.shape[1] :].tolist()
    text = tokenizer.decode(new_tokens, skip_special_tokens=True)
    text = text.strip()
    if not text:
        return {"_parse_error": True, "raw": ""}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_parse_error": True, "raw": text}


def _resolve_device(device: Optional[str | torch.device]) -> torch.device:
    """Resolve device: 'auto' / None -> cuda > mps > cpu; pass through explicit device."""
    if device is None or (isinstance(device, str) and device == "auto"):
        if torch.cuda.is_available():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if isinstance(device, torch.device):
        return device
    return torch.device(device)


def load_model_and_tokenizer(
    checkpoint_dir: str | Path,
    device: Optional[str | torch.device] = None,
) -> tuple[Qwen35GraphModel, Any]:
    """Load Qwen35GraphModel and tokenizer from a saved checkpoint."""
    checkpoint_dir = Path(checkpoint_dir)
    target = _resolve_device(device)
    model = Qwen35GraphModel.load_pretrained(
        checkpoint_dir,
        map_location="cpu",
    )
    tokenizer = model.tokenizer
    if tokenizer is None:
        tok_dir = checkpoint_dir / "tokenizer"
        if tok_dir.exists():
            try:
                from transformers import AutoTokenizer
                tokenizer = AutoTokenizer.from_pretrained(tok_dir, trust_remote_code=True)
            except Exception as e:
                raise RuntimeError(f"Failed to load tokenizer from {tok_dir}: {e}") from e
        if tokenizer is None:
            raise RuntimeError("No tokenizer in checkpoint and no tokenizer dir found.")
    if getattr(tokenizer, "pad_token_id", None) is None and getattr(tokenizer, "eos_token_id", None) is not None:
        tokenizer.pad_token = tokenizer.eos_token
    model = model.to(target)
    return model, tokenizer
