<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L3_external_interface.md
     sha256 : 5e84f855e619b9c040008e7e668473f2806187cb256d7f900fdc503830357edc
     document is 74 line(s); 22 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
| `o_gpio` | ≥ 1-bit | output | GPIO 輸出;預設 1 pin,可作為 simple debug bit 或 UART tx |
2. 外部 tester / FPGA framework 寫入 firmware hex 到 SRAM(`memsize` bytes 範圍)
---
layer: L3
ic: subservient
status: draft
written_at: 2026-05-22
sources:
  - reference/README.md ("exposes a port intended to be connected to an SRAM" + GPIO pin)
  - reference/doc/subservient_externals.png (interface block diagram)
  - reference/data/openlane_common.tcl (CLOCK_PORT = i_clk)
  - reference/data/sky130.tcl (CLOCK_PERIOD = 10 ns)
r1_r2_r3_compliance:
  r1_schema_only: "PASS — 僅描述對外 chip-level port 與 pad placement"
  r2_blackbox: "PASS — 引用 README 描述 + OpenLane config + externals block diagram;未閱讀 RTL"
  r3_multiple_correct: "PASS — IO cell 型別、pad sequence、SRAM 介面具體 protocol 由 Plugin 自選"
# L3 — External Interface
## Module 對外端口
> 來源:`reference/README.md` 描述「`subservient` exposes a port intended to be connected to an SRAM, and a GPIO pin」,加上 `openlane_common.tcl` 確認 `CLOCK_PORT = i_clk`。具體訊號表如下(基於 SoC top 對外契約;Plugin 自行決定每個 group 的具體位寬與 sub-port 數量):
| Port group | 寬度 | 方向 | 描述 |
|---|---|---|---|
| `i_clk` | 1-bit | input | 系統時脈;所有資料於上升沿同步 |
