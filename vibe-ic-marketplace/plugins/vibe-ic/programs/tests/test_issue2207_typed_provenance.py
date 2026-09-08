"""Typed document processing facts cannot become hardware requirements."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spec_coverage_check as coverage

RTL = '''module split_words(input [15:0] d, output [7:0] lo, hi);
assign lo=d[7:0]; assign hi=d[15:8]; endmodule'''
TB = '''module tb;
reg [15:0] d; wire [7:0] lo,hi;
split_words dut(d,lo,hi);
initial begin d=16'h8123; #1;
if ({hi,lo} !== d) $fatal(1); $display("TEST_PASS"); $finish; end
endmodule'''
PROMPT = 'Split the 16 input bits into two output bytes, preserving every bit.'


@pytest.mark.parametrize('value', [False, True])
@pytest.mark.parametrize('nested', [False, True])
def test_document_provenance_is_not_overflow(value, nested):
    doc = {'interface_prose_provenance': {'selection': 'whole', 'truncated': value}}
    if nested:
        doc = {'layers': [{'ports': {'details': doc}}]}
    result = coverage.run({'user_prompt': PROMPT, 'l_docs': json.dumps(doc)}, RTL, TB, None, True)
    assert result['blocking_gaps'] == 0, result
    assert result['blocked'] is False


def test_requirement_fields_survive_nested_metadata():
    doc = {'layers': [{'provenance': {'truncated': False},
                       'requirements': 'The output is truncated: discard the upper eight input bits.'}]}
    result = coverage.run({'l_docs': json.dumps(doc)}, RTL, TB, None, True)
    assert any(i['kind'] == 'overflow' and i['block_eligible'] and not i['covered']
               for i in result['items'])
    assert result['blocked'] is True


def test_cli_reads_a_directory_of_typed_documents(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    for n in (1, 9):
        (docs / f'L{n}.json').write_text(json.dumps({
            'requirements': PROMPT, 'provenance': {'truncated': True}}))
    rtl, tb, report = [tmp_path / n for n in ('rtl.v', 'tb.v', 'report.json')]
    rtl.write_text(RTL)
    tb.write_text(TB)
    assert coverage.main(['--ldocs', str(docs), '--rtl', str(rtl), '--tb', str(tb),
                          '--strict', '--json', str(report)]) == 0
    assert json.loads(report.read_text())['blocking_gaps'] == 0
