// EXCERPT, verbatim, of gf180mcuD libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/gf180mcu_fd_sc_mcu7t5v0.v
// (vibeic-eda 0.3.79): the mux2 functional model and the mux2_2 cell with its specify block.
// Copyright 2022 GlobalFoundries PDK Authors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

`ifndef GF180MCU_FD_SC_MCU7T5V0__MUX2_FUNC_V
`define GF180MCU_FD_SC_MCU7T5V0__MUX2_FUNC_V

`ifdef USE_POWER_PINS
module gf180mcu_fd_sc_mcu7t5v0__mux2_func( Z, I1, S, I0, VDD, VSS, VNW, VPW );
inout VDD, VSS, VNW, VPW;
`else // If not USE_POWER_PINS
module gf180mcu_fd_sc_mcu7t5v0__mux2_func( Z, I1, S, I0 );
`endif // If not USE_POWER_PINS
input I0, I1, S;
output Z;

	wire Z_row1;

	and MGM_BG_0( Z_row1, I0, I1 );

	wire S_inv_for_gf180mcu_fd_sc_mcu7t5v0__mux2_1;

	not MGM_BG_1( S_inv_for_gf180mcu_fd_sc_mcu7t5v0__mux2_1, S );

	wire Z_row2;

	and MGM_BG_2( Z_row2, S_inv_for_gf180mcu_fd_sc_mcu7t5v0__mux2_1, I0 );

	wire Z_row3;

	and MGM_BG_3( Z_row3, I1, S );

	or MGM_BG_4( Z, Z_row1, Z_row2, Z_row3 );

endmodule
`endif // GF180MCU_FD_SC_MCU7T5V0__MUX2_FUNC_V

`ifndef GF180MCU_FD_SC_MCU7T5V0__MUX2_2_V
`define GF180MCU_FD_SC_MCU7T5V0__MUX2_2_V


`ifdef USE_POWER_PINS
module gf180mcu_fd_sc_mcu7t5v0__mux2_2( Z, I1, S, I0, VDD, VSS, VNW, VPW );
inout VDD, VSS, VNW, VPW;
`else // If not USE_POWER_PINS
module gf180mcu_fd_sc_mcu7t5v0__mux2_2( Z, I1, S, I0 );
`endif // If not USE_POWER_PINS
input I0, I1, S;
output Z;

`ifdef USE_POWER_PINS
  gf180mcu_fd_sc_mcu7t5v0__mux2_func gf180mcu_fd_sc_mcu7t5v0__mux2_inst(.Z(Z),.I1(I1),.S(S),.I0(I0),.VDD(VDD),.VSS(VSS),.VNW(VNW),.VPW(VPW));
`else // If not USE_POWER_PINS
  gf180mcu_fd_sc_mcu7t5v0__mux2_func gf180mcu_fd_sc_mcu7t5v0__mux2_inst(.Z(Z),.I1(I1),.S(S),.I0(I0));
`endif // If not USE_POWER_PINS

`ifndef FUNCTIONAL
	// spec_gates_begin


	// spec_gates_end



   specify

	// specify_block_begin

	if(I1===1'b0)
	// comb arc I0 --> Z
	 (I0 => Z) = (1.0,1.0);

	if(I1===1'b1)
	// comb arc I0 --> Z
	 (I0 => Z) = (1.0,1.0);

	ifnone
	// comb arc I0 --> Z
	 (I0 => Z) = (1.0,1.0);

	if(I0===1'b0)
	// comb arc I1 --> Z
	 (I1 => Z) = (1.0,1.0);

	if(I0===1'b1)
	// comb arc I1 --> Z
	 (I1 => Z) = (1.0,1.0);

	ifnone
	// comb arc I1 --> Z
	 (I1 => Z) = (1.0,1.0);

	ifnone
	// comb arc posedge S --> (Z:S)
	 (posedge S => (Z:S)) = (1.0,1.0);

	ifnone
	// comb arc negedge S --> (Z:S)
	 (negedge S => (Z:S)) = (1.0,1.0);

	// specify_block_end

   endspecify

   `endif

endmodule
`endif // GF180MCU_FD_SC_MCU7T5V0__MUX2_2_V
