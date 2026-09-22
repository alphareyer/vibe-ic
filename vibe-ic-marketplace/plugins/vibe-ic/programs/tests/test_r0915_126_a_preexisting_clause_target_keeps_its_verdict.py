"""The step-14 case: a clause whose `--json` target ALREADY EXISTS.

THE REPRODUCER, and the reason the earlier probe missed it. v1.23.23 sends a
clause's receipt to scratch when the named target is a document the run had
already produced. The step-verdict reader then read the target path -- the
PRODUCER's document -- and the step disclosed NOT_MEASURED: "it ran without
measuring design-bound content, which names no cause to act on".

MEASURED by the owner on the SLT36 spm run tree, the same bytes copied twice and
audited from each reader's own commit:

    step      pre-icslot36 (c7b2a7072)   icslot36 (b4b7a5407)
    14        PASS                       NOT_MEASURED
    26.5ic    FAIL                       NOT_MEASURED

and exactly those two of 69 steps moved. A PASS-path run cannot show it, because
there the clause target does not pre-exist the same way; the PRECONDITION is that
the target is already on disk when the clause runs. That precondition is what
this fixture stages, in both of the shapes that moved: step 14's mixture of
advisory and optional clauses, and 26.5ic's single blocking clause.

NOT_MEASURED over a gate that ran, exited and wrote a verdict is the worst of the
answers available: it is indistinguishable from a gate that was never wired, and
it names no cause to act on.

26.5ic IS A DIFFERENT CAUSE, and this module does not claim it. Its
`die_finishing_check` reason -- "the gate reports its input was applicable and
was NOT examined" -- is IDENTICAL on both readers. What changed there is that the
audit stopped authoring `reports/phase3/die_finishing.json`, so the document is no
longer excluded as `audit_created`; the `missing_artefact` that had made the step
FAIL went with it, leaving the gate's own INCOMPLETE to classify as
`partial_population`. Its earlier FAIL was manufactured by the overwrite, so
restoring it would mean restoring the overwrite. The remedy there belongs to the
step, not to this reader.
"""
from __future__ import annotations

import json
import stat
import pytest
import flow_compliance_check as F

#: A gate that writes a typed verdict to its --json path and exits as told.
GATE = '''#!/usr/bin/env python3
import json, sys
from pathlib import Path
argv = sys.argv[1:]
out = None
for i, tok in enumerate(argv[:-1]):
    if tok in ("--json", "--report"):
        out = Path(argv[i + 1])
if out is not None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"program": %(name)r, "verdict": %(verdict)r,
                               "reason": "the gate's own receipt"}))
sys.exit(%(rc)d)
'''

#: What is actually sitting at the clause's --json path in a completed run: the
#: document the RUN wrote there, carrying the RUN's own older answer. MEASURED on
#: the SLT36 tree, `yosys_tiecell_recipe_order_check` at step 14, exit_code 0 in
#: BOTH arms:
#:      pre-icslot36  structured_verdict CLEAN        enforcement PASSED
#:      icslot36      structured_verdict NOT_CHECKED  enforcement DISCLOSED_INCOMPLETE
#:                    reason_class EXECUTION_ERROR
#: The gate exited 0 and said CLEAN. Once its receipt moved to scratch the reader
#: read THIS document instead and published the run's stale NOT_CHECKED, which
#: cascaded to reason_class `partial_population` and a NOT_MEASURED step.
STALE_DOC = {"program": "fixture_pass_gate", "verdict": "NOT_CHECKED",
             "reason_class": "EXECUTION_ERROR",
             "reason": "the RUN's older attempt, not this audit's invocation"}

FLOW = {
    "version": "fixture",
    "flow_name": "fixture_preexisting_target",
    "total_steps": 2,
    "stages": [{"id": "stageF", "name": "fixture stage"}],
    "steps": [
        {
            # STEP 14's SHAPE: advisory + optional clauses, the first of which
            # names a path the run already produced.
            "id": "S14", "name": "fixture advisory+optional step",
            "stage": "stageF",
            "programs": ["fixture_pass_gate", "fixture_opt_gate"],
            "required_outputs": ["reports/fixture_document.json"],
            "gate": {"all_of": [
                {"advisory_program_exit_zero": {
                    "command": ("fixture_pass_gate . --json "
                                "reports/fixture_document.json"),
                    "advisory_reason": "fixture: advisory, as step 14's is"}},
                {"advisory_program_exit_zero": {
                    "command": ("fixture_opt_gate . --json "
                                "reports/fixture_optional.json"),
                    "advisory_reason": "fixture: the second advisory clause"}},
            ]},
        },
        {
            # 26.5ic's SHAPE: one blocking clause over a produced document, and
            # the gate refuses. The measured answer is FAIL, not "unmeasured".
            "id": "S265", "name": "fixture blocking step", "stage": "stageF",
            "programs": ["fixture_fail_gate"],
            "required_outputs": ["reports/fixture_blocking.json"],
            "gate": {"program_exit_zero": ("fixture_fail_gate . --json "
                                           "reports/fixture_blocking.json")},
        },
    ],
}


def stage(tmp_path, monkeypatch, *, target_exists=True):
    programs = tmp_path / "programs"
    programs.mkdir()
    for name, verdict, rc in (("fixture_pass_gate", "PASS", 0),
                              ("fixture_opt_gate", "PASS", 0),
                              ("fixture_fail_gate", "FAIL", 1)):
        g = programs / f"{name}.py"
        g.write_text(GATE % {"name": name, "verdict": verdict, "rc": rc})
        g.chmod(g.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(F, "PROGRAMS_DIR", programs)

    project = tmp_path / "proj"
    (project / "reports").mkdir(parents=True)
    if target_exists:
        # THE PRECONDITION. Both blocking/advisory targets are already on disk,
        # as they are in a completed run.
        for rel in ("reports/fixture_document.json",
                    "reports/fixture_blocking.json"):
            (project / rel).write_text(json.dumps(STALE_DOC))

    flow = tmp_path / "fixture_flow.yaml"
    flow.write_text(json.dumps(FLOW))          # YAML is a superset of JSON
    return project, flow


def audit(project, flow, tmp_path):
    out = tmp_path / "audit.json"
    F.main([str(project), "--flow-def", str(flow), "--lenient",
            "--json", str(out)])
    doc = json.loads(out.read_text())

    def walk(o):
        if isinstance(o, dict):
            if "id" in o and "status" in o:
                yield o
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    seen = {}
    for row in walk(doc):
        seen.setdefault(str(row["id"]), row["status"])
    return seen


def test_the_step_keeps_the_verdict_ITS_OWN_GATE_JUST_REACHED(tmp_path,
                                                              monkeypatch):
    """THE REPRODUCER. The clause's gate exits 0 and its own receipt says PASS,
    while the run's older document at that path says NOT_CHECKED /
    EXECUTION_ERROR. Reading the document disclosed the step NOT_MEASURED; the
    step must carry the answer its own invocation reached."""
    project, flow = stage(tmp_path, monkeypatch)
    seen = audit(project, flow, tmp_path)
    assert seen.get("S14") == "PASS", seen


def test_a_blocking_refusal_is_unaffected_by_a_stale_document(tmp_path,
                                                             monkeypatch):
    """The neighbouring shape, pinned AS FOUND: a blocking clause whose gate
    exits non-zero is FAIL either way, because the exit code decides the clause
    and no report can launder it. Measured identical on both readers."""
    project, flow = stage(tmp_path, monkeypatch)
    seen = audit(project, flow, tmp_path)
    assert seen.get("S265") == "FAIL", seen


def test_the_producer_document_is_left_byte_for_byte_alone(tmp_path,
                                                           monkeypatch):
    """The half of #2486 that was right, and must stay right."""
    project, flow = stage(tmp_path, monkeypatch)
    rels = ("reports/fixture_document.json", "reports/fixture_blocking.json")
    was = {r: (project / r).read_bytes() for r in rels}
    audit(project, flow, tmp_path)
    for r in rels:
        assert (project / r).read_bytes() == was[r], r


def test_with_no_target_to_protect_nothing_is_redirected(tmp_path, monkeypatch):
    """The control. No redirect happens, so the reader's behaviour is the
    pre-change one, and the step is judged by the absent-producer rule instead --
    a different and correctly-named answer, not NOT_MEASURED."""
    project, flow = stage(tmp_path, monkeypatch, target_exists=False)
    seen = audit(project, flow, tmp_path)
    assert seen.get("S14") == "FAIL", seen
    assert seen.get("S265") == "FAIL", seen
