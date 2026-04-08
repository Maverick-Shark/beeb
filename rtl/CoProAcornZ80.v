// CoProAcornZ80.v — DIAGNOSTIC VERSION
// Forces R2 data_available=1 and break_type=0 to bypass host handshake.
// Use this ONLY for testing. Remove DIAG_BYPASS_R2 for production.

`timescale 1ns / 1ps
`define DIAG_BYPASS_R2

module CoProAcornZ80 (
    input        h_clk,
    input        h_cs_b,
    input        h_rdnw,
    input  [2:0] h_addr,
    input  [7:0] h_data_in,
    output [7:0] h_data_out,
    input        h_rst_b,
    output       h_irq_b,

    input        clk_cpu,
    input        cpu_clken,

    input        rom_sel,

    output [15:0] ram_addr,
    output  [7:0] ram_data_in,
    input   [7:0] ram_data_out,
    output        ram_wr,

    output  [7:0] test
);

// =====================================================================
// Power-on reset
// =====================================================================
reg  [8:0] por_counter = 9'd0;
reg        RSTn_sync;
wire       p_rst_b;

always @(posedge clk_cpu) begin
    if (!h_rst_b)
        por_counter <= 9'd0;
    else if (!por_counter[8])
        por_counter <= por_counter + 9'd1;
    RSTn_sync <= p_rst_b & por_counter[8];
end

// =====================================================================
// Z80 CPU signals
// =====================================================================
wire        z80_m1_n;
wire        z80_mreq_n;
wire        z80_iorq_n;
wire        z80_rd_n;
wire        z80_wr_n;
wire        z80_rfsh_n;
wire        z80_halt_n;
wire [15:0] z80_addr;
wire  [7:0] z80_dout;
wire  [7:0] z80_din;

wire        p_irq_b_raw;
wire        p_nmi_b_raw;
reg         IRQ_n_sync;
reg         NMI_n_sync;

always @(posedge clk_cpu) begin
    if (!RSTn_sync) begin
        IRQ_n_sync <= 1'b1;
        NMI_n_sync <= 1'b1;
    end else if (cpu_clken) begin
        IRQ_n_sync <= p_irq_b_raw;
        NMI_n_sync <= p_nmi_b_raw;
    end
end

// =====================================================================
// ROM shadow latch (IC15A)
// =====================================================================
reg rom_latch;

wire nmi_detect = !z80_m1_n && (z80_addr == 16'h0066);
wire inst_fetch = !z80_m1_n && !z80_mreq_n;
wire rom_clear  = inst_fetch && z80_addr[15];

always @(posedge clk_cpu) begin
    if (!RSTn_sync)
        rom_latch <= 1'b1;
    else if (cpu_clken) begin
        if (nmi_detect)
            rom_latch <= 1'b1;
        else if (rom_clear)
            rom_latch <= 1'b0;
    end
end

// =====================================================================
// Address decode
// =====================================================================
wire z80_mem    = !z80_mreq_n && z80_rfsh_n;
wire z80_io     = !z80_iorq_n && z80_m1_n;
wire z80_intack = !z80_iorq_n && !z80_m1_n;

wire rom_active = z80_mem && rom_latch && !z80_rd_n &&
                  (z80_addr[15:12] == 4'h0);

assign ram_wr      = z80_mem && !z80_wr_n;
assign ram_data_in = z80_dout;
assign ram_addr    = z80_addr;

// =====================================================================
// Z80 data input mux
// =====================================================================
wire [7:0] rom_data;
wire [7:0] tube_p_data_out;

`ifdef DIAG_BYPASS_R2
// DIAGNOSTIC: Intercept R2 status/data reads to bypass host handshake.
// R2 status (port 02h, p_addr=010): force bit7=1 (data available)
// R2 data   (port 03h, p_addr=011): force 0x00 (soft break type)
wire diag_r2_status = z80_io && !z80_rd_n && (z80_addr[2:0] == 3'h2);
wire diag_r2_data   = z80_io && !z80_rd_n && (z80_addr[2:0] == 3'h3);

assign z80_din =
    z80_intack     ? 8'hFE           :
    diag_r2_status ? 8'hFF           :  // DIAG: R2 always has data
    diag_r2_data   ? 8'h00           :  // DIAG: break type = soft break
    z80_io         ? tube_p_data_out :
    rom_active     ? rom_data        :
                     ram_data_out;
`else
assign z80_din =
    z80_intack ? 8'hFE           :
    z80_io     ? tube_p_data_out :
    rom_active ? rom_data        :
                 ram_data_out;
`endif

// =====================================================================
// Tube ULA
// =====================================================================
wire tube_p_cs_b = ~(z80_io & cpu_clken);

tube inst_tube (
    .h_addr     ( h_addr          ),
    .h_cs_b     ( h_cs_b          ),
    .h_data_in  ( h_data_in       ),
    .h_data_out ( h_data_out      ),
    .h_phi2     ( h_clk           ),
    .h_rdnw     ( h_rdnw          ),
    .h_rst_b    ( h_rst_b         ),
    .h_irq_b    ( h_irq_b         ),
    .p_addr     ( z80_addr[2:0]   ),
    .p_cs_b     ( tube_p_cs_b     ),
    .p_data_in  ( z80_dout        ),
    .p_data_out ( tube_p_data_out ),
    .p_rdnw     ( ~z80_rd_n       ),
    .p_phi2     ( clk_cpu         ),
    .p_rst_b    ( p_rst_b         ),
    .p_nmi_b    ( p_nmi_b_raw     ),
    .p_irq_b    ( p_irq_b_raw    )
);

// =====================================================================
// Boot ROM
// =====================================================================
acorn_z80_rom inst_rom (
    .CLK     ( clk_cpu         ),
    .ADDR    ( z80_addr[11:0]  ),
    .ROM_SEL ( rom_sel         ),
    .DATA    ( rom_data        )
);

// =====================================================================
// T80s Z80 CPU
// =====================================================================
T80s #(
    .Mode    (0),
    .T2Write (1),
    .IOWait  (1)
) inst_cpu (
    .RESET_n ( RSTn_sync  ),
    .CLK     ( clk_cpu    ),
    .CEN     ( cpu_clken  ),
    .WAIT_n  ( 1'b1       ),
    .INT_n   ( IRQ_n_sync ),
    .NMI_n   ( NMI_n_sync ),
    .BUSRQ_n ( 1'b1       ),
    .M1_n    ( z80_m1_n   ),
    .MREQ_n  ( z80_mreq_n ),
    .IORQ_n  ( z80_iorq_n ),
    .RD_n    ( z80_rd_n   ),
    .WR_n    ( z80_wr_n   ),
    .RFSH_n  ( z80_rfsh_n ),
    .HALT_n  ( z80_halt_n ),
    .BUSAK_n (            ),
    .OUT0    ( 1'b0       ),
    .A       ( z80_addr   ),
    .DI      ( z80_din    ),
    .DO      ( z80_dout   )
);

// =====================================================================
// Debug
// =====================================================================
assign test = { RSTn_sync, rom_latch, z80_halt_n, z80_m1_n,
                NMI_n_sync, IRQ_n_sync, p_rst_b, p_nmi_b_raw };

endmodule
