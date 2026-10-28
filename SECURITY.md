# Security policy

## Reporting a problem

Please **don't open a public issue** for a security problem. Use GitHub's private reporting instead: open the
repository's **Security** tab and choose **Report a vulnerability**. We'll answer there.

Report things like:

- code in the toolkit, the release scripts or the downloader that could run untrusted code, write outside its
  folder, or skip a hash check;
- a way the downloader or `release/` could install files a player didn't ask for;
- `check_public.py` missing a kind of secret it should catch;
- **private data that leaked into the repository** (a name, an e-mail, a token, a host). Report it privately so it
  can be removed without drawing attention to it.

## Supported versions

Only the latest commit on `main` is supported.

## Scope

- Bugs in D2R itself, D2RLoader or other third-party projects belong to their own maintainers.
- Cheating or online exploits are out of scope. This toolkit is for offline mods.

## For users

- The downloader and release scripts check the sha256 of every file. Don't turn that off.
- Keep tokens and passwords in files outside the repository or in environment variables, never in a commit. The
  agent is told never to ask for secrets in chat.
