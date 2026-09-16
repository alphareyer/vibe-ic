<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L2_architecture.md
     sha256 : ea11061a384bc620c6cc16762d9df9f267c473f0e7ff43e7b4dd691e9aee0450
     document is 84 line(s); 22 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
| **GPIO peripheral** | 至少 1 個 GPIO pin;可作為 simple output debug bit 或 UART tx |
---
layer: L2
ic: subservient
status: draft
written_at: 2026-05-22
sources:
  - reference/README.md
  - reference/doc/*.png (5 block diagrams — subservient.png / subservient_core.png / subservient_externals.png / subservient_fpga.png / subservient_tb.png)
  - reference_serv/doc/overview.rst + interface.rst
  - reference/subservient.core (FuseSoC dependency graph)
r1_r2_r3_compliance:
  r1_schema_only: "PASS — 描述 SoC 級功能與資料路徑語意,不描述模組階層內部訊號"
  r2_blackbox: "PASS — 引用對外公開文件(README + datasheet RST + block diagram PNG + FuseSoC manifest);未閱讀 RTL 內部"
  r3_multiple_correct: "PASS — Plugin 可自選 memory 介面、bus 介面、GPIO 實作"
# L2 — Architecture / Functional Spec
## 系統功能 (Functional Specification)
`subservient` 為**完整可獨立 tapeout 的 minimal RISC-V SoC**,核心由 SERV CPU + 共享 SRAM + GPIO peripheral 組成。
```
firmware(.hex)→ external memory preload → release reset
              → SERV CPU 從 RESET_PC 開始執行 → 透過共享 SRAM 取 instructions
              → 計算結果寫入 SRAM 或透過 GPIO/UART 輸出
