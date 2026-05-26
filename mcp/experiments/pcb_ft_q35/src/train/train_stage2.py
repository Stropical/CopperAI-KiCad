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
            layers.append(nn.GELU())
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


def _build_vocab() -> Dict[str, int]:
    special = ["<pad>", "<bos>", "<eos>", "<unk>"]
    charset = set('{}[]":,._- abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
    tokens = special + sorted(charset)
    return {token: idx for idx, token in enumerate(tokens)}


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


def _build_edit_json_text(rank_target: int, critique_target: torch.Tensor, rng: random.Random) -> str:
    issue_names = [
        "crossing",
        "decoupler_distance",
        "alignment",
        "connector_rotation",
        "net_label_conflict",
    ]
    actions = ["move", "rotate", "label", "reroute", "annotate"]
    selected = [issue_names[i] for i, flag in enumerate(critique_target.tolist()) if flag > 0.5]
    payload = {
        "task": "edit",
        "priority_rank": int(rank_target),
        "issues": selected[:3],
        "action": rng.choice(actions),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _make_synthetic_batch(
    args: argparse.Namespace,
    stoi: Dict[str, int],
    rng: random.Random,
    device: torch.device,
) -> Dict[str, torch.Tensor]:
    node_features = torch.randn(
        args.batch_size,
        args.num_nodes,
        args.graph_input_dim,
        device=device,
    )

    pooled = node_features.mean(dim=1)
    ranking_targets = pooled[:, : args.num_ranking_classes].argmax(dim=-1)
    critique_targets = (pooled[:, : args.num_critique_labels] > 0.0).float()

    texts = [
        _build_edit_json_text(int(ranking_targets[i].item()), critique_targets[i], rng)
        for i in range(args.batch_size)
    ]
    edit_tokens = torch.tensor(
        [_encode_json_text(text, args.max_seq_len, stoi) for text in texts],
        dtype=torch.long,
        device=device,
    )

    return {
        "node_features": node_features,
        "ranking_targets": ranking_targets,
        "critique_targets": critique_targets,
        "edit_tokens": edit_tokens,
    }


def _resolve_device(device_arg: str) -> torch.device:
    if device_arg == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device_arg)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage-2 synthetic multi-task training scaffold.")
    parser.add_argument("--steps", type=int, default=24, help="Number of training steps.")
    parser.add_argument("--batch-size", type=int, default=8, help="Synthetic batch size.")
    parser.add_argument("--num-nodes", type=int, default=24, help="Nodes per synthetic graph.")
    parser.add_argument("--graph-input-dim", type=int, default=32, help="Input node feature dim.")
    parser.add_argument("--encoder-hidden-dim", type=int, default=384, help="Encoder hidden dim.")
    parser.add_argument("--encoder-layers", type=int, default=3, help="Fallback encoder depth.")
    parser.add_argument("--decoder-hidden-dim", type=int, default=1024, help="Dummy decoder hidden dim.")
    parser.add_argument("--max-seq-len", type=int, default=96, help="Synthetic token sequence length.")
    parser.add_argument("--num-ranking-classes", type=int, default=3, help="Ranking class count.")
    parser.add_argument("--num-critique-labels", type=int, default=5, help="Critique label count.")
    parser.add_argument("--ranking-weight", type=float, default=0.7, help="Ranking loss weight.")
    parser.add_argument("--critique-weight", type=float, default=0.2, help="Critique loss weight.")
    parser.add_argument("--edit-weight", type=float, default=0.1, help="Edit generation loss weight.")
    parser.add_argument("--lr", type=float, default=3e-4, help="Learning rate.")
    parser.add_argument("--seed", type=int, default=11, help="Random seed.")
    parser.add_argument("--device", type=str, default="auto", help="Torch device or 'auto'.")
    parser.add_argument("--log-every", type=int, default=4, help="Logging interval.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.steps < 1:
        raise ValueError("--steps must be at least 1.")

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    rng = random.Random(args.seed)

    if args.ranking_weight < 0 or args.critique_weight < 0 or args.edit_weight < 0:
        raise ValueError("All task weights must be non-negative.")
    if (args.ranking_weight + args.critique_weight + args.edit_weight) <= 0:
        raise ValueError("At least one task weight must be positive.")

    device = _resolve_device(args.device)
    stoi = _build_vocab()
    vocab_size = len(stoi)
    pad_id = stoi["<pad>"]

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

    ranking_head = nn.Linear(args.decoder_hidden_dim, args.num_ranking_classes).to(device)
    critique_head = nn.Linear(args.decoder_hidden_dim, args.num_critique_labels).to(device)

    trainable_params = [
        *[p for p in encoder.parameters() if p.requires_grad],
        *[p for p in projector.parameters() if p.requires_grad],
        *[p for p in context_adapter.parameters() if p.requires_grad],
        *[p for p in ranking_head.parameters() if p.requires_grad],
        *[p for p in critique_head.parameters() if p.requires_grad],
    ]
    if not trainable_params:
        raise RuntimeError("No trainable parameters found for stage-2 scaffold.")

    optimizer = torch.optim.Adam(trainable_params, lr=args.lr)

    running_total = 0.0
    for step in range(1, args.steps + 1):
        batch = _make_synthetic_batch(args, stoi, rng, device)
        encoded = _forward_encoder(encoder, batch["node_features"])
        projected = _forward_projector(projector, encoded)
        conditioned = context_adapter(projected)
        pooled_context = conditioned.mean(dim=1)

        ranking_logits = ranking_head(pooled_context)
        critique_logits = critique_head(pooled_context)

        edit_input_ids = batch["edit_tokens"][:, :-1]
        edit_labels = batch["edit_tokens"][:, 1:]
        edit_logits = decoder(input_ids=edit_input_ids, context_tokens=conditioned)

        ranking_loss = F.cross_entropy(ranking_logits, batch["ranking_targets"])
        critique_loss = F.binary_cross_entropy_with_logits(critique_logits, batch["critique_targets"])
        edit_loss = F.cross_entropy(
            edit_logits.reshape(-1, vocab_size),
            edit_labels.reshape(-1),
            ignore_index=pad_id,
        )
        total_loss = (
            args.ranking_weight * ranking_loss
            + args.critique_weight * critique_loss
            + args.edit_weight * edit_loss
        )

        optimizer.zero_grad(set_to_none=True)
        total_loss.backward()
        optimizer.step()

        running_total += float(total_loss.item())
        if step == 1 or step % max(1, args.log_every) == 0 or step == args.steps:
            rank_acc = (ranking_logits.argmax(dim=-1) == batch["ranking_targets"]).float().mean().item()
            critique_pred = (torch.sigmoid(critique_logits) > 0.5).float()
            critique_match = (critique_pred == batch["critique_targets"]).float().mean().item()
            avg_total = running_total / step
            print(
                f"[stage2] step={step:04d}/{args.steps:04d} "
                f"total={total_loss.item():.4f} avg_total={avg_total:.4f} "
                f"rank_loss={ranking_loss.item():.4f} critique_loss={critique_loss.item():.4f} "
                f"edit_loss={edit_loss.item():.4f} rank_acc={rank_acc:.3f} critique_match={critique_match:.3f}"
            )

    print(
        json.dumps(
            {
                "status": "ok",
                "stage": "stage2",
                "steps": args.steps,
                "weights": {
                    "ranking": args.ranking_weight,
                    "critique": args.critique_weight,
                    "edit": args.edit_weight,
                },
                "final_total_loss": round(total_loss.item(), 6),
                "avg_total_loss": round(running_total / max(args.steps, 1), 6),
                "decoder_frozen": True,
                "device": str(device),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
