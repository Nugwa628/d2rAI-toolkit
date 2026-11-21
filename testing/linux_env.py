#!/usr/bin/env python
"""Helpers for running D2R test games on Linux under Proton / Wine. See docs/linux.md.

Pure functions plus a small CLI. Nothing here starts a game: it builds the environment, edits the files a slot's
prefix reads at launch, and reads the machine's load so a job can wait for a quiet GPU.

  python testing/linux_env.py env [--vram-mb N]                 environment lines for a game on a private display
  python testing/linux_env.py free-display [--start 100]        the first free X display number (:100 up)
  python testing/linux_env.py nofocus PREFIX/user.reg [--dry-run]   Wine X11 settings that keep windows from grabbing focus
  python testing/linux_env.py profile SETTINGS.json test|visual [--dry-run]   write a graphics profile's keys
  python testing/linux_env.py gate [--gpu-max 20] [--load-max 60]  GPU busy %, CPU load %, and whether to wait

Exit codes: 0 ok (gate: quiet), 1 gate: busy, 2 bad usage.
Only edit a prefix's files while nothing runs in that prefix: a running wineserver writes its own copy back.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(HERE, "profiles")

# ---------------------------------------------------------------- the private display
#
# Run every test game on its own Xvfb display, never on the desktop: under Wine the loader's first dialog and the game
# window take the keyboard focus whatever the prefix's settings say. Xvfb has no DRI3, so Mesa's Vulkan driver must
# present by CPU copy (MESA_VK_WSI_DEBUG=sw). vkd3d-proton paces frames with VK_KHR_present_wait, and those waits never
# end on Xvfb: with "sw" alone the frame stays black and the game's UI thread stalls. Hide the extension.
PRIVATE_DISPLAY_ENV = {
    "MESA_VK_WSI_DEBUG": "sw",
    "VKD3D_DISABLE_EXTENSIONS": "VK_KHR_present_wait,VK_KHR_present_wait2",
}


def free_display(start: int = 100, xdir: str = "/tmp/.X11-unix", lockdir: str = "/tmp") -> int:
    """The first display number >= start with no X socket and no lock file."""
    n = start
    while os.path.exists(os.path.join(xdir, f"X{n}")) or os.path.exists(os.path.join(lockdir, f".X{n}-lock")):
        n += 1
    return n


def xvfb_command(display: int, width: int = 1920, height: int = 1080) -> list[str]:
    """Xvfb for one game. The screen must be bigger than the game window plus its frame, or the bottom and right
    edges of the client are never drawn (shots and pixel checks lose the HUD)."""
    return ["Xvfb", f":{display}", "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp"]


def screen_for(client_w: int, client_h: int, minimum: tuple[int, int] = (1920, 1080)) -> tuple[int, int]:
    """A screen size that holds a client of this size placed at 0, 0 with its window frame."""
    return max(minimum[0], client_w + 64), max(minimum[1], client_h + 96)


# ---------------------------------------------------------------- VRAM cap (DXVK)
#
# Under Proton, D3D12 runs on vkd3d-proton with DXVK's dxgi.dll. dxgi.maxDeviceMemory (MB) lowers the adapter's
# DedicatedVideoMemory, and D2R sizes its streamed texture pool from that. It trims only that pool: a large part of a
# game's VRAM (UI and font atlases, shaders, the menu scene) is fixed and is in place before the world loads.
# QueryVideoMemoryInfo ignores every DXVK option, so the cap cannot be checked from inside the game that way; the
# game's DXVK log says "Found config env: dxgi.maxDeviceMemory = N".


def dxvk_config(vram_mb: int | None, existing: str | None = None) -> str | None:
    """DXVK_CONFIG with dxgi.maxDeviceMemory = vram_mb, other options of `existing` kept. vram_mb None or 0: no cap
    (existing returned without a maxDeviceMemory entry, or None when nothing is left)."""
    opts = [o.strip() for o in (existing or "").split(";") if o.strip()]
    opts = [o for o in opts if not o.replace(" ", "").startswith("dxgi.maxDeviceMemory=")]
    if vram_mb:
        opts.append(f"dxgi.maxDeviceMemory = {int(vram_mb)}")
    return "; ".join(opts) or None


def game_env(base: dict | None = None, vram_mb: int | None = None, display: int | None = None) -> dict:
    """The extra environment of one game launch: the private display settings, DISPLAY, and the VRAM cap."""
    base = os.environ if base is None else base
    env = dict(PRIVATE_DISPLAY_ENV)
    if display is not None:
        env["DISPLAY"] = f":{display}"
    cfg = dxvk_config(vram_mb, base.get("DXVK_CONFIG"))
    if cfg:
        env["DXVK_CONFIG"] = cfg
    return env


# ---------------------------------------------------------------- Wine registry: no focus grabs
#
# HKCU\Software\Wine\X11 Driver in the prefix's user.reg. These help, but on a real desktop they were not enough: the
# loader's dialog still took focus within seconds. Use them together with the private display, and measure the
# desktop's _NET_ACTIVE_WINDOW during every run rather than trusting them.
X11_DRIVER_KEY = "Software\\\\Wine\\\\X11 Driver"  # as user.reg spells it (backslashes doubled)
NO_FOCUS_VALUES = (("UseTakeFocus", "N"), ("GrabPointer", "N"), ("GrabFullscreen", "N"))


def reg_values(text: str, key: str) -> dict:
    """{name: data} of the string values in [key] of a user.reg text."""
    out, inside = {}, False
    for line in text.split("\n"):
        if line.startswith("["):
            inside = line.startswith(f"[{key}]")
        elif inside and line.startswith('"') and '"="' in line:
            k, v = line[1:].split('"="', 1)
            out[k] = v.rstrip('"')
    return out


def set_reg_values(text: str, key: str, values, stamp: int | None = None) -> str:
    """user.reg text with string `values` [(name, data)] set in [key]. A missing section is added at the end; other
    values and sections stay as they are."""
    lines = text.split("\n")
    head = f"[{key}]"
    start = next((i for i, ln in enumerate(lines) if ln == head or ln.startswith(head + " ")), None)
    if start is None:
        stamp = int(time.time()) if stamp is None else stamp
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", f"{head} {stamp}"] + [f'"{k}"="{v}"' for k, v in values] + [""]
        return "\n".join(lines)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    while end > start + 1 and lines[end - 1] == "":
        end -= 1
    for k, v in values:
        line = f'"{k}"="{v}"'
        at = next((i for i in range(start + 1, end) if lines[i].lower().startswith(f'"{k.lower()}"=')), None)
        if at is None:
            lines.insert(end, line)
            end += 1
        else:
            lines[at] = line
    return "\n".join(lines)


def apply_no_focus(user_reg: str, dry_run: bool = False) -> list:
    """Write NO_FOCUS_VALUES into a prefix's user.reg. Returns the values that were missing or different."""
    with open(user_reg, encoding="utf-8", newline="") as f:
        text = f.read()
    have = reg_values(text, X11_DRIVER_KEY)
    missing = [(k, v) for k, v in NO_FOCUS_VALUES if have.get(k) != v]
    if missing and not dry_run:
        tmp = user_reg + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(set_reg_values(text, X11_DRIVER_KEY, NO_FOCUS_VALUES))
        os.replace(tmp, user_reg)
    return missing


# ---------------------------------------------------------------- graphics profiles
#
# D2R keeps its graphics settings in Settings.json in the save folder. In a Wine prefix that folder is usually outside
# whatever a slot snapshot restores, so a setting changed in one run stays for the next. Write the profile before every
# launch. "test" (low, 30 fps cap, Resolution Scale 50) is for functional, UI and harness checks: UI text draws at full
# resolution whatever the scale. "visual" is for judging the 3D world's pixels (HD effects, models, item art).


def load_profile(name: str, folder: str = PROFILE_DIR) -> dict:
    path = os.path.join(folder, f"{name}.json")
    if not os.path.isfile(path):
        have = sorted(p[:-5] for p in os.listdir(folder) if p.endswith(".json"))
        raise ValueError(f"unknown graphics profile {name!r} (have: {', '.join(have)})")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def apply_profile(settings_path: str, profile: dict, dry_run: bool = False) -> dict:
    """Set the profile's keys in D2R's Settings.json, every other key and the key order kept, D2R's layout (4-space
    indent, no newline at the end). A missing file gets only these keys (D2R fills the rest). Returns
    {key: (old, new)} of what changed."""
    try:
        with open(settings_path, encoding="utf-8-sig") as f:
            cur = json.load(f, object_pairs_hook=collections.OrderedDict)
    except FileNotFoundError:
        cur = collections.OrderedDict()
    changed = {k: (cur.get(k), v) for k, v in profile.items() if cur.get(k) != v}
    if changed and not dry_run:
        cur.update(profile)
        os.makedirs(os.path.dirname(os.path.abspath(settings_path)), exist_ok=True)
        tmp = settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(cur, indent=4))
        os.replace(tmp, settings_path)
    return changed


# ---------------------------------------------------------------- GPU gate
#
# Before a GPU-heavy offline job (renders, bakes), wait until the GPU and CPU are quiet, so a running test game or the
# owner's desktop is not starved. amdgpu exposes gpu_busy_percent in sysfs; other drivers may not (then GPU is None).


def gpu_busy(drm: str = "/sys/class/drm", samples: int = 5, interval: float = 0.2, sleep=time.sleep) -> int | None:
    """The middle value of a few gpu_busy_percent readings of the busiest card, or None when no card exposes it."""
    files = sorted(glob.glob(os.path.join(drm, "card*", "device", "gpu_busy_percent")))
    if not files:
        return None
    best = None
    reads: dict[str, list[int]] = {f: [] for f in files}
    for i in range(samples):
        for f in files:
            try:
                with open(f) as fh:
                    reads[f].append(int(fh.read().strip()))
            except (OSError, ValueError):
                pass
        if i + 1 < samples:
            sleep(interval)
    for vals in reads.values():
        if vals:
            m = sorted(vals)[len(vals) // 2]
            best = m if best is None else max(best, m)
    return best


def cpu_load_percent(loadavg: str = "/proc/loadavg", cpus: int | None = None) -> float | None:
    """The one-minute load average as a percentage of the logical CPUs."""
    try:
        with open(loadavg) as f:
            load1 = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return 100.0 * load1 / max(1, cpus or os.cpu_count() or 1)


def vram_used_mb(drm: str = "/sys/class/drm") -> dict:
    """{card: (used MB, total MB)} from amdgpu's mem_info_vram_used / mem_info_vram_total."""
    out = {}
    for used in sorted(glob.glob(os.path.join(drm, "card*", "device", "mem_info_vram_used"))):
        dev = os.path.dirname(used)
        try:
            with open(used) as f:
                u = int(f.read())
            with open(os.path.join(dev, "mem_info_vram_total")) as f:
                t = int(f.read())
        except (OSError, ValueError):
            continue
        out[os.path.basename(os.path.dirname(dev))] = (u // 2**20, t // 2**20)
    return out


def gate(gpu: int | None, load: float | None, gpu_max: float = 20, load_max: float = 60) -> tuple[bool, str]:
    """(quiet, reason). Unknown readings never block."""
    why = []
    if gpu is not None and gpu >= gpu_max:
        why.append(f"GPU {gpu}% >= {gpu_max:g}%")
    if load is not None and load >= load_max:
        why.append(f"CPU load {load:.0f}% >= {load_max:g}%")
    return (not why), ("; ".join(why) or "quiet")


# ---------------------------------------------------------------- the window helper (testing/wctl)
#
# Window handles, Windows process ids and the plugin's named harness block live in the game's wineserver, so Linux
# tools cannot reach them. wctl.exe runs inside the slot's prefix on the game's private display and answers one
# TAB-separated request per line (see the header of testing/wctl/wctl.c). Keep one `wine wctl.exe serve` open per run.


class WctlError(Exception):
    pass


class Wctl:
    """A `wctl.exe serve` process. cmd: the full command, e.g. [proton, "run", wctl.exe, "serve"] or
    ["wine", wctl.exe, "serve"]; env: the prefix's environment (WINEPREFIX or STEAM_COMPAT_DATA_PATH, DISPLAY).
    A request without an answer in `timeout` seconds ends the helper with an error instead of hanging the run."""

    def __init__(self, cmd: list[str], env: dict | None = None, timeout: float = 20.0, popen=None, log=None):
        import subprocess
        self.timeout, self.log = timeout, log
        self.proc = (popen or subprocess.Popen)(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                                env=env, bufsize=1)
        first = self._line()
        if not first.startswith("ok wctl"):
            raise WctlError(f"wctl did not start: {first!r}")

    def _line(self) -> str:
        import threading
        box: list[str] = []
        t = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(self.timeout)
        if not box:
            self.close(hard=True)
            raise WctlError(f"wctl gave no answer in {self.timeout:g} s")
        if not box[0]:
            raise WctlError("wctl ended")
        return box[0].rstrip("\r\n")

    def request(self, *fields) -> tuple[str, list[str]]:
        """(the final "ok ..." line without "ok", the "- ..." list lines). An "err" answer raises WctlError."""
        line = "\t".join(str(f) for f in fields)
        if "\n" in line:
            raise WctlError("a request may not contain a line break")
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        items = []
        while True:
            ans = self._line()
            if ans.startswith("- "):
                items.append(ans[2:])
                continue
            if self.log and fields[0] not in ("ping", "rect", "vget", "find", "alive"):
                self.log(f"wctl: {line} -> {ans}")
            if ans.startswith("err"):
                raise WctlError(f"{fields[0]}: {ans[4:]}")
            return ans[3:], items

    def find(self, title: str, pid: int = 0):
        """(hwnd, pid, x11 id, client w, client h, iconic) of the visible top-level window titled `title`, or None."""
        try:
            ans, _ = self.request("find", title, pid)
        except WctlError:
            return None
        h, p, x, w, hh, ic = ans.split()
        return int(h), int(p), int(x, 16), int(w), int(hh), ic == "1"

    def post(self, hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> bool:
        return self.request("post", hwnd, msg, wparam, lparam)[0].strip() == "1"

    def close(self, hard: bool = False) -> None:
        try:
            if not hard and self.proc.poll() is None:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(5)
        except Exception:  # noqa: BLE001 - closing is best effort
            pass
        if self.proc.poll() is None:
            self.proc.kill()


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("env")
    p.add_argument("--vram-mb", type=int, default=0)
    p.add_argument("--display", type=int)
    p = sub.add_parser("free-display")
    p.add_argument("--start", type=int, default=100)
    p = sub.add_parser("nofocus")
    p.add_argument("user_reg")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("profile")
    p.add_argument("settings")
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("gate")
    p.add_argument("--gpu-max", type=float, default=20)
    p.add_argument("--load-max", type=float, default=60)
    a = ap.parse_args(argv)
    if a.cmd == "env":
        for k, v in game_env(vram_mb=a.vram_mb, display=a.display).items():
            print(f"{k}={v}")
        return 0
    if a.cmd == "free-display":
        print(free_display(a.start))
        return 0
    if a.cmd == "nofocus":
        missing = apply_no_focus(a.user_reg, a.dry_run)
        print("already set" if not missing else ("would set " if a.dry_run else "set ") +
              ", ".join(f"{k}={v}" for k, v in missing))
        return 0
    if a.cmd == "profile":
        try:
            changed = apply_profile(a.settings, load_profile(a.name), a.dry_run)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        for k, (old, new) in changed.items():
            print(f"{k}: {old!r} -> {new!r}")
        print(f"{len(changed)} keys {'would change' if a.dry_run else 'changed'}")
        return 0
    if a.cmd == "gate":
        g, load = gpu_busy(), cpu_load_percent()
        quiet, why = gate(g, load, a.gpu_max, a.load_max)
        print(f"gpu {'?' if g is None else g}% load {'?' if load is None else f'{load:.0f}'}% "
              f"vram {vram_used_mb() or '?'}: {why}")
        return 0 if quiet else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
