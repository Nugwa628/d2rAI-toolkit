"""Tests for hdkit.monster3d.common (the monster config, settings) and s0_base's helpers (no game files needed)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hdkit.monster3d import common  # noqa: E402


class MonsterConfigTests(unittest.TestCase):
    def test_defaults_and_derived_names(self):
        m = common.load_monster(data={"name": "ghoul", "donor": "zombie1"})
        self.assertEqual(m["unit"], "m3d_ghoul")
        self.assertEqual(m["folder"], "hd/character/enemy/m3d/ghoul")
        self.assertEqual(m["vfx_particles"], "hd/vfx/particles/m3d/ghoul")
        self.assertEqual(m["profile"], "zombie1")
        self.assertEqual(m["clipfit"], {})

    def test_overrides_and_templates(self):
        m = common.load_monster(data={"name": "ghoul", "donor": "zombie1", "unit_prefix": "my_",
                                      "folder": "hd/character/enemy/mymod/{unit}", "clipfit": {"el": 30}})
        self.assertEqual(m["unit"], "my_ghoul")
        self.assertEqual(m["folder"], "hd/character/enemy/mymod/my_ghoul")
        self.assertEqual(m["clipfit"], {"el": 30})

    def test_name_from_token(self):
        self.assertEqual(common.load_monster(data={"token": "~k", "donor": "x"})["name"], "7ek")

    def test_file_and_need(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.json"
            f.write_text(json.dumps({"name": "ghoul", "donor": "zombie1"}))
            m = common.load_monster(f)
            self.assertEqual(m["_source"], str(f))
        with mock.patch.object(common, "MONSTER", common.load_monster(data={})):
            with self.assertRaises(SystemExit):
                common.need_monster()

    def test_monster_arg_taken_out_of_argv(self):
        with mock.patch.object(sys, "argv", ["x", "a", "--monster", "m.json", "b"]):
            self.assertEqual(common._monster_arg(), "m.json")
            self.assertEqual(sys.argv, ["x", "a", "b"])
        with mock.patch.object(sys, "argv", ["x"]), mock.patch.dict(os.environ, {"HDKIT_M3D_MONSTER": "e.json"}):
            self.assertEqual(common._monster_arg(), "e.json")

    def test_setting_order(self):
        with mock.patch.dict(os.environ, {"HDKIT_M3D_TEST": "from-env"}):
            self.assertEqual(common.setting("HDKIT_M3D_TEST", "nokey", "d"), "from-env")
        with mock.patch.object(common, "_site_cfg", return_value={}):
            self.assertEqual(common.setting("HDKIT_M3D_UNSET_X", "nokey", "d"), "d")

    def test_child_env_puts_repo_first(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": "other"}):
            env = common.child_env()
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep), [str(common.REPO), "other"])

    def test_defaults_name_no_unit(self):
        self.assertEqual(common.MONSTER_DEFAULTS["name"], "")
        self.assertEqual(common.MONSTER_DEFAULTS["unit_prefix"], "m3d_")


class BaseStageHelpers(unittest.TestCase):
    def test_donor_file_case_insensitive(self):
        try:
            from hdkit.monster3d import s0_base
        except ImportError as e:  # numpy missing
            self.skipTest(str(e))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "hd" / "character" / "enemy" / "x" / "textures" / "mummy_alb.texture"
            f.parent.mkdir(parents=True)
            f.write_bytes(b"t")
            with mock.patch.object(common, "D2R", Path(d)):
                self.assertEqual(s0_base.donor_file("data/hd/character/enemy/x/textures/Mummy_ALB.texture"), f)
                with self.assertRaises(FileNotFoundError):
                    s0_base.donor_file("data/hd/character/enemy/x/textures/none.texture")


if __name__ == "__main__":
    unittest.main()
