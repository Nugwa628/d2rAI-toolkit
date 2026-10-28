release/ - release tooling for a D2R mod (Python 3.12+, standard library only)

What each file is
-----------------
release.example.toml   The one config every script reads. Copy it to release.toml and edit it. Every key is documented in it.
release_config.py      Loads the config. `py -3 release/release_config.py get site.url`, `... check`.
package_release.py     Mod folder -> dist/: <Folder>-<v>.7z, release-manifest.json (schema 2), notes, SHA256SUMS.
                       Also importable: find_7z, write_7z, file_list, empty_dirs, forbidden_files, sha256.
publish_release.py     publish | promote | list | gc | verify. Uploads only what the host lacks, to a dir: or sftp:// target.
dl_ingest.py           Runs ON the download host. Checks an upload, builds the player 7z, writes the channel file last.
dl_sync.py             Runs ON the download host. Mirrors the site to an S3-compatible bucket with rclone.
github_release.py      Creates or updates a GitHub / Forgejo release page from the changelog (REST, token from env or file).
release.sh             The whole release in one go: package, publish to test, verify over HTTPS, promote, release pages.
cloudflare-worker.js   Serves the bucket under your own domain. See CDN.md.
CDN.md                 Setting up the bucket, rclone, the sync units and the Worker.
server/                systemd units for dl_ingest.py and dl_sync.py (path + service + timer each). Edit the placeholders.

Quick start (everything local, no server)
-----------------------------------------
Needs 7-Zip on PATH (7z, 7zz) or in Program Files. Run from the repository root. MyMod stands for your finished mod folder.

  py -3 release/release_config.py get site.url
  py -3 release/package_release.py --mod-dir path/to/MyMod --version 0.1.0
  mkdir -p demo/mymod demo/incoming
  py -3 release/publish_release.py publish --target dir:demo/mymod --version 0.1.0 --channel test
  py -3 release/dl_ingest.py --root demo --site mymod
  py -3 release/publish_release.py list --target dir:demo/mymod

The publish step puts the upload in demo/incoming; dl_ingest.py is the server half, so here you run it by hand. On a real
host a systemd path unit runs it. `verify` fetches a site over HTTP and needs a server that answers Range requests (nginx, Caddy, or the
real host). Python's `http.server` does not, so `verify` against it stops at its Range check. With such a server on
demo/mymod, run `py -3 release/publish_release.py verify --url http://127.0.0.1:8080/ --channel test`.

Where things are set
--------------------
Command-line option, then environment variable, then release.toml, then the built-in default.
Environment: MODKIT_PUBLISH_TARGET (dir:<path> or sftp://user@host[:port]/path), MODKIT_SFTP (the sftp command, for
example "sftp -F my_ssh_config"). Tokens: the variable named in [repos] (github_token_env, forgejo_token_env) or the token
file named there. Never put a token, a key or a password in the repository or in release.toml.

Schemas: the manifest is in the package_release.py docstring; the channel file and READY in the publish_release.py docstring.
Tests: py -3 -m unittest discover tests (the release tests skip the 7-Zip ones when there is no 7-Zip).
