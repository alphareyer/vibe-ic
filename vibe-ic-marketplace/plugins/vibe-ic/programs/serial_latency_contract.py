"""One clock origin for serial input/output framing throughout Phase 2.

The first serial input bit is sampled at rising edge 0.  The matching first
output bit is observed after rising edge L (after nonblocking updates settle).
The oracle's capture-array index is therefore exactly ``latency_cycles``.
This is a serial data-path offset, not cycles since reset release.
"""

LATENCY_ORIGIN = (
    "first serial input bit sampled at rising edge 0; matching output "
    "bit observed after rising edge L"
)
GENERATED_INPUT_REGISTER_LATENCY = 1
POST_EDGE_SETTLE_VERILOG = "        #1;"
