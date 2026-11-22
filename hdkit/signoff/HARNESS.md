# Harness verbs the sign-off needs

The sign-off check (`hdkit/signoff/gamecheck.py`, check name `signoff`) drives a live game through console lines sent
over the test harness (the shared-memory block of [testing/README.md](../../testing/README.md#the-harness-protocol)).
The toolkit does not ship the game plugin that answers them: a project's test plugin must provide these verbs. Any
backend (`hdkit/signoff/backend.py`) can then run the check.

Each verb is one console line. The plugin sets `cmd_result` to 1 when it handled the line and appends its answer, one
line, to the harness console file. Answers are `key=value` words separated by spaces; the check reads them with a
`key=value` parser, so extra words are fine and their order does not matter.

| Verb | Arguments | What it must do | Answer the check reads |
|-|-|-|-|
| `spawn` | `<hcIdx> <count> <x> <y>` | spawn `count` monsters of the monstats row `hcIdx` at sub-tile `x, y` of the player's level (the game may move the spot to the nearest free one) | a line with `id=<unit id>` of the (first) spawned unit |
| `place` | `<x> <y>` | put the player at sub-tile `x, y` at once (no walk), in the same level | `cmd_result` 1 when the player stands there |
| `facing` | `<unit id>` | report the unit's state | `mode=<unit mode index> dir=<dir64 0..63> key=<facing key 0..7> x=<sub-tile x> y=<sub-tile y> px=<player x> py=<player y>` |
| `mcast` | `0 <mode index> unit <unit id>` | make the unit play that mode now, where it stands (a cast with no missile, so it does not walk) | `cmd_result` 1 |
| `kill` | `<unit id>` or `nearest` | kill that unit (its death mode DT then its corpse DD play as in normal play); `nearest`: the closest monster, `cmd_result` 0 when there is none | `cmd_result` 1 when a unit was killed |
| `move` | `<x> <y>` | walk the player to sub-tile `x, y` (normal pathing) | `cmd_result` 1 |

Mode indices are D2R's monster modes (DT 0, NU 1, WL 2, GH 3, A1 4, A2 5, BL 6, SC 7, S1 8 ... S4 11, DD 12, KB 13,
SQ 14, RN 15). The facing key is the unit's `dir64` turned into 8 screen directions, 0 at the screen's bottom and
counting clockwise (`hdkit/signoff/capture.py`, `key_of`).

## Why these verbs

- **Exact facings.** A monster that casts at the player faces the player, so the check spawns the unit beside the
  player, polls `facing` for where the game really put it, and moves the player with `place` until the unit faces the
  asked key. Walking would change the camera and the light; `place` does not.
- **One mode at a time.** `mcast` plays a mode on the spot. A real attack makes the unit walk first and leaves the
  frame.
- **Death and corpse.** `kill` gives the death clip and the corpse exactly as a player sees them, including the
  hand-over from `death` to `dead` that the engine does on its own.
- **Clean shots.** `kill nearest` clears other monsters out of the frame before each facing.

## Writing them in a plugin

The D2RLoader [plugin-template](../../plugin-template/README.md) is the place to start: it has the harness block and
the console-line hook. The engine functions these verbs need (spawn a monster, set a unit's mode, kill a unit, set the
player's position) are listed in [knowledge/plugin-techniques.md](../../knowledge/plugin-techniques.md) and
[knowledge/monsters-spawn.md](../../knowledge/monsters-spawn.md); the facing hook is in
[knowledge/d2r-93847-addresses.md](../../knowledge/d2r-93847-addresses.md). Keep the verbs in a test-only build of the
plugin, never in what players install.
