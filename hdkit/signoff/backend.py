"""The game half of the sign-off as an interface: a backend makes one game run, a seat hook decides whether it may start.

hdkit.signoff compares a built 3D unit in the real game against the viewer. How a game is started, locked, laid over
and restored differs per project and machine, so the sign-off only talks to a `Backend`:

  class Backend:
      name = "..."
      def run(self, req: RunRequest, log=print) -> int    one game run of the check `signoff` (gamecheck.py); 0 = ok,
                                                          2 = machine busy, 3 = slot locked, 4 = a tool is missing,
                                                          5 = no seat; anything else = the check failed

Built in (settings `backend`):
  "linux"    run.run_linux: a project's slot framework on a Linux test slot (settings `linux`)
  "windows"  run.main through the project's heavy lock and its game-run script (settings `windows`)
  "auto"     linux or windows by platform
  "<module>:<attribute>"   a project's own backend: the attribute is a Backend subclass or a factory taking the
                           settings dict; the module must be importable (put it on PYTHONPATH or beside hdkit_site)

The seat hook (settings `seats`) runs around every backend run. A machine that runs several projects' games needs a
budget of how many games may run at once; without it two projects start games together and both runs fail or slow down.

  "none" (default)   no budget: every run may start
  "testing"          the toolkit's own seat budget (testing/game_slots.py "Shared seats"): one machine-wide seat,
                     taken before the run, given back after it, also when it fails (the slot lock stays the
                     backend's); settings
                     `seats_config` names the testing.toml (else TESTING_CONFIG / ./testing.toml)
  "<module>:<attribute>"   a callable hook(settings) returning an object with acquire(req, wait_s, log) -> bool
                           and release(req)

The harness verbs the check sends to the game (spawn, place, facing, mcast, kill, move) are documented in
hdkit/signoff/HARNESS.md: a project's test plugin must answer them for any backend.
"""
from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunRequest:
    """One game run: which slot, which unit, where its overlay and the evidence go."""
    tag: str                    # the run's name (evidence subfolder)
    slot: int                   # the test slot
    token: str                  # the unit's classic token
    id: str                     # the unit's monstats Id
    overlay: Path               # the unit's overlay folder (build.json beside it)
    ev: Path                    # the evidence folder of the game half
    env: dict = field(default_factory=dict)     # HDKIT_SIGNOFF_* settings of the check
    dll: str | None = None      # a test plugin laid over the slot for this run (None: the settings')
    holder: str = "hdkit-signoff"   # the name the lock and the seat carry


class Backend:
    """Base class of a sign-off backend. Subclasses set `name` and implement run()."""
    name = "base"

    def __init__(self, settings: dict):
        self.settings = settings

    def run(self, req: RunRequest, log=print) -> int:  # pragma: no cover - interface
        raise NotImplementedError


class LinuxBackend(Backend):
    name = "linux"

    def run(self, req: RunRequest, log=print) -> int:
        from . import run as R
        return R.run_linux(self.settings, req.tag, req.slot, req.token, req.id, req.overlay, req.ev, req.env,
                           dll=req.dll, log=log)


class WindowsBackend(Backend):
    """The project's heavy lock script starts run.py (its game-run script) as a child process."""
    name = "windows"

    def command(self, req: RunRequest) -> list:
        from . import config
        s, w = self.settings, self.settings["windows"]
        here = Path(__file__).resolve().parent
        return (["py", "-3", config.need(s, "windows", "heavy_lock"), w.get("heavy_token") or "game", s["lock_holder"],
                 "--", "py", "-3", str(here / "run.py"), req.tag, "--slot", str(req.slot), "--token", req.token,
                 "--id", req.id, "--overlay", str(req.overlay), "--ev", str(req.ev)]
                + ["%s=%s" % kv for kv in req.env.items()])

    def run(self, req: RunRequest, log=print) -> int:
        import subprocess
        from . import config
        cmd = self.command(req)
        log(" ".join(cmd))
        return subprocess.run(cmd, cwd=str(Path(config.need(self.settings, "windows", "worktree_root")))).returncode


BUILTIN = {"linux": LinuxBackend, "windows": WindowsBackend}


def _load(spec: str):
    mod, _, attr = spec.partition(":")
    if not mod or not attr:
        raise SystemExit("hdkit.signoff: %r is not <module>:<attribute>" % spec)
    return getattr(importlib.import_module(mod), attr)


def get(settings: dict, platform: str | None = None) -> Backend:
    """The backend the settings name (see the module text)."""
    from . import config
    spec = settings.get("backend") or "auto"
    if ":" in spec:
        obj = _load(spec)
        b = obj(settings)
        if not hasattr(b, "run"):
            raise SystemExit("hdkit.signoff: backend %s has no run()" % spec)
        return b
    return BUILTIN[config.backend(settings, platform)](settings)


# ---- seats -------------------------------------------------------------------------------------------------------------

class NoSeats:
    """No budget: every run may start."""
    name = "none"

    def acquire(self, req: RunRequest, wait_s: float = 0, log=print) -> bool:
        return True

    def release(self, req: RunRequest) -> None:
        return None


class TestingSeats:
    """The toolkit's seat budget (testing/game_slots.py): one machine-wide seat, held for the run."""
    name = "testing"

    def __init__(self, settings: dict):
        self.settings = settings
        self._cfg = None

    def cfg(self):
        if self._cfg is None:
            import sys
            root = Path(__file__).resolve().parents[2]
            if str(root / "testing") not in sys.path:
                sys.path.insert(0, str(root / "testing"))
            import testing_config as tc
            self._cfg = tc.load(self.settings.get("seats_config") or None)
        return self._cfg

    def _slots(self):
        import sys
        self.cfg()
        return sys.modules.get("game_slots") or importlib.import_module("game_slots")

    def acquire(self, req: RunRequest, wait_s: float = 0, log=print, poll_s: float = 5.0, sleep=time.sleep) -> bool:
        """One machine-wide seat for the run's slot (no budget.json: nothing to take). The slot lock stays the
        backend's: a backend that locks the slot itself would refuse a slot this hook had locked. While it waits, the
        hook publishes game.want.<project> so projects that yield make room."""
        import os
        gs = self._slots()
        cfg = self.cfg()
        deadline = time.monotonic() + max(wait_s, float(cfg["seats"].get("wait") or 0))
        published = False
        try:
            while True:
                k, msg = gs.take_seat(cfg, req.slot, req.holder, os.getpid())
                if k is not None:
                    log("seat: %s" % (msg if k else "no seat budget on this machine"))
                    return True
                if not published and gs.budget(cfg):
                    published = bool(gs.publish_want(cfg, 1))
                left = deadline - time.monotonic()
                if left <= 0:
                    log("seat: %s" % msg)
                    return False
                log("seat: %s; waiting (about %d s left)" % (msg, int(left)))
                sleep(min(poll_s, max(left, 0.0)))
        finally:
            if published:
                gs.withdraw_want(cfg)

    def release(self, req: RunRequest) -> None:
        self._slots().release_seat(self.cfg(), req.slot, req.holder)


def seat_hook(settings: dict):
    spec = settings.get("seats") or "none"
    if spec == "none":
        return NoSeats()
    if spec == "testing":
        return TestingSeats(settings)
    obj = _load(spec)
    return obj(settings)


def run(settings: dict, req: RunRequest, wait_s: float | None = None, log=print, platform: str | None = None) -> int:
    """One sign-off game run: the seat first (waiting up to settings `seat_wait` s), then the backend, then the seat back
    (also when the run fails). Returns the backend's exit code, or 5 when no seat came free in time."""
    hook = seat_hook(settings)
    wait = float(settings.get("seat_wait") or 0) if wait_s is None else wait_s
    t0 = time.time()
    if not hook.acquire(req, wait, log):
        log("no seat for slot %d after %d s: no run" % (req.slot, time.time() - t0))
        return 5
    try:
        return get(settings, platform).run(req, log=log)
    finally:
        hook.release(req)
