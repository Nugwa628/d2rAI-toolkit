"""python -m hdkit.maps <overlay|automap-render|automap-rows|automap-art|automap-void> [args]"""
import importlib
import sys

COMMANDS = {"overlay": "overlay", "automap-render": "automap_render", "automap-rows": "automap_rows",
            "automap-art": "automap_art", "automap-void": "automap_void"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print(__doc__.strip())
        print("commands: " + ", ".join(COMMANDS))
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    mod = importlib.import_module("hdkit.maps." + COMMANDS[argv[0]])
    return mod.main(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
