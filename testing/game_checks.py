#!/usr/bin/env python
"""A small framework for named in-game checks.

  python testing/game_checks.py list [names or tags]
  python testing/game_checks.py run  [names or tags] --slot N [--run-id ID] [--keep] [--stop-on-fail]
                                     [--module M] [--path DIR] [--config F]

A check is a function registered with a decorator:

    from game_checks import check

    @check("main-menu-visible", tags=["smoke"])
    def main_menu_visible(ctx):
        ctx.ensure_running()
        ctx.shot("menu")
        assert ctx.game.screenshot().stats()[1] > 8, "the screen is blank"

It passes when it returns, fails on AssertionError or any other error, and is skipped with ctx.skip("why").
Checks are found in the modules listed in checks.modules of the config (default: example_checks) and in --module.

Selecting: a name picks that check, `tag:smoke` (or a bare word that is not a name) picks every check with that
tag. No selection runs everything.

The run id is MODKIT_RUN (else a generated one). Results go to <runs.dir>/<run id>/: results.json, summary.txt and a
screenshots folder. The slot lock is held for the run: when MODKIT_RUN already holds it (`game_slots.py lock N
<lane>`), that lock is kept; when the slot is free the run takes it and frees it at the end.

Exit codes: 0 all passed (skips are fine), 1 a check failed, 2 bad usage / config / the slot is not available.
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if __name__ == "__main__":
    # example checks do `from game_checks import check`: that must be THIS module, not a second copy
    sys.modules.setdefault("game_checks", sys.modules["__main__"])
import game_ctl  # noqa: E402
import game_slots  # noqa: E402
import testing_config as tc  # noqa: E402


class Skip(Exception):
    """Raised by a check that cannot run here (not an error)."""


class UnknownCheck(Exception):
    def __init__(self, names):
        super().__init__("unknown check or tag: " + ", ".join(names))
        self.names = names


class RunRefused(Exception):
    """The run could not start (slot 1, slot locked by someone else, bad run id)."""


# ---------------------------------------------------------------- registry


@dataclass
class CheckInfo:
    name: str
    fn: object
    tags: tuple
    doc: str = ""


REGISTRY: dict[str, CheckInfo] = {}


def check(name: str, tags=(), registry: dict | None = None):
    """Register a check function `fn(ctx)` under a unique name."""
    reg = REGISTRY if registry is None else registry
    if isinstance(tags, str):
        tags = (tags,)

    def deco(fn):
        old = reg.get(name)
        if old is not None and getattr(old.fn, "__qualname__", None) != getattr(fn, "__qualname__", ""):
            raise ValueError(f"check name {name!r} is already registered by {old.fn.__qualname__}")
        reg[name] = CheckInfo(name, fn, tuple(tags), (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "")
        return fn
    return deco


def select(tokens, registry: dict | None = None) -> list[CheckInfo]:
    """Resolve names / tags to checks, in registry order, each once. No tokens: all checks."""
    reg = REGISTRY if registry is None else registry
    if not tokens:
        return list(reg.values())
    chosen: dict[str, CheckInfo] = {}
    unknown = []
    for t in tokens:
        if t in reg:
            chosen[t] = reg[t]
            continue
        tag = t[4:] if t.startswith("tag:") else t
        hits = [c for c in reg.values() if tag in c.tags]
        if hits:
            chosen.update({c.name: c for c in hits})
        else:
            unknown.append(t)
    if unknown:
        raise UnknownCheck(unknown)
    return [c for c in reg.values() if c.name in chosen]


def load_checks(cfg: tc.Config, modules=None, paths=None) -> list[str]:
    """Import the modules that register checks. Returns the module names loaded."""
    for p in list(cfg["checks"]["paths"]) + list(paths or []):
        full = cfg.path(p)
        if full not in sys.path:
            sys.path.insert(0, full)
    names = list(dict.fromkeys(list(cfg["checks"]["modules"]) + list(modules or [])))
    for m in names:
        importlib.import_module(m)
    return names


# ---------------------------------------------------------------- results


@dataclass
class Result:
    name: str
    tags: tuple
    status: str  # pass | fail | skip
    message: str = ""
    duration_s: float = 0.0
    screenshots: list = field(default_factory=list)
    log: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "tags": list(self.tags), "status": self.status, "message": self.message,
                "duration_s": round(self.duration_s, 3), "screenshots": self.screenshots, "log": self.log}


@dataclass
class RunReport:
    run_id: str
    slot: int
    run_dir: str
    started: str
    finished: str = ""
    duration_s: float = 0.0
    results: list = field(default_factory=list)

    @property
    def counts(self) -> dict:
        c = {"pass": 0, "fail": 0, "skip": 0}
        for r in self.results:
            c[r.status] += 1
        return c

    @property
    def exit_code(self) -> int:
        return 1 if self.counts["fail"] else 0

    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "slot": self.slot, "started": self.started, "finished": self.finished,
                "duration_s": round(self.duration_s, 3), "counts": self.counts,
                "results": [r.as_dict() for r in self.results]}

    def summary(self) -> str:
        c = self.counts
        lines = [f"Run {self.run_id} on slot {self.slot}: {c['pass']} passed, {c['fail']} failed, "
                 f"{c['skip']} skipped ({self.duration_s:.1f} s)"]
        for r in self.results:
            lines.append(f"{r.status.upper():<5} {r.name}  ({r.duration_s:.1f} s)" + (f"  {r.message}" if r.message else ""))
            lines.extend(f"        screenshot: {s}" for s in r.screenshots)
        return "\n".join(lines) + "\n"

    def write(self) -> None:
        os.makedirs(self.run_dir, exist_ok=True)
        with open(os.path.join(self.run_dir, "results.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.as_dict(), f, indent=2)
            f.write("\n")
        with open(os.path.join(self.run_dir, "summary.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(self.summary())


# ---------------------------------------------------------------- context given to every check


class Context:
    """What a check gets: the config, the slot, the game (game_ctl.Game) and helpers."""

    def __init__(self, cfg: tc.Config, slot: int, game, run_dir: str, shots_dir: str, shared: dict, runner=None):
        self.cfg, self.slot, self.game = cfg, slot, game
        self.run_dir, self.shots_dir, self.shared = run_dir, shots_dir, shared
        self.check_name = ""
        self.shots: list[str] = []
        self.lines: list[str] = []
        self._runner = runner

    def log(self, msg: str) -> None:
        self.lines.append(f"{time.strftime('%H:%M:%S')} {msg}")

    def skip(self, why: str):
        raise Skip(why)

    def fail(self, why: str):
        raise AssertionError(why)

    def ensure_running(self) -> None:
        """Start the game if it is not up (the run stops it at the end, unless --keep)."""
        if not self.game.is_running():
            self.log(f"starting the game on slot {self.slot}")
            self.game.start()
            if self._runner is not None:
                self._runner.started_game = True

    def require_harness(self):
        """The plugin harness, or skip the check when it is not reachable."""
        try:
            return self.game.need_harness()
        except game_ctl.GameUnavailable as e:
            raise Skip(str(e)) from e

    def shot(self, name: str) -> str:
        """Screenshot of the game window into this run's screenshots folder; returns the file path."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        path = os.path.join(self.shots_dir, f"{self.check_name}-{safe}.png")
        saved = self.game.shot(path)
        rel = os.path.relpath(saved, self.run_dir).replace(os.sep, "/")
        self.shots.append(rel)
        return saved


# ---------------------------------------------------------------- the runner


class Runner:
    def __init__(self):
        self.started_game = False


def new_run_id(slot: int, now: datetime.datetime | None = None) -> str:
    return f"run-{(now or datetime.datetime.now()):%Y%m%d-%H%M%S}-s{slot}"


def run(selected, slot: int, cfg: tc.Config | None = None, game=None, run_id: str | None = None,
        keep: bool = False, stop_on_fail: bool = False, out=print) -> RunReport:
    """Run checks against a slot and write the results.

    selected: names / tags (strings) or CheckInfo objects. game: an object like game_ctl.Game (tests pass a
    fake; then no lock is taken and nothing is started). Slot 1 is refused."""
    cfg = cfg or tc.load()
    slot = cfg.check_slot_number(slot)
    if cfg.is_slot_one(slot):
        raise RunRefused(game_slots.SLOT1_MSG)
    infos = [s if isinstance(s, CheckInfo) else None for s in selected]
    if any(i is None for i in infos):
        infos = select(list(selected))
    run_id = run_id or tc.env_run() or new_run_id(slot)
    if not tc.valid_run_id(run_id):
        raise RunRefused(f"bad run id {run_id!r}: use letters, digits, dot, dash and underscore")
    taken = False
    if game is None:
        ok, msg, taken = game_slots.ensure_lock(cfg, slot, run_id)
        if not ok:
            raise RunRefused(msg + " (take it with: game_slots.py lock N <lane>, and run with MODKIT_RUN=<lane>)")
        game = game_ctl.Game(cfg, slot, owner=run_id)
    run_dir = cfg.run_dir(run_id)
    shots_dir = os.path.join(run_dir, cfg["runs"]["screenshots_dir"])
    os.makedirs(shots_dir, exist_ok=True)
    report = RunReport(run_id, slot, run_dir, datetime.datetime.now().isoformat(timespec="seconds"))
    runner = Runner()
    shared: dict = {}
    try:
        shared["crash_before"] = list(game.crash_files()) if hasattr(game, "crash_files") else []
    except Exception:
        shared["crash_before"] = []
    t_run = time.monotonic()
    try:
        for info in infos:
            ctx = Context(cfg, slot, game, run_dir, shots_dir, shared, runner)
            ctx.check_name = info.name
            t0 = time.monotonic()
            try:
                msg = info.fn(ctx)
                status, message = "pass", (msg if isinstance(msg, str) else "")
            except Skip as e:
                status, message = "skip", str(e)
            except AssertionError as e:
                status, message = "fail", str(e) or "assertion failed"
            except game_ctl.GameUnavailable as e:
                status = "skip" if not game_ctl.on_windows() else "fail"
                message = str(e)
            except Exception as e:  # a broken check or game must not stop the run
                status, message = "fail", f"{type(e).__name__}: {e}"
                ctx.lines.extend(traceback.format_exc().splitlines()[-6:])
            r = Result(info.name, info.tags, status, message, time.monotonic() - t0, list(ctx.shots), ctx.lines)
            report.results.append(r)
            out(f"{status.upper():<5} {info.name}" + (f"  {message}" if message else ""))
            if status == "fail" and stop_on_fail:
                break
    finally:
        report.duration_s = time.monotonic() - t_run
        report.finished = datetime.datetime.now().isoformat(timespec="seconds")
        if runner.started_game and not keep:
            try:
                game.stop()
            except Exception as e:
                out(f"could not stop the game: {e}")
        if taken:
            game_slots.release(cfg, slot, run_id, manual=True)  # this run made the lock, so it frees it
        report.write()
    return report


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tc.add_config_arg(ap)
    ap.add_argument("--module", action="append", default=[], help="extra module that registers checks")
    ap.add_argument("--path", action="append", default=[], help="extra folder to find modules in")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("list")
    p.add_argument("select", nargs="*")
    p = sub.add_parser("run")
    p.add_argument("select", nargs="*")
    p.add_argument("--slot", type=int, help="slot number (or MODKIT_SLOT)")
    p.add_argument("--run-id", help="run id (default MODKIT_RUN, else generated)")
    p.add_argument("--keep", action="store_true", help="leave the game running at the end")
    p.add_argument("--stop-on-fail", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        print(__doc__)
        return 0
    try:
        cfg = tc.load(a.config)
        load_checks(cfg, a.module, a.path)
        infos = select(a.select)
        if a.cmd == "list":
            for c in infos:
                print(f"{c.name:<28} {','.join(c.tags) or '-':<22} {c.doc}")
            return 0
        slot = a.slot or tc.env_slot()
        if slot is None:
            raise RunRefused("give --slot N or set MODKIT_SLOT")
        if not infos:
            raise RunRefused("no checks found (checks.modules in the config, or --module)")
        report = run(infos, slot, cfg, run_id=a.run_id, keep=a.keep, stop_on_fail=a.stop_on_fail)
        print(report.summary(), end="")
        print(f"results: {os.path.join(report.run_dir, 'results.json')}")
        return report.exit_code
    except (tc.ConfigError, UnknownCheck, RunRefused, game_ctl.GameCtlError, ImportError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
