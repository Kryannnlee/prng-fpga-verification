#include "prng_top.h"
#include "chaos_core.h"
#include "shift_mul.h"
#include <iostream>
#include <iomanip>
#include <cstdlib>
#include <cstring>

// ==========================================================================
// Golden Model — 3-stage pipelined (bit-exact with prng_top_fast.cpp)
// ==========================================================================

struct GoldenState {
    // Stage 2: current state
    ap_uint<16> x1, x2, x3, s0, s1, s2;
    // Stage 1 registers
    ap_uint<16> x1nr, x2nr, x3nr, x4nr;
    // DSP pipeline
    ap_uint<32> temp_reg;
    // Stage 2 registers (rotation output)
    ap_uint<16> x1r, x2r, x3r, s0r;
    ap_uint<4>  rot_cnt;
    // Stage 3: post-processor
    ap_uint<64> u_prev1, u_prev2;
    bool pp_init;
};

static void golden_init(GoldenState &st,
                         ap_uint<16> ix1, ap_uint<16> ix2,
                         ap_uint<16> ix3, ap_uint<16> ix4) {
    bool z = (ix1 == 0) && (ix2 == 0) && (ix3 == 0) && (ix4 == 0);
    st.x1 = z ? ap_uint<16>(1000) : ix1;
    st.x2 = z ? ap_uint<16>(2000) : ix2;
    st.x3 = z ? ap_uint<16>(3000) : ix3;
    st.s0 = z ? ap_uint<16>(4000) : ix4;
    st.s1 = 0;  st.s2 = 0;
    st.x1nr = 0; st.x2nr = 0; st.x3nr = 0; st.x4nr = 0;
    st.temp_reg = 0;
    st.x1r = st.x1; st.x2r = st.x2; st.x3r = st.x3; st.s0r = st.s0;
    st.rot_cnt = 0;
    st.u_prev1 = 0; st.u_prev2 = 0;
    st.pp_init = false;
}

static ap_uint<64> golden_next(GoldenState &st) {
    // Stage 1: shift-add
    ap_uint<16> x1n = mul_A_J1_0(st.x1) + mul_A_JN_0(st.s2);
    ap_uint<16> x2n = mul_A_J1_1(st.x2) + mul_A_JN_1(st.s1);
    ap_uint<16> x3n = mul_A_J1_2(st.x3) + mul_A_JN_2(st.s0);
    ap_uint<16> z   = (ap_uint<16>)(st.x3 + st.s0);
    ap_uint<16> x4n = mul_B_VAL(st.x2) + (ap_uint<16>)(st.temp_reg >> 14);
    ap_uint<16> inv = (~z) & 0xFFFF;
    st.temp_reg = (ap_uint<32>)z * (ap_uint<32>)inv;
    st.x1nr = x1n; st.x2nr = x2n; st.x3nr = x3n; st.x4nr = x4n;

    // Stage 2: state update + bit-rotation
    st.s2 = st.s1; st.s1 = st.s0; st.s0 = st.x4nr;
    st.x1 = st.x1nr; st.x2 = st.x2nr; st.x3 = st.x3nr;

    ap_uint<16> r1 = st.x1, r2 = st.x2, r3 = st.x3, r0 = st.s0;
    ap_uint<4> k = 1 + st.rot_cnt;
    ap_uint<64> Z = ((ap_uint<64>)r0 << 48) | ((ap_uint<64>)r3 << 32) |
                    ((ap_uint<64>)r2 << 16) | ((ap_uint<64>)r1);
    ap_uint<64> Z_rot = (k == 0) ? Z : ((Z << k) | (Z >> (64 - k)));
    r1 = (ap_uint<16>)(Z_rot & 0xFFFF);
    r2 = (ap_uint<16>)((Z_rot >> 16) & 0xFFFF);
    r3 = (ap_uint<16>)((Z_rot >> 32) & 0xFFFF);
    r0 = (ap_uint<16>)((Z_rot >> 48) & 0xFFFF);
    st.rot_cnt = (st.rot_cnt == 14) ? (ap_uint<4>)0 : (ap_uint<4>)(st.rot_cnt + 1);
    st.x1r = r1; st.x2r = r2; st.x3r = r3; st.s0r = r0;

    // Stage 3: post-processing
    ap_uint<64> u_t = ((ap_uint<64>)st.s0r << 48) | ((ap_uint<64>)st.x3r << 32) |
                      ((ap_uint<64>)st.x2r << 16) | ((ap_uint<64>)st.x1r);
    ap_uint<64> y_t;
    if (!st.pp_init) { y_t = u_t; st.pp_init = true; }
    else {
        ap_uint<64> rt1 = (st.u_prev1 << 17) | (st.u_prev1 >> 47);
        ap_uint<64> rt2 = (st.u_prev2 << 43) | (st.u_prev2 >> 21);
        y_t = u_t ^ rt1 ^ rt2;
    }
    st.u_prev2 = st.u_prev1;
    st.u_prev1 = u_t;
    return y_t;
}

// ==========================================================================
// Main
// ==========================================================================

int main() {
    std::cout << "========================================" << std::endl;
    std::cout << "  Chaos PRNG Fast — 3-Stage Pipelined" << std::endl;
    std::cout << "========================================" << std::endl;

    int errors = 0;
    ap_uint<16> z = 0;
    const int WARMUP = 1000;

    // Test 1: Warmup + first post-warmup output
    std::cout << "\n[Test 1] Warmup (" << WARMUP << " iterations) + first output..."
              << std::endl;
    {
        ap_uint<64> out; ap_uint<1> valid;

        // Discard first WARMUP outputs
        for (int i = 0; i < WARMUP; i++) {
            prng_top(1, z, z, z, z, out, valid);
        }
        if (!valid) { std::cerr << "  FAIL after warmup" << std::endl; errors++; }
        else std::cout << "  PASS, first post-warmup word = 0x"
                       << std::hex << std::setw(16) << std::setfill('0')
                       << out << std::dec << std::endl;
    }

    // Test 2: Golden model comparison after warmup (1000 outputs)
    std::cout << "\n[Test 2] Golden model comparison (1000 outputs, after warmup)..."
              << std::endl;
    {
        GoldenState g;
        golden_init(g, z, z, z, z);
        // Warmup golden model
        for (int i = 0; i < WARMUP; i++) golden_next(g);

        int local = 0;
        for (int i = 0; i < 1000; i++) {
            ap_uint<64> hw; ap_uint<1> v;
            prng_top(1, z, z, z, z, hw, v);
            ap_uint<64> sw = golden_next(g);
            if (!v || hw != sw) {
                if (local < 5) std::cerr << "  FAIL step " << i << std::endl;
                local++;
            }
        }
        if (local == 0) std::cout << "  PASS — 1000/1000 match after warmup" << std::endl;
        else { std::cout << "  FAIL — " << local << " mismatches" << std::endl; errors += local; }
    }

    // Test 3: STOP/START
    std::cout << "\n[Test 3] STOP/START..." << std::endl;
    {
        ap_uint<64> out; ap_uint<1> v;
        prng_top(1, z, z, z, z, out, v); if (!v) { errors++; std::cerr << "  FAIL start" << std::endl; }
        else std::cout << "  START → valid=1" << std::endl;
        prng_top(0, z, z, z, z, out, v); if (v || out != 0) { errors++; std::cerr << "  FAIL stop" << std::endl; }
        else std::cout << "  STOP → valid=0, out=0" << std::endl;
        prng_top(1, z, z, z, z, out, v); if (!v) { errors++; std::cerr << "  FAIL resume" << std::endl; }
        else std::cout << "  RESUME → valid=1" << std::endl;
    }

    std::cout << "\n========================================" << std::endl;
    if (errors == 0) std::cout << "  ALL TESTS PASSED" << std::endl;
    else std::cout << "  FAILED: " << errors << " errors" << std::endl;
    std::cout << "========================================" << std::endl;
    return (errors == 0) ? 0 : 1;
}
