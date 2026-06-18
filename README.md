# PRNG-371 FPGA Physical Verification System

**Chaos PRNG (A_odd=371) — FPGA silicon-validated with BER=0**

[![Verify](https://github.com/USERNAME/REPO/actions/workflows/verify.yml/badge.svg)](https://github.com/USERNAME/REPO/actions/workflows/verify.yml)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.7%2B-blue)](https://www.python.org/)
[![BER](https://img.shields.io/badge/BER-0.000000e+00-brightgreen)]()
[![Board](https://img.shields.io/badge/board-ACZ702%20(Zynq--7020)-blue)]()

---

## Installation

**No pip install, no virtualenv, no Docker required.** The project uses Python standard library only.

```bash
git clone <repo-url>
cd prng_fpga_project
```

Python 3.7+ is the only dependency. Verify with `python3 --version` (Linux/macOS) or `python --version` (Windows).

---

## One-Click Verification

### Linux / macOS / Git Bash

```bash
make verify                         # Fast offline (unit + integration, ~5 sec)
make test                           # Full suite (all layers, ~30 sec)
make validate ILA_CSV=ila.csv       # FPGA physical validation
```

### Windows CMD

```cmd
cd /d D:\path\to\prng_fpga_project
python tests\run_all_tests.py --quick
python tests\run_all_tests.py
python tests\run_all_tests.py --csv ila.csv
```

Or use the batch wrapper:

```cmd
cd /d D:\path\to\prng_fpga_project
scripts\verify.bat                  rem Quick verification
scripts\verify.bat full             rem Full test suite
scripts\verify.bat csv ila.csv      rem FPGA physical validation
```

> **Note for Windows CMD users:** `cd /d` is required to switch drives. Do NOT copy the `#` comments — CMD treats `#` as a regular character, not a comment. Use the commands exactly as shown above without trailing comments.

### Expected Output

```
[PASS] Unit Tests: 17 tests, 0 failed
[PASS] Integration Tests: 4 tests, 0 failed
FINAL VERDICT: PASS
FPGA PRNG-371: FPGA == RTL == GOLDEN
Reports: reports/
```

**What this proves: FPGA output == RTL model == Golden reference, bit-exact, 8,180/8,180 words.**

---

## Automated Verification Pipeline

```
  Vivado ILA                  Python Verification Engine
  ==========                  =========================
     .---.
    | FPGA |──── ILA probes ──> CSV Export
     '---'                           |
                                     v
                              +---------------+
                              |  ila_parser   |  Robust CSV parsing
                              +---------------+  (auto-detect headers,
                                     |          skip radix rows,
                                     v          name-based columns)
                              +---------------+
                              | axi_checker   |  AXI-Lite write verify
                              +---------------+  (6 writes: ctrl + seeds + start)
                                     |
                                     v
                              +---------------+
                              | prng_checker  |  Align (cfg_done/dbg_state/
                              +---------------+   first_word) + extract + compare
                                     |
                                     v
                              +---------------+
                              | golden_model  |  Single source of truth
                              +---------------+  (pure-Python PRNG-371 spec)
                                     |
                                     v
                              +---------------+
                              |   PASS/FAIL   |
                              +---------------+
                                     |
                                     v
                              reports/
                              ├── summary.md
                              ├── summary.json
                              ├── axi.txt
                              └── ber.txt
```

All steps run automatically from a single command.

---

## Project Structure

```
prng_fpga_project/
├── .github/workflows/            # GitHub Actions CI
│   └── verify.yml               #   Auto-verify on push/PR
├── rtl/                        # Verilog RTL (wrapper + top + PRNG core)
├── tools/
│   ├── core/                   # Library layer — reusable, testable, no CLI
│   │   ├── utils.py            #   UTF-8 safety, hex parsing, column mapping
│   │   ├── ila_parser.py       #   Robust Vivado ILA CSV parser
│   │   ├── golden_model.py     #   SSOT canonical PRNG-371 reference
│   │   ├── rtl_model.py        #   Cycle-accurate RTL wrapper
│   │   ├── axi_checker.py      #   AXI-Lite write transaction verifier
│   │   └── prng_checker.py     #   Alignment + extraction + comparison engine
│   └── cli/                    # CLI entry points (thin argparse wrappers)
├── tests/
│   ├── unit/                   # Per-module correctness tests
│   ├── integration/            # Cross-module pipeline tests
│   ├── system/                 # End-to-end + long-run stability tests
│   └── run_all_tests.py        # One-click entry point
├── scripts/                    # Shell wrappers (verify.bat / verify.sh)
├── reports/                    # Structured output (summary.md, .json, .txt)
├── docs/                       # Full documentation
├── hls_src/                    # HLS C++ source + golden reference files
├── .gitignore
├── LICENSE                       # MIT
├── requirements.txt              # Python 3.7+, stdlib only
├── Makefile                    # make verify / test / validate / clean
└── README.md
```

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.7+ | Standard library only (no pip packages needed) |
| Vivado | 2024.1+ | Only required for ILA capture and bitstream generation |
| Board | ACZ702 (XC7Z020) | JTAG-connected for FPGA validation |

**No IDE, no special Python packages, no Docker required for offline tests.**

---

## Quick Start

**Step 1 — Clone**

```bash
git clone <repo-url>
cd prng_fpga_project
```

**Step 2 — Offline verification (no FPGA required, ~5 seconds)**

```bash
make verify
```

Expected output:

```
[PASS] Unit Tests: 17 tests, 0 failed
[PASS] Integration Tests: 4 tests, 0 failed
FINAL VERDICT: PASS
FPGA PRNG-371: FPGA == RTL == GOLDEN
```

**Step 3 — FPGA physical validation (requires board + Vivado)**

1. Open Vivado project, verify ILA probe1 = DATA AND TRIGGER
2. Run synthesis + implementation + program device
3. In Vivado Tcl Console: `source scripts/capture_ila.tcl`
4. Validate the captured CSV:

```bash
make validate ILA_CSV=path/to/ila_capture.csv
```

---

## FPGA Validation Results

### ACZ702 Board (XC7Z020, 2026-06-18)

| Metric | Value |
|--------|-------|
| Words compared | 8,180 |
| Matched | 8,180 |
| Mismatched | 0 |
| Bit errors | 0 |
| BER | 0.000000e+00 |
| Seeds | [1000, 2000, 3000, 4000] |
| Timing (WNS) | +7.560 ns |
| Reproducibility | 2 independent captures, both PASS |

### First 10 Output Words (verified bit-exact on silicon)

```
Idx  0: 0xc661f290c4215270
Idx  1: 0x7fa2642199a17c60
Idx  2: 0xadf7fbf7aa526ae0
Idx  3: 0x1a888edad1ecfe26
Idx  4: 0x7d0cd5f6db306630
Idx  5: 0x174cada1cad56c73
Idx  6: 0x81b1aa0ae0dc409c
Idx  7: 0x62ee67fac81ef46b
Idx  8: 0x384a7e99a041051e
Idx  9: 0x61ae76d6cae4f188
```

---

## Engineering Quality

### Single Source of Truth

`tools/core/golden_model.py` is the **canonical** PRNG-371 specification. All other models derive from it:

```
golden_model.py  (pure Python spec)
    |
    +---> rtl_model.py    (cycle-accurate RTL wrapper, imports multipliers)
    +---> prng_checker.py (comparison engine, imports golden for reference)
```

No duplicated algorithm logic. No ambiguity about "which model is correct?"

### Robust CSV Parsing

`tools/core/ila_parser.py` handles Vivado ILA CSV format variations:
- Auto-detects header line (radix row may or may not be present)
- Column lookup by name, not index
- Handles bracket notation (`dbg_state[4:0]`)
- Works with Vivado 2024.1+ regardless of CSV export format version

### No Emoji, Cross-Platform

All output uses ASCII-only status markers (`PASS`/`FAIL`). UTF-8 encoding is enforced on all platforms. Scripts work on Windows, Linux, and macOS.

---

## Known Issues & Mitigations

| Issue | Mitigation | Doc Reference |
|-------|------------|---------------|
| Vivado ILA v6.2 silently downgrades probe1 to DATA-only | Check `ila_0` IP after Vivado restart: PROBE1 = DATA AND TRIGGER | [docs/ENGINEERING_STATUS.md](docs/ENGINEERING_STATUS.md) |
| Verilog `reg` without explicit width defaults to 1-bit | Always use `wire [N:0]` + `assign` for constants | [docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md) |
| First PRNG word after state transition is pipeline fill (0x0000) | Auto-skipped by `prng_checker.extract_prng_sequence()` | — (built-in) |
| C golden library (golden_371.so) cannot load on Windows | Automatic Python `GoldenModel` fallback | — (built-in) |

---

## Full Documentation

| Document | Location |
|----------|----------|
| Physical Validation Report | [docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md) |
| Engineering Status | [docs/ENGINEERING_STATUS.md](docs/ENGINEERING_STATUS.md) |
| Full Flow Guide (Vivado) | [docs/FULL_FLOW_GUIDE.md](docs/FULL_FLOW_GUIDE.md) |
| Design Summary | [docs/DESIGN_SUMMARY.md](docs/DESIGN_SUMMARY.md) |

---

## License

[MIT License](LICENSE)

---

*FPGA == RTL == GOLDEN — verified on XC7Z020 silicon, 2026-06-18*
