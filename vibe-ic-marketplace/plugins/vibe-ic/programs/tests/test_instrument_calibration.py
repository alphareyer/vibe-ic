#!/usr/bin/env python3
"""R-0915-86 (3) — every instrument is CALIBRATED before it judges.

Both directions, for every pair and for the rule:

  * the KNOWN-POSITIVE of each registered instrument MUST make it fire, with
    the outcome the entry names — and a test that only asserted "fired" would
    pass on an instrument firing for the wrong reason, so the outcome string is
    asserted too;
  * the KNOWN-NEGATIVE MUST leave it silent;
  * each pair is DISCRIMINATING: swapping the two samples must flip the answer,
    which is what separates a real pair from two samples an instrument cannot
    tell apart at all;
  * the RULE refuses a MISCALIBRATED instrument and an unregistered one, and
    `assert_calibrated` is a no-op only for a calibrated one;
  * the RATCHET is RED on a planted uncalibrated instrument in a fixture tree
    and GREEN on this tree;
  * every on-disk sample EXISTS and still carries the shape its provenance
    claims — a fixture whose bytes drifted would make every assertion above a
    statement about some other file.

The one MISCALIBRATED entry on this tree is asserted BY NAME, with its measured
reason, because "n of m are calibrated" is a count and this repo has measured
what a count hides (vibe-ic#900).
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
PLUGIN = PROG.parent
sys.path.insert(0, str(PROG))

import instrument_calibration as C  # noqa: E402


# ── the pairs, both directions ────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(C.INSTRUMENTS))
def test_the_known_positive_makes_it_fire(name):
    """A known-positive that leaves the instrument silent is the defect.

    AND FOR AN INSTRUMENT THIS TREE MEASURES AS BROKEN, THE MEASUREMENT IS
    ASSERTED — never skipped. `miscalibrated_evidence.fires_with` is the EXACT
    wrong answer, recorded at the source beside the pair, so the day the owner's
    fix lands this test goes RED and the evidence must be deleted in that
    commit. A skip would go green through the fix and say nothing; a list in
    this file would have to be hand-fed and would be stale the same day.
    """
    inst = C.INSTRUMENTS[name]
    outcome = inst.judge(inst.positive.artefact())
    ev = inst.miscalibrated_evidence
    if ev is not None and "positive" in ev.failed_sides:
        assert outcome == ev.fires_with, (
            f"{name}: the positive now answers {outcome!r}, not the "
            f"{ev.fires_with!r} its miscalibrated_evidence records.\n"
            f"  If it now FIRES correctly the instrument is FIXED — delete its "
            f"miscalibrated_evidence in the commit that fixed it.\n"
            f"  closed_by: {ev.closed_by}")
        return
    assert outcome is not None, (
        f"{name}: the known-positive did not make it fire, and the entry "
        f"records no miscalibrated_evidence for it.\n"
        f"  provenance: {inst.positive.provenance}")
    assert inst.expect in outcome, (
        f"{name}: fired with {outcome!r}, which does not carry the outcome the "
        f"registry names ({inst.expect!r}) — firing is not the same as firing "
        f"for the right reason")


@pytest.mark.parametrize("name", sorted(C.INSTRUMENTS))
def test_the_known_negative_leaves_it_silent(name):
    inst = C.INSTRUMENTS[name]
    outcome = inst.judge(inst.negative.artefact())
    ev = inst.miscalibrated_evidence
    if ev is not None and "negative" in ev.failed_sides:
        assert outcome == ev.fires_with, (
            f"{name}: the negative now answers {outcome!r}, not the "
            f"{ev.fires_with!r} its miscalibrated_evidence records. "
            f"closed_by: {ev.closed_by}")
        return
    assert outcome is None, (
        f"{name}: the known-negative made it fire with {outcome!r}.\n"
        f"  provenance: {inst.negative.provenance}")


@pytest.mark.parametrize("name", sorted(C.INSTRUMENTS))
def test_the_pair_discriminates(name):
    """The two samples must produce DIFFERENT answers.

    A pair whose halves an instrument cannot tell apart calibrates nothing, and
    both sides can still "hold" — the negative stays silent because the
    instrument never fires on anything. This is the arm that catches that.

    For an entry carrying evidence the expectation INVERTS: not discriminating
    IS the measured state, and the day it starts discriminating this goes red
    and the evidence is deleted.
    """
    inst = C.INSTRUMENTS[name]
    pos = inst.judge(inst.positive.artefact())
    neg = inst.judge(inst.negative.artefact())
    ev = inst.miscalibrated_evidence
    if ev is not None:
        assert pos == neg, (
            f"{name} carries miscalibrated_evidence because its pair does NOT "
            f"discriminate; it now does ({pos!r} vs {neg!r}) — delete or "
            f"re-measure it. closed_by: {ev.closed_by}")
        return
    assert pos != neg, (
        f"{name}: both samples answer {pos!r} — the pair does not discriminate")


# ── the rule, over the MEASURED state — no list of names anywhere ─────────

def test_the_miscalibrated_instrument_is_named_with_its_reason():
    """`check()` MEASURES the state; the registry's evidence must agree with it.

    Both directions, over every entry, with no set typed in this file:
      * an entry carrying `miscalibrated_evidence` must MEASURE as
        MISCALIBRATED, on exactly the sides the evidence names;
      * an entry carrying none must MEASURE as CALIBRATED.
    A landing that breaks an instrument fails the second; a landing that fixes
    one fails the first, and the fix is to delete the evidence in that commit.
    """
    cals = C.check_all()
    for name, cal in sorted(cals.items()):
        ev = C.INSTRUMENTS[name].miscalibrated_evidence
        if ev is None:
            assert cal.state == C.CALIBRATED, (
                f"{name} MEASURES {cal.state} ({cal.detail}) and carries no "
                f"miscalibrated_evidence — either a landing broke it, or its "
                f"evidence was deleted without fixing it")
            continue
        assert cal.state == C.MISCALIBRATED, (
            f"{name} carries miscalibrated_evidence and now MEASURES "
            f"{cal.state} — it is FIXED; delete the evidence in that commit. "
            f"closed_by: {ev.closed_by}")
        assert set(cal.failed_sides) == set(ev.failed_sides), (
            f"{name} fails {sorted(cal.failed_sides)}, evidence says "
            f"{sorted(ev.failed_sides)} — re-measure and re-state it")


def test_an_instrument_carrying_evidence_may_not_judge():
    """THE RULE, proven both directions over the measured state.

    `assert_calibrated` must REFUSE exactly the entries carrying evidence and be
    a no-op for every other one. That is what "an uncalibrated instrument may
    not judge" means at the source: its verdict becomes NOT_MEASURED with
    reason_class `uncalibrated`, never a PASS and never a FAIL.
    """
    refused, allowed = [], []
    for name in sorted(C.INSTRUMENTS):
        try:
            C.assert_calibrated(name)
            allowed.append(name)
        except C.Uncalibrated as exc:
            assert exc.verdict == "NOT_MEASURED", exc.verdict
            assert exc.reason_class == C.UNCALIBRATED, exc.reason_class
            assert exc.instrument == name
            refused.append(name)
    with_ev = sorted(n for n, i in C.INSTRUMENTS.items()
                     if i.miscalibrated_evidence is not None)
    assert refused == with_ev, (refused, with_ev)
    assert allowed == sorted(set(C.INSTRUMENTS) - set(with_ev))
    for name in refused:
        assert C.verdict_for(name) == ("NOT_MEASURED", C.UNCALIBRATED)
    for name in allowed:
        assert C.verdict_for(name) is None


def test_every_evidence_object_says_what_closes_it():
    """An entry that cannot name its own exit is a permanent exemption.

    `closed_by` names the owner and the change whose landing deletes it;
    `instead_of` says what the tree SEES meanwhile. Both are what make this an
    open finding rather than a quiet allowance.
    """
    for name, inst in sorted(C.INSTRUMENTS.items()):
        ev = inst.miscalibrated_evidence
        if ev is None:
            continue
        assert ev.failed_sides and set(ev.failed_sides) <= {"positive",
                                                            "negative"}, ev
        assert len(ev.closed_by) > 80, f"{name}: closed_by is too thin"
        assert len(ev.instead_of) > 40, f"{name}: instead_of is too thin"


def test_no_test_in_this_file_skips_or_xfails():
    """The standing rule, asserted on this file's own source.

    The first draft gated its positive-side assertion on a hand-typed set and
    called `pytest.skip` for the instrument that fails. Two forbidden shapes at
    once — a skip, and a register that must be hand-fed and goes stale the day
    the fix lands with nothing to say so. The registry already MEASURES the
    state; these tests read THAT.
    """
    tree = ast.parse(Path(__file__).read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in ("skip", "xfail", "importorskip"):
            raise AssertionError(
                f"pytest.{n.func.attr} at line {n.lineno} — this file asserts "
                f"the measurement, it never steps around it")
        if isinstance(n, ast.Attribute) and n.attr in ("skipif", "xfail") \
                and isinstance(n.value, ast.Attribute) \
                and n.value.attr == "mark":
            raise AssertionError(f"pytest.mark.{n.attr} at line {n.lineno}")


def test_the_drv_census_still_counts_the_empty_report():
    """Finding (a), pinned as a measurement rather than a sentence.

    The REAL 0-byte report OpenSTA wrote for the no-parasitics session, and the
    REAL 285-byte one it wrote for the same design one `read_spef` later, both
    run through the deck's OWN census text.
    """
    empty = (C.FIXTURES / "drv_no_parasitics_positive.rpt").read_text()
    full = (C.FIXTURES / "drv_with_parasitics_negative.rpt").read_text()
    assert empty == "", "the no-parasitics fixture is no longer the 0-byte file"
    assert full.count("(VIOLATED)") == 2, full

    out_empty = C.run_drv_census(empty)
    out_full = C.run_drv_census(full)
    assert "SDR_DRV_BY_KIND: total=0" in out_empty, out_empty
    assert "SDR_DRV_CENSUS_NOT_MEASURED" not in out_empty, (
        "the census now refuses the empty report — finding (a) is CLOSED and "
        "this test is rewritten to pin the new behaviour, never deleted")
    assert "SDR_DRV_BY_KIND: total=2 max_capacitance=2" in out_full, out_full


def test_the_parasitics_half_of_the_refusal_is_unreachable():
    """Finding (b). `_sdr_par_ok` cannot be 0 at the test that reads it.

    Structural, on the deck's own text: between the clear and the test there is
    a `break` on every path that would leave the flag at 0.
    """
    block = C._drv_census_block()
    clear = block.index("set _sdr_par_ok 0")
    setok = block.index("set _sdr_par_ok 1")
    test = block.index("if {!$_sdr_par_ok")
    assert clear < setok < test, (clear, setok, test)
    between = block[clear:setok]
    assert "break" in between, (
        "there is now a path from the clear to the set that does not break — "
        "the refusal's parasitics half may be reachable; re-measure it")


# ── the rule ──────────────────────────────────────────────────────────────

def test_assert_calibrated_is_a_no_op_for_a_calibrated_instrument():
    C.assert_calibrated("sdf_gate_sim::sdf_annotation_census")


def test_assert_calibrated_refuses_an_instrument_that_is_not_registered():
    with pytest.raises(C.Uncalibrated) as exc:
        C.assert_calibrated("some_program::some_reader")
    assert exc.value.verdict == "NOT_MEASURED"
    assert exc.value.reason_class == C.UNCALIBRATED


def test_verdict_for_answers_the_same_question_the_same_way():
    assert C.verdict_for("sdf_gate_sim::sdf_annotation_census") is None
    assert C.verdict_for("nope::nope") == ("NOT_MEASURED", C.UNCALIBRATED)


def test_the_verdict_it_emits_is_one_of_the_five():
    for name in ("sdf_gate_sim::sdf_annotation_census", "nope::nope"):
        v = C.verdict_for(name)
        if v is not None:
            assert v[0] in C.VERDICTS, v


def test_there_is_no_flag_that_skips_the_rule():
    """R-0915-85: no 'calibration mode'.

    `check()` is memoised, which is a cache; a cache returns the same answer.
    What must not exist is a way to get a DIFFERENT answer — an env var, an
    argument or a module global that makes an uncalibrated instrument judge.
    """
    src = (PROG / "instrument_calibration.py").read_text()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr in ("getenv", "environ"):
            raise AssertionError(
                f"instrument_calibration reads the environment at line "
                f"{n.lineno} — a rule with an env-var escape is not a rule")
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) \
                and n.value.attr == "environ":
            raise AssertionError(f"os.environ[...] at line {n.lineno}")
    fn = next(f for f in ast.walk(tree)
              if isinstance(f, ast.FunctionDef) and f.name == "assert_calibrated")
    assert [a.arg for a in fn.args.args] == ["name"], (
        "assert_calibrated takes something other than the instrument's name — "
        "a second argument is where a skip lives")
    assert not fn.args.kwonlyargs and fn.args.defaults == [], (
        "assert_calibrated grew a keyword or a default; both are places a "
        "caller can ask it to look away")


# ── the ratchet, both directions ──────────────────────────────────────────

_PLANTED = '''\
import re

#: A tool grammar, matched. This is the whole predicate.
_ABORT = ("[ERROR DRT-0305]", "DETAILED_ROUTE_NONFATAL")


def route_is_dead(log_text):
    """An instrument: it reads the router's own words and judges."""
    for marker in _ABORT:
        if marker in log_text:
            return {"status": "FAIL"}
    return {"status": "PASS"}
'''

_PLANTED_PHRASE = '''\
def annotated(transcript):
    """A second shape: a tool TRANSCRIPT grammar with no diagnostic id."""
    return transcript.count("Putting delay") > 0
'''


def _plant(tmp_path: Path, src: str, stem: str = "cal_planted") -> Path:
    root = tmp_path / "tree"
    (root / "programs").mkdir(parents=True)
    (root / "programs" / f"{stem}.py").write_text(src)
    return root


@pytest.mark.parametrize("src,fn", [(_PLANTED, "route_is_dead"),
                                    (_PLANTED_PHRASE, "annotated")])
def test_the_ratchet_is_red_on_a_planted_uncalibrated_instrument(
        tmp_path, src, fn):
    root = _plant(tmp_path, src)
    found = C.scan(root)
    assert f"cal_planted::{fn}" in found, found
    saved = dict(C._UNCALIBRATED_REGISTER)
    try:
        C._UNCALIBRATED_REGISTER.clear()
        rc = C._ratchet_verdict(found, root)
    finally:
        C._UNCALIBRATED_REGISTER.update(saved)
    assert rc == 1, "the ratchet passed over an uncalibrated instrument"


def test_the_ratchet_is_green_on_a_tree_with_no_instrument(tmp_path):
    """The known-negative of the ratchet ITSELF.

    A gate that refused everything would pass the arm above, so this is the
    other half: a program that reads a file and emits a status but never
    applies a tool grammar is NOT an instrument and must not be named.
    """
    root = _plant(tmp_path, (
        "import json\n"
        "def audit(path):\n"
        "    doc = json.loads(open(path).read())\n"
        "    return {'status': 'PASS' if doc.get('ok') else 'FAIL'}\n"))
    assert C.scan(root) == []
    # The declared register names functions in THIS tree, not in the fixture
    # one, so it is emptied for the arm — the register's own direction has its
    # own test below.
    saved = dict(C._UNCALIBRATED_REGISTER)
    try:
        C._UNCALIBRATED_REGISTER.clear()
        assert C._ratchet_verdict(C.scan(root), root) == 0
    finally:
        C._UNCALIBRATED_REGISTER.update(saved)


def test_the_ratchet_is_green_on_this_tree():
    found = C.scan(PLUGIN)
    known = set(C.INSTRUMENTS) | set(C._UNCALIBRATED_REGISTER)
    new = [n for n in found if n not in known]
    assert new == [], (
        f"{len(new)} instrument(s) read tool-emitted grammar and are neither "
        f"calibrated nor declared: {new}")
    assert C._ratchet_verdict(found, PLUGIN) == 0


def test_a_register_entry_that_outlived_its_instrument_is_refused(tmp_path):
    """The other direction of the ratchet: the register may only shrink."""
    root = _plant(tmp_path, _PLANTED)
    saved = dict(C._UNCALIBRATED_REGISTER)
    try:
        C._UNCALIBRATED_REGISTER.clear()
        C._UNCALIBRATED_REGISTER["cal_planted::route_is_dead"] = "declared"
        assert C._ratchet_verdict(C.scan(root), root) == 0
        # the instrument is gone from the tree; the entry is now a finding
        (root / "programs" / "cal_planted.py").write_text("x = 1\n")
        assert C._ratchet_verdict(C.scan(root), root) == 1
    finally:
        C._UNCALIBRATED_REGISTER.clear()
        C._UNCALIBRATED_REGISTER.update(saved)


def test_every_register_entry_still_exists_and_is_still_an_instrument():
    found = set(C.scan(PLUGIN))
    for name in C._UNCALIBRATED_REGISTER:
        assert C._defines_function(PLUGIN, name), (
            f"{name} is declared uncalibrated and does not exist in this tree")
        if name == "magic_illegal_overlap_check::check":
            continue          # registered sibling reads the dump; see the entry
        assert name in found, (
            f"{name} is declared uncalibrated and the scan no longer names it "
            f"— delete the entry in the commit that closed it")


def test_every_register_entry_names_an_owner():
    for name, why in C._UNCALIBRATED_REGISTER.items():
        assert why.split(" ")[0].startswith("ic"), (
            f"{name}'s declaration does not start with an owner lane: {why!r}")
        assert len(why) > 60, (
            f"{name}'s declaration does not say what is missing: {why!r}")


# ── the fixtures are what the provenance says they are ────────────────────

def test_every_on_disk_sample_exists():
    missing = [p.name for p in
               [C.FIXTURES / n for n in (
                   "antenna_abort_positive.log", "antenna_cosmetic_negative.log",
                   "sdf_unannotated_positive.log", "sdf_annotated_negative.log",
                   "drv_no_parasitics_positive.rpt",
                   "drv_with_parasitics_negative.rpt",
                   "lec_proven_negative.json",
                   "magic_overlap_positive.feedback",
                   "magic_overlap_negative.feedback",
                   "route_completed_positive.log", "route_aborted_negative.log",
                   "route_verified_negative.log",
                   "mpw_violating_positive.rpt", "mpw_clean_negative.rpt")]
               if not p.is_file()]
    assert missing == [], missing


def test_the_route_logs_are_the_tools_own_words():
    ok = (C.FIXTURES / "route_completed_positive.log").read_text()
    bad = (C.FIXTURES / "route_aborted_negative.log").read_text()
    assert "[INFO DRT-0702] Post-route verification: 0 violation(s)." in ok
    assert "[INFO DRT-0501] Runtime:" in ok
    assert "[ERROR DRT-0047]" in bad
    assert "DRT-0702" not in bad, (
        "the aborted fixture now carries a verification line — it is no longer "
        "the other half of this pair")


def test_the_sdf_pair_differs_only_in_the_include_typ_flag():
    """The measurement R-0915-75 named, reproduced on a two-cell chain.

    The two SDFs are OpenSTA's own output; the ONLY difference between the arms
    is `-include_typ`, and it is the difference between 3 delays and 0.
    """
    typ = (C.FIXTURES / "cal_include_typ.sdf").read_text()
    notyp = (C.FIXTURES / "cal_no_include_typ.sdf").read_text()
    assert '(PROGRAM "STA")' in typ and '(PROGRAM "STA")' in notyp
    assert "0.028:0.028:0.028" in typ, "the typ arm lost its full triple"
    assert "0.028::0.028" in notyp, "the no-flag arm lost its EMPTY typ field"
    # the transcripts those two produced
    pos = (C.FIXTURES / "sdf_unannotated_positive.log").read_text()
    neg = (C.FIXTURES / "sdf_annotated_negative.log").read_text()
    assert pos.count("Putting delay") == 0, pos.count("Putting delay")
    assert neg.count("Putting delay") == 3, neg.count("Putting delay")


def test_the_magic_negative_is_an_empty_dump_not_a_missing_one():
    """ABSENT IS NOT ZERO — the trap `magic_illegal_overlap_check` names.

    The negative must be a file that EXISTS and is empty, because that is the
    fact magic reports when `feedback count` is 0, and it is a different fact
    from no file at all.
    """
    p = C.FIXTURES / "magic_overlap_negative.feedback"
    assert p.is_file()
    assert p.read_text() == ""
    pos = (C.FIXTURES / "magic_overlap_positive.feedback").read_text()
    assert pos.count("feedback add") == 2, pos
    assert pos.count("Illegal overlap") == 2, pos
    # magic writes its own INTERNAL units back out (scaleFactor 2 on sky130A):
    # `box 20 20 35 40` in, `box 40 40 70 80` out. A hand-typed fixture would
    # carry the numbers that were typed in.
    assert "box 40 40 70 80" in pos, pos


def test_the_lec_positive_speaks_the_producers_own_dialect():
    """The landed R-0915-82 control was first written in a dialect its own
    grader could not read. Every key of the INCONCLUSIVE sample must be a key
    the PRODUCER writes, and the real PASS record is where that list comes
    from."""
    real = json.loads((C.FIXTURES / "lec_proven_negative.json").read_text())
    planted = C._lec_inconclusive_record()
    unknown = [k for k in planted if k not in real]
    assert unknown == [], (
        f"the INCONCLUSIVE sample uses key(s) `lec_run` does not write: "
        f"{unknown}")
    assert real["verdict"] == "PASS" and real["equivalent"] is True
    assert int(real["compared_points"]) > 0, (
        "the PROVEN record compares nothing — it is not a proof")


def test_the_drv_reports_are_one_read_spef_apart():
    empty = (C.FIXTURES / "drv_no_parasitics_positive.rpt").read_text()
    full = (C.FIXTURES / "drv_with_parasitics_negative.rpt").read_text()
    assert empty == ""
    assert "max capacitance" in full and full.count("(VIOLATED)") == 2
    spef = (C.FIXTURES / "cal_chain.spef").read_text()
    assert "*D_NET" in spef and "*CAP" in spef, (
        "the SPEF behind the with-parasitics arm is no longer a SPEF")


def test_the_mpw_pair_is_the_tools_own_table():
    pos = (C.FIXTURES / "mpw_violating_positive.rpt").read_text()
    assert "Required  Actual" in pos and pos.count("(VIOLATED)") == 10, pos[:200]
    assert (C.FIXTURES / "mpw_clean_negative.rpt").read_text() == "", (
        "the clean min-pulse-width arm is no longer OpenSTA's empty report")


def test_the_antenna_markers_are_the_flows_own(monkeypatch):
    """The two fixture logs carry the marker the FLOW prints, not a marker a
    fixture author invented — and the flow really prints it."""
    import phase3_one_shot_runner as R
    marker = R._ANTENNA_REROUTE_MARKER
    cosmetic = R._ANTENNA_COSMETIC_REROUTE_CAUSE
    pos = (C.FIXTURES / "antenna_abort_positive.log").read_text()
    neg = (C.FIXTURES / "antenna_cosmetic_negative.log").read_text()
    assert pos.count(marker) == 2 and neg.count(marker) == 2
    assert neg.count(f"{marker}: {cosmetic}") == 2, (
        "the negative no longer carries ONLY the cosmetic cause")
    assert pos.count(f"{marker}: {cosmetic}") == 1 and "DRT-0305" in pos, (
        "the positive no longer carries a second, real cause beside the "
        "cosmetic one — which is the exact-set rule it exists to test")
    # and the marker is a string the flow actually prints
    tcl = R._antenna_repair_tcl_marker_source() if hasattr(
        R, "_antenna_repair_tcl_marker_source") else None
    if tcl is not None:                                  # pragma: no cover
        assert marker in tcl


def test_no_sample_comes_from_a_design_under_test():
    """§4.05 and the brief's own rule, asserted on the bytes.

    A calibration structure is a two-inverter chain, a four-rectangle layout
    and a 4-bit registered adder. None of the five ICs under test, no golden,
    no oracle and no published cell may appear in a fixture.

    `sha256` is BOTH an IC under test and the digest algorithm every producer
    records, so it is matched only where it is NOT a digest spelling: a bare
    token not followed by `"` (a JSON key) or `:` (a `sha256:<hex>` value) and
    not inside a longer identifier such as `base_script_sha256`. The carve-out
    is written down rather than left as a shorter word list, because a word
    list that quietly dropped the one IC whose name is also an algorithm would
    be a check that cannot see its hardest case.
    """
    import re as _re
    forbidden = ("spm", "subservient", "opentitan", "hawaii_adc", "golden",
                 "oracle", "benchmark-data")
    for p in sorted(C.FIXTURES.iterdir()):
        text = p.read_text(errors="replace").lower()
        hit = [w for w in forbidden if _re.search(rf"(?<![\w-]){_re.escape(w)}", text)]
        assert hit == [], f"{p.name} mentions {hit}"
        design_sha = _re.search(r'(?<![\w])sha256(?![\w"\':])', text)
        assert design_sha is None, (
            f"{p.name} carries `sha256` in a non-digest position at "
            f"{design_sha.start()}")


def test_every_fixture_names_its_own_calibration_structure():
    """The other direction: a fixture must say WHICH structure it came from.

    Asserting the absence of every IC name can only ever be as complete as the
    list; asserting the PRESENCE of the calibration structure's own identity is
    a positive fact about the same file.
    """
    lec = json.loads((C.FIXTURES / "lec_proven_negative.json").read_text())
    assert lec.get("gold") == "cal_lec (RTL)", lec.get("gold")
    assert lec.get("gate") == "cal_lec.v (synth)", lec.get("gate")
    chain = (C.FIXTURES / "cal_chain.v").read_text()
    assert "module cal_chain" in chain and chain.count("inv_2") == 2, chain


def test_no_fixture_carries_a_host_path():
    for p in sorted(C.FIXTURES.iterdir()):
        text = p.read_text(errors="replace")
        assert "/home/" not in text, f"{p.name} carries a host home path"


# ── the CLI ───────────────────────────────────────────────────────────────

def test_the_cli_ratchet_exits_zero_on_this_tree():
    cp = subprocess.run(
        [sys.executable, str(PROG / "instrument_calibration.py"),
         "--ratchet", "--root", str(PLUGIN)],
        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr


def test_the_cli_report_exits_one_while_an_instrument_is_miscalibrated():
    cp = subprocess.run(
        [sys.executable, str(PROG / "instrument_calibration.py"),
         "--report", "--root", str(PLUGIN)],
        capture_output=True, text=True)
    with_ev = [n for n, i in C.INSTRUMENTS.items()
               if i.miscalibrated_evidence is not None]
    assert cp.returncode == (1 if with_ev else 0), cp.stdout + cp.stderr
    for name in with_ev:
        assert name in cp.stdout


def test_the_json_output_carries_both_sides_of_every_pair(tmp_path):
    out = tmp_path / "cal.json"
    subprocess.run(
        [sys.executable, str(PROG / "instrument_calibration.py"),
         "--report", "--root", str(PLUGIN), "--json", str(out)],
        capture_output=True, text=True)
    doc = json.loads(out.read_text())
    assert set(doc["instruments"]) == set(C.INSTRUMENTS)
    for name, row in doc["instruments"].items():
        assert "positive_outcome" in row and "negative_outcome" in row, row


# ── the wiring at the source ──────────────────────────────────────────────

def test_every_calibrated_instrument_calls_the_rule_at_its_own_source():
    """ONE LINE, AT THE DECLARED SITE — no decorator, no wrapper (R-0915-85).

    The site is the instrument's own body unless its entry DECLARES another one
    in `calls_at`, and the declaration has to carry its reason. Structural: the
    call must be a statement inside that function, naming THIS instrument. A
    call elsewhere in the module would be a wrapper by another name, and a call
    naming a different instrument would be a check of somebody else's ruler.
    """
    for name, inst in sorted(C.INSTRUMENTS.items()):
        if inst.miscalibrated_evidence is not None:
            continue          # it may not judge; nothing calls it yet
        module, _, fn = inst.call_site.partition("::")
        tree = ast.parse((PROG / f"{module}.py").read_text(errors="replace"))
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == fn), None)
        assert node is not None, f"{inst.call_site} is gone"
        named = [
            a.value for c in ast.walk(node)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "assert_calibrated"
            for a in c.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        assert name in named, (
            f"{inst.call_site} does not call assert_calibrated({name!r}) "
            f"(found {named})")


def test_a_declared_call_site_says_why_it_is_not_the_instruments_own_body():
    """`calls_at` is the one place this design could hide a wrapper.

    So every entry that sets it must argue for it AT THE SOURCE, in the entry,
    with enough of a reason that a reader can check the claim rather than take
    it. The one that sets it today was forced by a MEASUREMENT: 29 landed tests
    went red because a live-process calibration was reached inside a window
    where `os.listdir` is patched process-wide.
    """
    src = (PROG / "instrument_calibration.py").read_text()
    for name, inst in sorted(C.INSTRUMENTS.items()):
        if inst.calls_at is None:
            continue
        i = src.index(f'calls_at="{inst.calls_at}"')
        window = src[max(0, i - 2200):i]
        assert "MEASURED" in window, (
            f"{name} declares calls_at={inst.calls_at!r} with no measurement "
            f"beside it")


def test_the_wired_set_is_exactly_the_calibrated_set():
    """Derived from the tree, never from a list in this file.

    Every `assert_calibrated("X")` anywhere in `programs/*.py` must name an
    instrument that MEASURES CALIBRATED, and every calibrated instrument must be
    named by one. A newly-calibrated instrument that nobody wired is an
    instrument the rule does not reach.
    """
    wired = set()
    for f in sorted(PROG.glob("*.py")):
        try:
            tree = ast.parse(f.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "assert_calibrated":
                wired |= {a.value for a in n.args
                          if isinstance(a, ast.Constant)
                          and isinstance(a.value, str)}
    calibrated = {n for n, c in C.check_all().items()
                  if c.state == C.CALIBRATED}
    assert wired == calibrated, (
        f"wired but not calibrated: {sorted(wired - calibrated)}; "
        f"calibrated but not wired: {sorted(calibrated - wired)}")


def test_the_reentrancy_guard_admits_only_the_instrument_being_checked():
    """The base case, and the proof it is not an escape hatch.

    Calibrating an instrument RUNS it, so its own `assert_calibrated` must be a
    no-op for the duration — otherwise the check is its own precondition and
    recurses forever. What must NOT happen is that the guard leaks: after any
    check, `_IN_PROGRESS` is empty again, and no other instrument was ever in
    it.
    """
    seen = []
    name = "prose_polarity_consulted_check::scan"
    inst = C.INSTRUMENTS[name]
    original = inst.judge

    def _spy(artefact):
        seen.append(set(C._IN_PROGRESS))
        return original(artefact)

    C._CACHE.pop(name, None)
    object.__setattr__(inst, "judge", _spy)
    try:
        cal = C.check(name)
    finally:
        object.__setattr__(inst, "judge", original)
    assert cal.state == C.CALIBRATED
    assert seen and all(s == {name} for s in seen), seen
    assert C._IN_PROGRESS == set(), C._IN_PROGRESS


def test_nothing_but_check_can_put_a_name_in_the_reentrancy_guard():
    src = (PROG / "instrument_calibration.py").read_text()
    tree = ast.parse(src)
    writers = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "_IN_PROGRESS" \
                and n.func.attr in ("add", "update", "discard", "remove",
                                    "clear"):
            fn = next((f for f in ast.walk(tree)
                       if isinstance(f, ast.FunctionDef)
                       and f.lineno <= n.lineno <= (f.end_lineno or f.lineno)),
                      None)
            writers.add((fn.name if fn else "<module>", n.func.attr))
    assert writers == {("check", "add"), ("check", "discard")}, writers


# ── the predicate's own measurement, re-derived ───────────────────────────

def _clause_populations():
    """All three clauses over ONE parse of each file.

    Parsed once, not once per clause: the first draft walked the 1471-file tree
    four times and cost 40 s of the suite for an answer three passes already had.
    """
    grammar, record, status = [], [], []
    for p in sorted(PROG.glob("*.py")):
        if p.stem.startswith("test_") or p.stem == "instrument_calibration":
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        gn = C._grammar_names(tree)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = f"{p.stem}::{n.name}"
            if C._matches_grammar(n, gn):
                grammar.append(name)
            if C._reads_record_verdict(n):
                record.append(name)
            if C._emits_status(n):
                status.append(name)
    return grammar, record, status


def test_the_predicates_own_measurement_is_re_derived():
    """`scan`'s docstring argues from four populations. They are MEASURED here.

    Not pinned to the integers the docstring quotes — those are a DATED
    OBSERVATION and every landing moves them, which would make this test
    measure the landing schedule. What is pinned is the ARGUMENT: the grammar
    clause is the small one, the record-verdict and status clauses are each an
    order of magnitude larger, and that is why the population is the CHANNEL and
    not the literal reading of "reads a tool artefact AND writes a status".
    """
    grammar, record, status = _clause_populations()
    both = [n for n in grammar if n in set(status)]
    assert len(grammar) < 60, len(grammar)
    assert len(record) > 10 * len(grammar), (len(record), len(grammar))
    assert len(status) > 5 * len(record), (len(status), len(record))
    assert len(both) < len(grammar), (len(both), len(grammar))
    assert sorted(grammar) == C.scan(PLUGIN), (
        "scan() is no longer the grammar clause alone")


def test_adding_the_status_clause_would_drop_the_instrument_r0915_75_is_about():
    """The measurement that decided the predicate, kept as a falsifier.

    `sdf_gate_sim::sdf_annotation_census` returns three counts and lets its
    caller name the verdict. A predicate that also demanded a status word in the
    function's own body would not name it — and it is the instrument that
    published nine PASSes over a simulation with 0 delays annotated.
    """
    tree = ast.parse((PROG / "sdf_gate_sim.py").read_text(errors="replace"))
    gn = C._grammar_names(tree)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "sdf_annotation_census")
    assert C._matches_grammar(fn, gn), "the grammar clause no longer names it"
    assert not C._emits_status(fn), (
        "sdf_annotation_census now emits a status word in its own body — "
        "re-measure the predicate argument in scan()'s docstring")
