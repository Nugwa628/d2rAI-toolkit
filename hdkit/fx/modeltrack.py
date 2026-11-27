"""ModelInstanceTracking: an object effect drawn over its unit's own posed model (lane r4-glow).

What the game does: a unit entity with both a ModelDefinitionComponent and an ObjectEffectDefinitionComponent (Wraith
`entity_torso` -> `vfx_wraith1_objectFX`) starts that effect with the unit. Its mesh renderers have no `General.Mesh`;
with `ModelInstanceTracking` on they draw the tracked model instance (`ModelInstanceTracking.AnimatedModelInstanceIds`,
1 = the entity's model) with the renderer's material: the skinned body again, pushed out along its normals by
`VertexDisplacement.DisplacementDistance`, coloured by the particle (Wraith: Uber mesh, blue Color x exposure, scrolling
plasma noise, Fresnel to colour and alpha). The particle sits at the effect origin with no turn or scale (sim: Position 0,
Orientation identity, Scale 1), so the posed world mesh is drawn as it is, once per particle.

Older viewers took such an entity as a mask/strip layer (scene.pick_layer_textures, the Fire Golem pattern); an effect
whose textures give no mask/strip was skipped. Those are `scene.Unit.tracked_fx` and are drawn here; effects that do give
a layer keep it (no double draw).
"""
from __future__ import annotations

import numpy as np


def uses_tracked_mesh(r):
    return r.kind == "mesh" and r.get("ModelInstanceTracking") and not r.get("General.Mesh")


def posed_meshes(unit, posed, part=None, effect=None):
    """The posed meshes for a timeline slot or an explicitly selected object effect."""
    from ..view.vfs import normalise
    bound = {name for name, path in unit.tracked_fx if effect and normalise(path) == normalise(effect)}
    return [mesh for p in unit.parts
            if (p.unit_part.lower() == part.lower() if part else not bound or p.name in bound)
            for mesh in posed.get(p.name, ())]


def prep_tracked(fxr, r, mat, n, pblock):
    """FxDraws of mesh renderer `r` over the posed meshes in `fxr.tracked` ([(mesh, world pos, world normals)])."""
    from . import packers as pk
    from .render import FxDraw, standard_material
    X = np.repeat(np.eye(4)[None], max(int(n), 1), 0)
    disp = float(np.max(pblock[:, pk.OFF["sc"][0] - 10 + 3])) if mat.on("VERTEX_DISPLACEMENT") else 0.0
    out = []
    for m, Pw, Nw in fxr.tracked:
        dm = None
        if disp and getattr(m, "col", None) is not None:
            dm = np.asarray(m.col, float)[:, 0] / 255.0          # vertex colour R masks the displacement (VS 1733)
        v = pk.pack_mesh(Pw, Nw, m.uv, m.tris, X, pblock, None, dm)
        out.append(FxDraw("mesh", r.index, r.layer, standard_material(mat, m), v,
                          name="%d:tracked:%s" % (r.index, m.name), margin=abs(disp)))
    return out


class TrackedEffects:
    """The unit's tracked object effects, simulated with the unit (one Sim each at the unit's origin)."""

    def __init__(self, fs, unit, make_sim, attrs=None, seed=0, eye=None, skip=None):
        from . import rspec
        from ..view.vfs import normalise
        self.items = []
        for part, eff_path in getattr(unit, "tracked_fx", None) or []:
            if (skip and normalise(skip) == normalise(eff_path)) or not fs.exists(eff_path):
                continue
            data = fs.read(eff_path)
            eff = rspec.load(data)
            eff.path = eff_path
            if not any(uses_tracked_mesh(rd) for rd in eff.renderers):
                continue
            self.items.append((part, eff, make_sim(data, seed + 101 + len(self.items), np.eye(4), attrs, eye)))

    def draws(self, fxr, posed, t, cam, advance):
        """FxDraws of every tracked effect at unit time t over `posed` (gpu.Renderer.last_posed); fxr.lights collects
        their lights. `advance(sim, t, stop)` steps a sim (fxplace.advance)."""
        out, lights = [], []
        for part, eff, sm in self.items:
            advance(sm, t, None)
            fxr.tracked = posed.get(part) or []
            try:
                out += fxr.prepare(eff, sm.state(), cam)
            finally:
                fxr.tracked = []
            lights += fxr.lights
        fxr.lights = lights
        return out
