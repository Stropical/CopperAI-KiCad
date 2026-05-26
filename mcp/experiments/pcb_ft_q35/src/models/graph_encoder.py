"""Graph encoder modules for schematic-style heterogeneous graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import torch
from torch import nn


@dataclass
class GraphBatch:
    """Batch container for graph tensors.

    Shapes:
    - node_features: [B, N, node_feature_dim]
    - edge_index: [2, E], [B, 2, E], or [B, E, 2]
    - edge_features: [E, edge_feature_dim] or [B, E, edge_feature_dim]
    - node_type_ids: [B, N]
    - edge_type_ids: [E] or [B, E]
    - node_mask: optional [B, N] (1/True for valid nodes)
    """

    node_features: torch.Tensor
    edge_index: torch.Tensor
    edge_features: torch.Tensor
    node_type_ids: torch.Tensor
    edge_type_ids: torch.Tensor
    node_mask: Optional[torch.Tensor] = None


class _GraphTransformerLayer(nn.Module):
    """Compact single-head graph transformer layer with typed edge messages."""

    def __init__(self, hidden_dim: int, dropout: float = 0.1) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.q_proj = nn.Linear(hidden_dim, hidden_dim)
        self.k_proj = nn.Linear(hidden_dim, hidden_dim)
        self.v_proj = nn.Linear(hidden_dim, hidden_dim)
        self.edge_proj = nn.Linear(hidden_dim, hidden_dim)

        self.norm1 = nn.LayerNorm(hidden_dim)
        self.norm2 = nn.LayerNorm(hidden_dim)
        self.dropout = nn.Dropout(dropout)
        self.ffn = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim * 4),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim * 4, hidden_dim),
        )

    def forward(
        self,
        node_states: torch.Tensor,
        edge_index: torch.Tensor,
        edge_states: torch.Tensor,
        node_mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        # node_states: [B, N, H], edge_index: [B, 2, E], edge_states: [B, E, H]
        batch_size, num_nodes, _ = node_states.shape
        scale = self.hidden_dim**-0.5
        updates = []

        for batch_idx in range(batch_size):
            src = edge_index[batch_idx, 0].long()
            dst = edge_index[batch_idx, 1].long()
            valid = (src >= 0) & (src < num_nodes) & (dst >= 0) & (dst < num_nodes)

            if node_mask is not None:
                active = node_mask[batch_idx].bool()
                valid = valid & active[src] & active[dst]

            if not torch.any(valid):
                updates.append(torch.zeros_like(node_states[batch_idx]))
                continue

            src = src[valid]
            dst = dst[valid]
            edge_b = edge_states[batch_idx, valid]

            q = self.q_proj(node_states[batch_idx, dst])  # [E_valid, H]
            k = self.k_proj(node_states[batch_idx, src])  # [E_valid, H]
            v = self.v_proj(node_states[batch_idx, src])  # [E_valid, H]

            logits = (q * k).sum(dim=-1) * scale
            attn = torch.sigmoid(logits)  # [E_valid]
            msg = (v + self.edge_proj(edge_b)) * attn.unsqueeze(-1)

            aggregate = torch.zeros_like(node_states[batch_idx])  # [N, H]
            aggregate.index_add_(0, dst, msg)

            degree = torch.zeros(
                num_nodes,
                dtype=node_states.dtype,
                device=node_states.device,
            )
            degree.index_add_(0, dst, torch.ones_like(attn, dtype=node_states.dtype))
            aggregate = aggregate / degree.clamp_min(1.0).unsqueeze(-1)
            updates.append(aggregate)

        message_update = torch.stack(updates, dim=0)
        hidden = self.norm1(node_states + self.dropout(message_update))
        out = self.norm2(hidden + self.dropout(self.ffn(hidden)))

        if node_mask is not None:
            out = out * node_mask.unsqueeze(-1).to(dtype=out.dtype)
        return out


class SchematicGraphEncoder(nn.Module):
    """Compact graph-transformer style encoder for schematic graphs."""

    def __init__(
        self,
        node_feature_dim: int,
        edge_feature_dim: int,
        hidden_dim: int = 384,
        num_layers: int = 2,
        num_node_types: int = 16,
        num_edge_types: int = 16,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.node_feature_dim = node_feature_dim
        self.edge_feature_dim = edge_feature_dim
        self.hidden_size = hidden_dim
        self.hidden_dim = hidden_dim
        self.node_in = nn.Linear(node_feature_dim, hidden_dim)
        self.edge_in = nn.Linear(edge_feature_dim, hidden_dim)
        self.node_type_emb = nn.Embedding(num_node_types, hidden_dim)
        self.edge_type_emb = nn.Embedding(num_edge_types, hidden_dim)
        self.layers = nn.ModuleList(
            [_GraphTransformerLayer(hidden_dim=hidden_dim, dropout=dropout) for _ in range(num_layers)]
        )
        self.final_norm = nn.LayerNorm(hidden_dim)

    @staticmethod
    def _normalize_edge_index(edge_index: torch.Tensor, batch_size: int) -> torch.Tensor:
        if edge_index.dim() == 2 and edge_index.size(0) == 2:
            return edge_index.unsqueeze(0).expand(batch_size, -1, -1)
        if edge_index.dim() == 3 and edge_index.size(1) == 2 and edge_index.size(0) == batch_size:
            return edge_index
        if edge_index.dim() == 3 and edge_index.size(1) == 2 and edge_index.size(0) == 1:
            return edge_index.expand(batch_size, -1, -1)
        if edge_index.dim() == 3 and edge_index.size(2) == 2 and edge_index.size(0) == batch_size:
            return edge_index.transpose(1, 2)
        if edge_index.dim() == 3 and edge_index.size(2) == 2 and edge_index.size(0) == 1:
            return edge_index.expand(batch_size, -1, -1).transpose(1, 2)
        raise ValueError("edge_index must have shape [2,E], [B,2,E], or [B,E,2].")

    @staticmethod
    def _normalize_edge_features(edge_features: torch.Tensor, batch_size: int) -> torch.Tensor:
        if edge_features.dim() == 2:
            return edge_features.unsqueeze(0).expand(batch_size, -1, -1)
        if edge_features.dim() == 3 and edge_features.size(0) == batch_size:
            return edge_features
        if edge_features.dim() == 3 and edge_features.size(0) == 1:
            return edge_features.expand(batch_size, -1, -1)
        raise ValueError("edge_features must have shape [E,D] or [B,E,D].")

    @staticmethod
    def _normalize_edge_type_ids(edge_type_ids: torch.Tensor, batch_size: int) -> torch.Tensor:
        if edge_type_ids.dim() == 1:
            return edge_type_ids.unsqueeze(0).expand(batch_size, -1)
        if edge_type_ids.dim() == 2 and edge_type_ids.size(0) == batch_size:
            return edge_type_ids
        if edge_type_ids.dim() == 2 and edge_type_ids.size(0) == 1:
            return edge_type_ids.expand(batch_size, -1)
        raise ValueError("edge_type_ids must have shape [E] or [B,E].")

    def _encode_graph_batch(self, graph_batch: GraphBatch) -> torch.Tensor:
        node_features = graph_batch.node_features
        batch_size, num_nodes, _ = node_features.shape

        edge_index = self._normalize_edge_index(graph_batch.edge_index, batch_size)
        edge_features = self._normalize_edge_features(graph_batch.edge_features, batch_size)
        edge_type_ids = self._normalize_edge_type_ids(graph_batch.edge_type_ids, batch_size).long()

        if edge_features.dim() != 3 or edge_type_ids.dim() != 2:
            raise ValueError("edge_features and edge_type_ids must broadcast to [B,E,*] and [B,E].")
        if edge_features.size(1) != edge_index.size(2) or edge_type_ids.size(1) != edge_index.size(2):
            raise ValueError("edge feature/type dimensions must match edge_index edge count.")

        node_states = self.node_in(node_features) + self.node_type_emb(graph_batch.node_type_ids.long())
        edge_states = self.edge_in(edge_features) + self.edge_type_emb(edge_type_ids)

        if graph_batch.node_mask is not None:
            node_mask = graph_batch.node_mask
            if node_mask.shape != (batch_size, num_nodes):
                raise ValueError("node_mask must have shape [B,N].")
            node_states = node_states * node_mask.unsqueeze(-1).to(dtype=node_states.dtype)
        else:
            node_mask = None

        for layer in self.layers:
            node_states = layer(
                node_states=node_states,
                edge_index=edge_index,
                edge_states=edge_states,
                node_mask=node_mask,
            )

        encoded = self.final_norm(node_states)
        if node_mask is not None:
            encoded = encoded * node_mask.unsqueeze(-1).to(dtype=encoded.dtype)
        return encoded

    def _pack_nodes(
        self,
        node_features: torch.Tensor,
        batch: Optional[torch.Tensor],
        node_type_ids: Optional[torch.Tensor],
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        if node_features.dim() == 3:
            bsz, num_nodes, _ = node_features.shape
            mask = torch.ones(bsz, num_nodes, dtype=torch.bool, device=node_features.device)
            if node_type_ids is None:
                type_ids = torch.zeros(bsz, num_nodes, dtype=torch.long, device=node_features.device)
            elif node_type_ids.dim() == 2:
                type_ids = node_type_ids.long()
            elif node_type_ids.dim() == 1:
                type_ids = node_type_ids.unsqueeze(0).expand(bsz, -1).long()
            else:
                raise ValueError("node_type_ids must have shape [N] or [B,N].")
            flat_to_local = torch.arange(num_nodes, device=node_features.device)
            return node_features, type_ids, mask, flat_to_local

        if node_features.dim() != 2:
            raise ValueError("node_features must have shape [N,D] or [B,N,D].")

        num_nodes, feat_dim = node_features.shape
        if batch is None:
            dense_nodes = node_features.unsqueeze(0)
            dense_mask = torch.ones(1, num_nodes, dtype=torch.bool, device=node_features.device)
            if node_type_ids is None:
                dense_types = torch.zeros(1, num_nodes, dtype=torch.long, device=node_features.device)
            else:
                dense_types = node_type_ids.view(1, -1).long()
            flat_to_local = torch.arange(num_nodes, device=node_features.device)
            return dense_nodes, dense_types, dense_mask, flat_to_local

        batch = batch.long().to(device=node_features.device)
        if batch.numel() != num_nodes:
            raise ValueError("batch must have shape [N] for flat node features.")

        batch_size = int(batch.max().item()) + 1 if batch.numel() else 1
        counts = torch.bincount(batch, minlength=batch_size)
        max_nodes = int(counts.max().item()) if counts.numel() else 0

        dense_nodes = torch.zeros(
            batch_size,
            max_nodes,
            feat_dim,
            dtype=node_features.dtype,
            device=node_features.device,
        )
        dense_mask = torch.zeros(batch_size, max_nodes, dtype=torch.bool, device=node_features.device)
        dense_types = torch.zeros(batch_size, max_nodes, dtype=torch.long, device=node_features.device)
        flat_to_local = torch.full((num_nodes,), -1, dtype=torch.long, device=node_features.device)

        node_type_flat: Optional[torch.Tensor]
        if node_type_ids is None:
            node_type_flat = None
        elif node_type_ids.dim() == 1:
            node_type_flat = node_type_ids.long().to(device=node_features.device)
        else:
            node_type_flat = None

        for b in range(batch_size):
            idx = torch.nonzero(batch == b, as_tuple=False).squeeze(-1)
            if idx.numel() == 0:
                continue
            local = torch.arange(idx.numel(), device=node_features.device)
            dense_nodes[b, : idx.numel()] = node_features[idx]
            dense_mask[b, : idx.numel()] = True
            flat_to_local[idx] = local
            if node_type_flat is not None:
                dense_types[b, : idx.numel()] = node_type_flat[idx]
        return dense_nodes, dense_types, dense_mask, flat_to_local

    def _pack_edges(
        self,
        edge_index: Optional[torch.Tensor],
        edge_features: Optional[torch.Tensor],
        edge_type_ids: Optional[torch.Tensor],
        batch: Optional[torch.Tensor],
        flat_to_local: torch.Tensor,
        batch_size: int,
        device: torch.device,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        if edge_index is None:
            return (
                torch.zeros(batch_size, 2, 0, dtype=torch.long, device=device),
                torch.zeros(batch_size, 0, self.edge_feature_dim, dtype=torch.float32, device=device),
                torch.zeros(batch_size, 0, dtype=torch.long, device=device),
            )

        if edge_index.dim() == 3:
            edge_idx = edge_index.long().to(device=device)
            if edge_idx.size(0) == 1 and batch_size > 1:
                edge_idx = edge_idx.expand(batch_size, -1, -1)
            if edge_features is None:
                efeat = torch.zeros(
                    edge_idx.size(0),
                    edge_idx.size(2),
                    self.edge_feature_dim,
                    dtype=torch.float32,
                    device=device,
                )
            else:
                efeat = edge_features.to(device=device)
                if efeat.dim() == 2:
                    efeat = efeat.unsqueeze(0).expand(edge_idx.size(0), -1, -1)
            if edge_type_ids is None:
                etype = torch.zeros(edge_idx.size(0), edge_idx.size(2), dtype=torch.long, device=device)
            else:
                etype = edge_type_ids.to(device=device)
                if etype.dim() == 1:
                    etype = etype.unsqueeze(0).expand(edge_idx.size(0), -1)
            return edge_idx, efeat, etype.long()

        if edge_index.dim() != 2 or edge_index.size(0) != 2:
            raise ValueError("edge_index must have shape [2,E], [B,2,E], or [B,E,2].")

        edge_idx_flat = edge_index.long().to(device=device)
        num_edges = edge_idx_flat.size(1)
        if edge_features is None:
            edge_feat_flat = torch.zeros(
                num_edges,
                self.edge_feature_dim,
                dtype=torch.float32,
                device=device,
            )
        else:
            edge_feat_flat = edge_features.to(device=device)
        if edge_type_ids is None:
            edge_type_flat = torch.zeros(num_edges, dtype=torch.long, device=device)
        else:
            edge_type_flat = edge_type_ids.view(-1).long().to(device=device)

        if batch is None or batch_size == 1:
            return (
                edge_idx_flat.unsqueeze(0),
                edge_feat_flat.unsqueeze(0),
                edge_type_flat.unsqueeze(0),
            )

        batch = batch.long().to(device=device)
        src_flat = edge_idx_flat[0]
        dst_flat = edge_idx_flat[1]
        valid_global = (
            (src_flat >= 0)
            & (src_flat < batch.numel())
            & (dst_flat >= 0)
            & (dst_flat < batch.numel())
        )
        same_batch = valid_global & (batch[src_flat] == batch[dst_flat])

        per_batch_indices: list[torch.Tensor] = []
        max_edges = 0
        for b in range(batch_size):
            idx = torch.nonzero(same_batch & (batch[src_flat] == b), as_tuple=False).squeeze(-1)
            per_batch_indices.append(idx)
            max_edges = max(max_edges, int(idx.numel()))

        packed_edge_idx = torch.full((batch_size, 2, max_edges), -1, dtype=torch.long, device=device)
        packed_edge_feat = torch.zeros(
            batch_size,
            max_edges,
            self.edge_feature_dim,
            dtype=edge_feat_flat.dtype,
            device=device,
        )
        packed_edge_type = torch.zeros(batch_size, max_edges, dtype=torch.long, device=device)

        for b, idx in enumerate(per_batch_indices):
            if idx.numel() == 0:
                continue
            src_local = flat_to_local[src_flat[idx]]
            dst_local = flat_to_local[dst_flat[idx]]
            edge_valid = (src_local >= 0) & (dst_local >= 0)
            src_local = src_local[edge_valid]
            dst_local = dst_local[edge_valid]
            if src_local.numel() == 0:
                continue
            n = src_local.numel()
            packed_edge_idx[b, 0, :n] = src_local
            packed_edge_idx[b, 1, :n] = dst_local
            packed_edge_feat[b, :n] = edge_feat_flat[idx][edge_valid]
            packed_edge_type[b, :n] = edge_type_flat[idx][edge_valid]
        return packed_edge_idx, packed_edge_feat, packed_edge_type

    def _coerce_graph_batch(
        self,
        graph_batch: Optional[Any],
        edge_index: Optional[torch.Tensor],
        edge_type: Optional[torch.Tensor],
        batch: Optional[torch.Tensor],
        kwargs: dict[str, Any],
    ) -> GraphBatch:
        if isinstance(graph_batch, GraphBatch):
            return graph_batch

        graph_dict: dict[str, Any] = {}
        if isinstance(graph_batch, dict):
            graph_dict.update(graph_batch)
        if isinstance(kwargs.get("graph"), dict):
            graph_dict.update(kwargs["graph"])

        node_features = kwargs.pop("node_features", None)
        if node_features is None:
            node_features = kwargs.pop("x", None)
        if node_features is None:
            node_features = graph_dict.get("node_features", graph_dict.get("x"))
        if node_features is None and torch.is_tensor(graph_batch):
            node_features = graph_batch
        if node_features is None:
            raise ValueError("node_features are required.")
        node_features = node_features.to(dtype=torch.float32)

        node_type_ids = kwargs.pop("node_type_ids", None)
        if node_type_ids is None:
            node_type_ids = graph_dict.get("node_type_ids", graph_dict.get("node_types"))

        if edge_index is None:
            edge_index = kwargs.pop("edge_index", None)
        if edge_index is None:
            edge_index = graph_dict.get("edge_index")

        edge_features = kwargs.pop("edge_features", None)
        if edge_features is None:
            edge_features = graph_dict.get("edge_features")

        edge_type_ids = kwargs.pop("edge_type_ids", None)
        if edge_type_ids is None:
            edge_type_ids = edge_type
        if edge_type_ids is None:
            edge_type_ids = kwargs.pop("edge_type", None)
        if edge_type_ids is None:
            edge_type_ids = graph_dict.get("edge_type_ids", graph_dict.get("edge_type"))

        if batch is None:
            batch = kwargs.pop("batch", None)
        if batch is None:
            batch = graph_dict.get("batch")

        node_mask = kwargs.pop("node_mask", None)
        if node_mask is None:
            node_mask = graph_dict.get("node_mask")

        packed_nodes, packed_node_types, packed_mask, flat_to_local = self._pack_nodes(
            node_features=node_features,
            batch=batch,
            node_type_ids=node_type_ids,
        )
        packed_edges, packed_edge_features, packed_edge_types = self._pack_edges(
            edge_index=edge_index,
            edge_features=edge_features,
            edge_type_ids=edge_type_ids,
            batch=batch,
            flat_to_local=flat_to_local,
            batch_size=packed_nodes.size(0),
            device=packed_nodes.device,
        )

        if node_mask is None:
            node_mask = packed_mask
        else:
            node_mask = node_mask.to(device=packed_nodes.device).bool()
            if node_mask.dim() == 1:
                node_mask = node_mask.unsqueeze(0)

        return GraphBatch(
            node_features=packed_nodes,
            edge_index=packed_edges,
            edge_features=packed_edge_features,
            node_type_ids=packed_node_types,
            edge_type_ids=packed_edge_types,
            node_mask=node_mask,
        )

    def forward(
        self,
        graph_batch: Optional[Any] = None,
        edge_index: Optional[torch.Tensor] = None,
        edge_type: Optional[torch.Tensor] = None,
        batch: Optional[torch.Tensor] = None,
        **kwargs: Any,
    ) -> torch.Tensor:
        """Encode graph nodes.

        Supports GraphBatch input, dictionary input, or flat tensors.
        Returns node embeddings with shape [B, N, hidden_dim].
        """

        coerced = self._coerce_graph_batch(
            graph_batch=graph_batch,
            edge_index=edge_index,
            edge_type=edge_type,
            batch=batch,
            kwargs=kwargs,
        )
        return self._encode_graph_batch(coerced)
