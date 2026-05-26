"""Electrical correctness scoring for SchematicGym.

Computes the fraction of required connections that are satisfied in the
current net state.  A connection is satisfied when both pins resolve to
the same :class:`~schematic_gym.core.nets.Net`.
"""

from __future__ import annotations

from ..core.nets import Net
from ..core.project import TaskObjective


def _build_pin_to_net_map(nets: list[Net]) -> dict[str, str]:
    """Return a mapping ``"REF.PIN" -> net_id`` for every resolved pin.

    Pin references are constructed from the *instance* reference designator
    (looked up via the parent :class:`SymbolInstance`) and the pin number.
    Because :class:`Pin` only stores ``instance_id`` (a UUID), and the
    net list already carries the resolved pins with ``net_id`` set, we
    need an auxiliary map from ``instance_id`` to ``reference``.

    However, the :class:`Net` objects only expose :class:`Pin` which has
    ``instance_id`` and ``number`` -- not the human-readable reference.
    The caller (the environment) is expected to have set ``pin.instance_id``
    to the *reference designator* string when the pin's parent instance has
    a reference.  To be robust, we index by *both* instance_id and number.

    Scenario objectives use ``"R1.1"`` format (reference.pin_number).
    We therefore also accept if ``instance_id`` is the reference designator
    directly.
    """
    pin_to_net: dict[str, str] = {}
    for net in nets:
        for pin in net.pins:
            # Key = "instance_id.pin_number" (instance_id may be a UUID
            # or a reference designator depending on the caller).
            key = f"{pin.instance_id}.{pin.number}"
            pin_to_net[key] = net.net_id
    return pin_to_net


def _build_ref_to_instance_id(nets: list[Net]) -> dict[str, str]:
    """Attempt to discover a reference -> instance_id mapping.

    This is a best-effort helper.  It scans all pins and checks if
    ``instance_id`` looks like a reference designator (contains a letter
    followed by digits, e.g. "R1").  If so it records the mapping.
    """
    # Not needed if the caller already uses references as instance_ids.
    return {}


def score_electrical(nets: list[Net], objectives: list[TaskObjective]) -> float:
    """Score the fraction of required connections that are satisfied.

    For each objective that defines ``required_connections``, check whether
    ``pin_a`` and ``pin_b`` resolve to the same :class:`Net`.  Pin
    references use ``"R1.1"`` format -- reference designator + pin number.

    Parameters
    ----------
    nets:
        Resolved nets from the current sheet state.
    objectives:
        Task objectives that may contain ``required_connections``.

    Returns
    -------
    float
        Fraction of required connections completed, in ``[0.0, 1.0]``.
        Returns ``1.0`` if no objectives define any required connections.
    """
    # Collect all required connections across all objectives.
    all_connections: list[tuple[str, str]] = []
    for obj in objectives:
        for rc in obj.required_connections:
            all_connections.append((rc.pin_a, rc.pin_b))

    if not all_connections:
        return 1.0

    # Build a mapping from "{instance_id}.{pin_number}" -> net_id.
    pin_to_net = _build_pin_to_net_map(nets)

    completed = 0
    for pin_a_ref, pin_b_ref in all_connections:
        net_a = pin_to_net.get(pin_a_ref)
        net_b = pin_to_net.get(pin_b_ref)
        if net_a is not None and net_b is not None and net_a == net_b:
            completed += 1

    return completed / len(all_connections)
