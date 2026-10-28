"""Test double for OpenSSH `sftp -b -` backed by a local folder ($FAKE_SFTP_ROOT), so tests/test_release_publish.py can
exercise the sftp backend of release/publish_release.py offline. Remote paths such as "/mymod/objects/ab" resolve inside
that folder. Batch-mode behaviour it imitates: each command is echoed as `sftp> <command>`; a failing command aborts with
exit 1 unless the line begins with '-'; `ls` on a missing folder writes `Can't ls: "<path>" not found` to stderr;
`ls -ln` gives long listings; `rename` overwrites an existing target (posix-rename) and can move folders; `reput` appends
to a remote file that exists and is smaller (otherwise it fails, as OpenSSH does). Supported commands: ls mkdir put reput
get rename rm rmdir. This is a test helper, not a sandbox.

  FAKE_SFTP_ROOT=DIR FAKE_SFTP_FAIL_PUT=N py -3 tests/release_fakes/fake_sftp.py -b - ... user@host

Switches (environment):
  FAKE_SFTP_FAIL_PUT=N        the N-th put of a run fails (a publish that dies part-way)
  FAKE_SFTP_LEGACY_RENAME=1   rename will not overwrite (a server without posix-rename)
  FAKE_SFTP_LOG=FILE          append each command, plus one "LOGIN" line per run
  FAKE_SFTP_CUT=FILE          counter file "N K": a run whose put/reput sends more than K bytes in total writes K bytes and
                              then drops the connection; this happens N times, and the file counts N down
"""
import os
import shutil
import sys
import time
from pathlib import Path


def tokens(line):
    out, cur, quoted, i = [], "", False, 0
    started = False
    while i < len(line):
        c = line[i]
        if quoted:
            if c == "\\" and i + 1 < len(line):
                i += 1
                cur += line[i]
            elif c == '"':
                quoted = False
            else:
                cur += c
        elif c == '"':
            quoted, started = True, True
        elif c.isspace():
            if started or cur:
                out.append(cur)
                cur, started = "", False
        else:
            cur += c
            started = True
        i += 1
    if started or cur:
        out.append(cur)
    return out


def main():
    root = Path(os.environ["FAKE_SFTP_ROOT"])
    fail_put = int(os.environ.get("FAKE_SFTP_FAIL_PUT", "0"))
    log = os.environ.get("FAKE_SFTP_LOG")
    puts = 0
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write("LOGIN\n")
    cut = os.environ.get("FAKE_SFTP_CUT")
    budget = None
    if cut and Path(cut).is_file():
        n, k = (int(x) for x in Path(cut).read_text().split())
        if n > 0:
            budget = k

    def real(p):
        return root.joinpath(*[x for x in p.split("/") if x])

    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        print(f"sftp> {line}", flush=True)
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        ignore = line.startswith("-")
        t = tokens(line[1:] if ignore else line)
        cmd, args = t[0], [a for a in t[1:] if not a.startswith("-")]
        err = None
        try:
            if cmd == "ls":
                path = args[0]
                p = real(path)
                if not p.is_dir():
                    err = f'Can\'t ls: "{path}" not found'
                else:
                    long = "-l" in t or "-ln" in t
                    for c in sorted(p.iterdir()):
                        if long:
                            st = c.stat()
                            mode = ("d" if c.is_dir() else "-") + "rwxr-xr-x"
                            when = time.strftime("%b %d %H:%M", time.gmtime(st.st_mtime))
                            print(f"{mode}    1 1000     1000     {st.st_size if c.is_file() else 4096} {when} {c.name}")
                        else:
                            print(c.name)
            elif cmd == "mkdir":
                p = real(args[0])
                if p.exists() or not p.parent.is_dir():
                    err = f"Couldn't create directory: Failure"
                else:
                    p.mkdir()
            elif cmd in ("put", "reput"):
                puts += 1
                dest = real(args[1])
                if fail_put and puts == fail_put:
                    err = "Connection closed"
                    print("Connection closed", file=sys.stderr, flush=True)
                    sys.exit(1)
                if not dest.parent.is_dir():
                    err = f"remote open(\"{args[1]}\"): No such file or directory"
                elif cmd == "reput" and not dest.is_file():
                    err = f"stat remote: No such file or directory"
                elif cmd == "reput" and dest.stat().st_size >= Path(args[0]).stat().st_size:
                    err = "destination file bigger or same size as source file"
                else:
                    data = Path(args[0]).read_bytes()
                    start = dest.stat().st_size if cmd == "reput" else 0
                    chunk = data[start:]
                    if budget is not None and len(chunk) > budget:
                        with open(dest, "ab" if cmd == "reput" else "wb") as f:
                            f.write(chunk[:budget])
                        n, k = (int(x) for x in Path(cut).read_text().split())
                        Path(cut).write_text(f"{n - 1} {k}")  # counted only when a connection is really cut
                        print("client_loop: send disconnect: Connection reset by peer", file=sys.stderr, flush=True)
                        sys.exit(255)
                    if budget is not None:
                        budget -= len(chunk)
                    with open(dest, "ab" if cmd == "reput" else "wb") as f:
                        f.write(chunk)
            elif cmd == "get":
                src = real(args[0])
                if not src.is_file():
                    err = f'File "{args[0]}" not found.'
                else:
                    shutil.copyfile(src, args[1])
            elif cmd == "rename":
                a, b = real(args[0]), real(args[1])
                if not a.exists():
                    err = "Couldn't rename file: No such file or directory"
                elif a.is_dir() and b.exists():
                    err = "Couldn't rename file: Failure"
                elif os.environ.get("FAKE_SFTP_LEGACY_RENAME") and b.exists():
                    err = "Couldn't rename file: Failure"  # SFTP v3 rename does not replace
                else:
                    os.replace(a, b)
            elif cmd == "rm":
                p = real(args[0])
                if not p.is_file():
                    err = f'Couldn\'t delete file: No such file or directory'
                else:
                    p.unlink()
            elif cmd == "rmdir":
                try:
                    real(args[0]).rmdir()
                except OSError:
                    err = "Couldn't remove directory: Failure"
            else:
                err = f"Invalid command: {cmd}"
        except (IndexError, OSError) as e:
            err = f"{cmd}: {e}"
        if err:
            print(err, file=sys.stderr, flush=True)
            if not ignore:
                sys.exit(1)


if __name__ == "__main__":
    main()
