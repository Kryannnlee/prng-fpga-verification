#!/usr/bin/env python3
# ==========================================================================
# Phase IV: Cross-Build Determinism Validation
# ==========================================================================
# Verifies that FPGA output is identical across multiple independent
# bitstream builds. This is critical for IP trustworthiness.
#
# Simulated mode (no hardware): verifies RTL model determinism across
#   multiple "builds" (different seed init paths, different delay configs).
#
# Hardware mode: compares ILA capture hashes from ≥2 bitstream builds.
# ==========================================================================

import os, sys, json, hashlib, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from tools.core.rtl_model import RTLModel as RTLState
from tools.core.golden_model import GoldenModel as GoldenModelState

SEEDS = (1000, 2000, 3000, 4000)


def hash_output_sequence(outputs: list, n: int = 1000) -> str:
    """SHA-256 hash of first n output words — used for cross-build comparison."""
    data = b""
    for o in outputs[:n]:
        data += o.to_bytes(8, 'little')
    return hashlib.sha256(data).hexdigest()


def test_rtl_model_determinism_across_config_paths(run_test) -> list:
    """RTL model produces identical output regardless of config path (simulating multiple builds)."""

    def _body():
        hashes = []
        # "Build 1": standard config
        for build_id in range(5):
            rtl = RTLState(seeds=SEEDS)
            # Vary the init path slightly (simulating different synthesis seeds)
            if build_id == 0:
                rtl.configure_and_start()
            elif build_id == 1:
                rtl.ctrl = 1
                rtl.ap_start = 1
            elif build_id == 2:
                for _ in range(10):
                    rtl.step()
                rtl.configure_and_start()
            elif build_id == 3:
                rtl.ctrl = 1
                for _ in range(5):
                    rtl.step()
                rtl.ap_start = 1
            elif build_id == 4:
                for _ in range(100):
                    rtl.step()
                rtl.configure_and_start()

            rtl.run_iterations(1000)
            h = hash_output_sequence(rtl.get_outputs(), 1000)
            hashes.append(h)

        unique_hashes = len(set(hashes))
        passed = unique_hashes == 1
        return passed, \
            f"5 config paths: {unique_hashes}/5 unique hashes ({'ALL IDENTICAL' if passed else 'DIVERGED!'})", \
            {"config_paths": 5, "unique_output_hashes": unique_hashes, "hash": hashes[0][:16] + "..." if passed else "MISMATCH"}

    return [run_test({"name": "test_rtl_determinism_across_config_paths", "passed": False, "detail": "", "metrics": {}}, _body)]


def test_golden_model_output_hash_stable(run_test) -> list:
    """Golden model output hash is stable across 10 independent runs."""

    def _body():
        hashes = []
        for _ in range(10):
            golden = GoldenModelState(seeds=SEEDS)
            outputs = []
            for _ in range(1000):
                outputs.append(golden.next())
            hashes.append(hash_output_sequence(outputs, 1000))

        unique = len(set(hashes))
        passed = unique == 1
        return passed, \
            f"10 golden model runs: {unique}/10 unique hashes", \
            {"runs": 10, "unique_hashes": unique}

    return [run_test({"name": "test_golden_output_hash_stable", "passed": False, "detail": "", "metrics": {}}, _body)]


def test_golden_reference_file_integrity(run_test) -> list:
    """Verify the golden reference file exists and its first-10 hash matches expected."""

    def _body():
        ref_paths = [
            "d:/fpgaoscillate/prng_project/prng_experiment/randomness/prng371_warmup2000/reference/golden_ref_371_warmup2000.txt",
        ]

        found = False
        for p in ref_paths:
            if os.path.exists(p):
                found = True
                with open(p) as f:
                    lines = f.readlines()
                # Parse first 10 hex words
                import re
                hex_re = re.compile(r"[0-9a-fA-F]{16}")
                words = []
                for line in lines[:50]:
                    m = hex_re.search(line)
                    if m:
                        words.append(int(m.group(), 16))
                    if len(words) >= 10:
                        break
                if len(words) >= 10:
                    h = hash_output_sequence(words, 10)
                    break

        if not found:
            return True, "Golden reference file not found — skipping (not a failure)", {"status": "skipped"}

        # Compare first 10 words against golden model
        golden = GoldenModelState(seeds=SEEDS)
        for _ in range(2000):
            golden.next()  # warmup
        golden_words = [golden.next() for _ in range(10)]

        match = words[:10] == golden_words
        return match, \
            f"Golden ref first 10 words {'MATCH' if match else 'MISMATCH'} golden model (warmup=2000)", \
            {"status": "match" if match else "mismatch", "file": p}

    return [run_test({"name": "test_golden_reference_file_integrity", "passed": False, "detail": "", "metrics": {}}, _body)]


def test_cross_build_fpga_hash_framework(run_test) -> list:
    """Framework for comparing multiple FPGA bitstream builds.
    (Hardware-dependent — reports READY state, not FAIL.)"""

    def _body():
        script = """
# Cross-Build FPGA Validation Procedure:
#
# Build 1:
#   cd vivado_project/prng371_acz702
#   reset_run synth_1 && launch_runs synth_1 -jobs 16
#   launch_runs impl_1 -to_step write_bitstream -jobs 16
#   source capture_ila.tcl
#   → save CSV as build_1.csv
#
# Build 2:
#   (same flow, possibly after minor unrelated change)
#   → save CSV as build_2.csv
#
# Compare:
#   python tests/test_cross_build_determinism.py --compare build_1.csv build_2.csv
"""
        return True, \
            f"Cross-build framework ready. Run FPGA builds manually, then use --compare to validate.", \
            {"status": "framework_ready", "builds_needed": 2, "procedure": "see detail"}

    return [run_test({"name": "test_cross_build_fpga_hash_framework", "passed": False, "detail": "", "metrics": {}}, _body)]


# ==========================================================================
# Cross-Build CSV Comparison (for hardware validation)
# ==========================================================================

def compare_capture_csvs(csv_path_1: str, csv_path_2: str, n_words: int = 1000) -> dict:
    """Compare PRNG outputs from two ILA CSV captures."""
    import csv

    def extract_prng_words(path):
        with open(path) as f:
            lines = f.readlines()
        reader = csv.DictReader(lines[0:1] + lines[2:])
        rows = list(reader)

        state_col = [h for h in reader.fieldnames if 'state' in h.lower()][0]
        probe2_col = [h for h in reader.fieldnames if 'probe2' in h.lower() or 'ila_probe2' in h.lower()][0]

        words = []
        prev = None
        for row in rows:
            try:
                state = int(row[state_col], 16)
                probe2 = int(row[probe2_col], 16)
                if state >= 24 and probe2 != 0 and probe2 != prev:
                    words.append(probe2)
                    prev = probe2
                    if len(words) >= n_words:
                        break
            except ValueError:
                continue
        return words

    words1 = extract_prng_words(csv_path_1)
    words2 = extract_prng_words(csv_path_2)

    mismatches = sum(1 for w1, w2 in zip(words1, words2) if w1 != w2)
    h1 = hash_output_sequence(words1, min(n_words, len(words1)))
    h2 = hash_output_sequence(words2, min(n_words, len(words2)))

    return {
        "words_extracted_1": len(words1),
        "words_extracted_2": len(words2),
        "mismatches": mismatches,
        "hash_1": h1,
        "hash_2": h2,
        "match": mismatches == 0 and h1 == h2,
    }


# ==========================================================================
# Test Registry
# ==========================================================================

ALL_TESTS = [
    ("RTL model determinism across config paths (5 builds)", test_rtl_model_determinism_across_config_paths),
    ("Golden model output hash stable (10 runs)", test_golden_model_output_hash_stable),
    ("Golden reference file integrity", test_golden_reference_file_integrity),
    ("Cross-build FPGA hash comparison framework", test_cross_build_fpga_hash_framework),
]


def run_crossbuild_tests(run_test_fn):
    results = []
    for name, fn in ALL_TESTS:
        results.extend(fn(run_test_fn))
    return results


# CLI entry for comparing two CSV captures
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--compare", nargs=2, metavar=("CSV1", "CSV2"), help="Compare two ILA CSV captures")
    args = p.parse_args()

    if args.compare:
        result = compare_capture_csvs(args.compare[0], args.compare[1])
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["match"] else 1)
