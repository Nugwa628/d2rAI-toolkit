"""Lane calib3: the missile driver's game attributes and decelerating flight (no game files needed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
from hdkit.fx import context as C  # noqa: E402


class MissileDriverCalib3(unittest.TestCase):
    def _drv(self, **kw):
        return C.missile("data/hd/x.particles", start=[0, 0, 0], end=[60, 0, 0], **kw)

    def test_attrs_at(self):
        d = self._drv(speed=56.25, range_frames=14)
        a = d.attrs_at(0.2, 3)
        self.assertAlmostEqual(a["MissileFrames"][0], 14.0)
        self.assertAlmostEqual(a["MissileCurrentFrame"][0], 5.0)                  # counts up (lane calib5, game shots)
        self.assertEqual(a["MissileIndex"][0], 3)
        self.assertAlmostEqual(d.attrs_at(5.0)["MissileCurrentFrame"][0], 14.0)      # held at the end of the range

    def test_deceleration_stops_and_never_reverses(self):
        d = self._drv(speed=10.0, accel=-20.0)
        self.assertAlmostEqual(d.distance_at(0.5), 2.5)                         # stops at t = 0.5
        self.assertAlmostEqual(d.distance_at(3.0), 2.5)

    def test_accel_unit(self):
        # Frost Nova (Vel 24, Accel -1000): 1000 / 960 Vel per frame on average (lane calib4: read from the game's code;
        # calib3's one-sample fit was 0.704), not 1000
        self.assertAlmostEqual(C.ACCEL_UNITS_PER_S2 * -1000.0 / (C.FPS * C.VEL_UNITS_PER_S), -1000.0 / 960.0, places=6)


if __name__ == "__main__":
    unittest.main()
