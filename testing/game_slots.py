#!/usr/bin/env python
"""Game slots: separate copies of the game install, one per test run, with a queue lock per slot.

Slot 1 is YOUR OWN game (game.install_dir). These tools never write to it, never start it and never lock it.
Slots 2..game.slot_count are copies in game.slots_dir. See docs/testing.md.

  python testing/game_slots.py [--config F] status
  python testing/game_slots.py list
  python testing/game_slots.py setup [N ...] [--link] [--dry-run]      (alias: sync)
  python testing/game_slots.py lock N|any [OWNER] [--wait SECONDS]
  python testing/game_slots.py unlock N [OWNER] [--force]

Environment: MODKIT_RUN is the default OWNER, MODKIT_SLOT the default N. A lock is a folder
<runs.dir>/game.lock.N holding owner.txt: `<owner> pid=<pid> <time>`. A lock whose pid is dead is stale and is
replaced. pid=0 marks a manual lock (made by `lock`): it lasts until `unlock`.

Exit codes: 0 ok, 1 the lock is held by somebody else, 2 bad usage / config / refused (slot 1).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testing_config as tc  # noqa: E402

SLOT1_MSG = ("slot 1 is the user's own game: these tools never write to it, start it or lock it "
             "(use a slot from 2 to game.slot_count)")


class SlotError(Exception):
    """A refused or impossible slot operation."""


# ---------------------------------------------------------------- processes (Windows only; elsewhere: none)


def pid_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wt.DWORD()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def processes(names: tuple[str, ...] | None = None) -> list[tuple[int, str, str]]:
    """[(pid, exe name, full image path)] of processes with these image names (lower case). Windows only: on
    other systems the list is empty."""
    if os.name != "nt":
        return []
    import ctypes
    import ctypes.wintypes as wt

    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)]

    def image_path(pid: int) -> str:
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wt.DWORD(1024)
            k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
            return buf.value
        finally:
            k32.CloseHandle(h)

    out = []
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    pe = PE()
    pe.dwSize = ctypes.sizeof(PE)
    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if names is None or pe.szExeFile.lower() in names:
            out.append((pe.th32ProcessID, pe.szExeFile, image_path(pe.th32ProcessID)))
        ok = k32.Process32NextW(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return out


def under(path: str, folder: str) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    f = os.path.normcase(os.path.abspath(folder)).rstrip("\\/")
    return p.startswith(f + os.sep)


def slot_processes(cfg: tc.Config, n: int) -> list[tuple[int, str]]:
    """(pid, name) of the loader / game processes whose image lives in slot N's folder. Never matches by name
    alone: another slot's game has the same image name."""
    game = cfg.slot_dir(n)
    return [(pid, nm) for pid, nm, path in processes(cfg.process_names(n)) if path and under(path, game)]


# ---------------------------------------------------------------- owner text and lock files


def format_owner(owner: str, pid: int, when: datetime.datetime | None = None) -> str:
    return f"{owner} pid={pid} {(when or datetime.datetime.now()):%Y-%m-%d %H:%M:%S}"


def parse_owner(txt: str | None) -> tuple[str | None, int | None, str | None]:
    """`<owner> pid=<pid> <time>` -> (owner, pid, time). Missing parts are None."""
    parts = (txt or "").split()
    if not parts:
        return None, None, None
    owner, pid, rest = parts[0], None, []
    for p in parts[1:]:
        if pid is None and p.startswith("pid="):
            try:
                pid = int(p[4:])
                continue
            except ValueError:
                pass
        rest.append(p)
    return owner, pid, (" ".join(rest) or None)


def valid_owner(s: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", s or ""))


@dataclass
class LockInfo:
    path: str
    text: str | None  # None: the folder has no readable owner.txt (made by hand, or being written right now)

    @property
    def owner(self) -> str | None:
        return parse_owner(self.text)[0]

    @property
    def pid(self) -> int | None:
        return parse_owner(self.text)[1]

    @property
    def when(self) -> str | None:
        return parse_owner(self.text)[2]

    @property
    def manual(self) -> bool:
        return self.pid == 0

    def describe(self) -> str:
        return self.text if self.text else "someone (lock folder without owner.txt)"


def lock_info(cfg: tc.Config, n: int) -> LockInfo | None:
    """None when the slot is free."""
    p = cfg.lock_path(n)
    if not os.path.isdir(p):
        return None
    try:
        with open(os.path.join(p, "owner.txt"), encoding="utf-8") as f:
            txt = f.read().strip() or None
    except OSError:
        txt = None
    return LockInfo(p, txt)


def is_stale(info: LockInfo, alive=None) -> bool:
    """True when the owner's pid is known, is not 0 (manual) and no longer runs."""
    alive = alive or pid_alive
    return info.pid is not None and info.pid != 0 and not alive(info.pid)


def _remove_lock(path: str) -> None:
    """Rename first (atomic), then delete: two tools clearing the same stale lock cannot both win."""
    tmp = f"{path}.gone-{uuid.uuid4().hex[:8]}"
    try:
        os.rename(path, tmp)
    except OSError:
        return
    shutil.rmtree(tmp, ignore_errors=True)


def acquire(cfg: tc.Config, n: int, owner: str, pid: int | None = None, alive=None) -> tuple[bool, str]:
    """Take the lock of slot N. Returns (ok, message).

    Slot 1 is refused. A stale lock (its pid is dead) is replaced. pid=0 makes a manual lock that is never stale.
    Asking again with the same owner succeeds ("already ours")."""
    n = cfg.check_slot_number(n)
    if cfg.is_slot_one(n):
        return False, SLOT1_MSG
    if not valid_owner(owner):
        return False, f"bad owner name {owner!r}: use letters, digits, dot, dash, underscore"
    pid = os.getpid() if pid is None else pid
    info = lock_info(cfg, n)
    if info is not None:
        if info.owner == owner:
            return True, f"slot {n} already locked by {owner}"
        if info.text and is_stale(info, alive):
            _remove_lock(info.path)
        else:
            return False, f"slot {n} held by {info.describe()}"
    os.makedirs(cfg.runs_dir, exist_ok=True)
    path = cfg.lock_path(n)
    try:
        os.mkdir(path)
    except FileExistsError:
        return False, f"slot {n} was taken just now by someone else"
    with open(os.path.join(path, "owner.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(format_owner(owner, pid))
    return True, f"slot {n} locked by {owner}"


def release(cfg: tc.Config, n: int, owner: str | None = None, manual: bool = False, force: bool = False
            ) -> tuple[bool, str]:
    """Remove the lock of slot N.

    owner given: only a lock of that owner goes. A manual (pid=0) lock stays unless manual=True: a lane holds
    its slot for a whole session and the runs inside it must not free it. `unlock` passes manual=True.
    owner None: needs force=True (clears whatever is there, for stale locks of finished lanes)."""
    n = cfg.check_slot_number(n)
    if cfg.is_slot_one(n):
        return False, SLOT1_MSG
    info = lock_info(cfg, n)
    if info is None:
        return True, f"slot {n} is free"
    if owner is None:
        if not force:
            return False, "give the OWNER of the lock (or --force to clear any lock)"
    elif info.owner != owner:
        return False, f"slot {n} is held by {info.describe()}, not by {owner}"
    elif info.manual and not manual:
        return True, f"slot {n}: session lock of {owner} kept until `unlock`"
    _remove_lock(info.path)
    return True, f"slot {n} unlocked"


def ensure_lock(cfg: tc.Config, n: int, owner: str, alive=None) -> tuple[bool, str, bool]:
    """For tools that run a game: (ok, message, taken). taken is True when this call made the lock.
    Without a lock the call takes a manual (pid=0) session lock: a short-lived command must not leave a lock that
    looks stale the moment the command ends. Only `unlock` removes it, so a game started this way keeps its slot."""
    info = lock_info(cfg, n)
    if info is not None and info.owner == owner:
        return True, f"slot {n} locked by {owner}", False
    ok, msg = acquire(cfg, n, owner, pid=0, alive=alive)
    if ok:
        msg += f" (manual lock: run `unlock {n}` when you are done)"
    return ok, msg, ok


def acquire_wait(cfg: tc.Config, n: int | str, owner: str, wait_s: float = 0, poll_s: float = 5.0,
                 sleep=time.sleep, clock=time.monotonic, out=print, pid: int | None = None
                 ) -> tuple[bool, str, int | None]:
    """Like acquire, but queues: while the slot is held, say who holds it and poll until wait_s ran out.
    n may be "any": the first free slot from 2 to slot_count. Returns (ok, message, slot)."""
    deadline = clock() + wait_s
    while True:
        slots = list(range(2, cfg.slot_count + 1)) if n == "any" else [cfg.check_slot_number(n)]
        if not slots:
            return False, "there is no slot besides slot 1 (game.slot_count)", None
        msg = ""
        for s in slots:
            ok, msg = acquire(cfg, s, owner, pid=pid)
            if ok:
                return True, msg, s
            if cfg.is_slot_one(s):
                return False, msg, None
        left = deadline - clock()
        if left <= 0:
            return False, msg if n != "any" else "every slot is held:\n" + "\n".join(
                f"  slot {s}: {(lock_info(cfg, s) or LockInfo('', 'free')).describe()}" for s in slots), None
        out(f"{msg}; waiting (about {int(left)} s left)")
        sleep(min(poll_s, max(left, 0.0)))


# ---------------------------------------------------------------- slot setup


def is_within(path: str, folder: str) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    f = os.path.normcase(os.path.abspath(folder))
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def guard_writable(cfg: tc.Config, n: int) -> str:
    """The folder of slot N, after refusing slot 1 and any slot folder that touches the user's install."""
    n = cfg.check_slot_number(n)
    if cfg.is_slot_one(n):
        raise SlotError(SLOT1_MSG)
    dst = cfg.slot_dir(n)
    if is_within(dst, cfg.install_dir) or is_within(cfg.install_dir, dst):
        raise SlotError(f"slot {n} folder {dst} overlaps the user's install {cfg.install_dir}")
    return dst


def _needs_copy(src: str, dst: str) -> bool:
    try:
        a, b = os.stat(src), os.stat(dst)
    except OSError:
        return True
    return a.st_size != b.st_size or abs(a.st_mtime - b.st_mtime) > 2


def copy_install(cfg: tc.Config, n: int, link: bool | None = None, dry_run: bool = False, out=None) -> dict:
    """Copy (or sync) the user's install into slot N's folder. Never deletes, never writes into slot 1.
    Only changed files are copied. With hardlinks, files under game.link_dirs share their bytes with slot 1:
    they are read-only game data and nothing may edit them in place."""
    dst_root = guard_writable(cfg, n)
    src_root = cfg.install_dir
    if not os.path.isdir(src_root):
        raise SlotError(f"game.install_dir {src_root} does not exist")
    g = cfg["game"]
    use_link = (g["copy_mode"] == "hardlink") if link is None else link
    skip = {x.lower() for x in g["exclude"]}
    link_dirs = {x.lower() for x in g["link_dirs"]}
    if g["mods_policy"] == "skip":
        skip.add("mods")
    # the slot's own loader config is set up once and then belongs to the slot: a sync does not overwrite it
    keep = {os.path.normpath(cfg["loader"]["config_file"])}
    stats = {"copied": 0, "linked": 0, "same": 0, "bytes": 0}
    for root, dirs, files in os.walk(src_root):
        rel = os.path.relpath(root, src_root)
        top = "" if rel == "." else rel.split(os.sep)[0].lower()
        if rel == ".":
            dirs[:] = [d for d in dirs if d.lower() not in skip]
            files = [f for f in files if f.lower() not in skip]
        droot = dst_root if rel == "." else os.path.join(dst_root, rel)
        if not dry_run:
            os.makedirs(droot, exist_ok=True)
        for f in files:
            s, d = os.path.join(root, f), os.path.join(droot, f)
            if os.path.exists(d) and os.path.samefile(s, d):
                stats["same"] += 1
                continue
            if not _needs_copy(s, d) or (os.path.exists(d) and os.path.relpath(s, src_root) in keep):
                stats["same"] += 1
                continue
            hard = use_link and top in link_dirs
            if out:
                out(("link " if hard else "copy ") + os.path.relpath(s, src_root))
            if dry_run:
                stats["linked" if hard else "copied"] += 1
                continue
            if os.path.exists(d):
                os.chmod(d, 0o666)
                os.unlink(d)  # never write through a link into slot 1's bytes
            if hard:
                try:
                    os.link(s, d)
                    stats["linked"] += 1
                    continue
                except OSError:
                    pass  # another drive, or no link support: copy
            shutil.copy2(s, d)
            stats["copied"] += 1
            stats["bytes"] += os.path.getsize(d)
    return stats


def set_savepath(cfg: tc.Config, n: int) -> list[str]:
    """Point the mod's modinfo.json file(s) at this slot's own save folder. Only existing files are updated
    (other keys are kept); a missing file is made when its folder exists. Returns the files written."""
    guard_writable(cfg, n)
    written = []
    for target in cfg.modinfo_paths(n):
        if not os.path.isdir(os.path.dirname(target)):
            continue
        data = {}
        if os.path.isfile(target):
            try:
                with open(target, encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
        data.setdefault("name", cfg["mod"]["name"])
        data["savepath"] = cfg.save_name(n) + "/"
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        written.append(target)
    return written


def set_toml_value(text: str, section: str, key: str, value: str) -> str:
    """Return `text` with `key = value` set in [section]. A present or commented-out line for the key is replaced;
    otherwise the line goes right after the header, and a missing section is added at the end. `value` is TOML text."""
    lines = text.splitlines()
    head = f"[{section}]"
    start = next((i for i, ln in enumerate(lines) if ln.strip() == head), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n{head}\n{key} = {value}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    for i in range(start + 1, end):
        bare = lines[i].strip().lstrip("#").strip()
        if bare.startswith(key) and bare[len(key):].lstrip().startswith("="):
            lines[i] = f"{key} = {value}"
            break
    else:
        lines.insert(start + 1, f"{key} = {value}")
    return "\n".join(lines) + "\n"


def set_loader_value(cfg: tc.Config, n: int, key: str, value: str) -> bool:
    """Write key = "value" in the slot's own loader config (a copy, never slot 1's). False when there is none."""
    guard_writable(cfg, n)
    p = cfg.loader_config_path(n)
    if not os.path.isfile(p):
        return False
    with open(p, encoding="utf-8") as f:
        text = f.read()
    new = set_toml_value(text, cfg["loader"]["section"], key, json.dumps(value))
    if new != text:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    return True


def setup(cfg: tc.Config, n: int, link: bool | None = None, dry_run: bool = False, out=print) -> dict:
    """Make slot N ready: copy/sync the install, loader copy, savepath, default mod, save folder."""
    guard_writable(cfg, n)
    stats = copy_install(cfg, n, link, dry_run, out=None)
    res = {"slot": n, "dir": cfg.slot_dir(n), "files": stats, "saves": cfg.save_dir(n), "loader": cfg.loader_name(n)}
    if dry_run:
        return res
    base, alt = cfg.base_loader_path(n), cfg.loader_path(n)
    if os.path.isfile(base) and (not os.path.exists(alt) or os.path.getsize(alt) != os.path.getsize(base)):
        shutil.copy2(base, alt)  # a renamed loader: its process is told apart from the other slots'
    res["modinfo"] = set_savepath(cfg, n)
    if cfg["loader"]["set_default_mod"]:
        res["loader_config"] = set_loader_value(cfg, n, cfg["loader"]["default_mod_key"], cfg.mod_folder())
    os.makedirs(cfg.save_dir(n), exist_ok=True)
    return res


# ---------------------------------------------------------------- status


def slot_state(cfg: tc.Config, n: int) -> dict:
    try:
        game = cfg.slot_dir(n)
    except tc.ConfigError as e:
        return {"slot": n, "dir": "?", "present": False, "running": [], "lock": None, "error": str(e)}
    have = os.path.isfile(cfg.base_loader_path(n))
    lk = lock_info(cfg, n)
    sp = "?"
    for p in cfg.modinfo_paths(n):
        try:
            with open(p, encoding="utf-8-sig") as f:
                sp = json.load(f)["savepath"]
            break
        except (OSError, ValueError, KeyError):
            continue
    return {"slot": n, "dir": game, "present": have, "own": cfg.is_slot_one(n),
            "running": slot_processes(cfg, n) if have else [],
            "lock": None if lk is None else lk.describe(),
            "stale": bool(lk and lk.text and is_stale(lk)), "savepath": sp}


def format_status(st: dict) -> str:
    if st.get("error"):
        return f"slot {st['slot']}: {st['error']}"
    name = "slot 1 (your own game, never touched)" if st.get("own") else f"slot {st['slot']}"
    lock = "free" if st["lock"] is None else ("STALE " if st["stale"] else "") + f"locked: {st['lock']}"
    return (f"{name}: {st['dir']} {'ok' if st['present'] else 'MISSING (run setup)'}; "
            f"running {st['running'] or '-'}; {lock if not st.get('own') else 'not lockable'}; savepath {st['savepath']}")


# ---------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tc.add_config_arg(ap)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("list")
    for name in ("setup", "sync"):
        p = sub.add_parser(name)
        p.add_argument("slots", nargs="*", type=int)
        p.add_argument("--link", action="store_true", help="hard-link game.link_dirs instead of copying")
        p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("lock")
    p.add_argument("slot", nargs="?", help="slot number or 'any'")
    p.add_argument("owner", nargs="?")
    p.add_argument("--wait", type=float, default=0, help="queue: wait up to this many seconds")
    p = sub.add_parser("unlock")
    p.add_argument("slot", nargs="?")
    p.add_argument("owner", nargs="?")
    p.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        print(__doc__)
        return 0
    try:
        cfg = tc.load(a.config)
        return _run(cfg, a)
    except (tc.ConfigError, SlotError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _slot_arg(cfg: tc.Config, v) -> int | str:
    if v is None:
        v = tc.env_slot()
    if v is None:
        raise tc.ConfigError("give the slot number (or set MODKIT_SLOT)")
    return v if v == "any" else cfg.check_slot_number(v)


def _run(cfg: tc.Config, a) -> int:
    if a.cmd == "status":
        for n in range(1, cfg.slot_count + 1):
            print(format_status(slot_state(cfg, n)))
        return 0
    if a.cmd == "list":
        for n in range(1, cfg.slot_count + 1):
            st = slot_state(cfg, n)
            state = "own" if st.get("own") else ("free" if st["lock"] is None else "locked")
            print(f"{n}\t{state}\t{st['dir']}")
        return 0
    if a.cmd in ("setup", "sync"):
        rc = 0
        for n in a.slots or list(range(2, cfg.slot_count + 1)):
            try:
                r = setup(cfg, cfg.check_slot_number(n), a.link or None, a.dry_run)
                print(json.dumps(r, indent=2))
            except SlotError as e:
                print(f"slot {n}: {e}", file=sys.stderr)
                rc = 2
        return rc
    n = _slot_arg(cfg, a.slot)
    owner = a.owner or tc.env_run()
    if a.cmd == "lock":
        owner = owner or "manual"
        # pid 0 (manual): this process ends right away, and a lock tied to a dead pid counts as stale
        ok, msg, _ = acquire_wait(cfg, n, owner, a.wait, pid=0)
        print(msg)
        if not ok and n == 1:
            return 2
        return 0 if ok else 1
    if a.cmd == "unlock":
        ok, msg = release(cfg, n, owner, manual=True, force=a.force)
        print(msg)
        if cfg.is_slot_one(n):
            return 2
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
