#!/usr/bin/env python3
# ==========================================================================
# utils.py — Shared utilities for PRNG-371 FPGA verification framework
# ==========================================================================
# Provides:
#   - force_utf8()           : Windows-safe UTF-8 encoding for all I/O
#   - parse_hex64(text)      : regex extraction of 64-bit hex values
#   - parse_hex_any(text)    : robust hex/int/dec parser
#   - find_column()          : column name mapping (not index-based)
#   - WRITE_MARK / FAIL_MARK : ASCII-only status markers (no emoji)
# ==========================================================================

from __future__ import annotations

import re
import sys
from typing import List, Optional

# ---------------------------------------------------------------------------
# Status markers — ASCII only, no emoji
# ---------------------------------------------------------------------------

PASS_MARK = "PASS"
FAIL_MARK = "FAIL"

# ---------------------------------------------------------------------------
# Hex parsing
# ---------------------------------------------------------------------------

# Matches exactly 16 hex digits (64-bit), optionally prefixed with 0x
HEX64_RE = re.compile(r"(?i)(?<![0-9a-f])(?:0x)?([0-9a-f]{16})(?![0-9a-f])")

# Matches hex values of any width, optionally with 0x prefix
HEX_ANY_RE = re.compile(r"(?i)(?:0x)?([0-9a-f]+)")


def parse_hex64(text: str) -> Optional[int]:
    """Extract the first 64-bit hex word from a text cell.
    Returns None if no match found.
    """
    m = HEX64_RE.search(text.strip())
    return int(m.group(1), 16) if m else None


def parse_hex_any(text: str) -> int:
    """Parse a hex (0x...) or decimal value. Returns 0 on failure."""
    text = text.strip()
    if not text:
        return 0
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        # Try hex first (most common in ILA CSV), then decimal
        if all(c in "0123456789abcdefABCDEF" for c in text):
            return int(text, 16)
        return int(text)
    except (ValueError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# Column name mapping
# ---------------------------------------------------------------------------


def find_column(fieldnames: List[str], *candidates: str) -> Optional[str]:
    """Find a CSV column by name, supporting Vivado bracket notation.

    Vivado ILA exports column names like ``dbg_state[4:0]`` or
    ``ila_probe2_diag[63:0]``. This function matches either the exact
    name or the base name (text before ``[``).

    Example:
        >>> find_column(["dbg_state[4:0]", "sample"], "dbg_state")
        'dbg_state[4:0]'
    """
    for candidate in candidates:
        # Exact match first
        if candidate in fieldnames:
            return candidate
        # Prefix/bracket match
        base = candidate.split("[")[0]
        for name in fieldnames:
            name_base = name.split("[")[0]
            if name_base == base:
                return name
    return None


# ---------------------------------------------------------------------------
# Windows encoding safety
# ---------------------------------------------------------------------------


def force_utf8():
    """Reconfigure stdio for UTF-8 on Windows.

    Call this at the top of every entry-point script to avoid
    'UnicodeEncodeError' / GBK codec issues on Chinese Windows.
    """
    if sys.platform == "win32":
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except OSError:
            pass  # best-effort; non-console stdio may not support reconfigure


# ---------------------------------------------------------------------------
# File I/O helper
# ---------------------------------------------------------------------------


def read_text_file(path: str) -> str:
    """Read a text file with automatic encoding detection (UTF-8 first, then fallback)."""
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Last resort
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()
