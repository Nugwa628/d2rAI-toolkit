"""Tests for plugin-template/tools/item_type_ids.py (run: python -m unittest discover tests)."""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin-template" / "tools"))

import item_type_ids as iti  # noqa: E402


def itemtypes(codes: list[str], expansion_after: int | None = None) -> str:
    """A minimal itemtypes.txt: one row per code (ids 0..n-1), optionally an Expansion row after row N."""
    lines = ["ItemType\tCode\tEquiv1"]
    for i, code in enumerate(codes):
        lines.append(f"Type {i}\t{code}\t")
        if expansion_after is not None and i == expansion_after:
            lines.append("Expansion\t\t")
    return "\n".join(lines) + "\n"


def vanilla_codes() -> list[str]:
    """Codes laid out so every checked code sits at D2R's id; the rest are fillers."""
    by_id = {v: k for k, v in iti.D2R_IDS.items()}
    return [by_id.get(i, f"x{i:03d}") for i in range(max(by_id) + 1)]


class ReadIds(unittest.TestCase):
    def test_rows_count_from_zero(self) -> None:
        self.assertEqual(iti.read_ids(itemtypes(["aaaa", "bbbb", "cccc"])), {"aaaa": 0, "bbbb": 1, "cccc": 2})

    def test_expansion_row_not_counted(self) -> None:
        ids = iti.read_ids(itemtypes(["aaaa", "bbbb", "cccc"], expansion_after=0))
        self.assertEqual(ids["bbbb"], 1)
        self.assertEqual(ids["cccc"], 2)

    def test_blank_lines_and_crlf_skipped(self) -> None:
        text = itemtypes(["aaaa", "bbbb"]).replace("\n", "\r\n") + "\r\n\t\t\r\n"
        self.assertEqual(iti.read_ids(text), {"aaaa": 0, "bbbb": 1})

    def test_first_row_wins_for_a_repeated_code(self) -> None:
        self.assertEqual(iti.read_ids(itemtypes(["aaaa", "aaaa"]))["aaaa"], 0)

    def test_missing_code_column(self) -> None:
        with self.assertRaises(ValueError):
            iti.read_ids("ItemType\tEquiv1\nx\ty\n")

    def test_empty_file(self) -> None:
        with self.assertRaises(ValueError):
            iti.read_ids("")


class Check(unittest.TestCase):
    def test_vanilla_layout_ok(self) -> None:
        self.assertEqual(iti.check(iti.read_ids(itemtypes(vanilla_codes()))), [])

    def test_row_removed_before_67_shifts_later_ids(self) -> None:
        codes = vanilla_codes()
        del codes[67]  # a classic table without D2R's row 67
        problems = iti.check(iti.read_ids(itemtypes(codes)))
        self.assertIn("rune: id 74, D2R expects 75", problems)
        self.assertFalse(any(p.startswith("jewl") for p in problems))  # 58 is before the gap

    def test_missing_code_reported(self) -> None:
        codes = vanilla_codes()
        codes[96] = "zzzz"
        self.assertEqual(iti.check(iti.read_ids(itemtypes(codes))), ["gem4: missing (D2R expects id 96)"])


class Header(unittest.TestCase):
    def test_header_lists_every_code(self) -> None:
        text = iti.header({"rune": 75})
        self.assertIn('{"rune", 75}', text)
        self.assertIn('{"gem4", -1}', text)
        self.assertIn("#pragma once", text)
        for code in iti.D2R_IDS:
            self.assertIn(f'"{code}"', text)


class Main(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = iti.main(argv)
        return code, out.getvalue()

    def test_usage(self) -> None:
        self.assertEqual(self.run_main(["item_type_ids.py"])[0], 2)

    def test_ok_and_header_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "itemtypes.txt"
            src.write_text(itemtypes(vanilla_codes()), encoding="latin-1")
            out = Path(tmp) / "item_type_ids.h"
            code, text = self.run_main(["item_type_ids.py", str(src), str(out)])
            self.assertEqual(code, 0)
            self.assertIn("item type ids ok: 10 of 10", text)
            self.assertIn('{"rune", 75}', out.read_text(encoding="utf-8"))

    def test_mismatch_exits_1(self) -> None:
        codes = vanilla_codes()
        del codes[67]
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "itemtypes.txt"
            src.write_text(itemtypes(codes), encoding="latin-1")
            code, text = self.run_main(["item_type_ids.py", str(src)])
            self.assertEqual(code, 1)
            self.assertIn("rune: id 74", text)


if __name__ == "__main__":
    unittest.main()
