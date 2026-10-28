"""D2R string tables: data/local/lng/strings/*.json (and strings-legacy/, used by the classic graphics mode).

Each file is a JSON list of {"id", "Key", "enUS", "zhTW", ... 13 languages}. Rules (knowledge/strings.md):
  * A mod's file replaces the game's whole file, so every changed file is written complete.
  * New keys need IDs the game can tell apart. D2R matches string IDs by their low 16 bits (an ID above 65535
    answers for the ID 65536 lower: a port that numbered new strings from 251781 up showed "Arcane Ape" in Gheed's
    greeting). New IDs start at data/local/lng/next_string_id.txt (28106 in build 93847), stay below 65536, and
    skip every value whose low 16 bits any existing string uses.
  * Overriding a key D2R formats itself (printf placeholders like %s, %d) with a different set of placeholders
    crashes the game; set() refuses that unless force=True.
  * A key may sit in several files; set() changes every occurrence.
"""
from __future__ import annotations

import json
import re

from .base import Base

LANGS = ["enUS", "zhTW", "deDE", "esES", "frFR", "itIT", "koKR", "plPL", "esMX", "jaJP", "ptBR", "ruRU", "zhCN"]
FOLDERS = ["data/local/lng/strings", "data/local/lng/strings-legacy"]
START_ID = 28106  # build 93847's next_string_id.txt, used if the file is missing
PLACEHOLDER = re.compile(r"%(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?[sdiuxXcf%]")


class Strings:
    def __init__(self, base: Base):
        self.base = base
        self.files: dict[str, list[dict]] = {}  # "folder/name.json" -> entries (loaded on demand)
        self.changed: set[str] = set()
        self._next_id: int | None = None
        self._used16: set[int] = set()
        self._index: dict[str, list[tuple[str, dict]]] | None = None

    def _names(self) -> list[str]:
        out = []
        for folder in FOLDERS:
            try:
                out += [p for p in self.base.list(folder + "/") if p.endswith(".json") and p.count("/") == 4]
            except OSError:
                out += [str(p.relative_to(self.base.root)).replace("\\", "/")
                        for p in (self.base.root / folder).glob("*.json")]
        return out

    def load(self, rel: str) -> list[dict]:
        if rel not in self.files:
            self.files[rel] = json.loads(self.base.text(rel))
        return self.files[rel]

    def index(self) -> dict[str, list[tuple[str, dict]]]:
        if self._index is None:
            self._index = {}
            for rel in self._names():
                for e in self.load(rel):
                    self._index.setdefault(e["Key"].lower(), []).append((rel, e))
                    self._used16.add(int(e["id"]) & 0xFFFF)
            self._next_id = START_ID
            try:
                nums = re.findall(r"^\s*(\d+)\s*$", self.base.text("data/local/lng/next_string_id.txt"), re.M)
                self._next_id = int(nums[0]) if nums else START_ID
            except (FileNotFoundError, OSError):
                pass
        return self._index

    def _allocate(self) -> int:
        self.index()
        n = self._next_id
        while n & 0xFFFF in self._used16:
            n += 1
        if n > 0xFFFF:
            raise ValueError("no free string ID below 65536")
        self._used16.add(n)
        self._next_id = n + 1
        return n

    def get(self, key: str, lang: str = "enUS") -> str | None:
        hits = self.index().get(key.lower())
        return hits[0][1].get(lang) if hits else None

    def set(self, key: str, text: str | dict, file: str = "item-modifiers.json", all_languages: bool = True,
            force: bool = False) -> None:
        """Sets a key's text (a str for every language, or a dict lang -> text). Adds the key to `file` (in both
        folders) when no file has it."""
        texts = text if isinstance(text, dict) else {lang: text for lang in (LANGS if all_languages else ["enUS"])}
        hits = self.index().get(key.lower())
        if hits:
            for rel, entry in hits:
                old = entry.get("enUS", "")
                new = texts.get("enUS", old)
                if not force and sorted(PLACEHOLDER.findall(old)) != sorted(PLACEHOLDER.findall(new)):
                    raise ValueError(f"string '{key}': placeholders {PLACEHOLDER.findall(old)} -> "
                                     f"{PLACEHOLDER.findall(new)}; D2R may format this key itself (crash risk). "
                                     "Pass force=True only if you know it is plain text.")
                entry.update(texts)
                self.changed.add(rel)
            return
        new_id = self._allocate()
        for folder in FOLDERS:
            rel = f"{folder}/{file}"
            if not self.base.exists(rel):
                continue
            entry = {"id": new_id, "Key": key}
            entry.update({lang: texts.get(lang, texts.get("enUS", "")) for lang in LANGS})
            self.load(rel).append(entry)
            self.index().setdefault(key.lower(), []).append((rel, entry))
            self.changed.add(rel)

    def write(self, out_root) -> list[str]:
        written = []
        for rel in sorted(self.changed):
            dest = out_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.files[rel], ensure_ascii=False, indent=2).replace("\n", "\r\n")
            dest.write_text("﻿" + text, encoding="utf-8", newline="")  # BOM + CRLF, like D2R's files
            written.append(rel)
        return written
