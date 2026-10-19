module m(output wire y);
  reg b=0;
  function automatic bit f(input bit q); f=q & b; endfunction
  generate if(1) begin:g localparam bit b=1; assign y=f(b); end endgenerate
  initial begin
    #1; if(y!==0)$fatal(1,"initial");
    b=1; #1; if(y!==1)$fatal(1,"stale hidden port");
  end
endmodule
