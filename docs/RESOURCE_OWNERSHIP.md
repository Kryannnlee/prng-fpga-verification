# Chaos PRNG — Resource Ownership & Handoff

**Date**: 2026-05-13 | **IP Version**: 2.1 (3-stage pipeline, **100 MHz, 800 MB/s**) | **Top Performer**: A_odd=371

---

## Overview

```
┌──────────────────────────────────────────────────────────┐
│                    Chaos PRNG IP                          │
│                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────┐  │
│  │   Hardware   │    │   Embedded   │    │Application │  │
│  │   Engineer   │───→│   Software   │───→│  Software  │  │
│  │   (FPGA)     │    │   Engineer   │    │  Developer │  │
│  └──────────────┘    └──────────────┘    └────────────┘  │
│        │                    │                    │        │
│   • HLS C++          • AXI-Lite driver    • Python PRNG  │
│   • IP packaging     • C API              • Stream cipher│
│   • Timing/Pinout    • Linux kernel       • Keystream    │
│   • Vivado flow      • Bare-metal         • NIST testing │
└──────────────────────────────────────────────────────────┘
```

---

## 1. Hardware Engineer (FPGA)

**Responsibility**: Synthesize, place-and-route, and deliver the IP core as a bitstream or packaged IP.

### 1.1 Deliverables

| File | Path | Description |
|---|---|---|
| `prng_top.cpp` | `hw/src_371/prng_top.cpp` | Top-level HLS C++ — 3-stage pipeline (A_odd=371) |
| `prng_top.h` | `hw/src_371/prng_top.h` | Interface + register map constants |
| `chaos_core.h` | `hw/src_371/chaos_core.h` | Type defs, param includes, constants |
| `shift_mul.h` | `hw/src_371/shift_mul.h` | Shift-and-add constant multipliers (auto-gen) |
| `chaos_params.h` | `hw/src_371/chaos_params.h` | #define A_J1_0=371, B_VAL=111… |
| `tb_prng.cpp` | `hw/testbench/tb_prng.cpp` | C testbench with 3-stage golden model |
| `golden_prng.py` | `hw/src_371/golden_prng.py` | Python ctypes wrapper — 100% HLS bit-exact |
| (reference) | `hw/src/hls_src/` | A_odd=271 variant (70.4 MHz) |
| (reference) | `hw/src/org_src/` | Original v1 sources (62.5 MHz) |

### 1.2 Build Artifacts (from HLS `make ip`)

| File | Path | Description |
|---|---|---|
| `chaos_prng_371_ip.zip` | `output/ip/chaos_prng_371_ip.zip` | Packaged IP core for Vivado |
| Verilog RTL (3 files) | `solution1/impl/ip/hdl/verilog/` | Synthesized Verilog |
| VHDL RTL (3 files) | `solution1/impl/ip/hdl/vhdl/` | Synthesized VHDL |

### 1.3 Key Configuration Parameters

```c
// In chaos_params.h — modify before synthesis
#define A_J1_0  371    // Expansion coef x1 (odd)
#define A_J1_1  373    // Expansion coef x2 (odd)
#define A_J1_2  375    // Expansion coef x3 (odd)
#define A_JN_0  50     // Coupling coef (even)
#define A_JN_1  48     // Coupling coef (even)
#define A_JN_2  46     // Coupling coef (even)
#define B_VAL   111    // Feedback coef
```

```python
# Regenerate with:
python hw/generate_params.py --A_odd 371 --B_even 50
bash hw/sync_to_hls.sh
```

### 1.4 Synthesis Constraints

```
Clock:    100 MHz (10.0 ns) — Vivado-verified, WNS=+0.269ns
DSP:      Limit 1 via #pragma HLS ALLOCATION
Pipeline: 3-stage (shift-add → rotation → post-process)
LUT:      1,042 (2.0%)
FF:       707 (0.7%)
Power:    0.13 W
```

### 1.5 FPGA Engineer Checklist

- [ ] HLS project: `~/verilog-learning/chaos_prng_371/`
- [ ] Run `make csim` — verify functionality (WARMUP=2000, golden model match)
- [ ] Run `make csynth` — check resource/timing estimates
- [ ] Run `make cosim` — verify RTL matches C model
- [ ] Run `make ip` — export IP core → `output/ip/chaos_prng_371_ip.zip`
- [ ] Add IP to Vivado project via `ip_repo_paths`
- [ ] Connect `ap_clk` to PS FCLK0 (100 MHz) or external oscillator
- [ ] Connect `ap_rst_n` to system reset
- [ ] Wire AXI-Lite to PS `M_AXI_GP0`
- [ ] Add XDC: `create_clock -period 10.0 -name ap_clk [get_ports ap_clk]`
- [ ] Run `make impl` (or Vivado synthesis + P&R)
- [ ] Verify post-route timing closure (WNS ≥ 0 @ 100 MHz)
- [ ] Generate bitstream

---

## 2. Embedded Software Engineer

**Responsibility**: Write the low-level driver that configures the PRNG IP and reads output data.

### 2.1 HLS-Auto-Generated Driver

| File | Path |
|---|---|
| Driver source (10 files) | `solution1/impl/ip/drivers/prng_top_v1_0/` |

```c
// Auto-generated header: xprng_top_hw.h
// Contains: register offset macros, bit masks
#define XPRNG_TOP_CONTROL_ADDR_CTRL_DATA    0x00
#define XPRNG_TOP_CONTROL_ADDR_INIT_X1_DATA 0x10
#define XPRNG_TOP_CONTROL_ADDR_INIT_X2_DATA 0x14
#define XPRNG_TOP_CONTROL_ADDR_INIT_X3_DATA 0x18
#define XPRNG_TOP_CONTROL_ADDR_INIT_X4_DATA 0x1c
```

### 2.2 Register Map (for driver author)

| Offset | Name | Bits | Access | Description |
|---|---|---|---|---|
| 0x00 | CTRL | [0] | R/W | 1 = start output, 0 = stop (hold state) |
| 0x10 | INIT_X1 | [15:0] | R/W | Initial x₁ (default 1000 if all zeros) |
| 0x14 | INIT_X2 | [15:0] | R/W | Initial x₂ |
| 0x18 | INIT_X3 | [15:0] | R/W | Initial x₃ |
| 0x1C | INIT_X4 | [15:0] | R/W | Initial x₄ |

### 2.3 Bare-Metal C Driver (reference implementation)

```c
// prng_driver.h — minimal driver for bare-metal / RTOS
#ifndef PRNG_DRIVER_H
#define PRNG_DRIVER_H

#include <stdint.h>

typedef struct {
    volatile uint32_t* base_addr;   // AXI-Lite base (from Vivado Address Editor)
} prng_dev_t;

// Initialize device handle
void prng_open(prng_dev_t* dev, uint32_t base);

// Configure initial values and start
void prng_start(prng_dev_t* dev,
                uint16_t x1, uint16_t x2,
                uint16_t x3, uint16_t x4);

// Stop output (hold state, can resume)
void prng_stop(prng_dev_t* dev);

// Check if output is valid
int prng_output_valid(prng_dev_t* dev);

// Read one 64-bit output word (blocking — waits for valid)
uint64_t prng_read(prng_dev_t* dev);

// Read N words into buffer
void prng_read_buf(prng_dev_t* dev, uint64_t* buf, uint32_t n);

#endif
```

```c
// prng_driver.c — implementation
#include "prng_driver.h"

#define REG_CTRL     0x00
#define REG_INIT_X1  0x10
#define REG_INIT_X2  0x14
#define REG_INIT_X3  0x18
#define REG_INIT_X4  0x1C

void prng_open(prng_dev_t* dev, uint32_t base) {
    dev->base_addr = (volatile uint32_t*)base;
}

void prng_start(prng_dev_t* dev,
                uint16_t x1, uint16_t x2,
                uint16_t x3, uint16_t x4) {
    // Write init values (use zeros for defaults)
    dev->base_addr[REG_INIT_X1 >> 2] = x1;
    dev->base_addr[REG_INIT_X2 >> 2] = x2;
    dev->base_addr[REG_INIT_X3 >> 2] = x3;
    dev->base_addr[REG_INIT_X4 >> 2] = x4;
    // Start
    dev->base_addr[REG_CTRL >> 2] = 1;
}

void prng_stop(prng_dev_t* dev) {
    dev->base_addr[REG_CTRL >> 2] = 0;
}

int prng_output_valid(prng_dev_t* dev) {
    // prng_out_valid is an ap_none port, not in register map.
    // Read it via GPIO or custom IP wrapper.
    // For polling-based usage, assume data is always valid when ctrl=1.
    return (dev->base_addr[REG_CTRL >> 2] & 1);
}

uint64_t prng_read(prng_dev_t* dev) {
    // prng_out[63:0] is an ap_none output port.
    // In the default HLS export, this is a top-level port.
    // Wrap it: add a 64-bit register in a small RTL wrapper that
    // exposes prng_out via AXI or memory-mapped register.
    //
    // For direct connection (PL-only):
    //   extern volatile uint64_t* const PRNG_OUT = (uint64_t*)0x43C00020;
    //   return *PRNG_OUT;
    return 0;  // Stub — implement per integration
}
```

### 2.4 Embedded Engineer Checklist

- [ ] Obtain base address from Vivado Address Editor
- [ ] Port the auto-generated driver or write custom driver
- [ ] Verify register read/write via JTAG (XSCT `mrd`/`mwr`)
- [ ] Test init → start → read loop
- [ ] Test stop → resume
- [ ] Integrate with application data path (DMA, FIFO, or direct read)
- [ ] If using Linux: write UIO or `/dev/mem` userspace driver

---

## 3. Application Software Developer

**Responsibility**: Use the PRNG output for cryptographic or statistical applications.

### 3.1 Python Reference Model (algorithm validation)

| File | Path | Purpose |
|---|---|---|
| `golden_prng.py` | `hw/src_371/golden_prng.py` | **100% HLS bit-exact (C shared lib)** |
| `prng_export_371.py` | `tests/prng_export_371.py` | GUI export — HLS-verified output |
| `prng_fast_model.py` | `hw/src/prng_fast_model.py` | Python 3-stage pipeline model |
| `prng.py` | `src/chaos/application/prng/prng.py` | ChaosPRNG class (software PRNG) |
| `prng_export.py` | `src/chaos/application/prng/prng_export.py` | GUI export with A_odd dialog |

**For HLS-verified output**, use the C golden model:
```python
from hw.src_371.golden_prng import ChaosPRNG371
prng = ChaosPRNG371()                              # A_odd=371, warmup=2000
w = prng.next()                                    # 100% bit-exact with HLS
data = prng.generate_bytes(1024)
```

```python
from chaos.application.prng import ChaosPRNG

# Software PRNG (Python native, algorithm differs from hardware)
prng = ChaosPRNG.from_r1_ly(A_odd=371, B_even=50)
prng.set_state(1000, 2000, 3000, 4000)            # auto warmup=2000
words = prng.generate(10000)
```

### 3.2 Stream Cipher Usage (Python reference)

```python
from chaos.application.prng import ChaosPRNG

class ChaosStreamCipher:
    """XOR-based stream cipher using ChaosPRNG as keystream generator."""

    def __init__(self, key: int, nonce: int = 0):
        """Initialize with a key and optional nonce."""
        self.prng = ChaosPRNG.from_r1_ly(A_odd=271, B_even=50)
        # Derive init state from key + nonce
        mixed = (key ^ (nonce << 32)) & 0xFFFFFFFFFFFFFFFF
        self.prng.seed_from_key(mixed)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt by XOR with keystream."""
        keystream = self.prng.generate_bytes(len(plaintext))
        return bytes(p ^ k for p, k in zip(plaintext, keystream))

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt = encrypt (XOR symmetry)."""
        return self.encrypt(ciphertext)

# Usage
cipher = ChaosStreamCipher(key=0xDEADBEEFCAFE1234)
ct = cipher.encrypt(b"Attack at dawn")
# ... transmit ct ...
cipher2 = ChaosStreamCipher(key=0xDEADBEEFCAFE1234)
pt = cipher2.decrypt(ct)  # b"Attack at dawn"
```

### 3.3 C Stream Cipher (hardware-accelerated, reference)

```c
// chaos_stream_cipher.h
#include "prng_driver.h"
#include <string.h>

// Encrypt buffer in-place using hardware PRNG
void chaos_encrypt(prng_dev_t* dev, uint8_t* buf, size_t len) {
    prng_start(dev, 0, 0, 0, 0);  // default init
    for (size_t i = 0; i < len; i += 8) {
        uint64_t key_word = prng_read(dev);
        for (int b = 0; b < 8 && (i + b) < len; b++) {
            buf[i + b] ^= (uint8_t)(key_word >> (b * 8));
        }
    }
    prng_stop(dev);
}

// Decrypt = encrypt (same function)
#define chaos_decrypt chaos_encrypt
```

### 3.4 NIST Statistical Testing

```bash
# Generate HLS-verified test sequence
python tests/prng_export_371.py
# → saves raw binary file (100% bit-exact with hardware)

# Run NIST STS
cd nist_sts
./assess 1000000
```

### 3.5 Application Developer Checklist

- [ ] Use `ChaosPRNG371` (golden model) for HLS-verified output
- [ ] **Never reuse** the same (init_x1..x4) for two encryptions
- [ ] Warmup: 2000 outputs are automatically discarded on init
- [ ] For NIST testing: generate 10^8 bits via `prng_export_371.py`
- [ ] For hardware integration: coordinate with embedded engineer on data path

---

## 4. Cross-Team Interface Contract

### 4.1 Hardware → Software

| Item | Value | Owner |
|---|---|---|
| IP name | `prng_top` | HW |
| IP version | 2.1 (3-stage pipeline, A_odd=371, 100MHz) | HW |
| Vivado IP repo path | `output/ip/` | HW |
| Register base address | Assigned by Vivado Address Editor | HW |
| `prng_out` port width | 64-bit | HW |
| Output valid signal | `prng_out_valid` (ap_none) | HW |
| Max clock frequency | **100 MHz** (WNS=+0.269ns, Vivado-verified) | HW |

### 4.2 Software → Hardware

| Item | Value | Owner |
|---|---|---|
| Required throughput | N/A (application-defined) | SW |
| Preferred AXI data width | 32-bit (Zynq PS default) | SW |
| Interrupt or polling? | Polling (current design) | SW |
| Buffer size (if DMA) | TBD | SW |

### 4.3 Application → Both

| Item | Value | Owner |
|---|---|---|
| Initial values (key) | 4 × 16-bit, not all zero | App |
| Sequence length per key | Application-defined | App |
| Key rotation policy | New key per message (stream cipher security) | App |
