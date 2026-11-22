"""COF (a classic unit's composite file): the layer list and the frame and direction counts.

COMPS is the engine's composite order (the COF's layer code indexes it). parse_cof() reads the header, the 9-byte layers
(composite, shadow, selectable, overlay, transparency, weapon class) and checks the size of the keyframe and priority
tables.
"""

COMPS = ["hd", "tr", "lg", "ra", "la", "rh", "lh", "sh", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]
MODES = ["DT", "NU", "WL", "GH", "A1", "A2", "BL", "SC", "S1", "S2", "S3", "S4", "DD"]


def parse_cof(b: bytes) -> dict:
    """COF: u8 layers, u8 frames per direction, u8 directions, 25 header bytes, 9-byte layers (composite, shadow, selectable,
    overlay, transparency, weapon class[4]), then the keyframe flags (frames) and the layer priorities (frames x dirs x layers)."""
    if len(b) < 28:
        raise ValueError("short COF")
    n, frames, dirs = b[0], b[1], b[2]
    want = 28 + 9 * n + frames + frames * dirs * n
    if want != len(b):
        raise ValueError(f"COF size {len(b)} != {want} for {n} layers, {frames} frames, {dirs} directions")
    layers = []
    for i in range(n):
        o = 28 + 9 * i
        comp = b[o]
        if comp >= len(COMPS):
            raise ValueError(f"COF layer {i}: composite {comp}")
        layers.append((COMPS[comp], b[o + 5:o + 9].split(b"\0")[0].decode("latin-1").lower()))
    return {"frames": frames, "dirs": dirs, "layers": layers}
