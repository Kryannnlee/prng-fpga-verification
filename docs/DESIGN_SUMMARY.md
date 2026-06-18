# Chaos PRNG — Hardware Design Summary

**Date**: 2026-05-13 | **Target**: Xilinx Zynq-7020 (xc7z020clg400-1) | **Tool**: Vitis HLS 2024.1 + Vivado 2024.1 | **Status**: Verified (CSim ✓, CoSim ✓, OOC Synth ✓, **Real P&R ✓**, Timing ✓, Bitstream ✓, **FPGA Silicon ✓**)
**Top performer**: `chaos_prng_371` — 100 MHz, **800 MB/s**, P&R: 500 LUT, 370 FF, 1 DSP, 0.12 W. WNS=+0.549ns, 925/925 nets routed. **FPGA physical validation: 8,180/8,180 words match, BER=0** (2026-06-18, ACZ702 board).

## 1. Architecture

```
                        prng_top
  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │  ctrl ──→┌──────────┐    ┌──────────┐    ┌───────┐  │
  │  init ──→│  Chaos   │───→│   Bit    │───→│ Post  │──┼──→ prng_out[63:0]
  │          │  Core    │    │ Rotation │    │ Proc  │  │
  │          │ (4D R1-LY)│   │ Enhancer │    │(ROTL- │──┼──→ prng_out_valid
  │          └──────────┘    └──────────┘    │ XOR)  │  │
  │                                          └───────┘  │
  │  AXI-Lite CONTROL (addr 0x00–0x1C)                  │
  └─────────────────────────────────────────────────────┘
```

### 1.1 Dataflow (per clock cycle)

```
Step 1: Chaos iteration
  x1' = 371·x1 + 50·s2      (all mod 2^16, shift-and-add)
  x2' = 373·x2 + 48·s1
  x3' = 375·x3 + 46·s0
  x4' = 111·x2 + logistic(x3 + s0)

Step 2: Bit-rotation enhancement
  Z = x1 ∥ x2 ∥ x3 ∥ x4     (pack 4×16 → 64-bit)
  Z = ROL64(Z, k_t)          (k_t = 1 + t mod 15)
  unpack Z → {x1,x2,x3,x4}

Step 3: Post-processing
  u_t = x1 ∥ x2 ∥ x3 ∥ x4
  y_t = u_t ⊕ ROL64(u_{t-1}, 17) ⊕ ROL64(u_{t-2}, 43)
  prng_out = y_t
```

## 2. Chaotic System Equations

The underlying 4D map (R1-LY, A_odd=371, B_even=50):

\[
\left\{
\begin{alignedat}{3}
x_1(i+1) &= 371\,x_1(i) + 50\,x_4(i-2) \\
x_2(i+1) &= 373\,x_2(i) + 48\,x_4(i-1) \\
x_3(i+1) &= 375\,x_3(i) + 46\,x_4(i) \\
x_4(i+1) &= 111\,x_2(i) + 4\bigl(x_3(i)+x_4(i)\bigr)\bigl(1-(x_3(i)+x_4(i))\bigr)
\end{alignedat}
\right.
\]

All operations modulo 2^16. Lyapunov exponents: [5.59, 5.60, 5.65, 2.60] (all > 0).

## 3. Interface

| Port | Width | Direction | Protocol | Description |
|---|---|---|---|---|
| `s_axi_CONTROL_*` | — | — | AXI-Lite | Control/status registers |
| `ctrl` | 1 | IN (reg 0x00[0]) | — | 1=start, 0=stop (hold state) |
| `init_x1` | 16 | IN (reg 0x10) | — | Initial x₁ |
| `init_x2` | 16 | IN (reg 0x14) | — | Initial x₂ |
| `init_x3` | 16 | IN (reg 0x18) | — | Initial x₃ |
| `init_x4` | 16 | IN (reg 0x1C) | — | Initial x₄ |
| `prng_out` | 64 | OUT | ap_none | Pseudo-random output word |
| `prng_out_valid` | 1 | OUT | ap_none | High when output is valid |
| `ap_clk` | 1 | IN | — | System clock |
| `ap_rst_n` | 1 | IN | — | Active-low reset |

**Register map** (AXI-Lite, 32-bit aligned):

| Offset | Register | Bits | Access | Description |
|---|---|---|---|---|
| 0x00 | CTRL | [0] | R/W | 1=start output, 0=stop |
| 0x10 | INIT_X1 | [15:0] | R/W | Initial value x₁ |
| 0x14 | INIT_X2 | [15:0] | R/W | Initial value x₂ |
| 0x18 | INIT_X3 | [15:0] | R/W | Initial value x₃ |
| 0x1C | INIT_X4 | [15:0] | R/W | Initial value x₄ |

**Default**: If all four init values are zero, internal defaults [1000, 2000, 3000, 4000] are used.

## 4. Resource Utilization

Target: xc7z020clg400-1, 3-stage pipeline, DSP-pipelined. Vivado-verified at 14.2ns (70.4 MHz).

| Resource | HLS Estimate | Vivado Synthesis | Available | Util% |
|---|---|---|---|---|
| **DSP48** | 1 | **1** | 220 | 0.5% |
| LUT | 1,510 | **784** | 53,200 | 1.5% |
| FF | 837 | **904** | 106,400 | 0.9% |
| BRAM | 0 | 0 | 280 | 0% |
| CARRY4 | — | ~20 | 13,300 | 0.2% |

Note: FF increase from 699→904 reflects the 3-stage pipeline registers. LUT decrease from 903→784 from DSP pipelining.

### 4.1 Pipeline Stage Breakdown

| Stage | Content | Logic Levels | Registered Output |
|---|---|---|---|
| Stage 1 | Shift-add chain (7 const mults) + DSP logistic | ~5 | x1_next..x4_next, temp_reg |
| Stage 2 | State update + delay chain + bit-rotation (64-bit barrel) | ~4 | x1_rot..s0_rot, rot_cnt |
| Stage 3 | Post-processor (64-bit ROTL17⊕ROTL43⊕concat) | ~2 | prng_out, u_prev1/2 |

## 5. Performance

| Metric | Value |
|---|---|
| HLS target | 10 ns (keeps II=1 scheduling) |
| Vivado clock | **14.2 ns (70.4 MHz)** |
| Setup WNS | **+0.209 ns** — PASS |
| Failing endpoints | **0** |
| Critical path | 13.48 ns (logic 6.8 + route 6.7) |
| Logic levels | 11 |
| Throughput | **563 MB/s (4.5 Gbps)** |
| C Simulation | **Pass** (3 tests, DSP-pipelined golden model) |

## 6. Design Optimizations Applied

| Optimization | Technique | Savings |
|---|---|---|
| Constant-coeff multiplication | Shift-and-add decomposition (7 coefs) | 7 DSP → 0 DSP |
| Bit-rotation | Wiring-based ROL (no logic) | 0 cost |
| Post-processor ROL | Fixed rotations = wiring | 0 cost |
| Logistic core multiply | DSP inference (single DSP48) | Efficient 16×16 |
| Pipeline | II=1, fully pipelined | 1 output/cycle |
| DSP budget | `#pragma HLS ALLOCATION … limit=1` | Prevents shift-and-add → DSP reversal |
| Timing closure | Iterated OOC synthesis with clock sweep (10→13→15.4→16.0ns) | Converged at 62.5 MHz |

## 7. Power

| Metric | Value |
|---|---|
| Total on-chip power | **0.11 W** |
| Dynamic power | 0.007 W |
| Device static | 0.103 W |
| Junction temperature | 26.3 °C @ 25 °C ambient |
| Confidence level | Medium (synthesized only) |

The design is power-negligible: total consumption is dominated by the device's static leakage (94%).

## 8. Verification Status

| Test | Method | Result |
|---|---|---|
| Golden model comparison | 1000 consecutive outputs vs C model | Pass |
| All-zero init → defaults | init=(0,0,0,0) → uses [1000,2000,3000,4000] | Pass |
| STOP/START control | ctrl=0 → out=0, invalid; ctrl=1 → resume | Pass |
| Custom init values | init=(100,200,300,400) → golden match | Pass |
| Stream cipher demo | Encrypt + decrypt with keystream (HLS vs Python golden model) | Pass |
| Stream cipher alignment | Python golden model ciphertext == HLS CSim ciphertext | **100% bit-exact** |
| **FPGA Physical Validation** | **8,180 words on XC7Z020 (ACZ702), ILA capture vs cycle-accurate RTL model** | **✅ PASS — 0 errors, BER=0** |

## 9. Files

| File | Description |
|---|---|
| `hw/src/hls_src/prng_top.cpp` | Top-level PRNG — 3-stage pipeline (canonical) |
| `hw/src/hls_src/prng_top.h` | Interface + register map |
| `hw/src/hls_src/chaos_core.h` | Type definitions, parameter includes |
| `hw/src/hls_src/shift_mul.h` | Shift-and-add constant multipliers |
| `hw/src/org_src/` | Original v1 sources (reference, 62.5 MHz) |
| `hw/params/chaos_params.h` | Auto-generated R1-LY #defines |
| `hw/testbench/tb_prng.cpp` | C testbench with 3-stage golden model |
| `hw/generate_params.py` | Python → chaos_params.h + shift_mul.h |
| `hw/generate_vectors.py` | Python → test vectors (.dat) |
| `hw/sync_to_hls.sh` | Copy hls_src/ → chaos_prng_fast HLS project |
| `hw/pull_from_hls.sh` | Pull reports + RTL + config ← HLS project |
| `tools/analyze_reports.py` | Parse HLS + Vivado reports → summary table |
