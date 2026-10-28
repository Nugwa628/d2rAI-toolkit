# Contributing to d2rAI-toolkit

Thanks for helping. The most useful things you can add are **facts about the engine**, **fixes to the knowledge
base**, **new or better skills**, and **bug reports with logs**.

## Ground rules

1. **No Blizzard files.** Never commit game files, extracted tables, dumps, saves, or screenshots of the game. Keep
   them in `d2r-data/` and your mod's `out/` folder, which are git-ignored. Use vanilla names in examples ("a
   Whirlwind clone", "Andariel", "a new unique bow").
2. **No private things.** No real names, e-mails, server addresses, tokens, or names of other people's mods. Commit
   with a no-reply identity (GitHub: *Settings → Emails → Keep my email address private*).
3. **Say how you know.** A fact about the engine names the build (for example `3.3 build 93847`) and how it was
   found: a Ghidra function, a log line, a test in game. Mark one-off observations as "observed" or "unverified".
4. **Data before code.** If a vanilla function or column can do it, document that before you add a plugin.

## Set up

```powershell
git clone --recursive https://github.com/Nugwa628/d2rAI-toolkit.git
cd d2rAI-toolkit
py -3 tools\install_hooks.py      # runs the privacy scan on every commit
```

Optional but recommended: keep a private-words list outside the repo in `~/.d2r-modkit/private_terms.txt` (one word
per line). The scan then checks your own sensitive words too.

## Before you open a pull request

```powershell
py -3 -m unittest discover tests        # all tests pass
py -3 tools\check_public.py --no-history # prints OK
py -3 -m modkit build examples\showcase  # if you touched modkit/: 0 errors
```

- Plugin changes: build `plugin-template` with `build.ps1` without warnings.
- New Python code gets a test in `tests/` (`unittest`, standard library only, Python 3.12+).
- Check that every link and relative path in the docs you touched works.
- One topic per pull request. Fill in the pull request template.

## Style

- **Docs:** plain English, short sentences, tables where they help, code blocks that actually run. Put a fact in
  one place ([`knowledge/`](knowledge/README.md)) and link to it from elsewhere.
- **Skills:** a skill in `.claude/skills/<name>/SKILL.md` has a front matter `name` and a `description` that says
  *when* to use it. The body is steps the agent can follow, with links to the knowledge pages. Add new skills to the
  table in [README.md](README.md) and to the list in [CLAUDE.md](CLAUDE.md).
- **Python:** standard library where you can, `python -m modkit ...` style CLIs, no hard-coded paths or hosts (use
  config files and placeholders like `files.example.com`).
- **Addresses:** build-specific values go in [`knowledge/d2r-93847-addresses.md`](knowledge/d2r-93847-addresses.md)
  (or a new file per build), with the image timestamp they belong to.

## Reporting bugs

Use the issue templates. For a crash, attach the D2RLoader log and crash report (`<game>\d2rloader\logs\` and
`<game>\d2rloader\crashes\`) and say which game build and loader version you run. Remove your user name from paths
before you post them.

## License

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
