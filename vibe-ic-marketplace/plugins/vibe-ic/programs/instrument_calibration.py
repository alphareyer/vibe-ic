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
import sysconfig
import tempfile
import types
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
# `tclsh` with the tool commands stubbed. The two arms differ in what OpenSTA
# WROTE: the `report_check_types -violators` file and the
# `report_parasitic_annotation -report_unannotated` census of the same session,
# one `read_spef` apart. `get_pins`/`get_property`/`get_full_name` answer from
# the pin inventory OpenROAD printed for the same linked design
# (`calibration/cal_chain_pins.txt`), identical in both arms.

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


def _tcl_word(text: str) -> str:
    return "{" + text + "}"


def run_drv_census(report_text: Optional[str],
                   annotation_text: Optional[str] = None) -> str:
    """Run the EMITTED census in tclsh over one session's two reports.

    `report_text` is the `report_check_types -violators` file and
    `annotation_text` the `report_parasitic_annotation -report_unannotated`
    file the session left behind. `None` means the command never wrote one,
    `""` means it wrote an EMPTY one -- and OpenSTA writes exactly that
    violator report for a session with no parasitics AND for a clean design
    with them, which is why the annotation census is what decides.

    Only tool commands are stubbed; the Tcl is the deck's own text, cut out by
    its own markers. This EXECUTES the census instead of grepping it.
    """
    block = _drv_census_block()
    pins = [ln.split() for ln in
            (FIXTURES / "cal_chain_pins.txt").read_text().splitlines()
            if ln.strip()]
    inventory = " ".join(
        f"{_tcl_word(name)} {{direction {d} is_hierarchical {h}}}"
        for name, d, h in pins)
    with tempfile.TemporaryDirectory() as td:
        rpt = Path(td) / "sdr_drv.rpt"
        ann = Path(td) / "sdr_ann.rpt"
        if report_text is not None:
            rpt.write_text(report_text)
        if annotation_text is not None:
            ann.write_text(annotation_text)
        prelude = (
            "proc define_process_corner {args} {}\n"
            "proc extract_parasitics {args} {}\n"
            "proc write_spef {args} {}\n"
            "proc read_spef {args} {}\n"
            "proc report_check_types {args} {}\n"
            "proc report_parasitic_annotation {args} {}\n"
            f"set ::_cal_pins [dict create {inventory}]\n"
            "proc get_pins {args} { return [dict keys $::_cal_pins] }\n"
            "proc get_full_name {obj} { return $obj }\n"
            "proc get_property {obj prop} "
            "{ return [dict get $::_cal_pins $obj $prop] }\n"
            "set _sdr_tx_error 0\n")
        body = (block.replace(f"{_TCL_OUT_DIR}/sdr_drv.rpt", str(rpt))
                     .replace(f"{_TCL_OUT_DIR}/sdr_ann.rpt", str(ann)))
        script = prelude + "while {1} {\n" + body + "\nbreak\n}\n"
        path = Path(td) / "census.tcl"
        path.write_text(script)
        cp = subprocess.run(["tclsh", str(path)], capture_output=True,
                            text=True)
    return ((cp.stdout or "") + (cp.stderr or "")).strip()


def _judge_drv_census(
        session: Tuple[Optional[str], Optional[str]]) -> Optional[str]:
    """Fires with the refusal line; silent when it reports a number."""
    out = run_drv_census(*session)
    if "SDR_DRV_CENSUS_NOT_MEASURED" in out:
        if "SDR_DRV_BY_KIND" in out:
            return "REFUSED_AND_ALSO_REPORTED_A_NUMBER"
        return [l for l in out.splitlines()
                if "SDR_DRV_CENSUS_NOT_MEASURED" in l][0].strip()[:120]
    return None


def _drv_arm_no_parasitics() -> Tuple[str, str]:
    """The REAL session with no parasitics in STA: an EMPTY violator report
    and an annotation census that lists every driver."""
    return ((FIXTURES / "drv_no_parasitics_positive.rpt").read_text(),
            (FIXTURES / "drv_no_parasitics_positive.ann").read_text())


def _drv_arm_with_parasitics() -> Tuple[str, str]:
    return ((FIXTURES / "drv_with_parasitics_negative.rpt").read_text(),
            (FIXTURES / "drv_with_parasitics_negative.ann").read_text())


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

def _judge_sdf_errors(transcript: str) -> Optional[str]:
    import sdf_gate_sim as SG
    count = SG.sdf_error_count(transcript)
    return f"SDF ERROR RECORDS: {count}" if count else None


_register(Instrument(
    name="sdf_gate_sim::sdf_error_count",
    reads="the `vvp -sdf-info` transcript of the gate-level simulation",
    ruling="T106 (step 29 tool arm gates on SDF ERROR = 0)",
    owner="mig106",
    why=("Step 29's tool arm refuses a corner whose simulation dropped any SDF "
         "record. MEASURED on spm x gf180mcuD: every STAPostPNR corner SDF left "
         "63 refused records per case (xor2 / mux2 unconditional IOPATH against "
         "the models' conditional specify paths). The pair differs only in the "
         "cell: a gf180 mux2 (conditional S->Z paths) and an inverter chain."),
    judge=_judge_sdf_errors,
    positive=Sample(
        provenance=(
            "REAL `vvp -sdf-info` transcript, 8HD-4, vibeic-eda 0.3.79 (by "
            "digest). Netlist: `calibration/cal_sdf_error_mux.v`, one "
            "`gf180mcu_fd_sc_mcu7t5v0__mux2_2`; models: the PDK's own "
            "`primitives.v` + `gf180mcu_fd_sc_mcu7t5v0.v`; compiled `iverilog "
            "-g2012 -ginterconnect -gspecify`. SDF: OpenSTA 3.1.0 `write_sdf "
            "-include_typ -divider .` at the tt liberty. MEASURED: 2 `SDF "
            "ERROR` (Unable to match ModPath S -> Z)."),
        artefact=_read("sdf_error_positive.log")),
    expect="SDF ERROR RECORDS",
    negative=Sample(
        provenance=(
            "The SAME PDK, models, compile and SDF writer; the netlist is "
            "`calibration/cal_sdf_error_inv.v`, two `inv_1` in a chain. "
            "MEASURED: 0 `SDF ERROR`, 3 `Putting delay`."),
        artefact=_read("sdf_error_negative.log")),
))

def _sdf_class_bundle(side: str) -> Callable[[], Dict[str, str]]:
    def _load() -> Dict[str, str]:
        return {"transcript": _read(f"cal_sdf_class_{side}.log")(),
                "compile_log": _read(f"cal_sdf_class_{side}.compile.log")(),
                "sdf": _read("cal_sdf_class.sdf")(),
                "netlist": _read("cal_sdf_class.v")(),
                "cal_sdf_class_cells.v": _read("cal_sdf_class_cells.v")(),
                "cal_sdf_class_pad.v": _read("cal_sdf_class_pad.v")()}
    return _load


def _judge_sdf_error_classes(bundle: Dict[str, str]) -> Optional[str]:
    import sdf_gate_sim as SG
    explainer = SG.SdfErrorExplainer(bundle["netlist"], {
        name: bundle[name] for name in ("cal_sdf_class_cells.v", "cal_sdf_class_pad.v")})
    classes = SG.classify_sdf_errors(bundle["transcript"],
                                     compile_log=bundle["compile_log"],
                                     sdf_text=bundle["sdf"], explainer=explainer)
    return (f"UNEXPLAINED SDF ERROR RECORDS: {classes['unexplained']}"
            if classes["unexplained"] else None)


_register(Instrument(
    name="sdf_gate_sim::classify_sdf_errors",
    reads=("the `vvp -sdf-info` transcript, the iverilog compile log, the SDF, "
           "the gate netlist and the cell/pad Verilog models of one simulation"),
    ruling="F21 (step 29 refuses on an SDF ERROR no class explains)",
    owner="migf21",
    why=("Step 29 counts every refused SDF record by class and fails only on an "
         "unexplained one. MEASURED on spm x gf180mcuD: 315 per corner, two "
         "classes, both Icarus's -- 62/case `ifnone` edge paths the parser "
         "dropped (`sorry` in the compile log; fork PR vibeic/iverilog#4) and "
         "1/case INTERCONNECT on a bidirectional pad's inout PAD (vibeic/"
         "iverilog#5). A class is granted only on this run's own proof: the "
         "`sorry` names that cell's `ifnone` line; the model declares the "
         "endpoint `inout`. The pair is ONE structure and ONE SDF: without "
         "`-gspecify` the same records multiply with a cause neither class "
         "names, and the reader must see them."),
    judge=_judge_sdf_error_classes,
    positive=Sample(
        provenance=(
            "REAL transcripts, 8HD-4, vibeic-eda 0.3.79 (by digest), Icarus 14.0 "
            "(devel) 07454266b. Structure `calibration/cal_sdf_class.v`: one "
            "`gf180mcu_fd_sc_mcu7t5v0__mux2_2` driving one `gf180mcu_fd_io__bi_24t` "
            "whose PAD is the top output. Models: `calibration/cal_sdf_class_cells.v` "
            "and `calibration/cal_sdf_class_pad.v`, verbatim excerpts of the PDK's "
            "own models. SDF `calibration/cal_sdf_class.sdf`: OpenSTA 3.1.0 "
            "`write_sdf -include_typ -divider .` at the tt liberties. Bench "
            "`calibration/cal_sdf_class_tb.v`. Compiled `iverilog -g2012 "
            "-ginterconnect` WITHOUT `-gspecify`: MEASURED 19 `SDF ERROR`, of "
            "which 16 (`Unable to match COND ModPath` and plain paths with no "
            "`ifnone`) no class explains."),
        artefact=_sdf_class_bundle("positive")),
    expect="UNEXPLAINED SDF ERROR RECORDS",
    negative=Sample(
        provenance=(
            "The SAME structure, models, SDF and bench, compiled `iverilog -g2012 "
            "-ginterconnect -gspecify` (the flags step 29 uses). MEASURED: 3 `SDF "
            "ERROR` -- 2 `Unable to match ModPath S -> Z` with the compile log's "
            "`sorry: ifnone with an edge-sensitive path` on the mux2's two `ifnone` "
            "lines, 1 `Could not find intermodpath!` on `(INTERCONNECT u_pad.PAD p)` "
            "-- every one explained; 10 `Putting delay`."),
        artefact=_sdf_class_bundle("negative")),
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
    reads=("the OpenSTA session's `report_check_types -violators` file and "
           "its `report_parasitic_annotation -report_unannotated` census"),
    ruling="R-0915-83",
    owner="icaes",
    why=("`extract_parasitics` fills the ODB; OpenSTA sees nothing until a "
         "SPEF is read back, so a census taken without one reports 0 and is "
         "byte-identical to a clean design -- and the loop reads 0 as "
         "convergence, which makes the UNMEASURED case look best (R-0915-83). "
         "The pair EXECUTES the emitted census in tclsh over the REAL reports "
         "OpenSTA wrote for the same design one `read_spef` apart.\n\n"
         "This entry was registered MISCALIBRATED (a2f33ad76) and the judge "
         "was the defect: `_sdr_par_ok` was set by `read_spef` RETURNING, so "
         "the parasitics half of the refusal was unreachable, and the empty "
         "violator report was counted as 0. The empty report cannot be the "
         "discriminator -- MEASURED in vibeic-eda 0.3.79, OpenSTA writes the "
         "same 0 bytes for the clean design WITH parasitics. What does "
         "discriminate is OpenSTA's own annotation census: every driver is "
         "listed with no parasitics (and after a `read_spef` of a SPEF whose "
         "names do not match, which returns without error), none with them. "
         "The deck now sets the flag from that census only."),
    judge=_judge_drv_census,
    positive=Sample(
        provenance=(
            "The REAL session with NO `read_spef`, two-inverter "
            "`sky130_fd_sc_hd__inv_2` chain `calibration/cal_chain.v` at "
            "`set_max_capacitance 0.004`: `report_check_types -max_slew "
            "-max_capacitance -max_fanout -violators` wrote **0 bytes** over a "
            "design that has 2 violators "
            "(`calibration/drv_no_parasitics_positive.rpt`; first captured "
            "with OpenSTA 2.7.0, re-captured byte-identical with OpenROAD "
            "26Q3-2963-gc73a322d30 in vibeic-eda 0.3.79), and "
            "`report_parasitic_annotation -report_unannotated` wrote `Found 3 "
            "unannotated drivers.` listing A, u1/Y, u2/Y "
            "(`calibration/drv_no_parasitics_positive.ann`, 83 bytes, "
            "OpenROAD 26Q3-2963 in 0.3.79). A `read_spef` of a SPEF whose "
            "names do not match returns without error and writes the SAME "
            "83 bytes."),
        artefact=_drv_arm_no_parasitics),
    expect="SDR_DRV_CENSUS_NOT_MEASURED",
    negative=Sample(
        provenance=(
            "The SAME design, SAME limit, SAME commands -- one `read_spef "
            "cal_chain.spef` apart, where the SPEF is a genuine OpenRCX "
            "extraction with the PDK's own "
            "`rules.openrcx.sky130A.max.magic` (`[INFO RCX-0045] Extract 1 "
            "nets, 6 rsegs, 6 caps, 3 ccs`). The violator report is 285 "
            "bytes, 2 `(VIOLATED)` lines under a `max capacitance` heading "
            "(`calibration/drv_with_parasitics_negative.rpt`, re-captured "
            "byte-identical in 0.3.79); the annotation census is `Found 0 "
            "unannotated drivers.` (`calibration/drv_with_parasitics_"
            "negative.ann`, 68 bytes, OpenROAD 26Q3-2963 in 0.3.79). The "
            "deck counts them: `SDR_DRV_BY_KIND: total=2 max_capacitance=2`."),
        artefact=_drv_arm_with_parasitics),
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


# ---- the LEC counterexample search (FX_LEC_BMC_CEX) ------------------------

#: The depth both calibration runs searched to (`lec_run.bmc_script` rungs
#: 1, 2, 4, 8, 16 cycles after reset).
_BMC_CAL_DEPTH = 16


def _judge_bmc(log: str) -> Optional[str]:
    import lec_run
    got = lec_run.parse_bmc_log(log, _BMC_CAL_DEPTH)
    if got["result"] != lec_run.BMC_COUNTEREXAMPLE:
        return None
    cex = got["counterexample"]
    return (f"COUNTEREXAMPLE cycle={cex['cycle']} "
            f"outputs={','.join(cex['differing_outputs'])}")

_register(Instrument(
    name="lec_run::parse_bmc_log",
    reads="yosys `sat -seq` output on the port miter lec_run builds",
    ruling="FX_LEC_BMC_CEX",
    owner="fxlock",
    why=("The equiv ladder never prints a counterexample, and lec_run wrote "
         "`non_equivalent_points: 0` as a constant, so an unclosed LEC could "
         "not be told apart from a real sequential mismatch. The count now "
         "comes from this reader, so it must fire on a real model and stay "
         "silent on a real search that found none."),
    judge=_judge_bmc,
    positive=Sample(
        provenance=(
            "yosys 0.69+ 4d572059c in vibeic-eda 0.3.83 (8HD-4, 2026-09-28), "
            "the x-aware search (`miter -ignore_gold_x`, `sat -enable_undef "
            "-set-def-inputs -set-init-undef`) from an undefined GOLD and a "
            "defined gate (`setundef -zero -init gate`). calibration/cal_bmc_rtl.v (a counter, "
            "synchronous active-high reset, registered `hit <= q == 4`) "
            "against calibration/cal_bmc_gate_planted.v: its gf180mcuD "
            "netlist (cal_bmc_gate.v, synthesised by that yosys) with ONE real "
            "edit made in a scratch copy, or4 `_27_` .A1 `_06_` (= ~q[2]) -> "
            "q[2], so `hit` fires at q == 0. Predicted before the run: reset "
            "in step 1, en in step 2, `hit` differs in step 3 and nowhere "
            "else. The script is lec_run's own ladder read cut before "
            "`equiv_make` plus `bmc_script`; paths rewritten to "
            "<project>/<reports>."),
        artefact=_read("lec_bmc_cex_positive.log")),
    expect="COUNTEREXAMPLE cycle=3 outputs=hit",
    negative=Sample(
        provenance=(
            "Same yosys and search on a design whose registers are defined "
            "only LATE, the trap an all-zero start falls into (review_wave6 "
            "BMC): calibration/cal_bmc_rsync_rtl.v resets a 4-state FSM "
            "through a 2-flop synchroniser (no register is defined until "
            "cycle 3), and its gf180mcuD netlist cal_bmc_rsync_gate.v (same "
            "yosys) re-encoded the FSM one-hot. Started at all-zero "
            "(`-set-init-zero`) the pair gave `model found: FAIL!` (busy at "
            "step 2); from an undefined gold and a defined gate every rung "
            "to 16 cycles after reset ends `no model found: SUCCESS!`. The x don't-care pair "
            "(cal_bmc_xdc_*) is held by the tests."),
        artefact=_read("lec_bmc_rsync_negative.log")),
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


# Step 2 judges LibreLane Verilator.Lint's transcript. The reader must fire on
# an error and a curated warning in the design, and stay silent on a warning
# outside the curated set.
def _judge_verilator_lint_gate(log: str) -> Optional[str]:
    import verilator_lint_gate as gate
    return gate._judge_calibration(log)


_register(Instrument(
    name="verilator_lint_gate::parse_transcript",
    reads="LibreLane Verilator.Lint verilator-lint.log",
    ruling="T91", owner="mig-rtlver",
    why=("Step 2 promotes %Error and curated Verilator warning codes to "
         "blocking. The head-line grammar must find LATCH and MULTIDRIVEN "
         "and leave UNUSEDSIGNAL non-blocking."),
    judge=_judge_verilator_lint_gate,
    positive=Sample(
        provenance=("Real LibreLane 3.1.0.dev1 Verilator.Lint (Verilator "
                    "5.053, released vibeic-eda 0.3.77 by digest) on a "
                    "synthetic three-always module: a combinational if "
                    "without else and one reg written from posedge and "
                    "negedge blocks; generated on 192.168.1.121 under "
                    "/tmp/vlcal. calibration/librelane_verilator_lint_pos.log."),
        artefact=_read("librelane_verilator_lint_pos.log")),
    expect="BLOCKING",
    negative=Sample(
        provenance=("Same step and image on a one-flop module reading one "
                    "bit of a two-bit input: only UNUSEDSIGNAL is reported. "
                    "calibration/librelane_verilator_lint_neg.log."),
        artefact=_read("librelane_verilator_lint_neg.log")),
))


# Step 3 (T91 mig-rtlver) judges CDC/RDC from a Yosys JSON netlist written with
# LibreLane Yosys.JsonHeader's passes. Each rule gets its own pair: the netlist
# grammar (`$dff`/`$adff` pins, `$mux` arms, port directions) is read three
# different ways, and a calibrated crossing rule says nothing about the reset
# rule. Samples: yosys 0.69+ 4d572059c in the released vibeic-eda 0.3.77
# image, `read_verilog -sv; hierarchy -check -top T -nokeep_prints
# -nokeep_asserts; rename -top T; proc; flatten; opt_clean -purge; json`, on
# the calibration structures `calibration/cdc_netlist_*.v`, produced on
# 192.168.1.121 on 2026-09-26.
def _cdc_netlist_rule(rule: str) -> Callable[[str], Optional[str]]:
    def _judge(path: str) -> Optional[str]:
        import _cdc_netlist
        import cdc_async_input_check
        nl = _cdc_netlist.load(FIXTURES / path)
        fn = {"reg_crossing": _cdc_netlist.reg_crossing_findings,
              "reset_dependency": _cdc_netlist.reset_dependency_findings}.get(rule)
        rows = fn(nl) if fn else _cdc_netlist.async_input_findings(
            nl, cdc_async_input_check.is_probable_async_port)
        blocking = sorted({r["rule"] for r in rows if r["severity"] == "ERROR"})
        return ",".join(blocking) or None
    return _judge


_CDC_NETLIST_PROV = ("Real Yosys JSON (released vibeic-eda 0.3.77, yosys 0.69+ "
                     "4d572059c, Yosys.JsonHeader passes) of calibration/"
                     "cdc_netlist_{src}; generated on 192.168.1.121. "
                     "calibration/{out}.")

for _rule, _fn, _pos_src, _neg_src, _expect, _why in (
        ("reg_crossing", "reg_crossing_findings", "cdc_unsync.v", "cdc_sync.v",
         "CDC_REG_NO_SYNC",
         "a clk_a flop sampled once by clk_b must fire; the same flop "
         "re-registered twice in clk_b must stay silent"),
        ("async_input", "async_input_findings", "async_raw_unsync.v",
         "async_raw_sync.v", "ASYNC_INPUT_NO_SYNC",
         "an async-named PORT used as an enable must fire; the same port "
         "behind a two-flop chain must stay silent"),
        ("reset_dependency", "reset_dependency_findings", "reset_circular.v",
         "reset_sync_ok.v", "CIRCULAR_RESET_DEPENDENCY",
         "a reset OR-ed with a flop it resets must fire; a two-flop reset "
         "synchroniser releasing a second reset domain must stay silent")):
    _pos = f"cdc_netlist_{_rule.split('_')[0]}_positive.json"
    _neg = f"cdc_netlist_{_rule.split('_')[0]}_negative.json"
    _register(Instrument(
        name=f"_cdc_netlist::{_fn}",
        reads="Yosys JSON netlist (LibreLane Yosys.JsonHeader passes)",
        ruling="T91", owner="mig-rtlver",
        why=("Step 3 moves its CDC/RDC rules from RTL regex onto the netlist; "
             + _why + "."),
        judge=_cdc_netlist_rule(_rule),
        positive=Sample(provenance=_CDC_NETLIST_PROV.format(src=_pos_src, out=_pos),
                        artefact=lambda _p=_pos: _p),
        expect=_expect,
        negative=Sample(provenance=_CDC_NETLIST_PROV.format(src=_neg_src, out=_neg),
                        artefact=lambda _n=_neg: _n),
    ))



def _judge_kind_prove_arm(log: str) -> Optional[str]:
    """Fires when the transcript's `smtbmc` prove ARM reads as an unbounded
    proof after `formal_property_run.fold_prove_arms`."""
    import formal_property_run as F
    cfg = F.parse_sby_config((FIXTURES / "sby_prove_arms.sby").read_text())
    lp = F.parse_sby_log(log, sby_stem="formal_ctr", seed=cfg)
    _, records = F.fold_prove_arms(lp)
    for rec in records:
        for arm in rec["arms"]:
            if arm["engine"].startswith("smtbmc") and arm["bound"] == "unbounded":
                return "UNBOUNDED_BY_KIND"
    return None


_register(Instrument(
    name="formal_property_run::fold_prove_arms",
    reads="the SymbiYosys transcript of a `mode prove` task driven by "
          "`smtbmc yices` (k-induction) beside the `abc pdr` task",
    ruling="T91 (review70 step 5 correction: smtbmc prove is real)",
    owner="mig-rtlver",
    why=("k-induction reports its two halves separately — `returned pass for "
         "basecase` then `returned FAIL for induction` — and only `DONE (...)` "
         "is the task verdict. A reader that takes the basecase line as the "
         "task status turns a bounded result into an unbounded proof. The pair "
         "is two real transcripts of the SAME .sby that differ only in whether "
         "the induction step closed."),
    judge=_judge_kind_prove_arm,
    positive=Sample(
        provenance=(
            "REAL SBY v0.67-31-g2c2f04e / yices-smt2 transcript, released "
            "vibeic-eda 0.3.77 image (by digest), 8hd-3 (.121), 2026-09-26. "
            "`calibration/sby_prove_arms.sby` is `emit_sby(..., "
            "kind_engine=KIND_PROVE_ENGINE)` over an 8-bit counter wrapping at "
            "9 with `assert(q <= 9)` (sources: tests/fixtures/sby_prove_arms/"
            "pass/). `successful proof by k-induction`, `DONE (PASS, rc=0)`."),
        artefact=_read("sby_prove_arms_pass.sby.log")),
    expect="UNBOUNDED_BY_KIND",
    negative=Sample(
        provenance=(
            "The SAME .sby, same image and host, counter wrapping at 50 with "
            "`assert(q != 200)` (tests/fixtures/sby_prove_arms/unknown/): true, "
            "but not 20-inductive. `returned pass for basecase`, `returned FAIL "
            "for induction`, `DONE (UNKNOWN, rc=4)` — abc pdr proves it."),
        artefact=_read("sby_prove_arms_unknown.sby.log")),
))

# Step 4 (T91): the tool's own lcov line union beside the h-stripped union.
def _judge_line_union(sample: Tuple[Tuple[str, ...], str]) -> Optional[str]:
    import verilator_coverage_measure as vcm
    dats, info = sample
    got = vcm.cross_check_line_union(
        vcm.union_line_map([str(FIXTURES / d) for d in dats]),
        vcm.parse_lcov_info((FIXTURES / info).read_text()), ["cov_cal.v"])
    if got.get("status") != "MEASURED":
        return "NOT_MEASURED:" + str(got.get("reason"))
    return None if got["agree"] else "LINE_UNION_DISAGREES"


_register(Instrument(
    name="verilator_coverage_measure::cross_check_line_union",
    reads="verilator_coverage --write-info lcov union vs coverage.dat records",
    ruling="T91", owner="mig-rtlver",
    why=("Two producers of one line union must name the same lines and the "
         "same hits. Reading only a point's `l` field and not its `S` span "
         "dropped an `else` line lcov reports; the pair keeps that visible."),
    judge=_judge_line_union,
    positive=Sample(
        provenance=("Real verilator 5.053 coverage.dat of two calibration "
                    "testbenches (tb_cal_idle, tb_cal_toggle) over the "
                    "six-line calibration module cov_cal, and the real "
                    "`verilator_coverage --write-info` union of both, "
                    "released vibeic-eda:0.3.77 on 192.168.1.121. The "
                    "h-stripped side is built from the idle member only, "
                    "so line 6 (hit only by the toggle member) differs."),
        artefact=lambda: (("cov_union_tb_idle.dat",),
                          "cov_union_suite_lcov.info")),
    expect="LINE_UNION_DISAGREES",
    negative=Sample(
        provenance=("Same real files: both members on both sides agree "
                    "line for line and hit for hit."),
        artefact=lambda: (("cov_union_tb_idle.dat", "cov_union_tb_toggle.dat"),
                          "cov_union_suite_lcov.info")),
))


# Step 4 (T91): one cocotb TB under Icarus and Verilator, compared per case.
def _judge_sim_differential(sample: Tuple[str, str]) -> Optional[str]:
    import sim_dual_compare as sdc
    got = sdc.compare_junit(FIXTURES / sample[0], FIXTURES / sample[1])
    if got["verdict"] == "NOT_MEASURED":
        return "NOT_MEASURED:" + str(got.get("reason"))
    return got.get("finding")


_register(Instrument(
    name="sim_dual_compare::compare_junit",
    reads="cocotb JUnit results.xml from SIM=icarus and SIM=verilator",
    ruling="T91", owner="mig-rtlver",
    why=("A case whose outcome depends on the simulator is an X-semantics or "
         "race hazard. The reader must fire on a real per-case split and stay "
         "silent when both simulators pass the same cases."),
    judge=_judge_sim_differential,
    positive=Sample(
        provenance=("Real cocotb 2.2.0.dev JUnit, released vibeic-eda:0.3.77 "
                    "on 192.168.1.121: a calibration flop with no reset read "
                    "by int(dut.q.value); Icarus fails q_starts_low with "
                    "`Cannot convert Logic('X') to int`, Verilator passes."),
        artefact=lambda: ("sim_dual_icarus_x_positive.xml",
                          "sim_dual_verilator_x_positive.xml")),
    expect="SIM_DIFFERENTIAL_DISAGREES",
    negative=Sample(
        provenance=("Same calibration flop with a synchronous reset: both "
                    "simulators pass both cases."),
        artefact=lambda: ("sim_dual_icarus_negative.xml",
                          "sim_dual_verilator_negative.xml")),
))


def _judge_catalog_synth_safe(text: str) -> Optional[str]:
    import catalog_synth_safe_params_check as C
    rows = C.judge(json.loads(text), [{"ip_name": "cal", "files": ["ip.v"],
                                       "params": {"sim": 0}}])
    return "SYNTH_UNSAFE_PARAM" if any(not r["safe"] for r in rows) else None


_register(Instrument(
    name="catalog_synth_safe_params_check::judge",
    reads="Yosys write_json after `hierarchy -check -top` (step 1)",
    ruling="T95 step 1 (review70 correction: SYNTH_PARAMETERS reaches only the top)",
    owner="mig-front",
    why=("A catalog IP's synth-safe parameter is pinned by the glue at the "
         "instantiation, possibly through a wrapper. The pair is one IP and "
         "wrapper, instantiated once with sim=1 through the wrapper and once "
         "with sim=0; the reader must see the value the INNER module gets."),
    judge=_judge_catalog_synth_safe,
    positive=Sample(
        provenance=("Real Yosys 0.69+ 4d572059c (vibeic-eda 0.3.79, 8HD-4, "
                    "2026-09-26): `read_verilog -sv ip.v glue_bad.v; hierarchy "
                    "-top top; proc; write_json` of calibration/cal_catalog_ip.v "
                    "(staged as ip.v) and cal_catalog_glue_unsafe.v. Unedited. "
                    "calibration/catalog_synth_safe_unsafe_positive.json"),
        artefact=_read("catalog_synth_safe_unsafe_positive.json")),
    expect="SYNTH_UNSAFE_PARAM",
    negative=Sample(
        provenance=("The same command on cal_catalog_glue_pinned.v (the wrapper "
                    "instantiated with sim=0). Unedited. "
                    "calibration/catalog_synth_safe_pinned_negative.json"),
        artefact=_read("catalog_synth_safe_pinned_negative.json")),
))


def _judge_catalog_reuse_reached(text: str) -> Optional[str]:
    import catalog_synth_safe_params_check as C
    reached = C.reached_ips(json.loads(text), [
        {"ip_name": "cal", "files": ["ip.v"], "params": {"sim": 0}}])
    return "DECLARED_IP_NOT_REACHED" if "cal" not in reached else None


_register(Instrument(
    name="catalog_synth_safe_params_check::reached_ips",
    reads="Yosys write_json after `hierarchy -check -top` (step 1)",
    ruling="F28 (a declared catalog IP the top never reaches FAILs)",
    owner="migf28",
    why=("`hierarchy -top` drops every module the top does not reach, so an "
         "IP is reached when one of its source files still names a module. "
         "The pair reads the same IP file against a glue that instantiates it "
         "and a glue that does not; a reader that saw the IP from the files "
         "read, not from the elaborated netlist, would call both reached."),
    judge=_judge_catalog_reuse_reached,
    positive=Sample(
        provenance=("Real Yosys 0.69+ 4d572059c (vibeic-eda 0.3.79 by digest, "
                    "8hd-3, 2026-09-27): `read_verilog -sv ip.v "
                    "glue_unreached.v; hierarchy -top top; proc; write_json` of "
                    "calibration/cal_catalog_ip.v (staged as ip.v) and "
                    "cal_catalog_glue_unreached.v, whose top instantiates "
                    "nothing. Unedited. "
                    "calibration/catalog_reuse_unreached_positive.json"),
        artefact=_read("catalog_reuse_unreached_positive.json")),
    expect="DECLARED_IP_NOT_REACHED",
    negative=Sample(
        provenance=("The same IP with cal_catalog_glue_pinned.v, which "
                    "instantiates it (the ::judge negative). Unedited. "
                    "calibration/catalog_synth_safe_pinned_negative.json"),
        artefact=_read("catalog_synth_safe_pinned_negative.json")),
))


#: calibration/cal_two_clocks.sdc, the clocks both CTS samples were timed with.
_CTS_CAL_SOURCES = {"clk": "clk", "clk2": "clk2"}


def _judge_cts_clock_roots(log_txt: str) -> Optional[str]:
    import clock_plan_check as C
    return ("CTS_CLOCK_MISSING"
            if C.cts_missing_clocks(log_txt, _CTS_CAL_SOURCES) else None)


_CTS_CAL_PROV = (
    "Real OpenROAD 26Q3-2963-gc73a322d30 (the pinned vibeic-eda 0.3.79, 8HD-4, "
    "2026-09-26), unedited transcripts: read the gf180mcu_fd_sc_mcu7t5v0 nom "
    "tech LEF, cell LEF and tt liberty; a 60x60 um floorplan; place; "
    "`clock_tree_synthesis -buf_list clkbuf_4 -root_buf clkbuf_4`, with "
    "calibration/cal_two_clocks.sdc (create_clock clk on port clk, clk2 on "
    "port clk2). ")

_register(Instrument(
    name="clock_plan_check::cts_clock_roots",
    reads="the CTS transcript (`phase3/stage3/cts/clock_tree.rpt`)",
    ruling="review70 step 16 (T97)", owner="mig97",
    why=("Step 16's gate refuses an SDC clock CTS never took up; LibreLane's "
         "CTS only warns when CLOCK_PORT omits it. TritonCTS names a root it "
         "took up even when it then skips it for too few sinks (CTS-0041), so "
         "the pair is a root named vs a root never named -- not built vs "
         "skipped."),
    judge=_judge_cts_clock_roots,
    positive=Sample(
        provenance=(_CTS_CAL_PROV + "`-clk_nets clk` on "
                    "calibration/cal_two_clock_gf180.v (f0 on clk, f1 on "
                    "clk2): `[INFO CTS-0095] Net \"clk\" found.` and nothing "
                    "for clk2. calibration/cts_clock_root_dropped_positive.log"),
        artefact=_read("cts_clock_root_dropped_positive.log")),
    expect="CTS_CLOCK_MISSING",
    negative=Sample(
        provenance=(_CTS_CAL_PROV + "SDC roots, on "
                    "calibration/cal_two_clock_shared_gf180.v (both flops on "
                    "clk, clk2 unloaded): CTS-0007 names both clocks and "
                    "CTS-0041 skips clk2. "
                    "calibration/cts_clock_roots_seen_negative.log"),
        artefact=_read("cts_clock_roots_seen_negative.log")),
))


def _judge_pg_supply_ownership(pair: Tuple[str, str]) -> Optional[str]:
    import pg_supply_pin_ownership_check as C
    def_text, lef_text = pair
    rec = C.judge(def_text, [lef_text])
    return rec["code"] if rec["verdict"] == "FAIL" else None


_PG_SUPPLY_CAL_PROV = (
    "Real OpenROAD 26Q3-2963-gc73a322d30 (the pinned vibeic-eda 0.3.79, 8HD-4, "
    "2026-09-27), unedited `write_def` output: gf180mcu_fd_sc_mcu7t5v0 nom tech "
    "LEF + cell LEF, calibration/cal_pg_buf_gf180.v linked, a 40x40 um "
    "floorplan, the four `add_global_connection` rules a gf180 PnR deck "
    "registers (VDD/VNW -power, VSS/VPW -ground), `global_connect`, u0 placed. "
    "The LEF half is calibration/pg_supply_buf_1.lef, the buf_1 MACRO block "
    "copied verbatim from that cell LEF. ")

_register(Instrument(
    name="pg_supply_pin_ownership_check::judge",
    reads="a post-route candidate DEF + the LEFs of its masters",
    ruling="TF24 (lane migf24; T98 r2.3 finding for lane 32)", owner="migf24",
    why=("A fresh post-route session inserts cells after the PDN's one-shot "
         "global_connect with no rule registered; write_def then drops the "
         "`( * VDD )` wildcard and lists only the owned terminals, so an "
         "unpowered cell is visible only as an ABSENCE. The pair is the same "
         "database written before and after one such insertion."),
    judge=_judge_pg_supply_ownership,
    positive=Sample(
        provenance=(_PG_SUPPLY_CAL_PROV + "Then `odb::dbInst_create` of a "
                    "second buf_1 `hold1` (what the resizer does), placed, "
                    "`write_def`: VDD lists ( u0 VNW ) ( u0 VDD ) and no hold1. "
                    "calibration/pg_supply_unowned_positive.def"),
        artefact=lambda: (_read("pg_supply_unowned_positive.def")(),
                          _read("pg_supply_buf_1.lef")())),
    expect="PG_SUPPLY_PIN_OFF_SUPPLY_NET",
    negative=Sample(
        provenance=(_PG_SUPPLY_CAL_PROV + "`write_def` before the insertion: "
                    "VDD ( * VNW ) ( * VDD ). "
                    "calibration/pg_supply_owned_negative.def"),
        artefact=lambda: (_read("pg_supply_owned_negative.def")(),
                          _read("pg_supply_buf_1.lef")())),
))


def _judge_em_power_basis(log_txt: str) -> Optional[str]:
    import phase3_one_shot_runner as R
    basis = R._ppa_power.em_power_basis(log_txt, sdc="calibration/cal_em_clock_pad.sdc", spef=None,
                             spef_reason="calibration structure: unrouted",
                             liberties=[])
    return "CLOCK_NOT_REACHED" if basis["clock_reaches_network"] is False else None


_EM_BASIS_CAL_PROV = (
    "Real OpenROAD 26Q3-2963-gc73a322d30 (the pinned vibeic-eda 0.3.79, 8HD-4, "
    "2026-09-27), the report_power the Step-25 EM session prints between its "
    "EM_POWER_BASIS markers, unedited: calibration/cal_em_clock_pad.v (the "
    "PDK's gf180mcu_fd_io__in_c input pad driving one clkbuf_16 and two "
    "dffq_1) linked from the nom tech LEF, the cell LEF, the pad LEF and the "
    "tt_025C_5v00 cell liberty, calibration/cal_em_clock_pad.sdc (create_clock "
    "on the pad's PAD port), set_propagated_clock. ")

_register(Instrument(
    name="_ppa.power::em_power_basis",
    reads="the Step-25 PSM session log (`reports/phase3/ir_em.log`), its "
          "report_power between the EM_POWER_BASIS markers",
    ruling="review70 step 25 (T103)", owner="mig103",
    why=("PSM turns each instance's power into grid current, so an EM verdict "
         "is only as true as that power. With the SDC but without the IO pad's "
         "liberty, the clock stops at the pad and never reaches its tree: spm "
         "read 1.19 mW instead of 21.0 mW, an EM PASS that measured nothing. "
         "The pair is the same structure with and without the pad's liberty. "
         "The one line sits in the Step-25 producer, its only caller: the "
         "wired set is derived from programs/*.py, and MEASURED on this tree "
         "a call inside `_ppa/power.py` read as 'calibrated but not wired'."),
    calls_at="phase3_one_shot_runner::_emit_ir_em_reports",
    judge=_judge_em_power_basis,
    positive=Sample(
        provenance=(_EM_BASIS_CAL_PROV + "WITHOUT the pad's tt liberty: Clock "
                    "group 0.00e+00 W, total 9.54e-06 W. "
                    "calibration/em_power_basis_clock_unreached_positive.log"),
        artefact=_read("em_power_basis_clock_unreached_positive.log")),
    expect="CLOCK_NOT_REACHED",
    negative=Sample(
        provenance=(_EM_BASIS_CAL_PROV + "WITH gf180mcu_fd_io__tt_025C_5v00: "
                    "Clock group 1.52e-04 W, total 5.11e-04 W. "
                    "calibration/em_power_basis_clock_reached_negative.log"),
        artefact=_read("em_power_basis_clock_reached_negative.log")),
))


def _judge_tap_row_coverage(pair: Tuple[str, str]) -> Optional[str]:
    """Step 15's tap-coverage reader on one real placed DEF, at the deck's
    15 um (gf180mcu DF.13_MV / DF.14_MV), counting the endcap only on its
    LEF's evidence -- exactly as the runner calls it."""
    import tap_row_coverage_check as C
    def_text, lef_text = pair
    rec = C.audit(def_text, [lef_text], "gf180mcu_fd_sc_mcu7t5v0__filltie", 15.0,
                  "gf180mcu_fd_sc_mcu7t5v0__endcap", "cells")
    return "TAP_ROW_COVERAGE_GAP" if rec["uncovered"] else None


_TAP_COVERAGE_CAL_PROV = (
    "Real OpenROAD 26Q3-2963-gc73a322d30 (the pinned vibeic-eda 0.3.79, 8HD-8, "
    "2026-09-27), unedited `write_def` output: gf180mcu_fd_sc_mcu7t5v0 nom tech "
    "LEF + cell LEF, calibration/cal_chain_gf180.v linked (two dffq_1 around one "
    "inv_1, no design under test), a 140x40 um die with a 120x19.6 um core (4 "
    "rows, N/FS), `tapcell -tapcell_master ..__filltie -endcap_master ..__endcap "
    "-distance D`, f0/i0/f1 seeded at (50,10) (60,13.92) (90,17.84) um and "
    "`detailed_placement`. The LEF half is calibration/tap_coverage_cells.lef, "
    "the filltie/endcap/dffq_1/inv_1 MACRO blocks copied verbatim from that "
    "cell LEF. ")

_register(Instrument(
    name="tap_row_coverage_check::audit",
    reads="a placed DEF's ROWS + COMPONENTS and the LEFs of its masters",
    ruling="T96 (review70 step 15, harvest 2: deck distance, well-island coverage)",
    owner="mig96",
    why=("Step 15 on LibreLane drops the direct prune and well-tie repair, so "
         "the only thing standing between the tool's tap lattice and a DF.13/"
         "DF.14 violation is this reader. A same-row ruler read 181 of 413 "
         "cells uncovered on a layout the KLayout deck passes; the pair is one "
         "structure tapped at two distances, so only the island geometry "
         "decides."),
    judge=_judge_tap_row_coverage,
    positive=Sample(
        provenance=(_TAP_COVERAGE_CAL_PROV + "D = 40: ties 78.4 um apart in a "
                    "row, 2 of 3 cells further than 15 um from every tie in "
                    "their well island (worst 17.36 um). "
                    "calibration/tap_coverage_gap_positive.def"),
        artefact=lambda: (_read("tap_coverage_gap_positive.def")(),
                          _read("tap_coverage_cells.lef")())),
    expect="TAP_ROW_COVERAGE_GAP",
    negative=Sample(
        provenance=(_TAP_COVERAGE_CAL_PROV + "D = 20 (the PDK's own "
                    "FP_TAPCELL_DIST): every cell within 15 um of a tie in its "
                    "island. calibration/tap_coverage_covered_negative.def"),
        artefact=lambda: (_read("tap_coverage_covered_negative.def")(),
                          _read("tap_coverage_cells.lef")())),
))


def _ll_cts_sample(stem: str) -> Callable[[], Dict[str, str]]:
    def _load() -> Dict[str, str]:
        return {"cts.rpt": (FIXTURES / f"{stem}.rpt").read_text(errors="replace"),
                "openroad-cts.log": (FIXTURES / f"{stem}.log").read_text(
                    errors="replace")}
    return _load


def _judge_ll_cts_transcript(sample: Dict[str, str]) -> Optional[str]:
    """The step-19 LibreLane handoff, rebuilt around one real CTS step folder:
    its `cts.rpt` copied to the runner's report path and bound by the receipt,
    exactly as `librelane_cts_hold` writes them. Then step 16's reader."""
    import hashlib
    import clock_plan_check as C
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        folder = project / "phase3/librelane/19-cts-hold/01-openroad-cts"
        folder.mkdir(parents=True)
        for name, text in sample.items():
            _atomic_write_bytes(folder / name, text.encode())
        report = project / C._CTS_REPORT_REL
        report.parent.mkdir(parents=True)
        _atomic_write_bytes(report, sample["cts.rpt"].encode())
        digest = hashlib.sha256(report.read_bytes()).hexdigest()
        receipt = project / C._LL_CTS_HANDOFF_REL
        receipt.parent.mkdir(parents=True)
        _atomic_write_json(receipt, {
            "chain": {"OpenROAD.CTS": str(folder.relative_to(project))},
            "views": {"cts_rpt": {"source_sha256": digest,
                                  "dest_sha256": digest}}})
        text, _source, _note = C.cts_transcript(project)
    return ("CTS_CLOCK_MISSING"
            if C.cts_missing_clocks(text, _CTS_CAL_SOURCES) else None)


_LL_CTS_CAL_PROV = (
    "Real LibreLane 3.1.0.dev1 `OpenROAD.CTS` (its cts.tcl on OpenROAD "
    "26Q3-2963-gc73a322d30, the pinned vibeic-eda 0.3.79, 8HD-4, 2026-09-27): "
    "`librelane --pdk gf180mcuD --skip OpenROAD.STAMidPNR --to OpenROAD.CTS` "
    "(the image's STAMidPNR dies on est::check_corner_wire_cap) on "
    "calibration/cal_two_clock_gf180.v, run under the container's /tmp; the "
    "step folder's own `cts.rpt` (the report_cts summary, which names no root) "
    "and `openroad-cts.log`, unedited. Judged against "
    "calibration/cal_two_clocks.sdc. ")

_register(Instrument(
    name="clock_plan_check::cts_transcript",
    reads=("the LibreLane `OpenROAD.CTS` transcript the step-19 handoff binds "
           "(`reports/phase3/librelane_cts_hold_handoff.json`)"),
    ruling="F30 (review70 step 16 x T98 step 19)", owner="migf30",
    why=("With steps 19/20 on LibreLane the CTS report is the tool's "
         "report_cts summary, which names no root, so step 16 read "
         "CTS_CLOCK_MISSING on every LibreLane CTS. The roots are in the same "
         "step's transcript. The pair is two real LibreLane CTS step folders "
         "whose report_cts summaries are byte-identical: only the transcript "
         "can tell a root taken up from a root never named."),
    judge=_judge_ll_cts_transcript,
    positive=Sample(
        provenance=(_LL_CTS_CAL_PROV + "CLOCK_PORT=clk, PNR_SDC_FILE "
                    "calibration/cal_one_clock.sdc (clk only): `[INFO "
                    "CTS-0007] Net \"clk\" found for clock \"clk\".` and "
                    "nothing for clk2. calibration/"
                    "ll_cts_clock_root_dropped_positive.{rpt,log}"),
        artefact=_ll_cts_sample("ll_cts_clock_root_dropped_positive")),
    expect="CTS_CLOCK_MISSING",
    negative=Sample(
        provenance=(_LL_CTS_CAL_PROV + "CLOCK_PORT=[clk, clk2], PNR_SDC_FILE "
                    "calibration/cal_two_clocks.sdc: CTS-0007 names both "
                    "clocks (CTS-0041 then skips each, 1 sink). calibration/"
                    "ll_cts_clock_roots_seen_negative.{rpt,log}"),
        artefact=_ll_cts_sample("ll_cts_clock_roots_seen_negative")),
))


def _judge_decap_effect(log: str) -> Optional[str]:
    """The emitter's own transcript: every decap solve measurably below its
    net's quasi-static reference -> DECAP_MEASURED, else silent."""
    import dynamic_ir_vectored_emit as D
    blocks = D.transient_blocks(log)
    states = []
    for m in re.finditer(r"^=== DYN_IR PSM (\S+) \S+ period=\S+ decap_f=(\S+) ===$",
                         log, re.M):
        states.append(D.decap_effect(blocks.get(("ref", m.group(1)), ""),
                                     blocks.get(("psm", m.group(1)), ""),
                                     float(m.group(2)))["state"])
    return "DECAP_MEASURED" if states and set(states) == {"DECAP_MEASURED"} else None


_DECAP_CAL_PROV = (
    "Real `dynamic_ir_vectored_emit` transcript, vibeic-eda 0.3.79 (OpenROAD "
    "26Q3-2963-gc73a322d30), lane migf20 on 8hd-3, on the copied run23 "
    "(gf180mcuD): its routed DEF + constraint.sdc + extracted SPEF + the "
    "tt_025C_5v00 cell and IO liberties. A transient PSM solve needs a real "
    "routed power grid, which no calibration structure here has (see the "
    "`dynamic_ir_vectored_emit::emit` register entry). Per net (VDD, VSS) a quasi-static "
    "reference solve, then the decap solve with `-decap_cap` in the session's "
    "unit (`sta::capacitance_ui_sta 1.0` = 1e-12 there). Project path "
    "replaced by <project> and the DEF basename by <top>; otherwise unedited. ")

_register(Instrument(
    name="dynamic_ir_vectored_emit::decap_effect",
    reads="the transient emitter's PSM transcript (reference + decap solve per net)",
    ruling="F20 (lane migf20; T101 finding 4)", owner="migf20",
    why=("The shared rule 'on-die capacitance printed => genuine di/dt' called "
         "a ratio-2.00 result genuine: the fork read 1e-9 in pF (1.00e-21 F), "
         "and with the unit right 1 pF still leaves the droop equal to the "
         "quasi-static bound. The pair is the same session asked for two "
         "decaps: one that measurably lowers the droop, one that prints a "
         "capacitance and changes nothing."),
    judge=_judge_decap_effect,
    positive=Sample(
        provenance=(_DECAP_CAL_PROV + "decap 1e-9 F, printed 1.00e-09 F: VDD "
                    "1.48e-02 -> 1.37e-02 V (ratio 1.85), VSS 1.68e-02 -> "
                    "1.53e-02 V (1.82). calibration/"
                    "dynamic_ir_decap_measured_positive.log"),
        artefact=_read("dynamic_ir_decap_measured_positive.log")),
    expect="DECAP_MEASURED",
    negative=Sample(
        provenance=(_DECAP_CAL_PROV + "decap 1e-12 F, printed 1.00e-12 F: VDD "
                    "1.48e-02 and VSS 1.68e-02 V in both solves, ratio 2.00 — "
                    "the printed-capacitance case the old rule called genuine. "
                    "calibration/dynamic_ir_decap_unresolved_negative.log"),
        artefact=_read("dynamic_ir_decap_unresolved_negative.log")),
))


# ══════════════════════════════════════════════════════════════════════════
#  THE RULE
# ══════════════════════════════════════════════════════════════════════════

#: Memoised per process. Calibration runs the real pair — a subprocess for the
#: progress detector, tclsh for the DRV census — and an instrument asked twice
#: in one run must not pay for it twice. This is a cache, not a mode: there is
#: no flag, env var or argument anywhere in this module that skips a check.
#:
#: WHAT MAY BE STORED HERE. Only a calibration of the REGISTERED instrument —
#: its own judge, samples and expectation (`_REGISTERED`) — measured over
#: collaborators nobody had replaced (`_foreign_bindings`). A calibration taken
#: under a caller's fakes is returned to that caller and forgotten: stored, it
#: was a verdict about fakes handed to every later caller, the real one
#: included, and the outcome depended on who asked first (lane rfa, T118).
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
    inst = INSTRUMENTS.get(name)
    if inst is None:
        return Calibration(name, MISCALIBRATED, failed_sides=("unregistered",),
                           detail="not in INSTRUMENTS")
    registered = _is_registered(inst)
    if registered and name in _CACHE:
        return _CACHE[name]
    failed: List[str] = []
    detail: List[str] = []
    _IN_PROGRESS.add(name)
    try:
        cal, reached = _reaching(lambda: _run_pair(inst, failed, detail))
    finally:
        _IN_PROGRESS.discard(name)
    if registered and not _foreign_bindings(reached):
        _CACHE[name] = cal
    return cal


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
    return cal


# ── what a calibration measured: the registered instrument, or a fake? ─────

#: The fields that make an instrument what was registered, by identity. Taken
#: in `_register`; an instrument whose judge, samples or expectation are no
#: longer these objects is a different instrument, measured and never stored.
_IDENTITY_FIELDS: Tuple[str, ...] = ("judge", "positive", "negative", "expect")
_REGISTERED: Dict[str, Tuple[Any, ...]] = {
    n: tuple(getattr(i, f) for f in _IDENTITY_FIELDS)
    for n, i in INSTRUMENTS.items()}


def _is_registered(inst: Instrument) -> bool:
    want = _REGISTERED.get(inst.name)
    return want is not None and all(
        getattr(inst, f) is w for f, w in zip(_IDENTITY_FIELDS, want))


#: Where the import system's own `_find_and_load` lives.
_IMPORT_FILE = "<frozen importlib._bootstrap>"


class _Reached(list):
    """The module namespaces a run reached, plus the code objects it ENTERED."""
    entered: frozenset = frozenset()


def _reaching(run: Callable[[], Any]) -> Tuple[Any, List[Dict[str, Any]]]:
    """Run `run` and return its result with every module namespace it reached.

    Reached = the globals of every Python frame entered, the modules each
    entered code object names (`import X as R` inside a judge, whose module
    code never runs when its function is faked), and the modules bound in the
    globals of the frames entered (`subprocess.run` faked: `subprocess`'s own
    code never runs either). A profiler already installed keeps receiving
    every event.

    NOT reached: whatever runs inside an import. A judge that imports its
    module for the first time runs the import system and every finder a host
    installed (pytest's assertion-rewriting hook, whose module binds a
    per-test closure); that is how the module arrived, not what the judge read.

    The list also carries `.entered`: every code object the run entered, so a
    replaced function can be told apart from one the run actually called.
    """
    spaces: Dict[int, Dict[str, Any]] = {}
    codes: Dict[int, Any] = {}
    importing = [0]
    prior = sys.getprofile()

    def _seen(frame, event, arg):
        code = frame.f_code
        if code.co_name == "_find_and_load" and code.co_filename == _IMPORT_FILE:
            importing[0] += 1 if event == "call" else -1 if event == "return" else 0
        elif event == "call" and not importing[0]:
            spaces.setdefault(id(frame.f_globals), frame.f_globals)
            codes.setdefault(id(code), code)
        if prior is not None:
            prior(frame, event, arg)

    sys.setprofile(_seen)
    try:
        result = run()
    finally:
        sys.setprofile(prior)
    reached = dict(spaces)
    for code in codes.values():
        for n in code.co_names:
            mod = sys.modules.get(n)
            if isinstance(mod, types.ModuleType):
                reached.setdefault(id(mod.__dict__), mod.__dict__)
    for g in list(spaces.values()):
        for v in list(g.values()):
            if isinstance(v, types.ModuleType):
                reached.setdefault(id(v.__dict__), v.__dict__)
    out = _Reached(reached.values())
    out.entered = frozenset(codes.values())
    return result, out


def _is_foreign(value: Any, home: Optional[str], name: str) -> bool:
    """True when `value`, bound as `home.name`, is not what that module binds
    there: a mock; a lambda or closure from somewhere else; a function its own
    module no longer binds; or — read from `home`'s own source — a function
    from another module under a name `home` DEFINES, or under a name it
    imports from a module that binds something else."""
    mocks = sys.modules.get("unittest.mock")
    if mocks is not None and isinstance(value, mocks.NonCallableMock):
        return True
    if not isinstance(value, types.FunctionType):
        return False
    if value.__module__ == home or _interpreters_own(value.__code__.co_filename):
        return False
    obj: Any = sys.modules.get(value.__module__ or "")
    try:
        for part in value.__qualname__.split("."):
            obj = getattr(obj, part)
    except Exception:
        return True
    if obj is not value:
        return True
    decls = _declared_bindings(home).get(name, ())
    return bool(decls) and not any(
        d[0] == "other"
        or (d[0] == "from" and getattr(sys.modules.get(d[1]), d[2], None)
            is value)
        or (d[0] == "alias" and _resolve(home, d[1]) is value)
        for d in decls)


def _dotted(node: ast.AST) -> str:
    """`a.b.c` for a Name/Attribute chain, else ""."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    return ".".join([node.id] + parts[::-1])


def _resolve(home: Optional[str], dotted: str) -> Any:
    """`dotted` looked up in `home`'s globals, as the module's own line did."""
    head, *rest = dotted.split(".")
    obj = vars(sys.modules[home]).get(head) if home in sys.modules else None
    for part in rest:
        obj = getattr(obj, part, None)
    return obj


#: module name -> {bound name -> how its source binds it}, parsed once.
_DECLARED: Dict[str, Dict[str, Tuple[Tuple[str, ...], ...]]] = {}


def _declared_bindings(home: Optional[str]
                       ) -> Dict[str, Tuple[Tuple[str, ...], ...]]:
    """How `home`'s source binds each module-level name: ("def",) for a def or
    class, ("from", module, name) for a from-import, ("alias", "a.b") for
    `name = a.b`, ("other",) for anything else. Empty when there is no Python
    source to read."""
    key = home or ""
    if key in _DECLARED:
        return _DECLARED[key]
    found: Dict[str, List[Tuple[str, ...]]] = {}
    mod = sys.modules.get(key)
    src = getattr(mod, "__file__", None) or ""
    try:
        tree = ast.parse(Path(src).read_text()) if src.endswith(".py") else None
    except (OSError, SyntaxError, ValueError):
        tree = None
    package = (getattr(mod, "__package__", None) or "").split(".")

    def _other(target: ast.AST) -> None:
        for n in ast.walk(target):
            if isinstance(n, ast.Name):
                found.setdefault(n.id, []).append(("other",))

    def _walk(body: List[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                found.setdefault(node.name, []).append(("def",))
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = package[:len(package) - node.level + 1]
                    base = ".".join(p for p in parent + [base] if p)
                for a in node.names:
                    found.setdefault(a.asname or a.name, []).append(
                        ("from", base, a.name))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    found.setdefault(a.asname or a.name.partition(".")[0],
                                     []).append(("other",))
            elif isinstance(node, ast.Assign):
                dotted = _dotted(node.value)
                for t in node.targets:
                    if dotted and isinstance(t, ast.Name):
                        found.setdefault(t.id, []).append(("alias", dotted))
                    else:
                        _other(t)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.For)):
                _other(node.target)
            elif isinstance(node, ast.With):
                for item in node.items:
                    if item.optional_vars is not None:
                        _other(item.optional_vars)
            for field_name in ("body", "orelse", "finalbody"):
                sub = getattr(node, field_name, None)
                if isinstance(sub, list) and not isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
                    _walk(sub)
            for handler in getattr(node, "handlers", None) or []:
                _walk(handler.body)

    if tree is not None:
        _walk(tree.body)
    _DECLARED[key] = {k: tuple(v) for k, v in found.items()}
    return _DECLARED[key]


#: The interpreter's own library. A caller's fake is never defined there, and
#: it binds functions this rule would otherwise misread: a `site` closure as
#: `sys.__interactivehook__`, frozen importlib's `_relax_case`, `os`'s import of
#: `_check_methods` from the module that calls itself `collections.abc`.
_STDLIB_ROOTS: Tuple[str, ...] = tuple(sorted({
    os.path.realpath(sysconfig.get_paths()[k]) + os.sep
    for k in ("stdlib", "platstdlib")}))


def _interpreters_own(filename: str) -> bool:
    if filename.startswith("<frozen "):
        return True
    path = os.path.realpath(filename)
    parts = path.split(os.sep)
    return (path.startswith(_STDLIB_ROOTS)
            and "site-packages" not in parts and "dist-packages" not in parts)


def _took_part(value: Any, entered: Optional[frozenset]) -> bool:
    """Could `value` have shaped what the run measured? A mock always could (it
    answers attribute reads, not only calls); a function only if the run
    entered its code. A function bound where the run looked but never called
    measured nothing: the interpreter's start-up binds such things by
    environment (the EDA image's `sitecustomize` installs apport's closure as
    `sys.excepthook`; the host has none), and reading one as a caller's fake
    left every calibration uncached in the image and only there (F8b)."""
    if entered is None or not isinstance(value, types.FunctionType):
        return True
    return value.__code__ in entered


def _foreign_bindings(reached: List[Dict[str, Any]]) -> List[str]:
    """Every binding in `reached` a caller replaced and the run used, as
    `module.name`."""
    entered = getattr(reached, "entered", None)
    found = [f"{__name__}.{k}" for k, v in _IMPORTED_STATE.items()
             if globals().get(k) is not v]
    for g in reached:
        home = g.get("__name__")
        found.extend(f"{home}.{k}" for k, v in list(g.items())
                     if _is_foreign(v, home, k) and _took_part(v, entered))
    return found


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


#: This module's own globals as imported — FIXTURES, the judges, the readers.
#: One rebound by a caller (a moved FIXTURES, a faked `run_drv_census`) makes
#: every calibration taken meanwhile a calibration of something else.
_IMPORTED_STATE: Dict[str, Any] = dict(globals())


if __name__ == "__main__":                              # pragma: no cover
    sys.exit(main())
