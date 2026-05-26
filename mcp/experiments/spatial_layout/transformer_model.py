from __future__ import annotations

from typing import Any, Dict, List, Optional

import torch
import torch.nn as nn
from torch.nn import functional as F


class SpatialTransformer(nn.Module):
    def __init__(
        self,
        vocab_size,
        n_embd=128,
        n_head=4,
        n_layer=4,
        block_size=512,
        dropout=0.1,
        pad_idx=0,
        coord_scale=10.0,
        rel_max_bin=4,
        coord_feat_dropout=0.0,
    ):
        super().__init__()
        self.block_size = block_size
        self.pad_idx = pad_idx
        self.coord_scale = float(coord_scale) if coord_scale and coord_scale > 0 else 10.0
        self.coord_feat_dropout = float(max(0.0, min(1.0, coord_feat_dropout)))
        self.token_embedding_table = nn.Embedding(vocab_size, n_embd)
        self.position_embedding_table = nn.Embedding(block_size, n_embd)
        self.coord_feature_proj = nn.Sequential(
            nn.Linear(7, n_embd),
            nn.Tanh(),
            nn.Linear(n_embd, n_embd),
        )
        self.dropout = nn.Dropout(dropout)
        self.blocks = nn.ModuleList(
            [Block(n_embd, n_head=n_head, dropout=dropout, block_size=block_size) for _ in range(n_layer)]
        )
        self.ln_f = nn.LayerNorm(n_embd)
        self.lm_head = nn.Linear(n_embd, vocab_size, bias=False)
        self.coord_head = nn.Linear(n_embd, 2)

        # Relative-bias path inspired by iRPE / GeoPE: same sequence, but attention
        # can prefer tokens that share object/block context or occupy nearby 2D cells.
        self.rel_max_bin = int(rel_max_bin)
        self.rel_grid = self.rel_max_bin * 2 + 1
        self.rel_cell_mm = 2.54
        self.rel_bias = nn.Embedding(self.rel_grid * self.rel_grid, 1)
        self.same_block_bias = nn.Parameter(torch.zeros(1))
        self.same_object_bias = nn.Parameter(torch.zeros(1))

        self.apply(self._init_weights)
        self.lm_head.weight = self.token_embedding_table.weight

    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                torch.nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def _normalize_context(
        self, ctx: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if not ctx:
            return None
        if "token_positions" in ctx or "blocks" in ctx:
            return ctx
        nested = ctx.get("relation_context")
        if isinstance(nested, dict) and (
            "token_positions" in nested or "blocks" in nested
        ):
            return nested
        return ctx

    def _slice_relation_context(
        self,
        relation_context: Optional[List[Optional[Dict[str, Any]]]],
        seq_len: int,
    ) -> Optional[List[Optional[Dict[str, Any]]]]:
        if relation_context is None:
            return None
        sliced: List[Optional[Dict[str, Any]]] = []
        for ctx in relation_context:
            ctx = self._normalize_context(ctx)
            if not ctx:
                sliced.append(None)
                continue
            ctx_copy = dict(ctx)
            token_positions = list(ctx_copy.get("token_positions") or [])
            ctx_copy["token_positions"] = token_positions[:seq_len]
            sliced.append(ctx_copy)
        return sliced

    def _build_coord_features(
        self,
        relation_context: Optional[List[Optional[Dict[str, Any]]]],
        seq_len: int,
        device: torch.device,
    ) -> Optional[torch.Tensor]:
        if not relation_context:
            return None

        coord_scale = max(self.coord_scale, 1e-6)
        batch_feats: List[torch.Tensor] = []
        for ctx in relation_context:
            ctx = self._normalize_context(ctx)
            feats = torch.zeros((seq_len, 7), device=device)
            if not ctx:
                batch_feats.append(feats)
                continue

            token_positions = list(ctx.get("token_positions") or [])
            blocks = list(ctx.get("blocks") or [])
            n = min(len(token_positions), seq_len)
            if n == 0:
                batch_feats.append(feats)
                continue

            max_block_idx = max(len(blocks) - 1, 1)
            max_object_idx = 1
            for block in blocks:
                objects = block.get("objects") if isinstance(block, dict) else None
                if objects:
                    max_object_idx = max(max_object_idx, len(objects) - 1)

            for i in range(n):
                ann = token_positions[i] or {}
                x = ann.get("x")
                y = ann.get("y")
                if x is None or y is None:
                    continue

                x_f = float(x)
                y_f = float(y)
                block_index = ann.get("block_index")
                object_index = ann.get("object_index")
                anchor_xy = (0.0, 0.0)
                if (
                    block_index is not None
                    and 0 <= int(block_index) < len(blocks)
                    and isinstance(blocks[int(block_index)], dict)
                ):
                    anchor_xy = tuple(blocks[int(block_index)].get("anchor_xy") or (0.0, 0.0))

                rel_x = (x_f - float(anchor_xy[0])) / coord_scale
                rel_y = (y_f - float(anchor_xy[1])) / coord_scale
                feats[i, 0] = x_f / coord_scale
                feats[i, 1] = y_f / coord_scale
                feats[i, 2] = rel_x
                feats[i, 3] = rel_y
                feats[i, 4] = float(block_index) / float(max_block_idx) if block_index is not None else 0.0
                feats[i, 5] = float(object_index) / float(max_object_idx) if object_index is not None else 0.0
                feats[i, 6] = 1.0

            batch_feats.append(feats)

        return torch.stack(batch_feats, dim=0)

    def _build_relation_bias(
        self,
        relation_context: Optional[List[Optional[Dict[str, Any]]]],
        seq_len: int,
        device: torch.device,
    ) -> Optional[torch.Tensor]:
        if not relation_context:
            return None

        batch_bias: List[torch.Tensor] = []
        for ctx in relation_context:
            ctx = self._normalize_context(ctx)
            bias = torch.zeros((seq_len, seq_len), device=device)
            if not ctx:
                batch_bias.append(bias)
                continue

            token_positions = list(ctx.get("token_positions") or [])
            n = min(len(token_positions), seq_len)
            if n == 0:
                batch_bias.append(bias)
                continue

            coords = torch.full((seq_len, 2), float("nan"), device=device)
            block_ids = torch.full((seq_len,), -1, dtype=torch.long, device=device)
            object_ids = torch.full((seq_len,), -1, dtype=torch.long, device=device)

            for i in range(n):
                ann = token_positions[i] or {}
                x = ann.get("x")
                y = ann.get("y")
                if x is not None and y is not None:
                    coords[i, 0] = float(x)
                    coords[i, 1] = float(y)
                block_index = ann.get("block_index")
                object_index = ann.get("object_index")
                if block_index is not None:
                    block_ids[i] = int(block_index)
                if object_index is not None:
                    object_ids[i] = int(object_index)

            valid = torch.isfinite(coords[:, 0]) & torch.isfinite(coords[:, 1])
            safe_coords = torch.where(valid.unsqueeze(-1), coords, torch.zeros_like(coords))
            delta = safe_coords.unsqueeze(1) - safe_coords.unsqueeze(0)
            dx = torch.clamp(
                torch.round(delta[..., 0] / self.rel_cell_mm),
                -self.rel_max_bin,
                self.rel_max_bin,
            ).long() + self.rel_max_bin
            dy = torch.clamp(
                torch.round(delta[..., 1] / self.rel_cell_mm),
                -self.rel_max_bin,
                self.rel_max_bin,
            ).long() + self.rel_max_bin
            rel_idx = dx * self.rel_grid + dy

            pair_valid = valid.unsqueeze(1) & valid.unsqueeze(0)
            bias = self.rel_bias(rel_idx).squeeze(-1) * pair_valid.float()
            same_block = (block_ids.unsqueeze(1) >= 0) & (block_ids.unsqueeze(1) == block_ids.unsqueeze(0))
            same_object = (object_ids.unsqueeze(1) >= 0) & (object_ids.unsqueeze(1) == object_ids.unsqueeze(0))
            bias = bias + same_block.float() * self.same_block_bias
            bias = bias + same_object.float() * self.same_object_bias

            batch_bias.append(bias)

        return torch.stack(batch_bias, dim=0)

    def _hidden_from_idx(
        self,
        idx,
        relation_context: Optional[List[Optional[Dict[str, Any]]]] = None,
    ):
        """Return the final hidden states (B, T, C) for input token ids."""
        B, T = idx.shape
        tok_emb = self.token_embedding_table(idx)  # (B,T,C)
        pos_emb = self.position_embedding_table(torch.arange(T, device=idx.device))  # (T,C)
        x = tok_emb + pos_emb  # (B,T,C)

        coord_feats = self._build_coord_features(relation_context, T, idx.device)
        if coord_feats is not None:
            if self.training:
                if self.coord_feat_dropout > 0.0:
                    keep_prob = 1.0 - self.coord_feat_dropout
                    row_keep = (
                        torch.rand(coord_feats.size(0), coord_feats.size(1), 1, device=idx.device)
                        < keep_prob
                    ).to(coord_feats.dtype)
                    coord_feats = coord_feats * row_keep
            else:
                coord_feats = coord_feats.clone()
                coord_feats[:, :, 0:4] = 0.0
            x = x + self.coord_feature_proj(coord_feats)

        x = self.dropout(x)  # (B,T,C)

        attn_bias = self._build_relation_bias(relation_context, T, idx.device)
        for block in self.blocks:
            x = block(x, attn_bias=attn_bias)

        x = self.ln_f(x)  # (B,T,C)
        return x

    def _logits_from_idx(
        self,
        idx,
        relation_context: Optional[List[Optional[Dict[str, Any]]]] = None,
    ):
        """Return logits (B, T, vocab) for input token ids."""
        x = self._hidden_from_idx(idx, relation_context=relation_context)
        logits = self.lm_head(x)  # (B,T,vocab_size)
        return logits

    def forward(
        self,
        idx,
        targets=None,
        scheduled_sampling_prob: float = 0.0,
        label_smoothing: float = 0.0,
        relation_context: Optional[List[Optional[Dict[str, Any]]]] = None,
        return_hidden: bool = False,
    ):
        B, T = idx.shape
        logits_teacher = self._logits_from_idx(idx, relation_context=relation_context)
        hidden_teacher = None

        if self.training and scheduled_sampling_prob > 0.0 and targets is not None:
            idx2 = idx.clone()
            bern = torch.rand(B, T - 1, device=idx.device) < scheduled_sampling_prob
            pred = logits_teacher[:, :-1].argmax(dim=-1)
            idx2[:, 1:] = torch.where(bern, pred, idx[:, 1:])
            logits = self._logits_from_idx(idx2, relation_context=relation_context)
            if return_hidden:
                hidden_teacher = self._hidden_from_idx(idx2, relation_context=relation_context)
        else:
            logits = logits_teacher
            if return_hidden:
                hidden_teacher = self._hidden_from_idx(idx, relation_context=relation_context)

        if targets is None:
            loss = None
        else:
            _, _, C = logits.shape
            flat_logits = logits.reshape(B * T, C)
            flat_targets = targets.reshape(B * T)
            if label_smoothing > 0.0:
                loss = F.cross_entropy(
                    flat_logits,
                    flat_targets,
                    ignore_index=self.pad_idx,
                    label_smoothing=label_smoothing,
                )
            else:
                loss = F.cross_entropy(flat_logits, flat_targets, ignore_index=self.pad_idx)

        if return_hidden:
            return logits, loss, hidden_teacher
        return logits, loss

    def generate(
        self,
        idx,
        max_new_tokens,
        temperature=1.0,
        top_k=None,
        allowed_tokens=None,
        eos_id=None,
        pad_idx=None,
        relation_context: Optional[List[Optional[Dict[str, Any]]]] = None,
    ):
        """
        Generate new tokens given a context.

        allowed_tokens: optional callback taking a list of token ids (the sequence so far for one batch row)
        and returning a list of allowed token ids for the next step; see existing masking code below.
        """
        if relation_context is not None and not isinstance(relation_context, list):
            relation_context = [relation_context]

        for _ in range(max_new_tokens):
            idx_cond = idx[:, -self.block_size:]
            ctx = self._slice_relation_context(relation_context, idx_cond.size(1))
            logits, _ = self(idx_cond, relation_context=ctx)
            logits = logits[:, -1, :] / temperature

            # Mask out padding tokens
            if pad_idx is not None:
                logits[:, pad_idx] = float("-inf")

            if allowed_tokens:
                for b in range(idx.shape[0]):
                    allowed = allowed_tokens(idx[b].tolist())
                    if not allowed:
                        continue
                    mask = torch.full_like(logits[b], float("-inf"))
                    mask[allowed] = 0
                    logits[b] = logits[b] + mask

            if top_k is not None:
                v, _ = torch.topk(logits, min(top_k, logits.size(-1)))
                logits[logits < v[:, [-1]]] = -float("Inf")

            probs = F.softmax(logits, dim=-1)
            idx_next = torch.multinomial(probs, num_samples=1)
            idx = torch.cat((idx, idx_next), dim=1)

            if eos_id is not None and (idx_next == eos_id).all():
                break
        return idx


class Head(nn.Module):
    def __init__(self, n_embd, head_size, dropout, block_size):
        super().__init__()
        self.key = nn.Linear(n_embd, head_size, bias=False)
        self.query = nn.Linear(n_embd, head_size, bias=False)
        self.value = nn.Linear(n_embd, head_size, bias=False)
        self.register_buffer("tril", torch.tril(torch.ones(block_size, block_size)))
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, attn_bias=None):
        B, T, C = x.shape
        k = self.key(x)
        q = self.query(x)

        head_dim = k.size(-1)
        wei = q @ k.transpose(-2, -1) * (head_dim ** -0.5)
        if attn_bias is not None:
            wei = wei + attn_bias

        wei = wei.masked_fill(self.tril[:T, :T] == 0, float("-inf"))
        wei = F.softmax(wei, dim=-1)
        wei = self.dropout(wei)

        v = self.value(x)
        out = wei @ v
        return out


class MultiHeadAttention(nn.Module):
    def __init__(self, n_embd, n_head, dropout, block_size):
        super().__init__()
        head_size = n_embd // n_head
        self.heads = nn.ModuleList([Head(n_embd, head_size, dropout, block_size) for _ in range(n_head)])
        self.proj = nn.Linear(n_embd, n_embd)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, attn_bias=None):
        out = torch.cat([h(x, attn_bias=attn_bias) for h in self.heads], dim=-1)
        out = self.dropout(self.proj(out))
        return out


class FeedForward(nn.Module):
    def __init__(self, n_embd, dropout):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd),
            nn.ReLU(),
            nn.Linear(4 * n_embd, n_embd),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class Block(nn.Module):
    def __init__(self, n_embd, n_head, dropout, block_size):
        super().__init__()
        self.sa = MultiHeadAttention(n_embd, n_head, dropout, block_size)
        self.ffwd = FeedForward(n_embd, dropout)
        self.ln1 = nn.LayerNorm(n_embd)
        self.ln2 = nn.LayerNorm(n_embd)

    def forward(self, x, attn_bias=None):
        x = x + self.sa(self.ln1(x), attn_bias=attn_bias)
        x = x + self.ffwd(self.ln2(x))
        return x
