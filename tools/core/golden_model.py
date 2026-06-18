#!/usr/bin/env python3
# ==========================================================================
# golden_model.py — Single Source of Truth for PRNG-371
# ==========================================================================
#
# This module is the CANONICAL Python reference for PRNG-371 output.  All
# other models (RTL cycle model, FPGA capture comparison) MUST derive
# their expected output from this module so there is never ambiguity about
# "which model is correct?".
#
# Architecture:
#   GoldenModel  — pure-Python spec, identical to C tb_prng_371.cpp
#   generate_golden() — top-level helper with automatic C-library fallback
#   Multiplier functions — shared shift-and-add decompositions
#
# The RTL cycle model (rtl_model.py) imports the multiplier functions from
# here rather than duplicating them.
# ==========================================================================

from __future__ import annotations

import os
import sys
from typing import List, Optional, Tuple

# ==========================================================================
# Shift-and-add multipliers — exact RTL decomposition
# These are shared by both GoldenModel and RTLModel.
# ==========================================================================


def mul_371(x: int) -> int:
    """x * 371 = (x<<9) - (x<<7) - (x<<3) - (x<<2) - x  [5 ops]"""
    return ((x << 9) - (x << 7) - (x << 3) - (x << 2) - x) & 0xFFFF


def mul_373(x: int) -> int:
    """x * 373 = (x<<9) - (x<<7) - (x<<3) - (x<<1) - x  [5 ops]"""
    return ((x << 9) - (x << 7) - (x << 3) - (x << 1) - x) & 0xFFFF


def mul_375(x: int) -> int:
    """x * 375 = (x<<9) - (x<<7) - (x<<3) - x  [4 ops]"""
    return ((x << 9) - (x << 7) - (x << 3) - x) & 0xFFFF


def mul_50(x: int) -> int:
    """x * 50 = (x<<5) + (x<<4) + (x<<1)  [3 ops]"""
    return ((x << 5) + (x << 4) + (x << 1)) & 0xFFFF


def mul_48(x: int) -> int:
    """x * 48 = (x<<5) + (x<<4)  [2 ops]"""
    return ((x << 5) + (x << 4)) & 0xFFFF


def mul_46(x: int) -> int:
    """x * 46 = (x<<6) - (x<<4) - (x<<1)  [4 ops]"""
    return ((x << 6) - (x << 4) - (x << 1)) & 0xFFFF


def mul_111(x: int) -> int:
    """x * 111 = (x<<7) - (x<<4) - x  [3 ops]"""
    return ((x << 7) - (x << 4) - x) & 0xFFFF


# ==========================================================================
# GoldenModel — Pure-Python PRNG-371 Specification
# ==========================================================================

DEFAULT_SEEDS: Tuple[int, int, int, int] = (1000, 2000, 3000, 4000)

# Known first output word for seeds [1000,2000,3000,4000], warmup=0
KNOWN_FIRST_WORD: int = 0xC661F290C4215270


class GoldenModel:
    """Pure-Python PRNG-371 — canonical reference implementation.

    This is a line-by-line translation of tb_prng_371.cpp / golden_prng.py.
    It MUST produce identical output to the C golden library (golden_371.so)
    and to the HLS-generated RTL (after accounting for init_done / pipeline
    semantics captured by rtl_model.RTLModel).

    Usage::

        gm = GoldenModel(seeds=(1000, 2000, 3000, 4000))
        words = [gm.next() for _ in range(100)]
        # or equivalently:
        words = gm.run(100)
    """

    def __init__(self, seeds: Tuple[int, int, int, int] = DEFAULT_SEEDS):
        ix1, ix2, ix3, ix4 = seeds
        all_zero = (ix1 == 0) and (ix2 == 0) and (ix3 == 0) and (ix4 == 0)
        self.x1 = 1000 if all_zero else ix1
        self.x2 = 2000 if all_zero else ix2
        self.x3 = 3000 if all_zero else ix3
        self.s0 = 4000 if all_zero else ix4
        self.s1 = 0
        self.s2 = 0
        self.x1nr = 0
        self.x2nr = 0
        self.x3nr = 0
        self.x4nr = 0
        self.temp_reg = 0
        self.x1r = self.x1
        self.x2r = self.x2
        self.x3r = self.x3
        self.s0r = self.s0
        self.rot_cnt = 0
        self.u_prev1 = 0
        self.u_prev2 = 0
        self.pp_init = False
        self.outputs: List[int] = []

    def next(self) -> int:
        """Advance one PRNG iteration and return the 64-bit output word."""
        # ---- Stage 1: shift-and-add ----
        x1n = (mul_371(self.x1) + mul_50(self.s2)) & 0xFFFF
        x2n = (mul_373(self.x2) + mul_48(self.s1)) & 0xFFFF
        x3n = (mul_375(self.x3) + mul_46(self.s0)) & 0xFFFF
        z = (self.x3 + self.s0) & 0xFFFF
        x4n = (mul_111(self.x2) + ((self.temp_reg >> 14) & 0xFFFF)) & 0xFFFF
        inv = (z ^ 0xFFFF) & 0xFFFF
        self.temp_reg = (z * inv) & 0x3FFFFFFF
        self.x1nr = x1n
        self.x2nr = x2n
        self.x3nr = x3n
        self.x4nr = x4n

        # ---- Stage 2: state update + bit-rotation ----
        self.s2 = self.s1
        self.s1 = self.s0
        self.s0 = self.x4nr
        self.x1 = self.x1nr
        self.x2 = self.x2nr
        self.x3 = self.x3nr

        r1, r2, r3, r0 = self.x1, self.x2, self.x3, self.s0
        k = (1 + self.rot_cnt) & 0xF
        Z = ((r0 & 0xFFFF) << 48) | ((r3 & 0xFFFF) << 32) | \
            ((r2 & 0xFFFF) << 16) | (r1 & 0xFFFF)
        if k == 0:
            Z_rot = Z
        else:
            Z_rot = ((Z << k) | (Z >> (64 - k))) & 0xFFFFFFFFFFFFFFFF
        r1 = Z_rot & 0xFFFF
        r2 = (Z_rot >> 16) & 0xFFFF
        r3 = (Z_rot >> 32) & 0xFFFF
        r0 = (Z_rot >> 48) & 0xFFFF
        self.rot_cnt = 0 if self.rot_cnt == 14 else ((self.rot_cnt + 1) & 0xF)
        self.x1r = r1
        self.x2r = r2
        self.x3r = r3
        self.s0r = r0

        # ---- Stage 3: post-processing (XOR chain) ----
        u_t = ((self.s0r & 0xFFFF) << 48) | ((self.x3r & 0xFFFF) << 32) | \
              ((self.x2r & 0xFFFF) << 16) | (self.x1r & 0xFFFF)
        if not self.pp_init:
            y_t = u_t
            self.pp_init = True
        else:
            rt1 = ((self.u_prev1 << 17) | (self.u_prev1 >> 47)) & 0xFFFFFFFFFFFFFFFF
            rt2 = ((self.u_prev2 << 43) | (self.u_prev2 >> 21)) & 0xFFFFFFFFFFFFFFFF
            y_t = u_t ^ rt1 ^ rt2
        self.u_prev2 = self.u_prev1
        self.u_prev1 = u_t

        out = y_t & 0xFFFFFFFFFFFFFFFF
        self.outputs.append(out)
        return out

    def run(self, n: int) -> List[int]:
        """Generate *n* consecutive PRNG output words."""
        return [self.next() for _ in range(n)]

    def reset(self):
        """Reset to initial state (re-run constructor semantics)."""
        self.__init__()
        # __init__ already handled; the line above is intentional no-op coverage
        # for the "reset" concept — caller should re-construct for a fresh model.
        # We provide this as a convenience for test loops.
        pass


# ==========================================================================
# Top-level generator with C-library fallback
# ==========================================================================


def generate_golden(
    num_words: int,
    seeds: Tuple[int, int, int, int] = DEFAULT_SEEDS,
    warmup: int = 0,
    golden_lib_dir: Optional[str] = None,
) -> List[int]:
    """Generate *num_words* golden reference words.

    Tries the compiled C library first (faster for large batches), then
    falls back to the pure-Python ``GoldenModel`` (always available).

    Args:
        num_words: Number of output words to produce.
        seeds: 4-tuple of 16-bit seed values.
        warmup: Number of initial iterations to discard.
        golden_lib_dir: Directory containing ``golden_prng.py`` (C extension).
            Defaults to ``<project_root>/hls_src/``.

    Returns:
        List of *num_words* 64-bit integers.
    """
    # ---- Try C library ----
    if golden_lib_dir is None:
        # Default: hls_src/ relative to this file's grandparent (project root)
        this_dir = os.path.dirname(os.path.abspath(__file__))
        golden_lib_dir = os.path.normpath(
            os.path.join(this_dir, "..", "..", "hls_src")
        )

    if golden_lib_dir not in sys.path:
        sys.path.insert(0, golden_lib_dir)

    try:
        from golden_prng import ChaosPRNG371
        prng = ChaosPRNG371(init_x=seeds, warmup=warmup)
        return prng.generate(num_words)
    except (ImportError, OSError):
        pass  # Fall through to Python model

    # ---- Pure-Python fallback ----
    gm = GoldenModel(seeds=seeds)
    total = warmup + num_words
    all_words = gm.run(total)
    return all_words[warmup:]


# ==========================================================================
# Golden reference file I/O
# ==========================================================================


def load_golden_file(path: str, limit: Optional[int] = None) -> List[int]:
    """Load golden reference words from a plain hex-text file.

    Each 64-bit word is matched as exactly 16 hex digits (optional ``0x`` prefix).
    """
    from .utils import HEX64_RE, read_text_file

    text = read_text_file(path)
    words: List[int] = []
    for m in HEX64_RE.finditer(text):
        words.append(int(m.group(1), 16))
        if limit is not None and len(words) >= limit:
            break
    if not words:
        raise ValueError(f"No 64-bit hex words found in: {path}")
    return words
