# D2RLoader plugin template

A minimal mod-scoped D2RLoader plugin for Diablo II: Resurrected. Copy this folder into your mod project and extend
it. It is the part of an overhaul that data files alone cannot do: new skill functions, formula codes, hooks on
engine functions.

Target: **D2R 3.3.0 build 93847 + D2RLoader 1.3.1, PluginSDK 0.3.0.** Every address in `src/d2r.h` belongs to that
one game image. The plugin checks the image's PE timestamp (`0x6ab3782c`). On any other image it loads, logs
"not the expected game image: every engine part stays off", and changes nothing.

| File | What it is |
| --- | --- |
| `src/plugin.cpp` | Entry point: `PluginInfo`, the parts table (`g_parts`), the status console command |
| `src/log.*` | Log helper (`plog::Info/Warn/Error`, prefixed with the plugin name; `plog::Sample` for hot paths) |
| `src/build_guard.h` | Image check: PE TimeDateStamp of the running game against `ExpectedImage` |
| `src/off_switch.*` | Reads `<name>-off.txt`: part words to leave out |
| `src/env_guard.*` | Environment guard: another mod running, this DLL outside its mod folder, a second copy, other plugins |
| `src/sound_guard.*` | Sound-table guard: catches sound ids past the loaded table (the first-start cache) and asks for a restart |
| `src/item_type_check.*`, `src/item_type_ids.h` | Item type id self-check: the ids D2R.exe tests by number, against the mod's |
| `tools/item_type_ids.py` | Checks a built itemtypes.txt and writes `item_type_ids.h` |
| `src/d2r.h`, `src/d2r.cpp` | Address registry for build 93847: RVAs, typed function pointers (`d2r::api`), record offsets, `MissileParams`/`SummonParams`, `At<T>`, D2 RNG |
| `src/skill_functions.*` | Example of a new server skill function (`srvdofunc` 172, a missile fan) |
| `src/hook_example.*` | Example inline hook (Whirlwind's hit delay `0x56ac00`, passed through and counted) |
| `CMakeLists.txt`, `build.ps1` | Build |

## Build

You need CMake 3.29 or newer and Visual Studio with the C++ workload. The PluginSDK comes from
`../third_party/PluginSDK` (a git submodule of d2r-modkit).

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1                    # -> build\Release\mod-core.dll
powershell -ExecutionPolicy Bypass -File build.ps1 -Name my-mod-core  # another DLL / plugin id / command name
powershell -ExecutionPolicy Bypass -File build.ps1 -Mod MyMod -SoundRows 5100  # turn on the mod and sound checks
powershell -ExecutionPolicy Bypass -File build.ps1 -Source C:\mymod\plugin -Sdk C:\mymod\third_party\PluginSDK
```

`build.ps1` does the same as these commands:

```powershell
cmake -S . -B build -A x64 -DMODKIT_PLUGIN_NAME=mod-core -DMODKIT_MOD_NAME= -DMODKIT_SOUND_ROWS=0 [-DMODKIT_SDK_DIR=<PluginSDK>]
cmake --build build --config Release
```

CMake variables: `MODKIT_PLUGIN_NAME` (default `mod-core`) sets the DLL name, plugin id, console command and
off-file name. `MODKIT_PLUGIN_VERSION` (default `0.1.0`) is the plugin version; bump it with every release, because
shared plugins must match between host and client in multiplayer. `MODKIT_MOD_NAME` is the mod folder the plugin
ships in (`mods\<name>`); empty skips the mod checks. `MODKIT_SOUND_ROWS` is the row count of the mod's sounds.txt;
0 means the mod adds no sounds. `MODKIT_SDK_DIR` is the PluginSDK path. The
build uses the static CRT, so the DLL does not need the VC++ redistributable, and compiles with `/W4 /permissive-
/utf-8`. `build/` is git-ignored.

## Install and loading

Copy `build\Release\<name>.dll` to:

```text
<game>\mods\<Mod>\d2rloader\plugins\
```

The plugin declares `Shared | ModScopedOnly | NativeHooks`:

- **Shared**: it runs on both server and client. It is recorded in the character's plugin history and must match in
  TCP/IP games.
- **ModScopedOnly**: it loads only from the mod's own plugin folder, and only while that mod is active
  (`-mod <Mod>`). Your hooks never touch the unmodded game or other mods.
- **NativeHooks**: it may patch game code.

Fully restart the game after replacing the DLL.

## Logs and status

- The plugin's log is `<game>\mods\<Mod>\d2rloader\logs\<name>.log`, rewritten each launch. The first line has the
  plugin version, the mod, the D2R version, the image stamp and the DLL's path. Then one line per part, such as
  `mod-core: skills 1/1 installed: srvdofunc 172 missile fan` or `mod-core: typeids ok: 25 of 25 sites`.
- The loader's log is `<game>\d2rloader\logs\d2rloader.log`. Game asserts appear after "startup complete". Crash
  reports go to `<game>\d2rloader\crashes\*.log`, and D2R's own log is `blz-log.txt` in the game folder. Crash
  dumps go to `%LOCALAPPDATA%\CrashDumps\D2RLoader.exe.<pid>.dmp`.
- In game, type the console command `<name>` (by default `mod-core`). It prints the version, the image check, the
  off-file contents, the live status of every part and the other plugins loaded.

## Diagnosis switch

You can create `<name>-off.txt` next to the DLL (for example `mods\<Mod>\d2rloader\plugins\mod-core-off.txt`) and
list feature words to leave out. Separate the words with spaces, commas or new lines. Case does not matter.

```text
skills hook
```

The template's parts and their words:

| Word | Part | Needs the expected image |
| --- | --- | --- |
| `envguard` | Environment guard | no |
| `soundguard` | Sound-table guard | yes |
| `typeids` | Item type id self-check | yes |
| `skills` | Server skill functions | yes |
| `hook` | Example inline hook | yes |

When the game crashes or misbehaves, leave out half of the parts, restart, and repeat until you find the cause.
Delete the file for normal play. Each part you add gets its own word (a row in `g_parts` in `plugin.cpp`). You can
also add opt-in words, such as a trace that is only on when its word is listed.

## Guards

The guards change nothing in the game. They find the setups that break a mod and say so in the log and the status
command. Keep them in every release: they turn "it crashes" into one log line.

| Guard | What it catches | What it does |
| --- | --- | --- |
| Build (`build_guard.h`) | Another D2R or D2RLoader image (TimeDateStamp is not `0x6ab3782c`) | Every engine part stays off. Logs the stamp and D2R's version string |
| Environment (`env_guard.*`) | D2RLoader runs another mod (`default_mod`), this DLL sits outside `mods\<Mod>`, a second copy is loaded, other plugins | Warns at load and again 15 s later. Lists other plugins. It does not try to work with them |
| Sound table (`sound_guard.*`) | The first start after an install builds D2R's cache and loads only D2R's sounds; the mod's sound ids are past the table | Makes the read return "no sound" instead of crashing; logs once and asks for a restart |
| Item type ids (`item_type_check.*`) | The mod's itemtypes.txt moved a row D2R.exe tests by number (for example rune 75) | Logs each mismatch. Fix the data |

Background: [knowledge/d2rloader.md](../knowledge/d2rloader.md) and
[knowledge/porting-classic-mods.md](../knowledge/porting-classic-mods.md).

### Item type ids

`src/item_type_ids.h` holds the mod's ids for the codes D2R tests by number. It ships with D2R's values. Regenerate
it from the mod's built itemtypes.txt after every change, and fail the build when it exits 1:

```powershell
python plugin-template/tools/item_type_ids.py <project>\out\data\global\excel\itemtypes.txt plugin-template\src\item_type_ids.h
```

It prints `item type ids ok: 10 of 10 as D2R` or one line per wrong code.

## Adding a server skill function (srvdofunc)

When a skill reaches its cast frame, the server calls `srvdofunc` from a table of 191 pointers (RVA `0x238ea00`).
D2R 3.3 uses slots up to 171, so **slots 172-190 are free**.

1. In `src/skill_functions.cpp`, write a function with this signature:
   `int64_t Fn(d2r::Game* game, d2r::Unit* unit, int32_t skillId, int32_t level) noexcept`.
   Return non-zero when the skill ran. Check every pointer, and never throw.
2. Build it from `d2r::api`:
   - `getSkillRecord` gives the skills.txt row. Read its columns with `At<T>(skill, d2r::Sk...)`.
   - `d2r::EvalCalc(game, unit, skill, d2r::SkCalc1, skillId, level)` evaluates a formula column.
   - `skillTargetXY` and `skillTargetUnit` give the target.
   - Fill a `d2r::MissileParams` and call `createMissile`.
   - Fill a `d2r::SummonParams` and call `summonPet`, then `setPetLevel`, `petSkillStats` and `addPetToParty`, as
     D2R's own summons do.
   - Use `unitStat` / `unitStatNoParam` for stats, and `api.unitSeed` + `d2r::RandomBelow` for D2's RNG.
   - Call `setUnitSkillFlag(unit, 1)` first, like D2R's own do functions.
3. Add a row `{slot, Function, "name"}` to `Functions[]`.
4. In skills.txt, set the skill's **`srvdofunc` to the slot number** (for example `172`). Set `cltdofunc` to a client
   function that fits the animation, or 0. Missiles created on the server are sent to clients by the game.
5. Build, install, and check the log line `skills N/N installed`.

The slot is written with `PatchWriteU64(slot rva, 8 zero bytes, 8, function)`. The expected value of zero means the
write is refused, and reported as `FAILED`, if the slot is not empty. That way you never overwrite a D2R function or
another plugin's function.

## Adding an inline hook

1. Find the function in the dumped image (`tools/dump_image.py`, `tools/ghidra_analyze.ps1`), then decompile and
   disassemble it: `tools/ghidra_query.ps1 Decompile.java 0x<rva>` and `Disasm.java 0x<rva>`. Work out its
   arguments and its return type.
2. **Check whether D2RLoader already hooks it.** If the function entry is `FF 25 ...` (`jmp [rip+x]`), it is already
   hooked. Do not stack a hook on it. Use the D2RLoader service or D2RCore export for that function, or hook a
   caller or callee instead. `hook_example.cpp` checks this at runtime. The PluginSDK's `DiagnosticsService` can
   tell you who owns a patch.
3. Copy the entry bytes: **whole instructions, at least 14 bytes, REX prefixes included** (for example
   `40 53` is `push rbx`, not just `53`). D2RLoader writes a 14-byte absolute jump and moves the bytes you name
   into the trampoline. If the bytes do not match the live code (wrong image, or someone else patched it), the hook
   is refused and nothing changes.
4. Declare the function type `noexcept`, and keep an `original` pointer:
   ```cpp
   using Fn = int32_t (*)(void* unit, void* weapon) noexcept;
   Fn g_original = nullptr;
   auto Hook(void* unit, void* weapon) noexcept -> int32_t { return g_original(unit, weapon); }
   context->InstallInlineHook(Rva, EntryBytes, sizeof(EntryBytes), Hook, &g_original);
   ```
5. Give the hook a part word and a row in `g_parts` in `plugin.cpp`.

## Building a new game system

A system such as a mapping system or a new crafting bench is several parts in one plugin, next to a lot of data.
Plan it with [knowledge/new-game-systems.md](../knowledge/new-game-systems.md) before you write code. In the plugin:

- **One part per hook**, each in its own file with an `Install<Part>` function, a row in `g_parts` and an off-switch
  word (for example `mapportal`, `mapmods`, `maplevel`). Then a crash can be narrowed down by turning parts off.
- **Shared state in one place**, for example a per-game table ("this portal leads to that level, with these
  modifiers") that the parts read. Clear it when the game ends. Anything that must outlive the game belongs in item
  stats or the save, not in plugin memory.
- **Read the data, don't copy it.** Take tiers, modifiers and limits from the mod's tables (item stats, levels.txt
  columns) so the data lane can tune them without a new DLL.
- **Hooks from the registry first.** The [93847 registry](../knowledge/d2r-93847-addresses.md) already lists, for
  example, the portal maker, the cube's portal output, the spawn-monster function and room to level. Confirm each
  one in Ghidra (signature, callers, entry bytes) before you hook it, and add what you find.
- **Count and report.** Every part shows its counters in the status command (`mapmods: 412 monsters changed`). That
  is how a test check or a player's report proves the part ran.

## Rules

- Every RVA, offset and entry byte sequence is for build 93847 only. After a game or D2RLoader update, dump the
  image again, re-find each address, and only then change `ExpectedImage` in `build_guard.h`.
- Inline hooks need whole instructions, at least 14 bytes, with REX prefixes included. Hook function types must be
  `noexcept`.
- Check for an existing D2RLoader hook (`jmp [rip+x]` at the entry) before you patch any engine function.
- **Never patch or move the unit state arrays.** D2RLoader 1.3.1 already syncs more than 512 states
  ("wide states") and reads D2R's own layout. A plugin that moved them broke all client state sync (monster hover,
  overlays).
- Prefer data over code. Many effects are an existing D2R function with different TXT values. Add only what D2R
  lacks.
- Use table slots only with an expected value (`PatchWriteU64` with zero bytes for empty slots, or the old pointer
  when you replace one).
- Keep every part switchable through the off-file.
- Log with `plog`. In a hot path, log only when `plog::Sample(counter)` is true.
