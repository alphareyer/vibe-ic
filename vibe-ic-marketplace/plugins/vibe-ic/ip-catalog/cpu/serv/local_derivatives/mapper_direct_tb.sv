`timescale 1ns/1ps
`default_nettype none
// Reviewer-authored neutral direct-module challenge. No original firmware,
// reference DUT, imported test factory, or generated golden model is used.
module mapper_direct_tb;
  parameter integer CASE_ID = 0;
  parameter integer RF_REGS = 36;
  parameter integer CORRUPT_RETURN = 0;
  parameter integer SUPPRESS_PAUSE = 0;
  localparam integer DEPTH = 1024;
  localparam integer RF_AW = $clog2(RF_REGS*4);

  reg i_clk = 0;
  reg i_rst = 1;
  reg [RF_AW-1:0] i_waddr = 9;
  reg [7:0] i_wdata = 8'h87;
  reg i_wen = 0;
  reg [RF_AW-1:0] i_raddr = 12;
  wire [7:0] o_rdata;
  reg i_ren = 0;
  wire [9:0] o_sram_waddr;
  wire [7:0] o_sram_wdata;
  wire o_sram_wen;
  wire [9:0] o_sram_raddr;
  wire [7:0] i_sram_rdata;
  wire o_sram_ren;
  reg [9:2] i_wb_adr = 0;
  reg [31:0] i_wb_dat = 32'ha1b2c3d4;
  reg [3:0] i_wb_sel = 4'hf;
  reg i_wb_we = 0;
  reg i_wb_stb = 0;
  wire [31:0] o_wb_rdt;
  wire o_wb_ack;

  servile_rf_mem_if #(.depth(DEPTH), .rf_regs(RF_REGS)) dut (
    .i_clk(i_clk), .i_rst(i_rst), .i_waddr(i_waddr),
    .i_wdata(i_wdata), .i_wen(i_wen), .i_raddr(i_raddr),
    .o_rdata(o_rdata), .i_ren(i_ren),
    .o_sram_waddr(o_sram_waddr), .o_sram_wdata(o_sram_wdata),
    .o_sram_wen(o_sram_wen), .o_sram_raddr(o_sram_raddr),
    .i_sram_rdata(i_sram_rdata), .o_sram_ren(o_sram_ren),
    .i_wb_adr(i_wb_adr), .i_wb_dat(i_wb_dat),
    .i_wb_sel(i_wb_sel), .i_wb_we(i_wb_we), .i_wb_stb(i_wb_stb),
    .o_wb_rdt(o_wb_rdt), .o_wb_ack(o_wb_ack)
  );

  reg [7:0] memory [0:DEPTH-1];
  reg [9:0] read_address_q = 0;
  reg [9:0] write_address_q = 0;
  reg [7:0] write_data_q = 0;
  reg write_enable_q = 0;
  integer cycles = 0;
  integer ack_edges = 0;
  integer rf_pause_edges = 0;
  integer rf_write_commits = 0;
  integer whole_word_checks = 0;
  reg consume_pending_ack = 0;
  reg [31:0] consume_literal_word = 0;

  // Exact selected interface boundary: address/data/write qualifier register
  // together, asynchronous read data, writes commit at every admitted edge.
  // Only the existing top reset write-admission gate prevents reset writes.
  assign i_sram_rdata = (CORRUPT_RETURN && read_address_q == 10'h101)
                       ? memory[read_address_q] ^ 8'h01
                       : memory[read_address_q];
  always @(posedge i_clk) begin
    if (write_enable_q && !i_rst) begin
      memory[write_address_q] <= write_data_q;
      if (write_address_q == 10'h3f6)
        rf_write_commits = rf_write_commits + 1;
    end
    read_address_q <= o_sram_raddr;
    write_address_q <= o_sram_waddr;
    write_data_q <= o_sram_wdata;
    write_enable_q <= o_sram_wen;
    if (i_rst) write_enable_q <= 0;
  end

  // One 20 ns clock interval. Checks are port observations before issue,
  // followed by actual registered acknowledgement after the sampling edge.
  // owner: 0=WB read, 1=WB write, 2=RF priority, -1=no launch assertion.
  task step;
    input integer owner;
    input [9:0] address;
    input [7:0] data;
    input integer expected_we;
    input integer expected_ack;
    begin
      #1;
      if (consume_pending_ack) begin
        if (o_wb_ack !== 1'b1 || o_wb_rdt !== consume_literal_word)
          $fatal(1,"LAST_BYTE_ACK_CONSUMPTION_VALUE ack=%b actual=%08h expected=%08h",
                 o_wb_ack,o_wb_rdt,consume_literal_word);
        consume_pending_ack=0;
      end
      if (owner == 0 || owner == 1) begin
        if (o_sram_raddr !== address || o_sram_waddr !== address)
          $fatal(1, "ADDRESS_VALUE owner=%0d actual=%h/%h expected=%h", owner,
                 o_sram_raddr, o_sram_waddr, address);
        if (o_sram_wen !== expected_we[0])
          $fatal(1, "WRITE_SELECT_VALUE actual=%b expected=%b",o_sram_wen,expected_we[0]);
        if (o_sram_ren !== (owner == 0))
          $fatal(1, "READ_QUALIFIER_VALUE owner=%0d actual=%b",owner,o_sram_ren);
        if (owner == 1 && o_sram_wdata !== data)
          $fatal(1, "WRITE_BYTE_VALUE actual=%h expected=%h",o_sram_wdata,data);
      end
      if (owner == 2) begin
        if (o_sram_raddr !== 10'h3f3 || o_sram_waddr !== 10'h3f6 ||
            o_sram_wen !== 1'b1 || o_sram_wdata !== 8'h87 || o_sram_ren !== 1'b1)
          $fatal(1, "RF_PRIORITY_VALUE raddr=%h waddr=%h wen=%b data=%h ren=%b",
                 o_sram_raddr,o_sram_waddr,o_sram_wen,o_sram_wdata,o_sram_ren);
        if (!i_wb_stb || !i_wen)
          $fatal(1,"CHALLENGE_NOT_TRIGGERED: RF did not compete with an active WB transaction");
        rf_pause_edges = rf_pause_edges + 1;
      end
      #8; i_clk = 1;
      #1;
      if (o_wb_ack !== expected_ack[0])
        $fatal(1,"ACK_VALUE case=%0d cycle=%0d actual=%b expected=%b",CASE_ID,cycles,o_wb_ack,expected_ack[0]);
      if (o_wb_ack === 1'b1) ack_edges = ack_edges + 1;
      cycles = cycles + 1;
      #10; i_clk = 0;
    end
  endtask

  task fresh_fixture;
    integer n;
    begin
      i_wb_stb=0; i_wen=0; i_ren=0; i_rst=1; i_wb_we=0;
      i_waddr=9; i_raddr=12; i_wdata=8'h87;
      for (n=0; n<DEPTH; n=n+1) memory[n]=(37*n+11)&255;
      // Independent 32-bit word literals, all lanes distinct from RF poison.
      memory[10'h100]=8'h19; memory[10'h101]=8'ha6;
      memory[10'h102]=8'h3c; memory[10'h103]=8'hd2;
      memory[10'h140]=8'h7e; memory[10'h141]=8'h42;
      memory[10'h142]=8'h95; memory[10'h143]=8'hab;
      memory[10'h180]=8'h11; memory[10'h181]=8'h22;
      memory[10'h182]=8'h33; memory[10'h183]=8'h44;
      memory[10'h3f3]=8'he7; memory[10'h3f6]=8'h5d;
      step(-1,0,0,0,0); step(-1,0,0,0,0);
      i_rst=0;
    end
  endtask

  task rf_pause;
    begin
      if (!SUPPRESS_PAUSE) begin
        i_wen=1; i_ren=1; i_raddr=12; i_waddr=9; i_wdata=8'h87;
        step(2,0,0,1,0); step(2,0,0,1,0);
        // This is an observed, distinct RF return, not only requested input.
        if (i_sram_rdata !== 8'he7)
          $fatal(1,"RF_RETURN_CHALLENGE_VALUE actual=%h expected=e7",i_sram_rdata);
        i_wen=0; i_ren=0;
      end
    end
  endtask

  task read_word;
    input [9:0] base;
    input [31:0] literal_expected;
    input integer pause_case;
    integer lane;
    begin
      i_wb_adr=base[9:2]; i_wb_we=0; i_wb_sel=4'hf; i_wb_stb=1;
      for (lane=0; lane<4; lane=lane+1) begin
        // Case 4 pauses issuance of the final lane. Pausing AFTER its
        // successful issue cannot retract the already-valid acknowledgement.
        if (pause_case==4 && lane==3) rf_pause();
        step(0,base+lane,0,0,(lane==3));
        if (pause_case==lane+1 && lane<3) rf_pause();
      end
      if (o_wb_rdt !== literal_expected)
        $fatal(1,"WHOLE_WORD_VALUE case=%0d actual=%08h expected=%08h",CASE_ID,o_wb_rdt,literal_expected);
      whole_word_checks=whole_word_checks+1;
      if (pause_case && (rf_pause_edges < 2 || rf_write_commits == 0))
        $fatal(1,"CHALLENGE_NOT_TRIGGERED: no observed RF pause/write commit");
      if (pause_case==4) begin
        // A competing RF issue may change combinational addresses now, but
        // final WB byte3 and ACK must still be correct for their next sample.
        // This extra consumption boundary distinguishes case4 from case3.
        consume_pending_ack=1; consume_literal_word=literal_expected;
        i_wen=1; i_ren=1;
        step(2,0,0,1,0); step(2,0,0,1,0);
        if (i_sram_rdata !== 8'he7)
          $fatal(1,"LAST_LANE_RF_RETURN_VALUE actual=%h expected=e7",i_sram_rdata);
        i_wen=0; i_ren=0;
      end
      i_wb_stb=0;
      step(-1,0,0,0,0);
    end
  endtask

  task write_select_case;
    input [3:0] select_mask;
    input [31:0] expected_memory_word;
    integer lane;
    reg [7:0] literal_data;
    begin
      fresh_fixture();
      i_wb_adr=10'h180>>2; i_wb_stb=1; i_wb_we=1;
      i_wb_dat=32'ha1b2c3d4; i_wb_sel=select_mask;
      for (lane=0;lane<4;lane=lane+1) begin
        case (lane)
          0: literal_data=8'hd4;
          1: literal_data=8'hc3;
          2: literal_data=8'hb2;
          3: literal_data=8'ha1;
        endcase
        step(1,10'h180+lane,literal_data,select_mask[lane],(lane==3));
        if (lane==0) rf_pause();
      end
      i_wb_stb=0; i_wb_we=0;
      step(-1,0,0,0,0);
      if ({memory[10'h183],memory[10'h182],memory[10'h181],memory[10'h180]} !== expected_memory_word)
        $fatal(1,"WRITE_WORD_VALUE sel=%b actual=%08h expected=%08h",select_mask,
               {memory[10'h183],memory[10'h182],memory[10'h181],memory[10'h180]},expected_memory_word);
      if (memory[10'h3f6] !== 8'h87 || rf_pause_edges<2 || rf_write_commits==0)
        $fatal(1,"RF_WRITE_CHALLENGE_NOT_TRIGGERED: actual=%h",memory[10'h3f6]);
      whole_word_checks=whole_word_checks+1;
    end
  endtask

  integer lane;
  initial begin
    if (CASE_ID<0 || CASE_ID>7 || (RF_REGS!=32 && RF_REGS!=36))
      $fatal(1,"INVALID_REVIEW_FIXTURE_SELECTION");
    fresh_fixture();
    case (CASE_ID)
      0,1,2,3,4: read_word(10'h100,32'hd23ca619,CASE_ID);
      5: begin
        write_select_case(4'b0101,32'h44b222d4);
        write_select_case(4'b1010,32'ha133c311);
      end
      6: begin
        i_wb_stb=1; i_wb_we=0; i_wb_adr=10'h100>>2;
        step(0,10'h100,0,0,0); step(0,10'h101,0,0,0);
        // Abort an actual two-lane in-flight read. Reset must suppress ACK
        // and discard its pending owner; a fresh word must not mix epochs.
        i_rst=1; i_wen=1; i_ren=1;
        step(-1,0,0,0,0);
        i_wb_stb=0; i_rst=0;
        step(-1,0,0,0,0); step(-1,0,0,0,0);
        i_wen=0; i_ren=0;
        read_word(10'h140,32'hab95427e,0);
        if (ack_edges!=1)
          $fatal(1,"RESET_FALSE_ACK_VALUE actual=%0d expected=1",ack_edges);
      end
      7: begin
        i_wb_stb=0; i_ren=1;
        for (lane=0;lane<4;lane=lane+1) begin
          memory[1023-lane]=8'he0+lane;
          i_raddr=lane;
          step(-1,0,0,0,0);
          if (i_sram_rdata !== (8'he0+lane) || o_rdata !== 8'h00)
            $fatal(1,"X0_ZERO_GATE_VALUE lane=%0d raw=%h RF=%h",lane,i_sram_rdata,o_rdata);
        end
        i_raddr=8; memory[10'h3f7]=8'h5a;
        step(-1,0,0,0,0);
        if (o_rdata !== 8'h5a)
          $fatal(1,"NONZERO_RF_GATE_VALUE actual=%h expected=5a",o_rdata);
        if (RF_REGS==36) begin
          i_raddr=128; memory[10'h37f]=8'h6e;
          step(-1,0,0,0,0);
          if (o_rdata !== 8'h6e)
            $fatal(1,"CSR_RF_ADDRESS_GATE_VALUE actual=%h expected=6e",o_rdata);
        end
      end
    endcase
    if (CASE_ID<=4 && ack_edges!=1)
      $fatal(1,"ACK_COUNT_VALUE actual=%0d expected=1",ack_edges);
    if (CASE_ID!=7 && whole_word_checks==0)
      $fatal(1,"CHALLENGE_NOT_TRIGGERED: no independent literal word compared");
    $display("MAPPER_DIRECT_PASS case=%0d regs=%0d cycles=%0d ack_edges=%0d rf_pause_edges=%0d rf_write_commits=%0d literal_word_checks=%0d corrupt=%0d suppress=%0d",
             CASE_ID,RF_REGS,cycles,ack_edges,rf_pause_edges,rf_write_commits,
             whole_word_checks,CORRUPT_RETURN,SUPPRESS_PAUSE);
    $finish;
  end
  initial begin
    #20000; $fatal(1,"MAPPER_DIRECT_TIMEOUT: challenge did not complete");
  end
endmodule
`default_nettype wire
