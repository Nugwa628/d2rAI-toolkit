"""Tests for hdkit.signoff.backend: the backend interface, plug-in backends and the seat/budget hook (no game needed)."""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "testing"))
sys.path.insert(0, str(ROOT / "tests"))

from hdkit.signoff import backend as B  # noqa: E402
from hdkit.signoff import config  # noqa: E402


def req(**kw):
    d = dict(tag="t1", slot=2, token="zm", id="zombie1", overlay=Path("ov"), ev=Path("ev"), holder="signoff-test")
    d.update(kw)
    return B.RunRequest(**d)


class FakeBackend(B.Backend):
    name = "fake"
    calls: list = []

    def run(self, r, log=print):
        FakeBackend.calls.append(r.tag)
        if r.tag == "boom":
            raise RuntimeError("the run failed")
        return 0


class FakeSeats:
    def __init__(self, settings):
        self.ok = settings.get("fake_ok", True)
        self.events = []
        FakeSeats.last = self

    def acquire(self, r, wait_s=0, log=print):
        self.events.append(("acquire", r.slot, wait_s))
        return self.ok

    def release(self, r):
        self.events.append(("release", r.slot))


def plugin_module():
    m = types.ModuleType("signoff_test_plugin")
    m.FakeBackend, m.FakeSeats = FakeBackend, FakeSeats
    return m


class BackendTests(unittest.TestCase):
    def setUp(self):
        sys.modules["signoff_test_plugin"] = plugin_module()
        self.addCleanup(sys.modules.pop, "signoff_test_plugin", None)
        FakeBackend.calls = []

    def settings(self, **kw):
        return config.merge(config.DEFAULTS, kw)

    def test_builtin_by_platform(self):
        self.assertIsInstance(B.get(self.settings(), platform="linux"), B.LinuxBackend)
        self.assertIsInstance(B.get(self.settings(), platform="win32"), B.WindowsBackend)
        self.assertIsInstance(B.get(self.settings(backend="windows"), platform="linux"), B.WindowsBackend)

    def test_plugin_backend(self):
        b = B.get(self.settings(backend="signoff_test_plugin:FakeBackend"))
        self.assertEqual(b.name, "fake")
        self.assertEqual(B.run(self.settings(backend="signoff_test_plugin:FakeBackend"), req(), log=lambda m: None), 0)
        self.assertEqual(FakeBackend.calls, ["t1"])

    def test_bad_spec(self):
        with self.assertRaises(SystemExit):
            B.get(self.settings(backend="nocolon:"))

    def test_seat_refused_no_run(self):
        s = self.settings(backend="signoff_test_plugin:FakeBackend", seats="signoff_test_plugin:FakeSeats", fake_ok=False,
                          seat_wait=7)
        self.assertEqual(B.run(s, req(), log=lambda m: None), 5)
        self.assertEqual(FakeBackend.calls, [])
        self.assertEqual(FakeSeats.last.events, [("acquire", 2, 7.0)])

    def test_seat_released_after_failed_run(self):
        s = self.settings(backend="signoff_test_plugin:FakeBackend", seats="signoff_test_plugin:FakeSeats")
        with self.assertRaises(RuntimeError):
            B.run(s, req(tag="boom"), log=lambda m: None)
        self.assertEqual(FakeSeats.last.events, [("acquire", 2, 0.0), ("release", 2)])

    def test_default_is_no_seats(self):
        self.assertIsInstance(B.seat_hook(self.settings()), B.NoSeats)
        self.assertEqual(config.DEFAULTS["seats"], "none")

    def test_windows_command(self):
        s = self.settings(windows={"heavy_lock": "lock.py", "worktree_root": "."}, lock_holder="h")
        cmd = B.WindowsBackend(s).command(req(env={"HDKIT_SIGNOFF_AREA": "1"}))
        self.assertEqual(cmd[:6], ["py", "-3", "lock.py", "game", "h", "--"])
        self.assertIn("--overlay", cmd)
        self.assertEqual(cmd[-1], "HDKIT_SIGNOFF_AREA=1")


class TestingSeatsTests(unittest.TestCase):
    """The "testing" hook takes one machine-wide seat from testing/game_slots.py for the run and gives it back."""

    def setUp(self):
        from test_testing_slots import make_cfg
        import game_slots
        self.gs = game_slots
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        shared = Path(self.tmp.name) / "shared"
        shared.mkdir()
        (shared / "budget.json").write_text(json.dumps({"max_games": 1}))
        self.cfg = make_cfg(self.tmp.name, game={"slot_count": 4}, seats={"dir": shared.as_posix(), "project": "p"})

    def hook(self):
        h = B.TestingSeats({})
        h._cfg = self.cfg
        return h

    def test_seat_for_the_run(self):
        h = self.hook()
        self.assertTrue(h.acquire(req(slot=2), 0, log=lambda m: None))
        self.assertEqual(len(self.gs.seats(self.cfg)), 1)
        self.assertIsNone(self.gs.lock_info(self.cfg, 2))      # the slot lock is the backend's
        # the budget is 1: a second run on another slot gets no seat, and its want is withdrawn again
        self.assertFalse(h.acquire(req(slot=3, holder="other"), 0, log=lambda m: None))
        self.assertFalse(any(p.name.startswith("game.want.") for p in Path(self.cfg.seats_dir).iterdir()))
        h.release(req(slot=2))
        self.assertEqual(self.gs.seats(self.cfg), [])

    def test_waits_for_a_seat(self):
        h = self.hook()
        self.assertTrue(h.acquire(req(slot=2), 0, log=lambda m: None))
        waits = []

        def sleep(s):            # the first run ends while the second waits
            waits.append(s)
            h.release(req(slot=2))

        self.assertTrue(h.acquire(req(slot=3, holder="other"), 30, log=lambda m: None, poll_s=1, sleep=sleep))
        self.assertEqual(waits, [1])
        h.release(req(slot=3, holder="other"))


if __name__ == "__main__":
    unittest.main()
