# A04 source-authorized analog topology component

`--entry-step A04 --exit-step A04` runs the existing
`analog_a2_topology_emit` and its retained topology reader. The deterministic
library is a renderer, not an approval oracle. `input/flow109/A04.json` must
bind an independently approved analog-design declaration, positive gain,
noise and power budgets, typed model/corner scope, structured per-block specs,
and a current passing A02 component receipt. Missing or stale inputs refuse
before production.

The approved block row names the exact circuit class, selected topology and
required device-role population. The adapter rejects any mismatch with the
emitted IR, any honest library gap, a pre-existing topology not freshly
emitted by the retained producer, or a retained-checker failure. A successful
component freezes the exact topology artifacts under
`adopted/analog_topology/topology_freeze.json`.

This is a bounded A04 component result. Performance, statistical/PVT
coverage, G1-G9, full Flow 109 qualification and benchmark PASS remain
`NOT_MEASURED`.
