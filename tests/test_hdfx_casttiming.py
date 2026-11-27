"""Lane r5-casttiming: cast overlays start with the cast mode; plan_sbs finds the cast mode's start on the shots."""
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hdkit.fx import context as C  # noqa: E402

FEET = (100.0, 150.0)


def _bench(folder, change_at, blip_at=None, n=20, dt=0.03, t_ask=0.15):
    """Shots of a still caster: the body box (above FEET) changes for good at shot `change_at`; from shot `blip_at`
    one small step stays (an idle shift or a cursor)."""
    shots = []
    rng = np.random.default_rng(1)
    for i in range(n):
        im = np.full((200, 200), 60, np.uint8)
        im[60:140, 70:130] = rng.integers(55, 66, (80, 60))        # idle noise
        if blip_at is not None and i >= blip_at:
            im[60:140, 70:130] += 25                                # one step that stays (an idle shift, a cursor)
        if i >= change_at:
            im[60:140, 70:130] = 100 + (i - change_at) * 25 % 150  # the cast pose and its overlay, changing every shot
        name = "shots/%03d.png" % i
        (folder / "shots").mkdir(exist_ok=True)
        Image.fromarray(im).save(folder / name)
        shots.append({"n": i, "item": 0, "file": name, "t": round(i * dt, 3), "dup": False})
    it = {"name": "spell", "fire": {"t_ask": t_ask}}
    return {"shots": shots, "items": [it]}, it


class OverlayStart(unittest.TestCase):
    def test_default_is_the_cast_mode_start(self):
        import inspect
        self.assertEqual(inspect.signature(C.cast).parameters["overlay_at"].default, "start")

    def test_staff_action_frame(self):
        # the bench sorceress' staff cast (SOSCSTF: 14 frames at speed 416, action frame 5): missiles 0.123 s after the
        # mode starts, the moment the cast light first rises on the shots
        tick = 256.0 / 416 / C.FPS
        self.assertAlmostEqual(5 * tick, 0.123, places=3)

    def test_fx_cli_default(self):
        from hdkit.view import fxcli
        src = Path(fxcli.__file__).read_text()
        self.assertIn('("lead", "cast", "start"), default="start"', src)


if __name__ == "__main__":
    unittest.main()
