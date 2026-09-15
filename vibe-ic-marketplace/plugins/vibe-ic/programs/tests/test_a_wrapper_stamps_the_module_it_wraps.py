"""A declared output written by the program the flow lists as this step's own
PRODUCER must not be classified as the auditor's.

MEASURED 2026-09-15 (lane icsub2) on `subservient` x gf180mcuD, by calling
`flow_compliance_check._is_gate_verdict_document` directly over every declared
`required_output` that is also its step's own gate `--json` target::

    step  document                                stamp                      programs:
    26    reports/phase3/antenna_signoff.json     eda_report_audit:antenna   [antenna_report_check, ...]
    36    reports/audit/tapeout_checklist.json    signoff_audit:tapeout      [tapeout_signoff_check]

In both rows the document was written by the program the flow lists as THIS
STEP'S PRODUCER, and in both the stamp matched NEITHER the producer set nor the
gate set -- so the comparison fell through to the function's final
`return True` and a RUN-WRITTEN document was refused as self-certified
evidence, taking the step to MISSING.

WHY THE STAMP DOES NOT MATCH THE NAME. Those programs are WRAPPERS. Measured
across the tree, uniformly::

    antenna_report_check   from eda_report_audit import main   MODE = "antenna"
    drc_report_check       from eda_report_audit import main   MODE = "drc"
    sta_report_check       from eda_report_audit import main   MODE = "sta"
    em_report_check        from eda_report_audit import main   MODE = "em"
    ir_drop_report_check   from eda_report_audit import main   MODE = "ir_drop"
    lvs_report_check       from eda_report_audit import main   MODE = "lvs"
    tapeout_signoff_check  from signoff_audit import main      (mode in argv)

and the shared emitter stamps the WRAPPED module plus the mode. `_names()`
split on whitespace and `/` and stripped `.py`; nothing in it could see
through a wrapper.

THE ALIAS IS DERIVED FROM THE PROGRAM'S OWN SOURCE, never tabulated. A table
would go stale the first time a wrapper is added, and this repo's rule for
that is the one `die_level_density_rules` follows: read the artefact, do not
carry a name list.

BOTH DIRECTIONS ARE THE POINT. A genuinely gate-written document must STILL be
refused, and the second half of this file is that guard: steps 10, 21, 23, 24,
25, 26.5ic, 31 (drc/lvs), 37.5ip and 37.5ic declare a gate `--json` target with
`programs: []` or with producers that write other paths, and every one of them
must stay True after this change -- measured, and asserted below over the
shipped flow itself.

chip-AGNOSTIC: the fixtures are synthetic documents; the flow-wide assertion
reads the shipped yaml and the shipped programs, no design artefact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import flow_compliance_check as F  # noqa: E402

import pytest  # noqa: E402


def _doc(tmp_path: Path, stamp: str, name: str = "d.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"program": stamp, "passed": True}))
    return p


# ── the defect ────────────────────────────────────────────────────────────

def test_a_wrapper_stamp_is_the_producers_when_the_step_lists_the_wrapper(
        tmp_path):
    """THE MEASURED SHAPE. `antenna_report_check` is listed as the step's
    producer; the document it writes says `eda_report_audit:antenna`."""
    p = _doc(tmp_path, "eda_report_audit:antenna")
    assert F._is_gate_verdict_document(
        p, frozenset({"gds_antenna_deck_check"}),
        frozenset({"antenna_report_check", "gds_antenna_deck_check"})) is False


def test_a_wrapper_with_the_mode_in_argv_is_also_resolved(tmp_path):
    """`tapeout_signoff_check` pins no MODE -- it forwards `--mode tapeout` --
    so its only honest alias is the wrapped module, and the stamp's own
    prefix is what answers."""
    p = _doc(tmp_path, "signoff_audit:tapeout")
    assert F._is_gate_verdict_document(
        p, frozenset({"report_belongs_to_project_check"}),
        frozenset({"tapeout_signoff_check"})) is False


# ── the other direction: a gate document is STILL refused ─────────────────

def test_a_wrapper_stamp_is_the_GATES_when_only_the_gate_lists_it(tmp_path):
    """Steps 10/21/23/24/25 declare the target with `programs: []`. Nothing in
    the run writes it and it must stay refused."""
    p = _doc(tmp_path, "eda_report_audit:drc")
    assert F._is_gate_verdict_document(
        p, frozenset({"drc_report_check"}), frozenset()) is True


def test_one_wrappers_mode_is_not_anothers(tmp_path):
    """THE CLAUSE THAT KEEPS THE ALIAS NARROW. Two wrappers of ONE module are
    two different documents; collapsing them to the bare module would let a
    step's DRC gate document be credited to its antenna producer."""
    p = _doc(tmp_path, "eda_report_audit:drc")
    assert F._is_gate_verdict_document(
        p, frozenset({"drc_report_check"}),
        frozenset({"antenna_report_check"})) is True


def test_a_non_wrapper_gate_document_is_unchanged(tmp_path):
    p = _doc(tmp_path, "digital_hardmacro_check")
    assert F._is_gate_verdict_document(
        p, frozenset({"digital_hardmacro_check"}),
        frozenset({"digital_hardmacro_gen"})) is True


def test_a_document_with_no_stamp_is_still_not_the_auditors(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"rows": [1, 2, 3]}))
    assert F._is_gate_verdict_document(
        p, frozenset({"any_check"}), frozenset({"any_producer"})) is False


def test_an_unknown_stamp_still_keeps_the_presence_only_answer(tmp_path):
    p = _doc(tmp_path, "something_nobody_declares")
    assert F._is_gate_verdict_document(
        p, frozenset({"a_check"}), frozenset({"a_producer"})) is True


def test_the_presence_only_caller_is_unchanged(tmp_path):
    """Callers that pass no name sets keep the old answer exactly."""
    assert F._is_gate_verdict_document(
        _doc(tmp_path, "eda_report_audit:antenna")) is True


# ── over the SHIPPED flow, both directions at once ────────────────────────

def _flow_steps():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(
        (PROGRAMS.parent / "flow" / "phase1_phase2_phase3.yaml").read_text())

    def walk(n):
        if isinstance(n, dict):
            if "id" in n and ("required_outputs" in n or "gate" in n):
                yield n
            for v in n.values():
                yield from walk(v)
        elif isinstance(n, list):
            for v in n:
                yield from walk(v)
    return {str(s["id"]): s for s in walk(doc)}


def _sets(step):
    gp = frozenset(F._gate_name(c)
                   for c in F._declared_gate_commands(step.get("gate") or {}))
    pp = frozenset(str(p).strip() for p in (step.get("programs") or [])
                   if isinstance(p, str) and str(p).strip())
    return gp, pp


@pytest.mark.parametrize("sid,stamp", [
    ("26", "eda_report_audit:antenna"),
    ("36", "signoff_audit:tapeout"),
])
def test_the_shipped_step_credits_its_own_producers_document(sid, stamp,
                                                             tmp_path):
    step = _flow_steps().get(sid)
    if step is None:
        pytest.skip(f"step {sid} is not in the shipped flow any more")
    gp, pp = _sets(step)
    assert F._is_gate_verdict_document(_doc(tmp_path, stamp), gp, pp) is False


@pytest.mark.parametrize("sid,stamp", [
    ("10", "eda_report_audit:sta"),
    ("21", "eda_report_audit:drc"),
    ("23", "eda_report_audit:sta"),
    ("24", "eda_report_audit:ir_drop"),
    ("25", "eda_report_audit:em"),
    ("31", "eda_report_audit:drc"),
    ("37.5ip", "digital_hardmacro_check"),
])
def test_the_shipped_step_still_refuses_a_gate_only_document(sid, stamp,
                                                            tmp_path):
    """THE GUARD. These steps declare the target and list no producer that
    writes it; nothing in the run authors them and the refusal must stand."""
    step = _flow_steps().get(sid)
    if step is None:
        pytest.skip(f"step {sid} is not in the shipped flow any more")
    gp, pp = _sets(step)
    assert F._is_gate_verdict_document(_doc(tmp_path, stamp), gp, pp) is True


def test_every_wrapper_in_the_tree_resolves_to_the_module_it_wraps():
    """The alias is read from the SOURCE, so this asserts the mechanism over
    the shipped programs rather than over a list retyped here."""
    import re
    found = 0
    for src in sorted(PROGRAMS.glob("*_report_check.py")):
        text = src.read_text(errors="replace")
        m = re.search(r"^from\s+(\w+)\s+import\s+main\b", text, re.M)
        md = re.search(r"^MODE\s*=\s*[\"'](\w+)[\"']", text, re.M)
        if not (m and md):
            continue
        found += 1
        stamp = f"{m.group(1)}:{md.group(1)}"
        name = src.stem
        p = PROGRAMS / "tests" / "__alias_probe.json"
        try:
            p.write_text(json.dumps({"program": stamp}))
            assert F._is_gate_verdict_document(
                p, frozenset({"some_other_gate"}), frozenset({name})) is False, (
                f"{name} writes {stamp} and the flow lists it as a producer")
        finally:
            p.unlink(missing_ok=True)
    assert found >= 5, f"only {found} wrappers found; the shape has changed"
