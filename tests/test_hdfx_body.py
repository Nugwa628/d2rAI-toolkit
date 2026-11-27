"""Lane hdfx-body: a unit's meshes and material groups each draw with their own textures (gr2.Mesh.groups,
scene.Part.draw_groups, gpu._material_draws). Mephisto's body_meshShape has 3 groups (flesh body_2, bones, eyes_head_2);
before, the viewer drew every mesh of a part with the part's first textures, so his flesh was bones_ALB (paler than in game).
No game files needed; the model check skips when the local CASC cache is absent."""
import os
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit.view import gpu, gr2, scene  # noqa: E402

MEPH = Path(os.environ.get("HDFX_MEPH_MODEL") or "/nonexistent")


def _tex(name):
    return scene.Texture(name, 0, [np.zeros((4, 4, 4), np.uint8)], name.endswith("alb"))


def _mesh(name, ntri, groups):
    tris = np.arange(ntri * 3).reshape(-1, 3)
    z = np.zeros((ntri * 3, 3))
    maps = groups[0][2] if groups else {}
    prm = groups[0][3] if groups else {}
    return gr2.Mesh(name, z, z, np.zeros((ntri * 3, 4)), np.zeros((ntri * 3, 2)), tris, maps, [], params=prm, groups=groups)


class MaterialGroups(unittest.TestCase):
    def setUp(self):
        self.t = {n: _tex(n) for n in ("a_alb", "b_alb", "c_alb", "a_nrm")}

    def part(self, meshes, per_mesh):
        first = per_mesh[0][0]
        return scene.Part("p", "p.model", meshes, None, dict(first), mesh_textures=per_mesh)

    def test_groups_with_other_textures_draw_apart(self):
        t = self.t
        g = [(0, 2, {"AlbedoTexture": "a"}, {"alpha_cut": 0.5}), (2, 3, {"AlbedoTexture": "b"}, {"emissive": 0.27}),
             (5, 1, {"AlbedoTexture": "c"}, {})]
        m = _mesh("body", 6, g)
        p = self.part([m], [[{"AlbedoTexture": t["a_alb"], "NormalTexture": t["a_nrm"]}, {"AlbedoTexture": t["b_alb"]},
                             {"AlbedoTexture": t["c_alb"]}]])
        d = gpu._material_draws(p, 0, m)
        self.assertEqual([(f, c) for f, c, _t, _p in d], [(0, 2), (2, 3), (5, 1)])
        self.assertEqual([x[2]["AlbedoTexture"].logical for x in d], ["a_alb", "b_alb", "c_alb"])
        self.assertEqual(d[1][3], {"emissive": 0.27})
        self.assertIs(d[1][2]["NormalTexture"], t["a_nrm"])        # a map the group lacks: the part's

    def test_each_mesh_its_own_textures(self):
        t = self.t
        m0 = _mesh("pelvis", 2, [(0, 2, {"AlbedoTexture": "a"}, {})])
        m1 = _mesh("hair", 2, [(0, 2, {"AlbedoTexture": "b"}, {})])
        p = self.part([m0, m1], [[{"AlbedoTexture": t["a_alb"]}], [{"AlbedoTexture": t["b_alb"]}]])
        self.assertEqual(gpu._material_draws(p, 1, m1)[0][2]["AlbedoTexture"].logical, "b_alb")
        self.assertEqual(gpu._material_draws(p, 0, m0)[0][:2], (0, 2))

    def test_same_material_groups_stay_one_draw(self):
        t = self.t
        g = [(0, 2, {"AlbedoTexture": "a"}, {}), (2, 2, {"AlbedoTexture": "a"}, {})]
        m = _mesh("m", 4, g)
        p = self.part([m], [[{"AlbedoTexture": t["a_alb"]}, {"AlbedoTexture": t["a_alb"]}]])
        self.assertEqual([(f, c) for f, c, _t, _p in gpu._material_draws(p, 0, m)], [(0, 4)])

    def test_code_built_part_draws_whole(self):
        m = _mesh("card", 2, [])
        p = scene.Part("p", "x", [m], None, {"AlbedoTexture": self.t["a_alb"]})
        d = gpu._material_draws(p, 0, m)
        self.assertEqual((d[0][0], d[0][1], d[0][2]["AlbedoTexture"].logical), (0, 2, "a_alb"))

    @unittest.skipUnless(MEPH.exists(), "no local CASC cache of Mephisto's model")
    def test_mephisto_body_groups_match_the_capture(self):
        meshes, _sk = gr2.load_meshes(MEPH.read_bytes())
        body = next(m for m in meshes if m.name == "body_meshShape")
        # RenderDoc calib r3: eids 24399 / 24407 / 24415 draw 59778 / 62832 / 17088 indices at 0 / 59778 / 122610
        self.assertEqual([(f * 3, c * 3) for f, c, _m, _p in body.groups], [(0, 59778), (59778, 62832), (122610, 17088)])
        self.assertEqual([g[2]["AlbedoTexture"].split("/")[-1] for g in body.groups],
                         ["body_2_alb.texture", "bones_ALB.texture", "eyes_head_2_alb.texture"])


if __name__ == "__main__":
    unittest.main()
