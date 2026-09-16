<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L1_product_metadata.md
     sha256 : 03ff65dfda75c730001edbaad6c29e8f1f31d82212f6a975fdbbbcd3dd0bd2d5
     document is 63 line(s); 22 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
---
layer: L1
ic: subservient
status: draft
written_at: 2026-05-22
sources:
  - reference/README.md
  - reference/subservient.core (FuseSoC manifest)
  - reference/data/sky130.tcl + openlane_common.tcl
  - reference_serv/doc/overview.rst + interface.rst (借用作 SERV sub-module spec)
r1_r2_r3_compliance:
  r1_schema_only: "PASS — 描述 SoC-level 產品意圖與 tapeout target,不描述模組階層或實作"
  r2_blackbox: "PASS — 引用 README + datasheet + FuseSoC manifest + OpenLane config(均對外公開規格);未閱讀 RTL"
  r3_multiple_correct: "PASS — 允許 Plugin 選擇不同 memory 配置 / cell 階層"
# L1 — Product & Tapeout Metadata
## 產品基本資訊
| 欄位 | 值 |
|---|---|
| product_name | `subservient` |
| product_family | minimal RISC-V SoC(MCU class) |
| 功能一句話描述 | Minimal SERV-based RISC-V SoC,單一共享 SRAM(I-mem + D-mem + RF)+ GPIO peripheral,專為 ASIC tapeout(OpenMPW shuttle 級規模)設計 |
| 應用情境 | OpenMPW / chipIgnite shuttle tapeout、超小規模 embedded MCU、教學用 RISC-V MCU、IoT minimal compute node |
