#!/usr/bin/env python3
"""`sparse_fsm_detect` read three modules, judged none of them, and exited 0.

THE FINDING (vibe-ic#2102 row 6), measured by the shipped instrument
====================================================================
`sweep_reach_survey.py --silent-set sweep_silence_register.json`, run the CI way
on 8HD-9 over a pristine clone of 94617408759e in the pinned image (label
0.3.48), refused with exactly one name::

    FAIL: 1 driven sweep(s) are SILENT and named in NEITHER list of
    sweep_silence_register.json:
       sparse_fsm_detect.py  (positional/files)  | {

The survey's corpus is three valid, readable, trivial Verilog modules. The
detector READ all three, applied its rule to none of them — there is no enum,
no localparam group, no sparse-FSM flop and no `fsm_encoding` attribute in an
inverter — and printed the same report, with the same rc 0, that it prints over
a corpus of FSMs it judged and found dense. Handed an EMPTY directory it did
the same again, so its clean exit was reachable without reading anything.

WHAT WAS ADDED, AND WHAT WAS NOT
================================
The rule did not move and no threshold changed. `MIN_STATES` is still 3,
`MIN_HAMMING` is still 3, and every register the detector used to report it
still reports. What was added is the DISCLOSURE the survey looks for: rc 2 plus
the `VACUOUS_PASS:` sentinel when the run entered the sparse/dense decision on
nothing, and a `reach` block in the report saying how many of the files it read
carried a construct to decide about.

The register was NOT touched. `sparse_fsm_detect.py` is in neither
`permitted` nor `known_silent_untriaged`, and adding it to either would have
been a claim about its silence instead of an end to it.

THE DRIFT THIS FILE GUARDS
==========================
`_decision_entries` counts constructs with the same regexes and the same
`MIN_STATES` floor `detect_text` classifies with, but it is a second traversal
and could fall behind the first. The invariant that matters has one direction —
EVIDENCE IMPLIES A DECISION ENTRY — because the failure it prevents is a file
the detector reported a register from being recorded as `not_reached`, which
would put a real finding inside a run that had announced it judged nothing. It
is asserted over a corpus covering every one of the five evidence kinds.

There is NO arm over this repository's own RTL, and that is measured rather
than omitted: 31 tracked files carry an RTL suffix and none of them evidences a
sparse FSM, so such an arm's loop body never ran — it read as coverage of real
code and asserted nothing, including against the unfixed file. The measurement
is asserted directly instead, so the day such RTL lands the test says what to
wire.

chip-AGNOSTIC: SystemVerilog declaration grammar with invented identifiers. No
design, PDK, vendor, process or part literal appears here or can affect the
result.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAMS))

import _vacuous_exit as vx           # noqa: E402
import sparse_fsm_detect as sfd      # noqa: E402

_REGISTER = _PROGRAMS / "sweep_silence_register.json"

# One fixture per evidence kind `detect_text` knows, so the invariant below is
# asserted over the whole rule and not over the one branch that is easy.
_ENUM = """
typedef enum logic [4:0] {
  StIdle = 5'b00000,
  StGo   = 5'b01110,
  StDone = 5'b10101
} probe_state_e;
module probe_enum (input wire clk, output wire done);
  probe_state_e state_q;
  assign done = (state_q == StDone);
endmodule
"""

_MACRO = """
module probe_macro (input wire clk, output wire busy);
  logic [4:0] state_d;
  `PRIM_FLOP_SPARSE_FSM(u_state_regs, state_d, state_q, probe_state_e, StIdle)
  assign busy = |state_q;
endmodule
"""

_INST = """
module probe_inst (input wire clk, output wire busy);
  logic [4:0] nxt;
  prim_sparse_fsm_flop #(.Width(5)) u_flop (
    .clk_i(clk), .state_i(nxt), .state_o(cur)
  );
  assign busy = |nxt;
endmodule
"""

_ATTR = """
module probe_attr (input wire clk, output wire busy);
  (* fsm_encoding = "none" *) logic [4:0] state_q;
  assign busy = |state_q;
endmodule
"""

_LOCALPARAM = """
module probe_localparam (input wire clk, input wire [4:0] sel, output wire hit);
  localparam logic [4:0] S_IDLE = 5'b00000;
  localparam logic [4:0] S_GO   = 5'b01110;
  localparam logic [4:0] S_DONE = 5'b10101;
  always_comb begin
    case (sel)
      S_IDLE: ;
      S_GO:   ;
      default: ;
    endcase
  end
  assign hit = (sel == S_DONE);
endmodule
"""

#: Ordinary RTL. Read in full, decided about not at all — the survey's shape.
_PLAIN = """
module probe_and2 (input wire a, input wire b, output wire y);
  assign y = a & b;
endmodule
"""

_WITH_EVIDENCE = {"enum": _ENUM, "macro": _MACRO, "inst": _INST,
                  "attr": _ATTR, "localparam": _LOCALPARAM}


def _run_cli(*args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(_PROGRAMS / "sparse_fsm_detect.py"),
         *[str(a) for a in args]],
        cwd=str(_PROGRAMS), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)


def _sentinels(proc) -> int:
    return sum(1 for ln in (proc.stdout + "\n" + proc.stderr).splitlines()
               if ln.lstrip().startswith(vx.VACUOUS_STDOUT_SENTINEL))


# ───────────────────── the disclosure, both directions ────────────────────

def test_a_populated_corpus_it_judges_nothing_in_refuses(tmp_path):
    """THE FINDING. Three readable modules, no rule applied to any of them."""
    for i in range(3):
        (tmp_path / f"probe_{i}.v").write_text(_PLAIN, encoding="utf-8")
    proc = _run_cli(*sorted(tmp_path.glob("*.v")))

    assert proc.returncode == vx.RC_VACUOUS, (
        "a corpus this detector read in full and judged none of still exited "
        f"{proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert _sentinels(proc) == 1, (
        "no VACUOUS_PASS sentinel, so an automated reader cannot tell this run "
        f"from a clean one\n{proc.stdout}{proc.stderr}")
    rep = json.loads(proc.stdout)
    assert rep["files_scanned"] == 3, rep
    assert rep["reach"]["targets"] == 3 and rep["reach"]["reached"] == 0, rep
    assert rep["reach"]["not_reached_reasons"], (
        "an unexplained non-reach is indistinguishable from a judged-and-clean "
        f"target: {rep['reach']}")


def test_a_corpus_with_one_fsm_in_it_passes_and_says_nothing_vacuous(tmp_path):
    """DIRECTION 2. One construct reached is a PASS, however thin the sweep."""
    (tmp_path / "a_plain.v").write_text(_PLAIN, encoding="utf-8")
    (tmp_path / "b_fsm.sv").write_text(_ENUM, encoding="utf-8")
    proc = _run_cli("--rtl-dir", tmp_path)

    assert proc.returncode == vx.RC_PASS, f"{proc.stdout}{proc.stderr}"
    assert _sentinels(proc) == 0, (
        "a run that judged something announced that it had judged nothing:\n"
        + proc.stdout + proc.stderr)
    rep = json.loads(proc.stdout)
    assert rep["reach"]["targets"] == 2 and rep["reach"]["reached"] == 1, rep
    assert rep["register_names"] == ["state_q"], rep


def test_an_empty_corpus_refuses_and_says_why(tmp_path):
    """A directory with no RTL in it is not a clean sweep, and must say so with
    a REASON — an unexplained empty corpus reads exactly like a clean one."""
    (tmp_path / "notes.txt").write_text("no RTL here yet\n", encoding="utf-8")
    proc = _run_cli("--rtl-dir", tmp_path)

    assert proc.returncode == vx.RC_VACUOUS, f"{proc.stdout}{proc.stderr}"
    assert _sentinels(proc) == 1, proc.stdout + proc.stderr
    rep = json.loads(proc.stdout)
    assert rep["reach"]["targets"] == 0, rep
    assert rep["reach"]["empty_corpus_reason"], rep


def test_the_report_stays_on_stdout_and_the_sentinel_on_stderr(tmp_path):
    """The consumers parse stdout. The disclosure must not corrupt it."""
    (tmp_path / "probe.v").write_text(_PLAIN, encoding="utf-8")
    proc = _run_cli(tmp_path / "probe.v")
    json.loads(proc.stdout)          # raises if the sentinel leaked into it
    assert vx.VACUOUS_STDOUT_SENTINEL in proc.stderr


# ───────────── the invariant that keeps the two traversals honest ─────────

def test_evidence_implies_a_decision_entry_for_every_evidence_kind(tmp_path):
    """`_decision_entries` may not miss a file `detect_text` finds a register in.

    ONE DIRECTION ONLY, deliberately. A construct counted with no register
    reported is legitimate — a dense enum is entered, measured and rejected —
    so the converse is not asserted. What must never happen is a file whose
    finding is reported inside a run that announced it judged nothing.
    """
    for kind, src in _WITH_EVIDENCE.items():
        found = sfd.detect_text(src, source=kind)
        assert found, f"fixture '{kind}' evidences nothing; it cannot control anything"
        entries = sfd._decision_entries(src)
        assert sum(entries.values()) > 0, (
            f"'{kind}': detect_text reported {len(found)} record(s) and "
            f"_decision_entries counted nothing: {entries}")

    assert sum(sfd._decision_entries(_PLAIN).values()) == 0, \
        "ordinary combinational RTL was counted as a decision entry"
    assert sfd.detect_text(_PLAIN) == []


def test_there_is_no_committed_rtl_corpus_to_control_the_invariant_against():
    """WHY THE TEST ABOVE HAS NO CORPUS ARM — measured, not omitted.

    A corpus arm over this repository's own RTL was written first and then
    removed, because it was GREEN BY NEVER ENTERING ITS BRANCH: 31 files with
    an RTL suffix are tracked here and NONE of them evidences a sparse FSM, so
    the loop body that does the asserting never ran. It read as coverage of
    real code and asserted nothing at all — including against the base file,
    where every other test in this module is red.

    So the measurement is asserted directly instead. The day this repository
    gains RTL that declares a sparse FSM, this test fails and says what to do:
    that file becomes the corpus arm the invariant cannot have today.
    """
    root = _PROGRAMS.parents[3]
    corpus = [p for p in root.rglob("*")
              if p.is_file() and p.suffix in sfd.RTL_SUFFIXES
              and ".git/" not in str(p)]
    assert corpus, "no RTL file at all under the repository root"
    with_evidence = []
    for p in corpus:
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if sfd.detect_text(text, source=str(p)):
            with_evidence.append(str(p.relative_to(root)))
    assert not with_evidence, (
        "this repository now carries RTL that evidences a sparse FSM:\n  "
        + "\n  ".join(sorted(with_evidence))
        + "\nAdd it to test_evidence_implies_a_decision_entry_for_every_"
          "evidence_kind as a REAL-code arm; the invented fixtures were the "
          "only corpus available when this module was written.")


# ───────── the `_NOT_PROSE` claim for the reach counter, falsified ────────
#
# `prose_polarity_consulted_check._NOT_PROSE` classifies
# `sparse_fsm_detect::_decision_entries` as reading a formal grammar rather than
# prose, so it is exempt from consulting the polarity vocabulary — the same
# classification, with the same argument, its sibling `_sparse_enum_types`
# already carries. That is a CLASSIFICATION, not an allowlist, and it has to be
# checkable.
#
# THE DIRECTION HERE IS THE OPPOSITE OF THE USUAL ONE, and it is what makes the
# fixture below the right one. A sentence cannot withdraw a construct from this
# counter; it can only ADD one. A commented-out FSM read as a declaration would
# count 1, and a file the detector judged NOTHING in would be published as
# REACHED — a run that announced it had entered the decision when it had not,
# which is the exact false clean this disclosure exists to end. If a comment can
# move this count, the classification is false and the instruction is to DELETE
# THE ENTRY, never to relax the assertion below.

_COMMENTED_OUT_FSM = """
// The state machine below was REMOVED in this revision and is NOT part of the
// design any more. It is kept here only so the history is readable:
//   typedef enum logic [4:0] {
//     GONE_IDLE = 5'b00000,
//     GONE_RUN  = 5'b01110,
//     GONE_DONE = 5'b10101
//   } gone_state_e;
//   `PRIM_FLOP_SPARSE_FSM(u_gone_regs, gone_d, gone_q, gone_state_e, GONE_IDLE)
//   (* fsm_encoding = "none" *) logic [4:0] gone_q;
module probe_after_removal (input wire a, input wire b, output wire y);
  assign y = a | b;
endmodule
"""


def test_the_not_prose_claim_for_the_reach_counter_is_falsifiable():
    """No sentence reaches these regexes, because the function strips first.

    The claim is NOT that the prose is read and correctly overruled — it is
    that it is never read, so the denial and its absence answer alike.
    """
    denied = sfd._decision_entries(_COMMENTED_OUT_FSM)
    assert sum(denied.values()) == 0, (
        "a comment added a decision entry; the `_NOT_PROSE` entry for "
        "sparse_fsm_detect::_decision_entries claims no prose reaches it, and "
        f"that claim is now false — delete the entry rather than this "
        f"assertion. counted: {denied}")

    # ...and the same text through the whole detector: read in full, judged
    # not at all, and SAYING SO.
    assert sfd.detect_text(_COMMENTED_OUT_FSM) == []


def test_the_reach_counter_strips_comments_itself_not_via_its_callers(tmp_path):
    """The claim must be a property of the FUNCTION, and the CONTROL is the
    same text with the comment markers removed.

    Without the control this test passes for a counter that matches nothing at
    all. With it, the fixture is proved to carry constructs the counter DOES
    see once they are code — so the zero above is the strip working, not an
    inert fixture.
    """
    as_code = "\n".join(
        ln[3:] if ln.startswith("//   ") else ("" if ln.startswith("//") else ln)
        for ln in _COMMENTED_OUT_FSM.splitlines())
    counted = sfd._decision_entries(as_code)
    assert sum(counted.values()) > 0, (
        "the CONTROL does not fire: with the comment markers removed the same "
        f"text still counts nothing, so the zero above proves nothing: {counted}")
    assert counted["enum_state_group"] >= 1, counted

    # And end to end: as code the file is REACHED, as a comment it is not.
    code_file = tmp_path / "as_code.sv"
    code_file.write_text(as_code, encoding="utf-8")
    text_file = tmp_path / "as_comment.sv"
    text_file.write_text(_COMMENTED_OUT_FSM, encoding="utf-8")
    assert sfd.detect_paths([code_file])["reach"]["reached"] == 1
    assert sfd.detect_paths([text_file])["reach"]["reached"] == 0


# ─────────────────────── the register was not used ───────────────────────

def test_this_sweep_is_not_named_in_the_silence_register():
    """The fix is the disclosure, not an entry. If a future change puts this
    program into either list, this test says so rather than letting the row go
    green by being excused."""
    reg = json.loads(_REGISTER.read_text(encoding="utf-8"))
    for key in ("permitted", "known_silent_untriaged"):
        assert "sparse_fsm_detect.py" not in reg.get(key, {}), (
            f"sparse_fsm_detect.py was added to {key}; it discloses rc 2 and "
            f"the VACUOUS_PASS sentinel, so it has no silence to register")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))
