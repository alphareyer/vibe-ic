<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L5_register_map.md
     sha256 : 3c3ede509b857e14d42b95f7951f1f05cd564a468338c838eff0234c6db46385
     document is 24 line(s); 17 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
---
layer: L5
ic: subservient
status: not-applicable
written_at: 2026-05-22
# L5 — Register Map
## 適用性 — N/A(以 chip 級觀點)
`subservient` **無 SW-visible chip registers**。
**沒有**:
- chip-level control register
- chip-level status register
- chip-level configuration register
- chip-level interrupt enable / status
**有**(屬於 firmware / ISA 層級,不在 chip-level L5 範圍):
- SERV CPU 內部 CSRs(Control and Status Registers,RISC-V ISA Zicsr 範圍):若 `WITH_CSR=1` 則 enable,但這是 RV ISA 標準定義,屬於 firmware 寫的 CSR access,**不**是 chip 對外的 register map
- GPIO 寫入機制:firmware 透過 store instruction 寫入 SRAM 內某個 memory-mapped GPIO 位址(這是 firmware-defined memory mapping,不是 chip-defined register)
→ Plugin 不需產生任何 SW-visible register file 或 CSR decoder(若採 `WITH_CSR=1` 則 SERV 內部已自帶)。
