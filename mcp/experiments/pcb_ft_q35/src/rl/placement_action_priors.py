"""Pure heuristics for placement-action expansion.

The helpers in this module are intentionally lightweight and side-effect free.
They are meant to support action expansion, candidate ranking, and curriculum
generation for schematic placement/cleanup tasks.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

PointLike = tuple[float, float]

__all__ = [
    "normalize_reference_text",
    "is_probable_passive",
    "is_probable_connector",
    "is_probable_decoupler",
    "classify_instance_role",
    "score_edge_proximity",
    "score_alignment",
    "score_spacing",
    "score_alignment_spacing",
    "cluster_bbox",
    "cluster_centroid",
    "cluster_pairwise_stats",
    "cluster_compactness_score",
    "passive_cluster_score",
    "passive_cluster_report",
]

_REFERENCE_RE = re.compile(r"^[A-Z]+")
_VALUE_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>PF|NF|UF|ΜF|UA|UAH|F|OHM|KOHM|MOHM)?", re.IGNORECASE)

_PASSIVE_PREFIXES = ("R", "C", "L", "FB", "RV", "RT")
_CONNECTOR_PREFIXES = ("J", "P", "CN", "HDR", "CONN")
_SEMICONDUCTOR_PREFIXES = ("Q", "D", "M", "T", "Z", "VD")
_MECHANICAL_PREFIXES = ("TP", "MH", "H", "SW", "BTN")
_IC_PREFIXES = ("U", "IC", "A", "XU")

_POWER_HINTS = (
    "power",
    "vcc",
    "gnd",
    "vdd",
    "vss",
    "vin",
    "3v3",
    "5v",
    "1v8",
    "1v2",
    "12v",
    "+3v3",
    "+5v",
    "avcc",
    "dvcc",
)
_CONNECTOR_HINTS = (
    "connector",
    "header",
    "socket",
    "plug",
    "jst",
    "usb",
    "type-c",
    "micro usb",
    "mini usb",
    "hdmi",
    "eth",
    "ethernet",
    "sma",
    "b2b",
    "board to board",
    "ffc",
    "fpc",
)
_DECUPLING_HINTS = (
    "decoupl",
    "bypass",
    "bulk cap",
    "bulk capacitor",
    "stabiliz",
    "local cap",
    "local capacitor",
)


def normalize_reference_text(reference: str | None) -> str:
    """Normalize a reference designator to an uppercase alphanumeric token."""

    if not reference:
        return ""
    return re.sub(r"[^A-Z0-9]+", "", reference.upper())


def _reference_prefix(reference: str | None) -> str:
    normalized = normalize_reference_text(reference)
    match = _REFERENCE_RE.match(normalized)
    return match.group(0) if match else ""


def _text_blob(reference: str | None, value: str | None = None, lib_id: str | None = None) -> str:
    return " ".join(part for part in (reference, value, lib_id) if part).lower()


def _classify_unit_value(value: str | None) -> tuple[float | None, str]:
    if not value:
        return None, ""
    text = value.strip().upper().replace("Μ", "U")
    match = _VALUE_RE.search(text)
    if not match:
        return None, text
    num = float(match.group("num"))
    unit = (match.group("unit") or "").upper()
    return num, unit


def _looks_like_capacitance(value: str | None) -> bool:
    num, unit = _classify_unit_value(value)
    if num is None:
        return False
    return unit in {"PF", "NF", "UF", "F", ""} and num > 0


def is_probable_passive(reference: str | None, value: str | None = None, lib_id: str | None = None) -> bool:
    """Return True for small passive parts such as resistors, caps, inductors, and beads."""

    prefix = _reference_prefix(reference)
    if prefix.startswith(_PASSIVE_PREFIXES):
        return True

    blob = _text_blob(reference, value, lib_id)
    return any(token in blob for token in ("resistor", "capacitor", "inductor", "ferrite", "bead", "potentiometer", "trimmer"))


def is_probable_connector(reference: str | None, value: str | None = None, lib_id: str | None = None) -> bool:
    """Return True for connector-like references."""

    prefix = _reference_prefix(reference)
    blob = _text_blob(reference, value, lib_id)

    if prefix.startswith(_CONNECTOR_PREFIXES):
        return True

    if prefix.startswith("X") and any(token in blob for token in _CONNECTOR_HINTS):
        return True

    return any(token in blob for token in _CONNECTOR_HINTS)


def is_probable_decoupler(reference: str | None, value: str | None = None, lib_id: str | None = None) -> bool:
    """Return True for likely decoupling capacitors."""

    prefix = _reference_prefix(reference)
    if not prefix.startswith("C"):
        return False

    blob = _text_blob(reference, value, lib_id)
    if any(token in blob for token in _DECUPLING_HINTS):
        return True

    if _looks_like_capacitance(value):
        num, unit = _classify_unit_value(value)
        if num is None:
            return False
        if unit in {"PF", "NF"}:
            return True
        if unit in {"UF", "F", ""} and num <= 4.7:
            return True

    return False


def classify_instance_role(reference: str | None, value: str | None = None, lib_id: str | None = None) -> str:
    """Classify an instance into a coarse placement role."""

    blob = _text_blob(reference, value, lib_id)
    prefix = _reference_prefix(reference)

    if any(token in blob for token in _POWER_HINTS):
        return "power"
    if is_probable_connector(reference, value, lib_id):
        return "connector"
    if is_probable_decoupler(reference, value, lib_id):
        return "decoupler"
    if is_probable_passive(reference, value, lib_id):
        return "passive"
    if prefix.startswith(_IC_PREFIXES) or any(token in blob for token in ("opamp", "logic", "mcu", "fpga", "adc", "dac", "regulator")):
        return "ic"
    if prefix.startswith(_SEMICONDUCTOR_PREFIXES) or any(token in blob for token in ("mosfet", "transistor", "diode", "schottky", "tvs", "zener")):
        return "semiconductor"
    if prefix.startswith(_MECHANICAL_PREFIXES) or any(token in blob for token in ("mount", "mechanical", "testpoint", "test point")):
        return "mechanical"
    return "unknown"


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _as_point(value: Any) -> PointLike | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if "x" in value and "y" in value:
            return float(value["x"]), float(value["y"])
        if "center_x" in value and "center_y" in value:
            return float(value["center_x"]), float(value["center_y"])
        if "pos" in value:
            return _as_point(value["pos"])
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
        return float(value[0]), float(value[1])
    if hasattr(value, "x") and hasattr(value, "y"):
        return float(getattr(value, "x")), float(getattr(value, "y"))
    if hasattr(value, "center_x") and hasattr(value, "center_y"):
        return float(getattr(value, "center_x")), float(getattr(value, "center_y"))
    if hasattr(value, "position"):
        return _as_point(getattr(value, "position"))
    return None


def score_edge_proximity(point_or_instance: Any, board_size: PointLike, edge_band_mm: float | None = None) -> float:
    """Score how close a point is to the board boundary.

    The score is 1.0 on the edge and decays toward 0.0 as the point moves toward
    the board center.
    """

    point = _as_point(point_or_instance)
    if point is None:
        return 0.0

    width, height = float(board_size[0]), float(board_size[1])
    if width <= 0.0 or height <= 0.0:
        return 0.0

    x, y = point
    min_dist = min(x, y, width - x, height - y)
    if edge_band_mm is None:
        edge_band_mm = max(min(width, height) * 0.15, 1.0)
    return _clamp(1.0 - (min_dist / max(edge_band_mm, 1e-6)))


def score_alignment(a: Any, b: Any, tolerance_mm: float = 0.5) -> float:
    """Score whether two points line up on a shared X or Y axis."""

    pa = _as_point(a)
    pb = _as_point(b)
    if pa is None or pb is None or tolerance_mm <= 0.0:
        return 0.0

    dx = abs(pa[0] - pb[0])
    dy = abs(pa[1] - pb[1])
    x_score = _clamp(1.0 - dx / tolerance_mm)
    y_score = _clamp(1.0 - dy / tolerance_mm)
    return max(x_score, y_score)


def score_spacing(a: Any, b: Any, target_spacing_mm: float, tolerance_mm: float | None = None) -> float:
    """Score how close the Euclidean distance is to a target spacing."""

    pa = _as_point(a)
    pb = _as_point(b)
    if pa is None or pb is None or target_spacing_mm <= 0.0:
        return 0.0

    if tolerance_mm is None:
        tolerance_mm = max(target_spacing_mm * 0.5, 0.5)

    dist = math.dist(pa, pb)
    return _clamp(1.0 - abs(dist - target_spacing_mm) / max(tolerance_mm, 1e-6))


def score_alignment_spacing(
    a: Any,
    b: Any,
    target_spacing_mm: float = 2.54,
    tolerance_mm: float = 0.5,
    alignment_weight: float = 0.5,
    spacing_weight: float = 0.5,
) -> float:
    """Blend alignment and spacing into one placement score."""

    alignment = score_alignment(a, b, tolerance_mm=tolerance_mm)
    spacing = score_spacing(a, b, target_spacing_mm=target_spacing_mm, tolerance_mm=tolerance_mm)
    return _clamp(alignment_weight * alignment + spacing_weight * spacing)


def _points_from_instances(instances: Sequence[Any]) -> list[PointLike]:
    points: list[PointLike] = []
    for inst in instances:
        point = _as_point(inst)
        if point is not None:
            points.append(point)
    return points


def cluster_bbox(points_or_instances: Sequence[Any]) -> dict[str, float]:
    """Return bounding-box geometry for a compactness candidate cluster."""

    points = _points_from_instances(points_or_instances)
    if not points:
        return {
            "count": 0.0,
            "xmin": 0.0,
            "ymin": 0.0,
            "xmax": 0.0,
            "ymax": 0.0,
            "width": 0.0,
            "height": 0.0,
            "area": 0.0,
            "diagonal": 0.0,
            "center_x": 0.0,
            "center_y": 0.0,
        }

    xs = [pt[0] for pt in points]
    ys = [pt[1] for pt in points]
    xmin = min(xs)
    xmax = max(xs)
    ymin = min(ys)
    ymax = max(ys)
    width = xmax - xmin
    height = ymax - ymin
    return {
        "count": float(len(points)),
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "width": width,
        "height": height,
        "area": width * height,
        "diagonal": math.hypot(width, height),
        "center_x": (xmin + xmax) * 0.5,
        "center_y": (ymin + ymax) * 0.5,
    }


def cluster_centroid(points_or_instances: Sequence[Any]) -> PointLike | None:
    """Return the centroid of a cluster or None when the cluster is empty."""

    points = _points_from_instances(points_or_instances)
    if not points:
        return None
    return (sum(pt[0] for pt in points) / len(points), sum(pt[1] for pt in points) / len(points))


def cluster_pairwise_stats(points_or_instances: Sequence[Any]) -> dict[str, float]:
    """Summarize pairwise spacing inside a cluster."""

    points = _points_from_instances(points_or_instances)
    if len(points) < 2:
        return {
            "pair_count": 0.0,
            "pairwise_min": 0.0,
            "pairwise_mean": 0.0,
            "pairwise_max": 0.0,
            "pairwise_std": 0.0,
        }

    dists: list[float] = []
    for idx, pa in enumerate(points):
        for pb in points[idx + 1 :]:
            dists.append(math.dist(pa, pb))

    mean = sum(dists) / len(dists)
    variance = sum((dist - mean) ** 2 for dist in dists) / len(dists)
    return {
        "pair_count": float(len(dists)),
        "pairwise_min": min(dists),
        "pairwise_mean": mean,
        "pairwise_max": max(dists),
        "pairwise_std": math.sqrt(variance),
    }


def cluster_compactness_score(
    points_or_instances: Sequence[Any],
    grid_mm: float = 2.54,
) -> float:
    """Score how tightly packed a cluster is.

    Higher values indicate a smaller footprint and shorter pairwise distances.
    """

    bbox = cluster_bbox(points_or_instances)
    pairwise = cluster_pairwise_stats(points_or_instances)
    count = bbox["count"]
    if count < 2:
        return 0.0

    area_norm = bbox["area"] / max(grid_mm * grid_mm * max(count - 1.0, 1.0), 1e-6)
    span_norm = bbox["diagonal"] / max(grid_mm * max(count - 1.0, 1.0), 1e-6)
    pair_norm = pairwise["pairwise_mean"] / max(grid_mm, 1e-6)

    area_score = 1.0 / (1.0 + area_norm)
    span_score = 1.0 / (1.0 + span_norm)
    pair_score = 1.0 / (1.0 + pair_norm)
    return _clamp(0.4 * area_score + 0.3 * span_score + 0.3 * pair_score)


def passive_cluster_score(
    instances: Sequence[Any],
    grid_mm: float = 2.54,
) -> float:
    """Score a small passive cluster for placement usefulness."""

    if not instances:
        return 0.0

    points: list[PointLike] = []
    passive_count = 0
    decoupler_count = 0
    connector_count = 0
    for inst in instances:
        point = _as_point(inst)
        if point is not None:
            points.append(point)
        reference = None
        value = None
        lib_id = None
        if isinstance(inst, Mapping):
            reference = inst.get("reference") or inst.get("ref")
            value = inst.get("value")
            lib_id = inst.get("lib_id")
        else:
            reference = getattr(inst, "reference", getattr(inst, "ref", None))
            value = getattr(inst, "value", None)
            lib_id = getattr(inst, "lib_id", None)

        role = classify_instance_role(reference, value, lib_id)
        if role == "passive":
            passive_count += 1
        elif role == "decoupler":
            passive_count += 1
            decoupler_count += 1
        elif role == "connector":
            connector_count += 1

    if passive_count == 0:
        return 0.0

    compactness = cluster_compactness_score(points, grid_mm=grid_mm)
    density_bonus = _clamp(passive_count / max(len(instances), 1))
    decoupler_bonus = 1.0 if decoupler_count > 0 else 0.0
    connector_penalty = 1.0 - _clamp(connector_count / max(len(instances), 1))
    return _clamp(0.55 * compactness + 0.25 * density_bonus + 0.2 * decoupler_bonus * connector_penalty)


def passive_cluster_report(instances: Sequence[Any], grid_mm: float = 2.54) -> dict[str, float]:
    """Return a compactness report for a candidate passive cluster."""

    bbox = cluster_bbox(instances)
    pairwise = cluster_pairwise_stats(instances)
    score = passive_cluster_score(instances, grid_mm=grid_mm)
    return {
        **bbox,
        **pairwise,
        "compactness_score": score,
    }
