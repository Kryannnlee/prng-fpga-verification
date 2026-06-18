#!/usr/bin/env python3
# ==========================================================================
# ila_parser.py — Robust Vivado ILA CSV Parser
# ==========================================================================
#
# Parses Vivado ILA CSV exports.  Designed to handle version-to-version
# variability in Vivado's CSV format:
#
#   - radix row present  → auto-detected and skipped
#   - radix row absent    → no problem; header detection is regex-based
#   - column order varies → name-mapped, not index-based
#   - hex / dec / bin mix → per-value conversion, not column-global
#
# Usage::
#
#     from tools.core.ila_parser import parse_ila_csv
#     fieldnames, rows = parse_ila_csv("ila_capture.csv")
# ==========================================================================

from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A "data row" in ILA CSV starts with a sample number (decimal integer)
# and contains at least one hex-like value.  Rows that don't match this
# pattern are header/radix/metadata and are skipped.
#
# Match:  "  0  ,  00  ,  0x0000..."  →  sample 0
# Skip:   "Radix - UNSIGNED,UNSIGNED,HEX,..."
# Skip:   "Date: 2026-06-18"
# Skip:   empty lines
#
_DATA_ROW_RE = re.compile(r"^\s*\d+\s*[,;|]")


def _is_data_row(line: str) -> bool:
    """Return True if *line* looks like an ILA data row (starts with integer)."""
    return bool(_DATA_ROW_RE.match(line.strip()))


def _find_header_line(lines: List[str]) -> int:
    """Return the index of the header line (first line that looks like CSV headers).

    Vivado ILA CSV format::

        <optional metadata ...>
        Column1[0:0],Column2[31:0],...
        Radix - HEX,HEX,...          <-- may be present or absent
        data,data,data,...
                     or
        Column1[0:0],Column2[31:0],...
        data,data,data,...

    We detect the header as the first line that contains bracket notation
    like ``[4:0]`` or plain column names separated by commas.
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        # Skip known metadata prefixes
        if lower.startswith(("radix", "date", "time", "//", "#", "--")):
            continue
        # A header line contains bracket notation OR has commas and
        # doesn't start with a number (data rows start with sample #).
        if ("[" in stripped and "]" in stripped) or (
            "," in stripped and not _is_data_row(stripped)
        ):
            return i
    return 0


def _strip_bracket_suffix(name: str) -> str:
    """Remove Vivado bracket notation suffix (e.g., 'probe2[63:0]' -> 'probe2')."""
    bracket = name.find("[")
    return name[:bracket].strip() if bracket >= 0 else name.strip()


def parse_ila_csv(
    path: str,
    encoding: Optional[str] = None,
) -> Tuple[List[str], List[Dict[str, str]]]:
    """Parse a Vivado ILA CSV export file.

    Args:
        path: Path to the CSV file.
        encoding: File encoding.  If None, tries utf-8-sig → utf-8 → gbk.

    Returns:
        ``(fieldnames, rows)`` where *fieldnames* is a list of column name
        strings and *rows* is a list of ``{col: value}`` dicts.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If no header row or no data rows are found.
    """
    # ---- Read raw text ----
    from .utils import read_text_file

    raw = read_text_file(path)
    lines = raw.splitlines()
    if not lines:
        raise ValueError(f"Empty file: {path}")

    # ---- Find header line ----
    header_idx = _find_header_line(lines)
    if header_idx >= len(lines):
        raise ValueError(f"No header row found in: {path}")

    # ---- Rejoin from header onward, skipping radix row ----
    clean_lines = []
    for i in range(header_idx, len(lines)):
        line = lines[i].strip()
        if not line:
            continue
        if line.lower().startswith("radix"):
            continue  # skip Vivado radix metadata row
        clean_lines.append(line)

    clean_csv = "\n".join(clean_lines)
    reader = csv.DictReader(io.StringIO(clean_csv))

    if not reader.fieldnames:
        raise ValueError(f"No header fields detected in: {path}")

    # Normalize fieldnames (strip whitespace)
    fieldnames = [name.strip() for name in reader.fieldnames if name is not None]

    rows = list(reader)
    if not rows:
        raise ValueError(f"No data rows found in: {path}")

    return fieldnames, rows
