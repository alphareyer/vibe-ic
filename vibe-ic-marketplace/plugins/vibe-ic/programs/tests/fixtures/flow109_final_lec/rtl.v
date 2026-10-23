module proof_alpha(input a, input b, input c, output y, output z);
    assign y = (a & b) ^ c;
    assign z = ~c;
endmodule
