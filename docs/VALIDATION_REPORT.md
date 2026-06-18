# FPGA Physical Validation Report — PRNG-371 on XC7Z020 Silicon

**Date**: 2026-06-18 | **Board**: ACZ702 (Zynq-7020, xc7z020clg400-1) | **Status**: ✅ PASS

## 1. Executive Summary

PRNG-371 (A_odd=371, B_VAL=111) has been physically validated on XC7Z020 silicon with **mismatch=0, bit errors=0, BER=0** for 8,180 consecutive 64-bit output words (523,520 bits total).

A single-character Verilog bug — 1-bit `reg` declaration driving a 4-bit `WSTRB` port — was the root cause of initial validation failures. The fix is one line. Post-fix, FPGA output matches the cycle-accurate RTL model 100% bit-exact.

## 2. Validation Results

| Metric | Value |
|---|---|
| **Words compared** | 8,180 |
| **Total bits** | 523,520 |
| **Mismatches** | **0** |
| **Bit errors** | **0** |
| **Bit Error Rate (BER)** | **0.000000e+00** |
| **Validation set** | First 8,180 consecutive PRNG outputs from startup |
| **Comparison model** | Cycle-accurate RTL-equivalent Python model (`rtl_cycle_model.py`) |
| **Seeds** | [1000, 2000, 3000, 4000] (no warmup) |
| **AXI-Lite writes** | 6/6 correct (ctrl=1, all 4 seeds, ap_start=0x81) |
| **Timing** | WNS=+7.560 ns (clean) |

### First 20 Output Words

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
Idx 10: 0xcaa8beaff60810d4
Idx 11: 0xe00025d6d6d42a8e
Idx 12: 0xa1b7e971c4136754
Idx 13: 0xafac8ac4eb02a2e0
Idx 14: 0xadb565326984aff9
Idx 15: 0x06152c571f0383d8
Idx 16: 0x733dc13891c0f4b0
Idx 17: 0x34d5b9fa3cee5ba5
Idx 18: 0x6738440e00fd7f3e
Idx 19: 0xe66bbe2e412b0065
```

All 8,180 words verified — every single word matches the RTL model bit-exact.

## 3. Root Cause: 1-bit WSTRB Silently Truncates Seeds

### 3.1 The Bug

**File**: [`prng_wrapper.v`](../fpga/prng_wrapper.v) line 41 (before fix)

```verilog
// BEFORE (BROKEN):
reg         axi_wstrb = 4'hF;  // No explicit width → Verilog default = 1 BIT!
```

**Verilog semantics**: A `reg` declaration without an explicit range (e.g., `reg [3:0]`) defaults to **1-bit wide**. The initialization `= 4'hF` is silently truncated to `1'b1`.

Since `axi_wstrb` connects to the 4-bit `WSTRB` port of the AXI-Lite slave, the 1-bit wire pads to `4'b0001` (only the LSB is driven; the upper 3 bits float to 0).

### 3.2 Impact Chain

```
reg axi_wstrb = 4'hF     (1-bit, value = 1'b1)
  │
  └─→ s_axi_CONTROL_WSTRB = 4'b0001   (only byte 0 enabled)
        │
        ├─ wmask = {8{WSTRB[3]}, 8{WSTRB[2]}, 8{WSTRB[1]}, 8{WSTRB[0]}}
        │        = {8{0}, 8{0}, 8{0}, 8{1}}
        │        = 0x000000FF          (only low byte writable)
        │
        └─ int_init_x1[15:0] = WDATA[7:0]  (high byte dropped!)
           int_init_x2[15:0] = WDATA[7:0]
           int_init_x3[15:0] = WDATA[7:0]
           int_init_x4[15:0] = WDATA[7:0]
```

**Result**: Seeds written to the PRNG core were truncated to 8 bits:
- `1000` (0x03E8) → `0xE8` (232)
- `2000` (0x07D0) → `0xD0` (208)
- `3000` (0x0BB8) → `0xB8` (184)
- `4000` (0x0FA0) → `0xA0` (160)

### 3.3 The Fix

```verilog
// AFTER (FIXED):
wire [3:0]  axi_wstrb;
assign      axi_wstrb = 4'hF;  // explicit 4-bit, all bytes enabled
```

Using `wire` + `assign` instead of `reg` initialization guarantees the full 4-bit constant is driven to the port.

### 3.4 Why This Was Hard to Find

| Symptom | Why It Misled |
|---|---|
| **AXI handshake looked normal** | AWVALID/AWREADY/WVALID/WREADY all toggled correctly — the transaction completed, just with wrong data |
| **Seeds appeared "close"** | 0xE8 vs 0x03E8 — the low byte was correct, suggesting a "small" error |
| **PRNG still produced output** | The PRNG ran fine with truncated seeds — it just produced different output than expected |
| **Golden comparison failed** | With different seeds, the PRNG is chaotic → completely different output (0/10,000 sliding match) |
| **Verilog default width** | `reg` without `[N:0]` is a well-known trap, but easy to overlook in code review |
| **No synthesis/lint warning** | Vivado doesn't flag implicit 1-bit → 4-bit port width mismatch for driven signals |

## 4. Debug Methodology

The investigation followed a systematic 5-phase approach:

### Phase 1: Observability (ILA instrumentation)

- Added `dbg_heartbeat`, `cfg_done`, `ila_probe2_diag` mux, `dbg_state`, `dbg_axi_stat`, `dbg_time`
- ILA probe2 mux: `state < 24 → {wdata, awaddr}`; `state ≥ 24 → prng_out`
- Trigger on `dbg_heartbeat == 1` (FSM leaves startup gate)
- 15-second startup delay gate for ILA arming window

### Phase 2: Startup Gate

- Added 15-second FSM startup delay (750M cycles @ 50MHz) to allow Vivado ILA refresh/arm
- Added 256-cycle PRNG reset pulse for clean init_done state
- Level-triggered `cfg_done` for reliable ILA capture

### Phase 3: AXI Write Verification

- `inspect_axi_writes.py` decoded the probe2 mux to verify each AXI write
- Confirmed addresses and data values matched expectations
- **First capture showed WDATA low-byte correct but high-byte = 0x00** — led to WSTRB hypothesis

### Phase 4: Cycle-Accurate RTL Model

- Built `rtl_cycle_model.py` — precise Python mirror of HLS-generated RTL (FSM, pipeline registers, init_done semantics)
- Verified: RTL model == C golden model == golden references at all warmup levels
- **Critical diagnostic**: FPGA output matched RTL model 500/500 with 8-bit-truncated seeds → confirmed WSTRB was the cause

### Phase 5: Fix and Validate

- Changed `reg axi_wstrb` to `wire [3:0] axi_wstrb` with `assign`
- Rebuilt bitstream, re-captured ILA
- **8180/8180 words match** — root cause confirmed and fixed

## 5. Verification Infrastructure

| Component | File | Purpose |
|---|---|---|
| **RTL Cycle Model** | `prng_experiment/tools/rtl_cycle_model.py` | Cycle-accurate Python model of HLS-generated RTL (FSM, pipeline, init_done) |
| **AXI Inspector** | `prng_experiment/tools/inspect_axi_writes.py` | Decodes ILA probe2 mux to verify AXI write transactions |
| **ILA Capture Script** | `hw/fpga/capture_ila.tcl` | Automated ILA arming, triggering, and CSV export |
| **Validation Engine** | `prng_experiment/tools/validate_fpga_prng.py` | Multi-mode alignment, pipeline auto-skip, Python golden fallback |
| **Wrapper FSM** | `hw/fpga/prng_wrapper.v` | AXI-Lite config FSM with startup gate, reset control, debug probes |
| **Full Flow Guide** | `hw/fpga/FULL_FLOW_GUIDE.md` | End-to-end workflow with ILA IP configuration instructions |

> **ILA trigger note**: Vivado ILA v6.2 defaults all probes to DATA only. PROBE1 (`dbg_heartbeat`) must be manually set to DATA AND TRIGGER in the IP customization GUI, with Trigger Ports increased from 1 to 2. Closing and reopening Vivado may invalidate the IP cache and silently revert this setting — see [ENGINEERING_STATUS.md](../fpga/ENGINEERING_STATUS.md) §2 Issue 2 for details.

### 5.1 Reproducibility

Two independent captures (2026-06-18 12:17 UTC and 12:48 UTC) both yielded **8,180/8,180 words matched, BER=0**, confirming the validation pipeline is stable and reproducible from synthesis through bitstream to capture.

## 6. Key Design Insights

### 6.1 init_done is FSM-driven, Not Reset-driven

HLS-generated `init_done` is set to 1 by `ap_condition_186 = ap_start & ctrl & state1`, **not** by `ap_rst_n`. A reset-only approach won't clear `init_done`. The 256-cycle reset pulse was needed to ensure clean initialization.

### 6.2 Verilog Width Default Trap

`reg x = value` without explicit `[N:0]` is always 1-bit. This is a classic Verilog gotcha. Use `wire [N:0]` + `assign` for constants, or always specify explicit widths on `reg` declarations.

### 6.3 HLS FSM = 2-State Cycle

Each PRNG output requires 2 clock cycles:
- **State 1**: Load pipeline registers, compute intermediates, transition → State 2
- **State 2**: Commit state update, produce output word, transition → State 1

The `interval=2` from HLS synthesis directly reflects this 2-state cycle.

### 6.4 No Internal Warmup

The FPGA PRNG core has **no internal warmup**. It starts outputting immediately after `ap_start=1` and `ctrl=1`. For warmup-based validation, the comparison model must skip the first N FPGA outputs (where N = warmup iterations).

## 7. Design Rule: WSTRB Must Be All-Ones for Seed Writes

**Rule**: When using AXI-Lite to write PRNG seed registers, `WSTRB` **must** be `4'b1111` (all 4 bytes enabled). Seeds are 16-bit values stored in 32-bit registers — writing only the low byte truncates the upper half.

**Checklist for wrapper designers**:
- [ ] `WSTRB` is explicitly 4 bits wide (not relying on Verilog default)
- [ ] `WSTRB = 4'hF` (all bytes enabled) for all seed writes
- [ ] Verify with ILA: decode `{wdata, awaddr}` and check all 16 bits of each seed
- [ ] Cross-reference against RTL model: FPGA output[0..N] == RTL model output[0..N]

## 8. Lessons Learned

1. **Always specify explicit widths** on Verilog `reg` declarations, especially for constants connected to multi-bit ports
2. **Cycle-accurate modeling** is the most powerful debugging tool — when FPGA output matched the RTL model with truncated seeds, the root cause was immediately confirmed
3. **ILA probe2 mux** (`state < 24 → AXI diag, state ≥ 24 → PRNG output`) provides visibility into both the config phase and runtime phase in a single 64-bit probe
4. **Startup gate** (15-second delay) is essential for ILA capture — without it, the FSM finishes before Vivado can arm the ILA
5. **Bit-exact verification** eliminates false positives — "0/10,000 match" was the key signal that the FPGA was running a completely different computation, not just a timing glitch

---

## Appendix: Full Validation Script

```python
# validate_fpga_prng.py — used for final validation
from rtl_cycle_model import RTLState

seeds = [1000, 2000, 3000, 4000]
rtl = RTLState(seeds=tuple(seeds))
rtl.configure_and_start()
rtl.run_iterations(8180)

fpga_words = [...]  # from ILA CSV
rtl_words = rtl.get_outputs()

mismatches = 0
for fpga, rtl_out in zip(fpga_words, rtl_words):
    if fpga != rtl_out:
        mismatches += 1

assert mismatches == 0  # PASS
```

## Appendix: ILA Capture Commands

```tcl
# In Vivado Tcl Console:
source D:/fpgaoscillate/prng_project/hw/fpga/capture_ila.tcl

# CSV output: C:/Users/Kryan/AppData/Roaming/Xilinx/Vivado/ila_capture_<timestamp>.csv
```
