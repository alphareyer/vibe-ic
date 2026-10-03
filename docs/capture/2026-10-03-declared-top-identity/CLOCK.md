# Bind primary prose timing to the declared input clock

Program-first, Bucket A, producer owner:
`programs/phase1_doc_one_shot_runner.py::gen_l8_timing_waveform`.
The prose fmax classifier supplied generic `clk`; the typed post-pass copied
that placeholder into `source_pin` while L1 declared `i_clk`. The normal
waveform seeder emitted `i_clk` separately, with no bound period.

A fresh pinned-image base reproduction from all nine unchanged original
current-run documents emits L8 domains `clk` at GF180 20 ns and waveform
`i_clk` with an unknown period. Four of eight synthetic producer controls
fail on base. The repair binds unnamed primary prose only when L1 declares
exactly one recognized input clock, records the L1 port binding, and lets
the existing sibling frequency walker recognize that prefixed identity.
Explicit domains keep their names; multi-clock or undeclared cases acquire
no guessed binding. No generated layer is edited by hand.

The same current-run input reproduction on the changed producer emits
`i_clk` / `source_pin=i_clk` / 20 ns in both L8 views, retaining the separately
scoped Sky130 frequency as an alternate observation. Bounded affected
producer controls measure 34 passed, 1 skipped, rc=0. The existing skip reason is exactly
`checked-in converged artifact not present`; it earns no credit.
Wrapper controls were not repeated.

Receipts: `source-base-clock-artifact.json`, `source-clock-artifact.json`,
`source-base-clock-controls.log`, `source-clock-controls.log`, launch argv
and exits, in portable packet `declared-top-identity-evidence-r2.tar`.
SHA256 references are listed in `CAPTURE.md`.
Canonical regenerated L9 consumption and whole-IC acceptance remain pending.
