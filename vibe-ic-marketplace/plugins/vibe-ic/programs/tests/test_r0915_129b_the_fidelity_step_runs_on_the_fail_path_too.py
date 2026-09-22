"""SLT53B handback — step 37.3 produced nothing on the run that needed it most.

THE INPUT, the FULL S arm (spm end-to-end on 0.3.67, the rollback/FAIL path,
DRC 4, step 37 "GDS output only if PV fully clean" FAILs):

    step 37.3  FAIL  missing_artefact      <- and no receipt, no xor artefact,
                                             no log line
    step 37.4  NOT_MEASURED  upstream_failed

while BOTH of 37.3's inputs sat on disk throughout: the retained boundary
`chip_top.prefinish.gds` at 73,850,000 B (the retention added by the previous
commit DID fire) and the shipped GDS at 102,921,972 B.

TWO DEFECTS, and the second is why the first could never have worked.

(1) A VERDICT DEPENDENCY WHERE A FILE DEPENDENCY BELONGED. 37.3 declared
    `blocks_on: [37]`, so a failed PV cascaded it away. But its subject -- does
    the shipped GDS differ from this run's own pre-finishing boundary on the
    design layers -- is answerable from those two artefacts alone. And the run
    where the answer matters MOST is exactly this one: a fidelity defect
    (finishing corrupting design geometry) is a candidate CAUSE of the DRC
    violations that failed PV, so gating the check on PV being clean means it can
    never help diagnose the failure it might explain. R-0915-129's own words: "a
    metric with no producer, not a metric that cannot be produced".
    So the step is now CONDITIONAL on its subject and has no `blocks_on`.

(2) THE STEP'S ONLY PRODUCER WAS ITS OWN GATE CLAUSE, which cannot work:
    compliance reads `required_outputs` BEFORE it runs the clause, so the
    artefact is missing, the step reports FAIL/missing_artefact, and the clause
    that would have written it never runs. REPRODUCED LOCALLY by failing step 37
    on a copy of run21 -- same three rows as the S arm, `gds_xor.json produced:
    False`. The producer is now invoked in `_PRE_AUDIT_PRODUCERS`, which runs on
    EVERY path before the audit reads anything.

AND A TRAP I WALKED INTO WHILE FIXING IT, pinned below because it fails QUIETLY:
the pre-audit hook passes `--pdk-container` to PDK-aware gates. This gate drives
KLayout and takes `--container`. Wired without it, the producer ran, found no
runner, and wrote a NOT_DETERMINED receipt -- on a tree whose runner `covers()`
returns True for. The receipt EXISTED, so "produced" looked satisfied while the
comparison had not run. A produced receipt is not a performed measurement.

MEASURED after both fixes, on a copy of run21 with step 37 forced to FAIL:
    no subject            -> NOT_APPLICABLE (never FAIL/missing_artefact)
    subject + boundary    -> PASS, reference kind `retained`, 38 layer(s)
                             compared, 0 design-layer difference(s)
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parents[2]
PROGRAMS = PLUGIN / "programs"
sys.path.insert(0, str(PROGRAMS))


def _flow() -> dict:
    return yaml.safe_load(
        (PLUGIN / "flow" / "phase1_phase2_phase3.yaml").read_text())


def _step(sid: str) -> dict:
    d = _flow()

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "name" in o and "stage" in o:
                yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)
    return next(s for s in walk(d) if str(s["id"]) == sid)


# ── (1) conditional on its subject, not dependent on a verdict ──────────────

def test_the_fidelity_step_has_no_verdict_dependency_on_stream_out():
    """THE DEFECT. `blocks_on: [37]` cascaded it away on the FAIL path.

    The repair is NOT an empty list, which was this file's second draft.
    `blocks_on` must be present (the flow contract: "every step must declare
    `blocks_on`, even as an empty list") and it must carry the step's REAL data
    dependency, which dimension 5 measures independently: the condition names
    `phase3/stage3/pnr/routed.def`, step 21 produces it, so the edge is [21].

    WHAT IS ASSERTED IS THE PROPERTY, NOT THE LITERAL: step 37 must not be in the
    closure, because the ruling removed the dependency on 37's VERDICT, and the
    closure must be non-empty, because a step that reads another step's output and
    declares no edge is D5-MISSING-EDGE.
    """
    parents = _step("37.3").get("blocks_on")
    assert parents == [21], parents
    assert 37 not in parents and "37" not in [str(p) for p in parents], (
        "a verdict dependency on step 37 is what cascaded this step away on the "
        "one run where the fidelity answer mattered most")


def test_the_step_is_not_listed_as_a_dependency_graph_root():
    """The other half of the same fact, in the other file that states it.

    While the step declared `blocks_on: []` it was added to
    `flow_dependency_graph_check.DECLARED_ROOTS`. It has a real predecessor now,
    so listing it there would declare the absence of an ordering constraint that
    exists — and a root list that drifts from the yaml is how an orphan check
    stops checking.
    """
    import flow_dependency_graph_check as G
    assert "37.3" not in G.DECLARED_ROOTS, sorted(G.DECLARED_ROOTS)


def test_the_fidelity_step_is_conditional_on_the_routed_database():
    """THE TRIGGER, and the two answers the matrix refused before this one.

    NOT the route attestation: `flow_condition_reachability_check` calls a trigger
    the flow itself produces whose absence is loud NOWHERE a SELF-DISABLING
    CONDITION, and the runner writes that file only under
    `_chip_path_requests_pad_ring`, so every design off the chip pad-ring path had
    no fidelity check and nothing said so.

    NOT the shipped GDS: it is step 37's required_output, so naming it here IS a
    data dependency on 37 (D5-MISSING-EDGE), and the only thing that satisfies
    that clause is the verdict dependency the ruling removed.

    The routed DEF is the precondition the comparison actually has -- its
    REFERENCE is streamed from that database -- and it belongs to step 21, which
    is upstream of stream-out.
    """
    cond = _step("37.3").get("condition") or {}
    paths = cond.get("files_exist") or []
    assert "phase3/stage3/pnr/routed.def" in paths, cond
    assert "reports/phase3/pad_ring_route_evidence.json" not in paths, (
        "the attestation is the BINDING, not the subject; gating on it silently "
        "disables this step on every design without a chip pad ring")
    assert "phase3/stage4/gds/*.gds" not in paths, (
        "naming step 37's own required_output here is a data dependency on 37, "
        "which D5-MISSING-EDGE reports and only a blocks_on edge to 37 settles")


def test_an_unmet_trigger_names_its_producer_instead_of_going_quiet():
    """`condition_kind`, which the consumer branches on and whose default is the
    benign answer.

    `flow_gate_grid` named the omission when this step had none: "every
    conditional skip falls to the benign default and 'this design legitimately
    has none' cannot be told from 'someone forgot to author the trigger'".

    `dependency_required` is the accurate kind BECAUSE OF WHAT THE TRIGGER IS:
    `routed.def` is not a property of the design, it is an artefact step 21
    produces, so its absence means the producer did not deliver. That kind routes
    the unmet condition through `_resolve_dependency_condition_results`, which
    states it as MISSING plus `blocked-by-upstream(21)` and names the artefact --
    a design_dependent skip would have made a failed route and a design with
    nothing to compare read identically.
    """
    assert _step("37.3").get("condition_kind") == "dependency_required", (
        _step("37.3").get("condition_kind"))
    import flow_compliance_check as FCC
    src = Path(FCC.__file__).read_text()
    assert 'step.get("condition_kind") != "dependency_required"' in src, (
        "the consumer this kind was chosen for no longer reads it; the "
        "classification above would silently become a plain skip")


def test_the_trigger_has_a_loud_absence_and_a_declared_producer():
    """WHY THIS TRIGGER IS ADMISSIBLE, measured through the two indexes the
    reachability checker and dimension 5 actually read.

    A condition may only be gated on an artefact whose disappearance somebody
    reports (T7: a hard `files_exist`), and a step may only read an artefact whose
    producer is in its blocks_on closure. `phase3/stage3/pnr/routed.def` satisfies
    both at the SAME step, 21, which is what makes it the one admissible trigger:
    the route attestation satisfies neither.
    """
    import flow_condition_reachability_check as R
    steps = _flow()["steps"]
    hard = R._build_hard_gate_index(steps)
    assert hard.get("phase3/stage3/pnr/routed.def") == "21", hard.get(
        "phase3/stage3/pnr/routed.def")
    assert "reports/phase3/pad_ring_route_evidence.json" not in hard, (
        "if the attestation ever gains a hard clause this test should be "
        "revisited, not deleted -- the trigger choice was made on this fact")
    assert _step("37.3").get("blocks_on") == [21], (
        "the trigger's producer must be the declared edge, or the two halves of "
        "this argument are about different steps")


def test_its_sibling_keeps_its_own_dependency():
    """CONTROL: 37.4 aggregates the run's verdicts and IS downstream of 37. This
    change must not widen to it -- only the step whose subject is two files."""
    assert _step("37.4").get("blocks_on") == [37]


# ── (2) the producer runs before the audit reads its output ─────────────────

def test_the_producer_is_invoked_before_the_audit():
    import phase3_one_shot_runner as R
    names = [row[0] for row in R._PRE_AUDIT_PRODUCERS]
    assert "gds_xor" in names, names
    entry = next(r for r in R._PRE_AUDIT_PRODUCERS if r[0] == "gds_xor")
    assert entry[1] == "gds_xor_check.py"
    assert entry[2] == "reports/phase3/gds_xor.json", (
        "the declared output and the produced receipt must be the same path, or "
        "the audit still reads a missing artefact")


def test_a_gate_clause_alone_cannot_produce_its_own_declared_output():
    """WHY (2) IS NECESSARY, stated as the property: 37.3's required_output IS its
    gate's product, and compliance reads required_outputs before running gates.
    Without a pre-audit producer the step can only ever report missing_artefact."""
    s = _step("37.3")
    declared = set(s.get("required_outputs") or [])
    clause = " ".join(str(c) for c in (s.get("gate") or {}).get("all_of") or [])
    assert declared == {"reports/phase3/gds_xor.json"}
    assert "reports/phase3/gds_xor.json" in clause, (
        "the clause writes exactly the artefact the step declares — which is why "
        "something must run it BEFORE the audit looks")


# ── the container trap: a produced receipt is not a measurement ─────────────

def test_the_layout_producer_gets_the_container_by_its_own_flag_name():
    """THE QUIET TRAP. `--pdk-container` names a PDK-view volume for a different
    question; this gate drives KLayout and takes `--container`. Without it the
    producer wrote a NOT_DETERMINED receipt on a tree its runner covers, and the
    existing receipt made "produced" look satisfied."""
    import phase3_one_shot_runner as R
    assert "gds_xor" in R._KLAYOUT_CONTAINER_SIGNOFF_GATES
    assert "gds_xor" not in R._PDK_AWARE_SIGNOFF_GATES, (
        "the two flags are different questions and must not be conflated")
    src = (PROGRAMS / "phase3_one_shot_runner.py").read_text()
    i = src.index("for name, program, out_rel, extra_argv in _PRE_AUDIT_PRODUCERS:")
    body = src[i:i + 1800]
    assert '("--container", container)' in body
    assert body.index("_PDK_AWARE_SIGNOFF_GATES") < body.index(
        "_KLAYOUT_CONTAINER_SIGNOFF_GATES"), "both branches must remain present"


def test_the_gate_still_refuses_when_it_cannot_measure():
    """The teeth that survived: a receipt is not a pass. `--container` reaching the
    gate does not make it optimistic — with no runner it still writes
    NOT_DETERMINED and rc=1, which is what "an unrun XOR is not a zero" means."""
    src = (PROGRAMS / "gds_xor_check.py").read_text()
    assert 'return finish("NOT_DETERMINED", 1,' in src
    assert "an unrun XOR is not a zero" in src
