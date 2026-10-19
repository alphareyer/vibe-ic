"""PR 3197 native controls on the stronger published source identity schema.

The PR RTL, expected native verdicts and four-identity assertion are retained.
Only certificate-field assertions are adapted to the published implementation.
"""
import shutil
import sys
from pathlib import Path
import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))
import formal_structural_check as fsc


def _yosys():
    assert shutil.which("yosys"), "Native Yosys is required for this regression."


@pytest.mark.parametrize("main_reset,scratch_event,scratch_body,expected", [
    ("rst", "posedge clk", "if(en) scratch<=2'bxx; else scratch<=d;", fsc.PASS),
    ("!rst", "posedge clk", "if(en) scratch<=2'bxx; else scratch<=d;", fsc.REFUTED),
    ("rst", "posedge clk", "if(rst) scratch<=2'bxx; else scratch<=d;", fsc.NOT_DISCHARGED),
    ("rst", "posedge rst", "if(en) scratch<=2'bxx; else scratch<=d;", fsc.NOT_DISCHARGED),
], ids=["independent", "wrong-polarity", "reset-dependent-x", "reset-clock"])
def test_raw_graph_distinguishes_unrelated_undef_from_reset_data_and_clock(tmp_path, main_reset, scratch_event, scratch_body, expected):
    _yosys()
    rtl=tmp_path/"m.v"
    rtl.write_text("module m(input clk,rst,en,input [1:0] d,output reg [1:0] q,scratch);\n"
        + "always @(posedge clk) if("+main_reset+") q<=0; else q<=d;\n"
        + "always @("+scratch_event+") "+scratch_body+"\nendmodule\n")
    rec=fsc.check([rtl],"m",[{"rule":"reset_polarity","signal":"rst","value":"active_high"}],tmp_path)
    assert rec["verdict"] == expected, rec
    mapping=rec["claims"][0]["reset_control_mapping"]
    assert len(mapping["raw_identities"]) == 4
    if expected in {fsc.PASS,fsc.REFUTED}:
        independent=[r for r in mapping["rows"] if r.get("reset_independence_certificate")]
        assert len(independent) == 2 and all(r["classification"] == "NO_DEPENDENCE" for r in independent)
        assert all(r["reset_independence_certificate"]["roots"] for r in independent)
        assert mapping["query_progress"]["completed"] == mapping["query_progress"]["planned"]
    else:
        assert mapping["unresolved"] and not mapping["complete"]
