# CDN sync note

This is optional. You need it only when players should download from a bucket behind a CDN instead of from your own
server. The full release guide is [docs/release.md](../docs/release.md).

```
download host            S3-compatible bucket            Cloudflare Worker             player
/srv/mod-files/mymod  --rclone--> my-mod-files/mymod/  <--fetch--  files.example.com/mymod/ <--HTTPS-- downloader
(dl_sync.py)                                       (cloudflare-worker.js)
```

The host stays the origin and the backup. After every ingest, promote and gc the host copies the changed files to the
bucket (`dl_sync.py`). The Worker serves the bucket under your own domain.

Placeholders used below: bucket `my-mod-files`, prefix `mymod`, rclone remote `mirror`, endpoint `s3.example.com`, site
`https://files.example.com/mymod/`. Use your own values; keep them the same in `release.toml` (`[mirror]`).

## 1. The bucket

1. Create a bucket (`my-mod-files`) at any S3-compatible provider.
2. Turn **file listing off** (private listing). Players only need to read single objects by name.
3. Make the objects publicly readable. Either the provider's "public read" setting for the bucket, or per-object ACLs
   (`dl_sync.py` sets `public-read` by default; pass `--acl ""` for a provider without ACLs).
4. Create an access key that is limited to this bucket (read, write, delete). It is shown once. It goes into the
   server's `rclone.conf` below and nowhere else: not in the repository, not in `release.toml`, not in chat.

## 2. rclone on the host

```sh
apt install rclone
install -d -m 0750 -o root -g modrelease /etc/mod-release
install -m 0640 -o root -g modrelease /dev/null /etc/mod-release/rclone.conf
nano /etc/mod-release/rclone.conf        # paste the two keys here, never on a command line
```

`/etc/mod-release/rclone.conf` (`type = s3` works for any S3-compatible provider; `provider = Other` is the generic choice):

```ini
[mirror]
type = s3
provider = Other
env_auth = false
access_key_id = <access key id>
secret_access_key = <secret access key>
endpoint = s3.example.com
acl = public-read
```

Check it as the upload user. This must list the bucket without an error (empty at first):

```sh
sudo -u modrelease rclone --config /etc/mod-release/rclone.conf --s3-no-check-bucket lsf mirror:my-mod-files
```

## 3. The sync script and its units

Copy `release/dl_sync.py` to `/opt/mod-release/dl_sync.py` (root-owned, mode 0755) and the three `release/server/mod-release-sync.*`
files to `/etc/systemd/system/`. Edit `--site`, `--remote` and `--config` in `mod-release-sync.service`. Then:

```sh
install -d -m 0755 -o modrelease -g modrelease /srv/mod-files/mymod/.sync
systemctl daemon-reload
sudo -u modrelease python3 -I /opt/mod-release/dl_sync.py --site /srv/mod-files/mymod --remote mirror:my-mod-files/mymod \
    --config /etc/mod-release/rclone.conf --state /tmp/mod-release-sync-dry --dry-run          # preview: nothing is sent
systemctl enable --now mod-release-sync.path mod-release-sync.timer
systemctl start mod-release-sync.service               # the first full sync
journalctl -u mod-release-sync -n 30 --no-pager        # the last line reports the uploaded and deleted counts
sudo -u modrelease python3 -I /opt/mod-release/dl_sync.py --site /srv/mod-files/mymod --remote mirror:my-mod-files/mymod \
    --config /etc/mod-release/rclone.conf --check      # sizes of all files, sha256 and headers of a sample
```

What `dl_sync.py` does, in order: objects first, then the rest of `releases/` and `downloader/`, then the release
manifests, then `channels/*.json` last, and deletions only at the very end (never an object a release on the site still
uses, and never more than half the bucket at once without `--allow-mass-delete`). Headers per prefix:

| Prefix | Cache-Control | Content-Type |
|---|---|---|
| `objects/` | `public, max-age=31536000, immutable` | `application/octet-stream` |
| `channels/` | `no-cache` | `application/json` |
| `releases/`, `downloader/` | `public, max-age=3600` | by extension |

## 4. The Cloudflare Worker

1. Put your domain on Cloudflare (DNS). Use a sub-domain for the files, for example `files.example.com`.
2. Create a Worker, paste `release/cloudflare-worker.js`, deploy.
3. Settings, Variables (plain text): `BUCKET_URL` = the bucket's public endpoint without a trailing slash, for example
   `https://my-mod-files.s3.example.com`, and `SITE_PREFIX` = `/mymod/`.
4. Add the custom domain `files.example.com` to the Worker.
5. If a check gets HTTP 403 from Cloudflare, switch off "Browser Integrity Check" for that host: the downloader is
   a program, not a browser.

The Worker answers GET and HEAD only, forwards `Range`, caches by the bucket's own `Cache-Control`, and turns the
bucket's 403 into a 404. It holds no secrets.

Set `site.url` in `release.toml` (and the downloader's site setting) to `https://files.example.com/mymod/`.

## 5. Check it

```sh
curl -sI https://files.example.com/mymod/channels/stable.json     # 200, cache-control: no-cache
curl -sI https://files.example.com/mymod/objects/ab/<some sha256> # immutable, accept-ranges: bytes
curl -sI https://files.example.com/mymod/.sync/status.json        # 404: dot files are never uploaded
py -3 release/publish_release.py verify --url https://files.example.com/mymod/ --channel stable
py -3 release/publish_release.py list                             # last line: Mirror sync: ok at ...
```

## 6. When a channel looks stale

Channel files are `no-cache`, so a new version shows at once. If a CDN layer in between ever keeps an old one, purge
`mymod/channels/*` in that layer, or in the Worker's zone purge the two channel URLs by hand. A `publish --force` of an
existing version replaces files under `releases/<v>/`; purge them as well, or let their one-hour cache run out.

## 7. Rebuilding the bucket

The host is the origin, so a lost bucket is refilled by `systemctl start mod-release-sync`. A sync refuses to delete more than
half of the bucket in one go; after a large `gc`, run `dl_sync.py --allow-mass-delete` by hand once.
