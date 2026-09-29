"""Expected execution-policy probe that also runs on the pinned pre-fix tree.

The base tree has no global policy entry point, so this probe observes the
existing native consumer's answer to a policy request. It records its concrete
answer rather than an absent symbol/import failure. On the implementation tree
it exercises the standalone parser. This is not proof of production integration.
"""
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def observed_mode(project, requested):
    if importlib.util.find_spec('execution_modes') is not None:
        import execution_modes
        try:
            return execution_modes.mode(requested)
        except execution_modes.Refusal as exc:
            return 'REFUSED:' + exc.code
    import librelane_contract
    # Proposed policy file only; the old consumer currently ignores it. It
    # continues to read its own direct/librelane/dual switch, which is preserved.
    (project / 'phase3').mkdir()
    (project / 'phase3/execution_policy.json').write_text(json.dumps({'mode': requested}))
    native = librelane_contract.selected_mode(project, '23')
    return {'direct': 'default-mode', 'librelane': 'default-mode', 'dual': 'default-mode'}[native]


def test_expected_unspecified_default(tmp_path):
    assert observed_mode(tmp_path, None) == 'default-mode'


def test_expected_explicit_ultra(tmp_path):
    assert observed_mode(tmp_path, 'ultra-mode') == 'ultra-mode'


def test_expected_invalid_mode_refusal(tmp_path):
    assert observed_mode(tmp_path, 'invalid-mode') == 'REFUSED:INVALID_EXECUTION_MODE'
