"""Tests for the shared seats of testing/game_slots.py and its Wine process lookup (no game, no Wine needed).

Seats are a machine-wide game budget (<seats.dir>/budget.json) shared by several projects on one PC.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "testing"))
sys.path.insert(0, str(ROOT / "tests"))

import game_slots as gs  # noqa: E402
from test_testing_slots import alive, dead, make_cfg  # noqa: E402


class SeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.shared = Path(self.tmp.name) / "shared"
        self.shared.mkdir()
        self.cfg = make_cfg(self.tmp.name, game={"slot_count": 5},
                            seats={"dir": self.shared.as_posix(), "project": "mine"})

    def set_budget(self, n):
        (self.shared / "budget.json").write_text(json.dumps({"max_games": n}))

    def held(self):
        return [(k, (txt or "").split()[0]) for k, txt in gs.seats(self.cfg)]

    def test_no_budget_no_seats(self):
        self.assertIsNone(gs.budget(self.cfg))
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a")[0])
        self.assertEqual(gs.seats(self.cfg), [])

    def test_seat_goes_with_the_lock(self):
        self.set_budget(2)
        ok, msg = gs.acquire(self.cfg, 2, "lane-a")
        self.assertTrue(ok, msg)
        self.assertIn("seat 1 of 2", msg)
        txt = dict(gs.seats(self.cfg))[1]
        self.assertTrue(txt.startswith(f"lane-a pid={os.getpid()} "))
        self.assertTrue(txt.endswith(" slot=2"))
        self.assertTrue(gs.release(self.cfg, 2, "lane-a")[0])
        self.assertEqual(gs.seats(self.cfg), [])
        self.assertIsNone(gs.lock_info(self.cfg, 2))

    def test_budget_full_refuses_and_leaves_no_lock(self):
        self.set_budget(1)
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", alive=alive)[0])
        ok, msg = gs.acquire(self.cfg, 3, "lane-b", alive=alive)
        self.assertFalse(ok)
        self.assertIn("no free seat", msg)
        self.assertIn("lane-a", msg)
        self.assertIsNone(gs.lock_info(self.cfg, 3))

    def test_held_slot_gives_the_seat_back(self):
        self.set_budget(2)
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", alive=alive)[0])
        ok, _ = gs.acquire(self.cfg, 2, "lane-b", alive=alive)
        self.assertFalse(ok)
        self.assertEqual(self.held(), [(1, "lane-a")])

    def test_lost_race_for_the_lock_gives_the_seat_back(self):
        self.set_budget(2)
        orig = gs.lock_info
        calls = []

        def racy(cfg, n):  # the lock is free when checked, then someone takes it before our mkdir
            info = orig(cfg, n)
            if not calls:
                calls.append(1)
                Path(cfg.lock_path(n)).mkdir(parents=True)
            return info
        gs.lock_info = racy
        try:
            ok, msg = gs.acquire(self.cfg, 2, "lane-a", alive=alive)
        finally:
            gs.lock_info = orig
        self.assertFalse(ok)
        self.assertIn("taken just now", msg)
        self.assertEqual(gs.seats(self.cfg), [])

    def test_stale_seat_is_replaced_and_pid_zero_is_not(self):
        self.set_budget(2)
        (self.shared / "game.seat.1").mkdir()
        (self.shared / "game.seat.1" / "owner.txt").write_text("gone pid=99999999 2020-01-01 00:00:00 slot=4")
        (self.shared / "game.seat.2").mkdir()
        (self.shared / "game.seat.2" / "owner.txt").write_text("other pid=0 2020-01-01 00:00:00 slot=9")
        ok, msg = gs.acquire(self.cfg, 2, "lane-a", alive=dead)
        self.assertTrue(ok, msg)
        self.assertEqual(self.held(), [(1, "lane-a"), (2, "other")])
        self.assertFalse(gs.acquire(self.cfg, 3, "lane-b", alive=lambda pid: pid == os.getpid())[0])

    def test_seat_without_owner_file_is_held(self):
        self.set_budget(1)
        (self.shared / "game.seat.1").mkdir()
        self.assertFalse(gs.acquire(self.cfg, 2, "lane-a", alive=dead)[0])

    def test_stale_lock_takes_its_dead_owners_seat_with_it(self):
        self.set_budget(2)
        self.assertTrue(gs.acquire(self.cfg, 2, "dead-lane", pid=4242)[0])
        ok, msg = gs.acquire(self.cfg, 2, "lane-b", alive=dead)
        self.assertTrue(ok, msg)
        self.assertEqual([o for _, o in self.held()], ["lane-b"])

    def test_session_lock_keeps_its_seat_until_unlock(self):
        self.set_budget(1)
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", pid=0)[0])
        self.assertTrue(gs.release(self.cfg, 2, "lane-a")[0])  # a run inside the session: kept
        self.assertEqual(self.held(), [(1, "lane-a")])
        self.assertTrue(gs.release(self.cfg, 2, "lane-a", manual=True)[0])
        self.assertEqual(gs.seats(self.cfg), [])

    def test_force_unlock_frees_the_seat(self):
        self.set_budget(1)
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", pid=0)[0])
        self.assertTrue(gs.release(self.cfg, 2, None, force=True)[0])
        self.assertEqual(gs.seats(self.cfg), [])

    def write_want(self, project, count, pid=0):
        d = self.shared / f"game.want.{project}"
        d.mkdir()
        (d / "owner.txt").write_text(f"{project} pid={pid} 2026-01-01 00:00:00 count={count}")

    def test_yield_to_a_waiting_project(self):
        self.set_budget(3)
        self.write_want("other", 2)
        ok1, _ = gs.acquire(self.cfg, 2, "lane-a", alive=alive)   # 3 free -> 2 left: fine
        ok2, msg = gs.acquire(self.cfg, 3, "lane-b", alive=alive)  # 2 free -> 1 left < 2 wanted: yield
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertIn("yielding to other wants 2", msg)

    def test_want_count_is_capped_and_stale_wants_ignored(self):
        self.set_budget(4)
        self.write_want("big", 9)               # counts as want_cap (2)
        self.write_want("gone", 2, pid=99999999)
        self.assertEqual(gs.wanted_by_others(self.cfg, alive=dead), (2, ["big wants 2"]))

    def test_own_and_unlisted_wants_do_not_count(self):
        self.set_budget(2)
        self.write_want("mine", 2)
        self.assertEqual(gs.wanted_by_others(self.cfg)[0], 0)
        cfg = make_cfg(self.tmp.name, seats={"dir": self.shared.as_posix(), "yield_to": ["friend"]})
        self.write_want("stranger", 2)
        self.assertEqual(gs.wanted_by_others(cfg)[0], 0)
        self.write_want("friend", 1)
        self.assertEqual(gs.wanted_by_others(cfg)[0], 1)

    def test_acquire_wait_publishes_a_want_and_removes_it(self):
        self.set_budget(1)
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", alive=alive)[0])
        seen = []
        now = [0.0]

        def sleep(s):
            seen.append(os.path.isdir(self.shared / "game.want.mine"))
            now[0] += s
        ok, msg, slot = gs.acquire_wait(self.cfg, 3, "lane-b", wait_s=10, poll_s=5, sleep=sleep,
                                        clock=lambda: now[0], out=lambda *_: None, want=1)
        self.assertFalse(ok)
        self.assertTrue(seen and all(seen))
        self.assertFalse(os.path.isdir(self.shared / "game.want.mine"))

    def test_seats_wait_from_the_config(self):
        cfg = make_cfg(self.tmp.name, seats={"dir": self.shared.as_posix(), "wait": 30})
        self.set_budget(1)
        self.assertTrue(gs.acquire(cfg, 2, "lane-a", alive=alive)[0])
        now = [0.0]
        naps = []

        def sleep(s):
            naps.append(s)
            now[0] += s
        gs.acquire_wait(cfg, 3, "lane-b", wait_s=0, poll_s=10, sleep=sleep, clock=lambda: now[0], out=lambda *_: None)
        self.assertEqual(sum(naps), 30)

    def test_seats_dir_defaults_to_runs_dir(self):
        cfg = make_cfg(self.tmp.name)
        self.assertEqual(cfg.seats_dir, cfg.runs_dir)


class WineProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.slot = t / "slots" / "slot-2"
        (self.slot).mkdir(parents=True)
        self.pfx = t / "prefixes" / "s2" / "pfx"
        (self.pfx / "dosdevices").mkdir(parents=True)
        os.symlink(self.slot, self.pfx / "dosdevices" / "d:")

    def test_windows_path_maps_through_the_prefix_drive(self):
        got = gs.wine_image_path(r"D:\D2RLoader-s2.exe", {"WINEPREFIX": str(self.pfx)})
        self.assertEqual(got, str(self.slot / "D2RLoader-s2.exe"))
        self.assertEqual(gs.wine_image_path("/usr/bin/bash", {"WINEPREFIX": str(self.pfx)}), "")
        self.assertEqual(gs.wine_image_path(r"D:\x.exe", {}), "")

    def test_proc_scan(self):
        proc = Path(self.tmp.name) / "proc"
        for pid, argv0, env in ((101, r"D:\D2RLoader-s2.exe", f"WINEPREFIX={self.pfx}"),
                                (102, "/usr/bin/python3", "HOME=/x"),
                                (103, r"C:\windows\system32\services.exe", f"WINEPREFIX={self.pfx}")):
            d = proc / str(pid)
            d.mkdir(parents=True)
            (d / "cmdline").write_bytes(argv0.encode() + b"\0--arg\0")
            (d / "environ").write_bytes(env.encode() + b"\0")
        (proc / "self").mkdir()
        got = gs._linux_processes(("d2rloader-s2.exe",), proc=str(proc))
        self.assertEqual(got, [(101, "D2RLoader-s2.exe", str(self.slot / "D2RLoader-s2.exe"))])


if __name__ == "__main__":
    unittest.main()
