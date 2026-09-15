"""The program that MAKES the waiver growth declares it, scoped (icspm2).

MEASURED FAILURE THIS PINS
==========================
Every front-door run of a project that reaches an FPGA-capability gap ends with

    waiver_growth_check .                                             rc 1
    [ERROR] UNJUSTIFIED_WAIVER_GROWTH: waivers.json adds 2 root waiver(s) the
    baseline did not carry (> tolerance 0) without `growth_rationale`.
    New waivers: ['39', '6']. No baseline file exists at the path below, so the
    comparison was made against an EMPTY document and every current waiver
    counts as new.

and the two waivers are REAL and correct: `docker exec <eda> command -v
quartus_map quartus_sh quartus` -> rc 1, no binary, so step 6 (`fpga_compile`)
and step 39 (`fpga_onboard_test`) genuinely could not run.

MEASURED over the whole published corpus (benchmark-data @ 71071a1dd400):

    find . -name waivers_baseline.json   ->  0
    find . -name .vibe-ic-state -type d  ->  0
    find . -name waivers.json            ->  7   (none carries growth_rationale)
    flow yaml: `waiver_growth_check .`   — no --baseline, no --tolerance

No baseline has ever existed anywhere and the flow never passes one, so the
gate's growth arm has no reference and the gate is RED on every project that
has any waiver. A permanently red gate carries no information.

WHAT THIS CHANGE BUYS, AND WHAT IT MUST NOT COST
================================================
The declaration is written with the POPULATION spelling — `growth_rationale_
covers` as the LIST of root ids this program materialised — never the COUNT
spelling. Under a count, closing one waiver and opening another leaves the
number unmoved (the #948 swap). Under the population spelling every root must
be named exactly once, so any entry this program did NOT materialise is
`unnamed` and the gate refuses. The tests below are that claim, executable.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))

import waivers_materialize as wm      # noqa: E402
import waiver_growth_check as wgc     # noqa: E402

SANCTIONED = {
    39: {"id": 39, "reason": "ENV_UNAVAILABLE (fpga-board-prototype cap-gap): "
                             "the runner HONESTLY self-reports a deliberate "
                             "FPGA skip [ticket=capgap-v1, review_required=True]",
         "approver": "field-agent-attest (fpga-board cap-gap tier)",
         "ticket": "fpga-board-prototype-capgap-v1",
         "verdict_tier": "ENV_UNAVAILABLE",
         "evidence": ["reports/phase2/fpga/quartus_map_audit.json"],
         "_env_unavailable": True, "_fpga_skip": True},
    6: {"id": 6, "reason": "ENV_UNAVAILABLE (fpga-board-prototype cap-gap): "
                           "no Quartus on host [ticket=capgap-v1, "
                           "review_required=True]",
        "approver": "field-agent-attest (fpga-board cap-gap tier)",
        "ticket": "fpga-board-prototype-capgap-v1",
        "verdict_tier": "ENV_UNAVAILABLE",
        "evidence": ["reports/phase2/fpga/quartus_map_audit.json"],
        "_env_unavailable": True, "_fpga_skip": True},
}

HUMAN_WAIVER = {
    "id": 21, "reason": "sign-off engineer accepted deferring device-level LVS",
    "approver": "sign-off engineer", "ticket": "HUMAN-1",
    "verdict_tier": "WAIVED", "review_required": True,
    "evidence": ["reports/phase2/fpga/quartus_map_audit.json"],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = tmp_path / "proj"
    (p / "reports/phase2/fpga").mkdir(parents=True)
    (p / "reports/phase2/fpga/quartus_map_audit.json").write_text(
        json.dumps({"verdict": "SKIP", "sof_present": False}))
    monkeypatch.setattr(wm, "sanctioned_auto_waivers",
                        lambda _project: {k: dict(v)
                                          for k, v in SANCTIONED.items()})
    return p


def _run(program, project, *extra):
    """Run a gate the way the FLOW runs it — its own process, cwd=project, `.`
    as the argument — and return (rc, stdout+stderr). These programs parse
    `sys.argv` in a zero-argument `main()`, so calling in-process would be a
    different invocation from the one under test."""
    cp = subprocess.run(
        [sys.executable, str(PROGRAMS / f"{program}.py"), ".", *extra],
        cwd=str(project), capture_output=True, text=True)
    return cp.returncode, cp.stdout + cp.stderr


def _gate(project, *extra):
    """`waiver_growth_check .` exactly as `flow/phase1_phase2_phase3.yaml:6751`
    spells it — no --baseline, no --tolerance."""
    return _run("waiver_growth_check", project, *extra)


def _doc(project):
    return json.loads((project / "waivers.json").read_text())


# ---------------------------------------------------------------------------
# DIRECTION 1 — the machinery's own growth is declared, and the gate accepts it
# ---------------------------------------------------------------------------
def test_the_materialized_document_declares_the_population_it_wrote(project):
    wm.materialize(project)
    d = _doc(project)
    assert d["growth_rationale_covers"] == [39, 6], d.get("growth_rationale_covers")
    assert len(d["growth_rationale"]) >= wgc.GROWTH_RATIONALE_MIN_CHARS


def test_the_gate_accepts_a_purely_machinery_population(project):
    wm.materialize(project)
    rc, text = _gate(project)
    assert rc == 0, text
    assert "UNJUSTIFIED_WAIVER_GROWTH" not in text


def test_the_spelling_is_the_population_not_the_count(project):
    """A COUNT is satisfied by a swap; a population is not. #948."""
    wm.materialize(project)
    assert isinstance(_doc(project)["growth_rationale_covers"], list)


# ---------------------------------------------------------------------------
# DIRECTION 2 — anything the machinery did not write still refuses
# ---------------------------------------------------------------------------
def test_a_hand_added_waiver_is_not_covered_and_the_gate_refuses(project):
    wm.materialize(project)
    d = _doc(project)
    d["waived_steps"].append(dict(HUMAN_WAIVER))
    (project / "waivers.json").write_text(json.dumps(d, indent=2))
    rc, text = _gate(project)
    assert rc == 1, text
    assert "GROWTH_RATIONALE_SCOPE_MISMATCH" in text
    assert "'21'" in text or "21" in text


def test_a_swap_is_not_paid_for_by_the_closure(project):
    """Close one machinery waiver, open an unrelated one: the count is
    unchanged and the gate must still refuse. This is the #948 shape, and it is
    why the COUNT spelling is not used."""
    wm.materialize(project)
    d = _doc(project)
    d["waived_steps"] = [e for e in d["waived_steps"] if e.get("id") != 6]
    d["waived_steps"].append(dict(HUMAN_WAIVER))
    (project / "waivers.json").write_text(json.dumps(d, indent=2))
    rc, text = _gate(project)
    assert rc == 1, text
    assert "GROWTH_RATIONALE_SCOPE_MISMATCH" in text


def test_a_human_authored_waivers_json_is_never_given_a_rationale(project):
    """`_is_auto_generated` gates every write site. A human file wins untouched,
    so the gate still bites on human growth exactly as before."""
    human = {"_schema_version": "1", "waived_steps": [dict(HUMAN_WAIVER)]}
    (project / "waivers.json").write_text(json.dumps(human, indent=2))
    wm.materialize(project)
    d = _doc(project)
    assert "growth_rationale" not in d, d
    assert d["waived_steps"] == [HUMAN_WAIVER]
    rc, text = _gate(project)
    assert rc == 1, text
    assert "UNJUSTIFIED_WAIVER_GROWTH" in text


def test_an_empty_population_gets_no_rationale_at_all(project, monkeypatch):
    """A sentence standing over zero waivers is a blanket waiting for its
    first one."""
    d = wm.declare_growth({"waived_steps": []})
    assert "growth_rationale" not in d
    assert "growth_rationale_covers" not in d


def test_a_stale_declaration_is_re_derived_not_left_standing(project):
    """After the population moves, a `covers` describing the old one is exactly
    the stale scope the gate refuses. Every write site re-derives."""
    d = {"waived_steps": [dict(SANCTIONED[39]), dict(SANCTIONED[6])],
         "growth_rationale": "old", "growth_rationale_covers": [39, 6, 99]}
    d["waived_steps"] = [dict(SANCTIONED[39])]
    wm.declare_growth(d)
    assert d["growth_rationale_covers"] == [39]


def test_a_cascade_child_is_not_named(project):
    """`waiver_growth_check` counts a cascade target once under its root;
    naming the child would be an `unmatched` name and refuse the document."""
    parent = dict(SANCTIONED[39]); parent["cascades_to"] = [6]
    d = {"waived_steps": [parent, dict(SANCTIONED[6])]}
    wm.declare_growth(d)
    assert d["growth_rationale_covers"] == [39]


# ---------------------------------------------------------------------------
# The declaration must not break the other three waiver gates
# ---------------------------------------------------------------------------
def test_the_sibling_waiver_gates_still_accept_the_document(project):
    """Three other gates read this file. A field added for one of them must not
    be refused by any of the others."""
    wm.materialize(project)
    for name in ("waivers_schema_check", "waiver_legitimacy_check"):
        rc, text = _run(name, project)
        assert rc == 0, f"{name} rc={rc}\n{text}"
