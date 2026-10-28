"""Example checks. Copy this file, rename it, edit the checks, and list your module in checks.modules.

Each check gets `ctx` (see game_checks.Context): ctx.cfg, ctx.slot, ctx.game, ctx.shot(name), ctx.ensure_running(),
ctx.skip(why), ctx.require_harness(), ctx.log(msg). Return a short text, or nothing, to pass. Use `assert` (with a
message) or ctx.fail(why) to fail.
"""
import os
import time

from game_checks import check
import game_ctl


@check("config-sane", tags=["offline", "smoke"])
def config_sane(ctx):
    """The slot folder, the loader, the mod folder and the plugin files are where the config says."""
    cfg, n = ctx.cfg, ctx.slot
    assert os.path.isdir(cfg.slot_dir(n)), f"slot folder missing: {cfg.slot_dir(n)} (game_slots.py setup {n})"
    assert os.path.isfile(cfg.base_loader_path(n)), f"loader missing in {cfg.slot_dir(n)}"
    assert os.path.isdir(cfg.mod_dir(n)), f"mod folder missing: {cfg.mod_dir(n)} (install the mod into the slot)"
    for dll in cfg["mod"]["plugin_dlls"]:
        assert os.path.isfile(os.path.join(cfg.plugins_dir(n), dll)), f"plugin missing: {dll}"
    return f"slot {n} looks complete"


@check("game-starts", tags=["smoke", "game"])
def game_starts(ctx):
    """The game launches in the slot and gets a window."""
    ctx.ensure_running()
    assert ctx.game.pids(), "no game process in the slot"
    assert ctx.game.window(), "the game has no window"
    ctx.shot("started")
    return f"pids {ctx.game.pids()}"


@check("main-menu-visible", tags=["smoke", "game"])
def main_menu_visible(ctx):
    """The window shows a real picture (not black, not flat) within a minute.

    This is the generic version. To recognise YOUR menu, take a screenshot of it once, store
    img.signature(img.box(x0, y0, x1, y1)) of a fixed part of the screen, and compare with
    game_ctl.signature_distance(...) < a small number."""
    ctx.ensure_running()
    lo_mean = float(ctx.cfg["checks"]["menu_min_mean"])
    lo_dev = float(ctx.cfg["checks"]["menu_min_stddev"])
    seen = {}

    def looks_alive():
        img = ctx.game.screenshot()
        seen["stats"] = img.stats()
        seen["img"] = img
        return seen["stats"][0] >= lo_mean and seen["stats"][1] >= lo_dev

    try:
        game_ctl.wait_for(looks_alive, 60, 2.0, "a non-blank game screen")
    except game_ctl.WaitTimeout:
        ctx.shot("blank")
        raise AssertionError(f"the screen stayed blank (mean {seen['stats'][0]:.1f}, deviation {seen['stats'][1]:.1f})")
    ctx.shot("menu")
    return "mean {:.1f}, deviation {:.1f}".format(*seen["stats"])


@check("console-echo", tags=["harness"])
def console_echo(ctx):
    """A console line sent through the plugin harness is answered by the plugin."""
    ctx.ensure_running()
    ctx.require_harness()
    line = ctx.cfg["checks"]["echo_command"]
    mark = ctx.game.console_mark()
    assert ctx.game.console(line, timeout=15), f"the console did not handle {line!r}"
    time.sleep(0.5)
    return (ctx.game.console_new(mark).strip().splitlines() or ["handled, no output captured"])[0][:120]


@check("no-new-crash-files", tags=["smoke"])
def no_new_crash_files(ctx):
    """The loader wrote no crash file since the run began."""
    new = ctx.game.new_crash_files(ctx.shared.get("crash_before", []))
    assert not new, f"new crash files: {new}"
    return "no crash files"
