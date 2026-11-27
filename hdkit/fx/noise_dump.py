"""Dump the data tables of D2R's noise kernels from a decrypted image (read only) into hdkit/fx/noise_data.py.

    python -m hdkit.fx.noise_dump <image.exe> [out.py]

`image` is a memory dump of the game with file offset == RVA (as docs/hdfx-vm-re.md uses). Addresses are RVAs.
"""
import struct
import sys

TABLES = {
    # name: (rva, byte length, note)
    "PERM": (0x1DDA9F0, 513, "byte permutation: the classic 256 entries twice, then perm[512] = perm[0]"),
    "OFFS": (0x1DDAC00, 256, "int32 [8 sort orders][2 corners][4 lanes]: lattice offsets of corners 1 and 2"),
    "GRAD": (0x1DDAD00, 384, "float32 [8 sort orders][3 axes][4 corners]: corner minus origin per axis"),
    "FPERM": (0x1DBDFE0, 257 * 4, "uint32 [257] permutation of fast.noise (same values as PERM[:257])"),
}


def main(argv):
    img = open(argv[1], "rb").read()
    out = open(argv[2], "w") if len(argv) > 2 else sys.stdout
    out.write('"""Data tables of D2R.exe\'s noise kernels, dumped by hdkit.fx.noise_dump from the decrypted image.\n\n'
              "Addresses are RVAs. noise_float3 = FUN 0xc623e0 (simplex), noise_float = 0xc616a0, fast.noise_float = "
              '0xb3d5b0.\n"""\n')
    for name, (rva, n, note) in TABLES.items():
        raw = img[rva:rva + n]
        out.write("\n# %s at %#x (%d bytes): %s\n%s = bytes.fromhex(\n" % (name, rva, n, note, name))
        h = raw.hex()
        for i in range(0, len(h), 96):
            out.write('    "%s"\n' % h[i:i + 96])
        out.write(")\n")
    out.close()


if __name__ == "__main__":
    main(sys.argv)
