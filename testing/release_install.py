#!/usr/bin/env python
"""Test what players get: put the PUBLISHED release into a game slot, exactly as a player would have it.

  python testing/release_install.py [--config F] check   [--channel C] [--site S]
  python testing/release_install.py [--config F] install --slot N [--channel C] [--site S]
                                    [--method auto|archive|objects] [--harness-dll FILE ...]
  python testing/release_install.py [--config F] restore --slot N
  python testing/release_install.py [--config F] status  --slot N

The site (release.site_url or --site) is the same static layout the downloader reads:

  channels/<channel>.json          {"schemaVersion", "version", "manifest", "manifestSha256"}
  <manifest path>                  files[{path,size,sha256}], objectsBase, archive{path,size,sha256}, launch, ...
  <objectsBase><sha[0:2]>/<sha>    the bytes of one file, named by its sha256
  <archive.path>                   the whole mod folder as one 7z (top folder = modFolder)

--site can be https://host/path/, dir:<folder> or file:///folder (a local copy, for tests without a network).

install: needs the slot lock held by MODKIT_RUN, never touches slot 1, refuses while a game runs in the slot. It
downloads and verifies everything first (every sha256) before it changes the slot, then moves the slot's mod folder
aside (<slot>.release-backup), puts the release in its place, points modinfo at the slot's own save folder and writes
the release's launch keys (+ release.extra_launch_args) into the slot's loader config. Only that slot's mod folder is
replaced. `restore` puts the backup back. --harness-dll copies a harness-enabled build of a plugin over the release's
(release builds usually compile the harness out); the release's own file is kept in the state folder.

method: archive = one 7z (needs 7-Zip), objects = file by file (pure Python), auto = archive when 7-Zip is found.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import game_slots  # noqa: E402
import testing_config as tc  # noqa: E402

UA = "modkit-release-install"


class ReleaseError(Exception):
    """The release cannot be fetched, verified or installed (message is shown as is)."""


# ---------------------------------------------------------------- small helpers


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def safe_rel(p: str) -> str:
    """A relative path from a manifest, checked: no drive, no absolute path, no '..', no backslash."""
    if (not isinstance(p, str) or not p or p.startswith("/") or "\\" in p or re.match(r"^[A-Za-z]:", p)
            or any(part in ("..", "") for part in p.split("/"))):
        raise ReleaseError(f"unsafe path in the release: {p!r}")
    return p


def valid_sha(s) -> bool:
    return isinstance(s, str) and re.fullmatch(r"[0-9a-f]{64}", s) is not None


def find_7z(cfg: tc.Config | None = None) -> str | None:
    cands = []
    if cfg is not None and cfg["release"]["seven_zip"]:
        cands.append(cfg["release"]["seven_zip"])
    for name in ("7z", "7zz", "7za", "7zr"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    cands.append(r"C:\Program Files\7-Zip\7z.exe")
    return next((c for c in cands if c and os.path.isfile(c)), None)


# ---------------------------------------------------------------- the site


class Site:
    """Where the release lives: a web address or a local folder. Only reads."""

    def __init__(self, spec: str):
        if not spec:
            raise ReleaseError("no release site: set release.site_url in the config or pass --site")
        self.spec = spec
        if spec.startswith("dir:"):
            self.root = os.path.abspath(spec[4:])
        elif spec.lower().startswith("file:"):
            self.root = os.path.abspath(urllib.request.url2pathname(urllib.parse.urlparse(spec).path))
        elif re.match(r"^https?://", spec, re.I):
            self.root = None
            self.base = spec if spec.endswith("/") else spec + "/"
        elif os.path.isdir(spec):
            self.root = os.path.abspath(spec)
        else:
            raise ReleaseError(f"release site {spec!r} is not an http(s) address, dir:<folder> or file:// URL")
        if self.root is not None and not os.path.isdir(self.root):
            raise ReleaseError(f"release site folder {self.root} does not exist")

    @property
    def local(self) -> bool:
        return self.root is not None

    def fetch(self, rel: str, fresh: bool = False, timeout: float = 60) -> bytes:
        safe_rel(rel)
        if self.local:
            p = os.path.join(self.root, *rel.split("/"))
            try:
                with open(p, "rb") as f:
                    return f.read()
            except OSError as e:
                raise ReleaseError(f"not on the site: {rel} ({e.strerror})") from e
        url = self.base + urllib.parse.quote(rel)
        if fresh:  # the channel file may be cached by a CDN: a query string asks for the current one
            url += f"?t={int(time.time())}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            raise ReleaseError(f"download failed: {url} ({e})") from e

    def fetch_to(self, rel: str, dest: str, sha256: str | None = None, size: int | None = None,
                 retries: int = 3, sleep=time.sleep) -> str:
        """Download rel to dest (via dest.part), check size and sha256, retry a few times."""
        last = ""
        for attempt in range(max(retries, 1)):
            part = dest + ".part"
            try:
                os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
                if self.local:
                    shutil.copyfile(os.path.join(self.root, *safe_rel(rel).split("/")), part)
                else:
                    with open(part, "wb") as f:
                        f.write(self.fetch(rel, timeout=300))
                if size is not None and os.path.getsize(part) != size:
                    raise ReleaseError(f"{rel}: size {os.path.getsize(part)}, expected {size}")
                if sha256 and sha256_file(part) != sha256:
                    raise ReleaseError(f"{rel}: sha256 does not match")
                os.replace(part, dest)
                return dest
            except (ReleaseError, OSError) as e:
                last = str(e)
                if os.path.exists(part):
                    os.remove(part)
                sleep(min(2 ** attempt, 8) if not self.local else 0)
        raise ReleaseError(f"giving up on {rel}: {last}")


# ---------------------------------------------------------------- channel and manifest


def read_channel(site: Site, channel: str, cfg: tc.Config) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", channel or ""):
        raise ReleaseError(f"bad channel name {channel!r}")
    try:
        ch = json.loads(site.fetch(f"channels/{channel}.json", fresh=True))
    except ValueError as e:
        raise ReleaseError(f"channel file channels/{channel}.json is not JSON: {e}") from e
    want = cfg["release"]["channel_schema"]
    if want and ch.get("schemaVersion") != want:
        raise ReleaseError(f"channel schemaVersion {ch.get('schemaVersion')!r}, this tool reads {want}")
    for k in ("manifest", "manifestSha256"):
        if k not in ch:
            raise ReleaseError(f"channel file has no {k!r}")
    safe_rel(ch["manifest"])
    return ch


def read_manifest(site: Site, ch: dict, cfg: tc.Config) -> dict:
    raw = site.fetch(ch["manifest"])
    if sha256_bytes(raw) != ch["manifestSha256"]:
        raise ReleaseError("the manifest does not match the channel's sha256 (stale cache or half-published release)")
    try:
        man = json.loads(raw)
    except ValueError as e:
        raise ReleaseError(f"manifest is not JSON: {e}") from e
    want = cfg["release"]["manifest_schema"]
    if want and man.get("schemaVersion") != want:
        raise ReleaseError(f"manifest schemaVersion {man.get('schemaVersion')!r}, this tool reads {want}")
    if man.get("complete") is False:
        raise ReleaseError("the manifest says complete=false (a partial package): never install it for a player")
    files = man.get("files")
    if not isinstance(files, list) or not files:
        raise ReleaseError("the manifest lists no files")
    for f in files:
        safe_rel(f.get("path"))
        if not valid_sha(f.get("sha256")) or not isinstance(f.get("size"), int):
            raise ReleaseError(f"bad file entry in the manifest: {f.get('path')!r}")
    for d in man.get("emptyDirs", []):
        safe_rel(d)
    if man.get("version") != ch.get("version", man.get("version")):
        raise ReleaseError(f"channel says version {ch.get('version')}, manifest says {man.get('version')}")
    return man


def object_rel(man: dict, sha: str) -> str:
    base = man.get("objectsBase")
    if not base:
        raise ReleaseError("the manifest has no objectsBase: file-by-file download is not possible")
    return f"{base.rstrip('/')}/{sha[:2]}/{sha}"


# ---------------------------------------------------------------- staging


def verify_tree(man: dict, folder: str) -> list[str]:
    """Paths of manifest files that are missing or differ (size, then sha256) under folder."""
    bad = []
    for f in man["files"]:
        p = os.path.join(folder, *f["path"].split("/"))
        if not os.path.isfile(p) or os.path.getsize(p) != f["size"] or sha256_file(p) != f["sha256"]:
            bad.append(f["path"])
    return bad


def stage_objects(site: Site, man: dict, folder: str, only: list[str] | None = None, retries: int = 3,
                  log=print) -> int:
    """Download the files (all, or `only` these paths) object by object into folder. Returns files written."""
    wanted = [f for f in man["files"] if only is None or f["path"] in set(only)]
    cache: dict[str, str] = {}
    for f in wanted:
        dest = os.path.join(folder, *f["path"].split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if f["sha256"] in cache:
            shutil.copyfile(cache[f["sha256"]], dest)
            continue
        site.fetch_to(object_rel(man, f["sha256"]), dest, f["sha256"], f["size"], retries)
        cache[f["sha256"]] = dest
    for d in man.get("emptyDirs", []):
        os.makedirs(os.path.join(folder, *d.split("/")), exist_ok=True)
    return len(wanted)


def stage_archive(site: Site, man: dict, seven: str, stage: str, work: str, retries: int = 3, log=print) -> None:
    """Fetch the 7z, check it, unpack it into `stage` (it contains <modFolder>/...)."""
    a = man.get("archive")
    if not a or not a.get("path"):
        raise ReleaseError("the manifest has no archive")
    arc = os.path.join(work, os.path.basename(safe_rel(a["path"])))
    log(f"downloading {a['path']} ({a.get('size', '?')} bytes)")
    site.fetch_to(a["path"], arc, a.get("sha256"), a.get("size"), retries)
    log("unpacking with 7-Zip")
    r = subprocess.run([seven, "x", "-y", "-bd", "-bso0", "-bsp0", f"-o{stage}", arc], capture_output=True, text=True)
    if r.returncode != 0:
        raise ReleaseError(f"7-Zip failed ({r.returncode}): {(r.stdout + r.stderr).strip()[-400:]}")


# ---------------------------------------------------------------- slot checks and state


def state_file(cfg: tc.Config, n: int) -> str:
    return os.path.join(cfg.install_state_dir(n), "release-install.json")


def backup_dir(cfg: tc.Config, n: int) -> str:
    return os.path.normpath(cfg.slot_dir(n)) + ".release-backup"


def check_slot(cfg: tc.Config, n: int, owner: str | None = None, env: dict | None = None) -> int:
    """Slot 1 never; the slot lock must be held by MODKIT_RUN; no game running in the slot."""
    n = cfg.check_slot_number(n)
    if cfg.is_slot_one(n):
        raise ReleaseError(game_slots.SLOT1_MSG)
    owner = owner or tc.env_run(env)
    info = game_slots.lock_info(cfg, n)
    if not owner or info is None or info.owner != owner:
        raise ReleaseError(f"slot {n} is not locked by MODKIT_RUN={owner!r}: "
                           f"python testing/game_slots.py lock {n} <lane>, then MODKIT_RUN=<lane>")
    if game_slots.slot_processes(cfg, n):
        raise ReleaseError(f"slot {n} still has a running game: stop it first")
    return n


def load_release(cfg: tc.Config, site: Site, channel: str) -> tuple[dict, dict]:
    ch = read_channel(site, channel, cfg)
    man = read_manifest(site, ch, cfg)
    if man.get("modFolder", cfg.mod_folder()) != cfg.mod_folder():
        raise ReleaseError(f"the release's modFolder is {man.get('modFolder')!r} but mod.folder is {cfg.mod_folder()!r}")
    return ch, man


def stage_release(cfg: tc.Config, site: Site, man: dict, stage_root: str, method: str = "auto", seven: str | None = None,
                  log=print) -> str:
    """Download + unpack + verify the release into stage_root/<modFolder>. Returns that folder. Touches no slot."""
    retries = cfg["release"]["retries"]
    folder = os.path.join(stage_root, cfg.mod_folder())
    if os.path.exists(stage_root):
        shutil.rmtree(stage_root)
    os.makedirs(stage_root)
    seven = seven if seven is not None else find_7z(cfg)
    if method == "auto":
        method = "archive" if (seven and man.get("archive")) else "objects"
    if method == "archive":
        if not seven:
            raise ReleaseError("method archive needs 7-Zip (release.seven_zip, PATH, or C:\\Program Files\\7-Zip)")
        with tempfile.TemporaryDirectory(dir=stage_root) as work:
            stage_archive(site, man, seven, stage_root, work, retries, log)
        bad = verify_tree(man, folder)
        if bad:
            log(f"{len(bad)} file(s) missing or wrong after unpacking: fetching them one by one")
            stage_objects(site, man, folder, bad, retries, log)
    else:
        log(f"downloading {len(man['files'])} files object by object")
        stage_objects(site, man, folder, None, retries, log)
        for d in man.get("emptyDirs", []):
            os.makedirs(os.path.join(folder, *d.split("/")), exist_ok=True)
    bad = verify_tree(man, folder)
    if bad:
        raise ReleaseError(f"{len(bad)} file(s) still wrong after download, first: {bad[0]}")
    return folder


# ---------------------------------------------------------------- install / restore / status


def install(cfg: tc.Config, n: int, channel: str | None = None, site_spec: str | None = None, method: str = "auto",
            harness_dlls: list[str] | None = None, owner: str | None = None, log=print, seven: str | None = None) -> dict:
    n = check_slot(cfg, n, owner)
    channel = channel or cfg["release"]["channel"]
    site = Site(site_spec or cfg["release"]["site_url"])
    ch, man = load_release(cfg, site, channel)
    log(f"release {man.get('version')} on channel {channel}")
    state, bak, mod = cfg.install_state_dir(n), backup_dir(cfg, n), cfg.mod_dir(n)
    os.makedirs(state, exist_ok=True)
    # everything is downloaded and verified before the slot changes: a failure here changes nothing
    staged = stage_release(cfg, site, man, os.path.join(state, "stage"), method, seven, log)
    slot_mods = os.path.join(cfg.slot_dir(n), "mods")
    if not game_slots.is_within(mod, slot_mods) or os.path.normcase(os.path.abspath(mod)) == os.path.normcase(
            os.path.abspath(slot_mods)):
        raise ReleaseError(f"refusing to replace {mod}: it is not a folder inside the slot's mods folder")
    sf = state_file(cfg, n)
    cfg_toml = cfg.loader_config_path(n)
    if os.path.exists(sf):
        log("the slot already holds a release install: its backup stays, the release is installed again")
        shutil.rmtree(mod, ignore_errors=True)
    else:
        if os.path.exists(bak):
            raise ReleaseError(f"{bak} exists but there is no install record: check what it holds, then delete it yourself")
        os.makedirs(bak)
        if os.path.isdir(mod):
            shutil.move(mod, os.path.join(bak, cfg.mod_folder()))
        if os.path.isfile(cfg_toml):
            shutil.copy2(cfg_toml, os.path.join(bak, "loader-config.bak"))
    os.makedirs(slot_mods, exist_ok=True)
    shutil.move(staged, mod)
    shutil.rmtree(os.path.join(state, "stage"), ignore_errors=True)
    changes: dict = {"version": man.get("version"), "channel": channel, "site": site.spec,
                     "at": time.strftime("%Y-%m-%d %H:%M:%S"), "plugins": []}
    for dll in harness_dlls or []:
        if not os.path.isfile(dll):
            raise ReleaseError(f"--harness-dll {dll}: no such file")
        target = os.path.join(cfg.plugins_dir(n), os.path.basename(dll))
        if os.path.isfile(target):
            shutil.copy2(target, os.path.join(state, os.path.basename(dll) + ".release"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(dll, target)
        changes["plugins"].append(os.path.basename(dll))
    changes["modinfo"] = game_slots.set_savepath(cfg, n)
    changes["savepath"] = cfg.save_name(n) + "/"
    launch = man.get("launch") or {}
    if cfg["release"]["apply_launch"] and os.path.isfile(cfg_toml):
        lk = cfg["loader"]
        if launch.get("default_mod"):
            game_slots.set_loader_value(cfg, n, lk["default_mod_key"], launch["default_mod"])
        args = " ".join(a for a in (launch.get("launch_arguments", ""), cfg["release"]["extra_launch_args"]) if a)
        game_slots.set_loader_value(cfg, n, lk["args_key"], args)
        changes["launch_arguments"] = args
    os.makedirs(cfg.save_dir(n), exist_ok=True)
    with open(sf, "w", encoding="utf-8", newline="\n") as f:
        json.dump(changes, f, indent=2)
        f.write("\n")
    log(json.dumps(changes, indent=2))
    return changes


def restore(cfg: tc.Config, n: int, owner: str | None = None, log=print) -> None:
    n = check_slot(cfg, n, owner)
    sf, bak, mod = state_file(cfg, n), backup_dir(cfg, n), cfg.mod_dir(n)
    if not os.path.exists(sf):
        raise ReleaseError(f"slot {n} holds no release install (no {sf})")
    shutil.rmtree(mod, ignore_errors=True)
    kept = os.path.join(bak, cfg.mod_folder())
    if os.path.isdir(kept):
        shutil.move(kept, mod)
    toml_bak = os.path.join(bak, "loader-config.bak")
    if os.path.isfile(toml_bak):
        shutil.copy2(toml_bak, cfg.loader_config_path(n))
    shutil.rmtree(bak, ignore_errors=True)
    os.remove(sf)
    log(f"slot {n}: the previous mod folder and loader config are back")


def status(cfg: tc.Config, n: int, log=print) -> str:
    n = cfg.check_slot_number(n)
    if cfg.is_slot_one(n):
        raise ReleaseError(game_slots.SLOT1_MSG)
    sf = state_file(cfg, n)
    text = open(sf, encoding="utf-8").read() if os.path.exists(sf) else f"slot {n}: no release install"
    log(text)
    return text


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tc.add_config_arg(ap)
    ap.add_argument("cmd", nargs="?", choices=("check", "install", "restore", "status"))
    ap.add_argument("--slot", type=int, help="slot number (or MODKIT_SLOT); slot 1 is refused")
    ap.add_argument("--channel")
    ap.add_argument("--site", help="https://host/path/, dir:<folder> or file:///folder (default release.site_url)")
    ap.add_argument("--method", choices=("auto", "archive", "objects"), default="auto")
    ap.add_argument("--harness-dll", action="append", default=[], metavar="FILE")
    a = ap.parse_args(argv)
    if not a.cmd:
        print(__doc__)
        return 0
    try:
        cfg = tc.load(a.config)
        if a.cmd == "check":
            site = Site(a.site or cfg["release"]["site_url"])
            ch, man = load_release(cfg, site, a.channel or cfg["release"]["channel"])
            print(f"release {man.get('version')}: {len(man['files'])} files, archive "
                  f"{'yes' if man.get('archive') else 'no'}, manifest {ch['manifest']} verified")
            return 0
        slot = a.slot or tc.env_slot()
        if slot is None:
            raise ReleaseError("give --slot N or set MODKIT_SLOT")
        if a.cmd == "install":
            install(cfg, slot, a.channel, a.site, a.method, a.harness_dll)
        elif a.cmd == "restore":
            restore(cfg, slot)
        else:
            status(cfg, slot)
        return 0
    except (ReleaseError, tc.ConfigError, game_slots.SlotError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
