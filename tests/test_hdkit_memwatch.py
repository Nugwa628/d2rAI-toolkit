"""hdkit.memwatch: a command that grows past the limit is stopped with exit code 137, a small one passes. Linux only (it
reads /proc); no game files, no GPU."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hdkit import memwatch  # noqa: E402


@unittest.skipUnless(sys.platform.startswith("linux"), "memwatch reads /proc (Linux only)")
class MemWatch(unittest.TestCase):
    def test_stops_a_growing_command_and_passes_a_small_one(self):
        grow = [sys.executable, "-c", "import time\na = []\nfor i in range(60):\n    a.append(bytearray(20 << 20))\n"
                                      "    time.sleep(0.05)\n"]
        self.assertEqual(memwatch.run(grow, 200 << 20, 0.1, log=lambda s: None), 137)
        # Keep the monitor for the small command independent of the test process (a busy parent once read too high).
        result = subprocess.run([sys.executable, memwatch.__file__, "--gb", "0.2", "--every", "0.1",
                                 "--", sys.executable, "-I", "-S", "-c", "pass"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
