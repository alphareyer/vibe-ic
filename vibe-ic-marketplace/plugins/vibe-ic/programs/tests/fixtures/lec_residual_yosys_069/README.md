# LEC residual Yosys controls

Captured on 2026-09-29 on 8hd-3 with `ghcr.io/vibeic/vibeic-eda:0.3.85`
(`sha256:70ebc4fba7b456855f8711b70ffe0b94dff7152e5c354545201dc7a486ab469c`),
Yosys 0.69+ (git 4d572059c). The `.ys` scripts and RTL in this directory are
inputs; the `.log`, `.il` and `.json` files are raw tool output. Each proof
script was run as `yosys -s /work/<script>.ys` with this directory mounted at
`/work`. The two search scripts read the corresponding emitted equivalence IL.

`status.log` and `induct.log` compare the reset-equivalent binary counter and
one-hot ring. Both leave `y` unproven; only `induct.log` contains Yosys's
`Proved 0 previously unproven` line. `state_search.log` has a bounded model
from an all-zero one-hot state that the declared reset does not reach.

`diff_status.log` and `diff_induct.log` compare `a & b` with `a | b`.
`diff_search.log` has a complete combinational model with a real mismatch.

The end-to-end test drives the real producer and both gates with these outputs;
it substitutes only Yosys file writes. The pre-fix JUnit is graded by
`control_substance_check.py` so collection or presence failures cannot count.
