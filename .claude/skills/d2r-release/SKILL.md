---
name: d2r-release
description: Ship a new version of a Diablo II Resurrected mod to players - bump the version, write the changelog in player wording, build and publish the release package with a single fail-fast script, verify the version is live, create release pages, and update the bug list. Use after a batch of fixes is merged, when the user says "release", "publish", "ship it", "new version", or asks how to distribute the mod.
---

# Release a mod version

Release after **every** batch of merged fixes. Small releases are easier to debug and show players their report was
answered. Packaging, the installer and hosting live under `release/` and `downloader/` (see CLAUDE.md); the method and
its reasons are in `docs/ai-workflow.md` section 7. Where the files are served from (own host + downloader, GitHub
Releases, Nexus Mods, or a mix) is in `docs/distribution.md`: read it when the owner asks how to distribute the mod.

## Before you start
- [ ] **Ask the owner** the first time you publish, and whenever the target or channel changes. Publishing is
      outward-facing.
- [ ] All fixes for this version are merged on `main` and the targeted checks passed. Player-facing ones were also
      tested on a **release-like install** (the package installed into a slot like a player would).
- [ ] `python tools/check_public.py` prints nothing.
- [ ] Nobody (including you) has the release checkout busy. **Do not switch branches, rebase or edit** in the checkout
      while the release runs. Do other work in a separate worktree. Capture the commit first:
      `C=$(git rev-parse HEAD)`.
- [ ] Secrets (upload key, API tokens) are in files in the owner's home folder, not in chat or in the repo.

## Steps
1. **Pick the version.** Next number after the last release. Bump it in the one place the project keeps it (plugin
   version, and the installer's version if the installer changed). One bump per release.
   Requirement checks in the installer use the game version (`3.3`), never a build number (knowledge/d2rloader.md).
2. **Write the changelog section** `### <version> (<date>)` using `templates/CHANGELOG.md`: player wording, one line per
   change, bug numbers, and a bold note first if players must restart or update something.
3. **Commit** the bump and changelog (noreply identity). Generated headers that differ only in line endings are not
   a real change; a real content change must be committed first.
4. **Run the release as one script** with `set -euo pipefail`. The script should:
   - refuse to run if the version, the changelog section and the built plugin disagree;
   - build the release package (the release build compiles out the test harness);
   - publish, always passing the current installer so the release has an installer entry;
   - check that the new version is live (fetch the channel or manifest and compare);
   - **only then** create the public release pages from the changelog section.
5. **Never pipe publish output through `tail`, `head` or `grep`.** A pipe hides the failing command's exit status.
   To shorten output, write it to a log file and read the file.
6. **Verify like a player.** Run the release-like install on a slot from the published channel, start the game, check
   the plugin status line shows the new version and `(release)`.
7. **Mirrors, if the project uses them** (`docs/distribution.md`): only after the version is live. For Nexus Mods,
   package with `--nexus` and upload `<Folder>-<version>-nexus.7z` as a main file with the version and the changelog
   section (by hand, or the owner's upload Action). Never put the downloader exe on Nexus. The mod page needs an AI
   disclosure tag.
8. **Update the bug list.** Set each fixed row to `fixed in <version>`.
9. **Tell the owner** (Done / Running / Need): version, what's in it, link to the release page.

## If it fails
- Stop at the first failure and read the log. Don't re-run steps by hand out of order.
- A failed upload: nothing is public yet; fix and re-run the whole script.
- A release page for a version that isn't live: delete/unpublish it at once and tell the owner.
- A tag on the wrong commit: you switched branches mid-release. Retag from the captured commit hash after asking
  the owner.
- Never force-push a public repo without the owner's explicit say-so.

## Public mirror
If the project has a public mirror, push to it only through the project's scrub-and-mirror script, never directly,
and only after `check_public.py` is clean. Owner edits made directly on the mirror must be brought back first.
