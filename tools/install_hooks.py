#!/usr/bin/env python3
"""Install the git hooks from tools/hooks/ into this clone.

    python tools/install_hooks.py            install pre-commit and commit-msg
    python tools/install_hooks.py --force    overwrite hooks that did not come from here
    python tools/install_hooks.py --remove   remove the hooks this script installed

The hooks run tools/check_public.py before each commit. It reads banned terms from a file outside the repository
(~/.d2r-modkit/private_terms.txt, or $D2RAI_PRIVATE_TERMS); without one it still runs the generic checks.
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = ("pre-commit", "commit-msg")
MARK = "tools/check_public.py"


def hooks_dir(root: Path) -> Path:
    out = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "hooks"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"install_hooks: not a git repository: {root}")
    p = Path(out.stdout.strip())
    return p if p.is_absolute() else root / p


def is_ours(path: Path) -> bool:
    return path.is_file() and MARK in path.read_text(encoding="utf-8", errors="replace")


def install(root: Path, force: bool) -> int:
    dest = hooks_dir(root)
    dest.mkdir(parents=True, exist_ok=True)
    rc = 0
    for name in HOOKS:
        target = dest / name
        if target.exists() and not is_ours(target) and not force:
            print(f"skipped {name}: a different hook is already installed (use --force to replace it)")
            rc = 1
            continue
        data = (HERE / "hooks" / name).read_bytes().replace(b"\r\n", b"\n")   # sh needs LF endings
        target.write_bytes(data)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"installed {target}")
    return rc


def remove(root: Path) -> int:
    for name in HOOKS:
        target = hooks_dir(root) / name
        if is_ours(target):
            target.unlink()
            print(f"removed {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--root", default=str(HERE.parent), help="repository to install into (default: this one)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    return remove(root) if args.remove else install(root, args.force)


if __name__ == "__main__":
    sys.exit(main())
