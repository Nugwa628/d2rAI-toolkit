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
| Steam and Battle.net builds | Steam's 3.3 build 94995 ran the **same image** (PE timestamp `0x6ab3782c`) as Battle.net's 93847. The reason: D2RLoader runs its own embedded image and neither store's `D2R.exe` runs at all ([below](#the-game-runs-from-the-loaders-own-image)). The `D2R.exe` files, hashes and build numbers still differ |
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

## Inside D2RLoader 1.3.1

How the loader starts the game and what sits between a plugin and the engine. Sources: a clean-room reconstruction
of the loader that starts the game next to the shipped one, a live address probe plugin, and static reading of D2RCore
1.3.1 (DLL timestamp `0x6ab37824`). Labels: **checked live on 93847**, **static** (read in D2RCore or the launcher,
not exercised in game), **unverified**. Game RVAs are for the image with timestamp `0x6ab3782c`. "Core" RVAs are
offsets in `D2RCore.dll` 1.3.1 and change with every loader release.

### The game runs from the loader's own image

| Fact | Detail | Confidence |
|-|-|-|
| The code is D2RLoader's, not the installed `D2R.exe` | `D2RLoader.exe` carries the 3.3 build 93847 image (timestamp `0x6ab3782c`) as a compressed resource and runs that. The installed `D2R.exe` is never started | checked live on 93847 |
| The install supplies data only | The loader reads the install's `.build.info` and CASC storage. The data must be a build the 93847 code can read | checked live |
| 3.4 build 95032 data | Starts, after a notice that the version is untested (3.3 is named the latest officially supported) | checked live |
| Data and build metadata from different builds | Fails: CASC cannot find the build configuration, the string tables fail, the loader stops at `MonType.txt` | checked live |
| `context->buildVersion` / `buildName` | Come from the install's data, so they name the data's build, not the code's | static |

This is why Steam's 94995 and Battle.net's 93847 ran the same image ([Versions and builds](#versions-and-builds)):
neither store's `D2R.exe` runs. A D2R patch changes the data your mod reads, not the code your plugin hooks; a new
D2RLoader release changes the code.

**Image layout.** `D2RLoader.exe` has image base `0x140000000`. Its section table starts with the 13 game sections at
the game's own RVAs, all with **zero raw size** (address space reserved, nothing on disk). The loader's bootstrap code,
data, TLS and resources follow from RVA `0x4001000`. (Static; the reconstruction rebuilt this layout and the game
started from it.)

| Game section | RVA | Virtual size | Notes |
|-|-|-|-|
| `.text` | `0x1000` | `0x1caf000` | game code |
| `.rdata` | `0x1cb0000` | `0x5df000` | starts with the IAT of the 271 KERNEL32 names the game imports from D2RCore (below) |
| `.data` | `0x228f000` | `0x1970000` | |
| `.pdata` | `0x3bff000` | `0x1ba000` | unwind table: the list of function starts for an image check |
| `_RDATA`, `.rodata`, `.rsrc`, `.reloc` | `0x3db9000` to `0x3e28400` | | |
| `.maho` | `0x3e29000` | `0x2a00` | the second D2RCore import descriptor (FirstThunk `0x3e2a0f8`): 471 game-logic functions by ordinal plus `D2RCore_InitializeNgxD3D12` by name. The thunk slots `0x3e2a000`-`0x3e2b8xx` of [sdk-gaps.md](sdk-gaps.md) X2 are these imports |
| `.seija` | `0x3e2c000` | `0x400` | unverified |
| `.koishi` | `0x3e2d000` | `0x1200` | the startup stub; its first call goes through the `VirtualProtect` slot `0x1cb0920` |
| `.d2rrel` | `0x3e2f000` | `0x4a000` | unverified |
| `.bspace` | `0x3e79000` | `0x188000` | uninitialized data |

After mapping, the in-memory PE header carries the game's data directories (imports, exceptions and the rest), except
the resource and TLS directories, which stay the loader's, and the debug directory, which is cleared. The entry point
is the game's, and the timestamp a plugin reads at `exeBase` is `0x6ab3782c` (static).

D2RLoader.exe's other resources: a default `d2rloader.toml` (9,235 bytes) and the launcher itself, a DLL mapped at
`0xd20000000` with one export, `D2RLoader_Run`. `d2rloader.mpq` holds 53 assets: the loader's UI layouts, artwork,
fonts and strings, a stash proxy definition and the desecrated-zone table. D2RCore overlays its `data/` entries on the
game's files, except `data/d2rloader/`.

### Start sequence

| Step | What happens | Confidence |
|-|-|-|
| 1 | The bootstrap maps the launcher DLL at `0xd20000000` and calls `D2RLoader_Run` with the rest of the command line. Its return value is the process exit code | static, rebuilt |
| 2 | The launcher reads `default_mod` and `launch_arguments`, loads `D2RCore.dll`, unpacks the game image and copies it into the reserved sections at `0x140000000` | static |
| 3 | It hooks `GetModuleHandle*`, `GetModuleFileName*`, `FreeLibrary` and `FreeLibraryAndExitThread`, so lookups of the main module answer the game image | static |
| 4 | It binds the game's imports itself (the OS loader never saw the image), sets the section protections and registers the game's `.pdata` | static, rebuilt |
| 5 | `D2RCore_PrepareGame` (ordinal 2042) gets the game base, the image size, the game's command line and the game's TLS directory RVA. D2RCore writes its own TLS index into the game's TLS directory, so **the game's thread-local data lives in D2RCore's TLS slot** (template `0x13b4` bytes) | static, rebuilt |
| 6 | `D2RCore_InitializeGame` (ordinal 2040, no arguments) binds D2RCore's 55,565 pointers into the game (its `.d2rsym` section), opens `d2rloader.mpq`, and sets up the log and the plugin host | static |
| 7 | The game's entry point runs. Plugins load on the game thread from inside the game's start (`LoadStartupPlugins`, core `0x4722c0`), before "Starting D2R initialization" in the log | static |

If either core call returns 0 the game does not start: "D2RCore could not prepare the game image or per-thread
storage." or "D2RCore initialization failed." The game reads the command line `<exe path> -w -mod <mod>` through
D2RCore's own `GetCommandLineA/W` (ordinals 2073 and 2074), not the real one.

**Slot rule** (checked live on 93847: 2452 of 2453 pairs, at load, at the menu and in game): D2RCore's `.data` holds
16-byte pairs `{pointer, rva}`. On disk the pointer is 0; from step 6 on it holds `0x140000000 + rva`. Reading the RVA at
slot+8 in the DLL file tells you which game function or global D2RCore uses there, without running anything. The
D2RCore slots below are read this way.

### D2RCore's exports

The shipped export table has 763 live exports in 2281 ordinal slots: 612 named (269 of them KERNEL32 forwarders) and
151 without a name. The game image binds 743 of them at start. (Static. [sdk-gaps.md](sdk-gaps.md) counts 764.)

| Group | Ordinals | Count | What it means for a plugin |
|-|-|-|-|
| Public SDK | 1-15, 2000-2007 | 22 | `Version` to `PrepareHostEnvironment`. The eight reimplementation exports live in D2RCore's `.d2rlapi` section. All names and ordinals match the SDK headers |
| Startup | 2040-2042 | 3 | `D2RCore_InitializeGame`, `D2RCore_InitializeNgxD3D12`, `D2RCore_PrepareGame`. For the launcher only |
| KERNEL32 forwarders | from 2008 | 269 | The game imports these Windows calls **from D2RCore**, not from KERNEL32. They forward unchanged, but D2RCore sits in the path |
| KERNEL32 overrides | 2073, 2074 | 2 | `GetCommandLineA/W`, implemented by D2RCore |
| Game logic moved into D2RCore | 1000-1373 | 316 named | Engine functions D2RLoader replaced with its own code. The game calls them through `.maho` |
| Unnamed | from 100 | 151 | Internal; the game imports 150 of them |

The named game-logic exports by subsystem:

| Subsystem | Count | Examples (ordinal) |
|-|-|-|
| Wide stats, effects and charges | 103 | `ReadWideUnitStat` (1243), `ReadWideUnitBaseStat` (1242), `SetWideUnitStat` (1249), `ReadWideMaxDurability` (1267), `ReadWideSkillLevel` (1285), `AssignAndSendWideItemStatUpdate` (1331); the effect registry 1303-1308; item charges 1321-1326 |
| Save files and item serialization | 40 | `DecodeItem` (1211), `SerializeItem` (1218), `WritePlayerStats` / `ReadPlayerStats` (1227 / 1228), `ReadSharedStashStreams` (1214) |
| Network, chat and session | 34 | `AddPlayerToGame` (1030), `RemoveClientFromGame` (1357), `ReceivePlayerCommonStatChange{Big,Medium,Small}` (1032-1034) and the senders (1035-1037), `SendClientGameplayPacket` (1134), `HandleServerControlPacket` (1031) |
| Excel and data tables | 33 | `LoadExcelTable` (1038), `CompileExcelFile` (1039), drop-row and cube-row helpers 1170-1180, namespaced strings 1200-1204 |
| Skills, missiles and combat | 28 | Bind Demon 1076-1079, `HitSpawnHerald` (1084), `ProcessMonsterSpecialEndDeath` (1096) |
| Items, inventory and tooltips | 28 | `GetItemName` (1006), grid widget activate / put / pick up 1048-1050, `BuildItemTooltipWithStatRanges` (1099), `ActivateBeltItem` (1371) |
| Rendering, UI and ImGui | 19 | `UpdateWidget` (1002), `FinalizeImGuiFrameWithPluginOverlays` (1372, where plugin overlays draw) |
| World, levels and units | 18 | outdoor level generation 1350-1354, `InitializeGameMonsterRegions` (1156), `FindGroundDropPosition` (1154) |
| Console, logging, frontend, other | 13 | `AppendConsoleMessage` (1004), `D2RLogCallback` (1108) |

**The wide stat and spawn packets.** D2RCore owns the stat engine and the messages that carry stats:
`SendWideItemStatUpdate` (1330), `ReceiveWideItemStatPacket` (1333), `SendAllWideStateStats` and
`SendChangedWideStateStats` (1334, 1335), `ReceiveWideStateStatPacket` (1336), and for the new-monster message
`ValidateWideMonsterSpawnPacket` (1337), `AppendWideMonsterStatSection` and `ReadWideMonsterStatSection` (1338, 1339).
Under 1.3.1 the stat parts of `0x3e`, `0xa8` / `0xaa` and `0xac` are written and read by the loader, not by the game's
own code ([network-messages.md](network-messages.md)). (Static: names from the export table.)

**Thunks** (checked live on 93847). Several game functions are only a `jmp [rip+x]` into D2RCore: `0x2f5020` (total
stat), `0x2f48c0` (base stat), `0x2f4b60` (max durability), `0x2f7d10` (set stat), `0x33d1e0` (skill level) and
`0x43eb30` (item stat update). Probed, the six reach exports 1243, 1242, 1267, 1249, 1285 and 1331. A hook on the game
RVA hooks the jump; the patch API cannot reach the body in D2RCore ([sdk-gaps.md](sdk-gaps.md) X2).

### The plugin channel in the game stream (`0xd0`)

`NetworkService` (id 6) sends its traffic inside the game's own TCP stream, both ways, as message `0xd0` followed by a
u16 length, an 18-byte header and the payload. (Static: D2RCore 1.3.1, rebuilt and compared in 40,000 generated cases;
no multiplayer capture.)

| Offset | Size | Field |
|-|-|-|
| 0 | 4 | `"D2RL"` |
| 4 | 1 | version, 1 |
| 5 | 1 | kind |
| 6 | 2 | zero |
| 8 | 2 | channel (the host's channel id) |
| 10 | 2 | message id |
| 12 | 4 | sequence |
| 16 | 2 | payload length; must equal the bytes that follow |

| Kind | Meaning | Rules |
|-|-|-|
| 1 | connect request | payload: id length, load scope, local channel id (u16), token (two u32), attempt (u32), then the plugin id |
| 2 | accept | client channel, host channel id, attempt. Header channel, message id and sequence must be 0 |
| 3 | reject | client channel, reason, 0, attempt. Same header rule |
| 4 | data | payload at most **224 bytes** |
| 5 | close | needs a channel; no message id, sequence or payload. A client reports Disconnected and does not answer |
| 6-11 | | pass the parser and are ignored |

- **Sequences** start at 1 and skip 0. A client drops host data whose sequence is not the next one expected.
- **Rate limits**: two token buckets per sender, both starting full: 32 messages a second (cap 64) and 8192 bytes a
  second (cap 16384). A message costs 1 message and its size + 18 bytes. Each channel has one pair for `sendToHost` and
  one for local `sendToClient`, each remote peer has its own, and a client limits incoming host data with a third pair.
  Over the limit a send answers 15 and incoming data is dropped silently.
- **Connect requests** on the host have a per-client bucket of 4 a second and 1024 bytes a second (byte cap 2048);
  extra requests are dropped silently. A client connect times out after 5 s; a retry within 0.5 s answers 6.
- **A payload over 224 bytes** answers 11. Plan for 224 bytes a message and about 30 messages a second.
- A malformed packet logs "Dropped a malformed private D2RLoader plugin-network packet." once per session.
- A session change (role, session id or transport) resets every channel: sequences, buckets and attempts clear,
  channels report Disconnected, and host channel ids restart at 1.
- A client drops every channel frame until it has joined. In offline single player injected frames were all dropped
  (checked live); the local host and client talk directly, not over the wire path.
- The session record is the game global `0x23dc530`, `+0x14` role: 0 at the menu and 1 in offline single player
  (checked live), 2 for a joining client (static).

Network ids that live in the game's own messages are in [network-messages.md](network-messages.md); what the service
cannot do (no player joined or left events) is in [sdk-gaps.md](sdk-gaps.md) L3.

### Plugin loading

| Step | Detail | Confidence |
|-|-|-|
| Discovery | `*.dll` in the mod's `d2rloader\plugins\`, then in the game-wide one. A mod can switch the game-wide scope off. `allow_mod_extensions` and `allow_global_extensions` in the loader's toml gate the two scopes | static |
| Order | `load_order = [...]` under `[d2rloader.plugins]` in the mod's toml: plugin ids, no duplicates, at most one `*`. Ids before `*` load first in list order, unlisted ones in discovery order, ids after `*` last. Unmatched ids are logged | static |
| Same id twice | Within one scope the first found wins; across scopes the mod's copy wins | static |
| Manifest | `RT_RCDATA` resource 1001, first u32 = the manifest ABI, accepted **2 to 4**. Without the resource the DLL is rejected before it runs. ABI 5 is rejected with "Plugin needs a newer D2RLoader version" | checked live on 93847 |
| Exports | `D2RLoaderGetPluginInfo` and `D2RLoaderLoadPlugin` are required, `D2RLoaderUnloadPlugin` is optional | static |
| `PluginInfo` | At least the first `0x34` bytes (through `flags`). ABI 3 and later must give a role. The game-wide scope rejects `ModScopedOnly` | static |
| Extras | An embedded config, resource 1002 (at most 1 MiB); a companion `d2rl-<plugin id>.mpq` (file or folder) beside the DLL, opened before the load call. A broken companion stops the load | static |
| Resources | 64 MiB per resource and 256 MiB per plugin through the Resource service | static |
| Plugin log | Created as an empty UTF-8 file with a byte order mark even if the plugin never logs; lines are tagged `[Plugin:<id>]` | checked live |

**Services** answer only their exact version. In 1.3.1: Lifecycle 1, Resource 2, CustomTable 3, Panel 4, Inventory 5,
Network 6, Input 7, DataTable 8, SharedEvent 9, Diagnostics 10, GameRule 11, Widget 12, Thread 13, Localization 14,
Item 15, Http 16, ItemInteraction 17, Encounter 18, Mutation 19, Overlay 20, PluginCommunication 21. All are version 1
except **Localization, version 2**. Results: 0 ok, 1 bad argument, 2 unknown id, 3 wrong version, 5 owner not active.
The loader finds the caller by the **address** of its context: pass the pointer you were given, never a copy.
(Static; queries for all 21 answered 0 live.)

**When a load fails or a plugin unloads** (checked live on 93847 with a function-table slot):

- Inline hooks made through the API are removed, and MutationService transactions are rolled back to their staged
  expected bytes, before the unload export runs.
- **Direct writes (`patchBytes`, `patchFill`, `patchWrite`, `patchRel32`) stay.** A plugin that writes a function
  pointer into a game table and then returns false from its load call leaves a pointer into a freed DLL. Write
  pointers through a MutationService transaction (one per slot), or undo them yourself.
- A declined or faulting load skips `D2RLoaderUnloadPlugin`.
- Unload goes newest first, reordered so a plugin unloads before the providers whose PluginCommunication services it
  holds. Plugins unload only on an orderly process detach, not when the game is killed.
- **A fault in a plugin callback during gameplay crashes the game on purpose** (a crash report message, then a write to
  address 1). Outside gameplay the plugin is switched off instead: its services stop, its hooks stay until unload.
  (Static.)
- JSON patches in `d2rloader\patches\` (`version` 1, a `patches` list of `rva` and `op`) are applied at start with an
  expected-byte check per operation. A failing file stops at that operation and keeps its earlier writes; nothing undoes
  them. (Static.)

### The multiplayer panel

D2RLoader 1.3.1 has its own multiplayer panel (refresh routine core `0x303430`): browse and host, a direct join with a
password, the players, the plugins, and a **fingerprint report**. The fingerprint lists each loaded plugin with its id,
config path and embedded-config size, plus a SHA-256 per startup JSON patch group (`d2rl.multiplayer` with an `id` and
a `role` of `shared`, `host` or `client`) over the bytes the group writes. A failed shared patch marks its group
incomplete. The fingerprint does not see `PatchBytes` calls a plugin makes at run time, so it cannot tell two builds of
your plugin apart: bump the version with every release ([Plugin flags](#plugin-flags)). (Static; the panel was not
exercised.)

### Offsets checked live

Checked live on 93847 with a probe plugin: offline single player, a level 1 character in the Rogue Encampment.

| What | Value | Through |
|-|-|-|
| first client | `game+0x138` | D2RCore slot `0x706da0` -> `0x484d70` (`mov rax,[rcx+0x138]`) |
| next client | `client+0x628` | slot `0x706db8` -> `0x484fd0` |
| client's unit id / unit / game / room | `client+0x270` / `+0x278` / `+0x2b0` / `+0x2c0` | the room equals `0x34b440(unit)` |
| unit type / id / mode / data | `unit+0` / `+8` / `+0xc` / `+0x10` | |
| unit's data flag | `unit+0x1bd`, the same value as `game+0x106` (3); also `0x34a0e0(unit)` | |
| difficulty | `game+0x104` | 0 in normal |
| unit -> game / player -> client | `0x48ff00` / `0x48fde0` | |
| find a unit | `0x48fe80(game, type, id)`; `(game, 0, 1)` gave the player | |
| unit -> level id | `0x34b440` (room), then `0x2efc10` (room) | 1 in the Rogue Encampment |
| inventory | `0x34a360(unit, file, line)`, magic `0x1020304`; `+8` owner, `+0x10` first item, `+0x4c` item count; item data `+0xb0` next, `+0xa0` owning inventory | |
| game and UI thread | D2RCore `0x7df084` / `0x7df080` (the same thread in single player). The server game is at D2RCore's TLS block `+0x1c38` on the game thread | |
| ImGui context | game `.data` `0x34f0e50`: `+0x10` display size, `+0x18` frame time | |
| widgets | `0x89f760(root, name)` finds a top-level panel, with the root at `0x3440170`; `0x855c90(widget, name)` finds a child; bytes `+0x50` enabled and `+0x51` visible, set by vtable `+0x48` / `+0x50`; `+0x70` rect (4 x i32) | |

Corrections from the probe: `0x846170` takes only the name (it loads the root itself and jumps to `0x89f760`), so
calling it as `(root, name)` returns null. `0x34e5f0` (set mode) writes `unit+0xc` and always returns 0.

### Pitfalls

| Trap | What happens | What to do | Confidence |
|-|-|-|-|
| The game image has its asserts compiled in | Engine calls with out-of-range ids raise `BC_ASSERT` (for example a skill id of 100000), and some stock asserts appear in normal play. An assert can open a dialog that stops the game | On test machines set `assert_dialog_mode = false` under `[d2rloader.developer]` in `<game>\d2rloader\config\d2rloader.toml`: asserts go to `d2rloader.log` and the game continues. Range-check ids before calling the engine | observed |
| The first key is lost | After the game is ready, the first key event does not reach the action dispatch | A test script sends a harmless key (Shift) first | checked live on 93847 |
| Posted keys skip the key entry hooks | Keys posted to the window reach plugin actions through export 159, not through the key entry D2RCore hooks | Hook the action dispatch, not the key entry | checked live |
| Started from a path not on `C:` | Startup stops at "Preparing frontend launch task" (stage 24 of 24) with an exception, before "D2R startup complete". The frontend checks state tied to the drive the program runs from | Start the loader from the `C:` drive. For a copy elsewhere, a directory junction on `C:` is enough: the game reads the path as started, before the junction resolves | checked live |
| The untested-version notice on 95032 | With 3.4 build 95032 data the loader shows a notice before the game starts; an automated start waits on it | Acknowledge it once by hand; the answer is kept in `<game>\d2rloader\data\loader-state.json`. A test slot without that file sees the notice again | checked live |
| `context->buildVersion` on 95032 data | Reads 3.4 while the code is the 93847 image | Gate on the image timestamp, not the version label | static |
| Plugin callbacks during gameplay | An exception crashes the game on purpose | Catch everything inside your callbacks | static |
