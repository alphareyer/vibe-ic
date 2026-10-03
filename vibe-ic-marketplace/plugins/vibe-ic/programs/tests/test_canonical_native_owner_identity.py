"""A native consumer repair must reopen the ordinary canonical admission."""
import importlib.util
import os
from pathlib import Path
import shutil
import sys

import pytest


@pytest.fixture
def admission_case(tmp_path, monkeypatch):
    actual = Path(os.environ.get('VIBEIC_ADMISSION_TEST_PROGRAMS', Path(__file__).parents[1]))
    sys.path.insert(0, str(actual))
    spec = importlib.util.spec_from_file_location('native_owner_admission_under_test', actual / 'canonical_run_admission.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'image_identity', lambda _: 'sha256:79957120d37804ae0d23144307c5c166dcb609831326f19f2edb8729fe807cfe')
    monkeypatch.setattr(module.emit_attestation, 'phase1_provenance', lambda _: {'ran': False})
    project = tmp_path / 'subject'
    (project / 'input/docs').mkdir(parents=True)
    (project / 'input/docs/contract.md').write_text('One clock-sampled transaction interface.\n')
    programs = tmp_path / 'plugin/programs'
    programs.mkdir(parents=True)
    (programs.parent / 'flow').mkdir()
    for name in ['design_one_shot_runner.py', 'phase3_one_shot_runner.py', 'librelane_contract.py']:
        shutil.copyfile(actual / name, programs / name)
    shutil.copyfile(actual.parent / 'flow/phase1_phase2_phase3.yaml', programs.parent / 'flow/phase1_phase2_phase3.yaml')
    return module, project, programs


@pytest.mark.parametrize('span', ['phase2', 'phase3'])
def test_changed_native_owner_reopens_same_canonical_span(admission_case, span):
    module, project, programs = admission_case
    first = module.admit_span(project, span, programs, 'vibeic-eda', {'pdk': 'gf180mcuD'})
    assert first.admitted
    owner = programs / 'librelane_contract.py'
    owner.write_bytes(owner.read_bytes() + b'\n# changed native launch contract\n')
    changed = module.admit_span(project, span, programs, 'vibeic-eda', {'pdk': 'gf180mcuD'})
    assert changed.admitted, changed.reason
    assert changed.identity_sha256 != first.identity_sha256
    repeated = module.admit_span(project, span, programs, 'vibeic-eda', {'pdk': 'gf180mcuD'})
    assert not repeated.admitted and repeated.reason == 'DUPLICATE_NO_NEW_EVIDENCE'


@pytest.mark.parametrize('change_report', [False, True])
def test_unchanged_native_owner_keeps_duplicate_refusal(admission_case, change_report):
    module, project, programs = admission_case
    first = module.admit_span(project, 'phase2', programs, 'vibeic-eda', {'pdk': 'gf180mcuD'})
    assert first.admitted
    if change_report:
        (project / 'reports').mkdir()
        (project / 'reports/result.json').write_text('{"status":"new report bytes"}\n')
    repeated = module.admit_span(project, 'phase2', programs, 'vibeic-eda', {'pdk': 'gf180mcuD'})
    assert not repeated.admitted and repeated.reason == 'DUPLICATE_NO_NEW_EVIDENCE'
    assert repeated.identity_sha256 == first.identity_sha256
