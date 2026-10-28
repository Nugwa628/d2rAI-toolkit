"""Edit D2R's HD UI layouts (data/global/ui/layouts/*.json) in place.

The files are JSON with comments and trailing commas, so they are edited as text: find a widget by its
"name", then replace one field of its "fields" object (or add it). Untouched text stays byte-identical, so a
layout patch stays small and readable. knowledge/ui-layouts.md explains the widgets.

    t = base.text("data/global/ui/layouts/bankexpansionlayouthd.json")
    t = set_field(t, "grid", "cellCount", {"x": 14, "y": 14})
    t = set_field(t, "BankTabs", "rect", {"x": 389, "y": 199})
"""
from __future__ import annotations

import json
import re

WS = " \t\r\n"
SCALAR = re.compile(r"[^,\n}\]]*")


def _skip_string(text: str, i: int) -> int:
    """Index just past the string starting at text[i] == '"'."""
    j = i + 1
    while text[j] != '"':
        j += 2 if text[j] == "\\" else 1
    return j + 1


def _match(text: str, start: int) -> int:
    """Index just past the bracket that closes text[start] ('{' or '['); skips strings and // comments."""
    depth, i, n = 0, start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "/" and text[i + 1:i + 2] == "/":
            i = text.index("\n", i)
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced layout text")


def widget_span(text: str, name: str, occurrence: int = 0) -> tuple[int, int]:
    """(start, end) of the {...} object of the widget "name": name (the n-th one if several share the name)."""
    hits = [m.start() for m in re.finditer(r'"name"\s*:\s*"' + re.escape(name) + '"', text)]
    if len(hits) <= occurrence:
        raise KeyError(f"no widget named '{name}'")
    depth, j = 0, hits[occurrence]
    while j > 0:  # walk back to the brace that opens this widget
        j -= 1
        if text[j] == "}":
            depth += 1
        elif text[j] == "{":
            if depth == 0:
                break
            depth -= 1
    return j, _match(text, j)


def widget_names(text: str) -> list[str]:
    return re.findall(r'"name"\s*:\s*"([^"]+)"', text)


def _value_end(text: str, i: int) -> int:
    if text[i] in "{[":
        return _match(text, i)
    if text[i] == '"':
        return _skip_string(text, i)
    return i + len(SCALAR.match(text, i).group(0).rstrip())


def _locate(text: str, widget: str, field: str, occurrence: int) -> tuple[int, int | None, int | None]:
    """(index of the fields object's '{', value start, value end); start/end None if the field is absent."""
    s, e = widget_span(text, widget, occurrence)
    m = re.search(r'"fields"\s*:\s*\{', text[s:e])
    if m is None:
        raise KeyError(f"widget '{widget}' has no fields object")
    f0 = s + m.end() - 1
    f1 = _match(text, f0)
    i, depth = f0 + 1, 0
    while i < f1 - 1:
        c = text[i]
        if c == '"' and depth == 0:
            j = _skip_string(text, i)
            k = j
            while text[k] in WS:
                k += 1
            if text[k] == ":":
                v = k + 1
                while text[v] in WS:
                    v += 1
                ve = _value_end(text, v)
                if text[i + 1:j - 1] == field:
                    return f0, v, ve
                i = ve
                continue
            i = j
            continue
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c == "/" and text[i + 1] == "/":
            i = text.index("\n", i)
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return f0, None, None


def render(value) -> str:
    """JSON text of a value; theme constants ("$ItemCellSize") are plain strings too."""
    return json.dumps(value)


def set_field(text: str, widget: str, field: str, value, occurrence: int = 0) -> str:
    """Sets fields.<field> of a widget to value (any JSON value). A panel image path is the string
    "PANEL\\mymod\\stash" (one backslash each in the value; json.dumps doubles them in the file).
    Adds the field when the widget does not have it."""
    f0, v, ve = _locate(text, widget, field, occurrence)
    if v is not None:
        return text[:v] + render(value) + text[ve:]
    return text[:f0 + 1] + f' "{field}": {render(value)},' + text[f0 + 1:]


def get_field(text: str, widget: str, field: str, occurrence: int = 0):
    """Value of fields.<field> (parsed when it is plain JSON, else the raw text), or None if absent."""
    _f0, v, ve = _locate(text, widget, field, occurrence)
    if v is None:
        return None
    raw = text[v:ve]
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def replace_all(text: str, old: str, new: str) -> str:
    if old not in text:
        raise KeyError(f"'{old}' not in layout")
    return text.replace(old, new)


def scaled(x: float, y: float, sx: float, sy: float) -> tuple[int, int]:
    """A position measured on art of another size, in HD panel pixels (e.g. 512x704 art -> 1162x1507:
    sx = 1162/512, sy = 1507/704)."""
    return round(x * sx), round(y * sy)
