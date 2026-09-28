"""A declared response tie-off must reproduce a staged RTL wrapper binding."""
import json

import design_one_shot_runner as runner


def _project(tmp_path, ack="1'b1"):
    project = tmp_path / "project"
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "dut.sv").write_text("""
module dut(input logic clk_i, output logic req_o,
           input logic [33:0] rsp_i);
  assign req_o = clk_i;
endmodule
""")
    src = project / "input/vendor_rtl/wrap.sv"
    src.parent.mkdir(parents=True)
    src.write_text("""
module wrap(input logic clk_i);
  wire req;
  dut u_dut (.clk_i(clk_i), .req_o(req),
             .rsp_i({req, """ + ack + """, 32'h12345678}));
endmodule
""")
    (rtl / "SOURCE_MANIFEST.json").write_text(json.dumps({
        "reused_ip": True,
        "response_bindings": [{"input_port": "rsp_i", "request_port": "req_o",
                               "source": "input/vendor_rtl/wrap.sv"}],
    }))
    return project, rtl


def test_input_wrapper_response_is_emitted_without_external_response_pad(tmp_path):
    project, rtl = _project(tmp_path)
    emitted = runner._autoemit_chip_top_wrapper(project, rtl, "chip_top")
    assert emitted is not None
    text = emitted.read_text()
    assert "input logic [33:0] rsp_i" not in text
    assert ".rsp_i({req_o, 1'b1, 32'h12345678})" in text
    assert "output" in text and "req_o" in text
    receipt = json.loads((rtl / ".chip_top__response_bindings.json").read_text())
    assert receipt["bindings"][0]["entropy"] == "constant_non_random_test_only"


def test_unasserted_ack_source_is_refused(tmp_path):
    project, rtl = _project(tmp_path, ack="1'b0")
    try:
        runner._autoemit_chip_top_wrapper(project, rtl, "chip_top")
    except ValueError as exc:
        assert "request-ack plus constant data" in str(exc)
    else:
        raise AssertionError("a zero-ack response was accepted")
    assert not (rtl / "chip_top.sv").exists()
