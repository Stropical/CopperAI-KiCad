"""Job 2: parse simplified KiCad schematics into graph JSONL records."""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


_TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+')
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d+|\d+|\.\d+)$")


@dataclass(slots=True)
class GraphNode:
    """Graph node emitted from schematic parsing."""

    node_id: str
    node_type: str
    x: float | None = None
    y: float | None = None
    label: str | None = None
    symbol_class: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphEdge:
    """Graph edge emitted from schematic parsing."""

    src: str
    dst: str
    edge_type: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedSchematic:
    """Single parsed schematic record ready for JSONL export."""

    project_id: str
    project_root: str
    schematic_path: str
    parse_mode: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metrics: dict[str, Any]
    parse_error: str | None = None


def _to_float(value: Any) -> float | None:
    token = str(value)
    if not _FLOAT_RE.match(token):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _token_unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return bytes(token[1:-1], "utf-8").decode("unicode_escape")
    return token


def _tokenize(text: str) -> list[str]:
    return [_token_unquote(match.group(0)) for match in _TOKEN_RE.finditer(text)]


def _parse_s_expressions(tokens: Sequence[str]) -> tuple[list[list[Any]], bool]:
    roots: list[list[Any]] = []
    stack: list[list[Any]] = []
    balanced = True

    for token in tokens:
        if token == "(":
            new_form: list[Any] = []
            if stack:
                stack[-1].append(new_form)
            else:
                roots.append(new_form)
            stack.append(new_form)
        elif token == ")":
            if stack:
                stack.pop()
            else:
                balanced = False
        else:
            if stack:
                stack[-1].append(token)

    if stack:
        balanced = False
    return roots, balanced


def _iter_forms(expr: Any) -> Iterator[list[Any]]:
    if not isinstance(expr, list):
        return
    if expr and isinstance(expr[0], str):
        yield expr
    for child in expr:
        if isinstance(child, list):
            yield from _iter_forms(child)


def _first_child(form: list[Any], tag: str) -> list[Any] | None:
    for item in form[1:]:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def _children(form: list[Any], tag: str) -> Iterator[list[Any]]:
    for item in form[1:]:
        if isinstance(item, list) and item and item[0] == tag:
            yield item


def _extract_at(form: list[Any]) -> tuple[float, float] | None:
    at_form = _first_child(form, "at")
    if not at_form or len(at_form) < 3:
        return None
    x = _to_float(at_form[1])
    y = _to_float(at_form[2])
    if x is None or y is None:
        return None
    return (x, y)


def _extract_scalar(form: list[Any], tag: str) -> str | None:
    child = _first_child(form, tag)
    if not child or len(child) < 2:
        return None
    return str(child[1])


def _extract_property(form: list[Any], key: str) -> str | None:
    for prop in _children(form, "property"):
        if len(prop) >= 3 and str(prop[1]) == key:
            return str(prop[2])
    return None


def _extract_points(form: list[Any]) -> list[tuple[float, float]]:
    pts_form = _first_child(form, "pts")
    if not pts_form:
        return []
    points: list[tuple[float, float]] = []
    for item in pts_form[1:]:
        if isinstance(item, list) and item and item[0] == "xy" and len(item) >= 3:
            x = _to_float(item[1])
            y = _to_float(item[2])
            if x is not None and y is not None:
                points.append((x, y))
    return points


def _point_id(x: float, y: float) -> str:
    return f"pt:{x:.4f}:{y:.4f}"


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _classify_symbol(lib_id: str | None, reference: str | None, value: str | None) -> str:
    ref = (reference or "").upper()
    text = " ".join(filter(None, [lib_id, reference, value])).lower()

    if any(token in text for token in ("power", "vcc", "gnd", "3v3", "+5v", "vin")):
        return "power"
    if ref.startswith(("R", "C", "L", "FB", "RV", "RT")):
        return "passive"
    if ref.startswith(("U", "IC", "A")) or any(token in text for token in ("opamp", "logic", "mcu", "fpga", "adc", "dac")):
        return "ic"
    if ref.startswith(("Q", "D", "M", "T")):
        return "semiconductor"
    if ref.startswith(("J", "P", "X", "CN")):
        return "connector"
    if ref.startswith(("TP", "MH", "H")):
        return "mechanical"
    return "unknown"


def _add_node(nodes: dict[str, GraphNode], node: GraphNode) -> None:
    if node.node_id not in nodes:
        nodes[node.node_id] = node


def _add_edge(edges: dict[tuple[str, str, str], GraphEdge], edge: GraphEdge) -> None:
    key = (edge.src, edge.dst, edge.edge_type)
    if key not in edges:
        edges[key] = edge


def _nearest_point(
    xy: tuple[float, float],
    point_nodes: dict[str, tuple[float, float]],
    max_distance: float,
) -> str | None:
    best: tuple[float, str] | None = None
    for point_id, point_xy in point_nodes.items():
        dist = _distance(xy, point_xy)
        if dist <= max_distance:
            candidate = (dist, point_id)
            if best is None or candidate < best:
                best = candidate
    return None if best is None else best[1]


def _parse_with_forms(
    forms: Iterable[list[Any]],
) -> tuple[list[GraphNode], list[GraphEdge], dict[str, Any]]:
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    point_nodes: dict[str, tuple[float, float]] = {}
    symbol_nodes_with_xy: list[tuple[str, tuple[float, float]]] = []

    symbol_index = 0
    junction_index = 0
    label_index = 0

    for form in forms:
        if not form or not isinstance(form[0], str):
            continue
        tag = form[0]

        if tag == "wire":
            points = _extract_points(form)
            for x, y in points:
                pid = _point_id(x, y)
                point_nodes[pid] = (x, y)
                _add_node(nodes, GraphNode(node_id=pid, node_type="wire_point", x=x, y=y))
            for idx in range(len(points) - 1):
                src_xy = points[idx]
                dst_xy = points[idx + 1]
                src_id = _point_id(*src_xy)
                dst_id = _point_id(*dst_xy)
                _add_edge(
                    edges,
                    GraphEdge(
                        src=src_id,
                        dst=dst_id,
                        edge_type="wire_segment",
                        attrs={"length": _distance(src_xy, dst_xy)},
                    ),
                )

        elif tag == "symbol":
            symbol_index += 1
            xy = _extract_at(form)
            lib_id = _extract_scalar(form, "lib_id")
            uuid = _extract_scalar(form, "uuid")
            reference = _extract_property(form, "Reference")
            value = _extract_property(form, "Value")
            node_id = str(uuid) if uuid else f"symbol:{symbol_index:05d}"
            symbol_class = _classify_symbol(lib_id=lib_id, reference=reference, value=value)
            attrs = {
                "lib_id": lib_id,
                "reference": reference,
                "value": value,
            }
            node = GraphNode(
                node_id=node_id,
                node_type="symbol",
                x=None if xy is None else xy[0],
                y=None if xy is None else xy[1],
                label=reference,
                symbol_class=symbol_class,
                attrs=attrs,
            )
            _add_node(nodes, node)
            if xy is not None:
                symbol_nodes_with_xy.append((node_id, xy))

        elif tag == "junction":
            junction_index += 1
            xy = _extract_at(form)
            uuid = _extract_scalar(form, "uuid")
            node_id = str(uuid) if uuid else f"junction:{junction_index:05d}"
            node = GraphNode(
                node_id=node_id,
                node_type="junction",
                x=None if xy is None else xy[0],
                y=None if xy is None else xy[1],
            )
            _add_node(nodes, node)
            if xy is not None:
                point_id = _point_id(*xy)
                point_nodes[point_id] = xy
                _add_node(nodes, GraphNode(node_id=point_id, node_type="wire_point", x=xy[0], y=xy[1]))
                _add_edge(edges, GraphEdge(src=node_id, dst=point_id, edge_type="junction_on_wire"))

        elif tag in {"label", "global_label", "hierarchical_label"}:
            label_index += 1
            xy = _extract_at(form)
            uuid = _extract_scalar(form, "uuid")
            text = str(form[1]) if len(form) > 1 and isinstance(form[1], str) else None
            node_id = str(uuid) if uuid else f"label:{label_index:05d}"
            node = GraphNode(
                node_id=node_id,
                node_type=tag,
                x=None if xy is None else xy[0],
                y=None if xy is None else xy[1],
                label=text,
            )
            _add_node(nodes, node)
            if xy is not None:
                nearest = _nearest_point(xy, point_nodes, max_distance=1.0)
                if nearest is not None:
                    _add_edge(edges, GraphEdge(src=node_id, dst=nearest, edge_type="label_attached"))

    for symbol_id, symbol_xy in symbol_nodes_with_xy:
        nearest = _nearest_point(symbol_xy, point_nodes, max_distance=1.0)
        if nearest is not None:
            _add_edge(edges, GraphEdge(src=symbol_id, dst=nearest, edge_type="symbol_attached"))

    node_list = sorted(nodes.values(), key=lambda node: node.node_id)
    edge_list = sorted(edges.values(), key=lambda edge: (edge.src, edge.dst, edge.edge_type))
    metrics = {
        "node_count": len(node_list),
        "edge_count": len(edge_list),
        "symbol_count": sum(1 for node in node_list if node.node_type == "symbol"),
        "wire_point_count": sum(1 for node in node_list if node.node_type == "wire_point"),
        "junction_count": sum(1 for node in node_list if node.node_type == "junction"),
        "label_count": sum(1 for node in node_list if node.node_type in {"label", "global_label", "hierarchical_label"}),
        "symbol_attached_count": sum(1 for edge in edge_list if edge.edge_type == "symbol_attached"),
        "label_attached_count": sum(1 for edge in edge_list if edge.edge_type == "label_attached"),
    }
    return node_list, edge_list, metrics


def _parse_with_regex_fallback(text: str) -> tuple[list[GraphNode], list[GraphEdge], dict[str, Any]]:
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    point_nodes: dict[str, tuple[float, float]] = {}

    symbol_pattern = re.compile(
        r"\(symbol\b(?P<body>.{0,400}?)\(at\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))",
        flags=re.S,
    )
    wire_pair_pattern = re.compile(
        r"\(xy\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))\)\s*\(xy\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))\)",
    )
    label_pattern = re.compile(
        r"\((label|global_label|hierarchical_label)\s+\"([^\"]+)\".{0,200}?\(at\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))\s+([+-]?(?:\d+\.\d+|\d+|\.\d+))",
        flags=re.S,
    )

    symbol_idx = 0
    label_idx = 0

    for match in symbol_pattern.finditer(text):
        symbol_idx += 1
        x = float(match.group(2))
        y = float(match.group(3))
        body = match.group("body")
        ref_match = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', body)
        lib_match = re.search(r'\(lib_id\s+"([^"]+)"', body)
        value_match = re.search(r'\(property\s+"Value"\s+"([^"]+)"', body)
        reference = ref_match.group(1) if ref_match else None
        lib_id = lib_match.group(1) if lib_match else None
        value = value_match.group(1) if value_match else None
        node_id = f"symbol:{symbol_idx:05d}"
        _add_node(
            nodes,
            GraphNode(
                node_id=node_id,
                node_type="symbol",
                x=x,
                y=y,
                label=reference,
                symbol_class=_classify_symbol(lib_id, reference, value),
                attrs={"lib_id": lib_id, "reference": reference, "value": value},
            ),
        )

    for match in wire_pair_pattern.finditer(text):
        x1, y1, x2, y2 = map(float, match.groups())
        src_id = _point_id(x1, y1)
        dst_id = _point_id(x2, y2)
        point_nodes[src_id] = (x1, y1)
        point_nodes[dst_id] = (x2, y2)
        _add_node(nodes, GraphNode(node_id=src_id, node_type="wire_point", x=x1, y=y1))
        _add_node(nodes, GraphNode(node_id=dst_id, node_type="wire_point", x=x2, y=y2))
        _add_edge(
            edges,
            GraphEdge(
                src=src_id,
                dst=dst_id,
                edge_type="wire_segment",
                attrs={"length": _distance((x1, y1), (x2, y2))},
            ),
        )

    for match in label_pattern.finditer(text):
        label_idx += 1
        tag, text_label, x, y = match.groups()
        x_f, y_f = float(x), float(y)
        node_id = f"label:{label_idx:05d}"
        _add_node(
            nodes,
            GraphNode(node_id=node_id, node_type=tag, x=x_f, y=y_f, label=text_label),
        )
        nearest = _nearest_point((x_f, y_f), point_nodes, max_distance=1.0)
        if nearest is not None:
            _add_edge(edges, GraphEdge(src=node_id, dst=nearest, edge_type="label_attached"))

    for node in nodes.values():
        if node.node_type == "symbol" and node.x is not None and node.y is not None:
            nearest = _nearest_point((node.x, node.y), point_nodes, max_distance=1.0)
            if nearest is not None:
                _add_edge(edges, GraphEdge(src=node.node_id, dst=nearest, edge_type="symbol_attached"))

    node_list = sorted(nodes.values(), key=lambda node: node.node_id)
    edge_list = sorted(edges.values(), key=lambda edge: (edge.src, edge.dst, edge.edge_type))
    metrics = {
        "node_count": len(node_list),
        "edge_count": len(edge_list),
        "symbol_count": sum(1 for node in node_list if node.node_type == "symbol"),
        "wire_point_count": sum(1 for node in node_list if node.node_type == "wire_point"),
        "junction_count": sum(1 for node in node_list if node.node_type == "junction"),
        "label_count": sum(1 for node in node_list if node.node_type in {"label", "global_label", "hierarchical_label"}),
        "symbol_attached_count": sum(1 for edge in edge_list if edge.edge_type == "symbol_attached"),
        "label_attached_count": sum(1 for edge in edge_list if edge.edge_type == "label_attached"),
    }
    return node_list, edge_list, metrics


def parse_schematic_text(
    text: str,
) -> tuple[str, list[GraphNode], list[GraphEdge], dict[str, Any], str | None]:
    """Parse a schematic text payload into graph nodes and edges."""
    tokens = _tokenize(text)
    forms, balanced = _parse_s_expressions(tokens)

    if forms:
        all_forms: list[list[Any]] = []
        for root in forms:
            all_forms.extend(_iter_forms(root))
        nodes, edges, metrics = _parse_with_forms(all_forms)
        if nodes:
            metrics["balanced_parentheses"] = balanced
            return "sexpr", nodes, edges, metrics, None

    nodes, edges, metrics = _parse_with_regex_fallback(text)
    metrics["balanced_parentheses"] = balanced
    parse_error = None
    if not nodes:
        parse_error = "Unable to parse schematic into any nodes"
    return "fallback", nodes, edges, metrics, parse_error


def parse_schematic_file(
    schematic_path: Path,
    project_id: str,
    project_root: str,
) -> ParsedSchematic:
    """Parse a single `.kicad_sch` file into a graph record."""
    text = schematic_path.read_text(encoding="utf-8", errors="ignore")
    parse_mode, nodes, edges, metrics, parse_error = parse_schematic_text(text)
    return ParsedSchematic(
        project_id=project_id,
        project_root=project_root,
        schematic_path=schematic_path.as_posix(),
        parse_mode=parse_mode,
        nodes=nodes,
        edges=edges,
        metrics=metrics,
        parse_error=parse_error,
    )


def _resolve_schematic_path(path_text: str, root_path: str | None) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    if root_path:
        return (Path(root_path) / path).resolve()
    return path.resolve()


def run(manifest_jsonl: str | Path, output_jsonl: str | Path) -> int:
    """Parse schematics listed in a repository-manifest JSONL file."""
    manifest_path = Path(manifest_jsonl).expanduser().resolve()
    output_path = Path(output_jsonl).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with manifest_path.open("r", encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as sink:
        for line in source:
            stripped = line.strip()
            if not stripped:
                continue
            manifest = json.loads(stripped)
            project_id = str(manifest.get("project_id", ""))
            project_root = str(manifest.get("project_root", ""))
            root_path = manifest.get("root_path")
            schematic_files = manifest.get("schematic_files", [])

            for schematic_entry in schematic_files:
                if not isinstance(schematic_entry, dict) or "path" not in schematic_entry:
                    continue
                schematic_path = _resolve_schematic_path(str(schematic_entry["path"]), root_path)
                if not schematic_path.exists() or not schematic_path.is_file():
                    record = ParsedSchematic(
                        project_id=project_id,
                        project_root=project_root,
                        schematic_path=schematic_path.as_posix(),
                        parse_mode="missing",
                        nodes=[],
                        edges=[],
                        metrics={"node_count": 0, "edge_count": 0},
                        parse_error="Schematic file missing",
                    )
                else:
                    record = parse_schematic_file(
                        schematic_path=schematic_path,
                        project_id=project_id,
                        project_root=project_root,
                    )
                sink.write(json.dumps(asdict(record), sort_keys=True))
                sink.write("\n")
                written += 1

    return written


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse schematic manifests into graph JSONL")
    parser.add_argument("manifest_jsonl", help="Input project-manifest JSONL")
    parser.add_argument("output_jsonl", help="Output parsed-schematic JSONL")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    run(args.manifest_jsonl, args.output_jsonl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
