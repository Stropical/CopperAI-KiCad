"""Graph-conditioned causal LM + schematic graph encoder integration.

This module builds a decoder-only causal LM variant where schematic graph tokens
replace image/vision features in the conditioning path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

import torch
from torch import nn

from .graph_encoder import SchematicGraphEncoder
from .projector import GraphToQwenProjector
from .qwen_schematic_model import QwenSchematicModel


class Qwen35GraphModel(nn.Module):
    """Graph-conditioned Qwen model wrapper.

    The wrapper prepends projected graph tokens to text token embeddings and
    forwards the fused sequence into the Qwen decoder.
    """

    def __init__(
        self,
        decoder: nn.Module,
        graph_encoder: Optional[SchematicGraphEncoder] = None,
        projector: Optional[GraphToQwenProjector] = None,
        tokenizer: Optional[Any] = None,
        node_feature_dim: int = 32,
        edge_feature_dim: int = 16,
        graph_hidden_dim: int = 384,
        graph_layers: int = 6,
        projector_hidden_dim: int = 1024,
    ) -> None:
        super().__init__()
        self.decoder = decoder
        self.tokenizer = tokenizer

        qwen_hidden = self._decoder_hidden_size(decoder)
        self.graph_encoder = graph_encoder or SchematicGraphEncoder(
            node_feature_dim=node_feature_dim,
            edge_feature_dim=edge_feature_dim,
            hidden_dim=graph_hidden_dim,
            num_layers=graph_layers,
        )
        self.projector = projector or GraphToQwenProjector(
            in_dim=self.graph_encoder.hidden_size,
            out_dim=qwen_hidden,
            hidden_dim=projector_hidden_dim,
        )

        self.model = QwenSchematicModel(
            qwen_decoder=self.decoder,
            graph_encoder=self.graph_encoder,
            projector=self.projector,
        )

        # Disable vision pathways when present so graph path is the only
        # multimodal conditioner.
        self._disable_vision_modules()

    @staticmethod
    def _decoder_hidden_size(decoder: nn.Module) -> int:
        def _try_config(cfg: Any) -> Optional[int]:
            if cfg is None:
                return None
            for attr in ("hidden_size", "n_embd", "d_model"):
                if hasattr(cfg, attr):
                    value = getattr(cfg, attr)
                    if isinstance(value, int) and value > 0:
                        return value
            return None

        config = getattr(decoder, "config", None)
        out = _try_config(config)
        if out is not None:
            return out
        for base_attr in ("model", "base_model"):
            base = getattr(decoder, base_attr, None)
            if base is not None:
                out = _try_config(getattr(base, "config", None))
                if out is not None:
                    return out

        embed_getter = getattr(decoder, "get_input_embeddings", None)
        if callable(embed_getter):
            emb = embed_getter()
            if emb is not None and hasattr(emb, "embedding_dim"):
                value = getattr(emb, "embedding_dim")
                if isinstance(value, int) and value > 0:
                    return value

        raise ValueError("Unable to infer decoder hidden size.")

    def _disable_vision_modules(self) -> None:
        for attr in (
            "visual",
            "vision_tower",
            "vision_model",
            "multi_modal_projector",
            "image_tower",
        ):
            if hasattr(self.decoder, attr):
                try:
                    setattr(self.decoder, attr, None)
                except Exception:
                    # Some model implementations guard attributes; best effort.
                    pass

    @staticmethod
    def _augment_model_load_error(exc: Exception, model_name_or_path: str) -> Exception:
        message = str(exc)
        lowered = message.lower()
        if "gated repo" in lowered or "access to model" in lowered or "401 client error" in lowered:
            return RuntimeError(
                f"Failed to load {model_name_or_path}: the repository is gated on Hugging Face. "
                "Authenticate first with a token that has access, for example via "
                "`huggingface-cli login` or `HF_TOKEN=...`."
            )
        return exc

    @classmethod
    def from_pretrained(
        cls,
        model_name_or_path: str = "google/gemma-3-270m",
        *,
        node_feature_dim: int = 32,
        edge_feature_dim: int = 16,
        graph_hidden_dim: int = 384,
        graph_layers: int = 6,
        projector_hidden_dim: int = 1024,
        dtype: Optional[torch.dtype] = None,
        device_map: Optional[Any] = None,
        trust_remote_code: bool = True,
        use_unsloth: bool = False,
        load_in_4bit: bool = False,
        load_in_16bit: bool = False,
        max_seq_length: int = 2048,
        lora_r: int = 16,
        lora_alpha: int = 16,
        lora_dropout: float = 0.0,
        lora_target_modules: Optional[List[str]] = None,
        use_gradient_checkpointing: str = "unsloth",
        **kwargs: Any,
    ) -> "Qwen35GraphModel":
        """Load a decoder model and attach graph encoder/projector modules.

        When use_unsloth=True or when load_in_4bit/load_in_16bit is True, the decoder
        is loaded via Unsloth with optional 4-bit (QLoRA) or 16-bit LoRA.
        """
        use_unsloth_path = use_unsloth or load_in_4bit or load_in_16bit

        if use_unsloth_path:
            try:
                from unsloth import FastLanguageModel
            except ImportError as exc:
                raise RuntimeError(
                    "unsloth is required for use_unsloth/load_in_4bit/load_in_16bit. "
                    "Install with: pip install unsloth"
                ) from exc

            decoder, tokenizer = FastLanguageModel.from_pretrained(
                model_name=model_name_or_path,
                max_seq_length=max_seq_length,
                load_in_4bit=load_in_4bit,
                load_in_16bit=load_in_16bit,
                trust_remote_code=trust_remote_code,
                **{k: v for k, v in kwargs.items() if k not in ("torch_dtype",)},
            )
            target_modules = lora_target_modules or [
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj",
            ]
            decoder = FastLanguageModel.get_peft_model(
                decoder,
                r=lora_r,
                lora_alpha=lora_alpha,
                lora_dropout=lora_dropout,
                target_modules=target_modules,
                bias="none",
                use_gradient_checkpointing=use_gradient_checkpointing,
                random_state=3407,
                max_seq_length=max_seq_length,
            )
        else:
            try:
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except Exception as exc:  # pragma: no cover
                raise RuntimeError(
                    "transformers is required for Qwen35GraphModel.from_pretrained()."
                ) from exc

            try:
                decoder = AutoModelForCausalLM.from_pretrained(
                    model_name_or_path,
                    torch_dtype=dtype,
                    device_map=device_map,
                    trust_remote_code=trust_remote_code,
                    **kwargs,
                )
                tokenizer = AutoTokenizer.from_pretrained(
                    model_name_or_path,
                    trust_remote_code=trust_remote_code,
                )
            except Exception as exc:
                raise cls._augment_model_load_error(exc, model_name_or_path) from exc

        graph_encoder = SchematicGraphEncoder(
            node_feature_dim=node_feature_dim,
            edge_feature_dim=edge_feature_dim,
            hidden_dim=graph_hidden_dim,
            num_layers=graph_layers,
        )
        qwen_hidden = cls._decoder_hidden_size(decoder)
        projector = GraphToQwenProjector(
            in_dim=graph_hidden_dim,
            out_dim=qwen_hidden,
            hidden_dim=projector_hidden_dim,
        )

        instance = cls(
            decoder=decoder,
            graph_encoder=graph_encoder,
            projector=projector,
            tokenizer=tokenizer,
        )
        if use_unsloth_path:
            instance._base_model_name_or_path = model_name_or_path
            instance._load_in_4bit = load_in_4bit
            instance._load_in_16bit = load_in_16bit
            instance._lora_r = lora_r
            instance._max_seq_length = max_seq_length
        else:
            instance._base_model_name_or_path = None
            instance._load_in_4bit = False
            instance._load_in_16bit = False
            instance._lora_r = None
            instance._max_seq_length = None
        return instance

    def freeze_decoder(self) -> None:
        for param in self.decoder.parameters():
            param.requires_grad = False

    def unfreeze_decoder(self) -> None:
        for param in self.decoder.parameters():
            param.requires_grad = True

    def trainable_parameter_groups(self) -> dict[str, list[nn.Parameter]]:
        return {
            "graph_encoder": [p for p in self.graph_encoder.parameters() if p.requires_grad],
            "projector": [p for p in self.projector.parameters() if p.requires_grad],
            "decoder": [p for p in self.decoder.parameters() if p.requires_grad],
        }

    def forward(
        self,
        graph_batch: Optional[Any] = None,
        input_ids: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        labels: Optional[torch.Tensor] = None,
        inputs_embeds: Optional[torch.Tensor] = None,
        graph_tokens: Optional[torch.Tensor] = None,
        **kwargs: Any,
    ) -> Any:
        return self.model(
            graph_batch=graph_batch,
            input_ids=input_ids,
            attention_mask=attention_mask,
            labels=labels,
            inputs_embeds=inputs_embeds,
            graph_tokens=graph_tokens,
            **kwargs,
        )

    @torch.no_grad()
    def generate(
        self,
        graph_batch: Optional[Any] = None,
        input_ids: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        graph_tokens: Optional[torch.Tensor] = None,
        **generate_kwargs: Any,
    ) -> Any:
        """Generate with prepended graph token embeddings."""
        if graph_tokens is None:
            if graph_batch is None:
                raise ValueError("graph_batch or graph_tokens must be provided for generation.")
            graph_tokens = self.projector(self.graph_encoder(graph_batch))

        if input_ids is None and "inputs_embeds" not in generate_kwargs:
            raise ValueError("input_ids or inputs_embeds must be provided for generation.")

        if "inputs_embeds" in generate_kwargs:
            text_embeds = generate_kwargs.pop("inputs_embeds")
        else:
            embed = self.decoder.get_input_embeddings()
            if embed is None:
                raise ValueError("Decoder does not expose input embeddings.")
            text_embeds = embed(input_ids)

        fused_embeds = torch.cat([graph_tokens, text_embeds], dim=1)
        # Match decoder dtype (e.g. bfloat16 when decoder is half-precision) to avoid runtime errors
        decoder_dtype = next(self.decoder.parameters(), torch.tensor(0)).dtype
        if decoder_dtype in (torch.float16, torch.bfloat16) and fused_embeds.dtype != decoder_dtype:
            fused_embeds = fused_embeds.to(decoder_dtype)

        batch_size = fused_embeds.size(0)
        graph_len = graph_tokens.size(1)
        device = fused_embeds.device

        if attention_mask is None:
            text_len = text_embeds.size(1)
            text_mask = torch.ones(batch_size, text_len, dtype=torch.long, device=device)
        else:
            text_mask = attention_mask.to(device=device, dtype=torch.long)

        graph_mask = torch.ones(batch_size, graph_len, dtype=torch.long, device=device)
        fused_attention_mask = torch.cat([graph_mask, text_mask], dim=1)

        if not hasattr(self.decoder, "generate"):
            raise ValueError("Decoder does not implement generate().")
        return self.decoder.generate(
            inputs_embeds=fused_embeds,
            attention_mask=fused_attention_mask,
            **generate_kwargs,
        )

    def save_pretrained(self, output_dir: str | Path) -> None:
        """Save decoder, tokenizer, and graph modules."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        decoder_dir = output_path / "decoder"
        decoder_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(self.decoder, "save_pretrained"):
            self.decoder.save_pretrained(decoder_dir)
        else:
            torch.save(self.decoder.state_dict(), decoder_dir / "pytorch_model.bin")

        if self.tokenizer is not None and hasattr(self.tokenizer, "save_pretrained"):
            self.tokenizer.save_pretrained(output_path / "tokenizer")

        torch.save(self.graph_encoder.state_dict(), output_path / "graph_encoder.pt")
        torch.save(self.projector.state_dict(), output_path / "graph_projector.pt")

        meta = {
            "model_type": "qwen35_graph",
            "decoder_path": "decoder",
            "node_feature_dim": int(getattr(self.graph_encoder, "node_feature_dim", 32)),
            "edge_feature_dim": int(getattr(self.graph_encoder, "edge_feature_dim", 16)),
            "graph_encoder_hidden": int(getattr(self.graph_encoder, "hidden_size", 384)),
            "graph_encoder_layers": int(len(getattr(self.graph_encoder, "layers", []))),
        }
        if getattr(self, "_base_model_name_or_path", None) is not None:
            meta["use_unsloth"] = True
            meta["base_model_name_or_path"] = self._base_model_name_or_path
            meta["load_in_4bit"] = getattr(self, "_load_in_4bit", False)
            meta["load_in_16bit"] = getattr(self, "_load_in_16bit", False)
            meta["lora_r"] = getattr(self, "_lora_r", None)
            meta["max_seq_length"] = getattr(self, "_max_seq_length", None)
        (output_path / "graph_model_config.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @classmethod
    def load_pretrained(
        cls,
        model_dir: str | Path,
        *,
        map_location: Optional[str | torch.device] = None,
        trust_remote_code: bool = True,
    ) -> "Qwen35GraphModel":
        """Reload a model saved by `save_pretrained`."""
        model_path = Path(model_dir)
        decoder_dir = model_path / "decoder"
        meta_path = model_path / "graph_model_config.json"
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        use_unsloth = bool(meta.get("use_unsloth", False))
        adapter_config_path = decoder_dir / "adapter_config.json"
        if use_unsloth or adapter_config_path.exists():
            try:
                from unsloth import FastLanguageModel
            except ImportError as exc:
                raise RuntimeError(
                    "unsloth is required to load this checkpoint (saved with Unsloth/PEFT). "
                    "Install with: pip install unsloth"
                ) from exc
            base_name = meta.get("base_model_name_or_path")
            if not base_name:
                raise ValueError(
                    "graph_model_config.json must contain base_model_name_or_path when use_unsloth is true."
                )
            load_in_4bit = bool(meta.get("load_in_4bit", False))
            load_in_16bit = bool(meta.get("load_in_16bit", True))
            max_seq_length = int(meta.get("max_seq_length", 2048))
            decoder, tokenizer = FastLanguageModel.from_pretrained(
                model_name=base_name,
                max_seq_length=max_seq_length,
                load_in_4bit=load_in_4bit,
                load_in_16bit=load_in_16bit,
                trust_remote_code=trust_remote_code,
            )
            from peft import PeftModel
            from transformers import AutoTokenizer
            decoder = PeftModel.from_pretrained(decoder, str(decoder_dir), is_trainable=True)
            tok_dir = model_path / "tokenizer"
            if tok_dir.exists():
                tokenizer = AutoTokenizer.from_pretrained(str(tok_dir), trust_remote_code=trust_remote_code)
            else:
                tokenizer = tokenizer  # keep from FastLanguageModel.from_pretrained
        else:
            try:
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except Exception as exc:  # pragma: no cover
                raise RuntimeError(
                    "transformers is required for Qwen35GraphModel.load_pretrained()."
                ) from exc
            decoder = AutoModelForCausalLM.from_pretrained(
                decoder_dir,
                trust_remote_code=trust_remote_code,
                local_files_only=True,
            )
            tokenizer = None
            tok_dir = model_path / "tokenizer"
            if tok_dir.exists():
                tokenizer = AutoTokenizer.from_pretrained(
                    str(tok_dir), trust_remote_code=trust_remote_code
                )

        node_feature_dim = int(meta.get("node_feature_dim", 32))
        edge_feature_dim = int(meta.get("edge_feature_dim", 16))
        graph_hidden_dim = int(meta.get("graph_encoder_hidden", 384))
        graph_layers = int(meta.get("graph_encoder_layers", 6))

        model = cls(
            decoder=decoder,
            tokenizer=tokenizer,
            node_feature_dim=node_feature_dim,
            edge_feature_dim=edge_feature_dim,
            graph_hidden_dim=graph_hidden_dim,
            graph_layers=graph_layers,
        )
        if use_unsloth or adapter_config_path.exists():
            model._base_model_name_or_path = meta.get("base_model_name_or_path")
            model._load_in_4bit = meta.get("load_in_4bit", False)
            model._load_in_16bit = meta.get("load_in_16bit", True)
            model._lora_r = meta.get("lora_r")
            model._max_seq_length = meta.get("max_seq_length")
        else:
            model._base_model_name_or_path = None
            model._load_in_4bit = False
            model._load_in_16bit = False
            model._lora_r = None
            model._max_seq_length = None

        encoder_path = model_path / "graph_encoder.pt"
        projector_path = model_path / "graph_projector.pt"
        if encoder_path.exists():
            state = torch.load(encoder_path, map_location=map_location)
            model.graph_encoder.load_state_dict(state, strict=False)
        if projector_path.exists():
            state = torch.load(projector_path, map_location=map_location)
            model.projector.load_state_dict(state, strict=False)

        return model


__all__ = ["Qwen35GraphModel"]
