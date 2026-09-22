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

def test_the_fidelity_step_has_no_verdict_dependency():
    """THE DEFECT. `blocks_on: [37]` cascaded it away on the FAIL path.

    Declared as `[]`, not omitted: the flow contract requires every step to carry
    the key ("every step must declare `blocks_on`, even as an empty list", which
    the ledger enforces and which omitting it made red on ['37.3']). An empty list
    is also the better statement — it says "no verdict dependency" out loud.
    """
    assert _step("37.3").get("blocks_on") == []


def test_the_fidelity_step_is_conditional_on_its_subject():
    """AND THE SUBJECT IS THE SHIPPED GDS, not the run's attestation about it.

    RE-POINTED, because the first spelling was a defect the matrix named:
    conditioning on `reports/phase3/pad_ring_route_evidence.json` is a
    SELF-DISABLING CONDITION -- `flow_condition_reachability_check` measured its
    absence as loud NOWHERE (T7=None, T3=None, T5=None) -- and the runner writes
    that file only under `_chip_path_requests_pad_ring`, so every design off the
    chip pad-ring path had no fidelity check and nothing said so.

    `phase3/stage4/gds/*.gds` is the subject itself and its absence FAILs step 37,
    which is asserted below rather than described: that is the whole reason this
    trigger is admissible where the other was not.
    """
    cond = _step("37.3").get("condition") or {}
    assert "phase3/stage4/gds/*.gds" in (cond.get("files_exist") or []), cond
    assert "reports/phase3/pad_ring_route_evidence.json" not in (
        cond.get("files_exist") or []), (
        "the attestation is the BINDING, not the subject; gating on it silently "
        "disables this step on every design without a chip pad ring")


def test_the_trigger_has_a_loud_absence_somewhere():
    """WHY THIS TRIGGER IS ADMISSIBLE AND THE OLD ONE WAS NOT, measured through
    the same index the reachability checker reads.

    A condition may only be gated on an artefact whose disappearance is reported
    by somebody. `phase3/stage4/gds/*.gds` is a hard `files_exist` in step 37's own
    gate, so a run missing it FAILED there; the route attestation is in no step's
    hard set at all.
    """
    import flow_condition_reachability_check as R
    hard = R._build_hard_gate_index(_flow()["steps"])
    assert hard.get("phase3/stage4/gds/*.gds") == "37", hard.get(
        "phase3/stage4/gds/*.gds")
    assert "reports/phase3/pad_ring_route_evidence.json" not in hard, (
        "if the attestation ever gains a hard clause this test should be "
        "revisited, not deleted -- the trigger choice was made on this fact")


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
