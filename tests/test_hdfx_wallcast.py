"""Lane r4-firewall: Fire Wall as a line of missiles (fx/wallcast.py) and the empty skill filter (context.skill_pick)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np  # noqa: E402

from hdkit.fx import context as C  # noqa: E402
from hdkit.fx import wallcast  # noqa: E402


class LayoutTest(unittest.TestCase):
    def test_game_track_g2(self):
        # r3-integrate g2: Fire Wall level 1 aimed 6 sub-tiles along +x; the server's firewall missiles (class 69) stood at
        # T + (0, -4..+5), 10 of them
        lay = wallcast.layout((6, 0), 7, 2, 12, 1)
        self.assertEqual(lay["target"], (6, 0))
        self.assertEqual(sorted(f["at"] for f in lay["fires"]), [(6, y) for y in range(-4, 6)])
        self.assertEqual(lay["fires"][0]["frame"], 0)            # srvmissileb on the target at the cast
        self.assertEqual(max(f["frame"] for f in lay["fires"]), 8)

    def test_perpendicular(self):
        lay = wallcast.layout((0, -5), 7, 2, 12, 3)
        xs = {f["at"][1] for f in lay["fires"]}
        self.assertEqual(xs, {-5})                                 # the wall runs across the cast direction
        self.assertGreater(len(lay["fires"]), 10)                  # longer at a higher level

    def test_drops(self):
        self.assertEqual(wallcast.maker_drops((1, 0), 4, 0.5), [(1, (1, 0)), (3, (2, 0))])
        self.assertEqual(wallcast.maker_drops((1, 0), 0, 0.5), [])

    def test_range_formula(self):
        self.assertEqual(wallcast.range_of({"range": 7, "levrange": 2, "range_expr": "7"}, 1), 9.0)
        self.assertEqual(wallcast.range_of({"range": 0, "range_expr": "7+2*lvl"}, 3), 13.0)
        self.assertEqual(wallcast.range_of({"range": 90.0}, 5), 90.0)
        self.assertEqual(C.missile_facts({"range": "7+2*lvl", "levrange": "2"})["range_expr"], "7+2*lvl")


class SkillPickTest(unittest.TestCase):
    DEFN = {"entities": [
        {"name": "entity_vfx1", "components": [
            {"type": "UnitSourceSkillFilterComponent", "sourceSkill": ""},
            {"type": "VfxDefinitionComponent", "filename": "a/big.particles"}]},
        {"name": "entity_vfx_firewall", "components": [
            {"type": "UnitSourceSkillFilterComponent", "sourceSkill": "Fire Wall"},
            {"type": "VfxDefinitionComponent", "filename": "a/firewall.particles"}]},
        {"name": "entity_plain", "components": [{"type": "VfxDefinitionComponent", "filename": "a/plain.particles"}]}]}

    def eff(self):
        return C.MissileEffects("firewall", "firewall", None, None, C.bindings_of(self.DEFN))

    def files(self, skill):
        return [b.file for b in self.eff().vfx(skill)]

    def test_named_skill_replaces_default(self):
        self.assertEqual(self.files("Fire Wall"), ["a/firewall.particles", "a/plain.particles"])

    def test_default_without_a_match(self):
        self.assertEqual(self.files("Blaze"), ["a/big.particles", "a/plain.particles"])
        self.assertEqual(self.files(None), ["a/big.particles", "a/plain.particles"])


class ChildrenTest(unittest.TestCase):
    def test_children(self):
        maker = C.MissileEffects("firewallmaker", "k", None, None, [C.Binding("vfx", "m", file="maker.particles")],
                                 C.missile_facts({"vel": "12", "range": "7+2*lvl"}))
        fire = C.MissileEffects("firewall", "k", None, None, [C.Binding("vfx", "f", file="fire.particles")],
                                C.missile_facts({"range": "90"}))
        sk = C.SkillEffects("Fire Wall", "SC", "", [C.SkillRef("cltmissilea,srvmissilea", "firewallmaker", "missile", maker),
                                                   C.SkillRef("srvmissileb", "firewall", "missile", fire)])
        ch, info = wallcast.children(sk, (6, 0), 1, 0.28, None)
        fires = [d for _t, d in ch if d.name.startswith("missile:firewall#")]
        self.assertEqual(len(fires), 10)
        self.assertEqual(info["count"], 10)
        d = fires[-1]
        self.assertAlmostEqual(d.t_end, 90 / C.FPS)                # a fire lives its Range, standing still
        p0, p1 = d.position(d.start), d.position(d.start + 2.0)
        self.assertTrue(np.allclose(p0, p1))
        self.assertTrue(all(t >= 0.28 for t, _d in ch))


if __name__ == "__main__":
    unittest.main()
