"""Write a plain placeholder icon (a blue rounded square) for the downloader: downloader/res/placeholder.ico.

Replace it with your own with  cmake -DDL_ICON_FILE=path/to/icon.ico . Run:  python downloader/tools/make_placeholder_icon.py
"""
import struct
import sys
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 64)
FILL, EDGE = (91, 155, 213), (34, 39, 46)


def png(size: int) -> bytes:
    rows = []
    r = size * 0.18
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx = max(r - x - 0.5, x + 0.5 - (size - r), 0)
            dy = max(r - y - 0.5, y + 0.5 - (size - r), 0)
            inside = dx * dx + dy * dy <= r * r
            border = x < 2 or y < 2 or x >= size - 2 or y >= size - 2
            if not inside:
                row += bytes((0, 0, 0, 0))
            else:
                row += bytes(EDGE + (255,) if border else FILL + (255,))
        rows.append(bytes(row))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def main() -> int:
    images = [png(s) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, img in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    target = Path(__file__).resolve().parent.parent / "res" / "placeholder.ico"
    target.write_bytes(out + b"".join(images))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
