"""hdkit.monster3d: a classic monster sprite rebuilt as a D2R 3D model by hand (docs/monster-sprite-to-3d.md).

Run a step with `python -m hdkit.monster3d <script> --monster <monster.json> [args]`. The per-monster settings are in the
monster config (common.MONSTER_DEFAULTS), the machine's in hdkit.toml [monster3d] or HDKIT_M3D_* variables. The Blender
scripts in blender/ run inside Blender (a separate GPL program), never imported here. No art is stored in this package.
"""
