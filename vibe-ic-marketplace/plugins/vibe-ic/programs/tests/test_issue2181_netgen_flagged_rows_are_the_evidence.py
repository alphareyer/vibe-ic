"""vibe-ic#2181 — netgen's own `**Mismatch**` rows must decide, and must be shown.

THE DEFECT
----------
`lvs_verdict_tokens` modelled three netgen shapes — `(no pin, node is …)`,
`(no matching pin)`, `property errors were found` — and none of the rows netgen
flags ITSELF with `**Mismatch**`. Two consequences, both measured on real reports
in this fleet's run corpus (2362 netgen reports; 218 carry a flagged row, 0 of
those classify MATCH):

1. VERDICT.  Exactly ONE of the 2362 reached `POWER_PIN_ONLY` — the benign,
   waiver-candidate class — while carrying

       Number of nets: 365 **Mismatch**  |Number of nets: 367 **Mismatch**
       VGND                              |VPWR **Mismatch**
       VPWR                              |VGND **Mismatch**

   i.e. a two-net difference AND the power rails SWAPPED. `POWER_PIN_ONLY` is
   what `phase3_one_shot_runner` converts to `LVS_MATCH_POWER_AWARE` (status
   PASS) and what `lvs_tapeout_signoff_check` reports as a waiver candidate, so
   the worst LVS error there is was classified benign. The module's own doctrine
   — "the benign bucket is never reached by elimination" — did not hold, because
   a shape it never modelled reached it.

   The distinction is SEMANTIC, not statistical. The waivable artifact is an
   ABSENCE: the power-unaware yosys netlist has no rail port at all, and netgen
   prints `VPWR |(no matching pin)`. A `**Mismatch**` row is the opposite fact —
   netgen DID find a counterpart and it is the WRONG one.

2. EVIDENCE.  On a `Final result: Netlists do not match.` report there is no
   `(no pin, node is …)` row at all, so `pin_mismatch_evidence` fell through to
   the `(no matching pin)` rows — the benign sub-cell abstraction rows the
   sibling `mismatch_class` explicitly refuses to classify on. Measured on two
   real reports: every downstream reader (`lvs_verdict.json`'s
   `pin_mismatch_evidence`, the runner's FAIL detail, the tapeout check's
   `evidence`) was handed `ZN |(no matching pin)` twice, while netgen's own
   flagged rows read `VDD |x[14] **Mismatch**` and `VSS |x[28] **Mismatch**`.
   Handing a reader the power/well rows as "the mismatches" is exactly how a row
   that is NOT the cause gets reported as the cause.

PREDICTED DIRECTIONS, written before these ran
----------------------------------------------
  A  flagged power row + `failed pin matching`  : POWER_PIN_ONLY -> SIGNAL_NET_MISMATCH
  B  the same report with the flagged rows gone : POWER_PIN_ONLY, unchanged
     (the benign class must not be narrowed to empty)
  C  `Netlists do not match`, no node rows      : evidence moves from the
     `(no matching pin)` rows to the flagged rows
  D  a report carrying BOTH                     : node rows still win, unchanged
  E  `classify()` on every fixture              : unchanged in both directions
  F  a MATCH carrying a flagged row             : `NONE`, never sub-classified
All six held. MUTATION ARM: deleting the `_PORT_MISMATCH_RE` clause from
`mismatch_class` reddens A; deleting it from `pin_mismatch_evidence` reddens C.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import lvs_verdict_tokens as T  # noqa: E402


# The absence artifact the benign bucket exists for: the schematic carries no
# rail port at all, so netgen prints `(no matching pin)` beside each rail.
_ABSENCE_ONLY = (
    "netgen 1.5.257 compare\n"
    "Subcircuit pins:\n"
    "Circuit 1: top                             |Circuit 2: top\n"
    "-------------------------------------------|---------------------------\n"
    "VPWR                                       |(no matching pin)\n"
    "VGND                                       |(no matching pin)\n"
    "Final result: Top level cell failed pin matching.\n"
)

# The same failing shape PLUS the rows netgen flagged itself: a two-net
# difference and the rails matched to each other.
_FLAGGED_SWAP = (
    "netgen 1.5.257 compare\n"
    "Subcircuit summary:\n"
    "Number of nets: 365 **Mismatch**            |Number of nets: 367 **Mismatch**\n"
    "Subcircuit pins:\n"
    "Circuit 1: top                             |Circuit 2: top\n"
    "-------------------------------------------|---------------------------\n"
    "VPWR                                       |(no matching pin)\n"
    "VGND                                       |VPWR **Mismatch**\n"
    "VPWR                                       |VGND **Mismatch**\n"
    "Final result: Top level cell failed pin matching.\n"
)

# `Netlists do not match.` — no `failed pin matching`, no `(no pin, node is …)`
# row anywhere. The tail carries the rows netgen flagged; the FRONT carries the
# benign sub-cell abstraction rows the old fallback returned.
_NETLISTS_DO_NOT_MATCH = (
    "netgen 1.5.257 compare\n"
    + "ZN                                         |(no matching pin)\n" * 2
    + "Subcircuit pins:\n"
    "Circuit 1: top                             |Circuit 2: top\n"
    "-------------------------------------------|---------------------------\n"
    "VDD                                        |q[14] **Mismatch**\n"
    "VSS                                        |q[28] **Mismatch**\n"
    "Cell pin lists are equivalent.\n"
    "Device classes top and top are equivalent.\n"
    "\n"
    "Final result: Netlists do not match.\n"
    "Port matching may fail to disambiguate symmetries.\n"
)

# Both shapes present: the top-level failing table's `(no pin, node is …)` rows
# remain the most specific evidence and must keep winning.
_BOTH = (
    "netgen 1.5.257 compare\n"
    "Number of nets: 5 **Mismatch**              |Number of nets: 6 **Mismatch**\n"
    "(no pin, node is q[3])                     |r[3]\n"
    "Final result: Top level cell failed pin matching.\n"
)

_MATCH_WITH_FLAG = (
    "netgen 1.5.257 compare\n"
    "Number of nets: 5 **Mismatch**              |Number of nets: 6 **Mismatch**\n"
    "Final result: Circuits match uniquely.\n"
)


# --------------------------------------------------------------------------
# A / B — the verdict half: the benign bucket keeps its documented meaning
# --------------------------------------------------------------------------

def test_a_flagged_power_row_is_not_the_benign_class():
    """A rail netgen matched to the OTHER rail is a wrong correspondence, not
    the absence the waiver candidate is for."""
    assert T.classify(_FLAGGED_SWAP) == "MISMATCH"
    assert T.mismatch_class(_FLAGGED_SWAP) == "SIGNAL_NET_MISMATCH"


def test_the_benign_absence_artifact_is_still_reachable():
    """NEGATIVE CONTROL for A: the fix must not narrow the benign class to
    empty. Same report, flagged rows removed — still POWER_PIN_ONLY."""
    assert T.classify(_ABSENCE_ONLY) == "MISMATCH"
    assert T.mismatch_class(_ABSENCE_ONLY) == "POWER_PIN_ONLY"


# --------------------------------------------------------------------------
# C / D — the evidence half: show the reader what netgen flagged
# --------------------------------------------------------------------------

def test_evidence_names_the_rows_netgen_flagged():
    """On `Netlists do not match.` the reader used to be handed only the benign
    sub-cell rows. It must now be handed the rows netgen flagged."""
    ev = T.pin_mismatch_evidence(_NETLISTS_DO_NOT_MATCH)
    assert ev, "evidence expected"
    assert all("**Mismatch**" in line for line in ev), ev
    assert any("q[14]" in line for line in ev), ev
    assert not any("(no matching pin)" in line for line in ev), ev


def test_node_rows_still_outrank_flagged_rows():
    """NO-REGRESSION: the `(no pin, node is …)` top-level failing rows are the
    most specific evidence and keep first place."""
    ev = T.pin_mismatch_evidence(_BOTH)
    assert ev == ["(no pin, node is q[3])                     |r[3]"], ev


def test_the_absence_artifact_still_shows_its_own_rows():
    """NO-REGRESSION: with nothing flagged, the `(no matching pin)` rows are
    still what the reader gets."""
    ev = T.pin_mismatch_evidence(_ABSENCE_ONLY)
    assert ev and all("(no matching pin)" in line for line in ev), ev


# --------------------------------------------------------------------------
# E / F — the authority is untouched, and the change is one-directional
# --------------------------------------------------------------------------

def test_classify_is_untouched_by_the_flagged_rows():
    """`classify()` is the verdict authority and this change is sub-class and
    evidence only. Measured on the corpus: 0 of 2362 reports change verdict."""
    assert T.classify(_FLAGGED_SWAP) == "MISMATCH"
    assert T.classify(_NETLISTS_DO_NOT_MATCH) == "MISMATCH"
    assert T.classify(_ABSENCE_ONLY) == "MISMATCH"
    assert T.classify(_MATCH_WITH_FLAG) == "MATCH"


def test_a_matching_report_is_never_sub_classified():
    """The flagged-row clause lives inside the MISMATCH branch, so it can only
    move a mismatch from benign to real — never make a match fail."""
    assert T.mismatch_class(_MATCH_WITH_FLAG) == "NONE"
