"""The one config of the testing tools (game_slots, game_ctl, game_checks, release_install).

Nothing about a game folder, a mod name, a plugin or a server is written into the scripts: it all comes from one
TOML (or JSON) file. Where the file is looked for:

  1. --config <file> on the command line
  2. the environment variable MODKIT_TESTING_CONFIG
  3. ./modkit-testing.toml in the current folder

Copy testing/testing.example.toml to modkit-testing.toml and edit it. Missing keys fall back to DEFAULTS below.
Relative paths in the file are relative to the folder of the config file. `~` and %ENV% / $ENV are expanded.

This module only reads files and does path math. It never touches a game.
"""
from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

ENV_CONFIG = "MODKIT_TESTING_CONFIG"
ENV_SLOT = "MODKIT_SLOT"
ENV_RUN = "MODKIT_RUN"
DEFAULT_FILE = "modkit-testing.toml"

DEFAULTS: dict[str, Any] = {
    "game": {
        "install_dir": "",              # slot 1: the user's own game. Read by the tools, never written.
        "slots_dir": "",                # slot N (N >= 2) lives in <slots_dir>/<slot_dir_template>
        "slot_dir_template": "slot-{slot}",
        "slot_dirs": {},                # optional per-slot folder: { "2" = "D:/my/slot2" }
        "slot_count": 3,                # slot numbers 1..slot_count; slot 1 is the user's
        "loader_exe": "D2RLoader.exe",  # slot N >= 2 runs a copy named <stem>-sN<ext>
        "game_exe": "D2R.exe",
        "window_title_prefix": "",      # optional extra filter, applied only to windows of the slot's own processes
        "client_size": [1600, 900],     # the game window's client area is resized to this
        "window_origin": [100, 60],     # first slot window position; each slot is shifted by window_step
        "window_step": [60, 40],
        "copy_mode": "copy",            # "copy" or "hardlink" (hard links only for link_dirs, same drive)
        "link_dirs": ["Data"],          # top-level folders that may be hard-linked (read-only game data)
        "exclude": ["Logs"],            # top-level names that are not copied into a slot
        "mods_policy": "skip",          # "skip": the mods folder is not copied (the installer fills it); "copy"
    },
    "mod": {
        "name": "My Mod",
        "folder": "MyMod",              # folder name under <game>/mods/
        "plugins_subdir": "d2rloader/plugins",   # inside the mod folder
        "plugin_dlls": [],              # file names in plugins_subdir
        "modinfo_files": ["{folder}.mpq/modinfo.json", "modinfo.json"],   # inside the mod folder
    },
    "saves": {
        "root": "~/Saved Games/Diablo II Resurrected/mods",
        "slot1_name": "{folder}",       # the user's own save folder name
        "slot_name": "{folder}-s{slot}",
    },
    "loader": {
        "config_file": "d2rloader/config/d2rloader.toml",   # inside the game folder
        "section": "d2rloader",
        "default_mod_key": "default_mod",
        "args_key": "launch_arguments",
        "set_default_mod": True,        # setup writes default_mod = mod.folder into the slot's loader config
    },
    "logs": {
        "loader_log": "d2rloader/logs/d2rloader.log",   # inside the game folder
        "plugin_log": "",               # optional, inside the game folder (may use {folder})
        "crash_dir": "d2rloader/crashes",
        "startup_line": "D2R startup complete.",        # appears in loader_log when the game is up
    },
    "harness": {
        # Names and numbers of the plugin's test harness. See docs/testing.md "The harness protocol".
        "shm_name": "Local\\my-harness-block-{pid}",
        "shm_name_shared": "",          # optional fallback name without a PID ("" = none)
        "magic": 0x4D4B5448,
        "block_bytes": 576,
        "command_offset": 64,           # byte offset of the command text in the block
        "command_max": 500,
        "idx_magic": 0, "idx_active": 1, "idx_x": 2, "idx_y": 3, "idx_buttons": 4, "idx_mods": 5, "idx_seq": 6,
        "idx_flags": 7, "idx_cmd_seq": 8, "idx_cmd_ack": 9, "idx_cmd_result": 10,
        "flag_background": 1,           # value written to idx_flags: do not bring the game window to the front
        "word_file": "my-testharness.txt",              # in the plugins folder; its presence turns the harness on
        "console_file": "my-testharness-console.txt",   # in the plugins folder; the plugin appends command output
        "words": [],                    # words written into word_file, {slot} is replaced
        "cursor_start": [800, 450],     # where the virtual cursor starts (client pixels)
    },
    "runs": {
        "dir": "~/modkit-runs",         # lock directories, run folders, release-install state
        "screenshots_dir": "screenshots",
    },
    "checks": {
        "modules": ["example_checks"],  # modules that register checks with @check
        "paths": [],                    # extra folders put on sys.path to find them
        "start_timeout": 120,
        "echo_command": "echo modkit-check",
        "menu_min_mean": 6.0,           # example check main-menu-visible: a screen is "not blank" above these
        "menu_min_stddev": 8.0,
    },
    "release": {
        "site_url": "",                 # e.g. https://files.example.com/my-mod/   (or dir:<path>)
        "channel": "stable",
        "seven_zip": "",                # path to 7z; "" = search the usual places
        "apply_launch": True,           # write the manifest's launch keys into the slot's loader config
        "extra_launch_args": "-w",      # appended to launch_arguments (windowed, so the harness can capture it)
        "channel_schema": 1,            # accepted schemaVersion of the channel file (0 = do not check)
        "manifest_schema": 2,           # accepted schemaVersion of the manifest (0 = do not check)
        "retries": 3,
    },
}


class ConfigError(Exception):
    """The config is missing, unreadable or wrong."""


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and out[k] is not None and k != "slot_dirs":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def find_config_path(arg: str | None = None, env: dict | None = None, cwd: str | None = None) -> str | None:
    env = os.environ if env is None else env
    if arg:
        return arg
    if env.get(ENV_CONFIG):
        return env[ENV_CONFIG]
    p = os.path.join(cwd or os.getcwd(), DEFAULT_FILE)
    return p if os.path.isfile(p) else None


def read_file(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    try:
        if path.lower().endswith(".json"):
            return json.loads(raw.decode("utf-8-sig"))
        import tomllib
        return tomllib.loads(raw.decode("utf-8-sig"))
    except Exception as e:  # tomllib.TOMLDecodeError, JSONDecodeError, UnicodeDecodeError
        raise ConfigError(f"config {path} is not valid: {e}") from e


def load(path: str | None = None, env: dict | None = None, cwd: str | None = None) -> "Config":
    """Load the config. With no file found at all the defaults are used (install_dir is then empty and every
    command that needs it says so)."""
    found = find_config_path(path, env, cwd)
    if found is None:
        return Config({}, None)
    return Config(read_file(found), os.path.abspath(found))


class Config:
    """Typed access plus all the path math. Built from a dict so tests need no file."""

    def __init__(self, data: dict, source: str | None = None, base_dir: str | None = None):
        self.data = deep_merge(DEFAULTS, data or {})
        self.source = source
        self.base_dir = base_dir or (os.path.dirname(source) if source else os.getcwd())
        self._validate()

    # ------------------------------------------------------------ plumbing

    def section(self, name: str) -> dict:
        return self.data[name]

    def __getitem__(self, name: str) -> dict:
        return self.data[name]

    def path(self, value: str) -> str:
        """Expand ~ and environment variables; relative paths are relative to the config file's folder."""
        v = os.path.expandvars(os.path.expanduser(str(value)))
        if not os.path.isabs(v):
            v = os.path.join(self.base_dir, v)
        return os.path.normpath(v)

    def _validate(self) -> None:
        g = self.data["game"]
        if not isinstance(g["slot_count"], int) or g["slot_count"] < 1:
            raise ConfigError("game.slot_count must be a whole number >= 1")
        if g["copy_mode"] not in ("copy", "hardlink"):
            raise ConfigError('game.copy_mode must be "copy" or "hardlink"')
        if g["mods_policy"] not in ("skip", "copy"):
            raise ConfigError('game.mods_policy must be "skip" or "copy"')
        if "{slot}" not in g["slot_dir_template"]:
            raise ConfigError("game.slot_dir_template must contain {slot}")
        if "{slot}" not in self.data["saves"]["slot_name"]:
            raise ConfigError("saves.slot_name must contain {slot}")
        if not re.fullmatch(r"[A-Za-z0-9._ -]+", self.data["mod"]["folder"]):
            raise ConfigError("mod.folder must be a plain folder name")

    # ------------------------------------------------------------ slots

    @property
    def slot_count(self) -> int:
        return self.data["game"]["slot_count"]

    def check_slot_number(self, n: Any) -> int:
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise ConfigError(f"slot must be a number, got {n!r}") from None
        if not 1 <= n <= self.slot_count:
            raise ConfigError(f"slot {n} is outside 1..{self.slot_count} (game.slot_count)")
        return n

    @staticmethod
    def is_slot_one(n: int) -> bool:
        return int(n) == 1

    @property
    def install_dir(self) -> str:
        v = self.data["game"]["install_dir"]
        if not v:
            raise ConfigError("game.install_dir is not set (copy testing.example.toml to modkit-testing.toml)")
        return self.path(v)

    def slot_dir(self, n: int) -> str:
        n = self.check_slot_number(n)
        if n == 1:
            return self.install_dir
        g = self.data["game"]
        own = g["slot_dirs"].get(str(n))
        if own:
            return self.path(own)
        if not g["slots_dir"]:
            raise ConfigError("game.slots_dir is not set (where the slot copies live)")
        return self.path(os.path.join(g["slots_dir"], g["slot_dir_template"].format(slot=n)))

    def slot_of_dir(self, folder: str) -> int | None:
        want = os.path.normcase(os.path.abspath(folder))
        for n in range(1, self.slot_count + 1):
            try:
                if os.path.normcase(os.path.abspath(self.slot_dir(n))) == want:
                    return n
            except ConfigError:
                continue
        return None

    def mod_folder(self) -> str:
        return self.data["mod"]["folder"]

    def mod_dir(self, n: int) -> str:
        return os.path.join(self.slot_dir(n), "mods", self.mod_folder())

    def plugins_dir(self, n: int) -> str:
        return os.path.join(self.mod_dir(n), *self.data["mod"]["plugins_subdir"].split("/"))

    def modinfo_paths(self, n: int) -> list[str]:
        return [os.path.join(self.mod_dir(n), *p.format(folder=self.mod_folder()).split("/"))
                for p in self.data["mod"]["modinfo_files"]]

    # ------------------------------------------------------------ saves

    def save_name(self, n: int) -> str:
        s = self.data["saves"]
        tpl = s["slot1_name"] if int(n) == 1 else s["slot_name"]
        return tpl.format(folder=self.mod_folder(), slot=int(n))

    def save_dir(self, n: int) -> str:
        return os.path.join(self.path(self.data["saves"]["root"]), self.save_name(n))

    # ------------------------------------------------------------ loader, logs

    def loader_name(self, n: int) -> str:
        base = self.data["game"]["loader_exe"]
        if int(n) == 1:
            return base
        stem, ext = os.path.splitext(base)
        return f"{stem}-s{int(n)}{ext}"

    def loader_path(self, n: int) -> str:
        return os.path.join(self.slot_dir(n), self.loader_name(n))

    def base_loader_path(self, n: int) -> str:
        return os.path.join(self.slot_dir(n), self.data["game"]["loader_exe"])

    def process_names(self, n: int) -> tuple[str, ...]:
        g = self.data["game"]
        return tuple(sorted({g["loader_exe"].lower(), self.loader_name(n).lower(), g["game_exe"].lower()}))

    def loader_config_path(self, n: int) -> str:
        return os.path.join(self.slot_dir(n), *self.data["loader"]["config_file"].split("/"))

    def loader_log(self, n: int) -> str:
        return os.path.join(self.slot_dir(n), *self.data["logs"]["loader_log"].split("/"))

    def plugin_log(self, n: int) -> str:
        v = self.data["logs"]["plugin_log"]
        return os.path.join(self.slot_dir(n), *v.format(folder=self.mod_folder()).split("/")) if v else ""

    def crash_dir(self, n: int) -> str:
        return os.path.join(self.slot_dir(n), *self.data["logs"]["crash_dir"].split("/"))

    # ------------------------------------------------------------ harness

    def harness_word_file(self, n: int) -> str:
        return os.path.join(self.plugins_dir(n), self.data["harness"]["word_file"])

    def console_file(self, n: int) -> str:
        return os.path.join(self.plugins_dir(n), self.data["harness"]["console_file"])

    def shm_name(self, pid: int | None = None) -> str:
        h = self.data["harness"]
        if pid is None:
            return h["shm_name_shared"]
        return h["shm_name"].format(pid=pid)

    def harness_words(self, n: int) -> list[str]:
        return [w.format(slot=int(n)) for w in self.data["harness"]["words"]]

    # ------------------------------------------------------------ runs

    @property
    def runs_dir(self) -> str:
        return self.path(self.data["runs"]["dir"])

    def lock_path(self, n: int) -> str:
        return os.path.join(self.runs_dir, f"game.lock.{int(n)}")

    def run_dir(self, run_id: str) -> str:
        if not valid_run_id(run_id):
            raise ConfigError(f"bad run id {run_id!r}: use letters, digits, dot, dash and underscore")
        return os.path.join(self.runs_dir, run_id)

    def install_state_dir(self, n: int) -> str:
        return os.path.join(self.runs_dir, f"release-install-s{int(n)}")


def valid_run_id(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", s or ""))


def env_slot(env: dict | None = None) -> int | None:
    v = (os.environ if env is None else env).get(ENV_SLOT)
    return int(v) if v and v.strip().isdigit() else None


def env_run(env: dict | None = None) -> str | None:
    v = (os.environ if env is None else env).get(ENV_RUN)
    return v or None


def add_config_arg(ap) -> None:
    ap.add_argument("--config", help=f"config file (default: ${ENV_CONFIG}, then ./{DEFAULT_FILE})")
