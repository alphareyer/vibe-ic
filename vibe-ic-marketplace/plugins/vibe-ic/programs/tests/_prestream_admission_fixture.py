"""Admit a unit fixture's routed layout at the pre-stream boundary.

WHY THIS EXISTS. v1.24.73 (#2635, "no GDS before the layout has passed its
gates") put the pre-stream admission at the top of `step_gds`: unless
`reports/phase3/prestream_gate.json` records a PASS for the CURRENT routed
layout digest, the step returns NOT_MEASURED(upstream_failed) before any
stream-out code runs. That is the intended order. It also meant that every
suite driving the REAL `step_gds` over a synthetic DEF — the #789 layer-map
guard, the #509 sign-off map gate, the physical-top argv, the same-net-heal
engine choice, the re-emit provenance chain — stopped reaching its subject and
went red on main, and each such suite's NEGATIVE arms ("the map reason is NOT
in the detail") went green vacuously, because the admission refusal carries no
map reason either.

These suites own the stream-out, not the gate (T47's runner tests own the
gate). So each admits its fixture's layout the way a passing gate would record
it, and nothing more:

  * REAL: `_layout_basis` over the fixture's own routed DEF, netlist and SDC
    bytes; the record file the runner reads; and the `_ga.gate_passed`
    comparison inside `step_gds` — a record for a different digest is still
    refused (see `test_prestream_admission_fixture_binds_the_digest`).
  * SUPPLIED: the PDK hasher. A fixture PDK's paths (`/pdk/...`, `/t.tlef`)
    exist on no host and in no container, so the real hasher can only refuse.
    Each path's identity is the sha256 of the path string: stable, distinct per
    path, and still sensitive to every PdkConfig field, which `pdk_digest`
    hashes itself.

The same shape as `test_phase3_postpnr_disclosure_and_gds_guard.py`'s fixture
(a PASS record bound to the real basis digest), which #2635 wrote for its own
suites.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _fixture_pdk_hasher(_container):
    def _hash(paths):
        return {str(p): hashlib.sha256(f"fixture-pdk:{p}".encode()).hexdigest()
                for p in paths}
    return _hash


def admit_fixture_layout(monkeypatch, runner, project: Path, top: str, pdk,
                         container: str) -> str:
    """Write the missing basis inputs, then a PASS gate record for their digest.

    The routed DEF defaults to the fixture's own `<top>.def` bytes (the direct
    path streams `<top>.def`, and PnR writes the two identical); an existing
    `routed.def`, netlist or SDC is never overwritten."""
    project = Path(project)
    pnr = runner._pl.pnr_dir(project)
    routed = pnr / "routed.def"
    if not routed.is_file():
        routed.write_bytes((pnr / f"{top}.def").read_bytes())
    netlist = runner.pnr_input_netlist(project, top)[0]
    if not netlist.is_file():
        netlist.parent.mkdir(parents=True, exist_ok=True)
        netlist.write_text(f"module {top}();\nendmodule\n")
    sdc = pnr / "constraint.sdc"
    if not sdc.is_file():
        sdc.write_text("create_clock -name clk -period 10 [get_ports clk]\n")
    monkeypatch.setattr(runner, "_step_pdk_hasher", _fixture_pdk_hasher)
    digest, refusal = runner._layout_basis(project, top, pdk, container)
    assert digest and not refusal, refusal
    gate = runner._pl.reports_phase3_dir(project) / "prestream_gate.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps({
        "verdict": "PASS", "layout_digest": digest,
        "source": "unit fixture: routed basis admitted "
                  "(_prestream_admission_fixture)"}) + "\n")
    return digest
