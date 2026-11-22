"""python -m hdkit.monster3d <script> [args]: run one stage script, e.g. `python -m hdkit.monster3d s5_build --monster m.json ...`.

  python -m hdkit.monster3d              lists the scripts
"""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def scripts():
    return sorted(p.stem for p in HERE.glob("*.py") if p.stem not in ("__init__", "__main__"))


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        print("scripts:", " ".join(scripts()))
        return 0
    name = sys.argv[1].removesuffix(".py")
    if name not in scripts():
        print("no script %s; scripts: %s" % (name, " ".join(scripts())), file=sys.stderr)
        return 2
    sys.argv = ["hdkit.monster3d." + name] + sys.argv[2:]
    runpy.run_module("hdkit.monster3d." + name, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
