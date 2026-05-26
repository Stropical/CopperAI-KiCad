"""
B3: QA pair generator for eval/prompt development.

Reads index/blocks.jsonl and produces eval/qa_pairs.jsonl.
All answers are derived deterministically from block data — no LLM involved.
These pairs test retrieval quality and validate prompt structure.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

INDEX_DIR  = ROOT / "index"
EVAL_DIR   = ROOT / "eval"
EVAL_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# QA templates
# ---------------------------------------------------------------------------

def _qa_role(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What does this block do?"""
    summary = block.get("summary")
    if not summary:
        return None
    anchor = block.get("anchor_ref", "?")
    value  = block.get("anchor_value", "")
    atype  = block.get("anchor_type", "ic")
    label  = {"mcu": "MCU", "regulator": "regulator", "connector": "connector",
              "opamp": "op-amp", "crystal": "crystal", "ic": "IC"}.get(atype, "IC")
    q = f"What does the block centered on {anchor} ({value}) do?"
    return {
        "question": q,
        "answer": summary,
        "answer_type": "role_identification",
    }


def _qa_components(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What components are in this block?"""
    refs = block.get("component_refs", [])
    if len(refs) < 2:
        return None
    anchor = block.get("anchor_ref", "?")
    ref_list = ", ".join(refs[:10])
    suffix = f" (and {len(refs)-10} more)" if len(refs) > 10 else ""
    q = f"What components are in the block around {anchor}?"
    a = f"The block contains {len(refs)} components: {ref_list}{suffix}."
    return {"question": q, "answer": a, "answer_type": "component_list"}


def _qa_nets(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What nets does this block use?"""
    nets   = block.get("nets", [])
    power  = block.get("power_nets", [])
    signal = block.get("signal_nets", [])
    if not nets:
        return None
    anchor = block.get("anchor_ref", "?")
    q = f"What nets connect the components in the block around {anchor}?"
    parts = []
    if power:
        parts.append(f"power rails: {', '.join(power[:4])}")
    if signal:
        parts.append(f"signal nets: {', '.join(signal[:5])}")
    a = f"The block uses {len(nets)} nets — " + "; ".join(parts) + "."
    return {"question": q, "answer": a, "answer_type": "net_list"}


def _qa_support_caps(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """How many decoupling caps are near this anchor?"""
    caps   = block.get("support_caps", 0)
    anchor = block.get("anchor_ref", "?")
    if caps == 0:
        return None
    q = f"How many decoupling capacitors are placed near {anchor}?"
    a = f"There are {caps} decoupling capacitor{'s' if caps != 1 else ''} in the support block around {anchor}."
    return {"question": q, "answer": a, "answer_type": "component_count"}


def _qa_family(block: Dict[str, Any], families: Dict[str, Dict]) -> Optional[Dict[str, Any]]:
    """What family does this block belong to?"""
    fid = block.get("family_id")
    if not fid or fid not in families:
        return None
    fam = families[fid]
    anchor = block.get("anchor_ref", "?")
    count  = fam.get("count", 1)
    atype  = block.get("anchor_type", "ic")
    q = f"What type of circuit pattern does the block around {anchor} represent?"
    a = (f"This block is a {atype} cluster (family {fid}), a pattern seen {count} times "
         f"across the schematic corpus. It typically involves "
         f"{fam.get('avg_component_count', '?')} components on nets including "
         f"{', '.join(fam.get('common_nets', [])[:3]) or 'various nets'}.")
    return {"question": q, "answer": a, "answer_type": "family_pattern"}


def _qa_connectivity(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Is ref_a connected to ref_b?"""
    signal = block.get("signal_nets", [])
    if not signal:
        return None
    # Pick a signal net and two refs on it from the source block
    net_name = signal[0]
    # We don't have pin-level detail in the index block, so answer from nets
    anchor = block.get("anchor_ref", "?")
    q = f"What signal is carried on the {net_name} net near {anchor}?"
    from src.summarize import classify_net_role
    role = classify_net_role(net_name)
    a = f"The {net_name} net carries a {role} signal in the block around {anchor}."
    return {"question": q, "answer": a, "answer_type": "net_role"}


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def main(max_pairs: int = 5000) -> None:
    blocks_path = INDEX_DIR / "blocks.jsonl"
    families_path = INDEX_DIR / "families.jsonl"

    if not blocks_path.exists():
        print(f"ERROR: {blocks_path} not found. Run build_index.py first.")
        sys.exit(1)

    print(f"Loading blocks from {blocks_path}...")
    blocks = []
    with open(blocks_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                blocks.append(json.loads(line))

    families: Dict[str, Dict] = {}
    if families_path.exists():
        with open(families_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    fam = json.loads(line)
                    families[fam["family_id"]] = fam

    print(f"Loaded {len(blocks)} blocks, {len(families)} families")

    generators = [
        _qa_role,
        _qa_components,
        _qa_nets,
        _qa_support_caps,
        _qa_connectivity,
        lambda b: _qa_family(b, families),
    ]

    qa_pairs = []
    rng = random.Random(42)
    rng.shuffle(blocks)

    for block in blocks:
        if len(qa_pairs) >= max_pairs:
            break
        for gen in generators:
            try:
                pair = gen(block)
            except Exception:
                pair = None
            if pair:
                pair["block_id"] = block.get("block_id")
                pair["source"]   = block.get("source")
                pair["anchor_type"] = block.get("anchor_type")
                qa_pairs.append(pair)

    # Shuffle so types are interleaved
    rng.shuffle(qa_pairs)
    qa_pairs = qa_pairs[:max_pairs]

    out = EVAL_DIR / "qa_pairs.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for pair in qa_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    # Stats
    from collections import Counter
    type_counts = Counter(p["answer_type"] for p in qa_pairs)
    print(f"\nGenerated {len(qa_pairs)} QA pairs → {out}")
    for t, n in type_counts.most_common():
        print(f"  {t:<30} {n}")


if __name__ == "__main__":
    main()
