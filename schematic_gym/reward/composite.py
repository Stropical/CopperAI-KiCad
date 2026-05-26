"""Composite reward computation for SchematicGym.

Combines electrical correctness, readability, ERC penalties, and crossing
penalties into a single :class:`~schematic_gym.core.project.RewardBreakdown`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.project import ERCViolation, RewardBreakdown, ScoringConfig, TaskObjective
from ..core.nets import Net
from ..core.symbols import SymbolDef
from .electrical import score_electrical
from .readability import score_readability, _count_wire_crossings

if TYPE_CHECKING:
    from ..core.project import Sheet


def compute_reward(
    sheet: Sheet,
    nets: list[Net],
    objectives: list[TaskObjective],
    erc_violations: list[ERCViolation],
    config: ScoringConfig,
    prev_breakdown: RewardBreakdown | None = None,
    symbol_library: dict[str, SymbolDef] | None = None,
) -> RewardBreakdown:
    """Compute the composite reward for the current state.

    Parameters
    ----------
    sheet:
        Current schematic sheet.
    nets:
        Resolved nets.
    objectives:
        Task objectives (may define required connections).
    erc_violations:
        Active ERC violations after the latest action.
    config:
        Scoring weights and penalty magnitudes.
    prev_breakdown:
        Reward breakdown from the previous step (used to compute delta).
        ``None`` on the first step.
    symbol_library:
        Symbol definitions (passed through to readability scoring).

    Returns
    -------
    RewardBreakdown
        Fully populated breakdown with ``total``, ``electrical``,
        ``readability``, ``erc_penalty``, ``crossing_penalty``, and
        ``delta``.
    """
    # -- Detect cleanup mode -----------------------------------------------
    is_cleanup = any(obj.objective_type == "cleanup" for obj in objectives)

    # -- Electrical correctness [0, 1] -------------------------------------
    electrical = score_electrical(nets, objectives)

    # -- Readability [0, 1] ------------------------------------------------
    readability = score_readability(sheet, nets, symbol_library)

    # -- ERC penalty [<= 0] ------------------------------------------------
    erc_pen = 0.0
    for v in erc_violations:
        if v.severity == "error":
            erc_pen += config.erc_error_penalty   # negative value
        else:
            erc_pen += config.erc_warning_penalty  # negative value

    # -- Crossing penalty [<= 0] -------------------------------------------
    crossing_count = _count_wire_crossings(sheet.wires)
    cross_pen = config.crossing_penalty * crossing_count  # negative value

    # -- Composite total ---------------------------------------------------
    if is_cleanup:
        # Cleanup mode: heavily weight readability over electrical.
        elec_w = 0.2
        read_w = 0.8
    else:
        elec_w = config.electrical_weight
        read_w = config.readability_weight

    total = (
        elec_w * electrical
        + read_w * readability
        + erc_pen
        + cross_pen
    )

    # -- Cleanup bonuses / penalties ---------------------------------------
    if is_cleanup and prev_breakdown is not None:
        readability_delta = readability - prev_breakdown.readability
        connectivity_changed = abs(electrical - prev_breakdown.electrical) > 1e-9

        if readability_delta > 0.0 and not connectivity_changed:
            # Readability improved without disrupting connectivity.
            total += 0.1
        if connectivity_changed:
            # Connectivity was disrupted -- penalise.
            total -= 0.2

    # -- Delta from previous step ------------------------------------------
    delta = total - prev_breakdown.total if prev_breakdown is not None else total

    return RewardBreakdown(
        total=total,
        electrical=electrical,
        readability=readability,
        erc_penalty=erc_pen,
        crossing_penalty=cross_pen,
        delta=delta,
    )
