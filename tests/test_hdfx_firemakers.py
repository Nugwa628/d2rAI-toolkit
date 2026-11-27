"""Cast layout from synthetic tables with the stock skill links and the mod missile numbers."""
import json
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
from hdkit.view import vfs  # noqa: E402


class FireMakerTest(unittest.TestCase):
    def cast(self, skill, toward, **kwargs):
        tb = C.Tables({
            "skills": [
                {"skill": "Blaze", "srvdofunc": "23", "srvmissilea": "blaze", "cltmissilea": "firewall",
                 "aurastate": "blaze", "auralencalc": "dm12", "param1": "50", "param2": "500"},
                {"skill": "Immolation Arrow", "srvmissile": "immolationarrow", "calc1": "par1", "param1": "3"},
                {"skill": "Molten Boulder", "srvmissile": "moltenboulderemerge"},
            ],
            "missiles": [
                {"missile": "blaze", "psrvhitfunc": "8", "range": "90+25*lvl"},
                {"missile": "immolationarrow", "psrvhitfunc": "9", "vel": "24", "range": "40",
                 "hitsubmissile1": "immolationfire", "shitcalc1": "75"},
                {"missile": "immolationfire", "range": "100"},
                {"missile": "moltenboulderemerge", "vel": "3", "range": "5", "psrvhitfunc": "48",
                 "hitsubmissile1": "moltenboulder"},
                {"missile": "moltenboulder", "vel": "4", "range": "100", "psrvdofunc": "6",
                 "submissile1": "moltenboulderfirepath"},
                {"missile": "moltenboulderfirepath", "range": "37"},
            ],
            "charstats": [{"class": "Sorceress", "runvelocity": "12"}],
        })
        effects = C.effects_of_skill(vfs.Layered([]), skill, tb)
        return C.cast(effects, toward=toward, **kwargs)

    def test_boulder_emerges_then_drops_once_per_entered_tile(self):
        info = self.cast("Molten Boulder", (6, 0), start_subtile=(100, 60)).cast_info
        emerge, boulder, *fires = info["missiles"]
        self.assertEqual((emerge["frame"], boulder["frame"], boulder["at_subtile"]), (0, 5, [101, 60]))
        self.assertEqual([f["at_subtile"] for f in fires], [[x, 60] for x in range(102, 121)])
        self.assertEqual([f["frame"] for f in fires[:3]], [8, 13, 19])
        self.assertEqual({f["range_frames"] for f in fires}, {37})

    def test_blaze_drops_at_movement_tiles_after_the_cast_mode(self):
        drv = self.cast("Blaze", (3, 0), level=2, anim={"frames": 5, "action": 2})
        self.assertEqual([(f["name"], f["frame"], f["at_subtile"], f["range_frames"])
                          for f in drv.cast_info["missiles"]],
                         [("blaze", 4, [1, 0], 140), ("blaze", 5, [2, 0], 140), ("blaze", 7, [3, 0], 140)])
        self.assertAlmostEqual(drv.children[-1][0], 9 / C.FPS)
        self.assertFalse(self.cast("Blaze", (0, 0)).children)

    def test_immolation_disc_at_impact_with_hit_calc_lifetime(self):
        drv = self.cast("Immolation Arrow", (6, 0), start_subtile=(10, 20))
        arrow, *fires = drv.cast_info["missiles"]
        disc = {(x, y) for x in range(13, 20) for y in range(17, 24) if (x - 16)**2 + (y - 20)**2 <= 9}
        self.assertEqual({tuple(f["at_subtile"]) for f in fires}, disc)
        self.assertEqual({(f["frame"], f["range_frames"]) for f in fires}, {(5, 75)})
        self.assertEqual(len(fires), 29)
        center = next(d for _t, d in drv.children[1:] if np.allclose(d.end_pos, [12, 0, 0]))
        self.assertAlmostEqual(center.t_end, 3.0)
        self.assertEqual(arrow["name"], "immolationarrow")
        json.dumps(drv.cast_info)  # rendered metrics preserve numeric sub-tile coordinates
        negative = self.cast("Immolation Arrow", (-5, 0)).cast_info["missiles"][1:]
        self.assertEqual({f["frame"] for f in negative}, {5})
        expired = self.cast("Immolation Arrow", (100, 0)).cast_info["missiles"][1:]
        self.assertEqual({f["frame"] for f in expired}, {40})
        self.assertEqual([f["at_subtile"] for f in expired if f["at_subtile"][1] == 0],
                         [[x, 0] for x in range(42, 49)])


if __name__ == "__main__":
    unittest.main()
