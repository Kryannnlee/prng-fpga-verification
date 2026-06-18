#include "prng_top.h"
#include "chaos_core.h"
#include "shift_mul.h"

// ==========================================================================
// Chaos PRNG — 3-Stage Pipelined (target: 100 MHz, II=1)
// ==========================================================================
//
// Pipeline stages:
//   Stage 1: shift-add chain → registered x_next, logistic
//   Stage 2: state feedback + bit-rotation → registered rot outputs
//   Stage 3: post-processor → output
//
// Each stage has its own register bank. State feedback uses the
// registered outputs from the PREVIOUS cycle (1-cycle delay in the
// feedback path — increases dynamical complexity).
// ==========================================================================

void prng_top(
    ap_uint<1>  ctrl,
    ap_uint<16> init_x1, ap_uint<16> init_x2,
    ap_uint<16> init_x3, ap_uint<16> init_x4,
    ap_uint<64> &prng_out,
    ap_uint<1>  &prng_out_valid)
{
#pragma HLS INTERFACE s_axilite port=return   bundle=CONTROL
#pragma HLS INTERFACE s_axilite port=ctrl     bundle=CONTROL
#pragma HLS INTERFACE s_axilite port=init_x1  bundle=CONTROL
#pragma HLS INTERFACE s_axilite port=init_x2  bundle=CONTROL
#pragma HLS INTERFACE s_axilite port=init_x3  bundle=CONTROL
#pragma HLS INTERFACE s_axilite port=init_x4  bundle=CONTROL
#pragma HLS INTERFACE ap_none  port=prng_out
#pragma HLS INTERFACE ap_none  port=prng_out_valid
#pragma HLS PIPELINE II=1
#pragma HLS ALLOCATION operation instances=mul limit=1

    // ---- Stage 1 registers: shift-add results ----
    static chaos_word_t x1_next_reg = 0;
    static chaos_word_t x2_next_reg = 0;
    static chaos_word_t x3_next_reg = 0;
    static chaos_word_t x4_next_reg = 0;

    // ---- Stage 2 registers: current state + rotation output ----
    static chaos_word_t x1_reg = 0, x2_reg = 0, x3_reg = 0;
    static chaos_word_t s0_reg = 0, s1_reg = 0, s2_reg = 0;
    static chaos_word_t x1_rot = 0, x2_rot = 0, x3_rot = 0, s0_rot = 0;
    static ap_uint<4>  rot_cnt = 0;
    static bool         init_done = false;

    // DSP pipeline register
    static ap_uint<32> temp_reg = 0;

    // ---- Stage 3 registers: post-processor ----
    static ap_uint<64> u_prev1 = 0;
    static ap_uint<64> u_prev2 = 0;
    static bool        pp_init = false;

    if (ctrl == 0) {
        prng_out = 0;
        prng_out_valid = 0;
        return;
    }

    // Lazy initialisation (only sets state registers for first iteration)
    if (!init_done) {
        bool all_zero = (init_x1 == 0) && (init_x2 == 0) &&
                        (init_x3 == 0) && (init_x4 == 0);
        if (all_zero) {
            x1_reg = DEFAULT_X1;  x2_reg = DEFAULT_X2;
            x3_reg = DEFAULT_X3;  s0_reg = DEFAULT_X4;
        } else {
            x1_reg = init_x1;  x2_reg = init_x2;
            x3_reg = init_x3;  s0_reg = init_x4;
        }
        s1_reg = 0;  s2_reg = 0;
        rot_cnt = 0;
        x1_next_reg = 0;  x2_next_reg = 0;
        x3_next_reg = 0;  x4_next_reg = 0;
        temp_reg = 0;
        x1_rot = x1_reg;  x2_rot = x2_reg;
        x3_rot = x3_reg;  s0_rot = s0_reg;
        u_prev1 = 0;  u_prev2 = 0;
        pp_init = false;
        init_done = true;
    }

    // ================================================================
    // Stage 1: Shift-add chain (registered at end)
    // ================================================================
    chaos_word_t x1n = mul_A_J1_0(x1_reg) + mul_A_JN_0(s2_reg);
    chaos_word_t x2n = mul_A_J1_1(x2_reg) + mul_A_JN_1(s1_reg);
    chaos_word_t x3n = mul_A_J1_2(x3_reg) + mul_A_JN_2(s0_reg);

    // Logistic core with DSP pipeline
    chaos_word_t z    = (ap_uint<16>)(x3_reg + s0_reg);
    chaos_word_t x4n  = mul_B_VAL(x2_reg) + (ap_uint<16>)(temp_reg >> 14);
    ap_uint<16> inv_z = (~z) & 0xFFFF;
    temp_reg = (ap_uint<32>)z * (ap_uint<32>)inv_z;

    // Register stage 1 outputs
    x1_next_reg = x1n;
    x2_next_reg = x2n;
    x3_next_reg = x3n;
    x4_next_reg = x4n;

    // ================================================================
    // Stage 2: State update + delay chain + bit-rotation (registered)
    // ================================================================
    // State update uses registered next-values from stage 1
    s2_reg = s1_reg;
    s1_reg = s0_reg;
    s0_reg = x4_next_reg;
    x1_reg = x1_next_reg;
    x2_reg = x2_next_reg;
    x3_reg = x3_next_reg;

    // Bit-rotation on the updated state
    chaos_word_t r1 = x1_reg, r2 = x2_reg, r3 = x3_reg, r0 = s0_reg;
    ap_uint<4> k = 1 + rot_cnt;
    {
        ap_uint<64> Z = ((ap_uint<64>)r0 << 48) |
                        ((ap_uint<64>)r3 << 32) |
                        ((ap_uint<64>)r2 << 16) |
                        ((ap_uint<64>)r1);
        ap_uint<64> Z_rot;
        if (k == 0) Z_rot = Z;
        else        Z_rot = (Z << k) | (Z >> (64 - k));
        r1 = (chaos_word_t)(Z_rot & 0xFFFF);
        r2 = (chaos_word_t)((Z_rot >> 16) & 0xFFFF);
        r3 = (chaos_word_t)((Z_rot >> 32) & 0xFFFF);
        r0 = (chaos_word_t)((Z_rot >> 48) & 0xFFFF);
    }
    rot_cnt = (rot_cnt == 14) ? (ap_uint<4>)0 : (ap_uint<4>)(rot_cnt + 1);

    // Register stage 2 outputs (rotation results)
    x1_rot = r1;
    x2_rot = r2;
    x3_rot = r3;
    s0_rot = r0;

    // ================================================================
    // Stage 3: Post-processing (combinational, uses registered rot output)
    // ================================================================
    ap_uint<64> u_t = ((ap_uint<64>)s0_rot << 48) |
                      ((ap_uint<64>)x3_rot << 32) |
                      ((ap_uint<64>)x2_rot << 16) |
                      ((ap_uint<64>)x1_rot);
    ap_uint<64> y_t;
    if (!pp_init) {
        y_t = u_t;
        pp_init = true;
    } else {
        ap_uint<64> rot1 = (u_prev1 << 17) | (u_prev1 >> 47);
        ap_uint<64> rot2 = (u_prev2 << 43) | (u_prev2 >> 21);
        y_t = u_t ^ rot1 ^ rot2;
    }
    u_prev2 = u_prev1;
    u_prev1 = u_t;

    prng_out = y_t;
    prng_out_valid = 1;
}
