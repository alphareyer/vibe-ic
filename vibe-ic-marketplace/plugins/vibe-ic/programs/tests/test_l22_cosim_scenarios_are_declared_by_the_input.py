#!/usr/bin/env python3
"""A9's co-simulation scenario set is declared in L22, from the input alone.

WHY. `mixed_signal_cosim_check` graded the scenarios listed in the A9
producer's OWN report, so the producer decided how many scenarios there were
and the gate counted them: a producer that lists one easy scenario certifies
itself. Nothing upstream fixed the denominator.

THE RULE NOW. `l22_analog_verification_plan_emit` (Phase 1, before A9 exists)
writes `verification_plan.cosim_scenarios[]` beside `analog[]`:

  1. an explicit input section headed co-simulation wins, taken literally;
  2. otherwise rows are DERIVED only from declarations, each citing its line —
     R1 an intent clause naming an L5 quantity of a digital-output block,
     R2 a declared cross-block relation, R3 a digital pin with a declared
     range, R4 declared per-window reset semantics;
  3. standard categories nothing supports go to `cosim_scenario_gaps[]`.

No number is invented: every bound is an L5 record's own value. Zero rows is
NOT a producer refusal — the analog plan is still written, and
`cosim_status` says NO_DERIVABLE_SCENARIOS.

THE FIXTURE is a verbatim copy of a real mixed-signal input (an incremental
delta-sigma ADC array with one LDO-fed channel): its three input documents,
its interface netlist, and the Phase-1 L1/L5/L7/L22 records Phase 1 produced
from them. Its input never uses the word "scenario"; the rows below are what
the four rules derive, and the gaps are what they cannot.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import l22_analog_verification_plan_emit as EMIT  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "a9_cosim_scenarios"
L5_DOC = "input/docs/L5_ANALOG_SPEC.md"
SP = "input/interfaces/analog_blocks.sp"


def _project(tmp_path: Path) -> Path:
    dest = tmp_path / "proj"
    shutil.copytree(FIXTURE, dest)
    return dest


def _plan(project: Path) -> dict:
    l22 = json.loads((project / "phase1/generated_docs/"
                      "L22_VERIFICATION_PLAN.json").read_text())
    return l22["fields"]["verification_plan"]


def _emit(project: Path) -> dict:
    report = EMIT.run(project)
    assert report["status"] == "OK", report
    return _plan(project)


def _by_id(plan: dict) -> dict:
    return {row["id"]: row for row in plan["cosim_scenarios"]}


# ── the derived set ──────────────────────────────────────────────────────────
def test_the_input_derives_exactly_S1_to_S5(tmp_path):
    plan = _emit(_project(tmp_path))
    assert plan["cosim_status"] == "DERIVED"
    assert [r["id"] for r in plan["cosim_scenarios"]] == [
        "S1", "S2", "S3", "S4", "S5"]
    assert [r["extraction_strategy"] for r in plan["cosim_scenarios"]] == [
        "derived_R1", "derived_R1", "derived_R2", "derived_R3", "derived_R4"]


def test_every_row_cites_the_input_lines_it_was_derived_from(tmp_path):
    rows = _by_id(_emit(_project(tmp_path)))
    # S1: the intent clause (line 54) and the ENOB row (line 31).
    assert rows["S1"]["evidence"] == [f"{L5_DOC}:54", f"{L5_DOC}:31"]
    # S2: the same clause, the input range (32) and the reference (33).
    assert rows["S2"]["evidence"] == [
        f"{L5_DOC}:54", f"{L5_DOC}:32", f"{L5_DOC}:33"]
    # S3: the consumer's supply row (34) and the regulator's output (43).
    assert rows["S3"]["evidence"] == [f"{L5_DOC}:34", f"{L5_DOC}:43"]
    # S4: the clock row (35). S5: the reset semantics (28) and OSR (30).
    assert rows["S4"]["evidence"] == [f"{L5_DOC}:35"]
    assert rows["S5"]["evidence"] == [f"{L5_DOC}:28", f"{L5_DOC}:30"]
    # Every cited line exists and is non-blank in the input it names.
    for row in rows.values():
        for cite in row["evidence"]:
            rel, line = cite.rsplit(":", 1)
            text = (FIXTURE / rel).read_text().splitlines()[int(line) - 1]
            assert text.strip(), cite


def test_the_criteria_are_the_L5_bounds(tmp_path):
    rows = _by_id(_emit(_project(tmp_path)))
    s1 = rows["S1"]
    assert s1["grading"] == "BOUNDED"
    assert [(c["block"], c["quantity"], c["min"]) for c in s1["criteria"]] == [
        ("delta_sigma", "ENOB", 14.0)]
    assert s1["observe"] == [{"block": "delta_sigma", "pin": "bit_out"}]

    s2 = rows["S2"]
    assert s2["grading"] == "STRUCTURAL"
    assert [c["structural"] for c in s2["criteria"]] == [
        "polarity", "monotonic", "in_range"]
    # Swept against the reference, not over the raw 0-1.2 V range: full
    # scale IS the reference, and 1.2 V against a 1.0 V reference overloads
    # by construction.
    assert s2["stimulus"]["normalised_to"] == "Vref"
    assert s2["stimulus"]["reference_values"] == [1.0]
    assert s2["stimulus"]["u_grid"][0] == 0.0
    assert s2["stimulus"]["u_grid"][-1] == 1.0
    assert s2["stimulus"]["pin"] == "vin"

    s3 = rows["S3"]
    assert s3["grading"] == "BOUNDED"
    assert s3["blocks"] == ["ldo", "delta_sigma"]
    assert [(c["block"], c["pin"], c["min"], c["max"])
            for c in s3["criteria"]] == [
        ("ldo", "vout", 1.1, 1.3), ("delta_sigma", "vdd", 1.1, 1.3)]

    s4 = rows["S4"]
    assert s4["stimulus"]["pin"] == "clk"
    assert s4["stimulus"]["points"] == [0.1, 1.0, 1.2823]
    assert s4["stimulus"]["unit"] == "MHz"

    s5 = rows["S5"]
    assert [c["structural"] for c in s5["criteria"]] == ["window_independent"]
    assert s5["stimulus"]["window_length"] == {"quantity": "OSR",
                                               "value": 256.0}


def test_no_criterion_number_is_invented(tmp_path):
    """Every number a criterion or stimulus carries is an L5 record value."""
    project = _project(tmp_path)
    plan = _emit(project)
    l5 = json.loads((project / "phase1/generated_docs/L5_ADI_SPEC.json")
                    .read_text())
    declared = set()
    for block in l5["analog_blocks"]:
        for spec in block["spec"]["specs"]:
            for key in ("min", "max", "target"):
                if isinstance(spec.get(key), (int, float)):
                    declared.add(float(spec[key]))
    for row in plan["cosim_scenarios"]:
        for crit in row["criteria"]:
            for key in ("min", "max", "target"):
                if key in crit:
                    assert float(crit[key]) in declared, (row["id"], crit)
        stim = row.get("stimulus") or {}
        for value in stim.get("points", []) + stim.get("reference_values", []):
            assert float(value) in declared, (row["id"], stim)


def test_the_gaps_are_disclosed_not_run(tmp_path):
    plan = _emit(_project(tmp_path))
    gaps = plan["cosim_scenario_gaps"]
    categories = [g["category"] for g in gaps
                  if g["category"] != "input_ambiguity"]
    assert categories == ["power_up_sequencing", "reset_pin", "decimator"]
    by = {g["category"]: g for g in gaps}
    assert by["reset_pin"]["evidence"] == [f"{SP}:42"]
    assert by["decimator"]["evidence"] == [f"{L5_DOC}:22"]
    assert by["power_up_sequencing"]["evidence"] == []
    ambiguities = {g["reason"]: g for g in gaps
                   if g["category"] == "input_ambiguity"}
    assert ambiguities["range rows not paired"]["evidence"] == [
        f"{L5_DOC}:32", f"{L5_DOC}:33"]
    assert ambiguities["a declared range is quoted differently"][
        "evidence"] == [f"{SP}:37", f"{L5_DOC}:35"]
    ids = {r["id"] for r in plan["cosim_scenarios"]}
    assert not any(g.get("id") in ids for g in gaps)


# ── the association defect the derivation depends on ────────────────────────
def test_a_semicolon_joined_bullet_is_associated_clause_by_clause(tmp_path):
    """MEASURED: the whole bullet went to the regulator, the modulator got [].

    "(modulator)" matches no identity token of the modulator block, and the
    regulator's identity token won the whole bullet before any specification
    matching ran. Split at ';' first.
    """
    plan = _emit(_project(tmp_path))
    intent = {row["block"]: [i["method"] for i in row["verification_intent"]]
              for row in plan["analog"]}
    assert intent["delta_sigma"] == [
        "SNDR/ENOB transient + input sweep (modulator)."]
    assert intent["ldo"] == ["DC operating point + line/load regulation (LDO)"]
    for row in plan["analog"]:
        for clause in row["verification_intent"]:
            assert clause["phase"] == (
                "dc_operating_point_line_load_regulation_ldo_sndr")
            assert clause["clause_of"].startswith("DC operating point")


def test_a_bullet_without_a_semicolon_is_byte_identical():
    row = {"phase": "p", "method": "Run a DC operating point.",
           "evidence": "input/docs/x.md", "extraction_strategy": "s"}
    assert EMIT._clauses([row]) == [row]


# ── precedence, polarity, and the zero-row outcome ───────────────────────────
_SECTION = """
## Co-simulation scenarios
- Power up the regulator, then run the modulator for two windows.
- Check the delta_sigma ENOB through its serial output.
"""


def test_an_explicit_input_section_wins_and_is_taken_literally(tmp_path):
    project = _project(tmp_path)
    doc = project / L5_DOC
    base = len(doc.read_text().splitlines())
    doc.write_text(doc.read_text() + _SECTION)
    plan = _emit(project)
    assert plan["cosim_status"] == "DECLARED"
    rows = plan["cosim_scenarios"]
    assert [r["description"] for r in rows] == [
        "Power up the regulator, then run the modulator for two windows.",
        "Check the delta_sigma ENOB through its serial output."]
    assert [r["evidence"] for r in rows] == [
        [f"{L5_DOC}:{base + 3}"], [f"{L5_DOC}:{base + 4}"]]
    assert all(r["extraction_strategy"] == "cosim_scenario_section_v1"
               for r in rows)
    # The first names no quantity, so it has no criterion: kept, run,
    # reported, and it can never PASS.
    assert rows[0]["grading"] == "UNBOUNDED_IN_INPUT"
    assert rows[0]["criteria"] == []
    assert rows[1]["grading"] == "BOUNDED"
    assert [(c["quantity"], c["min"]) for c in rows[1]["criteria"]] == [
        ("ENOB", 14.0)]
    assert plan["cosim_scenario_gaps"] == []


def test_a_denied_heading_is_not_a_declaration(tmp_path):
    project = _project(tmp_path)
    doc = project / L5_DOC
    doc.write_text(doc.read_text()
                   + "\n## No co-simulation scenarios\n- anything\n")
    plan = _emit(project)
    assert plan["cosim_status"] == "DERIVED"
    assert len(plan["cosim_scenarios"]) == 5


def test_zero_rows_keeps_the_plan_and_says_why(tmp_path):
    """Nothing derivable is rc 0 with the analog plan written, not REFUSED.

    REFUSED is rc 1 in this producer's `_STATUS_EXIT` and returns before any
    plan is written, so using it here would fail Phase 1 for every analog IC
    whose input declares no co-simulation, and drop its analog[] with it.
    """
    project = _project(tmp_path)
    for rel in ("input/docs/L5_ANALOG_SPEC.md", "input/docs/L1_DATASHEET.md",
                "input/docs/L9_CONSTRAINTS.md", SP):
        (project / rel).write_text("")
    l5_path = project / "phase1/generated_docs/L5_ADI_SPEC.json"
    l5 = json.loads(l5_path.read_text())
    l5["analog_blocks"] = [b for b in l5["analog_blocks"]
                           if b["name"] == "ldo"]
    l5_path.write_text(json.dumps(l5))
    l7_path = project / "phase1/generated_docs/L7_TEST_DEBUG.json"
    l7 = json.loads(l7_path.read_text())
    l7["verification_strategy"] = []
    l7_path.write_text(json.dumps(l7))

    rc = EMIT.main([str(project)])
    assert rc == 0
    plan = _plan(project)
    assert [r["block"] for r in plan["analog"]] == ["ldo"]
    assert plan["cosim_scenarios"] == []
    assert plan["cosim_status"] == "NO_DERIVABLE_SCENARIOS"
    assert {g["category"] for g in plan["cosim_scenario_gaps"]} >= {
        "power_up_sequencing", "connectivity_polarity", "sinad_sine_fit"}


def test_the_cosim_keys_are_emitter_owned():
    assert {"cosim_scenarios", "cosim_scenario_gaps", "cosim_status"} <= \
        EMIT._EMITTER_OWNED_PLAN_KEYS


def test_re_running_is_idempotent(tmp_path):
    project = _project(tmp_path)
    first = _emit(project)
    report = EMIT.run(project)
    assert report["emitted_count"] == 0
    assert _plan(project) == first


def test_no_block_or_pin_literal_in_the_derivation():
    source = (PROGRAMS / "l22_analog_verification_plan_emit.py").read_text()
    for literal in ("delta_sigma", "ldo", "bit_out", "vout", "fclk", "ENOB",
                    "Vref", "OSR"):
        assert f'"{literal}"' not in source, literal
        assert f"'{literal}'" not in source, literal


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
