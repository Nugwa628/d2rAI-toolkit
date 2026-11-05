# D2RLoader: facts and traps

What a mod that ships through D2RLoader 1.3.1 (PluginSDK 0.3.0) runs into on players' machines. Seen in a large
overhaul released to players. Plugin API details: [engine-re.md](engine-re.md) and
`third_party/PluginSDK`; feature recipes built on these services: [plugin-techniques.md](plugin-techniques.md).

## How D2RLoader picks the mod

| Fact | Detail |
|---|---|
| `D2RLoader.exe` with no arguments reads `<game>\d2rloader\config\d2rloader.toml`, section `[d2rloader]` | It starts D2R with `<launch_arguments> -mod <default_mod>`. `-mod` is not allowed inside `launch_arguments`. The log shows the command line |
| `default_mod` is set in D2RLoader's own options | Gear on the character screen, Default Mod, Apply. It writes the game-wide file |
| A mod's own `mods\<Mod>\d2rloader\config\d2rloader.toml` | Overrides only the `[d2rcore.*]` settings (save format, stash tabs, ...). It **cannot choose the mod** |
| `-txt` is not needed with 1.3.1 | D2RLoader compiles the mod's `.txt` tables itself ("Compiled N tables from TXT"). Shipping a `.bin` switches it to a "custom BIN set" that must be complete |

**Trap: `default_mod` names another mod.** A player with an old or renamed mod folder in `default_mod` runs that
folder, not what your installer just wrote. Your new build looks broken ("the fix didn't work"). Your installer or
plugin should read `default_mod` and warn when it is not your mod. If your installer offers to change it, change
only that key (keep comments, other keys, line endings, BOM), ask first, and never while the game runs (D2RLoader
rewrites the file).

## The first start builds a cache

The first start after installing or updating D2RLoader or D2R builds the **base compiler snapshot**
(`<game>\d2rloader\data\compiler\<version>\`). The log says "Preparing base compiler snapshot ... because no valid
snapshot exists". On that start the sound step compiles **only D2R's own sounds table**, not the mod's (with or
without `-txt`). Every mod sound name then resolves to -1:

- all new sounds are silent;
- D2R's client sound readers do not skip a bad id: a monster whose sound is -1 can crash the client when it comes
  into view.

Later starts log "Validated the current base compiler snapshot" and load the mod's sounds. The fix is a restart.
Make your plugin check the loaded sounds row count after each table load (D2RLoader's lifecycle service,
`DataTablesLoaded`) and tell the player to restart when it is lower than the mod's
(`plugin-template/src/sound_guard.cpp`). Put "start the game twice the first time" in your install notes.

## Other plugins

D2RLoader loads the active mod's plugins (`mods\<Mod>\d2rloader\plugins\`) first, then the game-wide ones
(`<game>\d2rloader\plugins\`), which load with **every** mod.

| Trap | What happens | What to do |
|---|---|---|
| A stray copy of your own plugin in the game-wide folder (an old manual install, or an old build without `ModScopedOnly`) or in another mod's folder | Two copies run, or the old one runs | Detect it at load and warn (log, screen, status command) |
| Another mod's gameplay plugin that hooks the same code, or rewrites tooltips, item spells, vendors | Wrong text, crashes that you can't reproduce | **Don't make your plugin compatible with them.** Detect and name them in your log and status command; tell players to disable them for your mod. If players want what they offer, add it to your mod |
| The game-wide plugins load after yours | A check at your load sees none of them | Check again later (a one-shot thread after ~15 s, and in your status command) |

How to detect: list the process's modules (`CreateToolhelp32Snapshot` / `Module32First`) and keep those whose path
contains `\d2rloader\plugins\`; ask each for `D2RLoaderGetPluginInfo` to read its `id`. d2rloader.log's `[PLUGIN]`
lines list them too. See `plugin-template/src/env_guard.cpp`.

## Versions and builds

| Fact | Detail |
|---|---|
| **Check the game version (major.minor, e.g. `3.3`), never a build number**, in installers and requirement checks | D2R ships the same program under different build numbers (advice from D2RLoader's author) |
| Steam and Battle.net builds | Steam's 3.3 build 94995 ran the **same unpacked image** (PE timestamp `0x6ab3782c`) as Battle.net's 93847. The packed `D2R.exe` files differ on disk, so file hashes and build numbers differ |
| What a plugin checks | The image itself: the PE `TimeDateStamp` of `context->exeBase` (plus the entry bytes of every hook). That is the real "same code" test. `context->buildVersion` / `buildName` are labels for the log |
| `context->buildVersion` | e.g. `3.3.94995` on Steam. Print it in your log and status command: it is the first thing to read in a player's log |

## Files D2RLoader keeps

| File | What it is | Trap |
|---|---|---|
| `<game>\d2rloader\logs\d2rloader.log` | Loader log: plugins, table compile, asserts after "startup complete" | Ask players for it with every crash |
| `<game>\d2rloader\crashes\` | Crash reports (`d2r-crash-report ...`, `d2r-lastresort-crash-report (<date> UTC pid-N).log`) | See [crash-triage.md](crash-triage.md) |
| `<game>\d2rloader\config\input-bindings.toml` | The key of every action a plugin registers, stored **the first time** it is registered (`primary_key = <virtual-key code>`) | Changing a default key in a new plugin version does **not** reach players who ran the old one. Your installer must drop the stale entry, or tell players to rebind |
| `<game>\d2rloader\data\compiler\<version>\` | Table compiler snapshot | Missing = the next start is a cache-building start |
| `<game>\mods\<Mod>\d2rloader\logs\` | Your plugin's own log (`context->pluginLogPath`), written by `LogInfo` / `LogWarn` / `LogError` | Ask players for it too. Write one line per part at load, your version, D2R's version, the image stamp, the mod and your DLL's path |

## The plugin's own log: what to put in it

A player's log is often all you get. Make it answer the first questions without a reply:

- `<plugin> <version> loaded (mod <Mod>, D2R <buildVersion>, image 0x<stamp>, file <path>)`
- one line per part: `on` / `off: <why>` / `partial: <what failed>`
- the off-switch words in effect
- the other plugins (after they have loaded)
- warnings that point at the fix ("Close the game and start it again")
- before risky actions (spawning a rare monster, opening a portal), one line saying what is about to happen, so a
  log that ends there shows it

Keep it short in normal play: count repeated events and log the 1st, 100th, 10,000th.

## Stash settings

| What | Value (D2RLoader 1.3.1) | Confidence |
|-|-|-|
| More shared stash tabs | `add_shared_tabs = N` under `[d2rcore.stash]` in the mod's `d2rloader.toml`: D2R's 3 shared tabs + N. With 7, items created into tabs 0-9 landed, tab 10 was refused | observed |
| Installer and the key | write it only when the player has not set it by hand | |
| Extra personal stash page | D2RLoader has one custom player page (logical id `stash2`, item page 6, 14 x 14 in one port). Nothing shows it: a plugin panel in the left gameplay slot over the stock stash (`BankExpansionLayout`) with page arrows that send `PanelManager` messages did ([ui-layouts.md](ui-layouts.md)) | observed |
| A page registered through the inventory service | saved with the character; a character with items there refuses to load without the plugin ([saves.md](saves.md)) | observed |

## Threads and SDK services

| Rule | Why |
|-|-|
| Game (server) work runs inside the server frame: queue it from a frame hook (after the game-frame function) | Units, rooms and stat lists change only there; calls from another thread race the frame |
| The UI thread must **never wait** for a game-thread job | In single player the server frames are driven from the UI thread: waiting deadlocks the game. Queue the job and read the answer on a later frame |
| UI work (open a panel, press a panel button, reveal the automap, run a console line) goes to the UI thread through the thread service | Panel and automap code expect the UI thread |
| Overlay drawing: a frame callback in the after-game-UI phase, client side | Draws on top of the HUD; lines, rectangles and text only |
| A UI message listener sees the stock panel messages (a panel opening or closing) | Use it to know whether, say, the cube panel is open before acting on it |
| The widget service can send stock UI messages (`PanelManager:OpenPanel:<root>`) and read or hide widgets | Same effect as a layout button |
| Item service transactions | Create, list and move items between pages in one transaction; a tab beyond the configured count is refused |
| `ExecuteConsoleCommand` (D2RCore) runs a console line on the UI thread | Output of D2R's own commands and cheats goes only to the console panel: a plugin cannot read it back |
| Every called (not hooked) game function: check its first bytes before the first call | A mismatch switches that feature off with a log line instead of jumping into other code |

## Input actions

Actions registered through the input service appear in D2R's Controls menu under the plugin's category and can be
rebound; the first registration stores the key ([Files D2RLoader keeps](#files-d2rloader-keeps)). Choose defaults
D2R does not use: in 93847 **R** is run/walk, **C** the character screen and **V** another stock action, so a plugin
default on those presses two actions. X, L and K were free (observed).

## Table compile notes

- Without `-txt`, D2RLoader 1.3.1 still compiles the mod's tables: the log reads "Compiled N tables from TXT ...
  G game, M mod" (observed on a release install).
- `objtype.txt` is not compiled in either mode (observed): check the installed file itself, not the compile log.

## Plugin flags

The template declares `Shared | ModScopedOnly | NativeHooks`:

- **Shared**: runs on server and client; recorded in the character's plugin history; must match between host and
  client in TCP/IP games, so bump the version with every release.
- **ModScopedOnly**: loads only from the mod's plugin folder while that mod runs.
- **NativeHooks**: may patch game code.
