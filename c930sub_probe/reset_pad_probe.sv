`timescale 1ns/1ps
// INPUT-only probe: L2 synchronous active-high reset; L7 glitch/no fetch race
// and SRAM retention; L3/L9 20ns clock, 1024-byte declared shared SRAM.
// These five RV32I words are new directed stimulus, not supplied firmware.
module probe_lane #(parameter PAD=1)(input clk, rst,
  output [9:0] raddr, waddr, output [7:0] wdata,
  output we, ren, cyc, gpio);
  reg [7:0] memory[0:1023];
  reg [7:0] rdata=0;
  integer i, reads=0, writes=0;
  task word(input integer a, input [31:0] v);
    begin memory[a]=v[7:0];memory[a+1]=v[15:8];
      memory[a+2]=v[23:16];memory[a+3]=v[31:24];end
  endtask
  initial begin
    for(i=0;i<1024;i=i+1) memory[i]=0;
    word(0,32'h00100093); // addi x1,x0,1
    word(4,32'h40000137); // lui x2,0x40000
    word(8,32'h00112023); // sw x1,0(x2): GPIO
    word(12,32'h02102023);// sw x1,32(x0): shared data SRAM
    word(16,32'h0000006f);// jal x0,0
  end
  always @(posedge clk) begin
    if(ren===1'b1) begin rdata<=memory[raddr]; reads=reads+1;end
    if(we===1'b1) begin
      if($isunknown({waddr,wdata})) $fatal(1,"QUALIFIED_WRITE_X PAD=%0d addr=%h data=%h",PAD,waddr,wdata);
      memory[waddr]<=wdata;writes=writes+1;
    end
  end
  generate if(PAD) begin: padded
    supply1 vdd; supply0 vss;
    chip_top dut(.i_clk(clk),.i_rst(rst),.o_sram_addr(raddr),
      .o_sram_waddr(waddr),.o_sram_data(wdata),.i_sram_data(rdata),
      .o_sram_we(we),.o_sram_ren(ren),.o_sram_cyc(cyc),.o_gpio(gpio),.VDD(vdd),.VSS(vss));
  end else begin: core
    subservient dut(.i_clk(clk),.i_rst(rst),.o_sram_addr(raddr),
      .o_sram_waddr(waddr),.o_sram_data(wdata),.i_sram_data(rdata),
      .o_sram_we(we),.o_sram_ren(ren),.o_sram_cyc(cyc),.o_gpio(gpio));
  end endgenerate
endmodule

module reset_pad_probe;
  reg clk=0, rst=1, pulse=0;
  always #10 clk=~clk;
  wire [9:0] ra[0:2],wa[0:2]; wire [7:0] wd[0:2];
  wire [2:0] we,ren,cyc,gpio;
  probe_lane clean(clk,rst,ra[0],wa[0],wd[0],we[0],ren[0],cyc[0],gpio[0]);
  probe_lane pulse_pad(clk,rst|pulse,ra[1],wa[1],wd[1],we[1],ren[1],cyc[1],gpio[1]);
  probe_lane #(.PAD(0)) pulse_core(clk,rst|pulse,ra[2],wa[2],wd[2],we[2],ren[2],cyc[2],gpio[2]);
  integer edge_count=0, pulse_count=0, checked=0, idle_data_x=0;
  integer active_data_x=0, fetch_seen=0,first_fetch=-1;
  wire [31:0] ibadr=pulse_pad.padded.dut.u_core.u_servile.wb_ibus_adr;
  wire ibcyc=pulse_pad.padded.dut.u_core.u_servile.wb_ibus_stb;
  wire dbcyc=pulse_pad.padded.dut.u_core.u_servile.wb_dbus_stb;
  wire iback=pulse_pad.padded.dut.u_core.u_servile.wb_ibus_ack;
  wire dback=pulse_pad.padded.dut.u_core.u_servile.wb_dbus_ack;
  wire coreclk=pulse_pad.padded.dut.i_clk__core;
  wire corerst=pulse_pad.padded.dut.i_rst__core;
  wire [31:0] pc=ibadr;
  integer ibacks=0,dbaces=0;
  always @(posedge clk) edge_count=edge_count+1;
  always @(posedge coreclk) begin
    if(iback===1'b1) ibacks=ibacks+1;
    if(dback===1'b1) dbaces=dbaces+1;
  end
  task compare;
    begin
      if({we[0],ren[0],cyc[0],gpio[0]} !== {we[1],ren[1],cyc[1],gpio[1]})
        $fatal(1,"PULSE_CHANGED_CONTROL t=%0t clean=%b pulse=%b",$time,{we[0],ren[0],cyc[0],gpio[0]},{we[1],ren[1],cyc[1],gpio[1]});
      if(ren[0]===1'b1 && ra[0]!==ra[1]) $fatal(1,"PULSE_CHANGED_READ_ADDRESS");
      if(we[0]===1'b1 && {wa[0],wd[0]}!=={wa[1],wd[1]}) $fatal(1,"PULSE_CHANGED_WRITE");
      checked=checked+1;
    end
  endtask
  integer j; reg [31:0] pc_before; integer edge_before; string dump_path;
  reg [7:0] reset_snapshot[0:1023];
  initial begin
    if(!$value$plusargs("DUMPFILE=%s",dump_path)) dump_path="/evidence/reset-pad.vcd";
    $dumpfile(dump_path); $dumpvars(0,reset_pad_probe);
    repeat(4) @(negedge clk);
    for(j=0;j<1024;j=j+1) if(clean.memory[j]!==pulse_pad.memory[j]) $fatal(1,"RESET_MEMORY_DIFFERENCE");
    $display("RESET_ASSERT_SRAM_RETAINED word0=%h%h%h%h",clean.memory[3],clean.memory[2],clean.memory[1],clean.memory[0]);
    #4 rst=0;
    repeat(750) begin
      @(negedge clk); #4;
      compare();
      if(!fetch_seen && ibcyc===1'b1) begin
        fetch_seen=1;first_fetch=edge_count-4;
        if(ibadr!==32'h0) $fatal(1,"RESET_PC_MISMATCH addr=%h",ibadr);
        $display("FIRST_FETCH addr=%h sampled_edges=%0d",ibadr,first_fetch);
      end
      if($isunknown(wd[1]) && we[1]===1'b0) begin
        idle_data_x=idle_data_x+1;
`ifdef REQUIRE_IDLE_WDATA_KNOWN
        $fatal(1,"UNSUPPORTED_IDLE_WRITE_DATA_KNOWN we=%b data=%h",we[1],wd[1]);
`endif
      end
      if($isunknown(wd[1]) && we[1]===1'b1) active_data_x=active_data_x+1;
      pc_before=pc;edge_before=edge_count;pulse=1;
      #2; compare();
      #2 pulse=0;
      #1;
      if(edge_count!=edge_before) $fatal(1,"PROBE_PULSE_CROSSED_CLOCK_EDGE");
      if(pc!==pc_before) $fatal(1,"OFF_EDGE_PULSE_RESET_PC before=%h after=%h",pc_before,pc);
      pulse_count=pulse_count+1;compare();
      if(pulse_count<12 || we[1] || dbcyc)
        $display("TRACE t=%0t rst=%b core_rst=%b ibus=%b/%b adr=%h dbus=%b/%b we=%b ren=%b data=%h gpio=%b",$time,rst,corerst,ibcyc,iback,ibadr,dbcyc,dback,we[1],ren[1],wd[1],gpio[1]);
    end
    if(!fetch_seen || ibacks==0) $fatal(1,"NO_INSTRUCTION_FETCH");
    if(active_data_x) $fatal(1,"ACTIVE_WRITE_UNKNOWN");
    if(gpio[1]!==1'b1 || gpio[2]!==1'b1) $fatal(1,"DIRECTED_GPIO_STORE_FAILED");
    if({pulse_pad.memory[35],pulse_pad.memory[34],pulse_pad.memory[33],pulse_pad.memory[32]}!==32'd1)
      $fatal(1,"DIRECTED_PAD_DATA_STORE_FAILED");
    if({pulse_core.memory[35],pulse_core.memory[34],pulse_core.memory[33],pulse_core.memory[32]}!==32'd1)
      $fatal(1,"DIRECTED_CORE_DATA_STORE_FAILED");
    $display("COUNTS pulses=%0d comparisons=%0d idle_data_x=%0d active_data_x=%0d ibus_acks=%0d dbus_acks=%0d pad_gpio=%b core_gpio=%b pad_mem32=%h core_mem32=%h",pulse_count,checked,idle_data_x,active_data_x,ibacks,dbaces,gpio[1],gpio[2],pulse_pad.memory[32],pulse_core.memory[32]);
    @(negedge clk);#4;
    for(j=0;j<1024;j=j+1) reset_snapshot[j]=pulse_pad.memory[j];
    rst=1;
    @(negedge clk);#4;
    if(gpio[1]!==1'b0 || ibadr!==32'd0) $fatal(1,"EDGE_RESET_STATE_FAILED gpio=%b addr=%h",gpio[1],ibadr);
    for(j=0;j<1024;j=j+1) if(pulse_pad.memory[j]!==reset_snapshot[j]) $fatal(1,"EDGE_RESET_CHANGED_SRAM address=%0d",j);
    rst=0;#4;
    if(ibcyc!==1'b1 || ibadr!==32'd0) $fatal(1,"EDGE_RESET_REBOOT_FAILED");
    $display("EDGE_RESET_REBOOT PASS addr=%h SRAM=1024/1024 retained",ibadr);
    $display("NEUTRAL_RESET_PAD_PROBE PASS");$finish;
  end
endmodule
