"""Read D2R's game files straight from its CASC storage through CascLib (ctypes).

Windows: CascLib is built by tools/build_casclib.ps1 into build/CascLib/Release/CascLib.dll. Linux: build CascLib with
CMake (docs/linux.md) and point MODKIT_CASCLIB at the libcasc.so; the default is build/CascLib/libcasc.so. Names look like
"data:data/global/excel/skills.txt".

On Linux the Windows types are what CascPort.h defines there: DWORD is a 32-bit unsigned int (ctypes.wintypes.DWORD is a
64-bit c_ulong on Linux), the API returns C++ bool, MAX_PATH is 1024, paths are UTF-8, and the last error comes from
GetCascError().
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"
if WINDOWS:
    DWORD, BOOL, HANDLE = wintypes.DWORD, wintypes.BOOL, wintypes.HANDLE
    MAX_PATH, ENCODING = 260, "mbcs"     # CascLib is built ANSI (CASC_UNICODE off)
    DEFAULT_LIB = ROOT / "build" / "CascLib" / "Release" / "CascLib.dll"
else:
    DWORD, BOOL, HANDLE = ctypes.c_uint32, ctypes.c_bool, ctypes.c_void_p   # CascPort.h, !WINDOWS branch
    MAX_PATH, ENCODING = 1024, "utf-8"
    DEFAULT_LIB = ROOT / "build" / "CascLib" / "libcasc.so"
_casc = None


def _enc(text: str) -> bytes:
    return text.encode(ENCODING) if WINDOWS else text.encode(ENCODING, "surrogateescape")


def _dec(raw: bytes) -> str:
    return raw.decode(ENCODING) if WINDOWS else raw.decode(ENCODING, "surrogateescape")


def last_error() -> int:
    return ctypes.GetLastError() if WINDOWS else lib().GetCascError()


class CASC_FIND_DATA(ctypes.Structure):
    _fields_ = [
        ("szFileName", ctypes.c_char * MAX_PATH),
        ("CKey", ctypes.c_ubyte * 16),
        ("EKey", ctypes.c_ubyte * 16),
        ("TagBitMask", ctypes.c_uint64),
        ("FileSize", ctypes.c_uint64),
        ("szPlainName", ctypes.c_char_p),
        ("dwFileDataId", DWORD),
        ("dwLocaleFlags", DWORD),
        ("dwContentFlags", DWORD),
        ("dwSpanCount", DWORD),
        ("bFileAvailable", DWORD),  # 1-bit bitfield in a DWORD
        ("NameType", ctypes.c_int),
    ]


def lib():
    global _casc
    if _casc is not None:
        return _casc
    path = Path(os.environ.get("MODKIT_CASCLIB", DEFAULT_LIB))
    if not path.exists():
        how = "run tools/build_casclib.ps1" if WINDOWS else "build CascLib with CMake (docs/linux.md)"
        raise FileNotFoundError(f"{path} missing: {how} (or set MODKIT_CASCLIB)")
    c = ctypes.WinDLL(str(path)) if WINDOWS else ctypes.CDLL(str(path))
    c.CascOpenStorage.argtypes = [ctypes.c_char_p, DWORD, ctypes.POINTER(HANDLE)]
    c.CascOpenStorage.restype = BOOL
    c.CascCloseStorage.argtypes = [HANDLE]
    c.CascCloseStorage.restype = BOOL
    c.CascOpenFile.argtypes = [HANDLE, ctypes.c_void_p, DWORD, DWORD, ctypes.POINTER(HANDLE)]
    c.CascOpenFile.restype = BOOL
    c.CascGetFileSize.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
    c.CascGetFileSize.restype = DWORD
    c.CascReadFile.argtypes = [HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
    c.CascReadFile.restype = BOOL
    c.CascCloseFile.argtypes = [HANDLE]
    c.CascCloseFile.restype = BOOL
    c.CascFindFirstFile.argtypes = [HANDLE, ctypes.c_char_p, ctypes.POINTER(CASC_FIND_DATA), ctypes.c_char_p]
    c.CascFindFirstFile.restype = HANDLE
    c.CascFindNextFile.argtypes = [HANDLE, ctypes.POINTER(CASC_FIND_DATA)]
    c.CascFindNextFile.restype = ctypes.c_bool
    c.CascFindClose.argtypes = [HANDLE]
    c.CascFindClose.restype = ctypes.c_bool
    if not WINDOWS:
        c.GetCascError.argtypes = []
        c.GetCascError.restype = DWORD
    _casc = c
    return c


class Storage:
    """An open CASC storage (the D2R install folder)."""

    def __init__(self, game_dir: str | Path):
        self.handle = HANDLE()
        if not lib().CascOpenStorage(_enc(str(game_dir)), 0, ctypes.byref(self.handle)):
            raise OSError(last_error(), f"CascOpenStorage failed: {game_dir}")

    def close(self) -> None:
        if self.handle:
            lib().CascCloseStorage(self.handle)
            self.handle = HANDLE()

    def read(self, name: str) -> bytes:
        """Whole file, e.g. 'data:data/global/excel/skills.txt'. FileNotFoundError if absent."""
        c = lib()
        fh = HANDLE()
        if not c.CascOpenFile(self.handle, _enc(name), 0, 0, ctypes.byref(fh)):
            raise FileNotFoundError(last_error(), name)
        try:
            size = c.CascGetFileSize(fh, None)
            buf = ctypes.create_string_buffer(size)
            got = DWORD()
            if not c.CascReadFile(fh, buf, size, ctypes.byref(got)):
                raise OSError(last_error(), f"CascReadFile failed: {name}")
            return buf.raw[: got.value]
        finally:
            c.CascCloseFile(fh)

    def files(self, mask: str = "*") -> list[tuple[str, int, bool]]:
        """(name, size, available locally) for every named file matching mask."""
        c = lib()
        data = CASC_FIND_DATA()
        find = c.CascFindFirstFile(self.handle, _enc(mask), ctypes.byref(data), None)
        out: list[tuple[str, int, bool]] = []
        if not find:
            return out
        try:
            while True:
                if data.NameType == 0:  # CascNameFull
                    out.append((_dec(data.szFileName), data.FileSize, bool(data.bFileAvailable & 1)))
                if not c.CascFindNextFile(find, ctypes.byref(data)):
                    break
        finally:
            c.CascFindClose(find)
        return out
