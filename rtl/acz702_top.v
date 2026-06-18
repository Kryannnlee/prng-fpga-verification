// ==========================================================================
// acz702_top.v — ACZ702 PRNG-371 top-level with ILA debug (MCDP Step 2 diag)
// ==========================================================================
// Probes ILA signals:
//   probe0: dbg_state[4:0]   — AXI-Lite FSM state (0=wait, 25=running)
//   probe1: dbg_heartbeat     — TRIGGER: 1 when FSM leaves state 0 (AXI write start)
//   probe2: ila_probe2_diag   — mux: {wdata,awaddr} (state<24) else prng_out
//   probe3: dbg_axi_stat[3:0] — {awvalid, awready, wvalid, wready}
//   probe4: cfg_done           — passive marker (level high when state>=24)
//   probe5: dbg_time[31:0]     — free-running timestamp (absolute time axis)
// ==========================================================================

module acz702_top (
    input  wire        clk_50mhz,
    input  wire        btn_rst_n,
    output wire [7:0]  led,
    output wire        led_valid
);

    // ---- Clock buffer (required for ILA clock) ----
    wire clk;
    BUFG bufg_inst (.I(clk_50mhz), .O(clk));

    wire [7:0]  prng_led;
    wire        prng_led_valid;

    // ---- Debug probes ----
    wire [4:0]  dbg_state;
    wire [63:0] dbg_prng_out;
    wire [3:0]  dbg_axi_stat;
    wire        cfg_done;
    wire [31:0] dbg_time;
    wire        dbg_heartbeat;
    // AXI write phase diagnostics (MCDP Step 2)
    wire [5:0]  dbg_awaddr;
    wire [31:0] dbg_wdata;
    wire [63:0] ila_probe2_diag;

    // ---- PRNG wrapper ----
    prng_wrapper u_prng (
        .clk            (clk),
        .rst_n          (btn_rst_n),
        .led            (prng_led),
        .led_valid      (prng_led_valid),
        .dbg_state      (dbg_state),
        .dbg_prng_out   (dbg_prng_out),
        .dbg_axi_stat   (dbg_axi_stat),
        .cfg_done       (cfg_done),
        .dbg_time       (dbg_time),
        .dbg_heartbeat  (dbg_heartbeat),
        .dbg_awaddr     (dbg_awaddr),
        .dbg_wdata      (dbg_wdata),
        .ila_probe2_diag(ila_probe2_diag)
    );

    assign led       = prng_led;
    assign led_valid = prng_led_valid;

    // ---- ILA (Integrated Logic Analyzer) debug core ----
    // Must be generated in Vivado IP Catalog first:
    //   IP Catalog → Search "ILA" → Configure with 5 probe ports:
    //     probe0: 5 bits  (dbg_state)
    //     probe1: 1 bit   (dbg_heartbeat) — 1 when FSM has left state 0
    //     probe2: 64 bits (ila_probe2_diag — AXI write data mux)
    //     probe3: 4 bits  (dbg_axi_stat)
    //     probe4: 1 bit   (cfg_done) — passive marker
    //     probe5: 32 bits (dbg_time) — free-running timestamp
    //   Sample depth: 16384, pipe stages: 0, capture control: basic
    //   Trigger: NONE (ILA = passive waveform memory)
    ila_0 u_ila (
        .clk    (clk),
        .probe0 (dbg_state),
        .probe1 (dbg_heartbeat),
        .probe2 (ila_probe2_diag),
        .probe3 (dbg_axi_stat),
        .probe4 (cfg_done),
        .probe5 (dbg_time)
    );

endmodule
