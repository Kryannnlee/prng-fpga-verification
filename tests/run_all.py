#!/usr/bin/env python3
# ==========================================================================
# PRNG-371 FPGA Engineering Convergence Test Suite
# ==========================================================================
# Five-phase optimization & validation framework:
#   I.   Reset/Power-up Robustness
#   II.  AXI-Lite Stress / Corner Cases
#   III. Long-Run Stability (≥10^8 cycles)
#   IV.  Cross-Build Determinism Validation
#   V.   IP Engineering Acceptance
#
# Usage:
#   cd prng_experiment/tools
#   python tests/run_all.py                    # full suite
#   python tests/run_all.py --phase reset       # single phase
#   python tests/run_all.py --long-run-cycles 100000000  # custom long-run
# ==========================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Ensure we can import from parent (tools/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rtl_cycle_model import RTLState, GoldenModelState


# ==========================================================================
# Test Result Types
# ==========================================================================

@dataclass
class TestCase:
    name: str
    passed: bool = False
    detail: str = ""
    duration_ms: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PhaseReport:
    phase: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    tests: List[TestCase] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0


@dataclass
class SuiteReport:
    timestamp: str = ""
    phases: List[PhaseReport] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_tests": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": f"{self.pass_rate*100:.1f}%",
            "duration_ms": self.duration_ms,
            "phases": [
                {
                    "phase": p.phase,
                    "total": p.total,
                    "passed": p.passed,
                    "failed": p.failed,
                    "pass_rate": f"{p.pass_rate*100:.1f}%",
                    "duration_ms": p.duration_ms,
                    "tests": [{"name": t.name, "passed": t.passed, "detail": t.detail, "metrics": t.metrics} for t in p.tests],
                }
                for p in self.phases
            ],
        }


# ==========================================================================
# Acceptance Criteria (must ALL pass for final sign-off)
# ==========================================================================

ACCEPTANCE_CRITERIA = [
    "Reset randomization yields 100% deterministic output",
    "AXI stress test yields 0 transaction errors",
    "Long-run mismatch count == 0",
    "RTL model output matches golden model output",
    "All tests pass in all phases",
]


# ==========================================================================
# Runner
# ==========================================================================

def format_pass(s: str) -> str: return f"\033[32m{s}\033[0m"
def format_fail(s: str) -> str: return f"\033[31m{s}\033[0m"
def format_bold(s: str) -> str: return f"\033[1m{s}\033[0m"


def run_test(tc_or_dict, fn, *args, **kwargs) -> TestCase:
    """Run a single test function, catching exceptions.
    Accepts either a TestCase object or a dict with name/passed/detail/metrics keys."""
    if isinstance(tc_or_dict, dict):
        tc = TestCase(
            name=tc_or_dict.get("name", "unnamed"),
            passed=tc_or_dict.get("passed", False),
            detail=tc_or_dict.get("detail", ""),
            metrics=tc_or_dict.get("metrics", {}),
        )
    else:
        tc = tc_or_dict

    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, tuple) and len(result) == 3:
            tc.passed, tc.detail, tc.metrics = result
        elif isinstance(result, tuple) and len(result) == 2:
            tc.passed, tc.detail = result
        elif isinstance(result, bool):
            tc.passed = result
            tc.detail = "PASS" if result else "FAIL"
        else:
            tc.passed = True
            tc.detail = str(result)
    except Exception as e:
        tc.passed = False
        tc.detail = f"EXCEPTION: {e}"
    tc.duration_ms = (time.perf_counter() - t0) * 1000
    return tc


def print_header(title: str):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def print_test_result(tc: TestCase):
    status = format_pass("PASS") if tc.passed else format_fail("FAIL")
    print(f"  [{status}] {tc.name} ({tc.duration_ms:.0f}ms)")
    if not tc.passed or tc.detail not in ("PASS", ""):
        print(f"         {tc.detail}")


def main():
    parser = argparse.ArgumentParser(description="PRNG-371 Engineering Convergence Suite")
    parser.add_argument("--phase", choices=["reset", "axi", "longrun", "crossbuild", "ip", "all"],
                        default="all", help="Which phase to run (default: all)")
    parser.add_argument("--long-run-cycles", type=int, default=10_000_000,
                        help="Long-run cycles (default: 10^7, use 10^8 for full)")
    parser.add_argument("--json-report", type=str, default=None,
                        help="Save JSON report to file")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: reduced cycles for fast iteration")
    args = parser.parse_args()

    if args.quick:
        args.long_run_cycles = 1_000_000

    report = SuiteReport(timestamp=datetime.now(timezone.utc).isoformat())

    print_header("PRNG-371 FPGA Engineering Convergence Test Suite")
    print(f"  Time: {report.timestamp}")
    print(f"  Mode: {'QUICK' if args.quick else 'FULL'} (long-run={args.long_run_cycles:,} cycles)")
    print()

    # ---- Phase I: Reset/Power-up Robustness ----
    if args.phase in ("reset", "all"):
        from tests.test_reset_randomization import run_reset_tests
        phase = PhaseReport(phase="I. Reset/Power-up Robustness")
        phase.tests = run_reset_tests(run_test)
        phase.total = len(phase.tests)
        phase.passed = sum(1 for t in phase.tests if t.passed)
        phase.failed = phase.total - phase.passed
        report.phases.append(phase)

        print_header("I. Reset/Power-up Robustness")
        for t in phase.tests:
            print_test_result(t)
        print(f"  Summary: {phase.passed}/{phase.total} passed")

    # ---- Phase II: AXI-Lite Stress ----
    if args.phase in ("axi", "all"):
        from tests.test_axi_stress import run_axi_tests
        phase = PhaseReport(phase="II. AXI-Lite Stress / Corner Cases")
        phase.tests = run_axi_tests(run_test)
        phase.total = len(phase.tests)
        phase.passed = sum(1 for t in phase.tests if t.passed)
        phase.failed = phase.total - phase.passed
        report.phases.append(phase)

        print_header("II. AXI-Lite Stress / Corner Cases")
        for t in phase.tests:
            print_test_result(t)
        print(f"  Summary: {phase.passed}/{phase.total} passed")

    # ---- Phase III: Long-Run Stability ----
    if args.phase in ("longrun", "all"):
        from tests.test_long_run_stability import run_longrun_tests
        phase = PhaseReport(phase="III. Long-Run Stability")
        phase.tests = run_longrun_tests(run_test, args.long_run_cycles)
        phase.total = len(phase.tests)
        phase.passed = sum(1 for t in phase.tests if t.passed)
        phase.failed = phase.total - phase.passed
        phase.duration_ms = sum(t.duration_ms for t in phase.tests)
        report.phases.append(phase)

        print_header("III. Long-Run Stability")
        for t in phase.tests:
            print_test_result(t)
        print(f"  Summary: {phase.passed}/{phase.total} passed ({phase.duration_ms/1000:.1f}s)")

    # ---- Phase IV: Cross-Build Determinism ----
    if args.phase in ("crossbuild", "all"):
        from tests.test_cross_build_determinism import run_crossbuild_tests
        phase = PhaseReport(phase="IV. Cross-Build Determinism")
        phase.tests = run_crossbuild_tests(run_test)
        phase.total = len(phase.tests)
        phase.passed = sum(1 for t in phase.tests if t.passed)
        phase.failed = phase.total - phase.passed
        report.phases.append(phase)

        print_header("IV. Cross-Build Determinism")
        for t in phase.tests:
            print_test_result(t)
        print(f"  Summary: {phase.passed}/{phase.total} passed")

    # ---- Phase V: IP Engineering Acceptance ----
    if args.phase in ("ip", "all"):
        from tests.test_ip_engineering import run_ip_tests
        phase = PhaseReport(phase="V. IP Engineering Acceptance")
        phase.tests = run_ip_tests(run_test)
        phase.total = len(phase.tests)
        phase.passed = sum(1 for t in phase.tests if t.passed)
        phase.failed = phase.total - phase.passed
        report.phases.append(phase)

        print_header("V. IP Engineering Acceptance")
        for t in phase.tests:
            print_test_result(t)
        print(f"  Summary: {phase.passed}/{phase.total} passed")

    # ---- Final Report ----
    report.total = sum(p.total for p in report.phases)
    report.passed = sum(p.passed for p in report.phases)
    report.failed = report.total - report.passed

    print()
    print("=" * 72)
    print("  FINAL VERDICT")
    print("=" * 72)
    print(f"  Total:   {report.total} tests")
    print(f"  Passed:  {format_pass(str(report.passed))}")
    print(f"  Failed:  {format_fail(str(report.failed)) if report.failed else 0}")
    print(f"  Rate:    {report.pass_rate*100:.1f}%")

    # Acceptance criteria check
    all_criteria_met = report.failed == 0
    print()
    print("  Acceptance Criteria:")
    for criterion in ACCEPTANCE_CRITERIA:
        status = format_pass("[x]") if all_criteria_met else "[ ]"
        print(f"    {status} {criterion}")

    if all_criteria_met:
        print()
        print("  " + format_bold(format_pass(
            "*** FPGA PRNG-371 IS PRODUCTION-GRADE DETERMINISTIC IP CORE ***")))
    else:
        print()
        print("  " + format_fail(f"  {report.failed} test(s) failed — see details above."))

    # JSON report
    if args.json_report:
        with open(args.json_report, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\n  JSON report: {args.json_report}")

    return 0 if all_criteria_met else 1


if __name__ == "__main__":
    sys.exit(main())
