#!/usr/bin/env python3
"""lec_gate_netlist_select.py — Truthful diagnosis of a structural LEC abort.

WHAT THIS FIXES (and, just as important, what it deliberately does NOT do)
=========================================================================
When the OSS Fault ATPG path runs and no scan netlist exists yet,
``fault_atpg_run.py`` writes Fault's combinational CUT output verbatim to
``scan_netlist.v`` (``scan_netlist.write_bytes(cut_out.read_bytes())``, guarded
by ``if not scan_netlist.exists()``) — in the open flow the cut output IS what
the DFT chain hands on.  The cut netlist DELETES every flip-flop and replaces it
with a pair of top-level pseudo-ports: ``_NNN_`` (the flop's Q, a primary
input) and ``\\_NNN_.d`` (the flop's D, a primary output).  ``post_dft_opt``
``opt_clean``s that into ``post_dft_netlist.v``, which step 13 then hands to
yosys ``equiv_make`` as the gate side.  ``equiv_make`` aborts::

    ERROR: Can't match gate port `_508_.d_gate' to a gold port.

No miter is built, ``compared_points == 0``, and NOTHING was compared.
``lec_equivalence_check.py`` used to report that as::

    LEC_NOT_EQUIVALENT — "RTL and post-DFT netlist differ — fall back to
    step 9 (synth/DFT)."

That sentence is FALSE.  Nothing differed, because nothing was compared.  It
sends the next agent to re-synthesise a design that was never examined.  The
defect this module exists to close is a LYING DIAGNOSIS, and the repair is a
TRUTHFUL one — at the SAME hard-FAIL severity.

Three things this module explicitly refuses to do
-------------------------------------------------
1. **It never substitutes a different gate netlist.**  ``gate_netlist_for_lec``
   returns exactly what the pre-existing two-line rule returned
   (``post_dft_netlist.v`` when present, else ``netlist.v``).  Silently
   comparing ``<top>_synth.v`` instead would let the step canonically named
   ``13_equivalence_check_rtl_post_dft_netlist`` report PASS while the post-DFT
   netlist was never compared — a fabricated pass against a different artifact,
   and it would leave the real upstream bug (the ``fault_atpg_run.py``
   byte-copy) unflagged.  A corrupt post-DFT netlist must stay a visible,
   hard FAIL.
2. **It never downgrades a non-PASS to a waiver.**  The abort is still a hard
   FAIL (``rc=1``).  Only the rule name and the message change.  Reclassifying
   it as INCONCLUSIVE would route it to the ``PASS_WITH_WAIVERS`` /
   WAIVED-DEFERRED tier, so a genuine top-level port-set mismatch (e.g. scan
   ports present in the netlist and absent from the gold RTL — a real DFT
   integration bug) would be laundered into a near-pass.
3. **It never asserts a cause it has not measured.**  The yosys error string
   alone proves only "the gate port set could not be matched".  The claim
   "this is an ATPG-cut artifact" is made ONLY when
   ``is_atpg_cut_artifact()`` confirms it by reading the netlist that was
   actually compared.

Public API
----------
``is_atpg_cut_artifact(netlist_path) -> (bool, reason)``
    Structural predicate.  True iff the netlist's TOP-LEVEL PORT LIST contains
    at least one ``<inst>.d`` pseudo-port AND the netlist instantiates ZERO
    sequential cells.  Both signals are required.  Chip- and PDK-AGNOSTIC: it
    keys on observable structure, never on a filename, module name, or cell
    literal.  Scanning is restricted to the port list precisely because a
    whole-file scan flags a legitimate mapped netlist that merely carries a
    hierarchical net such as ``\\u_reg.d``.

``gate_netlist_for_lec(project, top_name) -> (rel_path, note, is_cut)``
    The step-13 gate-side selection.  IDENTICAL to the legacy rule — no
    reordering, no fallback, no substitution — plus a note recording whether
    the selected netlist is a confirmed ATPG-cut artifact.

``classify_port_abort(log_text, compared_points) -> str | None``
    ``CANONICAL_VERDICT`` when the log carries the equiv_make port-match abort
    AND zero points were compared; ``None`` otherwise.

``port_abort_cause(project, gate_field) -> (confirmed, evidence)``
    Reads the netlist named by ``reports/lec.json:gate`` and returns measured
    evidence, or ``(False, "")`` when the cause cannot be confirmed.

``CANONICAL_STATUS  = "LEC_STRUCTURAL_PORT_ABORT"``
``CANONICAL_VERDICT = "STRUCTURAL-PORT-ABORT"``

Fail-safe direction
-------------------
A MISS (the predicate does not recognise a cut netlist) costs only the
specific wording "confirmed ATPG-cut artifact" — the structural-abort FAIL
still fires from the log.  A FALSE POSITIVE would put a fabricated cause in a
sign-off report.  So every judgement call in here is biased towards the miss.

The step-13 SUBJECT is the netlist step 15 routes (owner decision F1)
--------------------------------------------------------------------
``gate_netlist_for_lec`` above answers "which file did step 12 hand on", and it
still does, for the cut-artifact diagnosis.  It is no longer the file arm A
proves.  Measured on spm (run23): arm A proved RTL == ``netlist.v``, the
technology-GENERIC phase-2 netlist (``$_NAND_``/``$_DFF_P_``), sha256
``41e72ef1...``, while step 15 routed ``spm_synth.v``, the MAPPED netlist,
sha256 ``15cd5ac7...``.  A proof about a netlist nobody builds says nothing
about the chip.

``lec_subject_for_step13`` therefore asks step 15's OWN resolver,
``phase3_one_shot_runner.pnr_input_netlist``, which netlist it routes: the
post-DFT netlist when step 11's published scan record authorises it, the mapped
pre-DFT netlist otherwise.  One implementation, so the proof and the router
cannot disagree about the file.  The flow YAML declares the same edge: step 13
and step 15 both read step 12's output.

``proof_subject_binding`` is the check behind the gate's refusal: the sha256
the proof recorded for its gate side (``lec.json:proof_identity.gate_netlist``)
must equal the sha256 of the file step 15 reads.

PURE: no subprocess, no Docker, no network.  Filesystem reads only.
Chip-AGNOSTIC: no design, module, or PDK literal is hard-coded.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# The step-15 resolver is imported by bare name, which works under
# `spec_from_file_location` only with this file's directory on sys.path.
# Spelled with pathlib: this module stays free of `os` (see its purity test).
import sys as _sys                                                  # noqa: E402
from pathlib import Path as _Path                                   # noqa: E402

if str(_Path(__file__).resolve().parent) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
# ---------------------------------------------------------------------------

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Exported status / verdict tokens (used by callers and the test suite)
# ---------------------------------------------------------------------------
# The rule name describes the EVIDENCE (equiv_make aborted on the port match),
# not a guessed cause.  The cause, when measurable, goes in the message.
CANONICAL_STATUS = "LEC_STRUCTURAL_PORT_ABORT"
CANONICAL_VERDICT = "STRUCTURAL-PORT-ABORT"

# Canonical step-13 gate-side location.
SYNTH_REL = "phase2/stage2/synth"

# ---------------------------------------------------------------------------
# Verilog port-list parser — STRUCTURAL only (no elaboration needed)
# ---------------------------------------------------------------------------

# Match the module header line to extract the raw port list string.
#   module <name> ( <ports> );
_MODULE_HDR_RE = re.compile(
    r"\bmodule\s+\w+\s*\(([^)]*)\)\s*;", re.DOTALL)

# Every port-direction keyword. A declaration statement runs from the keyword
# to the next `;` or the next direction keyword, whichever comes first — so
# ANSI headers (`module m(input a, output b);`), non-ANSI bodies (one
# declaration per line) and several declarations packed onto ONE line
# (`input _10_; output \_10_.d ;`) all parse. An earlier revision anchored the
# body pattern at `^`, which silently dropped every declaration after the first
# on a shared line and made the whole predicate a no-op on such a netlist.
# The lookbehind skips a pin connection (`.input(x)`) and an escaped
# identifier (`\input`), neither of which declares a port.
_PORT_DIR_RE = re.compile(r"(?<![.\\])\b(?:input|output|inout)\b",
                          re.IGNORECASE)

# Type/qualifier keywords that may sit between the direction and the names.
_PORT_NOISE_RE = re.compile(
    r"\b(?:wire|reg|logic|bit|signed|unsigned|integer)\b", re.IGNORECASE)

# ATPG cut pseudo-port suffix, applied to an ALREADY-EXTRACTED PORT NAME.
#
# Anchored at the END of the name on purpose.  Fault names the pseudo-port for
# the deleted flop's D pin `<inst>.d` and nothing else.  An unanchored `\.d`
# would also match a legitimate escaped identifier such as `\bus.data` or
# `\u_reg.dout`, and matching against the whole FILE (rather than the port
# list) would additionally match a hierarchical NET like `\u_reg.d` in a
# perfectly healthy mapped netlist.  Both of those are false positives that put
# a fabricated cause into a sign-off report.
_CUT_PORT_SUFFIX_RE = re.compile(r"\.d\Z")

# Sequential-cell recognition — chip-AGNOSTIC, applied SEGMENT-WISE.
#
# A cell type is split on non-alphanumeric characters and each segment is
# tested, so `sky130_fd_sc_hd__sdfxtp_1` is recognised through its `sdfxtp`
# segment and `$_SDFFE_PP0P_` through its `SDFFE` segment. An earlier revision
# used a whole-string regex whose segment boundary was `(?<![A-Za-z])`; that
# recognised `dfxtp` but NOT `sdfxtp`/`sedfxtp` — i.e. it was blind to the SCAN
# flops that make up a post-DFT netlist, which is the exact netlist this
# predicate looks at.
#
# Covered: sky130 dfxtp/dfrtp/dfstp/dfbbn/sdfxtp/sedfxtp/edfxtp, gf180
# dffq/dffrnq/sdffq, generic dff/sdff/adff/aldff, yosys $_DFF_*_/$_SDFF_*_/
# $_ALDFF_*_/$_DLATCH_*_, and the dlatch/dlx/dlr/dls/dlclk latch families.
#
# NOT covered on purpose: `dly*` delay cells (sky130 dlygate4sd3, gf180 dlyd)
# — they start `dl` but hold no state.
_FF_SEGMENT_RE = re.compile(
    r"^(?:s|e|a|se|sa|al|sal)?df[a-z0-9]*$"      # df*/sdf*/sedf*/aldf* families
    r"|^(?:dlatch|dlx|dlr|dls|dlclk)[a-z0-9]*$",  # latch families
    re.IGNORECASE)

_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_safe(path: Path) -> str:
    """Read a Verilog file with // and /* */ comments stripped."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def _names_in(fragment: str) -> Set[str]:
    """Pull identifier names out of one declaration fragment."""
    frag = re.sub(r"\[[^\]]*\]", " ", fragment)          # drop vector ranges
    frag = _PORT_NOISE_RE.sub(" ", frag)                 # drop type keywords
    frag = frag.replace("(", " ").replace(")", " ")
    out: Set[str] = set()
    for tok in re.split(r"[\s,]+", frag):
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("\\"):
            # Escaped identifier: everything up to the terminating whitespace.
            out.add(tok[1:])
        elif re.fullmatch(r"[A-Za-z_$][\w$]*", tok):
            out.add(tok)
    return out


def _extract_ports(text: str) -> Set[str]:
    """Return the set of declared port names in a Verilog source.

    The leading backslash of an escaped identifier is stripped, so
    ``output \\_10_.d ;`` yields the name ``_10_.d``.

    Handles ANSI headers, non-ANSI bodies, several declarations on one line,
    comma-separated name lists, and vector ranges.
    """
    ports: Set[str] = set()
    for dm in _PORT_DIR_RE.finditer(text):
        start = dm.end()
        end = len(text)
        semi = text.find(";", start)
        if semi != -1:
            end = semi
        nxt = _PORT_DIR_RE.search(text, start)
        if nxt is not None and nxt.start() < end:
            end = nxt.start()
        ports |= _names_in(text[start:end])
    # Fallback: a header port list with no directions given anywhere.
    if not ports:
        m = _MODULE_HDR_RE.search(text)
        if m:
            for tok in re.split(r"[\s,]+", m.group(1)):
                tok = tok.strip()
                if tok.startswith("\\"):
                    ports.add(tok[1:])
                elif re.fullmatch(r"[A-Za-z_$][\w$]*", tok):
                    ports.add(tok)
    return ports


def _is_ff_type(ident: str) -> bool:
    """True iff any `_`/`$`-separated segment of *ident* names a state element."""
    return any(_FF_SEGMENT_RE.match(seg)
               for seg in re.split(r"[^A-Za-z0-9]+", ident) if seg)


def _count_ff_cells(text: str) -> int:
    """Count sequential-cell instantiations in a Verilog netlist.

    Only the text BEFORE the instantiation's opening parenthesis is inspected —
    that is the cell type and instance name. Port connections such as
    ``.D(\\u_reg.d )`` are net references, not cell types, and must not be
    counted.
    """
    count = 0
    for line in text.splitlines():
        if "(" not in line:
            continue
        head = line.split("(", 1)[0]
        if any(_is_ff_type(ident) for ident in _IDENT_RE.findall(head)):
            count += 1
    return count


def cut_pseudo_ports(text: str) -> Set[str]:
    """Return the ``<inst>.d`` pseudo-ports present in the TOP-LEVEL port list.

    Scoped to the port list — NOT the whole file — and anchored at the end of
    the port name.  See ``_CUT_PORT_SUFFIX_RE`` for why both restrictions are
    load-bearing.
    """
    return {p for p in _extract_ports(text) if _CUT_PORT_SUFFIX_RE.search(p)}


# ---------------------------------------------------------------------------
# Public API — structural predicate
# ---------------------------------------------------------------------------


def is_atpg_cut_artifact(netlist_path: Path) -> Tuple[bool, str]:
    """Structural predicate: is *netlist_path* an ATPG combinational-cut view?

    BOTH signals are required — either alone is a known false-positive source:

      1. at least one top-level port whose name ends in ``.d``
         (Fault's cut pseudo-port convention for the deleted flop's D pin), and
      2. ZERO sequential-cell instantiations in the netlist.

    Requiring (2) as corroboration means a mapped netlist that happens to carry
    an escaped hierarchical port name ending in ``.d`` is not flagged while it
    still has its flops.  Requiring (1) means a legitimately combinational
    design is never flagged.

    Takes NO reference-design context.  An earlier revision derived a reference
    flip-flop count by running the liberty-cell regex over the RTL; that count
    is meaningless (RTL does not instantiate liberty cells — measured 0 for one
    real design and 1 for a core holding 1272 flops), and feeding it into the
    decision could REJECT a valid netlist whose PDK cell names the regex misses.

    Returns ``(is_cut, reason)``.  PURE, chip-AGNOSTIC.
    """
    text = _read_safe(netlist_path)
    if not text:
        return False, ""

    cut = cut_pseudo_ports(text)
    if not cut:
        return False, ""

    ff = _count_ff_cells(text)
    if ff > 0:
        # Has both `.d` ports AND flops — not a cut view.  Say nothing.
        return False, ""

    sample = sorted(cut)[:5]
    return (
        True,
        f"ATPG combinational-cut netlist: {len(cut)} top-level '<inst>.d' "
        f"pseudo-port(s) {sample}{'...' if len(cut) > 5 else ''} and 0 "
        f"sequential cells — every flip-flop has been replaced by a "
        f"primary-input/primary-output pseudo-port pair, so equiv_make has no "
        f"gold port to match them against",
    )


# ---------------------------------------------------------------------------
# Public API — step-13 gate-side selection (UNCHANGED from the legacy rule)
# ---------------------------------------------------------------------------


def legacy_gate_netlist_rel(project: Path) -> str:
    """The pre-existing step-13 gate-side rule, verbatim.

    ``post_dft_netlist.v`` when it exists on disk, otherwise ``netlist.v``.
    Kept as a named function so the no-scope-creep property is testable
    directly rather than asserted in a comment.
    """
    post_dft = project / "phase2" / "stage2" / "synth" / "post_dft_netlist.v"
    name = "post_dft_netlist.v" if post_dft.is_file() else "netlist.v"
    return f"{SYNTH_REL}/{name}"


def gate_netlist_for_lec(project: Path,
                         top_name: str = "") -> Tuple[str, str, bool]:
    """Choose the step-13 gate netlist and report whether it is a cut artifact.

    Returns ``(rel_path, note, is_cut)``.

    The selection is BYTE-IDENTICAL to ``legacy_gate_netlist_rel`` for every
    input.  ``top_name`` is accepted and ignored: it exists so a future caller
    cannot be tempted to reintroduce a ``<top>_synth.v`` fallback, which would
    silently change the compared artifact for the majority of designs (measured:
    19 of 27 real synth trees on one host have no ``post_dft_netlist.v`` but do
    have both ``<top>_synth.v`` and ``netlist.v``) and would make a PASS on the
    post-DFT LEC step mean something it does not say.

    When the selected netlist IS a confirmed cut artifact we still select it —
    and say so — so the run fails visibly on the corrupt artifact instead of
    passing quietly against a substitute.
    """
    del top_name  # intentionally unused; see docstring
    rel = legacy_gate_netlist_rel(project)
    chosen = project / rel
    is_cut, cut_reason = is_atpg_cut_artifact(chosen)
    if not is_cut:
        return rel, f"gate netlist {Path(rel).name} (legacy step-13 rule)", False
    note = (
        f"gate netlist {Path(rel).name} (legacy step-13 rule) is UNUSABLE for "
        f"LEC — {cut_reason}.  yosys equiv_make will abort on the port match "
        f"and compare nothing.  Root cause is upstream: fault_atpg_run.py "
        f"writes Fault's ATPG cut output verbatim to scan_netlist.v (when no "
        f"scan netlist exists yet) and post_dft_opt opt_cleans that into "
        f"post_dft_netlist.v.  This is reported as a hard "
        f"FAIL on the real artifact; it is NOT worked around by comparing a "
        f"different netlist, because that would report the post-DFT "
        f"equivalence step as PASS without ever reading the post-DFT netlist."
    )
    return rel, note, True


# ---------------------------------------------------------------------------
# Public API — port-abort classifier and its measured cause
# ---------------------------------------------------------------------------

# yosys equiv_make error when a gate port has no matching gold port.
# chip-AGNOSTIC: keys on the yosys error wording, never on a specific port name.
_EQUIV_MAKE_PORT_ABORT_RE = re.compile(
    r"Can'?t\s+match\s+gate\s+port\s+`[^']+'\s+to\s+a\s+gold\s+port",
    re.IGNORECASE)


def classify_port_abort(log_text: str,
                        compared_points: int) -> Optional[str]:
    """Return ``CANONICAL_VERDICT`` for a structural equiv_make port abort.

    Both conditions are required:
      * ``compared_points == 0`` — no miter was built, so nothing was compared;
      * the log carries the yosys equiv_make port-match abort.

    Returns ``None`` otherwise.  A run that compared points has real evidence
    and must be judged on that evidence, never reclassified from a log string.

    This function does NOT decide the severity tier.  The caller keeps a
    structural abort at hard-FAIL: nothing was compared, so equivalence is
    unproven, and an unproven equivalence is never a waiver.
    """
    if compared_points != 0:
        return None
    if _EQUIV_MAKE_PORT_ABORT_RE.search(log_text or ""):
        return CANONICAL_VERDICT
    return None


def _gate_name_from_field(gate_field: str) -> str:
    """Extract the bare netlist filename from a ``lec.json:gate`` value.

    lec_run writes ``"<name> (synth)"``.  Anything that is not a plain
    ``*.v`` / ``*.sv`` basename yields ``""`` — we never guess.
    """
    if not gate_field:
        return ""
    parts = str(gate_field).strip().split()
    if not parts:
        return ""
    # Basename only — the netlist is always resolved inside the canonical
    # synth dir, so a path in the field can never escape it.
    name = Path(parts[0]).name
    return name if name.endswith((".v", ".sv")) else ""


def port_abort_cause(project: Path,
                     gate_field: str = "") -> Tuple[bool, str]:
    """Measure WHY the port match aborted, or admit the cause is unknown.

    Reads the netlist named by ``reports/lec.json:gate`` (resolved inside
    ``phase2/stage2/synth/``) and returns ``(True, evidence)`` only when
    ``is_atpg_cut_artifact`` confirms the cut fingerprint on that exact file.

    Returns ``(False, "")`` when the gate field is absent, the file is missing,
    or the fingerprint is not there.  The caller must then report the abort
    WITHOUT naming a cause — a port-set mismatch has other real causes (most
    commonly DFT/scan ports present in the netlist and absent from the gold
    RTL, which is a genuine defect and must not be described as a cut artifact).
    """
    name = _gate_name_from_field(gate_field)
    if not name:
        return False, ""
    cand = project / SYNTH_REL / name
    if not cand.is_file():
        return False, ""
    is_cut, reason = is_atpg_cut_artifact(cand)
    if not is_cut:
        return False, ""
    return True, f"{name}: {reason}"


# ---------------------------------------------------------------------------
# Public API — the step-13 subject: the netlist step 15 routes
# ---------------------------------------------------------------------------

#: `proof_subject_binding` states. Only MATCH lets the proof stand.
BINDING_MATCH = "MATCH"
BINDING_STALE = "STALE"                    # proved sha != step-15 input sha
BINDING_UNBOUND = "UNBOUND"                # the proof recorded no gate sha256
BINDING_SCAN_UNCONSTRAINED = "SCAN_UNCONSTRAINED"
BINDING_NO_CONSUMER = "NO_CONSUMER"        # step 15's input does not exist yet
BINDING_TOP_UNKNOWN = "TOP_UNKNOWN"


def pnr_consumed_netlist(project: Path,
                         top: str) -> Tuple[Optional[Path], str, bool]:
    """``(path, note, is_scan_inserted)`` of the netlist step 15 routes.

    Asks ``phase3_one_shot_runner.pnr_input_netlist``, the resolver
    ``step_pnr`` itself calls, and restates none of its rule. ``path`` is None
    only when that resolver cannot be asked; a returned path may not exist yet
    (the mapped netlist is written by phase 3's synthesis half).
    """
    try:
        import phase3_one_shot_runner as _p3
        path, note, is_scan = _p3.pnr_input_netlist(Path(project), top)
    except Exception as exc:  # noqa: BLE001 — an unanswerable resolver is data
        return None, (f"step-15 netlist resolver unavailable: "
                      f"{type(exc).__name__}: {exc}"), False
    return Path(path), str(note), bool(is_scan)


def lec_subject_for_step13(project: Path,
                           top: str) -> Tuple[str, str, bool]:
    """``(gate_netlist, note, is_scan_inserted)`` for arm A of step 13.

    ``gate_netlist`` is project-relative when the file lies inside the project,
    and is ``""`` when step 15's resolver cannot be asked. The caller proves
    this file or nothing: it never substitutes another netlist when this one
    is absent.
    """
    path, note, is_scan = pnr_consumed_netlist(project, top)
    if path is None:
        return "", note, False
    try:
        rel = str(path.resolve().relative_to(Path(project).resolve()))
    except ValueError:
        rel = str(path)
    return rel, note, is_scan


def _file_sha256(path: Path) -> Optional[str]:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return "sha256:" + h.hexdigest()


def _proof_top(doc: Dict[str, Any]) -> str:
    identity = doc.get("proof_identity")
    if isinstance(identity, dict) and isinstance(identity.get("top"), str):
        return identity["top"]
    gold = doc.get("gold")
    if isinstance(gold, str) and gold.strip():
        return gold.split()[0]
    return ""


def proof_subject_binding(project: Path, doc: Dict[str, Any],
                          top: str = "") -> Dict[str, Any]:
    """Did the proof in ``doc`` (a ``reports/lec.json``) prove the netlist
    step 15 routes?

    Compares the sha256 lec_run bound into ``proof_identity.gate_netlist``
    with the sha256 of the file ``pnr_consumed_netlist`` names, and, when that
    file is the scan-inserted netlist, requires the proof to have applied the
    functional-mode constraint from step 11's own record
    (``scan_functional_mode.applied``). ``state`` is one of the ``BINDING_*``
    constants; only ``MATCH`` lets the proof stand for step 15's input.
    """
    doc = doc if isinstance(doc, dict) else {}
    top = top or _proof_top(doc)
    identity = doc.get("proof_identity")
    gate_id = identity.get("gate_netlist") if isinstance(identity, dict) else None
    proved_sha = (gate_id.get("sha256") if isinstance(gate_id, dict) else None)
    if not (isinstance(proved_sha, str) and proved_sha.startswith("sha256:")
            and len(proved_sha) > len("sha256:")):
        proved_sha = None
    out: Dict[str, Any] = {
        "top": top or None,
        "proved_path": (gate_id.get("path") if isinstance(gate_id, dict)
                        else None) or doc.get("gate"),
        "proved_sha256": proved_sha,
        "consumer_path": None, "consumer_sha256": None,
        "consumer_note": "", "consumer_scan_inserted": False,
    }
    if not top:
        out["state"] = BINDING_TOP_UNKNOWN
        return out
    path, note, is_scan = pnr_consumed_netlist(project, top)
    out["consumer_note"] = note
    out["consumer_scan_inserted"] = is_scan
    if path is None or not path.is_file():
        out["consumer_path"] = str(path) if path is not None else None
        out["state"] = BINDING_NO_CONSUMER
        return out
    try:
        out["consumer_path"] = str(
            path.resolve().relative_to(Path(project).resolve()))
    except ValueError:
        out["consumer_path"] = str(path)
    out["consumer_sha256"] = _file_sha256(path)
    if proved_sha is None:
        out["state"] = BINDING_UNBOUND
    elif proved_sha != out["consumer_sha256"]:
        out["state"] = BINDING_STALE
    elif is_scan and not (isinstance(doc.get("scan_functional_mode"), dict)
                          and doc["scan_functional_mode"].get("applied") is True):
        out["state"] = BINDING_SCAN_UNCONSTRAINED
    else:
        out["state"] = BINDING_MATCH
    return out
