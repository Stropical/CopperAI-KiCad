"""Filter and score parsed schematics into an RL-friendly corpus index."""

from __future__ import annotations

from typing import Any

from src.data_pipeline.quality import assess_parsed_schematic


def run(
    records: list[dict[str, Any]],
    min_quality_score: float = 0.45,
    require_sexpr: bool = False,
    dedupe_by_structure: bool = True,
) -> list[dict[str, Any]]:
    """Enrich parsed schematic records with quality metadata and filter for RL."""
    enriched: list[dict[str, Any]] = []
    best_by_structure: dict[str, dict[str, Any]] = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        quality = assess_parsed_schematic(
            record,
            min_quality_score=float(min_quality_score),
            require_sexpr=bool(require_sexpr),
        )
        item = dict(record)
        item.update(quality)
        enriched.append(item)

        if not dedupe_by_structure:
            continue
        structure_hash = str(item.get("structure_hash", ""))
        if not structure_hash:
            continue
        prev = best_by_structure.get(structure_hash)
        if prev is None:
            best_by_structure[structure_hash] = item
            continue
        prev_score = float(prev.get("quality_score", 0.0))
        new_score = float(item.get("quality_score", 0.0))
        if new_score > prev_score:
            best_by_structure[structure_hash] = item

    if dedupe_by_structure:
        keep_paths = {
            str(item.get("schematic_path", ""))
            for item in best_by_structure.values()
        }
        enriched = [
            item
            for item in enriched
            if str(item.get("schematic_path", "")) in keep_paths
        ]

    enriched.sort(
        key=lambda item: (
            not bool(item.get("rl_eligible", False)),
            -float(item.get("quality_score", 0.0)),
            str(item.get("schematic_path", "")),
        )
    )
    return enriched


__all__ = ["run"]
