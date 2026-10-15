`timescale 1ns/1ps
module serv_mtie_public_tb;
 parameter integer TIMER_CASE=0;
 reg clk=0; always #5 clk=~clk;
 reg rst=1, irq=0;
 // Fixed public Wishbone context: one wait cycle, responses while clock is low.
 reg iack=0,dack=0,iwaiting=0,dwaiting=0;
 reg [31:0] instruction=0;
 wire [31:0] ia, da, wd;
 wire icyc,dcyc,dwe; wire [3:0] sel;
 reg [31:0] rom[0:1023];
 reg [31:0] expected_addr[0:31],expected_data[0:31];
 integer count=0, expected_count, cycles=0, index;
 reg [1023:0] program_file, expected_file;
 always @(negedge clk) begin
  iack=0; dack=0;
  if (rst) begin iwaiting=0; dwaiting=0; end
  else begin
   if (icyc) begin
    if (iwaiting) begin instruction=rom[ia[11:2]]; iack=1; iwaiting=0; end
    else iwaiting=1;
   end else iwaiting=0;
   if (dcyc) begin
    if (dwaiting) begin dack=1; dwaiting=0; end else dwaiting=1;
   end else dwaiting=0;
  end
 end
 serv_rf_top #(.WITH_CSR(1),.W(1),.RESET_STRATEGY("MINI")) dut(
  .clk(clk),.i_rst(rst),.i_timer_irq(irq),
  .o_ibus_adr(ia),.o_ibus_cyc(icyc),.i_ibus_rdt(instruction),.i_ibus_ack(iack),
  .o_dbus_adr(da),.o_dbus_dat(wd),.o_dbus_sel(sel),.o_dbus_we(dwe),.o_dbus_cyc(dcyc),
  .i_dbus_rdt(32'b0),.i_dbus_ack(dack),.i_ext_rd(32'b0),.i_ext_ready(1'b0));
 initial begin
  if (!$value$plusargs("program=%s",program_file) ||
      !$value$plusargs("expected=%s",expected_file) ||
      !$value$plusargs("count=%d",expected_count)) $fatal(1,"missing fixed input");
  $readmemh(program_file,rom);
  begin : read_expected
   reg [63:0] pairs[0:31];
   $readmemh(expected_file,pairs);
   for(index=0;index<32;index=index+1) begin
    expected_addr[index]=pairs[index][63:32]; expected_data[index]=pairs[index][31:0];
   end
  end
  repeat(4) @(negedge clk); rst=0;
 end
 always @(posedge clk) if(!rst) begin
  cycles=cycles+1;
  if (cycles>20000) $fatal(1,"fixed component cycle budget exhausted");
  if (icyc && iack && ((^ia)===1'bx || ia[1:0]!==0 || ia>=4096))
   $fatal(1,"invalid instruction fetch address actual=%08x",ia);
  if (dcyc && dack) begin
   if (dwe!==1'b1 || sel!==4'hf || count>=expected_count)
    $fatal(1,"unknown, non-word or stray external transaction");
   $display("EXTERNAL_STORE index=%0d addr=%08x value=%08x",count,da,wd);
   if (da!==expected_addr[count] || wd!==expected_data[count])
    $fatal(1,"CSR_EFFECT index=%0d expected_addr=%08x actual_addr=%08x expected_value=%08x actual_value=%08x",count,expected_addr[count],da,expected_data[count],wd);
   if (TIMER_CASE && count==0) irq<=1;
   count=count+1;
   if (count==expected_count) begin
    $display("PUBLIC_MTIP_PASS stores=%0d cycles=%0d",count,cycles); $finish;
   end
  end
 end
endmodule
