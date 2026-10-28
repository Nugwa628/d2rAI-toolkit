"""Tests for testing/testing_config.py and testing/game_slots.py (run: python -m unittest discover tests).

No game, no network. Locks and copies happen in a temporary folder.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "testing"))

import game_slots as gs  # noqa: E402
import testing_config as tc  # noqa: E402


def make_cfg(tmp: str, **over) -> tc.Config:
    t = Path(tmp).as_posix()
    data = {
        "game": {"install_dir": f"{t}/install", "slots_dir": f"{t}/slots", "slot_count": 3},
        "mod": {"folder": "MyMod", "name": "My Mod", "plugin_dlls": ["my-plugin.dll"]},
        "saves": {"root": f"{t}/saves"},
        "runs": {"dir": f"{t}/runs"},
    }
    data = tc.deep_merge(data, over)
    return tc.Config(data, base_dir=tmp)


def dead(_pid):
    return False


def alive(_pid):
    return True


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_defaults_without_a_file(self):
        c = tc.load(None, env={}, cwd=self.dir)
        self.assertIsNone(c.source)
        self.assertEqual(c.slot_count, 3)
        with self.assertRaises(tc.ConfigError):
            c.install_dir

    def test_toml_file_and_search_order(self):
        p = os.path.join(self.dir, "modkit-testing.toml")
        Path(p).write_text('[game]\ninstall_dir = "install"\nslots_dir = "slots"\n[mod]\nfolder = "Abc"\n',
                           encoding="utf-8")
        c = tc.load(None, env={}, cwd=self.dir)  # ./modkit-testing.toml
        self.assertEqual(c.mod_folder(), "Abc")
        self.assertEqual(c.install_dir, os.path.join(self.dir, "install"))  # relative to the file
        other = os.path.join(self.dir, "other.toml")
        Path(other).write_text('[mod]\nfolder = "Env"\n', encoding="utf-8")
        self.assertEqual(tc.load(None, env={tc.ENV_CONFIG: other}, cwd=self.dir).mod_folder(), "Env")
        self.assertEqual(tc.load(p, env={tc.ENV_CONFIG: other}, cwd=self.dir).mod_folder(), "Abc")  # --config wins

    def test_json_file(self):
        p = os.path.join(self.dir, "c.json")
        Path(p).write_text(json.dumps({"mod": {"folder": "FromJson"}, "game": {"slot_count": 5}}), encoding="utf-8")
        c = tc.load(p)
        self.assertEqual((c.mod_folder(), c.slot_count), ("FromJson", 5))

    def test_errors(self):
        with self.assertRaises(tc.ConfigError):
            tc.load(os.path.join(self.dir, "missing.toml"))
        bad = os.path.join(self.dir, "bad.toml")
        Path(bad).write_text("[game\n", encoding="utf-8")
        with self.assertRaises(tc.ConfigError):
            tc.load(bad)
        with self.assertRaises(tc.ConfigError):
            tc.Config({"game": {"copy_mode": "symlink"}})
        with self.assertRaises(tc.ConfigError):
            tc.Config({"mod": {"folder": "../up"}})
        with self.assertRaises(tc.ConfigError):
            tc.Config({"game": {"slot_count": 0}})

    def test_example_config_parses(self):
        c = tc.load(str(ROOT / "testing" / "testing.example.toml"))
        self.assertEqual(c.slot_count, 3)
        self.assertEqual(c.data["harness"]["magic"], tc.DEFAULTS["harness"]["magic"])
        self.assertEqual(set(tc.DEFAULTS), set(c.data))

    def test_run_id_and_env_helpers(self):
        self.assertTrue(tc.valid_run_id("lane-1.a_b"))
        for bad in ("", "a/b", "..", "a b", "-x", "x" * 70):
            self.assertFalse(tc.valid_run_id(bad), bad)
        self.assertEqual(tc.env_slot({"MODKIT_SLOT": "3"}), 3)
        self.assertIsNone(tc.env_slot({"MODKIT_SLOT": "x"}))
        self.assertEqual(tc.env_run({"MODKIT_RUN": "lane"}), "lane")
        self.assertIsNone(tc.env_run({}))


class SlotPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)

    def test_slot_dirs(self):
        c = self.cfg
        self.assertEqual(c.slot_dir(1), os.path.join(self.tmp.name, "install"))
        self.assertEqual(c.slot_dir(2), os.path.join(self.tmp.name, "slots", "slot-2"))
        self.assertEqual(c.slot_dir(3), os.path.join(self.tmp.name, "slots", "slot-3"))
        c2 = make_cfg(self.tmp.name, game={"slot_dirs": {"2": f"{Path(self.tmp.name).as_posix()}/elsewhere"}})
        self.assertEqual(c2.slot_dir(2), os.path.join(self.tmp.name, "elsewhere"))
        self.assertEqual(c2.slot_dir(3), os.path.join(self.tmp.name, "slots", "slot-3"))

    def test_slot_number_checks(self):
        for bad in (0, 4, -1, "x", None):
            with self.assertRaises(tc.ConfigError):
                self.cfg.check_slot_number(bad)
        self.assertEqual(self.cfg.check_slot_number("2"), 2)

    def test_slot_of_dir(self):
        self.assertEqual(self.cfg.slot_of_dir(self.cfg.slot_dir(3)), 3)
        self.assertEqual(self.cfg.slot_of_dir(self.cfg.install_dir), 1)
        self.assertIsNone(self.cfg.slot_of_dir(self.tmp.name))

    def test_mod_save_and_loader_paths(self):
        c = self.cfg
        self.assertEqual(c.mod_dir(2), os.path.join(c.slot_dir(2), "mods", "MyMod"))
        self.assertEqual(c.plugins_dir(2), os.path.join(c.mod_dir(2), "d2rloader", "plugins"))
        self.assertEqual(c.save_name(1), "MyMod")
        self.assertEqual(c.save_name(3), "MyMod-s3")
        self.assertEqual(c.save_dir(3), os.path.join(self.tmp.name, "saves", "MyMod-s3"))
        self.assertEqual(c.loader_name(1), "D2RLoader.exe")
        self.assertEqual(c.loader_name(2), "D2RLoader-s2.exe")
        self.assertEqual(c.process_names(2), ("d2r.exe", "d2rloader-s2.exe", "d2rloader.exe"))
        self.assertEqual([os.path.basename(p) for p in c.modinfo_paths(2)], ["modinfo.json", "modinfo.json"])

    def test_harness_and_run_paths(self):
        c = self.cfg
        self.assertEqual(c.shm_name(1234), "Local\\my-harness-block-1234")
        self.assertEqual(c.shm_name(), "")
        self.assertEqual(os.path.basename(c.harness_word_file(2)), "my-testharness.txt")
        self.assertEqual(c.lock_path(2), os.path.join(self.tmp.name, "runs", "game.lock.2"))
        self.assertEqual(c.run_dir("lane-a"), os.path.join(self.tmp.name, "runs", "lane-a"))
        with self.assertRaises(tc.ConfigError):
            c.run_dir("../escape")
        c2 = make_cfg(self.tmp.name, harness={"words": ["tag=s{slot}", "x"]})
        self.assertEqual(c2.harness_words(3), ["tag=s3", "x"])


class OwnerTextTests(unittest.TestCase):
    def test_parse_and_format(self):
        txt = gs.format_owner("lane-a", 1234)
        self.assertRegex(txt, r"^lane-a pid=1234 \d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        owner, pid, when = gs.parse_owner(txt)
        self.assertEqual((owner, pid), ("lane-a", 1234))
        self.assertRegex(when, r"^\d{4}-")

    def test_parse_odd_input(self):
        self.assertEqual(gs.parse_owner(None), (None, None, None))
        self.assertEqual(gs.parse_owner(""), (None, None, None))
        self.assertEqual(gs.parse_owner("someone"), ("someone", None, None))
        self.assertEqual(gs.parse_owner("x pid=abc later"), ("x", None, "pid=abc later"))
        self.assertEqual(gs.parse_owner("x pid=0 2026-01-01 10:00:00")[1], 0)

    def test_valid_owner(self):
        self.assertTrue(gs.valid_owner("lane-1"))
        for bad in ("", "a b", "../x", "-x"):
            self.assertFalse(gs.valid_owner(bad), bad)

    def test_pid_alive(self):
        self.assertTrue(gs.pid_alive(os.getpid()))
        self.assertFalse(gs.pid_alive(0))
        self.assertFalse(gs.pid_alive(-5))


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)

    def owner_file(self, n):
        return os.path.join(self.cfg.lock_path(n), "owner.txt")

    def test_acquire_and_release(self):
        ok, msg = gs.acquire(self.cfg, 2, "lane-a")
        self.assertTrue(ok, msg)
        self.assertEqual(gs.parse_owner(Path(self.owner_file(2)).read_text())[:2], ("lane-a", os.getpid()))
        self.assertEqual(gs.lock_info(self.cfg, 2).owner, "lane-a")
        self.assertTrue(gs.release(self.cfg, 2, "lane-a")[0])
        self.assertIsNone(gs.lock_info(self.cfg, 2))

    def test_contention_names_the_holder(self):
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", alive=alive)[0])
        ok, msg = gs.acquire(self.cfg, 2, "lane-b", alive=alive)
        self.assertFalse(ok)
        self.assertIn("lane-a", msg)
        self.assertTrue(gs.acquire(self.cfg, 3, "lane-b")[0])  # another slot is independent

    def test_same_owner_again_is_ok(self):
        gs.acquire(self.cfg, 2, "lane-a")
        ok, msg = gs.acquire(self.cfg, 2, "lane-a")
        self.assertTrue(ok)
        self.assertIn("already", msg)

    def test_stale_lock_of_a_dead_pid_is_replaced(self):
        Path(self.cfg.lock_path(2)).mkdir(parents=True)
        Path(self.owner_file(2)).write_text("old-lane pid=99999999 2020-01-01 00:00:00")
        self.assertTrue(gs.is_stale(gs.lock_info(self.cfg, 2), dead))
        ok, msg = gs.acquire(self.cfg, 2, "new-lane", alive=dead)
        self.assertTrue(ok, msg)
        self.assertEqual(gs.lock_info(self.cfg, 2).owner, "new-lane")
        leftovers = [p for p in os.listdir(self.cfg.runs_dir) if "gone" in p]
        self.assertEqual(leftovers, [])

    def test_lock_of_a_living_pid_is_kept(self):
        Path(self.cfg.lock_path(2)).mkdir(parents=True)
        Path(self.owner_file(2)).write_text("busy pid=4242 2020-01-01 00:00:00")
        self.assertFalse(gs.acquire(self.cfg, 2, "x", alive=alive)[0])

    def test_pid_zero_is_a_manual_lock_and_never_stale(self):
        self.assertTrue(gs.acquire(self.cfg, 2, "lane-a", pid=0)[0])
        self.assertFalse(gs.is_stale(gs.lock_info(self.cfg, 2), dead))
        ok, msg = gs.acquire(self.cfg, 2, "lane-b", alive=dead)
        self.assertFalse(ok)
        self.assertIn("lane-a", msg)

    def test_lock_without_owner_file_is_respected(self):
        Path(self.cfg.lock_path(2)).mkdir(parents=True)  # made by hand
        info = gs.lock_info(self.cfg, 2)
        self.assertIsNone(info.text)
        ok, msg = gs.acquire(self.cfg, 2, "lane-a", alive=dead)
        self.assertFalse(ok)
        self.assertIn("without owner.txt", msg)

    def test_slot_one_is_refused_everywhere(self):
        ok, msg = gs.acquire(self.cfg, 1, "lane-a")
        self.assertFalse(ok)
        self.assertIn("slot 1", msg)
        self.assertFalse(os.path.exists(self.cfg.lock_path(1)))
        self.assertFalse(gs.release(self.cfg, 1, "lane-a", force=True)[0])
        self.assertFalse(gs.acquire_wait(self.cfg, 1, "lane-a")[0])
        with self.assertRaises(gs.SlotError):
            gs.guard_writable(self.cfg, 1)
        with self.assertRaises(gs.SlotError):
            gs.setup(self.cfg, 1)

    def test_bad_inputs(self):
        self.assertFalse(gs.acquire(self.cfg, 2, "bad owner")[0])
        with self.assertRaises(tc.ConfigError):
            gs.acquire(self.cfg, 9, "lane-a")

    def test_release_rules(self):
        gs.acquire(self.cfg, 2, "lane-a")
        self.assertFalse(gs.release(self.cfg, 2, "lane-b")[0])  # not yours
        self.assertIsNotNone(gs.lock_info(self.cfg, 2))
        self.assertFalse(gs.release(self.cfg, 2)[0])  # no owner and no force
        self.assertTrue(gs.release(self.cfg, 2, force=True)[0])
        self.assertIsNone(gs.lock_info(self.cfg, 2))
        self.assertTrue(gs.release(self.cfg, 2, "anyone")[0])  # already free

    def test_session_lock_survives_a_run_but_not_unlock(self):
        gs.acquire(self.cfg, 2, "lane-a", pid=0)
        ok, msg = gs.release(self.cfg, 2, "lane-a")  # what a run does at its end
        self.assertTrue(ok)
        self.assertIn("kept", msg)
        self.assertIsNotNone(gs.lock_info(self.cfg, 2))
        self.assertTrue(gs.release(self.cfg, 2, "lane-a", manual=True)[0])  # `unlock 2 lane-a`
        self.assertIsNone(gs.lock_info(self.cfg, 2))

    def test_ensure_lock(self):
        ok, msg, taken = gs.ensure_lock(self.cfg, 2, "lane-a")
        self.assertEqual((ok, taken), (True, True))
        # a lock made for a short-lived command is a manual one (pid 0): it must not look stale when the command ends
        info = gs.lock_info(self.cfg, 2)
        self.assertTrue(info.manual)
        self.assertFalse(gs.is_stale(info, lambda pid: False))
        self.assertIn("unlock 2", msg)
        ok, _, taken = gs.ensure_lock(self.cfg, 2, "lane-a")
        self.assertEqual((ok, taken), (True, False))
        ok, msg, taken = gs.ensure_lock(self.cfg, 2, "lane-b", alive=alive)
        self.assertEqual((ok, taken), (False, False))
        self.assertIn("lane-a", msg)

    def test_acquire_wait_queues_until_free(self):
        gs.acquire(self.cfg, 2, "lane-a", pid=0)
        now = [0.0]
        said = []

        def sleep(s):
            now[0] += s
            if now[0] >= 10:  # the holder lets go while we wait
                gs.release(self.cfg, 2, "lane-a", manual=True)

        ok, msg, slot = gs.acquire_wait(self.cfg, 2, "lane-b", wait_s=60, poll_s=5, sleep=sleep,
                                        clock=lambda: now[0], out=said.append)
        self.assertTrue(ok, msg)
        self.assertEqual(slot, 2)
        self.assertTrue(said and "lane-a" in said[0])
        self.assertEqual(gs.lock_info(self.cfg, 2).owner, "lane-b")

    def test_acquire_wait_times_out(self):
        gs.acquire(self.cfg, 2, "lane-a", pid=0)
        now = [0.0]

        def sleep(s):
            now[0] += s

        ok, msg, slot = gs.acquire_wait(self.cfg, 2, "lane-b", wait_s=12, poll_s=5, sleep=sleep,
                                        clock=lambda: now[0], out=lambda m: None)
        self.assertFalse(ok)
        self.assertIsNone(slot)
        self.assertIn("lane-a", msg)

    def test_acquire_wait_zero_does_not_wait(self):
        gs.acquire(self.cfg, 2, "lane-a", pid=0)
        slept = []
        ok, _, _ = gs.acquire_wait(self.cfg, 2, "lane-b", sleep=slept.append, out=lambda m: None)
        self.assertFalse(ok)
        self.assertEqual(slept, [])

    def test_any_picks_the_first_free_slot_but_never_slot_one(self):
        gs.acquire(self.cfg, 2, "lane-a", pid=0)
        ok, _, slot = gs.acquire_wait(self.cfg, "any", "lane-b")
        self.assertEqual((ok, slot), (True, 3))
        ok, msg, slot = gs.acquire_wait(self.cfg, "any", "lane-c")
        self.assertFalse(ok)
        self.assertIn("slot 2", msg)
        self.assertIn("slot 3", msg)
        solo = make_cfg(self.tmp.name, game={"slot_count": 1})
        self.assertFalse(gs.acquire_wait(solo, "any", "x")[0])


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)
        inst = Path(self.cfg.install_dir)
        (inst / "Data" / "data").mkdir(parents=True)
        (inst / "Data" / "data" / "big.bin").write_bytes(b"x" * 1000)
        (inst / "Logs").mkdir()
        (inst / "Logs" / "a.log").write_text("log")
        (inst / "D2RLoader.exe").write_bytes(b"loader-bytes")
        (inst / "D2R.exe").write_bytes(b"game-bytes")
        (inst / "d2rloader" / "config").mkdir(parents=True)
        (inst / "d2rloader" / "config" / "d2rloader.toml").write_text('[d2rloader]\n# default_mod = ""\n')
        (inst / "mods" / "MyMod" / "MyMod.mpq").mkdir(parents=True)
        (inst / "mods" / "MyMod" / "MyMod.mpq" / "modinfo.json").write_text('{"name":"x","savepath":"MyMod/"}')

    def test_copy_and_loader_copy(self):
        r = gs.setup(self.cfg, 2, out=lambda m: None)
        dst = Path(self.cfg.slot_dir(2))
        self.assertEqual((dst / "Data" / "data" / "big.bin").read_bytes(), b"x" * 1000)
        self.assertEqual((dst / "D2RLoader-s2.exe").read_bytes(), b"loader-bytes")
        self.assertFalse((dst / "Logs").exists())  # excluded
        self.assertFalse((dst / "mods").exists())  # mods_policy skip
        self.assertTrue(os.path.isdir(self.cfg.save_dir(2)))
        self.assertGreaterEqual(r["files"]["copied"], 4)
        again = gs.setup(self.cfg, 2)
        self.assertEqual(again["files"]["copied"], 0)  # nothing changed: nothing copied
        self.assertIn('default_mod = "MyMod"', Path(self.cfg.loader_config_path(2)).read_text())  # kept

    def test_sync_copies_only_what_changed_and_never_deletes(self):
        gs.setup(self.cfg, 2)
        dst = Path(self.cfg.slot_dir(2))
        (dst / "extra.txt").write_text("slot only")
        Path(self.cfg.install_dir, "D2R.exe").write_bytes(b"game-bytes-v2")
        r = gs.copy_install(self.cfg, 2)
        self.assertEqual(r["copied"], 1)
        self.assertEqual((dst / "D2R.exe").read_bytes(), b"game-bytes-v2")
        self.assertTrue((dst / "extra.txt").exists())

    def test_mods_policy_copy_and_savepath_and_default_mod(self):
        cfg = make_cfg(self.tmp.name, game={"mods_policy": "copy"})
        gs.setup(cfg, 3)
        info = json.loads(Path(cfg.mod_dir(3), "MyMod.mpq", "modinfo.json").read_text())
        self.assertEqual(info["savepath"], "MyMod-s3/")
        self.assertEqual(info["name"], "x")  # other keys are kept
        toml = Path(cfg.loader_config_path(3)).read_text()
        self.assertIn('default_mod = "MyMod"', toml)
        # slot 1's own copies are untouched
        self.assertEqual(json.loads(Path(cfg.mod_dir(1), "MyMod.mpq", "modinfo.json").read_text())["savepath"], "MyMod/")
        self.assertNotIn('default_mod = "MyMod"', Path(cfg.loader_config_path(1)).read_text())

    def test_hardlink_mode_never_writes_through_into_slot_one(self):
        src = Path(self.cfg.install_dir, "Data", "data", "big.bin")
        probe = Path(self.tmp.name, "probe")
        probe.write_text("a")
        try:
            os.link(probe, Path(self.tmp.name, "probe2"))
        except OSError:
            self.skipTest("hard links are not available here")
        r = gs.copy_install(self.cfg, 2, link=True)
        dst = Path(self.cfg.slot_dir(2), "Data", "data", "big.bin")
        self.assertEqual(r["linked"], 1)
        self.assertTrue(os.path.samefile(src, dst))
        self.assertEqual(gs.copy_install(self.cfg, 2, link=True)["linked"], 0)
        # slot 1's file is replaced by a new file (new bytes); the slot's link must not write through
        old = Path(self.tmp.name, "old-link")
        os.link(src, old)
        new = Path(self.tmp.name, "new-file")
        new.write_bytes(b"y" * 2000)
        os.replace(new, src)
        gs.copy_install(self.cfg, 2, link=False)
        self.assertEqual(old.read_bytes(), b"x" * 1000)  # the old shared bytes were not touched
        self.assertEqual(src.read_bytes(), b"y" * 2000)
        self.assertEqual(dst.read_bytes(), b"y" * 2000)
        self.assertFalse(os.path.samefile(src, dst))

    def test_dry_run_writes_nothing(self):
        r = gs.setup(self.cfg, 2, dry_run=True)
        self.assertFalse(os.path.exists(self.cfg.slot_dir(2)))
        self.assertGreater(r["files"]["copied"], 0)

    def test_slot_folder_may_not_overlap_the_install(self):
        inside = make_cfg(self.tmp.name, game={"slot_dirs": {"2": f"{Path(self.cfg.install_dir).as_posix()}/slot2"}})
        with self.assertRaises(gs.SlotError):
            gs.copy_install(inside, 2)
        same = make_cfg(self.tmp.name, game={"slot_dirs": {"2": Path(self.cfg.install_dir).as_posix()}})
        with self.assertRaises(gs.SlotError):
            gs.setup(same, 2)

    def test_missing_install(self):
        cfg = make_cfg(self.tmp.name, game={"install_dir": f"{Path(self.tmp.name).as_posix()}/nope"})
        with self.assertRaises(gs.SlotError):
            gs.copy_install(cfg, 2)

    def test_set_toml_value(self):
        t = gs.set_toml_value('[a]\nx = 1\n[d2rloader]\n# default_mod = ""\ny = 2\n', "d2rloader", "default_mod", '"M"')
        self.assertIn('default_mod = "M"', t)
        self.assertIn("y = 2", t)
        self.assertEqual(t.count("default_mod"), 1)
        t2 = gs.set_toml_value("[a]\nx = 1\n", "d2rloader", "k", '"v"')
        self.assertTrue(t2.rstrip().endswith('[d2rloader]\nk = "v"'))
        t3 = gs.set_toml_value("[d2rloader]\n", "d2rloader", "k", '"v"')
        self.assertEqual(t3, '[d2rloader]\nk = "v"\n')


class StatusAndCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)
        t = Path(self.tmp.name).as_posix()
        self.conf = os.path.join(self.tmp.name, "c.json")
        Path(self.conf).write_text(json.dumps({
            "game": {"install_dir": f"{t}/install", "slots_dir": f"{t}/slots", "slot_count": 3},
            "runs": {"dir": f"{t}/runs"}, "saves": {"root": f"{t}/saves"}}), encoding="utf-8")

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = gs.main(["--config", self.conf, *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_status_and_list(self):
        gs.acquire(self.cfg, 2, "lane-a", pid=0)
        rc, out, _ = self.cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("your own game", out)
        self.assertIn("lane-a", out)
        rc, out, _ = self.cli("list")
        rows = [ln.split("\t") for ln in out.splitlines()]
        self.assertEqual([r[:2] for r in rows], [["1", "own"], ["2", "locked"], ["3", "free"]])

    def test_lock_unlock_commands_and_exit_codes(self):
        rc, out, _ = self.cli("lock", "2", "lane-a")
        self.assertEqual(rc, 0, out)
        self.assertEqual(gs.lock_info(self.cfg, 2).pid, 0)  # manual lock
        rc, out, _ = self.cli("lock", "2", "lane-b")
        self.assertEqual(rc, 1)
        self.assertIn("lane-a", out)
        rc, _, _ = self.cli("unlock", "2", "lane-b")
        self.assertEqual(rc, 1)
        rc, _, _ = self.cli("unlock", "2")  # no owner, no force
        self.assertEqual(rc, 1)
        rc, _, _ = self.cli("unlock", "2", "lane-a")
        self.assertEqual(rc, 0)
        self.assertIsNone(gs.lock_info(self.cfg, 2))
        self.cli("lock", "3", "lane-a")
        rc, _, _ = self.cli("unlock", "3", "--force")
        self.assertEqual(rc, 0)

    def test_slot_one_exit_code_is_2(self):
        for cmd in (("lock", "1", "lane-a"), ("unlock", "1", "lane-a"), ("setup", "1")):
            rc, out, err = self.cli(*cmd)
            self.assertEqual(rc, 2, cmd)
            self.assertIn("slot 1", out + err)
        self.assertFalse(os.path.exists(self.cfg.lock_path(1)))

    def test_env_defaults(self):
        rc, _, _ = self.cli("lock", env={"MODKIT_SLOT": "3", "MODKIT_RUN": "lane-env"})
        self.assertEqual(rc, 0)
        self.assertEqual(gs.lock_info(self.cfg, 3).owner, "lane-env")
        rc, _, _ = self.cli("unlock", env={"MODKIT_SLOT": "3", "MODKIT_RUN": "lane-env"})
        self.assertEqual(rc, 0)
        env = {k: v for k, v in os.environ.items() if k != "MODKIT_SLOT"}
        with mock.patch.dict(os.environ, env, clear=True):
            rc, _, err = self.cli("lock")
        self.assertEqual(rc, 2)
        self.assertIn("slot", err)

    def test_lock_any(self):
        rc, out, _ = self.cli("lock", "any", "lane-a")
        self.assertEqual(rc, 0, out)
        self.assertEqual(gs.lock_info(self.cfg, 2).owner, "lane-a")
        self.assertEqual(gs.lock_info(self.cfg, 2).pid, 0)

    def test_bad_config_exit_code(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = gs.main(["--config", os.path.join(self.tmp.name, "nope.toml"), "status"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
