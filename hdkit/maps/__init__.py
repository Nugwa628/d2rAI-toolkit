"""hdkit.maps: map tools that work on any mod's classic maps (README.md).

  overlay          a map's HD build as a mod data overlay (preset + every file it references)
  automap_render   the automap (minimap) of a map, classic and HD, as the game builds it
  automap_rows     automap.txt rows for the mod's own wall tiles (stock levels unchanged, proven per build)
  automap_art      brighter legacy copies of the template cels for those rows, in free cels
  automap_void     walls no player can reach moved to a copy key without a row, so the minimap leaves them out

Run `python -m hdkit.maps <command> -h`. Places come from hdkit.toml (hdkit/site.py).
"""
