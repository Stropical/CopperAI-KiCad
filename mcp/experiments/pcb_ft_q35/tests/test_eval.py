import importlib
import inspect

import pytest


def _import_first(module_names):
    last_error = None
    for name in module_names:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            last_error = exc
    raise AssertionError(f"Could not import any of {module_names}: {last_error}")


def _instantiate_with_defaults(cls):
    sig = inspect.signature(cls.__init__)
    kwargs = {}
    for p in list(sig.parameters.values())[1:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is not inspect._empty:
            continue
        lname = p.name.lower()
        if "threshold" in lname:
            kwargs[p.name] = 0.5
        elif "topk" in lname:
            kwargs[p.name] = 1
        else:
            kwargs[p.name] = 1
    return cls(**kwargs)


def _resolve_metric_callable(module, preferred_names, contains_hint):
    for name in preferred_names:
        obj = getattr(module, name, None)
        if callable(obj):
            return obj

    for class_name, cls in inspect.getmembers(module, inspect.isclass):
        instance = _instantiate_with_defaults(cls)
        for name in preferred_names:
            method = getattr(instance, name, None)
            if callable(method):
                return method
        for method_name, method in inspect.getmembers(instance, callable):
            if contains_hint in method_name.lower():
                return method

    for name, obj in inspect.getmembers(module):
        if callable(obj) and contains_hint in name.lower():
            return obj

    raise AssertionError(
        f"No metric callable found in {module.__name__} for names={preferred_names} hint={contains_hint}"
    )


def _lookup_context_value(name, context):
    if name in context:
        return context[name]
    lname = name.lower()
    if lname in context:
        return context[lname]
    aliases = {
        "predictions": ["predictions", "preds", "y_pred", "outputs", "hyp"],
        "preds": ["predictions", "preds", "y_pred", "outputs", "hyp"],
        "targets": ["targets", "labels", "y_true", "gold", "references"],
        "labels": ["labels", "targets", "y_true", "gold", "references"],
        "outputs": ["outputs", "predictions", "responses", "texts"],
        "responses": ["responses", "outputs", "predictions", "texts"],
        "texts": ["texts", "outputs", "predictions", "responses"],
    }
    for candidate in aliases.get(lname, []):
        if candidate in context:
            return context[candidate]
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


def _extract_scalar_metric(result, keys):
    if isinstance(result, (int, float)):
        return float(result)
    if isinstance(result, dict):
        for key in keys:
            if key in result:
                return float(result[key])
    if isinstance(result, tuple):
        for item in result:
            if isinstance(item, (int, float)):
                return float(item)
    return None


def _extract_prf(result):
    if isinstance(result, dict):
        p = result.get("precision", result.get("micro_precision"))
        r = result.get("recall", result.get("micro_recall"))
        f = result.get("f1", result.get("micro_f1"))
        if p is not None and r is not None and f is not None:
            return float(p), float(r), float(f)
    if isinstance(result, tuple) and len(result) >= 3:
        p, r, f = result[:3]
        if all(isinstance(x, (int, float)) for x in (p, r, f)):
            return float(p), float(r), float(f)
    return None


def _compute_json_validity(metric_fn, samples):
    contexts = [
        {"outputs": samples, "responses": samples, "texts": samples},
        {"predictions": samples},
    ]
    for context in contexts:
        try:
            result = _invoke_with_context(metric_fn, context)
        except Exception:
            continue
        scalar = _extract_scalar_metric(result, ["json_validity", "validity", "rate", "value"])
        if scalar is not None:
            return scalar
        if isinstance(result, (list, tuple)) and result and all(isinstance(x, bool) for x in result):
            return sum(result) / len(result)
        if isinstance(result, bool):
            # Likely single-sample validity helper: fall back to per-sample averaging.
            break

    per_item = []
    for sample in samples:
        res = _invoke_with_context(
            metric_fn,
            {"outputs": sample, "responses": sample, "texts": sample, "prediction": sample},
        )
        if not isinstance(res, bool):
            raise AssertionError("JSON validity callable returned non-bool on single-item input")
        per_item.append(res)
    return sum(per_item) / len(per_item)


def test_ranking_accuracy_correctness():
    eval_mod = _import_first(
        ["src.eval.eval_local_tasks", "eval.eval_local_tasks", "eval_local_tasks"]
    )
    ranking_accuracy = _resolve_metric_callable(
        eval_mod,
        preferred_names=["ranking_accuracy", "compute_ranking_accuracy"],
        contains_hint="ranking",
    )

    preds = [2, 0, 1, 2]
    targets = [2, 1, 1, 0]
    expected = 0.5

    contexts = [
        {"predictions": preds, "targets": targets, "labels": targets},
        {
            "predictions": [{"best": p} for p in preds],
            "targets": [{"best": t} for t in targets],
            "labels": [{"best": t} for t in targets],
        },
    ]
    result = None
    for context in contexts:
        try:
            result = _invoke_with_context(ranking_accuracy, context)
            break
        except Exception:
            continue
    if result is None:
        raise AssertionError("Could not invoke ranking accuracy metric with supported call patterns")

    actual = _extract_scalar_metric(result, ["accuracy", "ranking_accuracy", "top1"])
    assert actual is not None, f"Ranking metric returned unsupported type: {type(result)}"
    assert actual == pytest.approx(expected)


def test_critique_precision_recall_f1_sanity():
    eval_mod = _import_first(
        ["src.eval.eval_local_tasks", "eval.eval_local_tasks", "eval_local_tasks"]
    )
    critique_prf = _resolve_metric_callable(
        eval_mod,
        preferred_names=[
            "critique_precision_recall_f1",
            "compute_critique_prf",
            "critique_prf",
        ],
        contains_hint="critique",
    )

    pred = [
        [{"target": "C3", "reason": "too_far_from_U1_VDD"}],
        [{"target": "X9", "reason": "spurious"}],
    ]
    gold = [
        [
            {"target": "C3", "reason": "too_far_from_U1_VDD"},
            {"target": "R1", "reason": "wire_crossing"},
        ],
        [],
    ]

    contexts = [
        {"predictions": pred, "targets": gold, "labels": gold},
        {
            "predictions": [{"issues": p} for p in pred],
            "targets": [{"issues": g} for g in gold],
            "labels": [{"issues": g} for g in gold],
        },
    ]
    result = None
    for context in contexts:
        try:
            result = _invoke_with_context(critique_prf, context)
            break
        except Exception:
            continue
    if result is None:
        raise AssertionError("Could not invoke critique PRF metric with supported call patterns")

    prf = _extract_prf(result)
    assert prf is not None, f"Critique metric returned unsupported type: {type(result)}"
    precision, recall, f1 = prf
    assert precision == pytest.approx(0.5)
    assert recall == pytest.approx(0.5)
    assert f1 == pytest.approx(0.5)


def test_json_validity_metric():
    eval_mod = _import_first(
        ["src.eval.eval_local_tasks", "eval.eval_local_tasks", "eval_local_tasks"]
    )
    json_validity = _resolve_metric_callable(
        eval_mod,
        preferred_names=["json_validity_rate", "compute_json_validity", "json_validity"],
        contains_hint="json",
    )

    samples = [
        '{"best": 2, "reason": "fewest_crossings"}',
        '{"ok": false, "issues": []}',
        '{"broken_json": ]',
    ]
    expected = 2.0 / 3.0
    actual = _compute_json_validity(json_validity, samples)
    assert actual == pytest.approx(expected)
