# Documentation map

Where to read about each part of d2rAI-toolkit. New here? Start with the [main README](../README.md), then
the table below.

## Start here

| I want to... | Read |
|---|---|
| Install the toolkit and build my first mod | [d2r-mod-setup skill](../.claude/skills/d2r-mod-setup/SKILL.md) |
| Know what the agent is told to do (and not do) | [CLAUDE.md](../CLAUDE.md) |
| See a small mod that builds with 0 errors | [examples/showcase](../examples/showcase/) |
| Look up a patch operation | [knowledge/modkit-ops.md](../knowledge/modkit-ops.md) |

## Running a project

| Guide | What it covers |
|---|---|
| [ai-workflow.md](ai-workflow.md) | Running a mod project with AI agents: coordinator and lanes, model choice, game slots, the bug list, releases, safety, owner updates, failure modes |
| [testing.md](testing.md) | Testing in the real game without a human: game slots and their queue, game control without real input, named checks, release-like installs |
| [release.md](release.md) | Packaging and publishing: 7z and manifest, content-addressed objects, delta upload, server ingest, CDN mirror, release pages, `release.sh` |
| [distribution.md](distribution.md) | Where players get the files: your own host + CDN + downloader, GitHub Releases, Nexus Mods (archive layout, uploads, API limits, Vortex), and combinations, with a comparison table |
| [downloader.md](downloader.md) | The player-facing installer and updater: what it does, building your own from one config header |
| [../templates/](../templates/) | Fill-in files: coordinator prompt, lane rules, lane prompt, `bugs.md`, `PLAN.md`, `CHANGELOG.md`, owner update |
| [../tools/publish_mirror.md](../tools/publish_mirror.md) | Publishing a scrubbed, squashed public copy of a private repo |

## How D2R works

The [knowledge base](../knowledge/README.md) is the reference for the engine and its files. Read
[engine-limits.md](../knowledge/engine-limits.md) before you design anything big.

| Area | Page |
|---|---|
| Limits the engine hard-codes | [engine-limits.md](../knowledge/engine-limits.md) |
| Planning a new game system (data vs plugin, hooks, state, UI) | [new-game-systems.md](../knowledge/new-game-systems.md) |
| Tables (`.txt`) | [tables.md](../knowledge/tables.md) |
| Strings | [strings.md](../knowledge/strings.md) |
| HD art, sprites, animations | [hd-graphics.md](../knowledge/hd-graphics.md) |
| HD UI layouts | [ui-layouts.md](../knowledge/ui-layouts.md) |
| Saves | [saves.md](../knowledge/saves.md) |
| D2RLoader on players' machines | [d2rloader.md](../knowledge/d2rloader.md) |
| Porting a classic mod | [porting-classic-mods.md](../knowledge/porting-classic-mods.md) |
| Reverse engineering and hooks | [engine-re.md](../knowledge/engine-re.md) |
| Addresses for build 93847 | [d2r-93847-addresses.md](../knowledge/d2r-93847-addresses.md) |
| Symptoms, causes, logs | [debugging.md](../knowledge/debugging.md) |
| Player crash reports | [crash-triage.md](../knowledge/crash-triage.md) |

## Engine plugins

| Read | For |
|---|---|
| [plugin-template/README.md](../plugin-template/README.md) | The D2RLoader plugin skeleton: build, guards, off-switch, status command, examples |
| [new-game-systems.md](../knowledge/new-game-systems.md) | Planning a whole new system (for example a mapping system) as data plus plugin parts |
| [d2r-engine skill](../.claude/skills/d2r-engine/SKILL.md) | When to write a plugin, and how to find the code with Ghidra |

## Contributing to the toolkit

[CONTRIBUTING.md](../CONTRIBUTING.md) · [SECURITY.md](../SECURITY.md) · [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)

## Images

[`images/`](images/) holds the original artwork used in these docs (SVG, MIT like the rest of the repo). It has no
game art or screenshots.
