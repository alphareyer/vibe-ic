"""R-0915-56(a) — OpenSTA's `---` placeholder truncated the SI timing JSON.

MEASURED on subservient x gf180mcuD (lane icsub2, r21). The run's own note said
only:

    SI timing-aware: screen errored (Expecting value: line 6151 column 122
    (char 1097728)) — keeping the floating-victim screen (advisory)

and the flow fell back to the conservative floating-victim ENVELOPE — the bound
that fails this design's step 27 by 0.266 ns while the nominal corner holds
+2.5 ns. So the conservative reading became the reading of record because a
producer crashed, and nothing named the artefact.

THE ROOT CAUSE, and it is none of the obvious candidates. `char 1097728` is ONE
PAST THE END of a file of exactly 1097728 bytes — 268 x 4096, a whole number of
4 KiB blocks — ending mid-token on `"slew_rise_max":`. The file is TRUNCATED,
not malformed, and the emitting OpenSTA's own log says why:

    Error: si_timing_subservient.tcl, 63 cannot use non-numeric string "---"
           as left operand of "-"

`report_required` printed `---` where it had no required time; the capturing
regex `([-0-9.eE+]+)` MATCHED it because `-` is inside its own character class;
`$x ne ""` is not a numeric test, so the token passed the guard; and
`expr {$_si_rrmx - $_si_armx}` raised an UNCAUGHT error that aborted the emit
loop before `close`, leaving the unflushed tail on the floor. Had the script
survived, `_si_jnum` would have written the bare token `---` into the JSON as
if it were a number — the same defect twice, on two paths.

Both directions are pinned here: the placeholder is planted, and the deck must
finish, parse, and record that one pin's slack as `null` — which every consumer
already handles, because the emitter's own docstring says an unavailable slack
degrades that pin to advisory.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import si_signoff_timing_aware as M  # noqa: E402
import phase3_one_shot_runner as R   # noqa: E402

tclsh = shutil.which("tclsh")
needs_tclsh = pytest.mark.skipif(tclsh is None, reason="tclsh not installed")

#: OpenSTA reports `---` where it has no value. This is the token, and the
#: walker below hands it back from `report_required` for exactly one pin.
_PLACEHOLDER = "---"

_WALK = r'''
set ::buf ""
proc unknown {args} { return "" }
namespace eval sta {
  proc redirect_string_begin {} { set ::buf "" }
  proc redirect_string_end {} { return $::buf }
}
proc get_pins {args} { return [list pinA pinB] }
proc get_ports {args} { return [list portA] }
proc get_full_name {o} { return "top/$o" }
proc report_arrival {o} { set ::buf "r 1.0:2.0 f 1.1:2.1" }
proc report_slews {o} { set ::buf "^ 0.1:0.2 v 0.1:0.2" }
proc report_required {o} {
  if {$o eq "pinB"} { set ::buf "r PH:PH f PH:PH" } else { set ::buf "r 3.0:4.0 f 3.0:4.0" }
}
if {[catch {source [lindex $argv 0]} e]} { puts "TCL_ERROR: $e"; exit 1 }
puts "TCL_OK"
'''.replace("PH", _PLACEHOLDER)


def _run_deck(tmp_path):
    out_json = tmp_path / "si_timing.json"
    deck = tmp_path / "si_timing.tcl"
    deck.write_text(M.build_opensta_si_tcl(
        "/p/ss.lib", "/p/n.v", "dut", "/p/c.sdc", "/p/x.spef", str(out_json)))
    walk = tmp_path / "walk.tcl"
    walk.write_text(_WALK)
    r = subprocess.run([tclsh, str(walk), str(deck)],
                       capture_output=True, text=True, timeout=120)
    return r, out_json


@needs_tclsh
def test_the_placeholder_does_not_abort_the_emit(tmp_path):
    r, out_json = _run_deck(tmp_path)
    assert "TCL_ERROR" not in r.stdout, r.stdout[-400:]
    assert "TCL_OK" in r.stdout
    assert "SI_TIMING_JSON_EMIT_DONE" in r.stdout, r.stdout[-400:]


@needs_tclsh
def test_the_json_it_leaves_behind_parses(tmp_path):
    """The whole failure was a file that existed, was 1 MB, and did not parse."""
    _r, out_json = _run_deck(tmp_path)
    data = json.loads(out_json.read_text())
    assert len(data["pins"]) == 3, sorted(data["pins"])


@needs_tclsh
def test_the_pin_with_no_required_time_records_null_not_a_token(tmp_path):
    """`null` is the answer the consumers already handle; `---` is not a
    number and must never be written as one."""
    _r, out_json = _run_deck(tmp_path)
    pins = json.loads(out_json.read_text())["pins"]
    assert pins["top/pinB"]["slack_max"] is None
    # and the pin's real measurements are untouched — the placeholder costs
    # this pin its slack, not its arrival windows
    assert pins["top/pinB"]["arr_rise_max"] == 2.0


@needs_tclsh
def test_the_other_pins_keep_their_real_numbers(tmp_path):
    """THE NEGATIVE CONTROL: the guard may not turn every slack into null."""
    _r, out_json = _run_deck(tmp_path)
    pins = json.loads(out_json.read_text())["pins"]
    assert pins["top/pinA"]["slack_max"] == pytest.approx(1.9)
    assert pins["top/portA"]["slack_max"] == pytest.approx(1.9)


def test_the_numeric_guard_is_in_the_emitted_deck():
    deck = M.build_opensta_si_tcl(
        "/p/ss.lib", "/p/n.v", "dut", "/p/c.sdc", "/p/x.spef", "/p/o.json")
    # `ne ""` is not a numeric test and `---` is what it let through
    assert "string is double -strict" in deck
    assert deck.count("string is double -strict") >= 3


# ── the consumer: a file that does not parse is refused BY NAME ───────────

class _Pdk:
    name = "gf180mcuD"
    liberty = "/pdk/ss.lib"
    tech_lef = "/pdk/tech.lef"
    cell_lef = "/pdk/cells.lef"
    macro_lefs = ()
    macro_libs = ()


def _consume(tmp_path, monkeypatch, body, stdout=""):
    out_json = tmp_path / "subservient_si_timing.json"

    def _exec(container, cmd, timeout=1800, **kw):
        out_json.write_text(body)
        return 0, stdout, ""

    monkeypatch.setattr(R, "_docker_exec", _exec)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: str(p))
    for name in ("spef", "sdc", "netlist"):
        (tmp_path / name).write_text("x")
    notes = []
    ok = R._emit_si_timing_json(
        tmp_path, "subservient", _Pdk(), "c",
        tmp_path / "spef", tmp_path / "sdc", tmp_path / "netlist",
        out_json, notes)
    return ok, notes


def test_a_truncated_timing_json_is_refused_and_named(tmp_path, monkeypatch):
    """r21's exact shape: present, big, and ending mid-token."""
    truncated = '{"tool": "OpenSTA", "pins": {"a": {"slew_rise_max":'
    ok, notes = _consume(tmp_path, monkeypatch, truncated)
    assert ok is False
    note = " ".join(notes)
    assert "DOES NOT PARSE" in note
    assert "subservient_si_timing.json" in note
    assert "TRUNCATED" in note and "the emit aborted" in note
    assert "CONSERVATIVE ENVELOPE" in note


def test_a_malformed_but_complete_json_says_malformed_not_truncated(
        tmp_path, monkeypatch):
    """The completion marker separates 'the tool stopped' from 'the tool
    finished and wrote something wrong' — different defects, different owners."""
    ok, notes = _consume(tmp_path, monkeypatch, '{"pins": {"a": ---}}',
                         stdout="SI_TIMING_JSON_EMIT_DONE pins=1\n")
    assert ok is False
    note = " ".join(notes)
    assert "malformed" in note and "TRUNCATED" not in note


def test_a_parseable_timing_json_is_accepted(tmp_path, monkeypatch):
    """THE NEGATIVE CONTROL: the new guard may not refuse a good file."""
    ok, notes = _consume(tmp_path, monkeypatch,
                         '{"tool": "OpenSTA", "pins": {"a": {"slack_max": 1.0}}}')
    assert ok is True
    assert not [n for n in notes if "DOES NOT PARSE" in n]


def test_an_absent_json_keeps_its_own_older_refusal(tmp_path, monkeypatch):
    """A LANDED CONTRACT: 'the tool produced nothing' and 'the tool produced
    something unreadable' are different sentences and both must survive."""
    out_json = tmp_path / "subservient_si_timing.json"

    def _exec(container, cmd, timeout=1800, **kw):
        return 1, "", ""

    monkeypatch.setattr(R, "_docker_exec", _exec)
    monkeypatch.setattr(R, "_to_container_path", lambda p, c: str(p))
    for name in ("spef", "sdc", "netlist"):
        (tmp_path / name).write_text("x")
    notes = []
    ok = R._emit_si_timing_json(tmp_path, "subservient", _Pdk(), "c",
                                tmp_path / "spef", tmp_path / "sdc",
                                tmp_path / "netlist", out_json, notes)
    assert ok is False
    assert "did not produce the timing JSON" in " ".join(notes)
