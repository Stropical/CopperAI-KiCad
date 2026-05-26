"""Train a graph-conditioned decoder model on Part A geometry tasks."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import torch
import torch.distributed as dist
from torch import nn
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import DataLoader, Dataset
from torch.utils.data.distributed import DistributedSampler

if __package__ in {None, ""}:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from src.models.qwen35_graph_model import Qwen35GraphModel  # noqa: E402


DEFAULT_TASKS = ("tag_issues", "suggest_repair", "score_block", "rank_candidates")


@dataclass(slots=True)
class GeometrySample:
    sample_id: str
    task: str
    prompt: str
    target_text: str
    graph_tensor: dict[str, Any]


class GeometryTaskDataset(Dataset[GeometrySample]):
    """JSONL dataset for geometry-task supervision."""

    def __init__(
        self,
        jsonl_path: str | Path,
        split: str = "train",
        include_tasks: Optional[Iterable[str]] = None,
        max_records: Optional[int] = None,
    ) -> None:
        self.jsonl_path = Path(jsonl_path)
        self.split = split
        self.include_tasks = set(include_tasks or DEFAULT_TASKS)
        self.max_records = max_records
        self.samples = self._load_samples()

    def _load_jsonl(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with self.jsonl_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return rows

    @staticmethod
    def _json_compact(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _pick_graph_tensor(record: dict[str, Any]) -> Optional[dict[str, Any]]:
        task = str(record.get("task", ""))
        data = record.get("input", {}) if isinstance(record.get("input", {}), dict) else {}

        if task == "rank_candidates":
            candidates = data.get("candidates", [])
            if isinstance(candidates, list) and candidates:
                first = candidates[0]
                if isinstance(first, dict):
                    graph = first.get("graph_tensor")
                    if isinstance(graph, dict):
                        return graph
            return None

        graph = data.get("graph_tensor")
        if isinstance(graph, dict):
            return graph
        return None

    @staticmethod
    def _prompt_text(record: dict[str, Any]) -> str:
        task = str(record.get("task", ""))
        data = record.get("input", {}) if isinstance(record.get("input", {}), dict) else {}

        if task == "rank_candidates":
            candidates = data.get("candidates", []) if isinstance(data.get("candidates", []), list) else []
            priors = []
            for idx, cand in enumerate(candidates):
                if not isinstance(cand, dict):
                    continue
                issues = cand.get("issue_priors", []) if isinstance(cand.get("issue_priors", []), list) else []
                priors.append({"idx": idx, "issues": issues})
            payload = {
                "task": task,
                "candidate_count": len(candidates),
                "candidate_issue_priors": priors,
            }
            return GeometryTaskDataset._json_compact(payload)

        payload = {
            "task": task,
            "input": data,
        }
        return GeometryTaskDataset._json_compact(payload)

    def _load_samples(self) -> list[GeometrySample]:
        rows = self._load_jsonl()
        out: list[GeometrySample] = []

        for row in rows:
            if self.max_records is not None and len(out) >= self.max_records:
                break
            if not isinstance(row, dict):
                continue
            if str(row.get("split", "")) != self.split:
                continue

            task = str(row.get("task", ""))
            if task not in self.include_tasks:
                continue

            graph_tensor = self._pick_graph_tensor(row)
            if graph_tensor is None:
                continue

            target = row.get("target", {})
            sample_id = str(row.get("sample_id", f"{task}_{len(out)}"))
            out.append(
                GeometrySample(
                    sample_id=sample_id,
                    task=task,
                    prompt=self._prompt_text(row),
                    target_text=self._json_compact(target),
                    graph_tensor=graph_tensor,
                )
            )
        return out

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> GeometrySample:
        return self.samples[idx]


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


class GeometryCollator:
    """Collate geometry samples into model-ready text + graph batch."""

    def __init__(
        self,
        tokenizer: Any,
        max_text_len: int = 1024,
        default_edge_feature_dim: int = 1,
    ) -> None:
        self.tokenizer = tokenizer
        self.max_text_len = max_text_len
        self.default_edge_feature_dim = default_edge_feature_dim

    def _encode_text(self, prompt: str, target: str) -> tuple[list[int], list[int]]:
        prompt_prefix = f"<task>\n{prompt}\n<answer>\n"
        prompt_ids = self.tokenizer.encode(prompt_prefix, add_special_tokens=False)
        target_ids = self.tokenizer.encode(target, add_special_tokens=False)

        bos = getattr(self.tokenizer, "bos_token_id", None)
        eos = getattr(self.tokenizer, "eos_token_id", None)

        token_ids: list[int] = []
        labels: list[int] = []

        if isinstance(bos, int) and bos >= 0:
            token_ids.append(bos)
            labels.append(-100)

        token_ids.extend(prompt_ids)
        labels.extend([-100] * len(prompt_ids))

        token_ids.extend(target_ids)
        labels.extend(target_ids)

        if isinstance(eos, int) and eos >= 0:
            token_ids.append(eos)
            labels.append(eos)

        if len(token_ids) > self.max_text_len:
            token_ids = token_ids[: self.max_text_len]
            labels = labels[: self.max_text_len]

        return token_ids, labels

    def _pack_graph(self, graph_tensors: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        node_features_list: list[torch.Tensor] = []
        edge_index_list: list[torch.Tensor] = []
        edge_features_list: list[torch.Tensor] = []

        max_nodes = 0
        max_edges = 0
        node_dim = 0
        edge_dim = self.default_edge_feature_dim

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
                # Allow missing features by zero-filling.
                ef = torch.zeros(ei.shape[1], max(1, ef.shape[1] if ef.ndim == 2 else edge_dim), dtype=torch.float32)

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

            n = nf.shape[0]
            e = ei.shape[1]

            node_features[b, :n, : nf.shape[1]] = nf
            node_mask[b, :n] = True
            node_type_ids[b, :n] = nf[:, 0].long().clamp_min(0)

            edge_index[b, :, :e] = ei
            edge_features[b, :e, : ef.shape[1]] = ef
            edge_type_ids[b, :e] = ef[:, 0].long().clamp_min(0)

        return {
            "node_features": node_features,
            "edge_index": edge_index,
            "edge_features": edge_features,
            "node_type_ids": node_type_ids,
            "edge_type_ids": edge_type_ids,
            "node_mask": node_mask,
        }

    def __call__(self, batch: list[GeometrySample]) -> dict[str, Any]:
        token_sequences: list[list[int]] = []
        label_sequences: list[list[int]] = []
        graph_tensors = [sample.graph_tensor for sample in batch]

        for sample in batch:
            ids, labels = self._encode_text(sample.prompt, sample.target_text)
            token_sequences.append(ids)
            label_sequences.append(labels)

        pad_id = getattr(self.tokenizer, "pad_token_id", None)
        if not isinstance(pad_id, int) or pad_id < 0:
            eos_id = getattr(self.tokenizer, "eos_token_id", None)
            pad_id = int(eos_id) if isinstance(eos_id, int) and eos_id >= 0 else 0

        max_len = max(len(seq) for seq in token_sequences)
        input_ids = torch.full((len(batch), max_len), pad_id, dtype=torch.long)
        labels = torch.full((len(batch), max_len), -100, dtype=torch.long)
        attention_mask = torch.zeros((len(batch), max_len), dtype=torch.long)

        for i, (ids, lbls) in enumerate(zip(token_sequences, label_sequences)):
            n = len(ids)
            input_ids[i, :n] = torch.tensor(ids, dtype=torch.long)
            labels[i, :n] = torch.tensor(lbls, dtype=torch.long)
            attention_mask[i, :n] = 1

        graph_batch = self._pack_graph(graph_tensors)

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
            "graph_batch": graph_batch,
            "sample_ids": [sample.sample_id for sample in batch],
            "tasks": [sample.task for sample in batch],
        }


def _load_tokenizer(model: Qwen35GraphModel, base_model: Optional[str] = None) -> Any:
    if model.tokenizer is not None:
        tok = model.tokenizer
        if getattr(tok, "pad_token_id", None) is None and getattr(tok, "eos_token_id", None) is not None:
            tok.pad_token = tok.eos_token
        return tok

    try:
        from transformers import AutoTokenizer
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("transformers is required to load tokenizer.") from exc

    if base_model is None:
        raise ValueError("base_model must be provided when tokenizer is not embedded in model.")

    tok = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tok.pad_token_id is None and tok.eos_token_id is not None:
        tok.pad_token = tok.eos_token
    return tok


def _move_graph_batch_to_device(graph_batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {k: v.to(device) for k, v in graph_batch.items()}


def _save_periodic_checkpoint(
    model: Qwen35GraphModel,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler._LRScheduler,
    path: Path,
    global_step: int,
    best_val: float,
) -> None:
    state = {
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "global_step": global_step,
        "best_val": best_val,
    }
    torch.save(state, path)


def _infer_graph_feature_dims(dataset: GeometryTaskDataset) -> tuple[int, int]:
    if len(dataset) == 0:
        raise RuntimeError("Cannot infer graph feature dims from empty dataset.")
    graph = dataset[0].graph_tensor
    node_features = graph.get("node_features", [])
    edge_features = graph.get("edge_features", [])
    if not isinstance(node_features, list) or not node_features or not isinstance(node_features[0], list):
        raise RuntimeError("Dataset sample is missing valid node_features for graph encoder init.")
    node_dim = int(len(node_features[0]))
    edge_dim = 1
    if isinstance(edge_features, list) and edge_features:
        first = edge_features[0]
        if isinstance(first, list) and first:
            edge_dim = int(len(first))
    return node_dim, edge_dim


def _extract_loss(output: Any, logits: Optional[torch.Tensor], labels: Optional[torch.Tensor]) -> torch.Tensor:
    if isinstance(output, dict) and isinstance(output.get("loss"), torch.Tensor):
        return output["loss"]
    loss_attr = getattr(output, "loss", None)
    if isinstance(loss_attr, torch.Tensor):
        return loss_attr

    if logits is None:
        if isinstance(output, dict) and isinstance(output.get("logits"), torch.Tensor):
            logits = output["logits"]
        else:
            logits = getattr(output, "logits", None)
    if not isinstance(logits, torch.Tensor) or labels is None:
        raise RuntimeError("Model output did not include loss/logits required for training.")

    shift_logits = logits[:, :-1, :].contiguous()
    shift_labels = labels[:, 1:].contiguous()
    return nn.functional.cross_entropy(
        shift_logits.view(-1, shift_logits.size(-1)),
        shift_labels.view(-1),
        ignore_index=-100,
    )


def _linear_warmup_decay(step: int, total_steps: int, warmup_steps: int) -> float:
    if total_steps <= 0:
        return 1.0
    if warmup_steps > 0 and step < warmup_steps:
        return float(step + 1) / float(max(1, warmup_steps))
    remain_steps = max(1, total_steps - warmup_steps)
    progress = float(step - warmup_steps) / float(remain_steps)
    return max(0.0, 1.0 - progress)


def _set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _is_distributed() -> bool:
    """True if RANK and WORLD_SIZE are set (e.g. by torchrun)."""
    return (
        os.environ.get("RANK") is not None
        and os.environ.get("WORLD_SIZE") is not None
    )


def _init_distributed() -> tuple[int, int, int]:
    """Init process group and return (rank, world_size, local_rank)."""
    rank = int(os.environ["RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    local_rank = int(os.environ.get("LOCAL_RANK", os.environ.get("OMPI_COMM_WORLD_LOCAL_RANK", "0")))
    backend = "nccl" if torch.cuda.is_available() else "gloo"
    dist.init_process_group(backend=backend)
    return rank, world_size, local_rank


def _resolve_device(device: str, local_rank: Optional[int] = None) -> torch.device:
    """In DDP mode, local_rank is set and overrides device; otherwise use device string."""
    if local_rank is not None:
        if torch.cuda.is_available():
            return torch.device("cuda", local_rank)
        return torch.device("cpu")
    if device == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    return torch.device(device)


def _build_param_groups(model: Qwen35GraphModel, lr_graph: float, lr_decoder: float, weight_decay: float) -> list[dict[str, Any]]:
    groups = model.trainable_parameter_groups()
    param_groups: list[dict[str, Any]] = []

    graph_params = groups["graph_encoder"] + groups["projector"]
    if graph_params:
        param_groups.append({"params": graph_params, "lr": lr_graph, "weight_decay": weight_decay})

    if groups["decoder"]:
        param_groups.append({"params": groups["decoder"], "lr": lr_decoder, "weight_decay": weight_decay})

    return param_groups


def _evaluate(
    model: Qwen35GraphModel,
    dataloader: DataLoader,
    device: torch.device,
    max_eval_batches: int,
) -> float:
    model.eval()
    losses: list[float] = []
    with torch.no_grad():
        for batch_idx, batch in enumerate(dataloader):
            if max_eval_batches > 0 and batch_idx >= max_eval_batches:
                break

            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)
            graph_batch = _move_graph_batch_to_device(batch["graph_batch"], device)

            output = model(
                graph_batch=graph_batch,
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            loss = _extract_loss(output, None, labels)
            losses.append(float(loss.detach().cpu().item()))

    model.train()
    if not losses:
        return float("nan")
    return sum(losses) / len(losses)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a graph-conditioned decoder with schematic graph inputs.")
    parser.add_argument("--model", type=str, required=True, help="Base HF model id or saved graph model dir")
    parser.add_argument("--dataset", type=str, required=True, help="Geometry task JSONL (prefer split-annotated)")
    parser.add_argument("--output-dir", type=str, required=True)

    parser.add_argument("--train-split", type=str, default="train")
    parser.add_argument("--val-split", type=str, default="val")
    parser.add_argument("--include-tasks", type=str, default=",".join(DEFAULT_TASKS))
    parser.add_argument("--max-train-records", type=int, default=None)
    parser.add_argument("--max-val-records", type=int, default=None)

    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=1)
    parser.add_argument("--max-text-len", type=int, default=1024)

    parser.add_argument("--lr-graph", type=float, default=2e-4)
    parser.add_argument("--lr-decoder", type=float, default=1e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)

    parser.add_argument("--freeze-decoder", action="store_true")
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--seed", type=int, default=13)

    # Unsloth / QLoRA (optional)
    parser.add_argument("--use-unsloth", action="store_true", help="Load decoder via Unsloth (enables LoRA)")
    parser.add_argument("--load-in-4bit", action="store_true", help="4-bit QLoRA (Unsloth: not recommended for Qwen3.5)")
    parser.add_argument("--load-in-16bit", action="store_true", help="16-bit LoRA (recommended for Qwen3.5)")
    parser.add_argument("--max-seq-length", type=int, default=2048, help="Max sequence length for Unsloth")
    parser.add_argument("--lora-r", type=int, default=16, help="LoRA rank")
    parser.add_argument("--lora-alpha", type=int, default=16, help="LoRA alpha")

    parser.add_argument("--eval-every", type=int, default=100)
    parser.add_argument("--max-eval-batches", type=int, default=50)
    parser.add_argument("--save-every", type=int, default=200)
    parser.add_argument("--resume-checkpoint", type=str, default=None)
    parser.add_argument("--save-interval-sec", type=int, default=0, help="Dump a single checkpoint file every N seconds.")
    parser.add_argument(
        "--max-train-steps",
        type=int,
        default=None,
        help="Stop after this many optimizer steps and save to final/ (for quick smoke checkpoints).",
    )
    # DDP: normally set by torchrun (LOCAL_RANK env). Optional for torch.distributed.launch compatibility.
    parser.add_argument("--local-rank", type=int, default=None, help="Local rank for DDP (prefer env LOCAL_RANK).")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _set_seed(args.seed)

    rank = 0
    world_size = 1
    local_rank: Optional[int] = None
    if _is_distributed():
        rank, world_size, local_rank = _init_distributed()
        if args.local_rank is None:
            args.local_rank = local_rank

    device = _resolve_device(args.device, local_rank)
    is_rank0 = rank == 0

    include_tasks = [t.strip() for t in args.include_tasks.split(",") if t.strip()]

    train_dataset = GeometryTaskDataset(
        jsonl_path=args.dataset,
        split=args.train_split,
        include_tasks=include_tasks,
        max_records=args.max_train_records,
    )
    val_dataset = GeometryTaskDataset(
        jsonl_path=args.dataset,
        split=args.val_split,
        include_tasks=include_tasks,
        max_records=args.max_val_records,
    )

    if len(train_dataset) == 0:
        raise RuntimeError("No training samples found. Check dataset split/task filters.")

    node_feature_dim, edge_feature_dim = _infer_graph_feature_dims(train_dataset)

    model_source = Path(args.resume_checkpoint) if args.resume_checkpoint else Path(args.model)
    config_path = model_source / "graph_model_config.json"

    if args.resume_checkpoint:
        model = Qwen35GraphModel.load_pretrained(args.resume_checkpoint)
        base_model_for_tokenizer = args.model
    elif config_path.exists():
        model = Qwen35GraphModel.load_pretrained(args.model)
        base_model_for_tokenizer = None
    else:
        model = Qwen35GraphModel.from_pretrained(
            args.model,
            node_feature_dim=node_feature_dim,
            edge_feature_dim=edge_feature_dim,
            use_unsloth=args.use_unsloth,
            load_in_4bit=args.load_in_4bit,
            load_in_16bit=args.load_in_16bit,
            max_seq_length=args.max_seq_length,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
        )
        base_model_for_tokenizer = args.model

    print("Model loaded, freezing decoder and moving to device...", flush=True)
    if args.freeze_decoder:
        model.freeze_decoder()

    model = model.to(device)
    if _is_distributed():
        model = DistributedDataParallel(
            model,
            device_ids=[device] if device.type == "cuda" else None,
        )
    model_to_save = getattr(model, "module", model)

    print("Loading tokenizer...", flush=True)
    tokenizer = _load_tokenizer(model_to_save, base_model=base_model_for_tokenizer)
    print("Tokenizer ready.", flush=True)

    collator = GeometryCollator(tokenizer=tokenizer, max_text_len=args.max_text_len)

    train_sampler: Optional[DistributedSampler] = None
    if _is_distributed():
        train_sampler = DistributedSampler(
            train_dataset,
            num_replicas=world_size,
            rank=rank,
            shuffle=True,
        )
    # num_workers=0 avoids fork/hang issues on macOS and ensures predictable behavior
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=(train_sampler is None),
        sampler=train_sampler,
        collate_fn=collator,
        num_workers=0,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=collator,
        num_workers=0,
    )

    param_groups = _build_param_groups(
        model=model,
        lr_graph=args.lr_graph,
        lr_decoder=args.lr_decoder,
        weight_decay=args.weight_decay,
    )
    if not param_groups:
        raise RuntimeError("No trainable parameters configured.")

    optimizer = torch.optim.AdamW(param_groups)

    steps_per_epoch = math.ceil(len(train_loader) / max(1, args.grad_accum))
    total_steps = max(1, args.epochs * steps_per_epoch)
    warmup_steps = int(total_steps * max(0.0, args.warmup_ratio))

    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer,
        lr_lambda=lambda step: _linear_warmup_decay(step, total_steps, warmup_steps),
    )

    output_dir = Path(args.output_dir)
    if is_rank0:
        output_dir.mkdir(parents=True, exist_ok=True)
    if _is_distributed():
        dist.barrier()
    metrics_path = output_dir / "train_metrics.jsonl"
    periodic_checkpoint_path = output_dir / "checkpoint_periodic.pt"
    last_periodic_save = time.time()

    global_step = 0
    best_val = float("inf")

    # Resume optimizer/scheduler state if available.
    if args.resume_checkpoint:
        state_path = Path(args.resume_checkpoint) / "trainer_state.pt"
        if state_path.exists():
            state = torch.load(state_path, map_location=device)
            if isinstance(state, dict):
                if "optimizer" in state:
                    optimizer.load_state_dict(state["optimizer"])
                if "scheduler" in state:
                    scheduler.load_state_dict(state["scheduler"])
                global_step = int(state.get("global_step", 0))
                best_val = float(state.get("best_val", best_val))

    model.train()
    optimizer.zero_grad(set_to_none=True)

    if is_rank0:
        print(f"Starting training: {len(train_dataset)} train samples, {len(val_dataset)} val, device={device}, world_size={world_size}", flush=True)
    for epoch in range(args.epochs):
        if train_sampler is not None:
            train_sampler.set_epoch(epoch)
        for step_in_epoch, batch in enumerate(train_loader):
            if is_rank0:
                print(f"  step {step_in_epoch + 1}/{len(train_loader)} (epoch {epoch + 1}) forward...", flush=True)
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)
            graph_batch = _move_graph_batch_to_device(batch["graph_batch"], device)

            output = model(
                graph_batch=graph_batch,
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            loss = _extract_loss(output, None, labels)
            scaled_loss = loss / max(1, args.grad_accum)
            scaled_loss.backward()

            if (step_in_epoch + 1) % max(1, args.grad_accum) == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=args.max_grad_norm)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
                global_step += 1

                metric = {
                    "step": global_step,
                    "epoch": epoch,
                    "train_loss": float(loss.detach().cpu().item()),
                    "lr_graph": float(optimizer.param_groups[0]["lr"]),
                }

                if args.eval_every > 0 and global_step % args.eval_every == 0 and len(val_dataset) > 0 and is_rank0:
                    val_loss = _evaluate(
                        model=model,
                        dataloader=val_loader,
                        device=device,
                        max_eval_batches=args.max_eval_batches,
                    )
                    metric["val_loss"] = float(val_loss)
                    if not math.isnan(val_loss) and val_loss < best_val:
                        best_val = val_loss
                        best_dir = output_dir / "best"
                        model_to_save.save_pretrained(best_dir)
                        torch.save(
                            {
                                "optimizer": optimizer.state_dict(),
                                "scheduler": scheduler.state_dict(),
                                "global_step": global_step,
                                "best_val": best_val,
                            },
                            best_dir / "trainer_state.pt",
                        )

                if is_rank0:
                    with metrics_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(metric, sort_keys=True))
                        handle.write("\n")

                if args.save_interval_sec > 0:
                    now = time.time()
                    if now - last_periodic_save >= args.save_interval_sec:
                        if _is_distributed():
                            dist.barrier()
                        if is_rank0:
                            _save_periodic_checkpoint(
                                model=model_to_save,
                                optimizer=optimizer,
                                scheduler=scheduler,
                                path=periodic_checkpoint_path,
                                global_step=global_step,
                                best_val=best_val,
                            )
                        last_periodic_save = now

                if args.save_every > 0 and global_step % args.save_every == 0:
                    if _is_distributed():
                        dist.barrier()
                    if is_rank0:
                        ckpt_dir = output_dir / f"checkpoint-{global_step}"
                        model_to_save.save_pretrained(ckpt_dir)
                        torch.save(
                            {
                                "optimizer": optimizer.state_dict(),
                                "scheduler": scheduler.state_dict(),
                                "global_step": global_step,
                                "best_val": best_val,
                            },
                            ckpt_dir / "trainer_state.pt",
                        )

    if _is_distributed():
        dist.barrier()
    if is_rank0:
        final_dir = output_dir / "final"
        model_to_save.save_pretrained(final_dir)
        torch.save(
            {
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "global_step": global_step,
                "best_val": best_val,
            },
            final_dir / "trainer_state.pt",
        )

        if args.save_interval_sec > 0:
            _save_periodic_checkpoint(
                model=model_to_save,
                optimizer=optimizer,
                scheduler=scheduler,
                path=periodic_checkpoint_path,
                global_step=global_step,
                best_val=best_val,
            )

        print(f"Training complete. global_step={global_step} best_val={best_val}", flush=True)
    if _is_distributed():
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
