module buf1(A, Z);
  input A;
  output Z;
  buf (Z, A);
endmodule
module top(a, q, p);
  input a;
  output [1:0] q;
  output p;
  buf1 u1(.A(a), .Z(q[1]));
  buf1 u2(.A(a), .Z(p));
endmodule
