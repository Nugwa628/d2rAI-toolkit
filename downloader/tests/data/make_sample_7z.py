#!/usr/bin/env python
"""Writes sample.7z and sample.json for the 7z decoder unit test (downloader/tests/core_tests.cpp TestSevenZip): a small
SampleMod/ folder packed with the release's own 7-Zip switches (release/package_release.py: solid LZMA2 -mx7, no time
stamps). Generated data only. 7-Zip puts the .dll into a second block with the BCJ filter, as in a release.

  py -3 downloader/tests/data/make_sample_7z.py      (needs 7-Zip; the output is committed)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEVENZ_ARGS = ["a", "-t7z", "-m0=lzma2", "-mx=7", "-ms=on", "-mmt=on", "-mtm=off", "-mtc=off", "-mta=off", "-bd", "-y"]


def find_7z() -> str:
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        exe = Path(os.environ.get(var, "")) / "7-Zip" / "7z.exe"
        if exe.is_file():
            return str(exe)
    for name in ("7z", "7zz", "7za", "7zr"):
        if shutil.which(name):
            return shutil.which(name)
    raise SystemExit("no 7-Zip found")


def files():
    unit = "".join(f"row {i}\tvalue {i * 7}\tname item{i % 9}\n" for i in range(40))
    big = "".join(f"#{k}\n" + unit * 50 for k in range(120))  # ~4 MB in 120 parts: several 1 MB chunks, little to store
    # a PE header (x64 DLL) so that 7-Zip puts this file in a BCJ block, as it does with a release's plugin
    pe = bytearray(0x400)
    pe[0:2] = b"MZ"
    pe[0x3C:0x40] = (0x80).to_bytes(4, "little")
    pe[0x80:0x84] = b"PE\0\0"
    pe[0x84:0x86] = (0x8664).to_bytes(2, "little")  # Machine AMD64
    pe[0x86:0x88] = (1).to_bytes(2, "little")  # NumberOfSections
    pe[0x94:0x96] = (0xF0).to_bytes(2, "little")  # SizeOfOptionalHeader
    pe[0x96:0x98] = (0x2022).to_bytes(2, "little")  # DLL | EXECUTABLE | LARGE_ADDRESS_AWARE
    pe[0x98:0x9A] = (0x20B).to_bytes(2, "little")  # PE32+
    pe[0xDC:0xDE] = (2).to_bytes(2, "little")  # Subsystem GUI
    for i in range(6000):  # x86 call instructions, something for the BCJ filter
        pe += b"\xe8" + (i * 16).to_bytes(4, "little") + b"\x90\x90\x55\x8b\xec"
    return {
        "SampleMod.mpq/modinfo.json": b'{"name": "SampleMod", "savepath": "SampleMod-test/"}\n',
        "SampleMod.mpq/data/global/excel/big.txt": big.encode(),
        "SampleMod.mpq/data/global/excel/small.txt": b"a\tb\n1\t2\n",
        "SampleMod.mpq/data/empty.txt": b"",
        "d2rloader/metadata.json": b'{"schemaVersion": 1}\n',
        "d2rloader/plugins/sample-core.dll": bytes(pe),
        "modinfo.json": "{\"name\": \"SampleMod\", \"é\": 1}\n".encode(),
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        mod = Path(tmp) / "SampleMod"
        fs = files()
        for rel, data in fs.items():
            (mod / rel).parent.mkdir(parents=True, exist_ok=True)
            (mod / rel).write_bytes(data)
        (mod / "d2rloader" / "patches").mkdir(parents=True)
        out = HERE / "sample.7z"
        out.unlink(missing_ok=True)
        subprocess.run([find_7z(), *SEVENZ_ARGS, str(out), mod.name], cwd=mod.parent, check=True, capture_output=True)
    listing = {"files": [{"path": rel, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for rel, d in sorted(fs.items())],
               "emptyDirs": ["d2rloader/patches"]}
    (HERE / "sample.json").write_text(json.dumps(listing, indent=1) + "\n", encoding="utf-8")
    print(f"{out}: {out.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
