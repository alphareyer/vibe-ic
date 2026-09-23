#!/usr/bin/env python3
"""Stage 3 (Physical Design + Sign-off) interim gate.

ENFORCEMENT: advisory

The line above is a DECLARATION, in the anchored form `flow_gate_enforcement_
audit.declared_intent` reads. This program is wired into the flow as an
`advisory_program_exit_zero` clause: it RUNS on every project that reaches its
step, its findings are printed, and its exit code cannot deny the step its PASS
tier. That is deliberate — it was wired to make a real check reachable, not to
block a landing on debt it did not create — and the declaration says so where
the audit looks. Without it, "wired where it cannot block" and "nobody decided"
are the same record, and the reliable way to stay clean is to say nothing.
Thin wrapper around `flow_compliance_check.py --stage 3`. Run this after
completing Steps 14-24. GDS (Step 27) is in Stage 4 and is gated on Stage 3
being fully clean — do NOT produce GDS if this gate fails.

Usage:
    python3 stage3_compliance.py <project_dir> [--json out.json]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from flow_compliance_check import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:] + ["--stage", "3", "--strict"]
    rc = main(argv)
    # step_metrics_adoption_check: step 37 declares this program, so its
    # outcome is emitted through the one metrics schema, attributed to the step
    # whose clause ran it. Best-effort: it cannot change the gate's rc.
    import step_metrics  # noqa: E402
    step_metrics.emit_gate_outcome("stage3_compliance", argv, rc)
    sys.exit(rc)
