import os
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch
from torch.utils.data import Dataset

from .dense_corpus import DenseCorpusReader
from .grammar import parse_token_stream, validate_sequence
from .identity import COMPONENT_ID_KEY, build_local_id_bindings, ensure_component_ids
from .json_to_tokens import extract_placement_segment
from .token_model import ID_PAGE, VOCAB

PAD = "<PAD>"
UNK = "<UNK>"
_BIN_TOKEN_RE = re.compile(r"^(DX|DY)_(N|P)(\d+)$")
_BIN_ZERO_TEMPLATE = {"DX": "DX_0", "DY": "DY_0"}

_SIDE_TO_VEC = {
    "SIDE_LEFT": (-1, 0),
    "SIDE_RIGHT": (1, 0),
    "SIDE_UP": (0, 1),
    "SIDE_DOWN": (0, -1),
    "SIDE_CENTER": (0, 0),
}
_VEC_TO_SIDE = {v: k for k, v in _SIDE_TO_VEC.items()}

_DIR_TO_VEC = {
    "DIR_N": (0, 1),
    "DIR_NE": (1, 1),
    "DIR_E": (1, 0),
    "DIR_SE": (1, -1),
    "DIR_S": (0, -1),
    "DIR_SW": (-1, -1),
    "DIR_W": (-1, 0),
    "DIR_NW": (-1, 1),
    "DIR_CENTER": (0, 0),
}
_VEC_TO_DIR = {v: k for k, v in _DIR_TO_VEC.items()}
_ROT_ORDER = ["ROT_0", "ROT_90", "ROT_180", "ROT_270"]


@dataclass(frozen=True)
class _ObjectGeometrySpan:
    direction_idx: int
    dx_idx: int
    dy_idx: int
    rot_idx: int
    mirror_idx: int


@dataclass(frozen=True)
class _BlockSpan:
    start_idx: int
    end_idx: int
    objects: List[_ObjectGeometrySpan]


def token_path_to_ir_path(token_path: str) -> str:
    if token_path.endswith(".tokens.json"):
        return token_path[: -len(".tokens.json")] + ".ir.json"
    return token_path.replace(".tokens.json", ".ir.json")


def _is_parquet_path(path: str) -> bool:
    return path.endswith(".parquet")


def _is_dense_binary_path(path: str) -> bool:
    return path.endswith(".dense.bin")


def _discover_vocab_file(data_dir: str) -> Optional[Path]:
    base = Path(data_dir)
    candidates = [
        base / "vocab.json",
        base / "parquet" / "vocab.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_vocab_file(vocab_path: Path) -> List[str]:
    with vocab_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        tokens = data.get("tokens", [])
    else:
        tokens = data
    if not isinstance(tokens, list):
        raise ValueError(f"Invalid vocab file: {vocab_path}")
    return [str(token) for token in tokens]


def write_vocab_sidecar(out_dir: str | Path, tokens: List[str]) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    vocab_path = out_dir / "vocab.json"
    with vocab_path.open("w", encoding="utf-8") as f:
        json.dump({"tokens": list(tokens)}, f, indent=2, sort_keys=False)
        f.write("\n")
    return vocab_path


def build_vocab_tokens_from_records(records: List[Dict[str, Any]]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for token in VOCAB:
        if token not in seen:
            ordered.append(token)
            seen.add(token)
    for row in records:
        for token in row.get("tokens", []):
            token = str(token)
            if token not in seen:
                ordered.append(token)
                seen.add(token)
    return ordered


def discover_data_files(data_dir: str) -> List[str]:
    base = Path(data_dir)
    dense_files = sorted(str(path) for path in base.rglob("*.dense.bin"))
    if dense_files:
        return dense_files
    parquet_files = sorted(str(path) for path in base.rglob("*.parquet"))
    if parquet_files:
        return parquet_files
    raise FileNotFoundError(
        f"No dense corpus (*.dense.bin) or Parquet shards found under {data_dir}"
    )


def _load_parquet_rows(parquet_path: str) -> List[Dict[str, Any]]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"Found Parquet data at {parquet_path} but pyarrow is not installed."
        ) from exc

    table = pq.read_table(parquet_path)
    rows = table.to_pylist()
    if not isinstance(rows, list):
        return []
    return rows


def _load_sample_records(data_file: str) -> Tuple[List[List[str]], List[Optional[Dict[str, Any]]]]:
    if _is_parquet_path(data_file):
        rows = _load_parquet_rows(data_file)
        samples: List[List[str]] = []
        ir_contexts: List[Optional[Dict[str, Any]]] = []
        for row in rows:
            tokens = row.get("tokens", [])
            samples.append(list(tokens))
            ir_json = row.get("ir_context_json")
            relation_json = row.get("relation_context_json")
            if isinstance(ir_json, str) and ir_json:
                try:
                    ir_ctx = json.loads(ir_json)
                except json.JSONDecodeError:
                    ir_ctx = None
            else:
                ir_ctx = None

            if ir_ctx is not None and isinstance(relation_json, str) and relation_json:
                try:
                    relation_context = json.loads(relation_json)
                except json.JSONDecodeError:
                    relation_context = None
                if relation_context is not None:
                    ir_ctx = dict(ir_ctx)
                    ir_ctx["relation_context"] = relation_context
                    if isinstance(relation_context, dict):
                        ir_ctx["blocks"] = relation_context.get("blocks")
                        ir_ctx["token_positions"] = relation_context.get("token_positions")
            if ir_ctx is not None:
                ir_ctx = dict(ir_ctx)
                if "placement_tokens" not in ir_ctx:
                    ir_ctx["placement_tokens"] = extract_placement_segment(list(tokens))
                if "target_start_idx" not in ir_ctx:
                    try:
                        ir_ctx["target_start_idx"] = list(tokens).index("PLACE_START") + 1
                    except ValueError:
                        pass

            ir_contexts.append(ir_ctx)
        return samples, ir_contexts

    raise FileNotFoundError(f"Unsupported legacy token file path: {data_file}")


def build_stoi_from_token_files(token_files: List[str]) -> Tuple[Dict[str, int], Dict[int, str], int, int]:
    """Single vocabulary for train+val so val never sees OOV ids, plus explicit UNK for safety."""
    vocab_tokens: Optional[List[str]] = None
    vocab_candidates = {_discover_vocab_file(str(Path(f).parent)) for f in token_files}
    vocab_candidates.discard(None)
    if vocab_candidates:
        # Prefer the first discovered vocab sidecar and treat it as authoritative.
        vocab_path = sorted(vocab_candidates, key=lambda p: str(p))[0]
        vocab_tokens = _load_vocab_file(vocab_path)

    stoi: Dict[str, int] = {}
    if vocab_tokens:
        stoi = {token: i for i, token in enumerate(vocab_tokens)}
        stoi[PAD] = len(stoi)
        stoi[UNK] = len(stoi)
        itos = {i: t for t, i in stoi.items()}
        pad_idx = stoi[PAD]
        unk_idx = stoi[UNK]
        return stoi, itos, pad_idx, unk_idx
    else:
        stoi = {token: i for i, token in enumerate(VOCAB)}
        for fpath in token_files:
            samples, _ = _load_sample_records(fpath)
            for tokens in samples:
                for t in tokens:
                    if t not in stoi:
                        stoi[t] = len(stoi)
    stoi[PAD] = len(stoi)
    stoi[UNK] = len(stoi)
    itos = {i: t for t, i in stoi.items()}
    pad_idx = stoi[PAD]
    unk_idx = stoi[UNK]
    return stoi, itos, pad_idx, unk_idx


def _materialize_relation_context(
    tokens: List[str],
    ir_ctx: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if ir_ctx is None:
        return None
    relation_context = ir_ctx.get("relation_context")
    if isinstance(relation_context, dict) and (
        "blocks" in relation_context or "token_positions" in relation_context
    ):
        return relation_context

    placement_tokens = list(ir_ctx.get("placement_tokens") or []) if isinstance(ir_ctx, dict) else []
    if not placement_tokens:
        placement_tokens = extract_placement_segment(tokens)
    relation_context = _build_relation_context(placement_tokens, ir_ctx)
    if relation_context is None:
        return None
    ir_ctx["relation_context"] = relation_context
    ir_ctx["blocks"] = relation_context.get("blocks")
    ir_ctx["token_positions"] = relation_context.get("token_positions")
    return relation_context


def collate_xy_only(
    batch: List[Tuple[torch.Tensor, torch.Tensor, Optional[Dict[str, Any]]]],
) -> Tuple[torch.Tensor, torch.Tensor]:
    xs, ys, _ = zip(*batch)
    return torch.stack(xs, dim=0), torch.stack(ys, dim=0)


def collate_xy_ir(
    batch: List[Tuple[torch.Tensor, torch.Tensor, Optional[Dict[str, Any]]]],
) -> Tuple[torch.Tensor, torch.Tensor, List[Optional[Dict[str, Any]]]]:
    xs, ys, irs = zip(*batch)
    return torch.stack(xs, dim=0), torch.stack(ys, dim=0), list(irs)


def make_dynamic_xy_ir_collate(
    pad_idx: int,
) -> Callable[
    [List[Tuple[torch.Tensor, torch.Tensor, Optional[Dict[str, Any]]]]],
    Tuple[torch.Tensor, torch.Tensor, List[Optional[Dict[str, Any]]]],
]:
    """Build a collate_fn that pads each batch to the max sequence in that batch.

    Combined with the length-bucketed sampler this eliminates the >3x padding
    waste from always padding to the global block_size.
    """

    def _collate(
        batch: List[Tuple[torch.Tensor, torch.Tensor, Optional[Dict[str, Any]]]],
    ) -> Tuple[torch.Tensor, torch.Tensor, List[Optional[Dict[str, Any]]]]:
        xs, ys, irs = zip(*batch)
        max_len = max(int(x.size(0)) for x in xs)
        bsz = len(xs)
        x_dtype = xs[0].dtype
        y_dtype = ys[0].dtype
        x_out = torch.full((bsz, max_len), pad_idx, dtype=x_dtype)
        y_out = torch.full((bsz, max_len), pad_idx, dtype=y_dtype)
        for i, (x, y) in enumerate(zip(xs, ys)):
            xl = int(x.size(0))
            yl = int(y.size(0))
            x_out[i, :xl] = x
            y_out[i, :yl] = y
        return x_out, y_out, list(irs)

    return _collate


class LengthBucketBatchSampler(torch.utils.data.Sampler):
    """Yield batches of similar-length samples to minimize per-batch padding.

    Within a "mega-batch" of mega_batch_mult * batch_size samples we sort by
    length, slice into batches, then shuffle the resulting batches across the
    epoch. The effect: each batch has tightly clustered lengths (so the
    dynamic-padding collate keeps sequences short), but the model still sees
    near-random order at batch granularity.

    `lengths` must be the same length as the dataset view this sampler is
    attached to and indexed in the same way.
    """

    def __init__(
        self,
        lengths: List[int],
        batch_size: int,
        *,
        shuffle: bool = True,
        seed: int = 0,
        mega_batch_mult: int = 8,
        drop_last: bool = False,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        self._lengths = list(lengths)
        self._batch_size = int(batch_size)
        self._shuffle = bool(shuffle)
        self._base_seed = int(seed)
        self._mega_batch_size = max(self._batch_size, int(mega_batch_mult) * self._batch_size)
        self._drop_last = bool(drop_last)
        self._epoch = 0

    def set_epoch(self, epoch: int) -> None:
        self._epoch = int(epoch)

    def __iter__(self):
        n = len(self._lengths)
        indices = list(range(n))
        if self._shuffle:
            rng = random.Random(self._base_seed + self._epoch)
            rng.shuffle(indices)
        else:
            rng = random.Random(self._base_seed)

        batches: List[List[int]] = []
        for start in range(0, n, self._mega_batch_size):
            chunk = indices[start : start + self._mega_batch_size]
            chunk.sort(key=lambda i: self._lengths[i])
            for j in range(0, len(chunk), self._batch_size):
                batch = chunk[j : j + self._batch_size]
                if self._drop_last and len(batch) < self._batch_size:
                    continue
                batches.append(batch)

        if self._shuffle:
            rng.shuffle(batches)
        for batch in batches:
            yield batch

    def __len__(self) -> int:
        n = len(self._lengths)
        if self._drop_last:
            return n // self._batch_size
        return (n + self._batch_size - 1) // self._batch_size


class PhasedMixedBatchLoader:
    """Two-phase curriculum wrapper around two DataLoaders.

    Phase 1 (epoch < phase1_epochs): yield only from the per-block loader so
    the model converges quickly on local placement skill over short sequences.

    Phase 2 (epoch >= phase1_epochs): per-step Bernoulli(p=phase2_block_ratio)
    picks which source to pull the next batch from. When one iterator
    exhausts, the rest of the epoch continues from the other. This lets each
    epoch see every training record once (one full pass through each loader)
    while keeping the blocks / schematics mix close to the target ratio.

    Compatible with the existing train loop: it exposes __iter__, __len__,
    set_epoch and forwards set_epoch to any inner `batch_sampler.set_epoch`.

    `dataset` attribute is the first non-empty inner loader's dataset (used
    only for `_set_loader_max_len` style hooks; curriculum does not currently
    need live max_len shrinking).
    """

    def __init__(
        self,
        blocks_loader: Optional["torch.utils.data.DataLoader"],
        schem_loader: Optional["torch.utils.data.DataLoader"],
        *,
        phase1_epochs: int,
        phase2_block_ratio: float,
        seed: int = 42,
    ) -> None:
        if blocks_loader is None and schem_loader is None:
            raise ValueError("PhasedMixedBatchLoader needs at least one inner loader")
        self._blocks_loader = blocks_loader
        self._schem_loader = schem_loader
        self._phase1_epochs = max(0, int(phase1_epochs))
        self._phase2_block_ratio = float(phase2_block_ratio)
        if not (0.0 <= self._phase2_block_ratio <= 1.0):
            raise ValueError("phase2_block_ratio must be in [0, 1]")
        self._seed = int(seed)
        self._epoch = 0
        primary = blocks_loader if blocks_loader is not None else schem_loader
        self.dataset = getattr(primary, "dataset", None)

    @property
    def blocks_loader(self):
        return self._blocks_loader

    @property
    def schem_loader(self):
        return self._schem_loader

    @property
    def current_phase(self) -> int:
        return 1 if self._epoch < self._phase1_epochs else 2

    def set_epoch(self, epoch: int) -> None:
        self._epoch = int(epoch)
        for loader in (self._blocks_loader, self._schem_loader):
            if loader is None:
                continue
            sampler = getattr(loader, "batch_sampler", None)
            if sampler is not None and hasattr(sampler, "set_epoch"):
                sampler.set_epoch(int(epoch))

    def __len__(self) -> int:
        # Degenerate case: missing loader -> defer to whichever exists.
        if self._blocks_loader is None:
            return len(self._schem_loader) if self._schem_loader is not None else 0
        if self._schem_loader is None:
            return len(self._blocks_loader)
        if self.current_phase == 1:
            return len(self._blocks_loader)
        return len(self._blocks_loader) + len(self._schem_loader)

    def __iter__(self):
        if self._blocks_loader is None:
            yield from (self._schem_loader or [])
            return
        if self._schem_loader is None or self.current_phase == 1:
            yield from self._blocks_loader
            return

        # Phase 2: interleave with Bernoulli(p_block) per batch until one
        # iterator exhausts, then drain the other.
        rng = random.Random(self._seed + self._epoch)
        block_iter = iter(self._blocks_loader)
        schem_iter = iter(self._schem_loader)
        p_block = self._phase2_block_ratio
        block_done = False
        schem_done = False
        while not (block_done and schem_done):
            if block_done:
                use_block = False
            elif schem_done:
                use_block = True
            else:
                use_block = rng.random() < p_block
            try:
                if use_block:
                    yield next(block_iter)
                else:
                    yield next(schem_iter)
            except StopIteration:
                if use_block:
                    block_done = True
                else:
                    schem_done = True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_local_id_maps(ir_ctx: Optional[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, str]]:
    if not ir_ctx:
        return {}, {ID_PAGE: "PAGE"}
    bindings = build_local_id_bindings(ir_ctx)
    return dict(bindings.ref_to_local), dict(bindings.local_to_ref)


def _resolve_component_ref(ref_token: Optional[str], local_to_ref: Dict[str, str]) -> Optional[str]:
    if not ref_token:
        return None
    if ref_token in {ID_PAGE, "PAGE"}:
        return "PAGE"
    return local_to_ref.get(ref_token, ref_token)


def _bin_to_mm(bin_str: Optional[str], step_mm: float = 2.54) -> float:
    if not bin_str or bin_str.endswith("_0"):
        return 0.0
    try:
        magnitude = int(bin_str.split("_")[-1][1:])
    except (TypeError, ValueError, IndexError):
        return 0.0
    sign = 1.0 if "_P" in bin_str else -1.0
    return magnitude * step_mm * sign


def _page_grid_to_xy(page_grid_x: Optional[str], page_grid_y: Optional[str]) -> List[float]:
    return [
        _bin_to_mm(page_grid_x, step_mm=10.0),
        _bin_to_mm(page_grid_y, step_mm=10.0),
    ]


def _build_relation_context(tokens: List[str], ir_ctx: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if ir_ctx is None:
        return None

    blocks = []
    canonical_ir = ensure_component_ids(ir_ctx, in_place=False)
    components = {
        comp.get("ref"): comp
        for comp in canonical_ir.get("components", [])
        if isinstance(comp, dict)
    }
    _, local_to_ref = _build_local_id_maps(canonical_ir)
    try:
        parsed_blocks = parse_token_stream(extract_placement_segment(tokens))
    except Exception:
        parsed_blocks = []

    for block in parsed_blocks:
        block_entry: Dict[str, Any] = {
            "anchor_ref": block.anchor_ref,
            "anchor_pin": block.anchor_pin,
            "page_grid_x": block.page_grid_x,
            "page_grid_y": block.page_grid_y,
            "objects": [],
        }
        anchor_ref = _resolve_component_ref(block.anchor_ref, local_to_ref)
        anchor_comp = components.get(anchor_ref)
        if anchor_comp is not None:
            block_entry["anchor_xy"] = [
                _safe_float(anchor_comp.get("x")),
                _safe_float(anchor_comp.get("y")),
            ]
        else:
            block_entry["anchor_xy"] = _page_grid_to_xy(block.page_grid_x, block.page_grid_y)

        for obj in block.objects:
            rel = obj.pose.to_feature_dict()
            obj_ref = _resolve_component_ref(obj.ref, local_to_ref)
            obj_comp = components.get(obj_ref)
            if obj_comp is not None:
                obj_xy = [_safe_float(obj_comp.get("x")), _safe_float(obj_comp.get("y"))]
            else:
                obj_xy = [
                    block_entry["anchor_xy"][0] + _bin_to_mm(obj.pose.dx_bin),
                    block_entry["anchor_xy"][1] + _bin_to_mm(obj.pose.dy_bin),
                ]
            obj_entry: Dict[str, Any] = {
                "ref": obj.ref,
                "component_id": str(obj_comp.get(COMPONENT_ID_KEY) or "") if obj_comp is not None else "",
                "type": obj.type,
                "role": obj.role,
                "net_role": obj.net_role,
                "topology": obj.topology,
                "pose": rel,
                "xy": obj_xy,
            }
            block_entry["objects"].append(obj_entry)

        blocks.append(block_entry)

    token_positions: List[Dict[str, Any]] = []
    cursor = 0
    if tokens and tokens[0] == "BOS":
        token_positions.append(
            {"kind": "bos", "x": None, "y": None, "block_index": None, "object_index": None}
        )
        cursor = 1

    for block_index, (block, block_entry) in enumerate(zip(parsed_blocks, blocks)):
        anchor_xy = tuple(block_entry.get("anchor_xy", [0.0, 0.0]))
        block_tokens = block.to_tokens()
        object_cursor = 0

        j = 0
        while j < len(block_tokens):
            tok = block_tokens[j]
            if cursor >= len(tokens):
                break
            if tokens[cursor] != tok:
                if tok in {"VAL", "PKG", "NET_ROLE"}:
                    j += 2
                    continue
                break

            kind = "other"
            object_index: Optional[int] = None
            xy = anchor_xy

            if tok in {"ANCHOR_BLOCK", "REF", "PIN", "PAGE_GRID_X", "PAGE_GRID_Y", "ANCHOR_BLOCK_END"}:
                kind = "block"
            elif tok == "OBJ_START":
                kind = "object"
                if object_cursor < len(block_entry["objects"]):
                    xy = tuple(block_entry["objects"][object_cursor]["xy"])
                    object_index = object_cursor
            elif tok in {"TYPE", "VAL", "PKG", "ROLE", "NET_ROLE", "ANCHOR_REF", "ANCHOR_PIN", "TOPO"}:
                kind = "field"
                if object_cursor < len(block_entry["objects"]):
                    xy = tuple(block_entry["objects"][object_cursor]["xy"])
                    object_index = object_cursor
            elif (
                tok.startswith("TYPE_")
                or tok.startswith("ROLE_")
                or tok.startswith("TOPO_")
                or tok.startswith("NET_")
            ):
                kind = "object"
                if object_cursor < len(block_entry["objects"]):
                    xy = tuple(block_entry["objects"][object_cursor]["xy"])
                    object_index = object_cursor
            elif (
                tok.startswith("DX_")
                or tok.startswith("DY_")
                or tok.startswith("PAGE_X_")
                or tok.startswith("PAGE_Y_")
                or tok.startswith("DIST_")
                or tok.startswith("ROT_")
                or tok.startswith("MIRROR_")
                or tok.startswith("DIR_")
                or tok.startswith("SIDE_")
            ):
                kind = "geometry"
                if object_cursor < len(block_entry["objects"]):
                    xy = tuple(block_entry["objects"][object_cursor]["xy"])
                    object_index = object_cursor
            elif tok == "OBJ_END":
                kind = "object"
                if object_cursor < len(block_entry["objects"]):
                    xy = tuple(block_entry["objects"][object_cursor]["xy"])
                    object_index = object_cursor
                    object_cursor += 1

            token_positions.append(
                {
                    "kind": kind,
                    "x": xy[0],
                    "y": xy[1],
                    "block_index": block_index,
                    "object_index": object_index,
                }
            )
            cursor += 1
            j += 1

    while cursor < len(tokens):
        tok = tokens[cursor]
        token_positions.append(
            {
                "kind": "eos" if tok == "EOS" else "other",
                "x": None,
                "y": None,
                "block_index": None,
                "object_index": None,
            }
        )
        cursor += 1

    return {"blocks": blocks, "token_positions": token_positions}


def _parse_signed_bin_value(token: str, prefix: str) -> Optional[int]:
    if token == _BIN_ZERO_TEMPLATE[prefix]:
        return 0
    match = _BIN_TOKEN_RE.match(token)
    if not match:
        return None
    tok_prefix, sign, magnitude_str = match.groups()
    if tok_prefix != prefix:
        return None
    try:
        magnitude = int(magnitude_str)
    except ValueError:
        return None
    if magnitude <= 0:
        return None
    return magnitude if sign == "P" else -magnitude


def _format_signed_bin_value(prefix: str, value: int) -> str:
    if value == 0:
        return _BIN_ZERO_TEMPLATE[prefix]
    sign = "P" if value > 0 else "N"
    return f"{prefix}_{sign}{abs(value)}"


def _rotate_vec_90(vec: Tuple[int, int], k: int) -> Tuple[int, int]:
    x, y = vec
    k = k % 4
    if k == 0:
        return (x, y)
    if k == 1:
        return (y, -x)
    if k == 2:
        return (-x, -y)
    return (-y, x)


def _mirror_vec(vec: Tuple[int, int], axis: str) -> Tuple[int, int]:
    x, y = vec
    if axis == "X":
        return (x, -y)
    if axis == "Y":
        return (-x, y)
    return (x, y)


def _rotate_direction_token(token: str, k: int) -> Optional[str]:
    if k % 4 == 0:
        return token
    if token in _SIDE_TO_VEC:
        vec = _SIDE_TO_VEC[token]
        return _VEC_TO_SIDE.get(_rotate_vec_90(vec, k))
    if token in _DIR_TO_VEC:
        vec = _DIR_TO_VEC[token]
        return _VEC_TO_DIR.get(_rotate_vec_90(vec, k))
    return None


def _mirror_direction_token(token: str, axis: str) -> Optional[str]:
    if token in _SIDE_TO_VEC:
        vec = _SIDE_TO_VEC[token]
        return _VEC_TO_SIDE.get(_mirror_vec(vec, axis))
    if token in _DIR_TO_VEC:
        vec = _DIR_TO_VEC[token]
        return _VEC_TO_DIR.get(_mirror_vec(vec, axis))
    return None


def _rotate_rotation_token(token: str, k: int) -> Optional[str]:
    if token not in _ROT_ORDER:
        return None
    idx = _ROT_ORDER.index(token)
    return _ROT_ORDER[(idx + (k % 4)) % 4]


def _mirror_mirror_token(token: str, axis: str) -> Optional[str]:
    axis_token = f"MIRROR_{axis}"
    if token == "NO_MIRROR":
        return axis_token
    if token == axis_token:
        return "NO_MIRROR"
    if token in {"MIRROR_X", "MIRROR_Y"}:
        return None
    return None


def _scan_object_geometry_span(tokens: List[str], start_idx: int) -> Tuple[Optional[_ObjectGeometrySpan], int]:
    i = start_idx
    if i >= len(tokens) or tokens[i] != "OBJ_START":
        return None, start_idx + 1
    i += 1
    if i + 1 >= len(tokens) or tokens[i] != "TYPE":
        return None, start_idx + 1
    i += 2  # TYPE value
    if i < len(tokens) and tokens[i] == "VAL":
        if i + 1 >= len(tokens):
            return None, start_idx + 1
        i += 2
    if i < len(tokens) and tokens[i] == "PKG":
        if i + 1 >= len(tokens):
            return None, start_idx + 1
        i += 2
    if i + 1 >= len(tokens) or tokens[i] != "ROLE":
        return None, start_idx + 1
    i += 2
    if i < len(tokens) and tokens[i] == "NET_ROLE":
        if i + 1 >= len(tokens):
            return None, start_idx + 1
        i += 2
    if i + 1 >= len(tokens) or tokens[i] != "REF":
        return None, start_idx + 1
    i += 2
    if i + 1 >= len(tokens) or tokens[i] != "ANCHOR_REF":
        return None, start_idx + 1
    i += 2
    if i + 1 >= len(tokens) or tokens[i] != "ANCHOR_PIN":
        return None, start_idx + 1
    i += 2
    if i + 7 >= len(tokens):
        return None, start_idx + 1
    direction_idx = i
    dx_idx = i + 1
    dy_idx = i + 2
    # dist at i + 3
    if tokens[i + 4] != "TOPO":
        return None, start_idx + 1
    # topo value at i + 5
    rot_idx = i + 6
    mirror_idx = i + 7
    end_idx = i + 8
    if end_idx >= len(tokens) or tokens[end_idx] != "OBJ_END":
        return None, start_idx + 1
    return _ObjectGeometrySpan(direction_idx, dx_idx, dy_idx, rot_idx, mirror_idx), end_idx + 1


def _scan_anchor_blocks(tokens: List[str]) -> Optional[List[_BlockSpan]]:
    blocks: List[_BlockSpan] = []
    i = 0
    while i < len(tokens):
        if tokens[i] != "ANCHOR_BLOCK":
            return None
        start_idx = i
        i += 1
        if i + 1 >= len(tokens) or tokens[i] != "REF":
            return None
        i += 2
        if i + 1 >= len(tokens) or tokens[i] != "PIN":
            return None
        i += 2
        if i < len(tokens) and tokens[i] == "PAGE_GRID_X":
            if i + 3 >= len(tokens):
                return None
            if tokens[i + 2] != "PAGE_GRID_Y":
                return None
            i += 4
        objects: List[_ObjectGeometrySpan] = []
        while i < len(tokens) and tokens[i] != "ANCHOR_BLOCK_END":
            if tokens[i] != "OBJ_START":
                return None
            geometry_span, next_i = _scan_object_geometry_span(tokens, i)
            if geometry_span is None:
                return None
            objects.append(geometry_span)
            i = next_i
        if i >= len(tokens):
            return None
        blocks.append(_BlockSpan(start_idx, i, objects))
        i += 1
    return blocks


def _apply_rotation_to_object(tokens: List[str], span: _ObjectGeometrySpan, k: int) -> None:
    if k % 4 == 0:
        return
    dx = _parse_signed_bin_value(tokens[span.dx_idx], "DX")
    dy = _parse_signed_bin_value(tokens[span.dy_idx], "DY")
    if dx is not None and dy is not None:
        rotated_dx, rotated_dy = _rotate_vec_90((dx, dy), k)
        tokens[span.dx_idx] = _format_signed_bin_value("DX", rotated_dx)
        tokens[span.dy_idx] = _format_signed_bin_value("DY", rotated_dy)
    new_direction = _rotate_direction_token(tokens[span.direction_idx], k)
    if new_direction is not None:
        tokens[span.direction_idx] = new_direction
    new_rot = _rotate_rotation_token(tokens[span.rot_idx], k)
    if new_rot is not None:
        tokens[span.rot_idx] = new_rot


def _apply_mirror_to_object(tokens: List[str], span: _ObjectGeometrySpan, axis: str) -> None:
    mirrored_token = _mirror_mirror_token(tokens[span.mirror_idx], axis)
    if mirrored_token is None:
        return
    dx = _parse_signed_bin_value(tokens[span.dx_idx], "DX")
    dy = _parse_signed_bin_value(tokens[span.dy_idx], "DY")
    if axis == "X":
        if dy is not None:
            tokens[span.dy_idx] = _format_signed_bin_value("DY", -dy)
    elif axis == "Y":
        if dx is not None:
            tokens[span.dx_idx] = _format_signed_bin_value("DX", -dx)
    new_direction = _mirror_direction_token(tokens[span.direction_idx], axis)
    if new_direction is not None:
        tokens[span.direction_idx] = new_direction
    tokens[span.mirror_idx] = mirrored_token


def _apply_layout_augmentation(
    tokens: List[str],
    rotation_k: int = 0,
    mirror_axis: Optional[str] = None,
    shuffle_blocks: bool = False,
    rng: Optional[random.Random] = None,
) -> List[str]:
    seq = list(tokens)
    if not seq:
        return seq
    has_bos = seq[0] == "BOS"
    has_eos = seq[-1] == "EOS"
    core = seq[1:-1] if has_bos and has_eos else list(seq)
    blocks = _scan_anchor_blocks(core)
    if not blocks:
        return seq

    aug_core = list(core)
    rotation_k = rotation_k % 4
    mirror_axis = mirror_axis if mirror_axis in {"X", "Y"} else None

    for block in blocks:
        for obj in block.objects:
            if rotation_k:
                _apply_rotation_to_object(aug_core, obj, rotation_k)
            if mirror_axis:
                _apply_mirror_to_object(aug_core, obj, mirror_axis)

    if shuffle_blocks and len(blocks) > 1:
        block_segments = [aug_core[b.start_idx : b.end_idx + 1] for b in blocks]
        if rng is not None:
            rng.shuffle(block_segments)
        else:
            random.shuffle(block_segments)
        aug_core = [tok for segment in block_segments for tok in segment]

    candidate = ["BOS", *aug_core, "EOS"] if has_bos and has_eos else aug_core
    if has_bos and has_eos and not validate_sequence(candidate):
        return seq
    return candidate


def _augment_token_sequence(tokens: List[str], rng: Optional[random.Random] = None) -> List[str]:
    local_rng = rng if rng is not None else random
    rotation_k = 0
    if local_rng.random() < 0.6:
        rotation_k = local_rng.randint(0, 3)
    mirror_axis: Optional[str] = None
    if local_rng.random() < 0.3:
        mirror_axis = local_rng.choice(["X", "Y"])
    shuffle_blocks = local_rng.random() < 0.35
    return _apply_layout_augmentation(
        tokens,
        rotation_k=rotation_k,
        mirror_axis=mirror_axis,
        shuffle_blocks=shuffle_blocks,
        rng=local_rng if isinstance(local_rng, random.Random) else None,
    )


class SpatialLayoutDataset(Dataset):
    def __init__(
        self,
        token_files: List[str],
        max_len: int = 512,
        stoi: Optional[Dict[str, int]] = None,
        itos: Optional[Dict[int, str]] = None,
        pad_idx: Optional[int] = None,
        unk_idx: Optional[int] = None,
        scan_for_new_tokens: bool = True,
        enable_augmentation: bool = False,
        augmentation_fn: Optional[Callable[[List[str]], List[str]]] = None,
    ):
        self.token_files = token_files
        self.max_len = max_len
        self.enable_augmentation = enable_augmentation
        self.augmentation_fn = augmentation_fn if augmentation_fn is not None else _augment_token_sequence

        if stoi is not None:
            self.stoi = dict(stoi)
            self.itos = dict(itos) if itos is not None else {i: t for t, i in self.stoi.items()}
            self.pad_idx = pad_idx if pad_idx is not None else self.stoi[PAD]
            self.unk_idx = unk_idx if unk_idx is not None else self.stoi.get(UNK, self.pad_idx)
        else:
            self.stoi = {token: i for i, token in enumerate(VOCAB)}
            if PAD not in self.stoi:
                self.stoi[PAD] = len(self.stoi)
            if UNK not in self.stoi:
                self.stoi[UNK] = len(self.stoi)
                self.pad_idx = self.stoi[PAD]
                self.unk_idx = self.stoi[UNK]
        self.itos = {i: t for t, i in self.stoi.items()}
        if scan_for_new_tokens:
            for fpath in token_files:
                samples, _ = _load_sample_records(fpath)
                for tokens in samples:
                    for t in tokens:
                        if t not in self.stoi:
                            self.stoi[t] = len(self.stoi)
                            self.itos[self.stoi[t]] = t

        self.samples: List[List[str]] = []
        self.ir_contexts: List[Optional[Dict[str, Any]]] = []
        for fpath in token_files:
            samples, ir_contexts = _load_sample_records(fpath)
            self.samples.extend(samples)
            self.ir_contexts.extend(ir_contexts)

    def __len__(self):
        return len(self.samples)

    def encode(self, tokens: List[str]) -> List[int]:
        return [self.stoi.get(t, self.unk_idx) for t in tokens]

    def decode(self, ids: List[int]) -> List[str]:
        return [self.itos.get(i, UNK) for i in ids]

    def _get_item(self, idx: int, apply_augmentation: bool = False):
        tokens = list(self.samples[idx])
        if apply_augmentation:
            tokens = self.augmentation_fn(tokens)
        ids = self.encode(tokens)

        if len(ids) > self.max_len:
            ids = ids[: self.max_len]

        x = ids[:-1]
        y = ids[1:]
        visible_tokens = tokens[: len(x)]

        pad_len = self.max_len - 1 - len(x)
        x = x + [self.pad_idx] * pad_len
        y = y + [self.pad_idx] * pad_len

        ir_ctx = self.ir_contexts[idx]
        if ir_ctx is not None:
            ir_ctx = ensure_component_ids(dict(ir_ctx), in_place=False)
            ir_ctx.setdefault("placement_tokens", extract_placement_segment(tokens))
            if "target_start_idx" not in ir_ctx:
                try:
                    ir_ctx["target_start_idx"] = list(tokens).index("PLACE_START") + 1
                except ValueError:
                    pass
            relation_context = _materialize_relation_context(visible_tokens, ir_ctx)
            if relation_context is not None:
                # Keep the commonly used keys at the top level so model/training
                # code can consume the spatial annotations without special-casing.
                ir_ctx["blocks"] = relation_context.get("blocks")
                ir_ctx["token_positions"] = relation_context.get("token_positions")
        return torch.tensor(x), torch.tensor(y), ir_ctx

    def __getitem__(self, idx):
        return self._get_item(idx, apply_augmentation=self.enable_augmentation)


class DenseBinarySpatialLayoutDataset(Dataset):
    def __init__(
        self,
        dense_file: str,
        max_len: int = 512,
        enable_augmentation: bool = False,
        augmentation_fn: Optional[Callable[[List[str]], List[str]]] = None,
        pad_to_max_len: bool = True,
    ):
        self.reader = DenseCorpusReader(dense_file)
        self.max_len = max_len
        self.enable_augmentation = enable_augmentation
        self.augmentation_fn = augmentation_fn if augmentation_fn is not None else _augment_token_sequence
        self.stoi = dict(self.reader.stoi)
        self.itos = dict(self.reader.itos)
        self.pad_idx = self.stoi[PAD]
        self.unk_idx = self.stoi[UNK]
        # When True (legacy), every sample is padded to max_len here. When False
        # (used with the dynamic-padding collate), samples come back at their
        # natural length and the collate pads each batch to its own max.
        self.pad_to_max_len = bool(pad_to_max_len)

    def __len__(self) -> int:
        return len(self.reader)

    def encode(self, tokens: List[str]) -> List[int]:
        return [self.stoi.get(t, self.unk_idx) for t in tokens]

    def decode(self, ids: List[int]) -> List[str]:
        return [self.itos.get(i, UNK) for i in ids]

    @property
    def token_lengths(self) -> List[int]:
        # Cheap: pulled from the reader index (no token decoding required).
        # Used by length-bucketed samplers; this is the raw token count per
        # record, the per-sample x/y after the right-shift will be one shorter
        # but the relative ordering is unchanged.
        return self.reader.token_lengths

    @property
    def record_kinds(self) -> List[int]:
        # Per-record kind flag (0=schematic, 1=per-block). v1/v2 corpora emit
        # all zeros (everything treated as schematic).
        return self.reader.record_kinds

    def _get_item(self, idx: int, apply_augmentation: bool = False):
        # If augmentation is disabled the precomputed relation context (built
        # over the original tokens) is still valid. Augmentation rotates/mirrors
        # geometry, so we recompute on the fly when active.
        if apply_augmentation:
            tokens, components = self.reader.get_sample(idx)
            tokens = self.augmentation_fn(tokens)
            relation_context: Optional[Dict[str, Any]] = None
        else:
            tokens, components, relation_context = self.reader.get_sample_with_context(idx)
        ids = self.encode(tokens)

        if len(ids) > self.max_len:
            ids = ids[: self.max_len]

        x = ids[:-1]
        y = ids[1:]
        visible_tokens = tokens[: len(x)]

        if self.pad_to_max_len:
            pad_len = self.max_len - 1 - len(x)
            x = x + [self.pad_idx] * pad_len
            y = y + [self.pad_idx] * pad_len

        ir_ctx = {
            "components": [
                {
                    "ref": component.ref,
                    "x": component.x,
                    "y": component.y,
                    COMPONENT_ID_KEY: str(getattr(component, "component_id", "") or ""),
                }
                for component in components
            ]
        }
        ir_ctx = ensure_component_ids(ir_ctx, in_place=False)
        ir_ctx["placement_tokens"] = extract_placement_segment(tokens)
        try:
            ir_ctx["target_start_idx"] = list(tokens).index("PLACE_START") + 1
        except ValueError:
            pass
        if relation_context is None:
            relation_context = _materialize_relation_context(visible_tokens, ir_ctx)
        if relation_context is not None:
            ir_ctx["blocks"] = relation_context.get("blocks")
            ir_ctx["token_positions"] = relation_context.get("token_positions")
        return torch.tensor(x), torch.tensor(y), ir_ctx

    def __getitem__(self, idx):
        return self._get_item(idx, apply_augmentation=self.enable_augmentation)


class _DatasetIndexView(Dataset):
    def __init__(
        self,
        base_dataset: SpatialLayoutDataset,
        indices: List[int],
        apply_augmentation: bool = False,
    ):
        self.base_dataset = base_dataset
        self.indices = list(indices)
        self.apply_augmentation = apply_augmentation

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, idx: int):
        return self.base_dataset._get_item(
            self.indices[idx],
            apply_augmentation=self.apply_augmentation,
        )


def create_dataloaders(
    data_dir: str,
    batch_size: int = 8,
    train_split: float = 0.8,
    seed: int = 42,
    max_len: int = 512,
    train_augmentation: bool = False,
    *,
    dataset_fraction: float = 1.0,
    num_workers: int = 0,
    pin_memory: bool = False,
    persistent_workers: bool = False,
    collate_fn: Optional[
        Callable[
            [List[Tuple[torch.Tensor, torch.Tensor, Optional[Dict[str, Any]]]]],
            Any,
        ]
    ] = None,
    use_length_bucketed_sampler: bool = True,
    bucket_mega_batch_mult: int = 8,
    phase1_epochs: int = 0,
    phase2_block_ratio: float = 0.8,
):
    files = discover_data_files(data_dir)
    files.sort()
    random.seed(seed)
    random.shuffle(files)

    dense_files = [path for path in files if _is_dense_binary_path(path)]
    is_dense_corpus = bool(dense_files)
    if dense_files:
        if len(dense_files) != len(files) or len(dense_files) != 1:
            raise ValueError("Dense corpus support expects a single .dense.bin file in the data directory")
        # When length bucketing is on we keep raw per-sample lengths and let the
        # dynamic collate pad per-batch. Otherwise we keep the legacy
        # always-pad-to-max_len behaviour for the parquet codepath compatibility.
        full_ds = DenseBinarySpatialLayoutDataset(
            dense_files[0],
            max_len=max_len,
            pad_to_max_len=not use_length_bucketed_sampler,
        )
    else:
        vocab_file = _discover_vocab_file(data_dir)
        if vocab_file is not None:
            vocab_tokens = _load_vocab_file(vocab_file)
            stoi = {token: i for i, token in enumerate(vocab_tokens)}
            stoi[PAD] = len(stoi)
            stoi[UNK] = len(stoi)
            itos = {i: t for t, i in stoi.items()}
            pad_idx = stoi[PAD]
            unk_idx = stoi[UNK]
            scan_for_new_tokens = False
        else:
            stoi, itos, pad_idx, unk_idx = build_stoi_from_token_files(files)
            scan_for_new_tokens = True

        full_ds = SpatialLayoutDataset(
            files,
            max_len=max_len,
            stoi=stoi,
            itos=itos,
            pad_idx=pad_idx,
            unk_idx=unk_idx,
            scan_for_new_tokens=scan_for_new_tokens,
        )

    indices = list(range(len(full_ds)))
    rng = random.Random(seed)
    rng.shuffle(indices)
    df = float(dataset_fraction)
    if df < 1.0:
        if not (0.0 < df <= 1.0):
            raise ValueError("dataset_fraction must be in (0, 1]")
        raw = int(len(indices) * df)
        if len(indices) >= 2:
            n_keep = min(len(indices), max(2, raw))
        else:
            n_keep = 1
        indices = indices[:n_keep]
        print(
            f"create_dataloaders: dataset_fraction={df} -> {len(indices)} samples (before train/val split)",
            flush=True,
        )
    split_idx = int(len(indices) * train_split)
    if len(indices) > 1:
        split_idx = min(max(split_idx, 1), len(indices) - 1)
    train_idx = indices[:split_idx]
    val_idx = indices[split_idx:]

    train_ds = _DatasetIndexView(
        full_ds,
        train_idx,
        apply_augmentation=train_augmentation,
    )
    val_ds = _DatasetIndexView(
        full_ds,
        val_idx,
        apply_augmentation=False,
    )

    # When the dense corpus and bucketed sampler are active we use the dynamic
    # padding collate so each batch is padded to its own max sequence length
    # (large compute saving vs always padding to max_len).
    bucket_mode = is_dense_corpus and use_length_bucketed_sampler
    if collate_fn is not None:
        effective_collate = collate_fn
    elif bucket_mode:
        pad_idx = full_ds.pad_idx if hasattr(full_ds, "pad_idx") else 0
        effective_collate = make_dynamic_xy_ir_collate(int(pad_idx))
    else:
        # Default includes IR sidecars for val overlap metrics; training ignores
        # the third field unless it explicitly opts in.
        effective_collate = collate_xy_ir

    base_loader_kw: Dict[str, Any] = {
        "collate_fn": effective_collate,
        "num_workers": max(0, int(num_workers)),
        "pin_memory": bool(pin_memory),
    }
    if base_loader_kw["num_workers"] > 0:
        base_loader_kw["persistent_workers"] = bool(persistent_workers)

    if bucket_mode:
        # Build per-view length tables. The view holds indices into the base
        # dataset; the bucketed sampler indexes the view positionally.
        base_lengths = full_ds.token_lengths if hasattr(full_ds, "token_lengths") else [max_len] * len(full_ds)
        base_kinds = (
            full_ds.record_kinds
            if hasattr(full_ds, "record_kinds")
            else [0] * len(full_ds)
        )

        # Detect curriculum applicability: any block-kind record in the train
        # split AND at least one schematic-kind record (otherwise there's
        # nothing to mix).
        train_block_positions = [pos for pos, i in enumerate(train_idx) if int(base_kinds[i]) == 1]
        train_schem_positions = [pos for pos, i in enumerate(train_idx) if int(base_kinds[i]) == 0]
        curriculum_requested = int(phase1_epochs) > 0 or (0.0 < float(phase2_block_ratio) < 1.0)
        use_curriculum = bool(train_block_positions and train_schem_positions and curriculum_requested)

        # Val stays on schematic records only so eval metrics are directly
        # comparable across phases (phase-1 val would otherwise produce a
        # different task — per-block local-placement rather than full layout).
        val_positions_for_sampler = [pos for pos, i in enumerate(val_idx) if int(base_kinds[i]) == 0]
        if not val_positions_for_sampler:
            # No schematic records in the val split (e.g. pure per-block corpus):
            # fall back to the full val split for evaluation.
            val_positions_for_sampler = list(range(len(val_idx)))

        # Build the val view & sampler from schematic indices only.
        val_view_idx = [val_idx[p] for p in val_positions_for_sampler]
        val_ds = _DatasetIndexView(full_ds, val_view_idx, apply_augmentation=False)
        val_lengths = [int(base_lengths[i]) for i in val_view_idx]
        val_sampler = LengthBucketBatchSampler(
            val_lengths,
            batch_size=batch_size,
            shuffle=False,
            seed=seed,
            mega_batch_mult=bucket_mega_batch_mult,
            drop_last=False,
        )
        val_loader = torch.utils.data.DataLoader(
            val_ds,
            batch_sampler=val_sampler,
            **base_loader_kw,
        )

        if use_curriculum:
            # Curriculum: two train loaders (blocks, schem) wrapped in a
            # PhasedMixedBatchLoader. Each loader has its own length-bucketed
            # sampler so padding stays tight within each stream.
            train_block_view_idx = [train_idx[p] for p in train_block_positions]
            train_schem_view_idx = [train_idx[p] for p in train_schem_positions]
            blocks_view = _DatasetIndexView(full_ds, train_block_view_idx, apply_augmentation=train_augmentation)
            schem_view = _DatasetIndexView(full_ds, train_schem_view_idx, apply_augmentation=train_augmentation)
            blocks_lengths = [int(base_lengths[i]) for i in train_block_view_idx]
            schem_lengths = [int(base_lengths[i]) for i in train_schem_view_idx]
            blocks_sampler = LengthBucketBatchSampler(
                blocks_lengths,
                batch_size=batch_size,
                shuffle=True,
                seed=seed,
                mega_batch_mult=bucket_mega_batch_mult,
                drop_last=False,
            )
            schem_sampler = LengthBucketBatchSampler(
                schem_lengths,
                batch_size=batch_size,
                shuffle=True,
                seed=seed + 1,
                mega_batch_mult=bucket_mega_batch_mult,
                drop_last=False,
            )
            blocks_loader = torch.utils.data.DataLoader(
                blocks_view,
                batch_sampler=blocks_sampler,
                **base_loader_kw,
            )
            schem_loader = torch.utils.data.DataLoader(
                schem_view,
                batch_sampler=schem_sampler,
                **base_loader_kw,
            )
            print(
                f"Curriculum ON: phase1_epochs={int(phase1_epochs)} (blocks only), "
                f"phase2_block_ratio={float(phase2_block_ratio):.2f} "
                f"(blocks records={len(train_block_view_idx)}, schem records={len(train_schem_view_idx)})",
                flush=True,
            )
            train_loader = PhasedMixedBatchLoader(
                blocks_loader,
                schem_loader,
                phase1_epochs=int(phase1_epochs),
                phase2_block_ratio=float(phase2_block_ratio),
                seed=seed,
            )
        else:
            # No curriculum: keep the original single-loader behavior.
            train_lengths = [int(base_lengths[i]) for i in train_idx]
            train_sampler = LengthBucketBatchSampler(
                train_lengths,
                batch_size=batch_size,
                shuffle=True,
                seed=seed,
                mega_batch_mult=bucket_mega_batch_mult,
                drop_last=False,
            )
            train_loader = torch.utils.data.DataLoader(
                train_ds,
                batch_sampler=train_sampler,
                **base_loader_kw,
            )
            if curriculum_requested and not train_block_positions:
                print(
                    "Curriculum requested but corpus has no per-block (kind=1) records; "
                    "rebuild with --emit_per_block to enable. Falling back to single-loader mode.",
                    flush=True,
                )
    else:
        loader_kw = {
            "batch_size": batch_size,
            **base_loader_kw,
        }
        train_loader = torch.utils.data.DataLoader(
            train_ds,
            shuffle=True,
            **loader_kw,
        )
        val_loader = torch.utils.data.DataLoader(
            val_ds,
            shuffle=False,
            **loader_kw,
        )

    return train_loader, val_loader, full_ds.stoi
