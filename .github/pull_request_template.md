## What and why

<!-- One topic per pull request. What does it change, and what problem does it solve? Link the issue. -->

## How it was checked

- [ ] `py -3 -m unittest discover tests` passes
- [ ] `py -3 tools/check_public.py --no-history` prints OK (hooks installed with `py -3 tools/install_hooks.py`)
- [ ] `py -3 -m modkit build examples/showcase` has 0 errors (if `modkit/` changed)
- [ ] The plugin template builds without warnings (if `plugin-template/` changed)
- [ ] Tested in game (say how), or not needed

## Docs

- [ ] Knowledge, skills, CLAUDE.md and README updated where needed; links checked
- [ ] Engine facts name the build and say how they were found

## Clean

- [ ] No game files, dumps, saves or game screenshots
- [ ] No real names, e-mails, hosts, tokens, or other projects' names
