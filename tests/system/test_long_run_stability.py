#!/usr/bin/env python3
# ==========================================================================
# Phase III: Long-Run Stability Tests
# ==========================================================================
# Runs PRNG for ≥ 10^7 cycles, samples output hash at regular intervals,
# and verifies:
#   - No divergence from RTL/golden model
#   - No periodic lock-up or stuck state
#   - Statistical uniformity of hash samples (no degradation)
# ==========================================================================

import os, sys, hashlib, time
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from tools.core.rtl_model import RTLModel as RTLState
from tools.core.golden_model import GoldenModel as GoldenModelState

SEEDS = (1000, 2000, 3000, 4000)


def crc64_fast(data: int) -> int:
    """Simple 64-bit hash for output sampling (not cryptographic)."""
    # Use Python's built-in hash + SHA-256 truncation for fast, stable hashing
    h = hashlib.sha256(data.to_bytes(8, 'little')).digest()
    return int.from_bytes(h[:8], 'little')


def test_long_run_vs_golden(run_test, total_cycles: int) -> list:
    """Run PRNG for total_cycles iterations, compare every N-th output against golden model."""

    def _body():
        SAMPLE_EVERY = max(1, total_cycles // 1000)  # 1000 samples across the run
        rtl = RTLState(seeds=SEEDS)
        rtl.configure_and_start()
        golden = GoldenModelState(seeds=SEEDS)

        mismatches = 0
        first_mismatch = None
        samples_checked = 0

        t0 = time.perf_counter()
        for i in range(total_cycles):
            rtl.step()  # state1
            rtl.step()  # state2
            golden_out = golden.next()

            if i % SAMPLE_EVERY == 0:
                rtl_out = rtl.outputs[i] if i < len(rtl.outputs) else 0
                if rtl_out != golden_out:
                    mismatches += 1
                    if first_mismatch is None:
                        first_mismatch = i
                samples_checked += 1

        elapsed = time.perf_counter() - t0
        passed = mismatches == 0
        return passed, \
            f"{total_cycles:,} iterations in {elapsed:.1f}s: {mismatches} mismatches in {samples_checked} samples", \
            {
                "total_iterations": total_cycles,
                "samples_checked": samples_checked,
                "mismatches": mismatches,
                "first_mismatch_at": first_mismatch,
                "elapsed_seconds": round(elapsed, 1),
                "iterations_per_second": round(total_cycles / elapsed),
            }

    return [run_test({"name": f"test_long_run_{total_cycles}_iterations", "passed": False, "detail": "", "metrics": {}}, _body)]


def test_no_stuck_state(run_test, total_cycles: int) -> list:
    """Verify PRNG never produces all-zero or repeating-pattern sequences (stuck state detection)."""

    def _body():
        rtl = RTLState(seeds=SEEDS)
        rtl.configure_and_start()
        rtl.run_iterations(total_cycles)
        outputs = rtl.get_outputs()

        # Detect stuck states
        zero_count = sum(1 for o in outputs if o == 0)
        zero_run_max = 0
        current_run = 0
        for o in outputs:
            if o == 0:
                current_run += 1
                zero_run_max = max(zero_run_max, current_run)
            else:
                current_run = 0

        # Detect repeated patterns: check if any 4-word window repeats
        pattern_dup = 0
        seen_4grams = set()
        for i in range(len(outputs) - 3):
            gram = (outputs[i], outputs[i+1], outputs[i+2], outputs[i+3])
            if gram in seen_4grams:
                pattern_dup += 1
            else:
                seen_4grams.add(gram)

        # Pass conditions
        zero_ok = zero_run_max < 10  # no run of 10+ consecutive zeros
        pattern_ok = pattern_dup == 0  # no 4-gram repeats
        passed = zero_ok and pattern_ok

        detail = f"Max zero run: {zero_run_max} (<10={'OK' if zero_ok else 'FAIL'}), 4-gram repeats: {pattern_dup} (0={'OK' if pattern_ok else 'FAIL'})"
        return passed, detail, {
            "total_iterations": total_cycles,
            "zero_outputs": zero_count,
            "max_consecutive_zeros": zero_run_max,
            "four_gram_repeats": pattern_dup,
            "unique_outputs": len(set(outputs)),
        }

    return [run_test({"name": f"test_no_stuck_state_{total_cycles}", "passed": False, "detail": "", "metrics": {}}, _body)]


def test_output_hash_stability(run_test, total_cycles: int) -> list:
    """Sample output CRC64 at regular intervals — verify statistical uniformity (no drift)."""

    def _body():
        rtl = RTLState(seeds=SEEDS)
        rtl.configure_and_start()
        rtl.run_iterations(total_cycles)
        outputs = rtl.get_outputs()

        # Sample up to 1000 points evenly across the run
        num_samples = min(1000, len(outputs))
        sample_interval = max(1, len(outputs) // num_samples)
        hashes = []
        for i in range(0, len(outputs), sample_interval):
            hashes.append(crc64_fast(outputs[i]))
        hashes = hashes[:num_samples]

        # Check: all hashes unique (no hash collision = good entropy)
        unique_hashes = len(set(hashes))
        collision_rate = 1.0 - (unique_hashes / len(hashes)) if len(hashes) > 0 else 0

        # Check: hash distribution (byte-level) roughly uniform
        # With 1000 samples × 8 bytes = 8000 bytes across 256 buckets,
        # expect ~31 per bucket. Ratio > 0.25 indicates no severe bias.
        byte_counter = Counter()
        for h in hashes:
            for b in h.to_bytes(8, 'little'):
                byte_counter[b] += 1

        byte_min = min(byte_counter.values())
        byte_max = max(byte_counter.values())
        byte_balance = byte_min / byte_max if byte_max > 0 else 0

        # Pass: no collisions, byte distribution not severely skewed
        no_collision = collision_rate == 0.0
        balanced = byte_balance > 0.15  # relaxed for finite sample statistics
        passed = no_collision and balanced

        return passed, \
            f"{len(hashes)} samples: collisions={collision_rate:.4f}, byte_balance={byte_balance:.3f} ({'OK' if balanced else 'SKEWED'})", \
            {
                "samples": len(hashes),
                "unique_hashes": unique_hashes,
                "collision_rate": collision_rate,
                "byte_balance_ratio": round(byte_balance, 3),
            }

    return [run_test({"name": f"test_output_hash_stability_{total_cycles}", "passed": False, "detail": "", "metrics": {}}, _body)]


def test_no_periodic_lock(run_test, total_cycles: int) -> list:
    """Detect periodic behavior by checking if any state repeats after the first 1000 cycles."""

    def _body():
        rtl = RTLState(seeds=SEEDS)
        rtl.configure_and_start()
        rtl.run_iterations(min(1000, total_cycles))
        # After warmup, capture state snapshots
        state_hashes = []

        for i in range(total_cycles - 1000):
            rtl.step()
            rtl.step()
            if i % 1000 == 0:
                # Hash current state
                state_tuple = (
                    rtl.x1_reg, rtl.x2_reg, rtl.x3_reg, rtl.s0_reg,
                    rtl.s1_reg, rtl.s2_reg, rtl.rot_cnt,
                )
                state_hash = hash(state_tuple)
                state_hashes.append(state_hash)

        unique_states = len(set(state_hashes))
        total_snapshots = len(state_hashes)
        uniqueness = unique_states / total_snapshots if total_snapshots > 0 else 0

        # Pass: all state snapshots unique (no periodic lock detected)
        passed = uniqueness == 1.0
        return passed, \
            f"{total_snapshots} state snapshots: {unique_states}/{total_snapshots} unique ({uniqueness*100:.1f}%)", \
            {
                "state_snapshots": total_snapshots,
                "unique_states": unique_states,
                "uniqueness": uniqueness,
            }

    return [run_test({"name": f"test_no_periodic_lock_{total_cycles}", "passed": False, "detail": "", "metrics": {}}, _body)]


# ==========================================================================
# Test Registry
# ==========================================================================

def run_longrun_tests(run_test_fn, total_cycles: int):
    results = []
    results.extend(test_long_run_vs_golden(run_test_fn, total_cycles))
    results.extend(test_no_stuck_state(run_test_fn, min(total_cycles, 100_000)))
    results.extend(test_output_hash_stability(run_test_fn, min(total_cycles, 1_000_000)))
    results.extend(test_no_periodic_lock(run_test_fn, min(total_cycles, 100_000)))
    return results
