"""A mod project and its build.

A project is a folder:

    mymod/
      mod.json          {"name": "MyMod", "savepath": "MyMod/", "extendedSaves": false, "plugins": []}
      patches/*.json    change lists, applied in file-name order (each: {"ops": [...]} or a bare list)
      assets/           PNGs and other inputs the patches name
      files/data/...    files copied as they are into the mod (applied before the patches)

`modkit build mymod` starts from the pristine game files (base.py) every time, applies files/ and every patch, and
writes only the changed files to mymod/out/data/. The same patches always give the same mod, so a patch can be
read, reviewed and reverted like code. Operations are listed in OPS (docs in knowledge/modkit-ops.md).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import dc6, hd, images, layouts
from .base import Base
from .strings import Strings
from .tsv import Table, fix_formula

EXCEL = "data/global/excel/"
BANKS = {"excel": "", "base": "base/"}  # excel/ = D2R's current (RotW) bank, excel/base/ = Classic/LoD


class BuildError(Exception):
    pass


class Build:
    def __init__(self, project: Path, base: Base | None = None):
        self.project = Path(project)
        self.mod = json.loads((self.project / "mod.json").read_text("utf-8"))
        self.base = base or Base(self.mod.get("game"))
        self.out = self.project / "out" / "data"
        self.tables: dict[str, Table] = {}
        self.texts: dict[str, str] = {}
        self.jsons: dict[str, object] = {}
        self.blobs: dict[str, bytes] = {}
        self.icons: dict[str, hd.IconStrip] = {}
        self.strings = Strings(self.base)
        self.log: list[str] = []
        self.warnings: list[str] = []
        self._copied: set[str] = set()

    # ---- file access (cached; written by finish())
    def table(self, name: str, bank: str = "excel") -> Table:
        rel = f"{EXCEL}{BANKS[bank]}{name.lower()}.txt"
        if rel not in self.tables:
            local = self.out / rel[5:]
            src = local.read_text("latin-1") if rel in self._copied else self.base.path(rel).read_text("latin-1")
            self.tables[rel] = Table.parse(name, src)
        return self.tables[rel]

    def banks(self, name: str, which: str = "both") -> list[str]:
        wanted = ["excel", "base"] if which == "both" else [which]
        return [b for b in wanted if self.base.exists(f"{EXCEL}{BANKS[b]}{name.lower()}.txt")]

    def _source_text(self, rel: str) -> str:
        """A file's current text: the project's files/ copy if it has one, else the game's."""
        if rel in self._copied:
            return (self.out / rel[5:]).read_text("utf-8-sig")
        return self.base.text(rel)

    def text(self, rel: str) -> str:
        if rel not in self.texts:
            self.texts[rel] = self._source_text(rel)
        return self.texts[rel]

    def set_text(self, rel: str, text: str) -> None:
        self.texts[rel] = text

    def json(self, rel: str):
        if rel not in self.jsons:
            self.jsons[rel] = json.loads(self._source_text(rel))
        return self.jsons[rel]

    def icon_strip(self, charclass: str) -> hd.IconStrip:
        if charclass not in self.icons:
            hd_rel, classic_rel = hd.icon_paths(charclass)
            strip, fw, frames = images.read_sprite(self.base.path(hd_rel))
            classic = dc6.read(self.base.path(classic_rel)) if self.base.exists(classic_rel) else None
            pal = dc6.palette(self.base.bytes("data/global/palette/units/pal.dat")) if classic else None
            self.icons[charclass] = hd.IconStrip(strip, fw, frames, classic, pal)
        return self.icons[charclass]

    def asset(self, rel: str) -> Path:
        p = self.project / rel
        if not p.exists():
            raise BuildError(f"asset not found: {p}")
        return p

    # ---- run
    def run(self) -> None:
        if self.out.parent.exists():
            shutil.rmtree(self.out.parent)
        self.out.mkdir(parents=True)
        files = self.project / "files" / "data"
        if files.exists():
            for p in files.rglob("*"):
                if p.is_file():
                    rel = "data/" + p.relative_to(files).as_posix()
                    dest = self.out / rel[5:]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                    self._copied.add(rel)
        for patch in sorted((self.project / "patches").glob("*.json")):
            doc = json.loads(patch.read_text("utf-8"))
            ops = doc["ops"] if isinstance(doc, dict) else doc
            for n, op in enumerate(ops):
                kind = op.get("op")
                if kind not in OPS:
                    raise BuildError(f"{patch.name} op {n}: unknown op '{kind}' (known: {', '.join(sorted(OPS))})")
                try:
                    OPS[kind](self, op)
                except (KeyError, ValueError, BuildError) as e:
                    raise BuildError(f"{patch.name} op {n} ({kind}): {e}") from e
        self.finish()

    def finish(self) -> None:
        for rel, t in self.tables.items():
            self._write(rel, t.text().encode("latin-1"))
        for rel, text in self.texts.items():
            self._write(rel, text.encode("utf-8-sig") if rel.endswith(".json") else text.encode("utf-8"))
        for rel, obj in self.jsons.items():
            self._write(rel, hd.dumps(obj).encode("utf-8"))
        for rel, data in self.blobs.items():
            self._write(rel, data)
        for charclass, strip in self.icons.items():
            hd_rel, classic_rel = hd.icon_paths(charclass)
            images.write_sprite(self.out / hd_rel[5:], strip.strip, strip.frame_width, strip.frames)
            if strip.classic is not None:
                dc6.write(self.out / classic_rel[5:], strip.classic)
        for rel in self.strings.write(self.out.parent):
            self.log.append(f"strings: {rel}")
        build_txt = self.out / "global" / "dataversionbuild.txt"
        if not build_txt.exists() and self.base.exists("data/global/dataversionbuild.txt"):
            build_txt.parent.mkdir(parents=True, exist_ok=True)
            build_txt.write_bytes(self.base.bytes("data/global/dataversionbuild.txt"))

    def _write(self, rel: str, data: bytes) -> None:
        dest = self.out / rel[5:]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


# =================================================================== operations
def _values(build: Build, values: dict, formula_cols: bool = True) -> dict:
    return {k: (fix_formula(v) if isinstance(v, str) and formula_cols else v) for k, v in values.items()}


def op_add_row(b: Build, op: dict) -> None:
    """{"op":"add_row","table":"misc","values":{...},"bank":"both"} - appends a record (new ID = last + 1)."""
    for bank in b.banks(op["table"], op.get("bank", "both")):
        t = b.table(op["table"], bank)
        key = str(op["values"].get(t.header[0], ""))
        if key and t.find(key) is not None:
            raise BuildError(f"{op['table']}: '{key}' already exists (use set)")
        t.append(_values(b, op["values"]))


def op_clone_row(b: Build, op: dict) -> None:
    """{"op":"clone_row","table":"skills","from":"Ice Bolt","key":"Frost Lance","set":{...}}"""
    for bank in b.banks(op["table"], op.get("bank", "both")):
        b.table(op["table"], bank).clone(op["from"], op["key"], _values(b, op.get("set", {})))


def op_set(b: Build, op: dict) -> None:
    """{"op":"set","table":"skills","key":"Ice Bolt","set":{...}} or "where": {"column": "value"} for many rows."""
    for bank in b.banks(op["table"], op.get("bank", "both")):
        t = b.table(op["table"], bank)
        rows = t.where(**op["where"]) if "where" in op else [t.need(op["key"], op.get("column"))]
        if not rows:
            raise BuildError(f"{op['table']}: no row matches {op.get('where')}")
        for r in rows:
            t.update(r, _values(b, op["set"]))


def op_delete_row(b: Build, op: dict) -> None:
    """{"op":"delete_row","table":"treasureclassex","key":"..."} - keyed tables only (IDs of later rows shift)."""
    for bank in b.banks(op["table"], op.get("bank", "both")):
        b.table(op["table"], bank).delete(op["key"])


def op_string(b: Build, op: dict) -> None:
    """{"op":"string","key":"MySkillName","text":"Frost Lance","file":"skills.json"} (text may be {lang: text})."""
    b.strings.set(op["key"], op["text"], op.get("file", "item-modifiers.json"), op.get("allLanguages", True),
                  op.get("force", False))


def op_layout(b: Build, op: dict) -> None:
    """{"op":"layout","file":"bankexpansionlayouthd.json","widget":"grid","field":"cellCount","value":{...}}
    or "set": {field: value, ...}; "replace": [[old, new], ...] does plain text replacements in the file."""
    rel = op["file"] if op["file"].startswith("data/") else f"data/global/ui/layouts/{op['file']}"
    t = b.text(rel)
    for old, new in op.get("replace", []):
        t = layouts.replace_all(t, old, new)
    fields = op.get("set", {})
    if "field" in op:
        fields = {op["field"]: op["value"], **fields}
    for field, value in fields.items():
        t = layouts.set_field(t, op["widget"], field, value, op.get("occurrence", 0))
    b.set_text(rel, t)


def op_sprite(b: Build, op: dict) -> None:
    """{"op":"sprite","png":"assets/x.png","to":"data/hd/global/ui/panel/mymod/x.sprite","size":[w,h],
    "frames":1,"lowend":true} - PNG -> HD sprite (+ .lowend twin); size rescales first."""
    img = images.read_png(b.asset(op["png"]))
    if "size" in op:
        img = images.scale(img, *op["size"])
    images.write_sprite(b.out / op["to"][5:], img, frames=op.get("frames", 1), lowend=op.get("lowend", True))


def op_panel(b: Build, op: dict) -> None:
    """{"op":"panel","png":"assets/stash.png","name":"mymod/stash","layout":"bankexpansionlayouthd.json",
    "replaceImage":"PANEL\\Stash\\StashPanel_BG_Personal"} - a panel background (1162 x 1507) and the layout
    reference switched to it (PANEL\\mymod\\stash = data/hd/global/ui/panel/mymod/stash.sprite)."""
    img = images.scale(images.read_png(b.asset(op["png"])), *op.get("size", (1162, 1507)))
    images.write_sprite(b.out / f"hd/global/ui/panel/{op['name'].lower()}.sprite", img)
    if "layout" in op:
        rel = f"data/global/ui/layouts/{op['layout']}"
        new = "PANEL\\" + op["name"].replace("/", "\\")
        olds = op["replaceImage"] if isinstance(op["replaceImage"], list) else [op["replaceImage"]]
        t = b.text(rel)
        for old in olds:
            t = layouts.replace_all(t, json.dumps(old), json.dumps(new))
        b.set_text(rel, t)


def op_copy(b: Build, op: dict) -> None:
    """{"op":"copy","from":"assets/sound.flac","to":"data/hd/global/sfx/x.flac"}"""
    b.blobs[op["to"]] = b.asset(op["from"]).read_bytes()


def op_json(b: Build, op: dict) -> None:
    """{"op":"json","file":"data/hd/missiles/missiles.json","path":["frostlance"],"value":"icebolt"} - sets a value
    inside any JSON game file (dict keys / list indexes along path)."""
    doc = b.json(op["file"])
    node = doc
    for k in op["path"][:-1]:
        node = node[k]
    node[op["path"][-1]] = op["value"]


def op_missile_hd(b: Build, op: dict) -> None:
    """{"op":"missile_hd","missile":"frostlance","like":"icebolt"} (or "model": "<HD model>") - HD look of a
    missile: missiles.json name -> model."""
    m = b.json("data/hd/missiles/missiles.json")
    model = op.get("model") or m.get(op["like"].lower())
    if model is None:
        raise BuildError(f"missiles.json has no entry '{op.get('like')}'")
    m[op["missile"].lower()] = model


def op_item_hd(b: Build, op: dict) -> None:
    """HD inventory picture of an item.
    {"op":"item_hd","code":"xyz","asset":"weapon/axe/axe"}                      base item -> existing asset
    {"op":"item_hd","code":"xyz","table":"weapons","png":"assets/x.png","cells":[2,3],"asset":"mymod/x"}
    {"op":"item_hd","unique":"My Unique","asset":"..."} / "set": "My Set Item"  (normal/uber/ultra all set)"""
    asset = op["asset"]
    if "png" in op:
        folder = hd.item_folder(op["table"])
        w, h = op.get("cells", (1, 1))
        img = images.scale(images.read_png(b.asset(op["png"])), 98 * w, 98 * h)
        images.write_sprite(b.out / f"hd/global/ui/items/{folder}/{asset}.sprite", img)
    if "code" in op:
        hd.set_list_entry(b.json("data/hd/items/items.json"), op["code"], {"asset": asset})
    for kind, file in (("unique", "uniques.json"), ("set", "sets.json")):
        if kind in op:
            hd.set_list_entry(b.json(f"data/hd/items/{file}"), hd.index_key(op[kind]),
                              {"normal": asset, "uber": asset, "ultra": asset})


def op_hd_sequence(b: Build, op: dict) -> None:
    """{"op":"hd_sequence","skill":"Frost Lance","like":"Whirlwind"} - HD body animation for a sequence (anim SQ)
    skill: a copy of the like-skill's state in the class's state machine."""
    skills = b.table("skills")
    row, like = skills.need(op["skill"]), skills.need(op["like"])
    charclass = op.get("class", skills.get(row, "charclass") or skills.get(like, "charclass"))
    sm = b.json(hd.state_machine_path(charclass))
    hd.add_sequence_state(sm, skills.record_id(like), skills.record_id(row), op["skill"])


def op_skill(b: Build, op: dict) -> None:
    """A complete skill in one op (see .claude/skills/d2r-skill/SKILL.md for the fields)."""
    from .skill import build_skill
    build_skill(b, op)


OPS = {
    "add_row": op_add_row, "clone_row": op_clone_row, "set": op_set, "delete_row": op_delete_row,
    "string": op_string, "layout": op_layout, "sprite": op_sprite, "panel": op_panel, "copy": op_copy,
    "json": op_json, "missile_hd": op_missile_hd, "item_hd": op_item_hd, "hd_sequence": op_hd_sequence,
    "skill": op_skill,
}
