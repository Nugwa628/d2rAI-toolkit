"""Upload a package_release.py result to the download host (docs/release.md "Hosting layout").

The host serves a single folder over HTTPS: the "site root" (site.url in the config). Its layout:

  channels/<channel>.json                   {"schemaVersion":1,"version","manifest","manifestSha256","publishedAt"}
  releases/<v>/release-manifest.json        the packager's manifest plus "objectsBase":"objects/", the optional "launcher"
                                            entry, and the "archive" entry the server adds
  releases/<v>/<Folder>-<v>.7z              the archive for manual installs; the server builds it from the objects
  releases/<v>/SHA256SUMS, RELEASE_NOTES-<v>.md
  objects/<sha256[0:2]>/<sha256>            file contents addressed by hash and shared across versions (what the downloader fetches)
  downloader/<v>/<exe>, <exe>.sha256        a downloader build published together with a release (--downloader); running
                                            downloaders update from the manifest's "launcher" entry

`publish` transfers only what the host is missing, packed into a single file, and leaves the rest to the server
(dl_ingest.py, run there by a systemd path unit):

  1. a single login reads which objects the host already has and where this version stands;
  2. the missing objects are packed into one solid 7z whose entries are named by sha256 (kept in dist/upload/ so a
     second run reuses it);
  3. the 7z, the manifest, the notes and the downloader are sent to <incoming>/<v>.uploading/, which sits beside the
     site root and is never served; each file is sent under a temporary name and then renamed, an interrupted 7z
     continues with `reput`, and the whole transfer is retried a few times;
  4. READY goes up as the final file and the folder is renamed to <incoming>/<v>/, which is what the server waits for.
  The server then verifies every byte, files the objects, builds the player 7z and writes releases/<v>/, with the
  channel file at the very end. Players never see a partial publish, and repeating the command picks up where it stopped.

Shared file formats (the packager's own manifest is documented in package_release.py):

  channel file v1   channels/<ch>.json   {schemaVersion:1, version, manifest, manifestSha256, publishedAt}
  READY v1          incoming/<v>/READY   {schemaVersion:1, version, channel|null, force, objectsPack|null, objectCount,
                                          launcher|null, files:{name:{size,sha256}}}   (lists every other file in the folder)
  manifest extras   objectsBase "objects/"; launcher {version, path "downloader/<v>/<exe>", size, sha256}; the server adds
                    archive {format, name, size, sha256, path}

Targets (they name the site root; <incoming> is site.incoming relative to it, "../incoming" by default):
  --target dir:<path>                       a local folder (tests: dir:<x>/site with <x>/incoming beside it)
  --target sftp://user@host[:port]/<path>   OpenSSH `sftp` in batch mode with key authentication (`--identity KEY`).
                                            <path> is the server's view, i.e. inside a chroot (/mymod); `/~/x` is x
                                            under the login's home folder.
  Without --target, site.target from the config or the environment variable MODKIT_PUBLISH_TARGET is used.
  MODKIT_SFTP replaces the `sftp` command (for example "sftp -F my_ssh_config").

  py -3 release/publish_release.py [--config release.toml] publish --version 1.0.0 --channel test [--downloader EXE]
  py -3 release/publish_release.py promote 1.0.0 --channel stable
  py -3 release/publish_release.py list
  py -3 release/publish_release.py gc --keep 3 [--dry-run]
  py -3 release/publish_release.py verify [--url https://files.example.com/mymod/] [--channel stable] [--sample 200]
                                          [--expect-version 1.0.0 [--wait 1200]]

promote and gc finish by writing <site>/.sync/wanted, which makes the server mirror the site to the S3 bucket
(dl_sync.py; the ingest does the same after a publish). `list` reports the most recent mirror run.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import struct
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_config  # noqa: E402

SCHEMA = 1  # channel files
MANIFEST_SCHEMA = 2
READY_SCHEMA = 1
CHANNELS = tuple(release_config.DEFAULTS["site"]["channels"])  # set from the config by configure()
OBJECTS_BASE = "objects/"
INCOMING = release_config.DEFAULTS["site"]["incoming"]  # relative to the site root, next to it, not served
LAUNCHER_NAME = release_config.DEFAULTS["downloader"]["exe_name"]  # the downloader's file name (downloader.exe_name)
LAUNCHER_DIR = "downloader"  # <site>/downloader/<v>/<exe>
MANIFEST_NAME = "release-manifest.json"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PACK_RE = re.compile(r"^objects-[0-9a-f]{16}\.7z$")
SYNC_WANTED = ".sync/wanted"  # touched after promote / gc: the server mirrors the site to the bucket (dl_sync.py)
SYNC_STATUS = ".sync/status.json"  # the server's last mirror run; dot files are not served
PART = ".part-"  # temp-name marker: <final name>.part-<random>
RESUME = ".part-0"  # the temp name of a resumable upload (fixed, so the next run finds it)
BATCH_FILES = 200
RETRIES = 5
RETRY_WAIT = 20  # seconds, times the attempt number
INGEST_UNIT = "mod-release-ingest"  # the systemd unit name in the hints (release/server/)
SYNC_UNIT = "mod-release-sync"
USER_AGENT = "modkit-publish-verify/1"  # not "Python-urllib": some CDNs' bot checks refuse that one
PACK_ARGS = ["a", "-t7z", "-m0=lzma2", "-mx=7", "-ms=on", "-mmt=on", "-mtm=off", "-mtc=off", "-mta=off", "-bd", "-y"]


def configure(cfg: dict):
    """Take the channel names, the incoming folder and the downloader's file name from the config."""
    global CHANNELS, INCOMING, LAUNCHER_NAME
    CHANNELS = tuple(cfg["site"]["channels"])
    INCOMING = cfg["site"]["incoming"]
    LAUNCHER_NAME = cfg["downloader"]["exe_name"]


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def object_path(sha: str) -> str:
    return f"{OBJECTS_BASE}{sha[:2]}/{sha}"


def version_key(v: str):
    """Sort key: 1.0.0 < 1.0.1 < 1.1.0-rc1 < 1.1.0 (a pre-release sorts before its release)."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$", v)
    nums = tuple(int(x) for x in m.groups()[:3])
    return nums + (0, m.group(4)) if m.group(4) else nums + (1, "")


def dumps(obj) -> bytes:
    return (json.dumps(obj, indent=1) + "\n").encode("utf-8")


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_version(v: str) -> str:
    v = v.lstrip("v")
    if not VERSION_RE.match(v):
        raise SystemExit(f"bad version {v!r} (expected 1.2.3 or 1.2.3-rc1)")
    return v


def exe_version(path) -> str:
    """x.y.z from the FILEVERSION of a Windows exe's version resource (VS_FIXEDFILEINFO), or "" when it has none.
    The downloader compares this number with its own: publishing the exe under another version (the release's) would
    make every downloader offer itself the same exe again, forever."""
    data = Path(path).read_bytes()
    # VS_FIXEDFILEINFO right after the UTF-16 key "VS_VERSION_INFO" (the same 4 signature bytes can occur in code)
    key = "VS_VERSION_INFO".encode("utf-16-le")
    i = data.find(key)
    while i >= 0:
        j = data.find(bytes.fromhex("bd04effe"), i, i + len(key) + 8)  # dwSignature, after the key's padding
        if j >= 0 and struct.unpack_from("<I", data, j + 4)[0] == 0x10000:  # dwStrucVersion 1.0
            ms, ls = struct.unpack_from("<II", data, j + 8)
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
        i = data.find(key, i + 1)
    return ""


def strip(man: dict) -> dict:
    """What makes two manifests of one version the same release: everything but the packaging date and the 7z (which the
    server builds; dl_ingest.py compares the same way)."""
    return {k: v for k, v in man.items() if k not in ("released", "archive")}


class PublishError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------- targets
class Target:
    """The host's folder. Paths are '/'-separated and relative to the site root. Files appear only complete (temp name,
    then rename); `put` overwrites. list_dir -> {name: (size, is_dir)}, or None when the folder does not exist."""

    def list_dir(self, rel):  # pragma: no cover - interface
        raise NotImplementedError

    def list_dirs(self, rels):
        return {r: self.list_dir(r) for r in rels}

    def snapshot(self, dirs, files):
        """Listings of `dirs` and contents of `files` (None when missing); one login on an sftp target."""
        return self.list_dirs(dirs), {f: self.read(f) for f in files}

    def read(self, rel):
        raise NotImplementedError

    def put_files(self, items):
        """items: [(local Path, rel)]"""
        raise NotImplementedError

    def put_bytes(self, data: bytes, rel):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "payload"
            p.write_bytes(data)
            self.put_files([(p, rel)])

    def delete_file(self, rel):
        raise NotImplementedError

    def delete_files(self, rels):
        for rel in rels:
            self.delete_file(rel)

    def delete_dir(self, rel):
        """Remove a folder and everything in it."""
        listing = self.list_dir(rel)
        if listing is None:
            return
        for name, (_, is_dir) in sorted(listing.items()):
            (self.delete_dir if is_dir else self.delete_file)(f"{rel}/{name}")
        self._rmdir(rel)

    def _rmdir(self, rel):
        raise NotImplementedError

    def deliver(self, up, final, items, ready: bytes, listing):
        """Upload items [(local Path, name)] into the folder `up`, then READY last, then rename `up` to `final`. `listing`:
        what `up` holds now ({} when it does not exist): a partial objects 7z is resumed."""
        raise NotImplementedError


def tmp_name(rel: str) -> str:
    return f"{rel}{PART}{secrets.token_hex(4)}"


class DirTarget(Target):
    def __init__(self, root):
        self.root = Path(root)

    def _p(self, rel):
        return self.root.joinpath(*rel.split("/"))

    def list_dir(self, rel):
        p = self._p(rel)
        if not p.is_dir():
            return None
        return {c.name: (c.stat().st_size if c.is_file() else 0, c.is_dir()) for c in p.iterdir()}

    def read(self, rel):
        p = self._p(rel)
        return p.read_bytes() if p.is_file() else None

    def put_files(self, items):
        for local, rel in items:
            dest = self._p(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._p(tmp_name(rel))
            shutil.copyfile(local, tmp)
            os.replace(tmp, dest)

    def delete_file(self, rel):
        self._p(rel).unlink(missing_ok=True)

    def _rmdir(self, rel):
        try:
            self._p(rel).rmdir()
        except OSError:
            pass

    def deliver(self, up, final, items, ready, listing):
        names = {n for _, n in items} | {"READY"}
        for n in listing:
            if n not in names:
                self.delete_file(f"{up}/{n}")
        self.put_files([(local, f"{up}/{n}") for local, n in items])
        self.put_bytes(ready, f"{up}/READY")
        if self._p(final).exists():
            raise PublishError(f"{final} exists already")
        os.replace(self._p(up), self._p(final))

    def __str__(self):
        return f"dir:{self.root}"


LS_RE = re.compile(r"^([-dlcbps\w])[-rwxsStTl+.@]{8,10}\s+\S+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\d+\s+\S+\s+(.+?)\s*$")
ECHO = "sftp> "


def sftp_quote(s) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_sftp_ls(lines):
    """`ls -ln` output lines -> {name: (size, is_dir)}."""
    out = {}
    for ln in lines:
        m = LS_RE.match(ln)
        if not m or m.group(3) in (".", ".."):
            continue
        out[m.group(3).rsplit("/", 1)[-1]] = (int(m.group(2)), m.group(1) == "d")
    return out


class SftpTarget(Target):
    """OpenSSH sftp in batch mode, no shell needed on the server (works for an internal-sftp chroot user). Each call is one
    ssh login that runs a batch of commands, so many files go through one connection."""

    def __init__(self, user, host, port, path, identity=None, sftp_cmd=None):
        self.user, self.host, self.port, self.path = user, host, port, path
        self.identity = identity
        self.cmd = sftp_cmd or shlex.split(os.environ.get(release_config.ENV_SFTP, "sftp"))
        self.logins = 0

    @classmethod
    def parse(cls, url, identity=None, sftp_cmd=None):
        m = re.match(r"^sftp://(?:([^@/]+)@)?([^:/]+)(?::(\d+))?(/.*)?$", url)
        if not m:
            raise SystemExit(f"bad sftp target {url!r}; expected sftp://user@host[:port]/path")
        path = m.group(4) or ""
        if path.startswith("/~/"):
            path = path[3:]
        elif path == "/~":
            path = ""
        return cls(m.group(1), m.group(2), int(m.group(3)) if m.group(3) else None, path.rstrip("/"), identity, sftp_cmd)

    def _argv(self):
        a = list(self.cmd) + ["-b", "-", "-oBatchMode=yes", "-oConnectTimeout=30", "-oServerAliveInterval=15",
                              "-oServerAliveCountMax=4"]
        if self.port:
            a += ["-P", str(self.port)]
        if self.identity:
            a += ["-i", str(self.identity), "-oIdentitiesOnly=yes"]
        return a + [f"{self.user}@{self.host}" if self.user else self.host]

    def _abs(self, rel):
        return f"{self.path}/{rel}" if self.path else rel

    def _run(self, commands):
        """Run commands in one session -> (stdout lines, stderr). Raises PublishError when sftp exits non-zero (a command
        without a leading '-' failed, or the login or the connection did)."""
        self.logins += 1
        proc = subprocess.run(self._argv(), input="\n".join(commands) + "\n", capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        if proc.returncode != 0:
            raise PublishError(f"sftp {self.host} failed ({proc.returncode}): {proc.stderr.strip()[-600:]}")
        return proc.stdout.splitlines(), proc.stderr

    def snapshot(self, dirs, files):
        """One session: `ls` every folder, `get` every file (a missing one is fine). A folder that does not exist -> None;
        any other `ls` failure raises."""
        dirs, files = list(dirs), list(files)
        with tempfile.TemporaryDirectory() as d:
            local = {f: Path(d) / f"f{i}" for i, f in enumerate(files)}
            cmds = [f"-ls -ln {sftp_quote(self._abs(r))}" for r in dirs] + \
                   [f"-get {sftp_quote(self._abs(f))} {sftp_quote(local[f].as_posix())}" for f in files]
            if not cmds:
                return {}, {}
            lines, err = self._run(cmds)
            contents = {f: (local[f].read_bytes() if local[f].is_file() else None) for f in files}
        missing = set(re.findall(r"Can't ls: \"(.*)\" not found", err))
        bad = [ln for ln in err.splitlines() if re.search(r"Couldn't|Can't ls|Permission denied|Failure", ln)
               and not re.search(r"Can't ls: \".*\" not found", ln)]
        if bad:
            raise PublishError("sftp ls failed: " + "; ".join(bad[:3]))
        out, idx = {r: [] for r in dirs}, -1
        for ln in lines:
            if ln.startswith(ECHO):  # batch mode echoes each command before its output
                idx += 1
            elif 0 <= idx < len(dirs):
                out[dirs[idx]].append(ln)
        return {r: (None if self._abs(r) in missing else parse_sftp_ls(out[r])) for r in dirs}, contents

    def list_dirs(self, rels):
        return self.snapshot(rels, [])[0] if rels else {}

    def list_dir(self, rel):
        return self.list_dirs([rel])[rel]

    def read(self, rel):
        return self.snapshot([], [rel])[1][rel]

    def _mkdirs(self, rels):
        dirs = set()
        for rel in rels:
            parts = rel.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                if parts[i - 1] != "..":
                    dirs.add("/".join(parts[:i]))
        return [f"-mkdir {sftp_quote(self._abs(d))}" for d in sorted(dirs, key=lambda d: (d.count("/"), d))]

    def put_files(self, items):
        items = list(items)
        for i in range(0, len(items), BATCH_FILES):
            chunk = items[i:i + BATCH_FILES]
            cmds = self._mkdirs([rel for _, rel in chunk])
            tmps = []
            for local, rel in chunk:
                t = tmp_name(rel)
                tmps.append((t, rel))
                cmds.append(f"put {sftp_quote(Path(local).as_posix())} {sftp_quote(self._abs(t))}")
            # OpenSSH's sftp-server replaces an existing target atomically (the posix-rename extension). A server without it
            # refuses; then the temp file is still there, and the old file is removed just before the rename.
            cmds += [f"-rename {sftp_quote(self._abs(t))} {sftp_quote(self._abs(rel))}" for t, rel in tmps]
            self._run(cmds)
            parents = sorted({rel.rpartition("/")[0] for _, rel in chunk})
            left = self.list_dirs(parents)
            stuck = [(t, rel) for t, rel in tmps if t.rpartition("/")[2] in (left[rel.rpartition("/")[0]] or {})]
            if stuck:
                self._run([c for t, rel in stuck for c in (f"-rm {sftp_quote(self._abs(rel))}",
                                                           f"rename {sftp_quote(self._abs(t))} {sftp_quote(self._abs(rel))}")])

    def delete_file(self, rel):
        self._run([f"rm {sftp_quote(self._abs(rel))}"])

    def delete_files(self, rels):
        rels = list(rels)
        for i in range(0, len(rels), BATCH_FILES * 5):  # one login per 1000 files; a file that is already gone is fine
            self._run([f"-rm {sftp_quote(self._abs(r))}" for r in rels[i:i + BATCH_FILES * 5]])

    def delete_dir(self, rel):
        """One login for a flat folder (an incoming upload); deeper trees go the generic way."""
        listing = self.list_dir(rel)
        if listing is None:
            return
        if any(d for _, d in listing.values()):
            return super().delete_dir(rel)
        self._run([f"-rm {sftp_quote(self._abs(rel + '/' + n))}" for n in sorted(listing)] + [f"rmdir {sftp_quote(self._abs(rel))}"])

    def _rmdir(self, rel):
        self._run([f"-rmdir {sftp_quote(self._abs(rel))}"])

    def deliver(self, up, final, items, ready, listing):
        """One login: stale files out, every item up (the objects 7z resumed with `reput` when a part of it is there), READY
        last, then the folder rename that the server's path unit sees."""
        q, a = sftp_quote, lambda n: self._abs(f"{up}/{n}")
        names = {n for _, n in items}
        keep = names | {n + RESUME for n in names if PACK_RE.match(n)}  # a part of this objects 7z is resumed
        cmds = [f"-mkdir {q(self._abs(INCOMING))}", f"-mkdir {q(self._abs(up))}"]
        cmds += [f"-rm {q(a(n))}" for n in sorted(listing) if n not in keep]
        for local, n in items:
            size, tmp = Path(local).stat().st_size, n + RESUME
            have, part = listing.get(n), listing.get(tmp)
            if PACK_RE.match(n):  # content-addressed name: what is there with this name is these bytes
                if have and have[0] == size and not part:
                    continue
                if part and part[0] == size:
                    pass  # complete, only the rename is missing
                elif part and 0 < part[0] < size:
                    cmds.append(f"reput {q(Path(local).as_posix())} {q(a(tmp))}")
                else:
                    cmds.append(f"put {q(Path(local).as_posix())} {q(a(tmp))}")
            else:  # small files: sent again every time (a same-named file of a re-run may have other bytes)
                cmds.append(f"put {q(Path(local).as_posix())} {q(a(tmp))}")
            if have:
                cmds.append(f"-rm {q(a(n))}")
            cmds.append(f"rename {q(a(tmp))} {q(a(n))}")
        with tempfile.TemporaryDirectory() as d:
            rp = Path(d) / "READY"
            rp.write_bytes(ready)
            cmds += [f"-rm {q(a('READY'))}", f"put {q(rp.as_posix())} {q(a('READY' + RESUME))}",
                     f"rename {q(a('READY' + RESUME))} {q(a('READY'))}",
                     f"rename {q(self._abs(up))} {q(self._abs(final))}"]
            self._run(cmds)

    def __str__(self):
        return f"sftp://{self.user + '@' if self.user else ''}{self.host}{':' + str(self.port) if self.port else ''}/{self.path.lstrip('/')}"


def open_target(spec, identity=None, cfg=None) -> Target:
    spec = spec or os.environ.get(release_config.ENV_TARGET) or ((cfg or {}).get("site", {}).get("target") or None)
    if not spec:
        raise SystemExit(f"no target: --target dir:<path> | sftp://user@host[:port]/path (or site.target in the config, "
                         f"or {release_config.ENV_TARGET})")
    if "<" in spec:
        raise SystemExit(f"the target {spec!r} still has a placeholder: set site.target in your release.toml "
                         f"(or {release_config.ENV_TARGET})")
    if identity:
        identity = os.path.expanduser(identity)
    if spec.startswith("dir:"):
        return DirTarget(spec[4:])
    if spec.startswith("sftp://"):
        return SftpTarget.parse(spec, identity)
    raise SystemExit(f"unknown target {spec!r}")


# --------------------------------------------------------------------------------------------------------------- sources
def find_7z():
    import package_release
    return package_release.find_7z()


class ArchiveSource:
    """File bytes of a release from the packager's 7z (the staging folder is gone after packaging): unpacked once into
    `work`, on first use."""

    def __init__(self, archive: Path, work: Path, top, sevenzip=None):
        self.archive, self.work, self.top, self.sevenzip = Path(archive), Path(work), top, sevenzip
        self.folder = None

    def extract(self, path, dest: Path) -> str:
        if self.folder is None:
            exe = self.sevenzip or find_7z()
            if not exe:
                raise PublishError("no 7-Zip found: it is needed to read the release 7z")
            out = self.work / "unpacked"
            shutil.rmtree(out, ignore_errors=True)
            r = subprocess.run([exe, "x", "-y", "-bd", f"-o{out}", str(self.archive)], capture_output=True, text=True,
                               errors="replace")
            if r.returncode != 0:
                raise PublishError(f"7-Zip cannot unpack {self.archive.name}: {(r.stdout + r.stderr).strip()[-300:]}")
            self.folder = out / self.top
        return link_or_copy(self.folder.joinpath(*path.split("/")), dest)

    def close(self):
        shutil.rmtree(self.work / "unpacked", ignore_errors=True)


class DirSource:
    """File bytes from a staged mod folder (the <Folder>/ folder itself)."""

    def __init__(self, folder):
        self.folder = Path(folder)

    def extract(self, path, dest: Path) -> str:
        return link_or_copy(self.folder.joinpath(*path.split("/")), dest)

    def close(self):
        pass


def link_or_copy(src: Path, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)
    return sha256_file(dest)


# ------------------------------------------------------------------------------------------------------------- publishing
def load_manifest(dist: Path, version: str, allow_incomplete: bool, channel):
    man_path = dist / MANIFEST_NAME
    if not man_path.is_file():
        raise PublishError(f"{man_path} not found: run release/package_release.py first")
    man = json.loads(man_path.read_text("utf-8"))
    if man.get("schemaVersion") != MANIFEST_SCHEMA:
        raise PublishError(f"manifest schemaVersion {man.get('schemaVersion')}, this publisher writes {MANIFEST_SCHEMA}: "
                           "package it again with this checkout's release/package_release.py")
    if man.get("version") != version:
        raise PublishError(f"{man_path} is version {man.get('version')}, not {version}")
    if not man.get("complete", False):
        if channel == "stable" or not allow_incomplete:
            raise PublishError("the manifest says complete=false (an --incomplete CI package): not for players "
                               "(--allow-incomplete publishes it to the test channel or with --no-channel)")
    for f in man["files"]:
        if not SHA_RE.match(f["sha256"]) or ".." in f["path"].split("/") or f["path"].startswith("/"):
            raise PublishError(f"bad manifest entry {f['path']!r}")
    return man


def object_table(files):
    """sha -> (size, first path): files with the same bytes (across versions, and inside one) are one object."""
    objs = {}
    for f in files:
        prev = objs.setdefault(f["sha256"], (f["size"], f["path"]))
        if prev[0] != f["size"]:
            raise PublishError(f"{f['path']} and {prev[1]} have the same sha256 but different sizes")
    return objs


def object_dirs(shas):
    return [f"objects/{p}" for p in sorted({s[:2] for s in shas})]


def objects_from_listings(listings, shas):
    """-> {sha: size} of the objects (among shas) in the prefix folder listings."""
    have = {}
    for d in object_dirs(shas):
        for name, (size, is_dir) in (listings.get(d) or {}).items():
            if SHA_RE.match(name) and not is_dir:
                have[name] = size
    return have


def missing_objects(target: Target, objs):
    """Objects the target lacks or has with the wrong size: {sha: (size, path)}."""
    have = objects_from_listings(target.list_dirs(object_dirs(objs)), objs)
    return {s: v for s, v in objs.items() if have.get(s) != v[0]}


def pack_objects(source, missing, work: Path, sevenzip=None, log=print):
    """The missing objects as one solid 7z, entries named by sha256. Cached in `work` by the set of objects, so a re-run
    after a dropped upload sends the same file (and can resume it). -> (path, size, sha256, remote name)."""
    order = sorted(missing)  # 7-Zip orders a solid block by entry name anyway (sha256 order)
    set_id = sha256_bytes("".join(f"{s}\n" for s in order).encode())[:16]
    cached, meta = work / f"objects-set-{set_id}.7z", work / f"objects-set-{set_id}.json"
    if cached.is_file() and meta.is_file():
        m = json.loads(meta.read_text("utf-8"))
        if m.get("size") == cached.stat().st_size:
            log(f"objects 7z of an earlier run reused ({m['size'] / 1e6:.1f} MB)")
            return cached, m["size"], m["sha256"], m["name"]
    exe = sevenzip or find_7z()
    if not exe:
        raise PublishError("no 7-Zip found: it is needed to pack the objects")
    for p in work.glob("objects-set-*"):  # only one cached pack: the newest
        p.unlink()
    stage = work / "pack"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    for sha, (_, path) in sorted(missing.items()):
        got = source.extract(path, stage / sha)
        if got != sha:
            raise PublishError(f"{path}: sha256 {got} does not match the manifest ({sha}); package again")
    names = work / "pack-list.txt"
    names.write_text("".join(f"{s}\n" for s in order), encoding="utf-8")
    tmp = work / f"objects-set-{set_id}.7z.tmp"
    tmp.unlink(missing_ok=True)
    t = time.time()
    r = subprocess.run([exe, *PACK_ARGS, "-scsUTF-8", str(tmp.resolve()), f"@{names.resolve()}"], cwd=stage,
                       capture_output=True, text=True, errors="replace")
    shutil.rmtree(stage, ignore_errors=True)
    names.unlink()
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        raise PublishError(f"7-Zip failed packing the objects ({r.returncode}): {(r.stdout + r.stderr).strip()[-300:]}")
    os.replace(tmp, cached)
    size, sha = cached.stat().st_size, sha256_file(cached)
    name = f"objects-{sha[:16]}.7z"
    meta.write_text(json.dumps({"size": size, "sha256": sha, "name": name, "count": len(missing)}), encoding="utf-8")
    log(f"packed {len(missing)} objects ({sum(v[0] for v in missing.values()) / 1e6:.0f} MB) into {size / 1e6:.1f} MB "
        f"({time.time() - t:.0f} s)")
    return cached, size, sha, name


def publish(target: Target, dist: Path, version: str, channel, source=None, downloader=None, downloader_version=None,
            allow_incomplete=False, force=False, retries=RETRIES, work=None, sevenzip=None, log=print):
    """-> a dict: what happened ("state": "uploaded" | "waiting" | "published"), and the sizes."""
    version = check_version(version)
    man = load_manifest(dist, version, allow_incomplete, channel)
    arch = man.get("archive") or {}
    arch_path = dist / arch.get("name", "-")
    if source is None:
        if arch.get("format") != "7z" or not arch_path.is_file():
            raise PublishError(f"{arch_path} not found, but the manifest names it (package again)")
        log(f"checking {arch_path.name} against the manifest")
        if arch_path.stat().st_size != arch["size"] or sha256_file(arch_path) != arch["sha256"]:
            raise PublishError(f"{arch_path.name} does not match archive.size / archive.sha256 of the manifest")
    work = Path(work or dist / "upload" / version)
    work.mkdir(parents=True, exist_ok=True)

    # the manifest as the server gets it: the packager's, plus the host's fields; the server adds "archive"
    pub = {k: v for k, v in man.items() if k != "archive"}
    pub["objectsBase"] = OBJECTS_BASE
    if downloader:
        own = exe_version(downloader)
        if downloader_version and own and check_version(downloader_version) != own:
            raise PublishError(f"--downloader-version {downloader_version}, but {Path(downloader).name} says it is {own}")
        if not (downloader_version or own):
            raise PublishError(f"{Path(downloader).name} has no version resource: give --downloader-version")
        lv = check_version(downloader_version or own)
        # the key is "launcher": the documented name the downloader reads for its self-update
        pub["launcher"] = {"version": lv, "path": f"{LAUNCHER_DIR}/{lv}/{LAUNCHER_NAME}", "size": Path(downloader).stat().st_size,
                           "sha256": sha256_file(downloader)}
    objs = object_table(man["files"])
    log(f"{len(man['files'])} files = {len(objs)} distinct objects")

    up, final = f"{INCOMING}/{version}.uploading", f"{INCOMING}/{version}"
    man_rel = f"releases/{version}/{MANIFEST_NAME}"
    for attempt in range(retries + 1):  # the inventory is one long SFTP session: a dropped connection is retried too
        try:
            listings, files = target.snapshot(object_dirs(objs) + [up, final],
                                              [man_rel, f"{final}/READY", f"{final}/REFUSED", f"{final}/{MANIFEST_NAME}"])
            break
        except PublishError as e:
            if attempt == retries:
                raise PublishError(f"reading what the host has failed {retries + 1} times, last: {e}")
            wait = RETRY_WAIT * (attempt + 1)
            log(f"reading the host's objects failed ({str(e)[-200:]}); again in {wait} s ({attempt + 2} of {retries + 1})")
            time.sleep(wait)
    have = objects_from_listings(listings, objs)
    missing = {s: v for s, v in objs.items() if have.get(s) != v[0]}
    old = files[man_rel]
    if old is not None and not force:
        if strip(json.loads(old)) != strip(pub):
            raise PublishError(f"{man_rel} is already published with different contents: a published version never "
                               f"changes; build a new version (or --force)")
        if missing:
            raise PublishError(f"{version} is published but the host lacks {len(missing)} of its objects; publish --force")
        log(f"{version} is already published")
        if channel:
            write_channel(target, channel, version, sha256_bytes(old), log)
        return {"state": "published"}

    if listings[final] is not None:  # an upload of this version is waiting for the server, or was refused
        if files[f"{final}/REFUSED"] is not None and not force:
            raise PublishError(f"the server refused the earlier upload of {version}: "
                               f"{files[f'{final}/REFUSED'].decode('utf-8', 'replace').strip()}. It stays in incoming/{version} "
                               f"for a look (journalctl -u {INGEST_UNIT}); `publish --force` deletes it and uploads again")
        if files[f"{final}/REFUSED"] is None and files[f"{final}/READY"] is not None and not force:
            waiting = files[f"{final}/{MANIFEST_NAME}"]
            if waiting is None or strip(json.loads(waiting)) != strip(pub):
                raise PublishError(f"incoming/{version} holds another upload of {version} that waits for the server; "
                                   "let it finish (and publish a new version), or `publish --force` replaces it")
            log(f"{version} is uploaded and waiting for the server (incoming/{version}); nothing to do")
            return {"state": "waiting"}
        target.delete_dir(final)
        log(f"incoming/{version}: the earlier upload deleted (--force)")

    log(f"{len(objs) - len(missing)} objects already on the host, {len(missing)} to upload "
        f"({sum(v[0] for v in missing.values()) / 1e6:.0f} MB unpacked)")
    own_source = source is None
    source = source or ArchiveSource(arch_path, work, man.get("modFolder", ""), sevenzip)
    try:
        items, info = [], {}
        meta = {}
        pack_name = None
        if missing:
            local, size, sha, pack_name = pack_objects(source, missing, work, sevenzip, log)
            items.append((local, pack_name))
            meta[pack_name] = {"size": size, "sha256": sha}
            info["pack"] = size
    finally:
        if own_source:
            source.close()
    man_local = work / MANIFEST_NAME
    man_local.write_bytes(dumps(pub))
    items.append((man_local, MANIFEST_NAME))  # after the 7z: the small files are sent again on each attempt anyway
    notes = dist / f"RELEASE_NOTES-{version}.md"
    if notes.is_file():
        items.append((notes, notes.name))
    if downloader:
        items.append((Path(downloader), LAUNCHER_NAME))
    for local, n in items:
        if n not in meta:
            meta[n] = {"size": Path(local).stat().st_size, "sha256": sha256_file(local)}
    ready = dumps({"schemaVersion": READY_SCHEMA, "version": version, "channel": channel, "force": bool(force),
                   "objectsPack": pack_name, "objectCount": len(missing), "launcher": LAUNCHER_NAME if downloader else None,
                   "files": meta})
    total = sum(m["size"] for m in meta.values())
    log(f"uploading {len(items)} files ({total / 1e6:.1f} MB) to incoming/{version} on {target}")
    current = listings[up] or {}
    t = time.time()
    for attempt in range(retries + 1):
        try:
            if attempt:
                now = target.list_dirs([up, final])
                if now[final] is not None and "READY" in now[final]:
                    break  # the last attempt got through; only its answer was lost
                current = now[up] or {}
            target.deliver(up, final, items, ready, current)
            break
        except PublishError as e:
            if attempt == retries:
                raise PublishError(f"upload failed {retries + 1} times, last: {e}. Run the same command again: it resumes")
            wait = RETRY_WAIT * (attempt + 1)
            log(f"upload interrupted ({str(e)[-200:]}); again in {wait} s, resuming ({attempt + 2} of {retries + 1})")
            time.sleep(wait)
    info.update(state="uploaded", bytes=total, seconds=time.time() - t, objects=len(missing))
    log(f"uploaded in {info['seconds']:.0f} s. The server checks it and publishes {version}"
        + (f" on channel {channel}" if channel else "") + f" (dl_ingest.py, journalctl -u {INGEST_UNIT}); "
        "`list` shows it once done")
    return info


def write_channel(target: Target, channel: str, version: str, man_sha: str, log=print):
    body = {"schemaVersion": SCHEMA, "version": version, "manifest": f"releases/{version}/{MANIFEST_NAME}",
            "manifestSha256": man_sha, "publishedAt": now_utc()}
    target.put_bytes(dumps(body), f"channels/{channel}.json")
    log(f"channel {channel} -> {version}")


def read_json(target: Target, rel):
    raw = target.read(rel)
    return None if raw is None else (json.loads(raw), raw)


def promote(target: Target, version: str, channel: str, log=print):
    version = check_version(version)
    got = read_json(target, f"releases/{version}/{MANIFEST_NAME}")
    if got is None:
        raise PublishError(f"release {version} is not on the host (publish it first)")
    man, raw = got
    if channel == "stable" and not man.get("complete", False):
        raise PublishError(f"{version} is complete=false: not for the stable channel")
    objs = object_table(man["files"])
    miss = missing_objects(target, objs)
    if miss:
        raise PublishError(f"{version} is missing {len(miss)} objects on the host; publish it again before promoting")
    write_channel(target, channel, version, sha256_bytes(raw), log)
    request_sync(target, f"promote {version} {channel}", log)


def request_sync(target: Target, reason: str, log=print):
    """Tell the server to mirror the site to the bucket (dl_sync.py): <site>/.sync/wanted is watched by a systemd path
    unit. The ingest does this itself; promote and gc change the site directly. Best effort: the server's timer syncs
    every 30 minutes anyway. Harmless where no mirror is set up."""
    try:
        target.put_bytes(f"{now_utc()} {reason}\n".encode(), SYNC_WANTED)
    except (PublishError, OSError) as e:
        log(f"warning: could not ask for a mirror sync ({str(e)[-200:]}); the server's timer does it within 30 minutes")


def sync_status(target: Target):
    """The server's last mirror sync (dl_sync.py writes <site>/.sync/status.json) -> one line, or None."""
    try:
        raw = target.read(SYNC_STATUS)
        st = json.loads(raw) if raw else None
    except (PublishError, ValueError):
        return "Mirror sync: status unreadable"
    if not st:
        return None
    if st.get("result") == "ok":
        return (f"Mirror sync: ok at {st.get('finishedAt')} ({st.get('uploaded', 0)} uploaded, {st.get('deleted', 0)} deleted; "
                f"{st.get('remote')})")
    return (f"Mirror sync: FAILED at {st.get('finishedAt')}: {st.get('error', '?')} (last ok {st.get('lastSuccessAt') or 'never'}; "
            f"journalctl -u {SYNC_UNIT})")


def release_versions(target: Target):
    listing = target.list_dir("releases") or {}
    return sorted((n for n, (_, d) in listing.items() if d and VERSION_RE.match(n)), key=version_key)


def channel_versions(target: Target):
    """Every channels/*.json (not only the configured ones: gc must see all of them) -> {channel: parsed file}."""
    out = {}
    for name, (_, is_dir) in sorted((target.list_dir("channels") or {}).items()):
        if name.endswith(".json") and not is_dir:
            out[name[:-5]] = json.loads(target.read(f"channels/{name}"))
    return out


def list_cmd(target: Target, log=print):
    chans = channel_versions(target)
    versions = release_versions(target)
    log(f"target {target}")
    for ch in CHANNELS:
        c = chans.get(ch)
        log(f"channel {ch:7s} " + (f"{c['version']}  published {c['publishedAt']}" if c else "(none)"))
    pointed = {c["version"]: [k for k, v in chans.items() if v["version"] == c["version"]] for c in chans.values()}
    for v in versions:
        got = read_json(target, f"releases/{v}/{MANIFEST_NAME}")
        if not got:
            log(f"release {v:12s} (no manifest: unfinished)")
            continue
        m = got[0]
        flags = ([f"<- {','.join(pointed[v])}"] if v in pointed else []) + ([] if m.get("complete") else ["incomplete"]) \
            + (["downloader " + m["launcher"]["version"]] if m.get("launcher") else [])
        arch = f"7z {m['archive']['size'] / 1e6:.0f} MB  " if m.get("archive") else ""
        log(f"release {v:12s} {m['fileCount']} files  {m['totalSize'] / 1e6:.0f} MB  {arch}{m.get('released', '')}  {' '.join(flags)}")
    incoming = target.list_dir(INCOMING) or {}
    for name in sorted(incoming):
        if incoming[name][1] and not name.startswith("."):
            log(f"incoming {name} (waiting for the server, or refused: see journalctl -u {INGEST_UNIT})")
    line = sync_status(target)
    if line:
        log(line)
    return versions, chans


def gc(target: Target, keep: int, dry_run=False, log=print):
    """Delete releases older than the newest `keep` (never one a channel points to), then the objects no remaining release
    references. Refuses when it cannot read a manifest it needs (a missing reference list would delete live files)."""
    if keep < 1:
        raise PublishError("--keep must be at least 1")
    versions = release_versions(target)
    chans = channel_versions(target)
    pinned = {c["version"] for c in chans.values()}
    manifests = {v: read_json(target, f"releases/{v}/{MANIFEST_NAME}") for v in versions}
    for v in pinned:
        if manifests.get(v) is None:
            raise PublishError(f"a channel points to {v}, which has no manifest on the host: not collecting anything")
    finished = [v for v in versions if manifests[v] is not None]  # a folder without manifest is an unfinished publish
    keepers = set(finished[-keep:]) | pinned
    doomed = [v for v in versions if v not in keepers]
    refs, downloaders = set(), set()
    for v in keepers:
        man = manifests[v][0]
        refs |= {f["sha256"] for f in man["files"]}
        if man.get("launcher"):
            downloaders.add(man["launcher"]["version"])
    log(f"keep {', '.join(sorted(keepers, key=version_key))}; remove {', '.join(doomed) or 'no release'}")
    doomed_downloaders = sorted({manifests[v][0]["launcher"]["version"] for v in doomed
                                 if manifests[v] and manifests[v][0].get("launcher")} - downloaders)
    prefixes = [f"objects/{p:02x}" for p in range(256)]
    listings = target.list_dirs(prefixes)
    orphans, stale = [], []
    for pre, listing in listings.items():
        for name, (size, is_dir) in (listing or {}).items():
            if PART in name:
                stale.append((f"{pre}/{name}", size))
            elif SHA_RE.match(name) and name not in refs:
                orphans.append((f"{pre}/{name}", size))
    log(f"{len(orphans)} unreferenced objects ({sum(s for _, s in orphans) / 1e6:.0f} MB), {len(stale)} leftover temp files")
    if dry_run:
        log("dry run: nothing deleted")
        return doomed, orphans
    for v in doomed:  # manifests first: a half-finished gc leaves orphan objects, never a release with holes
        target.delete_file(f"releases/{v}/{MANIFEST_NAME}")
    for v in doomed:
        target.delete_dir(f"releases/{v}")
    for lv in doomed_downloaders:
        target.delete_dir(f"{LAUNCHER_DIR}/{lv}")
    target.delete_files(rel for rel, _ in orphans + stale)
    request_sync(target, f"gc --keep {keep}", log)
    log("done")
    return doomed, orphans


# ------------------------------------------------------------------------------------------------------------------ verify
def http_get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def verify_file(url, size, sha, hash_it=True):
    """Download `url` in chunks and compare size (and sha256 when hash_it). A manual-download archive: too big to hold in memory."""
    h, n = hashlib.sha256(), 0
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT}), timeout=60) as r:
        if not hash_it:
            got = r.headers.get("Content-Length")
            if got is None or int(got) != size:
                raise PublishError(f"{url}: Content-Length {got}, manifest says {size}")
            return
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    if n != size or h.hexdigest() != sha:
        raise PublishError(f"{url}: bad content (size {n}, manifest says {size}; sha256 {h.hexdigest()[:12]}...)")


def wait_for_channel(base, channel, version, wait, log=print, interval=15):
    """Poll channels/<channel>.json until it names `version` (up to `wait` seconds). Raises PublishError when it never does.
    A mirror or CDN may need a while after the server wrote the file."""
    base = base.rstrip("/") + "/"
    deadline, seen = time.monotonic() + wait, None
    while True:
        try:
            _, _, raw = http_get(f"{base}channels/{channel}.json")
            seen = json.loads(raw).get("version")
            if seen == version:
                return
        except (urllib.error.URLError, ValueError, OSError) as e:
            seen = f"unreadable ({str(e)[:80]})"
        if time.monotonic() >= deadline:
            raise PublishError(f"channel {channel} at {base} shows {seen}, not {version}"
                               + (f" after {wait} s" if wait else ""))
        log(f"channel {channel} shows {seen}, waiting for {version} ...")
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


def verify_url(base, channel="stable", sample=0, log=print, expect_version=None, wait=0):
    """Fetch channel -> manifest -> objects over HTTP and check every hash (and Range support on one object), then the 7z.
    Returns the number of objects checked. This is what a downloader does; run it against the public URL.
    expect_version: the channel must name this version (waiting up to `wait` seconds for it)."""
    base = base.rstrip("/") + "/"
    if expect_version:
        wait_for_channel(base, channel, expect_version, wait, log)
    _, h, raw = http_get(f"{base}channels/{channel}.json")
    ch = json.loads(raw)
    log(f"channel {channel}: {ch['version']} (Cache-Control: {h.get('Cache-Control', '-')})")
    if ch.get("schemaVersion") != SCHEMA:
        raise PublishError(f"channel schemaVersion {ch.get('schemaVersion')}")
    if expect_version and ch["version"] != expect_version:
        raise PublishError(f"channel {channel} shows {ch['version']}, not {expect_version}")
    _, _, mraw = http_get(base + ch["manifest"])
    if sha256_bytes(mraw) != ch["manifestSha256"]:
        raise PublishError("manifest sha256 does not match the channel file")
    man = json.loads(mraw)
    if man.get("schemaVersion") != MANIFEST_SCHEMA:
        raise PublishError(f"manifest schemaVersion {man.get('schemaVersion')}, expected {MANIFEST_SCHEMA}")
    objs = object_table(man["files"])
    shas = sorted(objs)
    if sample and sample < len(shas):
        step = len(shas) / sample
        shas = [shas[int(i * step)] for i in range(sample)]
    first = True
    for sha in shas:
        url = base + man.get("objectsBase", OBJECTS_BASE) + f"{sha[:2]}/{sha}"
        _, h, body = http_get(url)
        if sha256_bytes(body) != sha or len(body) != objs[sha][0]:
            raise PublishError(f"{url}: bad content")
        if first:
            log(f"object Cache-Control: {h.get('Cache-Control', '-')}, Content-Encoding: {h.get('Content-Encoding', '-')}")
            first = False
    big = max(shas, key=lambda s: objs[s][0]) if shas else None
    if big and objs[big][0] > 2:
        n = objs[big][0]
        url = base + man.get("objectsBase", OBJECTS_BASE) + f"{big[:2]}/{big}"
        st, h, body = http_get(url, {"Range": "bytes=1-2", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Range": f"bytes={n - 1}-", "User-Agent": USER_AGENT})) as r:
            tail = r.read()
        if st != 206 or len(body) != 2 or len(tail) != 1:
            raise PublishError(f"{url}: Range requests are not served (status {st})")
        log("Range requests: ok (206)")
    log(f"{len(shas)} of {len(objs)} objects verified")
    arch = man.get("archive")
    if not arch:
        raise PublishError("the manifest has no \"archive\" (the server builds it; was the release ingested?)")
    # the manual download: sha256 of the whole file (with --sample only its Content-Length: it is the biggest file)
    verify_file(base + arch.get("path", f"releases/{man['version']}/{arch['name']}"), arch["size"], arch["sha256"], not sample)
    log(f"{arch['name']}: " + ("sha256 ok" if not sample else "size ok (--sample: not hashed)"))
    return len(shas)


# --------------------------------------------------------------------------------------------------------------------- cli
def main(argv=None) -> int:
    cfg = release_config.early_config(argv)
    configure(cfg)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    release_config.add_config_arg(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    chans = list(CHANNELS)

    def common(p):
        p.add_argument("--target", help=f"dir:<path> | sftp://user@host[:port]/path (default: ${release_config.ENV_TARGET}, "
                                        "then site.target)")
        p.add_argument("--identity", help="ssh private key for sftp targets (default site.identity)")

    p = sub.add_parser("publish", help="upload a package_release output for the server to publish")
    common(p)
    p.add_argument("--version", required=True)
    p.add_argument("--channel", choices=chans, default=cfg["site"]["publish_channel"],
                   help="channel the server moves to this version, last (default: %(default)s)")
    p.add_argument("--no-channel", action="store_true", help="upload only; promote later")
    p.add_argument("--dist", type=Path, default=Path(cfg["package"]["dist"]), help="package_release.py output folder (default: %(default)s)")
    p.add_argument("--stage", type=Path, help="read file bytes from this mod folder instead of the 7z")
    p.add_argument("--work", type=Path, help="where the objects 7z is built and kept (default: <dist>/upload/<version>)")
    p.add_argument("--downloader", type=Path, help=f"{cfg['downloader']['exe_name']} to publish with this release")
    p.add_argument("--downloader-version", help="default: the exe's own version resource (must agree when given)")
    p.add_argument("--allow-incomplete", action="store_true", help="accept an --incomplete package (test channel / --no-channel only)")
    p.add_argument("--force", action="store_true", help="replace a waiting or refused upload, or a published version")
    p.add_argument("--retries", type=int, default=RETRIES, help=f"tries again after a dropped connection (default {RETRIES})")

    p = sub.add_parser("promote", help="point a channel at a published version")
    common(p)
    p.add_argument("version")
    p.add_argument("--channel", choices=chans, default=cfg["site"]["promote_channel"], help="default: %(default)s")

    p = sub.add_parser("list", help="channels, releases and waiting uploads on the host")
    common(p)

    p = sub.add_parser("gc", help="remove old releases and the objects only they used")
    common(p)
    p.add_argument("--keep", type=int, required=True, help="newest N versions to keep (channel versions are always kept)")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("verify", help="fetch channel, manifest, objects and the 7z over HTTP(S) and check the hashes")
    p.add_argument("--url", default=cfg["site"]["url"], help="site root (default site.url: %(default)s)")
    p.add_argument("--channel", choices=chans, default="stable")
    p.add_argument("--sample", type=int, default=0, help="check only N objects spread over the set (default: all)")
    p.add_argument("--expect-version", help="fail unless the channel names this version")
    p.add_argument("--wait", type=int, default=0, help="with --expect-version: wait up to N seconds for the channel to show it")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "verify":
            verify_url(a.url, a.channel, a.sample, expect_version=a.expect_version, wait=a.wait)
            return 0
        target = open_target(a.target, a.identity or cfg["site"]["identity"] or None, cfg)
        if a.cmd == "publish":
            source = DirSource(a.stage) if a.stage else None
            channel = None if a.no_channel else a.channel
            publish(target, a.dist, a.version, channel, source, a.downloader, a.downloader_version,
                    a.allow_incomplete, a.force, a.retries, a.work)
        elif a.cmd == "promote":
            promote(target, a.version, a.channel)
        elif a.cmd == "list":
            list_cmd(target)
        elif a.cmd == "gc":
            gc(target, a.keep, a.dry_run)
        if isinstance(target, SftpTarget):
            print(f"({target.logins} sftp logins)")
    except (PublishError, urllib.error.URLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
