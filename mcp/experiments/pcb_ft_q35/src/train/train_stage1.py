from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

if __package__ in {None, ""}:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

try:
    from src.models.graph_encoder import SchematicGraphEncoder  # type: ignore
except Exception:
    SchematicGraphEncoder = None

try:
    from src.models.projector import GraphToQwenProjector  # type: ignore
except Exception:
    GraphToQwenProjector = None

try:
    from src.data import graph_schema as _graph_schema_module  # type: ignore
except Exception:
    _graph_schema_module = None

try:
    from src.data import window_sampler as _window_sampler_module  # type: ignore
except Exception:
    _window_sampler_module = None


class FallbackGraphEncoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, num_layers: int) -> None:
        super().__init__()
        layers: List[nn.Module] = []
        in_dim = input_dim
        for _ in range(max(1, num_layers)):
            layers.append(nn.Linear(in_dim, hidden_dim))
            layers.append(nn.ReLU())
            in_dim = hidden_dim
        self.layers = nn.Sequential(*layers)
        self.norm = nn.LayerNorm(hidden_dim)

    def forward(self, node_features: torch.Tensor) -> torch.Tensor:
        return self.norm(self.layers(node_features))


class FallbackProjector(nn.Module):
    def __init__(self, input_dim: int, output_dim: int) -> None:
        super().__init__()
        self.proj = nn.Linear(input_dim, output_dim)

    def forward(self, graph_tokens: torch.Tensor) -> torch.Tensor:
        return self.proj(graph_tokens)


class DummyDecoder(nn.Module):
    def __init__(self, vocab_size: int, hidden_dim: int) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.token_embedding = nn.Embedding(vocab_size, hidden_dim)
        self.context_mlp = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.lm_head = nn.Linear(hidden_dim, vocab_size)

    def forward(self, input_ids: torch.Tensor, context_tokens: torch.Tensor) -> torch.Tensor:
        token_states = self.token_embedding(input_ids)
        pooled_context = context_tokens.mean(dim=1, keepdim=True)
        conditioned = token_states + self.context_mlp(pooled_context)
        return self.lm_head(conditioned)


def _extract_tensor(value: Any) -> Optional[torch.Tensor]:
    if torch.is_tensor(value):
        return value
    if isinstance(value, (tuple, list)):
        for item in value:
            tensor = _extract_tensor(item)
            if tensor is not None:
                return tensor
    if isinstance(value, dict):
        for key in (
            "graph_tokens",
            "node_embeddings",
            "embeddings",
            "last_hidden_state",
            "x",
            "features",
        ):
            if key in value and torch.is_tensor(value[key]):
                return value[key]
        for item in value.values():
            tensor = _extract_tensor(item)
            if tensor is not None:
                return tensor
    return None


def _build_encoder(args: argparse.Namespace) -> nn.Module:
    if SchematicGraphEncoder is None:
        return FallbackGraphEncoder(args.graph_input_dim, args.encoder_hidden_dim, args.encoder_layers)

    ctor_kwargs = (
        {
            "input_dim": args.graph_input_dim,
            "hidden_dim": args.encoder_hidden_dim,
            "num_layers": args.encoder_layers,
        },
        {"hidden_dim": args.encoder_hidden_dim, "num_layers": args.encoder_layers},
        {"hidden_dim": args.encoder_hidden_dim},
        {},
    )
    for kwargs in ctor_kwargs:
        try:
            return SchematicGraphEncoder(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    return FallbackGraphEncoder(args.graph_input_dim, args.encoder_hidden_dim, args.encoder_layers)


def _build_projector(args: argparse.Namespace) -> nn.Module:
    if GraphToQwenProjector is None:
        return FallbackProjector(args.encoder_hidden_dim, args.decoder_hidden_dim)

    ctor_kwargs = (
        {"input_dim": args.encoder_hidden_dim, "output_dim": args.decoder_hidden_dim},
        {"in_dim": args.encoder_hidden_dim, "out_dim": args.decoder_hidden_dim},
        {"hidden_dim": args.encoder_hidden_dim, "output_dim": args.decoder_hidden_dim},
        {},
    )
    for kwargs in ctor_kwargs:
        try:
            return GraphToQwenProjector(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    return FallbackProjector(args.encoder_hidden_dim, args.decoder_hidden_dim)


def _forward_encoder(encoder: nn.Module, node_features: torch.Tensor) -> torch.Tensor:
    attempts: Sequence[Any] = (
        node_features,
        {"node_features": node_features},
        {"x": node_features},
    )
    for payload in attempts:
        try:
            output = encoder(payload)  # type: ignore[arg-type]
        except Exception:
            continue
        tensor = _extract_tensor(output)
        if tensor is not None:
            if tensor.dim() == 2:
                tensor = tensor.unsqueeze(1)
            return tensor
    raise RuntimeError("Unable to run encoder forward with synthetic node feature payloads.")


def _forward_projector(projector: nn.Module, graph_tokens: torch.Tensor) -> torch.Tensor:
    attempts: Sequence[Any] = (
        graph_tokens,
        {"graph_tokens": graph_tokens},
        {"x": graph_tokens},
    )
    for payload in attempts:
        try:
            output = projector(payload)  # type: ignore[arg-type]
        except Exception:
            continue
        tensor = _extract_tensor(output)
        if tensor is not None:
            if tensor.dim() == 2:
                tensor = tensor.unsqueeze(1)
            return tensor
    raise RuntimeError("Unable to run projector forward with synthetic graph token payloads.")


def _build_vocab() -> Tuple[Dict[str, int], Dict[int, str]]:
    special = ["<pad>", "<bos>", "<eos>", "<unk>"]
    charset = set('{}[]":,._- abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
    tokens = special + sorted(charset)
    stoi = {token: idx for idx, token in enumerate(tokens)}
    itos = {idx: token for token, idx in stoi.items()}
    return stoi, itos


def _encode_json_text(text: str, max_len: int, stoi: Dict[str, int]) -> List[int]:
    pad_id = stoi["<pad>"]
    bos_id = stoi["<bos>"]
    eos_id = stoi["<eos>"]
    unk_id = stoi["<unk>"]

    token_ids = [bos_id]
    for ch in text[: max_len - 2]:
        token_ids.append(stoi.get(ch, unk_id))
    token_ids.append(eos_id)

    if len(token_ids) < max_len:
        token_ids.extend([pad_id] * (max_len - len(token_ids)))
    return token_ids[:max_len]


def _sample_json_like_target(rng: random.Random) -> str:
    relations = ["connected_to", "adjacent_to", "crosses", "drives", "references"]
    summaries = [
        "window_clean",
        "missing_label",
        "junction_dense",
        "candidate_crossing",
        "decoupler_far",
    ]
    payload = {
        "task": "summary",
        "window_id": rng.randint(0, 999),
        "summary": rng.choice(summaries),
        "relation": {
            "src_node": rng.randint(1, 40),
            "dst_node": rng.randint(1, 40),
            "type": rng.choice(relations),
        },
        "valid": bool(rng.randint(0, 1)),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _make_synthetic_batch(
    args: argparse.Namespace,
    stoi: Dict[str, int],
    rng: random.Random,
    device: torch.device,
) -> Dict[str, Any]:
    node_features = torch.randn(
        args.batch_size,
        args.num_nodes,
        args.graph_input_dim,
        device=device,
    )
    texts = [_sample_json_like_target(rng) for _ in range(args.batch_size)]
    target_tokens = torch.tensor(
        [_encode_json_text(text, args.max_seq_len, stoi) for text in texts],
        dtype=torch.long,
        device=device,
    )
    return {
        "node_features": node_features,
        "target_tokens": target_tokens,
        "texts": texts,
    }


def _resolve_device(device_arg: str) -> torch.device:
    if device_arg == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device_arg)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage-1 synthetic training scaffold.")
    parser.add_argument("--steps", type=int, default=20, help="Number of training steps.")
    parser.add_argument("--batch-size", type=int, default=8, help="Synthetic batch size.")
    parser.add_argument("--num-nodes", type=int, default=24, help="Nodes per synthetic graph.")
    parser.add_argument("--graph-input-dim", type=int, default=32, help="Input node feature dim.")
    parser.add_argument("--encoder-hidden-dim", type=int, default=384, help="Encoder hidden dim.")
    parser.add_argument("--encoder-layers", type=int, default=3, help="Fallback encoder depth.")
    parser.add_argument("--decoder-hidden-dim", type=int, default=1024, help="Dummy decoder hidden dim.")
    parser.add_argument("--max-seq-len", type=int, default=96, help="Synthetic token sequence length.")
    parser.add_argument("--lr", type=float, default=3e-4, help="Learning rate.")
    parser.add_argument("--seed", type=int, default=7, help="Random seed.")
    parser.add_argument("--device", type=str, default="auto", help="Torch device or 'auto'.")
    parser.add_argument("--log-every", type=int, default=5, help="Logging interval.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.steps < 1:
        raise ValueError("--steps must be at least 1.")

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    rng = random.Random(args.seed)

    device = _resolve_device(args.device)
    stoi, _ = _build_vocab()
    pad_id = stoi["<pad>"]
    vocab_size = len(stoi)

    encoder = _build_encoder(args).to(device)
    projector = _build_projector(args).to(device)
    decoder = DummyDecoder(vocab_size=vocab_size, hidden_dim=args.decoder_hidden_dim).to(device)

    for param in decoder.parameters():
        param.requires_grad = False

    warmup_batch = _make_synthetic_batch(args, stoi, rng, device)
    with torch.no_grad():
        encoded = _forward_encoder(encoder, warmup_batch["node_features"])
        projected = _forward_projector(projector, encoded)
    context_adapter = nn.Linear(projected.size(-1), args.decoder_hidden_dim).to(device)

    trainable_params = [
        *[p for p in encoder.parameters() if p.requires_grad],
        *[p for p in projector.parameters() if p.requires_grad],
        *[p for p in context_adapter.parameters() if p.requires_grad],
    ]
    if not trainable_params:
        raise RuntimeError("No trainable parameters found for stage-1 scaffold.")
    optimizer = torch.optim.Adam(trainable_params, lr=args.lr)

    running_loss = 0.0
    for step in range(1, args.steps + 1):
        batch = _make_synthetic_batch(args, stoi, rng, device)
        encoded = _forward_encoder(encoder, batch["node_features"])
        projected = _forward_projector(projector, encoded)
        conditioned_context = context_adapter(projected)

        input_ids = batch["target_tokens"][:, :-1]
        labels = batch["target_tokens"][:, 1:]
        logits = decoder(input_ids=input_ids, context_tokens=conditioned_context)

        loss = F.cross_entropy(
            logits.reshape(-1, vocab_size),
            labels.reshape(-1),
            ignore_index=pad_id,
        )
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()

        running_loss += float(loss.item())
        if step == 1 or step % max(1, args.log_every) == 0 or step == args.steps:
            avg_loss = running_loss / step
            print(
                f"[stage1] step={step:04d}/{args.steps:04d} "
                f"loss={loss.item():.4f} avg_loss={avg_loss:.4f}"
            )

    print(
        json.dumps(
            {
                "status": "ok",
                "stage": "stage1",
                "steps": args.steps,
                "final_loss": round(loss.item(), 6),
                "avg_loss": round(running_loss / max(args.steps, 1), 6),
                "decoder_frozen": True,
                "device": str(device),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
