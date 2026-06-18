#!/usr/bin/env python3
# ==========================================================================
# axi_checker.py — AXI-Lite Write Transaction Verifier
# ==========================================================================
#
# Decodes the ila_probe2_diag mux during state < 24 to verify each
# AXI-Lite write transaction (address, data, expected value).
#
# Mux encoding (state < 24):
#   probe2[63:32] = wdata[31:0]
#   probe2[31:26] = awaddr[5:0]
#   probe2[25:0]  = 0
#
# Usage::
#
#     from tools.core.axi_checker import check_axi_writes
#     report = check_axi_writes(rows, fieldnames)
#     print(report.summary())
# ==========================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class AxiWrite:
    """A single decoded AXI-Lite write transaction."""
    sample: int
    state: int
    awaddr: int          # byte address (0x00 – 0x30)
    wdata: int            # 32-bit write data


@dataclass
class AxiReport:
    """Complete AXI-Lite write verification report."""
    writes: List[AxiWrite] = field(default_factory=list)
    expected_count: int = 6
    all_match: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return self.expected_count - len(self.errors) if self.all_match else \
            sum(1 for w in self.writes if not self._is_unexpected(w))

    def _is_unexpected(self, w: AxiWrite) -> bool:
        return False  # handled per-transaction

    def summary(self) -> str:
        status = "PASS" if self.all_match else "FAIL"
        lines = [
            f"AXI-Lite Write Verification: {status}",
            f"  Expected: {self.expected_count} writes",
            f"  Captured: {len(self.writes)} writes",
            f"  Matched:  {self.matched_count}/{self.expected_count}",
        ]
        if self.errors:
            for e in self.errors:
                lines.append(f"  ERROR: {e}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Expected write sequence (order matters)
# ---------------------------------------------------------------------------

EXPECTED_WRITES = [
    (0x10, 0x00000001, "ctrl = 1"),
    (0x18, 0x000003E8, "init_x1 = 1000"),
    (0x20, 0x000007D0, "init_x2 = 2000"),
    (0x28, 0x00000BB8, "init_x3 = 3000"),
    (0x30, 0x00000FA0, "init_x4 = 4000"),
    (0x00, 0x00000081, "ap_start=1 + auto_restart=1"),
]

# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------


def _find_col(fieldnames: List[str], *names: str) -> Optional[str]:
    """Find a column by candidate base names (case-insensitive)."""
    for name in names:
        name_lower = name.lower()
        for fn in fieldnames:
            fn_lower = fn.lower()
            # Exact or bracket-prefix match
            if fn_lower == name_lower:
                return fn
            base = fn_lower.split("[")[0]
            if base == name_lower:
                return fn
    return None


# ---------------------------------------------------------------------------
# Main verification function
# ---------------------------------------------------------------------------


def check_axi_writes(
    rows: List[Dict[str, str]],
    fieldnames: List[str],
    max_state: int = 24,
) -> AxiReport:
    """Verify AXI-Lite write transactions in an ILA capture.

    Args:
        rows: Parsed ILA CSV rows (list of dicts).
        fieldnames: CSV column names.
        max_state: FSM states below this are decoded as AXI writes
                   (default 24 = prng_wrapper.v writes ctrl/seeds/ap_start).

    Returns:
        ``AxiReport`` with decoded writes and match status.
    """
    from .utils import parse_hex_any

    state_col = _find_col(fieldnames, "dbg_state", "state")
    probe2_col = _find_col(fieldnames, "ila_probe2_diag", "probe2", "dbg_prng_out")

    if state_col is None:
        return AxiReport(errors=["dbg_state column not found in CSV"])
    if probe2_col is None:
        return AxiReport(errors=["ila_probe2_diag column not found in CSV"])

    # ---- Decode AXI writes (state < max_state) ----
    prev_state: Optional[int] = None
    axi_writes: List[AxiWrite] = []

    for i, row in enumerate(rows):
        state = parse_hex_any(row.get(state_col, "0"))
        probe2_val = parse_hex_any(row.get(probe2_col, "0"))

        if state < max_state:
            wdata = (probe2_val >> 32) & 0xFFFFFFFF
            awaddr = (probe2_val >> 26) & 0x3F

            # Only record transitions to avoid duplicate samples
            if state != prev_state:
                axi_writes.append(AxiWrite(sample=i, state=state,
                                           awaddr=awaddr, wdata=wdata))
            prev_state = state

    # ---- Match against expected sequence ----
    report = AxiReport(writes=axi_writes)
    all_match = True

    for exp_idx, (exp_addr, exp_data, desc) in enumerate(EXPECTED_WRITES):
        if exp_idx < len(axi_writes):
            aw = axi_writes[exp_idx]
            addr_ok = aw.awaddr == exp_addr
            data_ok = aw.wdata == exp_data
            if not (addr_ok and data_ok):
                all_match = False
                parts = []
                if not addr_ok:
                    parts.append(f"addr: got 0x{aw.awaddr:02x}, expected 0x{exp_addr:02x}")
                if not data_ok:
                    parts.append(f"data: got 0x{aw.wdata:08x}, expected 0x{exp_data:08x}")
                report.errors.append(f"{desc}: {', '.join(parts)}")
        else:
            all_match = False
            report.errors.append(f"{desc}: MISSING from capture")

    if len(axi_writes) != len(EXPECTED_WRITES):
        all_match = False
        report.errors.append(
            f"Write count mismatch: {len(axi_writes)} captured vs {len(EXPECTED_WRITES)} expected"
        )

    report.all_match = all_match
    return report
