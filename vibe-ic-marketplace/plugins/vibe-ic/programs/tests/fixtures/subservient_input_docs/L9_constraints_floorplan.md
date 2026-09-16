<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L9_constraints_floorplan.md
     sha256 : 387127a9edcb4f50d2e7aa470287d7a0a6f215a22e39c61f8b954fe580e315b1
     document is 101 line(s); 22 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
---
layer: L9
ic: subservient
status: draft
written_at: 2026-05-22
sources:
  - reference/data/sky130.tcl (CLOCK_PERIOD = 10)
  - reference/data/openlane_common.tcl (CLOCK_PORT = i_clk)
  - reference/data/gf180.tcl (TBV — file exists, content not yet inspected)
r1_r2_r3_compliance:
  r1_schema_only: "PASS — 描述物理約束目標,不描述具體 placement 結果"
  r2_blackbox: "PASS — 引用工具設定檔(對外規格)而非 OpenLane run 內部產物"
  r3_multiple_correct: "PASS — 允許不同 floorplan / placement,只要落在 target 內"
# L9 — Constraints / Floorplan
## 9.1 Synopsys Design Constraints (SDC)
### 9.1.1 主時脈定義
```sdc
set_units -time ns
create_clock [get_ports i_clk]  -name core_clock  -period <PERIOD>
```
### 9.1.2 各 PDK 對應的 `<PERIOD>`
| PDK / library | `<PERIOD>` (ns) | 對應頻率 |
