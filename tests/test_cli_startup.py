import subprocess
import sys
import unittest


class CliStartupTests(unittest.TestCase):
    def test_help_starts_without_game_data(self):
        result = subprocess.run(
            [sys.executable, '-m', 'modkit', '--help'],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('fetch', result.stdout)


if __name__ == '__main__':
    unittest.main()
