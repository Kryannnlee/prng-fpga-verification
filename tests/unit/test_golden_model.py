#!/usr/bin/env python3
# ==========================================================================
# Unit test: GoldenModel — verify canonical PRNG-371 output
# ==========================================================================

import os, sys, unittest

# Ensure tools/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tools.core.golden_model import GoldenModel, KNOWN_FIRST_WORD, DEFAULT_SEEDS


class TestGoldenModel(unittest.TestCase):
    """Verify the single-source-of-truth golden model."""

    def test_first_word_known_value(self):
        """First output word for default seeds is a known constant."""
        gm = GoldenModel(seeds=DEFAULT_SEEDS)
        w0 = gm.next()
        self.assertEqual(w0, KNOWN_FIRST_WORD,
                         f"First word mismatch: 0x{w0:016x} vs 0x{KNOWN_FIRST_WORD:016x}")

    def test_first_20_words_deterministic(self):
        """Same seeds always produce same sequence."""
        gm1 = GoldenModel(seeds=DEFAULT_SEEDS)
        words1 = gm1.run(20)

        gm2 = GoldenModel(seeds=DEFAULT_SEEDS)
        words2 = gm2.run(20)

        self.assertEqual(words1, words2)

    def test_different_seeds_different_output(self):
        """Different seeds produce different outputs."""
        gm1 = GoldenModel(seeds=(1000, 2000, 3000, 4000))
        gm2 = GoldenModel(seeds=(999, 2000, 3000, 4000))

        w1 = gm1.next()
        w2 = gm2.next()
        self.assertNotEqual(w1, w2,
                            f"Different seeds should produce different outputs")

    def test_warmup_skip(self):
        """Warmup correctly discards initial outputs."""
        gm = GoldenModel(seeds=DEFAULT_SEEDS)
        all_words = gm.run(100)

        gm2 = GoldenModel(seeds=DEFAULT_SEEDS)
        gm2.run(50)  # warmup=50
        post_warmup = gm2.run(50)

        self.assertEqual(all_words[50:100], post_warmup)

    def test_all_zero_seeds_use_defaults(self):
        """All-zero seeds trigger default seed values (per HLS semantics)."""
        gm_default = GoldenModel(seeds=DEFAULT_SEEDS)
        gm_zero = GoldenModel(seeds=(0, 0, 0, 0))
        self.assertEqual(gm_default.run(10), gm_zero.run(10))

    def test_16bit_wrapping(self):
        """All state values stay within 16-bit range."""
        gm = GoldenModel(seeds=(65535, 65535, 65535, 65535))
        words = gm.run(1000)
        for w in words:
            self.assertLess(w, 0xFFFFFFFFFFFFFFFF + 1)

    def test_output_unique_early(self):
        """Early outputs are not all identical."""
        gm = GoldenModel()
        words = gm.run(100)
        unique = len(set(words))
        self.assertGreater(unique, 50, f"Only {unique} unique words in first 100")


if __name__ == "__main__":
    unittest.main(verbosity=2)
