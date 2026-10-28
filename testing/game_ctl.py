#!/usr/bin/env python
"""Control a game slot without a real mouse or keyboard (and without taking focus).

  python testing/game_ctl.py --slot N status
  python testing/game_ctl.py --slot N start | stop
  python testing/game_ctl.py --slot N shot NAME            PNG of the game's drawing area
  python testing/game_ctl.py --slot N key esc enter ctrl+s  keys, as window messages
  python testing/game_ctl.py --slot N click 0.5 0.5 [right]  click at a fraction of the client area
  python testing/game_ctl.py --slot N console "some command"  a console line, run through the plugin harness
  python testing/game_ctl.py --slot N harness on|off       write / remove the plugin's harness word file

The slot comes from --slot or MODKIT_SLOT. There is no default: slot 1 is the user's own game and is refused.
MODKIT_RUN names the run (screenshots go to <runs.dir>/<run>/screenshots) and is the owner of the slot lock.

How it works: the game's process is found by PID (its image lives in the slot's folder), the window by that PID
(never by title alone), screenshots with PrintWindow (works behind other windows), keys and clicks with
PostMessage. The mouse position, button state and console commands go through the plugin's harness block in shared
memory ("The harness protocol" in docs/testing.md). Everything that needs Windows is behind functions, so this
module imports everywhere and says "needs Windows" instead of crashing.
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import game_slots  # noqa: E402
import testing_config as tc  # noqa: E402


class GameCtlError(Exception):
    """Something went wrong while controlling the game (message is meant to be shown as is)."""


class GameUnavailable(GameCtlError):
    """Not possible here: not Windows, or the game is not running, or the plugin harness is off."""


class WaitTimeout(GameCtlError):
    pass


def on_windows() -> bool:
    return os.name == "nt"


def need_windows(what: str = "this") -> None:
    if not on_windows():
        raise GameUnavailable(f"{what} needs Windows (the game and its window run only there)")


# ---------------------------------------------------------------- waiting


def wait_for(fn, timeout: float = 30.0, interval: float = 0.5, desc: str = "condition",
             sleep=time.sleep, clock=time.monotonic):
    """Call fn() until it returns something truthy; return that. Raises WaitTimeout after `timeout` seconds."""
    end = clock() + timeout
    while True:
        v = fn()
        if v:
            return v
        if clock() >= end:
            raise WaitTimeout(f"timed out after {timeout:g} s waiting for {desc}")
        sleep(interval)


def file_size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def read_from(p: str, offset: int = 0) -> str:
    """Text of file p from byte `offset` (0 again when the file was truncated since)."""
    try:
        if file_size(p) < offset:
            offset = 0
        with open(p, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def wait_log(path: str, needle: str, timeout: float = 90, offset: int = 0, sleep=time.sleep,
             clock=time.monotonic) -> bool:
    """True when `needle` shows up in the file after byte `offset` (False on timeout, never raises)."""
    try:
        wait_for(lambda: needle in read_from(path, offset), timeout, 1.0, f"{needle!r} in {path}", sleep, clock)
        return True
    except WaitTimeout:
        return False


# ---------------------------------------------------------------- images (pure Python)


class Image:
    """An RGB picture: width, height and w*h*3 bytes. Enough to save a PNG and compare screens."""

    def __init__(self, width: int, height: int, rgb: bytes):
        if len(rgb) != width * height * 3:
            raise ValueError("pixel data does not match the size")
        self.width, self.height, self.rgb = width, height, bytes(rgb)

    @classmethod
    def from_bgra(cls, width: int, height: int, bgra: bytes, crop: tuple[int, int, int, int] | None = None):
        """From a top-down BGRA buffer. crop = (left, top, right, bottom)."""
        l, t, r, b = crop or (0, 0, width, height)
        rows = []
        for y in range(t, b):
            row = bgra[(y * width + l) * 4:(y * width + r) * 4]
            out = bytearray(len(row) // 4 * 3)
            out[0::3], out[1::3], out[2::3] = row[2::4], row[1::4], row[0::4]
            rows.append(bytes(out))
        return cls(r - l, b - t, b"".join(rows))

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]

    def box(self, fx0: float, fy0: float, fx1: float, fy1: float) -> tuple[int, int, int, int]:
        return (int(fx0 * self.width), int(fy0 * self.height), max(int(fx1 * self.width), int(fx0 * self.width) + 1),
                max(int(fy1 * self.height), int(fy0 * self.height) + 1))

    def grey_values(self, box: tuple[int, int, int, int] | None = None) -> list[int]:
        l, t, r, b = box or (0, 0, self.width, self.height)
        out = []
        for y in range(t, b):
            base = (y * self.width) * 3
            seg = self.rgb[base + l * 3:base + r * 3]
            out.extend((seg[i] * 299 + seg[i + 1] * 587 + seg[i + 2] * 114) // 1000 for i in range(0, len(seg), 3))
        return out

    def stats(self, box: tuple[int, int, int, int] | None = None) -> tuple[float, float]:
        """(mean, standard deviation) of the grey level, 0..255."""
        v = self.grey_values(box)
        if not v:
            return 0.0, 0.0
        m = sum(v) / len(v)
        return m, (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5

    def signature(self, box: tuple[int, int, int, int] | None = None, cols: int = 12, rows: int = 16) -> bytes:
        """Grey cell averages (cols x rows) of a region: a small fingerprint of fixed screen art such as a menu
        panel. Compare two with signature_distance."""
        l, t, r, b = box or (0, 0, self.width, self.height)
        w, h = max(r - l, 1), max(b - t, 1)
        grey = self.grey_values((l, t, l + w, t + h))
        out = bytearray()
        for cy in range(rows):
            for cx in range(cols):
                x0, x1 = cx * w // cols, max((cx + 1) * w // cols, cx * w // cols + 1)
                y0, y1 = cy * h // rows, max((cy + 1) * h // rows, cy * h // rows + 1)
                cell = [grey[y * w + x] for y in range(y0, y1) for x in range(x0, x1)]
                out.append(sum(cell) // len(cell))
        return bytes(out)

    def png(self) -> bytes:
        def chunk(kind: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
        stride = self.width * 3
        raw = b"".join(b"\x00" + self.rgb[y * stride:(y + 1) * stride] for y in range(self.height))
        ihdr = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(self.png())
        return path


def signature_distance(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two signatures (0 = identical)."""
    return sum(abs(x - y) for x, y in zip(a, b)) / max(len(a), 1)


# ---------------------------------------------------------------- the plugin's harness block

MOD_BITS = {"shift": 1, "ctrl": 2, "alt": 4}
BUTTON_BITS = {"left": 1, "right": 2}


class Block:
    """An int32 array plus a byte area, as the plugin shares it. Subclasses: BytesBlock (memory, for tests),
    ShmBlock (the real shared memory)."""

    def get(self, i: int) -> int:
        raise NotImplementedError

    def set(self, i: int, v: int) -> None:
        raise NotImplementedError

    def write_bytes(self, offset: int, data: bytes) -> None:
        raise NotImplementedError


class BytesBlock(Block):
    def __init__(self, size: int = 576):
        self.buf = bytearray(size)

    def get(self, i: int) -> int:
        return struct.unpack_from("<i", self.buf, 4 * i)[0]

    def set(self, i: int, v: int) -> None:
        struct.pack_into("<i", self.buf, 4 * i, int(v))

    def write_bytes(self, offset: int, data: bytes) -> None:
        self.buf[offset:offset + len(data)] = data


class ShmBlock(Block):
    """A named shared-memory block opened with OpenFileMapping (Windows)."""

    def __init__(self, name: str, size: int):
        need_windows("the harness block")
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
        hm = k32.OpenFileMappingW(0xF001F, False, name)  # FILE_MAP_ALL_ACCESS
        if not hm:
            raise GameUnavailable(f"shared memory {name} not found (is the plugin's test harness on?)")
        p = k32.MapViewOfFile(hm, 0xF001F, 0, 0, size)
        if not p:
            raise GameUnavailable(f"cannot map shared memory {name}")
        self._ctypes, self.name, self.addr, self.size = ctypes, name, p, size
        self.ints = (ctypes.c_int32 * (size // 4)).from_address(p)

    def get(self, i: int) -> int:
        return self.ints[i]

    def set(self, i: int, v: int) -> None:
        self.ints[i] = v

    def write_bytes(self, offset: int, data: bytes) -> None:
        self._ctypes.memmove(self.addr + offset, data, len(data))


class Harness:
    """The test harness protocol over a Block (see docs/testing.md). All indexes and sizes come from the config."""

    def __init__(self, cfg: tc.Config, block: Block):
        self.h = cfg["harness"]
        self.block = block

    def idx(self, name: str) -> int:
        return self.h["idx_" + name]

    def get(self, name: str) -> int:
        return self.block.get(self.idx(name))

    def set(self, name: str, v: int) -> None:
        self.block.set(self.idx(name), v)

    def magic_ok(self) -> bool:
        return self.get("magic") == self.h["magic"]

    def activate(self) -> None:
        """From now on the game reads the virtual cursor, buttons and modifiers from the block."""
        if not self.get("active"):
            self.set("flags", self.h["flag_background"])
            self.set("x", self.h["cursor_start"][0])
            self.set("y", self.h["cursor_start"][1])
            self.set("active", 1)

    def set_mods(self, mods) -> None:
        self.set("mods", sum(MOD_BITS[m] for m in mods))

    def move(self, x: int, y: int) -> None:
        self.set("x", x)
        self.set("y", y)

    def buttons(self, bits: int) -> None:
        self.set("buttons", bits)

    def run_command(self, text: str, timeout: float = 10.0, sleep=time.sleep, clock=time.monotonic
                    ) -> tuple[bool, int]:
        """Put `text` in the command buffer, bump the command seq, wait for the ack. Returns (answered, result);
        result 1 = the console handled the line. (False, 0) when the plugin did not answer in time."""
        data = text.encode("utf-8")[:self.h["command_max"]] + b"\0"
        self.block.write_bytes(self.h["command_offset"], data)
        seq = (self.get("cmd_seq") + 1) & 0x7FFFFFFF
        self.set("cmd_seq", seq)
        end = clock() + timeout
        while clock() < end:
            if self.get("cmd_ack") == seq:
                return True, self.get("cmd_result")
            sleep(0.05)
        return False, 0


# ---------------------------------------------------------------- Windows calls (all inside functions)

VK = {"esc": 0x1B, "enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08, "ctrl": 0x11, "shift": 0x10,
      "alt": 0x12, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "`": 0xC0, "-": 0xBD, "=": 0xBB,
      ".": 0xBE, ",": 0xBC, "/": 0xBF, ";": 0xBA, "'": 0xDE}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})
EXTENDED = {"left", "up", "right", "down"}
WM_KEYDOWN, WM_KEYUP, WM_CHAR, WM_CLOSE = 0x100, 0x101, 0x102, 0x10
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP = 0x200, 0x201, 0x202, 0x204, 0x205
_dpi_set = False


def _user32():
    need_windows("window control")
    import ctypes
    global _dpi_set
    u = ctypes.windll.user32
    if not _dpi_set:
        _dpi_set = True
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            u.SetProcessDPIAware()
    return u


def key_lparam(vk: int, extended: bool, up: bool, scan: int = 0) -> int:
    lp = 1 | (scan << 16) | ((1 if extended else 0) << 24)
    return lp | (1 << 30) | (1 << 31) if up else lp


def find_windows(pids: set[int], title_prefix: str = "") -> list[int]:
    """Visible, unowned top-level windows of these PIDs, biggest first. The title is only an extra filter."""
    if not pids:
        return []
    import ctypes
    import ctypes.wintypes as wt
    u = _user32()
    found: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not u.IsWindowVisible(h) or u.GetWindow(h, 4):  # GW_OWNER
            return True
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        if title_prefix:
            n = u.GetWindowTextLengthW(h)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            if not buf.value.startswith(title_prefix):
                return True
        r = wt.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        found.append(((r.right - r.left) * (r.bottom - r.top), h))
        return True

    u.EnumWindows(cb, 0)
    return [h for _, h in sorted(found, reverse=True)]


def client_size(hwnd: int) -> tuple[int, int]:
    import ctypes
    import ctypes.wintypes as wt
    r = wt.RECT()
    _user32().GetClientRect(hwnd, ctypes.byref(r))
    return r.right, r.bottom


def grab_window(hwnd: int) -> Image:
    """Client area of a window via PrintWindow(PW_RENDERFULLCONTENT): no focus, no desktop capture."""
    import ctypes
    import ctypes.wintypes as wt
    u = _user32()
    gdi = ctypes.windll.gdi32
    if u.IsIconic(hwnd):
        restore_noactivate(hwnd)
    wr, cr, pt = wt.RECT(), wt.RECT(), wt.POINT(0, 0)
    u.GetWindowRect(hwnd, ctypes.byref(wr))
    u.GetClientRect(hwnd, ctypes.byref(cr))
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    W, H = wr.right - wr.left, wr.bottom - wr.top
    ox, oy = pt.x - wr.left, pt.y - wr.top
    if W <= 0 or H <= 0 or cr.right <= 0 or u.IsIconic(hwnd):
        raise GameCtlError("the game window is minimized or has no size: cannot capture it")

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    gdi.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    gdi.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi.DeleteDC.argtypes = [ctypes.c_void_p]
    u.GetDC.restype = ctypes.c_void_p
    u.GetDC.argtypes = [ctypes.c_void_p]
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    dc = u.GetDC(hwnd)
    mdc = gdi.CreateCompatibleDC(ctypes.c_void_p(dc))
    bmp = gdi.CreateCompatibleBitmap(ctypes.c_void_p(dc), W, H)
    gdi.SelectObject(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp))
    try:
        u.PrintWindow(hwnd, ctypes.c_void_p(mdc), 2)
        bi = BIH(40, W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(W * H * 4)
        gdi.GetDIBits(ctypes.c_void_p(mdc), ctypes.c_void_p(bmp), 0, H, buf, ctypes.byref(bi), 0)
    finally:
        gdi.DeleteObject(ctypes.c_void_p(bmp))
        gdi.DeleteDC(ctypes.c_void_p(mdc))
        u.ReleaseDC(hwnd, ctypes.c_void_p(dc))
    return Image.from_bgra(W, H, buf.raw, (ox, oy, ox + cr.right, oy + cr.bottom))


def restore_noactivate(hwnd: int) -> None:
    """Un-minimize without activating, then send the window to the bottom of the z-order."""
    u = _user32()
    u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    u.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_BOTTOM, NOSIZE | NOMOVE | NOACTIVATE
    time.sleep(1.0)


def send_to_back(hwnd: int) -> None:
    _user32().SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)


def fit_window(hwnd: int, client_w: int, client_h: int, x: int, y: int) -> tuple[int, int]:
    """Resize so the client area is client_w x client_h, at (x, y), without activating."""
    import ctypes
    import ctypes.wintypes as wt
    u = _user32()
    flags = 0x0004 | 0x0010  # SWP_NOZORDER | SWP_NOACTIVATE
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 4)
    u.SetWindowPos(hwnd, 0, x, y, client_w + 40, client_h + 80, flags)
    time.sleep(0.5)
    wr, cr = wt.RECT(), wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(wr))
    u.GetClientRect(hwnd, ctypes.byref(cr))
    dw, dh = (wr.right - wr.left) - cr.right, (wr.bottom - wr.top) - cr.bottom
    u.SetWindowPos(hwnd, 0, x, y, client_w + dw, client_h + dh, flags)
    time.sleep(0.8)
    return client_size(hwnd)


def post(hwnd: int, msg: int, wparam: int, lparam: int) -> None:
    _user32().PostMessageW(hwnd, msg, wparam, lparam)


def map_vk(vk: int) -> int:
    return _user32().MapVirtualKeyW(vk, 0)


# ---------------------------------------------------------------- the game of one slot


class Game:
    """One slot's game. Refuses slot 1. Every method works on this slot's processes and window only."""

    def __init__(self, cfg: tc.Config, slot: int, owner: str | None = None, block_factory=None, log=print):
        self.cfg = cfg
        self.slot = cfg.check_slot_number(slot)
        if cfg.is_slot_one(self.slot):
            raise GameCtlError(game_slots.SLOT1_MSG)
        self.owner = owner or tc.env_run() or f"run-{os.getpid()}"
        self.block_factory = block_factory
        self.log = log
        self.took_lock = False
        self._harness: Harness | None = None
        self._harness_pid: int | None = None

    # ------------------------------------------------------------ process and window

    def pids(self) -> list[int]:
        return [pid for pid, _ in game_slots.slot_processes(self.cfg, self.slot)]

    def is_running(self) -> bool:
        return bool(self.pids())

    def window(self) -> int | None:
        need_windows("finding the game window")
        ws = find_windows(set(self.pids()), self.cfg["game"]["window_title_prefix"])
        return ws[0] if ws else None

    def need_window(self, wait: float = 20.0) -> int:
        if not on_windows():
            raise GameUnavailable("the game window needs Windows")
        if not self.is_running():
            raise GameUnavailable(f"the game of slot {self.slot} is not running (game_ctl start)")
        return wait_for(self.window, wait, 0.5, f"the game window of slot {self.slot}")

    # ------------------------------------------------------------ screenshots, input

    def screenshot(self) -> Image:
        return grab_window(self.need_window())

    def shot(self, path: str) -> str:
        if not path.lower().endswith(".png"):
            path += ".png"
        return self.screenshot().save(path)

    def harness(self) -> Harness | None:
        """The plugin's harness for the current game process, or None when it is off or not found."""
        pids = self.pids()
        if not pids:
            self._harness = self._harness_pid = None
            return None
        if self._harness is not None and self._harness_pid in pids:
            return self._harness
        cfg, size = self.cfg, self.cfg["harness"]["block_bytes"]
        names = [cfg.shm_name(p) for p in pids] + [cfg.shm_name()]
        for pid, name in zip(pids + [pids[0]], names):
            if not name:
                continue
            try:
                block = self.block_factory(name, size) if self.block_factory else ShmBlock(name, size)
            except GameUnavailable:
                if not on_windows():
                    raise
                continue
            hn = Harness(cfg, block)
            if hn.magic_ok():
                hn.activate()
                self._harness, self._harness_pid = hn, pid
                return hn
        return None

    def need_harness(self) -> Harness:
        h = self.harness()
        if h is None:
            raise GameUnavailable(f"the plugin harness of slot {self.slot} is not reachable "
                                  f"(is {self.cfg['harness']['word_file']} next to the plugin?)")
        return h

    def key(self, spec: str, hold: float = 0.06) -> None:
        """A key press as window messages. spec: 'esc', 'i', 'f5', 'ctrl+s'. Modifier state is also given to the
        harness block, because the game asks the keyboard state, not the message."""
        *mods, name = [p.lower() for p in spec.split("+")] if len(spec) > 1 else [spec]
        if name not in VK or any(m not in MOD_BITS for m in mods):
            raise GameCtlError(f"unknown key {spec!r}")
        hwnd = self.need_window()
        hn = self.harness()
        if hn:
            hn.set_mods(mods)
        for m in mods:
            post(hwnd, WM_KEYDOWN, VK[m], key_lparam(VK[m], False, False, map_vk(VK[m])))
        time.sleep(0.03)
        vk, ext = VK[name], name in EXTENDED
        post(hwnd, WM_KEYDOWN, vk, key_lparam(vk, ext, False, map_vk(vk)))
        time.sleep(hold)
        post(hwnd, WM_KEYUP, vk, key_lparam(vk, ext, True, map_vk(vk)))
        for m in reversed(mods):
            post(hwnd, WM_KEYUP, VK[m], key_lparam(VK[m], False, True, map_vk(VK[m])))
        if hn:
            hn.set_mods(())
        time.sleep(0.15)

    def type_text(self, text: str) -> None:
        hwnd = self.need_window()
        for ch in text:
            post(hwnd, WM_CHAR, ord(ch), 1)
            time.sleep(0.03)

    def click(self, fx: float, fy: float, button: str = "left", move_only: bool = False) -> None:
        """Click at a fraction (0..1) of the client area. The game reads the position and buttons from the harness
        block, so the real cursor never moves; window messages are posted as well."""
        hwnd = self.need_window()
        hn = self.need_harness()
        w, h = client_size(hwnd)
        x, y = int(fx * w), int(fy * h)

        def lp(a, b):
            return ((b & 0xFFFF) << 16) | (a & 0xFFFF)

        hn.move(x - 4, y - 4)  # a click that arrives without a hover first is ignored by most buttons
        post(hwnd, WM_MOUSEMOVE, 0, lp(x - 4, y - 4))
        time.sleep(0.2)
        hn.move(x, y)
        post(hwnd, WM_MOUSEMOVE, 0, lp(x, y))
        time.sleep(0.4)
        if move_only:
            return
        down, up = (WM_LBUTTONDOWN, WM_LBUTTONUP) if button == "left" else (WM_RBUTTONDOWN, WM_RBUTTONUP)
        bit = BUTTON_BITS[button]
        hn.buttons(bit)
        post(hwnd, down, bit, lp(x, y))
        time.sleep(0.08)
        hn.buttons(0)
        post(hwnd, up, 0, lp(x, y))
        time.sleep(0.4)

    # ------------------------------------------------------------ console

    def console(self, cmd: str, timeout: float = 10.0) -> bool:
        """Run a console line through the harness. True when the console handled it. Raises GameUnavailable when
        the harness is not reachable; returns False when it did not answer in time."""
        answered, result = self.need_harness().run_command(cmd, timeout)
        self.log(f"console {cmd!r} -> " + (str(result) if answered else f"no answer in {timeout:g} s"))
        return answered and result == 1

    def console_mark(self) -> int:
        return file_size(self.cfg.console_file(self.slot))

    def console_new(self, mark: int) -> str:
        return read_from(self.cfg.console_file(self.slot), mark)

    def console_text(self, cmd: str, settle: float = 0.6) -> str:
        """console() plus the lines the plugin appended to the console output file for it."""
        mark = self.console_mark()
        self.console(cmd)
        time.sleep(settle)
        return self.console_new(mark)

    def wait_console(self, needle: str, mark: int, timeout: float = 15.0) -> str:
        """Wait until `needle` is in the console output written after `mark`; returns that new text."""
        def seen() -> str:
            t = self.console_new(mark)
            return t if needle in t else ""
        return wait_for(seen, timeout, 0.2, f"{needle!r} in the console output")

    # ------------------------------------------------------------ logs

    def log_mark(self) -> int:
        return file_size(self.cfg.loader_log(self.slot))

    def new_crash_files(self, before: list[str]) -> list[str]:
        d = self.cfg.crash_dir(self.slot)
        now = sorted(os.listdir(d)) if os.path.isdir(d) else []
        return [c for c in now if c not in before]

    def crash_files(self) -> list[str]:
        d = self.cfg.crash_dir(self.slot)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    # ------------------------------------------------------------ harness word file

    def harness_switch(self, on: bool) -> None:
        """Create / remove the harness word file next to the plugin (the plugin reads it when it loads)."""
        p = self.cfg.harness_word_file(self.slot)
        if on:
            if os.path.isdir(os.path.dirname(p)):
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(" ".join(self.cfg.harness_words(self.slot)))
        elif os.path.exists(p):
            os.remove(p)

    # ------------------------------------------------------------ start / stop

    def start(self, timeout: float | None = None) -> bool:
        """Lock the slot (unless this owner holds it: then a manual lock, pid 0, that only `unlock` removes), launch the loader, wait for the startup line and the
        window. Raises GameCtlError when something is missing; never starts slot 1."""
        need_windows("starting the game")
        timeout = timeout or self.cfg["checks"]["start_timeout"]
        ok, msg, self.took_lock = game_slots.ensure_lock(self.cfg, self.slot, self.owner)
        self.log(msg)
        if not ok:
            raise GameCtlError(msg)
        if not self.is_running():
            loader = self.cfg.loader_path(self.slot)
            if not os.path.isfile(loader):
                loader = self.cfg.base_loader_path(self.slot)
            if not os.path.isfile(loader):
                raise GameCtlError(f"no loader at {loader}: run game_slots.py setup {self.slot}")
            off = self.log_mark()
            self.harness_switch(True)
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 4  # SW_SHOWNOACTIVATE
            subprocess.Popen([loader], cwd=self.cfg.slot_dir(self.slot), startupinfo=si)
            self.log(f"launched {os.path.basename(loader)} in slot {self.slot}")
            line = self.cfg["logs"]["startup_line"]
            if line and not wait_log(self.cfg.loader_log(self.slot), line, timeout, off):
                raise GameCtlError(f"timed out after {timeout:g} s waiting for {line!r} in the loader log")
        hwnd = wait_for(self.window, 60, 1.0, "the game window")
        send_to_back(hwnd)
        g = self.cfg["game"]
        ox, oy = g["window_origin"]
        dx, dy = g["window_step"]
        fit_window(hwnd, g["client_size"][0], g["client_size"][1], ox + dx * (self.slot - 1), oy + dy * (self.slot - 1))
        return True

    def stop(self, timeout: float = 20.0) -> None:
        """Close the game of this slot (WM_CLOSE, then kill only the slot's own PIDs), remove the harness word
        file and release the lock this owner holds."""
        if self.is_running():
            if on_windows():
                hwnd = self.window()
                if hwnd:
                    post(hwnd, WM_CLOSE, 0, 0)
            end = time.monotonic() + timeout
            while time.monotonic() < end and self.is_running():
                time.sleep(1)
            for pid in self.pids():
                self.log(f"killing pid {pid} (slot {self.slot})")
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            time.sleep(1)
        self._harness = self._harness_pid = None
        self.harness_switch(False)
        game_slots.release(self.cfg, self.slot, self.owner)


# ---------------------------------------------------------------- cli


def run_screens_dir(cfg: tc.Config) -> str:
    run = tc.env_run() or "adhoc"
    return os.path.join(cfg.run_dir(run), cfg["runs"]["screenshots_dir"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tc.add_config_arg(ap)
    ap.add_argument("--slot", type=int, help="slot number (or MODKIT_SLOT); slot 1 is refused")
    ap.add_argument("cmd", nargs="?", choices=("status", "start", "stop", "shot", "key", "click", "console", "harness"))
    ap.add_argument("args", nargs="*")
    a = ap.parse_args(argv)
    if not a.cmd:
        print(__doc__)
        return 0
    try:
        cfg = tc.load(a.config)
        slot = a.slot or tc.env_slot()
        if slot is None:
            raise GameCtlError("give --slot N or set MODKIT_SLOT (there is no default: slot 1 is the user's own game)")
        g = Game(cfg, slot)
        return _run(g, a)
    except (tc.ConfigError, GameCtlError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run(g: Game, a) -> int:
    c, args = a.cmd, a.args
    if c == "status":
        print(f"slot {g.slot}: {g.cfg.slot_dir(g.slot)}; running pids {g.pids() or '-'}; "
              f"lock {(game_slots.lock_info(g.cfg, g.slot) or game_slots.LockInfo('', 'free')).describe()}")
    elif c == "start":
        g.start()
        print(f"slot {g.slot} is up (pids {g.pids()})")
    elif c == "stop":
        g.stop()
        print(f"slot {g.slot} stopped")
    elif c == "shot":
        name = args[0] if args else "shot"
        print(g.shot(os.path.join(run_screens_dir(g.cfg), name)))
    elif c == "key":
        for k in args:
            g.key(k)
    elif c == "click":
        if len(args) < 2:
            raise GameCtlError("click needs FX FY (fractions of the client area)")
        g.click(float(args[0]), float(args[1]), args[2] if len(args) > 2 else "left")
    elif c == "console":
        text = g.console_text(" ".join(args))
        print(text.rstrip() or "(no output captured)")
    elif c == "harness":
        g.harness_switch((args[:1] or ["on"])[0] == "on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
