from pathlib import Path

from spatial_layout.dense_corpus import (
    DEFAULT_MAX_TOKENS_PER_RECORD,
    RECORD_KIND_BLOCK,
    RECORD_KIND_SCHEMATIC,
    DenseComponent,
    DenseCorpusReader,
    DenseCorpusWriter,
    chunk_tokens_on_block_boundaries,
)


def test_dense_corpus_roundtrip(tmp_path: Path):
    out_path = tmp_path / "corpus.dense.bin"

    with DenseCorpusWriter(out_path) as writer:
        writer.add_record(
            tokens=["BOS", "ANCHOR_BLOCK", "EOS"],
            components=[DenseComponent(ref="U1", x=10.5, y=-2.25)],
        )
        writer.add_record(
            tokens=["BOS", "OBJ_START", "OBJ_END", "EOS"],
            components=[
                DenseComponent(ref="R1", x=0.0, y=0.0),
                DenseComponent(ref="C1", x=2.54, y=2.54),
            ],
        )

    with DenseCorpusReader(out_path) as reader:
        assert reader.sequence_count == 2
        assert reader.total_tokens == 7
        assert len(reader.vocab) >= 4
        assert reader.has_relation_context  # v2 format

        tokens0, comps0 = reader.get_sample(0)
        tokens1, comps1 = reader.get_sample(1)

        assert tokens0 == ["BOS", "ANCHOR_BLOCK", "EOS"]
        assert tokens1 == ["BOS", "OBJ_START", "OBJ_END", "EOS"]
        assert comps0[0].ref == "U1"
        assert comps0[0].x == 10.5
        assert comps0[0].y == -2.25
        assert [c.ref for c in comps1] == ["R1", "C1"]

        # Records written with relation_context_bytes=None come back as None.
        _t, _c, rc0 = reader.get_sample_with_context(0)
        assert rc0 is None

        # Token-length sidecar is populated for length-bucketed sampling.
        assert reader.token_lengths == [3, 4]


def test_dense_corpus_roundtrip_with_relation_context(tmp_path: Path):
    out_path = tmp_path / "corpus_rc.dense.bin"
    rc_blob = b'{"blocks":[{"anchor_xy":[1.0,2.0]}],"token_positions":[]}'
    with DenseCorpusWriter(out_path) as writer:
        writer.add_record(
            tokens=["BOS", "ANCHOR_BLOCK", "EOS"],
            components=[DenseComponent(ref="U1", x=0.0, y=0.0)],
            relation_context_bytes=rc_blob,
        )
    with DenseCorpusReader(out_path) as reader:
        tokens, comps, rc = reader.get_sample_with_context(0)
        assert tokens == ["BOS", "ANCHOR_BLOCK", "EOS"]
        assert comps[0].ref == "U1"
        assert isinstance(rc, dict)
        assert rc["blocks"][0]["anchor_xy"] == [1.0, 2.0]


def _block(anchor_id: str, n_objs: int) -> list[str]:
    """Synthesize a minimal ANCHOR_BLOCK ... ANCHOR_BLOCK_END token range."""
    tokens = ["ANCHOR_BLOCK", "REF", anchor_id, "PIN", "1"]
    for _ in range(n_objs):
        tokens.extend(["OBJ_START", "FILLER", "OBJ_END"])
    tokens.append("ANCHOR_BLOCK_END")
    return tokens


def test_chunk_no_split_when_under_cap():
    tokens = ["BOS", *_block("ID_0", 2), *_block("ID_1", 2), "EOS"]
    chunks = chunk_tokens_on_block_boundaries(tokens, max_tokens_per_record=1024)
    assert chunks == [tokens]


def test_chunk_splits_on_anchor_block_end():
    # Each block is ~10 tokens; with cap=24 we should get a chunk per block.
    blocks = [_block(f"ID_{i}", 2) for i in range(4)]
    flat = ["BOS", *[t for b in blocks for t in b], "EOS"]
    chunks = chunk_tokens_on_block_boundaries(flat, max_tokens_per_record=14)
    # Each chunk: BOS + one block + EOS, length 12; well under cap.
    assert len(chunks) == 4
    for chunk in chunks:
        assert chunk[0] == "BOS"
        assert chunk[-1] == "EOS"
        assert chunk.count("ANCHOR_BLOCK") == 1
        assert chunk.count("ANCHOR_BLOCK_END") == 1


def test_chunk_keeps_oversized_single_block():
    # A single mega-block bigger than the cap should still be emitted (one chunk).
    big = _block("ID_0", 100)  # well over 24 tokens
    flat = ["BOS", *big, "EOS"]
    chunks = chunk_tokens_on_block_boundaries(flat, max_tokens_per_record=24)
    assert len(chunks) == 1
    assert chunks[0][0] == "BOS"
    assert chunks[0][-1] == "EOS"


def test_chunk_groups_blocks_up_to_cap():
    blocks = [_block(f"ID_{i}", 1) for i in range(6)]  # each block is 9 tokens
    flat = ["BOS", *[t for b in blocks for t in b], "EOS"]
    # Cap of 30 should fit roughly 3 blocks per chunk (9*3 + 2 wrap = 29).
    chunks = chunk_tokens_on_block_boundaries(flat, max_tokens_per_record=30)
    assert 2 <= len(chunks) <= 3
    # Concatenated cores must equal the original interior
    rebuilt = []
    for c in chunks:
        rebuilt.extend(c[1:-1])
    assert rebuilt == [t for b in blocks for t in b]


def test_chunk_default_cap_is_reasonable():
    # Sanity: default cap is at least 1024 (so a typical schematic stays whole)
    assert DEFAULT_MAX_TOKENS_PER_RECORD >= 1024


def test_chunk_blocks_per_record_one():
    # blocks_per_record=1 forces every block into its own chunk regardless of
    # token budget (used to build the phase-1 curriculum corpus).
    blocks = [_block(f"ID_{i}", 2) for i in range(4)]
    flat = ["BOS", *[t for b in blocks for t in b], "EOS"]
    chunks = chunk_tokens_on_block_boundaries(flat, blocks_per_record=1)
    assert len(chunks) == 4
    for chunk in chunks:
        assert chunk[0] == "BOS"
        assert chunk[-1] == "EOS"
        assert chunk.count("ANCHOR_BLOCK") == 1
        assert chunk.count("ANCHOR_BLOCK_END") == 1


def test_chunk_blocks_per_record_groups():
    # blocks_per_record=2 packs 2 blocks per chunk (last chunk may be short).
    blocks = [_block(f"ID_{i}", 1) for i in range(5)]
    flat = ["BOS", *[t for b in blocks for t in b], "EOS"]
    chunks = chunk_tokens_on_block_boundaries(flat, blocks_per_record=2)
    assert len(chunks) == 3  # 2+2+1
    assert chunks[0].count("ANCHOR_BLOCK") == 2
    assert chunks[1].count("ANCHOR_BLOCK") == 2
    assert chunks[2].count("ANCHOR_BLOCK") == 1


def test_v3_record_kind_roundtrip(tmp_path: Path):
    out_path = tmp_path / "corpus_v3.dense.bin"
    with DenseCorpusWriter(out_path) as writer:
        writer.add_record(
            tokens=["BOS", "ANCHOR_BLOCK", "ANCHOR_BLOCK_END", "EOS"],
            components=[DenseComponent(ref="U1", x=0.0, y=0.0)],
            kind=RECORD_KIND_SCHEMATIC,
        )
        writer.add_record(
            tokens=["BOS", "ANCHOR_BLOCK", "ANCHOR_BLOCK_END", "EOS"],
            components=[DenseComponent(ref="U1", x=0.0, y=0.0)],
            kind=RECORD_KIND_BLOCK,
        )
        writer.add_record(
            tokens=["BOS", "ANCHOR_BLOCK", "ANCHOR_BLOCK_END", "EOS"],
            components=[DenseComponent(ref="U1", x=0.0, y=0.0)],
            kind=RECORD_KIND_BLOCK,
        )

    with DenseCorpusReader(out_path) as reader:
        assert reader.sequence_count == 3
        assert reader.record_kinds == [
            RECORD_KIND_SCHEMATIC,
            RECORD_KIND_BLOCK,
            RECORD_KIND_BLOCK,
        ]
        assert reader.get_record_kind(0) == RECORD_KIND_SCHEMATIC
        assert reader.get_record_kind(1) == RECORD_KIND_BLOCK


def test_v2_reader_backcompat_reports_all_schematic(tmp_path: Path):
    # v1/v2 files have no kind byte; the reader must report everything as
    # schematic-kind so old corpora still work unchanged.
    import struct

    out_path = tmp_path / "v2.dense.bin"
    # Hand-construct a minimal valid v2 corpus: header + one record (no RC) + vocab.
    from spatial_layout.dense_corpus import (
        _HEADER_SIZE,
        _HEADER_STRUCT,
        _MAGIC,
        _RELATION_LEN_STRUCT,
    )

    with open(out_path, "wb") as fh:
        fh.write(b"\0" * _HEADER_SIZE)
        record_body_start = fh.tell()
        # v2 record header: <II tokens_len comps_len>
        fh.write(struct.pack("<II", 2, 0))
        fh.write(struct.pack("<II", 0, 1))  # 2 token ids: <PAD>=0, custom=1
        fh.write(_RELATION_LEN_STRUCT.pack(0))
        vocab_offset = fh.tell()
        fh.write(struct.pack("<Q", 3))  # 3 vocab entries
        for tok in ("<PAD>", "<UNK>", "TOK"):
            enc = tok.encode("utf-8")
            fh.write(struct.pack("<I", len(enc)))
            fh.write(enc)
        fh.seek(0)
        fh.write(
            _HEADER_STRUCT.pack(
                _MAGIC, 2, 1, 2, 0, 3, vocab_offset,
            )
        )
        pad_remaining = _HEADER_SIZE - _HEADER_STRUCT.size
        if pad_remaining > 0:
            fh.seek(_HEADER_STRUCT.size)
            fh.write(b"\0" * pad_remaining)

    with DenseCorpusReader(out_path) as reader:
        assert reader.sequence_count == 1
        assert reader.record_kinds == [RECORD_KIND_SCHEMATIC]


def test_build_emit_per_block_tags_kinds(tmp_path: Path):
    # End-to-end: build_dense_corpus with emit_per_block=True on a synthetic
    # source must produce both schematic-kind and block-kind records.
    import types

    from spatial_layout import dense_corpus as dc

    # Fake IR + tokens so we don't need a real schematic source on disk.
    fake_ir = {"components": [{"ref": "U1", "x": 0.0, "y": 0.0}, {"ref": "R1", "x": 1.0, "y": 1.0}]}
    fake_tokens = [
        "BOS",
        *_block("ID_0", 1),
        *_block("ID_1", 1),
        "EOS",
    ]

    def _fake_extract_ir(_path: str):
        return fake_ir

    def _fake_iter(include_hf: bool, hf_root):
        # One local-path source is enough (the builder tries extract_ir on it).
        yield (Path("fake.kicad_sch"), Path("fake.kicad_sch"), None)

    def _fake_ir_to_tokens(_ir):
        return list(fake_tokens)

    orig_iter = dc._iter_schematic_sources
    orig_extract = dc.extract_ir
    orig_tok = dc.ir_to_tokens
    try:
        dc._iter_schematic_sources = _fake_iter
        dc.extract_ir = _fake_extract_ir
        dc.ir_to_tokens = _fake_ir_to_tokens
        out_path = tmp_path / "corpus.bin"
        stats = dc.build_dense_corpus(
            out_path,
            include_hf=False,
            emit_per_block=True,
            blocks_per_record=1,
            precompute_relation_context=False,
            progress_every=0,
        )
    finally:
        dc._iter_schematic_sources = orig_iter
        dc.extract_ir = orig_extract
        dc.ir_to_tokens = orig_tok

    assert stats["records_written_schematic"] >= 1
    assert stats["records_written_block"] >= 2  # two blocks in fake_tokens
    with DenseCorpusReader(out_path) as reader:
        kinds = reader.record_kinds
        assert RECORD_KIND_SCHEMATIC in kinds
        assert RECORD_KIND_BLOCK in kinds
        # Per-block records each carry exactly one ANCHOR_BLOCK.
        for idx, k in enumerate(kinds):
            toks, _c = reader.get_sample(idx)
            if k == RECORD_KIND_BLOCK:
                assert toks.count("ANCHOR_BLOCK") == 1
                assert toks.count("ANCHOR_BLOCK_END") == 1
