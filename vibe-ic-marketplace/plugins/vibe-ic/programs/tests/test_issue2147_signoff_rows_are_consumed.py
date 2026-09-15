#!/usr/bin/env python3
"""vibe-ic#2147 — the L7 sign-off rows must reach a VERDICT, and power must go
through the same technology ladder as area.

Measured by lane cz2136 on the subservient run (#2136's follow-up):

  * `area_total_vs_budget_check` read a DIE budget from L19 — null on that run —
    and returned NOT_APPLICABLE, a vacuous pass, over a design whose own L7
    table states an absolute standard-cell ceiling;
  * `power_total_vs_budget_check` read `L19.power_budget_uw` — also null — and
    returned INCOMPLETE, over a design whose same table states a total-power
    ceiling;
  * so BOTH sign-off rows the design wrote down were enforced by nobody, and
    the power row still carried #2136's cross-technology defect unfixed.

WHAT IS ASSERTED, and every one of these is a direction that must hold:

  1. On the baseline's OWN technology both rows reach a real verdict, with the
     tier that produced the threshold NAMED.
  2. On the technology the run was actually directed onto, both gates REFUSE BY
     NAME. Never PASS on a null, and never a cross-technology FAIL.
  3. The MUTATION: drop the consumer call and the rows go silent again — the
     gates return to NOT_APPLICABLE / INCOMPLETE. A consumer that cannot change
     the verdict is not a consumer.
  4. MEMBERSHIP: a run whose L19 declares a budget is decided by L19 exactly as
     before, byte for byte.
  5. The caller's RESOLVED library wins over what the run's artefacts say, which
     is the seam the Phase-3 step uses.

Fixtures are SYNTHETIC. The two open standard-cell libraries named here are the
run's technologies, which is the whole subject; no foundry, node or SKU appears.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import area_signoff_baseline as asb                # noqa: E402
import area_total_vs_budget_check as area_gate     # noqa: E402
import power_total_vs_budget_check as power_gate   # noqa: E402
from _ppa import power as _pw                      # noqa: E402

LIB_A = "sky130_fd_sc_hd"          # the library the L7 baseline was measured on
PDK_A = "sky130A"
LIB_B = "gf180mcu_fd_sc_mcu7t5v0"  # the library the measured run built against
PDK_B = "gf180mcuD"

#: The measured design's own numbers.
CELL_AREA_MEASURED = 57024.7104
CELL_CEILING_A = 16608.0
POWER_CEILING_A_UW = 2940.0

L7_DOC = f"""# L7 - Verification Plan

## 7.4 Quality Metrics

### 7.4.1 Baseline (top=`core`, memsize=1024, {LIB_A}, TT corner)

| metric | Baseline |
|---|---|
| stdcell count | **1,502** |
| stdcell area | **12,775 um^2** |
| die area | **34,269 um^2** |
| total power (TT) | **2.26 mW** |

### 7.4.2 Sign-off Acceptance Range

> The design should at least match baseline (area / power within baseline x 1.3).

| metric | accepted range | sign-off gate |
|---|---|---|
| stdcell count (informational) | baseline x [0.5, 2.0] = [751, 3004] | no |
| stdcell area | (0, baseline x 1.3] = <= 16,608 um^2 | yes |
| total power (TT) | (0, baseline x 1.3] = <= 2.94 mW | yes |
"""

#: A power report whose Total is 2.50e-03 W = 2500 uW — inside the 2940 uW row.
RPT_UNDER = """\
power analyser 2.7.0 — vectorless
POWER_ANALYSIS_MODE: vectorless_sdc
Group                  Internal  Switching    Leakage      Total
                          Power      Power      Power      Power (Watts)
----------------------------------------------------------------
Sequential             2.20e-03   8.19e-06   5.28e-10   2.21e-03  88.4%
Combinational          2.80e-04   1.13e-05   3.17e-10   2.91e-04  11.6%
----------------------------------------------------------------
Total                  2.48e-03   1.95e-05   8.45e-10   2.50e-03 100.0%
"""
#: The same report at 3.50e-03 W = 3500 uW — outside it.
RPT_OVER = RPT_UNDER.replace("2.50e-03 100.0%", "3.50e-03 100.0%")

_NOT_APPLICABLE_RATIONALE = (
    "The input states no die or core rectangle to fit inside; the die size is "
    "an outcome of the run, not a ceiling the design set.")


def _project(tmp_path: Path, *, built_on: str, l7: bool = True,
             rpt: str = RPT_UNDER, die_budget=None, power_budget=None,
             area_declaration="not_applicable", name="run") -> Path:
    """The measured run's shape, with the two knobs this issue turns."""
    p = tmp_path / name
    (p / "input" / "docs").mkdir(parents=True)
    if l7:
        (p / "input" / "docs" / "L7_verification_plan.md").write_text(L7_DOC)

    gd = p / "phase1" / "generated_docs"
    gd.mkdir(parents=True)
    (gd / "L19_CONSTRAINTS_PDK.json").write_text(json.dumps({
        "doc_id": "L19",
        "fields": {"pdk_target": "sky130",
                   "pdk_target_alternates": ["sky130", "gf180mcu"],
                   "die_area_budget_um": die_budget,
                   "power_budget_uw": power_budget}}, indent=2))

    # The run's OWN synthesis artefact — the only document that says what this
    # run actually built on, down to the corner file.
    st = p / "steps" / "phase2" / "stage2" / "9_synth"
    st.mkdir(parents=True)
    (st / "stats.json").write_text(json.dumps({
        "chip_area": CELL_AREA_MEASURED, "chip_area_unit": "um^2",
        "cell_count": 2180, "top_module": "core",
        "includes_submodules": False,
        "chip_area_unit_evidence": {
            "established": True, "unit": "um^2",
            "liberty": f"/pdks/{built_on}_pdk/libs.ref/{built_on}"
                       f"/lib/{built_on}__tt_025C.lib"}}, indent=2))

    rp = p / "reports" / "phase3"
    rp.mkdir(parents=True)
    (rp / "power.rpt").write_text(rpt)

    decl = p / "input" / "submission_template"
    decl.mkdir(parents=True)
    if area_declaration == "not_applicable":
        body = {"synthesis_area_budget": {"status": "NOT_APPLICABLE",
                                          "rationale": _NOT_APPLICABLE_RATIONALE}}
    elif area_declaration == "limit":
        body = {"synthesis_area_budget": {"status": "LIMIT",
                                          "max_die_dimensions_um": [300.0, 300.0]}}
    else:
        body = {}
    (decl / "tapeout_declaration.json").write_text(json.dumps(body, indent=2))
    return p


# ── 1. the baseline's own technology: BOTH rows reach a verdict ────────────
def test_area_reaches_a_verdict_against_the_declared_cell_ceiling(tmp_path):
    proj = _project(tmp_path, built_on=LIB_A)
    v, rep = area_gate.evaluate(proj, None, False)
    assert v == "FAIL", "57,024.71 um^2 is 3.4x the design's own 16,608 um^2 row"
    assert rep["comparison"]["basis"] == area_gate.BASIS_CELL_SIGNOFF
    assert rep["comparison"]["cell_ceiling_um2"] == CELL_CEILING_A
    # THE TIER IS NAMED. A threshold whose provenance is not published is the
    # defect #2136 closed, one consumer down.
    assert rep["comparison"]["ceiling_tier"] == asb.DECLARED_BASELINE_TIER
    assert rep["comparison"]["ceiling_technology"] == LIB_A
    assert rep["comparison"]["baseline_um2"] == 12775.0
    assert rep["comparison"]["ratio"] == 1.3
    assert rep["findings"][0]["rule"] == "CELL_AREA_OVER_DECLARED_SIGNOFF"


def test_area_passes_when_the_design_fits_its_own_cell_ceiling(tmp_path):
    proj = _project(tmp_path, built_on=LIB_A)
    st = next(proj.glob("steps/**/stats.json"))
    doc = json.loads(st.read_text())
    doc["chip_area"] = 9000.0
    st.write_text(json.dumps(doc))
    v, rep = area_gate.evaluate(proj, None, False)
    assert v == "PASS"
    assert rep["comparison"]["basis"] == area_gate.BASIS_CELL_SIGNOFF
    assert rep["comparison"]["ceiling_tier"] == asb.DECLARED_BASELINE_TIER


def test_power_reaches_a_verdict_against_the_declared_power_ceiling(tmp_path):
    proj = _project(tmp_path, built_on=LIB_A)
    v, rep = power_gate.evaluate(proj, None, library=LIB_A)
    assert v == "PASS"
    assert rep["comparison"]["power_budget_uw"] == POWER_CEILING_A_UW
    assert rep["comparison"]["authority"] == _pw.AUTHORITY_L7_SIGNOFF
    assert rep["requirement"]["tier"] == asb.DECLARED_BASELINE_TIER


def test_power_fails_when_the_design_exceeds_its_own_power_ceiling(tmp_path):
    proj = _project(tmp_path, built_on=LIB_A, rpt=RPT_OVER)
    v, rep = power_gate.evaluate(proj, None, library=LIB_A)
    assert v == "FAIL"
    assert rep["findings"][0]["rule"] == "POWER_TOTAL_OVER_BUDGET"
    assert rep["comparison"]["power_budget_uw"] == POWER_CEILING_A_UW


# ── 2. the technology the run was directed onto: refuse BY NAME ────────────
def test_the_measured_run_refuses_by_name_and_never_passes_on_a_null(tmp_path):
    """This is the run #2136 measured: built on a technology whose baseline the
    document never states. Both gates must say so and neither may go green."""
    proj = _project(tmp_path, built_on=LIB_B)

    v, rep = area_gate.evaluate(proj, None, False)
    assert v not in ("PASS", "FAIL")
    assert v == "NOT_APPLICABLE"          # the DIE is disposed by the design
    assert rep["disposition"]["cell_signoff"]["determined"] is False
    assert rep["disposition"]["cell_signoff"]["reason"] == \
        "baseline_not_attributed_to_this_technology"
    assert rep["cell_area_signoff"]["signoff_row_found"] is True

    pv, prep = power_gate.evaluate(proj, None)
    # OWNER RULING R-0915-22 (2026-09-15) CHANGED THIS ONE LINE, and only this
    # one. It used to read `assert pv == "INCOMPLETE"`.
    #
    # What the ruling did NOT change, and what every other assertion here still
    # pins: an unattributed baseline is NOT a budget for this run, the number
    # is never borrowed, and the row is disclosed BY NAME. Those are the
    # findings #2147 landed and they are untouched.
    #
    # What it changed is the TIER of that refusal. INCOMPLETE says an input
    # this gate needs was never produced and somebody still owes it. Nobody
    # does: this design's input states no power budget applicable to the
    # technology the run built against, and never promised one. The area gate
    # has answered NOT_APPLICABLE for exactly this kind of fact -- a ceiling
    # the design declines to state -- since #2147, three lines above. The two
    # gates now agree, and the refusal still never becomes a PASS or a FAIL.
    assert pv == "NOT_APPLICABLE"
    assert prep["reason_class"] == "DESIGN_DECLARED_NA"
    # UNCHANGED: no budget was adopted ...
    assert prep["power_budget_uw"] is None
    assert prep["requirement"] is None
    # ... the row was NOT_DETERMINED for this run, for the named reason ...
    assert prep["disposition"]["signoff_row_tier"] == "not_determined"
    assert prep["disposition"]["signoff_row_reason"] == \
        "baseline_not_attributed_to_this_technology"
    # ... and the disclosure names the attribution the document DOES carry,
    # so the unusable row is reported rather than quietly dropped.
    assert any(LIB_A in str(a)
               for a in prep["disposition"]["attributions_seen"])
    assert "baseline_not_attributed_to_this_technology" in prep["reason"] \
        or "no baseline is attributed to" in prep["reason"]


def test_a_cross_technology_row_is_never_borrowed(tmp_path):
    """The number itself must not appear anywhere in the other run's verdict."""
    proj = _project(tmp_path, built_on=LIB_B)
    _, rep = area_gate.evaluate(proj, None, False)
    assert rep["cell_area_signoff"]["threshold_um2"] is None
    _, prep = power_gate.evaluate(proj, None)
    assert prep["requirement"] is None


# ── 3. THE MUTATION: drop the consumer and the rows go silent again ────────
def test_mutation_dropping_the_area_consumer_makes_the_row_silent(monkeypatch,
                                                                  tmp_path):
    proj = _project(tmp_path, built_on=LIB_A)
    live, _ = area_gate.evaluate(proj, None, False)
    assert live == "FAIL", "the consumer must be live before it is dropped"

    monkeypatch.setattr(
        area_gate, "resolve_cell_signoff",
        lambda project, library="", pdk="": {"available": False,
                                             "determined": False,
                                             "reason": "consumer_removed",
                                             "note": "consumer removed"})
    dead, rep = area_gate.evaluate(proj, None, False)
    assert dead == "NOT_APPLICABLE", (
        "with the consumer dropped the gate must go back to the vacuous pass "
        "#2147 exists to remove; if it does not, this test is not exercising "
        "the consumer it claims to")
    assert "comparison" not in rep


def test_mutation_dropping_the_power_consumer_makes_the_row_silent(monkeypatch,
                                                                   tmp_path):
    proj = _project(tmp_path, built_on=LIB_A)
    live, _ = power_gate.evaluate(proj, None, library=LIB_A)
    assert live == "PASS", "the consumer must be live before it is dropped"

    monkeypatch.setattr(_pw, "_l7_signoff_requirement",
                        lambda project, library, pdk: (None, None, None))
    dead, rep = power_gate.evaluate(proj, None, library=LIB_A)
    assert dead == "INCOMPLETE", (
        "with the consumer dropped the power row must be unenforced again")
    assert rep["power_budget_uw"] is None


# ── 4. MEMBERSHIP: a declared L19 budget still decides, unchanged ──────────
def test_a_declared_l19_die_budget_decides_exactly_as_before(tmp_path):
    """The die comparison is a DIFFERENT quantity and keeps its own authority.

    Compared against the same run with the L7 document REMOVED: identical
    verdict, identical basis, identical numbers. The new authority may not
    reach a run whose L19 answered.
    """
    with_l7 = _project(tmp_path, built_on=LIB_A, die_budget="300x300",
                       area_declaration="limit", name="with_l7")
    without = _project(tmp_path, built_on=LIB_A, die_budget="300x300",
                       area_declaration="limit", l7=False, name="without_l7")
    v1, r1 = area_gate.evaluate(with_l7, None, False)
    v2, r2 = area_gate.evaluate(without, None, False)
    assert v1 == v2
    assert r1["comparison"]["basis"] == area_gate.BASIS_DECLARED_DIE
    assert r2["comparison"]["basis"] == area_gate.BASIS_DECLARED_DIE
    for key in ("cell_area_um2", "die_area_um2", "utilization", "over"):
        assert r1["comparison"][key] == r2["comparison"][key]


def test_a_declared_l19_power_budget_supersedes_the_l7_row(tmp_path):
    proj = _project(tmp_path, built_on=LIB_A, power_budget=9000.0)
    v, rep = power_gate.evaluate(proj, None, library=LIB_A)
    assert v == "PASS"
    assert rep["comparison"]["power_budget_uw"] == 9000.0
    assert rep["comparison"]["authority"] == _pw.AUTHORITY_L19


# ── 5. the caller's resolved library wins, which is the Phase-3 seam ───────
def test_the_callers_resolved_library_decides(tmp_path):
    """The Phase-3 step knows what it synthesised against; it says so, and that
    is what the gate uses — not a second derivation of the same fact."""
    proj = _project(tmp_path, built_on=LIB_B)
    # The artefact says B, so unaided the gate refuses ...
    assert area_gate.evaluate(proj, None, False)[0] == "NOT_APPLICABLE"
    # ... and a caller that states A gets A's row.
    v, rep = area_gate.evaluate(proj, None, False, library=LIB_A)
    assert v == "FAIL"
    assert rep["comparison"]["ceiling_technology"] == LIB_A


def test_the_phase3_step_hands_the_area_gate_its_resolved_library():
    """The wiring itself, read from the runner rather than assumed."""
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    assert '_area_flags += ["--library", _area_lib]' in src
    assert '_area_flags += ["--pdk", _area_pdk]' in src
    assert "library_name_from_liberty(" in src
    # The FLAGS are hoisted into a local; the gate's PATH is not. See below for
    # why that distinction is a requirement and not a preference.
    assert "[sys.executable, str(_area_prog), str(project)] + _area_flags" in src


def test_hoisting_the_whole_argv_would_break_edge_9s_remeasured_proof():
    """THE FALSIFIER for a regression this change actually caused and fixed.

    `closed_loop_executable_coverage_check` proves edge 9 -> 9's REMEASURED tier
    off THIS call site, and it follows exactly two hops: a local bound to an
    expression whose source names the gate module, and a spawn whose argv names
    that local. The first shape of this change hoisted the whole argv into a
    second local — whose own assignment never spells the gate's name — and the
    edge silently fell back to DECLARED_ONLY, reddening nine tests.

    So the mutant is that hoist, applied to the real runner source, and the real
    proof predicate must go False on it. A comment saying "do not hoist" would
    not have caught it; this does.
    """
    import ast
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_clc_probe", PROGRAMS / "closed_loop_executable_coverage_check.py")
    clc = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = clc
    spec.loader.exec_module(clc)

    runner = PROGRAMS / "phase3_one_shot_runner.py"
    shipped = runner.read_text()
    spawn = "[sys.executable, str(_area_prog), str(project)] + _area_flags"
    assert shipped.count(spawn) == 1
    mutant = shipped.replace(
        spawn, "_area_argv", 1).replace(
        "        _area_flags: List[str] = []",
        "        _area_argv = [sys.executable, str(_area_prog), str(project)]\n"
        "        _area_flags: List[str] = []", 1)
    assert mutant != shipped

    def _proved(source: str) -> bool:
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == "step_synth")
        return clc._gate_spawn_rc_guarded_fallback(
            fn, "area_total_vs_budget_check", "step_synth", 1)

    assert _proved(shipped) is True, (
        "the shipped spawn must keep edge 9 -> 9's actuate proof live")
    assert _proved(mutant) is False, (
        "the hoisted-argv mutant must LOSE the proof; if it does not, this "
        "test is not exercising the citation it claims to")


# ── the ladder is ONE ladder, and the units are converted or refused ───────
def test_power_uses_the_same_tier_names_as_area():
    docs = [("input/docs/L7_verification_plan.md", L7_DOC)]
    a = asb.resolve_texts(docs, [LIB_A], metric=asb.METRIC_CELL_AREA)
    p = asb.resolve_texts(docs, [LIB_A], metric=asb.METRIC_TOTAL_POWER)
    assert a["tier"] == p["tier"] == asb.DECLARED_BASELINE_TIER
    assert a["ratio"] == p["ratio"] == 1.3
    b = asb.resolve_texts(docs, [LIB_B], metric=asb.METRIC_TOTAL_POWER)
    assert b["tier"] == asb.NOT_DETERMINED_TIER
    assert b["disclosure"] == asb.METRIC_TOTAL_POWER.disclosure


def test_the_power_row_is_converted_to_the_compare_unit_not_guessed():
    docs = [("d", L7_DOC)]
    p = asb.resolve_texts(docs, [LIB_A], metric=asb.METRIC_TOTAL_POWER)
    assert p["unit"] == "uW"
    assert p["baseline"] == 2260.0        # 2.26 mW
    assert p["threshold"] == POWER_CEILING_A_UW
    # A figure in a unit this metric does not compare in is READ AND REFUSED,
    # and the refusal is its OWN finding — never collapsed into "nothing was
    # stated", which would turn a declared ceiling into an undeclared one.
    odd = L7_DOC.replace("**2.26 mW**", "**2.26 kW**") \
                .replace("<= 2.94 mW", "<= 2.94 kW")
    q = asb.resolve_texts([("d", odd)], [LIB_A], metric=asb.METRIC_TOTAL_POWER)
    assert q["baseline"] is None
    assert q["reason"] == "signoff_unit_not_comparable"
    assert any("kW" in str(u["stated"]) for u in q["unreadable_units"])
    assert "kW" in q["note"]
    # The AREA metric refuses the same way, so the rule is the metric's and not
    # one metric's special case.
    odd_a = L7_DOC.replace("**12,775 um^2**", "**12.775 mm^2**") \
                  .replace("<= 16,608 um^2", "<= 16.608 mm^2")
    qa = asb.resolve_texts([("d", odd_a)], [LIB_A], metric=asb.METRIC_CELL_AREA)
    assert qa["reason"] == "signoff_unit_not_comparable"


def test_the_run_artefact_outranks_an_l_doc_that_names_two_families(tmp_path):
    """The measured design's own shape: L19 names two open families, the run's
    stats.json names the one library it loaded. The artefact decides."""
    proj = _project(tmp_path, built_on=LIB_B)
    tech = asb.run_technology(proj)
    assert tech["ambiguous"] is False
    assert LIB_B in tech["candidates"]
    assert "stats.json" in str(tech["source"])


# ── never PASS on a null, with nothing declared anywhere ───────────────────
def test_with_nothing_declared_anywhere_neither_gate_passes(tmp_path):
    proj = _project(tmp_path, built_on=LIB_A, l7=False,
                    area_declaration="unset")
    v, rep = area_gate.evaluate(proj, None, False)
    assert v == "INCOMPLETE"
    assert "L7 standard-cell area sign-off row" in rep["missing_authority"]
    pv, prep = power_gate.evaluate(proj, None)
    assert pv == "INCOMPLETE"
    assert "state no total-power sign-off row" in prep["missing_authority"]


def test_cli_exit_codes_carry_the_new_basis(tmp_path):
    proj = _project(tmp_path, built_on=LIB_A)
    cp = subprocess.run(
        [sys.executable, str(PROGRAMS / "area_total_vs_budget_check.py"),
         str(proj), "--library", LIB_A], capture_output=True, text=True)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    assert "CELL_AREA_OVER_DECLARED_SIGNOFF" in cp.stdout
