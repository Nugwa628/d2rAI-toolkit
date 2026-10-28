"""Validate a D2R mod's TXT tables against D2R's own data guide.

Schema: an extracted D2R data guide folder (``data/global/dataguide/data/files``: one ``<table>.js`` per table plus
``enums.js``). The guide types every field: ``reference`` {file, field}, ``string`` (string key), ``text`` (max byte
length), ``int``, ``boolean``, ``parse`` (BBE formulas by scope, property codes or parameters), and allowed-value
lists (a field's own ``table`` or an ``enums.js`` list).

Data: ``data_dir`` is a mod's data root (``global/excel``, optionally ``global/excel/base``, and
``local/lng/strings``). Both banks are validated when present: ``global/excel`` (findings named ``skills``) and
``global/excel/base`` (findings named ``base/skills``). Tables the mod does not ship are taken from ``base_data_dir``
(pristine D2R) of the same bank when resolving references, the way D2R falls back to its own files.

Severities:
  error   the game rejects the value, or it points at something that does not exist
  plugin  valid only once a D2RLoader plugin provides it (calc codes/functions listed in ``extra_calc_codes``, calc
          codes the mod adds to skillcalc/misscalc/moncalc, function numbers above the highest D2R uses, the free
          srvdofunc slots 172-190, more than 30 class skills with ``allow_extended_saves``)
  warn    suspicious, probably fine; an error whose value D2R's own table uses in the same column becomes a warn

``extra_calc_codes`` (dict, all keys optional): ``{"skillcalc": [...], "misscalc": [...], "moncalc": [...],
"functions": [...], "conditions": [...]}`` - identifiers, formula functions and cond() names a plugin provides.

API:   validate(data_dir, guide_dir, strings_dir=None, extra_calc_codes=None, base_data_dir=None,
                allow_extended_saves=False) -> Report
CLI:   python -m modkit.validate --data <dir> --guide <dir> [--base <d2r data dir>] [--strings <dir> ...]
                                 [--extra-calc-codes codes.json] [--allow-extended-saves] [--report out.md]
       exit code 1 if there are errors.
Python standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, NamedTuple

csv.field_size_limit(1 << 30)

ENCODING = "latin-1"  # byte-exact round trip; lengths below are byte lengths, as D2R counts them

# ---- engine facts (D2R 3.3.0 build 93847) -------------------------------------------------------------------------
BBE_FUNCS = {"min", "max", "rand", "skill", "miss", "stat", "sklvl", "sksrc", "cond"}
# cond('Name', param): allowed parameters (None = any / checked against tables below, () = takes no parameter).
# IsDesecratedZonesEnabled is not in the guide's list but D2R's own tables use it.
CONDITIONS: dict[str, set[str] | None | tuple] = {
    "IsType": {"player", "monster", "object", "missile", "item", "roomtile"},
    "IsClass": None,
    "Desecrated": (),
    "IsDesecratedZonesEnabled": (),
    "Difficulty": {"normal", "nightmare", "hell"},
    "MonsterTestElite": {"unique", "champion", "super", "minion", "herald"},
    "ItemIsType": None,
    "ItemIsModType": {"unique", "set", "rare", "magic", "crafted", "lowquality", "normal", "hiquality", "tempered"},
    "MonsterHasMod": None,
}
COND_TABLES = {
    "IsClass": [("playerclass", "Code"), ("monstats", "Id"), ("missiles", "Missile"), ("weapons", "code"),
                ("armor", "code"), ("misc", "code")],
    "ItemIsType": [("itemtypes", "Code")],
    "MonsterHasMod": [("monumod", "uniquemod")],
}
STAT_MODES = {"accr", "base", "mod"}
SCOPE_CODES = {"Skill": "skillcalc", "Missile": "misscalc", "Monster": "moncalc"}  # Item/Treasure Class: none
MAX_FORMULA = 255        # bytes per formula cell
MAX_FORMULA_NAME = 31    # D2R's parser keeps 32 bytes per quoted name (with the terminator)
MAX_ITEMS_PER_SET = 6
MAX_CLASS_SKILLS = 30    # per class in D2R's save format
# Function-number columns: the engine has a fixed function table per column. Above D2R's highest number = plugin.
FUNC_COLUMN = re.compile(r"(func\d*|^(init|populate|operate|client)fn|^pspell|^op)$", re.I)
PLUGIN_FUNC_SLOTS = {("skills", "srvdofunc"): (172, 190)}  # free slots D2RLoader plugins register into
AUTO_TC = re.compile(r"^(weap|armo|mele|bow|mag[a-z]*|rar[a-z]*)\d+$", re.I)
TOKEN = re.compile(r"\s*(?:(?P<num>\d+)|(?P<str>'[^']*')|(?P<id>[A-Za-z_][A-Za-z0-9_]*)"
                   r"|(?P<op><=|>=|==|!=|[-+*/%^()?:<>,.]))")
INT = re.compile(r"-?\d+")
# References the checker enforces even where the guide does not type the column as a reference
# (table, column regex, target table, target column).
EXPLICIT_REFS = [
    ("skills", r"skilldesc", "skilldesc", "skilldesc"),
    ("skills", r"anim|seqtrans", "plrmode", "Code"),
    ("skills", r"(srv|clt)missile[a-d]?", "missiles", "Missile"),
    ("skills", r"aurastate|auratargetstate|passivestate|state\d", "states", "state"),
    ("skills", r"(srv|sum|tgt|prg|cast|itemcast|clt)overlay[ab]?", "overlay", "overlay"),
    ("missiles", r"explosionmissile|(clt)?(hit)?submissile\d", "missiles", "Missile"),
    ("missiles", r"progoverlay", "overlay", "overlay"),
    ("states", r"overlay\d|pgsvoverlay|castoverlay|removerlay", "overlay", "overlay"),
    ("states", r"missile", "missiles", "Missile"),
]


# ---- report -------------------------------------------------------------------------------------------------------
class Finding(NamedTuple):
    table: str
    row: str
    column: str
    message: str


class Report:
    def __init__(self) -> None:
        self.errors: list[Finding] = []
        self.warnings: list[Finding] = []
        self.plugin: list[Finding] = []
        self.tables: list[str] = []          # tables validated
        self.notes: list[str] = []           # things that could not be checked
        self._groups: dict[tuple[str, str, str, str], list[tuple[str, str]]] = defaultdict(list)
        self._seen: set[tuple] = set()
        self.raw: list[tuple[str, str, str, str, str, str]] = []   # (severity, table, row, column, problem, value)

    def add(self, sev: str, table: str, row: str, column: str, problem: str, value: str = "") -> None:
        key = (sev, table, row, column, problem, value)
        if key in self._seen:
            return
        self._seen.add(key)
        self.raw.append(key)
        msg = f"{problem}: {value}" if value != "" else problem
        f = Finding(table, row, column, msg)
        {"error": self.errors, "warn": self.warnings, "plugin": self.plugin}[sev].append(f)
        self._groups[(sev, table, column, problem)].append((row, value))

    @property
    def summary(self) -> dict:
        by_table: dict[str, dict[str, int]] = defaultdict(lambda: {"error": 0, "plugin": 0, "warn": 0})
        for (sev, table, _c, _p), items in self._groups.items():
            by_table[table][sev] += len(items)
        return {"errors": len(self.errors), "plugin": len(self.plugin), "warnings": len(self.warnings),
                "tables": len(self.tables), "by_table": dict(by_table)}

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_markdown(self, examples: int = 3) -> str:
        s = self.summary
        out = ["# Validation report", "",
               f"errors {s['errors']}, plugin {s['plugin']}, warnings {s['warnings']} "
               f"({s['tables']} tables)", ""]
        names = {"error": "Errors", "plugin": "Needs a D2RLoader plugin", "warn": "Warnings"}
        for sev in ("error", "plugin", "warn"):
            groups = sorted(((k, v) for k, v in self._groups.items() if k[0] == sev),
                            key=lambda kv: (-len(kv[1]), kv[0][1], kv[0][2]))
            if not groups:
                continue
            out += [f"## {names[sev]}", "", "| Table | Column | Problem | Count | Examples (row: value) |",
                    "|---|---|---|---|---|"]
            for (_s, table, col, problem), items in groups:
                ex = "; ".join(f"{r}: {v[:90]}" if v else r for r, v in items[:examples])
                out.append("| " + " | ".join(_md(x) for x in (table, col, problem, str(len(items)), ex)) + " |")
            out.append("")
        if self.notes:
            out += ["## Not checked", ""] + [f"- {_md(n)}" for n in self.notes] + [""]
        return "\n".join(out)


def _md(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


# ---- inputs -------------------------------------------------------------------------------------------------------
def load_js(path: Path) -> dict:
    t = path.read_text("utf-8-sig")
    return json.loads(t[t.index("=") + 1:].strip().rstrip(";"))


def cell(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].strip()
    return v


class Table(NamedTuple):
    header: list[str]
    rows: list[list[str]]      # records (Expansion marker rows and blank lines removed), cells cleaned
    lines: list[int]           # file line number of each record
    col: dict[str, int]        # lower-case column name -> first index

    def get(self, row: list[str], column: str) -> str:
        i = self.col.get(column.lower())
        return row[i] if i is not None and i < len(row) else ""

    def has(self, column: str) -> bool:
        return column.lower() in self.col


def read_table(path: Path) -> Table:
    with path.open(encoding=ENCODING, newline="") as fh:
        raw = list(csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE))
    if not raw:
        return Table([], [], [], {})
    header = [h.strip() for h in raw[0]]
    if header and header[0].startswith("\xef\xbb\xbf"):
        header[0] = header[0][3:]
    rows, lines = [], []
    for n, r in enumerate(raw[1:], start=2):
        r = [cell(c) for c in r]
        if not any(r) or r[0] == "Expansion" or r[0].lower().startswith("*end*"):
            continue
        rows.append(r)
        lines.append(n)
    col: dict[str, int] = {}
    for i, h in enumerate(header):
        col.setdefault(h.lower(), i)
    return Table(header, rows, lines, col)


def excel_root(p: Path | None) -> Path | None:
    """Accept a data root (…/data), its global folder, or the excel folder itself."""
    if p is None:
        return None
    for cand in (p / "global" / "excel", p / "excel", p):
        if cand.is_dir() and any(cand.glob("*.txt")):
            return cand
    return None


class Bank:
    """One table bank: the mod's copy of a table, else pristine D2R's."""

    def __init__(self, name: str, mod: Path, d2r: Path | None):
        self.name = name                      # "" (global/excel) or "base"
        self.mod, self.d2r = mod, d2r
        self._cache: dict[tuple[str, bool], Table | None] = {}
        self._vals: dict[tuple, set[str] | None] = {}

    def prefix(self, table: str) -> str:
        return f"{self.name}/{table}" if self.name else table

    def _load(self, name: str, pristine: bool) -> Table | None:
        key = (name.lower(), pristine)
        if key not in self._cache:
            roots = [self.d2r] if pristine else [self.mod, self.d2r]
            hit = None
            for root in roots:
                if root is None:
                    continue
                p = root / f"{name.lower()}.txt"
                if p.exists():
                    hit = read_table(p)
                    break
            self._cache[key] = hit
        return self._cache[key]

    def table(self, name: str) -> Table | None:
        return self._load(name, False)

    def pristine(self, name: str) -> Table | None:
        return self._load(name, True)

    def values(self, name: str, column: str, pristine: bool = False) -> set[str] | None:
        key = (name.lower(), column.lower(), pristine)
        if key not in self._vals:
            t = self._load(name, pristine)
            if t is None or not t.has(column):
                self._vals[key] = None
            else:
                self._vals[key] = {v for r in t.rows if (v := t.get(r, column)) != ""}
        return self._vals[key]

    def lower_values(self, name: str, column: str) -> set[str]:
        key = (name.lower(), column.lower(), "lower")
        if key not in self._vals:
            self._vals[key] = {v.lower() for v in self.values(name, column) or ()}
        return self._vals[key]

    def mod_tables(self) -> list[str]:
        return sorted(p.stem.lower() for p in self.mod.glob("*.txt"))


class Schema:
    def __init__(self, guide_dir: Path):
        self.files: dict[str, dict] = {}
        for p in sorted(guide_dir.glob("*.js")):
            try:
                self.files[p.stem.lower()] = load_js(p)
            except (ValueError, UnicodeDecodeError):
                continue
        self.enums = {f["name"]: f for f in self.files.get("enums", {}).get("fields", [])}
        self._fields: dict[str, tuple[list, set[str]]] = {}

    def has(self, table: str) -> bool:
        f = self.files.get(table)
        return bool(f) and not f.get("guideOnly")

    def _table_fields(self, table: str) -> tuple[list[tuple[re.Pattern, dict]], set[str]]:
        if table not in self._fields:
            f = self.files[table]
            ignore = {n.lower() for n in f.get("ignoreFields", [])}
            pats = []
            fields = [(fld, False) for fld in f["fields"]]
            for extra in f.get("appendFiles", []):
                fields += [(fld, True) for fld in self.files.get(extra.lower(), {}).get("fields", [])]
            for fld, appended in fields:
                names = [fld["name"], *fld.get("altNames", [])]
                if appended and any(n.lower() in ignore for n in names):
                    continue
                for n in names:
                    pats.append((re.compile(re.escape(n).replace("\\#", r"\d+").replace("#", r"\d+"), re.I), fld))
            self._fields[table] = (pats, ignore)
        return self._fields[table]

    def field(self, table: str, column: str) -> tuple[dict | None, bool]:
        """(field spec, ignored-by-guide)."""
        pats, ignore = self._table_fields(table)
        exact = [fld for p, fld in pats if p.pattern.replace("\\", "") == column]
        if exact:
            return exact[0], False
        for p, fld in pats:
            if p.fullmatch(column):
                return fld, False
        return None, column.lower() in ignore

    def allowed(self, spec: dict) -> set[str] | None:
        v = spec.get("d2rExcel", {}).get("validation", {})
        owner = spec
        if v.get("type") == "foreign" and v.get("foreignField") in self.enums:
            owner = self.enums[v["foreignField"]]
            v = owner.get("d2rExcel", {}).get("validation", {}) or {"type": "table"}
        if v.get("type") == "table" and owner.get("table"):
            col = v.get("tableModelColumn", 0)
            return {str(r[col]).strip() for r in owner["table"][v.get("tableStartRow", 1):] if col < len(r)}
        return None


def load_strings(dirs: Iterable[Path]) -> tuple[set[str], dict[str, int]]:
    """String keys D2R would see: per file name the first directory wins (a mod's file replaces the game's)."""
    keys: set[str] = set()
    ids: dict[str, int] = {}
    taken: set[str] = set()
    for d in dirs:
        for p in sorted(Path(d).glob("*.json")):
            if p.name.lower() in taken:
                continue
            taken.add(p.name.lower())
            try:
                data = json.loads(p.read_text("utf-8-sig"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(data, list):
                continue
            for e in data:
                if isinstance(e, dict) and "Key" in e:
                    k = str(e["Key"]).encode("utf-8").decode(ENCODING)  # compare as TXT bytes
                    keys.add(k)
                    if isinstance(e.get("id"), int):
                        ids.setdefault(k, e["id"])
    return keys, ids


# ---- validator ----------------------------------------------------------------------------------------------------
class Validator:
    def __init__(self, bank: Bank, schema: Schema, report: Report, strings: set[str] | None,
                 string_ids: dict[str, int], extra: dict, func_max: dict[tuple[str, str], int],
                 allow_extended_saves: bool):
        self.b, self.schema, self.r = bank, schema, report
        self.strings, self.string_ids = strings, string_ids
        self.extra = {k: set(v) for k, v in (extra or {}).items()}
        self.func_max = func_max
        self.allow_ext = allow_extended_saves
        self.has_base = bank.d2r is not None
        # findings D2R's own copies of the shipped tables produce: (table, column, problem, value)
        self.same: set[tuple[str, str, str, str]] | None = None
        self.fallback = False

    # -- findings --
    def add(self, sev: str, table: str, row: str, column: str, problem: str, value: str = "",
            item: str | None = None) -> None:
        """`value` is the cell; `item` the part of it the problem is about. An error D2R's own copy of the table
        has with the same value is a warn."""
        shown = f"{item} in {value}" if item else value
        known = self.same is not None and (self.b.prefix(table), column.lower(), problem, shown) in self.same
        if self.fallback:
            if known:
                return  # D2R's own table as D2R ships it: only what the mod's tables broke is of interest
            problem = f"{problem} (in D2R's own copy, which the mod does not ship)"
        elif sev == "error" and known:
            sev, problem = "warn", f"{problem} (D2R's own data has the same value)"
        self.r.add(sev, self.b.prefix(table), row, column, problem, shown)

    @staticmethod
    def rid(t: Table, i: int) -> str:
        key = t.rows[i][0] if t.rows[i] else ""
        return f"{key} (line {t.lines[i]})" if key else f"line {t.lines[i]}"

    # -- reference helpers --
    def ref_check(self, table: str, row: str, col: str, v: str, target: str, tcol: str, what: str = "") -> None:
        vals = self.b.values(target, tcol)
        if vals is None:
            note = f"{self.b.prefix(table)}.{col}: reference target {target}.{tcol} not found"
            if note not in self.r.notes:
                self.r.notes.append(note)
            return
        # D2R resolves names and codes regardless of letter case (its own tables rely on it)
        if v not in vals and v.lower() not in self.b.lower_values(target, tcol):
            self.add("error", table, row, col, what or f"no {target}.{tcol}", v)

    def calc_status(self, calc_table: str, code: str) -> tuple[str, str] | None:
        """None if D2R knows the code, else (severity, problem)."""
        d2r = self.b.values(calc_table, "code", pristine=True) if self.has_base else None
        mod = self.b.values(calc_table, "code") or set()
        if d2r is None:
            d2r = mod
        if code in d2r:
            return None
        if code in self.extra.get(calc_table, set()):
            return "plugin", f"{calc_table} code a plugin provides"
        if code in mod:
            return "plugin", f"{calc_table} code the mod adds (D2R implements only its own codes)"
        return "error", f"unknown {calc_table} code"

    # -- formulas --
    def check_bbe(self, table: str, col: str, row: str, text: str, scope: str) -> None:
        if len(text) > MAX_FORMULA:
            self.add("error", table, row, col, f"formula longer than {MAX_FORMULA} bytes", text)
        src = text.strip()
        tokens: list[tuple[str, str]] = []
        pos = 0
        while pos < len(src):
            m = TOKEN.match(src, pos)
            if not m or m.end() == pos:
                self.add("error", table, row, col, "formula: unreadable text", text)
                return
            tokens.append((m.lastgroup, m.group(m.lastgroup)))
            pos = m.end()
        depth = 0
        for k, v in tokens:
            if k == "op":
                depth += (v == "(") - (v == ")")
                if depth < 0:
                    break
        if depth < 0:
            self.add("error", table, row, col, "formula: ')' without a matching '('", text)
        elif depth > 0:  # D2R's own tables have formulas like this; the parser closes them at the end
            self.add("warn", table, row, col, "formula: unclosed '('", text)
        # bare negative literal inside an expression: D2R's parser rejects "min(0,-1*x)"; write (0-1)
        # (a formula that is only a negative constant is fine; "-N*..." at the start D2R's own tables have)
        if not re.fullmatch(r"-\s*\d+", src):
            for i, (k, v) in enumerate(tokens):
                if k == "op" and v == "-" and i + 1 < len(tokens) and tokens[i + 1][0] == "num":
                    if i == 0:
                        self.add("warn", table, row, col, "formula starts with a negative literal", text)
                    elif tokens[i - 1][0] == "op" and tokens[i - 1][1] != ")":
                        self.add("error", table, row, col, "formula: bare negative literal inside an expression "
                                 "(write (0-N))", text)
                        break
        for k, v in tokens:
            if k == "str" and len(v) - 2 > MAX_FORMULA_NAME:
                self.add("error", table, row, col, f"quoted name longer than {MAX_FORMULA_NAME} bytes in a formula "
                         "(D2R keeps 32 bytes)", v)
        calc_table = SCOPE_CODES.get(scope)
        consumed: set[int] = set()

        def tok(j: int) -> tuple[str, str]:
            return tokens[j] if 0 <= j < len(tokens) else ("", "")

        def code(ct: str, c: str, j: int, label: str) -> None:
            consumed.add(j)
            st = self.calc_status(ct, c)
            if st:
                self.add(st[0], table, row, col, f"{label}{st[1]}", text, c)

        for i, (k, v) in enumerate(tokens):
            if k != "id" or i in consumed:
                continue
            if tok(i + 1)[1] == "(":
                if v not in BBE_FUNCS:
                    if v in self.extra.get("functions", set()):
                        self.add("plugin", table, row, col, "formula function a plugin provides", text, f"{v}()")
                    else:
                        self.add("error", table, row, col, "unknown formula function", text, f"{v}()")
                    continue
                if v in ("skill", "sksrc", "sklvl", "miss", "stat"):
                    if tok(i + 2)[0] != "str":
                        self.add("error", table, row, col, f"{v}(): first argument must be a quoted name", text)
                        continue
                    name = tok(i + 2)[1][1:-1]
                    target = {"miss": ("missiles", "Missile"),
                              "stat": ("itemstatcost", "Stat")}.get(v, ("skills", "skill"))
                    names = self.b.values(*target)
                    if names is not None and name not in names and name.lower() not in self.b.lower_values(*target):
                        self.add("error", table, row, col, f"{v}(): unknown {target[0]} name", text, name)
                    j = i + 3
                    subs = []
                    while tok(j)[1] == "." and tok(j + 1)[0] in ("id", "num"):
                        subs.append((j + 1, tok(j + 1)[1]))
                        consumed.add(j + 1)
                        j += 2
                    want = 2 if v == "sklvl" else 1
                    if len(subs) != want:
                        self.add("error", table, row, col, f"{v}(): expected {want} '.' sub-code(s)", text)
                        continue
                    if v == "stat":
                        if subs[0][1] not in STAT_MODES:
                            self.add("error", table, row, col, "stat(): unknown mode (accr/base/mod)", text,
                                     subs[0][1])
                        continue
                    if v == "sklvl":  # level identifier is of the base scope
                        lvl = subs[0][1]
                        if not lvl.isdigit():
                            if calc_table:
                                code(calc_table, lvl, subs[0][0], "sklvl() level: ")
                            else:
                                self.add("error", table, row, col, f"{scope} scope formulas take no identifiers",
                                         text, lvl)
                    sub = subs[-1][1]
                    code("misscalc" if v == "miss" else "skillcalc", sub, subs[-1][0], f"{v}(): ")
                elif v == "cond":
                    if tok(i + 2)[0] != "str":
                        self.add("error", table, row, col, "cond(): first argument must be a quoted condition name",
                                 text)
                        continue
                    cname = tok(i + 2)[1][1:-1]
                    param = None
                    if tok(i + 3)[1] == ",":
                        consumed.add(i + 4)
                        param = tok(i + 4)[1].strip("'")
                    if cname not in CONDITIONS:
                        if cname in self.extra.get("conditions", set()):
                            self.add("plugin", table, row, col, "cond() condition a plugin provides", text, cname)
                        else:
                            self.add("error", table, row, col, "cond(): unknown condition", text, cname)
                        continue
                    allowed = CONDITIONS[cname]
                    if allowed == ():
                        if param is not None:
                            self.add("warn", table, row, col, f"cond('{cname}') takes no parameter", text)
                    elif param is None:
                        self.add("error", table, row, col, f"cond('{cname}') needs a parameter", text)
                    else:
                        if allowed is None:
                            allowed = set()
                            for t, c in COND_TABLES.get(cname, []):
                                allowed |= self.b.values(t, c) or set()
                        if allowed and param.lower() not in {a.lower() for a in allowed}:
                            self.add("error", table, row, col, f"cond('{cname}'): unknown parameter", text, param)
                continue
            if tok(i - 1)[1] == ".":
                continue
            if not calc_table:
                self.add("error", table, row, col, f"{scope} scope formulas take no identifiers", text, v)
            else:
                code(calc_table, v, i, "")

    # -- tables --
    def check_table(self, table: str, fallback: bool = False) -> None:
        """fallback: the table is D2R's own (the mod does not ship it); report only findings D2R itself lacks."""
        t = self.b.table(table)
        if t is None or not self.schema.has(table):
            return
        if not fallback:
            self.r.tables.append(self.b.prefix(table))
        self.fallback = fallback
        try:
            self._check_table(t, table)
        finally:
            self.fallback = False

    def _check_table(self, t: Table, table: str) -> None:
        for ci, col in enumerate(t.header):
            if not col or col.startswith("*"):
                continue
            spec, ignored = self.schema.field(table, col)
            explicit = next(((tt, tc) for tb, pat, tt, tc in EXPLICIT_REFS
                             if tb == table and re.fullmatch(pat, col, re.I)), None)
            if spec is None:
                if not ignored:
                    self.add("warn", table, "-", col, "column not in D2R's data guide")
                if explicit:
                    self._check_column(t, ci, col, {}, explicit, table)
                continue
            self._check_column(t, ci, col, spec, explicit, table)

    def _check_column(self, t: Table, ci: int, col: str, spec: dict, explicit: tuple[str, str] | None,
                      table: str) -> None:
        typ = spec.get("type", {}) if spec else {}
        kind = typ.get("type") if isinstance(typ, dict) else typ
        desc = re.sub(r"\s+", " ", (typ.get("description", "") if isinstance(typ, dict) else ""))
        allowed = self.schema.allowed(spec) if spec else None
        allowed_num = {int(a) for a in allowed if INT.fullmatch(a)} if allowed else set()
        ref = None
        if kind == "reference" and typ.get("file"):
            ref = (typ["file"].lower(), typ.get("field") or "")
            if ref[0] == "enums":
                ref = None                   # an enum list (checked through `allowed`)
        elif explicit and kind not in ("parse",):
            ref = explicit
        is_func = kind in ("int", None, "") and FUNC_COLUMN.search(col) is not None
        fmax = None
        if is_func:
            fmax = self.func_max.get((table, col.lower()))
            if allowed_num:
                fmax = max(fmax or 0, max(allowed_num))
        # parse columns
        scope = None
        alts: list[tuple[str, str]] | None = None
        params: list[tuple[str, str]] | None = None
        comp = False
        if kind == "parse":
            m = re.search(r"(Skill|Missile|Item|Monster|Treasure Class) scope BBE", desc)
            if m:
                scope = m.group(1)
            elif desc.startswith("A number or references"):
                params = re.findall(r"\$!([^!#]+)#([^!]+)!\$", desc)
            elif re.match(r"Reference", desc) and "described below" not in desc:
                alts = re.findall(r"\$!([^!#]+)#([^!]+)!\$", desc)
            elif "compcode" in desc:
                comp = True
        dlen = typ.get("dataLength") if isinstance(typ, dict) else 0
        alt_set = None
        if alts:
            alt_set = set()
            for f, fl in alts:
                alt_set |= self.b.values(f, fl) or set()
        param_set = None
        if params:
            param_set = set()
            for f, fl in params:
                param_set |= self.b.lower_values(f, fl)
        comp_set = self.b.values("compcode", "code") if comp else None

        for i, r in enumerate(t.rows):
            if ci >= len(r):
                continue
            v = r[ci]
            if v == "":
                continue
            row = self.rid(t, i)
            if kind == "int" and not INT.fullmatch(v):
                self.add("error", table, row, col, "not an integer", v)
                continue
            if kind == "boolean" and v not in ("0", "1"):
                self.add("error", table, row, col, "not 0/1", v)
                continue
            if kind in ("text", "string", "parse") and dlen and len(v) > dlen and not scope:
                self.add("error", table, row, col, f"longer than {dlen} bytes", v)
            if kind == "string" and self.strings is not None and v not in self.strings:
                self.add("error", table, row, col, "string key not in the string tables", v)
            if is_func and INT.fullmatch(v):
                n = int(v)
                slot = PLUGIN_FUNC_SLOTS.get((table, col.lower()))
                if fmax is not None and n > fmax:
                    if slot and slot[0] <= n <= slot[1]:
                        self.add("plugin", table, row, col, f"free D2RLoader plugin slot {slot[0]}-{slot[1]}", v)
                    else:
                        self.add("plugin", table, row, col, f"function number above D2R's highest ({fmax})", v)
                elif allowed_num and n not in allowed_num:  # within D2R's range; the guide may just omit it
                    self.add("warn", table, row, col, "function number not in the data guide's list", v)
            elif allowed is not None and v not in allowed:
                if not (INT.fullmatch(v) and int(v) in allowed_num):
                    self.add("error", table, row, col, "value not in D2R's allowed list", v)
            if ref is not None:
                self.ref_check(table, row, col, v, ref[0], ref[1])
            if alt_set is not None and alt_set and v not in alt_set:
                self.add("error", table, row, col, "not any of " + ", ".join(f"{f}.{fl}" for f, fl in alts), v)
            if param_set is not None and not INT.fullmatch(v) and v.lower() not in param_set:
                self.add("warn", table, row, col, "parameter is neither a number nor a known name", v)
            if comp_set:
                for part in v.split(","):
                    if part.strip() and part.strip() not in comp_set:
                        self.add("error", table, row, col, "unknown compcode", part.strip())
            if scope:
                self.check_bbe(table, col, row, v, scope)

    # -- rules the field schema does not express --
    def engine_rules(self, shipped: set[str], pristine: bool = False) -> list[tuple[str, str, str, str, str, str, str]]:
        """Rules D2R enforces at load time that the field schema does not express.

        Returns findings as (severity, table, row key, row label, column, problem, value) without recording them;
        run() runs the rules on pristine D2R too and turns an error D2R's own data also has into a warn."""
        b = self.b
        get = b.pristine if pristine else b.table
        vals = lambda t, c: b.values(t, c, pristine=pristine)  # noqa: E731
        out: list = []

        def emit(sev, table, t, i, col, problem, value=""):
            out.append((sev, table, t.rows[i][0], self.rid(t, i), col, problem, value))

        def rule(name: str) -> bool:
            return (pristine or name in shipped) and get(name) is not None

        if rule("treasureclassex"):
            t = get("treasureclassex")
            known: set[str] = set()
            for tn, c in (("weapons", "code"), ("armor", "code"), ("misc", "code"), ("itemtypes", "Code"),
                          ("treasureclassex", "Treasure Class"), ("uniqueitems", "index"), ("setitems", "index")):
                known |= vals(tn, c) or set()
            known_l = {k.lower() for k in known}
            pos = {r[0].lower(): i for i, r in enumerate(t.rows) if r and r[0]}
            for ci, c in enumerate(t.header):
                if not re.fullmatch(r"Item\d+", c):
                    continue
                for i, r in enumerate(t.rows):
                    v = r[ci] if ci < len(r) else ""
                    name = v.split(",", 1)[0].strip()
                    if not name:
                        continue
                    if name.lower() not in known_l and not AUTO_TC.match(name):
                        emit("error", "treasureclassex", t, i, c,
                             "not an item, item type, treasure class, unique or set item", v)
                    elif pos.get(name.lower(), -1) > i:
                        emit("error", "treasureclassex", t, i, c, "names a treasure class defined further down", v)

        if rule("setitems"):
            t = get("setitems")
            counts: dict[str, list[int]] = defaultdict(list)
            for i, r in enumerate(t.rows):
                if t.get(r, "set"):
                    counts[t.get(r, "set")].append(i)
            for name, idx in counts.items():
                if len(idx) > MAX_ITEMS_PER_SET:
                    emit("error", "setitems", t, idx[0], "set",
                         f"set has {len(idx)} items (D2R allows {MAX_ITEMS_PER_SET})", name)

        if rule("monstats") and get("monstats").has("MonStatsEx"):
            t = get("monstats")
            for i, r in enumerate(t.rows):
                if not t.get(r, "MonStatsEx"):
                    emit("error", "monstats", t, i, "MonStatsEx", "blank (a required link to monstats2)")

        if rule("hireling") and self.string_ids:
            t = get("hireling")
            for i, r in enumerate(t.rows):
                a, z = t.get(r, "NameFirst"), t.get(r, "NameLast")
                if a and z and not (self.string_ids.get(a, -1) < self.string_ids.get(z, -1)):
                    emit("error", "hireling", t, i, "NameLast",
                         "string id not above NameFirst's (the name range must run upwards)", f"{a}..{z}")

        lv, lp, lm = get("levels"), get("lvlprest"), get("lvlmaze")
        if lv and (pristine or shipped & {"levels", "lvlprest", "lvlmaze"}):
            if lp:
                claimed = {lp.get(r, "LevelId") for r in lp.rows}
                for i, r in enumerate(lv.rows):
                    if lv.get(r, "DrlgType") == "2" and lv.get(r, "Id") not in claimed:
                        emit("error", "levels", lv, i, "DrlgType", "preset level that no lvlprest row claims",
                             lv.get(r, "Id"))
            if lm:
                have = {lm.get(r, "Level") for r in lm.rows}
                for i, r in enumerate(lv.rows):
                    if lv.get(r, "DrlgType") == "1" and lv.get(r, "Id") not in have:
                        emit("error", "levels", lv, i, "DrlgType", "maze level without a lvlmaze row", lv.get(r, "Id"))
            dep = {lv.get(r, "Id"): (i, lv.get(r, "Depend")) for i, r in enumerate(lv.rows)}
            for start, (i, _d) in dep.items():
                seen, u = [], start
                while dep.get(u, (0, ""))[1] not in ("", "0") and u not in seen:
                    seen.append(u)
                    u = dep[u][1]
                if u == start and seen:
                    emit("error", "levels", lv, i, "Depend", "Depend chain loops back to this level", " -> ".join(seen))

        sk, sd = get("skills"), get("skilldesc")
        if sk and (pristine or shipped & {"skills", "skilldesc"}):
            per_class: dict[str, list[int]] = defaultdict(list)
            for i, r in enumerate(sk.rows):
                cc = sk.get(r, "charclass")
                if cc:
                    per_class[cc].append(i)
                    if not sk.get(r, "skilldesc"):
                        emit("error", "skills", sk, i, "skilldesc", "class skill without a skilldesc")
                    if sk.get(r, "anim") == "SQ" and sk.get(r, "seqnum") in ("", "0"):
                        emit("error", "skills", sk, i, "seqnum", "class skill with anim SQ (sequence) but no seqnum")
            for cc, idx in sorted(per_class.items()):
                if len(idx) > MAX_CLASS_SKILLS:
                    if self.allow_ext:
                        emit("plugin", "skills", sk, idx[MAX_CLASS_SKILLS], "charclass",
                             f"{len(idx)} class skills: needs D2RLoader's extended save format "
                             f"(D2R's saves hold {MAX_CLASS_SKILLS})", cc)
                    else:
                        emit("error", "skills", sk, idx[MAX_CLASS_SKILLS], "charclass",
                             f"{len(idx)} class skills (D2R's save format holds {MAX_CLASS_SKILLS} per class; "
                             "allow_extended_saves for D2RLoader's extended saves)", cc)
            if sd:
                where = {}
                for r in sd.rows:
                    where.setdefault(r[0].lower(), (sd.get(r, "SkillPage"), sd.get(r, "SkillRow"),
                                                    sd.get(r, "SkillColumn")))
                for cc, idx in sorted(per_class.items()):
                    cells: dict[tuple, list[int]] = defaultdict(list)
                    for i in idx:
                        p = where.get(sk.get(sk.rows[i], "skilldesc").lower())
                        if p and p[0] not in ("", "0"):
                            cells[p].append(i)
                    for p, rows in cells.items():
                        if len(rows) > 1:
                            emit("error", "skills", sk, rows[1], "skilldesc",
                                 f"class '{cc}': skills share skill tree page {p[0]} row {p[1]} column {p[2]} "
                                 "(skilldesc SkillPage/SkillRow/SkillColumn)",
                                 ", ".join(sk.rows[j][0] for j in rows))

        if not pristine and rule("inventory") and self.has_base:
            inv, ref = get("inventory"), b.pristine("inventory")
            if ref and len(inv.rows) != len(ref.rows):
                out.append(("error", "inventory", "", "-", "-",
                            f"{len(inv.rows)} rows; D2R's layout has {len(ref.rows)} (one per page and resolution)",
                            ""))
        return out

    def run(self) -> None:
        shipped = set(self.b.mod_tables())
        for t in sorted(shipped):
            if self.schema.has(t):
                self.check_table(t)
            elif t not in self.schema.files:
                self.r.notes.append(f"{self.b.prefix(t)}: not in D2R's data guide (not checked)")
        found = self.engine_rules(shipped)
        same = set()
        if self.has_base:
            same = {(t, k.lower(), c, p, v) for _s, t, k, _l, c, p, v in self.engine_rules(shipped, True)}
        for sev, table, key, label, col, problem, value in found:
            if sev == "error" and (table, key.lower(), col, problem, value) in same:
                sev, problem = "warn", f"{problem} (D2R's own data has the same)"
            self.r.add(sev, self.b.prefix(table), label, col, problem, value)


def function_maxima(base_data_dir: Path | None) -> dict[tuple[str, str], int]:
    """Highest number D2R's own tables use in each function column (both banks)."""
    out: dict[tuple[str, str], int] = {}
    root = excel_root(base_data_dir)
    if root is None:
        return out
    for d in (root, root / "base"):
        for p in d.glob("*.txt"):
            t = read_table(p)
            for ci, h in enumerate(t.header):
                if not FUNC_COLUMN.search(h):
                    continue
                nums = [int(r[ci]) for r in t.rows if ci < len(r) and INT.fullmatch(r[ci])]
                if nums:
                    k = (p.stem.lower(), h.lower())
                    out[k] = max(out.get(k, max(nums)), max(nums))
    return out


def validate(data_dir: Path, guide_dir: Path, strings_dir: Path | list[Path] | None = None,
             extra_calc_codes: dict | None = None, base_data_dir: Path | None = None,
             allow_extended_saves: bool = False) -> Report:
    """Validate both table banks of a mod's data root. See the module docstring."""
    data_dir, guide_dir = Path(data_dir), Path(guide_dir)
    base_data_dir = Path(base_data_dir) if base_data_dir else None
    report = Report()
    schema = Schema(guide_dir)
    if not schema.files:
        raise FileNotFoundError(f"no data guide *.js files in {guide_dir}")
    mod_root = excel_root(data_dir)
    if mod_root is None:
        raise FileNotFoundError(f"no global/excel/*.txt under {data_dir}")
    d2r_root = excel_root(base_data_dir)
    if base_data_dir and d2r_root is None:
        raise FileNotFoundError(f"no global/excel/*.txt under {base_data_dir}")

    if strings_dir is None:
        dirs = [p for p in (data_dir / "local" / "lng" / "strings",
                            base_data_dir / "local" / "lng" / "strings" if base_data_dir else None)
                if p and p.is_dir()]
    else:
        dirs = [Path(strings_dir)] if isinstance(strings_dir, (str, Path)) else [Path(p) for p in strings_dir]
    strings, ids = load_strings(dirs) if dirs else (None, {})
    if strings is None:
        report.notes.append("string keys: no string tables given (not checked)")

    func_max = function_maxima(base_data_dir)
    if not func_max:
        report.notes.append("function numbers: no base D2R data, only the guide's lists are used")
    if not base_data_dir:
        report.notes.append("no base D2R data: tables the mod does not ship cannot be resolved, "
                            "and errors are not compared with D2R's own values")

    banks = [Bank("", mod_root, d2r_root)]
    if (mod_root / "base").is_dir() and any((mod_root / "base").glob("*.txt")):
        banks.append(Bank("base", mod_root / "base", (d2r_root / "base") if d2r_root and (d2r_root / "base").is_dir() else None))
    for bank in banks:
        v = Validator(bank, schema, report, strings, ids, extra_calc_codes or {}, func_max, allow_extended_saves)
        unshipped: list[str] = []
        if bank.d2r is not None:
            # The same column checks on D2R's own tables: an error D2R itself has becomes a warn. D2R's tables the
            # mod does not ship are checked too, for references into rows the mod renamed or removed.
            shipped = set(bank.mod_tables())
            pristine = Bank(bank.name, bank.d2r, bank.d2r)
            scratch = Report()
            pv = Validator(pristine, schema, scratch, strings, ids, extra_calc_codes or {}, func_max,
                           allow_extended_saves)
            for t in pristine.mod_tables():
                if schema.has(t):
                    pv.check_table(t)
                    if t not in shipped:
                        unshipped.append(t)
            v.same = {(t, c.lower(), p, val) for _s, t, _r, c, p, val in scratch.raw}
        v.run()
        for t in unshipped:
            v.check_table(t, fallback=True)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m modkit.validate", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, type=Path, help="mod data root (contains global/excel)")
    ap.add_argument("--guide", type=Path, help="D2R data guide files folder (dataguide/data/files); "
                    "default: <base>/global/dataguide/data/files")
    ap.add_argument("--base", type=Path, help="pristine D2R data root to compare against and fall back to")
    ap.add_argument("--strings", type=Path, nargs="+", action="extend",
                    help="string JSON folders, mod's first (default: <data>/local/lng/strings, <base>/local/lng/strings)")
    ap.add_argument("--extra-calc-codes", type=Path, help="JSON: {skillcalc|misscalc|moncalc|functions|conditions: [...]}")
    ap.add_argument("--allow-extended-saves", action="store_true", help="more than 30 class skills is a plugin note")
    ap.add_argument("--report", type=Path, help="write the Markdown report here")
    ap.add_argument("--examples", type=int, default=3)
    a = ap.parse_args(argv)
    guide = a.guide
    if guide is None:
        for root in (a.base, a.data):
            if root and (root / "global" / "dataguide" / "data" / "files").is_dir():
                guide = root / "global" / "dataguide" / "data" / "files"
                break
    if guide is None:
        ap.error("--guide is required (no dataguide under --base or --data)")
    extra = json.loads(a.extra_calc_codes.read_text("utf-8")) if a.extra_calc_codes else None
    rep = validate(a.data, guide, a.strings, extra, a.base, a.allow_extended_saves)
    md = rep.to_markdown(a.examples)
    if a.report:
        a.report.write_text(md + "\n", encoding="utf-8")
    s = rep.summary
    print(f"errors {s['errors']}, plugin {s['plugin']}, warnings {s['warnings']} ({s['tables']} tables)")
    for line in md.splitlines():
        if line.startswith("| ") and not line.startswith("| Table") and not a.report:
            print(line)
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
