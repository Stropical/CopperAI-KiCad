"""
S-expression parser for KiCad schematic files (.kicad_sch).

KiCad S-expr format:
  (keyword atom atom ...)
  (keyword (nested ...) ...)
  "quoted strings" can contain spaces
  numbers: 3.14, -1.27, 0
  atoms: yes, no, default, passive, etc.
"""

from __future__ import annotations
from typing import Any, List, Union

SExpr = Union[str, List["SExpr"]]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\n\r":
            i += 1
        elif c == "(":
            tokens.append("(")
            i += 1
        elif c == ")":
            tokens.append(")")
            i += 1
        elif c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            tokens.append(text[i : j + 1])  # include surrounding quotes
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\n\r()":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse_tokens(tokens: list[str], pos: int) -> tuple[SExpr, int]:
    tok = tokens[pos]
    if tok == "(":
        lst: list[SExpr] = []
        pos += 1
        while tokens[pos] != ")":
            item, pos = _parse_tokens(tokens, pos)
            lst.append(item)
        return lst, pos + 1  # consume ')'
    else:
        # Unquote strings
        if tok.startswith('"') and tok.endswith('"'):
            return tok[1:-1].replace('\\"', '"'), pos + 1
        return tok, pos + 1


def parse(text: str) -> SExpr:
    """Parse a KiCad S-expression string into nested Python lists."""
    tokens = tokenize(text)
    if not tokens:
        return []
    result, _ = _parse_tokens(tokens, 0)
    return result


def parse_file(path: str) -> SExpr:
    with open(path, "r", encoding="utf-8") as f:
        return parse(f.read())


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def tag(node: SExpr) -> str | None:
    """Return the tag (first atom) of a list node, or None."""
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def children(node: SExpr, named: str | None = None) -> list[SExpr]:
    """Return child nodes, optionally filtered by tag name."""
    if not isinstance(node, list):
        return []
    kids = node[1:]
    if named is None:
        return kids
    return [k for k in kids if tag(k) == named]


def child(node: SExpr, named: str) -> SExpr | None:
    """Return first child with given tag, or None."""
    found = children(node, named)
    return found[0] if found else None


def atom(node: SExpr, index: int) -> str | None:
    """Return the atom at position index in a list node."""
    if isinstance(node, list) and index < len(node):
        v = node[index]
        return v if isinstance(v, str) else None
    return None


def number(node: SExpr, index: int) -> float | None:
    """Return numeric value at position index."""
    v = atom(node, index)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None
