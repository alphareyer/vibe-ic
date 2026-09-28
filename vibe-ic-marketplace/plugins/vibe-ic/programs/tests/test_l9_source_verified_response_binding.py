"""A source-proven response connection travels from L9 to the chip wrapper."""
import json

import design_one_shot_runner as design
import phase1_doc_one_shot_runner as phase1


def _project(tmp_path, *, ack="1'b1", directive=True, prompt_override=None):
    project = tmp_path / "project"
    vendor = project / "input/vendor_rtl/block"
    vendor.mkdir(parents=True)
    (vendor / "dut.sv").write_text(
        "module dut(input logic clk_i, output logic req_o, "
        "input logic [33:0] rsp_i);\n"
        "  assign req_o = clk_i;\nendmodule\n")
    (vendor / "wrap.sv").write_text(
        "module wrap(input logic clk_i);\n  wire req;\n"
        "  dut u_dut (.clk_i(clk_i), .req_o(req), "
        ".rsp_i({req, " + ack + ", 32'h12345678}));\nendmodule\n")
    prompt = prompt_override or ("Use the staged RTL. Tie-off the response interface rsp "
              "as the source wrapper documents.\n" if directive else
              "Use the staged RTL response interface rsp.\n")
    (project / "input/phase1_prompt.md").write_text(prompt)
    rtl = project / "phase2/stage1/rtl"
    rtl.mkdir(parents=True)
    (rtl / "dut.sv").write_bytes((vendor / "dut.sv").read_bytes())
    phase1.gen_l1_datasheet(project, {"phase1_prompt.md": prompt})
    phase1.gen_l9_integration_spec(project, {"phase1_prompt.md": prompt}, {})
    l9 = json.loads((project / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").read_text())
    return project, rtl, l9


def test_source_verified_response_reaches_l9_and_the_wrapper(tmp_path):
    project, rtl, l9 = _project(tmp_path)
    rows = l9["response_bindings"]
    assert len(rows) == 1
    assert rows[0]["input_port"] == "rsp_i"
    assert rows[0]["request_port"] == "req_o"
    assert rows[0]["source"] == "input/vendor_rtl/block/wrap.sv"
    assert rows[0]["ack_literal"] == "1'b1"
    assert rows[0]["entropy"] == "constant_non_random_test_only"

    wrapper = design._autoemit_chip_top_wrapper(project, rtl, "chip_top")
    assert wrapper is not None
    text = wrapper.read_text()
    assert "input logic [33:0] rsp_i" not in text
    assert ".rsp_i({req_o, 1'b1, 32'h12345678})" in text
    receipt = json.loads((rtl / ".chip_top__response_bindings.json").read_text())
    assert receipt["bindings"][0]["source"] == rows[0]["source"]


def test_unproved_or_unrequested_response_does_not_enter_l9(tmp_path):
    _, _, zero_ack = _project(tmp_path / "zero", ack="1'b0")
    assert not zero_ack.get("response_bindings")
    _, _, no_request = _project(tmp_path / "plain", directive=False)
    assert not no_request.get("response_bindings")
    _, _, denied = _project(
        tmp_path / "denied",
        prompt_override="Do not tie-off the response interface rsp.\n")
    assert not denied.get("response_bindings")


def test_consumer_rechecks_source_and_refuses_manifest_disagreement(tmp_path):
    project, rtl, l9 = _project(tmp_path / "source_mutation")
    assert l9["response_bindings"][0]["ack_literal"] == "1'b1"
    source = project / "input/vendor_rtl/block/wrap.sv"
    source.write_text(source.read_text().replace("1'b1", "1'b0"))
    try:
        design._autoemit_chip_top_wrapper(project, rtl, "chip_top")
    except ValueError as exc:
        assert "request-ack plus constant data" in str(exc)
    else:
        raise AssertionError("source changed after L9 and was still trusted")

    project2, rtl2, _ = _project(tmp_path / "manifest_conflict")
    (rtl2 / "SOURCE_MANIFEST.json").write_text(json.dumps({
        "reused_ip": True,
        "response_bindings": [{
            "input_port": "rsp_i", "request_port": "other_o",
            "source": "input/vendor_rtl/block/wrap.sv"}],
    }))
    try:
        design._autoemit_chip_top_wrapper(project2, rtl2, "chip_top")
    except ValueError as exc:
        assert "disagree with L9" in str(exc)
    else:
        raise AssertionError("conflicting manifest overrode L9")

    project3, rtl3, l9 = _project(tmp_path / "l9_claim_mutation")
    l9["response_bindings"][0]["ack_literal"] = "1'b0"
    (project3 / "phase1/generated_docs/L9_INTEGRATION_SPEC.json").write_text(
        json.dumps(l9))
    try:
        design._autoemit_chip_top_wrapper(project3, rtl3, "chip_top")
    except ValueError as exc:
        assert "L9 response binding disagrees with staged source" in str(exc)
    else:
        raise AssertionError("L9's false acknowledgment claim was accepted")
