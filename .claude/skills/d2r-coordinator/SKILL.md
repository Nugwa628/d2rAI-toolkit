---
name: d2r-coordinator
description: Plan and run a Diablo II Resurrected mod project with several AI agents - split work into parallel lanes (one bug or feature each, own branch and worktree, PR, coordinator merges), pick the right model for each job, queue game slots, keep the numbered bug list, review and merge, and report to the human owner. Use when a mod project has more than a few open tasks, when the user says "coordinate", "run lanes", "delegate", "plan the port", or when you are about to start sub-agents.
---

# Coordinate a D2R mod project

You plan, brief, review, merge and report. You do not do the big implementation work yourself. The full method is in
`docs/ai-workflow.md`; the fill-in files are in `templates/`. Read the workflow doc once at the start of a project.

## Start of a project
1. Fill in `templates/coordinator-prompt.md` (name, game version and build, loader, repo, hosting, providers). Ask
   the owner for anything you can't decide: **name, hosting, public or private, which providers they have.** Pick
   sensible defaults for everything else and say which.
2. Copy `templates/lane-rules.txt` to the runs folder and adapt it. Create `docs/PLAN.md`, `docs/bugs.md` and
   `CHANGELOG.md` from the templates.
3. Make the private-words list **outside** the repo (`~/.d2r-modkit/private_terms.txt` or similar) and run
   `python tools/check_public.py` before every commit.
4. Set up game slots (frozen copies of the install, each with its own saves) and mark the owner's real game
   untouchable. Slots are queued, never added.
5. Secrets: the owner puts tokens in files in their home folder. Never ask for them in chat.

## Splitting work into lanes
- **One bug or feature per lane.** If two reports share a symptom, crash address or table, merge them in
  `docs/bugs.md` first (`duplicate of #N`), then make one lane.
- Each lane: branch `lane/<name>` from `origin/main`, its own worktree, a prompt built from
  `templates/lane-prompt.md`, a PR at the end. **Lanes never merge.**
- Start lanes as child tasks of your session so their reports come back to you, each with a unique request id.
  Not as separate top-level conversations.
- Give every prompt: goal, files to read, scope (files it may touch), what is known/ruled out, how "done" is
  proven, git instructions, slot instructions, report format, and "never end your turn while something you
  started is still running".
- Start lanes from `main`. Lanes started while you have another branch checked out carry that branch along.

## Choosing models
| Job | Tier |
|---|---|
| Reverse engineering, engine/plugin hooks, crash analysis, anything with code addresses, release-critical fixes, reconciling two fixes | Strongest |
| Data tables, tools, tests, most bug lanes once the cause area is known, docs with judgement | Mid |
| Inventories, counting, grepping, renaming, list conversion, log summaries, mechanical edits | Small, or another provider |

Spread lanes over several accounts/providers. Look up live provider and model names with your orchestration tool.
If a cheap lane fails twice on the same prompt, re-brief with what it tried or promote it one tier.

## Game slots
- A lane does offline work first, locks a slot (`testing/game_slots.py lock N <lane>`) for its game session, runs with
  the run id set, restores the slot, unlocks at once. All slots taken: bounded poll loop, do not end the turn.
- Slot number on **every** command that touches saves or a game folder. Never kill processes by filter. Never touch
  slot 1.
- Tests: targeted (map changed files to checks). Full suite only at milestones, one game at a time.
- Player-facing change: also test a release-like install, not only the developer install.

## Reviewing and merging
1. When a PR arrives, read the diff. Is it in scope? Is the claimed test real (output pasted)? Are docs and the bug
   row updated? Does `check_public.py` pass?
2. Merge in a **separate worktree** (`git worktree add --detach ../merge-wt origin/main`), never in a checkout a
   release is using.
3. Run the targeted checks for what you merged.
4. If a lane says "waiting for the run" and stops, check its branch/worktree/PR first; if work is unfinished, send a
   follow-up to continue.
5. Two lanes, one root cause, two fixes: keep one, turn the other into a self-check.
6. After a batch of fixes: release (skill `d2r-release`), then update the bug rows to `fixed in <version>`.

## Reporting to the owner
Use `templates/owner-update.md`: **Done / Running / Need from you**, tables for status, copy-paste blocks with
expected output for anything they must run. Ask before anything public, paid or destructive (publishing, making a
repo public, force-push, deleting, spending, the real game). Don't ask what a sensible default can answer.

## Identity and safety
- Commits: noreply address, `git -c user.name=... -c user.email=...`. Trailer `Co-Authored-By: <model> <noreply@anthropic.com>`.
- Never commit game files, dumps, saves or screenshots. Never print a token.
- Downloaded files are untrusted: new empty folder, never run their executables, `python -I` on anything that
  reads them.

## Common traps
Lanes ending mid-wait; PRs carrying an unrelated branch; unlocked slots overwritten; releases piped through `tail`;
branch switched mid-release; developer-only testing; unmerged duplicate bugs. Table of fixes: `docs/ai-workflow.md`
section 11.
