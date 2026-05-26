"""Train a supervised oracle selector over mined cleanup candidates."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from src.rl.policy import CandidateActorCritic


def _resolve_device(device: str) -> torch.device:
    if device == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    return torch.device(device)


def _maybe_init_wandb(args: argparse.Namespace) -> Any | None:
    if not args.wandb:
        return None
    try:
        import wandb  # type: ignore
    except ImportError as exc:
        raise RuntimeError("wandb logging requested but wandb is not installed") from exc

    return wandb.init(
        project=args.wandb_project,
        entity=args.wandb_entity or None,
        name=args.wandb_run_name or None,
        tags=args.wandb_tags.split(",") if args.wandb_tags else None,
        config=vars(args),
        mode=args.wandb_mode,
    )


def _build_sample_weights(top2_margin: torch.Tensor, oracle_reward: torch.Tensor, *, min_weight: float) -> torch.Tensor:
    margin_term = torch.clamp(top2_margin, min=0.0)
    reward_term = torch.clamp(oracle_reward, min=0.0)
    weights = 1.0 + margin_term + 0.25 * reward_term
    return torch.clamp(weights, min=float(min_weight))


def _make_loader(
    payload: dict[str, Any],
    *,
    batch_size: int,
    shuffle: bool,
    min_sample_weight: float,
) -> DataLoader[tuple[torch.Tensor, ...]]:
    dataset = TensorDataset(
        payload["state_vec"].float(),
        payload["candidate_features"].float(),
        payload["candidate_mask"].bool(),
        payload["oracle_action"].long(),
        _build_sample_weights(payload["top2_margin"].float(), payload["oracle_reward"].float(), min_weight=min_sample_weight),
        payload["oracle_reward"].float(),
    )
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


def _run_epoch(
    model: CandidateActorCritic,
    loader: DataLoader[tuple[torch.Tensor, ...]],
    *,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None,
    value_coef: float,
) -> dict[str, float]:
    is_train = optimizer is not None
    model.train(is_train)
    total_loss = 0.0
    total_cls_loss = 0.0
    total_value_loss = 0.0
    total_correct = 0
    total_samples = 0
    total_weight = 0.0

    for state_vec, candidate_features, candidate_mask, oracle_action, sample_weight, oracle_reward in loader:
        state_vec = state_vec.to(device)
        candidate_features = candidate_features.to(device)
        candidate_mask = candidate_mask.to(device)
        oracle_action = oracle_action.to(device)
        sample_weight = sample_weight.to(device)
        oracle_reward = oracle_reward.to(device)

        with torch.set_grad_enabled(is_train):
            output = model(state_vec, candidate_features, candidate_mask)
            cls_loss_vec = nn.functional.cross_entropy(output.logits, oracle_action, reduction="none")
            weighted_cls_loss = (cls_loss_vec * sample_weight).sum() / sample_weight.sum()
            value_loss = nn.functional.mse_loss(output.value, oracle_reward)
            loss = weighted_cls_loss + value_coef * value_loss

            if is_train:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()

        preds = output.logits.argmax(dim=-1)
        total_correct += int((preds == oracle_action).sum().item())
        total_samples += int(state_vec.shape[0])
        total_weight += float(sample_weight.sum().item())
        total_loss += float(loss.item()) * int(state_vec.shape[0])
        total_cls_loss += float(weighted_cls_loss.item()) * int(state_vec.shape[0])
        total_value_loss += float(value_loss.item()) * int(state_vec.shape[0])

    denom = max(total_samples, 1)
    return {
        "loss": total_loss / denom,
        "cls_loss": total_cls_loss / denom,
        "value_loss": total_value_loss / denom,
        "accuracy": float(total_correct / denom),
        "samples": float(total_samples),
        "sample_weight_mean": float(total_weight / denom) if denom > 0 else 0.0,
    }


def train(args: argparse.Namespace) -> int:
    device = _resolve_device(args.device)
    wandb_run = _maybe_init_wandb(args)
    train_payload = torch.load(args.train_dataset, map_location="cpu")
    val_payload = torch.load(args.val_dataset, map_location="cpu") if args.val_dataset else None

    model = CandidateActorCritic(
        state_dim=int(train_payload["state_dim"]),
        candidate_dim=int(train_payload["candidate_dim"]),
        hidden_dim=args.hidden_dim,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)

    train_loader = _make_loader(
        train_payload,
        batch_size=args.batch_size,
        shuffle=True,
        min_sample_weight=args.min_sample_weight,
    )
    val_loader = (
        _make_loader(
            val_payload,
            batch_size=args.batch_size,
            shuffle=False,
            min_sample_weight=args.min_sample_weight,
        )
        if val_payload is not None
        else None
    )

    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    best_metric = -math.inf
    best_path = out_dir / "policy_best.pt"

    for epoch in range(1, args.epochs + 1):
        train_metrics = _run_epoch(model, train_loader, device=device, optimizer=optimizer, value_coef=args.value_coef)
        record: dict[str, Any] = {"epoch": epoch, **{f"train_{k}": v for k, v in train_metrics.items()}}

        if val_loader is not None:
            val_metrics = _run_epoch(model, val_loader, device=device, optimizer=None, value_coef=args.value_coef)
            record.update({f"val_{k}": v for k, v in val_metrics.items()})
            monitor = float(val_metrics["accuracy"])
        else:
            monitor = float(train_metrics["accuracy"])

        history.append(record)
        if wandb_run is not None:
            wandb_run.log(record, step=epoch)
        print(json.dumps(record))

        if monitor >= best_metric:
            best_metric = monitor
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "epoch": epoch,
                    "state_dim": int(train_payload["state_dim"]),
                    "candidate_dim": int(train_payload["candidate_dim"]),
                    "args": vars(args),
                },
                best_path,
            )

    final_path = out_dir / "policy_final.pt"
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "epoch": args.epochs,
            "state_dim": int(train_payload["state_dim"]),
            "candidate_dim": int(train_payload["candidate_dim"]),
            "args": vars(args),
        },
        final_path,
    )
    metrics_path = out_dir / "metrics.jsonl"
    metrics_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in history), encoding="utf-8")
    print(json.dumps({"saved_model": str(final_path), "best_model": str(best_path), "metrics": str(metrics_path)}))

    if wandb_run is not None:
        wandb_run.summary["best_monitor"] = best_metric
        wandb_run.summary["saved_model"] = str(final_path)
        wandb_run.summary["best_model"] = str(best_path)
        wandb_run.finish()
    return 0


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train a supervised oracle selector over cleanup candidates.")
    parser.add_argument("--train-dataset", type=str, required=True)
    parser.add_argument("--val-dataset", type=str, default="")
    parser.add_argument("--output-dir", type=str, required=True)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--value-coef", type=float, default=0.1)
    parser.add_argument("--hidden-dim", type=int, default=256)
    parser.add_argument("--min-sample-weight", type=float, default=1.0)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--wandb-project", type=str, default="pcb-ft-q35-oracle-selector")
    parser.add_argument("--wandb-entity", type=str, default="")
    parser.add_argument("--wandb-run-name", type=str, default="")
    parser.add_argument("--wandb-tags", type=str, default="")
    parser.add_argument("--wandb-mode", type=str, default="online")
    return parser


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()
    return train(args)


if __name__ == "__main__":
    raise SystemExit(main())
