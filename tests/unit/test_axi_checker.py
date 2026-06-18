#!/usr/bin/env python3
# ==========================================================================
# Unit test: axi_checker — AXI write transaction verification
# ==========================================================================

import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.core.axi_checker import check_axi_writes, AxiWrite, AxiReport


def _make_row(state: str, probe2: str) -> dict:
    return {"dbg_state[4:0]": state, "ila_probe2_diag[63:0]": probe2}


class TestAxiChecker(unittest.TestCase):
    """Verify AXI-Lite write decoding and matching."""

    def _build_rows(self, writes: list) -> list:
        """Build CSV rows from (state, awaddr, wdata) tuples.
        States are hex-formatted to match real Vivado ILA CSV output.
        """
        rows = []
        for state, awaddr, wdata in writes:
            probe2_val = (wdata << 32) | (awaddr << 26)
            rows.append(_make_row(f"{state:02x}", f"0x{probe2_val:016x}"))
        return rows

    def test_all_correct_writes(self):
        """Expected write sequence should PASS."""
        # States are hex (as in real Vivado ILA CSV)
        writes = [
            (0x01, 0x10, 0x00000001),
            (0x05, 0x18, 0x000003E8),
            (0x0a, 0x20, 0x000007D0),
            (0x0f, 0x28, 0x00000BB8),
            (0x14, 0x30, 0x00000FA0),
            (0x17, 0x00, 0x00000081),
        ]
        rows = self._build_rows(writes)
        report = check_axi_writes(rows, list(rows[0].keys()))
        self.assertTrue(report.all_match, f"Expected PASS, got: {report.errors}")

    def test_wrong_wdata_detected(self):
        """Mismatched write data is flagged."""
        writes = [
            (0x01, 0x10, 0x00000001),
            (0x05, 0x18, 0xDEADBEEF),  # WRONG
            (0x0a, 0x20, 0x000007D0),
            (0x0f, 0x28, 0x00000BB8),
            (0x14, 0x30, 0x00000FA0),
            (0x17, 0x00, 0x00000081),
        ]
        rows = self._build_rows(writes)
        report = check_axi_writes(rows, list(rows[0].keys()))
        self.assertFalse(report.all_match)
        self.assertGreater(len(report.errors), 0)

    def test_missing_writes_detected(self):
        """Too few writes are flagged."""
        writes = [
            (0x01, 0x10, 0x00000001),
            (0x05, 0x18, 0x000003E8),
        ]
        rows = self._build_rows(writes)
        report = check_axi_writes(rows, list(rows[0].keys()))
        self.assertFalse(report.all_match)
        self.assertTrue(any("count" in e.lower() for e in report.errors),
                        f"Should report count mismatch: {report.errors}")

    def test_summary_format(self):
        """Summary output uses PASS/FAIL text, not emoji."""
        report = AxiReport(writes=[], all_match=True)
        summary = report.summary()
        self.assertIn("PASS", summary)
        self.assertNotIn("✓", summary)  # no checkmark
        self.assertNotIn("✗", summary)  # no cross

        report2 = AxiReport(writes=[], all_match=False, errors=["test error"])
        summary2 = report2.summary()
        self.assertIn("FAIL", summary2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
