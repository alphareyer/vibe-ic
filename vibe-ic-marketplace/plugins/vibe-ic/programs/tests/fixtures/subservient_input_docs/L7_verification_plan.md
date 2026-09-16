<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L7_verification_plan.md
     sha256 : 193ac55e8514c8e66a5db9ecaecef0ae23f332157e4206ad09b7ca7ecf17d48f
     document is 123 line(s); 22 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
  - reference/README.md (simulation targets + firmware test files)
  - reference/sw/blinky.S / hello.S (firmware test cases for functional verification)
  r1_schema_only: "PASS — 只列驗證目標與覆蓋率要求,不列具體 testbench 寫法"
# L7 — Verification Plan
## 7.1 Functional Verification
### 7.1.1 Primary functional tests
**透過 firmware 執行驗證**:用 reference/sw/ 提供的 firmware hex 跑模擬與 FPGA 板測:
| Test firmware | 預期結果 | 涵蓋範圍 |
| Test class | 必過判定 |
| 整套 RV32I 指令(40+ 條)單元測試 | 100% PASS(可用 RISC-V Compliance suite 或 SERV 內附 testbench) |
## 7.2 Timing Verification (STA — Multi-corner)
## 7.3 Physical Verification
- ❌ 具體 testbench framework(cocotb / Verilator / iverilog 任選)
---
layer: L7
ic: subservient
status: draft
written_at: 2026-05-22
sources:
  - reference/data/sky130.tcl (clock + corner targets)
r1_r2_r3_compliance:
  r2_blackbox: "PASS — 不引用 reference RTL 內部結構;使用 firmware-level 對外可觀察行為"
