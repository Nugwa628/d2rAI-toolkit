#!/usr/bin/env python3
"""The server half of a release (docs/release.md, "The download host"). It runs on the download host under the upload
account (modrelease), triggered by a systemd path unit on <root>/incoming. Requirements: python3 3.9+ (standard library)
and 7-Zip (`7zz` from the Debian package 7zip, or `7z`). It is a single file; copy just this file to the server.

The publisher (publish_release.py) fills <root>/incoming/<version>.uploading/ and, as its final step, renames it to
<root>/incoming/<version>/. A finished upload contains:

  READY                        JSON checklist: version, channel, files {name: {size, sha256}}, objectsPack, launcher, force
  release-manifest.json        the release manifest (schemaVersion 2), still without "archive" (built here)
  objects-<sha16>.7z           the objects the host did not have yet, each entry named by its sha256 (may be absent)
  RELEASE_NOTES-<v>.md         optional
  <downloader>.exe             optional; READY's "launcher" field names it

Each finished upload is handled in version order:

  1. Verify READY: every listed file has the stated size and sha256, and nothing unlisted is present.
  2. Verify the manifest: schemaVersion 2, matching version, safe relative paths, consistent sizes and hashes.
  3. List the objects 7z before extracting (plain files only, each named by a sha256 the manifest needs), extract it to
     incoming/<v>/.work/, re-hash every file, and move the good ones to <site>/objects/<sha[0:2]>/.
  4. Make sure every object the manifest needs is present, re-hash each one while hard-linking the mod folder together,
     and pack that folder as releases/<v>/<Folder>-<v>.7z (solid LZMA2, -mx7, no time stamps). The large archive is
     produced here, so it never travels over the publisher's upload link. The manifest gains its "archive" entry.
  5. Write releases/<v>/ (7z, notes, manifest, SHA256SUMS) and downloader/<version>/ (exe and .sha256).
  6. Write channels/<channel>.json as the very last file, if READY names a channel.
  7. Remove the upload and the scratch folder, plus *.part-* temp files under the site that are older than an hour.
  8. If something was published, touch <site>/.sync/wanted so the bucket mirror (dl_sync.py) runs. This script itself
     needs no network access; without a mirror the file is simply ignored.

All writes go to a temporary name first and are renamed into place. When a check fails, the reason is stored in
incoming/<v>/REFUSED, the folder is kept for inspection, later runs skip it, and the exit status is 1. `--retry <v>`
deletes REFUSED and tries again. Re-running is harmless: an identical published manifest is left alone, and a different
one is refused unless READY sets force (published versions are immutable). Nothing outside <root> is touched.

  python3 -I /opt/mod-release/dl_ingest.py --root /srv/mod-files --site mymod [--channels stable,test] [--7z PATH] [--retry VERSION]
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

MANIFEST_SCHEMA = 2
READY_SCHEMA = 1
CHANNEL_SCHEMA = 1
CHANNELS = ("stable", "test")  # the default for --channels
OBJECTS_BASE = "objects/"
LAUNCHER_DIR = "downloader"  # <site>/downloader/<v>/<exe>
MANIFEST_NAME = "release-manifest.json"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PACK_RE = re.compile(r"^objects-[0-9a-f]{16}\.7z$")
NOTES_RE = re.compile(r"^RELEASE_NOTES-[0-9A-Za-z.+-]+\.md$")
EXE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.exe$")  # the downloader: any plain .exe name
PART_RE = re.compile(r"\.part-[0-9a-z]+$")
SYNC_DIR = ".sync"  # in the site, not served: `wanted` starts dl_sync.py (the bucket mirror)
WORK = ".work"  # the ingest's scratch folder inside incoming/<v>/
PART_AGE = 3600  # a temp file younger than this may belong to an upload that is still running
# the player 7z: the same switches as package_release.py
SEVENZ_ARGS = ["a", "-t7z", "-m0=lzma2", "-mx=7", "-ms=on", "-mmt=on", "-mtm=off", "-mtc=off", "-mta=off", "-bd", "-y"]
SEVENZ_NAMES = ("7zz", "7z", "7za")
JOURNAL = bool(os.environ.get("JOURNAL_STREAM"))  # systemd: "<3>" in front of a line makes it an error in the journal


class Refused(Exception):
    pass


def log(msg, error=False):
    print(("<3>" if error and JOURNAL else "") + msg, file=sys.stderr if error and not JOURNAL else sys.stdout, flush=True)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dumps(obj) -> bytes:
    return (json.dumps(obj, indent=1) + "\n").encode("utf-8")


def version_key(v: str):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$", v)
    nums = tuple(int(x) for x in m.groups()[:3])
    return nums + (0, m.group(4)) if m.group(4) else nums + (1, "")


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_7z(given=None):
    if given:
        return given
    for name in SEVENZ_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for var in ("ProgramFiles", "ProgramFiles(x86)"):  # a test run on Windows
        base = os.environ.get(var)
        if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            return str(Path(base) / "7-Zip" / "7z.exe")
    return None


def safe_rel(path: str) -> bool:
    """A '/'-separated relative path that cannot leave its folder (the downloader checks the same way, roughly)."""
    if not isinstance(path, str) or not path or len(path) > 400 or path.startswith("/") or path.endswith("/"):
        return False
    for seg in path.split("/"):
        if seg in ("", ".", "..") or seg[-1] in ". " or any(ord(c) < 0x20 or c in '\\:<>"|?*' for c in seg):
            return False
    return True


def write_atomic(dest: Path, data: bytes = None, src: Path = None, move=False):
    """Write `data` (or copy / move `src`) to dest through <dest>.part-<pid> and a rename: readers never see half a file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.part-{os.getpid():x}")
    if data is not None:
        tmp.write_bytes(data)
    elif move:
        os.replace(src, tmp)
    else:
        shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o644)
    os.replace(tmp, dest)


def strip(man: dict) -> dict:
    """What makes two manifests of one version the same release: everything but the packaging date and the server's 7z."""
    return {k: v for k, v in man.items() if k not in ("released", "archive")}


# ------------------------------------------------------------------------------------------------------------- checks
def check_ready(inc: Path, version: str, channels=CHANNELS) -> dict:
    try:
        ready = json.loads((inc / "READY").read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"READY is not readable JSON: {e}")
    if ready.get("schemaVersion") != READY_SCHEMA:
        raise Refused(f"READY schemaVersion {ready.get('schemaVersion')} (this ingest knows {READY_SCHEMA}): update dl_ingest.py")
    if ready.get("version") != version:
        raise Refused(f"READY is for version {ready.get('version')!r}, the folder is {version}")
    if ready.get("channel") not in (None, *channels):
        raise Refused(f"unknown channel {ready.get('channel')!r}")
    files = ready.get("files")
    if not isinstance(files, dict) or MANIFEST_NAME not in files:
        raise Refused(f"READY lists no {MANIFEST_NAME}")
    pack, launcher = ready.get("objectsPack"), ready.get("launcher")
    for name, meta in files.items():
        ok = name == MANIFEST_NAME or NOTES_RE.match(name) or PACK_RE.match(name) or EXE_RE.match(name)
        if not ok or not isinstance(meta, dict) or not SHA_RE.match(str(meta.get("sha256"))) or not isinstance(meta.get("size"), int):
            raise Refused(f"READY lists a file it may not: {name!r}")
    if pack is not None and (pack not in files or not PACK_RE.match(pack)):
        raise Refused(f"READY names objectsPack {pack!r}, which it does not list")
    uploaded = [n for n in files if EXE_RE.match(n)]
    if (launcher is not None and not EXE_RE.match(str(launcher))) or uploaded != ([launcher] if launcher else []):
        raise Refused("READY's launcher entry and its file list disagree")
    if any(PACK_RE.match(n) for n in files if n != pack):
        raise Refused("READY lists an objects 7z that is not its objectsPack")
    present = {p.name for p in inc.iterdir()} - {"READY", WORK}
    extra = sorted(present - set(files))
    if extra:
        raise Refused(f"files that READY does not list: {', '.join(extra[:5])}")
    for name, meta in sorted(files.items()):
        p = inc / name
        if name not in present or p.is_symlink() or not p.is_file():
            raise Refused(f"{name} is missing (or not a plain file)")
        if p.stat().st_size != meta["size"] or sha256_file(p) != meta["sha256"]:
            raise Refused(f"{name} does not match READY (size or sha256): the upload is damaged; publish it again")
    return ready


def check_manifest(raw: bytes, version: str, ready: dict) -> dict:
    try:
        man = json.loads(raw)
    except ValueError as e:
        raise Refused(f"{MANIFEST_NAME} is not JSON: {e}")
    if man.get("schemaVersion") != MANIFEST_SCHEMA:
        raise Refused(f"manifest schemaVersion {man.get('schemaVersion')} (this ingest knows {MANIFEST_SCHEMA})")
    if man.get("version") != version:
        raise Refused(f"the manifest is version {man.get('version')!r}, the folder is {version}")
    if man.get("objectsBase") != OBJECTS_BASE:
        raise Refused(f"objectsBase must be {OBJECTS_BASE!r}")
    mod = man.get("modFolder")
    if not safe_rel(mod) or "/" in mod:
        raise Refused(f"bad modFolder {mod!r}")
    if ready.get("channel") == "stable" and not man.get("complete", False):
        raise Refused("the manifest says complete=false: not for the stable channel")
    files = man.get("files")
    if not isinstance(files, list) or not files:
        raise Refused("the manifest lists no files")
    seen, sizes, total = set(), {}, 0
    for f in files:
        path, sha, size = f.get("path"), f.get("sha256"), f.get("size")
        if not safe_rel(path) or not SHA_RE.match(str(sha)) or not isinstance(size, int) or size < 0:
            raise Refused(f"bad manifest entry {f!r:.200}")
        if path.lower() in seen:
            raise Refused(f"duplicate path {path}")
        seen.add(path.lower())
        if sizes.setdefault(sha, size) != size:
            raise Refused(f"{path}: the same sha256 with two sizes")
        total += size
    if man.get("fileCount", len(files)) != len(files) or man.get("totalSize", total) != total:
        raise Refused("fileCount / totalSize do not match the file list")
    for d in man.get("emptyDirs", []):
        if not safe_rel(d):
            raise Refused(f"bad emptyDirs entry {d!r}")
    lr = man.get("launcher")
    if ready.get("launcher"):
        if not isinstance(lr, dict) or not VERSION_RE.match(str(lr.get("version"))) or \
                lr.get("path") != f"{LAUNCHER_DIR}/{lr.get('version')}/{ready['launcher']}":
            raise Refused("the manifest's launcher entry is missing or malformed")
        meta = ready["files"][ready["launcher"]]
        if lr.get("sha256") != meta["sha256"] or lr.get("size") != meta["size"]:
            raise Refused("the uploaded launcher does not match the manifest's launcher entry")
    elif lr is not None:
        raise Refused("the manifest names a launcher, but none was uploaded")
    return man


def list_pack(sevenzip, pack: Path, wanted: dict) -> dict:
    """`7z l -slt`: every entry must be a plain file named by a sha256 the manifest uses, with that object's size.
    -> {sha: size}. Checked before anything is unpacked (no folders, links or paths in it)."""
    r = subprocess.run([sevenzip, "l", "-slt", "-bd", str(pack)], capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0:
        raise Refused(f"7-Zip cannot list {pack.name} ({r.returncode}): {(r.stdout + r.stderr).strip()[-300:]}")
    body = r.stdout.split("\n----------\n", 1)
    if len(body) != 2:
        body = r.stdout.split("----------\n", 1)
    if len(body) != 2:
        raise Refused(f"unexpected 7-Zip listing of {pack.name}")
    entries, cur = {}, {}
    for ln in body[1].splitlines() + [""]:
        if not ln.strip():
            if cur:
                path = cur.get("Path", "")
                if not SHA_RE.match(path) or cur.get("Folder", "-") != "-" or re.search(r"\bl[-r][-w][-xsS]", cur.get("Attributes", "")):
                    raise Refused(f"{pack.name} holds {path!r}, which is not an object (a plain file named by its sha256)")
                if path not in wanted:
                    raise Refused(f"{pack.name} holds {path}, which the manifest does not use")
                if int(cur.get("Size", "-1") or -1) != wanted[path] or path in entries:
                    raise Refused(f"{pack.name}: entry {path} has the wrong size (or is there twice)")
                entries[path] = wanted[path]
                cur = {}
            continue
        k, sep, v = ln.partition(" = ")
        if sep:
            cur[k.strip()] = v
    return entries


# ------------------------------------------------------------------------------------------------------------- ingest
class Ingest:
    def __init__(self, root: Path, site_name="site", sevenzip=None, channels=CHANNELS):
        self.root = Path(root).resolve()
        self.site = self.root / site_name
        self.incoming = self.root / "incoming"
        self.sevenzip = sevenzip
        self.channels = tuple(channels)
        for p in (self.site, self.incoming):
            if p.is_symlink() or not p.is_dir() or p.resolve().parent != self.root:
                raise SystemExit(f"{p} must be a real folder directly in {self.root}")

    def obj(self, sha) -> Path:
        return self.site / "objects" / sha[:2] / sha

    def pending(self):
        out = []
        for p in self.incoming.iterdir():
            # <v>.uploading is still being written (and "1.0.0-rc1.uploading" would pass VERSION_RE)
            if VERSION_RE.match(p.name) and not p.name.endswith(".uploading") and p.is_dir() and not p.is_symlink() \
                    and (p / "READY").is_file() and not (p / "REFUSED").exists():
                out.append(p.name)
        return sorted(out, key=version_key)

    def run(self) -> int:
        failed, done, published = 0, set(), []
        while True:  # again after each pass: an upload may have finished while the last one ran
            todo = [v for v in self.pending() if v not in done]
            if not todo:
                break
            for v in todo:
                done.add(v)
                try:
                    self.ingest(v)
                    published.append(v)
                except Refused as e:
                    failed += 1
                    log(f"{v}: REFUSED: {e}", error=True)
                    try:
                        (self.incoming / v / "REFUSED").write_text(f"{now_utc()} {e}\n", encoding="utf-8")
                    except OSError:
                        pass
                    shutil.rmtree(self.incoming / v / WORK, ignore_errors=True)
        self.clean_parts()
        if published:
            self.request_sync(f"ingest {', '.join(published)}")
        return 1 if failed else 0

    def request_sync(self, reason):
        """<site>/.sync/wanted: the trigger of mod-release-sync.path (the bucket mirror, dl_sync.py)."""
        try:
            write_atomic(self.site / SYNC_DIR / "wanted", data=f"{now_utc()} {reason}\n".encode())
        except OSError as e:
            log(f"cannot write {SYNC_DIR}/wanted ({e}); the mirror's timer picks the release up", error=True)

    def ingest(self, v: str):
        inc = self.incoming / v
        log(f"{v}: checking the upload in {inc}")
        if not self.sevenzip:
            raise Refused("no 7-Zip on the server (apt install 7zip)")
        ready = check_ready(inc, v, self.channels)
        man_raw = (inc / MANIFEST_NAME).read_bytes()
        man = check_manifest(man_raw, v, ready)
        sizes = {f["sha256"]: f["size"] for f in man["files"]}
        rel_dir = self.site / "releases" / v
        published = rel_dir / MANIFEST_NAME
        old = None
        if published.is_file():
            old = json.loads(published.read_bytes())
            if strip(old) != strip(man) and not ready.get("force"):
                raise Refused(f"releases/{v} is already published with other contents; a published version never changes "
                              "(publish a new version, or publish --force)")
        work = inc / WORK  # inside the upload: the path unit watches incoming/ only, so this is no new trigger
        if work.is_symlink() or work.is_file():  # an upload cannot plant a link here to make the ingest write elsewhere
            work.unlink()
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()

        pack = ready.get("objectsPack")
        if pack:
            entries = list_pack(self.sevenzip, inc / pack, sizes)
            unpacked = work / "objects"
            r = subprocess.run([self.sevenzip, "x", "-y", "-bd", f"-o{unpacked}", str(inc / pack)], capture_output=True,
                               text=True, errors="replace")
            if r.returncode != 0:
                raise Refused(f"7-Zip cannot unpack {pack} ({r.returncode}): {(r.stdout + r.stderr).strip()[-300:]}")
            found = {}
            for dirpath, dirnames, filenames in os.walk(unpacked):
                if dirnames or Path(dirpath) != unpacked:
                    raise Refused(f"{pack} unpacked into folders")
                for n in filenames:
                    p = unpacked / n
                    if p.is_symlink() or not p.is_file() or n not in entries:
                        raise Refused(f"{pack} unpacked {n!r}, which its listing did not show")
                    got = sha256_file(p)
                    if got != n or p.stat().st_size != sizes[n]:
                        raise Refused(f"{pack}: {n} has sha256 {got}: the object is damaged; publish it again")
                    found[n] = p
            if set(found) != set(entries):
                raise Refused(f"{pack}: {len(set(entries) - set(found))} listed objects were not unpacked")
            moved = 0
            for sha, p in sorted(found.items()):
                dest = self.obj(sha)
                if dest.is_file() and dest.stat().st_size == sizes[sha]:
                    continue
                write_atomic(dest, src=p, move=True)
                moved += 1
            log(f"{v}: {len(found)} objects in {pack}, {moved} new on the host")

        missing = [s for s in sizes if not self.obj(s).is_file() or self.obj(s).stat().st_size != sizes[s]]
        if missing:
            raise Refused(f"the release needs {len(missing)} objects the host does not have, e.g. {missing[0]}; "
                          "publish it again (it uploads what is missing)")

        arch_name = f"{man['modFolder']}-{v}{'' if man.get('complete', False) else '-incomplete'}.7z"
        arch_path = rel_dir / arch_name
        reuse = old is not None and strip(old) == strip(man) and old.get("archive", {}).get("name") == arch_name and \
            arch_path.is_file() and arch_path.stat().st_size == old["archive"]["size"] and \
            sha256_file(arch_path) == old["archive"]["sha256"]
        if reuse:
            log(f"{v}: already published with the same content; {arch_name} kept")
            man_bytes = published.read_bytes()
        else:
            built = self.build_archive(man, work, arch_name)
            pub = dict(man)
            pub["archive"] = {"format": "7z", "name": arch_name, "size": built.stat().st_size, "sha256": sha256_file(built),
                              "path": f"releases/{v}/{arch_name}"}
            man_bytes = dumps(pub)
            write_atomic(arch_path, src=built, move=True)
            log(f"{v}: {arch_name} built ({pub['archive']['size'] / 1e6:.0f} MB from {man['totalSize'] / 1e6:.0f} MB)")
        notes = [n for n in ready["files"] if NOTES_RE.match(n)]
        sums = [(arch_name, json.loads(man_bytes)["archive"]["sha256"])]
        for n in notes:
            write_atomic(rel_dir / n, src=inc / n)
        write_atomic(published, data=man_bytes)  # the release is complete from here on (gc: a folder without it is not)
        sums.append((MANIFEST_NAME, sha256_bytes(man_bytes)))
        sums += [(n, ready["files"][n]["sha256"]) for n in notes]
        write_atomic(rel_dir / "SHA256SUMS", data="".join(f"{s}  {n}\n" for n, s in sums).encode())
        if ready.get("launcher"):
            lr = man["launcher"]
            ldir = self.site / LAUNCHER_DIR / lr["version"]
            name = ready["launcher"]
            exe = ldir / name
            if not (exe.is_file() and sha256_file(exe) == lr["sha256"]):
                write_atomic(exe, src=inc / name)
            write_atomic(ldir / f"{name}.sha256", data=f"{lr['sha256']}  {name}\n".encode())
            log(f"{v}: downloader {lr['version']} in {LAUNCHER_DIR}/{lr['version']}/{name}")
        if ready.get("channel"):  # LAST
            ch = {"schemaVersion": CHANNEL_SCHEMA, "version": v, "manifest": f"releases/{v}/{MANIFEST_NAME}",
                  "manifestSha256": sha256_bytes(man_bytes), "publishedAt": now_utc()}
            write_atomic(self.site / "channels" / f"{ready['channel']}.json", data=dumps(ch))
            log(f"{v}: channel {ready['channel']} -> {v}")
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(inc)
        log(f"{v}: published ({man['fileCount']} files); incoming/{v} removed")

    def build_archive(self, man: dict, work: Path, name: str) -> Path:
        """The player's mod folder linked (or copied) together from the objects, each read again for its sha256, then
        7-Zipped with the packager's switches."""
        tree = work / "tree"
        top = tree / man["modFolder"]
        for f in man["files"]:
            src, dst = self.obj(f["sha256"]), top.joinpath(*f["path"].split("/"))
            if sha256_file(src) != f["sha256"]:
                raise Refused(f"object {f['sha256']} on the host is damaged (sha256); delete it and publish again")
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
        for d in man.get("emptyDirs", []):
            top.joinpath(*d.split("/")).mkdir(parents=True, exist_ok=True)
        out = work / name
        r = subprocess.run([self.sevenzip, *SEVENZ_ARGS, str(out), man["modFolder"]], cwd=tree, capture_output=True, text=True,
                           errors="replace")
        if r.returncode != 0 or not out.is_file():
            raise Refused(f"7-Zip failed building {name} ({r.returncode}): {(r.stdout + r.stderr).strip()[-300:]}")
        return out

    def clean_parts(self):
        """Temp files (*.part-<hex>) of uploads or writes that died, older than an hour, anywhere in the site."""
        cutoff, n = time.time() - PART_AGE, 0
        for dirpath, _, filenames in os.walk(self.site):
            for name in filenames:
                p = Path(dirpath) / name
                if PART_RE.search(name) and not p.is_symlink() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    n += 1
        if n:
            log(f"removed {n} leftover temp files (*.part-*) from {self.site}")


def lock(incoming: Path):
    """One run at a time (the path unit and the timer may start it together): flock on the incoming folder itself. A lock
    file in it would be a write there, which the path unit sees and answers with another run, forever. -> an fd to close,
    or None where fcntl does not exist (Windows tests)."""
    try:
        import fcntl
    except ImportError:
        return None
    fd = os.open(incoming, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("/srv/mod-files"), help="holds incoming/ and the site folder (default /srv/mod-files)")
    ap.add_argument("--site", default="site", help="the site folder's name under --root (config server.site_dir, e.g. mymod)")
    ap.add_argument("--channels", default=",".join(CHANNELS), help="channel names a publisher may name (config site.channels)")
    ap.add_argument("--7z", dest="sevenzip", help="7-Zip binary (default: 7zz, 7z or 7za on PATH)")
    ap.add_argument("--retry", metavar="VERSION", help="remove incoming/VERSION/REFUSED and try that upload again")
    a = ap.parse_args(argv)
    channels = [c.strip() for c in a.channels.split(",") if c.strip()]
    if not channels or not all(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", c) for c in channels):
        raise SystemExit(f"bad --channels {a.channels!r}")
    ing = Ingest(a.root, a.site, find_7z(a.sevenzip), channels)
    held = lock(ing.incoming)
    try:
        if a.retry:
            if not VERSION_RE.match(a.retry):
                raise SystemExit(f"bad version {a.retry!r}")
            (ing.incoming / a.retry / "REFUSED").unlink(missing_ok=True)
        return ing.run()
    finally:
        if held is not None:
            os.close(held)


if __name__ == "__main__":
    sys.exit(main())
