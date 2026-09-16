#!/usr/bin/env python3
"""A setup shortfall was reported without saying whose die it was measured on.

vibe-ic#2160: subservient's SS sign-off stands at setup −0.280 ns / TNS −0.88
against a period the design DECLARES (20 ns, `clock_target_provenance.json`
`assumed=false, tier=declared_pdk_table`), so #2091's assumed-clock disclosure
correctly does not apply and the shortfall is real. All three violating paths are
register-to-OUTPUT-PORT, where clock insertion delay does not cancel, and the
insertion delay is 6.53 ns — 31 % of the declared period.

THE THING NOBODY SAID. `phase3_one_shot_runner._pdn_em_width_floor` records, in
its own docstring, why that die is the size it is: an EM strap width taken from
the I_total CONSERVATION BOUND demanded Metal4 20.77 µm where the per-segment
MEASUREMENT needed 5.62 µm (3.70×); seating those straps grew the die
227×227 → 416×416 µm; utilisation fell to 17 % against an L9-DECLARED 50 %; and
that "inflated CTS insertion delay to 6.47 ns, itself 40 % of the
register-to-output-port setup budget". Its own summary: **"Two sign-off failures,
one over-sized number"** — #2160's setup wall and #2148's density wall are ONE
root cause.

The owner ruling of 2026-09-02 already made the measurement supersede the bound.
But no measurement exists on a FIRST pass, so `_pdn_em_width_floor` returns None,
the bound is used, and the run is placed on the inflated die — and no artefact of
that sign-off said so. A reader saw a shortfall against a declared period and
could not tell "this design cannot meet 20 ns" from "this die was inflated by a
bound the flow can already beat".

REPRODUCED on 2026-09-10 in the pinned image against live main, on the issue's own
shape (reg → output port, SS, slack −0.280, launch insertion 6.53, no capture
insertion): the gate returns PASS with "no post-route sign-off path is PROVEN
architectural (1 violating path(s) read)" and says nothing about the die. Correct
as far as it goes — the residual bound genuinely does not settle it — and silent
about the one thing that would tell a reader what the number means.

WHAT THIS PINS. The disclosure is attached to the reason, and NOTHING else moves:
not the verdict, not the route, not the exit code. Which remedy a shortfall needs
is still decided by the residual arithmetic alone. A run whose die WAS sized from
a measurement reads exactly as before, and so does a clean design.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PROGRAMS = HERE.parent


def _mod():
    spec = importlib.util.spec_from_file_location(
        "sta_arch_resid_2160", PROGRAMS / "sta_architectural_residual_check.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


MOD = _mod()

BOUND_BASIS = "i_total_conservation_bound"
MEASURED_BASIS = "measured_max_segment"

#: What an unfixed tree says for the omitted disclosure: the reason ends at the
#: one-sided caveat and never names the die. Named so the assertions read as
#: "not this any more" and fail on the comparison.
UNFIXED_TAIL = "does not certify that any remaining violation is physical."


def _reg_to_port_report(slack="-0.280") -> str:
    """subservient's shape: register -> OUTPUT PORT, SS, big launch insertion and
    no capture insertion, so the residual bound cannot settle it."""
    arcs = "\n".join(
        f"   0.04    0.20    0.31   {8 + i * 0.31:5.2f} v _09{i:02d}_/X "
        f"(cell__nand2_2)" for i in range(9))
    return f"""=== SETUP corner: process=SS liberty=lib_ss ===
STA_BASIS: POST_ROUTE_SPEF
Startpoint: _1832_ (rising edge-triggered flip-flop clocked by i_clk)
Endpoint: o_sdram_dq[3] (output port clocked by i_clk)
Path Group: i_clk
Path Type: max

    Cap    Slew   Delay    Time   Description
                   6.53    6.53   clock network delay (propagated)
   0.05    0.41    0.62    7.15 ^ _1832_/Q (cell__dfxtp_1)
{arcs}
   0.02    0.18    0.24   11.20 v obuf1/X (cell__buf_2)
                  20.00   20.00   clock i_clk (rise edge)
                   0.00   20.00   clock network delay (propagated)
                          {slack}   slack (VIOLATED)
"""


def _architectural_report() -> str:
    """A path the residual bound DOES settle, so the FAIL branch is exercised
    too: 37 logic arcs, one small buffer, no adverse skew."""
    arcs = "\n".join(
        f"   0.04    0.20    0.68   {10 + i:5.2f} v _1{i:04d}_/X "
        f"(cell__maj3_4)" for i in range(37))
    return f"""=== SETUP corner: process=SS liberty=lib_ss ===
STA_BASIS: POST_ROUTE_SPEF
Startpoint: _17630_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: _17661_ (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

    Cap    Slew   Delay    Time   Description
                   6.33    6.33   clock network delay (propagated)
   0.05    0.38    1.06    7.39 ^ _17630_/Q (cell__dfxtp_2)
{arcs}
   0.01    0.15    0.55   99.00 v rebuffer1/X (cell__buf_1)
                  25.90   25.90   clock clk (rise edge)
                   6.33   31.20   clock network delay (propagated)
                          -2.46   slack (VIOLATED)
"""


def _project(tmp_path: Path, report: str, basis=None, extra=None) -> Path:
    d = tmp_path / "phase3" / "stage3" / "sta"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sta_mcorner_ocv.rpt").write_text(report, encoding="utf-8")
    if basis is not None or extra is not None:
        r = tmp_path / "reports" / "phase3"
        r.mkdir(parents=True, exist_ok=True)
        doc = {"program": "phase3_one_shot_runner._pdn_em_width_floor"}
        if basis is not None:
            doc["sizing_basis"] = basis
        if extra:
            doc.update(extra)
        (r / "pdn_em_sizing.json").write_text(json.dumps(doc, indent=2) + "\n",
                                              encoding="utf-8")
    return tmp_path


# --- the case the issue exists for ------------------------------------------

def test_a_shortfall_on_a_bound_sized_die_says_so(tmp_path):
    """THE REGRESSION, on subservient's own shape and numbers."""
    rep = MOD.check(_project(tmp_path, _reg_to_port_report(), BOUND_BASIS))
    assert not rep["reason"].endswith(UNFIXED_TAIL), (
        "the shortfall still does not name the die it was measured on",
        rep["reason"])
    assert "UNMEASURED EM BOUND" in rep["reason"], rep["reason"]
    assert BOUND_BASIS in rep["reason"], rep["reason"]
    assert "pdn_em_sizing.json" in rep["reason"], rep["reason"]


def test_the_disclosure_names_the_ruling_that_supersedes_the_bound(tmp_path):
    """A caveat a reader cannot act on is decoration. It has to say what changes
    the answer — re-running so the measurement supersedes the bound."""
    rep = MOD.check(_project(tmp_path, _reg_to_port_report(), BOUND_BASIS))
    assert "2026-09-02" in rep["reason"], rep["reason"]
    assert "supersede" in rep["reason"], rep["reason"]


def test_the_structured_record_carries_the_basis(tmp_path):
    """Prose is for the reader; a consumer needs the field."""
    rep = MOD.check(_project(
        tmp_path, _reg_to_port_report(), BOUND_BASIS,
        extra={"i_total_A": 0.0412, "bound_over_measured_x": None}))
    basis = rep["die_sizing_basis"]
    assert basis["sizing_basis"] == BOUND_BASIS, basis
    assert basis["from_unmeasured_bound"] is True, basis
    assert basis["record"].endswith("pdn_em_sizing.json"), basis


def test_the_architectural_fail_branch_carries_it_too(tmp_path):
    """The FAIL path is where a re-authoring request is issued. Sending one
    against a die that was never the design's is the worse version of this
    defect, so the caveat has to reach that reason as well."""
    rep = MOD.check(_project(tmp_path, _architectural_report(), BOUND_BASIS))
    assert rep["verdict"] == "FAIL", rep
    assert "UNMEASURED EM BOUND" in rep["reason"], rep["reason"]


# --- the decision must not move --------------------------------------------

@pytest.mark.parametrize("report,expected", [
    ("reg_to_port", "PASS"),
    ("architectural", "FAIL"),
])
def test_the_verdict_route_and_rc_are_untouched(tmp_path, report, expected):
    """Disclosure, not a new refusal. Which remedy a shortfall needs is still
    decided by the residual arithmetic alone — a caveat that changed a verdict
    would be relabelling a failure, which is forbidden."""
    body = (_reg_to_port_report() if report == "reg_to_port"
            else _architectural_report())
    with_basis = MOD.check(_project(tmp_path / "a", body, BOUND_BASIS))
    without = MOD.check(_project(tmp_path / "b", body))
    assert with_basis["verdict"] == without["verdict"] == expected
    assert with_basis["route_step"] == without["route_step"]
    assert [p["category"] for p in with_basis["paths"]] == \
           [p["category"] for p in without["paths"]]


def test_reasons_zero_still_equals_reason(tmp_path):
    """#2081's own first defect was a reader that knew only `reasons`. That
    contract is pinned elsewhere and must survive this change."""
    for body in (_reg_to_port_report(), _architectural_report()):
        rep = MOD.check(_project(tmp_path / str(hash(body)), body, BOUND_BASIS))
        assert rep["reasons"][0] == rep["reason"]


# --- the other tail: when nothing should be said ---------------------------

def test_a_measured_die_gets_no_caveat(tmp_path):
    """THE CONTROL. A die sized from the measurement is the preferred basis and
    needs no qualification; a gate that caveated every run would stop meaning
    anything."""
    rep = MOD.check(_project(tmp_path, _reg_to_port_report(), MEASURED_BASIS))
    assert "UNMEASURED EM BOUND" not in rep["reason"], rep["reason"]
    assert rep["die_sizing_basis"]["from_unmeasured_bound"] is False


def test_an_absent_sizing_record_gets_no_caveat(tmp_path):
    """Most runs in the corpus predate the field. Absence is not evidence the
    die was bound-sized, so it must add nothing."""
    rep = MOD.check(_project(tmp_path, _reg_to_port_report()))
    assert "UNMEASURED EM BOUND" not in rep["reason"], rep["reason"]
    assert rep["die_sizing_basis"] is None


@pytest.mark.parametrize("doc", [
    {"sizing_basis": "some_future_basis"},
    {"sizing_basis": None},
    {},
])
def test_a_basis_this_version_does_not_know_stays_quiet(tmp_path, doc):
    """The safe direction. An unrecognised basis makes this UNDER-disclose, never
    mis-disclose: it must not assert "bound" about a basis it cannot read."""
    d = tmp_path / "reports" / "phase3"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pdn_em_sizing.json").write_text(json.dumps(doc), encoding="utf-8")
    sta = tmp_path / "phase3" / "stage3" / "sta"
    sta.mkdir(parents=True, exist_ok=True)
    (sta / "sta_mcorner_ocv.rpt").write_text(_reg_to_port_report(),
                                             encoding="utf-8")
    rep = MOD.check(tmp_path)
    assert "UNMEASURED EM BOUND" not in rep["reason"], (doc, rep["reason"])
    assert rep["die_sizing_basis"] is None


def test_an_unreadable_sizing_record_stays_quiet(tmp_path):
    d = tmp_path / "reports" / "phase3"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pdn_em_sizing.json").write_text("{ truncated", encoding="utf-8")
    sta = tmp_path / "phase3" / "stage3" / "sta"
    sta.mkdir(parents=True, exist_ok=True)
    (sta / "sta_mcorner_ocv.rpt").write_text(_reg_to_port_report(),
                                             encoding="utf-8")
    rep = MOD.check(tmp_path)
    assert rep["die_sizing_basis"] is None
    assert "UNMEASURED EM BOUND" not in rep["reason"]


def test_a_clean_design_is_not_caveated(tmp_path):
    """A design with NO violating path has no shortfall to qualify. Attaching a
    die caveat to a clean sign-off would be noise on exactly the runs that are
    fine."""
    met = _reg_to_port_report().replace("-0.280   slack (VIOLATED)",
                                        " 1.200   slack (MET)")
    rep = MOD.check(_project(tmp_path, met, BOUND_BASIS))
    assert "UNMEASURED EM BOUND" not in rep["reason"], rep["reason"]
    assert rep["die_sizing_basis"] is None


def test_a_run_with_no_signoff_report_is_still_not_checked(tmp_path):
    """The no-input refusal is upstream of all of this and must not be softened
    by a record that happens to exist."""
    d = tmp_path / "reports" / "phase3"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pdn_em_sizing.json").write_text(
        json.dumps({"sizing_basis": BOUND_BASIS}), encoding="utf-8")
    rep = MOD.check(tmp_path)
    assert rep["verdict"] == "NOT_MEASURED", rep
    assert "has no input" in rep["reason"], rep["reason"]


# --- the two walls are one cause ------------------------------------------

def test_the_gate_records_why_this_is_one_cause_with_the_density_wall():
    """#2160 says the setup wall and the die-density wall (#2148) are two
    separate attributions. The runner that sizes the straps says otherwise in its
    own docstring — "Two sign-off failures, one over-sized number" — and a future
    reader of THIS gate needs that pointer, because the caveat it prints is only
    intelligible with it."""
    src = (PROGRAMS / "sta_architectural_residual_check.py").read_text()
    assert "one over-sized number" in src, \
        "the shared-cause attribution is not recorded where the caveat is emitted"
    assert "2148" in src and "2160" in src, "neither issue is named"
