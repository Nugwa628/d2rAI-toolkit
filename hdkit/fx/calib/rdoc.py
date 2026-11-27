"""RenderDoc, project-local: the capture layer for one game's environment only, and RenderDoc's own Python for scripts.

Nothing is registered on the system: the Vulkan layer is found through VK_LAYER_PATH (a folder holding a copy of the
release's layer manifest with its library path made absolute) and enabled with VK_INSTANCE_LAYERS / VK_LOADER_LAYERS_ENABLE,
and only in the environment handed to the game. The official Linux release (MIT; https://renderdoc.org) ships no Python
module for the host's Python, so scripts run in the Python 3.8 embedded in `qrenderdoc --python <script>`, on a private
Xvfb display, with HOME pointing at a folder of our own (its UI.config has analytics and update checks off, so it asks
nothing and sends nothing). Arguments go to the script as JSON in env HDKIT_RD_ARGS; it writes its answer to args["out"].

  root = find_root()                           # env HDKIT_RENDERDOC or the argument: the unpacked release
  env = game_env(root, work, capfile)          # add to the game's environment
  ans = run_script(root, SCRIPT, {...}, home)  # trigger / extract (rd_trigger.py, rd_extract.py)
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRIGGER = HERE / "rd_trigger.py"
EXTRACT = HERE / "rd_extract.py"
LAYER = "VK_LAYER_RENDERDOC_Capture"
UI_CONFIG = {"rdocConfigData": 1, "Analytics_TotalOptOut": True, "Analytics_ManualCheck": False,
             "CheckUpdate_AllowChecks": False, "Tips_HasSeenFirst": True}


def find_root(root=None) -> Path:
    r = Path(root or os.environ.get("HDKIT_RENDERDOC") or "")
    if not (r / "lib" / "librenderdoc.so").is_file() or not (r / "bin" / "qrenderdoc").is_file():
        raise SystemExit("no RenderDoc release at %r (set HDKIT_RENDERDOC to the unpacked renderdoc_1.x folder)" % str(r))
    return r


def layer_dir(root: Path, work: Path) -> Path:
    """<work>/vklayer/renderdoc_capture.json: the release's manifest with an absolute library_path (and its own
    enable_environment left as it is: VK_LAYER_PATH layers are explicit, enabled by name)."""
    src = root / "etc" / "vulkan" / "implicit_layer.d" / "renderdoc_capture.json"
    m = json.loads(src.read_text(encoding="utf-8"))
    m["layer"]["library_path"] = str((root / "lib" / "librenderdoc.so").resolve())
    d = Path(work) / "vklayer"
    d.mkdir(parents=True, exist_ok=True)
    (d / "renderdoc_capture.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    return d


def game_env(root: Path, work: Path, capfile: str, log: str | None = None) -> dict:
    """The variables that load the capture layer into one process tree: added to the game's environment only."""
    e = {"VK_LAYER_PATH": str(layer_dir(root, work)), "VK_INSTANCE_LAYERS": LAYER, "VK_LOADER_LAYERS_ENABLE": LAYER,
         "ENABLE_VULKAN_RENDERDOC_CAPTURE": "1", "RENDERDOC_CAPFILE": str(capfile)}
    if log:
        e["RENDERDOC_DEBUG_LOG_FILE"] = str(log)
    return e


def ensure_home(home: Path) -> Path:
    cfg = Path(home) / ".local" / "share" / "qrenderdoc" / "UI.config"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.is_file():
        try:
            cur = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError:
            cur = {}
        if all(cur.get(k) == v for k, v in UI_CONFIG.items() if k != "Tips_HasSeenFirst"):
            return Path(home)
        cur.update(UI_CONFIG)
        cfg.write_text(json.dumps(cur, indent=4), encoding="utf-8")
    else:
        cfg.write_text(json.dumps(UI_CONFIG, indent=4), encoding="utf-8")
    return Path(home)


# ------------------------------------------------------------------ private X display for qrenderdoc (it has only xcb)

def _free_display(start=140, xdir="/tmp/.X11-unix"):
    for n in range(start, start + 60):
        if not os.path.exists(os.path.join(xdir, "X%d" % n)) and not os.path.exists("/tmp/.X%d-lock" % n):
            return n
    raise RuntimeError("no free X display number")


class Xvfb:
    def __init__(self, size="1280x720"):
        self.size, self.proc, self.display = size, None, None

    def __enter__(self):
        if not shutil.which("Xvfb"):
            raise SystemExit("Xvfb is needed for qrenderdoc's Python (it has only the xcb platform)")
        n = _free_display()
        self.display = ":%d" % n
        self.proc = subprocess.Popen(["Xvfb", self.display, "-screen", "0", self.size + "x24", "-nolisten", "tcp"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        end = time.time() + 10
        while time.time() < end and not os.path.exists("/tmp/.X11-unix/X%d" % n):
            if self.proc.poll() is not None:
                raise RuntimeError("Xvfb %s ended at start" % self.display)
            time.sleep(0.1)
        return self

    def __exit__(self, *a):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return False


def run_script(root: Path, script: Path, args: dict, home: Path, timeout: float = 900, log: Path | None = None) -> dict:
    """Run `script` in RenderDoc's Python (qrenderdoc --python) with args; returns the JSON it wrote to args["out"]."""
    root = find_root(root)
    out = Path(args["out"])
    if out.exists():
        out.unlink()
    home = ensure_home(Path(home))
    with Xvfb() as x:
        env = dict(os.environ, DISPLAY=x.display, HOME=str(home), XDG_DATA_HOME=str(home / ".local" / "share"),
                   XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
                   HDKIT_RD_ARGS=json.dumps(args), QT_QPA_PLATFORM="xcb")
        for k in ("WAYLAND_DISPLAY", "VK_INSTANCE_LAYERS", "VK_LOADER_LAYERS_ENABLE", "ENABLE_VULKAN_RENDERDOC_CAPTURE"):
            env.pop(k, None)
        fh = open(log, "a") if log else subprocess.DEVNULL
        try:
            p = subprocess.Popen([str(root / "bin" / "qrenderdoc"), "--python", str(script)], env=env, stdout=fh,
                                 stderr=subprocess.STDOUT, start_new_session=True)
            try:
                p.wait(timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        finally:
            if log:
                fh.close()
    if not out.is_file():
        return {"ok": False, "error": "the script wrote no answer (rc %s)" % p.returncode}
    return json.loads(out.read_text(encoding="utf-8"))
