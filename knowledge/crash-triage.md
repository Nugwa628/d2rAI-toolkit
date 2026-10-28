# Crash triage: from a player's crash report to a fix

How to go from "it crashed" to the cause, using only the files a player can send. Worked on D2R 3.3.0 build 93847
under D2RLoader 1.3.1. Tools: the Ghidra scripts in `tools/ghidra_scripts/` (run through `tools/ghidra_query.ps1`)
on a memory dump of the same image ([engine-re.md](engine-re.md)). Symptom table: [debugging.md](debugging.md).

## 1. Ask for the right files

Ask once, for all of them, from the **same session**:

| File | Where | What it gives |
|---|---|---|
| D2RLoader crash reports | `<game>\d2rloader\crashes\` (`d2r-crash-report ...`, `d2r-lastresort-crash-report (<date> UTC pid-N).log`) | Exception, RIP, registers, raw stack slots |
| D2RLoader log | `<game>\d2rloader\logs\d2rloader.log` | Plugins loaded, table compile (a cache-building start?), `BC_ASSERT` / `BC_VERIFY` lines just before the crash |
| Your plugin's log | `<game>\mods\<Mod>\d2rloader\logs\` | Version, image stamp, parts on/off, other plugins, your last line before the crash |
| D2R's log | `blz-log.txt` | `File not found "..."` floods (missing assets) |
| What was on screen | the player | Which area, which monster, which action (opening a panel, picking up an item) |
| Versions | the player, or the logs | D2R version and store (Steam / Battle.net), your mod's version, other plugins |

A plugin log that just stops with nothing unusual means the crash was outside your logged code: you need the
crash report.

## 2. Read the report

A last-resort report looks like this (shortened):

```text
D2R last-resort crash report
First-Chance Exception
  Code: 0xC0000005 (EXCEPTION_ACCESS_VIOLATION)
  Address: 0000000140F3D028
  Allocation Base: 0000000140000000 + 0xF3D028
  RIP: 0x0000000140F3D028
Exception Parameters
  [00] 0x0000000000000001        <- 0 = read, 1 = write, 8 = execute
  [01] 0x0000000000000000        <- the address it touched
Registers
  RIP=0x0000000140F3D028 ... R8 =0x0000000000000000 ... R15=0x0000000000000000
Stack Slots
  +0x018  0x0000000140F6F0A2
  +0x048  0x0000000140E01234
  ...
```

1. **Which module.** `Allocation Base 0000000140000000` is D2R.exe (image base `0x140000000`). The RVA is
   `RIP - 0x140000000` (here `0xF3D028`). Another base is a DLL: your plugin, D2RLoader's D2RCore, a driver.
2. **What went wrong.** Parameter 0 says read or write, parameter 1 the address. A small address (0 to a few
   thousand) is a null pointer plus a field offset: `null + 0xc7` means "read field 0xc7 of a missing record".
   A value like `0x8000003F0` that is not a pointer means stale memory (see section 6).
3. **Which register was null.** Find the instruction at the RVA (`Disasm.java 0x<rva>`) and see which register it
   dereferences. That register's value in the report is the bad pointer; trace back where it came from.
4. **Return addresses.** Stack slots holding values inside the image (`0x140000000` .. `0x140000000 +
   SizeOfImage`) are candidates. A real return address follows a `call` instruction. Stack slots also hold stale
   values from earlier calls: trust a chain that makes sense (each caller calls the next), not every slot.

The script below does steps 1 and 4 on a dump: it maps the RIP and every image value in the stack to the function
holding it (from the dump's `.pdata`) and marks values that follow a call.

```python
# Crash report -> RVAs -> functions (.pdata of a memory-layout dump). Python 3.12+, stdlib only.
#   python triage.py <crash report .log> <dump.exe>
import re, struct, sys, bisect

report, dump = sys.argv[1], sys.argv[2]
text = open(report, encoding="utf-8", errors="replace").read()
img = open(dump, "rb").read()

pe = struct.unpack_from("<I", img, 0x3C)[0]
base = struct.unpack_from("<Q", img, pe + 0x30)[0]          # OptionalHeader.ImageBase
size = struct.unpack_from("<I", img, pe + 0x50)[0]          # SizeOfImage
pdata_rva, pdata_size = struct.unpack_from("<II", img, pe + 0x18 + 0x70 + 3 * 8)  # data directory 3: exceptions
funcs = sorted(struct.unpack_from("<III", img, pdata_rva + i)[:2] for i in range(0, pdata_size, 12))
starts = [b for b, _ in funcs]

def where(rva):
    i = bisect.bisect_right(starts, rva) - 1
    if i >= 0 and funcs[i][0] <= rva < funcs[i][1]:
        return f"in function 0x{funcs[i][0]:x} (+0x{rva - funcs[i][0]:x})"
    return "no .pdata entry (data, or a leaf function)"

rip = int(re.search(r"RIP[=:]\s*0x([0-9A-Fa-f]+)", text).group(1), 16)
print(f"RIP   0x{rip - base:x}  {where(rip - base)}")
for slot, value in re.findall(r"\+0x([0-9A-F]+)\s+0x([0-9A-F]{16})", text):
    v = int(value, 16)
    if base <= v < base + size:
        rva = v - base
        # a return address follows a call: E8 rel32 (5 bytes) or FF /2 (2-7 bytes)
        is_ret = img[rva - 5] == 0xE8 or any(img[rva - k] == 0xFF for k in (2, 3, 6, 7))
        print(f"+0x{slot}  0x{rva:x}  {where(rva)}{'  <- return address' if is_ret else ''}")
```

The dump must be **of the same image** as the player's (same PE timestamp; `tools/dump_image.py` writes sections
at their RVAs, so file offset = RVA). A Steam and a Battle.net build with the same stamp are the same image.
`.pdata` entries can be fragments of a function (chained unwind info), so the "function" start may be a few bytes
after the real entry: confirm in Ghidra.

## 3. Find the function and its job

With the RVAs:

| Step | Script | What you learn |
|---|---|---|
| Decompile the faulting function and each caller in the chain | `Decompile.java <rva>...` | What it does, which argument was null |
| Disassemble around the RIP | `Disasm.java <rva>` | The exact instruction and register |
| Find its source file | `StringXrefs.java <text>` on strings it uses; D2R's asserts name files (`D2Render\src\model_instance.cpp`) | Which subsystem: renderer, sound, items, DRLG ... |
| Who else calls it | `Refs.java <rva>` | Whether one caller or many hit the same path |
| Which record field it reads | `FindDisp.java <offset>` | Which table column feeds the bad value |

Typical register stories:

| What you see | Likely cause |
|---|---|
| Read of `null + small offset` right after a record getter | An id outside the table (`-1` from an unresolved name, a row number past the end): check the table the getter reads |
| Write to address 0 right after an allocator call | The allocator returned null: a pool ran out (section 5) |
| `BC_ASSERT` in d2rloader.log just before the crash | The assert names the file and line; the crash is its consequence |
| A non-pointer like `0x8000003F0` | Stale or freed memory: an engine race, or memory your plugin moved |
| RIP inside your plugin | Your code: build with symbols and map the offset |
| RIP inside D2RCore | A D2RLoader hook trampoline or service: check what your plugin patched that D2RLoader also reads |

## 4. Was it the mod, the data, or the machine?

Bisect before reading code:

1. **Your plugin**: the off-switch file (`<plugin>-off.txt`) leaves parts out; halve until the crash goes.
2. **Other plugins**: your log's "other plugins" line. Ask the player to remove them and retry.
3. **The environment**: `default_mod` pointing at another mod, an old copy of your plugin, a cache-building first
   start ([d2rloader.md](d2rloader.md)).
4. **The data**: the faulting function reads a table; find the row (an id out of range, a missing HD asset, a
   wrong function number).

Not every crash report is a crash:

| Report | Meaning |
|---|---|
| `Record: 1 of at most 2`, first-chance access violation in a non-D2R module, written within a second of the game closing | A handled exception in a system or driver module at shutdown. Noise |
| `D2Prism ... "Present failed"`, `systemCode 0x887A0005` (`DXGI_ERROR_DEVICE_REMOVED`) | The GPU driver dropped the device. The machine, not the mod |
| A real crash | A report whose module is D2R.exe (or your DLL) during play, or a `d2r-crash-report` before the end of the session |

## 5. Worked example: a renderer pool crash

**Report.** A player crashed in a busy fight. Access violation **writing** address 0 at `D2R.exe+0xF3D028`.
`R15 = 0`, `R8 = 0`. One of the stack values in the image is `+0xF6F0A2`.
The plugin log had nothing unusual before the crash.

**Instruction.** `0xf3d028: mov [r15], rax` in `0xf3cfd0`: moving 0x80-byte records into a new vector buffer.
The vector grow (`0xf3d587`) is called from code whose strings name `D2Render\src\model_instance.cpp`: a model
instance being built. So R15 is the new buffer, and it is null.

**Where the buffer came from.** The return address `+0xF6F0A2` was still on the stack: it is inside `0xf6f060`, the
**Renderer Pool** allocate wrapper. It calls the pool object's vtable `+8`; on a null answer it only reports
"Failed to allocate from Renderer pool!" (no break without a debugger) and returns null. The caller writes through
it.

**Why null.** The pool is one fixed **12 MB TLSF arena** made once at renderer start (`0xf6efe0`, `0xc00000`
bytes, names "Renderer Pool" / "TlsfAlloctor"). A scene with many more models, missiles and overlays than vanilla
(for example a monster that spawns several traps per attack, each with a missile and an explosion) can fill it. No table row was
out of range.

**Fix.** A plugin points the one pool object at a **copy** of its vtable (the vtable is shared with other
allocators, so it is not patched in place) whose allocate and reallocate entries fall back to the process heap
when D2R answers null, and whose free / size / realloc entries send pointers from outside the pool's range back to
the heap. Log the first, 100th and 10,000th fallback and show counts in the status command. Details:
[engine-limits.md](engine-limits.md) "Memory" and [d2r-93847-addresses.md](d2r-93847-addresses.md).

**If it comes back**, the plugin log tells you whether the pool filled and how fast.

## 6. Other patterns seen

| Crash | Cause | Fix |
|---|---|---|
| Client read of `null + 0xc7` in a sound reader when a monster comes into view | Sound id -1 (an unresolved name, or the cache-building start loaded D2R's sounds table) | Plugin sound guard; restart after the first start |
| `BC_ASSERT: hUnit`, then a null read in the client's item packet handler, right after a kill | The server's item serializer wrote nothing: a property put a layer on a stat with no `Save Param Bits` | Clear that property's parameter in the data ([saves.md](saves.md)) |
| `UnitGetType(hUnit) == UNIT_ITEM` (ItemMode.cpp:676) when picking up or wearing an item | Item type ids shifted: D2R's built-in type test hits the wrong type | D2R's rows at D2R's ids ([porting-classic-mods.md](porting-classic-mods.md)) |
| Null read (`[rdx+0x50]`) entering a maze level | `levels.txt` `Position 1` on a maze level with no entry rooms in its presets | Clear `Position` there ([tables.md](tables.md)) |
| Access violation at `D2R.exe+0x7761DC` while a character loads, payload pointer is not a pointer | D2R's own stale event in its HD unit-translation layer | Rare; a plugin guard can skip events whose payload is not readable memory |
| Crash at a vendor after a flood of `File not found ".../[default_unit]"` | Item without an HD picture entry | [hd-graphics.md](hd-graphics.md) |

## 7. Close the loop

- Write the cause and the evidence (report, log lines, the instruction) in your bug list.
- Add a check or a self-check that would catch it again (a build-time validator, a plugin startup check).
- If you could not reproduce it, say so in the fix notes and say what the log will show if it comes back.
