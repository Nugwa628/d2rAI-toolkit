"""Settings, paths and the per-monster config shared by the monster3d stage scripts (Windows and Linux).

Nothing machine- or project-specific is written in the code. Each machine setting is read from, in order: its
environment variable, the `[monster3d]` table of hdkit's config file (hdkit.toml, see `hdkit.site`), else the default.

  HDKIT_M3D_DIR        [monster3d] work       EV, the working folder (stages, shots, logs); default <hdkit work>/monster3d
  HDKIT_M3D_D2R        [monster3d] d2r_data   D2R's own extracted files, the `data` folder that holds hd/character/...
                                              (the donor units, skeletons, clips, effects are read from here; never
                                              written); default <hdkit d2r_extract>/data
  HDKIT_M3D_BLENDER    [monster3d] blender    Blender (4.x; run as a separate process only); default `blender` on PATH
  HDKIT_M3D_REALESRGAN [monster3d] realesrgan realesrgan-ncnn-vulkan, only for `s1_frames up`; default on PATH
  HDKIT_M3D_ROUND                             a sub folder of the monster's home for a later round of the same monster
  HDKIT_STORE / [paths] art_store             the content-addressed art store for large outputs (hdkit.store)

The monster: every stage script takes `--monster <monster.json>` (taken out of sys.argv when this module is imported), or
the environment variable HDKIT_M3D_MONSTER. The file holds what differs per monster (see MONSTER_DEFAULTS and
docs/monster-sprite-to-3d.md, "The monster config"); examples/monster3d/ has a filled-in one. Without a monster file the
scripts that need one stop with a message; the pure helpers (tests) work without it.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
from pathlib import Path

WINDOWS = os.name == "nt"
PKG = Path(__file__).resolve().parent          # hdkit/monster3d
REPO = PKG.parents[1]                          # the toolkit checkout (hdkit's parent)


def _site_cfg() -> dict:
    try:
        from hdkit import site
        return site.load().get("monster3d", {}) or {}
    except Exception:  # noqa: BLE001  (a broken or missing config must not stop the pure helpers)
        return {}


def setting(env: str, key: str, default=None):
    """The environment variable `env`, else hdkit.toml's [monster3d] `key`, else `default` (a string or None)."""
    v = os.environ.get(env)
    if v:
        return v
    v = _site_cfg().get(key)
    if v:
        try:
            from hdkit import site
            return site._path(v)
        except Exception:  # noqa: BLE001
            return str(v)
    return default


def _work_root() -> Path:
    try:
        from hdkit import site
        return Path(site.work_root())
    except Exception:  # noqa: BLE001
        return Path.home() / "hdkit-work"


def _d2r_default() -> Path | None:
    try:
        from hdkit import site
        ex = site.d2r_extract()
    except Exception:  # noqa: BLE001
        ex = ""
    return Path(ex) / "data" if ex else None


EV = Path(setting("HDKIT_M3D_DIR", "work") or _work_root() / "monster3d")
_d2r = setting("HDKIT_M3D_D2R", "d2r_data")
D2R = Path(_d2r) if _d2r else (_d2r_default() or EV / "d2r" / "data")


def _exe(env, key, name):
    v = setting(env, key)
    return Path(v) if v else Path(shutil.which(name) or name)


BLENDER = _exe("HDKIT_M3D_BLENDER", "blender", "blender")
ESRGAN = _exe("HDKIT_M3D_REALESRGAN", "realesrgan", "realesrgan-ncnn-vulkan")


# ---- the art store (hdkit.store) -------------------------------------------------------------------------------------

MANIFEST = EV / "ART-MANIFEST.json"


def store_root() -> Path | None:
    from hdkit import store
    return store.root()


def art(logical) -> Path:
    """A file by its logical path under EV (or an absolute path): the local file when it is there, else its object in the
    art store (EV/ART-MANIFEST.json). Raises FileNotFoundError when neither has it."""
    from hdkit import store
    return store.Manifest(EV, MANIFEST).find(logical)


# ---- the monster -----------------------------------------------------------------------------------------------------

# Every key a monster file may set, with its default. Keys left out take these. Folders are D2R data paths (no leading
# "data/"); {name} and {unit} are filled in.
MONSTER_DEFAULTS: dict = {
    "name": "",                 # file-safe name of the new unit's files (required)
    "token": "",                # the classic monster token (data/global/monsters/<token>/) of the reference frames
    "unit_prefix": "m3d_",      # the unit JSON is hd/character/enemy/<unit_prefix><name>.json
    "unit": "",                 # or the whole unit name ("" = unit_prefix + name)
    "folder": "hd/character/enemy/m3d/{name}",       # the unit's models, textures, clips, skeleton
    "vfx_particles": "hd/vfx/particles/m3d/{name}",  # the self-lit layer's particle files (s5_glow.py)
    "vfx_textures": "hd/vfx/textures/m3d/{name}",    # and its masks and colour strips
    "donor": "",                # the vanilla unit whose skeleton, state machine and clips are reused (hd/character/enemy/<donor>)
    "profile": "",              # the rig profile of hdkit.sprite3d.rig_fit (default: the donor's name)
    "K": 9.0,                   # the upright mesh (height 1) in game units
    "rigid_part": None,         # [c, m]: everything above the plane y = c + m z of the upright mesh is one rigid part
    "part_bone": "",            # the bone the rigid part is bound to alone ("" = the head, carried with it)
    "keep": [],                 # donor material maps left as they are (e.g. "MaskTexture")
    "shell_bone": "",           # the spare bone that carries the corpse shell (s5_death.py, s4_hdcorpse.py)
    "act": 1,                   # the act palette of the classic frames
    "clipfit": {},              # s5_clipfit.py / s5_limbfit.py settings (see clipfit_templates.json, "monster keys")
    "death": {},                # s5_death.py settings
    "corpse": {},               # s5_corpsefit.py / s4_hdcorpse.py settings
    "glow": {},                 # s5_glow.py settings
    "body": {},                 # s4_procbody.py / s4_hdbody.py / s4_hdcap.py settings
}


def _monster_arg():
    """--monster <file> on the command line (taken out of sys.argv), else HDKIT_M3D_MONSTER, else None."""
    if "--monster" in sys.argv:
        i = sys.argv.index("--monster")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--monster needs a file")
        path = sys.argv[i + 1]
        del sys.argv[i:i + 2]
        return path
    return os.environ.get("HDKIT_M3D_MONSTER") or None


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def safe_name(token: str) -> str:
    """A file-name-safe spelling of a token (`~k` -> `7ek`)."""
    return "".join(c if c.isalnum() else "%02x" % ord(c) for c in token.lower())


def load_monster(path=None, data: dict | None = None) -> dict:
    """The monster config: MONSTER_DEFAULTS, then the file (or `data`), with the derived names filled in."""
    raw = data if data is not None else (json.loads(Path(path).read_text(encoding="utf-8")) if path else {})
    m = _merge(MONSTER_DEFAULTS, raw)
    if not m["name"] and m["token"]:
        m["name"] = safe_name(m["token"])
    m["unit"] = m["unit"] or (m["unit_prefix"] + m["name"])
    m["profile"] = m["profile"] or m["donor"]
    for k in ("folder", "vfx_particles", "vfx_textures"):
        m[k] = m[k].format(name=m["name"], unit=m["unit"]).strip("/")
    m["_source"] = str(path) if path else ""
    return m


MONSTER_FILE = _monster_arg()
MONSTER = load_monster(MONSTER_FILE)


def need_monster() -> dict:
    """The monster config, or a clear stop when no --monster file was given."""
    if not MONSTER["name"] or not MONSTER["donor"]:
        raise SystemExit("no monster config: pass --monster <monster.json> (name, donor, ...; see "
                         "docs/monster-sprite-to-3d.md) or set HDKIT_M3D_MONSTER")
    return MONSTER


TOKEN = MONSTER["token"]
NAME = MONSTER["name"] or "monster"
UNIT = MONSTER["unit"]
FOLDER = MONSTER["folder"]                                    # hd/character/enemy/... (no "data/")
UNIT_JSON = "hd/character/enemy/%s.json" % UNIT               # the unit file, relative to an overlay / data folder
# one folder per monster under EV (stages, shots, logs, scratch); tmp, cache and shared tools stay in EV
HOME = EV / NAME
if os.environ.get("HDKIT_M3D_ROUND"):
    HOME = HOME / os.environ["HDKIT_M3D_ROUND"]
STAGES = HOME / "stages"


def mcfg(section: str) -> dict:
    """One settings block of the monster config ("clipfit", "death", "corpse", "glow", "body")."""
    return MONSTER.get(section) or {}


def donor_dir() -> str:
    """hd/character/enemy/<donor> (relative to D2R)."""
    return "hd/character/enemy/" + need_monster()["donor"]


# file direction numbers of an 8-direction monster, clockwise on screen from "down" (the monster faces the camera)
CLOCKWISE_8 = (4, 0, 5, 1, 6, 2, 7, 3)
DIR_NAMES_8 = ("down-left", "up-left", "up-right", "down-right", "down", "left", "up", "right")


def squash_u(img, strip, fill=0):
    """An (h, w, ...) image in an atlas layout squeezed into the left (1 - strip) of its width (bilinear), the right `strip`
    filled with `fill`: the layout of a mesh whose u was scaled by (1 - strip) (s5_build.py --strip)."""
    import numpy as np
    from PIL import Image
    if not strip:
        return img
    h, w = img.shape[:2]
    keep = int(round(w * (1 - strip)))
    small = np.asarray(Image.fromarray(img).resize((keep, h), Image.BILINEAR))
    out = np.empty_like(img)
    out[...] = fill
    out[:, :keep] = small
    return out


# ---- the GPU gate ----------------------------------------------------------------------------------------------------

def cpu_percent() -> int:
    """The machine's CPU load in percent (-1 when it cannot be read)."""
    from hdkit import gpugate
    try:
        cpu = gpugate.read_counters()[2]
    except Exception:  # noqa: BLE001
        cpu = None
    return int(round(cpu)) if cpu is not None else -1


def gpu_gate(wait: int = 0, limit: int = 25) -> bool:
    """True when no configured game lock is held (hdkit.toml [locks] globs) and the GPU's load is under `limit` percent;
    waits up to `wait` seconds."""
    from hdkit import gpugate
    end = time.time() + wait
    while True:
        rd = gpugate.check(gpu_max=limit, cpu_max=95.0)
        if rd.free:
            print("gate open: %s" % rd.why, flush=True)
            return True
        if time.time() >= end:
            print("gate closed: %s" % rd.why, flush=True)
            return False
        time.sleep(15)


def child_env() -> dict:
    """The environment for a child `python -m hdkit...`: this checkout first on PYTHONPATH, so it runs from any folder."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def opt(a, name, default):
    """The value after `name` in the argument list `a`, else `default`."""
    return a[a.index(name) + 1] if name in a else default


if __name__ == "__main__":
    sys.exit(0 if gpu_gate(int(sys.argv[1]) if len(sys.argv) > 1 else 0) else 1)
