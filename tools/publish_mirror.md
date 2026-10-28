# Publishing a scrubbed copy to a public host

For a maintainer who develops in a private repository and wants a clean public copy. The public repository gets
**one squashed commit** made from a checked tree, so private history, author names, branch names and old commit
messages never leave the private repository.

## One-time setup

1. Keep the banned terms in a file **outside** every repository, one per line (case-insensitive substrings).
   Lines starting with `#` are comments. The default location is `~/.d2r-modkit/private_terms.txt`; set
   `D2RAI_PRIVATE_TERMS` to use another path.
2. Install the hooks in your working clone, so a bad commit is stopped before it exists:

   ```
   python tools/install_hooks.py
   ```

3. Create an empty repository on the public host. Use a no-reply address for the commit author:

   ```
   git config user.name <your-public-name>
   git config user.email <id>+<your-public-name>@users.noreply.github.com
   ```

## Every release

1. Start from a clean checkout of the branch you want to publish.
2. Run the scan on the working tree, history included. It must print `OK`:

   ```
   python tools/check_public.py --require-terms
   ```

   Fix every hit and run it again. Hits show `file:line` and a term number, never the term. Private-term hits
   cannot be allow-listed: rewrite the text. For false positives of the generic checks (an example address, a
   version number that looks like an IP), add a line to `check_public.allow`.
3. Strip image metadata if the scan reported PNG chunks: `python tools/check_public.py --strip-png`.
4. Make a fresh single-commit copy in an empty directory:

   ```
   git clone --no-local . ../public-copy
   cd ../public-copy
   git checkout --orphan public
   git add -A
   git commit -m "d2rAI-toolkit <version>"
   ```

   Do not copy the `.git` directory of your private clone. The orphan branch has no parents, so none of the old
   history is reachable.
5. Scan the copy again, with the terms file, history included. Nothing may be printed except `OK`:

   ```
   python tools/check_public.py --require-terms
   ```

6. Check the commit identity and message by eye (`git log --format=fuller`), then publish:

   ```
   git remote add public <public-repo-url>
   git push public public:main
   ```

   For later releases make a new orphan commit and push it with `--force-with-lease`, or keep the public history
   as a chain of your own squashed release commits instead. Pick one rule and keep it.

## Rules of thumb

- Never put the terms file, its contents, or a private repository URL in a commit message, pull request,
  issue, CI log or code comment. The scanner prints term numbers for that reason.
- Pull requests from outside contributors are scanned by the public CI (generic checks). Run the full scan with
  your terms file on your machine before you merge anything.
- Never publish game files, dumps, saves or screenshots of the game. `.gitignore` already excludes the usual
  suspects; the scan does not know your private data, so look at `git status` before the first commit.
