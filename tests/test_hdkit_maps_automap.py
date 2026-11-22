"""Tests of hdkit.maps.automap_render, automap_rows, automap_art and automap_void: synthetic tables and files. The Real
cases need D2R's extract (hdkit.toml [mod] d2r_extract, or modkit's d2r-data) and are skipped without it; the stock check
over a mod's maps also needs the mod's classic files ([mod] classic).

  python -m unittest tests.test_hdkit_maps_automap
"""
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np

if np is not None:
    from hdkit.maps import automap_art, automap_rows, automap_void, legacy
    from hdkit.maps import automap_render as ar
    from hdkit.view.mapview import dt1

HEAD = "LevelName\tTileName\tStyle\tStartSequence\tEndSequence\t*Type1\tCel1\t*Type2\tCel2\t*Type3\tCel3\t*Type4\tCel4"


def keys_of(tiles):
    return [(t.orientation, t.main, t.sub) for t in tiles]


def write_automap(path, rows):
    path.write_text(HEAD + "\n" + "".join("%s\t%s\t%d\t-1\t-1\tx\t%s\n" % (lt, code, st, "\t\t".join(
        str(c) for c in (cels + [-1] * 4)[:4])) for lt, code, st, cels in rows), encoding="utf-8")


def _d2r_automap():
    if np is None:
        return None
    try:
        p = ar.d2r_data() / "global" / "excel" / "automap.txt"
    except Exception:          # no extract configured
        return None
    return p if p.is_file() else None


@unittest.skipIf(np is None, "needs numpy")
class Lookup(unittest.TestCase):
    def test_exact_sequence_first_then_any(self):
        rows = [("wr", 0, -1, -1, [240]), ("wr", 0, 2, 2, [253]), ("wl", -1, -1, -1, [241])]
        self.assertEqual(ar.lookup(rows, "wr", 0, 2), [253])
        self.assertEqual(ar.lookup(rows, "wr", 0, 5), [240])
        self.assertEqual(ar.lookup(rows, "wl", 31, 0), [241])         # style -1: any style
        self.assertIsNone(ar.lookup(rows, "wr", 31, 0))

    def test_level_type_name(self):
        self.assertEqual(ar.level_type_name("Act 2 - Tomb"), "2 Tomb")
        self.assertEqual(ar.level_type_name("Act 5 - Ice Caves"), "5 Ice")
        self.assertEqual(ar.level_type_name("Act 5 - Lava"), "5 Lava")
        self.assertEqual(automap_rows.act_of("5 Ice"), 5)

    def test_d2r_override(self):
        with tempfile.TemporaryDirectory() as d:
            ar.set_d2r(d)
            try:
                self.assertEqual(ar.d2r_data(), Path(d) / "data")
            finally:
                ar._D2R.clear()


@unittest.skipIf(np is None, "needs numpy")
class Write(unittest.TestCase):
    def test_appends_after_base_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            base, out = Path(d) / "base.txt", Path(d) / "out.txt"
            base.write_bytes((HEAD + "\r\n2 Tomb\twr\t0\t-1\t-1\tTmb_WR A\t240\t\t-1\t\t-1\t\t-1\r\n").encode())
            automap_rows.write(base, [("2 Tomb", "wl", 31, 0, 241)], out)
            text = out.read_bytes().decode()
            self.assertTrue(text.startswith(base.read_bytes().decode()))
            last = text.rstrip("\r\n").split("\r\n")[-1].split("\t")
            self.assertEqual(last[:7], ["2 Tomb", "wl", "31", "0", "0", "mod wl", "241"])
            self.assertEqual(len(last), len(HEAD.split("\t")))
            rows = ar.read_rows(out, "2 Tomb")
            self.assertEqual(ar.lookup(rows, "wl", 31, 0), [241])


@unittest.skipIf(np is None, "needs numpy")
class Templates(unittest.TestCase):
    def test_most_used_ties_siblings_generic(self):
        full = [(automap_rows.GENERIC, c, 0, [100 + i]) for i, c in enumerate(automap_rows.WALL_CODES)]
        rows = full + [
            ("2 Tomb", "wl", 0, [241]), ("2 Tomb", "wl", 1, [241, 252]), ("2 Tomb", "wl", 2, [252]),   # 241 x2
            ("2 Tomb", "wld", 0, [247]), ("2 Tomb", "wld", 1, [241]),                                  # tie: first
            *[("2 Tomb", c, 0, [200 + i]) for i, c in enumerate(["wr", "wtlr", "wtr", "wbl", "wbr"])],  # no wtll, co
            ("5 Lava", "wl", 0, [1483])]                                                               # core missing
        with tempfile.TemporaryDirectory() as d:
            write_automap(Path(d) / "a.txt", rows)
            t = automap_rows.templates(Path(d) / "a.txt")
        self.assertEqual(t["2 Tomb"]["wl"], 241)
        self.assertEqual(t["2 Tomb"]["wld"], 247)
        self.assertEqual(t["2 Tomb"]["wtll"], t["2 Tomb"]["wtlr"])        # sibling
        self.assertEqual(t["2 Tomb"]["co"], t["2 Tomb"]["wbr"])
        self.assertEqual(t["2 Tomb"]["wre"], t["2 Tomb"]["wr"])           # wre -> wrd (none) -> wr
        self.assertEqual(t["5 Lava"], t[automap_rows.GENERIC])            # whole generic set
        self.assertEqual(set(t["2 Tomb"]), set(automap_rows.WALL_CODES))

    def test_bright_cels(self):
        tpl = {"2 Tomb": dict(zip(automap_rows.WALL_CODES, range(240, 252))),
               "9 Dim": {c: (20 if c in ("wtll", "wtlr") else 30 + i) for i, c in enumerate(automap_rows.WALL_CODES)}}
        free = [5, 6, 1100] + list(range(1176, 1200))
        pins = {"2 Tomb": {c: 1176 + i for i, c in enumerate(automap_rows.BRIGHT_ORDER)}}
        old = dict(automap_rows.PINNED)
        automap_rows.PINNED.clear()
        automap_rows.PINNED.update(pins)
        try:
            b = automap_rows.bright_cels(["9 Dim", "2 Tomb"], tpl, free)
        finally:
            automap_rows.PINNED.clear()
            automap_rows.PINNED.update(old)
        self.assertEqual(b["2 Tomb"], pins["2 Tomb"])                         # pinned first, whatever the order
        self.assertEqual(b["9 Dim"]["wtll"], b["9 Dim"]["wtlr"])              # one cel per template cel
        self.assertEqual(set(b["9 Dim"]), set(automap_rows.BRIGHT_ORDER))
        self.assertLessEqual(len(set(b["9 Dim"].values())), automap_rows.BRIGHT_MAX)
        self.assertFalse(set(b["9 Dim"].values()) & set(b["2 Tomb"].values()))
        self.assertTrue(all(c >= 1184 for c in b["9 Dim"].values()))         # from FIRST_BRIGHT up, past the pinned
        with self.assertRaises(SystemExit):
            automap_rows.bright_cels(["9 Dim"], tpl, [5, 6])

    def test_no_pins_by_default(self):
        self.assertEqual(automap_rows.PINNED, {})

    def test_bright_cels_shared_per_act_and_gain(self):
        t = dict(zip(automap_rows.WALL_CODES, range(20, 32)))
        tpl = {"1 Crypt": t, "1 Cathedral": t, "3 Kurast": t}
        free = list(range(1176, 1240))
        b = automap_rows.bright_cels(["1 Crypt", "1 Cathedral", "3 Kurast"], tpl, free,
                                     {"1 Crypt": 1.6, "1 Cathedral": 1.6, "3 Kurast": 1.6})
        self.assertEqual(b["1 Crypt"], b["1 Cathedral"])             # same act, template and gain
        self.assertFalse(set(b["1 Crypt"].values()) & set(b["3 Kurast"].values()))   # another act's palette
        b2 = automap_rows.bright_cels(["1 Crypt", "1 Cathedral"], tpl, free, {"1 Crypt": 1.6, "1 Cathedral": 2.0})
        self.assertFalse(set(b2["1 Crypt"].values()) & set(b2["1 Cathedral"].values()))

    def test_gain(self):
        self.assertEqual(automap_rows.gain_of(0.207), automap_rows.GAIN_MAX)   # capped
        self.assertEqual(automap_rows.gain_of(0.504), 1.6)
        self.assertEqual(automap_rows.gain_of(0.9), 1.0)

    def test_prest_type(self):
        types = ["None", "Act 2 - Desert", "Act 2 - Tomb", "Act 5 - Ice Caves", "Act 5 - Lava"]
        self.assertEqual(automap_rows.prest_type("Act 2 - Tomb NSEW", types), "Act 2 - Tomb")
        self.assertEqual(automap_rows.prest_type("Act 5 - Ice Prev W", types), "Act 5 - Ice Caves")
        self.assertEqual(automap_rows.prest_type("Act 2 - Desert Tomb 1", types), "Act 2 - Desert")
        self.assertIsNone(automap_rows.prest_type("Act 4 - Diablo Wing", types))


D2R_AM = _d2r_automap()


@unittest.skipUnless(D2R_AM, "needs D2R's extract")
class Real(unittest.TestCase):
    """On the game's own tables: the derived templates and the free cels."""

    def test_tomb_template(self):
        self.assertEqual(automap_rows.templates(D2R_AM)["2 Tomb"], {
            "wr": 240, "wl": 241, "wbl": 242, "wtr": 243, "wtll": 244, "wtlr": 244, "wbr": 245, "wrd": 246, "wld": 247,
            "wre": 248, "wle": 249, "co": 250})

    def test_free_cels(self):
        free = automap_rows.free_cels(ar.d2r_data())
        self.assertGreaterEqual(len(free), 200)
        stock = {c for r in automap_rows.automap_rows_all(D2R_AM) for c in r[3]}
        self.assertFalse(set(free) & stock)

    def test_rows_leave_stock_levels(self):
        try:
            maps = automap_rows.map_list()
        except Exception as e:                                   # no mod classic files configured
            self.skipTest("needs the mod's classic files: %s" % e)
        if not maps:
            self.skipTest("the mod's classic files hold no maps")
        rows, plan = automap_rows.new_rows(maps, D2R_AM)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "automap.txt"
            automap_rows.write(D2R_AM, rows, out)
            problems, _nmaps, _n = automap_rows.check_stock(D2R_AM, out, list(plan["report"]))
        self.assertEqual(problems, [])
        for lt, code, st, sq, _cel in rows:                      # mod keys only, never a stock tile set's
            self.assertNotIn((code, st, sq), automap_rows.stock_keys(plan["types"][lt]["level_type"]))


@unittest.skipIf(np is None, "needs numpy")
class Art(unittest.TestCase):
    PLAN = {"types": {"2 Tomb": {"act": 2, "template": {"wr": 240, "wl": 241, "co": 250},
                                 "bright": {"wr": 1176, "wl": 1177, "co": 1182}}}}

    def test_bright_of(self):
        self.assertEqual(automap_art.bright_of(self.PLAN)["2 Tomb"]["wl"], (1177, 241))

    def test_check_flags_used_and_shared(self):
        with tempfile.TemporaryDirectory() as d:
            ex = Path(d) / "global" / "excel"
            ex.mkdir(parents=True)
            write_automap(ex / "automap.txt", [("1 Town", "wl", 0, [1177])])
            (ex / "objects.txt").write_text("Class\tAutoMap\nx\t0\n", encoding="latin-1")
            plan = {"types": dict(self.PLAN["types"])}
            plan["types"]["9 X"] = {"act": 2, "template": {"wr": 240}, "bright": {"wr": 1176}, "gain": 1.6}
            probs = automap_art.check(Path(d), plan)
        self.assertTrue(any("1177 is used" in p for p in probs))
        self.assertTrue(any("two different templates" in p for p in probs))

    def test_brighten_keeps_shape(self):
        pal = [(i, i // 4, i // 8) for i in range(256)]
        idx = np.array([[0, 40, 0], [60, 0, 60]], np.uint8)
        out = automap_art.brighten(idx, pal, 2.0)
        self.assertTrue(((out > 0) == (idx > 0)).all())
        self.assertEqual(int(out[0, 1]), 80)
        self.assertTrue(all(int(v) in automap_art.PAL_RANGE for v in out[out > 0]))

    def test_patch_dc6_one_frame(self):
        enc = legacy.encode_frame
        frames = [(2, 2, 0, 0, enc(np.array([[i + 1, 0], [0, i + 1]], np.uint8))) for i in range(3)]
        data = legacy.build_dc6(frames)
        new = (2, 2, 0, 0, enc(np.array([[9, 9], [9, 9]], np.uint8)))
        self.assertEqual(legacy.parse_dc6(automap_art.patch_dc6(data, {1: new})), [frames[0], new, frames[2]])
        self.assertEqual(automap_art.patch_dc6(data, {}), data)
        self.assertEqual(automap_art._indices(2, 2, new[4]).tolist(), [[9, 9], [9, 9]])

    def test_decode_palette(self):
        pal = np.stack([np.arange(256)] * 3, 1).astype(np.uint8)
        im = legacy.decode(2, 1, legacy.encode_frame(np.array([[0, 7]], np.uint8)), pal)
        self.assertEqual(im[0, 1].tolist(), [7, 7, 7, 255])
        self.assertEqual(int(im[0, 0, 3]), 0)

    def test_patch_sprite_frame_only(self):
        fw, n, h = 4, 3, 2
        sw = (fw + 2) * n
        H = legacy.SPRITE_HEADER
        strip = (np.arange(sw * h * 4) % 251).astype(np.uint8).reshape(h, sw, 4)
        data = legacy.sprite_bytes(fw, sw, h, n, strip.tobytes())
        out = automap_art.patch_sprite(data, {1: np.full((h, fw, 4), 7, np.uint8)})
        self.assertEqual(len(out), len(data))
        self.assertTrue((automap_art.sprite_frame(out, 1) == 7).all())
        a = np.frombuffer(data, np.uint8, sw * h * 4, H).reshape(h, sw, 4)
        b = np.frombuffer(out, np.uint8, sw * h * 4, H).reshape(h, sw, 4)
        keep = np.ones(sw, bool)
        keep[fw + 2:fw + 2 + fw] = False
        self.assertTrue((a[:, keep] == b[:, keep]).all())
        self.assertEqual(out[:H], data[:H])


@unittest.skipIf(np is None, "needs numpy")
class Void(unittest.TestCase):
    def test_add_tile_copies_one(self):
        data = dt1.synthetic([
            {"orientation": 4, "main": 31, "sub": 0, "height": -96, "blocks": [(0, 0, 1, b"AAAA")]},
            {"orientation": 0, "main": 4, "sub": 18, "flags": bytes(range(25)), "blocks": [(0, 0, 1, b"BBBBBB")]}])
        out = automap_void.add_tile(data, (4, 31, 0), 63)
        tiles, old = dt1.parse(out), dt1.parse(data)
        self.assertEqual(keys_of(tiles), [(4, 31, 0), (0, 4, 18), (4, 31, 63)])
        self.assertEqual([t.blocks for t in tiles[:2]], [t.blocks for t in old])
        self.assertEqual(tiles[2].blocks, old[0].blocks)
        self.assertEqual((tiles[2].height, tiles[2].flags), (old[0].height, old[0].flags))
        twice = automap_void.add_tile(out, (0, 4, 18), 62)
        t2 = dt1.parse(twice)
        self.assertEqual(keys_of(t2)[-1], (0, 4, 62))
        self.assertEqual([t.blocks[0][3] for t in t2], [b"AAAA", b"BBBBBB", b"AAAA", b"BBBBBB"])
        self.assertEqual(t2[3].flags, bytes(range(25)))

    def test_add_tile_unknown_key(self):
        data = dt1.synthetic([{"orientation": 4, "main": 31, "sub": 0, "blocks": [(0, 0, 1, b"A")]}])
        with self.assertRaises(ValueError):
            automap_void.add_tile(data, (4, 30, 0), 63)

    def test_find_walls_far_from_walkable_floor(self):
        w, h = 12, 3
        floor = np.zeros((h, w), np.uint32)
        floor[:, :2] = (1 << 20) | (1 << 8) | 1       # style 1 seq 1: walkable
        floor[:, 2:] = (2 << 20) | (1 << 8) | 1       # style 2 seq 1: blocked everywhere
        wall = np.zeros((h, w), np.uint32)
        wall[1, :] = (31 << 20) | 1
        orient = np.full((h, w), 2, np.int32)         # wr
        ds = SimpleNamespace(width=w, height=h, floors=lambda: [floor], walls=lambda: [wall],
                             orientation=lambda j: orient)
        orig = automap_void._walkable
        automap_void._walkable = lambda paths: {(1, 1): True, (2, 1): False}
        try:
            hits = automap_void.find(ds, [])
            far = automap_void.find(ds, [], 6)                  # a wider reach keeps more walls
        finally:
            automap_void._walkable = orig
        self.assertEqual(sorted(x for x, *_ in hits), list(range(2 + automap_void.REACH, w)))
        self.assertEqual(sorted(x for x, *_ in far), list(range(2 + 6, w)))

    def test_walkable_from_flags(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.dt1"
            p.write_bytes(dt1.synthetic([
                {"orientation": 0, "main": 1, "sub": 1, "flags": b"\x01" * 24 + b"\x00"},   # one walkable sub-tile
                {"orientation": 0, "main": 2, "sub": 1, "flags": b"\x01" * 25}]))
            self.assertEqual(automap_void._walkable([p]), {(1, 1): True, (2, 1): False})

    def test_reach_map(self):
        self.assertEqual(automap_void.REACH_MAP, {})
        with tempfile.TemporaryDirectory() as d:
            j = Path(d) / "r.json"
            j.write_text('{"Act1/X/Y": 5}', encoding="utf-8")
            self.assertEqual(automap_void.parse_reach_map(["a/b/c=6", str(j)]), {"a/b/c": 6, "act1/x/y": 5})


class Cli(unittest.TestCase):
    def test_commands_resolve(self):
        import importlib
        from hdkit.maps import __main__ as m
        for mod in m.COMMANDS.values():
            if mod != "overlay" and np is None:
                continue
            self.assertTrue(hasattr(importlib.import_module("hdkit.maps." + mod), "main"))
        self.assertEqual(m.main([]), 2)


if __name__ == "__main__":
    unittest.main()
