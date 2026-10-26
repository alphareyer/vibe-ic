# Verification plan

The verification plan must use cocotb for module-level regression.
The design does not require Verilator.
SymbiYosys is optional and is not part of the required verification plan.

## 7.5 不在 L7 約束的事

- ❌ 具體 testbench framework(cocotb / Verilator / iverilog 任選)
- ❌ Assertion 寫法(SVA / PSL 任選)
- ❌ 覆蓋率收集工具
- ❌ 是否做 gate-level simulation(建議但非強制)
