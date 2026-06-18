// ==========================================================================
// prng_wrapper.v — Top-level wrapper for chaos_prng_371 P&R test
// ==========================================================================
// Connects prng_top to external pins with minimal AXI-Lite config FSM.
// Outputs prng_out[7:0] to LED pins for verification.
//
// AXI-Lite address map (from prng_top_CONTROL_s_axi.v):
//   0x00 = ap_start + auto_restart   0x18 = init_x1    0x28 = init_x3
//   0x10 = ctrl (must be 1)         0x20 = init_x2    0x30 = init_x4
//
// Each write = AW_assert (1 cycle) + AW_wait (1+ cycles) + W (1 cycle) + GAP (1 cycle)
// The GAP state prevents WVALID overlap with next AWVALID (non-blocking delay)
// ==========================================================================

module prng_wrapper (
    input  wire        clk,          // system clock
    input  wire        rst_n,        // active-low reset
    output wire [7:0]  led,          // prng_out[7:0] for visual check
    output wire        led_valid,    // 1 when output is valid
    // ---- ILA debug probes ----
    output wire [4:0]  dbg_state,    // AXI FSM state (0-25, 5 bits)
    output wire [63:0] dbg_prng_out, // full 64-bit PRNG output
    output wire [3:0]  dbg_axi_stat, // {awvalid, awready, wvalid, wready}
    output wire        cfg_done,     // LEVEL HIGH when FSM reaches state 24+ (ILA trigger friendly)
    output wire [31:0] dbg_time,     // free-running timestamp counter (absolute time axis)
    output wire        dbg_liveness, // ~6Hz toggle proving FSM clock is alive (time_cnt[23])
    output wire        dbg_heartbeat,// =1 when FSM has left state 0 (|cfg_state)
    // ---- AXI write phase diagnostics (MCDP Step 2: seed reg verification) ----
    output wire [5:0]  dbg_awaddr,   // current AXI write address (live from FSM)
    output wire [31:0] dbg_wdata,    // current AXI write data (live from FSM)
    output wire [63:0] ila_probe2_diag // mux: {wdata, awaddr} when state<24, else prng_out
);

    // ---- AXI-Lite signals ----
    reg         axi_awvalid = 1'b0;
    wire        axi_awready;
    reg  [5:0]  axi_awaddr;
    reg         axi_wvalid = 1'b0;
    wire        axi_wready;
    reg  [31:0] axi_wdata;
    wire [3:0]  axi_wstrb;
    assign      axi_wstrb = 4'hF;  // all 4 bytes enabled (3:0 = 4-bit, not 1-bit!)
    wire        axi_bvalid;
    reg         axi_bready = 1'b1;
    reg         axi_arvalid = 1'b0;
    wire        axi_arready;
    reg  [5:0]  axi_araddr;
    wire        axi_rvalid;
    reg         axi_rready = 1'b0;
    wire [31:0] axi_rdata;

    // ---- PRNG outputs ----
    wire [63:0] prng_out;
    wire        prng_out_valid;

    // ---- PRNG instantiation ----
    prng_top u_prng (
        .s_axi_CONTROL_AWVALID(axi_awvalid),
        .s_axi_CONTROL_AWREADY(axi_awready),
        .s_axi_CONTROL_AWADDR (axi_awaddr),
        .s_axi_CONTROL_WVALID (axi_wvalid),
        .s_axi_CONTROL_WREADY (axi_wready),
        .s_axi_CONTROL_WDATA  (axi_wdata),
        .s_axi_CONTROL_WSTRB  (axi_wstrb),
        .s_axi_CONTROL_BVALID (axi_bvalid),
        .s_axi_CONTROL_BREADY (axi_bready),
        .s_axi_CONTROL_ARVALID(axi_arvalid),
        .s_axi_CONTROL_ARREADY(axi_arready),
        .s_axi_CONTROL_ARADDR (axi_araddr),
        .s_axi_CONTROL_RVALID (axi_rvalid),
        .s_axi_CONTROL_RREADY (axi_rready),
        .s_axi_CONTROL_RDATA  (axi_rdata),
        .s_axi_CONTROL_RRESP  (),
        .s_axi_CONTROL_BRESP  (),
        .ap_clk               (clk),
        .ap_rst_n             (prng_rst_n),
        .interrupt            (),
        .prng_out             (prng_out),
        .prng_out_valid       (prng_out_valid)
    );

    // ---- AXI-Lite configuration FSM ----
    // 26 states, 5 bits. Each write: AW_assert → AW_wait → W → GAP
    // The AW_wait state fixes: AWVALID must be STABLE before checking AWREADY
    // The GAP state fixes: WVALID (delayed 1 cycle by <=) must clear before next AWVALID
    // --- Startup gate: keep FSM in state 0 for STARTUP_DELAY cycles ---
    // Allows ILA to fully arm (refresh, detect, configure trigger) before
    // the FSM leaves state 0.  Without this gate, the FSM finishes startup
    // in ~0.6 us (30 cycles), but ILA arming takes seconds — the trigger
    // fires on an already-high cfg_done and captures a random PRNG window.
    //
    // 50M cycles = 1.0 s @ 50 MHz.  Default 15.0 s gives ample margin
    // for slow Vivado 2024.1 refresh_hw_device / get_hw_ilas retry loops.
    parameter STARTUP_DELAY_CYCLES = 32'd750_000_000;   // 15.0 s @ 50 MHz

    reg [4:0] cfg_state = 5'd0;
    reg [31:0] wait_cnt  = 32'd0;
    reg       cfg_done_reg = 1'b0;    // 256-cycle pulse (stable, ILA-observable)
    reg [7:0] done_cnt    = 8'd0;    // counter for pulse width
    reg [31:0] time_cnt   = 32'd0;   // free-running timestamp (absolute time axis)

    // --- PRNG reset pulse: hold ap_rst_n low for 256 cycles after board reset ---
    // HLS-generated prng_top does NOT reset init_done via ap_rst_n (only INIT
    // attribute).  A deliberate 256-cycle reset pulse ensures all PRNG internal
    // static state (init_done, x1_reg, pp_init, etc.) is cleared before the
    // FSM writes seeds and starts the PRNG.
    reg [7:0] prng_rst_cnt = 8'd0;
    wire      prng_rst_n;
    assign    prng_rst_n = (prng_rst_cnt == 8'd255);  // high only after counter saturates

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cfg_state    <= 5'd0;
            wait_cnt     <= 32'd0;
            cfg_done_reg <= 1'b0;
            done_cnt     <= 8'd0;
            time_cnt     <= 32'd0;
            axi_awvalid  <= 1'b0;
            axi_wvalid   <= 1'b0;
            axi_awaddr   <= 6'd0;
            axi_wdata    <= 32'd0;
            axi_bready   <= 1'b1;
            prng_rst_cnt <= 8'd0;
        end else begin
            // PRNG reset counter: saturates at 255 (~5 us)
            if (prng_rst_cnt != 8'd255)
                prng_rst_cnt <= prng_rst_cnt + 1;

            time_cnt <= time_cnt + 1'b1;  // free-running timestamp
            case (cfg_state)
                // ============================================
                // State 0: Startup gate (15.0 s delay for ILA arm window)
                // ============================================
                5'd0: begin
                    axi_wvalid <= 1'b0;
                    axi_awvalid <= 1'b0;
                    // Startup gate: hold until ILA is ready
                    if (wait_cnt >= STARTUP_DELAY_CYCLES) begin
                        cfg_state <= 5'd1;
                        wait_cnt  <= 32'd0;
                    end else begin
                        wait_cnt <= wait_cnt + 1;
                    end
                end

                // ============================================
                // Write 1: ctrl = 1 @ 0x10
                // ============================================
                // AW_assert
                5'd1: begin
                    axi_wvalid  <= 1'b0;
                    axi_awaddr  <= 6'h10;
                    axi_awvalid <= 1'b1;
                    cfg_state   <= 5'd2;
                end
                // AW_wait
                5'd2: begin
                    if (axi_awready) begin
                        axi_awvalid <= 1'b0;
                        cfg_state   <= 5'd3;
                    end
                end
                // W_phase
                5'd3: begin
                    axi_wdata  <= 32'd1;
                    axi_wvalid <= 1'b1;
                    cfg_state  <= 5'd4;
                end
                // GAP (clear WVALID before next AW)
                5'd4: begin
                    axi_wvalid <= 1'b0;
                    cfg_state  <= 5'd5;
                end

                // ============================================
                // Write 2: init_x1 = 1000 @ 0x18
                // ============================================
                5'd5: begin
                    axi_awaddr  <= 6'h18;
                    axi_awvalid <= 1'b1;
                    cfg_state   <= 5'd6;
                end
                5'd6: begin
                    if (axi_awready) begin
                        axi_awvalid <= 1'b0;
                        cfg_state   <= 5'd7;
                    end
                end
                5'd7: begin
                    axi_wdata  <= 32'd1000;
                    axi_wvalid <= 1'b1;
                    cfg_state  <= 5'd8;
                end
                5'd8: begin
                    axi_wvalid <= 1'b0;
                    cfg_state  <= 5'd9;
                end

                // ============================================
                // Write 3: init_x2 = 2000 @ 0x20
                // ============================================
                5'd9: begin
                    axi_awaddr  <= 6'h20;
                    axi_awvalid <= 1'b1;
                    cfg_state   <= 5'd10;
                end
                5'd10: begin
                    if (axi_awready) begin
                        axi_awvalid <= 1'b0;
                        cfg_state   <= 5'd11;
                    end
                end
                5'd11: begin
                    axi_wdata  <= 32'd2000;
                    axi_wvalid <= 1'b1;
                    cfg_state  <= 5'd12;
                end
                5'd12: begin
                    axi_wvalid <= 1'b0;
                    cfg_state  <= 5'd13;
                end

                // ============================================
                // Write 4: init_x3 = 3000 @ 0x28
                // ============================================
                5'd13: begin
                    axi_awaddr  <= 6'h28;
                    axi_awvalid <= 1'b1;
                    cfg_state   <= 5'd14;
                end
                5'd14: begin
                    if (axi_awready) begin
                        axi_awvalid <= 1'b0;
                        cfg_state   <= 5'd15;
                    end
                end
                5'd15: begin
                    axi_wdata  <= 32'd3000;
                    axi_wvalid <= 1'b1;
                    cfg_state  <= 5'd16;
                end
                5'd16: begin
                    axi_wvalid <= 1'b0;
                    cfg_state  <= 5'd17;
                end

                // ============================================
                // Write 5: init_x4 = 4000 @ 0x30
                // ============================================
                5'd17: begin
                    axi_awaddr  <= 6'h30;
                    axi_awvalid <= 1'b1;
                    cfg_state   <= 5'd18;
                end
                5'd18: begin
                    if (axi_awready) begin
                        axi_awvalid <= 1'b0;
                        cfg_state   <= 5'd19;
                    end
                end
                5'd19: begin
                    axi_wdata  <= 32'd4000;
                    axi_wvalid <= 1'b1;
                    cfg_state  <= 5'd20;
                end
                5'd20: begin
                    axi_wvalid <= 1'b0;
                    cfg_state  <= 5'd21;
                end

                // ============================================
                // Write 6: ap_start=1 + auto_restart=1 (0x81) @ 0x00
                // ============================================
                5'd21: begin
                    axi_awaddr  <= 6'h00;
                    axi_awvalid <= 1'b1;
                    cfg_state   <= 5'd22;
                end
                5'd22: begin
                    if (axi_awready) begin
                        axi_awvalid <= 1'b0;
                        cfg_state   <= 5'd23;
                    end
                end
                5'd23: begin
                    axi_wdata  <= 32'h81;
                    axi_wvalid <= 1'b1;
                    cfg_state  <= 5'd24;
                end
                5'd24: begin
                    axi_wvalid <= 1'b0;
                    done_cnt    <= 8'd0;
                    cfg_done_reg <= 1'b1;   // start 256-cycle pulse
                    cfg_state  <= 5'd25;
                end

                // ============================================
                // State 25: Running
                // ============================================
                5'd25: begin
                    axi_wvalid  <= 1'b0;
                    axi_awvalid <= 1'b0;
                    if (done_cnt < 8'd255) begin
                        done_cnt <= done_cnt + 1;
                    end else begin
                        cfg_done_reg <= 1'b0;   // pulse ends after 256 cycles
                    end
                end

                default: cfg_state <= 5'd0;
            endcase
        end
    end

    // Output
    assign led           = prng_out[7:0];
    assign led_valid     = prng_out_valid;
    assign dbg_state     = cfg_state;
    assign dbg_prng_out  = prng_out;
    assign dbg_axi_stat  = {axi_awvalid, axi_awready, axi_wvalid, axi_wready};
    assign cfg_done      = (cfg_state >= 5'd24);   // LEVEL trigger for ILA (was 256-cycle pulse)
    assign dbg_time      = time_cnt;
    assign dbg_liveness  = time_cnt[23];           // ~6Hz @ 50MHz proves clk alive
    assign dbg_heartbeat = |cfg_state;             // 1 = FSM has left reset wait (state > 0)
    assign dbg_awaddr    = axi_awaddr;             // live AXI write address
    assign dbg_wdata     = axi_wdata;              // live AXI write data

    // ---- ILA probe2 mux: AXI write diagnostics during config phase ----
    // In states 0-23 (AXI write phase): {wdata[31:0], awaddr[5:0], 26'd0}
    //   - Upper 32 bits = write data (seeds: 1000, 2000, 3000, 4000; ctrl=1; ap_start=0x81)
    //   - Bits [31:26] = write address
    // In states 24-25 (running): prng_out[63:0] (normal PRNG output)
    assign ila_probe2_diag = (cfg_state < 5'd24) ? {axi_wdata, axi_awaddr, 26'd0} : prng_out;

endmodule
