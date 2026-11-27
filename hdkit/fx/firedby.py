"""What fires each D2R HD particle effect: a reverse binding index (hdfx lane r3-bind3).

`fx/context.py` answers "what does this unit / missile / overlay / skill draw"; this module answers the reverse, "what
draws this `.particles` file", for every file, from the same data: the HD definition JSON and timelines, the excel tables,
and (optional) the game executable's strings. One pass over the definitions gives:

1. **refs**: every place a definition names a `.particles` path, with its trigger (the same names as
   `context.Binding.trigger`: `entity`, `object`, `timeline`, `screenspace`, `attack_success`, `global`; plus `manifest` for
   `dependencies.*[]` preload lists, which bind nothing, and `other`).
2. **chains**: how each defining file is reached: through other definitions that name it (`prefab`, `biomeFilename`,
   `presetFilename`, ... any `.json` path; `.timelines` -> the unit JSON of its folder, or the JSON whose skeleton sits in
   `<folder>/skeleton/`, as `context.timeline_path`) up to a **root**, something the
   game loads by itself: a missiles.txt / overlay.txt / monstats / objects.txt / lvlprest row (with the skills, states and
   missiles that use that row), an item code, a player class, a global table, or an exe string. A defining file that
   reaches no root is an **orphan** (a test room, a prop palette, a definition no row names).
3. **status** per effect: `bound` (a non-manifest ref from a reachable definition), `exe` (the path is a string in the
   executable: hard-coded), `orphan` (named only by orphan definitions), `manifest` (named only by preload lists),
   `unreferenced` (named nowhere). The last three are the unused ones; `evidence()` adds why (authoring folder, a bound
   copy with the same name, a bound sibling that merged the variants, raw name hits elsewhere).

Inputs are a data folder (`hd/`, `global/excel/`: an extracted CASC tree) or any `read(logical) -> bytes` with a name list.
Effects themselves never name other effect files: a baked `.particles` holds its child layers and spawners inside
(checked over the 2,162 files of build 95032: no `.particles` string in any of them), so there is no file-to-file edge.

  python -m hdkit.fx.firedby build <data folder> --corpus <root with hd/vfx> [--exe <exe>] [--raw rawhits.jsonl] --out <dir>
  python -m hdkit.fx.firedby show <index.json> <particle path or name>
Writes <dir>/firedby.json (the index) and <dir>/FIREDBY.md (the report). `load(index)` / `FiredBy.of(path)` serve the
viewer and `fx/api.py`'s `fx_fired_by`.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .context import (MISSILE_CHILD_COLS, SKILL_MISSILE_COLS, SKILL_OVERLAY_COLS, STATE_OVERLAY_COLS, Tables, norm_key)

TRIGGERS = ("entity", "object", "timeline", "screenspace", "attack_success", "global", "other", "manifest")
UNUSED = ("orphan", "manifest", "unreferenced")
AUTHORING = ("hd/vfx/personal/", "hd/vfx/library/", "hd/bvt/", "hd/vfx/presets/")
_TOKEN = re.compile(rb"[A-Za-z0-9_./\\ -]{6,}")
_TOKEN16 = re.compile(rb"(?:[A-Za-z0-9_./\\ -]\x00){6,}")


def logical(s: str) -> str:
    """'data/hd/vfx/Particles/X.particles' -> 'hd/vfx/particles/x.particles' (D2R paths are case-insensitive)."""
    s = s.strip().replace("\\", "/").lower().lstrip("/")
    return s[5:] if s.startswith("data/") else s


def loads(data: bytes):
    """JSON, or the game's relaxed JSON (// comments, trailing commas); None when neither parses."""
    text = data.decode("utf-8-sig", "replace")
    try:
        return json.loads(text)
    except ValueError:
        pass
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m.group(1) or "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(text)
    except ValueError:
        return None


@dataclass
class Ref:
    """One place a definition names an effect."""
    file: str               # the effect, logical lower-case
    src: str                # the definition (json / timelines), logical
    trigger: str
    key: str                # key path in the definition
    entity: str = ""        # entity name or timeline event name
    comp: str = ""          # component / parameter type
    mode: str = ""          # timeline name (animation mode)
    bone: str = ""


def _trigger(key: str, comp: str, event: str, src: str) -> str:
    if "/dependencies/" in key or key.startswith("dependencies/"):
        return "manifest"
    if event:
        return "timeline"
    if comp == "ObjectEffectDefinitionComponent":
        return "object"
    if comp == "VfxDefinitionComponent" or comp == "VfxVariantComponent":
        return "entity"
    if "screenspaceVfx" in key:
        return "screenspace"
    if "attackSuccessVfxOverrides" in key:
        return "attack_success"
    if src.startswith("hd/global/") or src.startswith("global/"):
        return "global"
    return "other"


def scan_definition(src: str, doc) -> tuple[list[Ref], set[str]]:
    """Every `.particles` reference (as Ref) and every `.json` / `.skeleton` path a parsed definition names."""
    refs, jsons = [], set()

    def walk(n, key, comp, ent, event, mode, bone):
        if isinstance(n, dict):
            t = n.get("type") if isinstance(n.get("type"), str) else ""
            if "eventTimestamp" in n:                      # a timeline event: its params name file, bone
                event, bone = t, ""
                for p in n.get("eventParams") or []:
                    if isinstance(p, dict) and p.get("parameterName") in ("boneName", "sourceBoneName", "unitPart"):
                        bone = str(p.get("value") or "")
                ent = str(n.get("name") or "")
            elif t.endswith(("Component", "Parameter")) or t in ("AttackVFXRule",):
                comp = t
            if "components" in n and isinstance(n.get("name"), str):
                ent = n["name"]
            for k, v in n.items():
                m = mode
                if key == "timelines":
                    m = k
                walk(v, key + "/" + k if key else k, comp, ent, event, m, bone)
        elif isinstance(n, list):
            for v in n:
                walk(v, key, comp, ent, event, mode, bone)
        elif isinstance(n, str):
            low = n.lower()
            if low.endswith(".particles"):
                refs.append(Ref(logical(n), src, _trigger(key, comp, event, src), key, ent, comp, mode, bone))
            elif low.endswith((".json", ".skeleton")) and "/" in low:
                jsons.add(logical(n))

    walk(doc, "", "", "", "", "", "")
    return refs, jsons


def snake(name: str) -> str:
    """objects.txt `Class` -> objects.json key: 'WallTorch1' -> 'wall_torch_1', 'TorchAct2' -> 'torch_act_2'."""
    return re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])", "_", name.strip()).lower().replace(" ", "_")


def exe_strings(data: bytes) -> set[str]:
    """Lower-cased printable runs (ASCII and UTF-16LE) of an executable that look like file names."""
    out = set()
    toks = _TOKEN.findall(data) + [t[0::2] for t in _TOKEN16.findall(data)] + [t[0::2] for t in _TOKEN16.findall(data[1:])]
    for t in toks:
        if b"." in t or b"/" in t:
            out.add(t.decode("latin-1").lower().replace("\\", "/"))
    return out


# ---------------------------------------------------------------------------------------------------- roots (excel, maps)
@dataclass
class Root:
    kind: str       # missile, overlay, monster, player, object, item, level, global, exe, vis
    name: str
    users: list = field(default_factory=list)       # "skill X (cltmissile)", "state Y (overlay1)", ...

    def text(self) -> str:
        u = "; ".join(self.users[:6]) + (" +%d" % (len(self.users) - 6) if len(self.users) > 6 else "")
        return "%s %s" % (self.kind, self.name) + (" <- " + u if u else "")


class Roots:
    """What the game loads by itself, from the excel tables and the HD name maps."""

    def __init__(self, read, names: list[str], tables: Tables | None, exe: set[str] | None):
        self.read, self.names, self.t, self.exe = read, set(names), tables, exe or set()
        self.missile = collections.defaultdict(list)     # hd/missiles/<x>.json -> missiles.txt names
        self.overlay = collections.defaultdict(list)     # hd/overlays/<f>/<x>.json -> overlay.txt names
        self.monster = collections.defaultdict(list)     # hd/character/<t>/<x>.json -> monstats ids
        self.object = collections.defaultdict(list)      # hd/objects/<f>/<x>.json -> objects.txt classes
        self.item = collections.defaultdict(list)        # hd/items/<k>/<asset>.json -> item codes
        self.level = collections.defaultdict(list)       # hd/env/preset/<ds1>.json -> lvlprest names
        self.warp = collections.defaultdict(list)        # hd/roomtiles/<x>.json -> lvlwarp names
        self._exe_tails = {}
        tails = {s for s in self.exe if "/" in s and "." not in s.rsplit("/", 1)[-1]}
        for n in self.names:
            if n.endswith(".json"):
                parts = n[:-5].split("/")
                for k in range(2, len(parts) - 1):
                    t = "/".join(parts[-k:])
                    if t in tails:
                        self._exe_tails[n[:-5]] = t
                        break
        self._maps()

    def _json(self, lg):
        try:
            return loads(self.read(lg))
        except (OSError, KeyError):
            return None

    def _rows(self, table):
        return self.t.rows(table) if self.t is not None and self.t.has(table) else []

    def _maps(self) -> None:
        mm = self._json("hd/missiles/missiles.json") or {}
        rows = self._rows("missiles")
        names = [r.get("missile", "") for r in rows] or [k for k in mm if k != "dependencies"]
        for n in names:
            k = norm_key(n)
            model = mm.get(k) if isinstance(mm.get(k), str) else (mm.get(n.lower()) if isinstance(mm.get(n.lower()), str) else None)
            for m in {model, k} - {None}:
                p = "hd/missiles/%s.json" % m.lower()
                if p in self.names:
                    self.missile[p].append(n)
                    break
        for r in self._rows("overlay"):
            n, c = r.get("overlay", ""), (r.get("character") or "").lower()
            folders = (["common" if c == "all" else c] if c else []) + ["common", "monster"]
            for f in folders:
                p = "hd/overlays/%s/%s.json" % (f, norm_key(n))
                if p in self.names:
                    self.overlay[p].append(n)
                    break
        alias = self._json("hd/character/monsters.json") or {}
        ids = {r.get("id", "").lower() for r in self._rows("monstats")} or set(alias)
        for mid, unit in alias.items():
            if isinstance(unit, str) and (mid.lower() in ids):
                for t in ("enemy", "npc"):
                    p = "hd/character/%s/%s.json" % (t, unit.lower())
                    if p in self.names:
                        self.monster[p].append(mid)
        for mid in ids:                                       # an id with no monsters.json entry: the id as-is
            for t in ("enemy", "npc"):
                p = "hd/character/%s/%s.json" % (t, mid)
                if p in self.names and mid not in self.monster[p]:
                    self.monster[p].append(mid)
        om = self._json("hd/objects/objects.json") or {}
        classes = {snake(r.get("class", "")): r.get("class", "") for r in self._rows("objects")}
        for key, v in om.items():                            # objects.json: the game's map object -> HD asset folder
            if isinstance(v, dict) and v.get("asset_path"):
                p = "hd/objects/%s/%s.json" % (v["asset_path"].lower(), key.lower())
                if p in self.names:
                    c = classes.get(key.lower())
                    self.object[p].append("%s (objects.txt %s)" % (key, c) if c else key)
        by_asset = collections.defaultdict(list)
        for n in self.names:
            if n.startswith("hd/items/") and n.endswith(".json"):
                by_asset["/".join(n[:-5].split("/")[3:])].append(n)
        for mapf in ("hd/items/items.json", "hd/items/uniques.json", "hd/items/sets.json"):
            for entry in self._json(mapf) or []:
                for code, v in (entry.items() if isinstance(entry, dict) else ()):
                    for a in ([v.get("asset")] if isinstance(v, dict) and "asset" in v else
                              (list(v.values()) if isinstance(v, dict) else [])):
                        for p in by_asset.get(str(a).lower(), []):
                            if code not in self.item[p]:
                                self.item[p].append(code)
        for r in self._rows("lvlprest"):
            for i in range(1, 7):
                f = (r.get("file%d" % i) or "").lower()
                if f.endswith(".ds1"):
                    p = "hd/env/preset/%s.json" % f[:-4]
                    if p in self.names:
                        self.level[p].append(r.get("name", ""))
        for r in self._rows("lvlwarp"):                        # hd/roomtiles/<lvlwarp Name, snake-cased>.json
            n = r.get("name", "")
            p = "hd/roomtiles/%s.json" % re.sub(r"[^a-z0-9]+", "_", n.lower()).strip("_")
            if p in self.names:
                self.warp[p].append(n)

    def _users(self, kind: str, name: str) -> list:
        """Who uses one excel row: skills (missile / overlay columns), states, missiles (children, ProgOverlay)."""
        out, low = [], name.lower()
        if kind == "missile":
            for r in self._rows("skills"):
                out += ["skill %s (%s)" % (r.get("skill"), c) for c in SKILL_MISSILE_COLS if (r.get(c) or "").lower() == low]
            for r in self._rows("missiles"):
                out += ["missile %s (%s)" % (r.get("missile"), c) for c in MISSILE_CHILD_COLS if (r.get(c) or "").lower() == low]
        elif kind == "overlay":
            for r in self._rows("skills"):
                out += ["skill %s (%s)" % (r.get("skill"), c) for c in SKILL_OVERLAY_COLS if (r.get(c) or "").lower() == low]
            for r in self._rows("states"):
                out += ["state %s (%s)" % (r.get("state"), c) for c in STATE_OVERLAY_COLS if (r.get(c) or "").lower() == low]
            for r in self._rows("missiles"):
                if (r.get("progoverlay") or "").lower() == low:
                    out.append("missile %s (ProgOverlay)" % r.get("missile"))
        return out

    def of(self, lg: str) -> list[Root]:
        """The roots that load definition `lg` directly (empty: it needs a parent definition, or nothing loads it)."""
        res = []
        for kind, m in (("missile", self.missile), ("overlay", self.overlay)):
            for n in m.get(lg, []):
                res.append(Root(kind, n, self._users(kind, n)))
        if lg.startswith("hd/overlays/monster/"):                      # the monster name rule (context: trigger "name")
            stem = lg.rsplit("/", 1)[1][:-5]
            for t in ("enemy", "npc"):
                if "hd/character/%s/%s.json" % (t, stem) in self.names and not any(r.kind == "overlay" for r in res):
                    res.append(Root("overlay", stem, ["unit %s (monster-folder overlay named after the unit)" % stem]))
        res += [Root("monster", n) for n in self.monster.get(lg, [])]
        res += [Root("object", n) for n in self.object.get(lg, [])]
        res += [Root("item", n) for n in self.item.get(lg, [])]
        res += [Root("level", n) for n in self.level.get(lg, [])]
        res += [Root("warp", n) for n in self.warp.get(lg, [])]
        m = re.match(r"(hd/.+)/animation/[^/]+\.timelines$", lg)
        if m and (m.group(1) in self.exe or "data/" + m.group(1) in self.exe):    # a timeline folder the code names
            res.append(Root("exe", "%s (folder string)" % m.group(1)))
        if re.match(r"hd/character/player/[^/]+\.json$", lg):
            res.append(Root("player", lg.rsplit("/", 1)[1][:-5]))
        if lg.startswith("hd/global/") or lg.startswith("hd/objects/vfx_only/"):
            res.append(Root("global", lg))
        if lg in self.exe or ("data/" + lg) in self.exe:
            res.append(Root("exe", lg))
        elif lg[:-5] in self._exe_tails:                 # 'gold/gold_large': a name the code completes ('%s.json')
            res.append(Root("exe", "%s (string %r)" % (lg, self._exe_tails[lg[:-5]])))
        return res


# ---------------------------------------------------------------------------------------------------- the index
def _parent_of_timeline(lg: str, names: set) -> list[str]:
    """`<unit folder>/animation/combined.timelines` belongs to `<unit folder>.json` (context.timeline_path's convention)."""
    m = re.match(r"(hd/.+?)/([^/]+)/animation/[^/]+\.timelines$", lg)
    if not m:
        return []
    own = "%s/%s.json" % (m.group(1), m.group(2))
    return [own] if own in names else []


def build(read, names, tables: Tables | None = None, exe: bytes | None = None, corpus: list[str] | None = None,
          raw: list[dict] | None = None, max_chains: int = 6, progress=None) -> dict:
    """The index. `read(logical) -> bytes`; `names`: the logical names of the definitions (json, timelines); `corpus`: the
    effects to report on (default: every effect named anywhere); `raw`: rows {src, target, kind, token} of a raw name scan
    over other files (weak evidence only)."""
    names = sorted({logical(n) for n in names})
    nset = set(names)
    exe_s = exe_strings(exe) if exe else set()
    refs: dict[str, list[Ref]] = collections.defaultdict(list)
    parents: dict[str, set] = collections.defaultdict(set)
    bad = []
    for i, lg in enumerate(names):
        if not lg.endswith((".json", ".timelines")):
            continue
        doc = loads(read(lg))
        if doc is None:
            bad.append(lg)
            continue
        rs, js = scan_definition(lg, doc)
        for r in rs:
            refs[r.file].append(r)
        own_tl = lg[:-5] + "/animation/combined.timelines" if lg.endswith(".json") else None
        for j in js:
            if j.endswith(".skeleton") and "/skeleton/" in j:      # context.timeline_path: the skeleton's folder's timeline
                tl = j.rsplit("/skeleton/", 1)[0] + "/animation/combined.timelines"
                if tl in nset and own_tl not in nset:
                    parents[tl].add(lg)
            elif j != lg and j in nset:
                parents[j].add(lg)
        for p in _parent_of_timeline(lg, nset):
            parents[lg].add(p)
        if progress and i % 1000 == 0:
            progress("%d/%d definitions" % (i, len(names)))
    roots = Roots(read, names, tables, exe_s)

    memo: dict[str, list] = {}

    def chains(lg: str, seen=()) -> list[list]:
        """Paths [definition, parent, ..., 'root text'] from `lg` up to roots (at most max_chains)."""
        if lg in memo:
            return memo[lg]
        out = [[lg, r.text()] for r in roots.of(lg)]
        for p in sorted(parents.get(lg, ())):
            if p in seen or len(out) >= max_chains:
                continue
            for c in chains(p, seen + (lg,)):
                out.append([lg] + c)
                if len(out) >= max_chains:
                    break
        if not seen:
            memo[lg] = out
        return out

    every = sorted(set(corpus) if corpus is not None else set(refs))
    exe_paths = {logical(s) for s in exe_s if s.endswith(".particles")}
    by_base = collections.defaultdict(list)
    for p in set(every) | set(refs):
        by_base[p.rsplit("/", 1)[-1]].append(p)
    raw_by = collections.defaultdict(list)
    for h in raw or []:
        raw_by[logical(h["target"])].append(h)
    effects = {}
    for p in every:
        rs = refs.get(p, [])
        live = [r for r in rs if r.trigger != "manifest"]
        ch = {}
        for r in live:
            if r.src not in ch:
                ch[r.src] = chains(r.src)
        reach = [r for r in live if ch.get(r.src)]
        if reach:
            status = "bound"
        elif p in exe_paths:
            status = "exe"
        elif live:
            status = "orphan"
        elif rs:
            status = "manifest"
        else:
            status = "unreferenced"
        effects[p] = {"path": p, "status": status,
                      "refs": [{k: v for k, v in asdict(r).items() if v and k != "file"} for r in rs],
                      "chains": {s: c for s, c in ch.items()},
                      "exe": p in exe_paths}
    for p, e in effects.items():
        if e["status"] in UNUSED:
            e["evidence"] = evidence(p, e, effects, by_base, raw_by.get(p, []))
    count = collections.Counter(e["status"] for e in effects.values())
    trig = collections.Counter(r["trigger"] for e in effects.values() for r in e["refs"])
    return {"definitions": len(names), "unparsed": bad, "effects": effects, "status": dict(count),
            "triggers": dict(trig), "raw_scanned": bool(raw)}


def _stem_tokens(p: str) -> list[str]:
    return [t for t in re.split(r"[_\-]+", p.rsplit("/", 1)[-1][:-len(".particles")]) if t]


def _base(toks: list[str]) -> list[str]:
    """Tokens without the trailing size / number ones ('20x20', '01', 'var02', 'height', 'offset' after them)."""
    t = list(toks)
    while len(t) > 1 and (re.search(r"\d", t[-1]) or t[-1] in ("height", "offset", "b", "l", "r")):
        t.pop()
    return t


def evidence(p: str, e: dict, effects: dict, by_base: dict, raw: list) -> list[str]:
    """Why an effect counts as unused, strongest facts first (each line stands alone in the report)."""
    ev = []
    if e["status"] == "orphan":
        srcs = sorted({r["src"] for r in e["refs"] if r["trigger"] != "manifest"})
        ev.append("named only by definitions no root reaches: " + ", ".join(srcs[:4]))
    if e["status"] == "manifest":
        ev.append("named only in preload lists (dependencies): " + ", ".join(sorted({r["src"] for r in e["refs"]})[:4]))
    if p.startswith(AUTHORING):
        ev.append("authoring/test folder (%s)" % next(a for a in AUTHORING if p.startswith(a)))
    for q in by_base.get(p.rsplit("/", 1)[-1], []):
        if q != p and effects.get(q, {}).get("status") in ("bound", "exe"):
            ev.append("copy: the same file name is bound at %s" % q)
    toks = _stem_tokens(p)
    folder = p.rsplit("/", 1)[0]
    parent = folder.rsplit("/", 1)[0]
    for q, f in effects.items():                       # a bound sibling whose name is this one with tokens removed
        if q == p or f["status"] not in ("bound", "exe") or not (q.startswith(folder + "/") or q.startswith(parent + "/")):
            continue
        qt = _stem_tokens(q)
        if len(qt) < len(toks) and qt and all(t in toks for t in qt) and qt[0] == toks[0]:
            ev.append("variant: the bound %s carries this name minus %s" % (
                q, "_".join(t for t in toks if t not in qt)))
            break
    base = _base(toks)
    if base != toks:
        sib = sorted(q for q, f in effects.items() if q != p and f["status"] in ("bound", "exe")
                     and q.rsplit("/", 1)[0] == folder and _base(_stem_tokens(q)) == base)
        if sib:
            ev.append("size/number sibling bound: %s%s" % (sib[0], " +%d" % (len(sib) - 1) if len(sib) > 1 else ""))
    strong = sorted({h["src"] for h in raw if h.get("kind") == "path"})
    other = sorted({"%s names %s" % (h["src"], logical(h["token"])) for h in raw if h.get("kind") == "other-path"})
    weak = sorted({h["src"] for h in raw if h.get("kind") == "stem"})
    if strong:
        ev.append("raw path hits: " + ", ".join(strong[:4]))
    if other:
        ev.append("the same file name at another path is what is named: " + "; ".join(other[:2])
                  + (" +%d" % (len(other) - 2) if len(other) > 2 else ""))
    if weak:
        ev.append("bare-name hits only (same word, no path; not a binding): " + ", ".join(weak[:4])
                  + (" +%d" % (len(weak) - 4) if len(weak) > 4 else ""))
    if not ev or (len(ev) == 1 and ev[0].startswith("bare-name")):
        ev.append("no definition, table or exe string names it")
    return ev


# ---------------------------------------------------------------------------------------------------- queries
class FiredBy:
    """A loaded index: `FiredBy.load(path).of('hd/vfx/particles/...')` (also by bare file name)."""

    def __init__(self, index: dict):
        self.index = index
        self.effects = index["effects"]
        self._base = collections.defaultdict(list)
        for p in self.effects:
            self._base[p.rsplit("/", 1)[-1]].append(p)

    @classmethod
    def load(cls, path) -> "FiredBy":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def of(self, path: str) -> dict | None:
        p = logical(path)
        if p in self.effects:
            return self.effects[p]
        b = p.rsplit("/", 1)[-1]
        b = b if b.endswith(".particles") else b + ".particles"
        hits = self._base.get(b, [])
        return self.effects[hits[0]] if len(hits) == 1 else None

    def tracking_contexts(self, path: str) -> list[dict]:
        """Indexed units and timeline modes that can supply a tracked model."""
        e = self.effects.get(logical(path), {})
        contexts = []
        for ref in e.get("refs", []):
            src = ref["src"]
            for chain in e["chains"].get(src, []):
                if ref["trigger"] == "object" and src.startswith(("hd/character/", "hd/objects/")):
                    context = {"unit": src, "entity": ref.get("entity", "")}
                elif ref["trigger"] == "object" and src.startswith("hd/items/"):
                    wearer = chain[1] if len(chain) > 2 and chain[1].startswith("hd/character/") else src
                    context = {"unit": wearer, "item": src, "entity": ref.get("entity", "")}
                elif (ref["trigger"] == "timeline" and ref.get("comp") == "ObjectEffectSourceAnimationEventParameter"
                      and ref.get("mode")
                      and len(chain) > 2 and chain[1].endswith(".json")):
                    context = {"unit": chain[1], "timeline": ref["mode"], "part": ref.get("bone", "")}
                else:
                    continue
                if context not in contexts:
                    contexts.append(context)
        return contexts

    def summary(self, path: str) -> str:
        """One line for a table cell: 'status: trigger src -> root' (first chain)."""
        e = self.of(path)
        if e is None:
            return "-"
        if e["status"] in UNUSED:
            return "%s: %s" % (e["status"], (e.get("evidence") or ["-"])[0])
        if e["status"] == "exe":
            return "exe: hard-coded path in the executable"
        live = [r for r in e["refs"] if r["trigger"] != "manifest" and e["chains"].get(r["src"])]
        r = live[0]
        return "%s %s -> %s" % (r["trigger"], r["src"], e["chains"][r["src"]][0][-1])


# ---------------------------------------------------------------------------------------------------- report and CLI
def markdown(idx: dict, legacy: set | None = None) -> str:
    eff = idx["effects"]
    st = idx["status"]
    out = ["# hdfx: what fires each effect (reverse binding index)", "",
           "%d effects; %d definitions read (%d unparsed). Status: %s. Refs by trigger: %s." % (
               len(eff), idx["definitions"], len(idx["unparsed"]),
               ", ".join("%s %d" % (k, st.get(k, 0)) for k in ("bound", "exe", "orphan", "manifest", "unreferenced")),
               ", ".join("%s %d" % kv for kv in sorted(idx["triggers"].items(), key=lambda kv: -kv[1]))), ""]
    roots = collections.Counter()
    for e in eff.values():
        if e["status"] == "bound":
            kinds = {ch[-1].split(" ", 1)[0] for c in e["chains"].values() for ch in c}
            roots.update(kinds)
    out += ["Bound effects by root kind (an effect counts once per kind it reaches): " + ", ".join("%s %d" % kv for kv in roots.most_common()), ""]
    groups = collections.OrderedDict()
    for p in sorted(eff):
        e = eff[p]
        if e["status"] not in UNUSED and e["status"] != "exe":
            continue
        groups.setdefault(e["status"], []).append(e)
    for s in ("exe", "orphan", "manifest", "unreferenced"):
        rows = groups.get(s, [])
        if not rows:
            continue
        out += ["## %s (%d)" % (s, len(rows)), "", "| effect | evidence |", "|-|-|"]
        for e in rows:
            mark = "" if legacy is None or e["path"] in legacy else " (new since bind2)"
            ev = "; ".join(e.get("evidence", [])) if s != "exe" else "path string in the executable (hard-coded)"
            out.append("| `%s`%s | %s |" % (e["path"], mark, ev.replace("|", "/")))
        out.append("")
    return "\n".join(out)


def _folder_reader(folder: Path):
    def read(lg: str) -> bytes:
        return (folder / lg).read_bytes()
    names = [p.relative_to(folder).as_posix().lower() for p in folder.rglob("*")
             if p.is_file() and p.suffix.lower() in (".json", ".timelines")]
    # the tree's own case may differ from the logical lower-case: map it
    real = {p.relative_to(folder).as_posix().lower(): p for p in folder.rglob("*") if p.is_file()}

    def read_ci(lg: str) -> bytes:
        p = real.get(lg)
        if p is None:
            raise KeyError(lg)
        return p.read_bytes()
    return read_ci, names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hdkit.fx.firedby", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the index from a data folder (hd/, global/excel/)")
    b.add_argument("data")
    b.add_argument("--corpus", help="root holding hd/vfx (the effects to report on); default: every named effect")
    b.add_argument("--exe", help="the game executable (decrypted dump): its strings mark hard-coded paths")
    b.add_argument("--raw", help="rawhits.jsonl of a raw name scan over other files (evidence only)")
    b.add_argument("--legacy", help="a list (json) of effects an earlier lane called unbound: marks the new ones")
    b.add_argument("--out", required=True)
    s = sub.add_parser("show", help="what fires one effect")
    s.add_argument("index")
    s.add_argument("effect")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        e = FiredBy.load(a.index).of(a.effect)
        print(json.dumps(e, indent=1) if e else "not in the index: %s" % a.effect)
        return 0 if e else 1
    data = Path(a.data)
    read, names = _folder_reader(data)
    tables = Tables.from_dir(data / "global" / "excel")
    corpus = None
    if a.corpus:
        c = Path(a.corpus)
        corpus = [p.relative_to(c).as_posix().lower() for p in c.rglob("*") if p.suffix.lower() == ".particles"]
    raw = [json.loads(ln) for ln in open(a.raw)] if a.raw else None
    exe = Path(a.exe).read_bytes() if a.exe else None
    idx = build(read, names, tables, exe, corpus, raw, progress=lambda m: print(m, file=sys.stderr, flush=True))
    idx["inputs"] = {"data": str(data), "corpus": a.corpus, "exe": a.exe, "raw": a.raw}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "firedby.json").write_text(json.dumps(idx, indent=0), encoding="utf-8")
    legacy = set(json.load(open(a.legacy))) if a.legacy else None
    (out / "FIREDBY.md").write_text(markdown(idx, legacy), encoding="utf-8")
    print("effects %d, status %s; wrote %s" % (len(idx["effects"]), idx["status"], out / "firedby.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
