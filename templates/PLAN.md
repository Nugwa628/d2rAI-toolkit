# Plan

Target: Diablo II: Resurrected `<GAME VERSION>` (build `<BUILD>`) + D2RLoader `<LOADER VERSION>`.
Mod: `<MOD NAME>`. Goal: `<one sentence>`.

Update this file when a phase item is done, with the date. The coordinator owns it. Progress per phase is a rough
estimate, not a promise.

| Phase | What | Progress |
|---|---|---|
| 0 | Setup | 0% |
| 1 | Tools | 0% |
| 2 | Data | 0% |
| 3 | Engine triage | 0% |
| 4 | Plugin | 0% |
| 5 | Graphics | 0% |
| 6 | Release | 0% |

## Phase 0: Setup
- [ ] Repository created from the toolkit; `main` is merged only by the coordinator
- [ ] Toolchain: git, Python 3.12+, CMake, C++ build tools
- [ ] Game installed at build `<BUILD>`; D2RLoader installed
- [ ] `python -m modkit fetch` works (tables, strings, layouts)
- [ ] Game slots set up (slot 1 is the owner's real game and is never touched)
- [ ] Private-words list created outside the repo; `tools/check_public.py` passes
- [ ] Lane rules, coordinator prompt, bug list, changelog created from `templates/`
- [ ] Owner decisions recorded: name, hosting, public/private, providers
- [ ] Source mod inventory (ports only): `docs/source-inventory.md`

## Phase 1: Tools
- [ ] Extractors for the source's archives (ports only)
- [ ] Table migration to D2R's formats (columns added, removed or renamed)
- [ ] String conversion to D2R's JSON
- [ ] ID mapping layer (source ids to D2R ids)
- [ ] Validation: `python -m modkit build <project>` reports the baseline

## Phase 2: Data
- [ ] Item types, items, affixes, uniques, sets, runewords
- [ ] Skills, missiles, states, sounds
- [ ] Monsters, levels, areas, drops
- [ ] Cube recipes, vendors, quests (data part)
- [ ] 0 validation errors; warnings reviewed
- [ ] First boot into the character screen

## Phase 3: Engine triage
- [ ] List every code change the source (or the design) needs that data cannot do
- [ ] Mark each one: `data can do it` / `plugin needed` / `D2R already has it` / `drop`
- [ ] Record unportable gaps with a reason in `docs/unported.md` (ports)

## Phase 4: Plugin
- [ ] Plugin skeleton builds and loads
- [ ] Every hook checks the game build and the expected bytes
- [ ] Each plugin part has an off switch and a line in the status console command
- [ ] A game check per part; the targeted-test map is up to date
- [ ] Crash-safety review of every hook

## Phase 5: Graphics
- [ ] HD first with the closest vanilla stand-ins
- [ ] Icons, item pictures, missile and monster models as needed
- [ ] Custom art last
- [ ] Classic mode later, if at all

## Phase 6: Release
- [ ] Release package builds; installs like a player's install on a slot
- [ ] Installer/downloader, with game and loader version checks
- [ ] Hosting set up; upload verified
- [ ] Changelog, release pages
- [ ] Public repository (only with the owner's approval)

## Milestone tests
Full game-check suite, one game at a time, at the end of each phase: `<command>`.

## Decisions
| Date | Decision | Why |
|---|---|---|
| | | |
