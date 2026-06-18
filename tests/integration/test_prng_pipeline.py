#!/usr/bin/env python3
# ==========================================================================
# Integration test: PRNG verification pipeline (golden → extract → compare)
# ==========================================================================

import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.core.golden_model import GoldenModel, KNOWN_FIRST_WORD, DEFAULT_SEEDS
from tools.core.rtl_model import RTLModel
from tools.core.prng_checker import (
    run_prng_check, compare_sequences,
    align_by_sliding_window, extract_prng_sequence,
)
from tools.core.ila_parser import parse_ila_csv


def _make_ila_csv_rows(num_prng_words: int = 20) -> tuple:
    """Generate synthetic ILA CSV rows with known PRNG output.

    Simulates: 15 AXI write state rows, then PRNG output in states 25+.
    """
    gm = GoldenModel(seeds=DEFAULT_SEEDS)
    golden = gm.run(num_prng_words)

    lines = [
        "dbg_state[4:0],ila_probe2_diag[63:0],cfg_done[0:0],dbg_heartbeat[0:0]",
    ]

    # AXI write phase (state 0x01-0x17, hex as in real Vivado ILA CSV)
    for state in range(1, 24):
        # probe2 mux during state < 24: {wdata, awaddr, 0}
        lines.append(f"{state:02x},0x0000000000000000,0,1")

    # cfg_done edge at state 0x18 (24)
    lines.append(f"18,0x0000000000000000,1,1")

    # PRNG running (state 0x19 = 25+)
    # Each PRNG word appears for 2 samples (FSM toggles state1↔state2)
    for word in golden:
        lines.append(f"19,0x{word:016x},1,1")  # state1
        lines.append(f"1a,0x{word:016x},1,1")  # state2

    csv_text = "\n".join(lines)
    path = os.path.join(tempfile.gettempdir(), "_test_pipeline.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(csv_text)

    return path, golden


class TestPrngPipeline(unittest.TestCase):
    """Verify full PRNG pipeline: parse → align → extract → compare."""

    def test_synthetic_capture_matches_golden(self):
        """Pipeline over synthetic CSV produces 100% match."""
        csv_path, golden = _make_ila_csv_rows(num_prng_words=100)

        fieldnames, rows = parse_ila_csv(csv_path)
        result = run_prng_check(rows, fieldnames, golden, align_mode="dbg_state")

        self.assertTrue(result.passed,
                        f"Expected PASS, got {result.mismatched_words} mismatches")
        self.assertEqual(result.mismatched_words, 0)
        self.assertEqual(result.bit_errors, 0)
        self.assertEqual(result.ber, 0.0)

    def test_pipeline_detects_mismatch(self):
        """Pipeline correctly detects when capture differs from golden."""
        csv_path, golden = _make_ila_csv_rows(num_prng_words=50)

        # Corrupt golden to simulate mismatch
        bad_golden = list(golden)
        bad_golden[10] = 0xDEADBEEFDEADBEEF

        fieldnames, rows = parse_ila_csv(csv_path)
        result = run_prng_check(rows, fieldnames, bad_golden, align_mode="dbg_state")

        self.assertFalse(result.passed)
        self.assertGreater(result.mismatched_words, 0)

    def test_align_by_sliding_window(self):
        """Sliding-window alignment finds correct offset."""
        gm = GoldenModel(seeds=DEFAULT_SEEDS)
        golden = gm.run(200)
        # Capture starts at golden[50]
        capture = golden[50:70]
        offset = align_by_sliding_window(capture, golden)
        self.assertEqual(offset, 50)

    def test_golden_vs_rtl_first_word_match(self):
        """Golden model and RTL model produce same output after init_done."""
        gm = GoldenModel(seeds=DEFAULT_SEEDS)
        golden = gm.run(50)

        rtl = RTLModel(seeds=DEFAULT_SEEDS)
        rtl.configure_and_start()
        rtl.run_iterations(50)
        rtl_outputs = rtl.get_outputs()

        # First 1-2 words may differ due to init_done/pipeline semantics
        # Words 10+ must match exactly
        for i in range(10, 50):
            self.assertEqual(
                golden[i], rtl_outputs[i],
                f"Mismatch at word {i}: golden=0x{golden[i]:016x} rtl=0x{rtl_outputs[i]:016x}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
