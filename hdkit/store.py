"""A content-addressed store for large outputs: `objects/sha256/<2 hex>/<sha256>`, written through `tmp/`, hash checked.

The layout of the art store README (one file per content hash, no extension, never changed once written; a copy goes to
`tmp/` first, its hash is checked, then it moves into `objects/`). The store folder is `HDKIT_STORE`, else `store_root()`
of the optional site adapter `hdkit_site`, else none (outputs then stay where they were written).

  put(path)               move one file into the store; returns {sha256, size, object}
  Manifest(base, path)    logical paths of files moved out of a work folder: find, fetch, put, check (the monster3d
                          scripts use it through hdkit/monster3d/store.py)
  offload(paths, min_bytes, manifest)   every listed file of at least min_bytes goes in; a manifest JSON (logical name,
                          sha256, size, object, source) is written next to the outputs and copied to
                          `manifests/hdkit-view/` in the store
"""
import hashlib
import importlib
import json
import os
import shutil
import time
from pathlib import Path


def root() -> Path | None:
    env = os.environ.get("HDKIT_STORE")
    if env:
        return Path(env)
    try:
        from hdkit import site as _site
        site = _site.adapter()
    except ImportError:
        return None
    fn = getattr(site, "store_root", None)
    r = fn() if fn else None
    return Path(r) if r else None


def sha256(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""):
            h.update(b)
    return h.hexdigest()


def put(path, store=None, keep: bool = False) -> dict:
    store = Path(store) if store else root()
    if store is None:
        raise RuntimeError("no store: set HDKIT_STORE")
    p = Path(path)
    dg = sha256(p)
    obj = store / "objects" / "sha256" / dg[:2] / dg
    if not (obj.exists() and obj.stat().st_size == p.stat().st_size and sha256(obj) == dg):
        tmp = store / "tmp" / ("%s.%d.part" % (dg, os.getpid()))
        tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, tmp)
        if sha256(tmp) != dg:
            tmp.unlink()
            raise RuntimeError("store copy of %s does not hash equal" % p)
        obj.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, obj)
    size = p.stat().st_size
    if not keep:
        p.unlink()
    return {"sha256": dg, "size": size, "object": str(obj)}


def offload(paths, min_bytes: int, manifest, source: dict | None = None, store=None) -> dict:
    """Move each existing file of at least min_bytes into the store. Returns {local path: entry}; writes `manifest`."""
    store = Path(store) if store else root()
    moved = {}
    if store is None:
        return moved
    for p in paths:
        p = Path(p)
        if p.is_file() and p.stat().st_size >= min_bytes:
            e = put(p, store)
            e["path"] = p.name
            moved[str(p)] = e
    if moved:
        man = {"tool": "hdkit.view", "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source or {},
               "files": list(moved.values())}
        mp = Path(manifest)
        mp.write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
        cp = store / "manifests" / "hdkit-view" / ("%s_%s.json" % (mp.parent.name, mp.stem))
        cp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(mp, cp)
    return moved


# ---- a manifest of moved files (logical path -> object) -----------------------------------------------------------------

class Manifest:
    """A JSON manifest that maps each moved file's logical path (its old path relative to `base`) to its content hash,
    and each moved tool or weights folder (`toolchain`) to a tree in the store. A copy goes to
    `manifests/<copy_as>/` in the store on every save.

      {"files": [{"path": "<logical>", "sha256": "...", "size": N}, ...], "toolchain": {"<old dir>": {"tree": "..."}}}
    """

    def __init__(self, base, path=None, store=None, copy_as: str = "monster3d"):
        self.base = Path(base)
        self.path = Path(path) if path else self.base / "ART-MANIFEST.json"
        self._store = Path(store) if store else None
        self.copy_as = copy_as
        self._data = None

    @property
    def store(self) -> Path | None:
        return self._store or root()

    def need_store(self) -> Path:
        s = self.store
        if s is None:
            raise SystemExit("no art store set: HDKIT_STORE or [paths] art_store in hdkit.toml")
        return s

    def data(self) -> dict:
        if self._data is None:
            d = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {"files": [], "toolchain": {}}
            d.setdefault("files", [])
            d.setdefault("toolchain", {})
            d["by_path"] = {f["path"]: f for f in d["files"]}
            self._data = d
        return self._data

    def object_path(self, digest: str) -> Path:
        return self.need_store() / "objects" / "sha256" / digest[:2] / digest

    def find(self, logical) -> Path:
        """The local file when it is there, else its store object. FileNotFoundError when neither has it."""
        p = Path(logical)
        if p.is_absolute():
            try:
                p = p.relative_to(self.base)
            except ValueError:
                return p
        local = self.base / p
        if local.is_file():
            return local
        entry = self.data()["by_path"].get(p.as_posix())
        if entry and self.store is not None and self.object_path(entry["sha256"]).is_file():
            return self.object_path(entry["sha256"])
        raise FileNotFoundError("%s: not under %s and not in the manifest %s" % (p.as_posix(), self.base, self.path))

    def tool(self, old: str) -> Path:
        """A tool or weights folder by its old place under base: the local folder when one is there, else its tree."""
        local = self.base / old
        if local.is_dir() and any(local.iterdir()):
            return local
        tree = self.data()["toolchain"].get(old)
        return self.store / tree["tree"] if tree and self.store is not None else local

    def select(self, pats) -> list:
        import fnmatch
        files = self.data()["files"]
        hit = []
        for pat in pats:
            pat = pat.replace("\\", "/")
            if any(c in pat for c in "*?["):
                got = [f for f in files if fnmatch.fnmatchcase(f["path"], pat)]
            else:
                got = [f for f in files if f["path"] == pat or f["path"].startswith(pat.rstrip("/") + "/")]
            if not got:
                raise SystemExit("no manifest entry matches %s" % pat)
            hit += got
        seen = set()
        return [f for f in hit if not (f["path"] in seen or seen.add(f["path"]))]

    def fetch(self, dest, entries) -> tuple[int, int]:
        """Copy each entry's object to dest/<logical path>, hash checked. Returns (fetched, already there)."""
        dest = Path(dest)
        n = skipped = 0
        for f in entries:
            out = dest / f["path"]
            if out.is_file() and out.stat().st_size == f["size"] and sha256(out) == f["sha256"]:
                skipped += 1
                continue
            src = self.object_path(f["sha256"])
            if not src.is_file():
                raise SystemExit("store object missing for %s: %s" % (f["path"], src))
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".fetch")
            shutil.copyfile(src, tmp)
            if sha256(tmp) != f["sha256"]:
                tmp.unlink()
                raise SystemExit("hash mismatch fetching %s" % f["path"])
            os.replace(tmp, out)
            n += 1
        return n, skipped

    def save(self):
        d = self.data()
        out = {k: v for k, v in d.items() if k != "by_path"}
        out["files"] = sorted(out["files"], key=lambda f: f["path"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(out, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, self.path)
        cp = self.need_store() / "manifests" / self.copy_as / self.path.name
        cp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path, cp)

    def put_files(self, files, delete: bool = False, dry: bool = False, log=print) -> dict:
        """Copy local files under base into the store and add them to the manifest; with delete the local file goes only
        after its object re-hashes equal."""
        base = self.base.resolve()
        d = self.data()
        new = copied = deleted = total = 0
        for q in files:
            q = Path(q).resolve()
            try:
                logical = q.relative_to(base).as_posix()
            except ValueError:
                raise SystemExit("%s is not under %s" % (q, base))
            if q.name.endswith((".fetch", ".tmp", ".put")):
                continue
            size = q.stat().st_size
            total += size
            if dry:
                log("would put %s (%d)" % (logical, size))
                continue
            before = self.object_path(sha256(q)).is_file()
            e = put(q, self.need_store(), keep=True)
            copied += not before
            ent = {"path": logical, "sha256": e["sha256"], "size": size}
            old = d["by_path"].get(logical)
            if old is None:
                d["files"].append(ent)
                d["by_path"][logical] = ent
                new += 1
            elif old["sha256"] != e["sha256"]:
                old.update(ent)
            self.save()
            if delete:
                obj = self.object_path(e["sha256"])
                if obj.stat().st_size == size and sha256(obj) == e["sha256"]:
                    q.unlink()
                    deleted += 1
                else:
                    raise SystemExit("store object of %s did not verify; local file kept" % logical)
        return {"files": len(files), "bytes": total, "copied": copied, "new": new, "deleted": deleted}

    def check(self, entries=None) -> list:
        """Re-hash the objects of the entries (default all); returns the logical paths whose object is missing or bad."""
        ents = self.data()["files"] if entries is None else entries
        uniq = {f["sha256"]: f for f in ents}.values()
        return [f["path"] for f in uniq
                if not self.object_path(f["sha256"]).is_file() or sha256(self.object_path(f["sha256"])) != f["sha256"]]
