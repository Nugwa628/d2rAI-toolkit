"""D2R's tab-separated tables (data/global/excel/*.txt): read, edit by column name, write back unchanged otherwise.

Engine rules this follows (knowledge/tables.md):
  * The first column is the row's key (case-insensitive lookups). Rows whose key is "Expansion" are separators the
    engine skips; every other row, even one with a blank key, is a record. A record's ID is its position among
    records, so appending keeps every existing ID.
  * Comment columns start with '*'. The "*Id"/"*ID" comment column, when present, is filled with the record ID.
  * Files are written latin-1 (round-trip safe) with CRLF line ends, like D2R's own.
"""
from __future__ import annotations

import re


class Table:
    def __init__(self, name: str, header: list[str], rows: list[list[str]]):
        self.name = name
        self.header = header
        self.rows = rows
        self._cols = {c.lower(): i for i, c in reversed(list(enumerate(header)))}

    # ---- parsing / writing
    @classmethod
    def parse(cls, name: str, text: str) -> "Table":
        lines = text.replace("\r\n", "\n").split("\n")
        while lines and lines[-1] == "":
            lines.pop()
        header = lines[0].split("\t")
        rows = []
        for line in lines[1:]:
            cells = line.split("\t")
            cells += [""] * (len(header) - len(cells))
            rows.append(cells[: max(len(header), len(cells))])
        return cls(name, header, rows)

    def text(self) -> str:
        return "\r\n".join("\t".join(r) for r in [self.header] + self.rows) + "\r\n"

    # ---- columns
    def col(self, column: str) -> int:
        i = self._cols.get(column.lower())
        if i is None:
            raise KeyError(f"{self.name}: no column '{column}'")
        return i

    def has_col(self, column: str) -> bool:
        return column.lower() in self._cols

    def id_col(self) -> int | None:
        for i, c in enumerate(self.header):
            if c.startswith("*") and c[1:].strip().lower() == "id":
                return i
        return None

    # ---- records
    def is_record(self, row: list[str]) -> bool:
        return row[0] != "Expansion"

    def records(self) -> list[list[str]]:
        return [r for r in self.rows if self.is_record(r)]

    def record_id(self, row: list[str]) -> int:
        n = 0
        for r in self.rows:
            if r is row:
                return n
            if self.is_record(r):
                n += 1
        raise ValueError("row not in table")

    def find(self, key: str, column: str | None = None) -> list[str] | None:
        i = 0 if column is None else self.col(column)
        k = key.lower()
        for r in self.rows:
            if self.is_record(r) and r[i].lower() == k:
                return r
        return None

    def need(self, key: str, column: str | None = None) -> list[str]:
        r = self.find(key, column)
        if r is None:
            raise KeyError(f"{self.name}: no row '{key}'" + (f" in column {column}" if column else ""))
        return r

    def where(self, **conditions: str) -> list[list[str]]:
        idx = {self.col(c): str(v).lower() for c, v in conditions.items()}
        return [r for r in self.records() if all(r[i].lower() == v for i, v in idx.items())]

    def get(self, row: list[str], column: str) -> str:
        return row[self.col(column)]

    def set(self, row: list[str], column: str, value) -> None:
        if isinstance(value, bool):
            value = int(value)
        row[self.col(column)] = "" if value is None else str(value)

    def update(self, row: list[str], values: dict) -> None:
        for c, v in values.items():
            self.set(row, c, v)

    def as_dict(self, row: list[str], non_empty: bool = True) -> dict[str, str]:
        return {c: v for c, v in zip(self.header, row) if v != "" or not non_empty}

    # ---- edits
    def append(self, values: dict | list[str]) -> list[str]:
        row = list(values) if isinstance(values, list) else [""] * len(self.header)
        row += [""] * (len(self.header) - len(row))
        if isinstance(values, dict):
            self.update(row, values)
        self.rows.append(row)
        self._fill_id(row)
        return row

    def clone(self, source_key: str, new_key: str, values: dict | None = None) -> list[str]:
        if self.find(new_key) is not None:
            raise KeyError(f"{self.name}: '{new_key}' already exists")
        row = list(self.need(source_key))
        row[0] = new_key
        self.rows.append(row)
        self._fill_id(row)
        if values:
            self.update(row, values)
        return row

    def delete(self, key: str) -> None:
        """Removes a row. Shifts the IDs of every later record: only safe for keyed tables (knowledge/tables.md)."""
        row = self.need(key)
        self.rows.remove(row)

    def _fill_id(self, row: list[str]) -> None:
        i = self.id_col()
        if i is not None and self.is_record(row):
            row[i] = str(self.record_id(row))


NEGATIVE = re.compile(r"(?<![\w)\]'.])-(\d+)")


def fix_formula(expr: str) -> str:
    """D2R's formula parser rejects bare negative literals: 'x*-5' -> 'x*(0-5)'. Only for formula columns: a plain
    number column keeps '-5'."""
    if "-" not in expr or re.fullmatch(r"-?\d+", expr.strip()):
        return expr
    return NEGATIVE.sub(lambda m: f"(0-{m.group(1)})", expr)
