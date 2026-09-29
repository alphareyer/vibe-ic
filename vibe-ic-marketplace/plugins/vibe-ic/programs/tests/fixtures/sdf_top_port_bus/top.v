module buf1(A, Z);
  input A;
  output Z;
  buf (Z, A);
endmodule
module top(a, q, p);
  input a;
  output [1:0] q;
  output p;
  wire pr, qr;
  buf1 u1(.A(a), .Z(qr));
  buf1 u2(.A(a), .Z(pr));
  assign q[1] = qr;
  assign p = pr;
endmodule
