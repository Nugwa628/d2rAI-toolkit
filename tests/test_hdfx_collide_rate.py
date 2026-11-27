"""Lane r3-intersect2: the game's 1-unit collision ray and the frame rate (confirmed in game: Cold Blood's ice chunks and
the barricade wall's debris rest ~1 unit above the floor). A particle steps p += v dt; before each step the game's query
looks 1 unit ahead along v's direction (fx/collide.intersect, game mode); on a hit it stays put and bounces. Debris faster
than ~1 unit per frame can pass the floor between two queries: it tunnels at 30 fps where 60 fps catches it."""
import sys
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdkit.fx import collide as C  # noqa: E402


def fall(speed, fps, h0=3.0, secs=1.0, bounce=0.4, mode="game"):
    """Lowest y and final y of a particle thrown down at `speed` units/s from h0, the physics template's step."""
    w = C.floor_world(0.0, mode=mode)
    p, v, dt = np.array([0.0, h0, 0.0]), np.array([0.0, -speed, 0.0]), 1.0 / fps
    low = p[1]
    for _ in range(int(secs * fps)):
        n = np.linalg.norm(v)
        d = v / n if n > 0 else np.array([1.0, 0.0, 0.0])
        r = C.intersect(w, p, d, n * dt if mode == "authored" else 1.0)[0]
        if np.isfinite(r[3]) and r[3] <= (n * dt if mode == "authored" else 1.0):
            nn = r[:3].astype(float)
            v = (v - 2 * np.dot(v, nn) * nn) * bounce
            if mode == "authored":
                p = p + d * float(r[3])
        else:
            v = v + np.array([0.0, -9.8, 0.0]) * dt
            p = p + v * dt
        low = min(low, p[1])
    return low, p[1]


class RateDependence(unittest.TestCase):
    def test_rests_about_one_unit_up(self):
        low, end = fall(5.0, 60)
        self.assertGreater(low, 0.0)
        self.assertGreater(end, 0.5)
        self.assertLessEqual(end, 1.05)

    def test_authored_reading_rests_on_the_floor(self):
        _low, end = fall(5.0, 60, secs=4.0, mode="authored")
        self.assertLess(end, 0.1)

    def test_fast_debris_tunnels_at_30_fps_not_60(self):
        low30, _ = fall(45.0, 30)          # 1.5 units per frame: steps over the 1-unit look-ahead
        low60, _ = fall(45.0, 60)          # 0.75 units per frame: caught
        self.assertLess(low30, 0.0)
        self.assertGreater(low60, 0.0)


if __name__ == "__main__":
    unittest.main()
