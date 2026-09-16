<!-- VENDORED INPUT EXCERPT — R-0915-64 fixture.
     source : benchmark-data origin/main :: ic/subservient/input/docs/L6_calibration.md
     sha256 : 87506486d4ccd5510fa32245506e1d5c414a712e3ef92e570f24302ea63ca469
     document is 20 line(s); 14 vendored here.
     INPUT ONLY — no oracle, harness or golden content (§4.05).
     Vendored 2026-09-16 by lane icsub2. -->
---
layer: L6
ic: subservient
status: not-applicable
written_at: 2026-05-22
# L6 — Calibration / Lab Procedures
## 適用性 — N/A
`subservient` 為純數位設計,**無 analog/mixed-signal 內容**:
- 無 trimming
- 無 OTP-based calibration
- 無 lab measurement procedure
- 無 analog bias adjustment
- 無溫度補償
→ Plugin 不需產生 calibration controller、OTP interface、analog trim DAC 等。
