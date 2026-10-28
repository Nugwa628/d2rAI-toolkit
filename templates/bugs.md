# Bug list

One numbered list of every report: from players, from the owner, and from agents' own testing. Rules:

- **Every report gets the next number at once**, with the date and who reported it.
- **Merge related bugs before fixing them.** Same crash address, same table, same root cause = one bug. Write
  `duplicate of #N` in the new row.
- Put the **cause** in the row as soon as it is known.
- One bug, one lane, one PR. Name the lane after the bug.
- Status: `open` | `in progress (lane <name>)` | `fixed in <version>` | `won't fix (<reason>)` | `duplicate of #N`.

| # | Reported | Status | Cause / notes |
|---|---|---|---|
| 1 | 2026-01-05, player: "Game crashes when I walk into the Den of Evil" | fixed in 0.1.1 | Monster row 12 referenced a spawn that has no row, so the sound code read a null pointer. Table build now fails when a spawn id has no row (`validate`, check `denspawns`). |
| 2 | | open | |
| 3 | | open | |

## Example workflow for one row

1. A player writes: "Game crashes when I walk into the Den of Evil". Add row 1. Status `open`.
2. The coordinator checks for related rows. None. A lane is started: `lane/bug-1-den-crash`. Status
   `in progress (lane bug-1-den-crash)`.
3. The lane finds the cause, fixes it, adds a check, opens a PR. The coordinator reviews and merges it.
4. The coordinator releases 0.1.1. Status becomes `fixed in 0.1.1`, with the cause in the last column.
5. The changelog for 0.1.1 says: "Fixed a crash when entering the Den of Evil."
