#!/usr/bin/env python3
"""`_log_invocation` records the outputs a CALL SITE declared — and only those.

WHY THIS EXISTS. `provenance_output_hash_completeness_check` enforces
anti-fabrication doctrine rule #2 ("Provenance entries carry SHA256 of every
output"). Measured on two converged spm cells, 37 of 44 `provenance.jsonl`
entries carried no `outputs` at all: every long tool run went through one
recorder that logged command/exit/version/timing and nothing else.

The tempting fix is to scan for files modified during each command's window
and attach whatever moved. That associates a hash with a command that may not
have produced it — a FABRICATED attestation, precisely what this gate exists
to catch. A fabricated audit chain is worse than an incomplete one, because it
is believed. So the caller declares, and the recorder hashes only that.

Direction 1 of every pair below is a case that must record NOTHING.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import phase3_one_shot_runner as R      # noqa: E402


def test_a_declared_and_produced_output_is_hashed(tmp_path):
    (tmp_path / "sub").mkdir()
    f = tmp_path / "sub" / "out.def"
    f.write_bytes(b"HELLO DEF\n")
    got = R._hash_declared_outputs(str(tmp_path), ["sub/out.def"])
    assert got == {
        "sub/out.def": "sha256:" + hashlib.sha256(b"HELLO DEF\n").hexdigest()}


def test_a_declared_but_UNPRODUCED_output_is_omitted(tmp_path):
    """DIRECTION 1. A tool that failed produced nothing.

    Recording a declaration the tool did not honour would be the same
    fabrication one layer down, and the gate's own
    PROVENANCE_OUTPUT_FILE_MISSING would then fire on our own entry.
    """
    assert R._hash_declared_outputs(str(tmp_path), ["never_made.gds"]) == {}


def test_an_output_outside_the_project_is_omitted(tmp_path):
    """DIRECTION 1 — the gate's PROVENANCE_PATH_OUTSIDE_PROJECT rule.

    The audit chain only attests artefacts the project owns.
    """
    outside = tmp_path.parent / "elsewhere.gds"
    outside.write_bytes(b"X")
    try:
        assert R._hash_declared_outputs(str(tmp_path), [str(outside)]) == {}
    finally:
        outside.unlink()


def test_declaring_nothing_records_nothing(tmp_path):
    """DIRECTION 1 — empty is honest."""
    assert R._hash_declared_outputs(str(tmp_path), None) == {}
    assert R._hash_declared_outputs(str(tmp_path), []) == {}


def test_the_hash_is_of_the_real_bytes_not_the_path(tmp_path):
    """A hash that did not read the file would survive a content change."""
    f = tmp_path / "a.gds"
    f.write_bytes(b"first")
    h1 = R._hash_declared_outputs(str(tmp_path), ["a.gds"])["a.gds"]
    f.write_bytes(b"second")
    h2 = R._hash_declared_outputs(str(tmp_path), ["a.gds"])["a.gds"]
    assert h1 != h2
    assert h2 == "sha256:" + hashlib.sha256(b"second").hexdigest()


def test_recorder_emits_no_outputs_key_when_nothing_survives(tmp_path, monkeypatch):
    """The entry must not carry an empty `outputs` — the gate treats an
    empty dict as PROVENANCE_OUTPUTS_MISSING, same as absent."""
    import json
    monkeypatch.setattr(R, "_PROV_SINK", str(tmp_path), raising=False)
    R._log_invocation("openroad -exit x.tcl", 0, 5, marker="pnr",
                      outputs=["did_not_happen.def"])
    line = (tmp_path / "provenance.jsonl").read_text().strip()
    entry = json.loads(line)
    assert "outputs" not in entry, entry


def test_recorder_emits_the_declared_output_when_it_exists(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(R, "_PROV_SINK", str(tmp_path), raising=False)
    (tmp_path / "r.def").write_bytes(b"DEF")
    R._log_invocation("openroad -exit x.tcl", 0, 5, marker="pnr",
                      outputs=["r.def"])
    entry = json.loads((tmp_path / "provenance.jsonl").read_text().strip())
    assert entry["outputs"] == {
        "r.def": "sha256:" + hashlib.sha256(b"DEF").hexdigest()}



def test_default31_erc_keeps_its_native_transcript(tmp_path, monkeypatch):
    """Actual Default31 ERC owner and full readers; native process is neutral.

    Other Step31 instruments return explicit NOT_MEASURED in this ERC-only
    control. It does not qualify their tools or the complete physical row.
    """
    import json
    import shlex
    from dataclasses import asdict
    import execution_backend_producers as backend
    import librelane_contract as ll
    import erc_density_check as erc
    import provenance_output_hash_completeness_check as provenance
    from test_drc_vacuous_pass_check import _write_gds

    calls = []
    projects = {}
    native_transcripts = {}

    def put(root, name, text):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def subject(name):
        project = tmp_path / name
        projects[name] = project
        layout = 'VERSION 5.8 ;\nDESIGN unit_top ;\nEND DESIGN\n'
        for filename in ('unit_top.def', 'routed.def'):
            put(project, 'phase3/stage3/pnr/' + filename, layout)
        put(project, 'phase2/stage2/synth/unit_top_synth.v',
            'module unit_top(input a, output b); assign b = a; endmodule\n')
        put(project, 'phase3/stage3/pnr/constraint.sdc',
            'create_clock -period 10 [get_ports a]\n')
        _write_gds(project / 'phase3/stage3/pnr/unit_top.gds',
                   n_shapes=12, cell='unit_top')
        root = project / 'input/pdk'
        pdk = R.PdkConfig('neutral',
            str(put(project, 'input/pdk/cells.lib',
                    'library(neutral) { time_unit : "1ns"; }\n')),
            str(put(project, 'input/pdk/tech.lef', 'VERSION 5.8 ;\n')),
            str(put(project, 'input/pdk/cells.lef', 'VERSION 5.8 ;\n')),
            None, 'neutral', None)
        # Existing density grammar is an unrelated current ERC prerequisite.
        put(project, 'reports/density.rpt',
            '# OpenROAD filler_placement\nstd-cell row utilization: 14.0%\n')
        monkeypatch.setattr(R, '_detect_pdk', lambda *_: pdk)
        monkeypatch.setattr(ll, 'pdk_root_resolution', lambda *a, **k: {'path': str(root)})
        basis, error = R._layout_basis(project, 'unit_top', pdk, '')
        assert not error, error
        put(project, 'reports/phase3/prestream_gate.json',
            json.dumps({'verdict': 'PASS', 'layout_digest': basis,
                        'scope': 'neutral source-control stream admission'}))
        R._ga.admit_gds(project, project / 'phase3/stage3/pnr/unit_top.gds',
                        basis, R._layout_basis_paths(project, 'unit_top'))
        return project, pdk, root

    def unmeasured(*args, **kwargs):
        return R.StepResult('unrelated_instrument', 'NOT_MEASURED', 0,
                            'Neutral unrelated instrument; ERC-only source control',
                            reason_class=R._V.ReasonClass.NOT_EXECUTED)

    monkeypatch.setattr(R, 'step_drc', unmeasured)
    monkeypatch.setattr(R, 'step_lvs', unmeasured)
    monkeypatch.setattr(R, '_run_declared_signoff_gate', unmeasured)
    monkeypatch.setattr(R, '_to_container_path', lambda path, container: path)

    def native_process(container, command, marker, **kwargs):
        argv = shlex.split(command)
        assert 'openroad' in argv and 'tee' in argv
        log = Path(argv[argv.index('tee') + 1])
        project = next(root for root in projects.values() if log.is_relative_to(root))
        assert marker == str(log.parent / 'erc_unit_top.tcl')
        native = native_transcripts[project.name]
        log.write_bytes(native)
        calls.append({'project': project.name, 'command': command,
                      'marker': marker, 'rc': 0})
        return 0, native.decode(), ''

    monkeypatch.setattr(R._dwd, 'run_docker_supervised', native_process)
    native_transcripts['clean'] = b'0 floating nets\n0 floating pins\nERC_NATIVE_DONE\n'
    project, pdk, root = subject('clean')
    result = backend.produce_signoff(project, {'step_id': '31', 'top': 'unit_top',
        'execution_mode': 'default', 'pdk_name': pdk.name,
        'image_id': 'SOURCE_CONTROL_ONLY', 'pdk_root': str(root), 'container': ''})
    verdict, findings, census = provenance.audit_counted(project, require_outputs_present=True)
    erc_rc = erc.main([str(project), '--json', str(project / 'erc-consumer.json')])
    report = project / 'reports/phase3/erc.rpt'
    log = report.with_suffix('.log')
    entries = [json.loads(line) for line in (project / 'provenance.jsonl').read_text().splitlines()]
    observation = {'scope': 'ERC-only source qualification; no physical Step31 PASS',
        'calls': calls, 'row_result': result, 'provenance_verdict': verdict,
        'findings': [asdict(f) for f in findings], 'census': census,
        'erc_consumer_rc': erc_rc, 'erc_consumer': json.loads((project / 'erc-consumer.json').read_text()),
        'native_sha256': hashlib.sha256(native_transcripts['clean']).hexdigest(),
        'published_log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
        'native_invocations': entries}
    observed = tmp_path / 'DEFAULT31_ERC_OBSERVATION.json'
    observed.write_text(json.dumps(observation, indent=2) + '\n')
    # Base reaches the original caller, native logger and measured mismatch.
    assert verdict == 'PASS', observation
    assert erc_rc == 0 and observation['erc_consumer']['summary']['pass']
    assert observation['erc_consumer']['findings']
    assert result['verdict'] == 'NOT_MEASURED'
    assert len(calls) == 1 and log.read_bytes() == native_transcripts['clean']
    expected = 'sha256:' + hashlib.sha256(log.read_bytes()).hexdigest()
    assert entries[0]['outputs'][str(log.relative_to(project))] == expected
    assert census['declared'] == census['verified_present'] == 1
    log.write_bytes(native_transcripts['clean'] + b'\n')
    changed, changed_findings = provenance.audit(project, require_outputs_present=True)
    assert changed == 'FAIL' and any(f.rule == 'PROVENANCE_HASH_MISMATCH' for f in changed_findings)
    assert erc.main([str(project)]) == 2
    log.unlink()
    missing, missing_findings = provenance.audit(project, require_outputs_present=True)
    assert missing == 'FAIL' and any(f.rule == 'PROVENANCE_OUTPUT_FILE_MISSING' for f in missing_findings)
    assert erc.main([str(project)]) == 2

    # A genuine functional float remains the original measured ERC failure.
    native_transcripts['dirty'] = (b'1 floating nets\n u_logic/unused\n'
                                   b'0 floating pins\nERC_NATIVE_DONE\n')
    dirty, pdk, root = subject('dirty')
    backend.produce_signoff(dirty, {'step_id': '31', 'top': 'unit_top',
        'execution_mode': 'default', 'pdk_name': pdk.name,
        'image_id': 'SOURCE_CONTROL_ONLY', 'pdk_root': str(root), 'container': ''})
    assert erc.main([str(dirty), '--json', str(dirty / 'erc-consumer.json')]) == 1
    dirty_report = json.loads((dirty / 'erc-consumer.json').read_text())
    assert any(f['category'] == 'ERC_DIRTY' for f in dirty_report['findings'])
    raw = json.loads((dirty / 'reports/phase3/erc.json').read_text())
    assert raw['floating_nets'] == 1 and raw['clean'] is False and raw['verdict'] == 'REVIEW'
    observation['inline_refusals'] = {'changed_log': changed, 'missing_log': missing,
        'functional_float': dirty_report, 'raw_float': raw}
    observed.write_text(json.dumps(observation, indent=2) + '\n')
