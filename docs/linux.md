# Running and testing D2R mods on Linux

How to build, run and test a D2R mod on Linux with Proton or Wine, without a human at the keyboard and without the
game ever touching your desktop. Everything here was learned by moving a large overhaul port from Windows to Linux,
running its whole test suite in game copies under Proton, and shipping releases built on Linux.

The game, D2RLoader and its plugins are Windows programs. They run under **Proton** (Valve's Wine build; Proton
Experimental 11 was used), each test copy in its own Wine prefix. The Python tools run natively. Measurements below
were taken with D2R 3.4 (build 95032) and D2RLoader 1.3.1 on an AMD GPU with 16 GB of VRAM. The mechanisms are not
tied to that build; the numbers are.

Helpers in this repo:

| File | What it does |
|-|-|
| [`testing/linux_env.py`](../testing/linux_env.py) | The private display environment, the VRAM cap, Wine's no-focus registry values, graphics profiles, the GPU gate, and a driver for the window helper |
| [`testing/profiles/`](../testing/profiles/) | `test.json` (low, 30 fps) and `visual.json` (high): the graphics keys of D2R's `Settings.json` |
| [`testing/wctl/`](../testing/wctl/) | `wctl.c`, a small Win32 helper that runs inside a slot's prefix and talks to the game's windows and harness block; `build.sh` builds it on Linux without Visual Studio or Wine |
| [`testing/game_slots.py`](../testing/game_slots.py) | Slot locks, **shared seats** (a machine-wide game budget), and on Linux the Wine process lookup |

Contents: [What works](#what-works) · [Slots under Proton](#slots-under-proton) ·
[The online check](#the-online-check-cookie-and-machine-id) · [A private display per game](#a-private-display-per-game) ·
[Focus](#focus-never-the-desktop) · [The window helper](#the-window-helper) ·
[Harness input under Wine](#harness-input-under-wine) · [Graphics profiles and VRAM](#graphics-profiles-and-vram) ·
[How many games at once](#how-many-games-at-once) · [GPU gate](#gpu-gate-for-offline-jobs) ·
[Building plugins on Linux](#building-plugins-on-linux) · [Third-party plugins under Proton](#third-party-plugins-under-proton) ·
[HD viewer in Chrome](#a-webgpu-viewer-in-chrome-on-linux) · [Files written on Linux](#files-written-on-linux) ·
[Troubleshooting](#troubleshooting)

## What works

| Part | On Linux |
|-|-|
| `modkit` (fetch, build, validate, install) | Native Python. CascLib builds with CMake as on Windows |
| D2R + D2RLoader + plugins | Under Proton, in a prefix per test slot. Data mods and plugin mods both run |
| Scripted game checks | End to end: launch, calibration screens, intro, main menu, a new character, in-game checks, quit, restore |
| Clicks, keys, typed text, harness console | Yes, through the plugin's harness, once its input hooks accept Wine's syscall stubs ([below](#harness-input-under-wine)) |
| Screenshots | From the game's private X display (XComposite). `PrintWindow` returns black under Wine |
| Several games at once | Yes. VRAM is the first limit ([below](#how-many-games-at-once)) |
| Plugin builds | MSVC under Wine (msvc-wine), or clang + lld-link for small helpers |
| Releases | Packaging and upload work; watch the [7z file modes](#files-written-on-linux) |
| The player downloader | Still a Windows program. Players on Linux run it in their game's prefix |

## Slots under Proton

A slot on Linux is two folders: the **game copy** (D2R, D2RLoader, the mod) and its **Wine prefix**. Give every slot its
own prefix: a prefix has one wineserver, and two games in one prefix share windows, named objects and the registry.

```
<work>/slots/s2/          the game copy: D2R.exe, D2RLoader-s2.exe, mods/<Mod>/ ...
<work>/prefixes/s2/       STEAM_COMPAT_DATA_PATH for Proton; the Wine prefix is <work>/prefixes/s2/pfx
<work>/prefixes/s2/pfx/dosdevices/d:  -> <work>/slots/s2       the game copy is drive D: inside the prefix
```

- **Launch:** `STEAM_COMPAT_DATA_PATH=<prefix> STEAM_COMPAT_CLIENT_INSTALL_PATH=<steam> <proton>/proton run
  'D:\D2RLoader-s2.exe' <args>`. Helpers that must share the game's wineserver (the window helper) run the same way.
  Stopping: `WM_CLOSE` to the window first, then `wineserver -k` with that prefix's `WINEPREFIX` only. If the
  wineserver is already gone after a crash, its client processes can stay: kill the processes whose `WINEPREFIX` is that
  prefix, nothing else.
- **Processes:** a Wine process's `argv[0]` is its Windows path (`D:\D2RLoader-s2.exe`), and its `WINEPREFIX` plus the
  prefix's `dosdevices` link maps it to the Linux file. `game_slots.processes()` does this on Linux, so the rule "match a
  process by the folder its program lives in, never by name" still holds. D2RLoader 1.3.1 on D2R 3.4 runs the game
  inside its own process: the loader's pid is the game's pid.
- **Saves and settings** live inside the prefix: `<pfx>/drive_c/users/steamuser/Saved Games/Diablo II Resurrected/`.
  `Settings.json` is there too, so a setting a run changes (a key pressed on a calibration screen) stays for the next run
  unless you write it back ([graphics profiles](#graphics-profiles-and-vram) do that before every launch).
- **Restore after each run:** the game rewrites `Data/data/*.idx` and `shmem`, and D2RLoader writes files under `Data/hd/`.
  Snapshot the whole slot folder before a run except the big CASC archives (`Data/data/data.NNN`; keep a size and mtime
  manifest of those instead), and after the run copy it back with `rsync --delete --checksum`, then check with a checksum
  dry run that must list nothing. Copy the logs and crash reports out **before** the restore, or it deletes them.
- **Copying a slot:** copy the game folder and the prefix, relink the prefix's `d:` to the new game folder, rename the
  save folder for the new slot number, and run `game_slots.py setup N` so `savepath` and the loader config point at it.

## The online check: cookie and machine id

D2R asks to go online every 30 days. Offline under Proton the game shows "not been online in the last 30 days" after
the intro, and OK quits. D2RLoader avoids it by decrypting the Battle.net client's `cookie.bin`
(`<pfx>/drive_c/users/steamuser/AppData/Local/Blizzard Entertainment/ClientSdk/`) from **your own** Battle.net prefix,
the one where you log in and play. Its key comes from two machine values, and a fresh test prefix has neither:

| Value | Where | What to do |
|-|-|-|
| Volume serial of the loader's drive | Wine makes it from the drive letter when the drive has no `.windows-serial` file | Use the same drive letter layout as your Battle.net prefix. Check with `wine cmd /c vol D:` in both |
| `MachineGuid` | `HKLM\Software\Microsoft\Cryptography` in the prefix's `system.reg` | Every prefix gets its own. Set the test prefix's value to your Battle.net prefix's value, with the prefix stopped, and keep a backup of the old `system.reg` |

Then copy `cookie.bin` unchanged into the same path of each test prefix (mode 600). It expires like the original: copy
it again when the 30-day dialog comes back. A wrong key shows as a `Botan::Decoding_Error` from D2RLoader, then the
dialog.

These are your account's credentials. **Never** print, log, commit, decode or upload the cookie or the `MachineGuid`:
compare them by hash, keep them out of every report and screenshot, copy them only into your own test prefixes, and
delete the copy when you delete the prefix. If agents do this step, give them an explicit, written rule naming the
exact prefixes they may copy into.

## A private display per game

Run every test game on its own **Xvfb** display, never on your desktop. On a real desktop the loader's first dialog and
the game window took the keyboard focus within seconds, whatever the prefix's Wine settings said.

```sh
python testing/linux_env.py free-display               # e.g. 100: the first :N with no X socket
Xvfb :100 -screen 0 1920x1080x24 -nolisten tcp &
env $(python testing/linux_env.py env --display 100 --vram-mb 2048) <proton> run 'D:\D2RLoader-s2.exe'
```

`linux_env.PRIVATE_DISPLAY_ENV` holds the two settings that matter, both measured:

| Setting | Why |
|-|-|
| `MESA_VK_WSI_DEBUG=sw` | Xvfb has no DRI3, so Mesa's Vulkan driver must present by CPU copy |
| `VKD3D_DISABLE_EXTENSIONS=VK_KHR_present_wait,VK_KHR_present_wait2` | vkd3d-proton paces frames with present_wait, and those waits never end on Xvfb. With `sw` alone the frame stays black and the game's UI thread stalls (a few `PeekMessage` calls a minute, the harness console never answers) |

- **Window placement:** put the window at 0, 0 of its own display and make the screen bigger than the client plus its
  frame (`linux_env.screen_for`). A window that reaches past the screen edge still runs, and clicks still land, but the
  part past the edge is never drawn: shots lose the HUD strip and pixel checks fail.
- **Screenshots:** capture the game window's own X pixmap with XComposite and cut it to the client size, or the private
  display's root cut to the client rectangle. `PrintWindow` is black under Wine (WineHQ bug 46257). Never capture the
  desktop display.
- **Stopping:** remember the Xvfb pid with the run, and check its command line (`Xvfb :N`) before you signal it.
- **Costs:** a game on Xvfb uses more CPU than on Windows (the CPU-copy present; 3 to 4 cores on average at high
  settings, 1.5 at the low test profile). The Xvfb servers themselves are cheap.

## Focus: never the desktop

Write these into each test prefix's `user.reg` (`HKCU\Software\Wine\X11 Driver`), with the prefix stopped:

```sh
python testing/linux_env.py nofocus <work>/prefixes/s2/pfx/user.reg
```

`UseTakeFocus=N`, `GrabPointer=N`, `GrabFullscreen=N`. They help, but on their own they were **not** enough: the loader's
dialog still took focus at about 3 s. So treat them as a second line behind the private display, and **measure** every
run: poll `xprop -root _NET_ACTIVE_WINDOW` on your desktop display from before the launch to the end (every 0.2 s is
plenty), and fail the run if it ever becomes a window whose `_NET_WM_PID` belongs to the slot's prefix or whose
`WM_CLASS` names an `.exe`. Keep the result (`focus.json`) with the run. In every measured run on private
displays the desktop's active window never changed.

A watchdog that on Windows checks "the user's cursor is in the game window" has nothing to look at here (the game is on
another display): watch the focus record instead.

## The window helper

Window handles, Windows process ids and the plugin's named shared memory live inside the game's wineserver, so a Linux
program cannot reach them. [`testing/wctl/wctl.c`](../testing/wctl/wctl.c) is a 250-line Win32 console helper that runs
inside the slot's prefix (same `proton run`, same private `DISPLAY`) and answers one TAB-separated request per line:
find a window, its client rectangle and X window id, post a message, size a window, list buttons of a dialog, list
processes, and read or write the harness block (`vopen NAME [MAGIC]`, `vget`, `vset`, `vtext`).
`linux_env.Wctl` keeps one `wctl.exe serve` open per run.

It can never take focus: it has no `SetForegroundWindow`, `SetActiveWindow`, `SetFocus`, `ShowWindow`, `SendInput` or
`SetCursorPos`; its `pos` always adds `SWP_NOACTIVATE`, drops `SWP_SHOWWINDOW`, and turns any z-order but `HWND_BOTTOM`
into `SWP_NOZORDER`. It uses `SWP_ASYNCWINDOWPOS`: a synchronous `SetWindowPos` waits for the game's thread and hung a
run once when that thread was busy. Give every request a timeout (20 s) and end the helper on a miss instead of
hanging the run.

D2RLoader's "notice" task dialog is answered with a posted `TDM_CLICK_BUTTON`. Under Wine its check box is a `Button`
too: click only push buttons (`BS_TYPEMASK` 0 or 1).

```sh
MSVC_ROOT=<msvc-wine tree> testing/wctl/build.sh     # clang --target=x86_64-pc-windows-msvc + lld-link; no Wine
```

## Harness input under Wine

The [harness protocol](testing.md#the-harness-protocol) is the same on Linux. Three things in a typical harness plugin
need care under Wine.

**1. Syscall stubs.** A harness that feeds the game a virtual cursor often hooks the `win32u` syscall stubs
(`NtUserGetCursorPos`, `NtUserGetAsyncKeyState`, `NtUserPeekMessage` ...), so every caller, user32's own code too, sees
the virtual input. A careful hook checks the stub's bytes first and leaves an unknown shape alone. Wine's stubs have
the same 16-byte head as Windows but a different tail, so a Windows-only check refuses every stub and the harness gets
no input at all:

```
head (both):  4c 8b d1 / b8 <ssn32> / f6 04 25 08 03 fe 7f 01     mov r10,rcx; mov eax,ssn; test byte [7ffe0308],1
Windows:      75 03 / 0f 05 / c3 / cd 2e / c3                          jne +3; syscall; ret; int 2e; ret
Wine/Proton:  75 03 / 0f 05 / c3 / eb 01 / c3 / ff 14 25 00 10 fe 7f / c3
              jne +3; syscall; ret; jmp +1; ret; call [7ffe1000] (Wine's syscall dispatcher); ret
```

The head has nothing rip-relative, so a 14-byte absolute jump over bytes 0 to 13 with the head copied to the trampoline
works on both. Accept both tails and nothing else:

```cpp
enum class StubShape { Unknown, Windows, Wine };

inline StubShape ClassifyStub(const uint8_t* c) noexcept {
	static constexpr uint8_t head[16] = {0x4c, 0x8b, 0xd1, 0xb8, 0, 0, 0, 0, 0xf6, 0x04, 0x25, 0x08, 0x03, 0xfe, 0x7f, 0x01};
	for (int i = 0; i < 16; ++i) if ((i < 4 || i >= 8) && c[i] != head[i]) return StubShape::Unknown;  // 4..7: the number
	static constexpr uint8_t common[5] = {0x75, 0x03, 0x0f, 0x05, 0xc3};
	for (int i = 0; i < 5; ++i) if (c[16 + i] != common[i]) return StubShape::Unknown;
	if (c[21] == 0xcd && c[22] == 0x2e && c[23] == 0xc3) return StubShape::Windows;
	static constexpr uint8_t wine[11] = {0xeb, 0x01, 0xc3, 0xff, 0x14, 0x25, 0x00, 0x10, 0xfe, 0x7f, 0xc3};
	for (int i = 0; i < 11; ++i) if (c[21 + i] != wine[i]) return StubShape::Unknown;
	return StubShape::Wine;
}
```

Log one line with the result (`33 win32u input calls traced, 33 of them Wine stubs`), so a run shows at once whether the
harness has input.

**2. An import fallback.** When a stub cannot be hooked (another hook got there first, a future layout), redirect the
game exe's import of the matching `user32` function instead (`GetCursorPos`, `GetAsyncKeyState`, `PeekMessageW`,
`GetForegroundWindow`, `SetFocus` ...). It only sees the game's direct calls, which is enough for the input it reads.
Never both for one function: the hook would run twice. A harness word that forces the import path lets you test it on a
system whose stubs do hook.

**3. Fake focus off by default.** Some harnesses make the game believe it has focus (drop `WM_ACTIVATEAPP(FALSE)` and
`WM_KILLFOCUS`, answer `GetForegroundWindow` with the game window), so it keeps reading input in the background. On a
private Xvfb display the game may hold that display's real focus, and with fake focus on, startup hung at "Initializing
graphics context" every time. Make it opt-in with a harness word. D2R under Proton called none of
`GetForegroundWindow`, `GetFocus` or `GetActiveWindow` in a traced run: only the messages matter there.

**Also:**

- **Loader lock deadlock.** A diagnostic logger that names modules (`GetModuleHandleEx`, `GetModuleFileName`) while it
  holds its own lock deadlocks under Wine: Wine takes the loader lock for those calls, and a thread that is loading a DLL
  holds the loader lock and then waits for the logger's lock. The game hung at graphics init. Build the whole line
  (module names included) **before** taking the logger's lock.
- **Posted keys work.** `WM_KEYDOWN` / `WM_KEYUP` / `WM_CHAR` posted to the game window arrive (the game takes them with
  `PeekMessage`): calibration screens, skipping the intro with a held Esc, typing a character name.
- **The calibration screens** (gamma, screen space, colour blind) come back on every launch of a fresh prefix. Click
  through them in the check, or write the matching keys in `Settings.json` once.
- **First-chance exception reports.** D2R's last-resort handler also writes a "crash report" for a first-chance access
  violation that a plugin catches itself (an SEH-guarded probe of game memory). One per launch in a module near the top
  of the address space looked like Wine; it was the project's own plugin probing a pointer pair. Read only memory that
  `VirtualQuery` says is committed and readable, with plausible-pointer checks, and the reports stop. Keep a list of
  known handled reports (exception code, module offset, read address) so a run summary tells them from real crashes.

## Graphics profiles and VRAM

D2R's graphics settings live in `Settings.json` in the save folder, inside the prefix, so they outlast a slot restore.
Write a profile's keys before every launch, with the prefix stopped:

```sh
python testing/linux_env.py profile "<pfx>/drive_c/users/steamuser/Saved Games/Diablo II Resurrected/mods/<Mod>-s2/Settings.json" test
```

| Profile | Settings | Use it for |
|-|-|-|
| `test` | 1600x900 windowed, Resolution Scale 50, 30 fps cap, VSync off, textures, shadows, AO, AA and VFX at 0 | Functional, harness, UI and tooltip checks. **UI text draws at the full window resolution whatever the scale**: panels, tooltips and hover labels read the same as at high settings |
| `visual` | Resolution Scale 100, textures / shadows / AO / VFX 4, AA 3, VSync on | Anything judged on the 3D world's pixels: HD effects and models, item and monster art, tints. At half scale an effect is visible but blurred: fine to see that it plays, not to judge how it looks |

Pick the profile from the checks in a run: `visual` when any check judges looks, else `test`.

**Per game, measured** (one game alone, in town and in an Act 1 field):

| | `test` | `visual` |
|-|-|-|
| VRAM | 4.2 to 4.4 GB, 3.6 GB with the VRAM cap below | about 5.1 GB, 4.4 GB with the cap |
| GPU busy | 10 to 17% | 60 to 75% (uncapped frame rate on Xvfb) |
| CPU | 1.5 to 1.8 cores | 3 to 4 cores, 10 to 13 at load |
| RAM (PSS) | about 3.7 GB | about 3.7 GB |

The low profile cuts GPU and CPU to a quarter or a half, but **VRAM only by about 0.9 GB**: most of a game's VRAM is
fixed. Under Linux a game used about 0.7 GB more VRAM, less RAM and two to three times the CPU of the same game on Windows.

### The VRAM cap

Under Proton, D3D12 runs on vkd3d-proton with DXVK's `dxgi.dll`. DXVK's `dxgi.maxDeviceMemory` (MB) lowers the adapter's
`DedicatedVideoMemory`, and D2R sizes its streamed texture pool from that:

```sh
DXVK_CONFIG="dxgi.maxDeviceMemory = 2048"     # linux_env.game_env(vram_mb=2048) adds it, keeping other options
```

- **What it does:** a DXGI probe in a slot prefix showed `DedicatedVideoMemory` following the cap (3072 / 2048 / 1024).
  `QueryVideoMemoryInfo` ignores every DXVK option, `dxvk.maxMemoryBudget` included, so you cannot check the cap from
  inside the game that way. The game's DXVK log says `Found config env: dxgi.maxDeviceMemory = N`.
- **How D2R reacts:** the cap trims only the streamed texture pool, 0.6 to 0.8 GB per game. Below about 2 GB on `test`
  nothing changes any more. **The floor is about 3.6 GB per game**, and 2.9 to 3.0 GB of it is in place at the main menu,
  before the world loads: UI and font atlases, shaders and pipeline state, the menu scene. Render targets at 1600x900
  are only tens of MB, and the CPU-copy present keeps its images in system memory, not VRAM.
- **How it looks:** `test` shots look the same at every cap down to 512 MB. `visual` at 3072 looks the same as uncapped;
  at 2048 tiles rendered black (a black checkerboard over part of the world). So `visual` needs a margin.
- **Recommended:** `test` 2048 MB, `visual` 3072 MB. Log the cap with every run.

### Spill instead of crash

When the games together need more VRAM than the card has, amdgpu moves buffers to system memory (GTT), as WDDM does on
Windows. Watch it per game in `/proc/<pid>/fdinfo/*` (`drm-memory-vram`, `drm-memory-gtt`) and for the card in
`/sys/class/drm/card*/device/mem_info_vram_used` (`linux_env.vram_used_mb`). Measured with 4 capped `test` games on a
16 GB card: VRAM full, about 0.7 GB spilled to GTT, every game still at its 30 fps cap with clean shots, and all 4 passed
their checks. Without the cap, 4 games spilled 4.1 GB; at high settings one of 3 uncapped games crashed when VRAM filled.

## How many games at once

The order in which limits were hit, on 32 threads, 62 GB of RAM and a 16 GB GPU:

1. **VRAM.** About 3.6 GB per capped `test` game, plus your desktop (2 to 3 GB). 3 capped `test` games used 82%,
   1 `visual` + 2 `test` 85%. 4 filled it and spilled a little, and still passed.
2. **GPU time** at high settings: two uncapped games kept the GPU at 99% and the desktop lagged. Cap the frame rate
   (the `test` profile) before adding a game.
3. **Start-up bursts.** Loaders compiling the mod's tables together pushed idle desktop pages into swap (about 5 GB in a
   few seconds with three loaders) while 30 GB was still free. **Stagger launches by 20 to 30 s.** With a zram swap the
   growth was harmless (no game process had anything swapped), so a "swap grew by more than N MB" stop rule should count
   the game processes' own `VmSwap`, not the machine total.
4. RAM and CPU were never close.

Twice in 13 multi-game launches a game hung during start-up while another game was loading (its log went quiet in the
mod compile); never in 19 single launches. A retry got in each time. Give start-up a timeout and retry once.

A good rule while someone uses the PC: as many capped `test` games as fit in VRAM with the desktop's share left over, at
most one `visual` game, and count a `visual` game as two. Put the number in a shared budget so that every project on the
machine respects it: see [shared seats](testing.md#shared-seats-a-game-budget-for-the-whole-pc).

## GPU gate for offline jobs

Before a GPU-heavy offline job (renders, bakes, a model viewer's batch), wait until the GPU and CPU are quiet, so a
running test game or the desktop is not starved. Check the slot locks first (a held slot means a game may be starting).

```sh
python testing/linux_env.py gate             # exit 0: quiet; 1: wait (GPU >= 20% or load >= 60% of the CPUs)
```

It reads `/sys/class/drm/card*/device/gpu_busy_percent` (amdgpu; the middle value of five samples of the busiest card) and the
one-minute load average divided by the logical CPU count. A driver without `gpu_busy_percent` reports no GPU reading,
and an unknown reading never blocks.

## Building plugins on Linux

**MSVC under Wine** builds D2RLoader plugins that match the Windows builds. Use
[msvc-wine](https://github.com/mstorsjo/msvc-wine) (it downloads MSVC and the Windows SDK under Microsoft's licence; keep
it out of your repository), its own Wine prefix (not a game prefix), Ninja and CMake:

```sh
cmake -G Ninja -S plugin-template -B build/plugin -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_COMPILER=<msvc-wine>/bin/x64/cl -DCMAKE_CXX_COMPILER=<msvc-wine>/bin/x64/cl \
      -DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded
cmake --build build/plugin
```

- **`/Zi` fails under Wine** (C1902, the PDB server): use embedded debug info (`/Z7`,
  `CMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded`).
- A CMake project that defaults to the Visual Studio generator on Windows may link its DLLs differently under Ninja; set
  the same linker options explicitly (for example `/SUBSYSTEM`) and compare exports, imports and sections with the
  Windows build.
- **Tests run under Wine.** A few tests of memory guards that ask Windows' memory APIs (`VirtualQuery` on odd regions)
  fail under Wine 9 and pass on Windows. Report them, don't let them stop the build.
- **Reproducible builds:** with `/Brepro` the bytes depend only on the inputs. msvc-wine may carry a newer MSVC than your
  Windows build PC; sizes then differ by a few percent and the newer static CRT can add imports. Pin the version if you
  need identical DLLs.
- **Line endings:** `core.autocrlf=true` on Windows turns LF into CRLF in files that `git archive` / `git apply` write,
  and those can end up inside a DLL or a shipped config. On Linux they keep the committed bytes. Both usually parse the
  same, but a byte comparison between a Windows and a Linux build will show it.
- Third-party plugins: build them from the pinned upstream commit plus your patches, exactly as the Windows script does,
  so both produce the same package. Some upstream CMake files require PowerShell for steps you never run; point that
  variable at a stub that fails if anything calls it.
- **Small helpers** without MSVC's runtime (such as `wctl.exe`): `clang --target=x86_64-pc-windows-msvc` plus
  `lld-link`, with the MSVC and SDK headers and libraries from the msvc-wine tree. No Wine needed.

## Third-party plugins under Proton

A plugin that works on Windows can crash under Proton because the prefix is not a full Windows install. Example from a
damage-numbers plugin built on Dear ImGui:

- **Symptom:** D2R crashed on the plugin's first hooked `Present` ("Present failed"), every start, only under Proton.
- **Cause:** the renderer loaded 12 fonts from `%WINDIR%\Fonts` before the first frame. A Proton prefix has no
  `segoeui.ttf` and several other Windows fonts. Dear ImGui 1.91.5 reports a font file it cannot open through its error
  recovery, which opens an error tooltip (`ImGui::Begin`); outside a frame and with no font yet, that reads a null font.
- **Found by:** a diagnostic build whose `IM_ASSERT` logs a stack. Not the fallback font, not vkd3d, not the hook.
- **Fix (a patch to the plugin):** check that a font file exists and is a font before `AddFontFromFileTTF`, use ImGui's
  built-in font otherwise, and build the atlas before the D3D12 backend uploads it. With every font present nothing
  changes, so Windows players are not affected.

Check any plugin that reads Windows system files (fonts, `%WINDIR%` paths, registry keys a Windows install has) for the
missing case. Installing Microsoft's fonts into test prefixes only hides the bug from you; players on Linux will hit it.

## A WebGPU viewer in Chrome on Linux

A browser tool that uses WebGPU (for example a local HD model viewer) needs WebGPU switched on in Chrome on Linux. In
`chrome://flags`, enable **Unsafe WebGPU Support**, **Vulkan**, and the **WebGPU interop** flag for your Chrome version
(search the flags page for "WebGPU" and "Vulkan"; names move between versions), then restart Chrome. Check with
`navigator.gpu.requestAdapter()` in the console. Serve the page over `http://127.0.0.1`: browsers block module and fetch
loads from `file://` URLs.

## Files written on Linux

- **7z file modes.** 7-Zip on Linux stores each entry's Unix mode, so the umask (002 on some distributions) changes the
  archive's bytes, and a content-addressed server rebuild no longer matches. `release/package_release.py` and
  `release/dl_ingest.py` set files to 0644 and folders to 0755 before archiving.
- **Text files** written on Linux have LF line ends. D2R and D2RLoader read both; byte comparisons with a Windows build
  will differ.
- **Case:** Linux paths are case-sensitive and Windows paths inside the prefix are not. Keep mod file names in one case.

## Troubleshooting

| Symptom | Cause and fix |
|-|-|
| "Not been online in the last 30 days" after the intro, OK quits | The test prefix cannot decrypt the cookie: copy it again (expired), or the `MachineGuid` / drive letters differ from your Battle.net prefix. D2RLoader logs `Botan::Decoding_Error` |
| Black frames, harness console never answers | Xvfb without `VKD3D_DISABLE_EXTENSIONS=VK_KHR_present_wait,VK_KHR_present_wait2` |
| Startup hangs at "Initializing graphics context" | The harness's fake focus on a private display (make it opt-in), or a logger deadlocking on the loader lock |
| Harness reports no input hooks under Proton | Its stub check only knows the Windows tail. Accept Wine's ([above](#harness-input-under-wine)) |
| Shots are cut off at the right and bottom | The window reaches past the private screen's edge: place it at 0, 0 and size the screen to the client plus its frame |
| Screenshots are black | `PrintWindow` under Wine. Capture the X window (XComposite) or the private display's root |
| The game takes focus on your desktop | It runs on your desktop display. Run every game on a private Xvfb display; the Wine registry settings alone are not enough |
| A slot stays locked after a crash | The wineserver died and left clients: kill the processes whose `WINEPREFIX` is that slot's prefix, then unlock |
| The next run's calibration screen looks different | A key changed `Settings.json` in the prefix's save folder, outside the slot restore. Write the profile before every launch |
| Plugin crashes on the first Present only under Proton | It reads a Windows file the prefix lacks (fonts). See [above](#third-party-plugins-under-proton) |
| Archive from Linux differs from the server's rebuild | The umask in 7z modes. Use the packager's fixed modes |
| A third game crashes or VRAM is full | Cap VRAM (`dxgi.maxDeviceMemory`), use the `test` profile, stagger launches, and lower the shared budget |
