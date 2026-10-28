"""Tests for testing/game_checks.py and testing/game_ctl.py (run: python -m unittest discover tests).

No game, no network, no Windows needed: the game is a fake, the plugin's harness block is plain memory.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "testing"))

import game_checks as gc  # noqa: E402
import game_ctl  # noqa: E402
import game_slots as gs  # noqa: E402
import testing_config as tc  # noqa: E402


def make_cfg(tmp: str, **over) -> tc.Config:
    t = Path(tmp).as_posix()
    data = {
        "game": {"install_dir": f"{t}/install", "slots_dir": f"{t}/slots", "slot_count": 3},
        "mod": {"folder": "MyMod", "plugin_dlls": ["my-plugin.dll"]},
        "saves": {"root": f"{t}/saves"},
        "runs": {"dir": f"{t}/runs"},
    }
    return tc.Config(tc.deep_merge(data, over), base_dir=tmp)


class FakeGame:
    """Stands in for game_ctl.Game."""

    def __init__(self, run_dir=None):
        self.running = False
        self.started = self.stopped = 0
        self.shots = []

    def is_running(self):
        return self.running

    def start(self):
        self.running = True
        self.started += 1

    def stop(self):
        self.running = False
        self.stopped += 1

    def shot(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_bytes(b"png")
        self.shots.append(path)
        return path

    def crash_files(self):
        return []

    def need_harness(self):
        raise game_ctl.GameUnavailable("harness is off")


def new_registry():
    return {}


class RegistryTests(unittest.TestCase):
    def test_decorator_registers_in_order_with_tags_and_doc(self):
        reg = new_registry()

        @gc.check("a", tags=["x", "y"], registry=reg)
        def a(ctx):
            """First line.

            More."""

        @gc.check("b", registry=reg)
        def b(ctx):
            pass

        self.assertEqual(list(reg), ["a", "b"])
        self.assertEqual(reg["a"].tags, ("x", "y"))
        self.assertEqual(reg["a"].doc, "First line.")
        self.assertEqual(reg["b"].tags, ())
        self.assertIs(a.__name__, "a")  # the function is returned unchanged

    def test_a_single_tag_string_is_one_tag(self):
        reg = new_registry()
        gc.check("a", tags="smoke", registry=reg)(lambda ctx: None)
        self.assertEqual(reg["a"].tags, ("smoke",))

    def test_duplicate_name_is_an_error(self):
        reg = new_registry()

        @gc.check("a", registry=reg)
        def one(ctx):
            pass

        with self.assertRaises(ValueError):
            @gc.check("a", registry=reg)
            def two(ctx):
                pass

    def test_selection(self):
        reg = new_registry()
        for name, tags in (("alpha", ["smoke", "game"]), ("beta", ["game"]), ("gamma", [])):
            gc.check(name, tags=tags, registry=reg)(lambda ctx: None)
        names = lambda toks: [c.name for c in gc.select(toks, reg)]  # noqa: E731
        self.assertEqual(names([]), ["alpha", "beta", "gamma"])
        self.assertEqual(names(["beta"]), ["beta"])
        self.assertEqual(names(["tag:game"]), ["alpha", "beta"])
        self.assertEqual(names(["smoke"]), ["alpha"])  # a bare word that is not a name is a tag
        self.assertEqual(names(["gamma", "alpha", "alpha"]), ["alpha", "gamma"])  # registry order, once each
        with self.assertRaises(gc.UnknownCheck) as cm:
            gc.select(["alpha", "nope", "tag:none"], reg)
        self.assertEqual(cm.exception.names, ["nope", "tag:none"])

    def test_example_checks_register(self):
        import example_checks  # noqa: F401
        self.addCleanup(lambda: None)
        for name in ("config-sane", "game-starts", "main-menu-visible", "console-echo", "no-new-crash-files"):
            self.assertIn(name, gc.REGISTRY)

    def test_load_checks_from_a_configured_path(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "my_probe_checks.py").write_text(
                "from game_checks import check\n@check('probe-from-path', tags=['probe'])\ndef f(ctx):\n    return 'ok'\n",
                encoding="utf-8")
            cfg = make_cfg(d, checks={"modules": [], "paths": ["."]})
            self.addCleanup(lambda: gc.REGISTRY.pop("probe-from-path", None))
            self.addCleanup(lambda: sys.modules.pop("my_probe_checks", None))
            self.addCleanup(lambda: sys.path.remove(os.path.normpath(d)) if os.path.normpath(d) in sys.path else None)
            self.assertEqual(gc.load_checks(cfg, ["my_probe_checks"]), ["my_probe_checks"])
            self.assertIn("probe-from-path", gc.REGISTRY)
            with self.assertRaises(ImportError):
                gc.load_checks(cfg, ["no_such_module_here"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)
        self.reg = new_registry()
        self.out = []
        env = {k: v for k, v in os.environ.items() if k != "MODKIT_RUN"}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def add(self, name, fn, tags=()):
        gc.check(name, tags=tags, registry=self.reg)(fn)

    def run_all(self, tokens=(), game=None, **kw):
        game = game or FakeGame()
        infos = gc.select(list(tokens), self.reg)
        rep = gc.run(infos, kw.pop("slot", 2), self.cfg, game=game, out=self.out.append, **kw)
        return rep, game

    def test_pass_fail_skip_and_error_are_told_apart(self):
        def good(ctx):
            return "fine"

        def bad(ctx):
            assert 1 == 2, "one is not two"

        def skipped(ctx):
            ctx.skip("not today")

        def broken(ctx):
            raise KeyError("boom")

        def silent_assert(ctx):
            assert False

        for n, f in (("good", good), ("bad", bad), ("skipped", skipped), ("broken", broken), ("silent", silent_assert)):
            self.add(n, f)
        rep, _ = self.run_all(run_id="t1")
        st = {r.name: (r.status, r.message) for r in rep.results}
        self.assertEqual(st["good"], ("pass", "fine"))
        self.assertEqual(st["bad"], ("fail", "one is not two"))
        self.assertEqual(st["skipped"], ("skip", "not today"))
        self.assertEqual(st["broken"][0], "fail")
        self.assertIn("KeyError", st["broken"][1])
        self.assertEqual(st["silent"], ("fail", "assertion failed"))
        self.assertEqual(rep.counts, {"pass": 1, "fail": 3, "skip": 1})
        self.assertEqual(rep.exit_code, 1)
        self.assertTrue(all(r.duration_s >= 0 for r in rep.results))

    def test_all_pass_and_skips_exit_zero(self):
        self.add("a", lambda ctx: None)
        self.add("b", lambda ctx: ctx.skip("later"))
        rep, _ = self.run_all(run_id="t2")
        self.assertEqual(rep.exit_code, 0)

    def test_results_files_are_written(self):
        self.add("a", lambda ctx: "hello", tags=["t"])
        self.add("b", lambda ctx: ctx.fail("nope"))
        rep, _ = self.run_all(run_id="lane-x")
        d = Path(self.cfg.runs_dir, "lane-x")
        data = json.loads((d / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "lane-x")
        self.assertEqual(data["slot"], 2)
        self.assertEqual(data["counts"], {"pass": 1, "fail": 1, "skip": 0})
        self.assertEqual([r["name"] for r in data["results"]], ["a", "b"])
        self.assertEqual(data["results"][0]["tags"], ["t"])
        self.assertEqual(data["results"][1]["message"], "nope")
        text = (d / "summary.txt").read_text(encoding="utf-8")
        self.assertIn("1 passed, 1 failed, 0 skipped", text)
        self.assertIn("FAIL  b", text)
        self.assertTrue((d / "screenshots").is_dir())
        self.assertNotIn(b"\r\n", (d / "results.json").read_bytes())

    def test_run_id_comes_from_modkit_run_or_is_generated(self):
        self.add("a", lambda ctx: None)
        with mock.patch.dict(os.environ, {"MODKIT_RUN": "my-lane"}):
            rep, _ = self.run_all()
        self.assertEqual(rep.run_id, "my-lane")
        rep, _ = self.run_all(slot=3)
        self.assertRegex(rep.run_id, r"^run-\d{8}-\d{6}-s3$")

    def test_bad_run_id_and_slot_one_are_refused_before_anything(self):
        self.add("a", lambda ctx: None)
        with self.assertRaises(gc.RunRefused):
            self.run_all(run_id="../x")
        with self.assertRaises(gc.RunRefused) as cm:
            self.run_all(slot=1, run_id="ok")
        self.assertIn("slot 1", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.cfg.runs_dir, "ok")))

    def test_screenshots_go_to_the_runs_folder(self):
        def snap(ctx):
            p = ctx.shot("the menu!")
            assert os.path.isfile(p)

        self.add("snap", snap)
        rep, game = self.run_all(run_id="shots")
        shots = rep.results[0].screenshots
        self.assertEqual(shots, ["screenshots/snap-the_menu_.png"])
        self.assertTrue(Path(rep.run_dir, shots[0]).is_file())

    def test_ensure_running_starts_and_the_run_stops_the_game(self):
        self.add("a", lambda ctx: ctx.ensure_running())
        self.add("b", lambda ctx: ctx.ensure_running())
        rep, game = self.run_all(run_id="g1")
        self.assertEqual((game.started, game.stopped), (1, 1))
        rep, game = self.run_all(run_id="g2", keep=True)
        self.assertEqual((game.started, game.stopped), (1, 0))

    def test_a_game_that_was_already_running_is_left_running(self):
        self.add("a", lambda ctx: ctx.ensure_running())
        game = FakeGame()
        game.running = True
        self.run_all(game=game, run_id="g3")
        self.assertEqual((game.started, game.stopped), (0, 0))

    def test_stop_on_fail(self):
        self.add("a", lambda ctx: ctx.fail("x"))
        self.add("b", lambda ctx: None)
        rep, _ = self.run_all(run_id="s1", stop_on_fail=True)
        self.assertEqual([r.name for r in rep.results], ["a"])

    def test_results_are_written_even_when_interrupted(self):
        def stop(ctx):
            raise KeyboardInterrupt

        self.add("a", stop)
        with self.assertRaises(KeyboardInterrupt):
            self.run_all(run_id="int")
        self.assertTrue(Path(self.cfg.runs_dir, "int", "results.json").is_file())

    def test_game_unavailable_skips_off_windows_and_fails_on_windows(self):
        def needs_game(ctx):
            ctx.game.need_harness()

        self.add("needs", needs_game)
        with mock.patch.object(game_ctl, "on_windows", return_value=False):
            rep, _ = self.run_all(run_id="u1")
        self.assertEqual(rep.results[0].status, "skip")
        self.assertIn("harness", rep.results[0].message)
        with mock.patch.object(game_ctl, "on_windows", return_value=True):
            rep, _ = self.run_all(run_id="u2")
        self.assertEqual(rep.results[0].status, "fail")

    def test_require_harness_skips(self):
        self.add("h", lambda ctx: ctx.require_harness())
        rep, _ = self.run_all(run_id="h1")
        self.assertEqual(rep.results[0].status, "skip")

    def test_real_run_takes_and_frees_the_slot_lock(self):
        seen = {}

        def look(ctx):
            seen["lock"] = gs.lock_info(ctx.cfg, 2)

        self.add("look", look)
        infos = gc.select([], self.reg)
        rep = gc.run(infos, 2, self.cfg, run_id="lockrun", out=self.out.append)  # no fake game: a real Game, never started
        self.assertEqual(seen["lock"].owner, "lockrun")
        self.assertIsNone(gs.lock_info(self.cfg, 2))
        self.assertEqual(rep.exit_code, 0)

    def test_run_refuses_a_slot_held_by_somebody_else_and_keeps_a_session_lock(self):
        self.add("a", lambda ctx: None)
        infos = gc.select([], self.reg)
        gs.acquire(self.cfg, 2, "lane-a", pid=0)
        with self.assertRaises(gc.RunRefused) as cm:
            gc.run(infos, 2, self.cfg, run_id="lane-b", out=self.out.append)
        self.assertIn("lane-a", str(cm.exception))
        gc.run(infos, 2, self.cfg, run_id="lane-a", out=self.out.append)  # the lane runs inside its own session lock
        self.assertEqual(gs.lock_info(self.cfg, 2).owner, "lane-a")  # and the lock is still there afterwards


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name).as_posix()
        self.conf = os.path.join(self.tmp.name, "c.json")
        Path(self.conf).write_text(json.dumps({
            "game": {"install_dir": f"{t}/install", "slots_dir": f"{t}/slots"},
            "mod": {"folder": "MyMod", "plugin_dlls": ["my-plugin.dll"]},
            "runs": {"dir": f"{t}/runs"}, "saves": {"root": f"{t}/saves"},
            "checks": {"modules": ["example_checks"]}}), encoding="utf-8")
        self.cfg = tc.load(self.conf)
        env = {k: v for k, v in os.environ.items() if k not in ("MODKIT_RUN", "MODKIT_SLOT")}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = gc.main(["--config", self.conf, *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_list_and_filters(self):
        rc, out, _ = self.cli("list")
        self.assertEqual(rc, 0)
        self.assertIn("game-starts", out)
        rc, out, _ = self.cli("list", "tag:harness")
        self.assertIn("console-echo", out)
        self.assertNotIn("game-starts", out)

    def test_unknown_check_and_missing_slot_and_slot_one_exit_2(self):
        self.assertEqual(self.cli("run", "no-such-check", "--slot", "2")[0], 2)
        rc, _, err = self.cli("run", "config-sane")
        self.assertEqual(rc, 2)
        self.assertIn("slot", err)
        rc, _, err = self.cli("run", "config-sane", "--slot", "1")
        self.assertEqual(rc, 2)
        self.assertIn("slot 1", err)

    def test_offline_check_fails_then_passes_once_the_slot_exists(self):
        rc, out, _ = self.cli("run", "config-sane", "--slot", "2", "--run-id", "cli1")
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out)
        data = json.loads(Path(self.cfg.runs_dir, "cli1", "results.json").read_text(encoding="utf-8"))
        self.assertEqual(data["counts"]["fail"], 1)
        slot = Path(self.cfg.slot_dir(2))
        (slot / "mods" / "MyMod" / "d2rloader" / "plugins").mkdir(parents=True)
        (slot / "D2RLoader.exe").write_bytes(b"x")
        (slot / "mods" / "MyMod" / "d2rloader" / "plugins" / "my-plugin.dll").write_bytes(b"x")
        rc, out, _ = self.cli("run", "tag:offline", "--slot", "2", "--run-id", "cli2")
        self.assertEqual(rc, 0, out)
        self.assertIsNone(gs.lock_info(self.cfg, 2))


# ---------------------------------------------------------------- game_ctl


def decode_png(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, size = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        payload = data[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        assert crc == zlib.crc32(kind + payload)
        if kind == b"IHDR":
            size = struct.unpack(">II", payload[:8])
        elif kind == b"IDAT":
            idat += payload
        pos += 12 + n
    raw = zlib.decompress(idat)
    w, h = size
    rows = [raw[y * (w * 3 + 1) + 1:(y + 1) * (w * 3 + 1)] for y in range(h)]
    return w, h, b"".join(rows)


class ImageTests(unittest.TestCase):
    def test_png_roundtrip(self):
        rgb = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 10, 20, 30])
        img = game_ctl.Image(2, 2, rgb)
        self.assertEqual(decode_png(img.png()), (2, 2, rgb))
        with tempfile.TemporaryDirectory() as d:
            p = img.save(os.path.join(d, "sub", "a.png"))
            self.assertEqual(decode_png(Path(p).read_bytes())[2], rgb)

    def test_from_bgra_swaps_channels_and_crops(self):
        bgra = bytes([1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255, 10, 11, 12, 255])  # 2 x 2, B G R A
        full = game_ctl.Image.from_bgra(2, 2, bgra)
        self.assertEqual(full.pixel(0, 0), (3, 2, 1))
        self.assertEqual(full.pixel(1, 1), (12, 11, 10))
        part = game_ctl.Image.from_bgra(2, 2, bgra, (1, 0, 2, 2))
        self.assertEqual((part.width, part.height), (1, 2))
        self.assertEqual(part.pixel(0, 1), (12, 11, 10))

    def test_size_mismatch(self):
        with self.assertRaises(ValueError):
            game_ctl.Image(2, 2, b"abc")

    def test_stats_tell_blank_from_picture(self):
        black = game_ctl.Image(8, 8, bytes(8 * 8 * 3))
        self.assertEqual(black.stats(), (0.0, 0.0))
        rows = b"".join((bytes([200] * 3) if (x + y) % 2 else bytes(3)) for y in range(8) for x in range(8))
        noisy = game_ctl.Image(8, 8, rows)
        mean, dev = noisy.stats()
        self.assertAlmostEqual(mean, 100, delta=1)
        self.assertGreater(dev, 90)

    def test_signature_distance(self):
        left = game_ctl.Image(8, 4, (bytes([255] * 3) * 4 + bytes(3) * 4) * 4)  # left half white, right half black
        same = game_ctl.Image(8, 4, (bytes([255] * 3) * 4 + bytes(3) * 4) * 4)
        flat = game_ctl.Image(8, 4, bytes([128] * 3) * 32)
        a, b, c = (i.signature(cols=4, rows=2) for i in (left, same, flat))
        self.assertEqual(game_ctl.signature_distance(a, b), 0)
        self.assertGreater(game_ctl.signature_distance(a, c), 50)
        self.assertEqual(len(a), 8)


class WaitTests(unittest.TestCase):
    def test_wait_for_returns_value_and_times_out(self):
        now = [0.0]
        calls = []

        def sleep(s):
            now[0] += s

        def cond():
            calls.append(now[0])
            return "yes" if now[0] >= 2 else None

        self.assertEqual(game_ctl.wait_for(cond, 10, 1, sleep=sleep, clock=lambda: now[0]), "yes")
        with self.assertRaises(game_ctl.WaitTimeout) as cm:
            game_ctl.wait_for(lambda: None, 3, 1, "the moon", sleep=sleep, clock=lambda: now[0])
        self.assertIn("the moon", str(cm.exception))

    def test_read_from_and_wait_log(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.log")
            self.assertEqual(game_ctl.read_from(p), "")
            Path(p).write_bytes(b"old line\n")
            mark = game_ctl.file_size(p)
            with open(p, "ab") as f:
                f.write(b"new line\nREADY\n")
            self.assertEqual(game_ctl.read_from(p, mark), "new line\nREADY\n")
            self.assertTrue(game_ctl.wait_log(p, "READY", 1, mark))
            self.assertFalse(game_ctl.wait_log(p, "READY", 0.01, os.path.getsize(p), sleep=lambda s: None))
            Path(p).write_text("x")  # truncated below the mark: read from the start
            self.assertEqual(game_ctl.read_from(p, 500), "x")


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)
        self.block = game_ctl.BytesBlock(self.cfg["harness"]["block_bytes"])
        self.h = game_ctl.Harness(self.cfg, self.block)

    def plugin(self, result=1, answer=True):
        """A thread playing the plugin: when cmd_seq changes, copy it to cmd_ack and set the result."""
        stop = threading.Event()
        seen = []

        def loop():
            last = 0
            while not stop.is_set():
                seq = self.block.get(self.cfg["harness"]["idx_cmd_seq"])
                if seq != last and answer:
                    off = self.cfg["harness"]["command_offset"]
                    seen.append(bytes(self.block.buf[off:self.block.buf.index(0, off)]).decode())
                    self.block.set(self.cfg["harness"]["idx_cmd_result"], result)
                    self.block.set(self.cfg["harness"]["idx_cmd_ack"], seq)
                    last = seq
                time.sleep(0.005)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self.addCleanup(lambda: (stop.set(), t.join(1)))
        return seen

    def test_layout_follows_the_config(self):
        h = self.cfg["harness"]
        self.assertFalse(self.h.magic_ok())
        self.block.set(h["idx_magic"], h["magic"])
        self.assertTrue(self.h.magic_ok())
        self.h.activate()
        self.assertEqual(self.block.get(h["idx_active"]), 1)
        self.assertEqual(self.block.get(h["idx_flags"]), h["flag_background"])
        self.assertEqual((self.h.get("x"), self.h.get("y")), tuple(h["cursor_start"]))
        self.h.move(10, 20)
        self.h.buttons(3)
        self.h.set_mods(["shift", "alt"])
        self.assertEqual([self.block.get(h[k]) for k in ("idx_x", "idx_y", "idx_buttons", "idx_mods")], [10, 20, 3, 5])
        with self.assertRaises(KeyError):
            self.h.set_mods(["hyper"])

    def test_a_custom_layout_is_honoured(self):
        cfg = make_cfg(self.tmp.name, harness={"idx_x": 12, "idx_y": 13, "magic": 77})
        block = game_ctl.BytesBlock(cfg["harness"]["block_bytes"])
        h = game_ctl.Harness(cfg, block)
        h.move(5, 6)
        self.assertEqual((block.get(12), block.get(13), block.get(2), block.get(3)), (5, 6, 0, 0))
        block.set(0, 77)
        self.assertTrue(h.magic_ok())

    def test_command_gets_acked(self):
        seen = self.plugin(result=1)
        self.assertEqual(self.h.run_command("echo hi", timeout=2), (True, 1))
        self.assertEqual(self.h.run_command("second", timeout=2), (True, 1))
        self.assertEqual(seen, ["echo hi", "second"])
        self.assertEqual(self.h.get("cmd_seq"), 2)

    def test_command_result_is_passed_on(self):
        self.plugin(result=0)
        self.assertEqual(self.h.run_command("nope", timeout=2), (True, 0))

    def test_no_answer_times_out(self):
        self.plugin(answer=False)
        self.assertEqual(self.h.run_command("x", timeout=0.1), (False, 0))

    def test_long_command_is_cut(self):
        seen = self.plugin()
        self.h.run_command("a" * 2000, timeout=2)
        self.assertEqual(len(seen[0]), self.cfg["harness"]["command_max"])

    def test_seq_wraps_to_positive(self):
        self.block.set(self.cfg["harness"]["idx_cmd_seq"], 0x7FFFFFFF)
        self.plugin()
        self.assertEqual(self.h.run_command("x", timeout=2), (True, 1))
        self.assertEqual(self.h.get("cmd_seq"), 0)


class GameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)

    def test_slot_one_is_refused(self):
        with self.assertRaises(game_ctl.GameCtlError) as cm:
            game_ctl.Game(self.cfg, 1)
        self.assertIn("slot 1", str(cm.exception))
        with self.assertRaises(tc.ConfigError):
            game_ctl.Game(self.cfg, 7)

    def test_not_running_and_not_windows_degrade_cleanly(self):
        g = game_ctl.Game(self.cfg, 2, owner="t", log=lambda m: None)
        self.assertEqual(g.pids(), [])
        self.assertFalse(g.is_running())
        self.assertIsNone(g.harness())
        with self.assertRaises(game_ctl.GameUnavailable):
            g.need_harness()
        with self.assertRaises(game_ctl.GameUnavailable):
            g.console("x")
        with mock.patch.object(game_ctl, "on_windows", return_value=False):
            for fn in (g.screenshot, g.start, lambda: g.key("esc"), lambda: g.click(0.5, 0.5), g.window):
                with self.assertRaises(game_ctl.GameUnavailable):
                    fn()
        g.stop()  # nothing running: harmless

    def test_unknown_key_is_an_error_not_a_crash(self):
        g = game_ctl.Game(self.cfg, 2, log=lambda m: None)
        with self.assertRaises(game_ctl.GameCtlError):
            g.key("hyper+z")

    def test_console_through_a_fake_block(self):
        h = self.cfg["harness"]
        block = game_ctl.BytesBlock(h["block_bytes"])
        block.set(h["idx_magic"], h["magic"])
        g = game_ctl.Game(self.cfg, 2, owner="t", block_factory=lambda name, size: block, log=lambda m: None)
        stop = threading.Event()

        def plugin():
            last = 0
            while not stop.is_set():
                seq = block.get(h["idx_cmd_seq"])
                if seq != last:
                    Path(self.cfg.console_file(2)).parent.mkdir(parents=True, exist_ok=True)
                    with open(self.cfg.console_file(2), "a", newline="\n") as f:
                        f.write("handled\n")
                    block.set(h["idx_cmd_result"], 1)
                    block.set(h["idx_cmd_ack"], seq)
                    last = seq
                time.sleep(0.005)

        t = threading.Thread(target=plugin, daemon=True)
        t.start()
        self.addCleanup(lambda: (stop.set(), t.join(1)))
        with mock.patch.object(gs, "slot_processes", return_value=[(4242, "D2R.exe")]):
            self.assertIs(g.harness().block, block)
            self.assertEqual(block.get(h["idx_active"]), 1)
            self.assertTrue(g.console("echo hi", timeout=2))
            mark = g.console_mark()
            self.assertEqual(g.console_text("again", settle=0.05), "handled\n")
            self.assertEqual(g.wait_console("handled", mark, timeout=1), "handled\n")
            with self.assertRaises(game_ctl.WaitTimeout):
                g.wait_console("never", g.console_mark(), timeout=0.05)

    def test_harness_word_file_is_written_and_removed(self):
        cfg = make_cfg(self.tmp.name, harness={"words": ["tag=s{slot}"]})
        g = game_ctl.Game(cfg, 3, log=lambda m: None)
        g.harness_switch(True)  # the plugins folder does not exist yet: nothing is created
        self.assertFalse(os.path.exists(cfg.harness_word_file(3)))
        os.makedirs(cfg.plugins_dir(3))
        g.harness_switch(True)
        self.assertEqual(Path(cfg.harness_word_file(3)).read_text(), "tag=s3")
        g.harness_switch(False)
        self.assertFalse(os.path.exists(cfg.harness_word_file(3)))

    def test_crash_file_helpers(self):
        g = game_ctl.Game(self.cfg, 2, log=lambda m: None)
        self.assertEqual(g.crash_files(), [])
        d = Path(self.cfg.crash_dir(2))
        d.mkdir(parents=True)
        (d / "a.dmp").write_text("x")
        before = g.crash_files()
        (d / "b.dmp").write_text("x")
        self.assertEqual(g.new_crash_files(before), ["b.dmp"])

    def test_cli_needs_a_slot_and_refuses_slot_one(self):
        env = {k: v for k, v in os.environ.items() if k != "MODKIT_SLOT"}
        conf = os.path.join(self.tmp.name, "c.json")
        Path(conf).write_text(json.dumps({"game": {"install_dir": "x", "slots_dir": "y"}}), encoding="utf-8")
        for argv in (["status"], ["--slot", "1", "status"]):
            err = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(err):
                rc = game_ctl.main(["--config", conf, *argv])
            self.assertEqual(rc, 2)
            self.assertIn("error:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
