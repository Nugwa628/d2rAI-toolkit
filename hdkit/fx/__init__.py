"""hdkit.fx: D2R's HD particle effects (baked PopcornFX `.particles`): read, edit, simulate, draw (docs/hdfx.md).

  schema    field layout of the 26 object classes, which fields are object/string references
  pfile     lossless object file: `PFile.read(bytes)`, `.data()`; add/remove/copy objects, merge strings
  graph     the effect graph (`load`, `Effect.to_json`) and edits (`apply_edits`)
  render    FxRenderer (fx/render.py), packers, rspec (renderers + constant inputs); view/fx.wgsl
  coverage  per-effect needs and drawn/partly/not report; samplers, context, api: see their modules
  cbvm      disassembler/assembler of the compiled scripts (CBVM), opcode tables
  vm        vectorised CBVM interpreter (numpy, one lane per particle), engine-call registry `ENGINE`
  routing   layer slots, event slots, scripts, samplers, stream defaults: what the runtime reads
  sim       runtime: effect clock, layers, spawns, events, particle storage (`Sim`)

`python -m hdkit.fx info|dump|survey|roundtrip|context|coverage|sim|vmcov|dis ...`.
"""
