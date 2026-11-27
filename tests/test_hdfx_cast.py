"""Lane cast: hdkit.fx.context cast pieces (D2 seed, Charged Bolt path, skill calc, animdata, cast driver). No game files."""
import struct
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdkit.fx import context as C  # noqa: E402


class SeedAndPath(unittest.TestCase):
    def test_seed_is_the_game_lcg(self):
        s = C.D2Seed.low(5)
        v = 5 * 0x6AC690C5 + 666
        self.assertEqual(s.roll(), v & 0xFFFFFFFF)
        self.assertEqual(s.hi, v >> 32)

    def test_chargedbolt_path(self):
        pts = C.chargedbolt_path((0, 0), (8, -8), 77, C.D2Seed.low(3))
        self.assertEqual(len(pts), 77 // 2 + 1)
        main = C.direction8(8, -8)
        self.assertEqual(main, 7)
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            d = C.D2_DIR_OFFSETS.index(((x1 - x0) // 2, (y1 - y0) // 2))
            self.assertIn((d - main) % 8, (0, 1, 7))
        again = C.chargedbolt_path((0, 0), (8, -8), 77, C.D2Seed.low(3))
        self.assertEqual(pts, again)
        other = C.chargedbolt_path((0, 0), (8, -8), 77, C.D2Seed.low(4))
        self.assertNotEqual(pts, other)

    def test_turn_table(self):
        self.assertEqual(len(C._CB_TURNS), 32)
        self.assertEqual(C._CB_TURNS[-2:], (-1, 1))


class Calc(unittest.TestCase):
    def test_charged_bolt_count(self):
        row = {"param1": "3", "param2": "1"}
        self.assertEqual(C.skill_calc("min(24,ln12)", row, 1), 3)
        self.assertEqual(C.skill_calc("min(24,ln12)", row, 10), 12)
        self.assertEqual(C.skill_calc("min(24,ln12)", row, 40), 24)
        self.assertEqual(C.skill_calc("par1+lvl*2", row, 2), 7)

    def test_unsupported(self):
        with self.assertRaises(ValueError):
            C.skill_calc("skill('Lightning'.blvl)", {}, 1)


def _animdata(records):
    buckets = {}
    for name, frames, speed, events in records:
        b = sum(name.upper().encode()) % 256
        ev = bytes(events) + bytes(144 - len(events))
        buckets.setdefault(b, []).append(name.encode().ljust(8, b"\0") + struct.pack("<II", frames, speed) + ev)
    out = b""
    for b in range(256):
        recs = buckets.get(b, [])
        out += struct.pack("<I", len(recs)) + b"".join(recs)
    return out


class Animdata(unittest.TestCase):
    def test_record(self):
        data = _animdata([("SOSCBOW", 14, 256, [0] * 7 + [1]), ("SONUBOW", 8, 256, [])])
        self.assertEqual(C.animdata_record(data, "soscbow"), {"frames": 14, "speed": 256, "action": 7})
        self.assertIsNone(C.animdata_record(data, "SOSCHTH"))
        self.assertIsNone(C.animdata_record(data, "SONUBOW")["action"])


def _skill(srvdofunc, count_calc="min(24,ln12)"):
    me = C.MissileEffects("chargedbolt", "chargedbolt", None, None,
                          [C.Binding(kind="vfx", entity="entity_vfx1", file="data/hd/vfx/x/bolt.particles")],
                          C.missile_facts({"vel": "12", "maxvel": "12", "range": "98"}), (0, 0, 0, 1), [], [])
    ov = C.OverlayEffects("light_cast_1", "light_cast_1", "sorceress", "x.json",
                          [C.Binding(kind="vfx", entity="entity_root", file="data/hd/vfx/x/cast.particles")], None, [])
    row = {"srvdofunc": str(srvdofunc), "calc1": count_calc, "param1": "3", "param2": "1"}
    refs = [C.SkillRef("srvmissilea", "chargedbolt", "missile", me), C.SkillRef("castoverlay", "light_cast_1", "overlay", ov)]
    return C.SkillEffects("Charged Bolt", "SC", "xx", refs, [], row)


class Cast(unittest.TestCase):
    def test_charged_bolt_cast(self):
        d = C.cast(_skill(17), None, (8, -8), level=2, seed=0, anim={"frames": 14, "speed": 256, "action": 7})
        info = d.cast_info
        self.assertEqual(info["count"], 4)
        self.assertAlmostEqual(info["launch_s"], 0.28)
        kinds = [c.name.split(":")[0] for _t, c in d.children]
        self.assertEqual(kinds.count("missile"), 4)
        self.assertEqual(kinds.count("overlay"), 1)
        ov = [c for _t, c in d.children if c.name.startswith("overlay")][0]
        self.assertEqual(ov.start, 0.0)                    # with the cast mode (r5-casttiming, game shots)
        d0 = C.cast(_skill(17), None, (8, -8), anim={"frames": 14, "speed": 256, "action": 7}, overlay_at="lead")
        self.assertAlmostEqual([c for _t, c in d0.children if c.name.startswith("overlay")][0].start,
                               0.28 - C.CAST_OVERLAY_LEAD_S)
        for _t, c in d.children:
            if c.name.startswith("missile"):
                self.assertAlmostEqual(c.start, 0.28)
                self.assertAlmostEqual(c.t_end, 77 / 25.0, places=3)       # life capped at 77 frames
                np.testing.assert_allclose(c.position(c.start), [0, 0, 0], atol=1e-9)   # from the caster
        paths = [tuple(map(tuple, m["path_subtiles"])) for m in info["missiles"]]
        self.assertEqual(len(set(paths)), 4)                                  # each bolt its own walk

    def test_plain_missile(self):
        d = C.cast(_skill(0), None, (12, 0), anim={"frames": 14, "speed": 256, "action": 7})
        self.assertEqual(d.cast_info["count"], 1)
        m = [c for _t, c in d.children if c.name.startswith("missile")][0]
        p = m.position(m.start + 1.0)
        self.assertGreater(p[0], 20.0)
        self.assertAlmostEqual(p[2], 0.0)


class BirthTimeXform(unittest.TestCase):
    """D2R.exe 0xbfdd60/0xc001b0 (lane vmfix): xform uses the effect's transform lerped prev -> now at each particle's
    sub-frame birth time when the run has no payload XForms (lane cast2 removed sim.SPAWN_POS; lane trailre's payload
    XForms rule: tests/test_hdfx_payloadxform.py)."""

    def _ctx(self, frac):
        from types import SimpleNamespace
        now, prev = np.eye(4, dtype=np.float32), np.eye(4, dtype=np.float32)
        now[:3, 3] = [10, 0, 0]
        env = SimpleNamespace(transform=now, prev_transform=prev, birth_frac=frac)
        return SimpleNamespace(env=env, n=0 if frac is None else len(frac))

    def test_position_at_the_birth_time(self):
        from hdkit.fx import vm
        f = vm.ENGINE["xform_l2w_f_masked_float3_int_pCtxS"]
        ctx = self._ctx(np.array([0.25, 1.0, 0.5], np.float32))
        out = f(ctx, np.zeros((1, 3), np.float32), np.array([[17]]))
        np.testing.assert_allclose(out[:, 0], [2.5, 10, 5], atol=1e-5)
        out = f(ctx, np.ones((1, 3), np.float32), np.array([[18]]))      # a direction: no translation
        np.testing.assert_allclose(out, np.ones((3, 3)), atol=1e-6)

    def test_without_birth_times_the_frame_end(self):
        from hdkit.fx import vm
        ctx = self._ctx(None)
        ctx.n = 1
        out = vm.ENGINE["xform_l2w_f_masked_float3_int_pCtxS"](ctx, np.zeros((1, 3), np.float32), np.array([[17]]))
        np.testing.assert_allclose(out.reshape(-1, 3)[0], [10, 0, 0])

    def test_no_spawn_pos(self):
        from hdkit.fx import sim
        self.assertFalse(hasattr(sim, "SPAWN_POS"))


if __name__ == "__main__":
    unittest.main()
