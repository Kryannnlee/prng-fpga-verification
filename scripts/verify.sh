#!/usr/bin/env bash
# PRNG-371 FPGA Verification Framework — Unix one-click wrapper
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-quick}" in
  quick)
    python3 tests/run_all_tests.py --quick
    ;;
  full)
    python3 tests/run_all_tests.py
    ;;
  csv)
    if [ -z "${2:-}" ]; then
      echo "Usage: $0 csv <ila_capture.csv>"
      exit 1
    fi
    python3 tests/run_all_tests.py --csv "$2"
    ;;
  *)
    echo "Usage: $0 [quick|full|csv <path>]"
    ;;
esac
