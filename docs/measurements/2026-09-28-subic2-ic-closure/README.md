# Subservient IC-path authored glue

`rtl/subservient.v` is the authored top-level glue staged into the isolated
Subservient DIE project. Its SHA-256 is
`5164a0cff5c1278480baa598d05bdf9a5c2e196d2a49faf67eb5a136b10886be`.
The unmodified IC-path project top-level glue had SHA-256
`ed2aa605b4fa3f2b975bf904eb7a75ce339d32bd3cff32007f8a963f8685b2b6`.

The selected request, address, data, strobes, write enable, and range-valid
decision are captured at a clock edge before the shared SRAM interface. The
declared 20 ns clock and 4 ns input/output delays are unchanged. SERV and
servile source files are unchanged. The physical verdict belongs to the
matching Step-9 synthesis and Phase-3 receipts, not to this source copy alone.
