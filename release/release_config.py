"""The one config file every release script reads (release.toml, see release.example.toml and docs/release.md).

Python 3.12+, standard library only (tomllib). Nothing about a mod is written into the scripts: its name, its folder, the
site URL, the upload target, the pinned D2RLoader, the files a player may edit and the files that must never ship all come
from here. A command-line option beats an environment variable, which beats the file, which beats the defaults below.

  python release/release_config.py [--config release.toml] get site.url          print one value (release.sh uses this)
  python release/release_config.py [--config release.toml] check                  load it and list what is still a placeholder
"""
import argparse
import copy
import re
import sys
import tomllib
from pathlib import Path

DEFAULT_CONFIG_NAME = "release.toml"
ENV_TARGET = "MODKIT_PUBLISH_TARGET"
ENV_SFTP = "MODKIT_SFTP"

# Files that never go into a release: the test harness, a player's saves and their sidecars, logs, dumps. Regular
# expressions, matched case-insensitively against the path inside the mod folder ('/' separators).
DEFAULT_FORBIDDEN = [
    r"(^|/)[^/]*testharness[^/]*$",
    r"(^|/)(imgui\.ini|debugsettings\.json|input-bindings\.toml)$",
    r"\.(d2s|d2i|d2x|d2rl|ctc|ctl|key|map|ma\d|log|dmp|pdb|orig)$",
]

DEFAULTS = {
    "mod": {
        "name": "My Mod",            # shown to players
        "folder": "MyMod",           # the folder under <D2R>/mods/ and the 7z's top folder
        "save_folder": "MyMod/",     # modinfo.json "savepath": characters live under Saved Games/.../mods/<this>
        "website": "https://example.com/your-name/your-mod",
    },
    "game": {
        "version": "3.3.0",          # D2R's version, shown in the notes
        "build": "93847",            # the D2R build the plugin needs
        "image_timestamp": "",       # optional: PE TimeDateStamp of D2R.exe, e.g. "0x6ab3782c"
    },
    "d2rloader": {                   # the pinned D2RLoader: informational for the downloader (it never downloads it)
        "version": "1.3.1-beta",
        "url": "https://d2rloader.net/",
        "size": 0,
        "sha256": "0" * 64,
        "signerThumbprint": "",
    },
    "launch": {"default_mod": "MyMod", "launch_arguments": ""},
    "package": {
        "dist": "dist",
        "plugin_dir": "d2rloader/plugins",          # --plugin DLL is copied here
        "plugin_forbidden_strings": ["testharness"],  # a release DLL must not contain these
        "preserve": ["d2rloader/config/d2rloader.toml"],  # the player's own files: written only when missing
        "forbidden": DEFAULT_FORBIDDEN,
        "data_dir": "{folder}.mpq/data",             # where --game-extract copies are looked for
        "keep_identical": [],                        # files --game-extract must not remove even when identical
    },
    "downloader": {
        "exe_name": "MyMod-Downloader.exe",
        "exe": "",                    # default path of the built exe for `publish --downloader` (release.sh)
        "min_version": "",            # optional: manifest "launcherMin", the oldest downloader that can install the release
    },
    "site": {
        "url": "https://files.example.com/mymod/",   # what players' downloaders read (HTTPS, ends in '/')
        "target": "sftp://modrelease@<your-server>/mymod",  # where publish uploads (or dir:<path> for a local test)
        "identity": "",               # ssh private key for the sftp target
        "incoming": "../incoming",    # next to the site root, never served
        "channels": ["stable", "test"],
        "publish_channel": "test",
        "promote_channel": "stable",
    },
    "server": {                       # the download host (docs/release.md "The download host"); used by the examples
        "root": "/srv/mod-files",
        "site_dir": "mymod",
        "user": "modrelease",
    },
    "mirror": {                       # optional S3-compatible copy of the site (docs/release.md "CDN mirror")
        "enabled": False,
        "endpoint": "s3.example.com",
        "bucket": "my-mod-files",
        "prefix": "mymod",
        "rclone_remote": "mirror",
        "rclone_config": "/etc/mod-release/rclone.conf",
        "acl": "public-read",         # "" for providers without object ACLs
    },
    "repos": {                        # release pages
        "changelog": "CHANGELOG.md",
        "github": "your-name/your-mod",
        "github_token_env": "MODKIT_GITHUB_TOKEN",
        "github_token_file": "~/.d2r-modkit/github_token",
        "forgejo_url": "https://forge.example.com",
        "forgejo": "your-name/your-mod",
        "forgejo_token_env": "MODKIT_FORGEJO_TOKEN",
        "forgejo_token_file": "~/.d2r-modkit/forgejo_token",
    },
}


class ConfigError(Exception):
    pass


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def defaults() -> dict:
    return copy.deepcopy(DEFAULTS)


def load(path=None, required=False) -> dict:
    """The defaults overlaid with the TOML file at `path` (default ./release.toml). A missing default file is fine unless
    `required`; a missing file that was asked for by name is always an error."""
    explicit = path is not None
    p = Path(path) if explicit else Path(DEFAULT_CONFIG_NAME)
    if not p.is_file():
        if explicit or required:
            raise ConfigError(f"no config file {p}: copy release/release.example.toml to {DEFAULT_CONFIG_NAME} and edit it")
        return defaults()
    try:
        data = tomllib.loads(p.read_text("utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ConfigError(f"{p}: {e}")
    cfg = deep_merge(DEFAULTS, data)
    validate(cfg, p)
    return cfg


def validate(cfg: dict, where="config"):
    mod = cfg["mod"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", str(mod["folder"])):
        raise ConfigError(f"{where}: mod.folder {mod['folder']!r} must be one plain word (letters, digits, - _ .)")
    if not str(mod["name"]).strip():
        raise ConfigError(f"{where}: mod.name is empty")
    exe = str(cfg["downloader"]["exe_name"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.exe", exe):
        raise ConfigError(f"{where}: downloader.exe_name {exe!r} must be a plain file name ending in .exe")
    chans = cfg["site"]["channels"]
    if not chans or not all(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(c)) for c in chans):
        raise ConfigError(f"{where}: site.channels must be lower-case names like [\"stable\", \"test\"]")
    for key in ("publish_channel", "promote_channel"):
        if cfg["site"][key] not in chans:
            raise ConfigError(f"{where}: site.{key} {cfg['site'][key]!r} is not in site.channels")
    inc = str(cfg["site"]["incoming"])
    if inc.startswith("/") or not inc:
        raise ConfigError(f"{where}: site.incoming must be a path relative to the site root, like \"../incoming\"")
    for pat in cfg["package"]["forbidden"]:
        try:
            re.compile(pat)
        except re.error as e:
            raise ConfigError(f"{where}: package.forbidden has a bad regular expression {pat!r}: {e}")


def get(cfg: dict, dotted: str, default=None):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def placeholders(cfg: dict) -> list:
    """Settings that still hold the example's placeholder text (so a release would carry or hit made-up values)."""
    found = []

    def walk(prefix, node):
        for k, v in node.items():
            if isinstance(v, dict):
                walk(f"{prefix}{k}.", v)
            elif isinstance(v, str) and ("<your-" in v or "example.com" in v or v == "0" * 64):
                found.append(f"{prefix}{k} = {v!r}")
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and ("<your-" in item or "example.com" in item):
                        found.append(f"{prefix}{k} has {item!r}")

    walk("", cfg)
    return found


def add_config_arg(ap: argparse.ArgumentParser):
    ap.add_argument("--config", help=f"the release config (default ./{DEFAULT_CONFIG_NAME}; see release/release.example.toml)")


def early_config(argv=None, required=False) -> dict:
    """Read only --config from argv (before the real parser is built, since its choices come from the config)."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    known, _ = pre.parse_known_args(argv)
    return load(known.config, required=required)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_arg(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get", help="print one setting, e.g. site.url (lists are printed one item per line)")
    g.add_argument("key")
    sub.add_parser("check", help="load the config and list the settings that are still placeholders")
    a = ap.parse_args(argv)
    try:
        cfg = load(a.config)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.cmd == "get":
        value = get(cfg, a.key)
        if value is None:
            print(f"error: no setting {a.key}", file=sys.stderr)
            return 1
        if isinstance(value, bool):
            print("true" if value else "false")
        elif isinstance(value, list):
            print("\n".join(str(v) for v in value))
        else:
            print(value)
        return 0
    left = placeholders(cfg)
    for line in left:
        print("placeholder: " + line)
    print(f"{len(left)} placeholders left" if left else "no placeholders left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
