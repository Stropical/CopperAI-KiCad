"""Record-based wrapper for job 5: scoring and ranking label generation."""

from __future__ import annotations

from typing import Any

from ..types import SchematicBlock
from .scorer_ranker import ScorerRanker


def _as_block(raw: Any) -> SchematicBlock:
    if isinstance(raw, SchematicBlock):
        return raw
    if isinstance(raw, dict):
        return SchematicBlock.from_dict(raw)
    return SchematicBlock(block_id="unknown_block")


def run(records: list[dict[str, Any]], seed: int = 0) -> list[dict[str, Any]]:
    """Assign heuristic scores and emit pairwise labels for ranking/repair tasks."""
    scorer = ScorerRanker(seed=seed)
    output: list[dict[str, Any]] = []

    for record in records:
        if not isinstance(record, dict):
            continue

        source_block = _as_block(record.get("source_block") or record.get("block") or record)
        perturbed_block = _as_block(record.get("perturbed_block") or record.get("normalized_graph") or record)

        perturbed_report = scorer.score_block(perturbed_block)
        clean_report = scorer.score_block(source_block)
        ranking = scorer.build_pairwise_sample(
            clean_block=source_block,
            perturbed_block=perturbed_block,
            seed=seed,
            issue_tags=record.get("issue_tags", []),
            metadata={"upstream_sample_type": record.get("sample_type")},
        )

        output.append(
            {
                "sample_type": "ranking",
                "block_id": perturbed_block.block_id,
                "family_id": record.get("family_id") or source_block.metadata.get("family_id"),
                "source_project": source_block.project_metadata.project_id,
                "source_block_id": source_block.block_id,
                "clean_block": source_block.to_dict(),
                "normalized_graph": perturbed_block.to_dict(),
                "score": perturbed_report["score"],
                "clean_score": clean_report["score"],
                "issue_tags": ranking.issue_tags,
                "perturbation_history": list(record.get("perturbation_history", [])),
                "preferred_candidate": ranking.preferred_index,
                "repair_action": record.get("repair_label"),
                "critique_target": ranking.reason,
                "ranking": ranking.to_dict(),
            }
        )

    output.sort(key=lambda item: (str(item.get("family_id", "")), str(item.get("block_id", ""))))
    return output


__all__ = ["run"]
