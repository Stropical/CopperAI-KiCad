"""Image observation builder for SchematicGym."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gymnasium
import numpy as np

if TYPE_CHECKING:
    from ..core.project import ERCViolation, Sheet
    from ..rendering.cairo_renderer import CairoRenderer


def build_image_obs(
    sheet: Sheet,
    renderer: CairoRenderer,
    size: tuple[int, int],
    erc_violations: list[ERCViolation] | None = None,
    *,
    symbol_library: dict | None = None,
) -> np.ndarray:
    """Render the sheet and return an RGB image observation.

    Parameters
    ----------
    sheet:
        The schematic sheet to render.
    renderer:
        A :class:`CairoRenderer` instance.
    size:
        ``(width, height)`` in pixels.
    erc_violations:
        Optional ERC markers to overlay.
    symbol_library:
        ``{lib_id: SymbolDef}`` for resolving placed symbols during rendering.

    Returns
    -------
    np.ndarray
        Shape ``(H, W, 3)``, dtype ``uint8``.
    """
    return renderer.render_sheet(
        sheet,
        size=size,
        erc_violations=erc_violations,
        symbol_library=symbol_library,
    )


def build_image_space(image_size: tuple[int, int]) -> gymnasium.spaces.Box:
    """Build a Gymnasium Box space for the image observation.

    Parameters
    ----------
    image_size:
        ``(width, height)`` in pixels.

    Returns
    -------
    gymnasium.spaces.Box
        Low=0, high=255, shape ``(H, W, 3)``, dtype ``uint8``.
    """
    w, h = image_size
    return gymnasium.spaces.Box(
        low=0,
        high=255,
        shape=(h, w, 3),
        dtype=np.uint8,
    )
