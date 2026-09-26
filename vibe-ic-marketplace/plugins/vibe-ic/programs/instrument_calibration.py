#!/usr/bin/env python3
"""instrument_calibration.py — every instrument is CALIBRATED before it judges.

THIS GATE BLOCKS (rc=1) on a NEW uncalibrated instrument (`--ratchet`).

OWNER RULING R-0915-86 (3), 2026-09-16: 「每把尺先校正再判人」 — a known-positive
must make the instrument FIRE, a known-negative must leave it SILENT, and an
instrument that cannot show both may not emit a verdict.

WHY, AND WHY IT IS NOT A THEORY
===============================
Eight judgements in this repository were WRONG in the same way inside two days.
Not one of them was a bug in the tool; each was an instrument reading a real
artefact and reporting the wrong thing about it, with nothing in the tree able
to tell the difference:

  R-0915-69  the antenna producer read a COSMETIC router-reader error
             (`DRT-1010`, raised on a wire the router itself wrote) as a route
             abort, and published `routing complete: NO` -> `FAIL` over a design
             carrying `[INFO DRT-0702] Post-route verification: 0 violation(s).`
  R-0915-75  step 29 was declared an "SDF-annotated post-layout gate sim" and
             had annotated 0 delays against 31,061 unmatched arcs. Nine PASSes
             were published under that name.
  R-0915-71/72 the step-29 supervisor watched the `docker exec` CLIENT, whose
             every byte goes to a file inside the command string. Output flat,
             cpu flat, io flat — indistinguishable from a corpse — so two
             simulations that reached `$finish` with 0 mismatches were reaped
             at 197 s.
  R-0915-83  a DRV census taken with no parasitics in STA reports 0 and is
             BYTE-IDENTICAL to a clean design; the loop read it as convergence.
  R-0915-82  an INCONCLUSIVE equivalence record was booked SKIP, so a netlist
             whose equivalence nobody proved carried a whole sign-off.
  R-0915-76  the ADC tone-bin rule placed the tone on the one bin whose error
             an SNDR removes as signal, reading the WORST placement as 2.8 bit
             the best.
  R-0915-78/79 a polarity ratchet named two GRAMMARS as prose extractors.
  (magic)    the illegal-overlap channel: an absent feedback dump and an empty
             one are different facts and were the same number.

None of these could be seen on a fixture its own author built, because the
author's fixture encodes the author's belief about the artefact. Each one IS
visible on a PAIR: a sample known to carry the condition the instrument must
fire on, and a sample known not to, which must leave it silent. That is the
whole design, and there is nothing else in it.

WHAT AN INSTRUMENT IS
=====================
The `program::function` that turns a TOOL ARTEFACT — a report, a log, a DEF, a
SPEF, an SDF transcript, a `lec.json`, a feedback dump, a live process tree —
into a VERDICT. Not the tool, not the step: the reader in between. That is the
only place the eight defects above could live and the only place this registry
speaks about.

THE RULE, AND IT HAS NO EXCEPTIONS
==================================
An instrument that is in `INSTRUMENTS` but MISCALIBRATED, or that judges from a
tool artefact and is NOT in `INSTRUMENTS`, MAY NOT JUDGE. Its verdict is
`NOT_MEASURED` with `reason_class = "uncalibrated"`. Each instrument calls
`instrument_calibration.assert_calibrated("<name>")` at its own source, one
line, before it judges — no decorator, no wrapper module, no "calibration mode"
flag that skips the rule (R-0915-85: 不要疊床架屋).

The five verdict words are the only ones this module emits: PASS,
PASS_WITH_WAIVERS, FAIL, NOT_MEASURED, NOT_APPLICABLE (R-0915-85). When the
icverdict batch lands `programs/verdict.py`, `VERDICTS` below is deleted and
imported from there — it is written as a tuple with that deletion in mind, not
as a second vocabulary.

WHERE THE FIXTURES COME FROM, AND WHY THAT IS THE LOAD-BEARING PART
===================================================================
Every sample in this registry is a REAL artefact, produced by the REAL tool from
the PDK, recorded under `programs/calibration/` with its provenance written into
the entry. NONE of them comes from a design under test, from a golden, from an
oracle or from a published cell — a calibration structure (a two-inverter chain
built from the PDK's own standard cells; a five-rectangle layout) is to an
instrument what a calibration net is to an extractor.

Produced on 8HD-6 (192.168.1.108) on 2026-09-16 inside the pinned image — the
one `_eda_pin.IMAGE_DIGEST` names, resolved to image id `da2314d4100c` on that
host. The digest is NOT spelled here: a second literal does not fail, it sits
there being right until the pin moves and it is the only thing that did not.
With:

  magic 8.3 revision 683 ........ the feedback save format, both sides
  OpenSTA 2.7.0 f21d4a3878 ...... `write_sdf` with and without `-include_typ`;
                                  `report_check_types -violators` with and
                                  without `read_spef`
  iverilog/vvp .................. `-sdf-info` transcripts, 3 delays vs 0
  OpenROAD 26Q3-2075-g18e98f9e44  a real routed design and its
                                  `[INFO DRT-0702]` verification line;
                                  OpenRCX extraction -> the SPEF
  yosys 0.68+ aa4f0d7d6 ......... the gate netlist behind the `lec.json`

Two of those pairs are independent reproductions of the ruling that named them,
on a design small enough to read by eye:

  R-0915-75  `write_sdf -include_typ` -> 3 `Putting delay`;
             `write_sdf` (no flag, min::max with an EMPTY typ) -> 0.
  R-0915-83  ONE `read_spef` apart, same design, same 0.004 pF limit:
             without it `report_check_types -violators` writes a 0-BYTE report;
             with it, 285 bytes and 2 `(VIOLATED)` lines.

THE RATCHET (`--ratchet`)
=========================
`scan()` derives the instrument population from the code — a function that
MATCHES tool-emitted evidence (a tool diagnostic id, a tool transcript grammar,
a producer record's verdict field) and turns it into a status — and the gate
fails when one of them is neither in `INSTRUMENTS` nor in `_UNCALIBRATED_REGISTER`.
The register is source, reviewed in the diff, with an owner per entry; there is
NO flag that writes it, and an entry that outlives its instrument is itself a
finding (patterned on `prose_polarity_consulted_check._OFFENDER_REGISTER`, which
removed exactly this hole from itself under vibe-ic#900: RATCHET ON MEMBERSHIP,
NOT ON COUNT).

chip-AGNOSTIC: no IC, vendor, node or SKU literal appears or can affect any
branch. The tool literals are the tools' own message grammars, which is what the
channel IS.

USAGE
-----
    instrument_calibration.py [--root .] [--report] [--json OUT] [--ratchet]

    exit 0 = every registered instrument is CALIBRATED (or, with --ratchet, the
             population is exactly `INSTRUMENTS` + the declared register)
    exit 1 = a MISCALIBRATED instrument, or an unregistered one (BLOCKING)
    exit 2 = could not be determined — never a vacuous pass
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# vibe-ic#1082 — a declared report destination is written atomically, so a
# reader never sees a half-written verdict file.
from _atomic_artefact import write_json as _atomic_write_json  # noqa: E402
from _atomic_artefact import write_bytes as _atomic_write_bytes  # noqa: E402

GATE = "instrument_calibration"

#: The five step verdicts (R-0915-85). Nothing here emits any other word.
#: DELETE THIS TUPLE and import it from `programs/verdict.py` when the icverdict
#: batch lands — it is not a second vocabulary, it is this one, early.
VERDICTS: Tuple[str, ...] = (
    "PASS", "PASS_WITH_WAIVERS", "FAIL", "NOT_MEASURED", "NOT_APPLICABLE")

#: The reason_class an uncalibrated instrument's NOT_MEASURED carries.
UNCALIBRATED = "uncalibrated"

CALIBRATED = "CALIBRATED"
MISCALIBRATED = "MISCALIBRATED"

#: Where the real artefacts live, beside this module.
FIXTURES = Path(__file__).resolve().parent / "calibration"


class Uncalibrated(RuntimeError):
    """Raised at an instrument's source when it may not judge.

    The caller turns this into `NOT_MEASURED` with `reason_class` =
    `"uncalibrated"`; it never becomes a PASS and never becomes a FAIL, because
    both of those would be a verdict nobody measured.
    """

    def __init__(self, name: str, detail: str):
        super().__init__(f"{name} may not judge: {detail}")
        self.instrument = name
        self.detail = detail
        self.verdict = "NOT_MEASURED"
        self.reason_class = UNCALIBRATED


# ══════════════════════════════════════════════════════════════════════════
#  The registry
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Sample:
    """One half of a calibration pair.

    `provenance` is not documentation. It is the claim that makes the sample
    evidence rather than an assumption, and `test_instrument_calibration.py`
    asserts that every on-disk sample it names exists and still has the shape
    the entry says it has.
    """
    provenance: str
    artefact: Callable[[], Any]


@dataclass(frozen=True)
class Miscalibration:
    """What an instrument that FAILS its own pair on this tree does instead.

    THIS IS NOT AN EXEMPTION AND IT DOES NOT SOFTEN ANYTHING. The instrument is
    still MISCALIBRATED, `assert_calibrated` still refuses it, and every verdict
    it would have produced is still `NOT_MEASURED` / `uncalibrated`. What this
    adds is the one thing a bare "it is red" cannot: the EXACT wrong answer, so
    the tests assert the MEASUREMENT rather than skipping it.

    It lives HERE, at the source, beside the pair it is about — never as a list
    in a test file. A list in a test file has to be hand-fed, and the day the
    owner's fix lands it is stale with nothing to say so. This is the opposite:
    `fires_with` is asserted, so the day the instrument starts behaving the test
    goes RED and this object must be DELETED in the same commit. That is the
    ratchet direction, and there is no other way out of it.

    * `failed_sides` — which half of the pair fails ("positive" / "negative").
    * `fires_with` — the exact outcome `judge()` returns on the sample that
      SHOULD have made it fire. `None` means it stays silent when it must speak.
    * `instead_of` — one line on what the tree sees because of it.
    * `closed_by` — the ruling/owner whose landing removes this object.
    """
    failed_sides: Tuple[str, ...]
    fires_with: Optional[str]
    instead_of: str
    closed_by: str


@dataclass(frozen=True)
class Instrument:
    """A reader that turns a tool artefact into a verdict, and its pair.

    `judge(artefact)` returns the NAMED OUTCOME when the instrument fires, and
    `None` when it stays silent. That signature is deliberate: "fired" and "what
    it said" are one value, so a pair cannot pass by firing for the wrong reason.

    `miscalibrated_evidence` is set ONLY for an instrument this tree MEASURES as
    failing its own pair — see `Miscalibration`.
    """
    name: str
    reads: str
    ruling: str
    owner: str
    why: str
    judge: Callable[[Any], Optional[str]]
    positive: Sample
    expect: str
    negative: Sample
    miscalibrated_evidence: Optional[Miscalibration] = None
    #: WHERE `assert_calibrated` is called, when that is not the instrument's
    #: own body. Declared, never implied — see `call_site` and the one entry
    #: that sets it.
    calls_at: Optional[str] = None

    @property
    def call_site(self) -> str:
        """`module::function` that must carry this instrument's one line."""
        return self.calls_at or self.name


@dataclass
class Calibration:
    name: str
    state: str
    positive_outcome: Optional[str] = None
    negative_outcome: Optional[str] = None
    failed_sides: Tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "instrument": self.name,
            "state": self.state,
            "failed_sides": list(self.failed_sides),
            "positive_outcome": self.positive_outcome,
            "negative_outcome": self.negative_outcome,
            "detail": self.detail,
        }


# ── the instruments ───────────────────────────────────────────────────────
#
# One entry per instrument, each with the pair that calibrates it. The order is
# the order R-0915-86 names them in.

def _read(name: str) -> Callable[[], str]:
    def _load() -> str:
        return (FIXTURES / name).read_text(errors="replace")
    return _load


# ---- 1. the route-abort classification (antenna producer), R-0915-69 ------

def _judge_route_abort(log_txt: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    return "ROUTING_INCOMPLETE" if R.antenna_routing_incomplete(log_txt) else None


# ---- 2. the SDF annotation census, R-0915-75 ------------------------------

def _judge_sdf_annotation(transcript: str) -> Optional[str]:
    import sdf_gate_sim as SG
    return SG.name_unannotated_run(SG.sdf_annotation_census(transcript))


# ---- 3. the gate-sim supervisor's progress detector, R-0915-71/72 ---------

#: Small enough that the pair costs ~3 s, large enough that a scheduling hiccup
#: on a loaded host cannot fake either side: the silent arm has to look dead for
#: three consecutive polls, and the working arm burns real CPU for longer than
#: that whole window.
_PROBE_POLL_S = 0.25
_PROBE_LOOKS = 3
_PROBE_WORK_S = 2.0


def _judge_progress(job: Dict[str, str]) -> Optional[str]:
    """Run `job` under the repo's own supervisor and report whether it was
    reaped. Fires (`"STALLED"`) when the supervisor calls it a stall.

    Every byte of the command goes to a file, which is the exact shape that
    made the client look dead in R-0915-71 — so this asks the supervisor the
    question that defect answered wrongly.
    """
    import _progress_run as _pr
    with tempfile.TemporaryDirectory() as td:
        sink = os.path.join(td, "out.log")
        cmd = f"{job['shell']} > {sink} 2>&1"
        try:
            _pr.run(["bash", "-c", cmd], poll_s=_PROBE_POLL_S,
                    stall_looks=_PROBE_LOOKS)
        except _pr.Stalled:
            return "STALLED"
        except Exception as exc:                       # pragma: no cover
            return f"ERROR:{type(exc).__name__}:{exc}"
    return None


# ---- 4. the DRV census, R-0915-83 ----------------------------------------
#
# The census is EMITTED TCL, so the calibration EXECUTES it: the block is taken
# out of the deck the runner actually emits, by its own markers, and run in
# `tclsh` with the five tool commands stubbed. The ONE difference between the
# two arms is whether `read_spef` returns or raises; the violator report handed
# to the counting arm is the REAL `report_check_types -violators` file OpenSTA
# wrote for the calibration chain.

_TCL_OUT_DIR = "/tmp/cal_pnr"
_TCL_CENSUS_BEGIN = "set _sdr_par_ok 0"
_TCL_CENSUS_END = "SDR_DRV_BY_KIND"


def _drv_census_block() -> str:
    """The census, cut out of the deck the runner emits. Never re-typed."""
    import phase3_one_shot_runner as R
    deck = R._v1_8_100_signoff_drv_repair_tcl(out_dir_c=_TCL_OUT_DIR)
    i = deck.index(_TCL_CENSUS_BEGIN)
    j = deck.index(_TCL_CENSUS_END, i)
    j = deck.index("\n", j)
    return deck[i:j + 1]


def run_drv_census(report_text: Optional[str]) -> str:
    """Run the EMITTED census in tclsh over `report_text` and return its line.

    `report_text` is the `report_check_types -violators` file the session left
    behind: `None` means the command never wrote one, `""` means it wrote an
    EMPTY one — and OpenSTA writes exactly that for a session with no
    parasitics, which is the whole point.

    The five tool commands the block calls are stubbed and nothing else is; the
    Tcl is the deck's own text, cut out by its own markers. This EXECUTES the
    census instead of grepping it, which is how the two findings below were
    found.
    """
    block = _drv_census_block()
    with tempfile.TemporaryDirectory() as td:
        rpt = Path(td) / "sdr_drv.rpt"
        if report_text is not None:
            rpt.write_text(report_text)
        prelude = (
            "proc define_process_corner {args} {}\n"
            "proc extract_parasitics {args} {}\n"
            "proc write_spef {args} {}\n"
            "proc read_spef {args} {}\n"
            "proc report_check_types {args} {}\n"
            "set _sdr_tx_error 0\n")
        body = block.replace(f"{_TCL_OUT_DIR}/sdr_drv.rpt", str(rpt))
        script = prelude + "while {1} {\n" + body + "\nbreak\n}\n"
        path = Path(td) / "census.tcl"
        path.write_text(script)
        cp = subprocess.run(["tclsh", str(path)], capture_output=True,
                            text=True)
    return ((cp.stdout or "") + (cp.stderr or "")).strip()


def _judge_drv_census(report_text: Optional[str]) -> Optional[str]:
    """Fires with the refusal line; silent when it reports a number."""
    out = run_drv_census(report_text)
    if "SDR_DRV_CENSUS_NOT_MEASURED" in out:
        if "SDR_DRV_BY_KIND" in out:
            return "REFUSED_AND_ALSO_REPORTED_A_NUMBER"
        return [l for l in out.splitlines()
                if "SDR_DRV_CENSUS_NOT_MEASURED" in l][0].strip()[:120]
    return None


def _drv_arm_no_parasitics() -> str:
    """The REAL report OpenSTA wrote with no parasitics in the session: EMPTY."""
    return (FIXTURES / "drv_no_parasitics_positive.rpt").read_text()


def _drv_arm_with_parasitics() -> str:
    return (FIXTURES / "drv_with_parasitics_negative.rpt").read_text()


# ---- 5. the LEC disposition, R-0915-82 -----------------------------------

def _judge_lec_disposition(doc: Dict[str, Any]) -> Optional[str]:
    """Fires when the record does NOT earn a clean walk-past.

    Silent means the step may take the record at face value (a proof). Anything
    else — a non-convergence, a cut-off proof, 0 compared points — is named.
    """
    import design_one_shot_runner as D
    verdict = str(doc.get("verdict", "")).strip().upper()
    if verdict == "PASS":
        return None
    if verdict != "INCONCLUSIVE":
        return f"{verdict or 'ABSENT'}"
    status, reason = D.lec_inconclusive_disposition(doc)
    return f"{status}: {reason[:90]}"


def _lec_inconclusive_record() -> Dict[str, Any]:
    """An INCONCLUSIVE record that RAN — the shape R-0915-82 was written about.

    Field names and value types are the producer's own; `test_…` asserts every
    key here is one `lec_run` writes, so this cannot drift into a dialect the
    disposition does not read (which is the defect the landed R-82 control had).
    """
    return {
        "verdict": "INCONCLUSIVE",
        "equivalent": None,
        "compared_points": 846,
        "unproven_points": 481,
        "non_equivalent_points": 0,
        "budget_exhausted": False,
        "exhausted_resource": None,
        "progress_stalled": False,
    }


def _lec_proven_record() -> Dict[str, Any]:
    p = FIXTURES / "lec_proven_negative.json"
    return json.loads(p.read_text())


# ---- 6. the tone-bin rule + matched decode, R-0915-76 --------------------

def _judge_tone_bin(windows_total: int) -> Optional[str]:
    """Fires with the tone it would grade at; silent when it refuses to grade.

    The instrument's failure mode was never "it returned nothing" — it was
    returning a bin whose cycle count DIVIDES the graded span, where the decoded
    error collapses onto the signal and an SNDR removes it as signal. So the
    outcome string carries the coprimality, and the positive's `expect` pins it.
    """
    import analog_resolution_stimulus as A
    plan = A.incremental_tone(int(windows_total))
    if plan is None:
        return None
    m, c = int(plan["graded_windows"]), int(plan["cycles"])
    return (f"cycles={c} windows={m} gcd={math.gcd(c, m)} "
            f"odd={c % 2 == 1}")


def _tone_gradable_windows() -> int:
    """The smallest record the rule itself says is gradable — derived by the
    product's own search, never typed."""
    import analog_resolution_stimulus as A
    return A.incremental_record_windows()


def _tone_ungradable_windows() -> int:
    """One window fewer than the product's own bound: by construction no
    gradable tone exists, so the rule must stay silent rather than invent one."""
    import analog_resolution_stimulus as A
    w = A.incremental_record_windows() - 1
    while w >= 2 and A.incremental_tone(w) is not None:   # pragma: no cover
        w -= 1
    return w


# ---- 7. the overlap-region gate (magic) ----------------------------------

def _judge_magic_overlap(text: str) -> Optional[str]:
    import magic_illegal_overlap_check as M
    records, warnings = M.parse_feedback(text)
    string_count = M.count_marker(text)
    if warnings:
        return f"UNREADABLE: {warnings[0][:80]}"
    if not records and not string_count:
        return None
    return f"OVERLAPS records={len(records)} string_count={string_count}"


# ---- 8. the polarity ratchet's extractor detector, R-0915-78/79 ----------

_POLARITY_BLIND_SRC = '''\
import re
_PAT = re.compile(r"target\\s+process\\s*[:=]\\s*(\\w+)")

def extract_pdk_target(text, record):
    """Searches prose and writes the value, never asking whether the sentence
    DENIES it. This is #706's shape, reduced to its structure."""
    m = _PAT.search(text)
    if m:
        record["pdk_target"] = m.group(1)
    return record
'''

_POLARITY_CLEAN_SRC = '''\
import re
import _prose_polarity

_PAT = re.compile(r"target\\s+process\\s*[:=]\\s*(\\w+)")

def extract_pdk_target(text, record):
    """The same extractor, consulting the vocabulary."""
    m = _PAT.search(text)
    if m and not _prose_polarity.denies(text, m.start()):
        record["pdk_target"] = m.group(1)
    return record
'''


def _judge_polarity_scan(root: Path) -> Optional[str]:
    import prose_polarity_consulted_check as P
    found = P.scan(root)
    return ("BLIND " + ",".join(found)) if found else None


def _polarity_tree(src: str) -> Callable[[], Path]:
    def _build() -> Path:
        d = Path(tempfile.mkdtemp(prefix="cal_polarity_"))
        (d / "programs").mkdir()
        (d / "programs" / "cal_extractor.py").write_text(src)
        return d
    return _build


def _judge_sparse_gpl_retry(log: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    # The placement command is the producer's Tcl grammar. Only the log is the
    # measured input to this instrument; use the same deck in both arms.
    deck = "global_placement -routability_driven -density 0.3 -timing_driven\n"
    result = R._sparse_gpl_retry_deck(deck, log)
    return "GPL_SPARSE_RETRY" if result is not None else None


def _antenna_retry_log(verified: bool) -> Tuple[int, str]:
    if verified:
        return (0, "ANTENNA_FULL_ROUTE_VERIFIED: router DRC 0\n"
                "ANTENNA_LOOP_CONVERGED: iter=1\n"
                + _read("route_verified_negative.log")()
                + "\nREPAIR_ANTENNA_DONE: diode=D iter=0 margin=0\n"
                "[INFO ANT-0002] Found 0 net violations.\n"
                "[INFO ANT-0001] Found 0 pin violations.\n")
    return (1, _read("route_aborted_negative.log")())


def _judge_antenna_rollback(sample: Tuple[int, str]) -> Optional[str]:
    """Exercise the real rollback selector; fake only the EDA process writes."""
    import phase3_one_shot_runner as R
    with tempfile.TemporaryDirectory(prefix="cal_antenna_rollback_") as td:
        out = Path(td) / "pnr"
        out.mkdir()
        (out / R._ANTENNA_PASS_CHECKPOINT_NAME).write_bytes(b"calibration odb")
        deck = Path(td) / "pnr.tcl"
        deck.write_text("\n".join((
            "read_verilog /cal/design.v", "link_design top",
            R._PNR_RESUME_ELIDE_BEGIN, "puts BASE_ROUTE",
            R._PNR_RESUME_ELIDE_END,
            R._pnr_stage_begin("postroute_drv_repair"), "puts DRV",
            R._pnr_stage_end("postroute_drv_repair"),
            R._pnr_stage_begin("postroute_antenna_repair"), "puts ANTENNA",
            R._pnr_stage_end("postroute_antenna_repair"),
            R._pnr_stage_begin("postroute_antenna_reconverge"),
            "puts RECONVERGE", R._pnr_stage_end("postroute_antenna_reconverge"),
            f"write_def {out}/routed.def", f"write_def {out}/top.def",
            f"write_verilog {out}/top_pnr.v", "")))
        request = ("ANTENNA_NATIVE_REROUTE_NONFATAL: DRT-0712\n"
                   "ANTENNA_REPAIR_REFUSED_ROLLBACK_REQUEST: "
                   f"checkpoint={out / R._ANTENNA_PASS_CHECKPOINT_NAME} "
                   "reason=DRT-0712\n")
        original = R._declared_session_exec
        def eda_writes(_container, cmd, products, **_kwargs):
            for product in products:
                Path(product).write_text("calibration EDA output\n")
            if "pnr_antenna_full_retry" in cmd:
                return sample[0], sample[1], ""
            return 0, _read("route_verified_negative.log")(), ""
        try:
            R._declared_session_exec = eda_writes
            result = R._pnr_rollback_refused_antenna_repair(
                container="calibration", out_dir=out, out_dir_c=str(out),
                pnr_tcl=deck, log_text=request, hard_ceiling_s=60)
        finally:
            R._declared_session_exec = original
    return "RECOVERED" if result["status"] == "RECOVERED" else None


def _judge_isolated_recovery(log: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    # The marker is emitted by the runner; the remainder is the captured
    # OpenROAD transcript. Keep the tool lines byte-for-byte.
    combined = "=== PNR ANTENNA ISOLATED ECO ===\n" + log
    return ("ROUTE_UNVERIFIED" if
            R._antenna_isolated_recovery_modified(combined) else None)


def _judge_antenna_isolated_drc_markers(report: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    with tempfile.TemporaryDirectory(prefix="cal_antenna_drc_") as td:
        path = Path(td) / "router.drc.rpt"
        path.write_text(report)
        count = len(R._antenna_isolated_drc_markers(path))
    return f"DRC_MARKERS:{count}" if count else None


def _judge_feedback_antenna(log: str) -> Optional[str]:
    import drc_feedback_repair as D
    net, pin = D._antenna(log)
    return f"ANTENNA_VIOLATIONS:{net}/{pin}" if net or pin else None


def _judge_feedback_scoped(sample: Tuple[str, Tuple[str, ...]]) -> Optional[str]:
    import drc_feedback_repair as D
    log, targets = sample
    return None if D._native_scoped_guard(log, set(targets)) else "WIRE_GUARD_REFUSED"


def _judge_feedback_measure(sample: Tuple[str, str]) -> Optional[str]:
    """Exercise the production KLayout log and RDB readers with real outputs."""
    import drc_feedback_repair as D
    from types import SimpleNamespace
    registry = json.loads((FIXTURES.parent / "router_invisible_rules.json").read_text())
    deck = registry["decks"][0]
    rule = deck["rules"][0]
    log_name, rdb_name = sample
    log = _read(log_name)()
    report_bytes = (FIXTURES / rdb_name).read_bytes()
    original = D._docker
    with tempfile.TemporaryDirectory(prefix="cal_feedback_") as td:
        scratch = Path(td)
        def eda_writes(_image, _project, args, **_kwargs):
            report = next(Path(a.partition("=")[2]) for a in args
                          if a.startswith("report="))
            _atomic_write_bytes(report, report_bytes)
            return SimpleNamespace(returncode=0, stdout=log, stderr="")
        try:
            D._docker = eda_writes
            markers = D._measure("calibration", scratch, deck["deck_basename"],
                                 rule, scratch / "calibration.gds", "calibration_top",
                                 scratch)
        finally:
            D._docker = original
    return "SIGNOFF_RULE_PRESENT" if markers else None


# ── the registry itself ───────────────────────────────────────────────────

INSTRUMENTS: Dict[str, Instrument] = {}


def _register(inst: Instrument) -> None:
    if inst.name in INSTRUMENTS:                       # pragma: no cover
        raise AssertionError(f"duplicate instrument {inst.name}")
    INSTRUMENTS[inst.name] = inst


_register(Instrument(
    name="phase3_one_shot_runner::antenna_routing_incomplete",
    reads="the PnR log (`phase3/stage3/pnr/openroad.log`)",
    ruling="R-0915-69",
    owner="icsha2",
    why=("It read a cosmetic router-READER refusal as a route abort and "
         "published FAIL over a verified detailed route. The pair is the exact "
         "distinction it got wrong: the SAME cosmetic refusal, once beside a "
         "real second failure and once alone over the router's own "
         "verification."),
    judge=_judge_route_abort,
    positive=Sample(
        provenance=(
            "A REAL OpenROAD 26Q3-2075-g18e98f9e44 run on 8HD-6 (floorplan -> "
            "place -> global_route -> detailed_route on a two-inverter "
            "sky130_fd_sc_hd chain; `calibration/route_verified_negative.log` "
            "is its unedited transcript), with the antenna loop's OWN two "
            "marker lines appended — one naming the cosmetic DRT-1010 and one "
            "naming DRT-0305. Both codes' format strings are present in the "
            "pinned image's openroad binary (`strings`); the marker text is "
            "the flow's own constant, asserted against the emitted deck by "
            "`test_the_antenna_markers_are_the_flows_own`."),
        artefact=_read("antenna_abort_positive.log")),
    expect="ROUTING_INCOMPLETE",
    negative=Sample(
        provenance=(
            "The same real run, same two lines, both naming ONLY DRT-1010 — "
            "the run15 shape. The log carries the router's own `[INFO "
            "DRT-0702] Post-route verification: 0 violation(s).`, which is "
            "what makes the cosmetic reading legal at all."),
        artefact=_read("antenna_cosmetic_negative.log")),
))

_register(Instrument(
    name="sdf_gate_sim::sdf_annotation_census",
    reads="the `vvp -sdf-info` transcript of the gate-level simulation",
    ruling="R-0915-75",
    owner="icsha2",
    why=("Step 29 is DECLARED an SDF-annotated post-layout simulation and "
         "nothing checked that one delay was annotated. The pair is two real "
         "transcripts of the same netlist that differ by one OpenSTA flag."),
    judge=_judge_sdf_annotation,
    positive=Sample(
        provenance=(
            "REAL `vvp -sdf-info` transcript, 8HD-6, pinned image. Netlist: a "
            "two-inverter `sky130_fd_sc_hd__inv_2` chain; models: the PDK's own "
            "`primitives.v` + `sky130_fd_sc_hd.v`; compiled `iverilog -gspecify "
            "-ginterconnect`. SDF: OpenSTA 2.7.0 `write_sdf` WITHOUT "
            "`-include_typ` (`calibration/cal_no_include_typ.sdf`, the min::max "
            "form with an EMPTY typ field). MEASURED: 0 `Putting delay`."),
        artefact=_read("sdf_unannotated_positive.log")),
    expect="NOT SDF-ANNOTATED",
    negative=Sample(
        provenance=(
            "The SAME netlist, SAME models, SAME compile; the ONLY difference "
            "is `write_sdf -include_typ` (`calibration/cal_include_typ.sdf`). "
            "MEASURED: 3 `Putting delay`. This independently reproduces "
            "R-0915-75's cause on a design small enough to read."),
        artefact=_read("sdf_annotated_negative.log")),
))

_register(Instrument(
    name="_container_exec::container_tree_probe",
    reads="the live process tree of the work (not the client that launched it)",
    ruling="R-0915-71/72",
    owner="icsha2",
    why=("The supervisor watched the `docker exec` CLIENT, whose every byte "
         "goes to a file inside the command string: output flat, cpu flat, io "
         "flat, indistinguishable from a corpse. Two simulations that reached "
         "`$finish` with 0 mismatches were reaped at 197 s. The pair is the "
         "same shape with nothing stubbed — every byte to a file, once with a "
         "process doing real work and once with one doing none."),
    judge=_judge_progress,
    #: THE ONE INSTRUMENT WHOSE LINE IS NOT IN ITS OWN BODY, and the reason is
    #: MEASURED, not a preference.
    #:
    #: `container_tree_probe` is a probe FACTORY that `_container_exec` calls on
    #: every supervised run, including from inside tests that stub the world.
    #: Calibrating a LIVE-PROCESS detector costs live processes, and
    #: `test_issue2083_container_progress_watches_the_tool_not_the_client`
    #: builds its subject with `monkeypatch.setattr(ce.os, "listdir", ...)` —
    #: and `ce.os` IS the `os` module, so that patch replaces `os.listdir`
    #: PROCESS-WIDE. MEASURED:
    #:
    #:     >>> import os, _container_exec as ce; ce.os is os
    #:     True
    #:
    #: A calibration reached inside that window walks a `/proc` holding one
    #: fake pid, finds no descendant of its own worker, reads flat, and reaps
    #: its own known-negative — so `container_tree_probe` RAISED `Uncalibrated`
    #: and 29 landed tests went red on a calibration question they never asked.
    #: The rule did not change what the probe measures; the CALL SITE did, and
    #: that is the defect.
    #:
    #: So the line lives at the consumer R-0915-86 actually names — the
    #: GATE-SIM SUPERVISOR, `sdf_gate_sim::_docker`, which is the call site
    #: R-0915-71/72 was written about ("`sdf_gate_sim._docker` ran `vvp ... >
    #: log` through a bare `_progress_run.run`"). `_container_exec` returns,
    #: raises and supervises exactly what it did before this batch.
    calls_at="sdf_gate_sim::_docker",
    positive=Sample(
        provenance=("A live `sleep` whose output goes to a file: no output, no "
                    "cpu, no io. A progress detector that cannot fire here "
                    "cannot fire at all."),
        artefact=lambda: {"shell": f"sleep {_PROBE_WORK_S * 6:.0f}"}),
    expect="STALLED",
    negative=Sample(
        provenance=("A live CPU burner whose EVERY BYTE goes to the same file "
                    "— the R-0915-71 shape exactly. It must not be reaped."),
        artefact=lambda: {
            "shell": ("python3 -c \"import time;t=time.time()\n"
                      f"while time.time()-t < {_PROBE_WORK_S}: sum(range(9999))\n"
                      "print('done')\"")}),
))

_register(Instrument(
    name="phase3_one_shot_runner::_v1_8_100_signoff_drv_repair_tcl",
    reads="the OpenSTA session's `report_check_types -violators` file",
    ruling="R-0915-83",
    owner="icaes",
    why=("`extract_parasitics` fills the ODB; OpenSTA sees nothing until a "
         "SPEF is read back, so a census taken without one reports 0 and is "
         "byte-identical to a clean design — and the loop reads 0 as "
         "convergence, which makes the UNMEASURED case look best (R-0915-83). "
         "The pair EXECUTES the emitted census in tclsh over the two REAL "
         "`report_check_types -violators` files OpenSTA wrote for the same "
         "design one `read_spef` apart.\n\n"
         "MEASURED ON THIS TREE, AND IT IS MISCALIBRATED. Two findings, both "
         "from RUNNING the deck rather than grepping it:\n"
         "  (a) the empty report is still counted. OpenSTA's real output for "
         "the no-parasitics session is a 0-BYTE file; `file exists` is true "
         "for it, so `_sdr_rpt_ok` stays 1 and the census prints "
         "`SDR_DRV_BY_KIND: total=0` — the phantom R-0915-83 was written to "
         "stop, arriving through the half the fix did not close.\n"
         "  (b) `parasitics_in_sta=0` is UNREACHABLE. `_sdr_par_ok` is cleared, "
         "then a failing `read_spef` prints `SDR_SPEFR_NONFATAL` and BREAKS, "
         "then it is set to 1 — so at the `if {!$_sdr_par_ok ...}` test it is "
         "always 1 and only the `violator_report` half can ever fire. The "
         "landed control asserts the refusal STRING is in the deck, which is "
         "true, and never that it is reachable.\n"
         "Owner icaes, in its own batch: this registry does not change what an "
         "instrument measures."),
    judge=_judge_drv_census,
    positive=Sample(
        provenance=(
            "The REAL `report_check_types -max_slew -max_capacitance "
            "-max_fanout -violators` file OpenSTA 2.7.0 wrote on 8HD-6 for a "
            "two-inverter `sky130_fd_sc_hd__inv_2` chain at "
            "`set_max_capacitance 0.004` with NO `read_spef`: **0 bytes**, over "
            "a design that has 2 violators. "
            "`calibration/drv_no_parasitics_positive.rpt`."),
        artefact=_drv_arm_no_parasitics),
    expect="SDR_DRV_CENSUS_NOT_MEASURED",
    negative=Sample(
        provenance=(
            "The SAME design, SAME limit, SAME command — one `read_spef "
            "cal_chain.spef` apart, where the SPEF is a genuine OpenRCX "
            "extraction with the PDK's own "
            "`rules.openrcx.sky130A.max.magic` (`[INFO RCX-0045] Extract 1 "
            "nets, 6 rsegs, 6 caps, 3 ccs`). 285 bytes, 2 `(VIOLATED)` lines "
            "under a `max capacitance` heading; the deck counts them "
            "(`SDR_DRV_BY_KIND: total=2 max_capacitance=2`). "
            "`calibration/drv_with_parasitics_negative.rpt`."),
        artefact=_drv_arm_with_parasitics),
    miscalibrated_evidence=Miscalibration(
        failed_sides=("positive",),
        # MEASURED by running the emitted census in tclsh over the REAL 0-byte
        # report, on cf37f6c92: it does not refuse. `judge` returns None.
        fires_with=None,
        instead_of=("the deck prints `SDR_DRV_BY_KIND: total=0 "
                    "max_capacitance=0` over a design that has 2 violators — "
                    "the same bytes a clean design produces, which is the "
                    "phantom R-0915-83 exists to stop"),
        closed_by=("icaes, R-0915-83 follow-up: (a) an EMPTY violator report "
                   "is not a measured 0 — `file exists` is true for a 0-byte "
                   "file, so `_sdr_rpt_ok` stays 1; (b) `parasitics_in_sta=0` "
                   "is unreachable — a failing `read_spef` prints "
                   "SDR_SPEFR_NONFATAL and BREAKS before the test that reads "
                   "the flag. When either is fixed this census starts "
                   "refusing, `fires_with` stops matching, and this object is "
                   "deleted in that commit."),
    ),
))

_register(Instrument(
    name="design_one_shot_runner::lec_inconclusive_disposition",
    reads="the equivalence producer's own record, `reports/lec.json`",
    ruling="R-0915-82",
    owner="icsha4",
    why=("INCONCLUSIVE was booked SKIP unconditionally, on the premise that it "
         "means 0 points compared. The record can contradict that premise and "
         "on sha256 run16 it did (846 compared, 481 unproven, nothing "
         "exhausted) — so a netlist nobody had proven equivalent carried a "
         "whole sign-off. The pair is a record that RAN and did not close "
         "against a record that PROVED."),
    judge=_judge_lec_disposition,
    positive=Sample(
        provenance=(
            "run16's own numbers in the producer's field names — 846 compared, "
            "481 unproven, 0 non-equivalent, every exhaustion flag clear. "
            "`test_the_lec_positive_speaks_the_producers_own_dialect` asserts "
            "each key is one `lec_run` writes, because the landed R-82 control "
            "was first written in a dialect its own grader could not read."),
        artefact=_lec_inconclusive_record),
    expect="FAIL",
    negative=Sample(
        provenance=(
            "A REAL `reports/lec.json` written by `lec_run.py` on 8HD-6: a "
            "4-bit registered adder, RTL against its OWN yosys 0.68+ gate "
            "netlist mapped to the PDK's standard cells — a proof that closes."),
        artefact=_lec_proven_record),
))

_register(Instrument(
    name="analog_resolution_stimulus::incremental_tone",
    reads="the emitted deck's decoded conversion-window plan",
    ruling="R-0915-76",
    owner="icadc",
    why=("The old rule took `band_bins // HARMONICS_IN_BAND` — one third of the "
         "decoded Nyquist BY CONSTRUCTION — which put the tone on a bin whose "
         "cycle count DIVIDES the graded span, where the decoded error is "
         "periodic at the signal's own frequency and an SNDR removes it as "
         "signal. Measured: it read the WORST placement as 2.8 bit the best. "
         "So the outcome carries `gcd`, and the pair is a record that admits a "
         "gradable tone against one that admits none."),
    judge=_judge_tone_bin,
    positive=Sample(
        provenance=("The smallest record the rule ITSELF says is gradable, "
                    "found by `incremental_record_windows()`'s own search — "
                    "not a number typed here."),
        artefact=_tone_gradable_windows),
    expect="gcd=1",
    negative=Sample(
        provenance=("One window fewer than that bound: by the bound's own "
                    "definition no gradable tone exists there, so the rule must "
                    "return None rather than place the tone somewhere."),
        artefact=_tone_ungradable_windows),
))

_register(Instrument(
    name="magic_illegal_overlap_check::parse_feedback",
    reads="magic's `feedback save` dump from the extraction",
    ruling="(icsha2 step-31, 14 illegal overlaps)",
    owner="icsha2",
    why=("An illegal overlap is the extractor saying it could not decide what "
         "the layout means, and netgen can still report `Circuits match "
         "uniquely` over the netlist it wrote anyway. The trap the gate's own "
         "docstring names is that ABSENT IS NOT ZERO — so the negative here is "
         "a real EMPTY dump from an extraction that demonstrably ran, not a "
         "missing file."),
    judge=_judge_magic_overlap,
    positive=Sample(
        provenance=(
            "Written by magic 8.3 revision 683 itself, in the pinned image: "
            "two `feedback add \"Illegal overlap between … (types do not "
            "connect)\" pale` records saved with `feedback save`, `feedback "
            "count` = 2. The boxes come back in magic's INTERNAL units (`box "
            "20 20 35 40` in, `box 40 40 70 80` out, scaleFactor 2) — a detail "
            "no hand-written fixture would carry."),
        artefact=_read("magic_overlap_positive.feedback")),
    expect="OVERLAPS records=2",
    negative=Sample(
        provenance=(
            "A REAL sky130A extraction that RAN and found nothing: `gds read` "
            "of a five-rectangle calibration layout, `extract all`, `feedback "
            "save` -> a 0-BYTE file. MEASURED, and it is the trap: an empty "
            "file is a measured zero and an absent one is an unmeasured "
            "nothing."),
        artefact=_read("magic_overlap_negative.feedback")),
))

_register(Instrument(
    name="prose_polarity_consulted_check::scan",
    reads="the program tree (AST) — the extractors that write declared fields",
    ruling="R-0915-78/79",
    owner="icadc / icsha2",
    why=("The detector named two GRAMMARS as prose extractors — a function "
         "reading a stamp's fields and one reading a testbench transcript — "
         "neither of which searches prose for a declared value. A detector "
         "that cannot tell a grammar from prose produces confident wrong "
         "names, so the negative here is a clean extractor and the positive is "
         "#706's shape reduced to its structure."),
    judge=_judge_polarity_scan,
    positive=Sample(
        provenance=("A planted tree carrying #706's structure exactly: a "
                    "function that matches prose with a module-level pattern "
                    "and writes the match into a declared field, never "
                    "consulting the polarity vocabulary."),
        artefact=_polarity_tree(_POLARITY_BLIND_SRC)),
    expect="BLIND cal_extractor::extract_pdk_target",
    negative=Sample(
        provenance=("The SAME extractor, same pattern, same field — the one "
                    "difference is that it reaches `_prose_polarity`. If the "
                    "detector fires here it is naming the shape, not the "
                    "defect."),
        artefact=_polarity_tree(_POLARITY_CLEAN_SRC)),
))


# ── the same channels, the same real logs, the rest of their readers ──────
#
# These are the OTHER functions `scan()` names in the two channels the pairs
# above already cover. They are registered rather than declared because the
# artefacts to calibrate them were already on disk: refusing to register an
# instrument whose pair you are holding is how a debt register grows.

def _judge_detail_route_completed(log_txt: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    return "ROUTE_COMPLETED" if R._detail_route_completed(log_txt) else None


def _judge_route_modified(log_txt: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    return ("MODIFIED_AFTER_VERIFICATION"
            if R.route_modified_after_last_verification(log_txt) else None)


def _judge_antenna_isolated_recovery_modified(log_txt: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    return ("MODIFIED_AFTER_VERIFICATION"
            if R._antenna_isolated_recovery_modified(log_txt) else None)


def _judge_refusal_after_verification(log_txt: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    return ("REFUSAL_AFTER_VERIFICATION"
            if R.antenna_reroute_refusal_after_last_verification(log_txt)
            else None)


def _judge_router_iter_counts(log_txt: str) -> Optional[str]:
    import _signoff_drc_format as S
    counts = S.router_loop_iter_counts(log_txt)
    return f"ITER_COUNTS {counts}" if counts else None


def _judge_router_final_count(log_txt: str) -> Optional[str]:
    import _signoff_drc_format as S
    n = S.router_post_route_final_count(log_txt)
    return None if n is None else f"FINAL_COUNT {n}"


def _judge_signoff_rigor(report_text: str) -> Optional[str]:
    import sta_signoff_rigor_check as S
    found = S._check_types_violations(report_text)
    return f"RIGOR_VIOLATIONS {len(found)}" if found else None


_ROUTE_LOG_PROV = (
    "A REAL OpenROAD 26Q3-2075-g18e98f9e44 session on 8HD-6, unedited: "
    "`calibration/route_completed_positive.log` is a two-inverter "
    "`sky130_fd_sc_hd` chain taken floorplan -> place -> global_route -> "
    "detailed_route (it ends `[INFO DRT-0702] Post-route verification: 0 "
    "violation(s).` / `[INFO DRT-0501] Runtime: 0.22s`), and "
    "`calibration/route_aborted_negative.log` is the SAME design whose "
    "detailed route never ran (`[ERROR DRT-0047] Design has no global routing "
    "guides.`). One has a route, the other does not, and nothing else differs.")

_register(Instrument(
    name="phase3_one_shot_runner::_detail_route_completed",
    reads="the PnR log's own routing-completion line",
    ruling="R-0915-69 (same channel)",
    owner="icsha2",
    why=("This is the evidence that separates `routing failed` from `routing "
         "succeeded`. It is the second reader of the log R-0915-69 was about, "
         "and an uncalibrated second reader of a channel whose first reader was "
         "wrong is not a safer place to be."),
    judge=_judge_detail_route_completed,
    positive=Sample(provenance=_ROUTE_LOG_PROV,
                    artefact=_read("route_completed_positive.log")),
    expect="ROUTE_COMPLETED",
    negative=Sample(provenance=_ROUTE_LOG_PROV,
                    artefact=_read("route_aborted_negative.log")),
))

_register(Instrument(
    name="phase3_one_shot_runner::route_modified_after_last_verification",
    reads="the PnR log — order of the last verification against later changes",
    ruling="R-0915-69d",
    owner="icsha2",
    why=("R-0915-69 lets a cosmetic refusal stand on the router's own "
         "verification, and R-0915-69d is the qualification: that verification "
         "may be OLDER than the last change to the route. The distinction is "
         "ORDER, so both samples carry the verification and differ only in what "
         "comes after it."),
    judge=_judge_route_modified,
    positive=Sample(
        provenance=("The real routed log with the antenna loop's two marker "
                    "lines after its `[INFO DRT-0702]` — the run15 order."),
        artefact=_read("antenna_cosmetic_negative.log")),
    expect="MODIFIED_AFTER_VERIFICATION",
    negative=Sample(
        provenance=("The SAME real log with nothing after the verification. "
                    "If it fires here it is reporting the verification, not "
                    "the order."),
        artefact=_read("route_completed_positive.log")),
))

_register(Instrument(
    name="phase3_one_shot_runner::_antenna_isolated_recovery_modified/post_verify_mutation",
    reads="the isolated OpenROAD scoped-route log after DRT-0711",
    ruling="R-0915-86 (T47f)",
    owner="T47f",
    why=("A DRT-0711 zero-violation line gives no release credit if a later "
         "mutation invalidated that verification. Both calibration sessions "
         "route the same two-inverter cal_chain; only the positive session "
         "removes its routed n1 net after verification. This second pair "
         "checks post-verification mutation at the same reader as the "
         "landed T57 pair for absent final verification. MEASURED on the "
         "real OpenROAD pair: the post-verification n1 deletion fires while "
         "the clean final route stays silent; the landed T57 pair separately "
         "measures an absent final verification. Both are verdicts from "
         "the one reader and must assert at that reader's source."),
    calls_at="phase3_one_shot_runner::_antenna_isolated_recovery_modified",
    judge=_judge_antenna_isolated_recovery_modified,
    positive=Sample(
        provenance=("Real OpenROAD 26Q3-2627-g9e33179906 on 8HD-4, image "
                    "sha256:7c343bfae1ff672176bbc76f2355faa3cc5d84d163aabee9992736454d063610; "
                    "calibration/isolated_route_modified.tcl routes cal_chain "
                    "then destroys n1 after DRT-0711. Full unedited stdout."),
        artefact=_read("isolated_route_modified_positive.log")),
    expect="MODIFIED_AFTER_VERIFICATION",
    negative=Sample(
        provenance=("Same real OpenROAD image and cal_chain input; "
                    "calibration/isolated_route_clean.tcl exits immediately "
                    "after scoped DRT-0711. Full unedited stdout."),
        artefact=_read("isolated_route_clean_negative.log")),
))

_register(Instrument(
    name=("phase3_one_shot_runner::"
          "antenna_reroute_refusal_after_last_verification"),
    reads="the PnR log — reroute refusals later than the last verification",
    ruling="R-0915-69d",
    owner="icsha2",
    why=("The disclosure that tells a reader the shipped route was modified "
         "after the router last checked it. Same channel, same order question, "
         "its own pair."),
    judge=_judge_refusal_after_verification,
    positive=Sample(
        provenance=("The real routed log with the two `REPAIR_ANTENNA_REROUTE_"
                    "NONFATAL` lines after `[INFO DRT-0702]`."),
        artefact=_read("antenna_cosmetic_negative.log")),
    expect="REFUSAL_AFTER_VERIFICATION",
    negative=Sample(
        provenance="The SAME real log with no refusal in it at all.",
        artefact=_read("route_completed_positive.log")),
))

_register(Instrument(
    name="_signoff_drc_format::router_loop_iter_counts",
    reads="the router's own per-iteration violation counts",
    ruling="(sign-off DRC formatting)",
    owner="icsha2",
    why=("The sign-off DRC number a reader sees comes from this. A reader that "
         "cannot tell a log WITH a verification from one WITHOUT reports the "
         "empty case as a clean case, which is the same shape as R-0915-83."),
    judge=_judge_router_iter_counts,
    positive=Sample(provenance=_ROUTE_LOG_PROV,
                    artefact=_read("route_completed_positive.log")),
    expect="ITER_COUNTS [0]",
    negative=Sample(provenance=_ROUTE_LOG_PROV,
                    artefact=_read("route_aborted_negative.log")),
))

_register(Instrument(
    name="_signoff_drc_format::router_post_route_final_count",
    reads="the router's last post-route verification count",
    ruling="(sign-off DRC formatting)",
    owner="icsha2",
    why=("`None` and `0` are different facts here — nobody looked versus the "
         "router looked and found nothing — and the pair is exactly those two."),
    judge=_judge_router_final_count,
    positive=Sample(provenance=_ROUTE_LOG_PROV,
                    artefact=_read("route_completed_positive.log")),
    expect="FINAL_COUNT 0",
    negative=Sample(provenance=_ROUTE_LOG_PROV,
                    artefact=_read("route_aborted_negative.log")),
))

_register(Instrument(
    name="sta_signoff_rigor_check::_check_types_violations",
    reads="OpenSTA's `report_check_types` output (recovery/removal/min pulse)",
    ruling="(sign-off rigor)",
    owner="icaes",
    why=("It reports a clean sign-off when it finds nothing, so the direction "
         "it must never fail in is the false clean — and the negative here is a "
         "report that is EMPTY because the design is clean, which is the same "
         "byte shape as a report nobody wrote."),
    judge=_judge_signoff_rigor,
    positive=Sample(
        provenance=(
            "REAL `report_check_types -min_pulse_width -violators` output, "
            "OpenSTA 2.7.0 on 8HD-6: a 4-bit registered adder mapped to the "
            "PDK's own cells by yosys 0.68+, clocked at 0.05 ns so the PDK's "
            "own `min_pulse_width` arcs are genuinely violated — 10 "
            "`(VIOLATED)` rows under the tool's `Required Width / Actual "
            "Width` header. `calibration/mpw_violating_positive.rpt`."),
        artefact=_read("mpw_violating_positive.rpt")),
    expect="RIGOR_VIOLATIONS 10",
    negative=Sample(
        provenance=("The SAME netlist and the SAME command at a 20 ns clock: "
                    "OpenSTA writes a **0-byte** report. "
                    "`calibration/mpw_clean_negative.rpt`."),
        artefact=_read("mpw_clean_negative.rpt")),
))

_register(Instrument(
    name="phase3_one_shot_runner::_sparse_gpl_retry_deck",
    reads="OpenROAD GPL area and divergence diagnostics",
    ruling="GPL-2597",
    owner="phase3",
    why=("A retry is justified only when the tool reports GPL-0305 and its "
         "measured movable-to-region ratio is below the existing sparse "
         "limit. A clean placement transcript must not trigger a retry."),
    judge=_judge_sparse_gpl_retry,
    positive=Sample(
        provenance=("Real OpenROAD GPL-0015, GPL-0018 and GPL-0305 lines "
                    "captured from the 2026-09-25 pinned-image placement "
                    "failure; selected verbatim into "
                    "calibration/gpl_sparse_diverged_positive.log."),
        artefact=_read("gpl_sparse_diverged_positive.log")),
    expect="GPL_SPARSE_RETRY",
    negative=Sample(
        provenance=("Real successful OpenROAD placement and routing of the "
                    "calibration chain, in route_verified_negative.log; no "
                    "GPL-0305 was emitted."),
        artefact=_read("route_verified_negative.log")),
))

_register(Instrument(
    name="phase3_one_shot_runner::_pnr_rollback_refused_antenna_repair",
    reads="OpenROAD DRT verification and antenna violation transcript",
    ruling="R-0915-74 / scoped route recovery",
    owner="phase3",
    why=("The full-route retry may be promoted only after the router verified "
         "zero DRC and the antenna census found zero violations. The "
         "negative is a real router abort and must fall back to rollback."),
    judge=_judge_antenna_rollback,
    positive=Sample(
        provenance=("Real OpenROAD two-inverter-chain transcript in "
                    "route_verified_negative.log, with the runner's own "
                    "completion markers and zero ANT-0001/0002 lines "
                    "observed in a successful OpenROAD antenna run. The EDA "
                    "session writes are supplied at the tool boundary."),
        artefact=lambda: _antenna_retry_log(True)),
    expect="RECOVERED",
    negative=Sample(
        provenance=("Real OpenROAD two-inverter-chain abort transcript in "
                    "route_aborted_negative.log; it has no final DRT-0702 "
                    "verification, so it cannot authorize the retry."),
        artefact=lambda: _antenna_retry_log(False)),
))

_register(Instrument(
    name="phase3_one_shot_runner::_antenna_isolated_recovery_modified",
    reads="the isolated OpenROAD retry transcript after the runner's ECO marker",
    ruling="T57 / R-0915-74(b)", owner="icdrcfb",
    why=("A route without the scoped router's final zero-DRC verification "
         "cannot be disclosed as verified at ship. The isolated retry must "
         "leave the verification as its last route-changing event."),
    judge=_judge_isolated_recovery,
    positive=Sample(
        provenance=("Real T57 OpenROAD isolate.log from the copied run23: "
                    "antenna repair inserts four diodes but no DRT-0711 "
                    "verification follows. Only the runner's own ECO marker "
                    "is prepended when exercising the reader."),
        artefact=_read("antenna_isolated_unverified_positive.log")),
    expect="ROUTE_UNVERIFIED",
    negative=Sample(
        provenance=("Real T57 OpenROAD isolated_retry_final.log from the same "
                    "copied run23: DRT-0711 reports whole-design zero on "
                    "entry and exit after DRT-0633/0634, followed by the "
                    "ANT-0001/0002 zero census."),
        artefact=_read("antenna_isolated_verified_negative.log")),
))

_register(Instrument(
    name="phase3_one_shot_runner::_antenna_isolated_drc_markers",
    reads="OpenROAD detailed_route -output_drc marker report",
    ruling="T67 / scoped DRT-0712", owner="icdrcfb",
    why=("A native Short with layer, nets and bbox may drive a local diode "
         "site trial; an empty clean report must not invent a marker."),
    judge=_judge_antenna_isolated_drc_markers,
    positive=Sample(
        provenance=("Real 0.3.76 OpenROAD scoped reroute on the copied T66 "
                    "antenna seed: DRT-0712 wrote one Metal2 Short to "
                    "-output_drc; original report bytes retained."),
        artefact=_read("antenna_scoped_drc_short_positive.rpt")),
    expect="DRC_MARKERS:1",
    negative=Sample(
        provenance=("Real 0.3.76 OpenROAD scoped reroute from the same "
                    "copied seed after legal diode relocation: router DRC "
                    "0 to 0 and an empty -output_drc file."),
        artefact=_read("antenna_scoped_drc_clean_negative.rpt")),
))

_register(Instrument(
    name="drc_feedback_repair::_antenna",
    reads="OpenROAD check_antennas ANT-0001/0002 lines",
    ruling="T63", owner="icdrcfb",
    why="A violating antenna census must fire; the zero census stays silent.",
    judge=_judge_feedback_antenna,
    positive=Sample(
        provenance=("Real T57 OpenROAD jumper.log, copied run23: last "
                    "ANT-0002 and ANT-0001 report four net and pin violations."),
        artefact=_read("feedback_antenna_violating_positive.log")),
    expect="ANTENNA_VIOLATIONS:4/4",
    negative=Sample(
        provenance=("Real T63 OpenROAD baseline.antenna.log, copied run23: "
                    "ANT-0002 and ANT-0001 both report zero."),
        artefact=_read("feedback_antenna_clean_negative.log")),
))

_register(Instrument(
    name="drc_feedback_repair::_native_scoped_guard",
    reads="OpenROAD DRT-0633/0634/0711 scoped route proof lines",
    ruling="T63", owner="icdrcfb",
    why=("A retry without native evidence that the named nets were touched "
         "and all other wires held fixed must refuse the trial."),
    judge=_judge_feedback_scoped,
    positive=Sample(
        provenance=("Real T57 OpenROAD isolate.log, copied run23: the "
                    "antenna repair leaves four nets unwired and emits no "
                    "DRT-0633/0634/0711 scoped proof. A one-net request must "
                    "therefore be refused."),
        artefact=lambda: (_read("feedback_native_unverified_positive.log")(),
                          ("calibration_target",))),
    expect="WIRE_GUARD_REFUSED",
    negative=Sample(
        provenance=("Real T63 OpenROAD trial1.log: DRT-0633 names one net, "
                    "holds 1175 others, DRT-0634 confirms one touched and "
                    "1175 unchanged, and DRT-0711 confirms zero DRC. The "
                    "one-element target set matches the tool's named count."),
        artefact=lambda: (_read("feedback_native_held_negative.log")(),
                          ("calibration_target",))),
))

_register(Instrument(
    name="drc_feedback_repair::run",
    reads="the KLayout deck transcript and its selected-rule RDB",
    ruling="T63", owner="icdrcfb",
    why=("The run's strict-decrease decision reads a selected-rule report. "
         "The same original deck must show a real marker before reroute and "
         "no marker on the candidate; the production _measure reader checks "
         "both the deck execution transcript and the RDB."),
    judge=_judge_feedback_measure,
    positive=Sample(
        provenance=("Real T63 KLayout baseline.drc.log and baseline.rpt from "
                    "the copied run23; the selected original deck reports "
                    "one CO.6a edge-pair marker."),
        artefact=lambda: ("feedback_rule_present_positive.log",
                          "feedback_rule_present_positive.rdb")),
    expect="SIGNOFF_RULE_PRESENT",
    negative=Sample(
        provenance=("Real T63 KLayout candidate1.drc.log and candidate1.rpt "
                    "from the same copied run23, same original deck: the RDB "
                    "items are empty."),
        artefact=lambda: ("feedback_rule_zero_negative.log",
                          "feedback_rule_zero_negative.rdb")),
))


# The P0 front end reads Verilator's fixed diagnostic grammar. Calibrate its
# code reader on transcripts emitted by the released image before it judges.
def _judge_p0_verilator_codes(log: str) -> Optional[str]:
    import p0_tool_frontend_check as p0
    return ("SELRANGE" if any(row["code"] == "SELRANGE"
                           for row in p0._diagnostic_codes(log)) else None)


_register(Instrument(
    name="p0_tool_frontend_check::_diagnostic_codes",
    reads="Verilator --lint-only --Wall diagnostic transcript",
    ruling="T86", owner="mig-p0",
    why=("P0 substitutes Verilator diagnostic codes for hand-written RTL "
         "regex scans. The code prefix must identify a real out-of-range "
         "selection and stay silent on unrelated tool warnings."),
    judge=_judge_p0_verilator_codes,
    positive=Sample(
        provenance=("Real Verilator 5.053 (released vibeic-eda:0.3.67) "
                    "--lint-only --Wall -Wno-fatal transcript on synthetic "
                    "RTL selecting bit 5 of logic [3:0]; generated on "
                    "192.168.1.114. calibration/p0_verilator_positive.log."),
        artefact=_read("p0_verilator_positive.log")),
    expect="SELRANGE",
    negative=Sample(
        provenance=("Same released Verilator on a valid one-module RTL. "
                    "DECLFILENAME and UNUSEDSIGNAL warnings are present "
                    "but no SELRANGE; generated on 192.168.1.114. "
                    "calibration/p0_verilator_negative.log."),
        artefact=_read("p0_verilator_negative.log")),
))


# ---- LibreLane OpenROAD.STAPrePNR readers (steps 8 and 10, T92) -----------

_STA_PREPNR_RUN = (
    "Real LibreLane 3.1.0.dev1 OpenROAD.STAPrePNR (OpenSTA via `sta`) in the "
    "released vibeic-eda 0.3.77 image, run by digest on 8hd-3 (.121) on "
    "2026-09-26 through librelane_contract.run_chain, nom_tt_025C_5v00 "
    "corner of gf180mcuD. Calibration structure: calibration/"
    "cal_chain_gf180.v (two dffq_1 around one inv_1, no design under test)")


def _judge_sta_black_box(log: str) -> Optional[str]:
    import librelane_prelayout as L
    return "BLACK_BOX" if L.black_boxes(log) else None


def _judge_sta_sdc_diag(sample: Tuple[str, str]) -> Optional[str]:
    import librelane_prelayout as L
    log, sdc = sample
    return "SDC_DIAGNOSTIC" if L.sdc_diagnostics(log, Path(sdc)) else None


def _judge_sta_check_setup(rpt: str) -> Optional[str]:
    import librelane_prelayout as L
    counts = L.check_setup_counts(rpt)
    if counts is None:
        return "NOT_MEASURED"
    return "UNTIMED" if any(counts.values()) else None


_register(Instrument(
    name="librelane_prelayout::black_boxes",
    reads="OpenSTA sta.log written by LibreLane STAPrePNR (link_design)",
    ruling="T92 step 10", owner="mig-sdcsta",
    why=("OpenSTA links a master missing from every liberty as an empty black "
         "box and exits 0; the slack it then reports omits that logic "
         "(harvest: 'No paths found' / wns 0.00 where the linked run had "
         "-33.88 ns). The pair differs only in one cell master."),
    judge=_judge_sta_black_box,
    positive=Sample(
        provenance=(_STA_PREPNR_RUN + ", with i0's master renamed to "
                    "cal_absent_master (calibration/cal_chain_absent.v). "
                    "Unedited sta.log; carries Warning 198 'Creating black "
                    "box for i0'. calibration/sta_prepnr_black_box_positive.log"),
        artefact=_read("sta_prepnr_black_box_positive.log")),
    expect="BLACK_BOX",
    negative=Sample(
        provenance=(_STA_PREPNR_RUN + ", unmodified netlist and "
                    "calibration/cal_full.sdc. Unedited sta.log. "
                    "calibration/sta_prepnr_linked_clean_negative.log"),
        artefact=_read("sta_prepnr_linked_clean_negative.log")),
))

_register(Instrument(
    name="librelane_prelayout::sdc_diagnostics",
    reads="OpenSTA sta.log diagnostics raised while reading the SDC",
    ruling="T92 step 8", owner="mig-sdcsta",
    why=("A constraint aimed at a port OpenSTA cannot find is a warning, the "
         "run exits 0 and the constraint applies to nothing. A regex over the "
         "SDC text cannot see it; OpenSTA's own read_sdc diagnostic can."),
    judge=_judge_sta_sdc_diag,
    positive=Sample(
        provenance=(_STA_PREPNR_RUN + ", SDC calibration/cal_bad_port.sdc "
                    "(set_input_delay on a port that does not exist). "
                    "Unedited sta.log; Warning 366 at cal_bad_port.sdc line 2. "
                    "calibration/sta_prepnr_sdc_bad_port_positive.log"),
        artefact=lambda: (_read("sta_prepnr_sdc_bad_port_positive.log")(),
                          "cal_bad_port.sdc")),
    expect="SDC_DIAGNOSTIC",
    negative=Sample(
        provenance=(_STA_PREPNR_RUN + ", SDC calibration/cal_full.sdc. "
                    "calibration/sta_prepnr_linked_clean_negative.log"),
        artefact=lambda: (_read("sta_prepnr_linked_clean_negative.log")(),
                          "cal_full.sdc")),
))

_register(Instrument(
    name="librelane_prelayout::check_setup_counts",
    reads="OpenSTA check_setup section of STAPrePNR checks.rpt",
    ruling="T92 step 8", owner="mig-sdcsta",
    why=("A staged SDC declaring only create_clock leaves every primary I/O "
         "untimed, which turns a red sign-off green by subtraction. OpenSTA's "
         "check_setup counts the missing input delays and unconstrained "
         "endpoints; the pair differs only in the SDC."),
    judge=_judge_sta_check_setup,
    positive=Sample(
        provenance=(_STA_PREPNR_RUN + ", SDC calibration/cal_clock_only.sdc. "
                    "Unedited checks.rpt: 1 input port missing "
                    "set_input_delay, 2 unconstrained endpoints. "
                    "calibration/sta_prepnr_check_setup_clock_only_positive.rpt"),
        artefact=_read("sta_prepnr_check_setup_clock_only_positive.rpt")),
    expect="UNTIMED",
    negative=Sample(
        provenance=(_STA_PREPNR_RUN + ", SDC calibration/cal_full.sdc. "
                    "Unedited checks.rpt with an empty check_setup section. "
                    "calibration/sta_prepnr_check_setup_clean_negative.rpt"),
        artefact=_read("sta_prepnr_check_setup_clean_negative.rpt")),
))


def _eqy_scratch(name: str) -> Callable[[], Path]:
    """Unpack an EQY sample (its unedited status files and partition.list,
    stored as one tar so every fixture stays a flat file) into a temp dir."""
    def _load() -> Path:
        import tarfile
        out = Path(tempfile.mkdtemp(prefix="cal_eqy_"))
        with tarfile.open(FIXTURES / f"{name}.tar") as tar:
            tar.extractall(out)  # our own fixture: relative paths only
        return out
    return _load


def _judge_eqy_partitions(scratch: Path) -> Optional[str]:
    import librelane_eqy as E
    parts = E.partition_status(scratch)
    if parts is None:
        return "NOT_MEASURED"
    if any("FAIL" in s.values() and "PASS" not in s.values() for s in parts.values()):
        return "NOT_EQUIVALENT"
    return None


_register(Instrument(
    name="librelane_eqy::partition_status",
    reads="EQY per-partition strategy status files (scratch/strategies/*/*/status)",
    ruling="T92 step 13", owner="mig-sdcsta",
    why=("EQY's top-level status folds 'undecided' into FAIL, so the step-13 "
         "arm-B reader works per partition. The pair differs only in one gate "
         "cell: inv (equivalent) vs buf (not) on a defined combinational cone."),
    judge=_judge_eqy_partitions,
    positive=Sample(
        provenance=("Real LibreLane 3.1.0.dev1 Yosys.EQY in the released "
                    "vibeic-eda 0.3.77 image on 8hd-3 (.121), 2026-09-26, with "
                    "EQY v0.69 plugins built from YosysHQ/eqy against the "
                    "image's Yosys 0.69 (the image ships none). Gold "
                    "calibration/cal_chain_rtl.v, gate calibration/"
                    "cal_eqy_gate_noneq.v (buf_1 where inv_1 belongs). Every "
                    "strategy status file and partition.list, unedited, in "
                    "calibration/eqy_not_equivalent_positive.tar"),
        artefact=_eqy_scratch("eqy_not_equivalent_positive")),
    expect="NOT_EQUIVALENT",
    negative=Sample(
        provenance=("Same run, gate calibration/cal_eqy_gate.v (inv_1). Every "
                    "partition PASS. calibration/eqy_equivalent_negative.tar"),
        artefact=_eqy_scratch("eqy_equivalent_negative")),
))


def _judge_eqy_xbits(scratch: Path) -> Optional[str]:
    import librelane_eqy as E
    found = E.xbit_partitions(scratch)
    if found is None:
        return "NOT_MEASURED"
    return "XBITS_VACUOUS" if found else None


_register(Instrument(
    name="librelane_eqy::xbit_partitions",
    reads="EQY partition.list (`xbits` partition tags)",
    ruling="T92 step 13", owner="mig-sdcsta",
    why=("EQY's miter passes any point that is x on the gold side. With a "
         "resetless flop and no init, a non-equivalent gate cell still ends "
         "`DONE (PASS, rc=0)`; the only trace is the `xbits` tag."),
    judge=_judge_eqy_xbits,
    positive=Sample(
        provenance=("Real LibreLane Yosys.EQY run (0.3.77 image, EQY v0.69 "
                    "plugins, 8hd-3, 2026-09-26): gold calibration/"
                    "cal_xbits_rtl.v (resetless registered output), gate "
                    "calibration/cal_chain_noninv.v (buf_1 where inv_1 "
                    "belongs), script without an init. Unedited partition.list "
                    "and status files (EQY logged `DONE (PASS, rc=0)` over the "
                    "non-equivalent gate). calibration/eqy_xbits_vacuous_positive.tar"),
        artefact=_eqy_scratch("eqy_xbits_vacuous_positive")),
    expect="XBITS_VACUOUS",
    negative=Sample(
        provenance=("Same tool, gold calibration/cal_chain_rtl.v, gate "
                    "calibration/cal_eqy_gate.v, script with `setundef -init "
                    "-zero` (librelane_eqy.eqy_script). No partition tagged "
                    "xbits. calibration/eqy_defined_init_negative.tar"),
        artefact=_eqy_scratch("eqy_defined_init_negative")),
))


def _judge_handoff_constants(netlist: str) -> Optional[str]:
    import synth_handoff_netlist_check as H
    return "CONSTANT_NOT_TIED" if H.constant_connections(netlist) else None


_register(Instrument(
    name="synth_handoff_netlist_check::constant_connections",
    reads="the Yosys write_verilog handoff netlist (step 14)",
    ruling="T92 step 14", owner="mig-sdcsta",
    why=("A constant left as a literal instead of a tie cell becomes an "
         "OpenROAD zero_/one_ net and fails detailed route (DRT-0305). The "
         "pair is one RTL synthesised with and without hilomap."),
    judge=_judge_handoff_constants,
    positive=Sample(
        provenance=("Real Yosys 0.69+ 4d572059c (vibeic-eda 0.3.77, 8hd-3, "
                    "2026-09-26): `synth -flatten; dfflibmap; abc -liberty "
                    "<gf180mcu tt lib>; opt_clean -purge; write_verilog "
                    "-noattr` of calibration/cal_const_rtl.v, NO hilomap. "
                    "Unedited: `assign lo = 1'h0; assign hi = 1'h1;`. "
                    "calibration/synth_const_no_hilomap_positive.v"),
        artefact=_read("synth_const_no_hilomap_positive.v")),
    expect="CONSTANT_NOT_TIED",
    negative=Sample(
        provenance=("Same RTL through LibreLane 3.1.0.dev1 Yosys.Synthesis in "
                    "the same image (tieh/tiel placed). Unedited netlist. "
                    "calibration/synth_const_tied_negative.v"),
        artefact=_read("synth_const_tied_negative.v")),
))


# ══════════════════════════════════════════════════════════════════════════
#  THE RULE
# ══════════════════════════════════════════════════════════════════════════

#: Memoised per process. Calibration runs the real pair — a subprocess for the
#: progress detector, tclsh for the DRV census — and an instrument asked twice
#: in one run must not pay for it twice. This is a cache, not a mode: there is
#: no flag, env var or argument anywhere in this module that skips a check.
_CACHE: Dict[str, Calibration] = {}

#: THE BASE CASE OF THE RECURSION, and it is not an escape hatch.
#:
#: Calibrating an instrument RUNS it: `check("…::scan")` calls `scan`, which
#: calls `assert_calibrated("…::scan")`. Without a base case that is infinite,
#: and the first wiring of this module was exactly that. While an instrument's
#: OWN pair is running, its `assert_calibrated` is a no-op — the calibration IS
#: the check, and a check cannot be its own precondition. Nothing else can put a
#: name in here: it is set by `check` and cleared in a `finally`, it is not
#: reachable from any argument, environment variable or public function, and
#: `test_the_reentrancy_guard_admits_only_the_instrument_being_checked` pins
#: that a name is in it only for the duration of its own check.
_IN_PROGRESS: set = set()


def check(name: str) -> Calibration:
    """Run both sides of `name`'s pair. CALIBRATED iff both hold."""
    if name in _CACHE:
        return _CACHE[name]
    inst = INSTRUMENTS.get(name)
    if inst is None:
        cal = Calibration(name, MISCALIBRATED, failed_sides=("unregistered",),
                          detail="not in INSTRUMENTS")
        _CACHE[name] = cal
        return cal
    failed: List[str] = []
    detail: List[str] = []
    _IN_PROGRESS.add(name)
    try:
        return _run_pair(inst, failed, detail)
    finally:
        _IN_PROGRESS.discard(name)


def _run_pair(inst: Instrument, failed: List[str],
              detail: List[str]) -> Calibration:
    name = inst.name
    try:
        pos = inst.judge(inst.positive.artefact())
    except Exception as exc:
        pos = None
        detail.append(f"positive raised {type(exc).__name__}: {exc}")
    if pos is None:
        failed.append("positive")
        detail.append("the known-positive did NOT make it fire")
    elif inst.expect not in pos:
        failed.append("positive")
        detail.append(f"fired with {pos!r}, expected {inst.expect!r}")
    try:
        neg = inst.judge(inst.negative.artefact())
    except Exception as exc:
        neg = f"ERROR:{type(exc).__name__}:{exc}"
        detail.append(f"negative raised {type(exc).__name__}: {exc}")
    if neg is not None:
        failed.append("negative")
        detail.append(f"the known-negative made it fire: {neg!r}")
    cal = Calibration(
        name=name,
        state=MISCALIBRATED if failed else CALIBRATED,
        positive_outcome=pos, negative_outcome=neg,
        failed_sides=tuple(failed), detail="; ".join(detail))
    _CACHE[name] = cal
    return cal


def check_all() -> Dict[str, Calibration]:
    return {n: check(n) for n in sorted(INSTRUMENTS)}


def assert_calibrated(name: str) -> None:
    """THE ONE LINE AN INSTRUMENT CALLS BEFORE IT JUDGES.

    Raises `Uncalibrated` when `name` is not in the registry, or is and fails
    either side of its pair. The caller records `NOT_MEASURED` with
    `reason_class = "uncalibrated"`; it is never a PASS and never a FAIL,
    because both would be a verdict nobody measured.
    """
    if name in _IN_PROGRESS:
        return                      # this call IS the calibration; see _IN_PROGRESS
    cal = check(name)
    if cal.state != CALIBRATED:
        raise Uncalibrated(name, cal.detail or cal.state)


def verdict_for(name: str) -> Optional[Tuple[str, str]]:
    """`("NOT_MEASURED", "uncalibrated")` when `name` may not judge, else None.

    For a caller that wants the verdict rather than the exception. Same
    question, same answer, no second rule.
    """
    if name in _IN_PROGRESS:
        return None
    cal = check(name)
    if cal.state == CALIBRATED:
        return None
    return ("NOT_MEASURED", UNCALIBRATED)


# ══════════════════════════════════════════════════════════════════════════
#  THE RATCHET — who else is judging from a tool artefact?
# ══════════════════════════════════════════════════════════════════════════
#
# THE PREDICATE, AND THE THREE ANSWERS IT WAS TUNED PAST. Measured on
# f145d8133 while this was written, because each wrong answer looked clean:
#
#   "reads a file and writes a status"                       385 functions.
#       `read_text` is not evidence of reading a TOOL artefact.
#   "carries [A-Z]{2,5}-\\d{3,5} outside its docstring"       268 in 78 modules.
#       Top carriers: profibus (18), lora (11), zigbee (10) — `RS-485`,
#       `MIL-STD-1553`, `IEC-61158`. PROTOCOL NAMES, not diagnostics.
#   restricted to the repo's own measured OpenROAD/OpenSTA prefix family
#       (`tool_diagnostic_id_gate._RE_BRACKETED`)            115 in 25 modules,
#       mostly Tcl the flow EMITS rather than evidence it READS.
#   restricted further to a MATCH OPERATION over that grammar  13 in 5 modules
#       — and it MISSED `antenna_routing_incomplete`, the instrument R-0915-69
#       is about, because its markers live in a module-level tuple and its regex
#       is a prefix ALTERNATION with no concrete digits.
#
# So the predicate resolves module-level constants, and accepts a regex SOURCE
# that encodes the grammar as well as a concrete id. A scan that cannot see the
# load-bearing instrument is the defect this module exists to refuse.

#: Family A — the OpenROAD suite's bracketed ids, and the same ids bare. The
#: prefix list is the one `tool_diagnostic_id_gate` RE-DERIVES from this repo's
#: own logs; it is spelled as an alternation so a `\\d{3,4}`-style regex SOURCE
#: matches too, which is how `_TOOL_ERROR_CODE_RE` is written.
_TOOL_PREFIXES = (
    "ANT", "CTS", "DPL", "DRT", "EST", "GPL", "GRT", "IFP", "ODB", "ORD",
    "PDN", "PPL", "PSM", "RCX", "RSZ", "TAP", "STA", "MPL", "PAR", "UPF",
    "GUI", "DFT",
)
_TOOL_ID = re.compile(
    r"\b(?:" + "|".join(_TOOL_PREFIXES) + r")-(?:\d{3,5}|\\d\{\d(?:,\d)?\})")

#: Family B — tool transcript grammars with no id at all. Each is a literal a
#: TOOL prints, and each is the channel one of the eight rulings was read off.
#: A phrase enters this tuple only with the tool that emits it named beside it.
_TOOL_PHRASES = (
    "Putting delay",              # iverilog $sdf_annotate
    "Unable to match ModPath",    # iverilog $sdf_annotate
    "Illegal overlap",            # magic's extractor
    "feedback add",               # magic's `feedback save` format
    "problems occurred",          # magic's feedback denominator
    "Circuits match",             # netgen's LVS verdict
    "(VIOLATED)",                 # OpenSTA report_check_types
    "Post-route verification",    # OpenROAD detailed_route
    "Number of violations",       # OpenROAD detailed_route
    "no estimated parasitics",    # OpenROAD resizer (EST-0027 text)
)

#: A producer record's own verdict field — the `lec.json` channel. Reading one
#: of these and turning it into a status is judging from a tool artefact just as
#: much as reading a log is; R-0915-82 lived here.
_RECORD_VERDICT_KEYS = ("verdict", "equivalent", "compared_points",
                        "non_equivalent_points", "unproven_points")

#: What "turns it into a status" looks like in the AST.
_STATUS_KEYS = {"status", "verdict", "result", "disposition", "outcome"}
_STATUS_WORDS = set(VERDICTS) | {
    "SKIP", "SKIPPED", "SKIPPED-CONDITION", "INCOMPLETE", "INCONCLUSIVE",
    "NOT_CHECKED", "NOT_EXECUTED", "BLOCKED", "STALLED", "NO_TOOL", "REFUSED",
    "ERROR", "MISSING", "WAIVED", "ADVISORY", "VACUOUS_PASS",
}

#: Judged and registered elsewhere, or not an instrument at all. Each entry
#: names WHY, and `_ratchet_verdict` refuses an entry whose function no longer
#: exists — so this cannot rot into a list of things that used to be true.
#: THERE IS NO FLAG THAT WRITES THIS. It is reviewed in the diff like any source.
_UNCALIBRATED_REGISTER: Dict[str, str] = {
    # ── magic's extraction channel ────────────────────────────────────────
    "magic_illegal_overlap_check::check":
        "icsha2 — the project-level driver around "
        "`parse_feedback` (registered). Its pair needs a staged "
        "`phase3/stage3/extracted/` with the transcript, the recipe and the "
        "extracted netlist beside each feedback dump; the two real dumps are "
        "already in `calibration/` and this is the wiring, not the artefact.",

    # ── channels whose artefact a calibration structure does not produce ──
    "phase3_one_shot_runner::_extract_overutil_pct":
        "icsha2 — needs a real `[ERROR GPL-0301]`. MEASURED on 8HD-6: a "
        "two-inverter chain squeezed to a 12x12 um die reports `[INFO "
        "IFP-0104] Effective utilization: 0.100` and places; at 6x6 it fails "
        "earlier with `[ERROR IFP-0065] No rows created`. A calibration "
        "structure cannot overflow a placer.",
    "phase3_one_shot_runner::_postroute_timing_repair_log_verdict":
        "icsha2 — needs a resizer transcript carrying `RSZ-0098 No setup "
        "violations` against a sign-off that found one; a two-cell chain has "
        "no setup path to violate.",
    "phase3_one_shot_runner::_emit_ir_em_reports":
        "icaes — needs a PSM/IR session over a real power grid: a "
        "two-cell calibration chain has no PDN, so neither side exists "
        "on a structure this registry may build.",
    "dynamic_ir_vectored_emit::emit":
        "icaes — same channel: a vectored IR run needs switching activity over "
        "a real grid.",
    "psm_analysis_coverage::analysis_coverage":
        "icaes — needs a real `[WARNING PSM-00xx]` coverage transcript.",
    "psm_analysis_coverage::unconnected_instances":
        "icaes — the same PSM transcript as the entry above; the two are "
        "calibrated together or not at all, and neither side exists on a "
        "structure with no power grid.",
    "cts_quality_check::_cts_declared_masters":
        "icsha2 — needs a CTS log; a two-cell chain has no clock tree.",
    "route_congestion_trade_disclosure::congestion_aborted":
        "icsha2 — needs a `global_route` that genuinely runs out of "
        "capacity; a two-cell chain on a 40x40 um die routes with 6 vias "
        "and cannot congest.",
    "_signoff_drc_format::router_post_route_verified_superseded":
        "icsha2 — needs a log with TWO post-route verifications where the "
        "later one supersedes the earlier. MEASURED: both real logs in "
        "`calibration/` return None, so neither side of a pair exists yet.",
    "_signoff_drc_format::_last_0701_not_superseded":
        "icsha2 — a private helper of the entry above, and it takes the "
        "caller's compiled pattern rather than a log: it is calibrated when "
        "its caller is.",

    # ── a code the PINNED IMAGE does not carry ───────────────────────────
    "phase3_one_shot_runner::_sdr_repair_parasitics_disclosure":
        "icmainred2 — I tried to calibrate it first and MEASURED that neither "
        "side of a pair exists on the image this flow runs. The reader counts "
        "`EST-0027` in the SDR child logs, and the pinned digest "
        "sha256:943f53b3 (OpenROAD 26Q3-2578-g36711c6c34) does not carry that "
        "string ANYWHERE: `strings /foss/tools/openroad/bin/openroad | grep -c "
        "EST-0027` is 0, `grep -oE '\\bEST-[0-9]{4}\\b'` over the same "
        "binary returns NOTHING AT ALL, the phrase `no estimated parasitics` "
        "is absent from it, and `grep -rl EST-0027 /foss/tools` finds no file. "
        "A positive sample would have to be a transcript this toolchain cannot "
        "produce, and a negative one is every transcript it does — so the pair "
        "would prove nothing about the reader. IT IS A DISCLOSURE AND NOT A "
        "VERDICT (`estimate_parasitics: not used`, a count and a file list; no "
        "status field), which is why an uncalibrated reading is survivable "
        "here at all. AND IT CARRIES A FINDING, stated rather than hidden by "
        "this entry: on this image `est0027_warnings` can only ever be 0, so "
        "the number is the absence of the code and not the absence of the "
        "condition. R-0915-94's figures were taken before the pin moved. "
        "CLOSES when either the counted code is re-derived from a message the "
        "pinned image does emit, or the image carries EST-0027 again — at "
        "which point both samples are producible from `calibration/cal_chain.v` "
        "plus `cal_chain.spef`, the pair the DRV-census instrument already "
        "uses for exactly this with/without-parasitics distinction.",
}


def _string_constants(fn: ast.AST) -> List[str]:
    doc = ast.get_docstring(fn) if isinstance(
        fn, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if doc is not None and n.value == doc:
                continue
            out.append(n.value)
    return out


def _carries_grammar(strings: List[str]) -> bool:
    hay = "\n".join(strings)
    if _TOOL_ID.search(hay):
        return True
    return any(p in hay for p in _TOOL_PHRASES)


def _grammar_names(tree: ast.Module) -> set:
    """Module-level names bound to a constant that CARRIES a tool grammar.

    Tuples, lists, bare strings and `re.compile(...)` all count: the defect
    R-0915-69 is about kept its markers in a tuple, and a scan that only looked
    at inline literals could not see it.
    """
    names = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not _carries_grammar(_string_constants(node.value)):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _reads_record_verdict(fn: ast.AST) -> bool:
    """`doc.get("verdict")` / `doc["compared_points"]` — the record channel."""
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "get" and n.args:
            a = n.args[0]
            if isinstance(a, ast.Constant) and a.value in _RECORD_VERDICT_KEYS:
                return True
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and n.slice.value in _RECORD_VERDICT_KEYS:
            return True
    return False


def _matches_grammar(fn: ast.AST, grammar_names: set) -> bool:
    """Does this function APPLY a tool grammar to text it is reading?

    Emitting a grammar into a Tcl deck is not reading one, which is why this
    asks for a match OPERATION and not for the literal's presence.
    """
    for n in ast.walk(fn):
        if isinstance(n, ast.Compare) and any(
                isinstance(o, ast.In) for o in n.ops):
            if _carries_grammar(_string_constants(n.left)):
                return True
            if isinstance(n.left, ast.Name) and n.left.id in grammar_names:
                return True
            for c in n.comparators:
                if isinstance(c, ast.Name) and c.id in grammar_names:
                    return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            attr = n.func.attr
            if attr in ("count", "startswith", "endswith", "find", "index",
                        "split", "partition", "rpartition", "splitlines"):
                if _carries_grammar([a.value for a in n.args
                                     if isinstance(a, ast.Constant)
                                     and isinstance(a.value, str)]):
                    return True
            if attr in ("search", "match", "fullmatch", "findall", "finditer",
                        "sub", "subn", "split"):
                f = n.func.value
                if isinstance(f, ast.Name) and f.id in grammar_names:
                    return True
                if _carries_grammar([a.value for a in n.args
                                     if isinstance(a, ast.Constant)
                                     and isinstance(a.value, str)]):
                    return True
        if isinstance(n, ast.Name) and n.id in grammar_names:
            # A bare reference to a grammar constant inside a comprehension or
            # a `for` — `any(m in log for m in _MARKERS)` is exactly this.
            continue
    # `any(m in text for m in _MARKERS)` / `[l for l in ... if M in l]`
    for n in ast.walk(fn):
        if isinstance(n, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            for gen in n.generators:
                if isinstance(gen.iter, ast.Name) and gen.iter.id in grammar_names:
                    return True
        if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) \
                and n.iter.id in grammar_names:
            return True
    return False


def _emits_status(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Subscript) and isinstance(
                        t.slice, ast.Constant) and isinstance(
                            t.slice.value, str) \
                        and t.slice.value.lower() in _STATUS_KEYS:
                    return True
        if isinstance(n, ast.Dict):
            for k in n.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                        and k.value.lower() in _STATUS_KEYS:
                    return True
        if isinstance(n, ast.keyword) and n.arg \
                and n.arg.lower() in _STATUS_KEYS:
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and n.value in _STATUS_WORDS:
            return True
        if isinstance(n, ast.Return) and isinstance(
                n.value, ast.Constant) and n.value.value in (True, False):
            # A boolean the caller turns into a verdict — `routing_incomplete`.
            return True
    return False


def scan(root: Path) -> List[str]:
    """`module::function` for every function that READS TOOL-EMITTED GRAMMAR.

    THE PREDICATE IS THE MATCH, NOT THE VERDICT, and that is a measurement
    rather than a preference. Taken on 599640dcf, over `programs/*.py`:

        applies a tool grammar ........................  19 functions
        reads a producer record's verdict field ....... 581
        emits a status word ......................... 4,019
        grammar AND status ............................   6

    "Reads a tool artefact AND writes a status" is the sentence R-0915-86 uses,
    and implemented literally it names 476 — because `doc.get("verdict")` is how
    every gate reads a SIBLING GATE's record, which is not a tool artefact, and
    because printing `[FAIL]` is how every gate speaks. Adding the status clause
    to the grammar clause does the opposite damage: it drops
    `sdf_gate_sim::sdf_annotation_census`, which returns three counts and lets
    its caller name the verdict — the instrument R-0915-75 is about.

    So the population is the CHANNEL: a function that applies a tool's own
    message grammar to text it is reading has an opinion about what a tool said,
    and that opinion becomes a verdict somewhere. Whether it does so in its own
    body or one frame up is a call-graph question this cannot answer soundly,
    and answering it wrongly in either direction loses a real instrument.

    Emitting a grammar into a Tcl deck is NOT reading one: `_matches_grammar`
    asks for a match OPERATION (`in`, `.count`, `.startswith`, an `re` call, or
    a `for`/comprehension over a module-level grammar constant), which is what
    separates the 19 from the 115 functions that merely carry a tool id.
    """
    found: List[str] = []
    prog = root / "programs"
    for p in sorted(prog.glob("*.py")):
        if p.stem.startswith("test_") or p.stem == Path(__file__).stem:
            continue
        try:
            tree = ast.parse(p.read_text(errors="replace"))
        except (OSError, SyntaxError):
            continue
        gnames = _grammar_names(tree)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _matches_grammar(n, gnames):
                found.append(f"{p.stem}::{n.name}")
    return sorted(set(found))


def _module_present(root: Path, name: str) -> bool:
    """Does this subject carry `programs/<module>.py` for `module::function`?"""
    module, _, _fn = name.partition("::")
    return (root / "programs" / f"{module}.py").is_file()


def _defines_function(root: Path, name: str) -> bool:
    module, _, fn = name.partition("::")
    src = root / "programs" / f"{module}.py"
    if not src.is_file():
        return False
    try:
        tree = ast.parse(src.read_text(errors="replace"))
    except (OSError, SyntaxError):
        return False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == fn for n in ast.walk(tree))


def _ratchet_verdict(population: List[str], root: Path) -> int:
    """`population == INSTRUMENTS + register`, by MEMBERSHIP.

    Two ways to fail, and they are the two directions of one rule:
      * an instrument judging from a tool artefact that is in NEITHER — a
        landing ADDED an uncalibrated judge;
      * a register entry whose function is in this tree and which the scan no
        longer names, or which no longer exists at all — the entry outlived its
        reason and must be deleted in the commit that closed it.
    """
    known = set(INSTRUMENTS) | set(_UNCALIBRATED_REGISTER)
    new = [n for n in population if n not in known]
    # THE REGISTER SPEAKS ONLY ABOUT MODULES THIS SUBJECT CARRIES. A subject
    # without `programs/<module>.py` at all is not a tree the entry is about —
    # it is a different tree — and reporting eleven "does not exist" findings
    # over one would bury the finding that matters. When the module IS here,
    # both directions are live: the function gone is an entry that outlived its
    # instrument, and the function present but no longer named by the scan is an
    # entry whose offender was fixed without deleting its line.
    here = [n for n in _UNCALIBRATED_REGISTER if _module_present(root, n)]
    stale = [n for n in here
             if _defines_function(root, n) and n not in population]
    gone = [n for n in here if not _defines_function(root, n)]
    rc = 0
    if new:
        rc = 1
        print(f"[FAIL] {len(new)} instrument(s) judge from a tool artefact and "
              f"are NOT calibrated:")
        for n in new:
            print(f"    {n}")
        print("    Each must be registered in INSTRUMENTS with a pair "
              "(a known-positive that MUST fire, a known-negative that MUST "
              "stay silent), or declared in _UNCALIBRATED_REGISTER with an "
              "owner. An uncalibrated instrument's verdict is NOT_MEASURED "
              f"(reason_class {UNCALIBRATED!r}).")
    for n in stale:
        rc = 1
        print(f"[FAIL] _UNCALIBRATED_REGISTER entry {n} is no longer an "
              f"uncalibrated instrument — delete it in the commit that fixed it")
    for n in gone:
        rc = 1
        print(f"[FAIL] _UNCALIBRATED_REGISTER entry {n} does not exist in this "
              f"tree")
    if rc == 0:
        print(f"[OK] {len(population)} instrument(s) judge from a tool "
              f"artefact; {len(INSTRUMENTS)} calibrated, "
              f"{len(_UNCALIBRATED_REGISTER)} declared.")
    return rc


# ══════════════════════════════════════════════════════════════════════════

def _population_lines(root: Path, cals: Optional[Dict[str, Calibration]] = None,
                      population: Optional[List[str]] = None) -> None:
    """WHAT THIS VERDICT WAS TAKEN OVER, printed before it.

    A verdict about a population that never says how big the population was is
    a claim nobody can check — and `--root` is only meaningful if something
    actually reads it, so the tool-grammar scan is counted here in BOTH modes.
    """
    if population is not None:
        scanned = len(population)          # already walked; never walk twice
    else:
        try:
            scanned = len(scan(root))
        except Exception:
            scanned = 0
    cals = cals if cals is not None else {}
    bad = sum(1 for c in cals.values() if c.state != CALIBRATED)
    print(f"  instruments registered:         {len(INSTRUMENTS)}")
    print(f"  calibrated:                     {len(cals) - bad}")
    print(f"  miscalibrated:                  {bad}")
    print(f"  declared uncalibrated:          {len(_UNCALIBRATED_REGISTER)}")
    print(f"  tool-grammar readers in tree:   {scanned}")


def _report(cals: Dict[str, Calibration]) -> None:
    for name in sorted(cals):
        c = cals[name]
        inst = INSTRUMENTS[name]
        mark = "OK " if c.state == CALIBRATED else "RED"
        print(f"[{mark}] {c.state:14s} {name}")
        print(f"        reads   : {inst.reads}")
        print(f"        ruling  : {inst.ruling}  (owner {inst.owner})")
        print(f"        positive: {c.positive_outcome!r}")
        print(f"        negative: {c.negative_outcome!r}")
        if c.state != CALIBRATED:
            print(f"        WHY     : {c.detail}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".",
                    help="plugin root holding programs/")
    ap.add_argument("--report", action="store_true",
                    help="run every pair and print both sides")
    ap.add_argument("--ratchet", action="store_true",
                    help="refuse an instrument that is not calibrated")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    if a.ratchet:
        try:
            population = scan(root)
        except Exception as exc:                       # pragma: no cover
            print(f"[ERROR] could not scan {root}: {exc}")
            return 2
        _population_lines(root, population=population)
        rc = _ratchet_verdict(population, root)
        if a.json_out:
            _atomic_write_json(a.json_out, {
                "gate": GATE, "mode": "ratchet", "population": population,
                "calibrated": sorted(INSTRUMENTS),
                "declared": _UNCALIBRATED_REGISTER, "rc": rc})
        return rc

    cals = check_all()
    _population_lines(root, cals)
    _report(cals)
    bad = [n for n, c in cals.items() if c.state != CALIBRATED]
    if a.json_out:
        _atomic_write_json(a.json_out, {
            "gate": GATE, "mode": "calibrate",
            "instruments": {n: c.as_dict() for n, c in cals.items()},
            "miscalibrated": bad})
    if bad:
        print(f"\n[FAIL] {len(bad)} of {len(cals)} instrument(s) MISCALIBRATED "
              f"— each may not judge; its verdict is NOT_MEASURED "
              f"(reason_class {UNCALIBRATED!r}).")
        return 1
    print(f"\n[OK] {len(cals)} instrument(s) CALIBRATED.")
    return 0


if __name__ == "__main__":                              # pragma: no cover
    sys.exit(main())
