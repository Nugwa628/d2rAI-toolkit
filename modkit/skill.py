"""The "skill" op: everything one new (or reworked) skill needs, in one place.

    {"op": "skill",
     "name": "Frost Lance",              skills.txt key (<= 31 characters if formulas will name it)
     "clone": "Ice Bolt",                the vanilla skill whose behaviour it starts from (its functions, anim,
                                         missiles, calcs); every column is copied, then "skills" overrides
     "replace": "Ice Bolt",              optional: overwrite this existing skill in place (keeps its ID, tree slot
                                         and icon) instead of adding a row. The safe way to change a class's
                                         skills: each class has exactly 30 and saves store one byte per class
                                         skill, so adding one needs mod.json "extendedSaves" (D2RLoader)
     "class": "sor",                     charclass (default: the clone's); "" for item/monster-only skills
     "skills": {"EMin": 12, ...},        skills.txt overrides (formula cells: bare negatives are fixed)
     "skilldesc": {...},                 skilldesc.txt overrides (description lines, damage display)
     "tree": {"page": 1, "row": 2, "column": 3},   skill tree slot (page 1-3, row 1-6, column 1-3)
     "text": {"name": "...", "short": "...", "long": "...", "alt": "..."},   tooltip strings
     "icon": "assets/frost_lance.png" or {"from": "Ice Blast"},   new icon (HD + classic) or another's IconCel
     "missiles": [{"name": "frostlance", "clone": "icebolt", "set": {...}, "hdLike": "icebolt"}],
     "states":   [{"name": "frostlanced", "clone": "freeze", "set": {...}}],
     "hdSequenceLike": "Whirlwind"       for anim SQ skills: whose HD body animation to copy (default: clone)
    }

Missiles and states are added first, so the skill's columns can name them. Both table banks (excel/, excel/base/)
get the same change.
"""
from __future__ import annotations

import re

from . import hd, images

PARTS = {"name": "str name", "short": "str short", "long": "str long", "alt": "str alt"}
CLASS_SKILLS = 30


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def build_skill(b, op: dict) -> None:
    from .project import BuildError

    name, clone, replace = op["name"], op.get("clone"), op.get("replace")
    if not clone and not replace:
        raise BuildError("a skill needs 'clone' (a skill to start from) or 'replace' (a skill to rework)")
    if len(name) > 31:
        b.warnings.append(f"skill '{name}': longer than 31 characters; formulas can't name it in quotes")

    for m in op.get("missiles", []):
        for bank in b.banks("missiles"):
            b.table("missiles", bank).clone(m["clone"], m["name"], m.get("set", {}))
        models = b.json("data/hd/missiles/missiles.json")
        like = m.get("hdLike", m["clone"]).lower()
        if m.get("hdModel") or like in models:
            models[m["name"].lower()] = m.get("hdModel") or models[like]
        else:
            b.warnings.append(f"missile {m['name']}: no HD model (missiles.json has no '{like}'): invisible in HD")
    for s in op.get("states", []):
        for bank in b.banks("states"):
            b.table("states", bank).clone(s["clone"], s["name"], s.get("set", {}))

    icon_cel = None
    for bank in b.banks("skills"):
        skills, descs = b.table("skills", bank), b.table("skilldesc", bank)
        src = skills.need(clone) if clone else None
        if replace:
            row = skills.need(replace)
            if src is not None:
                for i, col in enumerate(skills.header):
                    if i and not col.startswith("*") and col.lower() not in ("charclass", "skilldesc"):
                        row[i] = src[i]
            if name.lower() != replace.lower():
                if skills.find(name) is not None:
                    raise BuildError(f"skills: '{name}' already exists")
                for r in skills.records():
                    for c in ("reqskill1", "reqskill2", "reqskill3"):
                        if skills.get(r, c).lower() == replace.lower():
                            skills.set(r, c, name)
                row[0] = name
                if bank == "excel":
                    b.warnings.append(f"skill '{replace}' renamed to '{name}': other tables (monstats skills, item "
                                  "properties, charstats StartSkill, formulas) that name it must be updated too")
        else:
            row = skills.clone(clone, name)
        charclass = op.get("class", skills.get(row, "charclass"))
        skills.set(row, "charclass", charclass)
        skills.update(row, op.get("skills", {}))
        if not replace and charclass and bank == "excel" and not b.mod.get("extendedSaves"):
            count = len(skills.where(charclass=charclass))
            if count > CLASS_SKILLS:
                raise BuildError(f"class '{charclass}' would have {count} skills: saves hold {CLASS_SKILLS} per class. "
                                 "Use 'replace' on one of its skills, or set mod.json extendedSaves (D2RLoader "
                                 "extended save format) - knowledge/engine-limits.md")

        # skilldesc: reworked skills keep their own row, new skills get a copy of the clone's
        desc_key = skills.get(row, "skilldesc")
        if not replace or not desc_key:
            src_desc = skills.get(src, "skilldesc") if src is not None else ""
            desc_key = op.get("skilldescKey", slug(name))
            if descs.find(desc_key) is None:
                if src_desc:
                    descs.clone(src_desc, desc_key)
                else:
                    descs.append({"skilldesc": desc_key})
            skills.set(row, "skilldesc", desc_key)
        desc = descs.need(desc_key)
        for part, col in PARTS.items():
            text = op.get("text", {}).get(part)
            if text is not None:
                key = f"modkit_{slug(name)}_{part}"
                b.strings.set(key, text, "skills.json")
                descs.set(desc, col, key)
        if "tree" in op:
            t = op["tree"]
            descs.update(desc, {"SkillPage": t["page"], "SkillRow": t["row"], "SkillColumn": t["column"]})
            mine = {d.lower() for d in (skills.get(r, "skilldesc") for r in skills.where(charclass=charclass)) if d}
            for other in descs.records():
                if other is desc or other[0].lower() not in mine:
                    continue
                if (descs.get(other, "SkillPage"), descs.get(other, "SkillRow"), descs.get(other, "SkillColumn")) == \
                        (str(t["page"]), str(t["row"]), str(t["column"])):
                    raise BuildError(f"skill tree slot {t} is taken by '{other[0]}' (move or replace that skill)")
        descs.update(desc, op.get("skilldesc", {}))

        icon = op.get("icon")
        if isinstance(icon, str):
            if icon_cel is None:
                icon_cel = b.icon_strip(charclass).append(images.read_png(b.asset(icon)))
            descs.set(desc, "IconCel", icon_cel)
        elif isinstance(icon, dict):
            other = descs.need(skills.get(skills.need(icon["from"]), "skilldesc"))
            descs.set(desc, "IconCel", descs.get(other, "IconCel"))

    # HD body animation for sequence skills (the game reads the current bank's IDs)
    skills = b.table("skills")
    row = skills.need(name)
    if skills.get(row, "anim").upper() == "SQ":
        if not skills.get(row, "seqnum"):
            raise BuildError(f"skill '{name}': anim SQ needs a seqnum (copy it from a sequence skill)")
        like = op.get("hdSequenceLike", clone or replace)
        charclass = skills.get(row, "charclass")
        if charclass in hd.CLASSES and charclass:
            sm = b.json(hd.state_machine_path(charclass))
            try:
                hd.add_sequence_state(sm, skills.record_id(skills.need(like)), skills.record_id(row), name)
            except KeyError:
                b.warnings.append(f"skill '{name}': '{like}' has no HD sequence animation in the {charclass} state "
                                  "machine; set hdSequenceLike to a skill of that class that has one (e.g. "
                                  "Whirlwind, Zeal, Strafe) or the HD body will not animate")
    b.log.append(f"skill {name}: ID {skills.record_id(row)}" + (f", icon {icon_cel}" if icon_cel is not None else ""))
