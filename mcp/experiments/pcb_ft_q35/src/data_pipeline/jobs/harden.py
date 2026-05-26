"""Expand curated schematics into harder RL placement episodes.

v2: Exhaustive instance × perturbation enumeration replaces random seed sampling.
A complex schematic with N movable instances and P perturbation directions now
yields up to N×P episodes (capped by max_episodes_per_schematic), rather than
the previous 6-from-12-random-seeds approach.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from itertools import product
from pathlib import Path
from typing import Any

from src.rl.real_kicad_cleanup_env import GRID_MM, RealKiCadCleanupEnv


# ---------------------------------------------------------------------------
# Perturbation direction catalogs (matching env._sample_perturbation logic)
# ---------------------------------------------------------------------------

def _perturbation_catalog(difficulty: str) -> list[tuple[float, float]]:
    """Return all perturbation (dx, dy) vectors for a difficulty level."""
    if difficulty == "easy":
        scales = (2.0, 3.0)
    elif difficulty == "hard":
        scales = (4.0, 5.0, 6.0)
    else:
        scales = (3.0, 4.0, 5.0)
    choices: list[tuple[float, float]] = []
    for scale in scales:
        g = scale * GRID_MM
        choices.extend([(g, 0.0), (-g, 0.0), (0.0, g), (0.0, -g)])
    if difficulty == "hard":
        for scale in scales[:-1]:
            d = scale * GRID_MM
            choices.extend([(d, d), (-d, d), (d, -d), (-d, -d)])
    return choices


def _secondary_perturbation_catalog(
    primary_dx: float, primary_dy: float, difficulty: str,
) -> list[tuple[float, float]]:
    """Deterministic secondary perturbation vectors derived from the primary."""
    if difficulty != "hard":
        return [(0.0, 0.0)]
    results: list[tuple[float, float]] = []
    # Opposite direction (half magnitude)
    results.append((-primary_dx * 0.5, -primary_dy * 0.5))
    # Same direction (half magnitude)
    results.append((primary_dx * 0.5, primary_dy * 0.5))
    # Orthogonal
    if abs(primary_dx) > 1e-6 or abs(primary_dy) > 1e-6:
        results.append((-primary_dy * 0.5, primary_dx * 0.5))
    return results


# ---------------------------------------------------------------------------
# Hardness scoring (unchanged logic, cleaner structure)
# ---------------------------------------------------------------------------

def _hardness_bucket(score: float) -> str:
    if score >= 0.8:
        return "hard_plus"
    if score >= 0.6:
        return "hard"
    if score >= 0.45:
        return "medium"
    return "easy"


def _hardness_score(record: dict[str, Any], info: dict[str, Any]) -> float:
    quality = float(record.get("quality_score", 0.0))
    attachment_total = float(info.get("selected_attachment_total", 0.0))
    secondary_total = float(info.get("secondary_attachment_total", 0.0))
    cluster_candidates = float(
        info.get("candidate_kind_cluster_inverse_count", 0.0)
        + info.get("candidate_kind_cluster_half_inverse_count", 0.0)
        + info.get("candidate_kind_cluster_compact_cw_count", 0.0)
    )
    perturb_mm = float(info.get("perturb_move_magnitude_mm", 0.0))
    required_connections = float(info.get("required_connections", 0.0))
    num_instances = float(info.get("num_instances", 0.0))
    has_secondary = 1.0 if info.get("secondary_reference") else 0.0
    score = 0.0
    score += 0.20 * min(quality, 1.0)
    score += 0.18 * min(attachment_total / 6.0, 1.0)
    score += 0.12 * min(secondary_total / 6.0, 1.0)
    score += 0.15 * min(cluster_candidates / 3.0, 1.0)
    score += 0.15 * has_secondary
    score += 0.12 * min(perturb_mm / (6.0 * GRID_MM), 1.0)
    score += 0.04 * min(required_connections / 12.0, 1.0)
    score += 0.04 * min(num_instances / 16.0, 1.0)
    return float(max(0.0, min(score, 1.0)))


# ---------------------------------------------------------------------------
# Episode identity
# ---------------------------------------------------------------------------

def _episode_signature(schematic_path: str, info: dict[str, Any]) -> tuple[Any, ...]:
    return (
        schematic_path,
        str(info.get("selected_reference", "")),
        str(info.get("secondary_reference", "")),
        round(float(info.get("requested_perturb_dx_mm", 0.0)), 3),
        round(float(info.get("requested_perturb_dy_mm", 0.0)), 3),
        round(float(info.get("secondary_perturb_dx_mm", 0.0)), 3),
        round(float(info.get("secondary_perturb_dy_mm", 0.0)), 3),
    )


def _episode_id(signature: tuple[Any, ...]) -> str:
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Corpus root inference
# ---------------------------------------------------------------------------

def _infer_corpus_root(records: list[dict[str, Any]]) -> Path:
    parents = [
        str(Path(str(record.get("schematic_path", ""))).expanduser().resolve().parent)
        for record in records
        if str(record.get("schematic_path", ""))
    ]
    if not parents:
        raise ValueError("No schematic paths were provided")
    return Path(os.path.commonpath(parents))


# ---------------------------------------------------------------------------
# Exhaustive episode mining (v2)
# ---------------------------------------------------------------------------

def _enumerate_episodes_for_schematic(
    env: RealKiCadCleanupEnv,
    record: dict[str, Any],
    perturbations: list[tuple[float, float]],
    *,
    max_episodes_per_schematic: int,
    min_hardness_score: float,
    seed: int,
) -> list[dict[str, Any]]:
    """Mine episodes by systematically trying every movable instance × perturbation."""
    schematic_path = str(record.get("schematic_path", ""))
    if not schematic_path:
        return []

    # Phase 1: Probe the schematic to discover all movable instances.
    # We do this by resetting once with the first perturbation to load
    # the schematic, then reading the movable instance list from the env.
    try:
        env.reset(seed=seed, options={"schematic_path": schematic_path})
    except Exception:
        return []

    # Collect movable instance references from the loaded sheet
    movable = env._collect_movable_instances()
    if not movable:
        return []

    movable_refs = []
    for idx, _attachment in movable:
        ref = env.base_env._sheet.instances[idx].reference
        if ref:
            movable_refs.append(ref)

    if not movable_refs:
        return []

    # Phase 2: Enumerate instance × perturbation combinations
    episode_map: dict[tuple[Any, ...], dict[str, Any]] = {}
    attempt_seed = seed

    for ref, (pdx, pdy) in product(movable_refs, perturbations):
        if len(episode_map) >= max_episodes_per_schematic * 3:
            # Stop enumeration early if we already have plenty of candidates
            break

        attempt_seed += 1
        try:
            _, info = env.reset(
                seed=attempt_seed,
                options={
                    "schematic_path": schematic_path,
                    "preferred_reference": ref,
                    "preferred_perturb_dx_mm": pdx,
                    "preferred_perturb_dy_mm": pdy,
                },
            )
        except Exception:
            continue

        if not info.get("selected_reference"):
            continue
        # Verify we actually got the instance we asked for
        if str(info.get("selected_reference", "")) != ref:
            continue
        if env.difficulty == "hard" and not info.get("secondary_reference"):
            continue

        hardness = _hardness_score(record, info)
        if hardness < float(min_hardness_score):
            continue

        signature = _episode_signature(schematic_path, info)
        episode = dict(record)
        episode.update(
            {
                "episode_id": _episode_id(signature),
                "episode_kind": f"{env.difficulty}_placement",
                "difficulty": env.difficulty,
                "preferred_reference": str(info.get("selected_reference", "")),
                "preferred_secondary_reference": str(info.get("secondary_reference", "")) or None,
                "preferred_perturb_dx_mm": float(info.get("requested_perturb_dx_mm", 0.0)),
                "preferred_perturb_dy_mm": float(info.get("requested_perturb_dy_mm", 0.0)),
                "preferred_secondary_perturb_dx_mm": float(info.get("secondary_perturb_dx_mm", 0.0)),
                "preferred_secondary_perturb_dy_mm": float(info.get("secondary_perturb_dy_mm", 0.0)),
                "hardness_score": float(hardness),
                "hardness_bucket": _hardness_bucket(hardness),
                "hard_selected_attachment_total": int(info.get("selected_attachment_total", 0)),
                "hard_secondary_attachment_total": int(info.get("secondary_attachment_total", 0)),
                "hard_candidate_count": int(info.get("candidate_count", 0)),
                "hard_cluster_candidate_count": int(
                    info.get("candidate_kind_cluster_inverse_count", 0)
                    + info.get("candidate_kind_cluster_half_inverse_count", 0)
                    + info.get("candidate_kind_cluster_compact_cw_count", 0)
                ),
                "hard_perturb_move_magnitude_mm": float(info.get("perturb_move_magnitude_mm", 0.0)),
                "hard_required_connections": int(info.get("required_connections", 0)),
            }
        )
        prev = episode_map.get(signature)
        if prev is None or float(episode["hardness_score"]) > float(prev.get("hardness_score", 0.0)):
            episode_map[signature] = episode

    # Phase 3: For hard difficulty, also try varying the secondary peer
    # for the top primary instances (biggest multiplier for complex schematics)
    if env.difficulty == "hard" and len(movable_refs) > 1:
        # Pick the top instances by hardness so far
        top_episodes = sorted(
            episode_map.values(),
            key=lambda e: -float(e.get("hardness_score", 0.0)),
        )[:min(8, len(episode_map))]

        for ep in top_episodes:
            primary_ref = ep["preferred_reference"]
            primary_dx = ep["preferred_perturb_dx_mm"]
            primary_dy = ep["preferred_perturb_dy_mm"]

            # Try different secondary peers
            for secondary_ref in movable_refs:
                if secondary_ref == primary_ref:
                    continue
                if len(episode_map) >= max_episodes_per_schematic * 3:
                    break

                for sdx, sdy in _secondary_perturbation_catalog(primary_dx, primary_dy, env.difficulty):
                    attempt_seed += 1
                    try:
                        _, info = env.reset(
                            seed=attempt_seed,
                            options={
                                "schematic_path": schematic_path,
                                "preferred_reference": primary_ref,
                                "preferred_perturb_dx_mm": primary_dx,
                                "preferred_perturb_dy_mm": primary_dy,
                                "preferred_secondary_reference": secondary_ref,
                                "preferred_secondary_perturb_dx_mm": sdx,
                                "preferred_secondary_perturb_dy_mm": sdy,
                            },
                        )
                    except Exception:
                        continue

                    if not info.get("selected_reference"):
                        continue
                    if env.difficulty == "hard" and not info.get("secondary_reference"):
                        continue
                    # Verify we got the secondary we asked for
                    if str(info.get("secondary_reference", "")) != secondary_ref:
                        continue

                    hardness = _hardness_score(record, info)
                    if hardness < float(min_hardness_score):
                        continue

                    signature = _episode_signature(schematic_path, info)
                    episode = dict(record)
                    episode.update(
                        {
                            "episode_id": _episode_id(signature),
                            "episode_kind": f"{env.difficulty}_placement",
                            "difficulty": env.difficulty,
                            "preferred_reference": str(info.get("selected_reference", "")),
                            "preferred_secondary_reference": str(info.get("secondary_reference", "")) or None,
                            "preferred_perturb_dx_mm": float(info.get("requested_perturb_dx_mm", 0.0)),
                            "preferred_perturb_dy_mm": float(info.get("requested_perturb_dy_mm", 0.0)),
                            "preferred_secondary_perturb_dx_mm": float(info.get("secondary_perturb_dx_mm", 0.0)),
                            "preferred_secondary_perturb_dy_mm": float(info.get("secondary_perturb_dy_mm", 0.0)),
                            "hardness_score": float(hardness),
                            "hardness_bucket": _hardness_bucket(hardness),
                            "hard_selected_attachment_total": int(info.get("selected_attachment_total", 0)),
                            "hard_secondary_attachment_total": int(info.get("secondary_attachment_total", 0)),
                            "hard_candidate_count": int(info.get("candidate_count", 0)),
                            "hard_cluster_candidate_count": int(
                                info.get("candidate_kind_cluster_inverse_count", 0)
                                + info.get("candidate_kind_cluster_half_inverse_count", 0)
                                + info.get("candidate_kind_cluster_compact_cw_count", 0)
                            ),
                            "hard_perturb_move_magnitude_mm": float(info.get("perturb_move_magnitude_mm", 0.0)),
                            "hard_required_connections": int(info.get("required_connections", 0)),
                        }
                    )
                    prev = episode_map.get(signature)
                    if prev is None or float(episode["hardness_score"]) > float(prev.get("hardness_score", 0.0)):
                        episode_map[signature] = episode

    # Rank by hardness, return top N
    episodes = list(episode_map.values())
    episodes.sort(
        key=lambda item: (
            -float(item.get("hardness_score", 0.0)),
            -float(item.get("quality_score", 0.0)),
            str(item.get("episode_id", "")),
        )
    )
    return episodes[:max(int(max_episodes_per_schematic), 1)]


# ---------------------------------------------------------------------------
# Legacy random-seed mining (kept for backward compat via --strategy=random)
# ---------------------------------------------------------------------------

def _mine_episode_records_random(
    env: RealKiCadCleanupEnv,
    record: dict[str, Any],
    *,
    attempts_per_schematic: int,
    max_episodes_per_schematic: int,
    min_hardness_score: float,
    seed: int,
) -> list[dict[str, Any]]:
    schematic_path = str(record.get("schematic_path", ""))
    if not schematic_path:
        return []
    episode_map: dict[tuple[Any, ...], dict[str, Any]] = {}
    for attempt_idx in range(max(int(attempts_per_schematic), 1)):
        episode_seed = int(seed) + attempt_idx
        try:
            _, info = env.reset(seed=episode_seed, options={"schematic_path": schematic_path})
        except Exception:
            continue
        if not info.get("selected_reference"):
            continue
        if env.difficulty == "hard" and not info.get("secondary_reference"):
            continue
        hardness_score = _hardness_score(record, info)
        if hardness_score < float(min_hardness_score):
            continue
        signature = _episode_signature(schematic_path, info)
        episode = dict(record)
        episode.update(
            {
                "episode_id": _episode_id(signature),
                "episode_kind": f"{env.difficulty}_placement",
                "difficulty": env.difficulty,
                "preferred_reference": str(info.get("selected_reference", "")),
                "preferred_secondary_reference": str(info.get("secondary_reference", "")) or None,
                "preferred_perturb_dx_mm": float(info.get("requested_perturb_dx_mm", 0.0)),
                "preferred_perturb_dy_mm": float(info.get("requested_perturb_dy_mm", 0.0)),
                "preferred_secondary_perturb_dx_mm": float(info.get("secondary_perturb_dx_mm", 0.0)),
                "preferred_secondary_perturb_dy_mm": float(info.get("secondary_perturb_dy_mm", 0.0)),
                "hardness_score": float(hardness_score),
                "hardness_bucket": _hardness_bucket(hardness_score),
                "hard_selected_attachment_total": int(info.get("selected_attachment_total", 0)),
                "hard_secondary_attachment_total": int(info.get("secondary_attachment_total", 0)),
                "hard_candidate_count": int(info.get("candidate_count", 0)),
                "hard_cluster_candidate_count": int(
                    info.get("candidate_kind_cluster_inverse_count", 0)
                    + info.get("candidate_kind_cluster_half_inverse_count", 0)
                    + info.get("candidate_kind_cluster_compact_cw_count", 0)
                ),
                "hard_perturb_move_magnitude_mm": float(info.get("perturb_move_magnitude_mm", 0.0)),
                "hard_required_connections": int(info.get("required_connections", 0)),
            }
        )
        prev = episode_map.get(signature)
        if prev is None or float(episode["hardness_score"]) > float(prev.get("hardness_score", 0.0)):
            episode_map[signature] = episode
    episodes = list(episode_map.values())
    episodes.sort(
        key=lambda item: (
            -float(item.get("hardness_score", 0.0)),
            -float(item.get("quality_score", 0.0)),
            str(item.get("episode_id", "")),
        )
    )
    return episodes[: max(int(max_episodes_per_schematic), 1)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    records: list[dict[str, Any]],
    *,
    attempts_per_schematic: int = 12,
    max_episodes_per_schematic: int = 48,
    min_quality_score: float = 0.55,
    min_hardness_score: float = 0.45,
    difficulty: str = "hard",
    strategy: str = "exhaustive",
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Expand curated schematic records into explicit hard-placement episodes.

    Args:
        strategy: "exhaustive" (v2, default) enumerates all movable instances
            and perturbation directions. "random" uses the legacy random-seed
            approach for backward compatibility.
        max_episodes_per_schematic: Cap per schematic. With exhaustive strategy,
            a complex 20-component schematic can yield up to this many episodes
            (default raised from 6 to 48).
        min_hardness_score: Lowered default from 0.55 to 0.45 to capture more
            medium-difficulty episodes for curriculum learning.
    """
    if difficulty not in {"easy", "medium", "hard", "mixed"}:
        raise ValueError("difficulty must be one of: easy, medium, hard, mixed")
    if strategy not in {"exhaustive", "random"}:
        raise ValueError("strategy must be one of: exhaustive, random")

    # mixed: recursively call run() for each difficulty level with split budget
    if difficulty == "mixed":
        all_episodes: list[dict[str, Any]] = []
        # Budget: 50% hard, 30% medium, 20% easy
        budgets = [
            ("hard", int(max_episodes_per_schematic * 0.50)),
            ("medium", int(max_episodes_per_schematic * 0.30)),
            ("easy", max(1, max_episodes_per_schematic - int(max_episodes_per_schematic * 0.50) - int(max_episodes_per_schematic * 0.30))),
        ]
        for diff_level, budget in budgets:
            all_episodes.extend(
                run(
                    records,
                    attempts_per_schematic=attempts_per_schematic,
                    max_episodes_per_schematic=budget,
                    min_quality_score=min_quality_score,
                    min_hardness_score=min_hardness_score,
                    difficulty=diff_level,
                    strategy=strategy,
                    seed=seed,
                )
            )
        all_episodes.sort(
            key=lambda item: (
                -float(item.get("hardness_score", 0.0)),
                -float(item.get("quality_score", 0.0)),
                str(item.get("schematic_path", "")),
                str(item.get("episode_id", "")),
            )
        )
        return all_episodes

    base_records = [
        dict(record)
        for record in records
        if isinstance(record, dict)
        and str(record.get("schematic_path", ""))
        and bool(record.get("rl_eligible", True))
        and float(record.get("quality_score", 0.0)) >= float(min_quality_score)
    ]
    if not base_records:
        return []

    corpus_root = _infer_corpus_root(base_records)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False) as handle:
        temp_index = Path(handle.name)
        for record in base_records:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")

    env = RealKiCadCleanupEnv(
        corpus_root=corpus_root,
        corpus_index_path=temp_index,
        difficulty=difficulty,
        max_files=len(base_records),
        min_quality_score=min_quality_score,
        seed=seed,
    )
    perturbations = _perturbation_catalog(difficulty)
    expanded: list[dict[str, Any]] = []
    try:
        for record_idx, record in enumerate(base_records):
            record_seed = int(seed) + record_idx * max(int(attempts_per_schematic), 1) * 17

            if strategy == "exhaustive":
                expanded.extend(
                    _enumerate_episodes_for_schematic(
                        env,
                        record,
                        perturbations,
                        max_episodes_per_schematic=max_episodes_per_schematic,
                        min_hardness_score=min_hardness_score,
                        seed=record_seed,
                    )
                )
            else:
                expanded.extend(
                    _mine_episode_records_random(
                        env,
                        record,
                        attempts_per_schematic=attempts_per_schematic,
                        max_episodes_per_schematic=max_episodes_per_schematic,
                        min_hardness_score=min_hardness_score,
                        seed=record_seed,
                    )
                )
    finally:
        env.close()
        temp_index.unlink(missing_ok=True)

    expanded.sort(
        key=lambda item: (
            -float(item.get("hardness_score", 0.0)),
            -float(item.get("quality_score", 0.0)),
            str(item.get("schematic_path", "")),
            str(item.get("episode_id", "")),
        )
    )
    return expanded


__all__ = ["run"]
