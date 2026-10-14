#!/usr/bin/env python3
"""FX_P2_DECLARATION_BEFORE_STEP4 — the design's option selection is emitted
before Step 4 reads it, by the SAME producer, and a later emission that
disagrees refuses by name.

Step 4's gate narrows the conditional L10 cases (`applies_when`, R-0915-102)
against `plugin_output/declaration.json`. `step_arith_declaration_emit` wrote
that file at phase-2 row 33, after Step 4 at row 20, so on a FIRST run the gate
never saw a selection the producer could already derive, and a case the design
excludes stayed blocking; only a re-run tree was narrowed. The contract-driven
producer (`spec_declaration_emit`) reads only the design input, the RTL's
`key = value` comment blocks and a prior declaration, so the runner now asks it
before Step 4 as well (`declaration_before_step4`), and row 33 refuses when the
later emission is not identical or a superset (`declaration_disagreement`).

Driven through the real producer and the real Step-4 gate; the Step-4 tree is
the review fixture (execution record planted in the shape `run_unit_tbs`
writes). chip-AGNOSTIC: synthetic options and cases.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import cpu_functional_oracle_waiver_check as GATE  # noqa: E402
import design_one_shot_runner as D                 # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_fx_p2_review_fixture",
    Path(__file__).with_name(
        "test_fx_p2_review_input_gap_needs_positive_evidence.py"))
_FX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_FX)
_project, PASSING = _FX._project, _FX.PASSING

DECL = "plugin_output/declaration.json"
#: A conditional case, as Phase 1 emits it from "(if option M is chosen)".
COND = {"name": "opt_m_mul_div", "kind": "functional_vector",
        "stimulus": "(if option M is chosen) multiply/divide instructions",
        "expected": "PASS",
        "applies_when": {"option": "M", "stated": "(if option M is chosen)"}}

#: The input's own declaration contract. The design DESIGNATES its selection
#: with the producer's own grammar (`(primary)` on exactly one value).
_CONTRACT = """# L7

The Plugin MUST declare `{path}` before authoring:

| Field | Required | Example |
|---|---|---|
| `isa_extensions` | Yes | {values} |
"""


#: Asked through getattr, so a tree without the early emission ANSWERS (no
#: declaration exists before Step 4) instead of raising.
_early = getattr(D, "declaration_before_step4", lambda _p: {"emitted": False})


def _fresh(tmp_path: Path, values: str) -> Path:
    """A FIRST-run tree: the Step-4 fixture, no declaration yet, and the
    input's contract."""
    proj = _project(tmp_path, [PASSING, COND], {PASSING["name"]: "PASS"})
    (proj / "input" / "docs" / "L7_verification_plan.md").write_text(
        _CONTRACT.format(path=DECL, values=values), encoding="utf-8")
    assert not (proj / DECL).exists()
    return proj


EXCLUDES_M = '`["I", "Zifencei"]`(primary)/ `["I", "M", "Zifencei"]`(secondary)'
SELECTS_M = '`["I", "M", "Zifencei"]`(primary)/ `["I", "Zifencei"]`(secondary)'
UNDESIGNATED = '`["I", "Zifencei"]` / `["I", "M", "Zifencei"]`'


def test_on_the_first_run_an_excluded_case_is_not_applicable(tmp_path):
    """THE MEASURED GAP: the input designates a selection without M; on the
    FIRST run Step 4 books the M case NOT_APPLICABLE, citing the declaration."""
    proj = _fresh(tmp_path, EXCLUDES_M)
    rec = _early(proj)
    assert rec["emitted"] is True, rec
    rc, msg = GATE._evaluate(proj)
    assert rc == 0, (rc, msg)
    na = GATE._design_declared_na_disclosure(proj)
    assert [c["case"] for c in na["cases"]] == [COND["name"]], na
    assert na["declaration"] == DECL


def test_a_design_that_selects_the_option_still_demands_the_case(tmp_path):
    proj = _fresh(tmp_path, SELECTS_M)
    assert _early(proj)["emitted"] is True
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)
    assert COND["name"] in msg


def test_a_selection_the_input_does_not_designate_is_not_invented(tmp_path):
    """FAIL-CLOSED, as the producer is: a menu writes nothing, and the case
    stays where R-0915-102 puts it (blocking, with its condition named)."""
    proj = _fresh(tmp_path, UNDESIGNATED)
    rec = _early(proj)
    assert rec["emitted"] is False, rec
    assert not (proj / DECL).exists()
    rc, msg = GATE._evaluate(proj)
    assert rc == 1, (rc, msg)


def test_published_step4_report_names_the_undecided_conditional_reason(tmp_path):
    """The JSON a flow reader receives must retain the computed reason.

    This exercises the actual gate CLI/report writer rather than only the
    private classifier: a menu is fail-closed, and the reader must learn why
    the M case stayed blocking.
    """
    proj = _fresh(tmp_path, UNDESIGNATED)
    report = tmp_path / "reports" / "step4.json"
    assert GATE.main([str(proj), "--json", str(report)]) == 1
    published = json.loads(report.read_text(encoding="utf-8"))
    assert published["verdict"] == "FAIL"
    assert COND["name"] in published["message"]
    assert "applies only if the design selects option 'M'" in published["message"]
    assert "declares no selection at this point" in published["message"]


def test_main_emits_before_step4_reads_it():
    """Wiring, read off the AST: the early emission precedes Step 4 in main,
    and row 33 is checked against it."""
    src = (_PROGRAMS / "design_one_shot_runner.py").read_text()
    main = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    lines = {}
    for n in ast.walk(main):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            lines.setdefault(n.func.id, []).append(n.lineno)
    assert "declaration_before_step4" in lines, sorted(lines)[:40]
    assert (min(lines["declaration_before_step4"])
            < min(lines["step_step4_functional_evidence"]))
    assert "_refuse_a_disagreeing_declaration" in lines


# ---- the agreement between the two emissions -------------------------------
#: A tree without the check leaves row 33 exactly as the producer returned it.
_refuse = getattr(D, "_refuse_a_disagreeing_declaration",
                  lambda row, _early, _project: row)

def _row():
    return D.StepResult("arith_declaration_emit", "PASS", 0.1,
                        "spec_declaration_emit emitted plugin_output/"
                        "declaration.json", [DECL])


def _late(proj: Path, fields: dict) -> None:
    (proj / DECL).parent.mkdir(parents=True, exist_ok=True)
    (proj / DECL).write_text(json.dumps(fields))


def test_a_later_emission_that_changes_a_field_refuses_by_name(tmp_path):
    early = {"emitted": True, "fields": {"isa_extensions": ["I", "Zifencei"]}}
    _late(tmp_path, {"isa_extensions": ["I", "M", "Zifencei"]})
    row = _refuse(_row(), early, tmp_path)
    assert row.status == "FAIL", row
    assert "DECLARATION_DISAGREES_WITH_STEP4" in row.detail
    assert "isa_extensions" in row.detail


def test_a_later_emission_that_drops_a_field_refuses(tmp_path):
    early = {"emitted": True, "fields": {"isa_extensions": ["I"],
                                         "top_module": "t"}}
    _late(tmp_path, {"isa_extensions": ["I"]})
    row = _refuse(_row(), early, tmp_path)
    assert row.status == "FAIL" and "top_module: dropped" in row.detail


def test_an_identical_or_superset_later_emission_stands(tmp_path):
    early = {"emitted": True, "fields": {"isa_extensions": ["I"]}}
    _late(tmp_path, {"isa_extensions": ["I"], "top_module": "t"})
    row = _refuse(_row(), early, tmp_path)
    assert row.status == "PASS" and "superset" in row.detail


def test_no_early_emission_leaves_row_33_as_it_was(tmp_path):
    row = _refuse(_row(), {"emitted": False},
                                              tmp_path)
    assert row.status == "PASS" and "superset" not in row.detail


# #3112: neutral producer/currentness control, not extraction or AI approval.
def _current_interface_project(tmp_path, monkeypatch):
    import execution_policy
    import spec_declaration_emit as emit
    import phase1_expert_parse_track as expert
    from _staged_top_module import refresh_selected_interface
    from test_d2_renamed_reused_ip_ports_take_their_documents_side import _selected_project
    for key in (execution_policy.ENV, execution_policy._CAPABILITY_FD_ENV,
                'VIBEIC_EXECUTION_AUTH_SOCKET'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(execution_policy, '_ordinary_runtime', None)
    project = _selected_project(tmp_path)
    doc = project / 'input/docs/L3_external_interface.md'
    doc.write_text(doc.read_text() + '''\nThe Plugin MUST declare `plugin_output/declaration.json` before authoring:\n
| Field | Required | Example |
|---|---|---|
| `top_module` | Yes | `"core"` (primary) |
| `memory_interface_protocol` | Yes | `"split_read_write"` (primary) |
''')
    (project / 'input/docs/block.md').write_text(
        'The converter digitises to 12 bits and accepts REFHI as its external reference.\n')
    (project / 'phase1/generated_docs/L1_DATASHEET.json').write_text(json.dumps({
        'doc_id': 'L1', 'fields': {'resolution_bits': 12}, 'extraction_status': 'EXTRACTED'}))
    assert emit.main([str(project), '--set', 'top_module=core',
                      '--set', 'memory_interface_protocol=split_read_write']) == 0
    l9 = project / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json'
    refresh_selected_interface(project, l9, json.loads(l9.read_text()))
    # Existing two-pass reader protocol; authored expectation is SOURCE_CONTROL,
    # never an AI judgement/signature or certified extraction claim.
    assert expert.main([str(project), '--invoked-by', 'neutral-prior-producer']) == 4
    import _path_layout as paths
    answer = paths.report_path(project, 'phase1/expert_parse_track.json').parent / 'expert_parse_track_pack/l_doc_expectations.json'
    answer.write_text(json.dumps({'expectations': [{
        'id': 'neutral-selected-output', 'layer': 'L9_INTERFACE', 'field_path': 'top_ports',
        'requirement': 'the neutral current selected output is retained',
        'expected_tokens': ['o_memory_wdata'], 'evidence': ['neutral authored source-control input']}]}))
    assert expert.main([str(project), '--invoked-by', 'neutral-prior-producer']) == 0
    assert expert.check_report(project) == 0
    return project


def _current_interface_values(project):
    import hashlib
    import phase1_expert_parse_track as expert
    import ai_signed_judgement as judgement
    binding = json.loads((project / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json').read_text())['selected_interface_binding']
    return {'refs': {key: binding[key + '_sha256'] == hashlib.sha256(
        (project / binding[key]).read_bytes()).hexdigest()
        for key in ('declaration', 'declaration_provenance', 'selected_source')},
        'expert_check_rc': expert.check_report(project),
        'D1_credit': judgement.check(project, 'D1')[0],
        'D1_prior_rejection_current': judgement.check(project, 'D1', expected_verdict='FAIL')[0]}


import pytest


@pytest.mark.parametrize('change', ['late_provenance', 'selected_source'])
def test_late_native_declaration_closes_current_interface_before_audit(tmp_path, monkeypatch, change):
    import hashlib
    import subprocess
    import ai_signed_judgement as judgement
    import _path_layout as paths
    project = _current_interface_project(tmp_path, monkeypatch)
    prior_report = paths.report_path(project, 'phase1/expert_parse_track.json').read_bytes()
    receipt = project / 'reports/audit/ai_judgements/D1.json'
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({'schema': judgement.SCHEMA, 'step_id': 'D1',
        'evidence_sha256': (judgement.evidence_sha256(project, 'D1') if change == 'selected_source'
                            else hashlib.sha256(b'SOURCE_CONTROL prior different evidence').hexdigest()),
        'verdict': 'FAIL',
        'signed_by': 'SOURCE_CONTROL prior rejection fixture',
        'judgement': 'Prior synthetic rejection only; no AI approval is credited.'}))
    prior_signature = receipt.read_bytes()
    l9 = project / 'phase1/generated_docs/L9_INTEGRATION_SPEC.json'
    source = project / json.loads(l9.read_text())['selected_interface_binding']['selected_source']
    if change == 'selected_source':
        source.write_bytes(source.read_bytes() + b'\n// current neutral source derivation\n')
    late = D.step_arith_declaration_emit(project)
    assert late.status == 'PASS', late
    before = _current_interface_values(project)
    calls = []
    native_run = D.subprocess.run
    def transport(argv, *args, **kwargs):
        if len(argv) > 1 and Path(argv[1]).name == 'flow_declared_producer_run.py':
            calls.append('SOURCE_CONTROL unrelated declared sweep')
            return subprocess.CompletedProcess(argv, 0, '', '')
        return native_run(argv, *args, **kwargs)
    monkeypatch.setattr(D.subprocess, 'run', transport)
    monkeypatch.setattr(D._pl, 'emit_final_summary', lambda *args: None)
    def strict_currentness_audit(project, **kwargs):
        values = _current_interface_values(project)
        calls.append('actual refs/expert/D1 consumers')
        ready = all(values['refs'].values()) and values['expert_check_rc'] == 0
        return D.StepResult('final_audit', 'NOT_MEASURED' if ready else 'FAIL', 0,
                            'Existing D1 signed handoff remains required', extras=values,
                            reason_class='awaiting_signed_judgement' if ready else '')
    monkeypatch.setattr(D, 'step_final_audit', strict_currentness_audit)
    row = D._audit_after_declared_producers(project, True)
    after = _current_interface_values(project)
    saved = {'scope': 'SOURCE_CONTROL unrelated sweep/audit envelope; real declaration/interface/expert/D1 readers',
             'change': change, 'before': before, 'after': after, 'audit_status': row.status, 'calls': calls,
             'signature_preserved': receipt.read_bytes() == prior_signature,
             'prior_report_sha256': hashlib.sha256(prior_report).hexdigest(),
             'prior_report_retained': any(p.read_bytes() == prior_report
                 for p in (project / 'steps').rglob('expert_parse_track.json')),
             'prior_report_unchanged': paths.report_path(project, 'phase1/expert_parse_track.json').read_bytes() == prior_report,
             'original_input_sha256': {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (project / 'input/docs').glob('*.md')}}
    (tmp_path / 'current_interface_observed.json').write_text(json.dumps(saved, indent=2) + '\n')
    assert all(after['refs'].values()), saved
    assert after['expert_check_rc'] == 0, saved
    assert after['D1_credit'] is False and after['D1_prior_rejection_current'] is False, saved
    assert row.status == 'NOT_MEASURED', saved
    assert saved['signature_preserved'], saved
    assert saved['prior_report_retained'] or saved['prior_report_unchanged'], saved
    if change == 'selected_source':
        assert saved['prior_report_retained'] and not saved['prior_report_unchanged'], saved


def test_identical_declaration_replay_preserves_published_sidecar_bytes(tmp_path, monkeypatch):
    import spec_declaration_emit as emit
    project = _current_interface_project(tmp_path, monkeypatch)
    # Automatic continuation carries a prior choice. A new explicit --set is
    # a fresh author observation and legitimately changes first_recorded_at.
    argv = [str(project)]
    assert emit.main(argv) == 0
    before = (project / 'plugin_output/declaration.provenance.json').read_bytes()
    assert emit.main(argv) == 0
    after = (project / 'plugin_output/declaration.provenance.json').read_bytes()
    (tmp_path / 'sidecar_replay_observed.json').write_text(json.dumps({
        'unchanged': before == after,
        'only_generated_at_changed': {k: v for k, v in json.loads(before).items() if k != 'generated_at'} ==
                                    {k: v for k, v in json.loads(after).items() if k != 'generated_at'},
        'scope': 'Actual producer same-input byte idempotence; no consumer hash exemption'}) + '\n')
    assert after == before, 'same complete provenance payload rewrote generated_at bytes'
