"""The art store for the monster3d scripts: find, fetch and check files moved out of EV (hdkit.store.Manifest).

  python -m hdkit.monster3d.store where <logical path> [...]        where each file is now (local copy or store object)
  python -m hdkit.monster3d.store list <pattern> [...]              manifest paths that match (fnmatch, / separators)
  python -m hdkit.monster3d.store fetch <dest dir> <pattern> [...]  copy matching files to <dest>/<logical path>, hash checked
  python -m hdkit.monster3d.store fetch --in-place <pattern> [...]  the same into EV itself (puts files back where they were)
  python -m hdkit.monster3d.store tools                             the tool trees of the manifest and where they resolve
  python -m hdkit.monster3d.store check [<pattern>]                 re-hash the store objects of matching entries
  python -m hdkit.monster3d.store put [--min-kib N] [--delete] [--dry] <file or dir under EV> [...]
                                    copy local files (dirs: every file of at least N KiB, default 256) into the store
                                    and add them to EV/ART-MANIFEST.json; with --delete the local file goes only after
                                    its object re-hashes equal.

A pattern is a logical path (the file's path relative to EV, e.g. mymonster/stages/5-build/albedo_final.png) or an
fnmatch pattern on it. A file name list can be given as @list.txt (one per line). The store is HDKIT_STORE or
[paths] art_store in hdkit.toml; HDKIT_M3D_DIR moves EV. Standard library only.
"""
import sys
from pathlib import Path

from hdkit import store as hstore
from hdkit.monster3d import common


def patterns(args):
    out = []
    for a in args:
        if a.startswith("@"):
            out += [line.strip().replace("\\", "/") for line in open(a[1:], encoding="utf-8") if line.strip()]
        else:
            out.append(a.replace("\\", "/"))
    return out


def manifest() -> hstore.Manifest:
    return hstore.Manifest(common.EV, common.MANIFEST, copy_as="monster3d")


def put(m, args):
    min_kib, delete, dry, roots = 256.0, False, False, []
    i = 0
    while i < len(args):
        if args[i] == "--min-kib":
            min_kib = float(args[i + 1])
            i += 2
            continue
        if args[i] == "--delete":
            delete = True
        elif args[i] == "--dry":
            dry = True
        else:
            roots.append(Path(args[i]).resolve())
        i += 1
    files = []
    for r in roots:
        if r.is_dir():
            files += sorted(q for q in r.rglob("*") if q.is_file() and q.stat().st_size >= min_kib * 1024)
        elif r.is_file():
            files.append(r)
        else:
            raise SystemExit("not found: %s" % r)
    r = m.put_files(files, delete=delete, dry=dry)
    print("%s %d files (%.1f MiB): %d objects copied, %d new manifest entries, %d local files deleted"
          % ("would put" if dry else "put", r["files"], r["bytes"] / 2**20, r["copied"], r["new"], r["deleted"]))


def main(a):
    if not a:
        raise SystemExit(__doc__)
    m = manifest()
    cmd, rest = a[0], a[1:]
    if cmd == "where":
        for p in patterns(rest):
            print(m.find(p))
    elif cmd == "list":
        for f in m.select(patterns(rest)):
            print("%s  %d  %s" % (f["sha256"][:12], f["size"], f["path"]))
    elif cmd == "fetch":
        dest, pats = (common.EV, rest[1:]) if rest and rest[0] == "--in-place" else (Path(rest[0]), rest[1:])
        n, skipped = m.fetch(dest, m.select(patterns(pats)))
        print("fetched %d files to %s (%d already there)" % (n, dest, skipped))
    elif cmd == "tools":
        for old in sorted(m.data()["toolchain"]):
            print("%-22s %s" % (old, m.tool(old)))
        print("BLENDER", common.BLENDER, common.BLENDER.is_file())
        print("ESRGAN ", common.ESRGAN, common.ESRGAN.is_file())
    elif cmd == "put":
        put(m, rest)
    elif cmd == "check":
        ents = m.select(patterns(rest)) if rest else None
        bad = m.check(ents)
        n = len({f["sha256"] for f in (ents if ents is not None else m.data()["files"])})
        print("checked %d objects, %d bad" % (n, len(bad)))
        for b in bad:
            print("  BAD", b)
        sys.exit(1 if bad else 0)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
