"""hdkit.monster3d clip tools on synthetic data: the clip writer (s5_death.ClipWriter) read back by the viewer's curve
decoder, pose maths, the death config and its timing, classic's fall curve, the clip fit's template, scoring and
interpolation helpers, and the corpse fit's camera calibration. No game files, no Blender."""
import json
import sys
import tempfile
import unittest
try:
    import numpy, PIL, scipy, skimage  # noqa: E401,F401  (hdkit/requirements.txt and scikit-image)
except ImportError as _e:
    raise unittest.SkipTest("hdkit.monster3d needs numpy, Pillow, scipy and scikit-image: %s" % _e)
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from hdkit.formats import d2r_model as dm  # noqa: E402
from hdkit.monster3d import common  # noqa: E402
from hdkit.monster3d import s5_clipfit as cf  # noqa: E402
from hdkit.monster3d import s5_corpsefit as cfit  # noqa: E402
from hdkit.monster3d import s5_death as sd  # noqa: E402
from hdkit.sprite3d import cam_fit  # noqa: E402
from hdkit.view import curves  # noqa: E402

RULE = {"b_min": 120, "b_over_rg": 50}


def monster(**over):
    data = {"name": "testmon", "donor": "testdonor", "part_bone": "part", "shell_bone": "shell",
            "clipfit": {"part_mask": RULE, "loop": ["wl"]}}
    data.update(over)
    return common.load_monster(data=data)


def curve_templates():
    """A tree with one track of each curve type the writer copies (headers and Real32 knots as Granny objects)."""
    td = dm.TypeDef()

    def var(kind, extra):
        head = dm.Obj(td, {"Format": 7, "Degree": 9})
        return dm.Variant(td, dm.Obj(td, {"CurveDataHeader_" + kind: head, "Padding": 0, **extra}))
    real = dm.TypeDef()
    k = var("DaK32fC32f", {"Knots": [dm.Obj(real, {"Real32": 0.0})], "Controls": [dm.Obj(real, {"Real32": 0.0})]})
    q = var("D4Constant32f", {"Controls": [0.0, 0.0, 0.0, 1.0]})
    p = var("D3Constant32f", {"Controls": [0.0, 0.0, 0.0]})
    tracks = [{"Name": "a", "OrientationCurve": {"CurveData": q}, "PositionCurve": {"CurveData": k}},
              {"Name": "b", "OrientationCurve": {"CurveData": q}, "PositionCurve": {"CurveData": p}}]
    return {"Animations": [{"Name": "walk", "TrackGroups": [{"Name": "root", "TransformTracks": tracks}]}]}


def chain_skeleton():
    """root -> cog (y 4) -> head (y 6) and cog -> part (y 7): bind worlds are translations."""
    sk = sd.Skeleton.__new__(sd.Skeleton)
    sk.names = ["root", "cog", "head", "part"]
    sk.parent = [-1, 0, 1, 2]
    sk.B = [sd.trans(np.array(p, float)) for p in ([0, 0, 0], [0, 4, 0], [0, 6, 0], [0, 7, 0])]
    sk.Binv = [np.linalg.inv(m) for m in sk.B]
    sk.idx = {n: i for i, n in enumerate(sk.names)}
    return sk


class ClipWriterRoundTrip(unittest.TestCase):
    def test_moving_and_held_tracks_decode(self):
        tree = curve_templates()
        w = sd.ClipWriter(tree)
        tr = {"OrientationCurve": {}, "PositionCurve": {}}
        ts = sd.sample_times(0.5)
        qs = [sd.mat_q(sd.euler(0, 90 * t, 0)) for t in ts]
        ps = [[t, 2 * t, 0.0] for t in ts]
        w.set_track(tr, qs, ps, ts)
        oc, pc = curves.decode(tr["OrientationCurve"]["CurveData"]), curves.decode(tr["PositionCurve"]["CurveData"])
        self.assertEqual((oc.kind, oc.degree, pc.kind), ("DaK32fC32f", 1, "DaK32fC32f"))
        got = curves.sample(pc, 0.25)
        np.testing.assert_allclose(got, [0.25, 0.5, 0.0], atol=1e-5)
        q = curves.sample(oc, 0.5)
        np.testing.assert_allclose(sd.qmat(q), sd.euler(0, 45, 0), atol=1e-4)
        # a track that does not move is written as constants
        w.set_track(tr, [qs[0]] * 3, [[1, 2, 3]] * 3, ts[:3])
        oc, pc = curves.decode(tr["OrientationCurve"]["CurveData"]), curves.decode(tr["PositionCurve"]["CurveData"])
        self.assertEqual((oc.kind, pc.kind), ("D4Constant32f", "D3Constant32f"))
        np.testing.assert_allclose(curves.sample(pc, 0.1), [1, 2, 3])

    def test_hemisphere_and_missing_type(self):
        w = sd.ClipWriter(curve_templates())
        tr = {"OrientationCurve": {}, "PositionCurve": {}}
        q0 = sd.mat_q(sd.euler(0, 10, 0))
        w.set_track(tr, [q0, -q0 * 0.999 + 0.001], [[0, 0, 0], [0, 0, 1]], [0.0, 1.0])
        c = tr["OrientationCurve"]["CurveData"].obj["Controls"]
        a, b = np.array([x["Real32"] for x in c]).reshape(2, 4)
        self.assertGreater(float(a @ b), 0)          # the second key was flipped to the first one's side
        tree = curve_templates()
        tree["Animations"][0]["TrackGroups"][0]["TransformTracks"].pop(1)
        tree["Animations"][0]["TrackGroups"][0]["TransformTracks"][0]["PositionCurve"]["CurveData"] = \
            tree["Animations"][0]["TrackGroups"][0]["TransformTracks"][0]["OrientationCurve"]["CurveData"]
        with self.assertRaises(SystemExit):
            sd.ClipWriter(tree)

    def test_sample_times(self):
        t = sd.sample_times(0.76)
        self.assertEqual(t[0], 0.0)
        self.assertAlmostEqual(t[-1], 0.76)
        self.assertTrue(np.all(np.diff(t) > 0))


class PoseMaths(unittest.TestCase):
    def test_quaternion_round_trip_and_slerp(self):
        for r in ((10, 20, 30), (170, -40, 5), (0, 179, 0)):
            R = sd.euler(*r)
            np.testing.assert_allclose(sd.qmat(sd.mat_q(R)), R, atol=1e-9)
        Ra, Rb = sd.euler(0, 0, 0), sd.euler(0, 80, 0)
        np.testing.assert_allclose(sd.slerp_m(Ra, Rb, 0.0), Ra, atol=1e-9)
        np.testing.assert_allclose(sd.slerp_m(Ra, Rb, 1.0), Rb, atol=1e-9)
        np.testing.assert_allclose(sd.slerp_m(Ra, Rb, 0.5), sd.euler(0, 40, 0), atol=1e-9)

    def test_children_follow_and_lift(self):
        sk = chain_skeleton()
        D = {sk.idx["cog"]: sd.about(sd.euler(90, 0, 0), sk.B[1][:3, 3])}
        _loc, W = sk.pose(D, lift=np.array([0.0, 1.0, 0.0]))
        # cog turned 90 deg about x at its joint: the head, 2 above it, goes to +z by 2 (and 1 up from the lift)
        np.testing.assert_allclose(W[sk.idx["head"]][:3, 3], [0, 5, 2], atol=1e-9)
        np.testing.assert_allclose(W[sk.idx["cog"]][:3, 3], [0, 5, 0], atol=1e-9)

    def test_cap_rest_sits_on_the_ground(self):
        capb = np.array([[0, 7, 0], [1, 7.5, 0], [-1, 7.5, 0], [0, 8, 1.0]])
        cb = capb.mean(0)
        M, centre, _R = sd.cap_rest(capb, cb, {"cap": {"rot": [90, 0, 0], "centre": [2, 3], "sink": 0.1, "scale": 2}})
        Q = (M @ np.hstack([capb, np.ones((4, 1))]).T).T[:, :3]
        self.assertAlmostEqual(Q[:, 1].min(), -0.1)
        np.testing.assert_allclose(Q.mean(0)[[0, 2]], [2, 3], atol=1e-9)


class DeathConfig(unittest.TestCase):
    def setUp(self):
        self.saved = (list(sd.DEATH), sd.LENGTH, sd.DETACH, sd.LAND, sd.PIVOT_Z, sd.HEAD, sd.COG)

    def tearDown(self):
        sd.DEATH, sd.LENGTH, sd.DETACH, sd.LAND, sd.PIVOT_Z, sd.HEAD, sd.COG = self.saved[0], *self.saved[1:]

    def test_length_from_classic_frames_scales_keys(self):
        with mock.patch.object(common, "MONSTER", monster(death={"dt_frames": 30, "dt_fps": 25})):
            sd.configure()
        self.assertAlmostEqual(sd.LENGTH, 1.2)
        self.assertAlmostEqual(sd.DEATH[-1][0], 1.2)
        self.assertAlmostEqual(sd.DETACH, 0.30 * 1.2 / 0.76)
        self.assertAlmostEqual(float(sd.shell_show_default()), 1.2 * 0.74 / 0.76, places=3)

    def test_own_keys_and_names(self):
        keys = [[0.0, {"cog": [0, 0, 0]}], [0.5, {"cog": [-80, 0, 0]}]]
        with mock.patch.object(common, "MONSTER", monster(death={"keys": keys, "detach": 0.2, "land": 0.4,
                                                                 "head_bone": "skull"})):
            sd.configure()
        self.assertEqual((sd.LENGTH, sd.DETACH, sd.LAND, sd.HEAD), (0.5, 0.2, 0.4, "skull"))
        sk = chain_skeleton()
        D = sd.body_motion(sk, 0.5)
        self.assertIn(sk.idx["cog"], D)

    def test_timing_from_frames_json_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            meta = {"dt": {"frames": 19, "speed": 256, "box": [-40, -87, 40, 10]},
                    "dd": {"frames": 1, "speed": 256, "box": [-70, -51, 80, 60]}}
            Path(d, "frames.json").write_text(json.dumps(meta))
            with mock.patch.object(common, "MONSTER", monster()):
                self.assertEqual(sd.dt_timing(d), (19, 25.0, 36.0))
                with tempfile.TemporaryDirectory() as empty, self.assertRaises(SystemExit):
                    sd.dt_timing(empty)
            with mock.patch.object(common, "MONSTER", monster(clipfit={})):
                with self.assertRaises(SystemExit):
                    sd.part_rule()

    def test_classic_curve_of_a_falling_part(self):
        with tempfile.TemporaryDirectory() as d:
            n = 5
            (Path(d) / "dt").mkdir()
            (Path(d) / "dd").mkdir()
            for f in range(n + 1):
                for di in range(8):
                    im = np.zeros((60, 40, 4), np.uint8)
                    im[50:58, 10:30] = (90, 90, 90, 255)            # a grey body on the ground
                    y = 5 + 8 * f
                    im[y:y + 4, 12:20] = (20, 30, 220, 255)         # the blue part, falling
                    folder, name = ("dd", "d%d_f00.png" % di) if f == n else ("dt", "d%d_f%02d.png" % (di, f))
                    Image.fromarray(im).save(Path(d) / folder / name)
            with mock.patch.object(common, "MONSTER", monster()):
                t, cap, _top = sd.classic_curve(d, frames=n, fps=25.0, dd_dy=0)
        self.assertEqual(len(t), n + 1)
        np.testing.assert_allclose(cap, np.linspace(0, 1, n + 1), atol=1e-9)


class ClipFitHelpers(unittest.TestCase):
    def test_templates_file(self):
        tpl = cf.CFG["templates"]["biped_d2r"]
        names = [p["name"] for p in tpl["params"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("Larm_fwd", names)
        self.assertEqual(cf.CFG["clip_names"]["wl"], "walk")
        for p in tpl["params"]:
            self.assertLess(p["lo"], p["hi"])

    def test_mcfg_defaults_and_alias(self):
        with mock.patch.object(common, "MONSTER", monster(clipfit={"caprim": "cam.json", "part_mask": RULE})):
            m = cf.mcfg()
        self.assertEqual((m["camera"], m["template"], m["shell_bone"]), ("cam.json", "biped_d2r", "shell"))
        # no camera file: the ideal 8-direction ring, 45 degrees apart clockwise from "down", turned by az0
        with mock.patch.object(common, "MONSTER", monster(clipfit={"az0": 3.0})):
            self.assertIsNone(cf.camera_file())
        from hdkit.monster3d import s5_corpsefit
        with mock.patch.object(common, "MONSTER", monster(clipfit={"az0": 3.0})):
            az = s5_corpsefit.camera_azimuths(None)
        self.assertEqual(az[4], 3.0)          # down
        self.assertEqual(az[0], 48.0)         # down-left, next clockwise
        self.assertEqual(sorted(az), list(range(8)))

    def test_template_motions(self):
        rig = cf.Rig.__new__(cf.Rig)
        rig.sk = chain_skeleton()
        rig.tpl = {"base": {"head": [0, 0, 10]},
                   "params": [{"name": "cog_rx", "bones": {"cog": [1, 0, 0]}, "lo": -90, "hi": 90, "step": 10},
                              {"name": "tx", "translate": [1, 0, 0], "lo": -2, "hi": 2, "step": 0.5}]}
        rig.P = rig.tpl["params"]
        rig.cog = rig.sk.idx["cog"]
        D = rig.motions(np.array([90.0, 1.5]))
        p = D[rig.cog] @ np.array([0, 6, 0, 1.0])       # a point 2 above the cog joint
        np.testing.assert_allclose(p[:3], [1.5, 4, 2], atol=1e-9)
        np.testing.assert_allclose(D[rig.sk.idx["head"]][:3, :3], sd.euler(0, 0, 10), atol=1e-12)

    def test_masks_scores_and_tilt(self):
        im = np.zeros((40, 40, 4), np.uint8)
        im[10:20, 10:30] = (10, 20, 200, 255)
        im[12, 12] = (200, 200, 200, 255)                   # a speck inside: the hull fills it
        im[35, 35] = (10, 20, 200, 255)                     # a far single pixel: left out
        m = cf.part_mask(im, RULE)
        self.assertTrue(m[12, 12])
        self.assertFalse(m[35, 35])
        self.assertAlmostEqual(cf.iou(m, m), 1.0)
        self.assertEqual(cf.iou(np.zeros((3, 3), bool), np.zeros((3, 3), bool)), 0.0)
        yy, xx = np.mgrid[:60, :60]
        flat = ((xx - 30) / 20.0) ** 2 + ((yy - 30) / 5.0) ** 2 <= 1
        ang, hw = cf.tilt(flat)
        self.assertAlmostEqual(abs(ang), 0.0, places=5)
        self.assertLess(hw, 0.5)
        s = cf.splat(np.array([5.0, 6.0]), np.array([5.0, 5.0]), (10, 10))
        self.assertTrue(s[5, 5] and s[5, 6])
        self.assertTrue(np.isnan(cf.p50([np.nan])))
        self.assertEqual(cf.p50([1, np.nan, 3, 2]), 2.0)

    def test_catmull_through_keys_and_loop(self):
        P = np.array([[0.0], [1.0], [4.0], [9.0]])
        np.testing.assert_allclose(cf.catmull(P, [0, 1, 2, 3], False)[:, 0], [0, 1, 4, 9])
        self.assertAlmostEqual(cf.catmull(P, [3.999], True)[0, 0], 0.0, places=1)
        self.assertAlmostEqual(cf.catmull(P, [10.0], False)[0, 0], 9.0)

    def test_mode_timing(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "frames.json").write_text(json.dumps({"a1": {"frames": 15, "speed": 200, "keys": [0] * 7 + [1] + [0] * 7}}))
            n, fps, hits = cf.mode_timing(d, "a1")
        self.assertEqual((n, hits), (15, [7]))
        self.assertAlmostEqual(fps, 25 * 200 / 256)


class CorpseFit(unittest.TestCase):
    def test_calibrate_recovers_scale(self):
        f = cfit.Fit.__new__(cfit.Fit)
        ang = np.linspace(0, 2 * np.pi, 40, endpoint=False)
        f.capb = np.c_[np.cos(ang), np.full(40, 7.0), np.sin(ang)]    # a unit disc on top of the body
        f.az = {d: 45.0 * d for d in cfit.DIRS}
        f.frames = {"nu": {"box": [-50, -100, 50, 10]}}
        f.dd_dy = 0.0
        k_true = 10.0
        f.nucap = {}
        for d in cfit.DIRS:
            px, py = f.proj(f.capb, d, k_true, 35.0, "nu")
            m = np.zeros((120, 120), bool)
            m[int(py.min()):int(py.max()) + 1, int(round(px.min())):int(round(px.max())) + 1] = True
            f.nucap[d] = m
        cal = f.calibrate(35.0)
        self.assertAlmostEqual(cal["k"], k_true * 21 / 20, delta=0.6)  # (width + 1 px) / (model width)

    def test_pose_vector_and_boxes(self):
        x = np.array([0, 0, -90, 0, 0, 20, 40, 30, 60, 0, 0, -90, 5.0, 0, 0.1, 1.0, 0.0])
        pose = cfit.to_pose(x)
        self.assertEqual(pose["cog"], [0, 0, -90])
        self.assertEqual(pose["cap"]["centre"], [5.0, 0])
        self.assertEqual(len(cfit.NAMES), len(cfit.STEP))
        with mock.patch.object(common, "MONSTER", monster(corpse={"dd_part_box": {"3": [1, 2, 3, 4]}})):
            self.assertEqual(cfit.dd_part_box(), {3: (1, 2, 3, 4)})
        with tempfile.TemporaryDirectory() as d:
            Path(d, "cam.json").write_text(json.dumps({"views": {"4": {"az": 12.5}}}))
            self.assertEqual(cfit.camera_azimuths(Path(d, "cam.json")), {4: 12.5})

    def test_rim_mask_and_basis(self):
        im = np.zeros((20, 20, 4), np.uint8)
        im[5:9, 5:12] = (0, 0, 255, 255)
        with mock.patch.object(common, "MONSTER", monster()):
            m = cfit.rim_mask(im)
        self.assertEqual(int(m.sum()), 28)
        r, u, w = cam_fit.basis(30, 35)
        np.testing.assert_allclose([r @ u, r @ w, u @ w], 0, atol=1e-12)


if __name__ == "__main__":
    unittest.main()
