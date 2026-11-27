"""hdkit: a general library for D2R's HD art (models, skeletons, clips, textures, cards, maps) and its offline rendering.

The package holds code only: no game art, no mod-specific rules. Paths to game files come from the caller (a list of local
roots, see `hdkit.view.vfs`) or from the config file read by `hdkit.site` (hdkit.toml, see hdkit/hdkit.example.toml).
A project can replace the site rules with its own module `hdkit_site` on sys.path.

  hdkit.site      the config: work folder, art store, game folder for CASC reads, lock files, the mod's folders
  hdkit.gpugate   may a GPU job run now: lock files and GPU and CPU load (Windows counters, Linux amdgpu sysfs and load)
  hdkit.memwatch  run a command under a resident-memory cap (`python -m hdkit.memwatch --gb 9 -- ...`; Linux)
  hdkit.store     the content-addressed art store for large outputs
  hdkit.formats   D2R file formats: GR2 models and clips, glTF, textures, the model index, DCC, COF, particles
  hdkit.view      the offline viewer and renderer (`python -m hdkit.view ...`), the map viewer (`... map ...`), the browser
                  pages and the MCP server
  hdkit.fx        D2R's HD particle effects (`.particles`): read, simulate, draw, edit, author (`python -m hdkit.fx ...`,
                  `python -m hdkit.view fx ...`; knowledge/hd-effects.md, docs/hdfx.md)
  hdkit.maps      classic maps in HD: mod data overlays and minimap (automap) rows (`python -m hdkit.maps ...`)
  hdkit.signoff   the in-game sign-off of a 3D unit against the viewer (`python -m hdkit.signoff ...`); its game half
                  goes through a backend and a seat hook (hdkit/signoff/backend.py, HARNESS.md)
  hdkit.sprite3d  the fitting camera, the no-GPU renderer, the rig carry and the frame upscale the 3D routes share
  hdkit.monster3d the sprite-to-3D monster scripts (`python -m hdkit.monster3d <script> --monster <monster.json>`,
                  docs/monster-sprite-to-3d.md)
"""
