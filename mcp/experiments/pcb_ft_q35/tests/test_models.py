import importlib
import inspect
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")
nn = torch.nn


def _import_first(module_names):
    last_error = None
    for name in module_names:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            last_error = exc
    raise AssertionError(f"Could not import any of {module_names}: {last_error}")


def _resolve_class(module, preferred_names, contains_hint):
    for name in preferred_names:
        obj = getattr(module, name, None)
        if inspect.isclass(obj):
            return obj
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if contains_hint in name.lower():
            return obj
    raise AssertionError(
        f"No class found in {module.__name__} for names={preferred_names} hint={contains_hint}"
    )


def _make_config():
    return SimpleNamespace(
        hidden_size=384,
        d_model=384,
        input_dim=32,
        node_feature_dim=32,
        encoder_hidden_size=384,
        decoder_hidden_size=1024,
        output_dim=1024,
        out_dim=1024,
        num_layers=2,
        num_heads=4,
        dropout=0.0,
    )


def _default_value_for_param(name):
    n = name.lower()
    if "decoder" in n and "hidden" in n:
        return 1024
    if "hidden" in n or "d_model" in n or "model_dim" in n or "embed_dim" in n:
        return 384
    if "input" in n or "in_dim" in n or "feature_dim" in n:
        return 32
    if "output" in n or "out_dim" in n or "target_dim" in n:
        return 1024
    if "layer" in n or "depth" in n:
        return 2
    if "head" in n:
        return 4
    if "dropout" in n:
        return 0.0
    if "bool" in n or n.startswith("use_") or n.startswith("enable_"):
        return True
    if "config" in n:
        return _make_config()
    if "device" in n:
        return "cpu"
    return 1


def _instantiate_class(cls, overrides=None):
    overrides = overrides or {}
    attempts = []

    init_sig = inspect.signature(cls.__init__)
    inferred = {}
    for p in list(init_sig.parameters.values())[1:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.name in overrides:
            inferred[p.name] = overrides[p.name]
            continue
        if p.default is not inspect._empty:
            continue
        inferred[p.name] = _default_value_for_param(p.name)

    attempts.append(inferred)
    attempts.append(overrides)
    attempts.append({})
    attempts.append({"config": _make_config()})

    last_error = None
    for kwargs in attempts:
        try:
            return cls(**kwargs)
        except Exception as exc:  # pragma: no cover - exercised via fallback attempts
            last_error = exc
    raise AssertionError(f"Could not instantiate {cls.__name__}: {last_error}")


def _extract_tensor(value):
    if torch.is_tensor(value):
        return value
    if isinstance(value, (tuple, list)):
        for item in value:
            t = _extract_tensor(item)
            if t is not None:
                return t
        return None
    if isinstance(value, dict):
        for key in ("node_embeddings", "embeddings", "hidden_states", "last_hidden_state", "x"):
            if key in value and torch.is_tensor(value[key]):
                return value[key]
        for item in value.values():
            t = _extract_tensor(item)
            if t is not None:
                return t
        return None
    for attr in ("node_embeddings", "embeddings", "hidden_states", "last_hidden_state", "x", "logits"):
        if hasattr(value, attr):
            maybe = getattr(value, attr)
            if torch.is_tensor(maybe):
                return maybe
    return None


def _encoder_hidden_size(encoder):
    for attr in ("hidden_size", "d_model", "model_dim", "out_dim", "output_dim"):
        if hasattr(encoder, attr):
            maybe = getattr(encoder, attr)
            if isinstance(maybe, int) and maybe > 0:
                return maybe
    return 384


def _encoder_input_size(encoder):
    for attr in ("input_dim", "in_dim", "node_feature_dim", "num_node_features", "feature_dim"):
        if hasattr(encoder, attr):
            maybe = getattr(encoder, attr)
            if isinstance(maybe, int) and maybe > 0:
                return maybe
    return 32


def _call_encoder_forward(encoder, x, edge_index, edge_type, batch_idx):
    graph = {
        "x": x,
        "node_features": x,
        "edge_index": edge_index,
        "edge_type": edge_type,
        "batch": batch_idx,
    }

    attempts = [
        lambda: encoder(node_features=x, edge_index=edge_index, edge_type=edge_type, batch=batch_idx),
        lambda: encoder(x=x, edge_index=edge_index, edge_type=edge_type, batch=batch_idx),
        lambda: encoder(graph=graph),
        lambda: encoder(graph_batch=graph),
        lambda: encoder(graph),
        lambda: encoder(x, edge_index, edge_type, batch_idx),
    ]

    forward_sig = inspect.signature(encoder.forward)
    kwargs = {}
    for p in forward_sig.parameters.values():
        if p.name == "self" or p.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        lname = p.name.lower()
        if p.name in ("x", "node_features", "features", "node_feats"):
            kwargs[p.name] = x
        elif "edge_index" in lname:
            kwargs[p.name] = edge_index
        elif p.name in ("edge_type", "edge_types"):
            kwargs[p.name] = edge_type
        elif p.name in ("batch", "batch_idx"):
            kwargs[p.name] = batch_idx
        elif "graph" in lname:
            kwargs[p.name] = graph
    attempts.insert(0, lambda: encoder(**kwargs))

    last_error = None
    for fn in attempts:
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - exercised via fallback attempts
            last_error = exc
    raise AssertionError(f"Graph encoder forward failed for all call patterns: {last_error}")


def _call_projector(projector, x):
    attempts = [
        lambda: projector(x),
        lambda: projector(features=x),
        lambda: projector(node_embeddings=x),
        lambda: projector(hidden_states=x),
    ]
    last_error = None
    for fn in attempts:
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - exercised via fallback attempts
            last_error = exc
    raise AssertionError(f"Projector forward failed for all call patterns: {last_error}")


class _DummyDecoder(nn.Module):
    def __init__(self, hidden_size=1024, vocab_size=128):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, hidden_size)
        self.lm_head = nn.Linear(hidden_size, vocab_size)
        self.config = SimpleNamespace(hidden_size=hidden_size, vocab_size=vocab_size)
        self.model = SimpleNamespace(embed_tokens=self.embed)
        self.last_inputs_embeds = None
        self.last_input_ids = None

    def get_input_embeddings(self):
        return self.embed

    def forward(self, input_ids=None, inputs_embeds=None, attention_mask=None, **kwargs):
        if inputs_embeds is None:
            assert input_ids is not None
            inputs_embeds = self.embed(input_ids)
        self.last_inputs_embeds = inputs_embeds
        self.last_input_ids = input_ids
        logits = self.lm_head(inputs_embeds)
        out = {"logits": logits}
        out["last_hidden_state"] = inputs_embeds
        return out


class _DummyGraphEncoder(nn.Module):
    def __init__(self, hidden_size=384, graph_tokens=3):
        super().__init__()
        self.hidden_size = hidden_size
        self.graph_tokens = graph_tokens

    def forward(self, graph_batch=None, **kwargs):
        batch_size = 1
        if isinstance(graph_batch, dict):
            batch_size = int(graph_batch.get("batch_size", batch_size))
        return torch.randn(batch_size, self.graph_tokens, self.hidden_size)


class _DummyProjector(nn.Module):
    def __init__(self, in_dim=384, out_dim=1024):
        super().__init__()
        self.out_dim = out_dim
        self.linear = nn.Linear(in_dim, out_dim)

    def forward(self, x=None, node_embeddings=None, **kwargs):
        if x is None:
            x = node_embeddings
        return self.linear(x)


def _instantiate_wrapper(wrapper_cls, decoder, graph_encoder, projector):
    init_sig = inspect.signature(wrapper_cls.__init__)
    kwargs = {}
    for p in list(init_sig.parameters.values())[1:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        lname = p.name.lower()
        if "decoder" in lname or "qwen" in lname or "base_model" in lname:
            kwargs[p.name] = decoder
        elif "graph_encoder" in lname or lname == "encoder":
            kwargs[p.name] = graph_encoder
        elif "projector" in lname:
            kwargs[p.name] = projector
        elif "prepend" in lname or "concat" in lname or "fallback" in lname:
            kwargs[p.name] = True
        elif p.default is inspect._empty:
            kwargs[p.name] = _default_value_for_param(p.name)
    return wrapper_cls(**kwargs)


def _call_wrapper(wrapper, input_ids, attention_mask, graph_batch):
    graph_tokens = torch.randn(input_ids.shape[0], 3, 1024)
    attempts = [
        lambda: wrapper(
            input_ids=input_ids,
            attention_mask=attention_mask,
            graph_batch=graph_batch,
        ),
        lambda: wrapper(input_ids=input_ids, graph_batch=graph_batch),
        lambda: wrapper(input_ids=input_ids, graph=graph_batch, attention_mask=attention_mask),
        lambda: wrapper(
            input_ids=input_ids,
            attention_mask=attention_mask,
            graph_tokens=graph_tokens,
        ),
    ]

    forward_sig = inspect.signature(wrapper.forward)
    kwargs = {}
    for p in forward_sig.parameters.values():
        if p.name == "self" or p.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        lname = p.name.lower()
        if "input_ids" in lname:
            kwargs[p.name] = input_ids
        elif "attention_mask" in lname:
            kwargs[p.name] = attention_mask
        elif "graph_tokens" in lname:
            kwargs[p.name] = graph_tokens
        elif "graph" in lname:
            kwargs[p.name] = graph_batch
        elif p.default is inspect._empty:
            kwargs[p.name] = _default_value_for_param(p.name)
    attempts.insert(0, lambda: wrapper(**kwargs))

    last_error = None
    for fn in attempts:
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - exercised via fallback attempts
            last_error = exc
    raise AssertionError(f"Wrapper forward failed for all call patterns: {last_error}")


def test_graph_encoder_forward_shape():
    module = _import_first(
        [
            "src.models.graph_encoder",
            "models.graph_encoder",
            "graph_encoder",
        ]
    )
    encoder_cls = _resolve_class(
        module,
        preferred_names=["SchematicGraphEncoder", "GraphEncoder"],
        contains_hint="encoder",
    )
    encoder = _instantiate_class(encoder_cls)
    encoder.eval()

    num_nodes = 8
    num_edges = 12
    in_dim = _encoder_input_size(encoder)
    x = torch.randn(num_nodes, in_dim)
    edge_index = torch.tensor(
        [
            [0, 1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5],
            [1, 2, 3, 4, 5, 6, 7, 0, 1, 2, 3, 4],
        ],
        dtype=torch.long,
    )
    edge_type = torch.zeros(num_edges, dtype=torch.long)
    batch_idx = torch.zeros(num_nodes, dtype=torch.long)

    with torch.no_grad():
        output = _call_encoder_forward(encoder, x, edge_index, edge_type, batch_idx)

    hidden = _extract_tensor(output)
    assert hidden is not None, "Could not locate tensor output from graph encoder forward"
    assert hidden.ndim >= 2
    assert hidden.shape[-1] == _encoder_hidden_size(encoder)
    assert not torch.isnan(hidden).any()


def test_projector_output_dim():
    module = _import_first(
        [
            "src.models.projector",
            "models.projector",
            "projector",
        ]
    )
    projector_cls = _resolve_class(
        module,
        preferred_names=["GraphToQwenProjector", "Projector"],
        contains_hint="projector",
    )
    projector = _instantiate_class(projector_cls, overrides={"in_dim": 384, "out_dim": 1024})
    projector.eval()

    x = torch.randn(6, 384)
    with torch.no_grad():
        output = _call_projector(projector, x)

    y = _extract_tensor(output)
    assert y is not None, "Could not locate tensor output from projector forward"
    assert y.shape[0] == x.shape[0]
    assert y.shape[-1] == 1024


def test_qwen_wrapper_concat_path_smoke_with_dummy_decoder():
    module = _import_first(
        [
            "src.models.qwen_schematic_model",
            "models.qwen_schematic_model",
            "qwen_schematic_model",
        ]
    )
    wrapper_cls = _resolve_class(
        module,
        preferred_names=["QwenSchematicModel", "QwenWrapper"],
        contains_hint="qwen",
    )

    dummy_decoder = _DummyDecoder(hidden_size=1024)
    dummy_encoder = _DummyGraphEncoder(hidden_size=384, graph_tokens=3)
    dummy_projector = _DummyProjector(in_dim=384, out_dim=1024)
    wrapper = _instantiate_wrapper(
        wrapper_cls,
        decoder=dummy_decoder,
        graph_encoder=dummy_encoder,
        projector=dummy_projector,
    )
    wrapper.eval()

    batch_size = 2
    text_len = 5
    input_ids = torch.randint(0, 32, (batch_size, text_len))
    attention_mask = torch.ones_like(input_ids)
    graph_batch = {"batch_size": batch_size}

    with torch.no_grad():
        output = _call_wrapper(wrapper, input_ids, attention_mask, graph_batch)

    assert output is not None
    assert dummy_decoder.last_inputs_embeds is not None
    assert dummy_decoder.last_inputs_embeds.shape[0] == batch_size
    assert (
        dummy_decoder.last_inputs_embeds.shape[1] > text_len
    ), "Expected concatenated graph+text sequence length in decoder input"
