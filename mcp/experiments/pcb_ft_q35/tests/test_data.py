import importlib
import inspect

import pytest

torch = pytest.importorskip("torch")


def _import_first(module_names):
    last_error = None
    for name in module_names:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            last_error = exc
    raise AssertionError(f"Could not import any of {module_names}: {last_error}")


def _resolve_function(module, preferred_names, contains_hint):
    for name in preferred_names:
        obj = getattr(module, name, None)
        if callable(obj):
            return obj
    for name, obj in inspect.getmembers(module):
        if callable(obj) and contains_hint in name.lower():
            return obj
    raise AssertionError(
        f"No callable found in {module.__name__} for names={preferred_names} hint={contains_hint}"
    )


def _lookup_context_value(name, context):
    if name in context:
        return context[name]
    lname = name.lower()
    if lname in context:
        return context[lname]

    aliases = {
        "sample": ["sample", "data", "item"],
        "graph": ["graph", "sample", "data"],
        "nodes": ["nodes", "items", "components"],
        "components": ["components", "nodes", "items"],
        "items": ["items", "components", "nodes"],
        "center_ref": ["center_ref", "anchor_ref", "ref"],
        "radius_mm": ["radius_mm", "radius"],
        "radius": ["radius", "radius_mm"],
        "center_x": ["center_x", "x", "anchor_x"],
        "center_y": ["center_y", "y", "anchor_y"],
        "value": ["value", "x", "distance", "angle"],
        "bins": ["bins", "boundaries", "edges"],
    }
    for candidate in aliases.get(lname, []):
        if candidate in context:
            return context[candidate]

    base = lname.removesuffix("_mm")
    if base in context:
        return context[base]
    mm_name = f"{lname}_mm"
    if mm_name in context:
        return context[mm_name]

    for key, value in context.items():
        if lname in key.lower() or key.lower() in lname:
            return value
    return None


def _invoke_with_context(fn, context):
    sig = inspect.signature(fn)
    args = []
    kwargs = {}
    for p in sig.parameters.values():
        if p.kind == inspect.Parameter.VAR_POSITIONAL:
            continue
        if p.kind == inspect.Parameter.VAR_KEYWORD:
            continue
        value = _lookup_context_value(p.name, context)
        if value is None:
            if p.default is not inspect._empty:
                continue
            raise TypeError(f"Missing required argument '{p.name}' for {fn.__name__}")
        if p.kind == inspect.Parameter.POSITIONAL_ONLY:
            args.append(value)
        else:
            kwargs[p.name] = value
    return fn(*args, **kwargs)


def _outputs_equal(a, b):
    if torch.is_tensor(a) and torch.is_tensor(b):
        return torch.equal(a, b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
        return all(_outputs_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return all(_outputs_equal(a[k], b[k]) for k in a)
    return a == b


def _extract_bool_like(result):
    if isinstance(result, bool):
        return result
    if isinstance(result, tuple) and result and isinstance(result[0], bool):
        return result[0]
    if isinstance(result, dict):
        for key in ("ok", "valid", "is_valid"):
            if key in result and isinstance(result[key], bool):
                return result[key]
    return None


def _validation_rejects(validate_fn, sample):
    try:
        result = _invoke_with_context(validate_fn, {"sample": sample, "data": sample, "item": sample})
    except Exception:
        return True
    bool_like = _extract_bool_like(result)
    if bool_like is not None:
        return bool_like is False
    if isinstance(result, (list, tuple, set)) and len(result) > 0:
        # A non-empty errors collection is still a rejection.
        if all(isinstance(x, str) for x in result):
            return True
    return False


def _extract_ids(payload, original_nodes):
    if payload is None:
        return set()
    if isinstance(payload, dict):
        for key in ("nodes", "items", "components", "window_nodes"):
            if key in payload:
                return _extract_ids(payload[key], original_nodes)
        return set()
    if isinstance(payload, (list, tuple, set)):
        ids = set()
        for item in payload:
            if isinstance(item, dict):
                for k in ("id", "ref", "refdes", "name"):
                    if k in item:
                        ids.add(str(item[k]))
                        break
            elif isinstance(item, str):
                ids.add(item)
            elif isinstance(item, int) and 0 <= item < len(original_nodes):
                ids.add(str(original_nodes[item]["id"]))
        return ids
    return set()


def test_bucketization_is_deterministic():
    graph_schema = _import_first(
        ["src.data.graph_schema", "data.graph_schema", "graph_schema"]
    )
    bucketize = _resolve_function(
        graph_schema,
        preferred_names=["bucketize_geometry", "bucketize_value", "bucketize"],
        contains_hint="bucket",
    )

    context = {
        "value": 12.75,
        "x": 12.75,
        "y": -3.5,
        "w": 4.0,
        "h": 2.0,
        "distance": 12.75,
        "angle": 45.0,
        "bins": [0.0, 5.0, 10.0, 20.0, 40.0],
        "boundaries": [0.0, 5.0, 10.0, 20.0, 40.0],
        "edges": [0.0, 5.0, 10.0, 20.0, 40.0],
        "num_buckets": 8,
        "n_buckets": 8,
        "min_value": 0.0,
        "max_value": 40.0,
    }

    out1 = _invoke_with_context(bucketize, context)
    out2 = _invoke_with_context(bucketize, context)
    assert _outputs_equal(out1, out2), "Bucketization must be deterministic for identical inputs"


def test_bucketization_is_repeatable_across_calls():
    graph_schema = _import_first(
        ["src.data.graph_schema", "data.graph_schema", "graph_schema"]
    )
    bucketize = _resolve_function(
        graph_schema,
        preferred_names=["bucketize_geometry", "bucketize_value", "bucketize"],
        contains_hint="bucket",
    )

    context = {
        "value": 3.125,
        "x": 3.125,
        "y": 9.5,
        "w": 1.0,
        "h": 1.0,
        "distance": 3.125,
        "angle": 90.0,
        "bins": [0.0, 2.0, 4.0, 8.0, 16.0],
        "boundaries": [0.0, 2.0, 4.0, 8.0, 16.0],
        "edges": [0.0, 2.0, 4.0, 8.0, 16.0],
        "num_buckets": 6,
        "n_buckets": 6,
        "min_value": 0.0,
        "max_value": 16.0,
    }

    outputs = [_invoke_with_context(bucketize, context) for _ in range(4)]
    assert all(_outputs_equal(outputs[0], o) for o in outputs[1:])


def test_validate_sample_catches_obvious_errors():
    graph_schema = _import_first(
        ["src.data.graph_schema", "data.graph_schema", "graph_schema"]
    )
    validate_sample = _resolve_function(
        graph_schema,
        preferred_names=["validate_sample"],
        contains_hint="validate",
    )

    invalid_sample = {
        "sample_id": "win_bad_001",
        "task_type": "critique",
        # Missing graph intentionally.
        "target": {"ok": True, "issues": []},
    }
    assert _validation_rejects(validate_sample, invalid_sample)


def test_sample_window_includes_nearby_and_excludes_far_items():
    window_sampler = _import_first(
        ["src.data.window_sampler", "data.window_sampler", "window_sampler"]
    )
    sample_window = _resolve_function(
        window_sampler,
        preferred_names=["sample_window", "extract_window"],
        contains_hint="window",
    )

    nodes = [
        {"id": "U1", "x": 0.0, "y": 0.0, "type": "symbol"},
        {"id": "R1", "x": 5.0, "y": 0.0, "type": "symbol"},
        {"id": "C9", "x": 50.0, "y": 0.0, "type": "symbol"},
    ]
    graph = {"nodes": nodes, "edges": []}
    context = {
        "nodes": nodes,
        "items": nodes,
        "components": nodes,
        "graph": graph,
        "sample": {"graph": graph},
        "center_ref": "U1",
        "anchor_ref": "U1",
        "radius_mm": 10.0,
        "radius": 10.0,
        "center_x": 0.0,
        "center_y": 0.0,
    }

    result = _invoke_with_context(sample_window, context)
    selected_ids = _extract_ids(result, original_nodes=nodes)

    assert "U1" in selected_ids
    assert "R1" in selected_ids
    assert "C9" not in selected_ids
