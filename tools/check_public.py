#!/usr/bin/env python3
"""check_public: scan a repository for things that must not be published.

Two kinds of checks run on every tracked file (`git ls-files`), including text hidden inside binaries and the
text chunks of PNG images, and on the commit messages and author fields of the current branch:

1. Private terms. A list of banned words kept OUTSIDE the repository (one per line, case-insensitive
   substring match, `#` starts a comment). Default file: ~/.d2r-modkit/private_terms.txt. Override with the
   environment variable D2RAI_PRIVATE_TERMS or --terms. A hit is reported as `file:line: private term #N`: the
   term itself is never printed, so logs and CI output cannot leak it.
2. Generic checks that every project benefits from: e-mail addresses (except noreply and example.* ones),
   IPv4 addresses, Windows user paths (C:\\Users\\<name>), home directories, private keys and access tokens,
   and PNG text/EXIF metadata.

If no terms file exists the generic checks still run, and the report says so.

False positives in the generic checks go in `check_public.allow` (see --allow). Private-term hits cannot be
allow-listed: rewrite the text instead.

Exit status: 0 clean, 1 hits found, 2 usage error.

    python tools/check_public.py                 scan the whole tracked tree and the branch history
    python tools/check_public.py --staged        scan only what is staged (used by the pre-commit hook)
    python tools/check_public.py --message-file F  scan one commit message (used by the commit-msg hook)
    python tools/check_public.py --strip-png     remove text/EXIF chunks from tracked PNG files, in place
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import os
import re
import struct
import subprocess
import sys
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TERMS = Path.home() / ".d2r-modkit" / "private_terms.txt"
ENV_TERMS = "D2RAI_PRIVATE_TERMS"
ALLOW_NAME = "check_public.allow"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_TEXT_CHUNKS = (b"tEXt", b"zTXt", b"iTXt", b"eXIf")
MAX_ZIP_DEPTH = 2
MAX_ZIP_MEMBER = 64 * 1024 * 1024


@dataclass(frozen=True)
class Hit:
    where: str       # file path, or "commit <sha>"
    line: str        # line number, "0x<offset>", chunk name or field name
    kind: str        # "private-term" or a generic category
    detail: str      # never contains a private term

    def __str__(self) -> str:
        return f"{self.where}:{self.line}: [{self.kind}] {self.detail}"


# ---------------------------------------------------------------------------------------------------------------
# Terms


def terms_path(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    env = os.environ.get(ENV_TERMS)
    return Path(env).expanduser() if env else DEFAULT_TERMS


def load_terms(path: Path) -> list[str] | None:
    """Terms in file order, lower-cased; None if the file does not exist."""
    if not path.is_file():
        return None
    terms = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        t = raw.strip()
        if t and not t.startswith("#"):
            terms.append(t.lower())
    return terms


# ---------------------------------------------------------------------------------------------------------------
# Generic checks

_PLACEHOLDER_USERS = {"public", "default", "all users", "user", "username", "you", "yourname", "your-name",
                      "name", "me", "<user>", "%username%", "$user", "{user}", "example", "foo", "someone"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)")
IPV4_RE = re.compile(r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3})(?![\d]|\.\d)")
WINPATH_RE = re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"'<>|*?:]+)", re.IGNORECASE)
HOMEPATH_RE = re.compile(r"(?<![\w.~-])/(?:home|Users)/([A-Za-z0-9._-]+)")
SECRET_RES = [
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("API key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{32,}")),
    ("credential assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d|auth)\w*['\"]?\s*[:=]\s*(?:['\"][^'\"\s]{12,}['\"]|[0-9a-f]{32,})")),
]
# Documentation address blocks (RFC 5737), loopback and "any" addresses are not leaks.
_SAFE_IP_PREFIXES = ("127.", "0.", "192.0.2.", "198.51.100.", "203.0.113.", "255.")


def _is_example_domain(domain: str) -> bool:
    d = domain.lower()
    return d in {"example.com", "example.org", "example.net"} or d.endswith((".example.com", ".example.org",
            ".example.net", ".example", ".invalid", ".test", ".localhost"))


def _mask_email(m: re.Match) -> str:
    local = m.group(0).split("@")[0]
    return f"{local[:1]}***@{m.group(1)}"


def generic_line_hits(text: str, binary: bool = False) -> list[tuple[str, str, str]]:
    """(kind, detail, matched_text) for each generic problem on one line."""
    out: list[tuple[str, str, str]] = []
    for m in EMAIL_RE.finditer(text):
        domain = m.group(1)
        if "noreply" in m.group(0).lower() or _is_example_domain(domain):
            continue
        # "name@2x.png"-style file names and decorators are not addresses
        if re.search(r"\.(png|jpe?g|gif|webp|svg|css|js|json|txt|md)$", domain, re.IGNORECASE):
            continue
        out.append(("email", f"e-mail address {_mask_email(m)}", m.group(0)))
    if not binary:
        for m in IPV4_RE.finditer(text):
            ip = m.group(1)
            if not ip.startswith(_SAFE_IP_PREFIXES):
                out.append(("ip-address", f"IPv4 address {ip}", ip))
    for m in WINPATH_RE.finditer(text):
        if m.group(1).lower() not in _PLACEHOLDER_USERS and not m.group(1).startswith(("<", "%", "$", "{")):
            out.append(("user-path", "Windows user path with a real-looking user name", m.group(0)))
    for m in HOMEPATH_RE.finditer(text):
        if m.group(1).lower() not in _PLACEHOLDER_USERS:
            out.append(("user-path", "home directory path with a real-looking user name", m.group(0)))
    for label, rx in SECRET_RES:
        m = rx.search(text)
        if m:
            out.append(("secret", f"possible {label} ({m.group(0)[:6]}...)", m.group(0)))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Allow file


@dataclass
class Allow:
    """Entries are `<path glob>` or `<path glob> :: <regex>`. A glob alone allows every generic hit in the matching
    files; with a regex only hits whose matched text or line matches it."""
    rules: list[tuple[str, re.Pattern | None]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None) -> "Allow":
        a = cls()
        if path is None or not path.is_file():
            return a
        for raw in path.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            glob, sep, rx = s.partition("::")
            a.rules.append((glob.strip(), re.compile(rx.strip()) if sep and rx.strip() else None))
        return a

    def allows(self, path: str, matched: str, line: str) -> bool:
        for glob, rx in self.rules:
            if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob.rstrip("/") + "/*"):
                if rx is None or rx.search(matched) or rx.search(line):
                    return True
        return False


# ---------------------------------------------------------------------------------------------------------------
# Scanning


class Scanner:
    def __init__(self, terms: list[str] | None, allow: Allow):
        self.terms = terms or []
        self.allow = allow
        self.hits: list[Hit] = []
        self._seen: set[tuple] = set()

    def mask(self, text: str) -> str:
        """Replace every private term in text (file names can contain one) with its index."""
        for idx, term in enumerate(self.terms, 1):
            text = re.sub(re.escape(term), f"<#{idx}>", text, flags=re.IGNORECASE)
        return text

    def _add(self, hit: Hit) -> None:
        hit = Hit(self.mask(hit.where), hit.line, hit.kind, self.mask(hit.detail))
        key = (hit.where, hit.line, hit.kind, hit.detail)
        if key not in self._seen:
            self._seen.add(key)
            self.hits.append(hit)

    # -- text ---------------------------------------------------------------------------------------------------

    def term_hits_in(self, lowered: str) -> list[int]:
        return [i for i, t in enumerate(self.terms, 1) if t in lowered]

    def scan_text(self, where: str, text: str, *, binary: bool = False, line_label=None, allow_path: str | None = None) -> None:
        for n, line in enumerate(text.splitlines() or [text], 1):
            label = line_label(n) if line_label else str(n)
            for idx in self.term_hits_in(line.lower()):
                self._add(Hit(where, label, "private-term", f"private term #{idx}"))
            for kind, detail, matched in generic_line_hits(line, binary):
                if not self.allow.allows(allow_path or where, matched, line):
                    self._add(Hit(where, label, kind, detail))

    def scan_name(self, path: str) -> None:
        low = path.lower()
        for idx in self.term_hits_in(low):
            self._add(Hit(path, "path", "private-term", f"private term #{idx} in the file name"))

    # -- bytes --------------------------------------------------------------------------------------------------

    def scan_bytes(self, path: str, data: bytes, depth: int = 0, where: str | None = None) -> None:
        where = where or path
        if is_binary(data):
            self.scan_binary(path, data, depth, where)
        else:
            self.scan_text(where, decode_text(data), allow_path=path)

    def scan_binary(self, path: str, data: bytes, depth: int, where: str) -> None:
        low = data.lower()
        for idx, term in enumerate(self.terms, 1):
            for enc in ("utf-8", "utf-16-le"):
                try:
                    needle = term.encode(enc)
                except UnicodeEncodeError:
                    continue
                pos = low.find(needle)
                if pos >= 0:
                    self._add(Hit(where, f"0x{pos:x}", "private-term", f"private term #{idx} inside binary data"))
        for off, s in extract_strings(data):
            for kind, detail, matched in generic_line_hits(s, binary=True):
                if not self.allow.allows(path, matched, s):
                    self._add(Hit(where, f"0x{off:x}", kind, detail))
        if data.startswith(PNG_MAGIC):
            self.scan_png(path, data, where)
        elif depth < MAX_ZIP_DEPTH and data[:2] == b"PK" and zipfile.is_zipfile(io.BytesIO(data)):
            self.scan_zip(path, data, depth, where)

    def scan_png(self, path: str, data: bytes, where: str) -> None:
        for name, payload in png_text_chunks(data):
            tag = name.decode("ascii", "replace")
            if not self.allow.allows(path, tag, tag):
                self._add(Hit(where, tag, "png-metadata", f"PNG {tag} chunk (strip it: tools/check_public.py --strip-png)"))
            text = decode_png_chunk(name, payload)
            self.scan_text(where, text, line_label=lambda n, tag=tag: f"{tag}.{n}", allow_path=path)

    def scan_zip(self, path: str, data: bytes, depth: int, where: str) -> None:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                inner = f"{where}!{info.filename}"
                self.scan_name(inner)
                if info.is_dir() or info.file_size > MAX_ZIP_MEMBER:
                    continue
                try:
                    self.scan_bytes(path, zf.read(info), depth + 1, where=inner)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
                    continue

    # -- commits ------------------------------------------------------------------------------------------------

    def scan_commit(self, sha: str, fields: dict[str, str]) -> None:
        where = f"commit {sha[:10]}"
        for name in ("author", "committer"):
            ident = fields.get(name, "")
            self.scan_text(where, ident, line_label=lambda n, name=name: name)
        self.scan_text(where, fields.get("message", ""), line_label=lambda n: f"message.{n}")


def is_binary(data: bytes) -> bool:
    head = data[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    return b"\x00" in head


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


_STRING_RE = re.compile(rb"[\x20-\x7e]{6,}")
_WSTRING_RE = re.compile(rb"(?:[\x20-\x7e]\x00){6,}")


def extract_strings(data: bytes):
    """ASCII and UTF-16LE printable runs (>= 6 chars) with their byte offset."""
    for m in _STRING_RE.finditer(data):
        yield m.start(), m.group().decode("ascii")
    for m in _WSTRING_RE.finditer(data):
        yield m.start(), m.group().decode("utf-16-le")


# -- PNG ------------------------------------------------------------------------------------------------------------


def png_chunks(data: bytes):
    """Yield (offset, length, type, payload) for each chunk; stops quietly at damage."""
    i = len(PNG_MAGIC)
    while i + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[i:i + 8])
        end = i + 12 + length
        if end > len(data):
            return
        yield i, length, ctype, data[i + 8:i + 8 + length]
        i = end


def png_text_chunks(data: bytes):
    for _, _, ctype, payload in png_chunks(data):
        if ctype in PNG_TEXT_CHUNKS:
            yield ctype, payload


def decode_png_chunk(ctype: bytes, payload: bytes) -> str:
    """Best-effort text of a metadata chunk, including compressed ones."""
    try:
        if ctype == b"zTXt":
            key, _, rest = payload.partition(b"\x00")
            return key.decode("latin-1") + "\n" + zlib.decompress(rest[1:]).decode("latin-1")
        if ctype == b"iTXt":
            key, _, rest = payload.partition(b"\x00")
            compressed, rest = rest[0], rest[2:]
            _lang, _, rest = rest.partition(b"\x00")
            _tkey, _, body = rest.partition(b"\x00")
            if compressed:
                body = zlib.decompress(body)
            return key.decode("latin-1") + "\n" + body.decode("utf-8", errors="replace")
    except (zlib.error, IndexError):
        pass
    return payload.decode("latin-1")


def strip_png(data: bytes) -> bytes:
    """Return the PNG without tEXt/zTXt/iTXt/eXIf chunks (other chunks are copied byte for byte)."""
    if not data.startswith(PNG_MAGIC):
        return data
    out = bytearray(PNG_MAGIC)
    i = len(PNG_MAGIC)
    for off, length, ctype, _ in png_chunks(data):
        if ctype not in PNG_TEXT_CHUNKS:
            out += data[off:off + 12 + length]
        i = off + 12 + length
    out += data[i:]
    return bytes(out)


# ---------------------------------------------------------------------------------------------------------------
# Git plumbing


def git(root: Path, *args: str, check: bool = True) -> bytes:
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout


def repo_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel").decode().strip())


def tracked_files(root: Path) -> list[str]:
    raw = git(root, "ls-files", "-z", "--cached")
    return [p for p in raw.decode("utf-8", errors="surrogateescape").split("\0") if p]


def staged_files(root: Path) -> list[str]:
    raw = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in raw.decode("utf-8", errors="surrogateescape").split("\0") if p]


def commits(root: Path, limit: int | None = None):
    """(sha, {author, committer, message}) for every commit reachable from HEAD."""
    fmt = "%H%x1f%an <%ae>%x1f%cn <%ce>%x1f%B%x1e"
    args = ["log", f"--format={fmt}"]
    if limit:
        args.append(f"-n{limit}")
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if res.returncode != 0:      # no commits yet
        return
    for rec in res.stdout.decode("utf-8", errors="replace").split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, author, committer, message = rec.split("\x1f", 3)
        yield sha, {"author": author, "committer": committer, "message": message}


# ---------------------------------------------------------------------------------------------------------------
# Runs


def scan_tree(sc: Scanner, root: Path, staged: bool) -> int:
    files = staged_files(root) if staged else tracked_files(root)
    n = 0
    for rel in files:
        sc.scan_name(rel)
        if staged:
            data = git(root, "show", f":{rel}", check=False)
        else:
            p = root / rel
            if not p.is_file():       # submodule, symlink to a dir, or deleted in the working tree
                continue
            data = p.read_bytes()
        sc.scan_bytes(rel, data)
        n += 1
    return n


def strip_tracked_pngs(root: Path) -> list[str]:
    changed = []
    for rel in tracked_files(root):
        if rel.lower().endswith(".png") and (root / rel).is_file():
            old = (root / rel).read_bytes()
            new = strip_png(old)
            if new != old:
                (root / rel).write_bytes(new)
                changed.append(rel)
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--terms", help=f"private terms file (default: ${ENV_TERMS} or {DEFAULT_TERMS})")
    ap.add_argument("--require-terms", action="store_true", help="fail (exit 2) if the terms file is missing")
    ap.add_argument("--allow", help=f"allow file (default: <repo>/{ALLOW_NAME})")
    ap.add_argument("--root", default=".", help="repository to scan (default: current directory)")
    ap.add_argument("--staged", action="store_true", help="scan staged content only, no history")
    ap.add_argument("--no-history", action="store_true", help="do not scan commit messages and author fields")
    ap.add_argument("--message-file", help="scan this commit message file only (commit-msg hook)")
    ap.add_argument("--strip-png", action="store_true", help="strip text/EXIF chunks from tracked PNG files, then exit")
    ap.add_argument("--quiet", action="store_true", help="print hits only")
    args = ap.parse_args(argv)

    tpath = terms_path(args.terms)
    terms = load_terms(tpath)
    if terms is None and args.require_terms:
        print(f"check_public: terms file not found: {tpath}", file=sys.stderr)
        return 2

    if args.message_file:
        root = Path(args.root)
        allow = Allow.load(Path(args.allow) if args.allow else root / ALLOW_NAME)
        sc = Scanner(terms, allow)
        text = Path(args.message_file).read_text(encoding="utf-8", errors="replace")
        sc.scan_text("commit message", text, line_label=lambda n: str(n))
        return report(sc, tpath, terms, "the commit message", args.quiet)

    try:
        root = repo_root(Path(args.root))
    except (RuntimeError, FileNotFoundError) as e:
        print(f"check_public: not a git repository: {e}", file=sys.stderr)
        return 2

    if args.strip_png:
        changed = strip_tracked_pngs(root)
        for rel in changed:
            print(f"stripped {rel}")
        print(f"{len(changed)} PNG file(s) changed")
        return 0

    allow = Allow.load(Path(args.allow) if args.allow else root / ALLOW_NAME)
    sc = Scanner(terms, allow)
    nfiles = scan_tree(sc, root, args.staged)
    ncommits = 0
    if not args.staged and not args.no_history:
        for sha, fields in commits(root):
            sc.scan_commit(sha, fields)
            ncommits += 1
    what = f"{nfiles} file(s)" + (f" and {ncommits} commit(s)" if ncommits else "")
    return report(sc, tpath, terms, what, args.quiet)


def report(sc: Scanner, tpath: Path, terms: list[str] | None, what: str, quiet: bool) -> int:
    for h in sc.hits:
        print(h)
    if terms is None and not quiet:
        print(f"check_public: no private terms file at {tpath} (set ${ENV_TERMS} or --terms): "
              "ran the generic checks only", file=sys.stderr)
    if sc.hits:
        print(f"check_public: {len(sc.hits)} problem(s) in {what}. Private terms are shown as '#N' "
              "(line N of the terms file, counting only term lines).", file=sys.stderr)
        return 1
    if not quiet:
        mode = "generic checks" if terms is None else f"generic checks + {len(terms)} private term(s)"
        print(f"check_public: OK, scanned {what} ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
