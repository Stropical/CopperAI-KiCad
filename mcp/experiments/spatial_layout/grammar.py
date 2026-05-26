from typing import Any, Dict, Iterable, List, Optional, Set

from .token_model import (
    AnchorBlock,
    ANCHOR_BLOCK,
    ANCHOR_BLOCK_END,
    ANCHOR_PIN,
    ANCHOR_REF,
    BOS,
    DIR_CENTER,
    DIR_E,
    DIR_N,
    DIR_NE,
    DIR_NW,
    DIR_S,
    DIR_SE,
    DIR_SW,
    DIR_W,
    DIST_CLOSE,
    DIST_FAR,
    DIST_MID,
    DIST_NEAR,
    DIST_TOUCH,
    DX_BINS,
    DY_BINS,
    EOS,
    MIRROR_X,
    MIRROR_Y,
    NO_MIRROR,
    OBJ_END,
    OBJ_START,
    PIN,
    PAGE_GRID_X,
    PAGE_GRID_Y,
    PAGE_X_BINS,
    PAGE_Y_BINS,
    PKG,
    PKG_IC_LARGE,
    PKG_IC_SMALL,
    PKG_SMD_LARGE,
    PKG_SMD_MED,
    PKG_SMD_SMALL,
    PKG_TH,
    REF,
    RelativePose,
    NET_ROLE,
    NET_GND,
    NET_PWR,
    NET_SIGNAL,
    NET_AUDIO,
    NET_CTRL,
    ROT_0,
    ROT_180,
    ROT_270,
    ROT_90,
    ROLE_ACTIVE,
    ROLE_BIAS,
    ROLE_CONNECTOR,
    ROLE_DECOUP,
    ROLE_ESD,
    ROLE_FILTER,
    ROLE_OTHER,
    ROLE_PULLDOWN,
    ROLE_PULLUP,
    ROLE_REGULATOR,
    TokenObject,
    TOPO_BLOCK,
    TOPO_INLINE,
    TOPO_PIN_ATTACH,
    TOPO_SHUNT,
    TYPE_C,
    TYPE_CONN,
    TYPE_CPOL,
    TYPE_ESD,
    TYPE_FET,
    TYPE_GND,
    TYPE_IC,
    TYPE_NETTIE,
    TYPE_OTHER,
    TYPE_PWR,
    TYPE_R,
    VAL,
    VAL_LARGE,
    VAL_MED,
    VAL_NICE,
    VAL_SMALL,
)

TYPE_KW = "TYPE"
ROLE_KW = "ROLE"
TOPO_KW = "TOPO"
UNK_STR = "UNK"

_TYPE_VALUES: Set[str] = {
    TYPE_IC,
    TYPE_C,
    TYPE_CPOL,
    TYPE_R,
    TYPE_CONN,
    TYPE_ESD,
    TYPE_NETTIE,
    TYPE_FET,
    TYPE_GND,
    TYPE_PWR,
    TYPE_OTHER,
}
_ROLE_VALUES: Set[str] = {
    ROLE_DECOUP,
    ROLE_PULLUP,
    ROLE_PULLDOWN,
    ROLE_ESD,
    ROLE_CONNECTOR,
    ROLE_FILTER,
    ROLE_ACTIVE,
    ROLE_BIAS,
    ROLE_REGULATOR,
    ROLE_OTHER,
}
_TOPO_VALUES: Set[str] = {
    TOPO_SHUNT,
    TOPO_INLINE,
    TOPO_PIN_ATTACH,
    TOPO_BLOCK,
}
_NET_ROLE_VALUES: Set[str] = {
    NET_GND,
    NET_PWR,
    NET_SIGNAL,
    NET_AUDIO,
    NET_CTRL,
}
_DIR_VALUES: Set[str] = {
    DIR_N,
    DIR_NE,
    DIR_E,
    DIR_SE,
    DIR_S,
    DIR_SW,
    DIR_W,
    DIR_NW,
    DIR_CENTER,
}
# Legacy tokenizer output uses SIDE_* in many schematics; same slot as direction.
_SIDE_VALUES: Set[str] = {
    "SIDE_LEFT",
    "SIDE_RIGHT",
    "SIDE_UP",
    "SIDE_DOWN",
    "SIDE_CENTER",
}
_DIR_OR_SIDE: Set[str] = _DIR_VALUES | _SIDE_VALUES
_DIST_VALUES: Set[str] = {
    DIST_TOUCH,
    DIST_CLOSE,
    DIST_NEAR,
    DIST_MID,
    DIST_FAR,
}
_VAL_VALUES: Set[str] = {VAL_SMALL, VAL_MED, VAL_LARGE, VAL_NICE}
_PKG_VALUES: Set[str] = {
    PKG_SMD_SMALL,
    PKG_SMD_MED,
    PKG_SMD_LARGE,
    PKG_IC_SMALL,
    PKG_IC_LARGE,
    PKG_TH,
}
_DX_VALUES: Set[str] = set(DX_BINS)
_DY_VALUES: Set[str] = set(DY_BINS)
_PAGE_X_VALUES: Set[str] = set(PAGE_X_BINS)
_PAGE_Y_VALUES: Set[str] = set(PAGE_Y_BINS)
_ROT_VALUES: Set[str] = {ROT_0, ROT_90, ROT_180, ROT_270}
_MIRROR_VALUES: Set[str] = {MIRROR_X, MIRROR_Y, NO_MIRROR}


class GrammarError(Exception):
    pass


def _stoi_subset(strings: Iterable[str], stoi: Dict[str, int]) -> List[int]:
    return sorted({stoi[s] for s in strings if s in stoi})


def _normalize_prefix_tokens(raw: List[str]) -> List[str]:
    seq = list(raw)
    if seq and seq[0] == BOS:
        seq = seq[1:]
    if seq and seq[-1] == EOS:
        seq = seq[:-1]
    return seq


def parse_token_stream(tokens: List[str]) -> List[AnchorBlock]:
    """Parse a strict token stream into AnchorBlocks and TokenObjects.

    Accepts either bare block streams or full BOS/EOS-wrapped sequences.
    """
    seq = _normalize_prefix_tokens(tokens)
    if not seq:
        return []

    blocks = []
    i = 0
    while i < len(seq):
        if seq[i] != ANCHOR_BLOCK:
            raise GrammarError(f"Expected ANCHOR_BLOCK at index {i}, got {seq[i]}")
        block, i = _parse_anchor_block(seq, i)
        blocks.append(block)
    return blocks


def _parse_anchor_block(tokens: List[str], start_idx: int) -> tuple[AnchorBlock, int]:
    if tokens[start_idx] != ANCHOR_BLOCK:
        raise GrammarError(f"Expected ANCHOR_BLOCK at index {start_idx}, got {tokens[start_idx]}")

    i = start_idx + 1
    if i >= len(tokens) or tokens[i] != REF:
        got = tokens[i] if i < len(tokens) else "<eos>"
        raise GrammarError(f"Expected REF after ANCHOR_BLOCK at index {i}, got {got}")
    if i + 1 >= len(tokens):
        raise GrammarError("Missing anchor ref value")
    anchor_ref = tokens[i + 1]
    i += 2

    if i >= len(tokens) or tokens[i] != PIN:
        got = tokens[i] if i < len(tokens) else "<eos>"
        raise GrammarError(f"Expected PIN after anchor ref at index {i}, got {got}")
    if i + 1 >= len(tokens):
        raise GrammarError("Missing anchor pin value")
    anchor_pin = tokens[i + 1]
    i += 2

    page_grid_x = "PAGE_X_0"
    page_grid_y = "PAGE_Y_0"
    if i < len(tokens) and tokens[i] == PAGE_GRID_X:
        if i + 1 >= len(tokens):
            raise GrammarError("Missing page grid X value")
        page_grid_x = tokens[i + 1]
        if page_grid_x not in _PAGE_X_VALUES:
            raise GrammarError(f"Invalid PAGE_GRID_X value: {page_grid_x}")
        i += 2

        if i >= len(tokens) or tokens[i] != PAGE_GRID_Y:
            got = tokens[i] if i < len(tokens) else "<eos>"
            raise GrammarError(f"Expected PAGE_GRID_Y after PAGE_GRID_X at index {i}, got {got}")
        if i + 1 >= len(tokens):
            raise GrammarError("Missing page grid Y value")
        page_grid_y = tokens[i + 1]
        if page_grid_y not in _PAGE_Y_VALUES:
            raise GrammarError(f"Invalid PAGE_GRID_Y value: {page_grid_y}")
        i += 2

    block = AnchorBlock(anchor_ref, anchor_pin, page_grid_x=page_grid_x, page_grid_y=page_grid_y)

    while i < len(tokens) and tokens[i] != ANCHOR_BLOCK_END:
        if tokens[i] != OBJ_START:
            raise GrammarError(f"Expected OBJ_START or ANCHOR_BLOCK_END at index {i}, got {tokens[i]}")
        obj, i = _parse_object(tokens, i)
        block.objects.append(obj)

    if i >= len(tokens):
        raise GrammarError("Unterminated ANCHOR_BLOCK")

    return block, i + 1


def _parse_object(tokens: List[str], start_idx: int) -> tuple[TokenObject, int]:
    i = start_idx
    if tokens[i] != OBJ_START:
        raise GrammarError(f"Expected OBJ_START at index {i}, got {tokens[i]}")
    i += 1

    def expect_keyword(keyword: str) -> None:
        nonlocal i
        if i >= len(tokens):
            raise GrammarError(f"Unexpected end of token stream, expected {keyword}")
        if tokens[i] != keyword:
            raise GrammarError(f"Expected {keyword} at index {i}, got {tokens[i]}")
        i += 1

    def expect_value(label: str) -> str:
        nonlocal i
        if i >= len(tokens):
            raise GrammarError(f"Missing {label} value")
        value = tokens[i]
        i += 1
        return value

    expect_keyword(TYPE_KW)
    type_value = expect_value("TYPE")
    if type_value not in _TYPE_VALUES:
        raise GrammarError(f"Invalid TYPE value: {type_value}")

    value_bin = VAL_NICE
    package_bin = PKG_SMD_MED
    if i < len(tokens) and tokens[i] == VAL:
        expect_keyword(VAL)
        value_bin = expect_value("VAL")
        if value_bin not in _VAL_VALUES:
            raise GrammarError(f"Invalid VAL value: {value_bin}")

    if i < len(tokens) and tokens[i] == PKG:
        expect_keyword(PKG)
        package_bin = expect_value("PKG")
        if package_bin not in _PKG_VALUES:
            raise GrammarError(f"Invalid PKG value: {package_bin}")

    expect_keyword(ROLE_KW)
    role = expect_value("ROLE")
    if role not in _ROLE_VALUES:
        raise GrammarError(f"Invalid ROLE value: {role}")

    net_role = NET_SIGNAL
    if i < len(tokens) and tokens[i] == NET_ROLE:
        expect_keyword(NET_ROLE)
        net_role = expect_value("NET_ROLE")
        if net_role not in _NET_ROLE_VALUES:
            raise GrammarError(f"Invalid NET_ROLE value: {net_role}")

    expect_keyword(REF)
    ref = expect_value("REF")

    expect_keyword(ANCHOR_REF)
    anchor_ref = expect_value("ANCHOR_REF")

    expect_keyword(ANCHOR_PIN)
    anchor_pin = expect_value("ANCHOR_PIN")

    if i >= len(tokens):
        raise GrammarError("Unexpected end of token stream before direction")
    direction = tokens[i]
    if direction not in _DIR_OR_SIDE:
        raise GrammarError(f"Invalid direction token: {direction}")
    i += 1

    if i + 2 >= len(tokens):
        raise GrammarError("Unexpected end of token stream in geometry bins")
    dx = tokens[i]
    dy = tokens[i + 1]
    dist = tokens[i + 2]
    if dx not in _DX_VALUES:
        raise GrammarError(f"Invalid DX value: {dx}")
    if dy not in _DY_VALUES:
        raise GrammarError(f"Invalid DY value: {dy}")
    if dist not in _DIST_VALUES:
        raise GrammarError(f"Invalid DIST value: {dist}")
    i += 3

    expect_keyword(TOPO_KW)
    topology = expect_value("TOPO")
    if topology not in _TOPO_VALUES:
        raise GrammarError(f"Invalid TOPO value: {topology}")

    if i + 1 >= len(tokens):
        raise GrammarError("Unexpected end of token stream before rotation/mirror")
    rotation = tokens[i]
    mirror = tokens[i + 1]
    if rotation not in _ROT_VALUES:
        raise GrammarError(f"Invalid rotation value: {rotation}")
    if mirror not in _MIRROR_VALUES:
        raise GrammarError(f"Invalid mirror value: {mirror}")
    i += 2

    if i >= len(tokens) or tokens[i] != OBJ_END:
        got = tokens[i] if i < len(tokens) else "<eos>"
        raise GrammarError(f"Expected OBJ_END at index {i}, got {got}")

    pose = RelativePose(direction, dx, dy, dist, rotation, mirror)
    obj = TokenObject(
        ref=ref,
        type=type_value,
        role=role,
        topology=topology,
        anchor_ref=anchor_ref,
        anchor_pin=anchor_pin,
        pose=pose,
        value_bin=value_bin,
        package_bin=package_bin,
        net_role=net_role,
    )
    return obj, i + 1


def grammar_allowed_token_ids(
    prefix_ids: List[int],
    stoi: Dict[str, int],
    itos: Dict[int, str],
) -> Optional[List[int]]:
    """Return allowed next token ids, or None if the prefix is invalid.

    None means all vocabulary tokens are allowed (caller should not mask).
    """
    tokens = [itos.get(tid, UNK_STR) for tid in prefix_ids]
    seq = _normalize_prefix_tokens(tokens)

    stack: List[str] = []
    mode = "global"
    block_step: Optional[str] = None
    obj_step: Optional[str] = None

    def fail() -> None:
        nonlocal mode
        mode = "failed"

    for tok in seq:
        if mode == "failed":
            break

        if mode == "global":
            if tok == ANCHOR_BLOCK:
                stack.append(ANCHOR_BLOCK)
                mode = "block"
                block_step = "ref_kw"
            else:
                fail()

        elif mode == "block":
            assert block_step is not None
            if block_step == "ref_kw":
                if tok == REF:
                    block_step = "ref_val"
                elif tok == ANCHOR_BLOCK_END:
                    if not stack or stack[-1] != ANCHOR_BLOCK:
                        fail()
                        break
                    stack.pop()
                    mode = "global"
                    block_step = None
                else:
                    fail()
            elif block_step == "ref_val":
                block_step = "pin_kw"
            elif block_step == "pin_kw":
                if tok == PIN:
                    block_step = "pin_val"
                else:
                    fail()
            elif block_step == "pin_val":
                block_step = "page_or_obj"
            elif block_step == "page_or_obj":
                if tok == PAGE_GRID_X:
                    block_step = "page_x_val"
                elif tok == OBJ_START:
                    stack.append(OBJ_START)
                    mode = "obj"
                    obj_step = "type_kw"
                elif tok == ANCHOR_BLOCK_END:
                    if not stack or stack[-1] != ANCHOR_BLOCK:
                        fail()
                        break
                    stack.pop()
                    mode = "global"
                    block_step = None
                else:
                    fail()
            elif block_step == "page_x_val":
                if tok in _PAGE_X_VALUES:
                    block_step = "page_y_kw"
                else:
                    fail()
            elif block_step == "page_y_kw":
                if tok == PAGE_GRID_Y:
                    block_step = "page_y_val"
                else:
                    fail()
            elif block_step == "page_y_val":
                if tok in _PAGE_Y_VALUES:
                    block_step = "obj_or_end"
                else:
                    fail()
            elif block_step == "obj_or_end":
                if tok == OBJ_START:
                    stack.append(OBJ_START)
                    mode = "obj"
                    obj_step = "type_kw"
                elif tok == ANCHOR_BLOCK_END:
                    if not stack or stack[-1] != ANCHOR_BLOCK:
                        fail()
                        break
                    stack.pop()
                    mode = "global"
                    block_step = None
                else:
                    fail()
            else:
                fail()

        elif mode == "obj":
            assert obj_step is not None
            if obj_step == "type_kw":
                if tok == TYPE_KW:
                    obj_step = "type_val"
                else:
                    fail()
            elif obj_step == "type_val":
                if tok in _TYPE_VALUES:
                    obj_step = "after_type"
                else:
                    fail()
            elif obj_step == "after_type":
                if tok == VAL:
                    obj_step = "val_value"
                elif tok == PKG:
                    obj_step = "pkg_value"
                elif tok == ROLE_KW:
                    obj_step = "role_kw"
                else:
                    fail()
            elif obj_step == "val_value":
                if tok in _VAL_VALUES:
                    obj_step = "after_val"
                else:
                    fail()
            elif obj_step == "after_val":
                if tok == PKG:
                    obj_step = "pkg_value"
                elif tok == ROLE_KW:
                    obj_step = "role_kw"
                else:
                    fail()
            elif obj_step == "pkg_value":
                if tok in _PKG_VALUES:
                    obj_step = "role_kw"
                else:
                    fail()
            elif obj_step == "role_kw":
                if tok == ROLE_KW:
                    obj_step = "role_val"
                else:
                    fail()
            elif obj_step == "role_val":
                if tok in _ROLE_VALUES:
                    obj_step = "net_role_or_ref"
                else:
                    fail()
            elif obj_step == "net_role_or_ref":
                if tok == NET_ROLE:
                    obj_step = "net_role_val"
                elif tok == REF:
                    obj_step = "ref_val"
                else:
                    fail()
            elif obj_step == "net_role_val":
                if tok in _NET_ROLE_VALUES:
                    obj_step = "ref_kw"
                else:
                    fail()
            elif obj_step == "ref_kw":
                if tok == REF:
                    obj_step = "ref_val"
                else:
                    fail()
            elif obj_step == "ref_val":
                obj_step = "anchor_ref_kw"
            elif obj_step == "anchor_ref_kw":
                if tok == ANCHOR_REF:
                    obj_step = "anchor_ref_val"
                else:
                    fail()
            elif obj_step == "anchor_ref_val":
                obj_step = "anchor_pin_kw"
            elif obj_step == "anchor_pin_kw":
                if tok == ANCHOR_PIN:
                    obj_step = "anchor_pin_val"
                else:
                    fail()
            elif obj_step == "anchor_pin_val":
                obj_step = "direction"
            elif obj_step == "direction":
                if tok in _DIR_OR_SIDE:
                    obj_step = "dx"
                else:
                    fail()
            elif obj_step == "dx":
                if tok in _DX_VALUES:
                    obj_step = "dy"
                else:
                    fail()
            elif obj_step == "dy":
                if tok in _DY_VALUES:
                    obj_step = "dist"
                else:
                    fail()
            elif obj_step == "dist":
                if tok in _DIST_VALUES:
                    obj_step = "topo_kw"
                else:
                    fail()
            elif obj_step == "topo_kw":
                if tok == TOPO_KW:
                    obj_step = "topo_val"
                else:
                    fail()
            elif obj_step == "topo_val":
                if tok in _TOPO_VALUES:
                    obj_step = "rot"
                else:
                    fail()
            elif obj_step == "rot":
                if tok in _ROT_VALUES:
                    obj_step = "mirror"
                else:
                    fail()
            elif obj_step == "mirror":
                if tok in _MIRROR_VALUES:
                    obj_step = "end"
                else:
                    fail()
            elif obj_step == "end":
                if tok != OBJ_END:
                    fail()
                    break
                if not stack or stack[-1] != OBJ_START:
                    fail()
                    break
                stack.pop()
                obj_step = None
                mode = "block"
                block_step = "obj_or_end"
            else:
                fail()
        else:
            fail()

    if mode == "failed":
        return None

    if mode == "global":
        ids = _stoi_subset([ANCHOR_BLOCK, EOS], stoi)
        return ids if ids else None

    if mode == "block":
        assert block_step is not None
        if block_step == "ref_kw":
            ids = _stoi_subset([REF], stoi)
            return ids if ids else None
        if block_step in ("ref_val", "pin_val"):
            return None
        if block_step == "pin_kw":
            ids = _stoi_subset([PIN], stoi)
            return ids if ids else None
        if block_step == "page_or_obj":
            ids = _stoi_subset([PAGE_GRID_X, OBJ_START, ANCHOR_BLOCK_END], stoi)
            return ids if ids else None
        if block_step == "page_x_val":
            return _stoi_subset(_PAGE_X_VALUES, stoi) or None
        if block_step == "page_y_kw":
            return _stoi_subset([PAGE_GRID_Y], stoi) or None
        if block_step == "page_y_val":
            return _stoi_subset(_PAGE_Y_VALUES, stoi) or None
        if block_step == "obj_or_end":
            ids = _stoi_subset([OBJ_START, ANCHOR_BLOCK_END], stoi)
            return ids if ids else None
        return None

    assert mode == "obj" and obj_step is not None

    if obj_step == "type_kw":
        return _stoi_subset([TYPE_KW], stoi) or None
    if obj_step == "type_val":
        return _stoi_subset(_TYPE_VALUES, stoi) or None
    if obj_step == "after_type":
        return _stoi_subset([VAL, PKG, ROLE_KW], stoi) or None
    if obj_step == "val_value":
        return _stoi_subset(_VAL_VALUES, stoi) or None
    if obj_step == "after_val":
        return _stoi_subset([PKG, ROLE_KW], stoi) or None
    if obj_step == "pkg_value":
        return _stoi_subset(_PKG_VALUES, stoi) or None
    if obj_step == "role_kw":
        return _stoi_subset([ROLE_KW], stoi) or None
    if obj_step == "role_val":
        return _stoi_subset(_ROLE_VALUES, stoi) or None
    if obj_step == "net_role_or_ref":
        return _stoi_subset([NET_ROLE, REF], stoi) or None
    if obj_step == "net_role_val":
        return _stoi_subset(_NET_ROLE_VALUES, stoi) or None
    if obj_step == "ref_kw":
        return _stoi_subset([REF], stoi) or None
    if obj_step == "ref_val":
        return None
    if obj_step == "anchor_ref_kw":
        return _stoi_subset([ANCHOR_REF], stoi) or None
    if obj_step == "anchor_ref_val":
        return None
    if obj_step == "anchor_pin_kw":
        return _stoi_subset([ANCHOR_PIN], stoi) or None
    if obj_step == "anchor_pin_val":
        return None
    if obj_step == "direction":
        return _stoi_subset(_DIR_OR_SIDE, stoi) or None
    if obj_step == "dx":
        return _stoi_subset(_DX_VALUES, stoi) or None
    if obj_step == "dy":
        return _stoi_subset(_DY_VALUES, stoi) or None
    if obj_step == "dist":
        return _stoi_subset(_DIST_VALUES, stoi) or None
    if obj_step == "topo_kw":
        return _stoi_subset([TOPO_KW], stoi) or None
    if obj_step == "topo_val":
        return _stoi_subset(_TOPO_VALUES, stoi) or None
    if obj_step == "rot":
        return _stoi_subset(_ROT_VALUES, stoi) or None
    if obj_step == "mirror":
        return _stoi_subset(_MIRROR_VALUES, stoi) or None
    if obj_step == "end":
        return _stoi_subset([OBJ_END], stoi) or None

    return None


def validate_sequence(tokens: List[str]) -> bool:
    """Validate the full BOS/EOS-wrapped token grammar."""
    seq = list(tokens)
    if not seq or seq[0] != BOS or seq[-1] != EOS:
        return False
    try:
        parse_token_stream(seq)
        return True
    except GrammarError:
        return False
