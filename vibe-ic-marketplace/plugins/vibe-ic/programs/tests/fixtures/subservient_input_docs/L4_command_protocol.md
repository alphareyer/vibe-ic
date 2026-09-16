<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L4_command_protocol.md
     sha256 : a7db2cc35312d1bce0f4d07461a8d855e29aae3b000bac83f2d737d6d2b778b4
     document is 23 line(s); 16 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
---
layer: L4
ic: subservient
status: not-applicable
written_at: 2026-05-22
# L4 — Command / Protocol Layer
## 適用性 — N/A(以 chip 級觀點)
`subservient` 為 **MCU class SoC** — 它「執行 firmware」而非「接受外部 command」。
**沒有**:
- chip-level SPI / I2C / UART command interface
- opcode-encoded command parsing(SoC 沒做)
- software register write-based control(no SW-visible chip register;見 L5)
**有**(屬於 firmware 層級,不是 chip-level 規格):
- RV32I 指令集語意:**這是 firmware 寫的程式碼層級**,屬 RISC-V ISA 標準,不在本 chip spec 的 L4 範圍內
- GPIO 輸出可作為 UART tx(by firmware bit-banging GPIO):這是 application,**不是 chip 提供的 protocol**
→ Plugin 不需產生任何 chip-level protocol decoder。
