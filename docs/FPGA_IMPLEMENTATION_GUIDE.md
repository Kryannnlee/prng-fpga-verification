# Chaos PRNG — FPGA Implementation Guide

**Target**: Xilinx Zynq-7020 (xc7z020clg400-1) | **Toolchain**: Vitis HLS 2024.1 → Vivado 2024.1

## 1. Clock and Timing

| Parameter | Value | Notes |
|---|---|---|
| Target frequency | 100 MHz (10.00 ns) | |
| Post-synthesis estimate | 12.999 ns (~77 MHz) | As-reported by HLS; actual P&R may differ |
| Recommended constraint | `create_clock -period 10.0 [get_ports ap_clk]` | |
| Clock uncertainty | 2.70 ns | HLS estimate; tighten to 0.5 ns in Vivado for P&R |

```
# XDC constraint
create_clock -period 10.000 -name ap_clk [get_ports ap_clk]
set_clock_uncertainty 0.500 -setup [get_clocks ap_clk]
set_clock_uncertainty 0.200 -hold  [get_clocks ap_clk]
```

If timing fails at 100 MHz in Vivado implementation, constrain to 77 MHz and re-run HLS synthesis:

```tcl
# In scripts/run_hls.tcl
set clock_period 13  ;# ~77 MHz
```

## 2. Reset Strategy

| Signal | Polarity | Recommendation |
|---|---|---|
| `ap_rst_n` | Active-low | Drive from system reset controller; hold low ≥ 16 cycles after configuration |

**Power-on sequence**:
1. Assert `ap_rst_n = 0` during FPGA configuration
2. De-assert `ap_rst_n = 1` after clock stabilizes
3. Write initial values to AXI-Lite registers (or use default zeros)
4. Set `ctrl = 1` — PRNG starts outputting

**Warm reset** (no reconfiguration):
1. Set `ctrl = 0` (stop)
2. Pulse `ap_rst_n` low for ≥ 4 cycles
3. Re-program init values if needed
4. Set `ctrl = 1`

## 3. Pin Planning

| Signal Group | Recommended I/O Standard | Drive Strength | Slew |
|---|---|---|---|
| `ap_clk` | LVCMOS33 | 12 mA | FAST |
| `ap_rst_n` | LVCMOS33 | 4 mA | SLOW |
| `prng_out[63:0]` | LVCMOS33 | 8 mA | FAST |
| `prng_out_valid` | LVCMOS33 | 8 mA | FAST |
| AXI-Lite (if PS-connected) | N/A (internal M_AXI_GP) | — | — |

If connecting `prng_out` to external pins, add output register stage in user wrapper:

```verilog
// Recommended: register outputs for timing
reg [63:0] prng_out_reg;
reg        prng_out_valid_reg;
always @(posedge ap_clk) begin
    prng_out_reg       <= prng_out;
    prng_out_valid_reg <= prng_out_valid;
end
```

## 4. AXI-Lite Integration (PS-PL)

Connect the IP's `s_axi_CONTROL` to Zynq PS `M_AXI_GP0` via Vivado Block Design:

```
PS (ARM Cortex-A9)
  │
  M_AXI_GP0 (32-bit)
  │
  AXI Interconnect
  │
  s_axi_CONTROL (chaos_prng IP)
```

**Register access from Linux userspace** (via `/dev/mem` or UIO):

```c
// Example: configure and start PRNG
#define PRNG_BASE  0x43C00000  // assigned by Vivado Address Editor

void prng_init(uint32_t *base) {
    // Write initial values
    *(base + 0x10/4) = 1000;   // INIT_X1
    *(base + 0x14/4) = 2000;   // INIT_X2
    *(base + 0x18/4) = 3000;   // INIT_X3
    *(base + 0x1C/4) = 4000;   // INIT_X4
    // Start
    *(base + 0x00/4) = 1;      // CTRL = 1
}

void prng_stop(uint32_t *base) {
    *(base + 0x00/4) = 0;      // CTRL = 0
}
```

## 5. Standalone (PL-only, no PS)

If not using the ARM cores, instantiate the IP directly in RTL with a simple state machine for configuration:

```verilog
// Power-on configuration sequencer
reg [3:0] cfg_state;
always @(posedge ap_clk or negedge ap_rst_n) begin
    if (!ap_rst_n) begin
        cfg_state <= 0;
        ctrl      <= 0;
        init_x1   <= 0;
        init_x2   <= 0;
        init_x3   <= 0;
        init_x4   <= 0;
    end else begin
        case (cfg_state)
            0: begin
                // Wait after reset
                cfg_state <= 1;
            end
            1: begin
                // Use defaults (all zeros → internal defaults)
                // Or set custom values:
                // init_x1 <= 16'd1000;
                // init_x2 <= 16'd2000;
                // init_x3 <= 16'd3000;
                // init_x4 <= 16'd4000;
                cfg_state <= 2;
            end
            2: begin
                ctrl <= 1;     // Start PRNG
                cfg_state <= 2; // Stay here
            end
        endcase
    end
end
```

## 6. IP Integration in Vivado

```tcl
# 1. Add IP repository
set_property ip_repo_paths ./output/ip [current_project]
update_ip_catalog

# 2. Instantiate in block design
create_bd_cell -type ip -vlnv xilinx.com:hls:prng_top:1.0 prng_top_0

# 3. Connect clock and reset
connect_bd_net [get_bd_pins prng_top_0/ap_clk] [get_bd_pins processing_system7_0/FCLK_CLK0]
connect_bd_net [get_bd_pins prng_top_0/ap_rst_n] [get_bd_pins rst_ps7_0/peripheral_aresetn]

# 4. Connect AXI (if using PS)
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master "/processing_system7_0/M_AXI_GP0" Clk "Auto"} [get_bd_intf_pins prng_top_0/s_axi_CONTROL]

# 5. Assign address
assign_bd_address
```

## 7. Throughput Considerations

| Scenario | Throughput | Notes |
|---|---|---|
| 100 MHz, II=1 | 800 MB/s (6.4 Gbps) | Ideal; may not close timing |
| 77 MHz, II=1 | 616 MB/s (4.9 Gbps) | Conservative estimate from HLS |
| 50 MHz, II=1 | 400 MB/s (3.2 Gbps) | Ultra-conservative |

**Bottleneck analysis**:
- Critical path likely in: 64-bit barrel shifter (bit-rotation) + post-processor XOR tree
- If timing fails, add `#pragma HLS LATENCY` to pipeline the rotation stages
- The logistic core DSP48 has built-in pipelining; ensure it's fully registered

## 8. Post-Route Verification

After Vivado implementation:

```tcl
# Check timing
report_timing_summary -file timing.rpt

# Check utilization
report_utilization -file utilization.rpt

# Verify no DSP inference issues
report_dsp_utilization -file dsp.rpt
```

**Expected post-route resources**: Within +15% of HLS estimates.

## 9. Security Considerations

| Concern | Mitigation |
|---|---|
| Initial state leakage | Use key derivation (`seed_from_key` logic) to mix user key into init values |
| Power analysis | Add random dummy cycles (ctrl=0 for random intervals) |
| Keystream reuse | Never use same (init, ctrl) pair for two encryptions; increment nonce in init |
| FPGA bitstream extraction | Encrypt bitstream (AES key in BBRAM/eFUSE); use authentication |

## 10. Critical Design Rule: WSTRB Must Be All-Bytes-Enabled

### Verilog Width Default Trap

In Verilog, `reg` without explicit `[N:0]` range defaults to **1-bit**. This is a classic bug source:

```verilog
// BROKEN — axi_wstrb is 1-bit!
reg         axi_wstrb = 4'hF;  // Verilog: 4'hF truncated to 1'b1

// FIXED — explicit 4-bit wire with continuous assignment
wire [3:0]  axi_wstrb;
assign      axi_wstrb = 4'hF;  // All 4 bytes enabled
```

When a 1-bit signal drives a 4-bit port, the upper 3 bits float/pad to 0:
- `WSTRB = 4'b0001` → only byte 0 writable → 16-bit seeds truncated to 8 bits
- Seeds: `1000 (0x03E8)` → written as `0xE8` (232) — loses upper byte

**This is the root cause that delayed FPGA physical validation by multiple debug sessions.** Always specify explicit widths.

### Rule

> **AXI-Lite WSTRB must be `4'b1111` for all PRNG seed register writes.** Seeds are 16-bit values in 32-bit registers. Writing only byte 0 silently truncates the upper half.

See [FPGA_PHYSICAL_VALIDATION_REPORT.md](FPGA_PHYSICAL_VALIDATION_REPORT.md) for the full debug trace.

## 11. Quick Reference

```bash
# Full HLS flow
# ~/verilog-learning/ = designer HLS platform (ToDesk remote: contact designer)
cd ~/verilog-learning/chaos_prng
make csim      # Verify functionality
make csynth    # Resource + timing estimate
make cosim     # RTL co-simulation
make ip        # Export IP → output/ip/

# Vivado implementation (after make ip)
make impl      # Synthesis + P&R + bitstream

# Modify R1-LY parameters
cd hd_chaos_project
python hw/generate_params.py --A_odd 371 --B_even 50
bash hw/sync_to_hls.sh
```
