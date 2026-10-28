# Finding and hooking D2R engine code

Workflow that found every address in [d2r-93847-addresses.md](d2r-93847-addresses.md). Read-only analysis of the
game image; changes only through a D2RLoader plugin with byte-checked hooks.

## 1. Dump the running image

D2R.exe's code is **encrypted on disk**, and D2RLoader.exe's sections are empty on disk (filled at start-up), so
the code exists only in memory.

- Start the game through D2RLoader to the main menu (or a character).
- Read the main module of the running `D2RLoader.exe` process (OpenProcess with QUERY_INFORMATION | VM_READ,
  EnumProcessModulesEx, ReadProcessMemory) and write it as a PE whose **section file offsets equal their RVAs**
  ("memory layout"); unreadable pages as zeros. Then Ghidra addresses = image base + RVA, the same RVAs plugins and
  crash logs use.
- Record the PE header `TimeDateStamp` (build 93847 under D2RLoader 1.3.1: **`0x6ab3782c`**). It changes with every
  D2R patch and D2RLoader release: a new stamp means re-dump and re-verify every address.

## 2. Analyse once (Ghidra headless)

```
analyzeHeadless <projectDir> D2R -import <dump.exe> -overwrite -log <log>
```

Run detached (`GHIDRA_HEADLESS_MAXMEM=8G`); auto-analysis of the ~30 MB code section takes **hours**. Do not pass
`-analysisTimeoutPerFile 0` (it means zero seconds). Then query read-only, without re-analysis:

```
analyzeHeadless <projectDir> D2R -process <dump.exe> -noanalysis -readOnly -scriptPath <scripts> -postScript <Script.java> <args>
```

## 3. Query scripts (tools/ghidra_scripts)

| Script | Args | Output / use |
|---|---|---|
| `Decompile.java` | rva... | C pseudo-code per function into files |
| `Disasm.java` | rva... | `ASM <rva>: <instr>`; defines functions the analysis missed. Use for entry bytes |
| `Refs.java` | rva... | `REF <target> <- <from> <function@entry>`: who calls / reads it |
| `NearRefs.java` | [scalar] before after rva... | instructions addressing anything in [rva-before, rva+after]: data reached at an offset (assert descriptors), or with `scalar` plain numbers (sizes) |
| `StringXrefs.java` | substring [max] | functions referencing strings containing the text |
| `FindDisp.java` | displacement [mnemonic] | every instruction with that memory displacement, by function: all users of a record field or data-table offset |
| `FindDispRange.java` | lo hi | displacements in [lo, hi), non-stack: users of a struct region |
| `FieldDispatch.java` | displacement [window] | "read field at disp, then indirect call within N instructions": find which table a field indexes |
| `FindPtrTables.java` | minRun maxRun | runs of consecutive 8-byte function pointers in data blocks (dispatch tables) and the code that references their start |
| `FindSwitch.java` | bound [window] | `CMP reg, N` + `JA` + computed `JMP`: switches with N cases (e.g. 0x1b for 28 cases) |

## 4. Techniques that work

1. **Assert source paths.** D2R keeps file names in its assert strings, e.g.
   `D2Common\src\DataTbls\HoradricCube.cpp`, `D2Game\src\Player\PlrTrade.cpp`, `D2Game\src\Skills\Skills.cpp`,
   `D2Game\src\Items\ItemMode.cpp`, `D2Client UI/SkillDesc.cpp`. StringXrefs on the file name -> NearRefs on its
   assert descriptor -> Refs on the small assert stub = the functions of that source file. Usually the fastest lead.
2. **Record getters + FindDisp.** `<table>Record(dataFlag, index)` functions read `dataTables + offset`; FindDisp on
   that offset lists every user of the table (cubemain: +0x1570 records, +0x1578 count). Record sizes differ from
   1.13c (cubemain 0x148 -> 0x16c, skills 748 bytes): measure them from the getter's multiply.
3. **Dispatch tables.** FindPtrTables with a run length near the known function count (skill do functions 140-200,
   missile functions 60-90); confirm by FieldDispatch on the record field (`srvdofunc` u16 at skills +0x4e).
   Bounds are usually a `CMP` right before the indexed call (`0x43acb0` rejects srvdofunc > 0xbe).
4. **Switches.** Calc codes, cube conditions and item ops are switches: FindSwitch with the case count.
5. **Classic knowledge.** 1.10/1.13c structures mostly survive with 8-byte pointers and fields shifted (missile
   params: same fields, +0x10 after three pointers; missile record: 1.13c's from +0x24 moved by +0x24). Classic
   ordinals map to D2R functions by behaviour, not by name.
6. **Thread samples for hangs**: repeated RIPs in a hung game point at the loop (see 9).

Put every confirmed address in one header with its signature as a comment (the template's `d2r.h`).

## 5. Before hooking: is D2RLoader already there?

Disassemble the function entry. A `jmp [rip+x]` (FF 25) at the entry means D2RLoader/D2RCore already hooks it: your
inline hook would fail the byte check or fight it. D2RCore exports cover some engine work already, e.g. wide
state sync: D2R's state senders `0x53a210` (changed states) and `0x539890` (all states) are replaced by
`SendChangedWideStateStats` / `SendAllWideStateStats`, the client side by `ReceiveWideStateStatPacket`. They read
the state arrays at D2R's own offsets (+0xaf0), so **moving engine data D2RCore reads breaks D2RCore** (a self-made
"states fix" stopped all state sync: monsters unhoverable, no overlays). The server skill charge function
`0x436830` is also hooked at its entry by the loader.

## 6. Writing hooks (PluginSDK 0.3.0)

```cpp
// MOV [RSP+0x10],RBX / MOV [RSP+0x18],RBP / MOV [RSP+0x20],RSI
constexpr uint8_t Entry[] = {0x48,0x89,0x5c,0x24,0x10, 0x48,0x89,0x6c,0x24,0x18, 0x48,0x89,0x74,0x24,0x20};
using Fn = uint64_t (*)(uint64_t dataFlag, void* unit, uint64_t code, int32_t skill, uint32_t level) noexcept;
Fn g_original = nullptr;
auto Hook(uint64_t f, void* u, uint64_t c, int32_t s, uint32_t l) noexcept -> uint64_t { return g_original(f, u, c, s, l); }
bool ok = context->InstallInlineHook(0x33b3b0, Entry, sizeof(Entry), Hook, &g_original);
```

| Rule | Why |
|---|---|
| Expected bytes = **whole instructions, at least 14 bytes, REX prefixes included** (`40 53 ...`, `41 56 ...`) | The loader relocates them into the trampoline; a cut instruction crashes |
| Function pointer types must be **`noexcept`** | SDK requirement for hook targets |
| Every hook checks its bytes and **refuses on mismatch** | Build safety: a different build never gets patched |
| Table slots: `PatchWriteU64(rva, &expected, 8, newValue)` with the expected old value (0 for an empty slot, the current pointer when wrapping D2R's function) | Same safety for data writes (srvdofunc slots, missile hit slots) |
| u32 data fields: `PatchWriteU32` (e.g. the item spell table's flag) | |
| Several dependent byte patches: the mutation service (`QueryService(&MutationService)`, `beginTransaction`, `stageBytePatch` with expected bytes, `commit`; cancel on failure) | All-or-nothing |
| Call D2R functions through `exeBase + RVA` with exact signatures; D2R uses the Windows x64 ABI | |
| Functions D2R calls with a value in a callee-saved register you need (e.g. the unit in RDI) | Hook through a small stub that copies that register into an argument, chosen by the return address (the mana-cost hook does this) |
| Check the image timestamp at load (`IMAGE_NT_HEADERS64.FileHeader.TimeDateStamp` of `context->exeBase`); install nothing on a mismatch, log why | |
| Generated data headers (row ranges, ID maps) must be checked at load against what D2R actually loaded (row counts) | Stale headers silently misbehave |
| Register a console command that prints version, image match and each feature's install result | First thing to check in game |

## 7. Diagnosis switch (build it in from day one)

A text file next to the plugin DLL (`<mod>/d2rloader/plugins/<plugin>-off.txt`), read at launch: words separated
by spaces/commas/lines naming parts to leave out (`states levels calc skills items missiles mana ...`) and extra
trace words. Bisect any regression by turning halves off; delete the file for normal play. Know your dependencies
(a port found: never turn off calc codes alone while the skills/missiles that use them stay on: the game hangs on
load).

## 8. Logs and dumps

| What | Where |
|---|---|
| D2RLoader log (install lines, asserts after "startup complete") | `<game>\d2rloader\logs\d2rloader.log` |
| D2RLoader crash reports | `<game>\d2rloader\crashes\*.log` (exception RVA, stack, sometimes a "last-resort report") |
| Plugin's own log (`context->LogInfo`) | `<game>\mods\<Mod>\d2rloader\logs\` |
| D2R's log | `blz-log.txt` (file-not-found lines, "Failed to find an animation state" noise) |
| Windows crash dumps | `%LOCALAPPDATA%\CrashDumps\D2RLoader.exe.<pid>.dmp` (minidump: read the exception stream and memory list with a small parser to get the faulting RIP and stack values; no debugger needed) |

You may launch `D2RLoader.exe` to the menu yourself to read the install lines, then close it.

## 9. Hangs

Take a minidump of the hung process (MiniDumpWriteDump from PowerShell via P/Invoke), sample the busy thread's RIP
several times, and map the RIPs to functions with the image's `.pdata` (RUNTIME_FUNCTION begin/end RVAs). Repeated
D2R getters plus D2RCore trampolines with no usable stack = a loop over units/items; bisect with the diagnosis
switch.

## 10. Prefer data

Before writing code: is there a D2R function that does the same with other table values? (A classic mod's added
storm function = D2R's Armageddon 124; its resurrect = D2R's Revive 58; its itemless town portal = D2R's 109; a
classic timed potion = D2R pSpell 3; an explosion missile hit = D2R hit 1.) Plugins should add only what D2R lacks:
new functions in free table slots, calc codes, item spells, missile hits, engine limits.
