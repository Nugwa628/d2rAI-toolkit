# Distributing a mod

How players get your mod's files. The toolkit's own pipeline ([release.md](./release.md) plus the
[downloader](./downloader.md)) is one choice. GitHub Releases and Nexus Mods are others, and they mix well. This page
compares them and shows the designs that work.

Contents: [The options](#the-options) · [Comparison](#comparison) · [Self-hosted + CDN + downloader](#self-hosted--cdn--downloader) ·
[GitHub Releases](#github-releases) · [Nexus Mods](#nexus-mods) · [Combinations](#combinations) ·
[Choosing](#choosing) · [Sources](#sources)

## The options

| Option | What players do | What you run |
|---|---|---|
| **Self-hosted + CDN + downloader** | Download one `.exe`, press Install. Updates fetch only the changed files | A download host, optionally a CDN bucket, `release/` scripts, the downloader |
| **GitHub Releases** | Download a `.7z` from the release page and extract it | Nothing: GitHub hosts the files |
| **Nexus Mods** | Download from the mod page, by hand or with a mod manager | Nothing: Nexus hosts the files |
| **Combinations** | Whichever the player prefers | One of the above as the main source, the others as mirrors |

Every option ships the same thing: the finished mod folder that goes into `<D2R>\mods\<Name>\`.
`release/package_release.py` makes it, as a `.7z` with that folder at the top.

## Comparison

| | Self-hosted + downloader | GitHub Releases | Nexus Mods |
|---|---|---|---|
| **Bandwidth cost to you** | Yours (host and CDN egress). A CDN with free egress keeps it small | None. GitHub states no bandwidth limit for release files | None |
| **Download speed for players** | Your host or CDN | Fast | Capped for free accounts (about 1.5 to 3 MB/s); unlimited with Premium |
| **Discoverability** | Only what you advertise | Low (developers, not players) | High: D2R has its own Nexus section, search, endorsements, tracking |
| **Install** | One click, no extracting | Extract by hand | Extract by hand, or a mod manager |
| **Updates** | Automatic, only changed files (delta) | Players check and re-download | Players get "update available" when they track the mod; they re-download the whole archive |
| **File size limit** | Yours | Under 2 GiB per file | Large files are allowed (check the upload form for the current limit) |
| **Version check by a program** | Your channel file, no key | Public REST API, no key for public repos (rate-limited) | API needs a **per-user** API key |
| **Automated upload** | `release.sh` | REST API (`github_release.py`) | Upload API (open beta in 2026) and an official GitHub Action |
| **Your downloader on the page** | Yes | Yes (`--downloader` attaches it) | **No.** Nexus does not allow executables that download updates |
| **Rules to follow** | Yours | GitHub's terms | Nexus file submission guidelines, including an **AI disclosure tag** |

## Self-hosted + CDN + downloader

The toolkit's full pipeline: [release.md](./release.md) and [downloader.md](./downloader.md).

- **What you run:** a small Linux host with SFTP. Uploads land in `/srv/mod-files/incoming/`, owned by the
  `modrelease` user. The systemd units `mod-release-ingest` (checks each upload and publishes it) and, with a CDN,
  `mod-release-sync` (mirrors the site to an S3-compatible bucket) do the server's half
  ([The download host](./release.md#the-download-host), [CDN mirror](./release.md#cdn-mirror)).

- **Pros:** one-click installs, delta updates, repair, environment checks (wrong default mod, stray plugin copies),
  your name and colors, no third-party rules, release channels (`test`, `stable`).
- **Cons:** you pay for and look after a host. Players find you only through your own links. A downloader `.exe`
  from an unknown site makes some players (and virus scanners) nervous: sign it ([downloader.md](./downloader.md)).

## GitHub Releases

A release page per version, with the `.7z` attached. `release/github_release.py` already creates the page from your
changelog. It attaches the downloader, not the 7z, because the toolkit's pipeline keeps the 7z on the download host.
To serve the 7z from GitHub instead, upload it as a release asset (the GitHub web form, or `gh release upload v<version>
dist/MyMod-<version>.7z`).

- GitHub's limits: each release file must be under 2 GiB, up to 1000 files per release, and GitHub states no limit on
  total size or bandwidth.
- A program can read the latest version without a key:
  `GET https://api.github.com/repos/<owner>/<repo>/releases/latest` (unauthenticated calls are rate-limited per IP).
- **Pros:** free, fast, permanent links, works with a public source repo.
- **Cons:** players don't browse GitHub for D2R mods. No update notice for players.

## Nexus Mods

Nexus Mods has a [Diablo II: Resurrected section](https://www.nexusmods.com/diablo2resurrected) (game domain
`diablo2resurrected`). It hosts the files for free and is where many D2R players look for mods.

### A Nexus-ready archive

Players extract the archive into `<D2R>\mods\`. The archive must therefore hold **one top folder, named like the mod
folder**:

```text
MyMod-1.0.0-nexus.7z
└── MyMod\
    ├── INSTALL.txt                  install, update and uninstall steps
    ├── MyMod.mpq\
    │   ├── modinfo.json             {"name": "MyMod", "savepath": "MyMod/"}
    │   └── data\...                 the built tables, strings, layouts, HD art
    └── d2rloader\                   only for mods with a plugin or extended saves
        ├── plugins\mymod-core.dll
        └── config\d2rloader.toml
```

Make it with `--nexus`:

```powershell
py -3 release/package_release.py --mod-dir path\to\MyMod --version 1.0.0 --plugin build\mymod-core.dll `
    --plugin-version 1.0.0 --nexus
```

Next to the normal files, `dist\` then has `MyMod-1.0.0-nexus.7z`: the same mod folder plus `MyMod\INSTALL.txt`.
The INSTALL.txt is written from your `release.toml` and says:

- the D2R version the mod was built for;
- whether **D2RLoader** is needed. It is needed when the folder has a plugin DLL (`d2rloader\plugins\*.dll`), and the
  text names the pinned version and its site. A data-only mod does not need it. The text then says to start the game
  with `-mod MyMod -txt` (Battle.net: Game Settings, Additional command line arguments; Steam: Launch Options);
- how to extract, what the right result looks like, and the most common mistake (one folder too deep);
- how to update (delete `MyMod.mpq\data`, extract again, keep `d2rloader.toml`) and uninstall;
- to keep the mod off Battle.net.

It never mentions the downloader. The `.7z` itself stays reproducible and is listed in `SHA256SUMS`.

### Uploading files

On the mod page, **Manage files** takes an archive plus:

| Field | Use |
|---|---|
| **Category** | **Main files**: what a player needs. **Optional files**: extras (a translation, an alternative HD pack). **Update files**: a patch that moves an older version to a newer one. **Miscellaneous**, and **Old versions** for superseded files (or archive them) |
| **File version** | Use the release version (`1.0.1`). The file version does not have to match the mod's version, so set both |
| **Mod version** | The mod page's version. Players who track the mod see an update when it changes |
| **Changelog** | Per version, on the mod page's Logs tab. Paste the `### 1.0.1` section of `CHANGELOG.md` |
| **Newer version of** (the update chain) | Mark a new main file as the update of the previous one, so mod managers and "update available" follow the chain |
| **Mod manager download** | Shows the "Mod Manager Download" button for the file |

For each release: upload `MyMod-<version>-nexus.7z` as a **Main file**, mark it as the newer version of the last one
(or archive the old one), set the mod version, and paste the changelog. Full archives are simpler than update files
for D2R: the mod folder is replaced as a whole anyway.

**Automated uploads.** Nexus Mods opened an Upload API (v3, `https://api.nexusmods.com/v3`) as an open beta in 2026,
with an official GitHub Action, [`Nexus-Mods/upload-action`](https://github.com/Nexus-Mods/upload-action). It adds a
new version to an existing file: inputs are your API key, the `file_id` (from the Files tab's "Advanced" view), the
archive, the version, and optionally `category`, `changelog` (with `mod_id`), `archive_existing_version` and
`update_mod_version`. The first upload of a mod is done by hand. The API is still in beta: check its README before you
depend on it, and keep the key in a CI secret.

### What the public API allows

The read API is REST at `https://api.nexusmods.com/v1` (there is also a GraphQL API at `/v2/graphql`). Every request
sends the user's key in the `apikey` header, plus `Application-Name` and `Application-Version`.

| Endpoint (`/v1/games/diablo2resurrected/...`) | Returns |
|---|---|
| `mods/{mod_id}.json` | The mod: name, summary, **`version`** (the mod version), author, updated time |
| `mods/{mod_id}/files.json?category=main` | The files: `file_id`, `category_name`, `version`, `mod_version`, `is_primary`, `uploaded_timestamp`, `changelog_html`; and `file_updates` (the "newer version of" chain) |
| `mods/{mod_id}/files/{file_id}.json` | One file |
| `mods/{mod_id}/changelogs.json` | Changelogs by version |
| `mods/updated.json?period=1d` (`1w`, `1m`) | Mods updated in that period |
| `mods/{mod_id}/files/{file_id}/download_link.json` | Short-lived download URLs. See below |
| `/v1/users/validate.json` | Checks a key; tells you whether the user is Premium |

What that means for a third-party downloader:

- **Each user needs their own API key.** There is no anonymous access. A user makes a personal key on their Nexus
  account's API page, or a registered app gets one through Nexus's single sign-on.
- **Free users can't be served files directly.** `download_link.json` answers for **Premium** users only, unless the
  request carries the `key` and `expires` values from an `nxm://` link. Only the website makes those: the player clicks
  **Mod Manager Download**, the browser opens
  `nxm://diablo2resurrected/mods/<mod>/files/<file>?key=...&expires=...&user_id=...`, and the program registered for
  `nxm://` receives it. The key belongs to that user and that file, and it expires. So a downloader cannot fetch files
  for free users on its own. It can only take over after the player clicks the button on the website.
- **The download URLs expire** within minutes. Don't store them.
- **Rate limits:** 20,000 requests per user per 24 hours, then 500 per hour. The daily quota resets at 00:00 GMT. A
  version check is one request, so this only matters for tools that scan many mods.
- **Terms of use for apps** (the API Acceptable Use Policy): send an accurate `Application-Name` and
  `Application-Version`. A personal key is for testing and personal use only. A public app must be **registered**:
  e-mail Nexus support with a test build that works with a personal key, then send a name, short description and logo.
  Nexus then issues the app's **slug** for single sign-on. Nexus may refuse registration. Don't store users' keys on
  your server, and don't make requests the user didn't start. Don't bulk-fetch Nexus data to rehost it.

### File rules that matter for a D2R mod

From Nexus's file submission guidelines:

- **No updater executables.** Executables that connect to the internet to send or download data are not allowed unless
  that connection is crucial to the mod. Auto-update doesn't count. So the toolkit's downloader can't be uploaded to
  Nexus, and the Nexus archive must not contain it.
- **No game files** without the owner's permission. The toolkit already ships only what you changed (no byte copies
  of the game's files with `--game-extract`).
- **Permission** from other authors whose work you include, and credit.
- **AI disclosure.** Content made with generative AI needs one of the tags *AI-Generated Content*, *AI Media* or
  *AI Assisted*. A mod built with an AI agent and this toolkit needs one.
- No content that disrupts online multiplayer. Keep the mod offline.

### Mod managers

- **Vortex** lists Diablo II: Resurrected, through a community game extension. That extension was written for MPQ mods,
  lets only one mod be active at a time, and is several years old. Its description doesn't cover D2RLoader or plugins.
  Treat it as a convenience for simple data mods, and test it before you tell players to use it.
- **D2RMM**, a D2R mod manager distributed on Nexus, handles `nxm://` links, so its users can press **Mod Manager
  Download**. It checks for updates when a mod's info names its Nexus page. D2RMM mods are scripts that it applies to
  the game's data. A finished mod folder from this toolkit isn't one, so D2RMM is not the install path for these mods.
- For mods with a plugin, **extracting by hand** (INSTALL.txt) is the reliable path.

### Pros and cons of Nexus

- **Pros:** free hosting and bandwidth, the biggest audience, endorsements and comments, "update available" for
  players who track the mod, version history.
- **Cons:** free players download slowly and re-download the whole archive for each update. There is no one-click
  install for plugin mods. Your downloader can't be on the page. The site's rules and moderation apply. Automation is
  still in beta.

## Combinations

### a. Nexus only

Upload `--nexus` archives, nothing else. Players download by hand or with a mod manager and follow INSTALL.txt.

- Best for: a first public version, small mods, no budget for hosting.
- Release flow: `package_release.py --nexus`, upload by hand or with the upload Action, paste the changelog.
- You lose: delta updates, repair, the environment checks, one-click install.

### b. Nexus for files, downloader for "is there an update?"

Nexus hosts every file. Your downloader (or the plugin's status line) only checks the version and opens the mod page.

- The version source should **not** need a key. Asking every player for a Nexus API key just to check a version is a
  poor deal, and the rules forbid shipping your own key in a public app. Publish a tiny version file instead: a channel
  JSON on any static host, or GitHub's `releases/latest`. The check reads it and opens
  `https://www.nexusmods.com/diablo2resurrected/mods/<id>?tab=files` when it's newer.
- If you do want to read Nexus itself (for example from a tool for authors), call `mods/{mod_id}.json` with the
  user's own key and compare `version`.
- **The downloader must not download from Nexus.** For free users it can't, and it must not try to work around the
  website.
- A version checker that downloads nothing may still count as an internet-connected executable. Host it on your own
  page or GitHub, not in the Nexus archive.
- This is not built in yet: the [downloader](./downloader.md) reads a channel file and installs from your host. A
  "check only" mode would be a small change to it.

### c. Self-host as the main source, mirror to Nexus

The full pipeline stays the main source ([release.md](./release.md)), and every release is also uploaded to Nexus as a
`--nexus` archive.

- Best for: an established mod that wants both one-click updates and Nexus's audience.
- On the Nexus page, link to your own site for the downloader (a link is fine, the exe is not).
- Upload to Nexus **after** `release.sh` has verified and promoted the version, so the two never disagree. The same
  rule as for release pages: a Nexus file for a version that isn't live yet is a bug.
- Costs: you still pay for your host, but the players who come through Nexus don't use your bandwidth.

GitHub Releases mixes in the same way: attach the 7z to the release page as a free mirror.

## Choosing

| You are... | Pick |
|---|---|
| Publishing a first version, data only | **Nexus only** (a), or GitHub Releases if your source is public |
| A mod with a plugin, many players, frequent updates | **Self-host + downloader**, mirrored to Nexus (c) |
| Keen on Nexus's audience but not on running a server | **Nexus + version check** (b) |
| Unable to afford hosting, but wanting fast downloads | **GitHub Releases**, linked from a Nexus page |

Whatever you pick, ask the owner before the first public upload ([d2r-release skill](../.claude/skills/d2r-release/SKILL.md)):
publishing is outward-facing and hard to undo.

## Sources

Checked in October 2026. Nexus changes its API and rules from time to time: re-check before you build on them.

- Nexus Mods API Acceptable Use Policy: https://help.nexusmods.com/article/114-api-acceptable-use-policy
- Rate limits: https://help.nexusmods.com/article/105-i-have-reached-a-daily-or-hourly-limit-api-requests-have-been-consumed-rate-limit-exceeded-what-does-this-mean
- Download speed caps by membership: https://help.nexusmods.com/article/96-download-speed-caps-adblockers-and-different-types-of-membership
- File submission guidelines: https://help.nexusmods.com/article/28-file-submission-guidelines
- The official API client (endpoints, `key`/`expires`, headers, file categories): https://github.com/Nexus-Mods/node-nexus-api
- Upload API GitHub Action: https://github.com/Nexus-Mods/upload-action
- Upload API open beta and later monthly roundups on https://www.nexusmods.com/news
- Vortex game support list: https://www.nexusmods.com/site/about/vortex
- GitHub release limits: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
