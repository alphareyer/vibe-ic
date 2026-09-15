"""One non-conforming key in a step's own report must not cost the whole step's
metrics.

MEASURED 2026-09-15 (lane icsub2) on `subservient` x gf180mcuD, printed by
`stage3_compliance` on a completed run::

    [step_metrics] EMIT FAILED (step=25, domain=flow): ValueError:
      step_metrics.emit: '25__flow__max_segment_current_A': component
      'max_segment_current_A' must be lowercase alphanumeric/underscore

`flow_compliance_check` harvests every scalar key out of a step's declared
`.json` outputs and forwards them to `step_metrics.emit_best_effort`. `emit`
validates EVERY key and raises on the FIRST defect, before it writes anything
-- so step 25's `em_signoff.json`, which spells its peak current with the SI
unit capitalised, took every other step-25 metric down with it and the step's
metric file was never written at all. A batch emitter over names the emitter
does not own is a single point of loss.

THE NAME IS NOT REWRITTEN, and that is deliberate. This harvester does not own
these names -- the program that COMPUTED the number does -- and quietly
lower-casing one would publish a key nobody declared, under the authority of a
module whose `collect` explicitly refuses to derive anything. The
non-conforming key is DROPPED and NAMED on stderr; its conforming siblings are
emitted. That is `emit_best_effort`'s own stated rule ("a caller cannot fix
what it is never told about") applied per KEY instead of per batch.

chip-AGNOSTIC: a synthetic step and a synthetic report.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F  # noqa: E402
import step_metrics as SM  # noqa: E402


STEP = {"id": "25", "required_outputs": ["reports/phase3/em_signoff.json"]}


def _project(tmp_path: Path, doc: dict) -> Path:
    d = tmp_path / "reports/phase3"
    d.mkdir(parents=True, exist_ok=True)
    (d / "em_signoff.json").write_text(json.dumps(doc))
    return tmp_path


def _emitted(project: Path, step_id: str = "25") -> dict:
    f = project / SM.METRICS_REL / f"{SM.normalize_step(step_id)}.json"
    return json.loads(f.read_text()) if f.is_file() else {}


# ── the defect ────────────────────────────────────────────────────────────

def test_the_measured_key_really_is_non_conformant():
    """The fixture is the measured shape, and the SCHEMA is what refuses it --
    this file does not carry a second opinion about the rule."""
    assert SM.key_defect("25__flow__max_segment_current_A") is not None
    assert SM.key_defect("25__flow__segments_analysed") is None


def test_one_bad_name_does_not_take_its_siblings_with_it(tmp_path, capsys):
    p = _project(tmp_path, {"max_segment_current_A": 0.0021,
                            "segments_analysed": 41,
                            "violations": 0})
    _drive(F, p, STEP)
    got = _emitted(p)
    assert any(k.endswith("__segments_analysed") for k in got), got
    assert any(k.endswith("__violations") for k in got), got
    assert not any("max_segment_current_A" in k for k in got), got


def test_the_refused_key_is_NAMED_on_stderr(tmp_path, capsys):
    p = _project(tmp_path, {"max_segment_current_A": 0.0021,
                            "segments_analysed": 41})
    _drive(F, p, STEP)
    err = capsys.readouterr().err
    assert "max_segment_current_A" in err
    # NOT merely "the key appears somewhere in stderr" — the pre-fix tree
    # prints it too, from inside `EMIT FAILED`, while losing the batch. What
    # this asserts is the PER-KEY refusal: the conforming siblings were
    # emitted and this one was not.
    assert "not schema-conformant" in err, err
    assert "every conforming sibling was" in err, err
    assert "EMIT FAILED" not in err, (
        "the batch must not have failed; only the one key is refused")


def test_a_step_whose_keys_all_conform_is_unchanged(tmp_path):
    p = _project(tmp_path, {"segments_analysed": 41, "violations": 0})
    _drive(F, p, STEP)
    got = _emitted(p)
    # the step's own verdict is emitted alongside the report's numbers
    assert {"25__flow__segments_analysed", "25__flow__violations"} <= set(got), got
    assert not any("must be lowercase" in k for k in got)


def test_a_non_scalar_value_is_refused_the_same_way(tmp_path, capsys):
    """The other half of the schema. GREEN ON BOTH ARMS by construction — the
    harvest loop already only takes int/float out of a report, so a list never
    reaches `emit`. It is here as the guard that this change did not widen what
    gets emitted."""
    p = _project(tmp_path, {"violating_cells": [1, 2, 3], "violations": 0})
    _drive(F, p, STEP)
    got = _emitted(p)
    assert any(k.endswith("__violations") for k in got), got
    assert not any("violating_cells" in k for k in got), got


def _drive(mod, project: Path, step: dict) -> None:
    """Call whichever entry point this tree's harvester exposes.

    The harvest lives inside a larger function in some revisions; this locates
    the one that forwards a step's report keys to `step_metrics` rather than
    hard-coding a private name that would make the test a spelling assertion.
    """
    result = mod.StepResult(id=step["id"], name="fixture step",
                            stage="stage3", status="PASS")
    for name in ("_emit_step_metrics", "_harvest_step_metrics",
                 "_step_metrics_from_outputs"):
        fn = getattr(mod, name, None)
        if callable(fn):
            fn(project, step, result)
            return
    import pytest
    pytest.skip("the metric harvest entry point could not be located in this "
                "revision; re-derive it before trusting this file")
