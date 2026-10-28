"""Read D2R's game files straight from its CASC storage through CascLib (ctypes).

CascLib is built by tools/build_casclib.ps1 into build/CascLib/Release/CascLib.dll. Set MODKIT_CASCLIB to use a
DLL elsewhere. Names look like "data:data/global/excel/skills.txt".
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_casc = None


class CASC_FIND_DATA(ctypes.Structure):
    _fields_ = [
        ("szFileName", ctypes.c_char * 260),  # MAX_PATH
        ("CKey", ctypes.c_ubyte * 16),
        ("EKey", ctypes.c_ubyte * 16),
        ("TagBitMask", ctypes.c_uint64),
        ("FileSize", ctypes.c_uint64),
        ("szPlainName", ctypes.c_char_p),
        ("dwFileDataId", wintypes.DWORD),
        ("dwLocaleFlags", wintypes.DWORD),
        ("dwContentFlags", wintypes.DWORD),
        ("dwSpanCount", wintypes.DWORD),
        ("bFileAvailable", wintypes.DWORD),  # 1-bit bitfield in a DWORD
        ("NameType", ctypes.c_int),
    ]


def lib() -> ctypes.WinDLL:
    global _casc
    if _casc is not None:
        return _casc
    path = Path(os.environ.get("MODKIT_CASCLIB", ROOT / "build" / "CascLib" / "Release" / "CascLib.dll"))
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run tools/build_casclib.ps1 (or set MODKIT_CASCLIB)")
    c = ctypes.WinDLL(str(path))
    # CascLib is built ANSI (CASC_UNICODE off).
    c.CascOpenStorage.argtypes = [ctypes.c_char_p, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    c.CascOpenStorage.restype = wintypes.BOOL
    c.CascCloseStorage.argtypes = [wintypes.HANDLE]
    c.CascCloseStorage.restype = wintypes.BOOL
    c.CascOpenFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                               ctypes.POINTER(wintypes.HANDLE)]
    c.CascOpenFile.restype = wintypes.BOOL
    c.CascGetFileSize.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    c.CascGetFileSize.restype = wintypes.DWORD
    c.CascReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    c.CascReadFile.restype = wintypes.BOOL
    c.CascCloseFile.argtypes = [wintypes.HANDLE]
    c.CascCloseFile.restype = wintypes.BOOL
    c.CascFindFirstFile.argtypes = [wintypes.HANDLE, ctypes.c_char_p, ctypes.POINTER(CASC_FIND_DATA), ctypes.c_char_p]
    c.CascFindFirstFile.restype = wintypes.HANDLE
    c.CascFindNextFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(CASC_FIND_DATA)]
    c.CascFindNextFile.restype = ctypes.c_bool
    c.CascFindClose.argtypes = [wintypes.HANDLE]
    c.CascFindClose.restype = ctypes.c_bool
    _casc = c
    return c


class Storage:
    """An open CASC storage (the D2R install folder)."""

    def __init__(self, game_dir: str | Path):
        self.handle = wintypes.HANDLE()
        if not lib().CascOpenStorage(str(game_dir).encode("mbcs"), 0, ctypes.byref(self.handle)):
            raise OSError(ctypes.GetLastError(), f"CascOpenStorage failed: {game_dir}")

    def close(self) -> None:
        if self.handle:
            lib().CascCloseStorage(self.handle)
            self.handle = wintypes.HANDLE()

    def read(self, name: str) -> bytes:
        """Whole file, e.g. 'data:data/global/excel/skills.txt'. FileNotFoundError if absent."""
        c = lib()
        fh = wintypes.HANDLE()
        if not c.CascOpenFile(self.handle, name.encode("mbcs"), 0, 0, ctypes.byref(fh)):
            raise FileNotFoundError(ctypes.GetLastError(), name)
        try:
            size = c.CascGetFileSize(fh, None)
            buf = ctypes.create_string_buffer(size)
            got = wintypes.DWORD()
            if not c.CascReadFile(fh, buf, size, ctypes.byref(got)):
                raise OSError(ctypes.GetLastError(), f"CascReadFile failed: {name}")
            return buf.raw[: got.value]
        finally:
            c.CascCloseFile(fh)

    def files(self, mask: str = "*") -> list[tuple[str, int, bool]]:
        """(name, size, available locally) for every named file matching mask."""
        c = lib()
        data = CASC_FIND_DATA()
        find = c.CascFindFirstFile(self.handle, mask.encode("mbcs"), ctypes.byref(data), None)
        out: list[tuple[str, int, bool]] = []
        if not find:
            return out
        try:
            while True:
                if data.NameType == 0:  # CascNameFull
                    out.append((data.szFileName.decode("mbcs"), data.FileSize, bool(data.bFileAvailable & 1)))
                if not c.CascFindNextFile(find, ctypes.byref(data)):
                    break
        finally:
            c.CascFindClose(find)
        return out
