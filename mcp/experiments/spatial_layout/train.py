import argparse
import json
import os
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

import torch
from torch.nn import functional as F
from torch.optim.lr_scheduler import CosineAnnealingLR, LinearLR, SequentialLR

from .checkpoint import (
    _torch_load,
    infer_rel_max_bin_from_state_dict,
    resolve_checkpoint_path,
    save_training_checkpoint,
)
from .dataset import create_dataloaders
from .grammar import grammar_allowed_token_ids
from .metrics import (
    aggregate_net_role_stats,
    calculate_anchor_consistency,
    calculate_dist_distribution,
    calculate_gvr,
    calculate_overlap_fraction,
    calculate_side_diversity,
    net_role_match_stats,
)
from .transformer_model import SpatialTransformer


def _maybe_init_wandb(args: argparse.Namespace) -> Any | None:
    if not getattr(args, "wandb", False):
        return None
    try:
        import wandb  # type: ignore
    except ImportError as exc:
        raise RuntimeError("wandb logging requested but wandb is not installed") from exc

    base_url = (getattr(args, "wandb_base_url", "") or "").strip()
    if base_url:
        os.environ["WANDB_BASE_URL"] = base_url

    tags = [
        tag.strip()
        for tag in (getattr(args, "wandb_tags", "") or "").split(",")
        if tag.strip()
    ]
    run = wandb.init(
        project=getattr(args, "wandb_project", "spatial-layout"),
        entity=getattr(args, "wandb_entity", "") or None,
        name=getattr(args, "wandb_run_name", "") or None,
        tags=tags or None,
        config=vars(args),
        resume="allow" if getattr(args, "wandb_resume_id", "") else None,
        id=getattr(args, "wandb_resume_id", "") or None,
        mode=getattr(args, "wandb_mode", "online"),
    )
    if base_url:
        run.summary["wandb_base_url"] = base_url
    return run


def _wandb_log_checkpoint(
    run: Any,
    *,
    ckpt_path: str,
    artifact_name: str,
    artifact_aliases: List[str],
    metadata: Dict[str, Any],
) -> None:
    if run is None or not os.path.isfile(ckpt_path):
        return
    try:
        import wandb  # type: ignore
    except ImportError:
        return

    artifact = wandb.Artifact(
        artifact_name,
        type="model",
        metadata=metadata,
    )
    artifact.add_file(ckpt_path, name=os.path.basename(ckpt_path))
    run.log_artifact(artifact, aliases=artifact_aliases)


def _fmt_hist(h: dict) -> str:
    if not h:
        return "(empty — no parsed placement fields)"
    return "{" + ", ".join(f"{k}:{v:.2f}" for k, v in h.items()) + "}"


def _layout_score(
    *,
    gvr: float,
    anchor_consistency: float,
    overlap_fraction: float,
    net_role_match_rate: Optional[float],
) -> float:
    overlap_ok = 1.0 - max(0.0, min(1.0, overlap_fraction))
    nrm = 0.0 if net_role_match_rate is None else float(net_role_match_rate)
    return (
        0.35 * float(gvr)
        + 0.30 * float(anchor_consistency)
        + 0.25 * overlap_ok
        + 0.10 * nrm
    )


def _layout_rank_tuple(
    *,
    layout_score: float,
    gvr: float,
    anchor_consistency: float,
    overlap_fraction: float,
    net_role_match_rate: Optional[float],
    val_loss: float,
) -> tuple[float, float, float, float, float, float]:
    overlap_ok = 1.0 - max(0.0, min(1.0, overlap_fraction))
    nrm = -1.0 if net_role_match_rate is None else float(net_role_match_rate)
    return (
        float(layout_score),
        float(gvr),
        float(anchor_consistency),
        overlap_ok,
        nrm,
        -float(val_loss),
    )


_MASKABLE_PREFIXES = (
    "TYPE_",
    "ROLE_",
    "TOPO_",
    "VAL_",
    "PKG_",
    "DIR_",
    "SIDE_",
    "DX_",
    "DY_",
    "PAGE_X_",
    "PAGE_Y_",
    "DIST_",
    "ROT_",
    "MIRROR_",
    "NET_",
)
_MASKABLE_EXACT = {
    "TYPE",
    "ROLE",
    "TOPO",
    "VAL",
    "PKG",
    "REF",
    "PIN",
    "PAGE_GRID_X",
    "PAGE_GRID_Y",
    "ANCHOR_REF",
    "ANCHOR_PIN",
}


def _is_maskable_token(token: str) -> bool:
    if token in _MASKABLE_EXACT:
        return False
    return token.startswith(_MASKABLE_PREFIXES)


def _build_token_weights(
    y: torch.Tensor,
    vocab_itos: Dict[int, str],
    pad_idx: int,
    spatial_token_weight: float,
) -> torch.Tensor:
    weights = torch.ones_like(y, dtype=torch.float32)
    if spatial_token_weight <= 1.0:
        return weights

    for row in range(y.size(0)):
        for col in range(y.size(1)):
            token_id = int(y[row, col].item())
            if token_id == pad_idx:
                continue
            token = vocab_itos.get(token_id, "<UNK>")
            if token.startswith(_MASKABLE_PREFIXES):
                weights[row, col] = spatial_token_weight
    return weights


def _weighted_token_ce(
    logits: torch.Tensor,
    y: torch.Tensor,
    vocab_itos: Dict[int, str],
    pad_idx: int,
    spatial_token_weight: float,
    sample_contexts: Optional[List[Optional[Dict[str, Any]]]] = None,
) -> torch.Tensor:
    per_token_loss = F.cross_entropy(
        logits.reshape(-1, logits.size(-1)),
        y.reshape(-1),
        ignore_index=pad_idx,
        reduction="none",
    ).reshape_as(y)
    valid_mask = y != pad_idx
    if sample_contexts:
        for row, ctx in enumerate(sample_contexts[: y.size(0)]):
            if not isinstance(ctx, dict):
                continue
            target_start = ctx.get("target_start_idx")
            if target_start is None:
                continue
            try:
                cutoff = max(0, int(target_start) - 1)
            except (TypeError, ValueError):
                continue
            if cutoff > 0:
                valid_mask[row, : min(cutoff, y.size(1))] = False
    if not valid_mask.any():
        return torch.zeros((), device=logits.device)
    weights = _build_token_weights(y, vocab_itos, pad_idx, spatial_token_weight)
    weighted = per_token_loss * weights
    return weighted[valid_mask].sum() / weights[valid_mask].sum()


def _coordinate_regression_loss(
    hidden: torch.Tensor,
    relation_context: Optional[List[Optional[Dict[str, Any]]]],
    coord_head: torch.nn.Module,
    coord_scale: float,
) -> torch.Tensor:
    if relation_context is None or coord_scale <= 0.0:
        return torch.zeros((), device=hidden.device)

    pred = coord_head(hidden)
    losses: List[torch.Tensor] = []

    for row, ctx in enumerate(relation_context):
        ctx = _extract_spatial_context(ctx)
        if not ctx:
            continue
        blocks = ctx.get("blocks") or []
        token_positions = ctx.get("token_positions") or []
        for pos, ann in enumerate(token_positions[: hidden.size(1)]):
            if not isinstance(ann, dict):
                continue
            x = ann.get("x")
            y = ann.get("y")
            block_index = ann.get("block_index")
            if x is None or y is None or block_index is None:
                continue
            if block_index < 0 or block_index >= len(blocks):
                continue
            anchor_xy = blocks[block_index].get("anchor_xy") or [0.0, 0.0]
            tx = (float(x) - float(anchor_xy[0])) / coord_scale
            ty = (float(y) - float(anchor_xy[1])) / coord_scale
            target = torch.tensor([tx, ty], device=hidden.device, dtype=hidden.dtype)
            losses.append(F.smooth_l1_loss(pred[row, pos], target, reduction="mean"))

    if not losses:
        return torch.zeros((), device=hidden.device)
    return torch.stack(losses).mean()


def _extract_spatial_context(
    sample_ctx: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not sample_ctx:
        return None
    if "token_positions" in sample_ctx or "blocks" in sample_ctx:
        return sample_ctx
    nested = sample_ctx.get("relation_context")
    if isinstance(nested, dict) and (
        "token_positions" in nested or "blocks" in nested
    ):
        return nested
    return sample_ctx if "components" in sample_ctx else None


def _estimate_object_box(obj: Dict[str, Any]) -> tuple[float, float]:
    obj_type = obj.get("type")
    package = obj.get("package_bin")
    role = obj.get("role")
    if obj_type == "TYPE_IC":
        return 12.0, 7.0
    if obj_type == "TYPE_CONN":
        return 7.0, 4.5
    if obj_type in {"TYPE_GND", "TYPE_PWR"}:
        return 4.0, 2.4
    if obj_type in {"TYPE_C", "TYPE_CPOL", "TYPE_R"}:
        if package == "PKG_SMD_SMALL":
            return 2.2, 1.2
        if package == "PKG_SMD_LARGE":
            return 3.6, 1.8
        if package in {"PKG_IC_SMALL", "PKG_IC_LARGE"}:
            return 4.8, 2.4
        return 2.8, 1.4
    if role == "ROLE_CONNECTOR":
        return 6.0, 3.0
    if role in {"ROLE_REGULATOR", "ROLE_ACTIVE"}:
        return 5.0, 2.8
    return 3.2, 1.6


def _geometry_validity_loss(
    hidden: torch.Tensor,
    relation_context: Optional[List[Optional[Dict[str, Any]]]],
    coord_head: torch.nn.Module,
    coord_scale: float,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    if relation_context is None or coord_scale <= 0.0:
        zero = torch.zeros((), device=hidden.device)
        return zero, zero, zero

    pred = coord_head(hidden)
    alignment_terms: List[torch.Tensor] = []
    relation_terms: List[torch.Tensor] = []
    overlap_terms: List[torch.Tensor] = []

    for row, ctx in enumerate(relation_context):
        ctx = _extract_spatial_context(ctx)
        if not ctx:
            continue

        blocks = ctx.get("blocks") or []
        token_positions = ctx.get("token_positions") or []
        block_objects: Dict[int, List[tuple[torch.Tensor, torch.Tensor, float, float]]] = {}
        seen_objects = set()

        for pos, ann in enumerate(token_positions[: hidden.size(1)]):
            if not isinstance(ann, dict):
                continue
            block_index = ann.get("block_index")
            object_index = ann.get("object_index")
            if block_index is None or object_index is None:
                continue
            block_index = int(block_index)
            object_index = int(object_index)
            if block_index < 0 or block_index >= len(blocks):
                continue
            block = blocks[block_index] if isinstance(blocks[block_index], dict) else None
            if not block:
                continue
            objects = block.get("objects") or []
            if object_index < 0 or object_index >= len(objects):
                continue
            key = (block_index, object_index)
            if key in seen_objects:
                continue
            seen_objects.add(key)

            obj = objects[object_index] if isinstance(objects[object_index], dict) else {}
            anchor_xy = block.get("anchor_xy") or [0.0, 0.0]
            obj_xy = obj.get("xy") or anchor_xy
            target = torch.tensor(
                [
                    (float(obj_xy[0]) - float(anchor_xy[0])) / coord_scale,
                    (float(obj_xy[1]) - float(anchor_xy[1])) / coord_scale,
                ],
                device=hidden.device,
                dtype=hidden.dtype,
            )
            pred_xy = pred[row, pos]
            alignment_terms.append(F.smooth_l1_loss(pred_xy, target, reduction="mean"))

            box_w_mm, box_h_mm = _estimate_object_box(obj)
            block_objects.setdefault(block_index, []).append(
                (pred_xy, target, box_w_mm / coord_scale, box_h_mm / coord_scale)
            )

        for objects in block_objects.values():
            for i in range(len(objects)):
                pred_i, target_i, w_i, h_i = objects[i]
                for j in range(i + 1, len(objects)):
                    pred_j, target_j, w_j, h_j = objects[j]
                    relation_terms.append(
                        F.smooth_l1_loss(
                            pred_i - pred_j,
                            target_i - target_j,
                            reduction="mean",
                        )
                    )
                    dx = torch.abs(pred_i[0] - pred_j[0])
                    dy = torch.abs(pred_i[1] - pred_j[1])
                    overlap_w = F.relu((w_i + w_j) * 0.5 - dx)
                    overlap_h = F.relu((h_i + h_j) * 0.5 - dy)
                    overlap_terms.append(overlap_w * overlap_h)

    zero = torch.zeros((), device=hidden.device)
    alignment_loss = torch.stack(alignment_terms).mean() if alignment_terms else zero
    relation_loss = torch.stack(relation_terms).mean() if relation_terms else zero
    overlap_loss = torch.stack(overlap_terms).mean() if overlap_terms else zero
    return alignment_loss, relation_loss, overlap_loss


def _masked_refinement_loss(
    model: SpatialTransformer,
    x: torch.Tensor,
    y: torch.Tensor,
    vocab_itos: Dict[int, str],
    relation_context: Optional[List[Optional[Dict[str, Any]]]],
    sample_contexts: Optional[List[Optional[Dict[str, Any]]]],
    pad_idx: int,
    unk_idx: int,
    mask_prob: float,
    mask_weight: float,
    spatial_token_weight: float,
    scheduled_sampling_prob: float,
    label_smoothing: float,
) -> torch.Tensor:
    if mask_prob <= 0.0 or mask_weight <= 0.0:
        return torch.zeros((), device=x.device)

    masked_x = x.clone()
    target_mask = torch.zeros_like(y, dtype=torch.bool)

    for row in range(x.size(0)):
        candidates: List[int] = []
        target_start = None
        if sample_contexts and row < len(sample_contexts):
            ctx = sample_contexts[row]
            if isinstance(ctx, dict):
                target_start = ctx.get("target_start_idx")
        try:
            min_pos = max(1, int(target_start)) if target_start is not None else 1
        except (TypeError, ValueError):
            min_pos = 1
        for pos in range(min_pos, x.size(1)):
            token_id = int(x[row, pos].item())
            if token_id == pad_idx:
                continue
            token = vocab_itos.get(token_id, "<UNK>")
            if _is_maskable_token(token):
                candidates.append(pos)

        if not candidates:
            continue

        keep = max(1, int(round(len(candidates) * mask_prob)))
        keep = min(keep, len(candidates))
        choice = torch.randperm(len(candidates), device=x.device)[:keep]
        for idx in choice.tolist():
            pos = candidates[idx]
            masked_x[row, pos] = unk_idx
            target_pos = pos - 1
            if 0 <= target_pos < target_mask.size(1):
                target_mask[row, target_pos] = True

    if not target_mask.any():
        return torch.zeros((), device=x.device)

    logits, _ = model(
        masked_x,
        relation_context=relation_context,
        scheduled_sampling_prob=scheduled_sampling_prob,
        label_smoothing=label_smoothing,
    )
    per_token_loss = F.cross_entropy(
        logits.reshape(-1, logits.size(-1)),
        y.reshape(-1),
        ignore_index=pad_idx,
        reduction="none",
    ).reshape_as(y)
    if spatial_token_weight > 1.0:
        weights = _build_token_weights(
            y,
            vocab_itos,
            pad_idx,
            spatial_token_weight,
        )
        per_token_loss = per_token_loss * weights
    masked_values = per_token_loss[target_mask]
    if masked_values.numel() == 0:
        return torch.zeros((), device=x.device)
    return masked_values.mean() * mask_weight


def train(args):
    if torch.backends.mps.is_available():
        device = torch.device("mps")
        print("Using MPS (Apple Silicon GPU)")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
        print("Using CUDA GPU")
    else:
        device = torch.device("cpu")
        print("Using CPU")

    non_blocking = device.type == "cuda"
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        if getattr(args, "cudnn_benchmark", True):
            torch.backends.cudnn.benchmark = True

    ckpt_dir = os.path.abspath(getattr(args, "checkpoint_dir", ".") or ".")
    os.makedirs(ckpt_dir, exist_ok=True)
    wandb_run = _maybe_init_wandb(args)

    _nw = max(0, int(getattr(args, "num_workers", 0)))
    _pin = device.type == "cuda" and not getattr(args, "no_pin_memory", False)
    _persist = _nw > 0 and not getattr(args, "no_persistent_workers", False)
    if _nw > 0:
        print(f"DataLoader: num_workers={_nw}, pin_memory={_pin}, persistent_workers={_persist}", flush=True)

    _df = float(getattr(args, "dataset_fraction", 1.0))
    if not (0.0 < _df <= 1.0):
        raise ValueError(f"dataset_fraction must be in (0, 1], got {_df}")

    use_length_bucket = not bool(getattr(args, "no_length_bucket", False))
    bucket_mega_batch_mult = max(1, int(getattr(args, "bucket_mega_batch_mult", 8)))
    phase1_epochs = max(0, int(getattr(args, "phase1_epochs", 0)))
    phase2_block_ratio = float(getattr(args, "phase2_block_ratio", 0.8))
    train_loader, val_loader, vocab = create_dataloaders(
        args.data_dir,
        args.batch_size,
        max_len=args.block_size,
        train_augmentation=getattr(args, "train_augmentation", False),
        dataset_fraction=_df,
        num_workers=_nw,
        pin_memory=_pin,
        persistent_workers=_persist,
        use_length_bucketed_sampler=use_length_bucket,
        bucket_mega_batch_mult=bucket_mega_batch_mult,
        phase1_epochs=phase1_epochs,
        phase2_block_ratio=phase2_block_ratio,
    )
    if use_length_bucket:
        print(
            f"Length-bucketed sampling: ON (mega_batch_mult={bucket_mega_batch_mult}); "
            f"per-batch dynamic padding to in-batch max len.",
            flush=True,
        )
    print(
        f"Train batches/epoch: {len(train_loader)}, val batches: {len(val_loader)}"
        + (f"  (dataset_fraction={_df})" if _df < 1.0 else ""),
        flush=True,
    )
    vocab_size = len(vocab)
    vocab_itos = {i: t for t, i in vocab.items()}
    pad_idx = vocab.get("<PAD>", 0)
    eos_id = vocab.get("EOS")
    unk_idx = vocab.get("<UNK>", pad_idx)

    resume_blob = None
    resume_path = None
    model_rel_max_bin = args.rel_max_bin
    if getattr(args, "resume", None):
        resume_path, _tried = resolve_checkpoint_path(
            args.resume, extra_search_dirs=[ckpt_dir]
        )
        if not resume_path:
            print("ERROR: --resume checkpoint not found.")
            print(f"  Given: {args.resume!r}")
            print("  Tried:")
            for t in _tried:
                print(f"    {t}")
            print(
                f"  Hint: checkpoints are written under --checkpoint_dir (default cwd); "
                f"use e.g. --resume {os.path.join(ckpt_dir, 'checkpoint_latest.pt')}"
            )
            raise SystemExit(1)
        print(f"Resuming weights from: {resume_path}")
        resume_blob = _torch_load(resume_path, map_location=device)
        if isinstance(resume_blob, dict):
            state_dict = resume_blob.get("model_state_dict", {})
            ckpt_cfg = resume_blob.get("config", {}) or {}
            model_rel_max_bin = int(
                ckpt_cfg.get(
                    "rel_max_bin",
                    infer_rel_max_bin_from_state_dict(state_dict, default=args.rel_max_bin),
                )
            )

    model = SpatialTransformer(
        vocab_size,
        pad_idx=pad_idx,
        n_embd=args.n_embd,
        n_head=args.n_head,
        n_layer=args.n_layer,
        block_size=args.block_size,
        dropout=args.dropout,
        coord_scale=args.coord_scale,
        rel_max_bin=model_rel_max_bin,
        coord_feat_dropout=getattr(args, "coord_feat_dropout", 0.0),
    ).to(device)

    if resume_blob is not None:
        if isinstance(resume_blob, dict) and "model_state_dict" in resume_blob:
            model.load_state_dict(resume_blob["model_state_dict"])
        else:
            model.load_state_dict(resume_blob)

    if getattr(args, "compile_model", False) and device.type == "cuda":
        model = torch.compile(model)  # type: ignore[assignment]

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )

    steps_per_epoch = max(len(train_loader), 1)
    total_steps = max(1, args.epochs * steps_per_epoch)
    warmup_steps = min(max(1, args.warmup_steps), total_steps - 1)
    cosine_steps = max(1, total_steps - warmup_steps)
    warmup_sched = LinearLR(
        optimizer, start_factor=0.1, end_factor=1.0, total_iters=warmup_steps
    )
    cosine_sched = CosineAnnealingLR(
        optimizer, T_max=cosine_steps, eta_min=args.lr * 0.01
    )
    scheduler = SequentialLR(
        optimizer,
        schedulers=[warmup_sched, cosine_sched],
        milestones=[warmup_steps],
    )

    use_amp = device.type == "cuda" and not getattr(args, "no_amp", False)
    amp_dtype_str: str = getattr(args, "amp_dtype", "bf16")
    if use_amp and amp_dtype_str == "bf16" and not torch.cuda.is_bf16_supported():
        amp_dtype_str = "fp16"
    amp_dtype = torch.bfloat16 if amp_dtype_str == "bf16" else torch.float16
    use_grad_scaler = use_amp and amp_dtype == torch.float16
    scaler = torch.amp.GradScaler("cuda", enabled=use_grad_scaler)
    train_autocast = (
        torch.amp.autocast("cuda", dtype=amp_dtype) if use_amp else nullcontext()
    )
    val_autocast = train_autocast
    if use_amp:
        print(
            f"AMP enabled: dtype={amp_dtype_str}, GradScaler={use_grad_scaler}",
            flush=True,
        )

    masked_refine_every = max(1, int(getattr(args, "masked_refine_every", 1)))

    def grammar_mask(seq_ids):
        allowed = grammar_allowed_token_ids(seq_ids, vocab, vocab_itos)
        return allowed

    best_val_loss = float("inf")
    best_layout_rank: Optional[tuple[float, float, float, float, float, float]] = None
    early_stop_counter = 0
    prog = getattr(args, "progress_interval", 25)

    def _set_loader_max_len(loader: Any, max_len: int) -> None:
        ds = getattr(loader, "dataset", None)
        if ds is None:
            return
        if hasattr(ds, "max_len"):
            ds.max_len = max_len
        base_ds = getattr(ds, "base_dataset", None)
        if base_ds is not None and hasattr(base_ds, "max_len"):
            base_ds.max_len = max_len

    def _set_loader_epoch(loader: Any, epoch_idx: int) -> None:
        # Reshuffles the length-bucketed batch sampler(s) so each epoch sees a
        # different randomization of within-bucket ordering. Also drives the
        # phased curriculum loader so it knows which phase to yield from.
        if hasattr(loader, "set_epoch"):
            loader.set_epoch(int(epoch_idx))
        sampler = getattr(loader, "batch_sampler", None)
        if sampler is not None and hasattr(sampler, "set_epoch"):
            sampler.set_epoch(int(epoch_idx))

    for epoch in range(args.epochs):
        if getattr(args, "curriculum_epochs", 0) and args.curriculum_epochs > 0:
            frac = min(1.0, (epoch + 1) / args.curriculum_epochs)
            mlen = int(
                args.min_curriculum_len
                + frac * (args.block_size - args.min_curriculum_len)
            )
            _set_loader_max_len(train_loader, mlen)
            _set_loader_max_len(val_loader, mlen)

        _set_loader_epoch(train_loader, epoch)
        _set_loader_epoch(val_loader, epoch)

        n_train_batches = len(train_loader)
        max_tb = int(getattr(args, "max_train_batches", 0) or 0)
        if max_tb:
            print(
                f"Epoch {epoch + 1}/{args.epochs}: train (capped at {max_tb} of {n_train_batches} batches)...",
                flush=True,
            )
        else:
            print(
                f"Epoch {epoch + 1}/{args.epochs}: train ({n_train_batches} batches)...",
                flush=True,
            )
        model.train()
        z64 = torch.zeros((), device=device, dtype=torch.float64)
        train_loss_acc = z64.clone()
        train_ce_acc = z64.clone()
        train_coord_acc = z64.clone()
        train_ga_acc = z64.clone()
        train_gr_acc = z64.clone()
        train_go_acc = z64.clone()
        train_masked_refine_acc = z64.clone()
        train_masked_refine_batches = 0
        train_steps = 0
        for bi, batch in enumerate(train_loader):
            if max_tb and bi >= max_tb:
                break
            relation_context = (
                [_extract_spatial_context(ctx) for ctx in batch[2]]
                if len(batch) > 2
                else None
            )
            x, y = batch[0], batch[1]
            x = x.to(device, non_blocking=non_blocking)
            y = y.to(device, non_blocking=non_blocking)

            want_masked_refine = (
                args.masked_refine_prob > 0.0
                and args.masked_refine_weight > 0.0
                and (masked_refine_every <= 1 or bi % masked_refine_every == 0)
            )

            optimizer.zero_grad(set_to_none=True)

            with train_autocast:
                logits, _, hidden = model(
                    x,
                    y,
                    scheduled_sampling_prob=args.scheduled_sampling_prob,
                    label_smoothing=args.label_smoothing,
                    relation_context=relation_context,
                    return_hidden=True,
                )

                ce_loss = _weighted_token_ce(
                    logits,
                    y,
                    vocab_itos,
                    pad_idx,
                    args.spatial_token_weight,
                    sample_contexts=batch[2] if len(batch) > 2 else None,
                )
                if want_masked_refine:
                    masked_refine_loss = _masked_refinement_loss(
                        model=model,
                        x=x,
                        y=y,
                        vocab_itos=vocab_itos,
                        relation_context=relation_context,
                        sample_contexts=batch[2] if len(batch) > 2 else None,
                        pad_idx=pad_idx,
                        unk_idx=unk_idx,
                        mask_prob=args.masked_refine_prob,
                        mask_weight=args.masked_refine_weight,
                        spatial_token_weight=args.spatial_token_weight,
                        scheduled_sampling_prob=args.scheduled_sampling_prob,
                        label_smoothing=args.label_smoothing,
                    )
                else:
                    masked_refine_loss = torch.zeros(
                        (), device=device, dtype=torch.float32
                    )
                coord_loss = _coordinate_regression_loss(
                    hidden,
                    relation_context,
                    model.coord_head,
                    args.coord_scale,
                )
                geom_align_loss, geom_relation_loss, geom_overlap_loss = (
                    _geometry_validity_loss(
                        hidden,
                        relation_context,
                        model.coord_head,
                        args.coord_scale,
                    )
                )
                loss = ce_loss
                loss = loss + masked_refine_loss
                loss = loss + args.coord_loss_weight * coord_loss
                loss = loss + args.geometry_alignment_weight * geom_align_loss
                loss = loss + args.pairwise_geometry_weight * geom_relation_loss
                loss = loss + args.overlap_loss_weight * geom_overlap_loss

            if use_grad_scaler:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
            scheduler.step()

            with torch.no_grad():
                train_loss_acc += loss.detach().double()
                train_ce_acc += ce_loss.detach().double()
                train_coord_acc += coord_loss.detach().double()
                train_ga_acc += geom_align_loss.detach().double()
                train_gr_acc += geom_relation_loss.detach().double()
                train_go_acc += geom_overlap_loss.detach().double()
                if want_masked_refine:
                    train_masked_refine_acc += masked_refine_loss.detach().double()
                    train_masked_refine_batches += 1
            train_steps += 1
            # First few batches always log so long gaps (large progress_interval) do not look hung.
            if prog and (
                bi < 5
                or (bi + 1) % prog == 0
                or (bi + 1) == n_train_batches
            ):
                denom = min(n_train_batches, max_tb) if max_tb else n_train_batches
                print(
                    f"  train batch {bi + 1}/{denom}  loss={float(loss.detach().float()):.4f}",
                    flush=True,
                )

        ts = max(train_steps, 1)
        avg_train_loss = float(train_loss_acc / ts)
        avg_train_ce_loss = float(train_ce_acc / ts)
        avg_train_coord_loss = float(train_coord_acc / ts)
        avg_train_geom_align_loss = float(train_ga_acc / ts)
        avg_train_geom_relation_loss = float(train_gr_acc / ts)
        avg_train_geom_overlap_loss = float(train_go_acc / ts)
        avg_train_masked_refine_loss = (
            float(train_masked_refine_acc / train_masked_refine_batches)
            if train_masked_refine_batches
            else 0.0
        )

        model.eval()
        val_loss_sum = 0.0
        val_ce_loss_sum = 0.0
        val_steps = 0
        all_generated_tokens = []
        all_ir_ctxs = []
        net_role_parts = []

        max_metric_batches = (
            len(val_loader)
            if args.val_metric_max_batches <= 0
            else min(args.val_metric_max_batches, len(val_loader))
        )
        n_val_batches = len(val_loader)
        val_fwd_limit = (
            n_val_batches
            if getattr(args, "max_val_forward_batches", 0) <= 0
            else min(int(args.max_val_forward_batches), n_val_batches)
        )
        gen_batches_used = min(max_metric_batches, n_val_batches)
        gen_total_est = gen_batches_used * min(
            args.val_samples_per_batch, args.batch_size
        )
        gen_prog_step = max(1, prog // 5) if prog else 0
        gen_done = 0
        gen_header_printed = False

        print(
            f"Epoch {epoch + 1}: val forward ({val_fwd_limit} of {n_val_batches} batches)...",
            flush=True,
        )
        with torch.no_grad():
            for bi, batch in enumerate(val_loader):
                x, y = batch[0], batch[1]
                ir_batch = (
                    [_extract_spatial_context(ctx) for ctx in batch[2]]
                    if len(batch) > 2
                    else [None] * x.size(0)
                )
                x = x.to(device, non_blocking=non_blocking)
                y = y.to(device, non_blocking=non_blocking)

                if bi < val_fwd_limit:
                    with val_autocast:
                        logits, loss = model(x, y, relation_context=ir_batch)
                    ce_loss = _weighted_token_ce(
                        logits,
                        y,
                        vocab_itos,
                        pad_idx,
                        args.spatial_token_weight,
                        sample_contexts=batch[2] if len(batch) > 2 else None,
                    )
                    val_loss_sum += float(loss.detach())
                    val_ce_loss_sum += float(ce_loss.detach())
                    val_steps += 1

                    if prog and (
                        bi < 5
                        or (bi + 1) % prog == 0
                        or (bi + 1) == val_fwd_limit
                    ):
                        print(
                            f"  val forward {bi + 1}/{val_fwd_limit}  loss={float(loss.detach().float()):.4f}",
                            flush=True,
                        )

                if bi >= max_metric_batches:
                    continue

                n_samp = min(args.val_samples_per_batch, x.size(0))
                allowed = grammar_mask if args.grammar_mask else None
                if not gen_header_printed:
                    print(
                        f"  val generate: up to {gen_total_est} sequences "
                        f"({gen_batches_used} batches × ≤{args.val_samples_per_batch} samples, "
                        f"max {args.max_gen_tokens} new tokens each)...",
                        flush=True,
                    )
                    gen_header_printed = True
                for si in range(n_samp):
                    prompt_len = int(args.prompt_len)
                    ctx = ir_batch[si] if ir_batch and si < len(ir_batch) else None
                    if isinstance(ctx, dict) and ctx.get("target_start_idx") is not None:
                        try:
                            prompt_len = max(1, int(ctx["target_start_idx"]))
                        except (TypeError, ValueError):
                            prompt_len = int(args.prompt_len)
                    prompt = x[si : si + 1, : prompt_len]
                    prompt_ctx = [ctx] if ctx is not None else None
                    with val_autocast:
                        out = model.generate(
                            prompt,
                            max_new_tokens=args.max_gen_tokens,
                            eos_id=eos_id,
                            pad_idx=pad_idx,
                            allowed_tokens=allowed,
                            relation_context=prompt_ctx,
                        )
                    gen_tokens = [vocab_itos[i.item()] for i in out[0]]
                    all_generated_tokens.append(gen_tokens)
                    all_ir_ctxs.append(ir_batch[si])
                    net_role_parts.append(net_role_match_stats([gen_tokens]))
                    gen_done += 1
                    if gen_prog_step and (
                        gen_done % gen_prog_step == 0 or gen_done == gen_total_est
                    ):
                        print(
                            f"  val generate {gen_done}/{gen_total_est}",
                            flush=True,
                        )

            if gen_header_printed:
                print(
                    f"  val generate finished ({gen_done} sequences).",
                    flush=True,
                )

        gvr = calculate_gvr(all_generated_tokens)
        ac = calculate_anchor_consistency(all_generated_tokens, ir_contexts=all_ir_ctxs)
        ovr = calculate_overlap_fraction(
            all_generated_tokens, ir_contexts=all_ir_ctxs
        )
        nrm_agg = aggregate_net_role_stats(net_role_parts)
        nrm_rate = nrm_agg["rate"]
        layout_score = _layout_score(
            gvr=gvr,
            anchor_consistency=ac,
            overlap_fraction=ovr,
            net_role_match_rate=nrm_rate,
        )
        nrm_disp = (
            "n/a"
            if nrm_rate is None
            else f"{nrm_rate:.2%} (n_decoup={nrm_agg['total_relevant']})"
        )
        side_dist = calculate_side_diversity(all_generated_tokens)
        dist_dist = calculate_dist_distribution(all_generated_tokens)

        avg_val_loss = val_loss_sum / max(val_steps, 1)
        avg_val_ce_loss = val_ce_loss_sum / max(val_steps, 1)
        lr_now = scheduler.get_last_lr()[0]
        print(
            f"Epoch {epoch+1}, Loss: {avg_train_loss:.4f}, Val Loss: {avg_val_loss:.4f}, "
            f"val/ce_loss: {avg_val_ce_loss:.4f}, LR: {lr_now:.6f}"
        )
        print(
            "Train losses: "
            f"train/ce_loss={avg_train_ce_loss:.4f}, "
            f"train/masked_refine_loss={avg_train_masked_refine_loss:.4f}, "
            f"train/coord_loss={avg_train_coord_loss:.4f}, "
            f"train/geom_align_loss={avg_train_geom_align_loss:.4f}, "
            f"train/geom_relation_loss={avg_train_geom_relation_loss:.4f}, "
            f"train/geom_overlap_loss={avg_train_geom_overlap_loss:.4f}, "
            f"train/total_loss={avg_train_loss:.4f}"
        )
        print(
            f"Metrics: GVR={gvr:.2%}, Anchor={ac:.2%}, Overlap={ovr:.2%}, NetRole={nrm_disp}"
        )
        print(f"  layout_score={layout_score:.4f}")
        print(f"  side_dist={_fmt_hist(side_dist)}")
        print(f"  dist_dist={_fmt_hist(dist_dist)}")
        print(
            "METRICS "
            + json.dumps(
                {
                    "epoch": epoch + 1,
                    "train_loss": avg_train_loss,
                    "val_loss": avg_val_loss,
                    "train/ce_loss": avg_train_ce_loss,
                    "train/masked_refine_loss": avg_train_masked_refine_loss,
                    "train/coord_loss": avg_train_coord_loss,
                    "train/geom_align_loss": avg_train_geom_align_loss,
                    "train/geom_relation_loss": avg_train_geom_relation_loss,
                    "train/geom_overlap_loss": avg_train_geom_overlap_loss,
                    "train/total_loss": avg_train_loss,
                    "val/ce_loss": avg_val_ce_loss,
                    "lr": lr_now,
                    "gvr": gvr,
                    "anchor_consistency": ac,
                    "overlap_fraction": ovr,
                    "net_role_match_rate": nrm_rate,
                    "layout_score": layout_score,
                    "side_dist": side_dist,
                    "dist_dist": dist_dist,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if wandb_run is not None:
            wandb_run.log(
                {
                    "epoch": epoch + 1,
                    "train/loss": avg_train_loss,
                    "train/ce_loss": avg_train_ce_loss,
                    "train/masked_refine_loss": avg_train_masked_refine_loss,
                    "train/coord_loss": avg_train_coord_loss,
                    "train/geom_align_loss": avg_train_geom_align_loss,
                    "train/geom_relation_loss": avg_train_geom_relation_loss,
                    "train/geom_overlap_loss": avg_train_geom_overlap_loss,
                    "train/total_loss": avg_train_loss,
                    "val/loss": avg_val_loss,
                    "val/ce_loss": avg_val_ce_loss,
                    "train/lr": lr_now,
                    "metrics/gvr": gvr,
                    "metrics/anchor_consistency": ac,
                    "metrics/overlap_fraction": ovr,
                    "metrics/net_role_match_rate": nrm_rate,
                    "metrics/layout_score": layout_score,
                    **{f"metrics/side_dist/{k}": v for k, v in side_dist.items()},
                    **{f"metrics/dist_dist/{k}": v for k, v in dist_dist.items()},
                },
                step=epoch + 1,
            )

        ckpt_kw = {
            "n_embd": args.n_embd,
            "n_head": args.n_head,
            "n_layer": args.n_layer,
            "block_size": args.block_size,
            "dropout": args.dropout,
            "rel_max_bin": model.rel_max_bin,
            "coord_feat_dropout": getattr(args, "coord_feat_dropout", 0.0),
            "amp": use_amp,
            "amp_dtype": amp_dtype_str if use_amp else None,
            "masked_refine_every": masked_refine_every,
        }

        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            early_stop_counter = 0
            best_ckpt_path = os.path.join(ckpt_dir, "checkpoint_best.pt")
            save_training_checkpoint(
                best_ckpt_path,
                model.state_dict(),
                vocab,
                pad_idx,
                ckpt_kw,
            )
            _wandb_log_checkpoint(
                wandb_run,
                ckpt_path=best_ckpt_path,
                artifact_name="spatial-layout-checkpoint-best",
                artifact_aliases=["best", f"epoch-{epoch + 1}"],
                metadata={
                    "epoch": epoch + 1,
                    "val_loss": avg_val_loss,
                    "train_loss": avg_train_loss,
                },
            )
            print("Saved new best checkpoint.")
        else:
            early_stop_counter += 1

        layout_rank = _layout_rank_tuple(
            layout_score=layout_score,
            gvr=gvr,
            anchor_consistency=ac,
            overlap_fraction=ovr,
            net_role_match_rate=nrm_rate,
            val_loss=avg_val_loss,
        )
        if best_layout_rank is None or layout_rank > best_layout_rank:
            best_layout_rank = layout_rank
            best_layout_ckpt_path = os.path.join(ckpt_dir, "checkpoint_best_layout.pt")
            save_training_checkpoint(
                best_layout_ckpt_path,
                model.state_dict(),
                vocab,
                pad_idx,
                ckpt_kw,
            )
            _wandb_log_checkpoint(
                wandb_run,
                ckpt_path=best_layout_ckpt_path,
                artifact_name="spatial-layout-checkpoint-best-layout",
                artifact_aliases=["best-layout", f"epoch-{epoch + 1}"],
                metadata={
                    "epoch": epoch + 1,
                    "layout_score": layout_score,
                    "gvr": gvr,
                    "anchor_consistency": ac,
                    "overlap_fraction": ovr,
                    "net_role_match_rate": nrm_rate,
                    "val_loss": avg_val_loss,
                },
            )
            print("Saved new best layout checkpoint.")

        if (epoch + 1) % args.save_interval == 0:
            epoch_ckpt_path = os.path.join(ckpt_dir, f"checkpoint_epoch_{epoch+1}.pt")
            save_training_checkpoint(
                epoch_ckpt_path,
                model.state_dict(),
                vocab,
                pad_idx,
                ckpt_kw,
            )
            if getattr(args, "wandb_log_checkpoints", False):
                _wandb_log_checkpoint(
                    wandb_run,
                    ckpt_path=epoch_ckpt_path,
                    artifact_name="spatial-layout-checkpoint-epoch",
                    artifact_aliases=[f"epoch-{epoch + 1}"],
                    metadata={
                        "epoch": epoch + 1,
                        "val_loss": avg_val_loss,
                        "train_loss": avg_train_loss,
                    },
                )

        latest_ckpt_path = os.path.join(ckpt_dir, "checkpoint_latest.pt")
        save_training_checkpoint(
            latest_ckpt_path,
            model.state_dict(),
            vocab,
            pad_idx,
            ckpt_kw,
        )
        if wandb_run is not None:
            wandb_run.summary["best_val_loss"] = best_val_loss
            wandb_run.summary["best_layout_score"] = (
                None if best_layout_rank is None else best_layout_rank[0]
            )
            wandb_run.summary["last_epoch"] = epoch + 1
            wandb_run.summary["checkpoint_latest"] = latest_ckpt_path
            wandb_run.summary["checkpoint_dir"] = ckpt_dir

        if args.early_stop_patience and early_stop_counter >= args.early_stop_patience:
            print(f"Early stopping at epoch {epoch+1}")
            break

    if wandb_run is not None:
        wandb_run.finish()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data_dir", type=str, required=True)
    parser.add_argument(
        "--dataset_fraction",
        type=float,
        default=1.0,
        help="Use this fraction of the dataset after shuffle, before train/val split (e.g. 0.1 for quick runs).",
    )
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument(
        "--batch_size",
        type=int,
        default=16,
        help="Per-step batch size. Raise with spare VRAM; if you double batch, consider nudging LR upward.",
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=4,
        help="DataLoader background workers (CPU). 0 uses the main process only. Try 4–8 on CUDA hosts.",
    )
    parser.add_argument(
        "--no_pin_memory",
        action="store_true",
        help="Disable host pin_memory (default: on for CUDA, off for MPS/CPU).",
    )
    parser.add_argument(
        "--no_persistent_workers",
        action="store_true",
        help="Disable DataLoader persistent_workers when num_workers>0 (saves RAM; slower epoch restarts).",
    )
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight_decay", type=float, default=0.01)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--save_interval", type=int, default=5)
    parser.add_argument("--block_size", type=int, default=512)
    parser.add_argument("--early_stop_patience", type=int, default=None)
    parser.add_argument(
        "--checkpoint_dir",
        type=str,
        default=".",
        help="Where to write checkpoint_*.pt (created if missing). Resume paths are also searched here.",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="Checkpoint .pt to load weights from (searched on cwd, this package dir, and checkpoint_dir).",
    )
    parser.add_argument("--n_embd", type=int, default=128)
    parser.add_argument("--n_head", type=int, default=4)
    parser.add_argument("--n_layer", type=int, default=4)
    parser.add_argument(
        "--warmup_steps",
        type=int,
        default=200,
        help="Linear LR warmup steps (per-batch scheduler).",
    )
    parser.add_argument(
        "--scheduled_sampling_prob",
        type=float,
        default=0.15,
        help="Approximate scheduled sampling prob (0 disables).",
    )
    parser.add_argument(
        "--label_smoothing",
        type=float,
        default=0.05,
    )
    parser.add_argument(
        "--masked_refine_prob",
        type=float,
        default=0.25,
        help="Fraction of eligible geometry/role tokens to mask for the auxiliary refinement loss.",
    )
    parser.add_argument(
        "--masked_refine_weight",
        type=float,
        default=0.25,
        help="Weight for the masked refinement loss added on top of next-token CE.",
    )
    parser.add_argument(
        "--masked_refine_every",
        type=int,
        default=2,
        help=(
            "Run masked refinement (2nd forward) only every N train batches (1=every batch). "
            "Values >1 speed training; combine with LR tuning if needed."
        ),
    )
    parser.add_argument(
        "--spatial_token_weight",
        type=float,
        default=1.0,
        help="Upweight spatial/layout tokens in the main CE loss.",
    )
    parser.add_argument(
        "--coord_loss_weight",
        type=float,
        default=0.5,
        help="Weight for the auxiliary coordinate regression loss.",
    )
    parser.add_argument(
        "--coord_scale",
        type=float,
        default=10.0,
        help="Scale factor for coordinate regression targets.",
    )
    parser.add_argument(
        "--coord_feat_dropout",
        type=float,
        default=0.0,
        help="Drop probability for relation-context coordinate feature rows during training.",
    )
    parser.add_argument(
        "--rel_max_bin",
        type=int,
        default=30,
        help="Max absolute relative-bias bin for attention geometry.",
    )
    parser.add_argument(
        "--geometry_alignment_weight",
        type=float,
        default=0.10,
        help="Weight for object-level coordinate alignment loss.",
    )
    parser.add_argument(
        "--pairwise_geometry_weight",
        type=float,
        default=0.05,
        help="Weight for pairwise relative geometry consistency loss.",
    )
    parser.add_argument(
        "--overlap_loss_weight",
        type=float,
        default=0.10,
        help="Weight for predicted box-overlap penalty.",
    )
    parser.set_defaults(grammar_mask=True)
    parser.add_argument(
        "--no_grammar_mask",
        dest="grammar_mask",
        action="store_false",
        help="Disable grammar-constrained decoding during val metrics.",
    )
    parser.add_argument("--prompt_len", type=int, default=10)
    parser.add_argument("--max_gen_tokens", type=int, default=256)
    parser.add_argument(
        "--val_metric_max_batches",
        type=int,
        default=64,
        help="Cap val batches used for generative metrics (0 = all batches).",
    )
    parser.add_argument(
        "--max_val_forward_batches",
        type=int,
        default=0,
        help=(
            "Cap val batches used for CE forward only (0 = all). "
            "Smaller values speed epochs; early-stop uses this subset's avg loss."
        ),
    )
    parser.add_argument(
        "--val_samples_per_batch",
        type=int,
        default=2,
        help="Generations per val batch (for stable GVR / NetRole).",
    )
    parser.add_argument(
        "--curriculum_epochs",
        type=int,
        default=0,
        help="If >0, ramp max_len from min_curriculum_len to block_size over this many epochs.",
    )
    parser.add_argument("--min_curriculum_len", type=int, default=128)
    parser.add_argument(
        "--train_augmentation",
        action="store_true",
        help="Enable train-split token augmentation (rotation/mirror/block shuffle).",
    )
    parser.add_argument(
        "--progress_interval",
        type=int,
        default=25,
        help=(
            "Print train/val batch progress every N steps (0 = disable). "
            "Batches 1–5 always print when N>0. Large N (e.g. 100) means a long quiet gap until batch 100."
        ),
    )
    parser.add_argument(
        "--max_train_batches",
        type=int,
        default=0,
        help="If >0, stop each train epoch after this many batches (smoke tests; metrics are partial).",
    )
    parser.add_argument(
        "--no_length_bucket",
        action="store_true",
        help="Disable length-bucketed sampling + dynamic padding (fall back to fixed-len padding).",
    )
    parser.add_argument(
        "--bucket_mega_batch_mult",
        type=int,
        default=8,
        help=(
            "Length-bucketed sampler mega-batch multiplier; samples are sorted within "
            "windows of mega_batch_mult * batch_size before being sliced into batches."
        ),
    )
    parser.add_argument(
        "--phase1_epochs",
        type=int,
        default=0,
        help=(
            "Two-phase curriculum: epochs 1..N train on per-block records only "
            "(short sequences, fast local-placement convergence). Requires the "
            "corpus to have been built with --emit_per_block. Set to 0 to disable."
        ),
    )
    parser.add_argument(
        "--phase2_block_ratio",
        type=float,
        default=0.8,
        help=(
            "Phase 2 per-batch probability of sampling a per-block record vs a "
            "per-schematic record. 0.8 = 80%% blocks / 20%% schematics. Ignored "
            "when the corpus has no per-block records."
        ),
    )
    parser.add_argument("--wandb", action="store_true", help="Enable Weights & Biases logging.")
    parser.add_argument("--wandb-project", type=str, default="spatial-layout", help="W&B project name.")
    parser.add_argument("--wandb-entity", type=str, default="", help="W&B entity/team.")
    parser.add_argument("--wandb-run-name", type=str, default="", help="Explicit W&B run name.")
    parser.add_argument("--wandb-tags", type=str, default="", help="Comma-separated W&B tags.")
    parser.add_argument(
        "--wandb-mode",
        type=str,
        default="online",
        help="W&B mode: online, offline, or disabled.",
    )
    parser.add_argument(
        "--wandb-base-url",
        type=str,
        default="",
        help="Optional self-hosted W&B base URL. Also honored from WANDB_BASE_URL.",
    )
    parser.add_argument(
        "--wandb-resume-id",
        type=str,
        default="",
        help="Optional W&B run id for resume=allow.",
    )
    parser.add_argument(
        "--wandb-log-checkpoints",
        action="store_true",
        help="Upload save-interval checkpoints as W&B model artifacts.",
    )
    parser.add_argument(
        "--no_amp",
        action="store_true",
        help="Disable CUDA mixed precision (bf16/fp16 autocast; on by default on CUDA).",
    )
    parser.add_argument(
        "--amp_dtype",
        type=str,
        choices=("bf16", "fp16"),
        default="bf16",
        help="Autocast dtype on CUDA (fp16 uses GradScaler).",
    )
    parser.add_argument(
        "--compile_model",
        action="store_true",
        help="Wrap model in torch.compile (CUDA only; PyTorch 2+).",
    )
    parser.add_argument(
        "--no_cudnn_benchmark",
        dest="cudnn_benchmark",
        action="store_false",
        help="Disable cudnn.benchmark (default: enabled on CUDA).",
    )
    parser.set_defaults(cudnn_benchmark=True)
    args = parser.parse_args()
    train(args)
