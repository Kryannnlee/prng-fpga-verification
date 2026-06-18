# PRNG-371 FPGA Engineering Status

**Date**: 2026-06-18 | **Status**: ✅ VERIFIED — 2 independent captures, both PASS, BER=0

---

## 1. Validation Results

| # | Timestamp | Words | Mismatches | BER | Verdict |
|---|-----------|-------|------------|-----|---------|
| 1 | 2026-06-18 12:17 UTC | 8,180 | 0 | 0 | ✅ PASS |
| 2 | 2026-06-18 12:48 UTC | 8,180 | 0 | 0 | ✅ PASS |

Both captures used identical seeds [1000, 2000, 3000, 4000], warmup=0, validated against the Python golden model (`rtl_cycle_model.GoldenModelState`). Results are bit-exact and reproducible.

---

## 2. Issues Encountered and Resolved

### Issue 1: WSTRB 1-bit Truncation (P0)

- **Symptom**: Seeds truncated from 16-bit to 8-bit — all PRNG output mismatched
- **Root cause**: `reg axi_wstrb = 4'hF;` — Verilog `reg` without explicit width defaults to 1-bit
- **Fix**: Changed to `wire [3:0] axi_wstrb; assign axi_wstrb = 4'hF;` ([prng_wrapper.v:41](prng_wrapper.v))
- **Status**: ✅ FIXED

### Issue 2: ILA Probe Type Silent Downgrade (P0)

- **Symptom**: `capture_ila.tcl` fell back to FREE-RUN mode — `TRIGGER_COMPARE_VALUE` property didn't exist on `dbg_heartbeat` probe
- **Root cause**: Vivado ILA v6.2 with `C_ADV_TRIGGER=FALSE` silently downgraded 1-bit probe1 from DATA AND TRIGGER to DATA only during IP generation. User's `C_PROBE1_TYPE=1` in `.xci` was overridden to `0` in the resolved parameters.
- **Why it appeared after Vivado restart**: Closing/reopening Vivado invalidated the `.gen/` IP cache. `reset_run` triggered IP re-generation, which re-parsed `.xci` and applied the downgrade. The previous working build had correct cache from GUI-based IP generation that wasn't invalidated.
- **Fix**: In Vivado GUI, re-customized `ila_0` IP → set PROBE1 to DATA AND TRIGGER, changed Trigger Ports from 1 to 2 → Generate Output Products. Re-synthesized.
- **Prevention**: Do NOT run `reset_run ila_0_synth_1` after GUI Generate. The cached IP output is correct; only `reset_run synth_1` is needed for source changes.
- **Status**: ✅ FIXED

### Issue 3: Free-Run Capture During Startup Gate (P1)

- **Symptom**: When ILA couldn't trigger (Issue 2), free-run captured 1.5s of data during the 15s startup gate — all `dbg_state=00`, no PRNG output
- **Fix A (workaround)**: `capture_ila.tcl` now polls `dbg_heartbeat` INPUT_VALUE every 250ms for up to 25s before arming in free-run mode
- **Fix B (root cause)**: Issue 2 above — proper ILA trigger configuration
- **Status**: ✅ FIXED (both paths)

### Issue 4: Pipeline Fill Zero Word (P2)

- **Symptom**: First extracted PRNG word after `cfg_done` transition was `0x0000000000000000` — the PRNG pipeline fill cycle at the first sample of state 25
- **Fix**: `validate_fpga_prng.py` automatically skips leading zero-valued PRNG words after the state transition (pipeline fill detection). `min_state` filter set to 25 (state 24 = final AXI write of ctrl=1, not PRNG output).
- **Status**: ✅ FIXED

### Issue 5: C Golden Library Load Failure (P2)

- **Symptom**: `--generate-golden` failed with "not a valid Win32 application" — `golden_371.so` incompatible with Windows Python
- **Fix**: `generate_golden_live()` now falls back to pure-Python `GoldenModelState` (from `rtl_cycle_model.py`) when C library import fails. Output is identical.
- **Status**: ✅ FIXED

---

## 3. Files Modified (2026-06-18)

| File | Change | Lines |
|------|--------|-------|
| `ila_0` IP | PROBE1 → DATA AND TRIGGER, Trigger Ports=2 | GUI |
| `capture_ila.tcl` | Free-run mode: poll heartbeat, wait for startup gate | ~30 |
| `validate_fpga_prng.py` | `--align-mode`, pipeline auto-skip, Python golden fallback, min_state=25 | ~120 |
| `FULL_FLOW_GUIDE.md` | Complete rewrite: ILA trap, step 1.1, diagnostics, FAQ | Full file |
| `ENGINEERING_STATUS.md` | This file — status update to VERIFIED | Full file |

**Not modified** (already correct):
- `prng_wrapper.v` — WSTRB fix applied 2026-06-17
- `acz702_top.v` — ILA probe connections unchanged
- `ila_0.xci` — User parameters (all TYPE=1) were always correct; the issue was in Vivado's resolver

---

## 4. Validation Pipeline (Current)

```tcl
# Vivado Tcl Console
source D:/fpgaoscillate/prng_project/hw/fpga/capture_ila.tcl
  → Returns CSV path
```

```bash
# Terminal
cd D:/fpgaoscillate/prng_project/prng_experiment/tools
python validate_fpga_prng.py \
  --capture <CSV_path> \
  --generate-golden --num-words 8000 --warmup 0 \
  --align-mode cfg_done \
  --report validation_report.md --json-report validation_results.json
  → Expected: "mismatched words = 0, BER = 0, PASS"
```

---

## 5. Project Reopen Checklist

After closing and reopening Vivado:

- [ ] Check `ila_0` IP: PROBE1 must be DATA AND TRIGGER (not DATA)
- [ ] If locked/reconfigured: GUI double-click → fix → Generate Output Products
- [ ] `synth_1` and `impl_1` complete
- [ ] `capture_ila.tcl` runs in **TRIGGER** mode (not FREE-RUN)
- [ ] Validate: 8,180/8,180 PASS, BER=0

Full details: [FULL_FLOW_GUIDE.md](FULL_FLOW_GUIDE.md)

---

## 6. Known Vivado Behaviors

| Behavior | Explanation | Mitigation |
|----------|-------------|------------|
| `IP 'ila_0' is locked` on reopen | `.gen/` cache metadata stale (cache-ID mismatch). Not a license issue. | Ignore; check probe type in GUI |
| `C_PROBE1_TYPE` resolved to 0 despite user setting 1 | Vivado ILA v6.2 downgrades 1-bit probes in basic trigger mode | GUI re-customize → Generate |
| `reset_run ila_0_synth_1` destroys correct IP cache | Triggers IP re-generation which re-applies the downgrade | Don't reset ILA run; only `reset_run synth_1` |
| `TRIGGER_COMPARE_VALUE` property missing | Probe is DATA only, not DATA AND TRIGGER | See Issue 2 above |

---

*Last updated: 2026-06-18 12:55 UTC — Status changed from IN PROGRESS to VERIFIED after second independent capture confirmed PASS.*
