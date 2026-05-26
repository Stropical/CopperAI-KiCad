"""Heuristic block scoring and pairwise ranking-label generation job."""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence

from ..types import (
    BlockPerturbation,
    RankingSample,
    SchematicBlock,
    SchematicNode,
    ISSUE_ALIGNMENT_BREAK,
    ISSUE_DISTANCE_INCREASE,
    ISSUE_ROTATION_AWKWARDNESS,
    ISSUE_SPREAD_GROUP,
    stable_hash,
)


class ScorerRanker:
    """Heuristic quality scorer and pairwise ranking label builder."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = int(seed)

    def score_block(self, block: SchematicBlock) -> Dict[str, Any]:
        """Compute a deterministic quality score and issue tags for one block."""
        nodes = self._placement_nodes(block)
        if not nodes:
            return {
                "score": 0.0,
                "issue_tags": [ISSUE_SPREAD_GROUP],
                "metrics": {
                    "distance_penalty": 1.0,
                    "rotation_penalty": 1.0,
                    "alignment_penalty": 1.0,
                    "spread_penalty": 1.0,
                },
            }

        distance_penalty = self._distance_penalty(nodes)
        rotation_penalty = self._rotation_penalty(nodes)
        alignment_penalty = self._alignment_penalty(nodes)
        spread_penalty = self._spread_penalty(nodes)

        weighted_penalty = (
            0.35 * distance_penalty
            + 0.25 * rotation_penalty
            + 0.25 * alignment_penalty
            + 0.15 * spread_penalty
        )
        score = max(0.0, min(1.0, 1.0 - weighted_penalty))

        issue_tags = self._derive_issue_tags(
            distance_penalty=distance_penalty,
            rotation_penalty=rotation_penalty,
            alignment_penalty=alignment_penalty,
            spread_penalty=spread_penalty,
            existing_tags=block.metadata.get("issue_tags", []),
        )

        return {
            "score": score,
            "issue_tags": issue_tags,
            "metrics": {
                "distance_penalty": distance_penalty,
                "rotation_penalty": rotation_penalty,
                "alignment_penalty": alignment_penalty,
                "spread_penalty": spread_penalty,
            },
        }

    def build_pairwise_sample(
        self,
        clean_block: SchematicBlock,
        perturbed_block: SchematicBlock,
        seed: Optional[int] = None,
        sample_id: Optional[str] = None,
        issue_tags: Optional[Sequence[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RankingSample:
        """Create pairwise ranking labels from clean-vs-perturbed block comparison."""
        clean_report = self.score_block(clean_block)
        perturbed_report = self.score_block(perturbed_block)

        clean_score = float(clean_report["score"])
        perturbed_score = float(perturbed_report["score"])

        preferred_index = 0 if clean_score >= perturbed_score else 1
        pairwise_label = 1 if preferred_index == 0 else 0

        clean_tags = set(clean_report["issue_tags"])
        perturbed_tags = set(perturbed_report["issue_tags"])
        extra_tags = set(issue_tags or [])
        differential_tags = sorted((perturbed_tags | extra_tags) - clean_tags)
        merged_tags = differential_tags if differential_tags else sorted(perturbed_tags | extra_tags)

        reason = self._reason_from_tags(merged_tags, clean_score, perturbed_score, preferred_index)

        stable_seed = self.seed if seed is None else int(seed)
        if sample_id is None:
            sample_id = "rank_" + stable_hash(
                {
                    "clean": clean_block.block_id,
                    "perturbed": perturbed_block.block_id,
                    "seed": stable_seed,
                }
            )[:12]

        sample_metadata: Dict[str, Any] = {
            "seed": stable_seed,
            "clean_metrics": clean_report["metrics"],
            "perturbed_metrics": perturbed_report["metrics"],
        }
        if metadata:
            sample_metadata.update(metadata)

        return RankingSample(
            sample_id=sample_id,
            clean_block_id=clean_block.block_id,
            perturbed_block_id=perturbed_block.block_id,
            clean_score=clean_score,
            perturbed_score=perturbed_score,
            preferred_index=preferred_index,
            pairwise_label=pairwise_label,
            issue_tags=merged_tags,
            reason=reason,
            metadata=sample_metadata,
        )

    def build_pairwise_samples_from_perturbations(
        self,
        clean_block: SchematicBlock,
        perturbations: Sequence[BlockPerturbation],
        seed: Optional[int] = None,
        shuffle: bool = False,
    ) -> List[RankingSample]:
        """Generate pairwise ranking labels for each perturbation variant."""
        stable_seed = self.seed if seed is None else int(seed)
        samples: List[RankingSample] = []

        for perturbation in perturbations:
            sample = self.build_pairwise_sample(
                clean_block=clean_block,
                perturbed_block=perturbation.perturbed_block,
                seed=stable_seed,
                sample_id=f"rank_{perturbation.perturbation_id}",
                issue_tags=perturbation.issue_tags,
                metadata={
                    "perturbation_id": perturbation.perturbation_id,
                    "perturbation_type": perturbation.perturbation_type,
                    "repair_label": perturbation.repair_label.to_dict(),
                },
            )
            samples.append(sample)

        if shuffle:
            rng = random.Random(stable_seed)
            rng.shuffle(samples)

        return samples

    def _placement_nodes(self, block: SchematicBlock) -> List[SchematicNode]:
        preferred = [
            node
            for node in block.nodes
            if node.node_type not in {"pin", "wire_segment", "junction"}
        ]
        nodes = preferred if preferred else list(block.nodes)
        return sorted(nodes, key=lambda node: node.node_id)

    def _distance_penalty(self, nodes: Sequence[SchematicNode]) -> float:
        if len(nodes) <= 1:
            return 0.0
        cx = sum(node.x_mm for node in nodes) / len(nodes)
        cy = sum(node.y_mm for node in nodes) / len(nodes)
        mean_radius = sum(math.hypot(node.x_mm - cx, node.y_mm - cy) for node in nodes) / len(nodes)
        return min(1.0, mean_radius / 20.0)

    def _rotation_penalty(self, nodes: Sequence[SchematicNode]) -> float:
        if not nodes:
            return 1.0
        deviations: List[float] = []
        for node in nodes:
            angle = node.rotation_deg % 360.0
            nearest = min(abs(angle - cardinal) for cardinal in (0.0, 90.0, 180.0, 270.0, 360.0))
            deviations.append(min(1.0, nearest / 45.0))
        return sum(deviations) / len(deviations)

    def _alignment_penalty(self, nodes: Sequence[SchematicNode]) -> float:
        if len(nodes) <= 1:
            return 0.0

        tolerance = 0.75
        aligned_count = 0
        for idx, left in enumerate(nodes):
            is_aligned = False
            for jdx, right in enumerate(nodes):
                if idx == jdx:
                    continue
                if abs(left.x_mm - right.x_mm) <= tolerance or abs(left.y_mm - right.y_mm) <= tolerance:
                    is_aligned = True
                    break
            if is_aligned:
                aligned_count += 1

        alignment_ratio = aligned_count / len(nodes)
        return max(0.0, min(1.0, 1.0 - alignment_ratio))

    def _spread_penalty(self, nodes: Sequence[SchematicNode]) -> float:
        if len(nodes) <= 1:
            return 0.0
        xs = [node.x_mm for node in nodes]
        ys = [node.y_mm for node in nodes]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        area = width * height
        area_per_node = area / len(nodes)
        return min(1.0, area_per_node / 200.0)

    def _derive_issue_tags(
        self,
        distance_penalty: float,
        rotation_penalty: float,
        alignment_penalty: float,
        spread_penalty: float,
        existing_tags: Sequence[Any],
    ) -> List[str]:
        tags: List[str] = []
        if distance_penalty > 0.45:
            tags.append(ISSUE_DISTANCE_INCREASE)
        if rotation_penalty > 0.20:
            tags.append(ISSUE_ROTATION_AWKWARDNESS)
        if alignment_penalty > 0.55:
            tags.append(ISSUE_ALIGNMENT_BREAK)
        if spread_penalty > 0.45:
            tags.append(ISSUE_SPREAD_GROUP)

        for tag in existing_tags:
            tag_str = str(tag).strip()
            if tag_str:
                tags.append(tag_str)

        return sorted(set(tags))

    def _reason_from_tags(
        self,
        issue_tags: Sequence[str],
        clean_score: float,
        perturbed_score: float,
        preferred_index: int,
    ) -> str:
        if issue_tags:
            return issue_tags[0]
        if preferred_index == 0:
            if clean_score > perturbed_score:
                return "higher_clean_quality"
            return "clean_tie_break"
        return "perturbed_scores_higher"


def score_block_quality(block: SchematicBlock, seed: int = 0) -> Dict[str, Any]:
    """Convenience wrapper for one-shot block scoring."""
    scorer = ScorerRanker(seed=seed)
    return scorer.score_block(block)


def generate_pairwise_ranking_sample(
    clean_block: SchematicBlock,
    perturbed_block: SchematicBlock,
    seed: int = 0,
    issue_tags: Optional[Sequence[str]] = None,
) -> RankingSample:
    """Convenience wrapper for one pairwise clean-vs-perturbed label."""
    scorer = ScorerRanker(seed=seed)
    return scorer.build_pairwise_sample(
        clean_block=clean_block,
        perturbed_block=perturbed_block,
        seed=seed,
        issue_tags=issue_tags,
    )


def generate_pairwise_ranking_samples(
    clean_block: SchematicBlock,
    perturbations: Sequence[BlockPerturbation],
    seed: int = 0,
    shuffle: bool = False,
) -> List[RankingSample]:
    """Convenience wrapper for ranking-label generation over perturbations."""
    scorer = ScorerRanker(seed=seed)
    return scorer.build_pairwise_samples_from_perturbations(
        clean_block=clean_block,
        perturbations=perturbations,
        seed=seed,
        shuffle=shuffle,
    )


__all__ = [
    "ScorerRanker",
    "score_block_quality",
    "generate_pairwise_ranking_sample",
    "generate_pairwise_ranking_samples",
]
