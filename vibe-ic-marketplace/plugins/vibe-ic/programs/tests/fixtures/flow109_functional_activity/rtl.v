module activity_beta(input a, b, c, output y, z);
  assign y = (a ^ b) & c;
  assign z = ~a;
endmodule
