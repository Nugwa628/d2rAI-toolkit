"""Where the game puts an overlay effect (lane hdfx-r3-overlays).

A state / cast overlay is an HD unit definition `hd/overlays/<folder>/<key>.json` whose entities carry a
`VfxDefinitionComponent` (the `.particles` file) and, optionally, a `TransformDefinitionComponent`: the game draws the
effect at the unit's root x that transform (position, orientation, scale; `inheritOnlyPosition` false, so the unit's
facing turns it too). Example: `shrine_stamina.json` / `shrine_experience.json` put their effect at y = 14 world units
over the unit's feet, which a bench capture confirms (setstate player 136/137, slot 5: the winged foot and the scroll
float ~205 px over the feet at 16.97 px per unit and 30 degrees elevation = 14 x cos 30 x 16.97).

The effect files of overlay `<folder>/<key>` live in `hd/vfx/particles/overlays/<folder>/<key>/`, so the JSON is found
from the effect's own path (`overlay_for`). The fx viewer applies it by default (`--overlay auto`).
"""
import json
import re

from . import vfs as _vfs

# World yaw (degrees, camera.rot_y) of an overlay effect. The game does NOT turn an overlay with its unit's facing: bench
# o1 (slot 5) cast towards +x and towards -x, then setstate 136: the winged foot stood the same way both times.
OVERLAY_WORLD_YAW = 0.0

_OVL_RE = re.compile(r"(?:^|/)hd/vfx/particles/overlays/([^/]+)/([^/]+)/[^/]+\.particles$", re.I)


def overlay_candidates(asset: str) -> list:
    """Logical paths of the overlay JSONs that may place `asset` (same folder first, then `common`)."""
    m = _OVL_RE.search(_vfs.normalise(asset).replace("\\", "/"))
    if not m:
        return []
    folder, key = m.group(1).lower(), m.group(2).lower()
    out = ["hd/overlays/%s/%s.json" % (folder, key)]
    if folder != "common":
        out.append("hd/overlays/common/%s.json" % key)
    return out


def _names_asset(doc: dict, asset: str) -> bool:
    from .scene import vfx_entities
    want = _vfs.normalise(asset)
    return any(_vfs.normalise(e["filename"]) == want for e in vfx_entities(doc))


def overlay_for(fs, asset: str) -> str | None:
    """The overlay JSON (logical path) one of whose VfxDefinitionComponent entities names `asset`, or None."""
    for p in overlay_candidates(asset):
        try:
            if not fs.exists(p):
                continue
            doc = json.loads(fs.read(p).decode("utf-8"))
        except (OSError, ValueError, KeyError):
            continue
        if _names_asset(doc, asset):
            return p
    return None


def resolve_overlay_arg(fs, value: str | None, asset: str) -> str | None:
    """--overlay: `auto` (default) -> `overlay_for`; `none`/`off`/'' -> None; anything else is a JSON path."""
    if value is None or str(value).lower() == "auto":
        return overlay_for(fs, asset)
    if str(value).lower() in ("none", "off", ""):
        return None
    return value


# ---- how long the game plays an overlay (1.14d Game.exe, the same client code D2R keeps for its overlay list) ----------
# GFX_AddOverlayToUnit 0x470390: nFrameDelay = the sprite's frame count << 8 (GFXUTIL_GetOverlayFrameDelay 0x4dbe20),
# nAnimRate = overlay.txt AnimRate << 4; GFX_UpdateOverlayAnimations 0x470c30 adds nAnimRate per client frame (25 fps) and,
# for a play-once overlay (type 2: a skill's cast overlay), drops it once the counter reaches nFrameDelay. So a cast overlay
# lasts Frames x 16 / AnimRate frames. State overlays (type 8) loop while their state is on. When the overlay goes, the HD
# effect is stopped (effect.isRunning false): Lava Pit's MaindecalSmokeSahder fades its scorch in only then (scripts 144/145
# of vfx_druid_fire_cast_1: F grows only after isRunning falls; bench o1 shows the ember scorch after the flames).
FPS = 25.0
CAST_OVERLAY_COLS = ("castoverlay", "cltoverlaya", "cltoverlayb")


from ..fx.context import overlay_seconds  # noqa: E402  (Frames x 16 / AnimRate / 25)


def is_cast_overlay(tables, name: str) -> bool:
    """True when a skills.txt row names `name` in a cast-overlay column (played once on the caster)."""
    if tables is None or not tables.has("skills"):
        return False
    n = name.lower()
    return any((r.get(c) or "").lower() == n for r in tables.rows("skills") for c in CAST_OVERLAY_COLS)


def overlay_key(overlay_json: str | None) -> str | None:
    """`hd/overlays/<folder>/<key>.json` -> key."""
    if not overlay_json:
        return None
    return _vfs.normalise(overlay_json).rsplit("/", 1)[-1].rsplit(".", 1)[0]


def stop_time(arg, overlay_json: str | None, tables, fs=None) -> float | None:
    """--stop: a number of seconds, `none`, or `auto` (default): the overlay.txt pass of a cast overlay when tables are
    given; None = never stopped (state overlays, plain effects)."""
    if arg is None or str(arg).lower() == "auto":
        key = overlay_key(overlay_json)
        if not key or tables is None or not tables.has("overlay"):
            return None
        row = tables.row("overlay", key)
        if row is None or not is_cast_overlay(tables, key):
            return None
        return overlay_seconds(row, fs)
    if str(arg).lower() in ("none", "off", ""):
        return None
    return float(arg)


def advance(sm, t: float, stop_at: float | None = None) -> None:
    """Bring Sim `sm` to effect time `t` (carry on when t is ahead, else restart), calling sm.stop() at `stop_at`."""
    now = float(getattr(sm, "t", -1.0))
    if not (getattr(sm, "started", False) and 0.0 <= now <= t):
        sm.reset()
        now = 0.0
    if stop_at is not None and now < stop_at <= t and getattr(sm, "running", True):
        sm.step(stop_at - now)
        sm.stop()
        now = stop_at
    elif stop_at is not None and stop_at <= now:
        sm.stop()
    if t - now > 1e-9 or not getattr(sm, "started", False):
        sm.step(max(t - now, 0.0))
