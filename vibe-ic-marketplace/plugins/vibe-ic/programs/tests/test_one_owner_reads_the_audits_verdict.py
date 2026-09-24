"""test_one_owner_reads_the_audits_verdict.py

ONE PRODUCER, THREE READERS, AND THEY DISAGREED.

`flow_compliance_check` answers on two channels: one `Overall: <word>` line and an exit
code. Three programs read that answer and each read it its own way:

    design_one_shot_runner.step_final_audit          the phase-2/3 step table
    phase23_completion_self_audit_check              by its own docstring, "the ONLY signal
                                                     that authorises a 'Phase 2+3 complete'
                                                     claim"
    final_report_generate._run_audit                 the `final_summary.md` headline

MEASURED on main fd9e3342f, driving each real reader with the audit's reconciliation-canary
stdout and rc 1 — a state the audit is DESIGNED to reach (vibe-ic#2092: print the run word,
print "do not quote its counts", say the status is unchanged, `return 1`):

    step_final_audit                    NOT_MEASURED   (fixed in #2572)
    phase23_completion_self_audit_check exit 0, "Overall: PASS — every canonical step
                                        executed and verified."
    final_report_generate               headline PASS

So after #2572 the orchestrator said "could not certify" while the acceptance gate still
authorised completion. Two readers were left with the defect the third had fixed, which is
what a private copy of a shared question buys.

THE RULE, with no tool or step name in it:

    A producer has as many channels as it chooses to use, and one question has one owner.
    A consumer that reads a word while ignoring the exit code has not read the producer;
    three consumers that each read it their own way will eventually publish opposite
    verdicts about one run.

Fixtures quote the producer's OWN prints and the vocabulary's OWN words.
"""
from __future__ import annotations

import io
import contextlib
import sys
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))

import verdict as _V                                   # noqa: E402
import _audit_verdict as A                             # noqa: E402
import _watchdog as _wd                                # noqa: E402
import design_one_shot_runner as D                     # noqa: E402
import phase23_completion_self_audit_check as GATE     # noqa: E402
import final_report_generate as F                      # noqa: E402

HEADER = "Steps: 34 total (34/34 executed PASS, 0 DEFERRED via waiver)\n"


def canary(word: str = "PASS") -> str:
    """The audit's stdout when its own report does not reconcile — its own prints."""
    return (
        "=== Vibe-IC phase1_phase2_phase3 compliance ===\n" + HEADER +
        f"\nOverall: {word}  (strict=True)\n"
        "\nflow_compliance_check: THIS REPORT DOES NOT RECONCILE — 1 of 7 equation(s) over "
        "its own numbers are false. The report was still written, with `reconciled: false`; "
        "do not quote its counts.\n"
        "  ✗ [tally_sums] executed == PASS+FAIL+… — 34 != 33\n"
        f"  (the run's own status is unchanged and still {word}.)\n")


def clean(word: str = "PASS") -> str:
    return canary(word).split("\nflow_compliance_check: THIS REPORT")[0] + "\n"


# ═══ the owner ════════════════════════════════════════════════════════════

def test_the_owner_reads_both_channels():
    assert A.read(clean("PASS"), 0).is_green is True
    assert A.read(canary("PASS"), 1).is_green is False
    assert A.read(canary("PASS"), 1).certified is False
    assert A.read(canary("PASS_WITH_WAIVERS"), 1).is_green is False
    # a word the audit DOES stand behind, however unwelcome
    for w in (_V.Verdict.FAIL.value, _V.Verdict.NOT_MEASURED.value):
        v = A.read(f"\nOverall: {w}  (strict=True)\n", 1)
        assert v.certified is True and v.is_green is False, w
        assert v.word == w


def test_the_owner_returns_the_word_not_a_boolean():
    """A boolean cannot tell "found a defect" from "could not measure" — the distinction
    R-0915-159 exists to keep, and the reason the acceptance gate needed the vocabulary."""
    for w in [v.value for v in _V.RUN_PRECEDENCE]:
        assert A.read(f"\nOverall: {w}  (strict=True)\n", 0).word == w


def test_the_owner_anchors_and_takes_the_last_verdict_line():
    """The audit prints its blocker list AFTER the verdict, and those rows quote gate
    output; an unanchored or first-match rule reads the quotation."""
    out = ("\nOverall: FAIL  (strict=True)\n"
           "\nBlocker list (classified) — 1 non-PASS step(s).\n"
           "       observed : program failed | output: Overall: PASS  (strict=True)\n")
    assert out.rindex("Overall: PASS") > out.rindex("Overall: FAIL")
    assert A.verdict_word(out) == "FAIL"
    # and when there really are two verdict LINES, the later one is the run's
    assert A.verdict_word("Overall: PASS  (x)\nOverall: FAIL  (x)\n") == "FAIL"


def test_the_owner_keeps_issue_483s_token_rule():
    """#483: the token is everything up to the trailing annotation, never the first
    whitespace chunk. The three readers' rules differed; the owner carries the careful one."""
    assert A.verdict_word("Overall: PASS_WITH_OPEN_SOURCE_CONSTRAINTS  (strict=True)\n") == (
        "PASS_WITH_OPEN_SOURCE_CONSTRAINTS")
    assert A.verdict_word("Overall: FA IL\n") == "FA IL"
    assert F._extract_overall_token("Overall: FA IL") == "FA IL"


def test_the_owner_names_the_two_non_verdicts():
    """A timeout and a missing verdict line are not judgements about the design, and a
    green word with NO exit code is not a certificate either."""
    assert A.read("\nOverall: AUDIT_TIMEOUT\n", 124).certified is False
    assert A.read("nothing here\n", 0).word is None
    assert A.read("nothing here\n", 0).certified is False
    assert A.read(clean("PASS"), None).certified is False, (
        "a word read without the audit's exit code was treated as certified")


# ═══ reader 1 — the step table ════════════════════════════════════════════

def _final_audit(monkeypatch, tmp_path, rc, out):
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(D, "_run", lambda *a, **k: (rc, out, ""))
    return D.step_final_audit(tmp_path, phase=3)


def test_reader_one_still_refuses_and_now_asks_the_owner(monkeypatch, tmp_path):
    r = _final_audit(monkeypatch, tmp_path, 1, canary("PASS"))
    assert r.status == _V.Verdict.NOT_MEASURED.value, r.status
    assert (r.extras or {}).get("finding") == "AUDIT_DID_NOT_CERTIFY", r.extras


def test_reader_one_clean_run_is_unchanged(monkeypatch, tmp_path):
    assert _final_audit(monkeypatch, tmp_path, 0, clean("PASS")).status == "PASS"
    assert _final_audit(monkeypatch, tmp_path, 0,
                        clean("PASS_WITH_WAIVERS")).status == "PASS_WITH_WAIVERS"
    assert _final_audit(monkeypatch, tmp_path, 1, clean("FAIL")).status == "FAIL"


def test_reader_one_no_longer_owns_the_rule():
    """The helper #2572 put here MOVED; it was not copied. A second name for one rule is
    the defect this branch closes."""
    assert not hasattr(D, "_final_audit_verdict_word"), (
        "`design_one_shot_runner` still exposes its own verdict-word helper, so the rule "
        "exists in two places again")
    assert not hasattr(D, "_FINAL_VERDICT_RE")


# ═══ reader 2 — the sole acceptance gate ══════════════════════════════════

def _gate(monkeypatch, tmp_path, rc, out):
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(GATE, "_run_compliance", lambda p, strict=True: (rc, out))
    monkeypatch.setattr(sys, "argv", ["gate", str(tmp_path)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = GATE.main()
    return code, buf.getvalue()


def test_the_acceptance_gate_does_not_authorise_an_uncertified_run(monkeypatch, tmp_path):
    """THE REPRODUCTION. On main this exits 0 and prints "every canonical step executed and
    verified" — the one verdict in the tree that authorises a completion claim."""
    code, out = _gate(monkeypatch, tmp_path, 1, canary("PASS"))
    assert code == 1, (
        f"the SOLE ACCEPTANCE GATE authorised completion for a run whose audit exited 1 "
        f"while refusing to vouch for its counts (exit {code})")
    assert "every canonical step executed and verified" not in out, out
    assert "AUDIT_DID_NOT_CERTIFY" in out, out
    assert "did not certify" in out, out


def test_the_acceptance_gate_says_the_same_thing_it_exits(monkeypatch, tmp_path):
    """The printed word and the exit code are ONE decision. These branches keyed on the
    word while the exit keyed on completeness, so reading the rc made them disagree — the
    gate printed `[PASS]` and exited 1 at the first cut of this change."""
    for rc, out_text, want_zero in ((0, clean("PASS"), True),
                                    (0, clean("PASS_WITH_WAIVERS"), True),
                                    (1, canary("PASS"), False),
                                    (1, clean("FAIL"), False),
                                    (1, clean("NOT_MEASURED"), False)):
        code, text = _gate(monkeypatch, tmp_path, rc, out_text)
        head = text.strip().splitlines()[0]
        assert (code == 0) is want_zero, (out_text, code, head)
        says_pass = head.startswith("[PASS]") or head.startswith("[PASS_WITH_WAIVERS]")
        assert says_pass is want_zero, (
            f"the gate printed {head!r} and exited {code}: the human and the script get "
            f"opposite answers")


def test_the_acceptance_gate_knows_the_word_2572_made_reachable(monkeypatch, tmp_path):
    """NOT_MEASURED had no alternative in the old regex: it read as UNKNOWN and printed
    `[FAIL]` — an absence labelled a defect found. It fails closed either way, so this is
    about the LABEL, which is what R-0915-159 is about."""
    code, out = _gate(monkeypatch, tmp_path, 1, clean("NOT_MEASURED"))
    assert code == 1
    assert out.strip().splitlines()[0].startswith("[NOT_MEASURED]"), out
    assert "NOT a FAIL" in out, out


def test_the_acceptance_gate_keeps_the_timeouts_own_name(monkeypatch, tmp_path):
    """#525 NAMED THE TIMEOUT ON PURPOSE, and my first cut of this change lost that name.

    `_audit_verdict` reports a timeout as uncertified — true, but not specific — so folding it
    into the generic did-not-certify branch relabelled it `[AUDIT_DID_NOT_CERTIFY]` and threw
    away #525's distinction (審不完 timed out, versus 沒審 never audited; neither a verdict
    about the design). The exit code was right either way, which is exactly why only an arm
    about the LABEL catches it. Same shape as `test_the_timeout_tier_is_untouched` in #2572.
    """
    timeout_out = ("Overall: AUDIT_TIMEOUT\nAUDIT TIMEOUT after 900s — the compliance audit "
                   "did not run to completion; this is NOT a verdict on the project "
                   "(INCONCLUSIVE, #525).\n")
    code, out = _gate(monkeypatch, tmp_path, 124, timeout_out)
    assert code == 1, out
    head = out.strip().splitlines()[0]
    assert head.startswith("[AUDIT_TIMEOUT]"), (
        f"the timeout lost its own name and was reported as a generic refusal: {head!r}")
    assert "NOT a verdict on the project" in out, out


def test_the_acceptance_gate_still_passes_a_clean_run(monkeypatch, tmp_path):
    """NEGATIVE CONTROL, and the one that matters most: the gate must still authorise a run
    the audit certified."""
    code, out = _gate(monkeypatch, tmp_path, 0, clean("PASS"))
    assert code == 0, out
    assert "every canonical step executed and verified" in out, out


# ═══ reader 3 — the final_summary.md headline ═════════════════════════════

def _supervised(rc, out):
    return _wd.SupervisedResult(rc=rc, out=out, err="", outcome="completed",
                                elapsed_s=1.0, abort_reason=None, scope=None,
                                supervision=None)


def _run_audit(monkeypatch, tmp_path, rc, out):
    monkeypatch.setattr(F._wd, "run_host_supervised", lambda cmd, **kw: _supervised(rc, out))
    return F._run_audit(tmp_path)


def test_the_headline_does_not_read_pass_for_an_uncertified_audit(monkeypatch, tmp_path):
    """THE REPRODUCTION for reader 3: on main the `final_summary.md` headline reads
    `Overall: PASS` for a run whose audit refused to vouch for its counts."""
    text, overall = _run_audit(monkeypatch, tmp_path, 1, canary("PASS"))
    assert overall == F.AUDIT_DID_NOT_CERTIFY_VERDICT, overall
    assert overall != "PASS"
    assert "AUDIT DID NOT CERTIFY" in text, text[-300:]


def test_the_headline_withholds_the_snapshot_it_was_told_not_to_quote():
    """The audit said "do not quote its counts", so the per-step snapshot must be withheld
    the way it already is for a timeout. A headline that refuses while the table beside it
    quotes the withdrawn numbers would be the same defect one line down."""
    src = (PROGRAMS / "final_report_generate.py").read_text()
    idx = src.index("AUDIT_TIMEOUT_VERDICT, AUDIT_NOT_RUN_VERDICT")
    window = src[idx:idx + 200]
    assert "AUDIT_DID_NOT_CERTIFY_VERDICT" in window, (
        "the did-not-certify headline does not withhold the fresh audit snapshot")


def test_the_headline_is_unchanged_for_a_certified_audit(monkeypatch, tmp_path):
    """NEGATIVE CONTROL, all four words the audit stands behind."""
    for w in ("PASS", "PASS_WITH_WAIVERS", "FAIL", "NOT_MEASURED"):
        rc = 0 if w in ("PASS", "PASS_WITH_WAIVERS") else 1
        _t, overall = _run_audit(monkeypatch, tmp_path, rc, clean(w))
        assert overall == w, (w, overall)


# ═══ one owner, and no fourth copy ════════════════════════════════════════

def test_no_program_parses_the_verdict_line_outside_the_owner():
    """THE ARM THAT STOPS THE FOURTH COPY, and it is the reason this branch exists at all:
    three private copies of one question is how two readers kept a defect the third had
    fixed. A new reader must ASK, and this fails the moment one spells the rule itself."""
    import re as _re
    offenders = []
    pat = _re.compile(r'startswith\(\s*["\']Overall:|'
                      r're\.(?:compile|search|match|findall|finditer)\([^)]*Overall:')
    for py in sorted(PROGRAMS.glob("*.py")):
        if py.name == "_audit_verdict.py":
            continue                       # the owner, and its own commentary
        for lineno, line in enumerate(py.read_text(errors="replace").splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{py.name}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        "the audit's verdict line is parsed outside `_audit_verdict` again — ask "
        "`_audit_verdict.read(out, rc)` (or `is_verdict_line` for the LINE question) "
        "instead of spelling the rule:\n  " + "\n  ".join(offenders))


def test_all_three_readers_ask_the_owner():
    """The positive half: a sweep that finds no parser is also satisfied by a reader that
    stopped reading the verdict at all."""
    for name in ("design_one_shot_runner", "phase23_completion_self_audit_check",
                 "final_report_generate"):
        src = (PROGRAMS / f"{name}.py").read_text(errors="replace")
        assert "_audit_verdict" in src, f"{name} does not ask the owner"
