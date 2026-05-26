from __future__ import annotations

import gzip
import json
import mmap
import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from sch2py.src.ir import extract_ir

from .identity import COMPONENT_ID_KEY, ensure_component_ids
from .json_to_tokens import build_training_sequence, placement_tokens, structure_tokens
from .token_model import ANCHOR_BLOCK, ANCHOR_BLOCK_END, BOS, EOS, SEP, PLACE_START, PLACE_END

REPO_ROOT = Path(__file__).resolve().parents[3]
PAD = "<PAD>"
UNK = "<UNK>"
_MAGIC = b"SLDBIN1\0"
# v1: legacy records with no relation context blob.
# v2: same record header + token + components, then u4 rc_len followed by rc_len JSON bytes.
# v3: record header extended with a u8 `kind` flag so a single file can mix
#     per-schematic records (kind=0) and per-block curriculum records (kind=1).
# v4: per-component payload stores both ref and component_id.
_VERSION = 4
_HEADER_STRUCT = struct.Struct("<8sIQQQQQ")
_HEADER_SIZE = 64
_RECORD_HEADER_V2_STRUCT = struct.Struct("<II")     # tokens_len, comps_len
_RECORD_HEADER_V3_STRUCT = struct.Struct("<IIB")    # tokens_len, comps_len, kind
_RECORD_HEADER_STRUCT = _RECORD_HEADER_V3_STRUCT    # active writer format
_TOKEN_ID_STRUCT = struct.Struct("<I")
_COMPONENT_HEADER_STRUCT = struct.Struct("<I")
_COMPONENT_HEADER_V4_STRUCT = struct.Struct("<II")
_COMPONENT_DATA_STRUCT = struct.Struct("<ff")
_RELATION_LEN_STRUCT = struct.Struct("<I")

# Record kinds. RECORD_KIND_SCHEMATIC = a full-schematic (possibly chunked on
# ANCHOR_BLOCK_END) record. RECORD_KIND_BLOCK = a single AnchorBlock record
# used by the phase-1 "local placement only" curriculum.
RECORD_KIND_SCHEMATIC = 0
RECORD_KIND_BLOCK = 1

# Default soft cap on tokens per record. Long schematics get split on
# ANCHOR_BLOCK_END boundaries so we keep all data without truncating mid-block.
DEFAULT_MAX_TOKENS_PER_RECORD = 1536

# Restrict HF dataset rows to actual KiCad sexpr schematics. The HF parquet has
# a `type` column with values like ".kicad_sch", ".sch" (mostly junk text), and
# ".schdoc" (Altium); the parser silently drops the latter two but ingesting them
# is a large IO+CPU waste.
_HF_KEEP_TYPES = {".kicad_sch"}

# Backward-compatible alias retained for tests and older callers that monkeypatch
# dense_corpus.ir_to_tokens during corpus-build smoke tests.
ir_to_tokens = build_training_sequence


@dataclass(frozen=True)
class DenseComponent:
    ref: str
    x: float
    y: float
    component_id: str = ""


def _require_pyarrow() -> Any:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only when dependency missing
        raise RuntimeError("Dense corpus export requires pyarrow to read the HF parquet shards") from exc
    return pq


def _iter_local_schematic_paths() -> Iterator[Path]:
    def _discover_scraped_schematic_roots() -> list[Path]:
        roots: list[Path] = []
        for base in (
            REPO_ROOT / "data" / "scraped_schematics",
            REPO_ROOT / "mcp" / "experiments" / "pcb_ft_q35" / "data" / "scraped_schematics",
        ):
            if not base.exists():
                continue
            for path in base.rglob("files"):
                if path.is_dir():
                    roots.append(path)
        return roots

    schematic_roots = [
        REPO_ROOT / "qa" / "data",
        REPO_ROOT / "demos",
        REPO_ROOT / "mcp" / "experiments" / "sch2py" / "examples",
        REPO_ROOT / "mcp" / "experiments" / "schematic_block_mvp",
        REPO_ROOT / "mcp" / "experiments" / "netlistsvg_probe",
        *_discover_scraped_schematic_roots(),
    ]

    seen: set[Path] = set()
    for root in schematic_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.kicad_sch")):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def _iter_hf_schematic_texts(hf_root: Path) -> Iterator[tuple[str, str]]:
    parquet_files = sorted((hf_root / "data").glob("*.parquet"))
    if not parquet_files:
        return

    pq = _require_pyarrow()
    for parquet_path in parquet_files:
        table = pq.ParquetFile(parquet_path)
        # Pull `type` so we can pre-filter to KiCad-format rows only. ~56% of HF
        # rows are non-KiCad (.sch/.schdoc); skipping them at parquet-read time
        # avoids loading their schematic text and the downstream parse attempt.
        wanted = ["schematic", "name"]
        schema_names = set(table.schema.names)
        if "type" in schema_names:
            wanted.append("type")
        for batch_index, batch in enumerate(table.iter_batches(batch_size=128, columns=wanted)):
            rows = batch.to_pylist()
            for row_index, row in enumerate(rows):
                row_type = row.get("type") if isinstance(row, dict) else None
                if isinstance(row_type, str) and row_type.strip().lower() not in _HF_KEEP_TYPES:
                    continue
                schematic = row.get("schematic")
                if not isinstance(schematic, str) or not schematic.strip():
                    continue
                name = row.get("name")
                if isinstance(name, str) and name.strip():
                    source_path = f"hf::{parquet_path.name}::{name.strip()}::{batch_index}:{row_index}"
                else:
                    source_path = f"hf::{parquet_path.name}::{batch_index}:{row_index}"
                yield source_path, schematic


def _iter_schematic_sources(
    *,
    include_hf: bool = True,
    hf_root: str | Path | None = None,
) -> Iterator[tuple[str, Path | None, str | None]]:
    for path in _iter_local_schematic_paths():
        yield str(path), path, None

    if not include_hf:
        return

    root = Path(hf_root).expanduser().resolve() if hf_root else REPO_ROOT / "mcp" / "experiments" / "pcb_ft_q35" / "data" / "open_schematics_hf"
    if root.exists():
        for source_path, schematic_text in _iter_hf_schematic_texts(root):
            yield source_path, None, schematic_text


def chunk_tokens_on_block_boundaries(
    tokens: Sequence[str],
    *,
    max_tokens_per_record: int = DEFAULT_MAX_TOKENS_PER_RECORD,
    blocks_per_record: Optional[int] = None,
) -> List[List[str]]:
    """Split a token sequence into chunks on ANCHOR_BLOCK_END boundaries.

    Two modes:
      * Default (`blocks_per_record=None`): pack as many complete blocks as fit
        within `max_tokens_per_record`. Single blocks larger than the cap are
        still emitted whole (we never split mid-block).
      * Fixed-group (`blocks_per_record=N`): always emit exactly N blocks per
        chunk (the last chunk may have fewer). Used by the phase-1 curriculum
        corpus where each record is a single AnchorBlock.

    Each chunk is a well-formed BOS + blocks + EOS sequence when the source
    has BOS/EOS wrapping. If the input has no parseable block structure the
    sequence is returned unchanged for the caller to decide.
    """
    seq = list(tokens)
    fixed_group = blocks_per_record is not None and blocks_per_record > 0
    if not fixed_group and len(seq) <= max_tokens_per_record:
        return [seq]

    has_bos = bool(seq) and seq[0] == BOS
    has_eos = bool(seq) and seq[-1] == EOS
    core = seq[1:-1] if (has_bos and has_eos) else seq

    # Walk core, collecting (start, end_exclusive) ranges for each ANCHOR_BLOCK ... ANCHOR_BLOCK_END.
    blocks: List[Tuple[int, int]] = []
    i = 0
    while i < len(core):
        if core[i] != ANCHOR_BLOCK:
            return [seq]  # malformed structure - don't split
        j = i + 1
        while j < len(core) and core[j] != ANCHOR_BLOCK_END:
            j += 1
        if j >= len(core):
            return [seq]  # unterminated block - don't split
        blocks.append((i, j + 1))
        i = j + 1

    if not blocks:
        return [seq]

    wrap_overhead = (1 if has_bos else 0) + (1 if has_eos else 0)
    chunks: List[List[str]] = []
    current: List[Tuple[int, int]] = []
    current_len = 0

    def _flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        chunk_core: List[str] = []
        for (start, end) in current:
            chunk_core.extend(core[start:end])
        chunk = []
        if has_bos:
            chunk.append(BOS)
        chunk.extend(chunk_core)
        if has_eos:
            chunk.append(EOS)
        chunks.append(chunk)
        current = []
        current_len = 0

    if fixed_group:
        group_size = int(blocks_per_record)
        for (start, end) in blocks:
            current.append((start, end))
            current_len += end - start
            if len(current) >= group_size:
                _flush()
        _flush()
        return chunks if chunks else [seq]

    for (start, end) in blocks:
        block_len = end - start
        candidate_len = current_len + block_len + wrap_overhead
        # Always emit at least one block per chunk, even if it exceeds the cap.
        if current and candidate_len > max_tokens_per_record:
            _flush()
        current.append((start, end))
        current_len += block_len

    _flush()
    return chunks if chunks else [seq]


def _build_chunked_training_sequences(
    ir: Dict[str, Any],
    *,
    max_tokens_per_record: int,
) -> List[List[str]]:
    struct = structure_tokens(ir)
    place = placement_tokens(ir)
    place_core = [BOS, *place[1:-1], EOS] if place[:1] == ["PLACE_START"] and place[-1:] == ["PLACE_END"] else [BOS, *place, EOS]
    place_chunks = chunk_tokens_on_block_boundaries(
        place_core,
        max_tokens_per_record=max_tokens_per_record,
    )
    combined: List[List[str]] = []
    for chunk in place_chunks:
        core = chunk[1:-1] if chunk[:1] == [BOS] and chunk[-1:] == [EOS] else list(chunk)
        combined.append([BOS, *struct, SEP, PLACE_START, *core, PLACE_END, EOS])
    return combined or [build_training_sequence(ir)]


def _build_per_block_training_sequences(
    ir: Dict[str, Any],
    *,
    blocks_per_record: int,
) -> List[List[str]]:
    struct = structure_tokens(ir)
    place = placement_tokens(ir)
    place_core = [BOS, *place[1:-1], EOS] if place[:1] == ["PLACE_START"] and place[-1:] == ["PLACE_END"] else [BOS, *place, EOS]
    place_chunks = chunk_tokens_on_block_boundaries(
        place_core,
        blocks_per_record=max(1, int(blocks_per_record)),
    )
    out: List[List[str]] = []
    for chunk in place_chunks:
        core = chunk[1:-1] if chunk[:1] == [BOS] and chunk[-1:] == [EOS] else list(chunk)
        out.append([BOS, *struct, SEP, PLACE_START, *core, PLACE_END, EOS])
    return out


# Marker prepended to gzipped relation-context blobs. Plain JSON blobs are
# detected by their leading "{" so the reader can transparently handle both.
_RC_GZIP_MARKER = b"\x1f\x8b"


def _slim_relation_context(rc: Dict[str, Any]) -> Dict[str, Any]:
    """Drop fields the training-time consumers do not read.

    Inspecting train.py: only `blocks[i].anchor_xy`, `blocks[i].objects[j]`'s
    `type` / `role` / `package_bin` / `xy`, and `token_positions[k]`'s
    `x`/`y`/`block_index`/`object_index` are used by the auxiliary losses /
    geometry validity checks. Stripping the rest cuts blob size ~5x without
    changing training behavior.
    """
    slim_blocks: List[Dict[str, Any]] = []
    for blk in rc.get("blocks", []) or []:
        if not isinstance(blk, dict):
            continue
        slim_objs: List[Dict[str, Any]] = []
        for obj in blk.get("objects") or []:
            if not isinstance(obj, dict):
                continue
            slim_obj: Dict[str, Any] = {"xy": obj.get("xy")}
            if "type" in obj:
                slim_obj["type"] = obj["type"]
            if "role" in obj:
                slim_obj["role"] = obj["role"]
            if "package_bin" in obj:
                slim_obj["package_bin"] = obj["package_bin"]
            slim_objs.append(slim_obj)
        slim_blocks.append({"anchor_xy": blk.get("anchor_xy"), "objects": slim_objs})

    slim_positions: List[Dict[str, Any]] = []
    for ann in rc.get("token_positions") or []:
        if not isinstance(ann, dict):
            slim_positions.append({})
            continue
        slim_ann: Dict[str, Any] = {}
        if ann.get("x") is not None:
            slim_ann["x"] = ann.get("x")
        if ann.get("y") is not None:
            slim_ann["y"] = ann.get("y")
        if ann.get("block_index") is not None:
            slim_ann["block_index"] = ann.get("block_index")
        if ann.get("object_index") is not None:
            slim_ann["object_index"] = ann.get("object_index")
        slim_positions.append(slim_ann)

    return {"blocks": slim_blocks, "token_positions": slim_positions}


def _precompute_relation_context_json(
    tokens: Sequence[str],
    components: Sequence[DenseComponent],
    *,
    compress: bool = True,
) -> Optional[bytes]:
    """Build the per-sample relation context dict and JSON-encode it.

    Returns gzipped JSON bytes by default (typically ~10x smaller for large
    schematics). Returns None if the build fails or yields nothing useful, in
    which case the dataset will fall back to lazy materialization at training
    time.
    """
    # Lazy import to avoid a circular import (dataset imports dense_corpus).
    try:
        from .dataset import _build_relation_context
    except Exception:  # pragma: no cover
        return None

    ir_ctx = {
        "components": [
            {
                "ref": c.ref,
                "x": c.x,
                "y": c.y,
                COMPONENT_ID_KEY: str(c.component_id or ""),
            }
            for c in components
        ]
    }
    try:
        relation_context = _build_relation_context(list(tokens), ir_ctx)
    except Exception:
        return None
    if not relation_context:
        return None
    try:
        slim = _slim_relation_context(relation_context)
        raw = json.dumps(slim, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if not compress:
        return raw
    return gzip.compress(raw, compresslevel=6)


def _decode_relation_context_blob(blob: bytes) -> Optional[Dict[str, Any]]:
    if not blob:
        return None
    if blob.startswith(_RC_GZIP_MARKER):
        try:
            blob = gzip.decompress(blob)
        except OSError:
            return None
    try:
        return json.loads(blob.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


class DenseCorpusWriter:
    def __init__(self, out_path: str | Path):
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.out_path.open("wb+")
        self._fh.write(b"\0" * _HEADER_SIZE)
        self._token_to_id: Dict[str, int] = {PAD: 0, UNK: 1}
        self._vocab: List[str] = [PAD, UNK]
        self.sequence_count = 0
        self.total_tokens = 0
        self.total_components = 0

    def close(self) -> None:
        if self._fh.closed:
            return
        vocab_offset = self._fh.tell()
        self._fh.write(struct.pack("<Q", len(self._vocab)))
        for token in self._vocab:
            encoded = token.encode("utf-8")
            self._fh.write(struct.pack("<I", len(encoded)))
            self._fh.write(encoded)

        self._fh.flush()
        self._fh.seek(0)
        header = _HEADER_STRUCT.pack(
            _MAGIC,
            _VERSION,
            self.sequence_count,
            self.total_tokens,
            self.total_components,
            len(self._vocab),
            vocab_offset,
        )
        self._fh.write(header)
        if _HEADER_SIZE > _HEADER_STRUCT.size:
            self._fh.write(b"\0" * (_HEADER_SIZE - _HEADER_STRUCT.size))
        self._fh.flush()
        self._fh.close()

    def __enter__(self) -> "DenseCorpusWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _token_id(self, token: str) -> int:
        token = str(token)
        token_id = self._token_to_id.get(token)
        if token_id is not None:
            return token_id
        token_id = len(self._vocab)
        self._token_to_id[token] = token_id
        self._vocab.append(token)
        return token_id

    def add_record(
        self,
        *,
        tokens: Sequence[str],
        components: Sequence[DenseComponent],
        relation_context_bytes: Optional[bytes] = None,
        kind: int = RECORD_KIND_SCHEMATIC,
    ) -> None:
        token_ids = [self._token_id(token) for token in tokens]
        component_entries = list(components)
        kind_byte = int(kind) & 0xFF

        self._fh.write(
            _RECORD_HEADER_V3_STRUCT.pack(len(token_ids), len(component_entries), kind_byte)
        )
        for token_id in token_ids:
            self._fh.write(_TOKEN_ID_STRUCT.pack(token_id))
        for component in component_entries:
            ref_bytes = component.ref.encode("utf-8")
            component_id_bytes = str(component.component_id or "").encode("utf-8")
            self._fh.write(_COMPONENT_HEADER_V4_STRUCT.pack(len(ref_bytes), len(component_id_bytes)))
            self._fh.write(ref_bytes)
            self._fh.write(component_id_bytes)
            self._fh.write(_COMPONENT_DATA_STRUCT.pack(float(component.x), float(component.y)))

        # v2 trailer: relation context JSON blob (length-prefixed). Length 0 means
        # the dataset must compute the relation context lazily at __getitem__ time.
        rc = relation_context_bytes or b""
        self._fh.write(_RELATION_LEN_STRUCT.pack(len(rc)))
        if rc:
            self._fh.write(rc)

        self.sequence_count += 1
        self.total_tokens += len(token_ids)
        self.total_components += len(component_entries)


class DenseCorpusReader:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = self.path.open("rb")
        self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        self._header = self._read_header()
        self.vocab = self._read_vocab()
        self.stoi = {token: idx for idx, token in enumerate(self.vocab)}
        self.itos = {idx: token for token, idx in self.stoi.items()}
        version = int(self._header["version"])
        self._has_relation_context = version >= 2
        self._has_record_kind = version >= 3
        self._has_component_id = version >= 4
        self._seq_index = self._build_index()
        # Cache the per-record token count so length-bucketed samplers can
        # query lengths without parsing tokens. Exposed via token_lengths.
        self._token_lengths = [entry[1] for entry in self._seq_index]
        # Per-record kind flag (v3+). v1/v2 records are all schematic.
        self._record_kinds = [entry[6] for entry in self._seq_index]

    def close(self) -> None:
        try:
            self._mm.close()
        finally:
            self._fh.close()

    def __enter__(self) -> "DenseCorpusReader":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def sequence_count(self) -> int:
        return int(self._header["sequence_count"])

    @property
    def total_tokens(self) -> int:
        return int(self._header["total_tokens"])

    @property
    def total_components(self) -> int:
        return int(self._header["total_components"])

    @property
    def vocab_offset(self) -> int:
        return int(self._header["vocab_offset"])

    @property
    def has_relation_context(self) -> bool:
        return bool(self._has_relation_context)

    @property
    def token_lengths(self) -> List[int]:
        return list(self._token_lengths)

    @property
    def record_kinds(self) -> List[int]:
        """Per-record kind flag. 0 = schematic record, 1 = per-block record.

        v1/v2 corpora report all records as schematic (kind=0).
        """
        return list(self._record_kinds)

    def _read_header(self) -> Dict[str, int | bytes]:
        if self._mm.size() < _HEADER_SIZE:
            raise ValueError(f"Dense corpus file is too small: {self.path}")
        magic, version, sequence_count, total_tokens, total_components, vocab_count, vocab_offset = _HEADER_STRUCT.unpack_from(self._mm, 0)
        if magic != _MAGIC:
            raise ValueError(f"Invalid dense corpus magic in {self.path}")
        if version not in (1, 2, 3, 4):
            raise ValueError(f"Unsupported dense corpus version {version} in {self.path}")
        return {
            "magic": magic,
            "version": version,
            "sequence_count": sequence_count,
            "total_tokens": total_tokens,
            "total_components": total_components,
            "vocab_count": vocab_count,
            "vocab_offset": vocab_offset,
        }

    def _read_vocab(self) -> List[str]:
        offset = self.vocab_offset
        vocab_count = int(self._header["vocab_count"])
        (stored_vocab_count,) = struct.unpack_from("<Q", self._mm, offset)
        offset += 8
        if stored_vocab_count != vocab_count:
            raise ValueError(
                f"Vocab count mismatch in {self.path}: header={vocab_count} stored={stored_vocab_count}"
            )

        vocab: List[str] = []
        for _ in range(vocab_count):
            (length,) = struct.unpack_from("<I", self._mm, offset)
            offset += 4
            token = self._mm[offset : offset + length].decode("utf-8")
            offset += length
            vocab.append(token)
        return vocab

    def _build_index(self) -> List[tuple[int, int, int, int, int, int, int]]:
        """Returns per-record tuple:
        (token_start, token_count, component_start, component_count, rc_start, rc_len, kind).

        For v1 corpora, rc_start == rc_len == 0. For v1/v2 corpora, kind is
        always 0 (schematic record).
        """
        index: List[tuple[int, int, int, int, int, int, int]] = []
        cursor = _HEADER_SIZE
        seq_count = self.sequence_count
        vocab_offset = self.vocab_offset
        header_struct = _RECORD_HEADER_V3_STRUCT if self._has_record_kind else _RECORD_HEADER_V2_STRUCT
        for _ in range(seq_count):
            if cursor >= vocab_offset:
                raise ValueError(f"Dense corpus record table ended early in {self.path}")
            if self._has_record_kind:
                token_count, component_count, kind = header_struct.unpack_from(self._mm, cursor)
            else:
                token_count, component_count = header_struct.unpack_from(self._mm, cursor)
                kind = RECORD_KIND_SCHEMATIC
            cursor += header_struct.size
            token_start = cursor
            cursor += token_count * _TOKEN_ID_STRUCT.size
            component_start = cursor
            for _component_idx in range(component_count):
                if self._has_component_id:
                    ref_len, component_id_len = _COMPONENT_HEADER_V4_STRUCT.unpack_from(self._mm, cursor)
                    cursor += _COMPONENT_HEADER_V4_STRUCT.size
                else:
                    (ref_len,) = _COMPONENT_HEADER_STRUCT.unpack_from(self._mm, cursor)
                    component_id_len = 0
                    cursor += _COMPONENT_HEADER_STRUCT.size
                cursor += ref_len
                cursor += component_id_len
                cursor += _COMPONENT_DATA_STRUCT.size
            if self._has_relation_context:
                (rc_len,) = _RELATION_LEN_STRUCT.unpack_from(self._mm, cursor)
                cursor += _RELATION_LEN_STRUCT.size
                rc_start = cursor
                cursor += rc_len
            else:
                rc_start = 0
                rc_len = 0
            index.append((token_start, token_count, component_start, component_count, rc_start, rc_len, int(kind)))

        if cursor != vocab_offset:
            raise ValueError(
                f"Dense corpus index did not end at the vocab table in {self.path}: cursor={cursor} vocab_offset={vocab_offset}"
            )
        return index

    def __len__(self) -> int:
        return len(self._seq_index)

    def _read_tokens(self, token_start: int, token_count: int) -> List[str]:
        tokens: List[str] = []
        cursor = token_start
        for _ in range(token_count):
            (token_id,) = _TOKEN_ID_STRUCT.unpack_from(self._mm, cursor)
            cursor += _TOKEN_ID_STRUCT.size
            tokens.append(self.itos.get(int(token_id), UNK))
        return tokens

    def _read_components(self, component_start: int, component_count: int) -> List[DenseComponent]:
        components: List[DenseComponent] = []
        cursor = component_start
        for _ in range(component_count):
            if self._has_component_id:
                ref_len, component_id_len = _COMPONENT_HEADER_V4_STRUCT.unpack_from(self._mm, cursor)
                cursor += _COMPONENT_HEADER_V4_STRUCT.size
            else:
                (ref_len,) = _COMPONENT_HEADER_STRUCT.unpack_from(self._mm, cursor)
                component_id_len = 0
                cursor += _COMPONENT_HEADER_STRUCT.size
            ref = self._mm[cursor : cursor + ref_len].decode("utf-8")
            cursor += ref_len
            component_id = (
                self._mm[cursor : cursor + component_id_len].decode("utf-8")
                if component_id_len > 0
                else ""
            )
            cursor += component_id_len
            x, y = _COMPONENT_DATA_STRUCT.unpack_from(self._mm, cursor)
            cursor += _COMPONENT_DATA_STRUCT.size
            components.append(DenseComponent(ref=ref, x=float(x), y=float(y), component_id=component_id))
        return components

    def _read_relation_context(self, rc_start: int, rc_len: int) -> Optional[Dict[str, Any]]:
        if rc_len <= 0:
            return None
        return _decode_relation_context_blob(bytes(self._mm[rc_start : rc_start + rc_len]))

    def get_sample(self, idx: int) -> tuple[List[str], List[DenseComponent]]:
        entry = self._seq_index[idx]
        token_start, token_count, component_start, component_count = entry[0], entry[1], entry[2], entry[3]
        return self._read_tokens(token_start, token_count), self._read_components(component_start, component_count)

    def get_sample_with_context(
        self, idx: int
    ) -> tuple[List[str], List[DenseComponent], Optional[Dict[str, Any]]]:
        entry = self._seq_index[idx]
        token_start, token_count, component_start, component_count, rc_start, rc_len = entry[0:6]
        tokens = self._read_tokens(token_start, token_count)
        components = self._read_components(component_start, component_count)
        relation_context = self._read_relation_context(rc_start, rc_len)
        return tokens, components, relation_context

    def get_token_count(self, idx: int) -> int:
        return int(self._seq_index[idx][1])

    def get_record_kind(self, idx: int) -> int:
        return int(self._seq_index[idx][6])


def build_dense_corpus(
    out_path: str | Path,
    *,
    include_hf: bool = True,
    hf_root: str | Path | None = None,
    limit: int | None = None,
    max_tokens_per_record: int = DEFAULT_MAX_TOKENS_PER_RECORD,
    precompute_relation_context: bool = True,
    emit_per_block: bool = False,
    blocks_per_record: int = 1,
    progress_every: int = 1000,
) -> Dict[str, int]:
    """Stream schematics into a dense binary corpus.

    When `emit_per_block` is True, every source schematic contributes both:
      * the usual per-schematic records (chunked on ANCHOR_BLOCK_END to stay
        within `max_tokens_per_record`), and
      * a sequence of per-block records (each grouping `blocks_per_record`
        consecutive anchor blocks, default 1).

    The per-block records are tagged with `kind=RECORD_KIND_BLOCK` so the
    curriculum loader can treat them as a separate stream. Downstream training
    opts into the curriculum by looking at `reader.record_kinds`; consumers
    that ignore the flag treat every record the same.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    stats = {
        "schematics": 0,              # source schematics ingested
        "records_written": 0,         # total records written (schematic + block)
        "records_written_schematic": 0,
        "records_written_block": 0,
        "long_chunked": 0,            # source schematics whose schematic-records were split into >1 chunks
        "skipped": 0,
        "errors": 0,
        "empty_schematic_text": 0,
        "parse_errors": 0,
        "no_components": 0,
        "tokenization_errors": 0,
    }

    with tempfile.TemporaryDirectory(prefix="spatial_layout_hf_") as tmpdir, DenseCorpusWriter(out_path) as writer:
        temp_sch = Path(tmpdir) / "hf_materialized.kicad_sch"
        for source_path, local_path, schematic_text in _iter_schematic_sources(include_hf=include_hf, hf_root=hf_root):
            if limit is not None and limit > 0 and stats["schematics"] >= limit:
                break
            try:
                if local_path is None and (not isinstance(schematic_text, str) or not schematic_text.strip()):
                    stats["empty_schematic_text"] += 1
                    stats["skipped"] += 1
                    continue
                if local_path is not None:
                    try:
                        ir = extract_ir(str(local_path))
                    except Exception:
                        stats["parse_errors"] += 1
                        stats["skipped"] += 1
                        continue
                else:
                    assert schematic_text is not None
                    temp_sch.write_text(schematic_text, encoding="utf-8")
                    try:
                        ir = extract_ir(str(temp_sch))
                    except Exception:
                        stats["parse_errors"] += 1
                        stats["skipped"] += 1
                        continue
                ir = ensure_component_ids(ir, in_place=False)
                tokens = ir_to_tokens(ir)
                components = [
                    DenseComponent(
                        ref=str(comp.get("ref", "")),
                        x=float(comp.get("x", 0.0) or 0.0),
                        y=float(comp.get("y", 0.0) or 0.0),
                        component_id=str(comp.get(COMPONENT_ID_KEY) or ""),
                    )
                    for comp in ir.get("components", [])
                    if isinstance(comp, dict) and comp.get("ref")
                ]
                if not tokens or not components:
                    if not components:
                        stats["no_components"] += 1
                    else:
                        stats["tokenization_errors"] += 1
                    stats["skipped"] += 1
                    continue

                if SEP in tokens and PLACE_START in tokens:
                    token_chunks = _build_chunked_training_sequences(
                        ir,
                        max_tokens_per_record=max_tokens_per_record,
                    )
                else:
                    token_chunks = chunk_tokens_on_block_boundaries(
                        tokens,
                        max_tokens_per_record=max_tokens_per_record,
                    )
                if len(token_chunks) > 1:
                    stats["long_chunked"] += 1

                for chunk_tokens in token_chunks:
                    rc_bytes: Optional[bytes] = None
                    if precompute_relation_context:
                        rc_bytes = _precompute_relation_context_json(chunk_tokens, components)
                    writer.add_record(
                        tokens=chunk_tokens,
                        components=components,
                        relation_context_bytes=rc_bytes,
                        kind=RECORD_KIND_SCHEMATIC,
                    )
                    stats["records_written"] += 1
                    stats["records_written_schematic"] += 1

                if emit_per_block:
                    if SEP in tokens and PLACE_START in tokens:
                        block_chunks = _build_per_block_training_sequences(
                            ir,
                            blocks_per_record=max(1, int(blocks_per_record)),
                        )
                    else:
                        block_chunks = chunk_tokens_on_block_boundaries(
                            tokens,
                            blocks_per_record=max(1, int(blocks_per_record)),
                        )
                    # Only emit per-block records when the source actually split
                    # into >1 block (else the per-block record would duplicate
                    # the full-schematic record).
                    if len(block_chunks) > 1:
                        for block_tokens in block_chunks:
                            rc_bytes = None
                            if precompute_relation_context:
                                rc_bytes = _precompute_relation_context_json(block_tokens, components)
                            writer.add_record(
                                tokens=block_tokens,
                                components=components,
                                relation_context_bytes=rc_bytes,
                                kind=RECORD_KIND_BLOCK,
                            )
                            stats["records_written"] += 1
                            stats["records_written_block"] += 1

                stats["schematics"] += 1
                if progress_every and stats["schematics"] % progress_every == 0:
                    print(
                        f"[dense_corpus] schematics={stats['schematics']} records={stats['records_written']} "
                        f"(schem={stats['records_written_schematic']} block={stats['records_written_block']}) "
                        f"chunked={stats['long_chunked']} skipped={stats['skipped']} last={source_path}",
                        flush=True,
                    )
            except Exception:
                stats["errors"] += 1

    stats["out_path"] = str(out_path)
    stats["max_tokens_per_record"] = int(max_tokens_per_record)
    stats["precompute_relation_context"] = bool(precompute_relation_context)
    stats["emit_per_block"] = bool(emit_per_block)
    stats["blocks_per_record"] = int(blocks_per_record) if emit_per_block else 0
    return stats


__all__ = [
    "DEFAULT_MAX_TOKENS_PER_RECORD",
    "RECORD_KIND_SCHEMATIC",
    "RECORD_KIND_BLOCK",
    "DenseComponent",
    "DenseCorpusReader",
    "DenseCorpusWriter",
    "build_dense_corpus",
    "chunk_tokens_on_block_boundaries",
]
