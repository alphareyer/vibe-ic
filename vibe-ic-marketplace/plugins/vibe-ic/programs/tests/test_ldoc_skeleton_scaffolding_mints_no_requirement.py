"""spec_coverage_check read an emitted L-doc's OWN SCAFFOLDING as design text,
so the plugin's instruction-to-self minted design requirements the design never
stated -- including one minted out of the sentence that states the NEGATION.

MEASURED (subservient x gf180mcuD, plugin 1.21.6, L-docs from
`phase1_post_process.emit_l_doc_skeleton`). The design INPUT states no DFT, no
JTAG and no BIST: `spec_test_debug_extract.extract()` over all 9 staged input
documents returns `[]`, and
`grep -rniE 'jtag|bist|scan[_ ]?(en|in|out|chain)|dft|self-test' input/docs/`
returns 0 matches in all 9. Run over the EMITTED `L20_DFT_SCAN_TOPOLOGY.json`
the same extractor minted THREE requirements, its own `evidence` naming each
source:

  * `jtag_tap`, `bist`  <- `extraction_hints`:
        "Look for scan / DFT / BIST / JTAG sections."
  * `scan_chain`        <- the skeleton's boilerplate `fields.notes`:
        "Spec does not specify DFT/scan topology; this is deferred to
         integration."

while that same document's structured answers read `dft_present: false`,
`jtag_tap: null`, `bist_mbist: []`, `scan_chains: []`. The gate reported the
three as coverage GAPs under its doctrine line "this is OUR gap ... a hidden
scorer derived from the same spec WILL test it" -- directing the author to build
a TAP controller and a BIST engine into a design that has neither, and BLOCKING
under `--strict`.

Fix: `_ldoc_design_text()` removes the emitter's scaffolding keys
(`extraction_hints`, `extraction_status`, `emitted_by`, `_generator`) before an
L-doc's text reaches any requirement extractor, and treats a document that
DECLARES `extraction_status: "NOT_YET_EXTRACTED"` as contributing no design text
at all. Anything that is not a recognisable L-doc JSON passes through unchanged.
chip-AGNOSTIC: L-doc schema keys only, no chip / vendor / SKU literal.

Both directions are asserted here: the phantom requirements must disappear AND a
document that GENUINELY states the same features must still derive them.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))
import spec_coverage_check as S  # noqa: E402

_PROG = _PROGRAMS / "spec_coverage_check.py"

# A prompt with no test/debug vocabulary whatsoever.
_PROMPT = ("The block registers its input on the rising edge of clk and drives "
           "the registered value on q. An active-high synchronous reset clears "
           "q to 0.\n")
_RTL = ("module m(input clk, input rst, input d, output reg q);\n"
        "  always @(posedge clk) if (rst) q <= 1'b0; else q <= d;\n"
        "endmodule\n")
_TB = ("module tb; reg clk=0, rst=1, d=0; wire q; m u(clk,rst,d,q);\n"
       "  initial begin #10 rst=0; #10 d=1; #20 $display(\"errors: 0\"); "
       "$display(\"ALL_TESTS_PASSED\"); $finish; end\n"
       "  always #5 clk=~clk;\nendmodule\n")

# The emitter's skeleton, verbatim in shape: nothing extracted, every structured
# answer says ABSENT, and the only test/debug words on the page are the
# emitter's own hint line and its own negation note.
_SKELETON = {
    "doc_id": "L20",
    "doc_name": "L20_DFT_SCAN_TOPOLOGY",
    "applicability": "APPLICABLE",
    "fields": {
        "scan_chains": [],
        "test_compression": None,
        "bist_mbist": [],
        "jtag_tap": None,
        "dft_present": False,
        "notes": ("Spec does not specify DFT/scan topology; this is deferred "
                  "to integration."),
    },
    "extraction_hints": [
        "Look for scan / DFT / BIST / JTAG sections.",
        "Capture chain count, length, frequency.",
    ],
    "extraction_status": "NOT_YET_EXTRACTED",
    "emitted_by": "phase1_post_process.emit_l_doc_skeleton",
    "extraction_evidence": {},
    "source_documents": [],
}

# The SAME document after a real extraction: the design genuinely states the
# features. This is the negative control -- the fix must not blind the gate.
_EXTRACTED = {
    "doc_id": "L20",
    "doc_name": "L20_DFT_SCAN_TOPOLOGY",
    "applicability": "APPLICABLE",
    "fields": {
        "scan_chains": [{"name": "chain0", "length": 120}],
        "bist_mbist": [{"name": "mbist0"}],
        "jtag_tap": {"present": True},
        "dft_present": True,
        "notes": ("The design provides a scan chain with scan_en, scan_in and "
                  "scan_out, a JTAG TAP controller driven by TCK/TMS/TDI/TDO, "
                  "and a BIST controller."),
    },
    "extraction_hints": ["Look for scan / DFT / BIST / JTAG sections."],
    "extraction_status": "EXTRACTED",
    "emitted_by": "phase1_post_process.emit_l_doc_skeleton",
}

_TESTDEBUG_KINDS = {"scan_chain", "jtag_tap", "bist", "test_mode"}


def _run(tmp_path, ldoc_obj):
    d = tmp_path / "gd"
    d.mkdir(exist_ok=True)
    (d / "L20_DFT_SCAN_TOPOLOGY.json").write_text(
        json.dumps(ldoc_obj, ensure_ascii=False), encoding="utf-8")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text(_PROMPT, encoding="utf-8")
    rtl = tmp_path / "m.v"
    rtl.write_text(_RTL, encoding="utf-8")
    tb = tmp_path / "tb.v"
    tb.write_text(_TB, encoding="utf-8")
    report = tmp_path / "r.json"
    subprocess.run(
        [sys.executable, str(_PROG), "--prompt", str(prompt),
         "--ldocs", str(d), "--rtl", str(rtl), "--tb", str(tb),
         "--json", str(report)],
        capture_output=True, text=True, check=False)
    return json.loads(report.read_text(encoding="utf-8"))


def _kinds(report):
    return {it.get("kind") for it in report.get("items") or []}


# ── the defect: scaffolding must mint nothing ────────────────────────────────
def test_unextracted_skeleton_mints_no_test_debug_requirement(tmp_path):
    """The emitter's hint line and its own negation note are not design text."""
    got = _kinds(_run(tmp_path, _SKELETON)) & _TESTDEBUG_KINDS
    assert got == set(), (
        "an L-doc SKELETON that declares dft_present=false / jtag_tap=null / "
        "bist_mbist=[] minted test-debug requirement(s) %r out of the emitter's "
        "own scaffolding" % sorted(got))


def test_extraction_hints_mint_nothing_even_when_the_doc_is_extracted(tmp_path):
    """`extraction_hints` is instruction-to-self on EVERY doc, not just a
    skeleton -- an extracted doc that still carries the hint line must not
    derive a requirement from the hint alone."""
    doc = json.loads(json.dumps(_SKELETON))
    doc["extraction_status"] = "PARTIALLY_EXTRACTED"
    doc["fields"]["notes"] = "The block has one clock domain."
    got = _kinds(_run(tmp_path, doc)) & _TESTDEBUG_KINDS
    assert got == set(), (
        "`extraction_hints` minted test-debug requirement(s) %r on an extracted "
        "document" % sorted(got))


# ── the other direction: a real statement must still derive ──────────────────
def test_genuinely_stated_test_infrastructure_still_derives(tmp_path):
    """The control. A fix that simply stopped deriving these would pass the two
    tests above and be worthless; the gate must still fire on a design that
    really does state a scan chain, a TAP and a BIST."""
    got = _kinds(_run(tmp_path, _EXTRACTED)) & _TESTDEBUG_KINDS
    assert {"scan_chain", "jtag_tap", "bist"} <= got, (
        "a document that STATES scan_en/scan_in/scan_out, a JTAG TAP with "
        "TCK/TMS/TDI/TDO and a BIST controller derived only %r" % sorted(got))


# ── unit: the sanitiser never swallows what it does not understand ───────────
def test_non_json_station_text_passes_through_unchanged():
    raw = "# L3 — External Interface\n\n| `i_clk` | 1-bit | input |\n"
    assert S._ldoc_design_text(raw) == raw


def test_json_that_is_not_an_l_doc_passes_through_unchanged():
    raw = json.dumps({"nodes": [], "edges": [], "extraction_hints": ["JTAG"]})
    assert S._ldoc_design_text(raw) == raw


def test_scaffold_keys_are_removed_but_design_fields_are_kept():
    out = S._ldoc_design_text(json.dumps(_EXTRACTED, ensure_ascii=False))
    doc = json.loads(out)
    for key in S._LDOC_SCAFFOLD_KEYS:
        assert key not in doc, "%s survived the sanitiser" % key
    assert doc["fields"]["dft_present"] is True
    assert doc["fields"]["scan_chains"][0]["name"] == "chain0"


def test_unextracted_doc_contributes_no_text_at_all():
    assert S._ldoc_design_text(json.dumps(_SKELETON, ensure_ascii=False)) == ""
