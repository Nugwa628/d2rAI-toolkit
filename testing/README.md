# testing/

Tools to test a D2R mod in the real game without a human at the keyboard. Python 3.12+, standard library only.
Windows is needed to run the game; everything else (locks, config, checks, release installs) works anywhere.

| File | What it is |
|---|---|
| `testing.example.toml` | The one config. Copy to `modkit-testing.toml` and edit. No paths or names live in the scripts. |
| `testing_config.py` | Reads the config and does all the path math (slot folders, save folders, lock folders). |
| `game_slots.py` | Slots = separate copies of the game install. Setup, sync, lock, unlock, status. |
| `game_ctl.py` | Starts, stops and drives one slot's game: screenshots, keys, clicks, console commands. |
| `game_checks.py` | Small checks framework: `@check` registry, runner, results JSON, summary, screenshots. |
| `example_checks.py` | Example checks to copy (`config-sane`, `game-starts`, `main-menu-visible`, `console-echo`, `no-new-crash-files`). |
| `release_install.py` | Installs the PUBLISHED release into a slot, as a player would get it. |

## Quick start

```
cp testing/testing.example.toml modkit-testing.toml        # edit game.install_dir, game.slots_dir, [mod]
python testing/game_slots.py setup 2                       # copy your install into slot 2
python testing/game_slots.py lock 2 my-lane                # take the slot (queue: add --wait 600)
MODKIT_RUN=my-lane python testing/game_checks.py run --slot 2
python testing/game_slots.py unlock 2 my-lane
```

`python testing/game_checks.py list` shows the checks. Results: `<runs.dir>/<run id>/results.json`,
`summary.txt` and `screenshots/`. Exit code 0 = no failures, 1 = a check failed, 2 = bad usage or slot not available.

## Rules

- Slot 1 is your own game (`game.install_dir`). These tools read it to make copies and never write, start or lock it.
  Every command refuses slot 1.
- `game_ctl.py start` without a prior `lock` takes a **manual** lock (pid 0) for the owner (`MODKIT_RUN`), so it never
  looks stale when the command ends. Only `unlock N <owner>` removes it; `stop` leaves it.
- There is no default slot. Pass `--slot N` or set `MODKIT_SLOT=N`, on every command.
- `MODKIT_RUN=<lane>` is the run id and the lock owner. A lock is a folder `<runs.dir>/game.lock.N` with
  `owner.txt` = `<owner> pid=<pid> <time>`. A dead pid makes it stale (replaced on the next lock). `pid=0` is a manual
  lock made by `lock`; it lasts until `unlock`, and runs inside it do not free it.
- `lock N <owner> --wait SECONDS` queues: it prints who holds the slot and polls. `lock any <owner>` takes the first
  free slot from 2 up. `unlock N <owner>`, or `unlock N --force` to clear a stale lock of a finished lane.
- Nothing here sends real mouse or keyboard input or takes focus. The window is found by PID, never by title.

## Command lines

```
python testing/game_slots.py [--config F] status | list
python testing/game_slots.py setup|sync [N ...] [--link] [--dry-run]
python testing/game_slots.py lock N|any [OWNER] [--wait SECONDS]
python testing/game_slots.py unlock N [OWNER] [--force]
python testing/game_ctl.py   --slot N status|start|stop|shot NAME|key KEY..|click FX FY [right]|console CMD|harness on|off
python testing/game_checks.py list [names or tags]
python testing/game_checks.py run [names or tags] --slot N [--run-id ID] [--keep] [--stop-on-fail] [--module M] [--path DIR]
python testing/release_install.py check [--channel C] [--site S]
python testing/release_install.py install --slot N [--channel C] [--site S] [--method auto|archive|objects] [--harness-dll FILE]
python testing/release_install.py restore|status --slot N
```

Config file: `--config F`, else env `MODKIT_TESTING_CONFIG`, else `./modkit-testing.toml`. A JSON file works too.
Env vars: `MODKIT_SLOT`, `MODKIT_RUN`, `MODKIT_TESTING_CONFIG`.

## Config keys

| Section | Keys |
|---|---|
| `[game]` | `install_dir` (slot 1), `slots_dir`, `slot_dir_template`, `slot_dirs`, `slot_count`, `loader_exe`, `game_exe`, `window_title_prefix`, `client_size`, `window_origin`, `window_step`, `copy_mode`, `link_dirs`, `exclude`, `mods_policy` |
| `[mod]` | `name`, `folder`, `plugins_subdir`, `plugin_dlls`, `modinfo_files` |
| `[saves]` | `root`, `slot1_name`, `slot_name` |
| `[loader]` | `config_file`, `section`, `default_mod_key`, `args_key`, `set_default_mod` |
| `[logs]` | `loader_log`, `plugin_log`, `crash_dir`, `startup_line` |
| `[harness]` | `shm_name`, `shm_name_shared`, `magic`, `block_bytes`, `command_offset`, `command_max`, `idx_*`, `flag_background`, `word_file`, `console_file`, `words`, `cursor_start` |
| `[runs]` | `dir`, `screenshots_dir` |
| `[checks]` | `modules`, `paths`, `start_timeout`, `echo_command`, `menu_min_mean`, `menu_min_stddev` |
| `[release]` | `site_url`, `channel`, `seven_zip`, `apply_launch`, `extra_launch_args`, `channel_schema`, `manifest_schema`, `retries` |

## The harness protocol

The game plugin (not part of this repo) must offer a test harness. It turns on when the word file exists in the
plugins folder (`harness.word_file`). All names and numbers below come from `[harness]`.

Shared memory `harness.shm_name` (default `Local\my-harness-block-{pid}`), one block per game process,
`block_bytes` long, little-endian int32 array:

| Index (config key) | Meaning | Written by |
|---|---|---|
| 0 `idx_magic` | `harness.magic`; the tool refuses a block without it | plugin |
| 1 `idx_active` | 1 = the game reads cursor, buttons and modifiers from this block instead of the real devices | tool |
| 2, 3 `idx_x`, `idx_y` | virtual cursor, client pixels | tool |
| 4 `idx_buttons` | button bits: 1 = left, 2 = right | tool |
| 5 `idx_mods` | modifier bits: 1 = shift, 2 = ctrl, 4 = alt | tool |
| 6 `idx_seq` | input sequence counter (reserved) | tool |
| 7 `idx_flags` | `flag_background` = do not bring the window to the front | tool |
| 8 `idx_cmd_seq` | command sequence; the tool bumps it after writing the command | tool |
| 9 `idx_cmd_ack` | plugin copies `cmd_seq` here when the command was run | plugin |
| 10 `idx_cmd_result` | 1 = the console handled the line, 0 = not | plugin |
| bytes `command_offset`.. | command text, UTF-8, NUL terminated, at most `command_max` bytes | tool |

Flow of one console command: write the text, bump `cmd_seq`, wait until `cmd_ack == cmd_seq`, read `cmd_result`.
The plugin appends any output to `harness.console_file` (in the plugins folder); `game_ctl` reads it from a byte mark.
The word file holds free-form words (`harness.words`, `{slot}` replaced) that the plugin reads at load.
