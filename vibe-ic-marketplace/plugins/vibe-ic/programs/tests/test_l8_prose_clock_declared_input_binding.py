"""The prose clock producer must bind one declared input, not invent its pin."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phase1_doc_one_shot_runner as runner


def emit(tmp_path, pins, text="| Target clock period | 32 ns (31.25 MHz) |\n"):
    docs = tmp_path / 'phase1/generated_docs'
    docs.mkdir(parents=True)
    (docs / 'L1_DATASHEET.json').write_text(json.dumps({'pin_table': pins}))
    runner.gen_l8_timing_waveform(tmp_path, {'product_overview.md': text})
    runner._post_emit_typed_clock_domains(tmp_path)
    return json.loads((docs / 'L8_RTL_CONSTANTS.json').read_text())


@pytest.mark.parametrize('name', ['i_clk', 'clk_i', 'in_clock', 'clk'])
def test_prose_target_binds_the_single_declared_input_clock(tmp_path, name):
    doc = emit(tmp_path, [{'name': name, 'mode': 'input'}])
    domains = doc['clock_domains']
    assert len(domains) == 1
    assert domains[0]['name'] == name
    assert domains[0]['source_pin'] == name
    assert domains[0]['period_ns'] == pytest.approx(32)


def test_output_clock_does_not_displace_the_declared_input(tmp_path):
    doc = emit(tmp_path, [{'name': 'o_clk', 'mode': 'output'},
                          {'name': 'i_clk', 'mode': 'input'}])
    assert doc['clock_domains'][0]['source_pin'] == 'i_clk'


def test_multiple_declared_inputs_do_not_guess_the_prose_target(tmp_path):
    doc = emit(tmp_path, [{'name': 'i_clk0', 'mode': 'input'},
                          {'name': 'i_clk1', 'mode': 'input'}])
    assert doc['clock_domains'][0]['source_pin'] == 'clk'


def test_no_declared_input_keeps_the_existing_unbound_prose_record(tmp_path):
    doc = emit(tmp_path, [{'name': 'data', 'mode': 'input'}])
    assert doc['clock_domains'][0]['source_pin'] == 'clk'


def test_explicit_domain_name_keeps_its_own_identity(tmp_path):
    doc = emit(tmp_path, [{'name': 'i_clk', 'mode': 'input'}],
               'domain clk_aux 31.25 MHz\n')
    assert doc['clock_domains'][0]['name'] == 'clk_aux'
    assert doc['clock_domains'][0]['source_pin'] == 'clk_aux'
