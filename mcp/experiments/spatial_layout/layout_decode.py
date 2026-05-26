"""
Inverse of json_to_tokens quantization: map pose bins back to millimeters for preview export.

Training uses _quantize_signed_bin(value_mm, step_mm, max_abs, prefix) → DX_P5, etc.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from .token_model import (
    MIRROR_X,
    MIRROR_Y,
    ROT_0,
    ROT_180,
    ROT_270,
    ROT_90,
)

# Matches training: json_to_tokens._quantize_signed_bin(dx, 2.54, 30, "DX")
DX_STEP_MM = 2.54
DX_MAX_ABS = 30


def signed_bin_to_mm(token: str, *, step_mm: float, prefix: str) -> float:
    """
    Parse DX_P5 / DX_N3 / DX_0 / PAGE_X_P2 style tokens into millimeters.
    """
    if not token or not isinstance(token, str):
        return 0.0
    pfx = prefix + "_"
    if not token.startswith(pfx):
        return 0.0
    rest = token[len(pfx) :]
    if rest == "0":
        return 0.0
    m = re.match(r"^P(\d+)$", rest)
    if m:
        return float(int(m.group(1))) * step_mm
    m = re.match(r"^N(\d+)$", rest)
    if m:
        return -float(int(m.group(1))) * step_mm
    return 0.0


def decode_pose_bins_to_offset_mm(pose: Dict[str, Any]) -> Tuple[float, float]:
    """Decode dx_bin/dy_bin (relative to anchor) to (dx_mm, dy_mm)."""
    dx_t = str(pose.get("dx_bin") or "")
    dy_t = str(pose.get("dy_bin") or "")
    dx = signed_bin_to_mm(dx_t, step_mm=DX_STEP_MM, prefix="DX")
    dy = signed_bin_to_mm(dy_t, step_mm=DX_STEP_MM, prefix="DY")
    return dx, dy


def rotation_token_to_deg(rotation: str) -> float:
    return {
        ROT_0: 0.0,
        ROT_90: 90.0,
        ROT_180: 180.0,
        ROT_270: 270.0,
    }.get(rotation, 0.0)


def mirror_token_to_sch2py(mirror: str) -> str:
    """sch2py Circuit uses mirror '' | 'x' | 'y'."""
    if mirror == MIRROR_X:
        return "x"
    if mirror == MIRROR_Y:
        return "y"
    return ""
