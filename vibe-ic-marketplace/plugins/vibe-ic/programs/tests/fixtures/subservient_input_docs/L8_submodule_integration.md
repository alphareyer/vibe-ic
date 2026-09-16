<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L8_submodule_integration.md
     sha256 : f002674aff1eca35d870e597257a0bbdbc77e0a10b5ccd37ad625c1a75d4da06
     document is 84 line(s); 22 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
- 角色:至少 1 個 output GPIO pin;可作為 simple debug 或 firmware bit-banged UART tx
### 8.2.6 Debug switch(視 Plugin 設計可選包入)
- 角色:reference 內 `subservient_debug_switch.v` 提供 boot-time 模式切換
---
layer: L8
ic: subservient
status: draft
written_at: 2026-05-22
sources:
  - reference/subservient.core (FuseSoC manifest — 列出 RTL fileset)
  - reference/README.md (top-level architecture)
  - reference_serv/doc/interface.rst (SERV core 接口契約)
r1_r2_r3_compliance:
  r1_schema_only: "PASS — 描述對外契約與 sub-module 整合需求,不指定內部寫法"
  r2_blackbox: "PASS — 從 FuseSoC dependency graph + SERV datasheet 推導 sub-module 結構;未閱讀 RTL"
  r3_multiple_correct: "PASS — 內部 sub-module 階層由 Plugin 自選,只要對外契約滿足"
# L8 — Submodule Integration Spec
## 8.1 Top 層級
`subservient` 提供兩個 top module 選擇,Plugin 在 declaration.json 聲明:
| Top option | 包含 | 用途 |
|---|---|---|
| `subservient` | subservient_core + GPIO peripheral | 完整 SoC,**tape-out 預設 top**(對應 sky130 OpenLane target) |
