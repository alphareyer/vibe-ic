#!/usr/bin/env python3
"""THE AUDIT'S VERDICT, READ IN ONE PLACE.

`flow_compliance_check` is the compliance audit, and it answers on TWO channels: it prints
one `Overall: <word>` line, and it exits with a code. Three programs in this tree read that
answer, and each read it its own way:

    design_one_shot_runner.step_final_audit          the phase-2/3 step table
    phase23_completion_self_audit_check              the SOLE ACCEPTANCE GATE, by its own
                                                     docstring: "the ONLY signal that
                                                     authorises a 'Phase 2+3 complete' claim"
    final_report_generate._run_audit                 the `final_summary.md` headline

WHY THEY CANNOT EACH HAVE THEIR OWN COPY. MEASURED on main 7a63a037f, driving each real
reader with the audit's reconciliation-canary stdout and rc 1 -- a state
`flow_compliance_check` is DESIGNED to reach: its vibe-ic#2092 canary prints the run's word,
then "THIS REPORT DOES NOT RECONCILE ... do not quote its counts", then "(the run's own
status is unchanged and still PASS.)", then `return 1`. Its own contract says it in words:
"the report is still WRITTEN ... and the run exits non-zero. `run_status` is untouched --
what changes is that the artefact stops CERTIFYING its own arithmetic."

    step_final_audit                    recorded PASS          (fixed in #2572)
    phase23_completion_self_audit_check exited 0 and printed
                                        "Overall: PASS - every canonical step executed
                                        and verified."
    final_report_generate               headline PASS

One producer, three readers, and after #2572 landed they DISAGREED: the orchestrator would
say NOT_MEASURED while the acceptance gate still certified completion. That is worse than
the original defect, and it is what a fourth private copy buys.

WHAT THIS MODULE OWNS, AND WHAT IT DELIBERATELY DOES NOT
=======================================================
It owns ONE question -- "what did the audit say, and does it stand behind it" -- and answers
it with the audit's own WORD plus a single boolean, never a verdict of its own. Each reader
still decides what to DO about the answer, because a step table, an acceptance gate and a
markdown headline owe their readers different things. Nothing here knows about steps,
projects or files.

THE WORD IS THE FULL VOCABULARY, NOT A BOOLEAN. `verdict.Verdict` has five words and
`verdict.run_verdict` can return four of them; a reader handed a boolean cannot tell
"found a defect" from "could not measure", which is the distinction R-0915-159 exists to
keep. `phase23_completion_self_audit_check` had a four-alternative regex with NO
NOT_MEASURED, so once the orchestrator started publishing that word the gate read UNKNOWN
and printed FAIL -- failing closed, so never a false pass, but mislabelling an absence as a
defect found.

THE LINE RULE IS THE MOST CAREFUL OF THE THREE IT REPLACES:
  * ANCHORED to the start of a line (`^Overall:`), because the audit prints its blocker
    list AFTER the verdict and those rows quote gate output -- an unanchored search reads
    the quotation. `phase23_completion_self_audit_check` searched `stdout + stderr`
    unanchored and took the FIRST match.
  * the LAST such line wins. The audit prints exactly one (measured: six sites embed a
    nested clause's stdout and every one truncates to `out[:200]`, which its own header plus
    two absolute paths already exceed; three real full-pass logs carry one line each), so
    "the last one" IS that line -- and if a second ever appears, the later one is the run's.
  * the token is everything up to the trailing `(strict=...)`, NOT the first whitespace-
    delimited chunk. That is ORGANIC #483's fix inside `final_report_generate`, kept here
    rather than lost: slicing on the first space truncated a verdict with internal
    whitespace, and `test_483_extract_overall_token_full_verdict` pins it.

chip-AGNOSTIC: nothing here reasons about any IC, vendor, SKU or process.
"""
# NO `from __future__ import annotations` IN THIS FILE, AND THAT IS DELIBERATE.
#
# This module is imported BY PATH by callers outside the plugin's `programs/` dir -- the
# FPGA pre-burn guard in `mcp-eda` loads it through the same resolver it uses to find
# `flow_compliance_check.py`, so that a hardware action and the orchestrator apply ONE rule.
# A module that combines `@dataclass` with the postponed-annotations future import cannot be
# loaded that way unless the importer registers it in `sys.modules` FIRST: the dataclass
# machinery resolves `sys.modules[cls.__module__].__dict__` while processing the class body
# and raises `AttributeError: 'NoneType' object has no attribute '__dict__'`. Measured here
# while wiring that guard.
#
# The annotations in this file are simple enough not to need the future import, so the fix is
# to not need the importer to know anything. `test_issue2104_programs_load_by_path` is the
# gate that cares, and a by-path arm below pins it for this module specifically.
import re
from dataclasses import dataclass
from typing import Optional

__all__ = ["AuditVerdict", "read", "verdict_word", "is_verdict_line", "GREEN_WORDS",
           "TIMEOUT_WORD", "DID_NOT_CERTIFY"]

#: The audit's own final verdict line. It prints this once, at its tail:
#: `print(f"\\nOverall: {overall}  (strict={not args.lenient})")`.
_OVERALL_LINE_RE = re.compile(r"^Overall:(?P<body>.*)$", re.M)

#: The trailing annotation the audit appends to that line.
_ANNOTATION_RE = re.compile(r"\s*\(")

#: The words that mean the audit found nothing wrong. Taken from `verdict` so this cannot
#: drift from the vocabulary; imported lazily so this module stays importable by path.
def _green_words() -> frozenset:
    try:
        import verdict as _V                            # noqa: PLC0415
        return frozenset({_V.Verdict.PASS.value, _V.Verdict.PASS_WITH_WAIVERS.value})
    except Exception:                                   # pragma: no cover
        return frozenset({"PASS", "PASS_WITH_WAIVERS"})


GREEN_WORDS = _green_words()

#: NOT a verdict at all. `phase23_completion_self_audit_check._run_compliance` SYNTHESISES
#: this word into the text when the audit times out -- the timeout travels as a string, not
#: as an rc -- and `final_report_generate` has its own constant for it. It is named here so
#: a reader cannot mistake it for the audit's judgement of the design.
TIMEOUT_WORD = "AUDIT_TIMEOUT"

#: What a reader should call the state where the word is green and the exit code is not.
DID_NOT_CERTIFY = "AUDIT_DID_NOT_CERTIFY"

#: THE EXIT CODE THAT MEANS "KILLED ON A BUDGET", and it OUTRANKS every line in the stream.
#:
#: MEASURED, and it is why this constant exists rather than a line-ordering rule. Both
#: callers that can time the audit out report it with rc 124 -- `subprocess.run(timeout=)`
#: raising `TimeoutExpired`, which is the convention `step_final_audit` has always checked --
#: and `phase23_completion_self_audit_check._run_compliance` then SYNTHESISES an
#: `Overall: AUDIT_TIMEOUT` line and puts the audit's PARTIAL STDOUT after it.
#:
#: That ordering defeats a last-line rule, and the partial stdout really can carry a verdict
#: line: `flow_compliance_check` prints its `Overall:` at line 21490 and then keeps working --
#: the blocker list, a second full design-input rescan, the publish -- so a kill in that tail
#: leaves the verdict line already through the pipe. The owner then read
#: `Overall: PASS_WITH_WAIVERS` with rc 124 and reported "did not certify", naming the #2092
#: reconciliation canary for what was a TIMEOUT; and a consumer keying on
#: `overall == "AUDIT_TIMEOUT"` saw the green word instead and called the run MEASURED.
#:
#: So the rc decides this one, first, and no line can move it. A process killed on a budget
#: examined an unknown fraction of the flow, and the fraction is what `AUDIT_TIMEOUT` names.
TIMEOUT_RC = 124


@dataclass(frozen=True)
class AuditVerdict:
    """What the audit said, and whether it stands behind it.

    `word`      the audit's own final verdict word, verbatim, or `None` if it printed no
                verdict line at all.
    `rc`        the exit code the caller observed, or `None` when the caller has none.
    `certified` the audit stands behind `word`. FALSE for a green word with a non-zero
                exit, for `AUDIT_TIMEOUT`, and for no verdict line. TRUE for FAIL and for
                NOT_MEASURED: those are the audit's account and it does stand behind them.
    `why`       one sentence, empty when `certified`.
    """

    word: Optional[str]
    rc: Optional[int]
    certified: bool
    why: str = ""

    @property
    def is_green(self) -> bool:
        """The one question every reader asks: may this run be called a pass?"""
        return self.certified and (self.word or "").upper() in GREEN_WORDS


def is_verdict_line(line: str) -> bool:
    """Is this line the audit's verdict line?

    Exposed because ONE other question in the tree is about the verdict LINE rather than
    the verdict: `design_one_shot_runner._final_audit_detail` finds where the verdict
    paragraph starts so it can quote the gating block beneath it. That is not a fourth copy
    of "what did the audit say", but it must agree with this module about what a verdict
    line IS -- otherwise the two could disagree about which line that is.
    """
    return bool(_OVERALL_LINE_RE.match(line or ""))


def verdict_word(out: str) -> Optional[str]:
    """The audit's own final verdict word, or `None` when it printed no verdict line."""
    bodies = _OVERALL_LINE_RE.findall(out or "")
    if not bodies:
        return None
    token = _ANNOTATION_RE.split(bodies[-1], maxsplit=1)[0].strip()
    return token or None


def read(out: str, rc: Optional[int] = None) -> AuditVerdict:
    """The audit's verdict from BOTH its channels.

    `rc=None` means the caller genuinely has no exit code to offer (it read a transcript,
    not a process). That is not the same as `rc=0` and is never treated as one: a word with
    no exit code behind it is reported as the word, uncertified, saying so.
    """
    word = verdict_word(out)
    upper = (word or "").upper()
    # THE BUDGET KILL IS DECIDED BY THE rc, BEFORE ANY LINE IS CONSULTED. See `TIMEOUT_RC`:
    # the caller that times the audit out prepends its own `Overall: AUDIT_TIMEOUT` line and
    # appends the partial stdout, which can itself carry a real verdict line, so neither
    # "first line" nor "last line" is a safe way to recognise this state.
    if rc == TIMEOUT_RC:
        return AuditVerdict(
            TIMEOUT_WORD, rc, False,
            f"the audit was stopped on its budget (exit {rc}), so it examined an unknown "
            f"fraction of the flow and this is not a verdict on the project "
            f"(INCONCLUSIVE, #525)"
            + (f"; the partial output's own last verdict line said `{word}`, which is a"
               f" fraction's word and not the run's" if word and upper != TIMEOUT_WORD
               else ""))
    if word is None:
        return AuditVerdict(None, rc, False,
                            "the audit printed no `Overall:` line, so it stated no verdict")
    if upper == TIMEOUT_WORD:
        return AuditVerdict(word, rc, False,
                            "the audit did not run to completion, so this is not a "
                            "verdict on the project (INCONCLUSIVE, #525)")
    if upper in GREEN_WORDS:
        if rc is None:
            return AuditVerdict(word, rc, False,
                                f"`Overall: {word}` was read without the audit's exit "
                                f"code, and the word alone does not say whether the audit "
                                f"certified it")
        if rc != 0:
            return AuditVerdict(
                word, rc, False,
                f"the audit printed `Overall: {word}` and then exited {rc}. A run word "
                f"and a non-zero exit are its two accounts of itself and they disagree, "
                f"so it did not certify: the known shape is its own reconciliation "
                f"canary (vibe-ic#2092), which leaves the word standing and refuses to "
                f"vouch for the arithmetic behind it (\"do not quote its counts\")")
    return AuditVerdict(word, rc, True, "")


if __name__ == "__main__":  # pragma: no cover - a smoke entry, not a gate
    import sys
    print(__doc__.strip().splitlines()[0], file=sys.stderr)
