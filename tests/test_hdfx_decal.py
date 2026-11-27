"""lane r3-decal: D2R's FX decal record and surface blend (hdkit/fx/decal.py). No game files needed."""
import math
import unittest

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

from hdkit.fx import decal as dc


PROPS = {"Decal.DiffuseMap": "a_ALB.texture", "Decal.NormalMap": "a_NRM.texture", "Decal.OrmMap": "a_ORM.texture",
         "Decal.EmissiveMap": None, "Decal.ApplyToAlbedo": True, "Decal.ApplyToNormal": True,
         "Decal.ApplyToOcclusion": False, "Decal.ApplyToRoughness": True, "Decal.ApplyToMetalness": False,
         "Emissive": False}


class Flags(unittest.TestCase):
    def test_material_bits(self):
        self.assertEqual(dc.material_flags(PROPS), dc.ALBEDO | dc.NORMAL | dc.ROUGHNESS)

    def test_switch_needs_its_map(self):
        p = dict(PROPS, **{"Decal.OrmMap": "None", "Decal.ApplyToOcclusion": True})
        self.assertEqual(dc.material_flags(p), dc.ALBEDO | dc.NORMAL)

    def test_emissive_needs_map_and_switch(self):
        self.assertFalse(dc.material_flags(dict(PROPS, Emissive=True)) & dc.EMISSIVE)
        p = dict(PROPS, Emissive=True, **{"Decal.EmissiveMap": "e.texture"})
        self.assertTrue(dc.material_flags(p) & dc.EMISSIVE)

    def test_ignored_switches_change_nothing(self):
        for k in dc.UNUSED_BY_GAME:
            self.assertEqual(dc.material_flags(dict(PROPS, **{k: True})), dc.material_flags(PROPS))

    def test_texture_slots(self):
        self.assertEqual(dc.textures(PROPS), {0: "a_ALB.texture", 1: "a_ORM.texture", 10: "a_NRM.texture"})


class Pack(unittest.TestCase):
    def test_box_downward(self):
        x, y, z = dc.box_axes((0, -1, 0))
        np.testing.assert_allclose([x, y, z], np.eye(3), atol=1e-12)

    def test_box_tilted_is_orthonormal(self):
        x, y, z = dc.box_axes((0.3, -1.0, 0.2))
        M = np.array([x, y, z])
        np.testing.assert_allclose(M @ M.T, np.eye(3), atol=1e-9)
        np.testing.assert_allclose(y, -np.array([0.3, -1.0, 0.2]) / np.linalg.norm([0.3, -1.0, 0.2]))
        self.assertAlmostEqual(np.linalg.det(M), 1.0)

    def test_box_full_extents(self):
        r = dc.pack((1.0, 0.0, 2.0), (4.0, 1.0, 2.0))
        p = r.world_to_box @ np.array([3.0, 0.5, 3.0, 1.0])        # corner: +2, +0.5, +1 from the centre
        np.testing.assert_allclose(p[:3], (0.5, 0.5, 0.5))

    def test_bytes_as_packed(self):
        r = dc.pack((0, 0, 0), (1, 1, 1), edge_fade=0.25, fade_in=0.0, fade_out=55.0, thr_value=0.5, thr_hardness=0.794,
                    mat_flags=dc.ALBEDO, has_orm=True)
        self.assertEqual((r.edge, r.thr, r.hard), (63, 127, 202))
        self.assertEqual((r.cos_min, r.cos_max), (int(math.cos(math.radians(55)) * 127), 127))
        self.assertEqual(r.flags, dc.ALBEDO | dc.ANGLE | dc.THRESHOLD)

    def test_no_angle_test_when_equal(self):
        r = dc.pack((0, 0, 0), (1, 1, 1), fade_in=45.0, fade_out=45.0)
        self.assertFalse(r.flags & dc.ANGLE)

    def test_threshold_needs_value_and_orm(self):
        self.assertFalse(dc.pack((0, 0, 0), (1, 1, 1), thr_value=0.0, has_orm=True).flags & dc.THRESHOLD)
        self.assertFalse(dc.pack((0, 0, 0), (1, 1, 1), thr_value=0.3, has_orm=False).flags & dc.THRESHOLD)

    def test_emissive_bytes_roundtrip(self):
        r = dc.pack((0, 0, 0), (1, 1, 1), em_intensity=1000.0, em_focus=2.0, mat_flags=dc.EMISSIVE)
        self.assertEqual(r.em, int(0.25 * 255))
        self.assertAlmostEqual(math.log10(r.em_intensity()), 2.0 + r.em / 63.75, places=4)
        self.assertAlmostEqual(r.em_power(), int(0.25 * 255) * 64 / 255, places=3)

    def test_records_from_inputs(self):
        inp = {"Decal.DecalSize": np.array([[2.0, 1.0, 2.0], [1.0, 1.0, 1.0]]), "Decal.EdgeFade": np.array([0.1, 0.2])}
        recs = dc.records(PROPS, inp, 2, np.zeros((2, 3)))
        self.assertEqual([r.edge for r in recs], [25, 51])


class Shade(unittest.TestCase):
    rec = dc.pack((0, 0, 0), (2, 1, 2), edge_fade=0.5, thr_value=0.4, thr_hardness=0.5,
                  mat_flags=dc.ALBEDO | dc.NORMAL | dc.ROUGHNESS | dc.OCCLUSION | dc.METALNESS, has_orm=True)

    def test_outside_box(self):
        self.assertEqual(dc.coverage(self.rec, (0.6, 0, 0), 1.0), 0.0)

    def test_edge_fade(self):
        self.assertAlmostEqual(dc.coverage(dc.pack((0, 0, 0), (1, 1, 1), edge_fade=0.5), (0.0, 0, 0), 1.0), 1.0)
        self.assertAlmostEqual(dc.coverage(dc.pack((0, 0, 0), (1, 1, 1), edge_fade=0.5), (0.375, 0, 0), 1.0), 0.5,
                               places=2)

    def test_threshold(self):
        # sat((orm.a - thr) x hard^2 x 64 + 0.5): at orm.a = thr exactly half
        w = dc.coverage(dc.pack((0, 0, 0), (1, 1, 1), thr_value=0.4, thr_hardness=0.5, has_orm=True), (0, 0, 0), 1.0,
                        orm_a=0.4 + 0.5 / 255)
        self.assertAlmostEqual(w, 0.5, places=1)

    def test_lerps(self):
        base = {"albedo": (0.5, 0.5, 0.5), "normal": (0, 1, 0), "ao": 1.0, "rough": 0.9, "metal": 0.0}
        tex = {"albedo": (0.1, 0.0, 0.0), "nrm": (1.0, 0.5, 0.0, 0.5), "orm": (0.5, 0.04, 1.0, 1.0)}
        o = dc.shade(self.rec, 0.5, base, tex)
        np.testing.assert_allclose(o["albedo"], (0.3, 0.25, 0.25))
        np.testing.assert_allclose(o["normal"], (0, 1, 0), atol=1e-9)    # flat normal map: the box Y
        self.assertAlmostEqual(o["rough"], 0.47)
        self.assertAlmostEqual(o["ao"], 0.75)
        self.assertAlmostEqual(o["metal"], 0.5)

    def test_normal_map_x_along_box_x(self):
        base = {"albedo": (0.5,) * 3, "normal": (0, 1, 0), "ao": 1.0, "rough": 0.9, "metal": 0.0}
        tex = {"albedo": (0,) * 3, "nrm": (1.0, 0.5, 0.0, 1.0), "orm": (1, 1, 0, 1)}
        o = dc.shade(self.rec, 1.0, base, tex)
        np.testing.assert_allclose(o["normal"], (1, 0, 0), atol=1e-9)


class Wgsl(unittest.TestCase):
    def test_entries_exist(self):
        from pathlib import Path
        src = (Path(dc.__file__).parents[1] / "view" / "fx.wgsl").read_text()
        for name in ("fn decal_eval", "fn fs_decal_mul", "fn fs_decal_add"):
            self.assertIn(name, src)

    def test_coverage_sets(self):
        from hdkit.fx import render
        self.assertTrue(dc.SUPPORTED_FEATURES <= render.SUPPORTED_FEATURES)
        self.assertIn("4", render.SUPPORTED_KINDS)


if __name__ == "__main__":
    unittest.main()
