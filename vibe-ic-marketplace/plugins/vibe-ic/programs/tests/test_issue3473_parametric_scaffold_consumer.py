"""#3473: consume Phase-1's audited parametric-width provenance.

The source module may be redirected with ``ISSUE3473_PROGRAMS`` so the exact
same assertions run against the parent revision for the bidirectional control.
The real producer artefact is supplied with ``ISSUE3473_PROJECT`` during the
bounded acceptance run; no host path is compiled into the test.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_DEFAULT_PROGRAMS = Path(__file__).resolve().parents[1]
_PROGRAMS = Path(os.environ.get("ISSUE3473_PROGRAMS", _DEFAULT_PROGRAMS))
sys.path.insert(0, str(_PROGRAMS))
scaf = importlib.import_module("phase2_scaffold_gen")


def _document(*, symbolic="word_len-1:0", identifier="word_len",
              parameter_name="word_len", default="32", width=32):
    resolution = {
        "width_symbolic": symbolic,
        "identifier": identifier,
        "parameter_default": default,
        "parameter_default_int": int(default),
        "scope": "same_document_parameters",
        "resolved_width": width,
        "msb": width - 1,
        "lsb": 0,
    }
    return {
        "top_module": "vector_cell",
        "parameters": [{"name": parameter_name, "default": default,
                         "source": "interface.md"}],
        "top_ports": [
            {"name": "clock_i", "direction": "input", "width": 1},
            {"name": "reset_i", "direction": "input", "width": 1},
            {"name": "payload_i", "direction": "input", "width": width,
             "msb": width - 1, "lsb": 0,
             "width_resolution": resolution},
            {"name": "valid_o", "direction": "output", "width": 1},
        ],
    }


def _emit(doc):
    signals = scaf.derive_signals({}, doc)
    return signals, scaf.emit_top_v(doc["top_module"], signals, "vector cell",
                                    parameters=doc.get("parameters"))


def _connection(wrapper: str, port: str) -> str:
    prefix = f".{port}("
    return next(line.strip().rstrip(",") for line in wrapper.splitlines()
                if line.strip().startswith(prefix))


@pytest.mark.parametrize(("reset_name", "expected"), [
    ("rst", ".rst(~PRESETn)"),
    ("rst_n", ".rst_n(PRESETn)"),
])
def test_soc_wrapper_adapts_only_known_reset_polarity(reset_name, expected):
    doc = _document()
    doc["top_ports"][1]["name"] = reset_name
    signals = scaf.derive_signals({}, doc)
    wrapper = scaf.emit_soc_wrap_v(doc["top_module"], signals, [])
    assert _connection(wrapper, reset_name) == expected


def test_soc_wrapper_without_reset_does_not_fabricate_native_reset():
    doc = _document()
    signals = [s for s in scaf.derive_signals({}, doc)
               if not scaf._is_reset_name(s["name"])]
    wrapper = scaf.emit_soc_wrap_v(doc["top_module"], signals, [])
    assert ".reset(" not in wrapper
    assert ".rst(" not in wrapper


def test_real_producer_active_high_reset_is_adapted_to_apb_presetn():
    raw = os.environ.get("ISSUE3473_PROJECT")
    if not raw:
        pytest.skip("set ISSUE3473_PROJECT to the fresh #3473 producer project")
    docs = Path(raw) / "phase1/generated_docs"
    l9 = json.loads((docs / "L9_INTEGRATION_SPEC.json").read_text())
    l17 = json.loads((docs / "L17_CHANNEL_SIGNAL_CATALOG.json").read_text())
    fields = l9.get("fields", l9)
    signals = scaf.derive_signals(l17.get("fields", l17), fields)
    rst, active_low = scaf._detect_rst_port(signals)
    assert (rst, active_low) == ("rst", False)
    wrapper = scaf.emit_soc_wrap_v(fields["top_module"], signals, [])
    assert _connection(wrapper, rst) == ".rst(~PRESETn)"


def test_owned_resolution_parameterizes_top_and_preserves_numeric_consumers():
    doc = _document()
    signals, text = _emit(doc)
    payload = next(s for s in signals if s["name"] == "payload_i")
    assert payload["width"] == 32
    assert payload.get("width_declared") == "word_len-1:0"
    assert "parameter word_len = 32" in text
    assert "input  [word_len-1:0] payload_i" in text
    # The default-only TB consumer has no DUT parameter in its own scope and
    # must retain the producer's audited numeric default, not a free symbol.
    assert "reg  [31:0] payload_i;" in scaf.emit_tb_v("vector_cell", signals)


def test_unhinted_l17_default_is_replaced_by_validated_l9_numeric_width():
    doc = _document()
    l17 = {"channels": [{"name": "payload_i", "direction": "input",
                          # This legacy field is not a supported L17 width
                          # declaration; only a bracketed channel name is.
                          "width": 16}]}
    signals = scaf.derive_signals(l17, doc)
    payload = next(s for s in signals if s["name"] == "payload_i")
    assert payload["width"] == 32
    assert "input  [word_len-1:0] payload_i" in scaf.emit_top_v(
        "vector_cell", signals, "vector cell", parameters=doc["parameters"])
    assert "reg  [31:0] payload_i;" in scaf.emit_tb_v("vector_cell", signals)
    assert "input  [31:0] payload_i," in scaf.emit_soc_wrap_v(
        "vector_cell", signals, [])


def test_explicit_l17_width_disagreement_refuses_before_output_write(tmp_path):
    doc = _document()
    l17 = {"channels": [{"name": "payload_i[7:0]",
                          "direction": "input"}]}
    signals = scaf.derive_signals(l17, doc)
    payload = next(s for s in signals if s["name"] == "payload_i")
    assert payload["width"] is None
    assert "[8] disagree with resolved width 32" in payload["width_declared"]
    target = tmp_path / "top.v"
    with pytest.raises(scaf.UnresolvedPortWidth):
        target.write_text(scaf.emit_top_v(
            "vector_cell", signals, "vector cell",
            parameters=doc["parameters"]))
    assert not target.exists()


def test_owned_offset_range_keeps_default_separate_from_resolved_width():
    doc = _document(symbolic="word_len:0", width=33)
    l17 = {"channels": [{"name": "payload_i[32:0]",
                          "direction": "input"}]}
    signals = scaf.derive_signals(l17, doc)
    payload = next(s for s in signals if s["name"] == "payload_i")
    assert payload["width_declared"] == "word_len:0"
    assert payload["width"] == 33
    assert payload["width_parameter_binding"]["default"] == 32
    assert payload["width_parameter_binding"]["resolved_width"] == 33
    top = scaf.emit_top_v("vector_cell", signals, "vector cell",
                          parameters=doc["parameters"])
    assert "parameter word_len = 32" in top
    assert "input  [word_len:0] payload_i" in top
    assert "reg  [32:0] payload_i;" in scaf.emit_tb_v(
        "vector_cell", signals)
    assert "input  [32:0] payload_i," in scaf.emit_soc_wrap_v(
        "vector_cell", signals, [])


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: d["top_ports"][2]["width_resolution"].update(
        parameter_default_int=16), "stale"),
    (lambda d: d["top_ports"][2]["width_resolution"].update(
        resolved_width=16), "recompute"),
    (lambda d: d["parameters"][0].update(name="WORD_LEN"), "matches 0"),
    (lambda d: d["top_ports"][2]["width_resolution"].update(
        width_symbolic="clog2(word_len)-1:0"), "unsupported"),
])
def test_corrupt_or_unowned_resolution_refuses_instead_of_freezing_32(mutate,
                                                                      reason):
    doc = _document()
    mutate(doc)
    signals = scaf.derive_signals({}, doc)
    payload = next(s for s in signals if s["name"] == "payload_i")
    assert payload["width"] is None
    assert reason in payload["width_declared"]
    with pytest.raises(scaf.UnresolvedPortWidth):
        scaf.emit_top_v("vector_cell", signals, "vector cell",
                        parameters=doc["parameters"])


def test_constant_and_scalar_ports_are_unchanged():
    doc = {"top_module": "plain_cell", "top_ports": [
        {"name": "clock_i", "direction": "input", "width": 1},
        {"name": "data_i", "direction": "input", "width": 12},
        {"name": "done_o", "direction": "output", "width": 1},
    ]}
    signals, text = _emit(doc)
    assert "parameter" not in text
    assert "input  [11:0] data_i" in text
    assert next(s for s in signals if s["name"] == "done_o")["width"] == 1


def test_existing_unresolved_symbolic_form_still_refuses():
    doc = {"top_module": "unknown_cell", "parameters": [], "top_ports": [
        {"name": "data_i", "direction": "input",
         "width": "depth-1:0", "width_symbolic": "depth-1:0"}]}
    signals = scaf.derive_signals({}, doc)
    assert signals[0]["width"] is None
    with pytest.raises(scaf.UnresolvedPortWidth):
        scaf.emit_top_v("unknown_cell", signals, "unknown",
                        parameters=doc["parameters"])


def test_real_producer_metadata_elaborates_default_32_and_overrides_8_16(
        tmp_path):
    raw = os.environ.get("ISSUE3473_PROJECT")
    if not raw:
        pytest.skip("set ISSUE3473_PROJECT to the fresh #3473 producer project")
    project = Path(raw)
    docs = project / "phase1/generated_docs"
    l9 = json.loads((docs / "L9_INTEGRATION_SPEC.json").read_text())
    l17 = json.loads((docs / "L17_CHANNEL_SIGNAL_CATALOG.json").read_text())
    signals = scaf.derive_signals(l17.get("fields", l17), l9.get("fields", l9))
    fields = l9.get("fields", l9)
    text = scaf.emit_top_v(fields["top_module"], signals, fields["ic_name"],
                           parameters=fields["parameters"])
    top = tmp_path / "producer_top.v"
    top.write_text(text)

    iverilog = shutil.which("iverilog")
    vvp = shutil.which("vvp")
    if not iverilog or not vvp:
        pytest.fail("the bounded #3473 acceptance requires exact Icarus tools")
    parameterized = "parameter size = 32" in text
    override8 = "#(.size(8)) " if parameterized else ""
    override16 = "#(.size(16)) " if parameterized else ""
    probe = tmp_path / "probe.v"
    probe.write_text(f"""
module probe;
  reg clk, rst, y;
  reg [31:0] x32; reg [7:0] x8; reg [15:0] x16;
  wire p32, p8, p16;
  spm d32(.clk(clk),.rst(rst),.x(x32),.y(y),.p(p32));
  spm {override8}d8(.clk(clk),.rst(rst),.x(x8),.y(y),.p(p8));
  spm {override16}d16(.clk(clk),.rst(rst),.x(x16),.y(y),.p(p16));
  initial begin
    $display("PORT_BITS default=%0d size8=%0d size16=%0d",
             $bits(d32.x), $bits(d8.x), $bits(d16.x));
    if ($bits(d32.x) != 32 || $bits(d8.x) != 8 || $bits(d16.x) != 16)
      $fatal(1, "parametric port width did not follow size");
    $finish;
  end
endmodule
""")
    image = tmp_path / "probe.vvp"
    compile_cmd = [iverilog, "-g2012", "-s", "probe", "-o", str(image),
                   str(top), str(probe)]
    compiled = subprocess.run(compile_cmd, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run([vvp, str(image)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    import re
    match = re.search(r"PORT_BITS default=(\d+) size8=(\d+) size16=(\d+)",
                      ran.stdout)
    assert match, ran.stdout
    assert tuple(map(int, match.groups())) == (32, 8, 16)
    assert parameterized
    assert "input  [size-1:0] x" in text
