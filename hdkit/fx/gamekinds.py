"""What D2R does with the rare renderer kinds, materials and switches (lane r3-misc), read from the game, not guessed.

D2R's material factory (D2R.exe 1.6.8x build 95032 dump, 0x140ec3b60) turns a renderer into a material class by its
kind (renderer field 0) and material path:

    kind 0, 1, 2 (billboard, ribbon, mesh)   the first name the path contains: Default_Billboard / Default_Ribbon
                                             (0x141013640), Diablo_Uber, Diablo_Smoke (the Uber class, flag +0x88),
                                             Diablo_Liquid, Default_Mesh, Diablo_Standard, Diablo_Stencil; none ->
                                             no material (0x140ec3f17 stores null)
    kind 5 (light; decal by its material)    the light/decal class 0x14101b0b0
    kind 3 (triangle), 4 (decal), 6 (sound)  no material: the switch at 0x140ec3bff sends every other kind to null

A renderer without a material draws nothing, so `Distortion_Billboard.pkma` (kind 0, `Distortion` on), triangle
renderers (`Default_Triangle.pkma`, `GeometryTriangle`) and sound renderers (`Default_Sound.pkma`, `GeometrySound`,
`Sound`) are invisible in the game. No D2R code reads the `Distortion`, `Sound`, `Sound.SoundData` or `LegacyLit*`
properties either: their name globals are only written by the property-name table init 0x141728e00 and cleared at
0x141728c40 (no getter calls them), and the game has no PopcornFX sound backend (no sound renderer class name in the
image). `MeshSampling` is read by the Uber reader (getter 0x141008d30, call 0x14102d1c0), and when on it only logs
"Mesh sampling is no longer supported!" (0x14102bf70); the renderer draws as without it.

So the viewer matches the game by drawing nothing for those renderers and ignoring those switches; GAME_IGNORES says
why for each. Sound renderers are still reported, so a user sees where PopcornFX would have played a sound:
`sound_record` (one per sound renderer with particles in fx/sim.SimState.sounds) gives the sound path, positions,
volume and the particles that started this step (`fired`: a PopcornFX sound renderer starts one voice per new
particle); fx/render.FxRenderer.sounds collects them and `sound_markers` draws an optional marker at each (viewer only).
"""
from __future__ import annotations

import numpy as np

# renderer field 0 -> kind, as fx/graph.RENDERER_KINDS names them (5 is "decal" when its material is a decal)
KIND_IDS = {0: "billboard", 1: "ribbon", 2: "mesh", 3: "triangle", 4: "decal", 5: "light", 6: "sound"}
NO_MATERIAL_KINDS = {"triangle", "sound"}        # kind 3 and 6: never a material in D2R (kind 4 too: lane decal's)

# switches the game never reads, or reads and ignores: the viewer ignores them the same way (coverage counts them)
GAME_IGNORES = {
    "Distortion": "Distortion_Billboard has no D2R material class (0x140ec3b60): the renderer is not drawn",
    "GeometryTriangle": "renderer kind 3 has no D2R material class: not drawn",
    "TriangleCustomNormals": "renderer kind 3 has no D2R material class: not drawn",
    "TriangleCustomUVs": "renderer kind 3 has no D2R material class: not drawn",
    "GeometrySound": "renderer kind 6 has no D2R material class and D2R has no PopcornFX sound backend: silent",
    "Sound": "Sound / Sound.SoundData are never read by D2R (name table only): silent",
    "SoundAttenuation": "never read by D2R",
    "Doppler": "never read by D2R",
    "LegacyLit": "LegacyLit* are never read by D2R (name table only): the material draws unlit, as without it",
    "MeshSampling": "D2R logs 'Mesh sampling is no longer supported!' (0x14102bf70) and draws the renderer as without it",
}
# material files the game makes no material for (drawn as the game: nothing)
NO_MATERIAL = {"vfx/Library/PopcornFXCore/Materials/%s.pkma" % n
               for n in ("Distortion_Billboard", "Default_Triangle", "Default_Sound")}


def kind_name(kind) -> str:
    """A renderer kind as a name: an int (field 0) or a name/`kind<N>`/`<N>` string."""
    if isinstance(kind, (int, np.integer)):
        return KIND_IDS.get(int(kind), "kind%d" % int(kind))
    s = str(kind)
    if s.isdigit():
        return KIND_IDS.get(int(s), s)
    if s.startswith("kind") and s[4:].isdigit():
        return KIND_IDS.get(int(s[4:]), s)
    return s


def game_class(kind, material: str | None):
    """D2R's material class family of a renderer (fx/shad.material_family names), or None when the game makes no
    material for it and so draws nothing (see the module doc)."""
    from . import shad
    k = kind_name(kind)
    if k in NO_MATERIAL_KINDS:
        return None
    if k in ("light", "decal"):
        return shad.material_family(material or "", k)
    return shad.material_family(material or "", k)


def drawn_by_game(kind, material: str | None) -> bool:
    return game_class(kind, material) is not None


def notes_for(r) -> list[str]:
    """The game's behaviour for a renderer's switched-on GAME_IGNORES switches (r: rspec.RSpec or graph.RendererDef)."""
    props = getattr(r, "props", {}) or {}
    out = []
    for name, why in GAME_IGNORES.items():
        p = props.get(name)
        v = p.get("value") if isinstance(p, dict) else p
        if v is True or (isinstance(v, (bytes, bytearray)) and v[:1] == b"\xff"):
            out.append("%s: %s" % (name, why))
    return out


# ---------------------------------------------------------------- sound renderers (where a sound would fire)
def sound_record(r, L, layer: str, ri: dict, seen: int = -1) -> dict:
    """One sound renderer's particles (fx/sim.SimState.sounds): `position` (n, 3), `volume` (n,), `on` (Enabled),
    `ids`, `fired` (on and id > `seen`: the particles that started this step, as PopcornFX starts one voice per new
    particle), `sound` (Sound.SoundData path, None when the file has none)."""
    n = L.n
    on = np.ones(n, bool)
    if "Enabled" in ri:
        on &= np.asarray(ri["Enabled"]).reshape(n, -1)[:, 0] != 0

    def get(name, k, default):
        if name in ri:
            a = np.asarray(ri[name], np.float32).reshape(n, -1)
            return a[:, :k] if a.shape[1] >= k else np.repeat(a[:, :1], k, axis=1)
        p = (r.props.get(name) or {}).get("value")
        v = np.asarray(default if p is None else p, np.float32).reshape(1, -1)[:, :k]
        return np.repeat(v, n, axis=0)
    ids = np.asarray(L.ids, np.int64)
    sd = r.props.get("Sound.SoundData") or {}
    return {"layer": layer, "renderer": r.index, "kind": "sound", "count": n, "on": on,
            "position": get("Position", 3, (0.0, 0.0, 0.0)), "volume": get("Sound.Volume", 1, 1.0)[:, 0],
            "ids": ids, "fired": on & (ids > seen), "sound": sd.get("string"),
            "game": GAME_IGNORES["GeometrySound"]}


def sounds_summary(sounds) -> list[dict]:
    """JSON-able lines of SimState.sounds / FxRenderer.sounds: renderer, layer, sound, live and fired counts, the
    fired positions (rounded)."""
    out = []
    for s in sounds or []:
        on, fired = np.asarray(s["on"], bool), np.asarray(s["fired"], bool)
        pos = np.asarray(s["position"], float).reshape(-1, 3)
        out.append({"renderer": s["renderer"], "layer": s["layer"], "sound": s.get("sound"), "live": int(on.sum()),
                    "fired": int(fired.sum()), "at": np.round(pos[fired], 3).tolist()[:16]})
    return out


MARKER_COLOR = (1.0, 0.2, 1.0, 1.0)     # magenta: nothing in the game is drawn like it


def sound_markers(sounds, basis, size: float = 0.35, fired_only: bool = False):
    """(n x 6, FLOATS) billboard vertices of a marker square at each live (or only `fired`) sound particle, for
    FxRenderer (viewer only: the game draws nothing). Fired particles get a marker twice the size. None when empty."""
    from . import packers as pk
    pos, sz = [], []
    for s in sounds or []:
        on, fired = np.asarray(s["on"], bool), np.asarray(s["fired"], bool)
        keep = fired if fired_only else on
        p = np.asarray(s["position"], float).reshape(-1, 3)[keep]
        pos.append(p)
        sz.append(np.where(fired[keep], 2.0 * size, size))
    if not pos or not sum(len(p) for p in pos):
        return None
    pos, sz = np.concatenate(pos), np.concatenate(sz)
    n = len(pos)
    pblock = pk.particle_block({"Color": np.tile(np.asarray(MARKER_COLOR, np.float32), (n, 1))}, n, "default")
    return pk.pack_billboards(0, pos, np.stack([sz, sz], 1), np.zeros(n), basis, pblock)
