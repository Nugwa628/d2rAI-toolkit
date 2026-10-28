"""The pristine game files every mod build starts from.

D2R's files live in its CASC storage. modkit copies the ones a build touches into a local cache,
d2r-data/<build>/<path> (git-ignored; Blizzard's files are never committed), and reads them from there:

    base = Base()                                   # game folder from MODKIT_GAME or the default
    text = base.text("data/global/excel/skills.txt")
    base.fetch(["data/global/excel/", "data/local/lng/strings/"])   # bulk copy whole folders

Paths are always "data/..." with forward slashes, the same paths a mod uses inside <Mod>.mpq/.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import casc

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GAME = r"C:\Program Files (x86)\Diablo II Resurrected"
CACHE = Path(os.environ.get("MODKIT_CACHE", ROOT / "d2r-data"))

# Folder sets for `modkit fetch` (prefixes of CASC names, lower case)
SETS = {
    "excel": ["data/global/excel/"],
    "strings": ["data/local/lng/strings/"],
    "layouts": ["data/global/ui/layouts/"],
    "dataguide": ["data/global/dataguide/"],
    "hd-json": ["data/hd/missiles/missiles.json", "data/hd/items/", "data/hd/character/player/",
                "data/hd/overlays/", "data/hd/global/excel/"],
    "hd-ui": ["data/hd/global/ui/"],
    "legacy-ui": ["data/global/ui/"],
}
DEFAULT_SETS = ["excel", "strings", "layouts", "dataguide"]


class Base:
    def __init__(self, game: str | Path | None = None):
        self.game = Path(game or os.environ.get("MODKIT_GAME", DEFAULT_GAME))
        self._storage: casc.Storage | None = None
        self.build = self._build()
        self.root = CACHE / self.build

    def _build(self) -> str:
        cached = sorted(CACHE.glob("*/data/global/dataversionbuild.txt")) if CACHE.exists() else []
        try:
            return self.storage().read("data:data/global/dataversionbuild.txt").decode().strip()
        except (OSError, FileNotFoundError):
            if cached:  # no game or no CascLib here: use the newest cache
                return cached[-1].parent.parent.parent.parent.name
            raise

    def storage(self) -> casc.Storage:
        if self._storage is None:
            self._storage = casc.Storage(self.game)
        return self._storage

    def path(self, rel: str) -> Path:
        """Local copy of a game file, extracted on first use. FileNotFoundError if D2R has no such file."""
        rel = rel.replace("\\", "/").lstrip("/")
        local = self.root / rel
        if not local.exists():
            data = self.storage().read("data:" + rel)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
        return local

    def exists(self, rel: str) -> bool:
        try:
            self.path(rel)
            return True
        except (FileNotFoundError, OSError):
            return False

    def bytes(self, rel: str) -> bytes:
        return self.path(rel).read_bytes()

    def text(self, rel: str) -> str:
        return self.path(rel).read_text("utf-8-sig")

    def list(self, prefix: str) -> list[str]:
        """Game file paths ("data/...") under a prefix, from CASC's name list."""
        prefix = prefix.replace("\\", "/").lower()
        mask = "data:" + prefix.replace("/", "\\") + "*"
        names = [n for n, _size, avail in self.storage().files(mask) if avail]
        out = []
        for n in names:
            p = n.split(":", 1)[-1].replace("\\", "/")
            if p.lower().startswith(prefix):
                out.append(p)
        return sorted(set(out))

    def fetch(self, prefixes: list[str]) -> int:
        n = 0
        for prefix in prefixes:
            files = [prefix] if not prefix.endswith("/") else self.list(prefix)
            for rel in files:
                try:
                    self.path(rel)
                    n += 1
                except FileNotFoundError:
                    print(f"   not in this D2R build: {rel}")
        return n
