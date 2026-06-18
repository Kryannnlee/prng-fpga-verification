#!/usr/bin/env python3
"""
FPGA PRNG-371 Validation Engine — Reset-Synchronized Capture Analysis

Parses Vivado ILA CSV captures, aligns to cfg_done trigger (reset-
synchronized), extracts PRNG output sequence, and compares against
the golden reference model.

Usage:
    # Basic: compare ILA capture against golden reference text file
    python validate_fpga_prng.py --capture ila_all_probes.csv --golden golden.txt

    # With warmup skip (default: 2000)
    python validate_fpga_prng.py --capture ila_all_probes.csv --golden golden.txt --warmup 2000

    # Generate golden on-the-fly (no golden file needed)
    python validate_fpga_prng.py --capture ila_all_probes.csv --generate-golden --num-words 1000

    # Full validation report (text + JSON)
    python validate_fpga_prng.py --capture ila_all_probes.csv --golden golden.txt --report report.md --json results.json

Architecture:
    CSV Parse → cfg_done Align → Extract & Dedup → Skip Warmup → Compare → Report
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


# ==========================================================================
# Constants
# ==========================================================================

HEX64_RE = re.compile(r"(?i)(?<![0-9a-f])(?:0x)?([0-9a-f]{16})(?![0-9a-f])")
DEFAULT_WARMUP = 2000
DEFAULT_SEEDS = (1000, 2000, 3000, 4000)


# ==========================================================================
# Exceptions
# ==========================================================================

class ValidationError(Exception):
    """User-facing validation error."""


# ==========================================================================
# Data Structures
# ==========================================================================

@dataclass
class AlignmentInfo:
    """Result of reset alignment (cfg_done, dbg_state, or sliding-window)."""
    trigger_sample: int           # Sample index of alignment point
    cfg_done_rising_edge: int     # Sample index of alignment point (legacy name)
    total_samples: int            # Total samples in capture
    pre_trigger_samples: int      # Samples before alignment point
    post_trigger_samples: int     # Samples after alignment point (inclusive)
    align_mode: str = "cfg_done"  # Which alignment strategy was used


@dataclass
class ValidationResult:
    """Complete validation result."""
    # Input info
    capture_path: str
    golden_source: str            # 'file' or 'generated'
    golden_path: Optional[str]
    # Alignment
    alignment: Optional[AlignmentInfo]
    # Extraction
    total_samples_parsed: int
    unique_prng_words_extracted: int
    warmup_skipped: int
    # Comparison
    total_compared: int
    matched_words: int
    mismatched_words: int
    bit_errors: int
    ber: float
    # Timestamp
    time_column_detected: Optional[str]     # detected dbg_time column name (None if unavailable)
    alignment_time_cnt: Optional[int]       # time_cnt at cfg_done rising edge (cycles from reset)
    # Diagnostics
    first_mismatch_index: Optional[int]     # absolute index (0 = first word after warmup)
    first_mismatch_golden: Optional[int]
    first_mismatch_capture: Optional[int]
    first_mismatch_capture_raw_idx: Optional[int]  # raw sample index in CSV
    first_mismatch_time_cnt: Optional[int]  # time_cnt at first mismatch (cycles from reset)
    # Status
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ==========================================================================
# CSV Parsing — Vivado ILA Format
# ==========================================================================

def parse_vivado_ila_csv(
    path: str,
) -> Tuple[List[str], List[Dict[str, str]]]:
    """
    Parse Vivado ILA CSV export.

    Vivado ILA CSV has a peculiar format:
      Row 0: Column headers (e.g., "dbg_state[4:0]")
      Row 1: Radix row (e.g., "Radix - UNSIGNED,UNSIGNED,HEX,...")
      Row 2+: Data rows

    Some columns (like dbg_axi_stat) contain string radix values like
    "WRDATA" instead of numbers. We preserve them as-is.

    Returns:
        (fieldnames, rows) where rows is a list of dicts.
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        raw = f.read()

    # Detect and skip Vivado radix row
    lines = raw.splitlines()
    if not lines:
        raise ValidationError(f"Empty CSV file: {path}")

    # Find the true header: first line that doesn't start with "Radix"
    header_idx = 0
    for i, line in enumerate(lines):
        if not line.strip().lower().startswith("radix"):
            header_idx = i
            break

    # Re-join remaining lines for standard CSV parsing
    clean_csv = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(clean_csv))

    if not reader.fieldnames:
        raise ValidationError(f"ILA CSV has no header row: {path}")

    fieldnames = [name.strip() for name in reader.fieldnames if name is not None]
    rows = list(reader)

    if not rows:
        raise ValidationError(f"ILA CSV has no data rows: {path}")

    return fieldnames, rows


def find_column(fieldnames: List[str], *candidates: str) -> Optional[str]:
    """
    Find a column name matching one of the candidates.
    Handles Vivado bracket notation (e.g., "dbg_state[4:0]" matches "dbg_state").
    """
    for candidate in candidates:
        # Exact match first
        if candidate in fieldnames:
            return candidate
        # Prefix match for bracket notation
        for name in fieldnames:
            if name.startswith(candidate) or candidate.startswith(name.split("[")[0]):
                return name
    return None


# ==========================================================================
# cfg_done Alignment
# ==========================================================================

def find_cfg_done_rising_edge(
    rows: List[Dict[str, str]],
    cfg_done_column: str,
) -> Optional[int]:
    """
    Find the first sample index where cfg_done transitions 0→1.

    Returns the sample index, or None if no rising edge found.
    """
    prev = "0"
    for i, row in enumerate(rows):
        val = row.get(cfg_done_column, "0").strip()
        # Normalize: "1", "01", "0x1" → all mean 1
        is_one = val in ("1", "01") or val.lower().startswith("0x1")
        is_zero = val in ("0", "00") or val.lower().startswith("0x0")

        cur = "1" if is_one else "0"
        if prev == "0" and cur == "1":
            return i
        prev = cur

    return None


def align_to_reset(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
) -> AlignmentInfo:
    """
    Find reset-synchronization point via cfg_done rising edge.
    """
    cfg_done_col = find_column(fieldnames, "cfg_done")
    if cfg_done_col is None:
        raise ValidationError(
            "cfg_done column not found in ILA CSV. "
            "Available columns: " + ", ".join(fieldnames) + "\n"
            "Make sure ILA probe4 (cfg_done) is connected and CSV was re-exported after rebuild."
        )

    trigger = find_cfg_done_rising_edge(rows, cfg_done_col)
    if trigger is None:
        raise ValidationError(
            "No cfg_done 0→1 transition found in capture data.\n"
            "Possible causes:\n"
            "  1. ILA was armed while cfg_done was already 1 (false trigger)\n"
            "  2. Reset button was not pressed after arming ILA\n"
            "  3. Trigger condition in ILA doesn't match cfg_done signal\n"
            "Remedy: Re-run capture with correct flow:\n"
            "  a) Program FPGA, wait for cfg_done to return to 0\n"
            "  b) Arm ILA (trigger on cfg_done == 1)\n"
            "  c) Press board reset button\n"
            "  d) Wait for ILA to trigger"
        )

    return AlignmentInfo(
        trigger_sample=trigger,
        cfg_done_rising_edge=trigger,
        total_samples=len(rows),
        pre_trigger_samples=trigger,
        post_trigger_samples=len(rows) - trigger,
        align_mode="cfg_done",
    )


# ==========================================================================
# dbg_state-based Alignment (for free-run captures without cfg_done edge)
# ==========================================================================

# The first PRNG output word after init is deterministic and can be used
# as an alignment anchor when the cfg_done edge is not in the capture window.
KNOWN_FIRST_WORD = 0xc661f290c4215270


def align_by_sliding_window(
    capture_words: List[int],
    golden_words: List[int],
    min_match: int = 4,
) -> int:
    """
    Find where the capture sequence appears in the golden sequence using
    a sliding-window match of the first `min_match` capture words.

    Returns the offset into golden_words where capture_words[0] matches,
    or -1 if no match is found.

    This is used when the cfg_done transition is not in the ILA capture
    window (e.g., free-run captures taken after the startup gate).
    """
    if len(capture_words) < min_match or len(golden_words) < min_match:
        return -1

    prefix = capture_words[:min_match]

    for offset in range(len(golden_words) - min_match + 1):
        if all(golden_words[offset + j] == prefix[j] for j in range(min_match)):
            return offset

    return -1


def align_to_running_state(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
) -> AlignmentInfo:
    """
    Find the first sample where dbg_state indicates the FSM has entered
    the running state (state >= 24), for captures without a cfg_done edge.

    This is a fallback alignment for free-run ILA captures.
    """
    state_col = find_column(fieldnames, "dbg_state")
    if state_col is None:
        raise ValidationError(
            "dbg_state column not found in ILA CSV. "
            "Available columns: " + ", ".join(fieldnames)
        )

    for i, row in enumerate(rows):
        raw = row.get(state_col, "").strip()
        try:
            state = int(raw, 16) if raw else 0
        except ValueError:
            continue

        # FSM state >= 25 means PRNG is running (state 24 = final AXI write of ctrl=1)
        if state >= 25:
            return AlignmentInfo(
                trigger_sample=i,
                cfg_done_rising_edge=i,
                total_samples=len(rows),
                pre_trigger_samples=i,
                post_trigger_samples=len(rows) - i,
                align_mode="dbg_state",
            )

    raise ValidationError(
        "No sample found with dbg_state >= 24. "
        "The entire capture window may be within the startup gate (state 0).\n"
        "Remedy: Wait 16s after programming before arming ILA, or use --align-mode first_word."
    )


# ==========================================================================
# PRNG Word Extraction
# ==========================================================================

def extract_prng_sequence(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
    start_idx: int = 0,
    valid_column: Optional[str] = None,
    prng_column: Optional[str] = None,
    time_column: Optional[str] = None,
    state_column: Optional[str] = None,
    min_state: Optional[int] = None,
) -> Tuple[List[Tuple[int, int, Optional[int]]], Optional[str]]:
    """
    Extract unique PRNG output words from ILA data starting at start_idx.

    The PRNG FSM toggles state1↔state2. Output updates in state2 only.
    This means each unique 64-bit output value persists for 2 ILA samples.
    We deduplicate by only recording when the value changes.

    Args:
        rows: Parsed CSV rows
        fieldnames: CSV column names
        start_idx: Row index to start extraction from (after alignment)
        valid_column: Column name for prng_out_valid signal
        prng_column: Column name for prng_out value
        time_column: Column name for free-running timestamp counter
        state_column: Column name for FSM state (dbg_state). If provided,
            only rows where state >= min_state are considered as PRNG output.
            This handles the ila_probe2_diag mux: state<24 = AXI data, state>=24 = prng_out.
        min_state: Minimum FSM state that indicates PRNG is running (default: 24).

    Returns:
        Tuple of (word_list, detected_time_column_name_or_None)
        word_list: List of (sample_index, prng_word, time_cnt) tuples, deduplicated.
        time_cnt is None if time_column not found.
    """
    # Auto-detect columns
    if valid_column is None:
        valid_column = find_column(fieldnames, "prng_led_valid", "led_valid",
                                   "prng_out_valid", "dbg_out_valid")
    if prng_column is None:
        prng_column = find_column(fieldnames, "dbg_prng_out", "prng_out",
                                  "dbg_prng_data", "ila_probe2_diag")
    if time_column is None:
        time_column = find_column(fieldnames, "dbg_time")
    if state_column is None:
        state_column = find_column(fieldnames, "dbg_state")
    if min_state is None:
        min_state = 25  # FSM state >= 25 → PRNG running (state 24 = final AXI write, not PRNG output)

    if prng_column is None:
        raise ValidationError(
            "Cannot auto-detect PRNG output column. Available columns: "
            + ", ".join(fieldnames)
        )

    words: List[Tuple[int, int, Optional[int]]] = []
    last_word: Optional[int] = None
    skipped_invalid = 0
    skipped_duplicate = 0
    skipped_state = 0
    pipeline_fill_skip = 0
    pipeline_fill_active = True  # True until first non-zero PRNG word seen after state transition

    def _parse_time(raw: str) -> Optional[int]:
        """Parse time_cnt value — hex (0x...) or decimal."""
        raw = raw.strip()
        if not raw:
            return None
        try:
            if raw.lower().startswith("0x"):
                return int(raw, 16)
            return int(raw)
        except ValueError:
            return None

    def _parse_state(raw: str) -> int:
        """Parse FSM state — hex or decimal."""
        raw = raw.strip()
        if not raw:
            return 0
        try:
            return int(raw, 16)
        except ValueError:
            return 0

    for i in range(start_idx, len(rows)):
        row = rows[i]

        # Filter by FSM state if state column is available.
        # ila_probe2_diag is a mux: state<24 = {wdata, awaddr}, state>=24 = prng_out.
        if state_column is not None:
            fsm_state = _parse_state(row.get(state_column, "0"))
            if fsm_state < min_state:
                skipped_state += 1
                continue

        # Check valid flag if column exists
        if valid_column is not None:
            valid_val = row.get(valid_column, "1").strip()
            is_valid = valid_val in ("1", "01") or valid_val.lower() in ("0x1", "true")
            if not is_valid:
                skipped_invalid += 1
                continue

        # Parse PRNG word
        cell = row.get(prng_column, "").strip()
        match = HEX64_RE.search(cell)
        if not match:
            continue

        word = int(match.group(1), 16)

        # Auto-skip pipeline fill: after state transition to >= min_state,
        # the first 1-2 samples may show 0x0000 while the PRNG pipeline fills.
        # Skip leading zero-valued words until the first non-zero PRNG output.
        if pipeline_fill_active and min_state is not None and min_state > 0:
            if word == 0:
                pipeline_fill_skip += 1
                continue
            pipeline_fill_active = False

        # Deduplicate: only record when value changes
        if word == last_word:
            skipped_duplicate += 1
            continue

        # Read timestamp if available
        t = None
        if time_column is not None:
            t = _parse_time(row.get(time_column, ""))

        words.append((i, word, t))
        last_word = word

    return words, time_column


# ==========================================================================
# Golden Reference
# ==========================================================================

def load_golden_text(path: str) -> List[int]:
    """Load golden reference from a plain hex-text file."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        words: List[int] = []
        for line in f:
            for match in HEX64_RE.finditer(line):
                words.append(int(match.group(1), 16))
    if not words:
        raise ValidationError(f"No 64-bit hex words found in golden file: {path}")
    return words


def generate_golden_live(
    num_words: int,
    seeds: Tuple[int, int, int, int] = DEFAULT_SEEDS,
    warmup: int = DEFAULT_WARMUP,
) -> List[int]:
    """
    Generate golden reference using the C golden model (.so/.dll),
    with automatic fallback to the pure-Python GoldenModelState.
    """
    # Try C library first
    script_dir = os.path.dirname(os.path.abspath(__file__))
    golden_dir = os.path.join(script_dir, "..", "..", "hw", "src_371")
    golden_dir = os.path.normpath(golden_dir)

    if golden_dir not in sys.path:
        sys.path.insert(0, golden_dir)

    try:
        from golden_prng import ChaosPRNG371
        prng = ChaosPRNG371(init_x=seeds, warmup=warmup)
        return prng.generate(num_words)
    except (ImportError, OSError):
        pass  # Fall through to Python model

    # Fallback: pure-Python golden model (rtl_cycle_model.py)
    try:
        from rtl_cycle_model import GoldenModelState
    except ImportError:
        raise ValidationError(
            "Cannot import golden_prng module or rtl_cycle_model.\n"
            f"C library location: {golden_dir}\n"
            "Make sure golden_371.so (or .dll) is compiled, or that "
            "rtl_cycle_model.py is in the same directory as this script.\n"
            "Alternative: use --golden to specify a pre-computed golden text file."
        )

    gm = GoldenModelState(seeds=seeds)
    # Generate warmup + requested words
    total = warmup + num_words
    all_words = gm.run(total)
    return all_words[warmup:]


# ==========================================================================
# Comparison
# ==========================================================================

def compare_sequences(
    golden: List[int],
    capture: List[int],
    capture_indices: List[int],
    capture_times: List[Optional[int]],
) -> Tuple[int, int, int, Optional[int], Optional[int], Optional[int], Optional[int], Optional[int]]:
    """
    Bit-exact comparison of two PRNG word sequences.

    Returns:
        (matched, mismatched, bit_errors, first_mismatch_idx,
         first_mismatch_golden, first_mismatch_capture,
         first_mismatch_capture_raw_idx, first_mismatch_time_cnt)
    """
    total = min(len(golden), len(capture))
    matched = 0
    mismatched = 0
    bit_errors = 0
    first_idx: Optional[int] = None
    first_golden: Optional[int] = None
    first_capture: Optional[int] = None
    first_raw_idx: Optional[int] = None
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
                first_raw_idx = capture_indices[i] if i < len(capture_indices) else None
                first_time = capture_times[i] if i < len(capture_times) else None

    return (matched, mismatched, bit_errors, first_idx,
            first_golden, first_capture, first_raw_idx, first_time)


# ==========================================================================
# Report Generation
# ==========================================================================

def fmt_hex(word: Optional[int]) -> str:
    return "N/A" if word is None else f"0x{word:016x}"


def render_text_report(result: ValidationResult) -> str:
    """Render a human-readable Markdown validation report."""
    align = result.alignment
    lines = [
        "# PRNG-371 FPGA Validation Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Status:** {'[PASS]' if result.passed else '[FAIL]'}",
        "",
        "## Input",
        "",
        f"- Capture file: `{result.capture_path}`",
        f"- Golden source: {result.golden_source}",
    ]
    if result.golden_path:
        lines.append(f"- Golden file: `{result.golden_path}`")

    lines.extend([
        "",
        f"## Alignment (mode: {align.align_mode if align else 'none'})",
        "",
    ])
    if align:
        mode_label = {
            "cfg_done": "cfg_done rising edge",
            "dbg_state": "FSM state >= 24 (running)",
            "first_word": "Sliding-window match against golden",
        }.get(align.align_mode, "alignment point")
        lines.extend([
            f"- {mode_label} at sample: {align.trigger_sample}",
            f"- Total samples in capture: {align.total_samples}",
            f"- Samples before alignment: {align.pre_trigger_samples}",
            f"- Samples after alignment: {align.post_trigger_samples}",
        ])
    else:
        lines.append("- No cfg_done alignment performed (trigger column not found)")

    if result.time_column_detected:
        lines.append(f"- Time column detected: `{result.time_column_detected}`")
        if result.alignment_time_cnt is not None:
            lines.append(f"- **Time at cfg_done rising edge: {result.alignment_time_cnt} cycles from reset**")
            lines.append(f"  ({result.alignment_time_cnt * 20 / 1000:.2f} us at 50 MHz)")
    else:
        lines.append("- No time counter column detected (dbg_time not found in CSV)")

    lines.extend([
        "",
        "## Extraction",
        "",
        f"- Total ILA samples parsed: {result.total_samples_parsed}",
        f"- Unique PRNG words extracted: {result.unique_prng_words_extracted}",
        f"- Warmup words skipped: {result.warmup_skipped}",
        "",
        "## Comparison",
        "",
        f"- Total words compared: {result.total_compared}",
        f"- Matched: {result.matched_words}",
        f"- Mismatched: {result.mismatched_words}",
    ])

    if result.mismatched_words > 0:
        match_pct = 100 * result.matched_words / result.total_compared if result.total_compared else 0
        lines.extend([
            f"- Match rate: {match_pct:.4f}%",
            f"- Bit errors: {result.bit_errors}",
            f"- Bit Error Rate (BER): {result.ber:.12g}",
            f"- **First mismatch at word index {result.first_mismatch_index}:**",
            f"  - Golden:  {fmt_hex(result.first_mismatch_golden)}",
            f"  - Capture: {fmt_hex(result.first_mismatch_capture)}",
        ])
        if result.first_mismatch_capture_raw_idx is not None:
            lines.append(f"  - Raw CSV sample: {result.first_mismatch_capture_raw_idx}")
        if result.first_mismatch_time_cnt is not None:
            lines.append(f"  - **Time at mismatch: {result.first_mismatch_time_cnt} cycles from reset**")

    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        for w in result.warnings:
            lines.append(f"- WARNING: {w}")

    if result.errors:
        lines.extend(["", "## Errors", ""])
        for e in result.errors:
            lines.append(f"- ERROR: {e}")

    lines.append("")
    return "\n".join(lines)


def render_json_report(result: ValidationResult) -> str:
    """Render a machine-readable JSON validation report."""
    data = {
        "status": "PASS" if result.passed else "FAIL",
        "capture_path": result.capture_path,
        "golden_source": result.golden_source,
        "golden_path": result.golden_path,
        "alignment": None,
        "timestamp": {
            "column_detected": result.time_column_detected,
            "alignment_time_cnt": result.alignment_time_cnt,
            "alignment_time_us": (
                result.alignment_time_cnt * 20 / 1000
                if result.alignment_time_cnt is not None else None
            ),
        },
        "extraction": {
            "total_samples_parsed": result.total_samples_parsed,
            "unique_prng_words_extracted": result.unique_prng_words_extracted,
            "warmup_skipped": result.warmup_skipped,
        },
        "comparison": {
            "total_compared": result.total_compared,
            "matched_words": result.matched_words,
            "mismatched_words": result.mismatched_words,
            "bit_errors": result.bit_errors,
            "ber": result.ber,
        },
        "first_mismatch": None,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    if result.alignment:
        data["alignment"] = asdict(result.alignment)
    if result.first_mismatch_index is not None:
        data["first_mismatch"] = {
            "word_index": result.first_mismatch_index,
            "golden": fmt_hex(result.first_mismatch_golden),
            "capture": fmt_hex(result.first_mismatch_capture),
            "raw_csv_sample": result.first_mismatch_capture_raw_idx,
            "time_cnt": result.first_mismatch_time_cnt,
        }
    return json.dumps(data, indent=2, ensure_ascii=False)


# ==========================================================================
# Validation Pipeline
# ==========================================================================

def run_validation(
    capture_path: str,
    golden_path: Optional[str] = None,
    generate_golden: bool = False,
    num_golden_words: int = 1000,
    golden_seeds: Tuple[int, int, int, int] = DEFAULT_SEEDS,
    warmup: int = DEFAULT_WARMUP,
    skip_capture: int = 0,
    max_words: Optional[int] = None,
    valid_column: Optional[str] = None,
    prng_column: Optional[str] = None,
    align_mode: str = "cfg_done",
) -> ValidationResult:
    """
    Run the complete FPGA PRNG validation pipeline.

    Pipeline stages:
    1. Parse Vivado ILA CSV
    2. Align to reset via cfg_done trigger (or fallback mode)
    3. Extract unique PRNG output words (deduplicating 2x samples)
    4. Skip warmup iterations
    5. Compare with golden reference
    6. Generate report

    Args:
        align_mode: Alignment strategy:
            - "cfg_done": Find cfg_done 0→1 transition (default, needs trigger-capable ILA)
            - "dbg_state": Find first sample where dbg_state >= 24 (for free-run captures)
            - "first_word": Extract all PRNG words, then sliding-window match against golden
    """
    warnings: List[str] = []
    errors: List[str] = []

    # ---- Stage 1: Parse CSV ----
    fieldnames, rows = parse_vivado_ila_csv(capture_path)

    # ---- Stage 2: Align to reset ----
    if align_mode == "dbg_state":
        alignment = align_to_running_state(rows, fieldnames)
    elif align_mode == "first_word":
        # For first_word mode, we extract words from the entire capture first,
        # then align by matching against golden. We still need a coarse alignment
        # to know where to start extracting PRNG words.
        # Use dbg_state alignment as the coarse filter, but don't error if not found.
        try:
            alignment = align_to_running_state(rows, fieldnames)
        except ValidationError:
            # No running state found — try extracting from the entire capture
            alignment = AlignmentInfo(
                trigger_sample=0,
                cfg_done_rising_edge=0,
                total_samples=len(rows),
                pre_trigger_samples=0,
                post_trigger_samples=len(rows),
                align_mode="first_word",
            )
            warnings.append(
                "No running state (dbg_state >= 24) found in capture. "
                "Extracting PRNG words from entire capture — may include AXI write data. "
                "First-word alignment will filter out non-PRNG values."
            )
    else:
        # Default: cfg_done mode
        alignment = align_to_reset(rows, fieldnames)

    # ---- Stage 3: Extract PRNG sequence ----
    start_idx = alignment.trigger_sample + skip_capture
    prng_words, detected_time_col = extract_prng_sequence(
        rows, fieldnames, start_idx=start_idx,
        valid_column=valid_column, prng_column=prng_column,
    )

    if not prng_words:
        # If no words found with state filtering, try without state filter
        if align_mode == "first_word":
            warnings.append(
                "No PRNG words found with state filter. Trying without state filter..."
            )
            prng_words, detected_time_col = extract_prng_sequence(
                rows, fieldnames, start_idx=start_idx,
                valid_column=valid_column, prng_column=prng_column,
                min_state=0,  # Disable state filter
            )

        if not prng_words:
            raise ValidationError(
                f"No valid PRNG words extracted from capture starting at sample {start_idx}.\n"
                f"Check that the capture window includes PRNG output after the trigger point.\n"
                f"Total samples: {len(rows)}, start index: {start_idx}"
            )

    # Separate indices, values, and timestamps
    raw_indices = [idx for idx, _, _ in prng_words]
    capture_words = [word for _, word, _ in prng_words]
    capture_times = [t for _, _, t in prng_words]

    # Timestamp at alignment point (first extracted word)
    alignment_time = capture_times[0] if capture_times else None

    # ---- Stage 4: Golden reference ----
    if generate_golden:
        golden_source = "generated"
        golden_path_used = None
        # For sliding window alignment, generate extra golden words to search within
        golden_gen_count = len(capture_words) if max_words is None else max_words
        if align_mode == "first_word":
            golden_gen_count += 500  # Extra headroom for alignment search
        golden_words = generate_golden_live(
            num_words=golden_gen_count,
            seeds=golden_seeds,
            warmup=warmup,
        )
    elif golden_path:
        golden_source = "file"
        golden_path_used = golden_path
        golden_words = load_golden_text(golden_path)
    else:
        raise ValidationError(
            "Either --golden <file> or --generate-golden must be specified."
        )

    # ---- Stage 4b: First-word sliding window alignment ----
    if align_mode == "first_word":
        offset = align_by_sliding_window(capture_words, golden_words)
        if offset < 0:
            raise ValidationError(
                f"Could not align capture to golden reference using sliding window.\n"
                f"First 4 capture words: {[f'0x{w:016x}' for w in capture_words[:4]]}\n"
                f"First 4 golden words:  {[f'0x{w:016x}' for w in golden_words[:4]]}\n"
                f"Check that the capture contains actual PRNG output (not AXI write data)."
            )
        warnings.append(
            f"Sliding-window alignment: capture[0] matches golden[{offset}]. "
            f"Skipping {offset} golden words before comparison."
        )
        golden_words = golden_words[offset:]
        # Recalculate alignment info
        alignment = AlignmentInfo(
            trigger_sample=raw_indices[0] if raw_indices else 0,
            cfg_done_rising_edge=raw_indices[0] if raw_indices else 0,
            total_samples=len(rows),
            pre_trigger_samples=raw_indices[0] if raw_indices else 0,
            post_trigger_samples=len(rows) - (raw_indices[0] if raw_indices else 0),
            align_mode="first_word",
        )

    # ---- Stage 5: Skip warmup ----
    if warmup > 0:
        if warmup >= len(capture_words):
            raise ValidationError(
                f"Warmup ({warmup}) exceeds extracted unique word count "
                f"({len(capture_words)}).\n"
                f"Either the capture window is too small or warmup value is too large."
            )
        capture_words = capture_words[warmup:]
        raw_indices = raw_indices[warmup:]
        capture_times = capture_times[warmup:]

    if max_words is not None:
        capture_words = capture_words[:max_words]
        raw_indices = raw_indices[:max_words]
        capture_times = capture_times[:max_words]

    # ---- Stage 6: Compare ----
    (matched, mismatched, bit_errors, first_idx,
     first_golden, first_capture, first_raw, first_time) = compare_sequences(
        golden_words, capture_words, raw_indices, capture_times
    )

    total_compared = min(len(golden_words), len(capture_words))
    ber = bit_errors / (total_compared * 64) if total_compared > 0 else 0.0

    if len(capture_words) < len(golden_words):
        warnings.append(
            f"Capture has fewer words ({len(capture_words)}) than golden "
            f"({len(golden_words)}). Only {len(capture_words)} words compared."
        )
    elif len(capture_words) > len(golden_words):
        warnings.append(
            f"Capture has more words ({len(capture_words)}) than golden "
            f"({len(golden_words)}). Extra capture words ignored."
        )

    return ValidationResult(
        capture_path=capture_path,
        golden_source=golden_source,
        golden_path=golden_path_used,
        alignment=alignment,
        total_samples_parsed=len(rows),
        unique_prng_words_extracted=len(prng_words),
        warmup_skipped=warmup,
        total_compared=total_compared,
        matched_words=matched,
        mismatched_words=mismatched,
        bit_errors=bit_errors,
        ber=ber,
        time_column_detected=detected_time_col,
        alignment_time_cnt=alignment_time,
        first_mismatch_index=first_idx,
        first_mismatch_golden=first_golden,
        first_mismatch_capture=first_capture,
        first_mismatch_capture_raw_idx=first_raw,
        first_mismatch_time_cnt=first_time,
        passed=(mismatched == 0 and bit_errors == 0),
        warnings=warnings,
        errors=errors,
    )


# ==========================================================================
# CLI
# ==========================================================================

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FPGA PRNG-371 Validation Engine — Reset-synchronized capture analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compare ILA capture against golden text file (2000 warmup)
  python validate_fpga_prng.py --capture ila_capture.csv --golden golden_ref.txt

  # Generate golden on-the-fly (no golden file needed)
  python validate_fpga_prng.py --capture ila_capture.csv --generate-golden --num-words 1000

  # Full report output
  python validate_fpga_prng.py --capture ila_capture.csv --golden golden.txt \\
      --report validation_report.md --json validation_results.json

  # Custom seeds and warmup
  python validate_fpga_prng.py --capture ila_capture.csv --generate-golden \\
      --seed 1000 2000 3000 4000 --warmup 0 --num-words 500
        """,
    )
    parser.add_argument(
        "--capture", required=True,
        help="Path to Vivado ILA CSV capture file.",
    )
    parser.add_argument(
        "--golden", default=None,
        help="Path to golden reference text file (one 64-bit hex word per line).",
    )
    parser.add_argument(
        "--generate-golden", action="store_true",
        help="Generate golden reference on-the-fly using the C golden model.",
    )
    parser.add_argument(
        "--num-words", type=int, default=1000,
        help="Number of golden words to generate (only with --generate-golden). Default: 1000",
    )
    parser.add_argument(
        "--seed", nargs=4, type=int, default=list(DEFAULT_SEEDS),
        metavar=("X1", "X2", "X3", "X4"),
        help=f"Golden PRNG seeds. Default: {DEFAULT_SEEDS}",
    )
    parser.add_argument(
        "--warmup", type=int, default=DEFAULT_WARMUP,
        help=f"Number of warmup iterations to skip. Default: {DEFAULT_WARMUP}",
    )
    parser.add_argument(
        "--skip-capture", type=int, default=0,
        help="Additional capture samples to skip after the trigger point.",
    )
    parser.add_argument(
        "--max-words", type=int, default=None,
        help="Maximum number of words to compare.",
    )
    parser.add_argument(
        "--align-mode", default="cfg_done",
        choices=["cfg_done", "dbg_state", "first_word"],
        help="Alignment strategy. 'cfg_done' (default) finds cfg_done 0→1 edge. "
             "'dbg_state' finds first sample with FSM state >= 24. "
             "'first_word' uses sliding-window match against golden reference "
             "(most robust for free-run captures).",
    )
    parser.add_argument(
        "--valid-column", default=None,
        help="CSV column name for PRNG valid signal (auto-detected if not specified).",
    )
    parser.add_argument(
        "--prng-column", default=None,
        help="CSV column name for PRNG output value (auto-detected if not specified).",
    )
    parser.add_argument(
        "--report", default=None,
        help="Path to write Markdown validation report.",
    )
    parser.add_argument(
        "--json-report", "--json", default=None,
        help="Path to write JSON validation report.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress output except errors.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit:
        return 2

    # Validate mutually exclusive golden options
    if not args.golden and not args.generate_golden:
        print("ERROR: Either --golden <file> or --generate-golden must be specified.",
              file=sys.stderr)
        return 2
    if args.golden and args.generate_golden:
        print("ERROR: --golden and --generate-golden are mutually exclusive.",
              file=sys.stderr)
        return 2

    # Validate files exist
    if not os.path.exists(args.capture):
        print(f"ERROR: Capture file not found: {args.capture}", file=sys.stderr)
        return 2
    if args.golden and not os.path.exists(args.golden):
        print(f"ERROR: Golden file not found: {args.golden}", file=sys.stderr)
        return 2

    try:
        result = run_validation(
            capture_path=args.capture,
            golden_path=args.golden,
            generate_golden=args.generate_golden,
            num_golden_words=args.num_words,
            golden_seeds=tuple(args.seed),
            warmup=args.warmup,
            skip_capture=args.skip_capture,
            max_words=args.max_words,
            valid_column=args.valid_column,
            prng_column=args.prng_column,
            align_mode=args.align_mode,
        )
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: File I/O failed: {exc}", file=sys.stderr)
        return 2

    # Text report
    text_report = render_text_report(result)
    if not args.quiet:
        print(text_report)

    # Write report files
    if args.report:
        report_dir = os.path.dirname(args.report)
        if report_dir:
            os.makedirs(report_dir, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(text_report)
        if not args.quiet:
            print(f"Report written to: {args.report}")

    if args.json_report:
        json_dir = os.path.dirname(args.json_report)
        if json_dir:
            os.makedirs(json_dir, exist_ok=True)
        json_text = render_json_report(result)
        with open(args.json_report, "w", encoding="utf-8") as f:
            f.write(json_text)
        if not args.quiet:
            print(f"JSON report written to: {args.json_report}")

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
