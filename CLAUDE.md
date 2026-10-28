# d2rAI-toolkit: instructions for Claude

This repo (d2rAI-toolkit; Python package and CLI `modkit`) is a toolkit for building Diablo II: Resurrected mods, up to full overhauls, from D2R's own files and
engine. You (Claude) are the main user: someone describes what they want in their mod, and you make it with these
tools. Target: D2R 3.3 (engine addresses for build 93847 only), D2RLoader 1.3.1 (PluginSDK 0.3.0). Windows.

## Where things are
- `modkit/`: the Python toolkit (`python -m modkit -h`). A mod is a project folder of JSON patches applied to the
  pristine game files; see knowledge/modkit-ops.md for every op.
- `knowledge/`: what D2R hard-codes and how its files work (limits, tables, strings, layouts, saves, HD graphics,
  reverse engineering, debugging). Read `knowledge/engine-limits.md` before designing anything big, and the topic
  file before working in an area. A whole new game system (for example a mapping system) starts with
  `knowledge/new-game-systems.md`: the data vs plugin split, the hooks, where state lives, the lanes.
- `docs/`: how to run a project. `docs/ai-workflow.md` is the method for a mod built with AI agents: a coordinator
  plus parallel lanes, model choice, game slots, the bug list, releases, safety.
- `templates/`: fill-in files for a new project: coordinator prompt, lane rules, lane prompt, `bugs.md`, `PLAN.md`,
  `CHANGELOG.md`, owner update.
- `testing/`: how to test a mod in the real game without a human: game slots (frozen copies of the install, queued
  with locks), named game checks driven through a plugin test harness, and release-like installs.
- `release/`: packaging and publishing: build the release package, a manifest, a fail-fast release script, release
  pages. `docs/distribution.md` compares serving the files from your own host, GitHub Releases and Nexus Mods
  (`package_release.py --nexus` writes a Nexus-ready archive).
- `downloader/`: the player-facing installer/updater source (installs the mod into the player's D2R `mods` folder,
  checks the game and loader versions, updates itself).
- `plugin-template/`: a D2RLoader plugin skeleton, for behaviour that data can't express.
- `tools/`: CascLib build, image dump and the Ghidra scripts for reverse engineering, and
  `tools/check_public.py`, the privacy scan (see rule 11).
- `examples/showcase/`: a small working mod project (a reworked class skill with a new icon and HD animation, an
  item skill with a new missile, a bigger stash).
- `.claude/skills/`: how to do each kind of job. Use them. Craft skills:
  - `d2r-mod-setup`: setup, build, install, run.
  - `d2r-skill`: skills.
  - `d2r-items`: items, affixes and drops.
  - `d2r-monsters`: monsters and areas.
  - `d2r-ui`: layouts, panels and grids.
  - `d2r-cube`: recipes.
  - `d2r-engine`: plugins, reverse engineering and new game systems (with knowledge/new-game-systems.md).
  - `d2r-debug`: crashes and wrong behaviour.
  - `d2r-crash`: a player's crash report (with knowledge/crash-triage.md).
  - `d2r-port`: porting a classic mod (with knowledge/porting-classic-mods.md).

  Project skills:
  - `d2r-coordinator`: plan and run a project with several agents (lanes, models, slots, bug list, review).
  - `d2r-release`: ship a version (bump, changelog, fail-fast publish, release pages, mirrors such as Nexus Mods).
  Testing, releases, distribution and the downloader are documented in `docs/` (testing.md, release.md,
  distribution.md, downloader.md).

## Rules
1. **Never commit game files.** `d2r-data/` (the cache of extracted game files), mod projects' `out/` folders and
   anything copied from the game stay out of git. Blizzard's files are only read from the user's install.
2. **Data before code.** Most of a mod is table rows, strings, layouts and sprites. Reach for a plugin only when no
   vanilla function or column can do it, and say so when it's needed.
3. **Start from vanilla.** New content clones the closest vanilla row (`modkit show`, `modkit functions`) and
   changes it. Don't invent column values or function numbers.
4. **Build and validate every change** (`python -m modkit build <project>`). Fix all errors; read the warnings.
   0 errors on a pristine build is the baseline.
5. **Respect the limits** in knowledge/engine-limits.md. The ones people hit:
   - 30 skills per class (saves);
   - string IDs below 65536 (modkit allocates them);
   - indexed tables are append-only;
   - formula syntax;
   - sequence skills need SQ plus an HD state;
   - new missiles need an HD model, new items need an HD picture entry.
6. **Install only with the game closed**; the user restarts the game after every install.
7. **The user tests in game; you diagnose from files.** Keep a TESTING.md in the mod project with a row per
   installed change (what, how, expected) and keep working; don't stop to ask for each test. On "it crashed", read
   the logs (d2r-debug skill) before asking anything. A player's crash report goes through the d2r-crash skill and
   knowledge/crash-triage.md. Porting a classic mod goes through the d2r-port skill and
   knowledge/porting-classic-mods.md.
8. **Saves**: back up first; edit only with `modkit save-set`; never hand-write items.
9. **Plugins are tied to one game image.** Engine addresses belong to build 93847 only (image timestamp
   `0x6ab3782c`); the plugin checks the timestamp and installs nothing on a mismatch. Re-find addresses after any
   game or D2RLoader update. Look for an existing D2RLoader hook on a function before you patch it. Version checks
   in installers and requirements use the game's major.minor (`3.3`), never a build number: Steam and Battle.net
   ship the same program under different builds.
   **Item types:** keep D2R-only itemtypes rows at D2R's numbers, and run
   `python plugin-template/tools/item_type_ids.py <project>/out/data/global/excel/itemtypes.txt` after every
   itemtypes change (knowledge/porting-classic-mods.md).
10. When you learn something new about the engine (a limit, an address, a fix), add it to `knowledge/`. Mark build-
    specific values with the build number.
11. **Keep the repo public-clean.** No real names, e-mails, hosts, IPs or tokens; no other projects' names. Keep a
    private-words list outside the repo (`~/.d2r-modkit/private_terms.txt`, one word per line) and run
    `python tools/check_public.py` before every commit. Commit with a noreply identity.
12. **Working with agents:** one bug or feature per lane (own branch, own worktree, PR); lanes never merge; the
    coordinator reviews, merges and releases; related bugs are merged in the bug list before fixing. Details:
    `docs/ai-workflow.md`.
13. **Ask the human before** anything public, paid or destructive (publishing, making a repo public, force-push,
    deleting saves or branches, spending money, touching their real game install). Never ask for secrets in chat.
