"""R-0915-63: an output owed by a step the design declares N/A is not owed.

MEASURED 2026-09-16, run33 on main 7700250eb. Phase 3 refused to dispatch::

    [preflight] pnr: REFUSED TO RUN: 1 declared input(s) ABSENT —
      phase2/stage2/synth/post_dft_netlist.v (owed by step 12, read by step 15)

and 18 steps went MISSING; the run went from PASS=34/MISSING=0 to
PASS=9/MISSING=18. The ONLY difference from the green run before it was ONE
FIELD of `phase2/stage2/synth/post_dft_not_run.json`::

    run32   capability_flag "cap:post_dft_scan_optimization"  -> excused
    run33   capability_flag null                              -> REFUSED

R-0915-57 rightly replaced a capability-gap skip with a DESIGN-DECLARED one —
a STRONGER statement, since the design's L20 says it has no DFT at all — and
`_declared_sibling_self_skip_for_missing` knew only how to honour the weaker.

SO THERE ARE NOW TWO GROUNDS, and the capability flag is NOT restored:
  (a) unchanged — a sibling marker with an OPEN `capability_flag` entitled to
      defer that output;
  (b) new — the marker carries `reason_class: DESIGN_DECLARED_NA` and a
      `declaration` naming an L-doc and its field/value pairs, AND THIS SIDE
      RE-READS THAT DOCUMENT and checks the bytes agree.

(b) IS NOT A PROSE MATCH. The marker's `reason` already names L20 and its
fields in English and it would have been easy to read that; granting a
DESIGN_DECLARED_NA from a sentence is exactly the #2272 regression, where a
clue was treated as a declaration. The marker is a POINTER to evidence.

chip-AGNOSTIC: synthetic projects in tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import pytest  # noqa: E402

import design_one_shot_runner as D  # noqa: E402
import flow_compliance_check as F  # noqa: E402

OWNED = "phase2/stage2/synth/post_dft_netlist.v"
NO_DFT = {"dft_present": False, "scan_chains": [], "bist_mbist": [],
          "jtag_tap": None}


def _l20(project, fields):
    gd = project / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L20_DFT_SCAN_TOPOLOGY.json").write_text(
        json.dumps({"doc_id": "L20", "fields": fields}))


def _marker(project, **over):
    """The marker exactly as the runner writes it, plus any override."""
    payload = {"verdict": "SKIPPED-CONDITION",
               "reason": "no scan_netlist.v because the DESIGN declares no "
                         "DFT: L20 records dft_present=false …",
               "tool_attempted": True}
    payload.update(D._POST_DFT_SKIP_DECLARED)
    payload.update(over)
    p = project / "phase2" / "stage2" / "synth" / "post_dft_not_run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2))
    return payload


def _excused(project):
    return F._declared_sibling_self_skip_for_missing(project, [OWNED])


# ── the marker states the facts, and does not re-mint a capability ───────

def test_the_marker_carries_a_structured_citation_not_only_prose():
    d = D._POST_DFT_SKIP_DECLARED
    assert d["reason_class"] == "DESIGN_DECLARED_NA"
    assert d["declaration"]["l_doc"] == "L20"
    assert d["declaration"]["fields"] == dict(sorted(NO_DFT.items()))
    assert d["skips_required_output"] == OWNED


def test_the_capability_flag_is_NOT_restored():
    """R-57 removed a claim the design does not make; re-minting it would be
    the mis-statement, not the fix."""
    assert "capability_flag" not in D._POST_DFT_SKIP_DECLARED


def test_the_citation_cannot_drift_from_the_flow_condition():
    assert D._POST_DFT_SKIP_DECLARED["declaration"]["fields"] == dict(
        sorted(D._L20_DFT_ABSENT_FIELDS.items()))


# ── POSITIVE: run33's exact case, now excused ────────────────────────────

def test_a_design_declared_na_output_is_excused(tmp_path):
    _l20(tmp_path, NO_DFT)
    _marker(tmp_path)
    got = _excused(tmp_path)
    assert got, "the input must be excused"
    assert "design-declared N/A" in got
    assert "L20_DFT_SCAN_TOPOLOGY.json" in got
    assert "dft_present=False" in got


def test_the_ground_b_verifier_reads_the_document_itself(tmp_path):
    _l20(tmp_path, NO_DFT)
    data = _marker(tmp_path)
    cite = F._marker_declares_output_not_applicable(tmp_path, data)
    assert cite and cite.startswith("phase1/generated_docs/")
    assert (tmp_path / cite.split(" records ")[0]).is_file()


# ── NEGATIVE: the three the ruling names, and two more ───────────────────

def test_a_marker_with_NEITHER_ground_is_still_refused(tmp_path):
    """run33 as shipped: no capability_flag, no reason_class."""
    _l20(tmp_path, NO_DFT)
    _marker(tmp_path, reason_class=None, declaration=None)
    assert _excused(tmp_path) is None


def test_a_marker_citing_a_document_that_does_not_say_NA_is_refused(tmp_path):
    """THE DESIGN DECLARES DFT. The marker still claims the output is N/A;
    the document is re-read and contradicts it."""
    _l20(tmp_path, {"dft_present": True, "scan_chains": [{"name": "chain0"}],
                    "bist_mbist": [], "jtag_tap": None})
    _marker(tmp_path)
    assert _excused(tmp_path) is None


def test_a_DFT_declaring_design_with_a_missing_scan_netlist_is_refused(
        tmp_path):
    """The ruling's third control, stated as the run would meet it: L20 says
    there IS DFT, so the absent post-DFT netlist is a real gap."""
    _l20(tmp_path, {"dft_present": True, "scan_chains": [{"name": "c0"}],
                    "bist_mbist": [], "jtag_tap": None})
    _marker(tmp_path)
    assert F._marker_declares_output_not_applicable(
        tmp_path, json.loads((tmp_path / "phase2/stage2/synth"
                              / "post_dft_not_run.json").read_text())) is None
    assert _excused(tmp_path) is None


def test_a_missing_or_unparseable_L20_is_refused(tmp_path):
    """Fail-closed, the corrected R-0915-19 rule: a document that is not
    there, or will not parse, has declared nothing."""
    _marker(tmp_path)                      # no L20 at all
    assert _excused(tmp_path) is None
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    (gd / "L20_DFT_SCAN_TOPOLOGY.json").write_text("{not json")
    assert _excused(tmp_path) is None


@pytest.mark.parametrize("decl", [None, {}, {"l_doc": "L20"},
                                  {"fields": {"dft_present": False}},
                                  {"l_doc": "L20", "fields": {}}])
def test_an_incomplete_declaration_is_refused(tmp_path, decl):
    _l20(tmp_path, NO_DFT)
    _marker(tmp_path, declaration=decl)
    assert _excused(tmp_path) is None


def test_a_marker_owning_a_DIFFERENT_output_defers_nothing(tmp_path):
    _l20(tmp_path, NO_DFT)
    _marker(tmp_path, skips_required_output="phase2/stage2/synth/netlist.v")
    assert _excused(tmp_path) is None


def test_a_hard_signoff_output_is_never_deferred_on_this_ground(tmp_path):
    """The absolute refusal above ground (b) still stands: no DRC / LVS / ERC
    / STA artefact can be deferred by any marker."""
    _l20(tmp_path, NO_DFT)
    _marker(tmp_path, skips_required_output="reports/phase3/drc_signoff.rpt")
    assert F._declared_sibling_self_skip_for_missing(
        tmp_path, ["reports/phase3/drc_signoff.rpt"]) is None


def test_ground_a_is_untouched(tmp_path):
    """The capability-gap path keeps working for a flag that IS registered and
    IS entitled to the output it claims."""
    flag, outputs = next(
        (f, o) for f, o in F._DECLARED_CAPABILITY_GAP_FLAGS.items() if o)
    owned = outputs[0]
    _l20(tmp_path, NO_DFT)
    payload = {"verdict": "SKIPPED-CONDITION", "reason": "tool absent",
               "tool_attempted": True, "capability_flag": flag,
               "skips_required_output": owned}
    p = tmp_path / Path(owned).parent / "sibling_not_run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))
    assert F._declared_sibling_self_skip_for_missing(tmp_path, [owned]), \
        f"ground (a) must still excuse {owned} under {flag}"


def test_the_retired_post_dft_flag_no_longer_excuses_anything(tmp_path):
    """R-0915-57 RETIRED `cap:post_dft_scan_optimization` from the registry —
    the design has no such capability gap — and that removal must stand. This
    is why ground (b) had to exist: with the flag gone there was no ground
    left at all, which is what refused PnR on run33."""
    assert not F._is_declared_capability_gap("cap:post_dft_scan_optimization")
    _l20(tmp_path, NO_DFT)
    _marker(tmp_path, reason_class=None, declaration=None,
            capability_flag="cap:post_dft_scan_optimization")
    assert _excused(tmp_path) is None
