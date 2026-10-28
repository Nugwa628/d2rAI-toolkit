# Lane prompt: <short title, e.g. "Bug 17: crash when entering Andariel's lair">

> One job per lane prompt. Fill in the placeholders, delete the notes, and send it as the lane's task. The shared
> rules live in the rules file, so this prompt only carries what is specific to this job.
> Guide: [docs/ai-workflow.md](../docs/ai-workflow.md#10-writing-a-good-lane-prompt).

Read `<RUNS PATH>/lane-rules.txt` and follow it exactly. You are the lane `<lane-name>` of `<MOD NAME>`.

## Goal
<One sentence: what must be true when you are done, and who it matters to.>
Example: "The game no longer crashes when a player enters the Catacombs with a Whirlwind-type skill equipped (bug
#17)."

## Read first
- `docs/bugs.md`, row #<N>
- `<doc 1>` (why)
- `<doc 2>` (why)
- `knowledge/<topic>.md`

## Scope
You may change:
- `<path or glob>`
- `<path or glob>`

Do not change anything else. If you find another problem, list it in your report.

## What is already known
- <Cause found, symptom, log excerpt, address, build number.>
- <What was already tried or ruled out.>
- <Suspicions, marked as suspicions.>

## Do
1. <Step or constraint. Be concrete: commands, file names, expected output.>
2. <...>
3. <Add a check or unit test for what you built.>
4. <Update the docs and the bug row.>

## Don't
- <The obvious wrong approach and why.>

## Done means
- <Check name(s)> pass: `<command>` prints `<expected line>`.
- `python -m modkit build <project>` shows 0 errors.
- `python -m unittest` passes.
- `python tools/check_public.py` prints nothing.
- <Release-package test if player-facing.>

## Git
- Branch `lane/<lane-name>` from `origin/main`. Open a PR to `main`. Do not merge.
- Do not bump the release version. <Or: "Bump to the next free version after main's.">

## Game slot
<"No game needed." or "Needs a game: lock a slot as the rules say; wait in a bounded loop if all are taken.">
Checks to run: `<list>`.

## Report
PR URL, head SHA, files changed, tests run with results, what is left, any incident. Do not end your turn while
anything you started is still running.
