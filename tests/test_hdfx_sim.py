"""Tests of the particle life/kill rule of hdkit.fx.sim, as read from D2R.exe (lane vmfix, docs/hdfx-vm-re.md
"Life, death and the dying evolve").

The helpers and Sim._page are tested on a synthetic effect (no game files); with the current-build corpus present
(HDFX_D2R_ROOT_95032 or the default path) Charged Bolt and Corpse Explosion are also checked end to end.
Run: python -m unittest tests.test_hdfx_sim"""
import copy
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
from hdkit.fx import sim as S  # noqa: E402
from tests.test_hdfx_vm import synthetic_effect  # noqa: E402

F32, I32 = np.float32, np.int32
D2R = Path((os.environ.get("HDFX_D2R_ROOT_95032") or "/nonexistent"))
MISSILES = D2R / "hd/vfx/particles/missiles"
INF = np.inf


def col(*v):
    return np.asarray(v, F32).reshape(-1, 1)


class Helpers(unittest.TestCase):
    def test_newborn_life(self):
        # 0xaef3d0: lifeRatio + dt x invLife where invLife is finite, else exactly 1 (whatever dt is, 0 included)
        lr = col(0, 0, 0, 0, 0.25, 0)
        inv = col(INF, INF, -INF, np.nan, 2, 2)
        dt = col(0, 0.02, 0.02, 0.02, 0.1, 0)
        np.testing.assert_array_equal(S.newborn_life(lr, inv, dt), col(1, 1, 1, 1, F32(0.25) + F32(0.1) * 2, 0))

    def test_dead_mask(self):
        # 0xb814c0: lifeRatio bits as int32 > 0x3f7fffff
        nan_pos = np.array([0x7FC00000], np.int32).view(F32)[0]
        nan_neg = np.array([-0x400000], np.int32).view(F32)[0]
        lr = col(1.0, np.nextafter(F32(1), F32(0)), INF, nan_pos, nan_neg, -INF, 0, 7)
        self.assertEqual(S.dead_mask(lr).tolist(), [True, False, True, True, False, False, False, True])

    def test_kill_order_swaps_like_d2r(self):
        # from the last row down, a dead row swaps with the last live one (not a stable compaction)
        perm, m = S.kill_order(np.array([1, 0, 1, 0, 0], bool))
        self.assertEqual(m, 3)
        self.assertEqual(perm.tolist(), [3, 1, 4, 0, 2])
        perm, m = S.kill_order(np.zeros(3, bool))
        self.assertEqual((perm.tolist(), m), ([0, 1, 2], 3))

    def test_dying_dt(self):
        # 0xaae8a0: the dying evolve runs for the life left at the start of the step; zero life (inf) -> 0
        dt = F32(0.1)
        lr = col(1.0, 1.25, F32(0.95) + dt * 2, 1.0)   # after the update: inf; 0.25 over; 0.05 left at 2/s; killed
        inv = col(INF, 2.5, 2.0, 2.0)
        d = col(dt, dt, dt, 0)                          # a killed particle's dying evolve gets d = 0
        out = S.dying_dt(lr, inv, col(dt, dt, dt, dt), d)
        np.testing.assert_allclose(out[:, 0], [0, 0, 0.025, 0], atol=1e-6)
        out = S.dying_dt(col(1.15), col(2.0), col(dt), col(dt))   # had 0.025 s left -> lived 0.025 of 0.1
        np.testing.assert_allclose(out[:, 0], [0.025], atol=1e-6)


class Page(unittest.TestCase):
    """Sim._page orchestration with a recording stand-in for the script runs."""

    def setUp(self):
        self.sim = S.Sim(synthetic_effect(), seed=0)
        self.sim.step(1 / 30)
        self.L = next(L for L in self.sim.layers if L.slot.name == "Sparks")
        self.L.slot.newborn = copy.copy(self.L.slot.evolve)      # a kind-5 script to tell the dying runs apart
        self.runs = []

        def fake_run(L, script, rows, payload=None, dt=None):
            ids = L.ids[rows].tolist()
            self.runs.append((script, ids, None if dt is None else np.asarray(dt).ravel().tolist(),
                              L.streams["self.lifeRatio"][rows, 0].tolist(), payload))

            class E:
                killed = np.isin(L.ids[rows], self.kill_ids)
            return E()
        self.sim._run = fake_run
        self.kill_ids = []

    def fill(self, lr, inv):
        L = self.L
        L.keep(np.zeros(L.n, bool))
        rows = L.grow(len(lr), 100)
        L.streams["self.lifeRatio"][rows] = col(*lr)
        L.streams["self.invLife"][rows] = col(*inv)
        return rows

    def test_frame_page(self):
        self.sim.dt_now = 0.1
        self.fill([0.5, 1.2, 0.3, 1.0, 0.2], [2, 2, 2, INF, 2])   # ids 100..104; 101 and 103 dead
        self.kill_ids = [102]                                     # the evolve kills 102
        evolve, dying = self.L.slot.evolve, self.L.slot.newborn
        self.sim._page(self.L, 0, self.L.n, evolve, None)
        (s1, ids1, dt1, _, _), (s2, ids2, dt2, lr2, _), (s3, ids3, dt3, lr3, _) = self.runs
        self.assertIs(s1, evolve)
        self.assertEqual(ids1, [100, 104, 102])           # survivors, in KillDeadParticles' swap order
        self.assertIsNone(dt1)                            # frame dt
        self.assertIs(s2, dying)
        self.assertEqual(ids2, [101, 103])
        self.assertEqual(lr2, [1.0, 1.0])
        np.testing.assert_allclose(dt2, [0.0, 0.0], atol=1e-6)   # 101 had used its life up; 103 (inf) -> 0
        self.assertEqual((ids3, dt3, lr3), ([102], [0.0], [1.0]))   # killed by the evolve: dying evolve, dt 0
        self.assertEqual(self.L.ids.tolist(), [100, 104])

    def test_newborn_zero_life_emits_once(self):
        """The calib2 case: a zero-life spawner born at the frame end (dt_i 0) is dead before its evolve, runs the
        dying evolve once (dt 0, lifeRatio 1) and is gone: no second evolve next frame."""
        self.sim.dt_now = 1 / 30
        rows = self.fill([0, 0, 0], [INF, INF, 4])
        pl = {S.FRAC: col(1.0, 0.5, 1.0)}             # sub-frame times: born at the end, mid-frame, at the end
        self.sim._newborn(self.L, rows, pl, np.zeros(3, bool))
        (s1, ids1, dt1, lr1, p1), (s2, ids2, dt2, lr2, p2) = self.runs
        self.assertTrue(s1 is s2 is self.L.slot.newborn)
        self.assertEqual((ids1, dt1, lr1), ([102], [0.0], [0.0]))   # finite life, born at the end: evolves, dt 0
        self.assertEqual(ids2, [100, 101])
        self.assertEqual((dt2, lr2), ([0.0, 0.0], [1.0, 1.0]))
        np.testing.assert_array_equal(p2[S.FRAC][:, 0], [1.0, 0.5])   # payload rows follow the swap
        np.testing.assert_array_equal(p1[S.FRAC][:, 0], [1.0])
        self.assertEqual(self.L.ids.tolist(), [102])
        self.runs.clear()
        self.sim._frame(1 / 30)
        self.assertEqual([r[1] for r in self.runs if r[0] is self.L.slot.evolve], [[102]])


@unittest.skipUnless(MISSILES.exists(), "current-build corpus not present")
class Corpus(unittest.TestCase):
    def run_sim(self, rel, frames=60):
        kicks = []
        sim = S.Sim(str(MISSILES / rel), seed=0)
        orig = sim.kick

        def kick(slot, event, counts, *a, **k):
            kicks.append((slot, event, np.asarray(counts).ravel().copy()))
            return orig(slot, event, counts, *a, **k)
        sim.kick = kick
        with np.errstate(all="ignore"):
            for _ in range(frames):
                sim.step(1 / 30)
        return sim, kicks

    def test_charged_bolt_spawners_emit_once(self):
        # calib2 capture: 14 Lightning per bolt; each slot-14 spawner (generate(14), zero life) kicks once
        sim, kicks = self.run_sim("charged_bolt/chragedbolt.particles")
        born = sum(int(c.sum()) for s, e, c in kicks if 14 in (sim.routing.event_for(s, e).targets
                                                               if sim.routing.event_for(s, e) else []))
        out = [c for s, e, c in kicks if s == 14]
        self.assertGreater(born, 0)
        self.assertEqual(len(np.concatenate(out)), born)
        self.assertTrue(all((c == 14).all() for c in out))

    def test_killed_particles_fire_on_death(self):
        # meat_chunks kill themselves (bounce count); their dying evolve sees lifeRatio 1 and fires __e_OnDeath
        sim, _ = self.run_sim("corpse_explode_guts/vfx_corpse_explosion.particles")
        self.assertGreater(sim.stats["spawned"]["meat_chunks_dead"], 1)


if __name__ == "__main__":
    unittest.main()
