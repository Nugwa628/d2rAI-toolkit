"""The site adapter of hdkit: every place, folder and lock rule comes from one config file, never from the code.

hdkit's modules ask this module (through `adapter()`) where things are: the game folder whose CASC storage is read, the
CASC cache, the art store for large outputs, the work folder for renders, the mod's classic files and built data, and
which lock files mean "a game run is in progress, keep the GPU and the game folder free".

Where the config is looked for:
  1. the environment variable HDKIT_CONFIG (a .toml or .json file)
  2. ./hdkit.toml in the current folder
  3. none: the defaults below (a work folder under the home folder, no game, no locks)
Copy `hdkit/hdkit.example.toml` to `hdkit.toml` and edit it. Relative paths are relative to the config file; `~` and
$ENV / %ENV% are expanded.

A project that wants its own rules can still put a module `hdkit_site` on sys.path: `adapter()` returns it instead of this
module, and it must offer the same functions (store_root, lock_globs, signoff_settings, casc_game_dir, casc_cache_dir,
casc_cache_gb, casc_seeds, casc_opener, game_dir_free; optional: work_root, mod_classic, d2r_extract, mod_data,
preset_subdir, rounds_dir, forbidden_roots).

This module only reads its config and does path math. It never writes a file and never touches a game.
"""
from __future__ import annotations

import copy
import glob
import importlib
import json
import os
import re
from pathlib import Path

ENV_CONFIG = "HDKIT_CONFIG"
DEFAULT_FILE = "hdkit.toml"

DEFAULTS: dict = {
    "paths": {
        "work": "~/hdkit-work",     # renders, map working copies, sign-off evidence, the CASC cache (default)
        "art_store": "",            # content-addressed store for large outputs ("" = none, outputs stay where written)
        "rounds": "",               # folder of review rounds for the map page (default <work>/rounds)
    },
    "game": {
        "dir": "",                  # a D2R folder whose CASC storage is read (best a test copy; read only, never written)
        "casc_cache": "",           # default <work>/cache/casc
        "casc_cache_gb": 4.0,
        "casc_seeds": [],           # read-only folders with game files extracted earlier (looked at before CASC)
        "casclib": "",              # CascLib library (CascLib.dll / libcasc.so); default: modkit's (MODKIT_CASCLIB)
    },
    "locks": {
        "globs": [],                # lock files that mean a game run holds the GPU / the game folder, e.g. ".../game.lock*"
    },
    "mod": {
        "classic": "",              # the mod's classic files: a folder holding data/global/tiles and data/global/excel
        "data": "",                 # the built mod's data folder (holding hd/...), e.g. <project>/out/data
        "d2r_extract": "",          # D2R's own extracted files (holding data/global/...); default modkit's d2r-data/<build>
        "preset_subdir": "",        # the build moves the mod's own map presets to hd/env/preset/<subdir>/<map>.json
    },
    "signoff": {},                  # hdkit.signoff settings (see hdkit/signoff/config.py), merged over its defaults
}

_cache: dict = {}


def _expand(s: str) -> str:
    s = os.path.expandvars(os.path.expanduser(str(s)))
    return re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", lambda m: os.environ.get(m.group(1), m.group(0)), s)


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def config_path(env=None, cwd=None) -> str | None:
    env = os.environ if env is None else env
    if env.get(ENV_CONFIG):
        return env[ENV_CONFIG]
    p = Path(cwd or os.getcwd()) / DEFAULT_FILE
    return str(p) if p.is_file() else None


def read_file(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if str(path).lower().endswith(".json"):
        return json.loads(text)
    import tomllib
    return tomllib.loads(text)


def load(path: str | None = None, refresh: bool = False) -> dict:
    """The merged config: DEFAULTS, then the file. `_base` holds the folder relative paths are resolved against."""
    path = path or config_path()
    key = path or ""
    if key in _cache and not refresh:
        return _cache[key]
    data = merge(DEFAULTS, read_file(path) if path else {})
    data["_base"] = str(Path(path).resolve().parent) if path else os.getcwd()
    data["_source"] = path
    _cache[key] = data
    return data


def _path(value, cfg=None) -> str:
    """'' stays ''; otherwise expanded and made absolute against the config file's folder."""
    if not value:
        return ""
    cfg = cfg or load()
    p = Path(_expand(value))
    return str(p if p.is_absolute() else Path(cfg["_base"]) / p)


def adapter():
    """A project's own `hdkit_site` module when one is importable, else this module."""
    try:
        return importlib.import_module("hdkit_site")
    except ImportError:
        import sys
        return sys.modules[__name__]


# ---- places --------------------------------------------------------------------------------------------------------------

def work_root() -> str:
    return _path(os.environ.get("HDKIT_WORK") or load()["paths"]["work"])


def store_root() -> str:
    return _path(load()["paths"]["art_store"])


def rounds_dir() -> str:
    return _path(load()["paths"]["rounds"]) or str(Path(work_root()) / "rounds")


def mod_classic() -> str:
    return _path(load()["mod"]["classic"])


def mod_data() -> str:
    return _path(load()["mod"]["data"])


def preset_subdir() -> str:
    return str(load()["mod"]["preset_subdir"] or "").strip("/")


def d2r_extract() -> str:
    """D2R's own extract: the config's, else modkit's cache d2r-data/<newest build> (python -m modkit fetches into it)."""
    p = _path(load()["mod"]["d2r_extract"])
    if p:
        return p
    root = Path(os.environ.get("MODKIT_CACHE") or Path(__file__).resolve().parents[1] / "d2r-data")
    builds = sorted((d for d in root.glob("*") if d.is_dir()), key=lambda d: d.name) if root.is_dir() else []
    return str(builds[-1]) if builds else ""


def forbidden_roots() -> list:
    """Folders a map working copy may never be made in or written to: the game folder, the CASC cache and seeds, the
    extracts and the art store (edits go to the work folder only)."""
    cfg = load()
    out = [_path(cfg["game"]["dir"]), casc_cache_dir(), store_root(), mod_classic(), d2r_extract()]
    out += [_path(s) for s in cfg["game"]["casc_seeds"]]
    return [p for p in out if p]


# ---- locks and the GPU gate ----------------------------------------------------------------------------------------------

def lock_globs() -> list:
    return [_path(g) for g in load()["locks"]["globs"] if g]


def locks_held() -> list:
    return [p for g in lock_globs() for p in glob.glob(g)]


# ---- in-game sign-off ----------------------------------------------------------------------------------------------------

def signoff_settings() -> dict:
    s = copy.deepcopy(load()["signoff"])
    s.setdefault("runs_root", str(Path(work_root()) / "signoff"))
    if store_root():
        s.setdefault("art_store", store_root())
    return s


# ---- CASC on demand (hdkit.view.casc.from_site) --------------------------------------------------------------------------

def casc_game_dir() -> str:
    g = os.environ.get("HDKIT_CASC_GAME") or _path(load()["game"]["dir"])
    if not g:
        raise RuntimeError("no game folder for CASC reads: set [game] dir in hdkit.toml (or HDKIT_CASC_GAME)")
    return g


def casc_cache_dir() -> str:
    return _path(load()["game"]["casc_cache"]) or str(Path(work_root()) / "cache" / "casc")


def casc_cache_gb() -> float:
    return float(load()["game"]["casc_cache_gb"])


def casc_seeds() -> list:
    return [p for p in (_path(s) for s in load()["game"]["casc_seeds"]) if p and Path(p).is_dir()]


def casc_opener(game_dir):
    """modkit's CascLib wrapper (Windows CascLib.dll, Linux libcasc.so) as the storage object hdkit.view.casc expects."""
    lib = _path(load()["game"]["casclib"])
    if lib:
        os.environ.setdefault("MODKIT_CASCLIB", lib)
    from modkit import casc
    return casc.Storage(game_dir)


def game_dir_free(game_dir) -> bool:
    """No run holds the game folder: none of the configured lock files exists."""
    return not locks_held()
