"""Lane calib4: the game's missile motion (D2MOO-read stepped velocity, frame counter), the nova ring, and a regression
check that a cast's overlay draws particles at the release frame (sim only, no GPU; skipped without the game data)."""
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

from hdkit.fx import context as C  # noqa: E402

TABLES = (os.environ.get("HDFX_TABLES") or "/nonexistent")


class SteppedMotion(unittest.TestCase):
    def _nova_missile(self):
        facts = C.missile_facts({"vel": "24", "maxvel": "24", "accel": "-1000", "range": "14"})
        return C.missile("data/hd/x.particles", start=[0, 0, 0], end=[200, 0, 0], row=facts)

    def test_frost_nova_steps(self):
        m = self._nova_missile()
        self.assertTrue(m.stepped)
        sub = [m.distance_at(k / C.FPS) / C.UNITS_PER_SUBTILE for k in range(15)]
        steps = np.diff(sub)
        np.testing.assert_allclose(steps[:4], 1.125, atol=1e-6)            # Vel 24 x 3/64
        np.testing.assert_allclose(steps[4:9], 1.125 - 1000 / 4096, atol=1e-6)  # Accel every 5th frame, raw / 4096
        np.testing.assert_allclose(steps[9:14], 1.125 - 2000 / 4096, atol=1e-6)
        self.assertAlmostEqual(sub[14], 4 * 1.125 + 5 * (1.125 - 1000 / 4096) + 5 * (1.125 - 2000 / 4096), places=6)
        self.assertAlmostEqual(m.t_end, 14 / C.FPS)

    def test_accel_and_maxvel_units(self):
        self.assertAlmostEqual(C.ACCEL_VEL_PER_FRAME * 1000, 1000 / 960)
        self.assertAlmostEqual(C.missile_facts({"vel": "10", "maxvel": "30"})["max_speed_units_s"],
                               30 * C.VEL_UNITS_PER_S / 0.75)

    def test_accelerating_caps_and_holds(self):
        m = C.missile("data/hd/x.particles", start=[0, 0, 0], end=[5000, 0, 0],
                      row=C.missile_facts({"vel": "6", "maxvel": "9", "accel": "500"}))
        v = np.diff([m.distance_at(k / C.FPS) for k in range(200)]) * C.FPS
        self.assertAlmostEqual(v.max(), 9 * C.VEL_UNITS_PER_S / 0.75, places=6)
        self.assertAlmostEqual(v[-1], v.max())

    def test_frame_counts_up(self):
        # the client's MissileCurrentFrame counts up (lane calib5; the server's counter counts down)
        m = self._nova_missile()
        a0, a1 = m.attrs_at(0.0), m.attrs_at(0.2)
        self.assertAlmostEqual(a0["MissileCurrentFrame"][0], 0.0)
        self.assertAlmostEqual(a1["MissileCurrentFrame"][0], 5.0)
        self.assertAlmostEqual(m.attrs_at(5.0)["MissileCurrentFrame"][0], 14.0)


class NovaRing(unittest.TestCase):
    def test_ring_table(self):
        self.assertEqual(len(C.RING_DX), 64)
        self.assertEqual(len(C.RING_DY), 64)
        self.assertEqual((C.RING_DX[0], C.RING_DY[0]), (30, 0))
        self.assertEqual((C.RING_DX[16], C.RING_DY[16]), (0, 30))
        self.assertEqual((C.RING_DX[1], C.RING_DY[1]), (29, 2))
        r = np.hypot(C.RING_DX, C.RING_DY)
        self.assertTrue(np.all(np.abs(r - 30) < 1.5))

    def test_cast_makes_64(self):
        mis = C.MissileEffects("frostnova", "", None, None, [], row=C.missile_facts(
            {"vel": "24", "maxvel": "24", "accel": "-1000", "range": "14"}))
        sk = C.SkillEffects("Frost Nova", "SC", "", [C.SkillRef("srvmissilea", "frostnova", "missile", mis)],
                            row={"srvdofunc": "22"})
        d = C.cast(sk, None, (3, -3))
        ms = [c for _t, c in d.children if c.name.startswith("missile")]
        self.assertEqual(len(ms), 64)
        self.assertEqual(sorted(m.index for m in ms), list(range(64)))
        ends = np.array([m.end_pos for m in ms])
        r = np.hypot(ends[:, 0], ends[:, 2]) / C.UNITS_PER_SUBTILE
        np.testing.assert_allclose(r, 12.087890625, atol=1e-6)


def _casc():
    """The game's CASC data (HDKIT_CASC_GAME, a D2R folder), or None."""
    try:
        from hdkit.view import vfs
        if not os.environ.get("HDKIT_CASC_GAME") or not Path(TABLES).is_dir():
            return None
        return vfs.Layered(vfs.parse_roots(["casc"]))
    except (Exception, SystemExit):  # noqa: BLE001
        return None


class CastOverlayDraws(unittest.TestCase):
    """The caster's overlay of Fire Bolt (fire_cast_1) and Charged Bolt (light_cast_1) has live rendered particles shortly
    after the release frame (lane calib4: a whole-cast sheet once lost the cast flash)."""

    @classmethod
    def setUpClass(cls):
        cls.fs = _casc()
        if cls.fs is None:
            raise unittest.SkipTest("no game data (HDKIT_CASC_GAME + casc, HDFX_TABLES)")
        cls.tb = C.Tables.from_dir(TABLES)

    def _overlay_particles(self, skill, overlay):
        from hdkit.view import fxcast, fxcli
        sk = C.effects_of_skill(self.fs, skill, self.tb)
        drv = C.cast(sk, None, (0, 12))
        found = {}
        for label, ch, i, path in fxcast.items_of(drv):
            if not ch.name.startswith("overlay:") or overlay not in ch.name:
                continue
            data = self.fs.read(C.resolve_particle(self.fs, path) or path)
            sm = fxcli.make_sim(data, 0, ch.layer_transform(ch.start, ch.layers[i]))
            for age in (0.04, 0.12, 0.2):
                fxcast.step_to(sm, ch, i, age)
                st = sm.state()
                found[age] = sum(int(b.count) for b in st.layers.values() if b.renderer_inputs)
        return drv, found

    def test_fire_bolt_cast_flash(self):
        drv, found = self._overlay_particles("Fire Bolt", "fire_cast_1")
        self.assertTrue(found, "no fire_cast_1 overlay child in the Fire Bolt cast")
        self.assertGreater(max(found.values()), 0, found)

    def test_charged_bolt_cast_arcs(self):
        drv, found = self._overlay_particles("Charged Bolt", "light_cast_1")
        self.assertTrue(found, "no light_cast_1 overlay child in the Charged Bolt cast")
        self.assertGreater(max(found.values()), 0, found)


if __name__ == "__main__":
    unittest.main()
