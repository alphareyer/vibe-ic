// Subservient chip glue authored from the design input around official SERV.
// The external SRAM is a single shared 8-bit store with independent synchronous
// read and write address ports. Its highest RF_SPACE bytes hold the CPU RF.
`default_nettype none
module subservient #(
    parameter integer memsize = 1024,
    parameter [31:0] RESET_PC = 32'h00000000,
    parameter [0:0] WITH_CSR = 1'b0
) (
    input wire i_clk,
    input wire i_rst,
    output wire [$clog2(memsize)-1:0] o_sram_addr,
    output wire [$clog2(memsize)-1:0] o_sram_waddr,
    output wire [7:0] o_sram_data,
    input wire [7:0] i_sram_data,
    output wire o_sram_we,
    output wire o_sram_ren,
    output wire o_sram_cyc,
    output reg o_gpio
);
    localparam integer MEM_AW = $clog2(memsize);
    localparam integer RF_REGS = 32 + WITH_CSR * 4;
    localparam integer RF_AW = $clog2(RF_REGS * 4);
    localparam integer RF_SPACE = 1 << RF_AW;

    wire [31:0] mem_adr, mem_dat, mem_rdt;
    wire [3:0] mem_sel;
    wire mem_we, mem_stb, mem_ack;
    wire [31:0] ext_adr, ext_dat;
    wire [3:0] ext_sel;
    wire ext_we, ext_stb;
    reg ext_ack;
    wire [31:0] ext_rdt = {31'b0, o_gpio};

    wire [RF_AW-1:0] rf_waddr, rf_raddr;
    wire [7:0] rf_wdata, rf_rdata;
    wire rf_wen, rf_ren;
    wire [31:0] shared_rdt;
    wire shared_ack;
    // Capture the selected transaction and its full-width range check at the
    // clock boundary.  The shared SRAM outputs are driven from this request
    // register; neither the core's reset gate nor its address arbiter/decode
    // sits in their combinational output path.  SERV holds strobe until ACK,
    // so this is an ordinary one-entry Wishbone wait-state boundary.
    wire mem_valid = mem_adr < (memsize - RF_SPACE);
    reg req_active;
    reg req_wait_drop;
    reg req_valid;
    reg [MEM_AW-1:2] req_adr;
    reg [31:0] req_dat;
    reg [3:0] req_sel;
    reg req_we;
    reg invalid_mem_ack;
    wire req_ack = req_active && (req_valid ? shared_ack : invalid_mem_ack);

    servile #(
        .width(1), .rf_width(8), .reset_pc(RESET_PC),
        .reset_strategy("MINI"), .sim(1'b0), .debug(1'b0),
        .with_c(1'b0), .with_csr(WITH_CSR), .with_mdu(1'b0)
    ) u_servile (
        .i_clk(i_clk), .i_rst(i_rst), .i_timer_irq(1'b0),
        .o_wb_mem_adr(mem_adr), .o_wb_mem_dat(mem_dat),
        .o_wb_mem_sel(mem_sel), .o_wb_mem_we(mem_we),
        .o_wb_mem_stb(mem_stb), .i_wb_mem_rdt(mem_rdt),
        .i_wb_mem_ack(mem_ack),
        .o_wb_ext_adr(ext_adr), .o_wb_ext_dat(ext_dat),
        .o_wb_ext_sel(ext_sel), .o_wb_ext_we(ext_we),
        .o_wb_ext_stb(ext_stb), .i_wb_ext_rdt(ext_rdt),
        .i_wb_ext_ack(ext_ack),
        .o_rf_waddr(rf_waddr), .o_rf_wdata(rf_wdata),
        .o_rf_wen(rf_wen), .o_rf_raddr(rf_raddr),
        .i_rf_rdata(rf_rdata), .o_rf_ren(rf_ren)
    );

    servile_rf_mem_if #(
        .depth(memsize), .rf_regs(RF_REGS)
    ) u_shared_memory_if (
        .i_clk(i_clk), .i_rst(i_rst),
        .i_waddr(rf_waddr), .i_wdata(rf_wdata), .i_wen(rf_wen),
        .i_raddr(rf_raddr), .o_rdata(rf_rdata), .i_ren(rf_ren),
        .o_sram_waddr(o_sram_waddr), .o_sram_wdata(o_sram_data),
        .o_sram_wen(o_sram_we), .o_sram_raddr(o_sram_addr),
        .i_sram_rdata(i_sram_data), .o_sram_ren(o_sram_ren),
        .i_wb_adr(req_adr), .i_wb_dat(req_dat),
        .i_wb_sel(req_sel), .i_wb_we(req_we),
        .i_wb_stb(req_active & req_valid),
        .o_wb_rdt(shared_rdt), .o_wb_ack(shared_ack)
    );

    assign mem_rdt = req_active && req_valid ? shared_rdt : 32'b0;
    assign mem_ack = req_ack;
    assign o_sram_cyc = o_sram_we | o_sram_ren;

    // Addresses above the shared SRAM's program/data region receive a
    // deterministic empty response. They cannot alias the RF reservation.
    always @(posedge i_clk) begin
        if (i_rst) begin
            req_active <= 1'b0;
            req_wait_drop <= 1'b0;
            req_valid <= 1'b0;
            invalid_mem_ack <= 1'b0;
            ext_ack <= 1'b0;
            o_gpio <= 1'b0;
        end else begin
            invalid_mem_ack <= req_active & !req_valid & !invalid_mem_ack;
            if (req_ack) begin
                req_active <= 1'b0;
                req_wait_drop <= 1'b1;
            end else if (req_wait_drop) begin
                if (!mem_stb)
                    req_wait_drop <= 1'b0;
            end else if (!req_active && mem_stb) begin
                req_active <= 1'b1;
                req_valid <= mem_valid;
                req_adr <= mem_adr[MEM_AW-1:2];
                req_dat <= mem_dat;
                req_sel <= mem_sel;
                req_we <= mem_we;
            end
            ext_ack <= ext_stb & !ext_ack;
            if (ext_stb && !ext_ack && ext_we && ext_sel[0]
                    && ext_adr == 32'h40000000)
                o_gpio <= ext_dat[0];
        end
    end
endmodule
`default_nettype wire
