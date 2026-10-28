# The mod downloader

A small Windows program that puts your mod into a player's own copy of Diablo II: Resurrected and keeps it up to
date. The player downloads one `.exe`, presses **Install**, and that is all.

It is built from `downloader/`. The mod name, colors, logo and site address are settings in **one header file**, so
you can build your own downloader without touching any C++.

The downloader is the player's end of the release pipeline in [release.md](./release.md): it reads the channel file
and manifest that `release/publish_release.py` puts on your site. [testing.md](./testing.md) shows how to test
what a player gets. Other ways to serve the files (GitHub Releases, Nexus Mods) and how they combine with the
downloader are in [distribution.md](./distribution.md). Note that Nexus Mods does not allow an updater exe on its pages.

## What it does

| Job | How |
|---|---|
| Find the game | Battle.net and Steam install records, then the default folders. The player can also pick the folder. |
| Check the game version | The mod needs one D2R **major.minor** (for example `3.3`). Any hotfix build of 3.3 is fine. A newer 3.4 is reported as needing a new mod release. |
| Check D2RLoader | Compares the file version of `D2RLoader.exe` with the version the release asks for. A button opens the D2RLoader site. It never installs D2RLoader. |
| Install and update | Reads the channel file, then the release manifest, then fetches only the files that changed. A fresh install uses the release `.7z`. |
| Repair | Hashes every file and fetches the ones that do not match. |
| Uninstall | Removes the mod folder, but only if the downloader's marker file is inside it. |
| Self-update | Replaces itself when the manifest offers a higher version whose sha256 is not the running exe's. |
| Environment warnings | Warns when D2RLoader would start another mod, or an old copy of your plugin sits in another plugins folder. Both can be fixed with a button (after asking). |

It never starts the game. It writes only into `<D2R>\mods\<your mod folder>` and into its own data folder in
`%LOCALAPPDATA%`.

## Quick start

You need Windows, Visual Studio (any version with the C++ desktop workload) and CMake 3.20 or newer.

1. **Copy the config header** and edit your copy:

   ```
   copy downloader\config\downloader_config.h my_downloader_config.h
   ```

   Change at least `DL_MOD_NAME`, `DL_MOD_FOLDER`, `DL_EXE_NAME`, `DL_SITE_URL` and the colors you like.
   Every setting is described in the header.

2. **Configure and build:**

   ```
   cmake -S downloader -B build/downloader -A x64 -DDL_CONFIG_HEADER=%CD%\my_downloader_config.h
   cmake --build build/downloader --config Release
   ```

   You get `build/downloader/Release/<DL_EXE_NAME>.exe` and a `.sha256` file next to it. It is one static exe: the
   player installs no runtime.

3. **Optional:** add a logo and a background:

   ```
   cmake -S downloader -B build/downloader -DDL_LOGO_FILE=%CD%\logo.png -DDL_BACKGROUND_FILE=%CD%\background.png -DDL_ICON_FILE=%CD%\mod.ico
   ```

   Without them you get a plain colored window with your mod name as the logo, and a plain blue square as the
   icon (made by `downloader/tools/make_placeholder_icon.py`). Use only pictures you own.

4. **Run the unit tests** (they build in the same project; no game needed):

   ```
   build\downloader\tests\Release\downloader_tests.exe
   ```

5. **Try it** on a scratch copy of the game against a site folder you published locally (see
   [release.md](./release.md) quick start):

   ```
   MyMod-Downloader.exe --cli install --game-dir D:\D2R-scratch --site D:\site --data-dir D:\dl-data
   ```

## The config header

`downloader/config/downloader_config.h` is the only file you edit. The most important settings:

| Setting | Meaning |
|---|---|
| `DL_MOD_NAME` | Name shown to players ("My Mod"). |
| `DL_MOD_FOLDER` | The folder in `<D2R>\mods\` (must match `modFolder` in your release manifest). |
| `DL_EXE_NAME` | File name of the exe, without `.exe`. |
| `DL_SITE_URL` | Your download site, for example `https://files.example.com/mymod/`. A local folder also works. |
| `DL_DEFAULT_CHANNEL` | Release channel to follow (`stable`). |
| `DL_STATE_FOLDER` | Folder name under `%LOCALAPPDATA%` for the state file and log. |
| `DL_MARKER_FILE` | File at the top of the mod folder that proves the downloader made it. |
| `DL_DOWNLOAD_FOLDER` | Name of the staging folder inside the mod folder. |
| `DL_PLUGIN_DLL_PREFIX`, `DL_PLUGIN_PRODUCT_NAME` | How to recognize a stray copy of your plugin. Leave empty if you have none. |
| `DL_PROTECTED_APPDATA_FOLDERS` | Other programs' data folders the downloader must never write into. |
| `DL_REQUIRED_GAME_VERSION` | D2R major.minor the mod needs (`3.3`). |
| `DL_LOADER_VERSION`, `DL_LOADER_SITE` | D2RLoader version to expect and the page the button opens. |
| `DL_HOW_TO_PLAY`, `DL_FOOTER` | The two text lines at the bottom of the window. |
| `DL_COLOR_*`, `DL_FONT_*` | Window colors (`0xRRGGBB`) and fonts. |
| `DL_COMPANY` | Shown in the exe's file properties. |

The downloader's own version is the file `downloader/VERSION` (`x.y.z`). Raise it every time you publish a new exe:
the self-update compares it.

## Where it writes

| Where | What |
|---|---|
| `<D2R>\mods\<mod folder>\` | Exactly the files of the release. |
| `<mod folder>\<DL_MARKER_FILE>` | The marker: who made the folder, an id and the version. Written first. |
| `<mod folder>\<DL_DOWNLOAD_FOLDER>\` | Downloads in progress. It is inside the mod folder on purpose, so every finished file is a rename on the same disk. Removed at the end. |
| `%LOCALAPPDATA%\<DL_STATE_FOLDER>\` | State file, log, the release 7z while it downloads, and files moved away by "Move old plugin". |
| Next to the exe | `.download` and `.old` files during a self-update. |

It never writes into the game's own files, D2RLoader, `Saved Games` (characters) or other mods. It refuses to
install, repair or uninstall while D2R or D2RLoader is running.

A mod folder **without** the marker (a manual install) is never overwritten without asking: the window asks, and the
command line needs `--replace`.

The exe runs with normal user rights (`asInvoker`). If the game folder is not writable, the message tells the player
to run it as administrator once.

## Install, update and repair

One button does the right thing: **Install**, **Update**, or **Up to date** (disabled). Each run:

1. Read `channels/<channel>.json`, then the manifest it points to. The manifest's sha256 must equal the channel's
   `manifestSha256`. The manifest must be schema 2 (see [release.md](./release.md)).
2. Make a plan. Missing or changed files are fetched. Anything else in the mod folder is deleted, except `preserve`
   entries (player-editable files), D2RLoader's logs and crash folders, the marker and the download folder.
3. Fetch each file, check its size and sha256, and rename it into place. A file is only replaced once its new version
   has fully arrived.

**Cancel**, closing the window or a dropped connection keep every finished file; the next run picks up from there.

### 7z or objects

A release has two forms of the same files: one `.7z` of the whole folder, and one small object per file
(`objects/<aa>/<sha256>`). The downloader picks the cheaper one:

- Total bytes to fetch **more than** the 7z size: it downloads the 7z and unpacks it. This is a fresh install.
- Otherwise: it downloads only the changed objects, 8 in parallel. This is an update or a repair.

If the 7z cannot be used, it falls back to objects. `--via auto|7z|objects` forces one. Both ways produce the same
folder. Downloads resume with HTTP Range requests, are retried, and are checked against the manifest.

The 7z decoder is the public-domain LZMA SDK, vendored in `downloader/third_party/lzma/`.

## Environment warnings

D2RLoader decides which mod starts, not the downloader. Two things commonly go wrong, and the window shows a red
line for each:

- **Default mod.** The game-wide `d2rloader\config\d2rloader.toml` names a different mod (or none). The **Make
  default** button asks, then changes only that one key (comments, other keys, CRLF line ends and a BOM are kept).
- **Stray plugin copies.** A copy of your plugin in `<D2R>\d2rloader\plugins` or in another mod's plugins folder loads
  as well. **Move old plugin** asks, then moves the files under `%LOCALAPPDATA%\<state>\removed\<date>\`. Nothing is
  deleted.

Both buttons are refused while the game runs.

## Self-update

`release/publish_release.py` can upload the exe with a release. Its version, path, size and sha256 go into the
manifest's `launcher` entry. If that version is higher than the running one and the sha256 is not the running exe's,
the window offers the update. The new exe is downloaded next to the running one, its size and sha256 must equal the manifest's,
then the running exe becomes `.old`, the new one takes its name, and the window restarts. A signature is logged but not
required.

## The command line

```
MyMod-Downloader.exe --cli status|detect|install|update|repair|uninstall|self-update|fix-default-mod|remove-stray-plugins
    [--data-dir DIR] [--site URL|DIR] [--channel stable|test] [--game-dir D2R_DIR] [--replace]
    [--via auto|7z|objects] [--snapshot FILE]
```

`--snapshot FILE` draws the window once into a PNG without showing it. The exe is a Windows GUI program, so from
PowerShell use `Start-Process -NoNewWindow -Wait`.

## Release and signing

1. Build in CI or locally (above). Keep the unsigned exe and its `.sha256`.
2. Sign it if you have a certificate (any tool, for example `signtool`). Then publish **the signed file** with
   `release/publish_release.py publish ... --downloader path\to\MyMod-Downloader.exe`. The publisher reads the version
   from the exe's version resource and refuses a different `--downloader-version`.
3. An unsigned exe from the web gets Windows SmartScreen's "protected your PC" screen ("More info", "Run anyway").
   A certificate helps over time; the build does not depend on it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Nothing is published on the stable channel yet" | Nothing is live under `channels/stable.json`. Run `publish` and `promote` ([release.md](./release.md)). |
| "Download site unreachable" | Wrong `DL_SITE_URL`, no internet, or a firewall. |
| "... is made for D2R ..., found ..." | The game updated. Rebuild the plugin and bump `DL_REQUIRED_GAME_VERSION`. |
| "Cannot write into ..." | Run as administrator once, or give your user write access to the game folder. |
| Build error `DL_... undeclared` | Your config header is missing a setting. Compare it with `downloader/config/downloader_config.h`. |
