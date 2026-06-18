#ifndef PRNG_TOP_H
#define PRNG_TOP_H

#include <ap_int.h>

// ==========================================================================
// Chaos PRNG — Top-Level Module with AXI-Lite Control Interface
// ==========================================================================
//
// Register map (AXI-Lite, base address offsets):
//   0x00  CTRL       [0]  enable (1=start output, 0=stop)
//   0x10  INIT_X1    16-bit initial x1 (default: 1000 if all four are 0)
//   0x14  INIT_X2    16-bit initial x2
//   0x18  INIT_X3    16-bit initial x3
//   0x1C  INIT_X4    16-bit initial x4
//
// Outputs:
//   prng_out[63:0]  64-bit pseudo-random word
//   prng_out_valid   1 when prng_out is valid
// ==========================================================================

// ---- Default initial values (used when all 4 init inputs are 0) ----
#define DEFAULT_X1  1000
#define DEFAULT_X2  2000
#define DEFAULT_X3  3000
#define DEFAULT_X4  4000

void prng_top(
    // Control
    ap_uint<1>  ctrl,           // 1=start output, 0=stop (hold state)

    // Initial values (16-bit each, external input)
    ap_uint<16> init_x1,
    ap_uint<16> init_x2,
    ap_uint<16> init_x3,
    ap_uint<16> init_x4,

    // Output
    ap_uint<64> &prng_out,
    ap_uint<1>  &prng_out_valid
);

#endif // PRNG_TOP_H
