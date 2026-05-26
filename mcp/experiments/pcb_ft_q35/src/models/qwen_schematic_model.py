"""Wrapper model that fuses schematic graph tokens with Qwen text tokens."""

from __future__ import annotations

from typing import Any, Optional

import torch
from torch import nn

from .graph_encoder import GraphBatch, SchematicGraphEncoder
from .projector import GraphToQwenProjector


class QwenSchematicModel(nn.Module):
    """Compose graph encoder + projector + decoder into one forward path."""

    def __init__(
        self,
        qwen_decoder: nn.Module,
        graph_encoder: SchematicGraphEncoder,
        projector: GraphToQwenProjector,
    ) -> None:
        super().__init__()
        self.qwen_decoder = qwen_decoder
        self.graph_encoder = graph_encoder
        self.projector = projector

    def _get_text_embeds(
        self,
        input_ids: Optional[torch.Tensor],
        inputs_embeds: Optional[torch.Tensor],
    ) -> Optional[torch.Tensor]:
        if inputs_embeds is not None:
            return inputs_embeds
        if input_ids is None:
            return None
        if not hasattr(self.qwen_decoder, "get_input_embeddings"):
            raise ValueError("qwen_decoder must expose get_input_embeddings() when input_ids are used.")
        embedding_layer = self.qwen_decoder.get_input_embeddings()
        if embedding_layer is None:
            raise ValueError("qwen_decoder.get_input_embeddings() returned None.")
        return embedding_layer(input_ids)

    @staticmethod
    def _build_graph_attention_mask(
        node_mask: Optional[torch.Tensor],
        batch_size: int,
        num_graph_tokens: int,
        device: torch.device,
    ) -> torch.Tensor:
        if node_mask is None:
            return torch.ones(batch_size, num_graph_tokens, dtype=torch.long, device=device)
        if node_mask.shape != (batch_size, num_graph_tokens):
            raise ValueError("graph_batch.node_mask must have shape [B, N].")
        return node_mask.to(device=device).long()

    @staticmethod
    def _merge_labels(
        labels: Optional[torch.Tensor],
        batch_size: int,
        num_graph_tokens: int,
        text_seq_len: int,
        device: torch.device,
    ) -> Optional[torch.Tensor]:
        if labels is None:
            return None
        if labels.dim() != 2:
            return labels

        labels = labels.to(device=device)
        full_seq_len = num_graph_tokens + text_seq_len

        if labels.size(1) == full_seq_len:
            return labels
        if text_seq_len > 0 and labels.size(1) == text_seq_len:
            prefix = torch.full(
                (batch_size, num_graph_tokens),
                fill_value=-100,
                dtype=labels.dtype,
                device=device,
            )
            return torch.cat([prefix, labels], dim=1)
        return labels

    def forward(
        self,
        graph_batch: Optional[Any] = None,
        input_ids: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        labels: Optional[torch.Tensor] = None,
        inputs_embeds: Optional[torch.Tensor] = None,
        graph: Optional[Any] = None,
        graph_tokens: Optional[torch.Tensor] = None,
        **kwargs: Any,
    ) -> Any:
        """Forward pass.

        Flow:
        1) graph_batch -> graph_encoder -> [B, N, Hg]
        2) projector -> graph token embeds [B, N, Hq]
        3) concatenate with text embeds (if provided) on sequence axis
        4) pass to decoder via inputs_embeds
        """

        if graph_tokens is not None:
            graph_token_embeds = graph_tokens
        else:
            if graph_batch is None:
                graph_batch = graph
            if graph_batch is None:
                raise ValueError("graph_batch or graph_tokens must be provided.")
            graph_node_embeds = self.graph_encoder(graph_batch)  # [B, N, Hg]
            graph_token_embeds = self.projector(graph_node_embeds)  # [B, N, Hq]

        batch_size, num_graph_tokens, _ = graph_token_embeds.shape
        device = graph_token_embeds.device

        text_embeds = self._get_text_embeds(input_ids=input_ids, inputs_embeds=inputs_embeds)
        if text_embeds is not None:
            if text_embeds.size(0) != batch_size:
                raise ValueError("Text batch size must match graph batch size.")
            text_embeds = text_embeds.to(device=device)
            graph_token_embeds = graph_token_embeds.to(dtype=text_embeds.dtype)
            fused_embeds = torch.cat([graph_token_embeds, text_embeds], dim=1)
            text_seq_len = text_embeds.size(1)
        else:
            if hasattr(self.qwen_decoder, "get_input_embeddings"):
                embedding_layer = self.qwen_decoder.get_input_embeddings()
                if embedding_layer is not None and hasattr(embedding_layer, "weight"):
                    graph_token_embeds = graph_token_embeds.to(dtype=embedding_layer.weight.dtype)
            fused_embeds = graph_token_embeds
            text_seq_len = 0

        graph_node_mask = None
        if isinstance(graph_batch, GraphBatch):
            graph_node_mask = graph_batch.node_mask
        elif isinstance(graph_batch, dict):
            graph_node_mask = graph_batch.get("node_mask")

        graph_attn_mask = self._build_graph_attention_mask(
            node_mask=graph_node_mask,
            batch_size=batch_size,
            num_graph_tokens=num_graph_tokens,
            device=device,
        )

        if text_seq_len > 0:
            if attention_mask is None:
                text_attn_mask = torch.ones(batch_size, text_seq_len, dtype=graph_attn_mask.dtype, device=device)
            else:
                text_attn_mask = attention_mask.to(device=device, dtype=graph_attn_mask.dtype)

            if text_attn_mask.size(1) == text_seq_len:
                fused_attention_mask = torch.cat([graph_attn_mask, text_attn_mask], dim=1)
            elif text_attn_mask.size(1) == num_graph_tokens + text_seq_len:
                fused_attention_mask = text_attn_mask
            else:
                raise ValueError(
                    "attention_mask must match text length or fused graph+text length."
                )
        else:
            fused_attention_mask = graph_attn_mask

        fused_labels = self._merge_labels(
            labels=labels,
            batch_size=batch_size,
            num_graph_tokens=num_graph_tokens,
            text_seq_len=text_seq_len,
            device=device,
        )

        decoder_kwargs = dict(kwargs)
        decoder_kwargs.pop("input_ids", None)
        decoder_kwargs.pop("inputs_embeds", None)
        decoder_kwargs.pop("attention_mask", None)
        decoder_kwargs.pop("labels", None)

        decoder_kwargs["inputs_embeds"] = fused_embeds
        decoder_kwargs["attention_mask"] = fused_attention_mask
        if fused_labels is not None:
            decoder_kwargs["labels"] = fused_labels

        return self.qwen_decoder(**decoder_kwargs)
