#!/usr/bin/env bash
# One release, every step or nothing. It stops at the first error (set -euo pipefail), and the release pages come last,
# only after the version is live and promoted. Everything comes from release.toml (release/release.example.toml).
#
#   bash release/release.sh <version> --mod-dir <finished mod folder> [options]
#
#   --config FILE            the release config (default ./release.toml)
#   --plugin DLL             a release plugin DLL to copy into the mod folder (see package_release.py)
#   --plugin-version V       the version text that must be inside the DLL
#   --game-extract DIR       leave out files that are byte copies of the game's own
#   --downloader EXE         the downloader to publish (default downloader.exe in the config, if that file exists)
#   --commit-github SHA      commit to tag on GitHub (default: HEAD)
#   --commit-forgejo SHA     commit to tag on Forgejo (default: HEAD)
#   --no-github, --no-forgejo  skip that release page
#   --wait SECONDS           how long to wait for the public site to show the new version (default 1200)
#   --allow-dirty            do not insist on a clean git checkout
#
# Steps:
#   1. checks: version format, a "### <version>" section in the changelog, a clean checkout, the config loads
#   2. package   -> dist/ (7z, release-manifest.json, notes, SHA256SUMS)
#   3. publish   -> the test channel (the server checks and publishes it)
#   4. verify    -> the version is live over HTTPS on the test channel: every hash, Range requests, the 7z
#   5. promote   -> the stable channel
#   6. verify    -> the stable channel shows the version (a sample of the objects)
#   7. release pages on GitHub and Forgejo, from the changelog section (only now)
#
# Rerunning after a failure is safe: publish resumes, promote is idempotent, release pages are updated, not duplicated.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }
[ $# -ge 1 ] || { usage; exit 2; }
case "$1" in -h|--help) usage; exit 0;; esac
V="${1#v}"; shift

CONFIG="" MOD_DIR="" PLUGIN="" PLUGIN_VERSION="" GAME_EXTRACT="" DOWNLOADER="" C_GITHUB="" C_FORGEJO=""
NO_GITHUB=0 NO_FORGEJO=0 WAIT=1200 ALLOW_DIRTY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2;;
    --mod-dir) MOD_DIR="$2"; shift 2;;
    --plugin) PLUGIN="$2"; shift 2;;
    --plugin-version) PLUGIN_VERSION="$2"; shift 2;;
    --game-extract) GAME_EXTRACT="$2"; shift 2;;
    --downloader) DOWNLOADER="$2"; shift 2;;
    --commit-github) C_GITHUB="$2"; shift 2;;
    --commit-forgejo) C_FORGEJO="$2"; shift 2;;
    --no-github) NO_GITHUB=1; shift;;
    --no-forgejo) NO_FORGEJO=1; shift;;
    --wait) WAIT="$2"; shift 2;;
    --allow-dirty) ALLOW_DIRTY=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2;;
  esac
done

# Python 3.12+: $PYTHON, python3, `py -3` (Windows), python
if [ -n "${PYTHON:-}" ]; then PY=("$PYTHON")
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1; then PY=(python3)
elif command -v py >/dev/null 2>&1; then PY=(py -3)
else PY=(python); fi
CFG=(); [ -z "$CONFIG" ] || CFG=(--config "$CONFIG")
cfg() { "${PY[@]}" "$HERE/release_config.py" ${CFG[@]+"${CFG[@]}"} get "$1"; }

# ---- 1. checks --------------------------------------------------------------------------------------------------------
echo "$V" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$' || { echo "bad version '$V' (expected 1.2.3 or 1.2.3-rc1)"; exit 1; }
[ -n "$MOD_DIR" ] && [ -d "$MOD_DIR" ] || { echo "--mod-dir must be the finished mod folder"; exit 1; }
"${PY[@]}" "$HERE/release_config.py" ${CFG[@]+"${CFG[@]}"} check >/dev/null   # exits 1 when the config does not load
DIST="$(cfg package.dist)"; SITE_URL="$(cfg site.url)"
PUB_CH="$(cfg site.publish_channel)"; PROMOTE_CH="$(cfg site.promote_channel)"
CHANGELOG="$(cfg repos.changelog)"
GH_REPO="$(cfg repos.github)"; FJ_REPO="$(cfg repos.forgejo)"
case "$SITE_URL" in https://*/) ;; *) echo "site.url must be an https:// URL that ends in '/': $SITE_URL"; exit 1;; esac
case "$SITE_URL" in *example.com*) echo "site.url is still the example: $SITE_URL"; exit 1;; esac
[ -f "$CHANGELOG" ] || { echo "no changelog at $CHANGELOG"; exit 1; }
VRE="${V//./\\.}"
grep -Eq "^### ${VRE}([^0-9A-Za-z.+-]|\$)" "$CHANGELOG" || { echo "$CHANGELOG has no '### $V' section"; exit 1; }
if [ -z "$DOWNLOADER" ]; then
  EXE="$(cfg downloader.exe)"; [ -z "$EXE" ] || [ ! -f "$EXE" ] || DOWNLOADER="$EXE"
fi
[ -n "$DOWNLOADER" ] || echo "note: no downloader exe: this release carries no downloader entry"
[ -z "$DOWNLOADER" ] || [ -f "$DOWNLOADER" ] || { echo "no downloader at $DOWNLOADER"; exit 1; }
COMMIT="none"
if git rev-parse --git-dir >/dev/null 2>&1; then
  if [ "$ALLOW_DIRTY" -eq 0 ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "the checkout has uncommitted changes: commit them first (or --allow-dirty)"; exit 1
  fi
  COMMIT="$(git rev-parse HEAD)"
fi
[ -n "$C_GITHUB" ] || C_GITHUB="$COMMIT"; [ -n "$C_FORGEJO" ] || C_FORGEJO="$COMMIT"
echo "release $V from $COMMIT"

# ---- 2. package -------------------------------------------------------------------------------------------------------
case "$DIST" in ""|/|.|..) echo "refusing to clear package.dist = '$DIST'"; exit 1;; esac
rm -rf "$DIST"
PKG=(--mod-dir "$MOD_DIR" --version "$V")
[ -z "$PLUGIN" ] || PKG+=(--plugin "$PLUGIN")
[ -z "$PLUGIN_VERSION" ] || PKG+=(--plugin-version "$PLUGIN_VERSION")
[ -z "$GAME_EXTRACT" ] || PKG+=(--game-extract "$GAME_EXTRACT")
"${PY[@]}" "$HERE/package_release.py" ${CFG[@]+"${CFG[@]}"} "${PKG[@]}"

# ---- 3. publish to the test channel ----------------------------------------------------------------------------------
DL=(); [ -z "$DOWNLOADER" ] || DL=(--downloader "$DOWNLOADER")
"${PY[@]}" "$HERE/publish_release.py" ${CFG[@]+"${CFG[@]}"} publish --version "$V" --channel "$PUB_CH" --dist "$DIST" ${DL[@]+"${DL[@]}"}

# ---- 4. the version is live over HTTPS --------------------------------------------------------------------------------
"${PY[@]}" "$HERE/publish_release.py" ${CFG[@]+"${CFG[@]}"} verify --url "$SITE_URL" --channel "$PUB_CH" --expect-version "$V" --wait "$WAIT" \
  || { echo "$V is not live on $SITE_URL: no promote, no release pages"; exit 1; }
echo "$V is live on $SITE_URL (channel $PUB_CH)"

# ---- 5. promote -------------------------------------------------------------------------------------------------------
"${PY[@]}" "$HERE/publish_release.py" ${CFG[@]+"${CFG[@]}"} promote "$V" --channel "$PROMOTE_CH"
"${PY[@]}" "$HERE/publish_release.py" ${CFG[@]+"${CFG[@]}"} verify --url "$SITE_URL" --channel "$PROMOTE_CH" --expect-version "$V" --wait "$WAIT" --sample 20 \
  || { echo "$V is not on the $PROMOTE_CH channel of $SITE_URL: no release pages"; exit 1; }
echo "$V is on the $PROMOTE_CH channel"

# ---- 6. release pages: only now ----------------------------------------------------------------------------------------
GH=("${PY[@]}" "$HERE/github_release.py")
if [ "$NO_GITHUB" -eq 0 ] && [[ "$GH_REPO" != *your-name* ]]; then
  "${GH[@]}" ${CFG[@]+"${CFG[@]}"} "$V" --service github --commit "$C_GITHUB" ${DL[@]+"${DL[@]}"} --latest
fi
if [ "$NO_FORGEJO" -eq 0 ] && [[ "$FJ_REPO" != *your-name* ]]; then
  "${GH[@]}" ${CFG[@]+"${CFG[@]}"} "$V" --service forgejo --commit "$C_FORGEJO" ${DL[@]+"${DL[@]}"}
fi
echo "released $V"
