"""
Retrieval index builder.

Merges all extracted, summarized blocks into flat JSONL indexes:
  - index/blocks.jsonl      — flat block corpus (all projects)
  - index/projects.jsonl    — project-level summaries
  - index/families.jsonl    — clustered block families

These are the retrieval artifacts Gemini reads at query time.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_index(blocks_dir: Path, ir_dir: Path, out_dir: Path) -> None:
    """
    Walk blocks_dir for *.jsonl files, merge into flat indexes.

    Args:
        blocks_dir: root of per-schematic block JSONL files
        ir_dir:     root of per-schematic IR JSON files (for project summaries)
        out_dir:    destination for index/*.jsonl
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    all_blocks: List[Dict[str, Any]] = []
    block_files = sorted(blocks_dir.rglob("*.jsonl"))

    print(f"  Loading blocks from {len(block_files)} files...")
    for bf in block_files:
        all_blocks.extend(_load_jsonl(bf))

    print(f"  Total blocks: {len(all_blocks)}")

    # --- blocks.jsonl ---
    blocks_out = out_dir / "blocks.jsonl"
    with open(blocks_out, "w", encoding="utf-8") as f:
        for block in all_blocks:
            f.write(json.dumps(block, ensure_ascii=False) + "\n")
    print(f"  Written: {blocks_out}  ({len(all_blocks)} blocks)")

    # --- projects.jsonl ---
    # Group blocks by source schematic
    blocks_by_source: Dict[str, List[Dict]] = defaultdict(list)
    for block in all_blocks:
        blocks_by_source[block["source"]].append(block)

    project_records = []
    ir_files = {f.stem: f for f in ir_dir.rglob("*.json")}

    for source, src_blocks in sorted(blocks_by_source.items()):
        families = [b.get("family_id") for b in src_blocks if b.get("family_id")]
        family_counts: Dict[str, int] = defaultdict(int)
        for fid in families:
            family_counts[fid] += 1
        top_families = sorted(family_counts, key=lambda k: -family_counts[k])[:5]

        # Load IR for component/net counts if available
        source_stem = Path(source).stem
        comp_count = 0
        net_count = 0
        if source_stem in ir_files:
            try:
                ir = json.loads(ir_files[source_stem].read_text(encoding="utf-8"))
                comp_count = len(ir.get("components", []))
                net_count  = len(ir.get("nets", []))
            except Exception:
                pass

        project_records.append({
            "source": source,
            "name": Path(source).stem,
            "component_count": comp_count,
            "net_count": net_count,
            "block_count": len(src_blocks),
            "block_ids": [b["block_id"] for b in src_blocks],
            "top_families": top_families,
        })

    proj_out = out_dir / "projects.jsonl"
    with open(proj_out, "w", encoding="utf-8") as f:
        for rec in project_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"  Written: {proj_out}  ({len(project_records)} projects)")

    # --- families.jsonl ---
    family_blocks: Dict[str, List[Dict]] = defaultdict(list)
    for block in all_blocks:
        fid = block.get("family_id")
        if fid:
            family_blocks[fid].append(block)

    family_records = []
    for fid, members in sorted(family_blocks.items()):
        # Find the most "central" member (median component count)
        members_sorted = sorted(members, key=lambda b: b.get("component_count", 0))
        representative = members_sorted[len(members_sorted) // 2]

        # Common nets across members
        from collections import Counter
        net_counter: Counter = Counter()
        for b in members:
            for n in b.get("nets", []):
                net_counter[n] += 1
        common_nets = [n for n, c in net_counter.most_common(10) if c >= len(members) * 0.5]

        # Anchor libs
        anchor_libs = sorted({b["anchor_lib_id"].split(":")[0] for b in members})

        avg_comp = sum(b.get("component_count", 0) for b in members) / len(members)

        family_records.append({
            "family_id": fid,
            "anchor_type": members[0].get("anchor_type", "ic"),
            "count": len(members),
            "representative_block_id": representative["block_id"],
            "anchor_libs": anchor_libs,
            "avg_component_count": round(avg_comp, 1),
            "common_nets": common_nets,
        })

    family_records.sort(key=lambda r: -r["count"])
    fam_out = out_dir / "families.jsonl"
    with open(fam_out, "w", encoding="utf-8") as f:
        for rec in family_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"  Written: {fam_out}  ({len(family_records)} families)")
