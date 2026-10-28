"""Package a finished mod folder as a player release: a .7z for manual installs and the manifest the downloader reads.

The script does not build anything. You give it the finished mod folder (the folder that goes into <D2R>\\mods\\<folder>)
and it writes into dist/:

  <Folder>-<version>.7z        the archive for manual installs: solid LZMA2 7z (-mx7) with the mod folder at the top,
                               no time stamps and fixed file times, so it is reproducible with a given 7-Zip build.
                               7-Zip is required; without it the run stops before it changes anything.
  release-manifest.json        schemaVersion 2: the release's metadata and every file with size and sha256
                               (docs/release.md "Release manifest")
  RELEASE_NOTES-<version>.md   a skeleton to fill in: requirements and install steps
  SHA256SUMS                   sha256 of the 7z(s), the manifest and the notes (sha256sum -c format)
  <Folder>-<version>-nexus.7z  with --nexus: the same mod folder plus <Folder>/INSTALL.txt (install steps for players who
                               download by hand or with a mod manager), for an upload to Nexus Mods (docs/distribution.md)

release-manifest.json, schemaVersion 2 (the publisher adds objectsBase and launcher, the server adds archive.path):

  schemaVersion  2                         name         mod.name              version   as given
  pluginVersion  --plugin-version (opt.)   released     date, SOURCE_DATE_EPOCH honoured
  complete       false for --incomplete    modFolder    mod.folder            saveFolder mod.save_folder
  d2r            {build, version[, imageTimestamp]}      the game build the release was made for
  d2rloader      {version, url, size, sha256, signerThumbprint}   the pinned loader (informational: never downloaded)
  launch         {default_mod, launch_arguments}         what the downloader writes into the loader config
  preserve       files a player edits; never overwritten after the first install (package.preserve)
  archive        {format: "7z", name, size, sha256}      the manual download (the server rebuilds it, same bytes)
  emptyDirs      folders that exist but hold no file     fileCount, totalSize   counts over files[]
  files          [{path, size, sha256}] sorted by path   path is relative to the mod folder, "/" separators
  launcherMin    downloader.min_version (opt.)           older downloaders are told to update first

Nothing else goes in: no test harness files, saves or logs (the run stops if one is in the folder; the patterns are
package.forbidden in the config). The folder you give is never changed: it is copied to dist/stage/ first.

  py -3 release/package_release.py --mod-dir path/to/MyMod --version 1.0.0 [--config release.toml]
        [--plugin build/mymod-core.dll [--plugin-version 1.0.0]] [--game-extract DIR] [--incomplete] [--nexus] [--out dist]

--plugin DLL        copy a plugin DLL into <mod>/<package.plugin_dir>/ (it is checked: a DLL that contains one of
                    package.plugin_forbidden_strings is refused, and with --plugin-version it must carry that version text)
--game-extract DIR  the game's own files, extracted (DIR/data/... or DIR/...): a mod file that is a byte copy of the
                    same path there is left out of the release, because the game reads its own copy anyway
--incomplete        a CI package without the game data: the manifest says complete=false and the 7z is named
                    <Folder>-<version>-incomplete.7z. Publishing it to the stable channel is refused.
--nexus             also write <Folder>-<version>-nexus.7z: players extract it into <D2R>\\mods\\ and read INSTALL.txt
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_config  # noqa: E402

SCHEMA = 2  # 2: the 7z ("archive") replaced the zip; the downloader installs from it or from the objects
STAMP = (2026, 1, 1, 0, 0, 0)  # the staged files' mtime
# 7z: LZMA2, one solid block, multithreaded. No time stamps go in the archive (-mtm/-mtc/-mta off) and the staged files get
# STAMP as mtime anyway; the same input gives the same bytes with the same 7-Zip build.
SEVENZ_ARGS = ["a", "-t7z", "-m0=lzma2", "-mx=7", "-ms=on", "-mmt=on", "-mtm=off", "-mtc=off", "-mta=off", "-bd", "-y"]
SEVENZ_NAMES = ("7z", "7zz", "7za", "7zr")
NO_7Z = ("no 7-Zip found (Program Files\\7-Zip\\7z.exe, or 7z / 7zz / 7za / 7zr on PATH). A release is a .7z only: install "
         "7-Zip (https://www.7-zip.org; Debian / Ubuntu: apt install 7zip) and run again")

FORBIDDEN = re.compile("|".join(f"(?:{p})" for p in release_config.DEFAULT_FORBIDDEN), re.I)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def forbidden_regex(patterns=None):
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I) if patterns else FORBIDDEN


def forbidden_files(mod: Path, patterns=None) -> list:
    """Paths (relative, '/') in the folder that must not ship. `patterns`: regular expressions, default the built-in list."""
    rx = forbidden_regex(patterns)
    return [p.relative_to(mod).as_posix() for p in sorted(mod.rglob("*")) if p.is_file() and rx.search(p.relative_to(mod).as_posix())]


def file_list(mod: Path) -> list:
    return [{"path": p.relative_to(mod).as_posix(), "size": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(mod.rglob("*"), key=lambda q: q.relative_to(mod).as_posix()) if p.is_file()]


def empty_dirs(mod: Path) -> list:
    """Folders with nothing in them (for example d2rloader/patches): listed in the manifest, so the server builds the same 7z."""
    return sorted(p.relative_to(mod).as_posix() for p in mod.rglob("*") if p.is_dir() and not any(p.iterdir()))


def find_7z():
    """The 7-Zip command line: 7-Zip\\7z.exe under Program Files, else 7z / 7zz / 7za / 7zr on PATH. None when there is none."""
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        exe = Path(base) / "7-Zip" / "7z.exe" if base else None
        if exe and exe.is_file():
            return str(exe)
    for name in SEVENZ_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def fix_mtimes(mod: Path) -> None:
    """Every file and folder of the staged copy gets STAMP as its mtime."""
    t = time.mktime(STAMP + (0, 0, -1))
    for p in [mod, *mod.rglob("*")]:
        os.utime(p, (t, t))


def write_7z(mod: Path, out: Path, sevenzip=None) -> None:
    """Solid LZMA2 7z of the folder, its own name as the top folder (empty folders kept). Sets the folder's file times to
    STAMP. Raises RuntimeError when there is no 7-Zip or it fails; nothing is left behind then."""
    mod, out = Path(mod), Path(out)
    exe = sevenzip or find_7z()
    if not exe:
        raise RuntimeError(NO_7Z)
    fix_mtimes(mod)
    out.unlink(missing_ok=True)  # `7z a` would add to an existing archive
    r = subprocess.run([exe, *SEVENZ_ARGS, str(out.resolve()), mod.name], cwd=mod.parent, capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        raise RuntimeError(f"7-Zip failed ({r.returncode}): {(r.stdout + r.stderr).strip()[-600:]}")


def check_release_plugin(dll: Path, version=None, forbidden_strings=()) -> list:
    """Problems of a plugin DLL for a release: none of `forbidden_strings` inside it (a test harness must be compiled
    out), and, when `version` is given, that version text followed by a NUL byte (the plugin carries its own version)."""
    b = Path(dll).read_bytes()
    problems = []
    for s in forbidden_strings:
        if s.encode() in b:
            problems.append(f"{dll} contains {s!r} (a release build must compile the test harness out)")
    if version and version.encode() + b"\0" not in b:
        problems.append(f"{dll} does not carry the version {version}")
    return problems


def drop_game_copies(mod: Path, extract: Path, data_dir: str, keep=()) -> int:
    """Remove mod files that are byte copies of the game's own: same path under `extract` (`extract/data` when it has a
    data folder). `data_dir` is where those files sit inside the mod folder. -> number removed."""
    data = mod.joinpath(*data_dir.split("/"))
    base = extract / "data" if (extract / "data").is_dir() else extract
    if not base.is_dir() or not data.is_dir():
        return 0
    keep = {k.lower() for k in keep}
    n = 0
    for p in sorted(data.rglob("*")):
        rel = p.relative_to(data).as_posix()
        if not p.is_file() or rel.lower() in keep:
            continue
        twin = base.joinpath(*rel.split("/"))
        if twin.is_file() and twin.stat().st_size == p.stat().st_size and twin.read_bytes() == p.read_bytes():
            p.unlink()
            n += 1
    return n


def stage(mod_dir: Path, stage_dir: Path, plugin: Path = None, plugin_dir="d2rloader/plugins") -> Path:
    """Copy the finished mod folder to stage_dir (named like the mod folder), then the plugin DLL in. -> the staged folder."""
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    shutil.copytree(mod_dir, stage_dir)
    if plugin is not None:
        dest = stage_dir.joinpath(*plugin_dir.split("/"))
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plugin, dest / plugin.name)
    return stage_dir


def archive_name(cfg: dict, version: str, complete=True) -> str:
    return f"{cfg['mod']['folder']}-{version}{'' if complete else '-incomplete'}.7z"


def manifest(cfg: dict, version: str, files: list, archive_path: Path, complete: bool, dirs=(), plugin_version="") -> dict:
    """The release manifest, schemaVersion 2. archive_path: the .7z. The downloader installs from the objects
    (docs/release.md), the 7z is the manual download. publish_release.py adds objectsBase and launcher; the server's
    ingest adds archive.path and replaces archive by the 7z it builds."""
    mod, game = cfg["mod"], cfg["game"]
    d2r = {"build": str(game["build"]), "version": game["version"]}
    if game.get("image_timestamp"):
        d2r["imageTimestamp"] = game["image_timestamp"]
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    released = (datetime.datetime.fromtimestamp(int(epoch), datetime.timezone.utc).date() if epoch else datetime.date.today())
    man = {
        "schemaVersion": SCHEMA,
        "name": mod["name"],
        "version": version,
        "released": released.isoformat(),
        "complete": complete,  # false: an --incomplete build (no game data); not for players
        "d2r": d2r,
        "d2rloader": dict(cfg["d2rloader"]),
        "launch": dict(cfg["launch"]),
        "modFolder": mod["folder"],  # the paths below are relative to <D2R>/mods/<modFolder>/
        "saveFolder": mod["save_folder"],
        "preserve": list(cfg["package"]["preserve"]),
        "archive": {"format": "7z", "name": archive_path.name, "size": archive_path.stat().st_size,
                    "sha256": sha256(archive_path)},  # the manual download
        "emptyDirs": list(dirs),  # folders the 7z holds although no file is in them
        "fileCount": len(files),
        "totalSize": sum(f["size"] for f in files),
        "files": files,
    }
    if plugin_version:
        man["pluginVersion"] = plugin_version
    if cfg["downloader"].get("min_version"):
        man["launcherMin"] = cfg["downloader"]["min_version"]
    return man


UNPACK_7Z = ("Unpack `{archive}` into `<D2R folder>\\mods\\` so that `<D2R folder>\\mods\\{folder}\\{folder}.mpq\\modinfo.json` "
             "exists\n   (Windows 11 opens .7z files; otherwise use 7-Zip, https://www.7-zip.org).")

NOTES = """# {name} {version}

{name} for Diablo II: Resurrected. TODO: one sentence about what this release is.

## Requirements

- Diablo II: Resurrected **build {build}** (D2R {d2rver}).
- D2RLoader **{loader}** ({loader_url}).

## Install

1. Install D2RLoader {loader} into your D2R folder (build {build}).
2. Run `{exe}` ({site}) and choose **Install** (or **Update**). The mod goes into
   `<D2R folder>\\mods\\{folder}`; nothing else is changed.
3. Make {folder} D2RLoader's default mod (once): in D2RLoader's settings, or with
   `default_mod = "{folder}"` in `<D2R folder>\\d2rloader\\config\\d2rloader.toml`. Then start `D2RLoader.exe`.

By hand (offline), into a D2R folder at build {build} that has D2RLoader {loader}:

1. {unpack}
2. Make {folder} the default mod, as in step 3 above.
3. Start `D2RLoader.exe`. Saves go to `Saved Games\\Diablo II Resurrected\\mods\\{folder}\\`.
   To update by hand, remove `mods\\{folder}\\{folder}.mpq\\data` before unpacking the new release, and keep
   `mods\\{folder}\\d2rloader\\config\\d2rloader.toml` if you edited it.

## Changes

- TODO

## Known issues

- TODO

## Checksums

See SHA256SUMS.
"""


def release_notes(cfg: dict, version: str, archive_name_: str) -> str:
    mod = cfg["mod"]
    return NOTES.format(
        name=mod["name"], folder=mod["folder"], version=version, build=cfg["game"]["build"], d2rver=cfg["game"]["version"],
        loader=cfg["d2rloader"]["version"], loader_url=cfg["d2rloader"]["url"], exe=cfg["downloader"]["exe_name"],
        site=cfg["site"]["url"], unpack=UNPACK_7Z.format(archive=archive_name_, folder=mod["folder"]))


NEXUS_INSTALL = r"""{head}
{rule}

Requirements
- Diablo II: Resurrected {d2rver} (made for build {build}).
- {loader_line}

Install
1. Close the game.
2. Extract this archive into <D2R folder>\mods\ (for example
   C:\Program Files (x86)\Diablo II Resurrected\mods\). Afterwards this file is
   <D2R folder>\mods\{folder}\INSTALL.txt and <D2R folder>\mods\{folder}\{folder}.mpq\modinfo.json exists.
   If you see mods\{folder}\{folder}\, you extracted one folder too deep: move it up one level.
3. {start}

Update
- Delete <D2R folder>\mods\{folder}\{folder}.mpq\data, then extract the new version the same way.
  Keep mods\{folder}\d2rloader\config\d2rloader.toml if you changed it.

Uninstall
- Delete <D2R folder>\mods\{folder}\. Your characters stay in
  Saved Games\Diablo II Resurrected\mods\{folder}\.

Modded games are offline games: do not use the mod on Battle.net.
"""

LOADER_NEEDED = "D2RLoader {loader} ({loader_url}): this mod ships an engine plugin and does not run without it."
LOADER_OPTIONAL = "D2RLoader is not needed: this mod is data only."
START_LOADER = ("Start D2RLoader.exe in your D2R folder. The first time, click the gear on the character screen, set\n"
                "   Default Mod to {folder}, Apply, and start D2RLoader again (its first start builds a cache).")
START_PLAIN = ("Start the game with the arguments -mod {folder} -txt (Battle.net: Game Settings, Additional command\n"
               "   line arguments; Steam: Properties, Launch Options).")


def has_plugin(mod: Path, plugin_dir="d2rloader/plugins") -> bool:
    """True when the staged mod folder holds a plugin DLL, i.e. players need D2RLoader."""
    d = Path(mod).joinpath(*plugin_dir.split("/"))
    return d.is_dir() and any(p.suffix.lower() == ".dll" for p in d.iterdir())


def nexus_install_text(cfg: dict, version: str, plugin: bool) -> str:
    """INSTALL.txt of the Nexus archive (CRLF, for Notepad): requirements, install, update and uninstall steps. It does not
    mention the downloader: Nexus Mods does not allow executables that download updates (docs/distribution.md)."""
    mod, loader = cfg["mod"], cfg["d2rloader"]
    head = f"{mod['name']} {version} for Diablo II: Resurrected"
    text = NEXUS_INSTALL.format(
        head=head, rule="=" * len(head), d2rver=cfg["game"]["version"], build=cfg["game"]["build"], folder=mod["folder"],
        loader_line=(LOADER_NEEDED if plugin else LOADER_OPTIONAL).format(loader=loader["version"], loader_url=loader["url"]),
        start=(START_LOADER if plugin else START_PLAIN).format(folder=mod["folder"]))
    return text.replace("\n", "\r\n")


def nexus_archive_name(cfg: dict, version: str) -> str:
    return f"{cfg['mod']['folder']}-{version}-nexus.7z"


def write_nexus_7z(mod: Path, out: Path, cfg: dict, version: str, plugin_dir="d2rloader/plugins", sevenzip=None) -> None:
    """The Nexus Mods archive: the staged mod folder as the top folder, plus <folder>/INSTALL.txt. The INSTALL.txt is
    removed from the stage again, so the manifest and the main 7z never see it."""
    readme = Path(mod) / "INSTALL.txt"
    readme.write_bytes(nexus_install_text(cfg, version, has_plugin(mod, plugin_dir)).encode("utf-8"))
    try:
        write_7z(mod, out, sevenzip)
    finally:
        readme.unlink(missing_ok=True)


def main(argv=None) -> int:
    cfg = release_config.early_config(argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    release_config.add_config_arg(ap)
    ap.add_argument("--mod-dir", type=Path, required=True, help="the finished mod folder (what goes into <D2R>/mods/<folder>)")
    ap.add_argument("--version", required=True, help="release version, e.g. 1.0.0 or 1.0.0-rc1")
    ap.add_argument("--plugin", type=Path, help="a release plugin DLL to copy into the mod folder (package.plugin_dir)")
    ap.add_argument("--plugin-version", default="", help="the plugin's own version: must be inside the DLL; goes in the manifest")
    ap.add_argument("--game-extract", type=Path, help="the game's own extracted files: byte copies are left out")
    ap.add_argument("--incomplete", action="store_true", help="a package without game data (CI): complete=false, not for players")
    ap.add_argument("--nexus", action="store_true", help="also write <Folder>-<version>-nexus.7z with INSTALL.txt (Nexus Mods)")
    ap.add_argument("--out", type=Path, help=f"output folder (default package.dist = {cfg['package']['dist']})")
    a = ap.parse_args(argv)

    version = a.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        print(f"error: bad version {version!r} (expected 1.2.3 or 1.2.3-rc1)")
        return 1
    folder, pkg = cfg["mod"]["folder"], cfg["package"]
    sevenzip = find_7z()
    if not sevenzip:  # before the long part of the run
        print(f"error: {NO_7Z}")
        return 1
    if not a.mod_dir.is_dir() or not any(a.mod_dir.rglob("*")):
        print(f"error: {a.mod_dir} is not a mod folder with files in it")
        return 1
    if not any(a.mod_dir.glob("*.mpq/modinfo.json")) and not (a.mod_dir / "modinfo.json").is_file():
        print(f"warning: {a.mod_dir} has no <name>.mpq/modinfo.json; D2R will not load it as a mod")
    if a.plugin:
        if not a.plugin.is_file():
            print(f"error: no plugin at {a.plugin}")
            return 1
        problems = check_release_plugin(a.plugin, a.plugin_version or None, pkg["plugin_forbidden_strings"])
        if problems:
            print("\n".join(problems))
            return 1

    out = a.out or Path(pkg["dist"])
    out.mkdir(parents=True, exist_ok=True)
    mod = stage(a.mod_dir, out / "stage" / folder, a.plugin, pkg["plugin_dir"])
    if a.game_extract:
        dropped = drop_game_copies(mod, a.game_extract, pkg["data_dir"].format(folder=folder), pkg["keep_identical"])
        if dropped:
            print(f"left out {dropped} files that are byte copies of the game's own ({a.game_extract})")
    bad = forbidden_files(mod, pkg["forbidden"])
    if bad:
        print("refusing to package test or player files: " + ", ".join(bad[:20]))
        shutil.rmtree(out / "stage")
        return 1

    complete = not a.incomplete
    for old in out.glob(f"{folder}-*.7z"):  # an earlier release must not sit next to this one
        old.unlink()
    seven_path = out / archive_name(cfg, version, complete)
    t7 = time.time()
    try:
        write_7z(mod, seven_path, sevenzip)
    except RuntimeError as e:
        print(f"error: {e}")
        shutil.rmtree(out / "stage", ignore_errors=True)
        return 1
    t7 = time.time() - t7
    files = file_list(mod)
    sums = [seven_path]
    if a.nexus:
        nexus_path = out / nexus_archive_name(cfg, version)
        try:
            write_nexus_7z(mod, nexus_path, cfg, version, pkg["plugin_dir"], sevenzip)
        except RuntimeError as e:
            print(f"error: {e}")
            shutil.rmtree(out / "stage", ignore_errors=True)
            return 1
        sums.append(nexus_path)
    man_path = out / "release-manifest.json"
    man_path.write_text(json.dumps(manifest(cfg, version, files, seven_path, complete, empty_dirs(mod), a.plugin_version),
                                   indent=1) + "\n", encoding="utf-8", newline="\n")
    notes = out / f"RELEASE_NOTES-{version}.md"
    notes.write_text(release_notes(cfg, version, seven_path.name), encoding="utf-8", newline="\n")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in (*sums, man_path, notes)),
                                    encoding="utf-8", newline="\n")
    shutil.rmtree(out / "stage")
    print(f"{seven_path.name}: {len(files)} files, {sum(f['size'] for f in files) / 1e6:.0f} MB unpacked, "
          f"{seven_path.stat().st_size / 1e6:.0f} MB as 7z ({t7:.0f} s); {man_path.name}, {notes.name}, SHA256SUMS"
          f"{', ' + sums[1].name if a.nexus else ''} in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
