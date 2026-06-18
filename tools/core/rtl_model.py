#!/usr/bin/env python3
# ==========================================================================
# rtl_model.py — Cycle-Accurate RTL-Equivalent Model of PRNG-371
# ==========================================================================
#
# This module mirrors the EXACT cycle-by-cycle behavior of the HLS-generated
# prng_top.v RTL.  It does NOT duplicate the PRNG algorithm — it imports
# multiplier primitives from ``golden_model.py`` (the single source of truth).
#
# Key RTL semantics captured here:
#   - 2-state FSM (state1 = compute, state2 = commit)
#   - Pipeline registers (5-stage shift pipeline)
#   - init_done gating (FSM-driven, not reset-driven)
#   - pp_init mechanism (first output = raw u_t, subsequent = XOR chain)
#   - 16-bit wrapping on all state values
#   - interval=2 (one output per 2 cycles)
#
# Usage::
#
#     from tools.core.rtl_model import RTLModel
#     rtl = RTLModel(seeds=(1000, 2000, 3000, 4000))
#     rtl.configure_and_start()
#     rtl.run_iterations(100)
#     print(rtl.get_outputs()[:10])
# ==========================================================================

from __future__ import annotations

from typing import List, Optional, Tuple

# Import multiplier functions from the single source of truth
from .golden_model import (
    mul_50, mul_48, mul_46,
    mul_371, mul_373, mul_375, mul_111,
)

# ==========================================================================
# RTLModel — Cycle-Accurate Mirror of prng_top.v
# ==========================================================================

DEFAULT_SEEDS: Tuple[int, int, int, int] = (1000, 2000, 3000, 4000)


class RTLModel:
    """Exact cycle-level mirror of HLS-generated ``prng_top.v`` RTL.

    This class models the FSM, pipeline registers, intermediate compute
    registers, and init_done semantics that make the RTL output differ
    slightly from the pure algorithmic model on the first 1-2 outputs.

    For algorithmic verification (the "what should the PRNG produce"),
    use ``golden_model.GoldenModel`` instead.
    """

    def __init__(self, seeds: Tuple[int, int, int, int] = DEFAULT_SEEDS):
        self.init_x1, self.init_x2, self.init_x3, self.init_x4 = seeds

        # ---- FSM ----
        self.ap_CS_fsm = 1         # 1=state1, 2=state2
        self.ap_start = 0
        self.ctrl = 0
        self.ctrl_read_reg = 0

        # ---- State registers (updated in state2) ----
        self.x1_reg = 0
        self.x2_reg = 0
        self.x3_reg = 0
        self.s0_reg = 0
        self.s1_reg = 0
        self.s2_reg = 0
        self.rot_cnt = 0
        self.temp_reg = 0
        self.u_prev1 = 0
        self.u_prev2 = 0
        self.pp_init = 0

        # ---- Pipeline registers (loaded in state1) ----
        self.x_reg_240 = 0
        self.x_2_reg_200 = 0
        self.x_3_reg_258 = 0
        self.x_4_reg_210 = 0
        self.x_5_reg_249 = 0

        # ---- Intermediate pipeline registers ----
        self.sub_ln19_reg = 0
        self.sub_ln43_reg = 0
        self.mul_ln31_reg = 0
        self.shl_ln19_1_reg = 0
        self.sub_ln127_reg = 0
        self.k_reg = 0

        # ---- loc_0 registers (initialization shadow) ----
        self.temp_reg_loc_0 = 0
        self.u_prev1_loc_0 = 0
        self.u_prev2_loc_0 = 0
        self.pp_init_flag_0 = 0
        self.pp_init_loc_0 = 0

        # ---- Output ----
        self.storemerge3_reg = 0
        self.storemerge2_reg = 0
        self.init_done = 0

        # ---- Output buffer ----
        self.outputs: List[int] = []

    # ------------------------------------------------------------------
    # Combinational helpers
    # ------------------------------------------------------------------

    @property
    def ap_CS_fsm_state1(self) -> bool:
        return self.ap_CS_fsm == 1

    @property
    def ap_CS_fsm_state2(self) -> bool:
        return self.ap_CS_fsm == 2

    @property
    def ap_condition_186(self) -> bool:
        return self.ap_start == 1 and self.ctrl == 1 and self.ap_CS_fsm_state1

    # ------------------------------------------------------------------
    # Mux selectors
    # ------------------------------------------------------------------

    def _icmp_ln66(self) -> bool:
        return (self.init_x1 | self.init_x2 | self.init_x3 | self.init_x4) == 0

    def _select_ln66(self) -> int:
        return 1000 if self._icmp_ln66() else self.init_x1

    def _select_ln66_1(self) -> int:
        return 2000 if self._icmp_ln66() else self.init_x2

    def _select_ln66_2(self) -> int:
        return 3000 if self._icmp_ln66() else self.init_x3

    def _select_ln66_3(self) -> int:
        return 4000 if self._icmp_ln66() else self.init_x4

    def _ap_phi_mux_x_1_phi(self) -> int:
        if self.init_done == 0:
            return 0
        return self.s2_reg & 0xFFFF

    def _ap_phi_mux_x_2_phi(self) -> int:
        if self.init_done == 0:
            return self._select_ln66_1()
        return self.x2_reg & 0xFFFF

    def _ap_phi_mux_x_4_phi(self) -> int:
        if self.init_done == 0:
            return self._select_ln66_2()
        return self.x3_reg & 0xFFFF

    def _ap_phi_mux_rot_cnt(self) -> int:
        if self.init_done == 0:
            return 0
        return self.rot_cnt & 0xF

    # ------------------------------------------------------------------
    # State1 computation
    # ------------------------------------------------------------------

    def _compute_state1_intermediates(self):
        x2_phi = self._ap_phi_mux_x_2_phi()
        x4_phi = self._ap_phi_mux_x_4_phi()
        rot = self._ap_phi_mux_rot_cnt()

        shl_ln19 = (x2_phi << 9) & 0xFFFF
        add_ln19 = ((x2_phi << 7) + x2_phi) & 0xFFFF
        sub_ln19 = (shl_ln19 - add_ln19) & 0xFFFF

        shl_ln25 = (x4_phi << 9) & 0xFFFF
        shl_ln25_1 = (x4_phi << 7) & 0xFFFF
        add_ln43 = (shl_ln25_1 + x4_phi) & 0xFFFF
        sub_ln43 = (shl_ln25 - add_ln43) & 0xFFFF

        shl_ln19_1 = (x2_phi << 7) & 0xFFFF

        x1_phi = self._ap_phi_mux_x_1_phi()
        mul_ln31 = mul_50(x1_phi)

        k = (rot + 1) & 0xF
        sub_ln127 = (64 - k) & 0x7F

        self.sub_ln19_reg = sub_ln19
        self.sub_ln43_reg = sub_ln43
        self.mul_ln31_reg = mul_ln31
        self.shl_ln19_1_reg = shl_ln19_1
        self.sub_ln127_reg = sub_ln127
        self.k_reg = k
        self.ctrl_read_reg = self.ctrl

    # ------------------------------------------------------------------
    # State2 computation (produces output word)
    # ------------------------------------------------------------------

    def _compute_state2_outputs(self):
        # x1n = 371 * x_reg_240 + mul_ln31_reg
        x1n = (mul_371(self.x_reg_240) + self.mul_ln31_reg) & 0xFFFF

        # x2n = 373 * x_2_reg_200 + 48 * x_3_reg_258
        sub_ln19_1 = (self.sub_ln19_reg - ((self.x_2_reg_200 << 3) & 0xFFFF)) & 0xFFFF
        sub_ln19_2 = (sub_ln19_1 - ((self.x_2_reg_200 << 1) & 0xFFFF)) & 0xFFFF
        shl_ln37 = (self.x_3_reg_258 << 6) & 0xFFFF
        shl_ln37_1 = (self.x_3_reg_258 << 4) & 0xFFFF
        sub_ln37 = (shl_ln37 - shl_ln37_1) & 0xFFFF
        x2n = (sub_ln19_2 + sub_ln37) & 0xFFFF

        # x3n = 375 * x_4_reg_210 + 46 * x_5_reg_249
        shl_ln25_2 = (self.x_4_reg_210 << 3) & 0xFFFF
        sub_ln25 = (self.sub_ln43_reg - shl_ln25_2) & 0xFFFF
        shl_ln43_1 = (self.x_5_reg_249 << 4) & 0xFFFF
        sub_ln25_1 = (sub_ln25 - shl_ln43_1) & 0xFFFF
        shl_ln43 = (self.x_5_reg_249 << 6) & 0xFFFF
        add_ln25 = (sub_ln25_1 + shl_ln43) & 0xFFFF
        shl_ln43_2 = (self.x_5_reg_249 << 1) & 0xFFFF
        x3n = (add_ln25 - shl_ln43_2) & 0xFFFF

        # x4n
        shl_ln49 = (self.x_2_reg_200 << 4) & 0xFFFF
        add_ln49 = (shl_ln49 + self.x_2_reg_200) & 0xFFFF
        sub_ln49 = (self.shl_ln19_1_reg - add_ln49) & 0xFFFF
        trunc_ln = (self.temp_reg_loc_0 >> 14) & 0xFFFF
        x4n = (sub_ln49 + trunc_ln) & 0xFFFF

        # z, inv_z, temp_reg
        z = (self.x_5_reg_249 + self.x_4_reg_210) & 0xFFFF
        inv_z = (z ^ 0xFFFF) & 0xFFFF
        temp = (z * inv_z) & 0x3FFFFFFF

        # Rotation
        Z = ((x4n & 0xFFFF) << 48) | ((x3n & 0xFFFF) << 32) | \
            ((x2n & 0xFFFF) << 16) | (x1n & 0xFFFF)
        k = self.k_reg & 0xF
        if k == 0:
            Z_rot = Z
        else:
            Z_rot = ((Z << k) | (Z >> (64 - k))) & 0xFFFFFFFFFFFFFFFF
        u_t = Z_rot

        # Post-processor (XOR chain)
        if self.pp_init_loc_0 == 0:
            y_t = u_t
        else:
            rot1 = ((self.u_prev1_loc_0 << 17) | (self.u_prev1_loc_0 >> 47)) & 0xFFFFFFFFFFFFFFFF
            rot2 = ((self.u_prev2_loc_0 << 43) | (self.u_prev2_loc_0 >> 21)) & 0xFFFFFFFFFFFFFFFF
            y_t = u_t ^ rot1 ^ rot2

        u_prev1_new = u_t
        u_prev2_new = self.u_prev1_loc_0 & 0xFFFFFFFFFFFFFFFF

        pp_init_flag_1 = self.pp_init_flag_0 | (1 if self.pp_init_loc_0 == 0 else 0)
        not_pp_init_loc = 1 - self.pp_init_loc_0
        pp_init_new = not_pp_init_loc if pp_init_flag_1 else self.pp_init

        rot_cnt_new = 0 if self.rot_cnt == 14 else ((self.rot_cnt + 1) & 0xF)

        # State register update
        self.x1_reg = x1n
        self.x2_reg = x2n
        self.x3_reg = x3n
        self.s0_reg = x4n
        self.s1_reg = self.x_5_reg_249 & 0xFFFF
        self.s2_reg = self.x_3_reg_258 & 0xFFFF
        self.temp_reg = temp
        self.u_prev1 = u_prev1_new
        self.u_prev2 = u_prev2_new
        self.pp_init = pp_init_new & 0x1
        self.rot_cnt = rot_cnt_new

        self.storemerge3_reg = y_t
        self.storemerge2_reg = 1

    # ------------------------------------------------------------------
    # Cycle execution
    # ------------------------------------------------------------------

    def step(self):
        """Execute one cycle of the RTL FSM."""
        if self.ap_CS_fsm_state1:
            self._compute_state1_intermediates()

            if self.ap_condition_186:
                if self.init_done == 0:
                    self.x_reg_240 = self._select_ln66()
                    self.x_2_reg_200 = self._select_ln66_1()
                    self.x_3_reg_258 = 0
                    self.x_4_reg_210 = self._select_ln66_2()
                    self.x_5_reg_249 = self._select_ln66_3()
                    self.temp_reg_loc_0 = 0
                    self.u_prev1_loc_0 = 0
                    self.u_prev2_loc_0 = 0
                    self.pp_init_flag_0 = 1
                    self.pp_init_loc_0 = 0
                    self.init_done = 1
                else:
                    self.x_reg_240 = self.x1_reg & 0xFFFF
                    self.x_2_reg_200 = self.x2_reg & 0xFFFF
                    self.x_3_reg_258 = self.s1_reg & 0xFFFF
                    self.x_4_reg_210 = self.x3_reg & 0xFFFF
                    self.x_5_reg_249 = self.s0_reg & 0xFFFF
                    self.temp_reg_loc_0 = self.temp_reg & 0x3FFFFFFF
                    self.u_prev1_loc_0 = self.u_prev1 & 0xFFFFFFFFFFFFFFFF
                    self.u_prev2_loc_0 = self.u_prev2 & 0xFFFFFFFFFFFFFFFF
                    self.pp_init_flag_0 = 0
                    self.pp_init_loc_0 = self.pp_init & 0x1

            if self.ap_start == 1:
                self.ap_CS_fsm = 2

        elif self.ap_CS_fsm_state2:
            if self.ctrl_read_reg == 1:
                self._compute_state2_outputs()
                self.outputs.append(self.storemerge3_reg & 0xFFFFFFFFFFFFFFFF)
            else:
                self.storemerge3_reg = 0
                self.storemerge2_reg = 0

            self.ap_CS_fsm = 1

    def configure_and_start(self):
        """Simulate AXI-Lite configuration: set ctrl=1, ap_start=1."""
        self.ctrl = 1
        self.ap_start = 1

    def run_cycles(self, n_cycles: int):
        """Run *n_cycles* FSM cycles (each cycle = one state)."""
        for _ in range(n_cycles):
            self.step()

    def run_iterations(self, n_iterations: int):
        """Run *n_iterations* PRNG iterations (state1 + state2 = 2 cycles each)."""
        for _ in range(n_iterations):
            self.step()
            self.step()

    def get_outputs(self) -> List[int]:
        """Return all collected output words."""
        return self.outputs
