from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

# --- Token Vocabulary Definitions ---

# Structural Tokens
BOS = "BOS"
EOS = "EOS"
OBJ_START = "OBJ_START"
OBJ_END = "OBJ_END"
ANCHOR_BLOCK = "ANCHOR_BLOCK"
ANCHOR_BLOCK_END = "ANCHOR_BLOCK_END"
STRUCT_START = "STRUCT_START"
STRUCT_END = "STRUCT_END"
PLACE_START = "PLACE_START"
PLACE_END = "PLACE_END"
SEP = "SEP"
COMPONENT = "COMPONENT"
COMPONENT_END = "COMPONENT_END"
NET = "NET"
NET_END = "NET_END"
MEMBER = "MEMBER"
MEMBER_END = "MEMBER_END"
PIN_COUNT = "PIN_COUNT"
PIN_NUM = "PIN_NUM"
MOVABLE = "MOVABLE"
FIXED = "FIXED"
LOCKED = "LOCKED"
CONTEXT = "CONTEXT"

# Component Type Tokens
TYPE_IC = "TYPE_IC"
TYPE_C = "TYPE_C"
TYPE_CPOL = "TYPE_CPOL"
TYPE_R = "TYPE_R"
TYPE_CONN = "TYPE_CONN"
TYPE_ESD = "TYPE_ESD"
TYPE_NETTIE = "TYPE_NETTIE"
TYPE_FET = "TYPE_FET"
TYPE_GND = "TYPE_GND"
TYPE_PWR = "TYPE_PWR"
TYPE_OTHER = "TYPE_OTHER"

# Component Role Tokens
ROLE_DECOUP = "ROLE_DECOUP"
ROLE_PULLUP = "ROLE_PULLUP"
ROLE_PULLDOWN = "ROLE_PULLDOWN"
ROLE_ESD = "ROLE_ESD"
ROLE_CONNECTOR = "ROLE_CONNECTOR"
ROLE_FILTER = "ROLE_FILTER"
ROLE_ACTIVE = "ROLE_ACTIVE"
ROLE_BIAS = "ROLE_BIAS"
ROLE_REGULATOR = "ROLE_REGULATOR"
ROLE_OTHER = "ROLE_OTHER"

# Topology Tokens
TOPO_SHUNT = "TOPO_SHUNT"
TOPO_INLINE = "TOPO_INLINE"
TOPO_PIN_ATTACH = "TOPO_PIN_ATTACH"
TOPO_BLOCK = "TOPO_BLOCK"

# Relative Placement Tokens (Quantized Directional Sectors)
DIR_N = "DIR_N"
DIR_NE = "DIR_NE"
DIR_E = "DIR_E"
DIR_SE = "DIR_SE"
DIR_S = "DIR_S"
DIR_SW = "DIR_SW"
DIR_W = "DIR_W"
DIR_NW = "DIR_NW"
DIR_CENTER = "DIR_CENTER"

def _signed_bins(prefix: str, max_abs: int) -> List[str]:
    return [f"{prefix}_N{i}" for i in range(max_abs, 0, -1)] + [f"{prefix}_0"] + [
        f"{prefix}_P{i}" for i in range(1, max_abs + 1)
    ]


# Expanded spatial bins: +/- 30 grid units at 2.54 mm resolution.
DX_BINS = _signed_bins("DX", 30)
DY_BINS = _signed_bins("DY", 30)

# Absolute page-grid bins: +/- 40 bins at 10 mm resolution.
PAGE_GRID_X = "PAGE_GRID_X"
PAGE_GRID_Y = "PAGE_GRID_Y"
PAGE_X_BINS = _signed_bins("PAGE_X", 40)
PAGE_Y_BINS = _signed_bins("PAGE_Y", 40)

# Multi-scale distance tokens
DIST_TOUCH = "DIST_TOUCH"    # < 1mm
DIST_CLOSE = "DIST_CLOSE"    # 1-3mm
DIST_NEAR = "DIST_NEAR"     # 3-10mm
DIST_MID = "DIST_MID"      # 10-30mm
DIST_FAR = "DIST_FAR"      # > 30mm

# Geometry Tokens
ROT_0 = "ROT_0"
ROT_90 = "ROT_90"
ROT_180 = "ROT_180"
ROT_270 = "ROT_270"
MIRROR_X = "MIRROR_X"
MIRROR_Y = "MIRROR_Y"
NO_MIRROR = "NO_MIRROR"

# Anchor and Net-Role Fields
ANCHOR_REF = "ANCHOR_REF"
ANCHOR_PIN = "ANCHOR_PIN"
REF = "REF"
PIN = "PIN"
VAL = "VAL"
PKG = "PKG"
NET_ROLE = "NET_ROLE"

NET_GND = "NET_GND"
NET_PWR = "NET_PWR"
NET_SIGNAL = "NET_SIGNAL"
NET_AUDIO = "NET_AUDIO"
NET_CTRL = "NET_CTRL"

# Closed-vocabulary local object IDs used instead of raw schematic refs.
ID_PAGE = "ID_PAGE"
LOCAL_ID_TOKENS = [ID_PAGE] + [f"ID_{i}" for i in range(512)]

# Value/Package Buckets
VAL_SMALL = "VAL_SMALL"   # pF, ohms
VAL_MED = "VAL_MED"     # nF, k-ohms
VAL_LARGE = "VAL_LARGE"   # uF, M-ohms
VAL_NICE = "VAL_NICE"    # 0.1u, 10k, etc (common)

PKG_SMD_SMALL = "PKG_SMD_SMALL" # 0402, 0201
PKG_SMD_MED = "PKG_SMD_MED"   # 0603, 0805
PKG_SMD_LARGE = "PKG_SMD_LARGE" # 1206+
PKG_IC_SMALL = "PKG_IC_SMALL"  # SOT23, SO8
PKG_IC_LARGE = "PKG_IC_LARGE"  # QFP, BGA
PKG_TH = "PKG_TH"        # Through-hole

# --- Python Representations ---

@dataclass
class RelativePose:
    direction: str
    dx_bin: str
    dy_bin: str
    dist: str
    rotation: str
    mirror: str = NO_MIRROR
    dx_mm: Optional[float] = None
    dy_mm: Optional[float] = None
    distance_mm: Optional[float] = None
    angle_deg: Optional[float] = None

    @property
    def side(self) -> str:
        """Backward-compatible alias for older code paths."""
        return self.direction

    def to_feature_dict(self) -> Dict[str, Any]:
        """Return compact 2D relation features for downstream consumers."""
        return {
            "direction": self.direction,
            "dx_bin": self.dx_bin,
            "dy_bin": self.dy_bin,
            "dist": self.dist,
            "rotation": self.rotation,
            "mirror": self.mirror,
            "dx_mm": self.dx_mm,
            "dy_mm": self.dy_mm,
            "distance_mm": self.distance_mm,
            "angle_deg": self.angle_deg,
        }

@dataclass
class TokenObject:
    ref: str
    type: str
    role: str
    topology: str
    anchor_ref: str
    anchor_pin: str
    pose: RelativePose
    value_bin: str = VAL_NICE
    package_bin: str = PKG_SMD_MED
    net_role: str = NET_SIGNAL

    def to_tokens(self) -> List[str]:
        return [
            OBJ_START,
            "TYPE", self.type,
            "VAL", self.value_bin,
            "PKG", self.package_bin,
            "ROLE", self.role,
            NET_ROLE, self.net_role,
            "REF", self.ref,
            "ANCHOR_REF", self.anchor_ref,
            "ANCHOR_PIN", self.anchor_pin,
            self.pose.direction,
            self.pose.dx_bin,
            self.pose.dy_bin,
            self.pose.dist,
            "TOPO", self.topology,
            self.pose.rotation,
            self.pose.mirror,
            OBJ_END
        ]

@dataclass
class AnchorBlock:
    anchor_ref: str
    anchor_pin: str
    page_grid_x: str = "PAGE_X_0"
    page_grid_y: str = "PAGE_Y_0"
    objects: List[TokenObject] = field(default_factory=list)

    def to_tokens(self) -> List[str]:
        tokens = [
            ANCHOR_BLOCK,
            "REF", self.anchor_ref,
            "PIN", self.anchor_pin,
            PAGE_GRID_X, self.page_grid_x,
            PAGE_GRID_Y, self.page_grid_y,
        ]
        for obj in self.objects:
            tokens.extend(obj.to_tokens())
        tokens.append(ANCHOR_BLOCK_END)
        return tokens

# --- Vocabulary Builder ---

VOCAB = [
    BOS, EOS, OBJ_START, OBJ_END, ANCHOR_BLOCK, ANCHOR_BLOCK_END,
    STRUCT_START, STRUCT_END, PLACE_START, PLACE_END, SEP,
    COMPONENT, COMPONENT_END, NET, NET_END, MEMBER, MEMBER_END,
    PIN_COUNT, PIN_NUM, MOVABLE, FIXED, LOCKED, CONTEXT,
    TYPE_IC, TYPE_C, TYPE_CPOL, TYPE_R, TYPE_CONN, TYPE_ESD, TYPE_NETTIE, TYPE_FET, TYPE_GND, TYPE_PWR, TYPE_OTHER,
    ROLE_DECOUP, ROLE_PULLUP, ROLE_PULLDOWN, ROLE_ESD, ROLE_CONNECTOR, ROLE_FILTER, ROLE_ACTIVE, ROLE_BIAS, ROLE_REGULATOR, ROLE_OTHER,
    TOPO_SHUNT, TOPO_INLINE, TOPO_PIN_ATTACH, TOPO_BLOCK,
    DIR_N, DIR_NE, DIR_E, DIR_SE, DIR_S, DIR_SW, DIR_W, DIR_NW, DIR_CENTER,
    DIST_TOUCH, DIST_CLOSE, DIST_NEAR, DIST_MID, DIST_FAR,
    ROT_0, ROT_90, ROT_180, ROT_270, MIRROR_X, MIRROR_Y, NO_MIRROR,
    ANCHOR_REF, ANCHOR_PIN, REF, PIN, VAL, PKG, "TYPE", "ROLE", "TOPO",
    PAGE_GRID_X, PAGE_GRID_Y,
    NET_ROLE,
    NET_GND, NET_PWR, NET_SIGNAL, NET_AUDIO, NET_CTRL,
    VAL_SMALL, VAL_MED, VAL_LARGE, VAL_NICE,
    PKG_SMD_SMALL, PKG_SMD_MED, PKG_SMD_LARGE, PKG_IC_SMALL, PKG_IC_LARGE, PKG_TH
] + DX_BINS + DY_BINS + PAGE_X_BINS + PAGE_Y_BINS + LOCAL_ID_TOKENS
