#!/usr/bin/env python3
"""
Generate a simulated Vivado ILA CSV capture with cfg_done trigger.

This creates realistic test data for the validate_fpga_prng.py pipeline.
The simulated data mimics the exact behavior of the FPGA PRNG-371:
- Each output value appears for 2 ILA samples (state1→state2 FSM toggle)
- cfg_done rises at a configurable trigger point
- Output uses the real golden model for bit-exact values

Usage:
    python generate_simulated_capture.py --output simulated_ila_capture.csv
    python generate_simulated_capture.py --output test.csv --warmup 2000 --num-words 100
"""

import argparse
import os
import re
import sys
from typing import List, Tuple


DEFAULT_SEEDS = (1000, 2000, 3000, 4000)


# Path to pre-computed golden reference (fallback when C library unavailable)
_GOLDEN_REF_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "prng_experiment", "randomness", "prng371_warmup2000",
    "reference", "golden_ref_371_warmup2000.txt"
)
_GOLDEN_REF_PATH = os.path.normpath(_GOLDEN_REF_PATH)

HEX64_RE = re.compile(r"(?i)(?<![0-9a-f])(?:0x)?([0-9a-f]{16})(?![0-9a-f])")


def _load_golden_from_text(num_words: int) -> List[int]:
    """Load golden words from pre-computed text file (cross-platform fallback).

    The golden reference file is post-warmup-2000 output. When we simulate a
    capture with warmup=0, we use these words directly as the PRNG output.
    This is valid because the validation pipeline skips the same number of
    warmup words on both sides.
    """
    if not os.path.exists(_GOLDEN_REF_PATH):
        raise FileNotFoundError(
            f"Golden reference file not found: {_GOLDEN_REF_PATH}\n"
            "Either compile the C golden model or ensure the reference file exists."
        )
    all_words = []
    with open(_GOLDEN_REF_PATH, "r", encoding="utf-8") as f:
        for line in f:
            for match in HEX64_RE.finditer(line):
                all_words.append(int(match.group(1), 16))

    if len(all_words) < num_words:
        raise ValueError(
            f"Golden reference file only has {len(all_words)} words, "
            f"but {num_words} needed."
        )
    return all_words[:num_words]


def load_golden_model():
    """Import golden_prng module (Linux only — requires golden_371.so)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    golden_dir = os.path.join(script_dir, "..", "..", "hw", "src_371")
    golden_dir = os.path.normpath(golden_dir)
    if golden_dir not in sys.path:
        sys.path.insert(0, golden_dir)
    from golden_prng import ChaosPRNG371
    return ChaosPRNG371


def generate_real_prng_output(
    num_words: int,
    seeds: Tuple[int, int, int, int] = DEFAULT_SEEDS,
) -> List[int]:
    """
    Generate PRNG output words.

    Tries C library first; falls back to pre-computed text file on Windows.

    NOTE: The generated words are the POST-WARMUP output (matching the golden
    reference file format). Set warmup=0 in the validation pipeline when using
    simulated data, since both sides are already aligned to post-warmup state.

    Each word is duplicated (2 samples per word, matching FPGA behavior).
    """
    try:
        ChaosPRNG371 = load_golden_model()
        # With C library, we can generate from any seed with any warmup and
        # then output the next num_words — equivalent to post-warmup output.
        # Use the default seeds with 2000 warmup to match golden file.
        prng = ChaosPRNG371(init_x=seeds, warmup=2000)
        all_words = [prng.next() for _ in range(num_words)]
    except (OSError, ImportError) as e:
        print(f"  (C golden library unavailable: {e})")
        print(f"  Falling back to pre-computed golden text file...")
        all_words = _load_golden_from_text(num_words)

    # Each word appears twice (FPGA state1→state2 toggle)
    duplicated = []
    for word in all_words:
        duplicated.append(word)
        duplicated.append(word)

    return duplicated


def generate_simulated_csv(
    output_path: str,
    warmup: int = 2000,
    num_words: int = 100,
    trigger_sample: int = 500,
    pre_trigger_noise: int = 100,
):
    """
    Generate a simulated Vivado ILA CSV file.

    Structure:
    - pre_trigger_noise samples before trigger (random-looking data from mid-PRNG)
    - At trigger_sample, cfg_done rises 0→1 (stays 1 for 256 samples)
    - After trigger, PRNG starts from iteration 0 with the golden model
    - Each PRNG output appears twice (state1→state2 FSM)
    """
    # Generate the PRNG sequence (post-warmup, matching golden reference format)
    # warmup=0 for validation because both simulated data and golden file are
    # already aligned to the same post-warmup state.
    raw_prng = generate_real_prng_output(num_words)

    # Before trigger: dummy data (PRNG mid-stream, warmup already passed)
    # After trigger: the real sequence starts from iteration 0
    total_samples = trigger_sample + len(raw_prng)

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        # Vivado ILA CSV header (2 rows)
        f.write("Sample in Buffer,Sample in Window,TRIGGER,"
                "dbg_state[4:0],prng_led_valid,dbg_prng_out[63:0],"
                "dbg_axi_stat[3:0],cfg_done,dbg_time[31:0]\n")
        f.write("Radix - UNSIGNED,UNSIGNED,UNSIGNED,HEX,HEX,HEX,HEX,HEX,HEX\n")

        for i in range(total_samples):
            buf = i
            win = i

            if i < trigger_sample:
                # Pre-trigger: dummy mid-stream data
                state = 25  # Running state
                valid = 1
                prng_val = 0xDEADBEEF00000000 + i
                axi_stat = 0
                cfg_done = 0
                trigger = 0
                time_val = 1000000 + i    # pre-reset: large counter value
            elif i < trigger_sample + 256:
                # Post-trigger: cfg_done high, PRNG output begins
                offset = i - trigger_sample
                state = 25
                valid = 1
                prng_val = raw_prng[offset]
                axi_stat = 0
                cfg_done = 1
                trigger = 1 if i == trigger_sample else 0
                time_val = i - trigger_sample   # post-reset: counter from 0
            else:
                # Post-trigger, cfg_done back to 0
                offset = i - trigger_sample
                state = 25
                valid = 1
                prng_val = raw_prng[offset]
                axi_stat = 0
                cfg_done = 0
                trigger = 0
                time_val = i - trigger_sample   # post-reset: counter from 0

            f.write(f"{buf},{win},{trigger},{state},{valid},"
                    f"0x{prng_val:016x},{axi_stat},{cfg_done},0x{time_val:08x}\n")

    print(f"Simulated ILA capture written to: {output_path}")
    print(f"  Trigger sample: {trigger_sample}")
    print(f"  cfg_done pulse: {trigger_sample} → {trigger_sample + 255}")
    print(f"  Total samples: {total_samples}")
    print(f"  Post-warmup output words: {num_words}")
    print(f"  First post-trigger word: 0x{raw_prng[0]:016x}")
    print()
    print("To validate this capture (warmup=0 because simulated data is post-warmup):")
    print(f"  python validate_fpga_prng.py --capture {output_path} --generate-golden "
          f"--warmup 0 --num-words {num_words}")
    print()
    print("Or using the golden text file:")
    golden_ref = os.path.normpath(os.path.join(
        os.path.dirname(output_path) or ".",
        "..", "..", "prng_experiment", "randomness", "prng371_warmup2000",
        "reference", "golden_ref_371_warmup2000.txt"
    ))
    print(f"  python validate_fpga_prng.py --capture {output_path} "
          f"--golden \"{golden_ref}\" --warmup 0")


def main():
    parser = argparse.ArgumentParser(
        description="Generate simulated Vivado ILA CSV with cfg_done trigger"
    )
    parser.add_argument("--output", default="simulated_ila_capture.csv",
                        help="Output CSV file path")
    parser.add_argument("--warmup", type=int, default=2000,
                        help="Number of warmup iterations (default: 2000)")
    parser.add_argument("--num-words", type=int, default=100,
                        help="Number of output words after warmup (default: 100)")
    parser.add_argument("--trigger-sample", type=int, default=500,
                        help="Sample index where cfg_done rises (default: 500)")
    parser.add_argument("--seed", nargs=4, type=int, default=list(DEFAULT_SEEDS),
                        help="PRNG seeds (default: 1000 2000 3000 4000)")
    args = parser.parse_args()

    generate_simulated_csv(
        output_path=args.output,
        warmup=args.warmup,
        num_words=args.num_words,
        trigger_sample=args.trigger_sample,
    )


if __name__ == "__main__":
    main()
