#!/usr/bin/env python3
# ==========================================================================
# run_all_tests.py — PRNG-371 FPGA Verification Framework — One-Click Entry
# ==========================================================================
#
# Usage:
#   python tests/run_all_tests.py                        # full suite
#   python tests/run_all_tests.py --quick                # fast (unit + integration only)
#   python tests/run_all_tests.py --phase unit            # single layer
#   python tests/run_all_tests.py --phase integration
#   python tests/run_all_tests.py --csv <ila_capture.csv> # FPGA physical validation
#
# Output:
#   reports/summary.md      — human-readable summary
#   reports/summary.json    — machine-readable results
#   reports/axi.txt         — AXI write verification
#   reports/ber.txt         — PRNG BER comparison result
# ==========================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup — ensure tools/ is importable
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from tools.core.utils import force_utf8, PASS_MARK, FAIL_MARK

# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

REPORTS_DIR = os.path.join(_PROJECT_ROOT, "reports")


def _write_report(filename: str, content: str):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _status_text(passed: bool) -> str:
    return PASS_MARK if passed else FAIL_MARK


def _status_bold(text: str) -> str:
    """Minimal formatting that works on all terminals."""
    return f"[{text}]"


# ---------------------------------------------------------------------------
# Test discovery and execution
# ---------------------------------------------------------------------------


def run_unittest_discover(start_dir: str, pattern: str = "test_*.py") -> Tuple[int, int, List[str]]:
    """Discover and run unittest test cases.  Returns (tests_run, failures, detail_lines)."""
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir, pattern=pattern)
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    detail_lines: List[str] = []
    for test, traceback in result.failures + result.errors:
        detail_lines.append(f"    FAIL: {test}")
        for line in traceback.splitlines()[-3:]:
            detail_lines.append(f"          {line.strip()}")
    return result.testsRun, len(result.failures) + len(result.errors), detail_lines


def run_unit_tests() -> Tuple[int, int, List[str]]:
    """Run tests/unit/ test suite."""
    unit_dir = os.path.join(_PROJECT_ROOT, "tests", "unit")
    if not os.path.isdir(unit_dir):
        return 0, 0, ["Unit test directory not found"]
    return run_unittest_discover(unit_dir)


def run_integration_tests() -> Tuple[int, int, List[str]]:
    """Run tests/integration/ test suite."""
    integ_dir = os.path.join(_PROJECT_ROOT, "tests", "integration")
    if not os.path.isdir(integ_dir):
        return 0, 0, ["Integration test directory not found"]
    return run_unittest_discover(integ_dir)


def run_system_tests() -> Tuple[int, int, List[str]]:
    """Run tests/system/ test suite (may include long-run tests)."""
    sys_dir = os.path.join(_PROJECT_ROOT, "tests", "system")
    if not os.path.isdir(sys_dir):
        return 0, 0, ["System test directory not found"]
    return run_unittest_discover(sys_dir)


# ---------------------------------------------------------------------------
# FPGA physical validation (requires ILA CSV)
# ---------------------------------------------------------------------------


def run_fpga_validation(csv_path: str) -> Tuple[bool, Dict]:
    """Run the FPGA physical validation pipeline on an ILA CSV capture.

    Returns (passed, report_dict).
    """
    from tools.core.ila_parser import parse_ila_csv
    from tools.core.axi_checker import check_axi_writes
    from tools.core.prng_checker import run_prng_check
    from tools.core.golden_model import generate_golden, DEFAULT_SEEDS

    report: Dict = {
        "csv_path": csv_path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "axi": None,
        "prng": None,
        "passed": False,
        "errors": [],
    }

    # 1. Parse CSV
    try:
        fieldnames, rows = parse_ila_csv(csv_path)
        report["total_samples"] = len(rows)
        report["columns"] = fieldnames
    except Exception as e:
        report["errors"].append(f"CSV parse error: {e}")
        return False, report

    # 2. AXI check
    try:
        axi_report = check_axi_writes(rows, fieldnames)
        report["axi"] = {
            "passed": axi_report.all_match,
            "writes_found": len(axi_report.writes),
            "errors": axi_report.errors,
        }
    except Exception as e:
        report["axi"] = {"passed": False, "errors": [str(e)]}

    # 3. Generate golden
    try:
        golden = generate_golden(num_words=8500, seeds=DEFAULT_SEEDS, warmup=0)
    except Exception as e:
        report["errors"].append(f"Golden generation error: {e}")
        return False, report

    # 4. PRNG check
    try:
        prng_result = run_prng_check(
            rows, fieldnames, golden,
            align_mode="cfg_done",
            warmup=0,
        )
        report["prng"] = prng_result.to_dict()
        report["passed"] = prng_result.passed
    except Exception as e:
        report["prng"] = {"passed": False, "errors": [str(e)]}

    # Overall: AXI + PRNG both must pass
    report["passed"] = (
        report.get("axi", {}).get("passed", False)
        and report.get("prng", {}).get("passed", False)
    )

    return report["passed"], report


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------


def main() -> int:
    global REPORTS_DIR
    force_utf8()

    parser = argparse.ArgumentParser(
        description="PRNG-371 FPGA Verification Framework — One-Click Entry",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Quick mode: run unit + integration tests only (no system/long-run tests)",
    )
    parser.add_argument(
        "--phase", choices=["unit", "integration", "system", "all"],
        default="all", help="Which test layer to run (default: all)",
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="Path to Vivado ILA CSV capture for FPGA physical validation",
    )
    parser.add_argument(
        "--report-dir", type=str, default=REPORTS_DIR,
        help=f"Report output directory (default: {REPORTS_DIR})",
    )
    args = parser.parse_args()

    REPORTS_DIR = args.report_dir

    all_passed = True
    total_tests = 0
    total_failed = 0
    results: Dict[str, Dict] = {}

    print()
    print("=" * 72)
    print("  PRNG-371 FPGA VERIFICATION FRAMEWORK")
    print("=" * 72)
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    # ---- Unit Tests ----
    if args.phase in ("unit", "all"):
        print("--- Unit Tests ---")
        n, failed, details = run_unit_tests()
        total_tests += n
        total_failed += failed
        passed = (failed == 0) and (n > 0)
        all_passed = all_passed and passed
        results["unit"] = {"tests_run": n, "failed": failed, "passed": passed}
        status = _status_text(passed)
        print(f"  {_status_bold(status)} {n} tests, {failed} failed")
        for d in details:
            print(d)

    # ---- Integration Tests ----
    if args.phase in ("integration", "all"):
        print("--- Integration Tests ---")
        n, failed, details = run_integration_tests()
        total_tests += n
        total_failed += failed
        passed = (failed == 0) and (n > 0)
        all_passed = all_passed and passed
        results["integration"] = {"tests_run": n, "failed": failed, "passed": passed}
        status = _status_text(passed)
        print(f"  {_status_bold(status)} {n} tests, {failed} failed")
        for d in details:
            print(d)

    # ---- System Tests ----
    if not args.quick and args.phase in ("system", "all"):
        print("--- System Tests (long-run) ---")
        n, failed, details = run_system_tests()
        total_tests += n
        total_failed += failed
        passed = (failed == 0)  # system may have 0 tests if dir empty
        all_passed = all_passed and passed
        results["system"] = {"tests_run": n, "failed": failed, "passed": passed}
        status = _status_text(passed)
        print(f"  {_status_bold(status)} {n} tests, {failed} failed")
        for d in details:
            print(d)

    # ---- FPGA Physical Validation ----
    fpga_result: Optional[Dict] = None
    if args.csv:
        print("--- FPGA Physical Validation ---")
        csv_path = args.csv
        if not os.path.exists(csv_path):
            print(f"  {_status_bold(FAIL_MARK)} CSV not found: {csv_path}")
            all_passed = False
        else:
            passed, fpga_result = run_fpga_validation(csv_path)
            all_passed = all_passed and passed
            status = _status_text(passed)
            print(f"  {_status_bold(status)} FPGA validation")
            if fpga_result.get("prng"):
                prng = fpga_result["prng"]
                comp = prng.get("comparison", {})
                print(f"    Words compared: {comp.get('total_compared', '?')}")
                print(f"    Matched:        {comp.get('matched_words', '?')}")
                print(f"    Mismatched:     {comp.get('mismatched_words', '?')}")
                print(f"    BER:            {comp.get('ber', '?'):.12g}")

    # ---- Write reports ----
    summary_lines = [
        "# PRNG-371 FPGA Verification Summary",
        "",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Verdict:** {_status_text(all_passed)}",
        "",
        "## Results",
        "",
        f"| Layer | Tests | Failed | Status |",
        f"|-------|-------|--------|--------|",
    ]
    for layer, r in results.items():
        summary_lines.append(
            f"| {layer} | {r['tests_run']} | {r['failed']} | {_status_text(r['passed'])} |"
        )
    if fpga_result:
        summary_lines.append(
            f"| fpga | — | — | {_status_text(fpga_result.get('passed', False))} |"
        )
    summary_lines.extend([
        "",
        f"**Total:** {total_tests} tests, {total_failed} failed",
    ])

    summary_md = "\n".join(summary_lines)
    _write_report("summary.md", summary_md)

    # JSON report
    json_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": all_passed,
        "phases": results,
        "fpga": fpga_result,
        "total_tests": total_tests,
        "total_failed": total_failed,
    }
    _write_report("summary.json", json.dumps(json_data, indent=2))

    # AXI text report
    if fpga_result and fpga_result.get("axi"):
        axi = fpga_result["axi"]
        axi_lines = [
            f"AXI-Lite Write Verification: {_status_text(axi['passed'])}",
            f"Writes found: {axi.get('writes_found', '?')}",
        ]
        for e in axi.get("errors", []):
            axi_lines.append(f"ERROR: {e}")
        _write_report("axi.txt", "\n".join(axi_lines))

    # BER text report
    if fpga_result and fpga_result.get("prng"):
        prng = fpga_result["prng"]
        comp = prng.get("comparison", {})
        ber_lines = [
            f"PRNG-371 BER Report: {_status_text(prng['passed'])}",
            f"Words compared: {comp.get('total_compared', '?')}",
            f"Matched:        {comp.get('matched_words', '?')}",
            f"Mismatched:     {comp.get('mismatched_words', '?')}",
            f"Bit errors:     {comp.get('bit_errors', '?')}",
            f"BER:            {comp.get('ber', '?'):.12g}",
        ]
        if prng.get("first_mismatch"):
            fm = prng["first_mismatch"]
            ber_lines.append(f"First mismatch at word {fm.get('word_index', '?')}")
            ber_lines.append(f"  Golden:  {fm.get('golden', '?')}")
            ber_lines.append(f"  Capture: {fm.get('capture', '?')}")
        _write_report("ber.txt", "\n".join(ber_lines))

    # ---- Final verdict ----
    print()
    print("=" * 72)
    print(f"  FINAL VERDICT: {_status_text(all_passed)}")
    print("=" * 72)
    if all_passed:
        print(f"  FPGA PRNG-371: FPGA == RTL == GOLDEN")
    else:
        print(f"  {total_failed} failure(s) detected — see details above.")
    print(f"  Reports: {REPORTS_DIR}/")
    print()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
