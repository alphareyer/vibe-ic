"""A hard-macro view the FLOW ITSELF emits must be a source of the synthesis
that has to resolve it.

MEASURED on u_hawaii_adc × ihp-sg13g2 (plugin v1.20.60, 2026-09-10). The analog
A8 step writes a real Verilog view per block to
``phase3/analog/hardmacro/<block>/<block>.v``, and the digital hard-macro step
writes one to ``phase3/stage4/hardmacro/<name>/<name>.v``. Neither directory was
in ``staged_hardmacro_models``' search set: that helper looked ONLY at
``staged_pdk_roots`` (Phase-1-recorded PDK-local staging, else
``input/pdk_local``). So for a design whose top instantiates an analog
hard-macro, discovery returned ZERO, no ``(* blackbox *)`` stub was ever added
to the source set, and all three consumers of the helper failed on the same
design:

  * ``step_yosys_synth``  -> ``ERROR: Module `\\delta_sigma' referenced in
    module `\\u_hawaii_adc' in cell `\\u_mod6' is not part of the design.``
  * ``reference_tb``      -> iverilog ``Unknown module type: ldo`` /
    ``delta_sigma``, 8 errors during elaboration.
  * ``lec_run``           -> gold build ``unknown module ldo`` (x1) /
    ``delta_sigma`` (x6), no miter built.

Phase 2 therefore FAILed and phase3 never ran. The stub emitter and the three
call sites were all correct and already wired; the DISCOVERY was blind to the
flow's own output directory.

Chip-AGNOSTIC: the rule pinned here is structural — "a hard-macro view this
flow emitted is a source" — and names no design, block, PDK or vendor. The
negative controls keep the helper a no-op for designs that emit no hard-macro
view, and the coexistence case keeps the pre-existing staged-PDK root working
(the emitted roots must EXTEND that set, never replace it).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _hardmacro_stage as hms  # noqa: E402

MACRO = "acme_blk"          # arbitrary; no design/vendor meaning
_VIEW = (
    "module {n}(input vin, input vref, output dout);\n"
    "  assign dout = 1'b0;\n"
    "endmodule\n"
)


def _mk(tmp_path, *, emitted=None, instantiate=True, define_in_rtl=False,
        staged_pdk=False):
    """emitted: relative dir the FLOW writes the macro view into, or None."""
    proj = tmp_path
    rtl = proj / "phase2" / "stage1" / "rtl"
    rtl.mkdir(parents=True)
    body = "module top(input vin, input vref, output d);\n"
    if instantiate:
        body += (f"  {MACRO} u_blk (.vin(vin), .vref(vref), .dout(d));\n")
    body += "endmodule\n"
    if define_in_rtl:
        body += _VIEW.format(n=MACRO)
    (rtl / "top.v").write_text(body)
    if emitted is not None:
        d = proj / emitted
        d.mkdir(parents=True)
        (d / f"{MACRO}.v").write_text(_VIEW.format(n=MACRO))
    if staged_pdk:
        d = proj / "input" / "pdk_local" / "vendorY"
        d.mkdir(parents=True)
        (d / "ram8x8.v").write_text(
            "module ram8x8(input clk, output q);\n  assign q=1'b0;\n"
            "endmodule\n")
        # instantiate it too, so it is genuinely referenced
        t = (rtl / "top.v").read_text().replace(
            "endmodule\n", "  ram8x8 u_ram (.clk(vin), .q());\nendmodule\n", 1)
        (rtl / "top.v").write_text(t)
    return proj


def _rtl(proj):
    return sorted((proj / "phase2" / "stage1" / "rtl").glob("*.v"))


def _names(found):
    return sorted(m["name"] for m in found)


# ---- POSITIVE: the two directories the flow actually writes views into ----

def test_analog_a8_emitted_view_is_discovered(tmp_path):
    """phase3/analog/hardmacro/<block>/<block>.v — the u_hawaii_adc case."""
    proj = _mk(tmp_path, emitted=f"phase3/analog/hardmacro/{MACRO}")
    found = hms.staged_hardmacro_models(proj, _rtl(proj))
    assert _names(found) == [MACRO], (
        "the analog track's own emitted hard-macro view was not discovered, "
        "so no blackbox stub reaches synth/sim/LEC")
    assert found[0]["v"] is not None
    assert found[0]["v"].name == f"{MACRO}.v"


def test_digital_stage4_emitted_view_is_discovered(tmp_path):
    """phase3/stage4/hardmacro/<name>/<name>.v — digital_hardmacro_gen."""
    proj = _mk(tmp_path, emitted=f"phase3/stage4/hardmacro/{MACRO}")
    found = hms.staged_hardmacro_models(proj, _rtl(proj))
    assert _names(found) == [MACRO]


def test_emitted_view_yields_a_blackbox_stub(tmp_path):
    """End of the chain: the discovered view must stub like any other."""
    proj = _mk(tmp_path, emitted=f"phase3/analog/hardmacro/{MACRO}")
    m = hms.staged_hardmacro_models(proj, _rtl(proj))[0]
    stub = hms.emit_blackbox_stub(m["v"], m["name"], tmp_path / "bb")
    text = stub.read_text()
    assert stub.name == f"{MACRO}.bb.v"
    assert f"(* blackbox *)\nmodule {MACRO}" in text


# ---- COEXISTENCE: emitted roots must EXTEND staged_pdk_roots, not replace ----

def test_staged_pdk_root_still_works_alongside_an_emitted_view(tmp_path):
    proj = _mk(tmp_path, emitted=f"phase3/analog/hardmacro/{MACRO}",
               staged_pdk=True)
    assert _names(hms.staged_hardmacro_models(proj, _rtl(proj))) == [
        MACRO, "ram8x8"]


# ---- NEGATIVE CONTROLS: the helper stays a no-op where it should ----

def test_negative_no_emitted_view_and_no_staged_root_is_noop(tmp_path):
    proj = _mk(tmp_path, emitted=None)
    assert hms.staged_hardmacro_models(proj, _rtl(proj)) == []


def test_negative_emitted_view_not_instantiated_is_noop(tmp_path):
    """Emitted but never referenced by the RTL — nothing to resolve."""
    proj = _mk(tmp_path, emitted=f"phase3/analog/hardmacro/{MACRO}",
               instantiate=False)
    assert hms.staged_hardmacro_models(proj, _rtl(proj)) == []


def test_negative_macro_defined_in_rtl_is_not_an_emitted_macro(tmp_path):
    """Authored in rtl/ — already resolvable, must not be blackboxed."""
    proj = _mk(tmp_path, emitted=f"phase3/analog/hardmacro/{MACRO}",
               define_in_rtl=True)
    assert hms.staged_hardmacro_models(proj, _rtl(proj)) == []
