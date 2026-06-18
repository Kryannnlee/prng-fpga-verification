#!/usr/bin/env python3
# ==========================================================================
# Unit test: ila_parser — robust Vivado CSV parsing
# ==========================================================================

import io, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.core.ila_parser import parse_ila_csv, _find_header_line, _is_data_row


class TestIlaParser(unittest.TestCase):
    """Verify CSV parsing handles Vivado ILA format variations."""

    def _write_csv(self, content: str) -> str:
        path = os.path.join(tempfile.gettempdir(), "_test_ila.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_basic_parse(self):
        """Standard format with radix row."""
        csv = (
            "dbg_state[4:0],ila_probe2_diag[63:0],cfg_done[0:0]\n"
            "Radix - UNSIGNED,HEX,UNSIGNED\n"
            "0,0000000000000000,0\n"
            "1,0000000000000001,0\n"
            "2,0000000000000002,1\n"
        )
        path = self._write_csv(csv)
        fields, rows = parse_ila_csv(path)
        self.assertIn("dbg_state[4:0]", fields)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["dbg_state[4:0]"].strip(), "0")

    def test_no_radix_row(self):
        """Format without radix row."""
        csv = (
            "sample,prng_out[63:0]\n"
            "0,0x0000000000000000\n"
            "1,0xC661F290C4215270\n"
        )
        path = self._write_csv(csv)
        fields, rows = parse_ila_csv(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["prng_out[63:0]"].strip(), "0xC661F290C4215270")

    def test_bracket_notation_columns(self):
        """Columns with Vivado bracket notation are preserved."""
        csv = (
            "dbg_state[4:0],dbg_heartbeat[0:0],cfg_done[0:0]\n"
            "00,0,0\n"
            "19,1,1\n"
        )
        path = self._write_csv(csv)
        fields, rows = parse_ila_csv(path)
        self.assertTrue(any("dbg_state" in f for f in fields))
        self.assertTrue(any("dbg_heartbeat" in f for f in fields))

    def test_header_detection(self):
        """_find_header_line skips metadata."""
        lines = [
            "Date: 2026-06-18",
            "",
            "sample,prng_out[63:0],state[4:0]",
            "0,0x0000,00",
        ]
        idx = _find_header_line(lines)
        self.assertEqual(idx, 2)

    def test_data_row_detection(self):
        """_is_data_row correctly identifies data rows."""
        self.assertTrue(_is_data_row("  0  ,  0x0000  ,  1"))
        self.assertTrue(_is_data_row("1234,abcd"))
        self.assertFalse(_is_data_row("Radix - HEX,HEX"))
        self.assertFalse(_is_data_row(""))
        self.assertFalse(_is_data_row("column1,column2"))

    def test_empty_file_raises(self):
        """Empty CSV raises ValueError."""
        path = self._write_csv("")
        with self.assertRaises(ValueError):
            parse_ila_csv(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
