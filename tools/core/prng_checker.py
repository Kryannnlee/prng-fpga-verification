#!/usr/bin/env python3
# ==========================================================================
# prng_checker.py — PRNG Output Alignment, Extraction, and Comparison
# ==========================================================================
#
# The core verification engine: takes parsed ILA CSV rows, aligns to the
# FSM running state (cfg_done edge, dbg_state threshold, or sliding-window
# golden match), extracts unique PRNG output words, and compares against
# a golden reference.
#
# Pipeline:
#   rows → align → extract (dedup + pipeline-skip) → compare → result
#
# Usage::
#
#     from tools.core.prng_checker import run_prng_check
#     result = run_prng_check(rows, fieldnames, golden_words)
#     print(result.passed)  # True/False
# ==========================================================================

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class AlignmentInfo:
    """Result of aligning capture to FSM running state."""
    trigger_sample: int         # Sample index of alignment point
    total_samples: int          # Total samples in capture
    pre_trigger_samples: int    # Samples before alignment
    post_trigger_samples: int   # Samples after alignment (inclusive)
    align_mode: str = "cfg_done"  # cfg_done | dbg_state | first_word

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PrngCheckResult:
    """Complete PRNG comparison result."""
    # Alignment
    alignment: Optional[AlignmentInfo] = None

    # Extraction
    total_samples_parsed: int = 0
    unique_words_extracted: int = 0
    warmup_skipped: int = 0

    # Comparison
    total_compared: int = 0
    matched_words: int = 0
    mismatched_words: int = 0
    bit_errors: int = 0
    ber: float = 0.0

    # First mismatch diagnostics
    first_mismatch_index: Optional[int] = None
    first_mismatch_golden: Optional[int] = None
    first_mismatch_capture: Optional[int] = None
    first_mismatch_raw_sample: Optional[int] = None
    first_mismatch_time_cnt: Optional[int] = None

    # Status
    passed: bool = False
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        if self.total_compared == 0:
            return 0.0
        return 100.0 * self.matched_words / self.total_compared

    def to_dict(self) -> Dict:
        d: Dict = {
            "passed": self.passed,
            "alignment": self.alignment.to_dict() if self.alignment else None,
            "extraction": {
                "total_samples_parsed": self.total_samples_parsed,
                "unique_words_extracted": self.unique_words_extracted,
                "warmup_skipped": self.warmup_skipped,
            },
            "comparison": {
                "total_compared": self.total_compared,
                "matched_words": self.matched_words,
                "mismatched_words": self.mismatched_words,
                "bit_errors": self.bit_errors,
                "ber": self.ber,
                "match_rate_pct": self.match_rate,
            },
            "first_mismatch": None,
            "warnings": self.warnings,
            "errors": self.errors,
        }
        if self.first_mismatch_index is not None:
            d["first_mismatch"] = {
                "word_index": self.first_mismatch_index,
                "golden": f"0x{self.first_mismatch_golden:016x}" if self.first_mismatch_golden is not None else None,
                "capture": f"0x{self.first_mismatch_capture:016x}" if self.first_mismatch_capture is not None else None,
                "raw_csv_sample": self.first_mismatch_raw_sample,
                "time_cnt": self.first_mismatch_time_cnt,
            }
        return d


# ---------------------------------------------------------------------------
# Known constants
# ---------------------------------------------------------------------------

# First PRNG output word for seeds [1000,2000,3000,4000], warmup=0
KNOWN_FIRST_WORD: int = 0xC661F290C4215270

# FSM state threshold: state >= 25 means PRNG is running
# (state 24 = final AXI write of ctrl=1, NOT PRNG output)
PRNG_RUNNING_STATE: int = 25


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------


def _find_col(fieldnames: List[str], *candidates: str) -> Optional[str]:
    """Find column by candidate base names."""
    for cand in candidates:
        cand_lower = cand.lower()
        for fn in fieldnames:
            fn_lower = fn.lower()
            if fn_lower == cand_lower:
                return fn
            base = fn_lower.split("[")[0]
            if base == cand_lower:
                return fn
    return None


# ---------------------------------------------------------------------------
# Alignment strategies
# ---------------------------------------------------------------------------


def align_to_cfg_done(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
) -> Optional[AlignmentInfo]:
    """Find first sample where cfg_done transitions 0 → 1.

    This is the preferred alignment when the ILA has hardware trigger on
    ``dbg_heartbeat == 1`` — the cfg_done edge corresponds to FSM leaving
    the startup gate and entering the config phase.

    Returns None if cfg_done column is missing or no rising edge is found.
    """
    cfg_done_col = _find_col(fieldnames, "cfg_done")
    if cfg_done_col is None:
        return None

    prev = "0"
    for i, row in enumerate(rows):
        val = row.get(cfg_done_col, "0").strip()
        is_one = val in ("1", "01") or val.lower().startswith("0x1")
        is_zero = val in ("0", "00") or val.lower().startswith("0x0")
        cur = "1" if is_one else "0"
        if prev == "0" and cur == "1":
            return AlignmentInfo(
                trigger_sample=i,
                total_samples=len(rows),
                pre_trigger_samples=i,
                post_trigger_samples=len(rows) - i,
                align_mode="cfg_done",
            )
        prev = cur

    return None


def align_to_running_state(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
    min_state: int = PRNG_RUNNING_STATE,
) -> Optional[AlignmentInfo]:
    """Find first sample where dbg_state >= *min_state*.

    Fallback for free-run ILA captures where the cfg_done edge is not
    in the capture window.
    """
    state_col = _find_col(fieldnames, "dbg_state")
    if state_col is None:
        return None

    for i, row in enumerate(rows):
        raw = row.get(state_col, "").strip()
        try:
            state = int(raw, 16) if raw else 0
        except ValueError:
            continue
        if state >= min_state:
            return AlignmentInfo(
                trigger_sample=i,
                total_samples=len(rows),
                pre_trigger_samples=i,
                post_trigger_samples=len(rows) - i,
                align_mode="dbg_state",
            )

    return None


def align_by_sliding_window(
    capture_words: List[int],
    golden_words: List[int],
    min_match: int = 4,
) -> int:
    """Find capture[0] position in golden_words via sliding-window prefix match.

    Returns the offset into *golden_words*, or -1 if not found.
    """
    if len(capture_words) < min_match or len(golden_words) < min_match:
        return -1
    prefix = capture_words[:min_match]
    for offset in range(len(golden_words) - min_match + 1):
        if all(golden_words[offset + j] == prefix[j] for j in range(min_match)):
            return offset
    return -1


# ---------------------------------------------------------------------------
# PRNG word extraction
# ---------------------------------------------------------------------------


def extract_prng_sequence(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
    start_idx: int = 0,
    state_column: Optional[str] = None,
    prng_column: Optional[str] = None,
    time_column: Optional[str] = None,
    min_state: int = PRNG_RUNNING_STATE,
) -> Tuple[List[int], List[int], List[Optional[int]]]:
    """Extract unique PRNG output words from ILA data.

    Handles:
    - ``ila_probe2_diag`` mux (state < 24 = AXI data, state >= 24 = prng_out)
    - Pipeline fill auto-skip (leading zero-valued words after state transition)
    - Duplicate removal (same value = same PRNG output cycle; FSM outputs each
      word for 2 ILA samples)

    Args:
        rows: Parsed CSV rows.
        fieldnames: CSV column names.
        start_idx: Row index to start extraction from.
        state_column: Column name for FSM state (auto-detected if None).
        prng_column: Column name for PRNG output (auto-detected if None).
        time_column: Column name for free-running timer (auto-detected if None).
        min_state: FSM state threshold for PRNG running.

    Returns:
        ``(indices, words, times)`` — three parallel lists.
        *indices*: raw CSV sample indices.
        *words*: unique PRNG output words (64-bit).
        *times*: time_cnt at each word (or None).
    """
    from .utils import parse_hex_any

    # Auto-detect columns
    if state_column is None:
        state_column = _find_col(fieldnames, "dbg_state")
    if prng_column is None:
        prng_column = _find_col(fieldnames, "ila_probe2_diag", "dbg_prng_out", "prng_out")
    if time_column is None:
        time_column = _find_col(fieldnames, "dbg_time")

    if prng_column is None:
        raise ValueError(
            "Cannot auto-detect PRNG output column. "
            "Available columns: " + ", ".join(fieldnames)
        )

    indices: List[int] = []
    words: List[int] = []
    times: List[Optional[int]] = []

    last_word: Optional[int] = None
    pipeline_fill_active = True  # True until first non-zero word after transition

    for i in range(start_idx, len(rows)):
        row = rows[i]

        # State filter
        if state_column is not None:
            fsm_state = parse_hex_any(row.get(state_column, "0"))
            if fsm_state < min_state:
                continue

        # Parse PRNG word
        cell = row.get(prng_column, "").strip()
        # Try as full 64-bit hex value
        try:
            if cell.lower().startswith("0x"):
                word = int(cell, 16)
            elif all(c in "0123456789abcdefABCDEF" for c in cell) and len(cell) >= 16:
                word = int(cell, 16)
            else:
                continue
        except (ValueError, AttributeError):
            continue

        # Pipeline fill auto-skip
        if pipeline_fill_active and min_state > 0:
            if word == 0:
                continue
            pipeline_fill_active = False

        # Deduplicate: same value = same PRNG output
        if word == last_word:
            continue

        # Read timestamp
        t: Optional[int] = None
        if time_column is not None:
            t_val = row.get(time_column, "").strip()
            try:
                t = int(t_val, 16) if t_val.lower().startswith("0x") else int(t_val)
            except (ValueError, AttributeError):
                t = None

        indices.append(i)
        words.append(word)
        times.append(t)
        last_word = word

    return indices, words, times


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare_sequences(
    golden: List[int],
    capture: List[int],
    capture_indices: Optional[List[int]] = None,
    capture_times: Optional[List[Optional[int]]] = None,
) -> Tuple[int, int, int, Optional[int], Optional[int], Optional[int], Optional[int], Optional[int]]:
    """Bit-exact comparison of two PRNG word sequences.

    Returns:
        ``(matched, mismatched, bit_errors, first_idx, first_golden, first_capture, first_raw_sample, first_time)``
    """
    total = min(len(golden), len(capture))
    matched = 0
    mismatched = 0
    bit_errors = 0
    first_idx: Optional[int] = None
    first_golden: Optional[int] = None
    first_capture: Optional[int] = None
    first_raw: Optional[int] = None
    first_time: Optional[int] = None

    for i in range(total):
        g = golden[i]
        c = capture[i]
        if g == c:
            matched += 1
        else:
            mismatched += 1
            bit_errors += bin(g ^ c).count('1')
            if first_idx is None:
                first_idx = i
                first_golden = g
                first_capture = c
                if capture_indices and i < len(capture_indices):
                    first_raw = capture_indices[i]
                if capture_times and i < len(capture_times):
                    first_time = capture_times[i]

    return (matched, mismatched, bit_errors, first_idx,
            first_golden, first_capture, first_raw, first_time)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_prng_check(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
    golden_words: List[int],
    *,
    align_mode: str = "cfg_done",
    warmup: int = 0,
    skip_capture: int = 0,
    max_words: Optional[int] = None,
) -> PrngCheckResult:
    """Run the complete PRNG verification pipeline.

    Stages:
        1. Align to FSM running state (cfg_done / dbg_state / first_word)
        2. Extract unique PRNG output words
        3. Skip warmup
        4. Compare against golden reference

    Args:
        rows: Parsed ILA CSV rows.
        fieldnames: CSV column names.
        golden_words: Golden reference words.
        align_mode: ``"cfg_done"`` | ``"dbg_state"`` | ``"first_word"``
        warmup: Number of initial words to skip before comparison.
        skip_capture: Additional capture samples to skip after alignment.
        max_words: Maximum number of words to compare.

    Returns:
        ``PrngCheckResult`` with full diagnostics.
    """
    result = PrngCheckResult(total_samples_parsed=len(rows))

    # ---- Stage 1: Align ----
    alignment: Optional[AlignmentInfo] = None

    if align_mode == "cfg_done":
        alignment = align_to_cfg_done(rows, fieldnames)
        if alignment is None:
            # Fall back to dbg_state
            result.warnings.append(
                "No cfg_done rising edge found; falling back to dbg_state alignment"
            )
            alignment = align_to_running_state(rows, fieldnames)

    elif align_mode == "dbg_state":
        alignment = align_to_running_state(rows, fieldnames)

    elif align_mode == "first_word":
        alignment = align_to_running_state(rows, fieldnames)
        if alignment is None:
            alignment = AlignmentInfo(
                trigger_sample=0, total_samples=len(rows),
                pre_trigger_samples=0, post_trigger_samples=len(rows),
                align_mode="first_word",
            )
            result.warnings.append(
                "No running state found; extracting from entire capture"
            )

    if alignment is None:
        result.errors.append(
            "Cannot align capture. No cfg_done edge and no dbg_state >= "
            f"{PRNG_RUNNING_STATE} found."
        )
        return result

    # ---- Stage 2: Extract PRNG sequence ----
    start_idx = alignment.trigger_sample + skip_capture
    try:
        indices, capture_words, capture_times = extract_prng_sequence(
            rows, fieldnames, start_idx=start_idx,
        )
    except ValueError as e:
        result.errors.append(str(e))
        return result

    if not capture_words:
        result.errors.append(
            f"No PRNG words extracted starting at sample {start_idx}. "
            f"Capture window may be entirely within startup gate (state 0)."
        )
        return result

    result.unique_words_extracted = len(capture_words)

    # ---- Stage 2b: Sliding-window alignment (first_word mode) ----
    if align_mode == "first_word":
        offset = align_by_sliding_window(capture_words, golden_words)
        if offset < 0:
            result.errors.append(
                "Could not align capture to golden via sliding window. "
                f"First 4 capture: {[f'0x{w:016x}' for w in capture_words[:4]]}"
            )
            return result
        result.warnings.append(
            f"Sliding-window: capture[0] matches golden[{offset}]"
        )
        golden_words = golden_words[offset:]
        alignment = AlignmentInfo(
            trigger_sample=indices[0] if indices else 0,
            total_samples=len(rows),
            pre_trigger_samples=indices[0] if indices else 0,
            post_trigger_samples=len(rows) - (indices[0] if indices else 0),
            align_mode="first_word",
        )

    result.alignment = alignment

    # ---- Stage 3: Skip warmup ----
    if warmup > 0:
        if warmup >= len(capture_words):
            result.errors.append(
                f"Warmup ({warmup}) exceeds extracted words ({len(capture_words)})"
            )
            return result
        capture_words = capture_words[warmup:]
        indices = indices[warmup:]
        capture_times = capture_times[warmup:]
        result.warmup_skipped = warmup

    # ---- Stage 4: Compare ----
    if max_words is not None:
        capture_words = capture_words[:max_words]
        indices = indices[:max_words]
        capture_times = capture_times[:max_words]

    (matched, mismatched, bit_errors, first_idx,
     first_golden, first_capture, first_raw, first_time) = \
        compare_sequences(golden_words, capture_words, indices, capture_times)

    result.total_compared = min(len(golden_words), len(capture_words))
    result.matched_words = matched
    result.mismatched_words = mismatched
    result.bit_errors = bit_errors
    result.ber = bit_errors / (result.total_compared * 64) if result.total_compared > 0 else 0.0
    result.first_mismatch_index = first_idx
    result.first_mismatch_golden = first_golden
    result.first_mismatch_capture = first_capture
    result.first_mismatch_raw_sample = first_raw
    result.first_mismatch_time_cnt = first_time
    result.passed = (mismatched == 0 and bit_errors == 0)

    if len(capture_words) < len(golden_words):
        result.warnings.append(
            f"Capture has fewer words ({len(capture_words)}) than golden "
            f"({len(golden_words)}). Only {len(capture_words)} compared."
        )

    return result
