# SDK gaps: where plugins still patch the game, and the service that would replace each patch

For the D2RLoader / Plugin SDK author. Every entry is a place where a real plugin had to hook, byte-patch, write a
function-table slot, swap an import or call an engine function by RVA, because no SDK service covered the need. The
goal it serves: plugins that only call services and never patch, so they stay compatible with each other and could
later run against a host that implements the same services without D2R.exe (for example a non-Windows game server).

Engine detail is not repeated here; the links point to the toolkit file that has it.

## What was checked

| Item | Version |
|-|-|
| Plugin SDK headers | **0.4.0** (plugin ABI 4), upstream `D2RLoader/PluginSDK` at commit `03a5af9` (2026-10-14, "Add getWidgetScreenRect") |
| SDK the plugins were built with | 0.3.0 (ABI 4). 0.4.0 adds `WidgetService` text / tooltip / sprite / rect / dropdown / toggle / slider calls and `getWidgetScreenRect`, `InputService` key listeners, `ItemService.getItemDetails` / `getItemTooltip` / `getItemPreviewTooltip` |
| D2RLoader / D2RCore | 1.3.1, D2RCore timestamp `0x6ab37824`; its export table (764 exports, private ordinals 1000-1373) was read for names only |
| Game image | D2R 3.3 build 93847, PE timestamp `0x6ab3782c`. Every RVA below is for this image only ([d2r-93847-addresses.md](d2r-93847-addresses.md)) |

Sources, without names: two overhaul-mod ports built as D2RLoader plugins (a large one and a second, smaller one), the
audit of what a classic-era overhaul's own engine DLL changed in the 1.13 engine (each such change is a need a plugin
will have on D2R), a survey made for a from-scratch server that groups about 90 of those hooks into one decision point
per rule, a buff HUD plugin (open source), a belt refill plugin (need only, from its documentation) and a few other
installed plugins (by name and version string only: a hook-chaining plugin, a performance-patch plugin, a controller
plugin, a charm inventory plugin, a potion pickup plugin).

What the plugins patch today, counted in their source:

| Plugin | Inline hooks | Byte patches | Checked scalar / slot writes | Raw writes outside the SDK | MutationService | Direct engine calls / reads |
|-|-|-|-|-|-|-|
| Large overhaul port | 96 call sites in 44 files (86 outside diagnostics) | 24 call sites (19 forward) | 25 (16 are 8-byte function-table slots) | 9 files: a packet-handler slot, an import slot, D2RCore's item-event table, D2RCore code, NPC tables | 2 files | `exeBase + RVA` in 66 files, about 150 call targets |
| Second overhaul port | 36 sites (about 40 hooks) | 22 sites (about 45 places) | 9 slot writes, 2 rel32 retargets | 7 (a widget vtable, D2RCore thunk slots when the loader refused the write) | 2 parts | 293 distinct addresses |
| Buff HUD plugin | 0 | 0 | 1 (chains a D2RCore bridge slot by compare-and-swap) | 0 | 0 | 6 functions, 4 structs read or written in place |
| Belt refill plugin | 2-4 on the belt routines | 0 | 0 | 0 | 0 | about 10 |

## How to read an entry

- **Need**: what the plugin wanted, in game terms.
- **Why the SDK can't**: the closest 0.4.0 service and what it lacks. If a service already covers part of it, the entry
  names the service and method and describes only the rest.
- **Workaround today**: the mechanism (inline hook, byte patch, table-slot write, import / thunk swap, direct call,
  struct read) and the build 93847 RVAs.
- **Risk**: what breaks (patch drift on a D2R update, a collision with another plugin or with D2RLoader, threading, works
  only for the host).
- **Service**: the suggested shape (see the vocabulary below), the thread it runs on and the side (host / authoritative
  server, local client, both).
- **Priority** and **Confidence** as below.

### Service vocabulary

The shapes are the ones the from-scratch server survey settled on, and they map onto how the SDK is already built
(listeners with a `structSize`, `Has<Service>Field`, handles not pointers):

| Shape | Meaning | Example in the SDK today |
|-|-|-|
| Event | observe only, after the fact | `MonsterDeathListener` |
| Decision point | an in/out struct pre-filled with the vanilla answer; a handler may change fields or veto with a reason; handlers run by priority then registration order, a veto stops the chain | `EncounterService.decideHerald` (one rule only) |
| Registry | a plugin claims an id (skill function, AI id, calc code) and gets called only for that id; the loader owns slot numbers, refuses double claims and keeps a safe default for empty ids | none |
| Query | read a fact through a handle, never a pointer | `GameRuleService.getMaxSockets` |
| Action | ask the authoritative side to change something; applied at a safe point of the frame | `ItemService.executeTransaction` |

Two rules apply to every suggestion: arguments are handles and plain values (unit handle, stat id, frame number), never
native pointers, so a non-D2R host can implement the same table; and every callback names its thread (game = the
authoritative server frame on the host or in single player, UI = the local client).

### Priority

| Priority | Meaning |
|-|-|
| P1 | Several plugins need it, or the workaround writes into D2RLoader's own memory, or two plugins already collided on it |
| P2 | One major feature area of an overhaul needs it; today it costs one or more hooks per feature |
| P3 | Narrow, one mod's rule, or data / configuration could serve it |

### Confidence

Same labels as the rest of the toolkit: **checked** (worked in game in a port, usually with a named game check),
**observed** (seen once in one setup), **static only** (read in the disassembly, not run), **unverified**. SDK
coverage statements were checked against the 0.4.0 headers and README.

## Summary

SDK column: **none** = no service touches it; **partial** = a service covers part of it (named in the entry);
**covered** = a service exists and the entry lists only what is left.

| Id | Need | Workaround today | Priority | SDK |
|-|-|-|-|-|
| X1 | Several plugins on one engine function | inline hook, bridge-slot compare-and-swap | P1 | partial |
| X2 | Plugins writing into D2RCore memory and thunk slots | raw write, slot write | P1 | none |
| X3 | Calling engine functions without RVAs | direct call by RVA | P2 | partial |
| X4 | Engine asserts and crash guards | byte patch, entry hook, stub slots | P2 | none |
| X5 | Knowing which plugins are loaded and what they claim | module walk | P3 | partial |
| X6 | A host-neutral service contract | design | P2 | partial |
| L1 | Server frame tick and frame-based timers | inline hook `0x406550`, re-queued game callbacks | P1 | none |
| L2 | Game created / destroyed on the authoritative side | inline hook `0x486be0` | P1 | partial |
| L3 | Player joined / left on the host | frame walk | P2 | partial |
| L4 | Session and identity queries | struct reads, network-table probe | P1 | partial |
| L5 | Save events and per-character plugin data | log scraping | P2 | partial |
| L6 | Unit death and player death rules | inline hooks | P2 | partial |
| T1 | Read and write unit stats | direct call of D2RCore stat thunks | P1 | none |
| T2 | State queries and state events | bridge-slot chain, direct call, struct read | P1 | none |
| T3 | Apply a timed state / stat list | direct calls | P2 | none |
| S1 | Skill start / do functions (server and client) | function-table slot write | P1 | none |
| S2 | Missile functions and empty-slot crashes | function-table slot write, stubs | P1 | none |
| S3 | Calc codes | inline hook | P2 | none |
| S4 | Mana cost and affordability | call-site hook with own stub | P2 | none |
| S5 | Cooldowns | inline hooks, server and client | P2 | none |
| S6 | Skill usability and recast | inline hook, byte patch | P2 | none |
| S7 | Skill damage formulas | inline hooks | P2 | none |
| S8 | Hard-coded skill and state behaviour | inline hook, rel32, byte patch | P3 | none |
| S9 | Missile collision types above 8 | relocated table, instruction patches | P3 | partial |
| C1 | Damage pipeline decision points | inline hooks, clamp patches | P1 | none |
| C2 | Experience award | inline hooks, immediate patch | P2 | none |
| C3 | Regeneration and fixed stat amounts | inline hook, immediate patch | P2 | none |
| C4 | Hard-coded rule constants | byte patches | P2 | partial |
| I1 | Item event functions (on hit, on kill, ...) | write into D2RCore's handler table | P1 | none |
| I2 | Property functions | function-table slot write | P2 | none |
| I3 | Item use and item spells | inline hooks, table-slot write | P2 | partial |
| I4 | Item generation | inline hooks, byte patches | P2 | partial |
| I5 | Item requirements and equip rules | inline hook, D2RCore code patch | P2 | partial |
| I6 | Item move veto with feedback | inline hook | P2 | partial |
| I7 | Cube recipes | inline hooks | P2 | none |
| I8 | Drops and treasure classes | direct calls, byte patch | P2 | partial |
| I9 | Runewords | inline hooks | P3 | none |
| I10 | Vendors and gambling | byte patch, inline hook | P3 | none |
| I11 | Item level above 99 and stack rules | byte patch | P3 | partial |
| M1 | Monster AI ids | getter hook, table-slot write | P2 | none |
| M2 | Monster creation | inline hook, byte patches | P2 | partial |
| M3 | Spawning units | direct calls | P2 | partial |
| M4 | Hireling rules | inline hooks, NPC table write | P3 | none |
| W1 | Quest events and rewards | about 14 handler hooks | P2 | partial |
| W2 | NPC menu lines and actions | client and server hooks, record writes | P2 | none |
| W3 | NPC service and town tables | byte writes | P3 | none |
| W4 | Level entry and portal veto | inline hooks | P2 | partial |
| W5 | Waypoints beyond the stock panel | inline hook, relocated table | P3 | none |
| W6 | Travel and progress actions | direct calls, console | P3 | partial |
| W7 | Portals to levels above 255 | struct write | P3 | partial |
| A1 | Automap reveal, markers and labels | direct calls, inline hooks | P2 | none |
| A2 | Level types above 36 on the automap | inline hook | P3 | none |
| A3 | Map memory for layers above 99 | data only | P3 | none |
| V1 | More stash pages and tabs | MutationService byte patches, vtable writes | P2 | partial |
| V2 | Batch moves and sorting | not built | P3 | partial |
| V3 | Belt | inline hooks, direct calls | P2 | partial |
| V4 | Inventory change events | polling | P2 | none |
| U1 | Extending stock panels | inline hooks, vtable slot | P2 | partial |
| U2 | Native widget behaviour and art | raw widget writes, vtable wrap | P2 | partial |
| U3 | Overlay images | filled rectangles | P2 | partial |
| U4 | Tooltip and text builders | inline hooks, thunk swaps | P2 | partial |
| U5 | Message-log lines and notices | chat packet | P2 | none |
| U6 | Input and controller | window subclass, import swaps | P3 | partial |
| U7 | Unit name colours and hover text | inline hooks | P3 | none |
| U8 | Plugin settings page, panel count | none | P3 | none |
| G1 | A string id for a plugin key | import-slot swap | P1 | partial |
| G2 | String lookup fallbacks | thunk-slot swap | P3 | partial |
| D1 | Changing table rows at load | raw write after load | P2 | partial |
| D2 | Typed columns of built-in tables | heuristic offsets | P2 | partial |
| D3 | Loader table snapshots out of step with the mod | crash guards | P2 | none |
| N1 | Plugin messages between host and client | packet-handler slot swap | P1 | covered |
| N2 | Server-only facts on a joining client | none possible | P1 | none |
| N3 | Filter or hold a client request | handler hook | P2 | none |
| N4 | Game-side work on a joining client | none possible | P2 | partial |
| R1 | Renderer caps | byte patches | P3 | none |

Totals: 75 gaps, 14 P1, 42 P2, 19 P3. SDK status: 38 none, 36 partial, 1 covered.

### The ten that remove the most patches

1. **X1** hook chaining: the loader takes one inline hook per address, so plugins collide or build their own chains on
   D2RCore's bridge slots.
2. **L1** server frame tick: one hook on `0x406550` fans out to six unrelated features in one port; a HUD re-queues a
   game callback every frame to fake it.
3. **T1 / T2** stat and state access: every gameplay plugin calls D2RCore's stat thunks by RVA, and the buff HUD chains
   `STATLIST` posts through a D2RCore bridge slot.
4. **S1** skill function registry: free slots of D2R's skill tables are claimed by `PatchWriteU64`; two plugins or the
   next D2R patch take the same slot.
5. **S2** missile function registry and a null check in the dispatchers.
6. **I1** item event registration: the only workaround that writes into D2RCore's private table.
7. **C1** damage decision points: the per-hit step is already wrapped by D2RLoader, so a plugin hook there sits on top
   of the loader's.
8. **N2** server facts on joiners: state end frames, cooldowns and non-saved stats never reach a joining client.
9. **G1** register a string id for a key: done today by swapping D2RCore's string-lookup import slot.
10. **L2** game lifetime on the authoritative side: the real destroy function cannot be hooked.

## Infrastructure and coexistence

### X1. Several plugins on one engine function

- **Need**: two plugins (or a plugin and D2RLoader) both want to run code at the same engine function: the server
  frame, an item requirement test, a sound lookup, the free-belt-slot routine, the stat-list post.
- **Why the SDK can't**: `MutationService.stageInlineHook` and `PluginContext::InstallInlineHook` take one hook per
  address; a second one is refused ("could not install a Native Hook at RVA ..."), and a transaction may not overlap a
  loader-tracked patch. `DiagnosticsService.queryHookStatus` / `enumerateModificationRanges` can say who owns a range
  and whether `callThrough` works, which is the right base, but nothing lets a second plugin join the chain.
- **Workaround today**: one port runs its own dispatcher on `0x406550` and lets its parts register callbacks; another
  folds a second rule into an existing hook on `0x36bc50` instead of hooking it twice; the buff HUD reads D2RCore's
  bridge slot `0x3e2a208` (the `jmp [rip]` that D2RLoader put at `STATLIST` post `0x2f2cb0`) and writes its own
  pointer with `PatchWriteU64` using the old value as the expected value; a hook-chaining plugin exists only to share
  hook sites; a belt plugin dropped its hooks on the free-belt-slot routine after it broke a potion pickup plugin that
  hooks and integrity-checks the same routine; a controller plugin byte-checks functions other plugins hook.
- **Risk**: load order decides who wins; a plugin that verifies entry bytes fails when another one is first; unload of
  the middle link of a hand-made chain leaves a dangling pointer.
- **Service**: a loader-owned chain per site: `HookService.attach(site, before | after | replace, priority)` with
  ordered call-through, unlink on unload and `DiagnosticsService` reporting every link. Better still, each frequently
  hooked site becomes one of the events or decision points below, and raw hooks stay for research.
- **Priority**: P1. **Confidence**: checked (refusal and the belt / potion collision seen in game); the bridge-slot
  chain is static only.

### X2. Plugins writing into D2RCore memory and thunk slots

- **Need**: change behaviour that D2RLoader moved into D2RCore.dll: item event handlers, the required-level rule, the
  string lookup, the tooltip stat ranges, the stat engine.
- **Why the SDK can't**: the patch API targets D2R.exe only (rel32 targets must be inside the image), and D2RCore's
  code and private tables have no public entry.
- **Workaround today**: raw `VirtualProtect` writes into D2RCore's item-event handler table (core RVA `0x630260`, found
  through the `register` thunk slot `0x3e2a400`); a 3-byte NOP pair inside D2RCore's required-level function (core
  `+0x3d49ee`, `+0x3d49e2`); `PatchWriteU64` into thunk slots in `0x3e2a000-0x3e2b8xx` (string lookup `0x3e2b736`,
  stat-range tooltip `0x3e2af58`, a carry-once compare `0x3e2abb0`), with a raw write as fallback because some loader
  builds refuse that write.
- **Risk**: highest of all: any D2RLoader update moves the table or changes slot order; a plugin can double-fire with a
  handler the loader adds itself. One port also saw a loader thunk pass a too-narrow argument (`0x47a190`, slot
  `0x3e2a1b8`) and had to patch around it.
- **Service**: replace each case with its own service (I1, I5, G1, G2, U4, T1). Until then, publish the thunk-slot map
  as "do not write" and have `DiagnosticsService` report writes into it.
- **Priority**: P1. **Confidence**: checked (both ports run these writes; one is guarded by expected values).

### X3. Calling engine functions without RVAs

- **Need**: reach the engine's own routines (create missile, find stat list, skill record, warp, pick up) from plugin
  code without tying every call to one image.
- **Why the SDK can't**: the reimplementation exports 2000-2007 (`GetLevelIdFromUnit`, `GetLevelIdFromRoomContext`,
  `GetItemHandednessForUnit`, `CanEquipItemPair`, `GenerateUnitCastId`, `SetUnitCastId`, `TryGetMonumodConstant`,
  `CastAmplifyDamage`) are the right model but cover eight functions.
- **Workaround today**: about 150 call targets in one port, 293 addresses in the other; each port gates the whole plugin
  on the PE stamp plus byte probes of a few called functions, and one resolves its UI functions by byte signature.
- **Risk**: every D2R patch turns every feature off until the address table is redone; a call into code another plugin
  patched runs the patched code.
- **Service**: grow the 2000-2999 range with the functions plugins call most (the lists in T1, T3, M3, W6), each with a
  handle-based twin in a service so the same call works on a non-D2R host. Signature lookup is a stopgap, not a
  contract.
- **Priority**: P2. **Confidence**: checked.

### X4. Engine asserts and crash guards

- **Need**: content that the classic engine accepted makes D2R assert or crash: empty function-table slots called
  without a null check, sound ids past the loaded rows, a stale unit-translation event, a unit reassigned with no room,
  state 13 / 22 range asserts, an item-event skill resolver that asserts instead of returning 0.
- **Why the SDK can't**: no policy service; a plugin can only patch around each site.
- **Workaround today**: about 20 assert jump-overs and guards in one port (for example `0x44b152`, twelve sites from
  `0x21ee84` to `0x5787e1`, `0x34d935`, `0x56d38b`, `0x553ef4`, entry hooks `0x3402d0`, `0x589930`, `0x775fc0`,
  `0x12ab40`), five sound-lookup guards (`0xd8c70`, `0xd9180`, `0xd9030`, `0xd9e60`, `0xda360`) and no-op stubs in
  every empty missile slot (S2).
- **Risk**: each is a byte patch in shared code; the sound guards collide with a controller plugin that hooks
  `0x1a0780`.
- **Service**: null checks and bounds checks inside D2RLoader where D2R lacks them (missile and skill dispatchers, sound
  ids), and a small `EngineGuardService` that lets a mod opt into "return the default instead of asserting" for named
  asserts. These belong to the loader, not to each mod.
- **Priority**: P2. **Confidence**: checked (crashes reproduced, guards verified).

### X5. Knowing which plugins are loaded and what they claim

- **Need**: refuse to install a part when another plugin already owns the same table slots, and name conflicting
  plugins in the log ([d2rloader.md](d2rloader.md), Other plugins).
- **Why the SDK can't**: `PluginCommunicationService` finds a provider only by a service name it publishes; there is no
  list of loaded plugins or of their claims. `DiagnosticsService` names owners of byte ranges only.
- **Workaround today**: walk the process modules, call each DLL's `D2RLoaderGetPluginInfo`, re-check after about 15 s
  because game-wide plugins load later.
- **Risk**: a renamed DLL that loads later is noticed only after the parts installed.
- **Service**: `PluginRegistryService.enumerate` (id, version, scope, flags) plus the claims of the registries in this
  document; a "plugins finished loading" event.
- **Priority**: P3. **Confidence**: checked.

### X6. A host-neutral service contract

- **Need**: the same plugin logic running under D2RLoader on Windows and under a server that implements the services
  itself.
- **Why the SDK can't**: most services are already handle-based and versioned by size, which is what a second host
  needs. The exceptions take native pointers: `ItemService.editNativeItem`, the reimplementation exports, the console
  command callback's `D2R::Game::Client*`, and the panel / widget services assume D2R's widget tree.
- **Workaround today**: the ports split each rule into an engine-free logic header and a thin hook that reads the game
  into plain integers, calls the logic and writes back; game actions go through a struct of callbacks. That split is
  what services should formalise.
- **Service**: mark each service as gameplay (host-neutral, handles only) or presentation (client UI); keep gameplay
  services free of native pointers; let a host advertise which services it implements through the existing
  `QueryService` result.
- **Priority**: P2. **Confidence**: static only (design reading of the headers).

## Lifecycle and events

### L1. Server frame tick and frame-based timers

- **Need**: run plugin rules once per server frame with the frame number: a periodic spawn roll, travel rules,
  flushing held notices, timed challenges, buff timer refresh, a test harness.
- **Why the SDK can't**: `OverlayService.registerFrameCallback` is a client draw phase; `ThreadService.runOnGameThread`
  runs once per call; no listener runs every authoritative frame, and no service gives the frame counter.
- **Workaround today**: inline hook on the per-game frame `0x406550` (it increments `game+0x170`), shared by a home-made
  dispatcher because the loader refuses a second hook there; the buff HUD instead re-queues one game callback from inside
  itself every frame and reads `game+0x170` through `UNITS_GetGame` `0x48ff00`. See
  [engine-server.md](engine-server.md).
- **Risk**: the most-hooked function in both ports; any second plugin is refused (X1). A re-queued callback costs one
  frame of latency and stops on a session change.
- **Service**: `LifecycleService.registerGameFrameListener` (game thread, per game, payload: game handle, frame number,
  difficulty) and `TimerService.schedule(game, frames, callback)` in game frames, cancelled on game end.
- **Priority**: P1. **Confidence**: checked.

### L2. Game created / destroyed on the authoritative side

- **Need**: create per-game plugin state and drop it when the game ends: per-game craft flags, lockout clocks, kill
  stamps.
- **Why the SDK can't**: `GameJoined` / `GameLeft` (`LifecycleService` gameplay events) run on the UI thread for the
  local session; a TCP/IP host destroying a game it hosts, or a dedicated host, gets nothing.
- **Workaround today**: inline hook on the client-list teardown `0x486be0` inside game destroy; the real destroy
  `0x4057c0` cannot be detoured (a rip-relative load in its prologue) and create `0x4049d0` has no direct callers, so
  state is created lazily. One port keyed per-game flags by game pointer and leaked them across games.
- **Risk**: games live 300 s after the last client leaves ([engine-limits.md](engine-limits.md)); state keyed by
  pointer is reused by the next game at the same address.
- **Service**: `GameCreated` / `GameDestroyed` events on the game thread with a stable game id, plus a per-game
  storage slot the loader frees on destroy.
- **Priority**: P1. **Confidence**: checked (teardown hook); static only (destroy and create).

### L3. Player joined / left on the host

- **Need**: start per-player clocks, refuse a join, clean up when a joined player leaves.
- **Why the SDK can't**: `NetworkService` reports the plugin channel's connection states, not the game's players; no
  event fires when a joined player leaves.
- **Workaround today**: walk the game's client list from the frame hook and diff it.
- **Service**: `PlayerJoined` (decision point: may refuse with a reason) and `PlayerLeft` events, game thread, host.
- **Priority**: P2. **Confidence**: observed.

### L4. Session and identity queries

- **Need**: is this single player, the host or a joining client; which unit is the real local player; the difficulty
  and current level on the client; HD or classic graphics; the current game frame; the character's name or save folder.
- **Why the SDK can't**: `InventoryService.getLocalPlayer` gives a handle on the UI thread; `runOnGameThread` answering
  `Unavailable` is the only documented way to detect a joiner. The buff HUD found D2RLoader's shared-stash proxy (a
  player-shaped unit marked with state 186) in its stat-list hook and must filter it by hand.
- **Workaround today**: read the server game's difficulty bytes (`+0x104` / `+0x106`) from the client, read the HD flag
  `0x846210`, probe the system TCP table, treat "first player unit posted" as the local player.
- **Risk**: wrong player on a host with several players; joiners show stock text.
- **Service**: `SessionService.getInfo` (mode: single / host / joiner / dedicated; difficulty; current level; graphics
  mode; game frame; character name and save id) and `isRealPlayer(unit)`. UI and game thread.
- **Priority**: P1. **Confidence**: observed.

### L5. Save events and per-character plugin data

- **Need**: know when a character save failed or was cancelled; keep plugin data (flags, a timer, map memory, a
  personal page) with the character and roll it back with it.
- **Why the SDK can't**: custom player pages are saved for the plugin, but there is no save event, no pre-save veto (the
  README says so) and no per-character data blob.
- **Workaround today**: scrape `d2rloader.log` for "Character save failed"; keep flags in spare quest-record bits
  (quest 47 bit 0 in one port) or in plugin files keyed by character name.
- **Risk**: a spare quest bit can be taken by a later D2R quest; files keyed by name break on rename or copy.
- **Service**: `SaveService`: `BeforeSave` (decision, may veto), `AfterSave` (result), and a small keyed blob per
  plugin per character inside the `.d2rl` sidecar ([saves.md](saves.md)), loaded and saved with the character.
- **Priority**: P2. **Confidence**: checked (log scraping, quest bit).

### L6. Unit death and player death rules

- **Need**: react to any unit's death before loot (an on-death item event, explosions, a death report); change the
  player death penalty (no corpse, respawn wait, Hardcore converting to Softcore in single player).
- **Why the SDK can't**: `MonsterDeathListener` is observation-only, monsters only, and runs after the drop.
- **Workaround today**: inline hooks on the unit death function, the corpse creator `0x42c110`, the death penalty
  `0x424ac0`, the respawn request `0x4b6160` (held back for 5 s), the dead-mode handlers `0x42a5e0` / `0x42de40`;
  corpses already in saves removed by repeating the engine's unit-removal steps by hand.
- **Risk**: hand-copied removal steps break silently on a D2R patch.
- **Service**: `UnitDeath` event (any unit type, before drop, killer and skill, game thread) and a `PlayerDeathRules`
  decision point (drop corpse, gold and experience loss, respawn delay in frames, convert Hardcore) plus a
  `removeUnit` action.
- **Priority**: P2. **Confidence**: checked.

## Stats and states

### T1. Read and write unit stats

- **Need**: read a stat by id and layer (all layers of one stat), add or set a stat, from both game and UI code.
- **Why the SDK can't**: `ItemService.getItemDetails` copies an item's own stat lists; nothing reads a unit's stats.
- **Workaround today**: direct calls of `0x2f5020` (get), `0x2f7d10` (set), `0x2f48c0` (base), `0x2f5c60`, `0x2f34f0`,
  `0x2f7c00`, `0x2f5b80`, `0x2f5940` from 25+ files; they are `jmp [rip]` thunks into D2RCore's wide stat engine (the
  buff HUD reads the target out of slot `0x3e2a1b0` and calls it). See [units-stats.md](units-stats.md).
- **Risk**: thunk RVAs move with D2R; the target is D2RCore, which D2RLoader may rework.
- **Service**: `StatService.get(unit, stat, layer)`, `getAllLayers`, `add / set(unit, stat, layer, value, sourceId)` so
  a plugin can remove what it added. Game thread for writes; reads on the UI thread use the client copy.
- **Priority**: P1. **Confidence**: checked.

### T2. State queries and state events

- **Need**: does a unit have a state; its end frame, source skill and level; an event when a state is set, renewed or
  cleared. Used by buff HUDs, timed challenges, quest rules that clear buffs.
- **Why the SDK can't**: nothing about states.
- **Workaround today**: has-state `0x3351b0`; stat list for a state `0x2f5940` and its fields (`+0x1c` flags, `0x20` =
  curse; `+0x20` state; `+0x24` end frame as a float; `+0x28` source skill; `+0x2c` skill level); the buff HUD chains
  `STATLIST` post `0x2f2cb0` through D2RCore's bridge slot `0x3e2a208` to see states arrive, and still re-scans each
  frame because an in-place renewal does not post. See [units-stats.md](units-stats.md),
  [engine-limits.md](engine-limits.md) (States).
- **Risk**: X1 and X2 at once; the end frame exists only on the server unit (N2).
- **Service**: `StateService.has(unit, state)`, `get(unit, state)` -> {end frame, source skill, level, flags},
  `forEach(unit)`; `StateChanged` event (set / renewed / cleared, game thread on the host, plus a client-side variant
  fed by the state messages).
- **Priority**: P1. **Confidence**: checked (has-state, list fields); static only (renewal path).

### T3. Apply a timed state or stat list

- **Need**: start a timed buff with stats and an expiry callback, the way D2R's own start functions do.
- **Why the SDK can't**: no state action.
- **Workaround today**: repeat D2R's recipe from `0x569670`: stat list `0x2f7300`, state `0x2f72f0`, expiry callback
  `0x2f7a50`, attach `0x2f2cb0`, state bit `0x3354c0`, timed event `0x48b720`. See
  [skills-missiles.md](skills-missiles.md).
- **Service**: `StateService.apply(unit, state, frames, stats[], sourceSkill)` and `remove`, game thread, host.
- **Priority**: P2. **Confidence**: checked.

## Skills and missiles

### S1. Skill start / do functions (server and client)

- **Need**: new skill behaviour that no stock function number gives: multi-summon, ranged teleport with a pickup,
  layered auras, area damage, timed self-states, client visuals for them.
- **Why the SDK can't**: no skill-function registry; `GameRuleService` answers point allocation and the skill cap only.
- **Workaround today**: `PatchWriteU64` (expected 0) into free slots of the server do table `0x238ea00` (191 slots, D2R
  fills 0-171; slots 172-190 used), the server start table `0x238e710` (91 slots, D2R uses 0-70; slots 71-72), the
  client do table `0x235f7d0` (130 slots, bound `0x235f7c4`; slots 110-113), the client start table `0x235f590`; a
  wrapper around stock slot 27. The skill rows are renumbered in data to the claimed slots. See
  [skills-missiles.md](skills-missiles.md).
- **Risk**: a D2R patch that adds functions fills these slots first (the write is refused and the skill does nothing);
  two plugins claim the same free slot; the second port refuses to install when a known plugin writes the same tables.
- **Service**: `SkillFunctionRegistry.register(kind, callback)` -> a loader-assigned id that the mod's skills.txt refers
  to by name (`srvdofunc=plugin:<id>/<name>` resolved at load), for server do / start and client do / start. Callbacks
  on the game thread (server kinds) or UI thread (client kinds); the loader keeps the tables large enough.
- **Priority**: P1. **Confidence**: checked (casts verified in game).

### S2. Missile functions and empty-slot crashes

- **Need**: new missile hit and do behaviour (server and client); no crash when a table row names an empty slot.
- **Why the SDK can't**: no registry; the dispatchers check the bound but not null.
- **Workaround today**: `PatchWriteU64` into the server hit table `0x2391030` (bound 71 at `0x2391404`), client hit
  `0x2358710` (bound 81 at `0x2358a64`), server do `0x2390e80`, client do `0x2358430` (bound 91 at `0x2358a60`), and a
  logging no-op stub written into every empty slot below the bound (up to 96 / 80 slots).
- **Risk**: if D2R raises a bound past the stubs, new empty slots crash again; if D2R fills a slot, the claim is refused.
- **Service**: `MissileFunctionRegistry` (server hit, server do, client hit, client do) by name, as S1; a null check in
  the four dispatchers inside D2RLoader (X4).
- **Priority**: P1. **Confidence**: checked.

### S3. Calc codes

- **Need**: skill and missile formula functions beyond D2R's codes (one mod uses codes 73-110: item stat lookups, skill
  points in a tab, ...).
- **Why the SDK can't**: `DataTableService` exposes the `SkillCalc` / `MissCalc` rows read-only; codes cannot be added.
- **Workaround today**: inline hooks on the calc-code evaluator `0x33b3b0` and the missile calc `0x3b9dc0`.
- **Risk**: both are hot paths; a second plugin is refused.
- **Service**: `CalcFunctionRegistry.register(name, callback(unit, skill, level, args) -> int)`, referred to by name in
  the formula text and resolved at table load. Runs on whichever side evaluates the formula, so it must be pure.
- **Priority**: P2. **Confidence**: checked (three codes still return 0 in one port).

### S4. Mana cost and affordability

- **Need**: a mod's mana cost formula with a "% mana cost" stat, free casting in town, formula-cost skills that test
  affordability from calc columns.
- **Why the SDK can't**: no skill-cost point.
- **Workaround today**: D2R's cost function `0x33aa00` has no caster argument and D2RLoader already relocates
  `0x436830`, so one port patches the server call site (`0x4369fb`, return `0x436a00`; client `0x218937`, return
  `0x21893c`) through its own stub that passes the caller's register as a fourth argument; affordability hooks
  `0x340900`.
- **Risk**: the most fragile hook in the port: it depends on two call sites inside other functions and on the loader's
  relocation.
- **Service**: `SkillCost` decision point (unit, skill, level -> mana cost, cooldown frames, cooldown group) used for
  both the charge and the affordability test, on both sides.
- **Priority**: P2. **Confidence**: checked.

### S5. Cooldowns

- **Need**: cooldown reduction by a stat, and skills of one group sharing a timer.
- **Why the SDK can't**: no cooldown point; `StatService` would only let a plugin rewrite the timer after the fact.
- **Workaround today**: inline hooks on the server cooldown start `0x439500` (state `0xb9` and stat 359 at layer =
  skill hold the end frame) and its client twin `0x217b90`.
- **Risk**: cooldowns are predicted on the client: no message carries them ([network-messages.md](network-messages.md)),
  so a joining client without the same rule shows the wrong timer.
- **Service**: part of `SkillCost` (S4), plus a query for the remaining cooldown of a skill on the client.
- **Priority**: P2. **Confidence**: checked (server); the client half was not reached by the test cast.

### S6. Skill usability and recast

- **Need**: restriction modes (needs a state, refuses a state), recasting a skill while its state runs.
- **Why the SDK can't**: `GameRuleService.canAllocateSkill` is about spending points, not using a skill.
- **Workaround today**: inline hook on the usability check; 13 NOPs over the recast guard `0x56f859`.
- **Service**: `SkillCanUse` decision point (unit, skill, level -> allowed, reason) and a `SkillCast` veto after the
  server checks.
- **Priority**: P2. **Confidence**: checked.

### S7. Skill damage formulas

- **Need**: synergy, static damage scaling and mastery formulas that differ from D2R's.
- **Why the SDK can't**: no damage-calc point.
- **Workaround today**: inline hooks on the shared skill-damage functions `0x3387d0`, `0x338340`, `0x3385b0`,
  `0x338160` and mastery `0x337f40` (code shared by client and server).
- **Service**: `SkillDamageCalc` decision point (unit, skill, level -> min / max per damage type), both sides.
- **Priority**: P2. **Confidence**: checked.

### S8. Hard-coded skill and state behaviour

- **Need**: classic-engine behaviour D2R changed: whirlwind hit rate (`0x56ac00`), a conversion length read from a
  different column (server do 51, rel32 at `0x57091b`), state 13 / 22 walk-step functions (`0x221650`, `0x578680`), pet
  placement and warp to owner (`0x471500`, `0x500a40`, `0x596d40`, AI think `0x5b7590`, `0x5b2b10`).
- **Why the SDK can't**: each is a constant or a branch inside a stock function.
- **Service**: mostly S1 (register a replacement function) plus C4 (named constants). No dedicated service.
- **Priority**: P3. **Confidence**: checked.

### S9. Missile collision types above 8

- **Need**: two more collision types (one mod gives them to 15 missiles).
- **Why the SDK can't**: D2R's missiles loader clamps `CollideType` to 8; the two 16-byte-row tables are fixed.
  `DataTablesLoadedListener` is the right moment, but there is nothing to extend.
- **Workaround today**: copy the server table `0x2391370` and client table `0x23589d0` into larger pages, re-point nine
  `lea` readers (`0x455aa6`, `0x457b7f`, `0x4621a7`, `0x4632b4`, `0x46627a`, `0x5378cc`, `0x1b52d6`, `0x1b5642`,
  `0x1b9fdf`) with `PatchBytes`, and write 9 back into the records (`+0x1ab`) after load.
- **Service**: loader-owned collision table with registered rows (mask and function type), and no clamp at load.
- **Priority**: P3. **Confidence**: checked.

## Combat and damage

### C1. Damage pipeline decision points

- **Need**: caps on avoid / dodge / evade, absorb stats, per-damage-type reduction after mitigation, a different max
  resist cap, a floor on a missile's second calc, a slow floor for players only.
- **Why the SDK can't**: no damage point.
- **Workaround today**: inline hooks on the avoid check `0x44ef70`, the absorb step `0x450670`, the per-type hit step
  `0x4523e0` (D2RLoader already wraps its call at `0x3e2b496`), the slow floor `0x449f40`; `PatchWriteU8` on the max
  resist clamps (`0x4524dd`, `0x14e7294`, `0x14f4686`, compare `0x14e728f`); `0x45edce` (`cmovl` to `cmovg`). See
  [combat-formulas.md](combat-formulas.md).
- **Risk**: sits on top of the loader's own wrapper; melee and missile damage reach the pipeline at two different
  places, and a hook in shared code sees derived totals.
- **Service**: `DamageAvoid` (result bits, modify), `DamageApply` (per landed hit, per type amounts before resistances,
  modify or veto; a veto must clear hit, knockback, leech and the rest), and named caps in C4. Game thread, host.
- **Priority**: P1. **Confidence**: checked.

### C2. Experience award

- **Need**: a level-gap penalty, full party experience without a split, flat quest experience.
- **Workaround today**: inline hooks on the penalty `0x44fef0`, the party iterator `0x506f90`, the act 5 quest
  experience `0x550c20`; `PatchWriteU32` of the party range `0x44fe7b`; direct call of add-experience `0x44f2e0`.
- **Why the SDK can't**: no experience point.
- **Service**: `ExperienceAward` decision point per recipient (base, level gap, party count -> amount), game thread.
  The most common rule in classic mods.
- **Priority**: P2. **Confidence**: checked (penalty); observed (party, one player).

### C3. Regeneration and fixed stat amounts

- **Need**: stamina recovery by mode, mana regeneration without the floor of 1, a drain instead of regeneration, the
  amount a potion or a quest tome gives.
- **Workaround today**: inline hooks replacing the stamina step `0x42e850` and the mana step `0x42e350`; `PatchBytes`
  on two immediates (`0x5806b5`, `0x5ed70d`).
- **Why the SDK can't**: no regeneration or item-use point.
- **Service**: `Regeneration` decision point (unit, kind, amount) and I3 for item amounts.
- **Priority**: P2. **Confidence**: checked.

### C4. Hard-coded rule constants

- **Need**: change a number D2R keeps in code: max resist, summon and hireling leash (six compares `0x5bec51`,
  `0x5bd29f`, `0x5bd474`, `0x5c1136`, `0x5c96aa`, `0x5ccb39`), a boss throne delay (`0x5c71e6`), gamble offer count
  (`0x541a7e`, 14 -> 32), party range, monster level in Normal (`0x543d32`, `0x543d44`).
- **Why the SDK can't**: `TryGetMonumodConstant` reads one table; nothing exposes code constants.
- **Workaround today**: one-byte or four-byte immediate patches.
- **Service**: a `RuleConstants` registry the loader routes these sites through (like the D2R constants already in
  data), set at load from the mod's data. Cheap per constant, removes many tiny patches.
- **Priority**: P2. **Confidence**: checked (leash), static only (throne).

## Items

### I1. Item event functions (on hit, on kill, on death)

- **Need**: itemstatcost event functions D2R lacks (attacker takes damage, cast on event, flat life on event, on-death
  effects).
- **Why the SDK can't**: the registry lives in D2RCore and has no public entry.
- **Workaround today**: overwrite D2RCore's handler table (core `0x630260`, slots 15 / 16 / 33 / 34) through the
  `register` thunk slot `0x3e2a400`; a death hook raises event 14 by hand.
- **Risk**: X2: breaks or double-fires on any D2RLoader build that moves the table or implements the ids itself.
- **Service**: `ItemEventRegistry.register(eventFunctionId or name, callback(unit, event, stat, layer, value, target,
  skill))`, plus a way to wrap or gate an existing id. Game thread, host.
- **Priority**: P1. **Confidence**: checked.

### I2. Property functions

- **Need**: property functions beyond D2R's (innate elemental damage, boss monster properties).
- **Workaround today**: `PatchWriteU64` into the property function table `0x2386ab0` (count 37 at `0x2386bd8`; D2R
  fills 1-25 and 36), slots 27-29.
- **Why the SDK can't**: no registry. `ItemService.createItem` accepts properties but cannot define a new function.
- **Service**: `PropertyFunctionRegistry` (apply / remove callbacks), as S1.
- **Priority**: P2. **Confidence**: checked.

### I3. Item use and item spells

- **Need**: usable items D2R has no spell for: orbs that open portals, dyes, keys, containers, a classic food item that
  casts a skill, potion amounts.
- **Why the SDK can't**: `ItemInteractionService` reports a client-side activation (UI thread) and can `Consume` it, but
  the effect must happen on the server and there is no server-side item-use point.
- **Workaround today**: inline hooks on the use / check dispatchers `0x581680` / `0x5817f0` and a flag write in the
  spell table `0x236fe30` (one port); slot writes into the check table `0x1d3ea30` and the use table `0x1d3ea90` (the
  other port); direct calls of create portal `0x432ce0`, free item `0x43ec10`.
- **Risk**: the spell table has 20 entries (16-19 empty); a plugin rewriting the same item spells collides.
- **Service**: `ItemUse` decision point on the server (player, item -> handled, consume) and an `ItemSpellRegistry` for
  new spell ids. Game thread, host.
- **Priority**: P2. **Confidence**: checked.

### I4. Item generation

- **Need**: ethereal chance by item level, sockets for every quality, automagic for all qualities, auto-identify,
  class-locked rolls, item colours.
- **Why the SDK can't**: `ItemService.createItem` / `editItem` / `augmentItemAffix` change an item the plugin makes or
  holds; nothing runs while D2R generates its own items. `GameRuleService.getMaxSockets` reads, it does not decide.
- **Workaround today**: inline hooks on the requirement check `0x36bc50`, the generator `0x442d60`, make-ethereal
  `0x3d3510`, socket roll `0x441fc0`, item colour `0x3710d0`, identify flag `0x36d8f0`; byte patches `0x44356a` (socket
  guard) and `0x44364c` (automagic jump table).
- **Service**: `ItemGenerate` decision point after quality is chosen (base, level, quality, source -> ethereal, sockets,
  identified, affixes, colour), with the item's start seed so rules stay deterministic.
- **Priority**: P2. **Confidence**: checked.

### I5. Item requirements and equip rules

- **Need**: class-locked items, mercenary weapon types, no +6 required-level penalty for other-class skills, a second
  copy of a carry-once charm in a custom page.
- **Why the SDK can't**: `CanEquipItemPair` (export 2003) answers one pair rule; the custom page item policy decides
  admission to a plugin page only; the required-level and carry-once rules sit in D2RCore.
- **Workaround today**: inline hook on the requirement test `0x36bc50` (two parts share one hook, X1); NOPs inside
  D2RCore's required-level code (X2); a carry-once test hook `0x473960` plus a D2RCore compare-thunk slot `0x3e2abb0`.
- **Service**: `ItemRequirement` decision point (unit or hireling, item -> allowed, reason, level requirement) and a
  documented carry-once policy per page.
- **Priority**: P2. **Confidence**: checked.

### I6. Item move veto with feedback

- **Need**: refuse a move or pickup (a carry limit for some uniques) and tell the player why.
- **Why the SDK can't**: page item policies cover plugin pages only; failed item requests get no reply from D2R, so a
  veto looks like lag.
- **Workaround today**: inline hooks on the container-move funnel `0x471e90` (all container packets) and ground pickup
  `0x471950`, plus a chat line.
- **Service**: `ItemMove` decision point (player, item, from, to -> allowed, reason) on the host, with the loader
  sending the refusal so the client stays in step.
- **Priority**: P2. **Confidence**: checked.

### I7. Cube recipes

- **Need**: more condition operators (D2R has 1-28; one mod uses 29-42), a second condition, gold cost, output effects
  (pay gold, set a player or game flag), a second `useitem` output.
- **Why the SDK can't**: `CubeMain` is readable (`DataTableService`), not extendable; D2RCore compiles and resizes cube
  rows itself (private `ResizeCubeRows`, `CompileCubeInputField`).
- **Workaround today**: inline hooks on the recipe search `0x529090`, the input matcher `0x529d50` and the executor
  `0x5269c0`; direct calls of duplicate `0x43d660`, place `0x471500`, send `0x535f60`.
- **Service**: `CubeConditionRegistry` (op id -> callback), `CubeMatch` decision point, `CubeDone` event with the
  outputs. `ItemService.executeTransaction` already does the atomic exchange part.
- **Priority**: P2. **Confidence**: checked.

### I8. Drops and treasure classes

- **Need**: extra drops for quest and bonus rewards, a different treasure class step in Normal, injected drops.
- **Why the SDK can't**: `MonsterDeathListener` runs after the drop and cannot add to it; `TreasureClasses` is
  read-only; D2RCore's `AppendTreasureClassItem` / `SetTreasureClassItemProbability` are private.
- **Workaround today**: direct calls of the treasure class executor `0x441300` and item creation `0x517530` /
  `0x43cd50`; a nine-byte NOP at `0x44138f`.
- **Service**: `TreasurePick` decision point (monster, killer, area level -> class, level step, extra classes) and
  `DropRolled` (list of rolled items before placement); a `dropTreasureClass(unit, class)` action.
- **Priority**: P2. **Confidence**: checked.

### I9. Runewords

- **Need**: more runeword properties than D2R applies, and a padded rune list.
- **Workaround today**: inline hooks on the runeword apply `0x3d44c0` and match `0x372260`.
- **Service**: `RunewordApply` decision point (item, row -> extra properties).
- **Priority**: P3. **Confidence**: static only (not tested in game).

### I10. Vendors and gambling

- **Need**: more gamble offers, a vendor item-level cap, extra magic copies, prices for superior items, a mercenary
  revive price.
- **Workaround today**: byte patch `0x541a7e`; inline hooks on vendor stock and on the revive price `0x3c1ad0`.
- **Service**: `VendorStock` decision point (offer count, item level cap, copies) and `Price` decision point (kind,
  item or hireling -> gold).
- **Priority**: P3. **Confidence**: checked.

### I11. Item level above 99 and stack rules

- **Need**: item levels up to 127 in saves; stackable consumables.
- **Why the SDK can't**: `GameRuleService.getTotalMaxStack` and `ItemService.splitStack` read and split stacks; use,
  cube and save paths assume D2R's rules.
- **Workaround today**: byte patch of the serializer clamp `0x37d744` (99 -> 127, extended saves only); stacking was
  researched and not built.
- **Service**: data-driven limits in the loader's save format, not a plugin service.
- **Priority**: P3. **Confidence**: checked (item level).

## Monsters and AI

### M1. Monster AI ids

- **Need**: a new AI for a group of bosses; small changes to stock AIs.
- **Why the SDK can't**: the AI table `0x2396e90` (155 entries of `0x20` bytes) cannot grow; `EncounterService` only
  decides Herald and boss-clone modifiers.
- **Workaround today**: AI id 155 answered by an inline hook on the getter `0x4a36c0`; a slot write into entry 83's
  init; two byte patches in a stock AI (`0x5b4e49`, `0x5b4fed`); direct calls into AI helpers (`0x4a6d10`, `0x4a78e0`,
  `0x4a7bc0`, `0x4a7270`, `0x4a8740`). See [monster-ai.md](monster-ai.md).
- **Risk**: a D2R patch that appends an AI entry turns id 155 into D2R's new AI.
- **Service**: `MonsterAiRegistry.register(name, think, init)` with loader-assigned ids that monai rows name; a
  `MonsterAiThink` override for stock ids. Game thread.
- **Priority**: P2. **Confidence**: checked.

### M2. Monster creation

- **Need**: monster level rules, players-count scaling of life without experience, spawn overlays, minion stats.
- **Why the SDK can't**: `EncounterService` decides Herald and boss modifiers only.
- **Workaround today**: inline hook on the scaling setup `0x543e90`, NOPs `0x543d32` / `0x543d44`, a hook on creation
  `0x493cc0`.
- **Service**: `MonsterCreate` decision point after the unit exists and before the first message about it (level,
  life %, experience %, AI id, treasure class). It must not sit in the spawn function itself, which also runs as a
  placement probe.
- **Priority**: P2. **Confidence**: checked.

### M3. Spawning units

- **Need**: spawn a monster near a player (treasure-goblin style events), a portal, a missile.
- **Why the SDK can't**: `EncounterService.runTestSpawn` is a test aid.
- **Workaround today**: direct calls of `0x4929e0`, `0x492cf0`, create missile `0x5371a0`, create portal `0x432ce0`.
- **Risk**: a plugin spawn draws the game or room seed and shifts later unit ids, so later drops and spawns differ from
  stock D2R; a separate seed stream per plugin keeps rolls stable but ids still shift.
- **Service**: `SpawnService` actions (monster, missile, portal, object), queued to the end of the frame, with a
  per-plugin seed stream.
- **Priority**: P2. **Confidence**: checked (calls); static only (id shift).

### M4. Hireling rules

- **Need**: class bonuses at hire, load and level-up; hire gates; revive price; leash.
- **Workaround today**: inline hook on `0x544f60`; raw writes into the NPC table `0x237ea60`; C4 leash patches.
- **Why the SDK can't**: `Hireling` and `Npc` tables are read-only.
- **Service**: `HirelingEvent` decision point (hired, loaded, levelled, revived -> stats, price). See
  [hirelings.md](hirelings.md).
- **Priority**: P3. **Confidence**: checked.

## Quests, NPCs and travel

### W1. Quest events and rewards

- **Need**: change or add quest rewards, issue a reward again, give flat quest experience, react to a kill, an NPC
  click or an NPC message.
- **Why the SDK can't**: `QuestCompleted` is an observation on the UI thread for the local player; rewards must be
  decided on the server before D2R's own.
- **Workaround today**: about 14 per-quest handler hooks (for example `0x5e0830`, `0x5e1f00`, `0x5e1d30`, `0x5e10c0`,
  `0x5a5490`, `0x5ef040`, `0x54c810`, `0x54fa70`) because the central dispatcher was not found; event wrappers on kill
  / NPC click / NPC message (`0x515640`, `0x515980`, `0x516940`); reward byte patches (`0x54c9ac`, `0x5d9a6d`,
  `0x5e1fbd`, `0x5e1fc7`); direct quest-bit calls `0x325c50` / `0x325c00` / `0x325990`. See
  [quests-acts.md](quests-acts.md).
- **Service**: `QuestEvent` decision point (player, quest, event -> skip vanilla) and `QuestReward` (experience, items,
  in / out); quest-bit query and set actions.
- **Priority**: P2. **Confidence**: checked (act 1); observed (later acts).

### W2. NPC menu lines and actions

- **Need**: new NPC menu entries (a respec, a materials submenu, a confirmation page) and their server answer.
- **Why the SDK can't**: the menu is a stock list widget with no way to add a line; `NetworkService` can carry the
  answer.
- **Workaround today**: inline hooks on the client menu builder `0x1147a0` (patch the NPC's record in table
  `0x2a48830`; at most five lines; D2R re-initialises records in `0x115d00`) and the server menu packet `0x4b0470`
  (client request packet `0x38` with kinds from `0x10`); list widget calls `0x1e9200`, `0x1e9030`, `0x1e9390`.
- **Risk**: two plugins adding lines to one NPC overwrite each other; five lines is a hard limit.
- **Service**: `NpcMenuService.addEntry(npc, label key, requirement callback)` with a server-side `NpcMenuAction`
  handler; submenus as plugin panels.
- **Priority**: P2. **Confidence**: checked.

### W3. NPC service and town tables

- **Need**: shop / gamble / hire gates, healing at every town NPC, which levels count as towns.
- **Workaround today**: byte writes into the NPC table `0x237ea60` (43 records of `0x1c` bytes); the is-town function
  rewritten (34 bytes at `0x327850`).
- **Service**: writable rows at load for these built-in tables (D1), and an `IsTown` query backed by data.
- **Priority**: P3. **Confidence**: checked.

### W4. Level entry and portal veto

- **Need**: refuse entry to a level (requirements, a lockout clock, sealed levels), start a clock on entry, operate a
  throne-type object both ways.
- **Why the SDK can't**: `LevelChanged` arrives after the move, on the UI thread, for the local player.
- **Workaround today**: inline hooks on the warp `0x491ff0`, the portal operate `0x58f680` and object function 70
  (`0x548c40`); the countdown is drawn with `OverlayService` (already a service).
- **Service**: `LevelEnter` decision point (player, from, to, kind: walk / waypoint / portal / object -> allowed,
  reason), game thread, host.
- **Priority**: P2. **Confidence**: checked.

### W5. Waypoints beyond the stock panel

- **Need**: more than nine waypoints per act tab, waypoint numbers up to 111, automatic activation.
- **Workaround today**: inline hook on the tab fill `0x1ea9b0`; the 9-entry button table `0x2a969e0` moved and three
  readers (`0x1ea198`, `0x1ec05a`, `0x1ec582`) re-pointed. See [ui-layouts.md](ui-layouts.md) and
  [plugin-techniques.md](plugin-techniques.md).
- **Why the SDK can't**: `PanelService` makes new panels; it cannot widen a stock panel.
- **Service**: loader-sized waypoint panel driven by `actinfo` data; a `WaypointActivate` event.
- **Priority**: P3. **Confidence**: checked.

### W6. Travel and progress actions

- **Need**: from a developer menu or a quest rule: warp a player, set a waypoint, award experience, set quest bits.
- **Why the SDK can't**: only `ExecuteConsoleCommand` (core export 14), which runs D2R's cheat commands with no result.
- **Workaround today**: direct calls of warp `0x491ca0`, `0x4f2e40`, waypoint bits `0x3d6890` / `0x3d6930`, add
  experience `0x44f2e0`.
- **Service**: `PlayerActions` (warp, set waypoint, award experience, set quest bit), queued to the game thread, host.
- **Priority**: P3. **Confidence**: checked.

### W7. Portals to levels above 255

- **Need**: an item-created portal that leads to a level id above 255.
- **Why the SDK can't**: D2RLoader already has 16-bit portal and warp functions (`SendLinkTownPortal16`,
  `HandleSetWarpDestination16`, `SetObjectInteraction16`), but no public way to create such a portal.
- **Workaround today**: write the destination's high byte at object data `+0x78` after D2R's setter `0x34e9d0`.
- **Risk**: two layouts for one fact; if the loader moves where it keeps the 16-bit destination, the portal lands in
  the wrong level.
- **Service**: `SpawnService.createPortal(destinationLevel)` (M3) using the loader's own 16-bit path.
- **Priority**: P3. **Confidence**: static only (not checked whether the loader's path already covers these portals).

## Map and levels

### A1. Automap reveal, markers and labels

- **Need**: reveal a level on the automap, show the area level, mark and name monsters on the automap.
- **Why the SDK can't**: no automap service.
- **Workaround today**: direct calls of the automap room function `0xd2240` and room activation `0x3289a0` with the
  automap layer at `0x2a2cf68`; inline hooks on the automap terror-zone level lookup `0x90c70` and the automap draws
  `0xd76e0`, `0xd78f0`, `0xd6b20`. The marker tint at `0xd6db0` could not be changed.
- **Risk**: a reveal flag is the local client's only; the area level needs the server's difficulty (L4).
- **Service**: `AutomapService.revealLevel`, `addMarker(position or unit, style, label)`, `setAreaLabel`, UI thread.
- **Priority**: P2. **Confidence**: checked.

### A2. Level types above 36 on the automap

- **Need**: automap tiles for level types past D2R's 36 (the automap loader matches 36 names into a fixed range array
  `0x2a9a8e0`).
- **Workaround today**: inline hook on the lookup `0x32bdf0` returning rows from a generated table.
- **Service**: the loader sizing the automap type array from `LvlTypes` (a loader fix, not a plugin service).
- **Priority**: P3. **Confidence**: checked.

### A3. Map memory for layers above 99

- **Need**: explored-room memory for levels whose `Layer` is 100 or more (the map files keep 100 buckets; save
  `0xd5fe0`, load `0xd5710`).
- **Workaround today**: data only: such levels share a lower layer, so their memory mixes.
- **Service**: a loader-side larger map-memory format (it changes the map save files).
- **Priority**: P3. **Confidence**: observed.

## Inventory, stash and belt

### V1. More stash pages and tabs

- **Need**: several personal stash pages, extra stash tabs with their own art and deposit highlight, stash pages in
  classic graphics.
- **Why the SDK can't**: `InventoryService.registerPlayerPage` gives one custom page in retail saves (up to 247 in
  extended saves; page size cannot change after first use); D2R's stash page table at `+0x278` holds five entries and the
  tab getter `0x23af50` caps the count. `PanelService.registerChildLayout` adds children to `PlayerInventory` only.
  `ItemPolicyRequest` carries neither the requesting player nor the target cell.
- **Workaround today**: a `MutationService` transaction of 22 byte patches (a stub in padding at `0x23afd2` plus 21
  caller retargets of `0x23af50`), hooks on the tab update `0x23e630` and slot code read `0x2ce910`, tab flash
  `0x23bb50` / `0x15ef70`; raw writes into five slots of the inventory grid widget vtable (draw, item rect, valid cell,
  cell at point, cell rect).
- **Risk**: a controller plugin byte-checks the same tab getter; the vtable wrap is restored only when its pointer is
  still on top.
- **Service**: `StashTabService.registerTab(art, label key, page)` that sizes D2R's page table, plus child layouts for
  the stash panel and an item policy request with player and cell.
- **Priority**: P2. **Confidence**: checked.

### V2. Batch moves and sorting

- **Need**: sort an inventory or stash page; move many items with one press.
- **Why the SDK can't**: `ItemService.executeExistingItemTransaction` already moves up to 4,096 items atomically
  between inventory, cube, stashes and the current custom page, which is most of a sort. `ItemInfo` has no item width
  or height, and belt, equipment and cursor moves are refused.
- **Workaround today**: not built.
- **Service**: item size in `ItemInfo`; a "find free cell for this item in page" query.
- **Priority**: P3. **Confidence**: static only (headers).

### V3. Belt

- **Need**: a belt refill: belt contents and row count, where the game would put a given potion, a shift-click-to-belt
  move that works for joining clients, potion consumed events.
- **Why the SDK can't**: `forEachInventoryItem` and `ItemInfo.container == Belt` read belt items, and
  `ItemInteractionService` can report belt activations (opt-in container bit), but belt moves are refused by
  `executeExistingItemTransaction`, and there is no free-belt-slot query.
- **Workaround today**: inline hooks or direct calls of the game's free-belt-slot routine (`0x3862d0`, observed) and the
  ground pickup `0x471950`, and of the belt checks; polling every few frames. Two belt plugins hooking the same routine
  broke each other (X1). Technique: [plugin-techniques.md](plugin-techniques.md).
- **Risk**: a slot the plugin computes itself is refused silently by the server; two moves in flight get the same slot.
- **Service**: `BeltService.getLayout(player)` (rows, column kinds), `findSlot(item)` (the game's routing),
  `moveToBelt(item)` (sends the same request a shift-click sends, so it works for joiners), and V4 events.
- **Priority**: P2. **Confidence**: observed.

### V4. Inventory change events

- **Need**: item added, removed, consumed, picked up, moved; belt potion used.
- **Why the SDK can't**: no inventory events; plugins poll every frame.
- **Workaround today**: polling `forEachInventoryItem` or the funnel hook `0x471e90`.
- **Service**: `InventoryChanged` event (player, item handle, from, to, reason) on the UI thread for the local player and
  on the game thread on the host.
- **Priority**: P2. **Confidence**: observed.

## UI

### U1. Extending stock panels

- **Need**: change a stock panel instead of replacing it: rows of the character sheet (bonus columns, damage bonus
  rows), more skill tree pages, waypoint tabs (W5), NPC menu lines (W2), level-up buttons in classic graphics.
- **Why the SDK can't**: `registerChildLayout` adds a widget to `StockPanel::PlayerInventory` only; stock rows are built
  by code per record.
- **Workaround today**: inline hooks on the stat-sheet row functions `0x14eb1b0` / `0x14eb250`; a `MutationService`
  transaction of 13 byte patches plus a hook on the skill tab setter `0x14c3b10`; a slot write into the level-up button
  vtable `0x1fd9ee0` slot 3.
- **Service**: more `StockPanel` values for child layouts (stash, character sheet, skill tree, waypoint), and row
  providers for the sheet (`StatSheetService.addRow(label key, value callback)`).
- **Priority**: P2. **Confidence**: checked.

### U2. Native widget behaviour and art

- **Need**: set a button's icon from a skill icon atlas frame, change a grid widget's drawing, read a stash slot widget.
- **Why the SDK can't**: 0.4.0 covers much of what the ports patched for: `setText`, `setTooltipText`,
  `setImageSprite`, `getWidgetState`, `setWidgetRect`, `getWidgetScreenRect`. What is left: choosing a frame of a
  multi-frame sprite (skill icons are frames of a class atlas), widgets inside the advanced stash, and per-widget
  behaviour overrides (the grid callbacks in V1).
- **Workaround today** (written against 0.3.0): the buff HUD calls `FindTopLevelPanel` `0x846170`,
  `FindChildWidgetByName` `0x856220`, writes button frames at `+0x7e8` / `+0x7ec` and calls the frame setter `0x859700`
  and refresh `0x870460`; it overwrites text and tooltip buffers in place (`+0x88`). Another port subclassed the game
  window to get click positions for its menus.
- **Service**: `setImageFrame(widget, sprite, frame)` and a `skillIcon(skill)` query; stash widgets reachable by name.
- **Priority**: P2. **Confidence**: checked (the raw writes); static only (whether 0.4.0 removes each one).

### U3. Overlay images

- **Need**: draw icons (a buff bar, markers) on the HUD.
- **Why the SDK can't**: `OverlayService` draws lines, rectangles and text only. One port drew each icon as about 490
  filled rectangles; another saw `drawText` draw nothing in one phase and moved to native widgets.
- **Workaround today**: rectangles built from art at load, or a plugin panel with widgets.
- **Service**: `drawSprite(path, frame, rect, tint)` in the overlay.
- **Priority**: P2. **Confidence**: observed.

### U4. Tooltip and text builders

- **Need**: lines for custom stats in item tooltips (descfuncs past D2R's), long descriptions, skill tooltip labels, a
  stat-range display that does not duplicate the loader's.
- **Why the SDK can't**: `SharedEventService.registerItemTooltipListener` adds text at fixed positions, which covers
  plain extra lines. It does not let a plugin format a stat line by its own descfunc, and the item text buffer is 256
  bytes (34 long texts of one mod were cut). Skill tooltips have no listener.
- **Workaround today**: inline hooks on the item text builder `0x2dc4b0` and formatter `0x2d6520`, the skill tooltip
  line renderer `0x287a50`; swaps of D2RCore thunk slots for the stat-range tooltip (`0x3e2af58`, thunk `0x3e2b826`).
- **Service**: `StatDescriptionRegistry` (descfunc id -> formatter), a skill tooltip listener, a larger tooltip buffer.
- **Priority**: P2. **Confidence**: checked.

### U5. Message-log lines and notices

- **Need**: a line in the message log (quest reward, refusal reason), a client-only line, a notice that does not freeze
  NPC speech.
- **Why the SDK can't**: `OverlayService` draws on screen, not into the log; nothing writes a log line.
- **Workaround today**: chat packet `0x26` through `0x477a30` (a line sent while an NPC speaks freezes the speech, so
  notices are held); a client-only line through the chat handler `0x12c190`.
- **Service**: `MessageLogService.addLine(player or local, text key, args, colour)` that queues during NPC speech and
  formats in the receiving client's language.
- **Priority**: P2. **Confidence**: checked.

### U6. Input and controller

- **Need**: typing in a plugin text box without triggering hotkeys, wheel and page keys while a panel is open, more than
  one controller route, synthetic input for tests.
- **Why the SDK can't**: 0.4.0 covers typing (`PanelFlags::TextEntry`) and raw keys (`InputService.registerKeyListener`,
  added after the ports' 0.3.0); controller routes are one per game (a second plugin gets `Conflict`); no mouse wheel
  event.
- **Workaround today**: a game-window subclass, a virtual-key swap; test harnesses swap `GetKeyState` and
  `XInputGetState` imports.
- **Service**: wheel events in the key listener; several controller routes; a test-input service.
- **Priority**: P3. **Confidence**: observed.

### U7. Unit name colours and hover text

- **Need**: monster name colour by rank, hover text.
- **Workaround today**: inline hook on the hover name `0xcea50`.
- **Service**: `UnitDisplay` decision point (unit -> name colour, extra line), UI thread.
- **Priority**: P3. **Confidence**: checked.

### U8. Plugin settings page and panel count

- **Need**: settings in the game's options menu; more than 64 panel names per plugin (one port registered empty panels
  as click targets for a long list).
- **Why the SDK can't**: the settings menu has no plugin page; `PanelService` has a per-plugin cap.
- **Service**: an options page built from a declared settings schema; widget events (`setWidgetEventMessage`, 0.4.0)
  already remove the need for empty panels as click targets.
- **Priority**: P3. **Confidence**: observed.

## Strings and localization

### G1. A string id for a plugin key

- **Need**: a numeric string id for the plugin's own key, because D2R's widgets and menu records take ids, not keys
  (NPC menu lines, table name fields).
- **Why the SDK can't**: `LocalizationService.getStringByKey` copies text by key and `getStringById` accepts original
  game ids only. D2RCore's private `GetNamespacedStringId` (ordinal 1203) does exactly this, without a stability
  promise.
- **Workaround today**: swap the import slot `0x3e2ae10` used by D2RCore's string-lookup thunk `0x3e2b730` and serve 64
  plugin ids counting down from `0xFE00`. An earlier id collision was a real bug. See [strings.md](strings.md).
- **Risk**: D2RLoader may allocate ids in that range for its namespaces, or change the thunk.
- **Service**: `LocalizationService.registerKey(key) -> id`, stable for the session, visible to every lookup path.
- **Priority**: P1. **Confidence**: checked.

### G2. String lookup fallbacks

- **Need**: a fallback for a missing string (rune letters), `@key` text in mod layouts that draws "Missing string".
- **Why the SDK can't**: the loader refuses a new `d2r:` key; there is no lookup hook.
- **Workaround today**: swap the D2RCore string thunk slot `0x3e2b736` (only one caller changed) with a raw-write
  fallback.
- **Service**: a `MissingString` decision point, and namespaced keys accepted in layouts.
- **Priority**: P3. **Confidence**: checked.

## Data tables

### D1. Changing table rows at load

- **Need**: write values D2R's loader clamps (collide type 9), rewrite a column after every load (carry-once uniques),
  patch built-in tables that have no TXT (NPC records, requirement gates).
- **Why the SDK can't**: `DataTableService` is read-only; `CustomTableService` serves plugin tables only.
- **Workaround today**: raw writes into rows after `DataTablesLoaded`, `VirtualProtect` writes when the loader refuses a
  data write.
- **Service**: `DataTableService.beginEdit(table)` valid only inside the `DataTablesLoaded` listener, with typed column
  writes and range checks; built-in fixed tables (NPC, hireling services) exposed as tables.
- **Priority**: P2. **Confidence**: checked.

### D2. Typed columns of built-in tables

- **Need**: read a column by name (a skill's icon cel, its description row, a state's flags).
- **Why the SDK can't**: rows come as raw bytes; `rowSize` must be checked and offsets known per build. The buff HUD
  guesses `Skills` / `SkillDesc` offsets at run time by scanning rows.
- **Service**: `DataTableService.getColumn(table, name) -> {offset, type}` for the running build. See
  [tables.md](tables.md).
- **Priority**: P2. **Confidence**: observed.

### D3. Loader table snapshots out of step with the mod

- **Need**: the mod's sound table (12,449 rows) loaded everywhere the game reads it.
- **Why the SDK can't**: the loader's base snapshot loads D2R's 11,913 sound rows in one path; a plugin cannot reach it.
- **Workaround today**: six hooks on sound lookups that refuse ids past the loaded rows and tell the player to restart.
- **Service**: a loader fix; and a check at load that every table snapshot matches the active mod.
- **Priority**: P2. **Confidence**: checked.

## Networking

### N1. Plugin messages between host and client

- **Need**: server-to-client data (a dungeon clock, an NPC page answer, open an info panel) and client-to-server
  requests (an NPC action, a stat unassign).
- **Covered by**: `NetworkService` (`registerChannel`, `connectToHost`, `sendToHost`, `sendToClient` with 224-byte
  payloads, `getPeerPlayer`, a compatibility token). The ports predate it or have not moved: they swapped the client
  packet-handler slot for chat packet `0x26` (table `0x22bbc40`, entry `0x26 * 24`, original handler `0x12c190`) and
  sent hex payloads up to 125 bytes, and rode client packet `0x38` for requests.
- **What is left**: messages larger than 224 bytes (a chunking helper), and a defined order relative to game messages
  (a plugin message about an item that the game message creating it has not reached yet). Not available on Battle.net
  by design.
- **Priority**: P1 to migrate, small gap. **Confidence**: checked (the old path); static only (the migration).

### N2. Server-only facts on a joining client

- **Need**: a joining client's HUD needs facts the game never sends: state end frames (the state messages `0xa7`-`0xaa`
  carry on / off and stats only), cooldowns (no message; client-predicted), non-saved and computed stats (other
  players' stats beyond speed, attack rate, level, strength and dexterity), the server's difficulty.
- **Why the SDK can't**: nothing replicates them; each plugin must design its own messages on `NetworkService`.
- **Workaround today**: none; buff timers show for single player and the host only; one HUD may latch onto another
  player's unit on a host. See [network-messages.md](network-messages.md) and [engine-limits.md](engine-limits.md).
- **Service**: a loader-side replication for the common facts: state end frame and source skill, cooldown end per
  skill, a plugin-declared list of stats to send, difficulty. Clients read them through the same `StateService` /
  `StatService` queries as the host.
- **Priority**: P1. **Confidence**: static only (message catalogue); observed (HUD on host only).

### N3. Filter or hold a client request

- **Need**: hold a respawn request for some frames, refuse an item request with feedback, see a request before D2R.
- **Why the SDK can't**: no client-message point; the handlers return 4 to retry a message later, which is how the
  server holds one.
- **Workaround today**: hooks on single handlers (respawn `0x4b6160`, NPC menu `0x4b0470`, item funnel `0x471e90`); the
  handler table is at `0x1d2a790` (`0x66` entries).
- **Service**: typed decision points for the requests that matter (C1, I6, L6, W2) rather than raw packet filters; a raw
  `ClientMessage` filter only for research builds.
- **Priority**: P2. **Confidence**: checked (hooks); static only (handler table).

### N4. Game-side work on a joining client

- **Need**: client-side rules that need the client's world (has-state on the local unit, the client's stat copy) on a
  joining client.
- **Why the SDK can't**: `ThreadService.runOnGameThread` returns `Unavailable` on a TCP/IP client, by design.
- **Service**: client-world queries on the UI thread (T1, T2 read the client copy), so plugins never need the server
  thread on a joiner.
- **Priority**: P2. **Confidence**: checked.

## Rendering

### R1. Renderer caps

- **Need**: more HD model cache entries for a mod with many monsters, a larger classic draw list, no HD ground plane on
  a level that should not have one.
- **Workaround today**: byte patches of the model cache size (`0xdfb61d`, `0xdfb5b9`, `0xf75cc1`, 2,750 -> 8,250), a
  `MutationService` transaction on the draw list builder `0x21bae0` and seven inserters, a skip for one level at
  `0x6b4b80`.
- **Service**: loader configuration keys for the caps, read from the mod's `d2rloader.toml`.
- **Priority**: P3. **Confidence**: checked.

## Already covered, or no service needed

| Need | Why no new service |
|-|-|
| More than 511 unit states | D2RLoader's wide state engine (`SendAllWideStateStats`, `SendChangedWideStateStats`, `ReceiveWideStateStatPacket`) does it; a plugin's own relocation collided with it and was retired. Open: whether joiners receive wide ids in every message |
| Item stat cost rows up to 32,768 | D2RLoader built-in |
| Inventory and stash grid sizes, shared stash tabs | data plus `add_shared_tabs` ([d2rloader.md](d2rloader.md)) |
| Cube rows, treasure class entries | D2RLoader resizes them (I7 and I8 are about rules, not counts) |
| Running work on the right thread | `ThreadService` |
| Atomic patches with rollback | `MutationService` (two ports use it for their largest patch sets) |
| Who patched a range | `DiagnosticsService` |
| Unit to level id | reimplementation exports 2000 / 2001 |
| Creating, editing, moving and exchanging items | `ItemService` (create, edit, destroy, transactions, split, affix) |
| New panels, child widgets in the inventory, widget text and art | `PanelService`, `WidgetService` (0.4.0) |
| Extra lines in item tooltips | `SharedEventService` item tooltip listener (U4 covers what is left) |
| Plugin tables in TXT | `CustomTableService` |
| Hotkeys in the Controls menu, raw key events | `InputService` |
| HTTP | `HttpService` |
| Disabling D2R-only systems (terror zones and the like) | data only |
| Asset cache warnings (skeleton, animation) | D2R grows the caches itself |

## Open questions for the SDK author

1. **Hook sharing (X1)**: is a loader-owned chain per site planned, or should plugins expect one owner per address and
   ask for events instead?
2. **D2RCore internals (X2, I1, G1)**: can item-event registration and string-id allocation become public before any
   other D2RCore change, since plugins write into those tables today?
3. **Registries (S1, S2, I2, M1, S3)**: would the loader rather assign function ids (plugin names resolved in the
   tables at load) or reserve fixed ranges per plugin? Named ids survive D2R patches that add functions.
4. **Server frame (L1)**: can a per-game frame listener run in the same place the loader runs its own game-thread
   callbacks, with the frame number?
5. **Joiners (N2)**: would the loader replicate state end frames and cooldowns itself, or should each plugin send them
   over `NetworkService`?
6. **Portals (W7)**: do the loader's 16-bit portal functions already cover portals created by item spells?
7. **Wide states**: do joiners receive state ids above 511 in every state message (`0xa7`-`0xaa`)?
8. **Host-neutral services (X6)**: is a second host implementing the gameplay services (handles only) a goal the SDK
   wants to keep compatible with, so the decision points above get the same structs on both?
