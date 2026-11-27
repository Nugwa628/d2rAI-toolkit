"""Disassemble, edit and reassemble the compiled code of D2R's HD particle files (PopcornFX CBVM bytecode).

The code moved to `hdkit.fx.cbvm` (knowledge/hd-effects.md); this module is that module under its old name, so
`from hdkit.formats import hd_cbvm` and `python -m hdkit.formats.hd_cbvm dis|dump|build|tint|streams|check ...` keep
working unchanged.
"""
import sys

from hdkit.fx import cbvm as _cbvm

if __name__ == "__main__":
    sys.exit(_cbvm.main())
sys.modules[__name__] = _cbvm
