"""R-0915-97 — equiv_make pairs by NAME, and a mapped memory has two names.

MEASURED, sha256 x sky130A, lane icsha2, run17's own RTL+netlist pair.  Gold's
`memory_map` splits each memory into named WORDS (`\\h_reg[0]`..`\\h_reg[7]`,
`\\w_reg[0]`..`\\w_reg[15]`); the gate netlist carries the same storage as ONE
WIDE VECTOR under the base name (`wire [255:0] \\h_reg[0]`, `wire [511:0]
\\w_reg[0]`).  672 bits of state are therefore unpaired — FREE and unrelated on
the two sides — and every point downstream of them is unprovable: exactly the
481 run17 reported (`dig_reg` 224, `hh/g/f/d/c/b_reg` 32 each, `e_s_reg` 32,
`delta_reg` 32, `e_reg` 1).

It is not a logic difference and not induction weakness: four ladder arms
(`equiv_struct -icells`, `opt_dff -sat` both sides, a carry-save cut-point,
`-seq 66/80`) each stopped at 501-1000 unproven with 0 counterexamples.  With
the words aliased the same pair proves in 75 s — 2209 proven, 0 unproven.

R-0915-97(2), the second half: `opt_dff` folds a flop's feedback leaf into an
enable and leaves the PUBLIC `*_d` name on the mux output, so gold's `clean_d`
is paired against a different function.  Reproduced here: 38/1 unproven as-is,
38/38 proven with those names blacklisted.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import lec_run as L


# --------------------------------------------------------------------------
# deriving the word set — from the two sides' own output, never a literal
# --------------------------------------------------------------------------
_SEL = "\n".join([r"  \h_reg[%d]" % k for k in range(8)]
                 + [r"  \w_reg[%d]" % k for k in range(16)]
                 + [r"  \a_reg", r"  \dig_reg[0]"])
_GATE = ("  wire [255:0] \\h_reg[0] ;\n"
         "  wire [511:0] \\w_reg[0] ;\n"
         "  wire [31:0] \\a_reg ;\n")


def test_the_gold_word_families_are_read_off_the_select_list():
    fam = L.gold_memory_words(_SEL)
    assert {k: len(v) for k, v in fam.items()} == {"h_reg": 8, "w_reg": 16}


def test_a_single_indexed_wire_is_not_a_memory():
    """NEGATIVE CONTROL. `\\dig_reg[0]` alone is an ordinary indexed wire;
    aliasing it would be a rename with no referent."""
    assert "dig_reg" not in L.gold_memory_words(_SEL)
    assert "a_reg" not in L.gold_memory_words(_SEL)


def test_the_gate_wide_vectors_are_read_off_the_netlist():
    assert L.gate_wide_vectors(_GATE) == {"h_reg[0]": 256, "w_reg[0]": 512,
                                          "a_reg": 32}


def test_the_plan_carries_run17s_own_numbers():
    plan, findings = L.memory_word_alias_plan(L.gold_memory_words(_SEL),
                                              L.gate_wide_vectors(_GATE))
    assert findings == []
    assert plan == [{"base": "h_reg", "words": 8, "width": 32, "total": 256},
                    {"base": "w_reg", "words": 16, "width": 32, "total": 512}]


def test_a_gate_that_already_has_per_word_names_gets_nothing():
    """NEGATIVE CONTROL, and the one that matters for every other design: when
    synthesis kept the words, the recipe must be byte-unchanged."""
    gate = dict(L.gate_wide_vectors(_GATE))
    gate.update({f"h_reg[{k}]": 32 for k in range(8)})
    plan, findings = L.memory_word_alias_plan(
        {"h_reg": list(range(8))}, gate)
    assert plan == [] and findings == []
    assert L.memory_word_alias_tcl(plan, "top") == ""


def test_a_width_that_does_not_divide_is_a_finding_not_a_silent_skip():
    plan, findings = L.memory_word_alias_plan({"m": [0, 1, 2]},
                                              {"m[0]": 32})
    assert plan == []
    assert findings and findings[0].startswith("LEC_MEMORY_WIDTH_MISMATCH")


def test_non_contiguous_words_are_a_finding_not_a_silent_skip():
    plan, findings = L.memory_word_alias_plan({"m": [0, 2, 3]},
                                              {"m[0]": 24})
    assert plan == []
    assert findings and findings[0].startswith("LEC_MEMORY_WORDS_NOT_CONTIGUOUS")


def test_the_alias_block_runs_in_the_modules_own_scope():
    """MEASURED on the 4x8 fixture the moment it did not: `rename`/`add`/
    `connect` name objects INSIDE a module, and without the `cd` yosys answers
    "ERROR: Object `\\m[0]' not found!"."""
    tcl = L.memory_word_alias_tcl(
        [{"base": "m", "words": 4, "width": 8, "total": 32}], "mem48")
    lines = [ln for ln in tcl.splitlines() if not ln.startswith("#")]
    assert lines[0] == "cd mem48"
    assert lines[-1] == "cd .."
    assert "rename \\m[0] \\m__lecwide" in lines
    assert "connect -set \\m[1] \\m__lecwide[15:8]" in lines


def test_every_word_is_a_slice_so_the_alias_adds_no_state():
    """The alias can only ever express the identity the gate already holds."""
    tcl = L.memory_word_alias_tcl(
        [{"base": "m", "words": 4, "width": 8, "total": 32}], "t")
    conn = [ln for ln in tcl.splitlines() if ln.startswith("connect")]
    assert len(conn) == 4
    assert conn[0].endswith("\\m__lecwide[7:0]")
    assert conn[3].endswith("\\m__lecwide[31:24]")


# --------------------------------------------------------------------------
# the emitted script
# --------------------------------------------------------------------------
def _script(**kw):
    return L.build_equiv_script(["g.v"], "n.v", "top", None, **kw)


def test_a_design_with_no_mapped_memory_emits_the_script_it_emits_today():
    assert _script() == _script(memory_word_aliases=[])


def test_the_alias_block_lands_on_the_gate_side_before_the_stash():
    s = _script(memory_word_aliases=[{"base": "m", "words": 4, "width": 8,
                                      "total": 32}])
    assert "rename \\m[0] \\m__lecwide" in s
    i_gate = s.index("hierarchy -check -top top")
    assert i_gate < s.index("rename \\m[0]") < s.index("design -stash gate")


# --------------------------------------------------------------------------
# R-0915-97(2) — the opt_dff enable-fold blacklist, EVIDENCE-GATED
#
# BLANKET BLACKLISTING IS WRONG AND THE FULL DESIGN SAYS SO. MEASURED on
# opentitan_aes: all 158 folded D names blacklisted gave 3717 proven / 293
# UNPROVEN against 4025 / 3 for the flow's own script — the removed pairs were
# the intermediate CUT POINTS induction relies on. So the blacklist is a SECOND
# PASS over pass 1's result and is gated on evidence.
# --------------------------------------------------------------------------
_FOLD = (r"Adding EN signal on $auto$ff.cc:266:slice$1 ($_DFF_P_) from module "
         r"aes_reg_status (D = \clean_d, Q = \clean_q)." "\n"
         r"Adding SRST signal on $auto$ff.cc:266:slice$2 ($_DFF_P_) from module "
         r"aes_reg_status (D = \new_d, Q = \new_q)." "\n"
         r"Adding ARST signal on $auto$ff.cc:266:slice$3 ($_DFF_P_) from module "
         r"aes_reg_status (D = \a_d, Q = \a_q)." "\n")


def test_every_fold_line_is_read_with_its_d_and_its_q():
    pairs = L.enable_folded_pairs(_FOLD)
    assert [(p["d"], p["q"]) for p in pairs] == [
        ("clean_d", "clean_q"), ("new_d", "new_q"), ("a_d", "a_q")]
    assert all("Adding" in p["line"] for p in pairs)


def test_the_aes_module_receipt_yields_a_one_name_blacklist():
    """(a) 38/1 -> 38/38 was a ONE-name blacklist, and this is that name."""
    cand = L.blacklist_candidates(_FOLD, unproven=["clean_d"],
                                  proven=["clean_q", "new_q", "a_q"])
    assert [c["d"] for c in cand] == ["clean_d"]
    assert cand[0]["q"] == "clean_q"
    assert "Adding EN signal" in cand[0]["line"]


def test_a_d_whose_q_is_ALSO_unproven_is_not_blacklisted():
    """(b) THE GATE. A real divergence stays red — the register itself is not
    proven, so dropping the net would hide it."""
    assert L.blacklist_candidates(_FOLD, unproven=["clean_d", "clean_q"],
                                  proven=[]) == []


def test_a_proven_point_is_never_blacklisted():
    """Only pass 1's UNPROVEN points are candidates; blacklisting a proven one
    would remove a cut point for nothing."""
    assert L.blacklist_candidates(_FOLD, unproven=[],
                                  proven=["clean_d", "clean_q"]) == []


def test_the_blanket_set_is_not_what_is_emitted():
    """The measured refutation, as a guard: three folds in the log, three
    proven Qs, but only the ONE unproven D is a candidate."""
    assert len(L.enable_folded_pairs(_FOLD)) == 3
    assert len(L.blacklist_candidates(_FOLD, unproven=["clean_d"],
                                      proven=["clean_q", "new_q", "a_q"])) == 1


def test_the_blacklist_never_contains_a_q_name():
    cand = L.blacklist_candidates(_FOLD, unproven=["clean_d", "new_d", "a_d"],
                                  proven=["clean_q", "new_q", "a_q"])
    for rec in cand:
        assert rec["d"] != rec["q"]
        assert not rec["d"].endswith("_q"), rec


def test_a_log_with_no_folding_yields_no_second_pass():
    """(d) no folding lines -> no candidates -> no pass 2 at all."""
    assert L.enable_folded_pairs("Removed 3 unused cells.\nrunning ABC\n") == []
    assert L.blacklist_candidates("no folds here", ["x_d"], ["x_q"]) == []


def test_no_blacklist_means_the_script_is_byte_identical():
    assert _script() == _script(equiv_blacklist_path="")


def test_a_blacklist_path_reaches_equiv_make():
    s = _script(equiv_blacklist_path="/p/bl.txt")
    assert "equiv_make -blacklist /p/bl.txt gold gate" in s


def test_the_selector_is_pure():
    for fn in (L.enable_folded_pairs, L.blacklist_candidates):
        src = inspect.getsource(fn)
        for forbidden in ("open(", "Path(", "subprocess"):
            assert forbidden not in src, (fn.__name__, forbidden)
