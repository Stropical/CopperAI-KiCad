"""Record-based wrapper for job 3: block extraction + dedupe."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .block_extractor import extract_blocks_from_record


def run(
    records: list[dict[str, Any]],
    radius: float = 40.0,
    k_hops: int = 2,
    min_nodes: int = 3,
    dedupe: bool = True,
) -> list[dict[str, Any]]:
    """Extract blocks from parsed-sheet records.

    Dedupe is canonical-signature based and defaults to enabled.
    """
    output: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    for parsed_record in records:
        if not isinstance(parsed_record, dict):
            continue
        candidates = extract_blocks_from_record(
            parsed_record,
            radius=radius,
            k_hops=k_hops,
            min_nodes=min_nodes,
        )
        for candidate in candidates:
            payload = asdict(candidate)
            signature = str(payload.get("canonical_signature", ""))
            if dedupe and signature:
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
            output.append(payload)

    output.sort(key=lambda item: (str(item.get("family_id", "")), str(item.get("block_id", ""))))
    return output


__all__ = ["run"]
