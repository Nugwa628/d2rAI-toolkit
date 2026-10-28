"""modkit command line: python -m modkit <command> ...   (python -m modkit <command> -h for options)

  fetch [--set S]... [--prefix P]...   copy game files into the local cache d2r-data/<build>/
  new DIR --name NAME                  start a mod project
  build DIR [--no-validate]            pristine game files + DIR/files + DIR/patches -> DIR/out/data
  validate DIR                         check DIR/out/data against D2R's data guide
  install DIR [--game G]               copy DIR/out into <game>/mods/<name> (game must be closed)
  show TABLE KEY [--bank base]         one row's non-empty columns (start a clone from it)
  find TABLE COL=VAL ...               rows matching every condition (key column printed)
  functions [--column srvdofunc]       which vanilla skills use each skill function number
  widgets LAYOUT                       widget names (and rects) of a UI layout file
  png2sprite PNG OUT [--size W H]      PNG -> HD .sprite (+ .lowend)
  sprite2png SPRITE OUT                HD .sprite (version 31) -> PNG, to look at or edit game art
  save-info SAVE [--extended]          stats and class skill points of a .d2s
  save-set SAVE STAT=VALUE... [--skill SLOT=N]... [--extended]   edit a .d2s (backs up the folder first)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from . import images, layouts
from .base import DEFAULT_SETS, SETS, Base
from .tsv import Table

EXAMPLE_PATCH = {
    "ops": [
        {"op": "set", "table": "experience", "key": "MaxLvl", "set": {"Amazon": 99}},
    ]
}


def table(base: Base, name: str, bank: str = "excel") -> Table:
    sub = "base/" if bank == "base" else ""
    return Table.parse(name, base.path(f"data/global/excel/{sub}{name.lower()}.txt").read_text("latin-1"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="modkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", help="D2R folder (default: MODKIT_GAME or the standard install)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch")
    p.add_argument("--set", action="append", choices=sorted(SETS))
    p.add_argument("--prefix", action="append", default=[])
    p = sub.add_parser("new")
    p.add_argument("dir")
    p.add_argument("--name", required=True)
    p = sub.add_parser("build")
    p.add_argument("dir")
    p.add_argument("--no-validate", action="store_true")
    p = sub.add_parser("validate")
    p.add_argument("dir")
    p = sub.add_parser("install")
    p.add_argument("dir")
    p = sub.add_parser("show")
    p.add_argument("table")
    p.add_argument("key")
    p.add_argument("--bank", default="excel", choices=["excel", "base"])
    p = sub.add_parser("find")
    p.add_argument("table")
    p.add_argument("conditions", nargs="+")
    p = sub.add_parser("functions")
    p.add_argument("--column", default="srvdofunc")
    p = sub.add_parser("widgets")
    p.add_argument("layout")
    p = sub.add_parser("png2sprite")
    p.add_argument("png")
    p.add_argument("out")
    p.add_argument("--size", nargs=2, type=int)
    p = sub.add_parser("sprite2png")
    p.add_argument("sprite")
    p.add_argument("out")
    p = sub.add_parser("save-info")
    p.add_argument("save")
    p.add_argument("--extended", action="store_true", help="D2RLoader extended save format (16-bit stat ids)")
    p.add_argument("--project", help="mod project whose built itemstatcost to use")
    p = sub.add_parser("save-set")
    p.add_argument("save")
    p.add_argument("stats", nargs="*", help="STAT=VALUE (stat name or id; life/mana/stamina in whole points)")
    p.add_argument("--skill", action="append", default=[], help="SLOT=POINTS (class skill slot, 0-based)")
    p.add_argument("--extended", action="store_true")
    p.add_argument("--project")
    args = ap.parse_args(argv)

    if args.cmd == "new":
        d = Path(args.dir)
        (d / "patches").mkdir(parents=True, exist_ok=True)
        (d / "assets").mkdir(exist_ok=True)
        (d / "files" / "data").mkdir(parents=True, exist_ok=True)
        mod = {"name": args.name, "savepath": f"{args.name}/", "title": args.name, "version": "0.1.0",
               "extendedSaves": False, "plugins": []}
        (d / "mod.json").write_text(json.dumps(mod, indent=2) + "\n", encoding="utf-8")
        (d / "patches" / "000-example.json").write_text(json.dumps(EXAMPLE_PATCH, indent=2) + "\n", encoding="utf-8")
        (d / ".gitignore").write_text("out/\n", encoding="utf-8")
        print(f"new mod project {args.name} in {d}")
        return 0

    base = Base(args.game)
    if args.cmd == "fetch":
        prefixes = [p for s in (args.set or DEFAULT_SETS) for p in SETS[s]] + args.prefix
        print(f"D2R build {base.build}: {base.fetch(prefixes)} files in {base.root}")
    elif args.cmd == "build":
        from .project import Build, BuildError
        b = Build(Path(args.dir), base)
        try:
            b.run()
        except BuildError as e:
            print(f"BUILD FAILED: {e}")
            return 1
        for line in b.log:
            print("  " + line)
        for w in b.warnings:
            print("  warning: " + w)
        n = sum(1 for p in b.out.rglob("*") if p.is_file())
        print(f"built {n} files -> {b.out}")
        if not args.no_validate:
            return _validate(base, Path(args.dir))
    elif args.cmd == "validate":
        return _validate(base, Path(args.dir))
    elif args.cmd == "install":
        from .install import install
        print(install(Path(args.dir), base.game))
    elif args.cmd == "show":
        t = table(base, args.table, args.bank)
        r = t.need(args.key)
        print(f"{t.name} '{r[0]}' (ID {t.record_id(r)})")
        for c, v in t.as_dict(r).items():
            print(f"  {c} = {v}")
    elif args.cmd == "find":
        t = table(base, args.table)
        conds = dict(c.split("=", 1) for c in args.conditions)
        for r in t.where(**conds):
            print(f"  {t.record_id(r)}: {r[0]}")
    elif args.cmd == "functions":
        t = table(base, "skills")
        uses = defaultdict(list)
        for r in t.records():
            v = t.get(r, args.column)
            if v:
                uses[int(v)].append(r[0])
        print(f"skills.txt {args.column}: number -> vanilla skills using it")
        for k in sorted(uses):
            print(f"  {k:4}: {', '.join(uses[k][:8])}" + (f" (+{len(uses[k]) - 8})" if len(uses[k]) > 8 else ""))
    elif args.cmd == "widgets":
        rel = args.layout if args.layout.startswith("data/") else f"data/global/ui/layouts/{args.layout}"
        text = base.text(rel)
        seen: dict[str, int] = defaultdict(int)
        for name in layouts.widget_names(text):
            occ = seen[name]
            seen[name] += 1
            try:
                rect = layouts.get_field(text, name, "rect", occ)
            except KeyError:
                rect = None
            print(f"  {name}" + (f" [{occ}]" if occ else "") + (f"  rect {rect}" if rect is not None else ""))
    elif args.cmd == "png2sprite":
        img = images.read_png(args.png)
        if args.size:
            img = images.scale(img, *args.size)
        images.write_sprite(args.out, img)
        print(f"{args.out}: {img[0]} x {img[1]}")
    elif args.cmd == "sprite2png":
        img, fw, frames = images.read_sprite(args.sprite)
        images.write_png(args.out, img)
        print(f"{args.out}: {img[0]} x {img[1]}, {frames} frame(s) of {fw} px")
    elif args.cmd in ("save-info", "save-set"):
        return _saves(base, args)
    return 0


def _validate(base: Base, project: Path) -> int:
    from . import validate
    # the validator compares against every pristine table (mods ship only what they change) and the data guide
    for rel, prefix in (("data/global/dataguide/data/files/enums.js", "data/global/dataguide/"),
                        ("data/global/excel/base/skillcalc.txt", "data/global/excel/"),
                        ("data/local/lng/strings/ui.json", "data/local/lng/strings/")):
        if not (base.root / rel).exists():
            print(f"  fetching {prefix} (first validation only)")
            base.fetch([prefix])
    out = project / "out" / "data"
    report = validate.validate(out, base.root / "data/global/dataguide/data/files",
                               [out / "local/lng/strings", base.root / "data/local/lng/strings"],
                               base_data_dir=base.root / "data",
                               allow_extended_saves=json.loads((project / "mod.json").read_text("utf-8")).get(
                                   "extendedSaves", False))
    (project / "out" / "validation.md").write_text(report.to_markdown(), encoding="utf-8")
    print(f"validation: {len(report.errors)} errors, {len(report.plugin)} need a plugin, "
          f"{len(report.warnings)} warnings -> {project / 'out' / 'validation.md'}")
    for f in report.errors[:15]:
        print(f"  ERROR {f.table} {f.row} {f.column}: {f.message}")
    return 1 if report.errors else 0


def _saves(base: Base, args) -> int:
    from . import saves
    if args.project:
        src = Path(args.project) / "out/data/global/excel/itemstatcost.txt"
        isc = Table.parse("itemstatcost", src.read_text("latin-1")) if src.exists() else table(base, "itemstatcost")
    else:
        isc = table(base, "itemstatcost")
    names = {r[0].lower(): isc.record_id(r) for r in isc.records()}
    ids = {v: k for k, v in names.items()}
    s = saves.Save(args.save, saves.stat_bits(isc), 16 if args.extended else 9)
    if args.cmd == "save-info":
        for sid, v in sorted(s.stats.items()):
            shown = v >> 8 if sid in (6, 7, 8, 9, 10, 11) else v
            print(f"  {sid:3} {ids.get(sid, '?'):20} {shown}")
        print("  class skill points: " + " ".join(str(b) for b in s.class_skills()))
        return 0
    dest = saves.backup(Path(args.save).parent, label=Path(args.save).stem)
    print(f"backup: {dest}")
    for item in args.stats:
        k, v = item.split("=", 1)
        sid = int(k) if k.isdigit() else names[k.lower()]
        val = int(v) << 8 if sid in (6, 7, 8, 9, 10, 11) else int(v)
        s.stats[sid] = val
    for item in args.skill:
        slot, pts = item.split("=")
        s.set_class_skill(int(slot), int(pts))
    s.save()
    print(f"saved {args.save} (checksum and .d2rl updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
