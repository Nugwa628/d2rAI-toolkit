"""Lane calib5: liquid inputs as the game's liquid VS reads them (capture calib5 s3 eid 8331): EdgeColor and the
mask-distortion UV no longer share an attribute, the Blend* distortion pair is not drawn; the client's missile frame
counts up; cast overlays lead the release. No game files needed."""
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
from hdkit.fx import packers as pk  # noqa: E402


class LiquidInputs(unittest.TestCase):
    def test_edge_colour_and_mask_distortion_apart(self):
        ins = {"Liquid.EdgeColor": np.array([[0.1, 0.2, 0.3]]), "Liquid.CenterColor": np.array([[0.4, 0.5, 0.6]]),
               "UVDistortion.MaskDistortUVScaleAndOffset": np.array([[0.5, 0.5, 0.0, 0.0]]),
               "UVDistortion.MaskDistortIntensity": np.array([[0.25]]),
               "UVDistortion.BlendDistortUVScaleAndOffset": np.array([[3.0, 3.0, 0.0, 0.0]]),
               "UVDistortion.BlendDistortIntensity": np.array([[0.9]]),
               "Liquid.EdgeHardness": np.array([[0.804]]), "Liquid.EdgeControl": np.array([[0.827]])}
        b = pk.particle_block(ins, 1, "liquid")
        o = lambda k: pk.OFF[k][0] - 10                    # noqa: E731
        np.testing.assert_allclose(b[0, o("so_db"):o("so_db") + 3], [0.1, 0.2, 0.3], atol=1e-6)    # EdgeColor
        np.testing.assert_allclose(b[0, o("so_dm"):o("so_dm") + 4], [0.5, 0.5, 0.0, 0.0], atol=1e-6)  # mask UV
        self.assertAlmostEqual(float(b[0, o("dist") + 1]), 0.25, places=6)                      # mask intensity
        self.assertAlmostEqual(float(b[0, o("dist")]), 0.0, places=6)                           # blend pair dropped
        np.testing.assert_allclose(b[0, o("so_blend") + 2:o("so_blend") + 4], [0.804, 0.827], atol=1e-6)
        u = pk.particle_block(ins, 1, "uber")                                                   # uber keeps its map
        np.testing.assert_allclose(u[0, o("so_db"):o("so_db") + 4], [3.0, 3.0, 0.0, 0.0], atol=1e-6)


class LayerBatch(unittest.TestCase):
    def test_duplicate_layer_names(self):
        from types import SimpleNamespace as NS
        from hdkit.fx import render as R
        a = NS(count=3, renderer_inputs={4: {}}, fields={})
        b = NS(count=0, renderer_inputs={5: {}}, fields={})
        st = NS(layers={"Bubble": a, "Bubble#7": b})
        self.assertIs(R.layer_batch(st, NS(layer="Bubble", index=5)), b)    # not slot 5's particles
        self.assertIs(R.layer_batch(st, NS(layer="Bubble", index=4)), a)
        self.assertIs(R.layer_batch(NS(layers={"Core": a}), NS(layer="Core", index=9)), a)   # name fallback


class Calib5Timing(unittest.TestCase):
    def test_counter_up_and_overlay_lead(self):
        self.assertFalse(C.MISSILE_FRAME_COUNTS_DOWN)
        self.assertAlmostEqual(C.CAST_OVERLAY_LEAD_S, 0.08)


if __name__ == "__main__":
    unittest.main()
