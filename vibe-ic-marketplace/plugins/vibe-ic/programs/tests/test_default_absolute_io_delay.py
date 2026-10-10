"""Default Step 7 must use the absolute I/O budget the input declares."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase3_one_shot_runner as runner


def project(tmp_path, body):
    docs = tmp_path / 'input/docs'
    docs.mkdir(parents=True)
    (docs / 'design.md').write_text('# Timing constraints\n' + body)
    return tmp_path


def test_default_reads_the_declared_absolute_pair(tmp_path):
    case = project(tmp_path, 'Clock period: 100 ns. Input delay: 5 ns. Output delay: 5 ns.\n')
    value, note = runner._declared_io_delay_ns(case, 100)
    assert value == 5
    assert 'VIBEIC_DECLARED_IO_DELAY' in note
    assert 'design.md:2' in note


def test_actual_sdc_uses_the_input_budget(tmp_path):
    case = project(tmp_path, 'Input delay: 5 ns.\nOutput delay: 5 ns.\n')
    deck = runner._build_auto_silicon_sdc(case)
    assert 'set_input_delay  5 -clock' in deck
    assert 'set_output_delay 5 -clock' in deck
    assert 'THIS PLUGIN\'S ASSUMPTION' not in deck


@pytest.mark.parametrize('body,expected', [
    ('I/O delay: 0 ns.\n', 0),
    ('Input delay: 5 ns.\nOutput delay: 5000 ps.\n', 5),
    ('Input delay: 0.005 us.\nOutput delay: 5 ns.\n', 5),
])
def test_absolute_units_reach_the_same_sdc_budget(tmp_path, body, expected):
    value, _ = runner._declared_io_delay_ns(project(tmp_path, body), 100)
    assert value == expected


def test_incompatible_directions_refuse_before_authoring(tmp_path):
    case = project(tmp_path, 'Input delay: 5 ns. Output delay: 8 ns.\n')
    with pytest.raises(ValueError, match='IO_DELAY_DECLARATION_CONFLICT'):
        runner._build_auto_silicon_sdc(case)


def test_retracted_absolute_pair_is_not_a_mandate(tmp_path):
    case = project(tmp_path, 'Input delay: 5 ns no longer applies.\nOutput delay: 5 ns no longer applies.\n')
    value, _ = runner._declared_io_delay_ns(case, 100)
    assert value is None
