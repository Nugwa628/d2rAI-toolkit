"""Install a built mod into the game: <game>/mods/<Name>/ (D2R folder mod + D2RLoader files).

    <game>/mods/<Name>/
      <Name>.mpq/modinfo.json         {"name": "<Name>", "savepath": "<Name>/"}  (own save folder)
      <Name>.mpq/data/...             the project's out/data (only files the mod changes)
      modinfo.json                    a copy (D2RLoader's guide layout)
      d2rloader/metadata.json         D2RLoader mod metadata
      d2rloader/config/d2rloader.toml [d2rcore.save_format] mode = "extended" when mod.json extendedSaves
      d2rloader/plugins/*.dll         mod.json "plugins" (built D2RLoader plugins; mod-scoped)

Only <Name>.mpq/data is replaced on reinstall. Saves live in Saved Games and are never touched. Install only while
the game is closed, and restart the game fully afterwards. Without plugins or extended saves the mod also runs
on plain D2R: D2R.exe -mod <Name> -txt.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def game_running() -> bool:
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return "d2r.exe" in out or "d2rloader.exe" in out


def install(project: Path, game: Path) -> str:
    project = Path(project)
    mod = json.loads((project / "mod.json").read_text("utf-8"))
    name = mod["name"]
    src = project / "out" / "data"
    if not src.exists():
        raise FileNotFoundError(f"{src} missing: run modkit build first")
    if not (game / "D2R.exe").exists():
        raise FileNotFoundError(f"{game} is not a D2R folder")
    if game_running():
        raise RuntimeError("D2R / D2RLoader is running: close the game before installing")
    root = game / "mods" / name
    mpq = root / f"{name}.mpq"
    if (mpq / "data").exists():
        shutil.rmtree(mpq / "data")
    shutil.copytree(src, mpq / "data")
    modinfo = json.dumps({"name": name, "savepath": mod.get("savepath", f"{name}/")}, indent=2)
    for target in (mpq / "modinfo.json", root / "modinfo.json"):
        target.write_text(modinfo, encoding="utf-8")

    d2rl = root / "d2rloader"
    for sub in ("config", "plugins", "patches"):
        (d2rl / sub).mkdir(parents=True, exist_ok=True)
    meta = {"schemaVersion": 1,
            "metadata": {"name": mod.get("title", name), "modVersion": mod.get("version", "0.1.0"),
                         "author": mod.get("author", ""), "description": mod.get("description", ""),
                         "website": mod.get("website", "")}}
    (d2rl / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    plugins = []
    for p in mod.get("plugins", []):
        dll = (project / p).resolve()
        if not dll.exists():
            raise FileNotFoundError(f"plugin {dll} missing: build it first (plugin-template/build.ps1)")
        shutil.copy2(dll, d2rl / "plugins" / dll.name)
        plugins.append(dll.name)
    toml = d2rl / "config" / "d2rloader.toml"
    if mod.get("extendedSaves") and not toml.exists():  # never overwrite a config someone edited
        toml.write_text('[d2rcore.save_format]\nmode = "extended"\n', encoding="utf-8")
    files = sum(1 for p in (mpq / "data").rglob("*") if p.is_file())
    return f"installed {files} files -> {root}" + (f"; plugins: {', '.join(plugins)}" if plugins else "")
