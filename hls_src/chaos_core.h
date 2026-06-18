#ifndef CHAOS_CORE_H
#define CHAOS_CORE_H

#include <ap_int.h>

// ==========================================================================
// 4D R1-LY Chaotic Map — Core Types and Interface
// ==========================================================================
// Bit-width: 16-bit fixed-point on Z_{2^16}
// State:    6 × 16-bit registers (x1, x2, x3, s0, s1, s2)
//           where s0 = x4, s1/s2 are delay-chain entries
// ==========================================================================

#define WORD_SIZE  16
#define STATE_DIM  6    // 2n-2 for n=4: x1,x2,x3 + s0,s1,s2
#define N_DIMS     4

typedef ap_uint<WORD_SIZE> chaos_word_t;

// --------------------------------------------------------------------------
// R1-LY parameters — include auto-generated header
// --------------------------------------------------------------------------
#include "chaos_params.h"

// Fallback defaults if params header is not used

#ifndef A_J1_0
#define A_J1_0  271
#define A_J1_1  273
#define A_J1_2  275
#endif

#ifndef A_JN_0
#define A_JN_0  50
#define A_JN_1  48
#define A_JN_2  46
#endif

#ifndef B_VAL
#define B_VAL    81
#endif

// --------------------------------------------------------------------------
// Top-level: one iteration of the 4D enhanced chaotic map
// --------------------------------------------------------------------------
// Input:  (none — state is maintained internally via static registers)
// Output: chaos_word_t state_out[4] — {x1, x2, x3, x4}
//
// This function performs ONE clock-cycle iteration:
//   1. Reads internal state (6 × 16-bit registers)
//   2. Computes next x1,x2,x3,x_n via R1-LY equations
//   3. Shifts delay chain
//   4. Applies bit-rotation enhancement (pack → ROL → unpack)
//   5. Writes back internal state
//   6. Outputs physical state {x1,x2,x3,x4}
// --------------------------------------------------------------------------

void chaos_iterate(
    chaos_word_t &x1_out,
    chaos_word_t &x2_out,
    chaos_word_t &x3_out,
    chaos_word_t &x4_out
);

#endif // CHAOS_CORE_H
