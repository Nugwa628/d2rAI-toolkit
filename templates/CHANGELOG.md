# Changelog

All player-facing changes, newest first. Write for the person playing, not the person coding: what they will notice,
in plain words, without file names. The release script takes the section for the version being released, so each
version needs exactly one section with the heading `### <version> (<YYYY-MM-DD>)`
(three `#`: the release scripts look for exactly that).

Guidelines:
- One line per change. Start with a verb: Fixed, Added, Changed, Removed.
- Mention the bug number if there is one, like `(#17)`.
- Group under bold labels **Fixes**, **New**, **Changed** (not headings: the next `###` or `##` ends the section). Leave
  out empty groups.
- If players must do something (restart the game, update the loader), say so first, in bold.
- No internal jargon, addresses or lane names.

### 0.1.1 (2026-01-12)

**Restart the game after updating.**

**Fixes**
- Fixed a crash when entering the Den of Evil (#1).
- Fixed the second page of the skill tree showing the wrong icons (#2).

**Changed**
- The installer now warns when another mod is set as the loader's default.

### 0.1.0 (2026-01-05)

First public release.

**New**
- <Describe the headline features in a few lines.>
