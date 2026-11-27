"""Tests for the capture water comparison and scene rig selection."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import numpy, PIL, scipy  # noqa: E401,F401  (hdkit's own dependencies: pip install -r hdkit/requirements.txt)
except ImportError as _e:
    raise unittest.SkipTest("hdkit needs numpy, Pillow and scipy (hdkit/requirements.txt): %s" % _e)
import numpy as np
from PIL import Image

from hdkit.fx import lighting
from hdkit.view import fxcli
from test_hdfx_lighting import FakeFS, vis_doc


class SceneEvent(unittest.TestCase):
    def test_den_event_reads_vis_and_keeps_the_cave_cube(self):
        quat = {"x": -0.212607518, "y": 0.768177748, "z": -0.318189651, "w": -0.51328}
        base = "data/hd/env/vis/act1_caves_day.json"
        den = "data/hd/env/vis/denofevil_vis.json"
        cube = "data/hd/env/ibl/act1_caves_CUBEMAP.ibls"
        fs = FakeFS({
            "data/hd/env/biome/act1_caves.json": {
                "visualDataFilenames": [{"visualDataFilename": base}], "iblSettings": {"cubemapFilename": cube}},
            "data/hd/env/weather/vis_event_map.json": {"DenOfEvil": {"definition": den}},
            base: vis_doc((1, 1, 1), 0, quat, ibl_i=2.0),
            den: vis_doc((1, 0, 0), 4, quat, ibl_i=1.0),
        })
        scene = lighting.resolve_rig("scene:act1_caves;tod=1.54;clut=lut", fs).lighting
        event = lighting.resolve_rig("scene:act1_caves;tod=1.54;clut=lut;event=DenOfEvil", fs).lighting
        self.assertEqual((scene.ibl, event.ibl), (2.0, 1.0))
        self.assertEqual(event.sources["event"], "DenOfEvil")
        self.assertEqual(event.dirs(lighting.TERRAIN_MASK)[0]["color"], [4, 0, 0])
        np.testing.assert_array_equal(event.sh, scene.sh)


