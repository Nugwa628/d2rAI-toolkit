"""HD-side data a new skill, item or missile needs (knowledge/hd-graphics.md).

* Player sequence animations: data/hd/character/player/<class>/<class>_state_machine.json. A skill whose anim is
  SQ plays the clip of the AnimationState with modeId 18 and skillIndex = its skill ID; without one the HD body
  shows no animation (the classic one still plays). add_sequence_state() copies a vanilla skill's state.
* Skill icons: one horizontal strip per class, data/hd/global/ui/spells/<class>/<xx>skillicon.sprite (130 px icons
  on a 132 px stride, normal + pressed frame per skill, at most 16384 px wide), and the classic
  data/global/ui/spells/<xx>skillicon.dc6. skilldesc IconCel = the normal frame's index in both.
* Missiles: data/hd/missiles/missiles.json maps a missiles.txt name (lower case) to an HD model.
* Items: data/hd/items/items.json (code -> {"asset"}), uniques.json / sets.json (index key -> normal/uber/ultra
  asset); assets are data/hd/global/ui/items/<weapon|armor|misc>/<asset>.sprite, 98 px per inventory cell.
"""
from __future__ import annotations

import copy
import json
import re

from . import dc6, images

SEQUENCE_MODE = 18
# charclass -> (HD folder, icon file prefix). "" = skills with no class (SubMenu icons).
CLASSES = {"ama": ("amazon", "am"), "sor": ("sorceress", "so"), "nec": ("necromancer", "ne"),
           "pal": ("paladin", "pa"), "bar": ("barbarian", "ba"), "dru": ("druid", "dr"),
           "ass": ("assassin", "as"), "war": ("warlock", "wa"), "": ("submenu", "")}
ICON_SIZE, ICON_STRIDE, MAX_STRIP = 130, 132, 16384


def state_machine_path(charclass: str) -> str:
    folder = CLASSES[charclass][0]
    return f"data/hd/character/player/{folder}/{folder}_state_machine.json"


def add_sequence_state(sm: dict, like_skill_id: int, skill_id: int, label: str) -> int:
    """Copies the sequence state of skill like_skill_id for skill_id (with transitions to and from it).
    Returns the new stateId. KeyError if like_skill_id has no sequence state in this class."""
    src = next((s for s in sm["states"] if s.get("modeId") == SEQUENCE_MODE and s.get("skillIndex") == like_skill_id),
               None)
    if src is None:
        raise KeyError(f"no HD sequence state for skill {like_skill_id} in this class's state machine")
    tag = re.sub(r"\W+", "_", label)
    own = next((s for s in sm["states"] if s.get("modeId") == SEQUENCE_MODE and s.get("skillIndex") == skill_id), None)
    if own is not None:  # a reworked vanilla sequence skill: its state now plays the like-skill's clips
        if own is not src:
            keep = {k: own[k] for k in ("stateId", "skillIndex", "name", "_name") if k in own}
            own.clear()
            own.update(copy.deepcopy(src))
            own.update(keep)
        return own["stateId"]
    new_id = max(s["stateId"] for s in sm["states"]) + 1
    state = copy.deepcopy(src)
    state.update(stateId=new_id, skillIndex=skill_id, name=f"{src['name']}_MOD_{tag}", _name=f"MOD_{tag}")
    sm["states"].append(state)
    for group in sm["transitions"]:
        to_src = next((t for t in group["settings"] if t["to"] == src["stateId"]), None)
        if to_src is not None:
            item = copy.deepcopy(to_src)
            item.update(to=new_id, name=f"{to_src['name']}_MOD_{tag}")
            group["settings"].append(item)
    src_group = next(g for g in sm["transitions"] if g["from"] == src["stateId"])
    group = copy.deepcopy(src_group)
    group.update({"from": new_id, "name": f"{src_group['name']}_MOD_{tag}"})
    for t in group["settings"]:
        if t["to"] == new_id:
            t["name"] = f"transition_self_MOD_{tag}"
    sm["transitions"].append(group)
    return new_id


def sequence_skills(sm: dict) -> set[int]:
    return {s["skillIndex"] for s in sm["states"] if s.get("modeId") == SEQUENCE_MODE and "skillIndex" in s}


def icon_paths(charclass: str) -> tuple[str, str]:
    """(HD sprite, classic DC6) of a class's skill icon strip."""
    folder, prefix = CLASSES[charclass]
    name = f"{prefix}skillicon"
    return f"data/hd/global/ui/spells/{folder}/{name}.sprite", f"data/global/ui/spells/{name}.dc6"


def pressed(img: images.Image) -> images.Image:
    """The pressed frame: the icon darkened, as D2R's pressed icons look."""
    w, h, rgba = img
    out = bytearray(rgba)
    for i in range(0, len(out), 4):
        out[i], out[i + 1], out[i + 2] = out[i] * 3 // 5, out[i + 1] * 3 // 5, out[i + 2] * 3 // 5
    return w, h, bytes(out)


class IconStrip:
    """A class's HD icon strip and classic DC6, loaded once per build; append() adds a normal + pressed pair."""

    def __init__(self, hd_strip: images.Image, frame_width: int, frames: int, classic: list, pal):
        self.strip, self.frame_width, self.frames = hd_strip, frame_width, frames
        self.classic, self.pal = classic, pal
        self.stride = hd_strip[0] // max(frames, 1)

    def append(self, icon: images.Image) -> int:
        cel = self.frames
        if (self.frames + 2) * self.stride > MAX_STRIP:
            raise ValueError(f"icon strip full ({self.frames // 2} icons; {MAX_STRIP} px texture limit)")
        size = self.frame_width
        big = images.scale(icon, size, self.strip[1])
        w, h, _ = self.strip
        grown = images.blank(w + 2 * self.stride, h)
        grown = images.paste(grown, self.strip, 0, 0)
        grown = images.paste(grown, big, w, 0)
        grown = images.paste(grown, pressed(big), w + self.stride, 0)
        self.strip = grown
        self.frames += 2
        if self.classic is not None:
            # IconCel indexes both files, so the classic frames go to the same index. The classic file can hold
            # more frames than the HD strip (the barbarian's has 156, 96 of them unused) or fewer: overwrite at
            # that index, padding with empty frames if needed.
            cw, ch = self.classic[0][0], self.classic[0][1]
            small = images.scale(icon, cw, ch)
            while len(self.classic) < cel:
                self.classic.append(dc6.encode(images.blank(cw, ch), self.pal))
            for k, frame in ((cel, small), (cel + 1, pressed(small))):
                encoded = dc6.encode(frame, self.pal)
                if k < len(self.classic):
                    self.classic[k] = encoded
                else:
                    self.classic.append(encoded)
        return cel


def item_folder(table: str) -> str:
    return {"weapons": "weapon", "armor": "armor", "misc": "misc"}[table.lower()]


def index_key(index: str) -> str:
    """uniques.json / sets.json key of a uniqueitems / setitems index ("Buriza-Do Kyanon" -> "buriza_do_kyanon")."""
    return re.sub(r"[ \-]", "_", index.lower()).replace("'", "")


def set_list_entry(entries: list[dict], key: str, value) -> None:
    """items.json-style lists of single-key objects: replace key's entry or append it."""
    for e in entries:
        if key in e:
            e[key] = value
            return
    entries.append({key: value})


def dumps(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False)
