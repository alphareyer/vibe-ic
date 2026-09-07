#!/usr/bin/env python3
"""An oracle adapter counted a module declaration out of a COMMENT.

MEASURED (vibe-ic#2102, row 4)
==============================
`hdl_declaration_scan_strips_comments_check` read residual 160 against a
baseline of 157: three call sites scanned `^[ \t]*module\\s+(\\w+)` over text no
stripper had touched --

    rtllm_oracle_adapter::top_module::_MODULE_DECL_RE(text)
    rtllm_oracle_adapter::golden_candidate::_MODULE_DECL_RE(text)
    verilogeval_oracle_adapter::golden_candidate::_MODULE_DECL_RE(text)

These patterns are anchored `^[ \t]*module`, so the shape that mints a phantom
through them is the BLOCK-COMMENTED-OUT MODULE -- an old implementation left in
the file inside `/* ... */`, whose own header sits at the start of its line.
MEASURED, and it is why every fixture below carries a negative control asserting
the raw text really does mint the phantom: a `//` line comment does NOT match
this particular pattern, and a fixture written with one would pass without the
fix. What the phantom costs is not cosmetic in either adapter, and both
directions are pinned below:

  * VerilogEval REFUSES a golden it can map. `golden_candidate` requires
    `decls == ["RefModule"]` before it will rename; a phantom makes the list
    two long and the adapter raises "declares [...] and will not guess which
    of several modules is the top" -- naming a module the file does not
    contain. The oracle sweep then reports that problem NOT_MEASURED.
  * RTLLM decides the WRONG top, or refuses. In `top_module` a phantom
    defeats the `len(decls) == 1` early return and then joins `roots` (nothing
    instantiates a module that does not exist), so a single-module golden
    raises "cannot decide the top module". In `golden_candidate` the same
    phantom lands in `others` and raises the collision refusal against a
    helper module that is not there.

THE FIX IS ONE BLANKED COPY PER READ, through the shared, offset-preserving
`_hdl_code_text.strip_hdl_comments_and_strings`. It BLANKS rather than deletes,
which is what lets `_slice_module` keep choosing its span on the code and
slicing the ORIGINAL bytes at the same indices.

EVERY FIXTURE HERE CARRIES ITS OWN NEGATIVE CONTROL: each test first asserts
that the RAW text really does mint the phantom (so the fixture is not vacuous),
then asserts the adapter is unmoved by it. A comment-free control pins that
nothing else changed.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rtllm_oracle_adapter as R          # noqa: E402
import verilogeval_oracle_adapter as V    # noqa: E402

VE_ENTRY = {"layout": {"prompt_suffix": "_prompt.txt", "ref_suffix": "_ref.sv"}}
RT_ENTRY = {"layout": {"prompt_filename": "design_description.txt",
                       "ref_glob": "verified_*.v"}}

# A golden whose ONLY extra `module` token is in a comment at line start --
# the shape the scan reads and the shape a real golden's header carries.
VE_REF_WITH_COMMENT = """\
/* The inverting reference below supersedes this one, which is n/a now:
module RefModule_v2 (input a, output y);
  assign y = a;
endmodule
*/
module RefModule (input a, output y);
  assign y = ~a;
endmodule
"""
VE_REF_PLAIN = """\
module RefModule (input a, output y);
  assign y = ~a;
endmodule
"""


@pytest.fixture()
def tmpdir_path():
    d = Path(tempfile.mkdtemp(prefix="oracle_adapter_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# VerilogEval
# --------------------------------------------------------------------------
def test_verilogeval_a_commented_module_does_not_make_the_adapter_refuse(
        tmpdir_path):
    """The golden maps, and the candidate is the ORIGINAL bytes renamed."""
    # NEGATIVE CONTROL FIRST: the raw text really does mint the phantom, so a
    # green below is a statement about the fix and not about the fixture.
    raw = V._MODULE_DECL_RE.findall(VE_REF_WITH_COMMENT)
    assert raw == ["RefModule_v2", "RefModule"], (
        "the fixture no longer mints a phantom declaration; this test would "
        "pass without the fix and must be repaired, not deleted")

    (tmpdir_path / "Prob001_ref.sv").write_text(VE_REF_WITH_COMMENT)
    name, cand, seed = V.golden_candidate(tmpdir_path, "Prob001", VE_ENTRY)
    assert name == "Prob001_sample01.sv"
    assert "module TopModule" in cand
    assert cand == seed
    # The comment survives into the candidate: this adapter renames, it does
    # not rewrite the golden.
    assert "supersedes this one" in cand


def test_verilogeval_control_a_golden_with_no_comment_is_unchanged(
        tmpdir_path):
    """CONTROL — the fix is a no-op on every ordinary golden."""
    (tmpdir_path / "Prob001_ref.sv").write_text(VE_REF_PLAIN)
    name, cand, seed = V.golden_candidate(tmpdir_path, "Prob001", VE_ENTRY)
    assert (name, cand, seed) == (
        "Prob001_sample01.sv",
        VE_REF_PLAIN.replace("RefModule", "TopModule"),
        VE_REF_PLAIN.replace("RefModule", "TopModule"))


def test_verilogeval_a_second_REAL_module_is_still_refused(tmpdir_path):
    """THE REFUSAL MUST SURVIVE. Stripping comments must not also stop the
    adapter refusing a golden that genuinely declares two modules."""
    (tmpdir_path / "Prob001_ref.sv").write_text(
        VE_REF_PLAIN + "\nmodule helper (input a);\nendmodule\n")
    with pytest.raises(ValueError, match="will not guess"):
        V.golden_candidate(tmpdir_path, "Prob001", VE_ENTRY)


# --------------------------------------------------------------------------
# RTLLM — top_module
# --------------------------------------------------------------------------
RT_ONE_MODULE_WITH_COMMENT = """\
/* The 2-stage variant is obsolete and was removed, not translated:
module adder_64bit_v2 (input [63:0] a, output [63:0] s);
  assign s = a + 1;
endmodule
*/
module verified_adder_64bit (input [63:0] a, output [63:0] s);
  assign s = a;
endmodule
"""


def test_rtllm_top_module_ignores_a_commented_declaration():
    """A single-module golden stays single-module."""
    raw = R._MODULE_DECL_RE.findall(RT_ONE_MODULE_WITH_COMMENT)
    assert raw == ["adder_64bit_v2", "verified_adder_64bit"], (
        "the fixture no longer mints a phantom declaration")
    # ...and the phantom really does break the pre-fix path: with two names
    # the `len(decls) == 1` return is gone, no `preferred` name matches, and
    # nothing instantiates a module that does not exist, so BOTH end up in
    # `roots` and the structural rule cannot decide.
    assert R.top_module(RT_ONE_MODULE_WITH_COMMENT,
                        ["adder_pipe_64bit"]) == "verified_adder_64bit"


RT_TWO_MODULES = """\
module helper_lut (input a, output b);
  assign b = a;
endmodule

/* the retired top, no longer built:
module verified_thing_v1 (input a, output b);
  helper_lut u_old (.a(a), .b(b));
endmodule
*/
module verified_thing (input a, output b);
  // helper_lut u_ghost (.a(a), .b(b));
  helper_lut u_real (.a(a), .b(b));
endmodule
"""


def test_rtllm_top_module_structural_rule_reads_only_code():
    """The instantiation half was already stripped; the declaration half is
    now stripped by the SAME copy, so the two cannot disagree."""
    raw = R._MODULE_DECL_RE.findall(RT_TWO_MODULES)
    assert "verified_thing_v1" in raw, (
        "the fixture no longer mints a phantom declaration")
    assert R.top_module(RT_TWO_MODULES, ["nothing_matches"]) == \
        "verified_thing"


# --------------------------------------------------------------------------
# RTLLM — golden_candidate and the stub seed
# --------------------------------------------------------------------------
def _rtllm_design(tmp: Path, leaf: str, golden: str, spec_name: str | None):
    d = tmp / leaf
    d.mkdir(parents=True)
    (d / "verified_x.v").write_text(golden)
    if spec_name is not None:
        (d / "design_description.txt").write_text(
            f"Module name:\n{spec_name}\n")
    return d


RT_GOLDEN_COLLIDING_COMMENT = """\
module verified_thing (input a, output b);
  assign b = a;
endmodule
/* the name this file used to carry, kept for reference; it is n/a now:
module thing (input a, output b);
  assign b = ~a;
endmodule
*/
"""


def test_rtllm_golden_candidate_a_commented_module_is_not_a_collision(
        tmpdir_path):
    """`others` must not name a helper module that does not exist."""
    raw = R._MODULE_DECL_RE.findall(RT_GOLDEN_COLLIDING_COMMENT)
    assert "thing" in raw, "the fixture no longer mints a phantom declaration"

    _rtllm_design(tmpdir_path, "thing", RT_GOLDEN_COLLIDING_COMMENT, "thing")
    name, cand, seed = R.golden_candidate(tmpdir_path, "thing", RT_ENTRY)
    assert name == "thing.v"
    # THE TOP IS THE REAL MODULE, NOT THE PHANTOM. `preferred` is tried
    # against `decls` before the structural rule, so on the raw text the
    # commented-out `module thing` IS the spec's own name and wins outright:
    # the adapter then renames nothing (old == new), and seeds the constant
    # stub from code inside a comment. The two bodies differ (`b = a` against
    # `b = ~a`) precisely so that substitution cannot hide here.
    assert seed.startswith("module thing (")
    assert "assign b = a;" in seed
    assert "assign b = ~a;" not in seed, (
        "the stub was seeded from the commented-out module")
    assert seed.rstrip().endswith("endmodule")
    assert cand.startswith("module thing (input a, output b);")


RT_GOLDEN_COMMENTED_ENDMODULE = """\
module verified_thing (input a, output b);
  assign b = a;
/* the old tail, kept for reference and no longer built:
endmodule
*/
  assign b = a;
endmodule
"""


def test_rtllm_stub_seed_is_not_truncated_by_a_commented_endmodule(
        tmpdir_path):
    """THE OFFSETS ARE THE POINT. The span is chosen on the blanked copy and
    the bytes come from the ORIGINAL, so the seed is the whole module."""
    import re as _re
    assert _re.search(r"^[ \t]*endmodule", RT_GOLDEN_COMMENTED_ENDMODULE,
                      _re.M).start() < \
        RT_GOLDEN_COMMENTED_ENDMODULE.rstrip().rfind("endmodule"), (
        "the fixture no longer carries a commented `endmodule` ahead of the "
        "real one; this test would pass without the fix")
    _rtllm_design(tmpdir_path, "thing", RT_GOLDEN_COMMENTED_ENDMODULE,
                  "thing")
    _name, cand, seed = R.golden_candidate(tmpdir_path, "thing", RT_ENTRY)
    # The seed carries BOTH assigns and the real `endmodule` -- a raw-text
    # search stops at the commented one and seeds a module with no end.
    assert seed.count("assign b = a;") == 2
    assert seed.rstrip().endswith("endmodule")
    # ...and the bytes are the ORIGINAL's, comment included: blanking is not
    # rewriting.
    assert "no longer built" in seed
    assert seed.rstrip() == cand.rstrip()


def test_rtllm_control_a_golden_with_no_comment_is_unchanged(tmpdir_path):
    """CONTROL — the fix is a no-op on every ordinary golden."""
    plain = ("module verified_thing (input a, output b);\n"
             "  assign b = a;\nendmodule\n")
    _rtllm_design(tmpdir_path, "thing", plain, "thing")
    name, cand, seed = R.golden_candidate(tmpdir_path, "thing", RT_ENTRY)
    assert name == "thing.v"
    assert cand == plain.replace("verified_thing", "thing")
    assert seed == cand
