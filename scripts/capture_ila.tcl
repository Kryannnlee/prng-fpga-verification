# ==========================================================================
# capture_ila.tcl — ILA Trigger Capture for PRNG-371 Validation
# ==========================================================================
#
# Trigger mode: cfg_done == 1 (probe4)
# Fallback mode: free-run if trigger not available
#
# Usage (Vivado Tcl Console):
#   source D:/fpgaoscillate/prng_project/hw/fpga/capture_ila.tcl
#
# Prerequisites:
#   - hw_server running on TCP:localhost:4122 (or adjust capture_hw_server_url)
#   - Bitstream generated with ila_0 probe1 + probe4 = DATA AND TRIGGER
# ==========================================================================

# ---- Configuration ----
if { ![info exists capture_hw_server_url] }  { set capture_hw_server_url "TCP:localhost:4122" }
if { ![info exists capture_bitstream] }      { set capture_bitstream "" }
if { ![info exists capture_output_dir] }     { set capture_output_dir "C:/Users/Kryan/AppData/Roaming/Xilinx/Vivado" }
if { ![info exists capture_device_index] }   { set capture_device_index 1 }

# Auto-detect paths from current Vivado project
if { $capture_bitstream == "" } {
    if { [catch { set run_dir [get_property DIRECTORY [current_run]] } err] } {
        puts "ERROR: No active Vivado project found. Cannot auto-detect bitstream path."
        puts "       Please set bitstream path before sourcing:"
        puts "         set capture_bitstream \"D:/.../acz702_top.bit\""
        return -code error "bitstream path not set and no active project found"
    }
    set capture_bitstream "${run_dir}/acz702_top.bit"
}

# Timestamp for unique filename
set timestamp [clock format [clock seconds] -format "%Y%m%d_%H%M%S"]
set csv_file "${capture_output_dir}/ila_capture_${timestamp}.csv"

proc log {msg} { puts "\[capture_ila\] $msg" }
proc log_error {msg} { puts "\[capture_ila\] ERROR: $msg" }

# ==========================================================================
# 1. Connect
# ==========================================================================
log "Step 1/5: Initializing hw_manager and connecting to hw_server..."
catch { disconnect_hw_server }
after 500

# open_hw_manager is required before connect_hw_server in Vivado 2024.1
catch { open_hw_manager }

if { [catch { connect_hw_server -url $capture_hw_server_url } err] } {
    log_error "Cannot connect: $err"
    return -code error "hw_server connection failed"
}

if { [catch { open_hw_target } err] } {
    log_error "Cannot open target: $err"
    disconnect_hw_server
    return -code error "hw_target open failed"
}

set devices [get_hw_devices]
set dev [lindex $devices $capture_device_index]
log "  Device: $dev"

# ==========================================================================
# 2. Program
# ==========================================================================
log "Step 2/5: Programming FPGA..."
if { ![file exists $capture_bitstream] } {
    log_error "Bitstream not found: $capture_bitstream"
    disconnect_hw_server
    return -code error "bitstream not found"
}

set_property PROGRAM.FILE $capture_bitstream $dev
program_hw_devices $dev

# ==========================================================================
# 3. Refresh to detect ILA, with Vivado 2024.1 disconnect workaround
# ==========================================================================
log "Step 3/5: Refreshing device to detect ILA..."
if { [catch { refresh_hw_device $dev } err] } {
    log "  Note: refresh_hw_device caused disconnect (Vivado 2024.1 bug). Reconnecting..."
    catch { open_hw_target }
    set devices [get_hw_devices]
    set dev [lindex $devices $capture_device_index]
    log "  Reconnected to: $dev"
}

# ILA may take a moment to appear after refresh.
set ila ""
for {set i 0} {$i < 10} {incr i} {
    set ila [lindex [get_hw_ilas] 0]
    if { $ila != "" } {
        break
    }
    after 200
}
if { $ila == "" } {
    log_error "No ILA core found. Check bitstream includes ila_0."
    disconnect_hw_server
    return -code error "ila not found"
}
log "  ILA: $ila"

# ==========================================================================
# 4. Configure trigger
# ==========================================================================
log "Step 4/5: Configuring ILA trigger..."

set cfg_done_probe [get_hw_probes -quiet -of_objects $ila -filter {NAME =~ "*cfg_done*"}]
set heartbeat_probe [get_hw_probes -quiet -of_objects $ila -filter {NAME =~ "*heartbeat*"}]

if { $heartbeat_probe == "" } {
    log_error "dbg_heartbeat probe not found in ILA."
    disconnect_hw_server
    return -code error "heartbeat probe missing"
}

# After a fresh program_hw_devices, FSM is in state 0 (startup gate).
# dbg_heartbeat = |cfg_state = 0 during state 0, then 1 when FSM leaves state 0.
# Trigger on heartbeat 0→1 edge = capture ENTIRE AXI-Lite write sequence.
# Reset ILA before configuring trigger (clean state)
catch { reset_hw_ila $ila }

# Attempt to set trigger: dbg_heartbeat == 1
set trigger_ok 0
set trigger_err ""

catch {
    set_property TRIGGER_COMPARE_VALUE eq1'b1 $heartbeat_probe
    set trigger_ok 1
} trigger_err

set free_run_mode 0
if { $trigger_ok == 0 } {
    log ""
    log "WARNING: Could not configure heartbeat trigger. Falling back to FREE-RUN mode."
    log "  Detail: $trigger_err"
    log ""
    set free_run_mode 1
}

# ==========================================================================
# 4b. Pre-arm check: verify startup gate is active
# ==========================================================================
# With the startup gate patch (state 0 = 15s delay), dbg_heartbeat should be 0
# immediately after program.  If it's already 1, the startup gate has expired
# and we'll miss the AXI write sequence.
if { !$free_run_mode } {
    log ""
    log "  Pre-arm gate check..."
    set hb_pre 99
    catch { set hb_pre [get_property INPUT_VALUE $heartbeat_probe] }

    if { $hb_pre == 0 } {
        log "    dbg_heartbeat = 0  ✓  Startup gate ACTIVE (FSM in state 0)"
        log "    ILA will trigger on heartbeat 0→1, capturing AXI write sequence."
    } elseif { $hb_pre == 1 } {
        log "    dbg_heartbeat = 1  ⚠  Startup gate EXPIRED"
        log "    ILA will trigger immediately — will MISS AXI write phase."
        log "    Check STARTUP_DELAY_CYCLES > arm time."
    } else {
        log "    dbg_heartbeat = $hb_pre (could not read — assuming gate is present)"
    }
    log ""
}

# ==========================================================================
# 5. Arm, wait, upload, export
# ==========================================================================
log "Step 5/5: Arming ILA..."
log "======================================"
if { $free_run_mode } {
    log "  Mode: FREE-RUN (no trigger) — waiting for startup gate to expire..."
    log ""
    log "  Startup gate: 750M cycles @ 50 MHz = 15 seconds."
    log "  Polling dbg_heartbeat until FSM leaves state 0..."
    log ""

    # Poll dbg_heartbeat until it becomes 1, or timeout.
    # After program_hw_devices, FSM is in state 0 (dbg_heartbeat=0).
    # When the 15s startup gate expires, FSM leaves state 0, heartbeat→1.
    set gate_passed 0
    set poll_interval_ms 250
    set poll_timeout_ms 25000
    set poll_elapsed 0

    while { $poll_elapsed < $poll_timeout_ms } {
        set hb_val -1
        catch { set hb_val [get_property INPUT_VALUE $heartbeat_probe] }

        if { $hb_val == 1 } {
            log "  dbg_heartbeat = 1  ✓  Startup gate EXPIRED (${poll_elapsed}ms after arm start)"
            set gate_passed 1
            # Small delay to let AXI writes begin (~2µs in HW, but JTAG is slow)
            after 10
            break
        }

        if { $poll_elapsed % 5000 == 0 && $poll_elapsed > 0 } {
            log "    ... still waiting (${poll_elapsed}ms elapsed, heartbeat=$hb_val)"
        }

        after $poll_interval_ms
        set poll_elapsed [expr {$poll_elapsed + $poll_interval_ms}]
    }

    if { !$gate_passed } {
        log ""
        log "  WARNING: dbg_heartbeat did not rise within ${poll_timeout_ms}ms."
        log "  Either INPUT_VALUE read failed or FSM is truly stuck."
        log "  Falling back to 16s fixed delay, then arming anyway..."
        after 16000
    }
} else {
    log "  Mode: TRIGGER on dbg_heartbeat == 1 (AXI write phase capture)"
}
log "======================================"

run_hw_ila $ila

if { $free_run_mode } {
    log "  Free-run capture in progress (~1 sec)..."
    after 1500
} else {
    log "  Waiting for trigger (timeout: 30s)..."
    if { [catch { wait_on_hw_ila $ila -timeout 30 } err] } {
        log_error "ILA did not trigger within 30s."
        log_error "  Possible causes: FSM stuck, no clock, or heartbeat never rises."
        disconnect_hw_server
        return -code error "ILA trigger timeout"
    }
}

log "  Uploading data..."
set data [upload_hw_ila_data $ila]
write_hw_ila_data -force -csv_file $csv_file $data

log ""
log "======================================"
log "  CAPTURE COMPLETE (MCDP Step 2: AXI Write Phase diag)"
log "======================================"
log "  CSV: $csv_file"
log ""
log "  Inspect AXI writes first (MCDP Step 2):"
log "    cd D:/fpgaoscillate/prng_project/prng_experiment/tools"
log "    python inspect_axi_writes.py --capture \"$csv_file\""
log ""
log "  probe2 = ila_probe2_diag mux: state<24={wdata,awaddr}, state>=24=prng_out"
log ""

disconnect_hw_server
log "Done."
return $csv_file
