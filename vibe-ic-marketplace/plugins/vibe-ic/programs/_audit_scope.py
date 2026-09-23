#!/usr/bin/env python3
"""Is a completion audit the WHOLE RUN's verdict, or one scope's? R-0915-150.

`reports/audit/phase23_completion_audit.json` is written by every
`flow_compliance_check` pass, scoped or not, and four readers treat it as the run's own
verdict:

    benchmark_evidence_publish._audit_verdict   and its convergence guard
    phase3_one_shot_runner._derive_headline_verdict
    the FPGA pre-burn guard (mcp-eda de10lite driver)

A `--stage 4` pass judges 10 of the flow's 70 steps and a `--phase 2` pass 32, so reading
either as "the run's completion audit has 0 failed gates" -- the owner's bar for spm -- is
reading a different question's answer. The frozen subservient_r26 and sha256_run16_pass2
snapshots already hold 9-step audits of exactly that kind.

ONE PREDICATE, SHARED, because four copies of this rule would drift the way two copies of
the verdict rule already did. The producer records `scope` (R-0915-147); this reads it.

A document with NO scope block predates that work, so its population cannot be read off
it. It is accepted only on the weaker `phase == "all"` signal, and the caller is told WHICH
signal answered -- a weak signal read silently is the same mistake in a quieter voice.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

#: Returned as the signal when the document states its own population.
SIGNAL_SCOPE = "scope.whole_flow"
#: Returned when only the legacy `phase` field is available.
SIGNAL_LEGACY_PHASE = ("phase (no scope block: this audit predates R-0915-147, so its "
                       "population is taken on the weaker signal)")


def audit_scope_is_whole_flow(doc: Any) -> Tuple[bool, str, str]:
    """``(is_whole_flow, signal, why)`` for a completion-audit document.

    `why` is empty when the answer is yes; otherwise it says what the document judged, in
    words a disclosure can carry verbatim.
    """
    if not isinstance(doc, Mapping):
        return False, "", "the completion audit is not a JSON object"
    scope = doc.get("scope")
    if isinstance(scope, Mapping):
        if scope.get("whole_flow") is True:
            return True, SIGNAL_SCOPE, ""
        return False, SIGNAL_SCOPE, (
            f"it judged {scope.get('step_count')} of {scope.get('flow_step_total')} "
            f"step(s)"
            + (f" (stage {scope.get('stage_id') or scope.get('stage')})"
               if (scope.get('stage_id') or scope.get('stage')) else "")
            + (f" (phase {scope.get('phase')})"
               if str(scope.get("phase") or "all") != "all" else ""))
    phase = str(doc.get("phase") or "all")
    if phase == "all":
        return True, SIGNAL_LEGACY_PHASE, ""
    return False, SIGNAL_LEGACY_PHASE, f"it is stamped phase={phase!r}"
