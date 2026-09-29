#!/usr/bin/env python3
"""Tapeout signoff check — wrapper for signoff_audit --mode tapeout.

ENFORCEMENT: advisory

WHERE THE VERDICT IS CONSUMED: the flow's step 36 gate clause
(`program_exit_zero: "tapeout_signoff_check . --mode tapeout --json
reports/audit/tapeout_signoff.json"`), judged by flow_compliance_check. No
runner spawns this gate inline: R-0915-141 (#2525) moved phase3_one_shot_runner's
pre-audit producer slot to `tapeout_checklist_gen`, the program that PRODUCES the
step's declared checklist, because a gate's own `--json` verdict can never be
the step's produced evidence. The runner never withheld the release on this
exit status even before that ("these steps' own yaml clauses already decide
them in the audit"), so the step-36 clause is, and was, where it decides.

Forwards all passthrough arguments (--json, --lenient, --strict, etc.) to
the underlying signoff_audit entry point. Prior versions hardcoded only
the project_dir + --mode and silently dropped --json PATH, preventing
reports/tapeout_checklist.json from being written when called via the
33-step flow gate. Fix recorded 2026-04-22 via <benchmark> full-flow pilot."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from signoff_audit import main  # noqa: E402
from _audit_receipt import emit_receipt  # noqa: E402


def _json_target(argv):
    """The `--json PATH` this run was asked for, or None.

    Both spellings, because a receipt that only appears for one of them is a
    receipt whose absence means nothing.
    """
    for i, a in enumerate(argv):
        if a == "--json" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--json="):
            return a.split("=", 1)[1]
    return None


def emit_receipt_for(project_dir, json_path):
    """#2050 — the receipt this 22-line shim owes the compliance checker.

    `signoff_audit` has already written the caller's `--json`; this reads that
    file back rather than re-deriving anything, so the receipt can never
    disagree with the audit it certifies. Reading back is also the only option
    available: the shim never sees the AuditResult, only an exit code.

    `verdict` keeps `signoff_audit`'s own spelling. A PASS_WITH_WAIVERS run is
    passing but not clean, and rule 11 forbids collapsing it onto a bare PASS,
    so it travels as PASS_WITH_WAIVERS and lands on the non-PASS side of the
    compliance checker's line.
    """
    if not json_path:
        return None
    src = Path(json_path)
    try:
        payload = json.loads(src.read_text())
    except (OSError, ValueError) as e:
        print(f"tapeout_signoff_check: NO RECEIPT — could not read back "
              f"{src}: {e.__class__.__name__}: {e}", file=sys.stderr)
        return None
    summary = payload.get("summary") or {}
    tier = summary.get("verdict_tier") or ""
    if payload.get("passed"):
        verdict = "PASS_WITH_WAIVERS" if tier == "PASS_WITH_WAIVERS" else "PASS"
    else:
        verdict = "FAIL"
    return emit_receipt(
        "tapeout_signoff_check", json_path, verdict,
        int(summary.get("evidence_count") or 0), [project_dir],
        extra={"program": payload.get("program"),
               "verdict_tier": tier,
               "threshold": summary.get("threshold")})


#: Step 36's DECLARED REQUIRED OUTPUT, assembled by its producer
#: `tapeout_checklist_gen`. This gate READS it (R-0929-U14-OWNER-WAIVER: a real
#: reader, replacing the machine-waiver fallback string that used to be the
#: only mention of it).
CHECKLIST_REL = "reports/audit/tapeout_checklist.json"
CHECKLIST_PRODUCER = "tapeout_checklist_gen"
CHECKLIST_READY = "READY_FOR_TAPEOUT"


def judge_checklist(project_dir) -> dict:
    """What step 36's own checklist says, judged. Never a pass by absence.

    FAIL when the checklist is absent or unreadable, was not written by its
    producer, or does not report every blocker present (`READY_FOR_TAPEOUT`
    with `blockers_missing == 0`). PASS otherwise, carrying the counts read.
    """
    path = Path(project_dir) / CHECKLIST_REL
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return {"verdict": "FAIL", "checklist": CHECKLIST_REL,
                "why": f"step 36's declared checklist is unreadable: "
                       f"{exc.__class__.__name__}: {exc}"}
    if not isinstance(doc, dict):
        return {"verdict": "FAIL", "checklist": CHECKLIST_REL,
                "why": "step 36's checklist is not a JSON object"}
    if doc.get("program") != CHECKLIST_PRODUCER:
        return {"verdict": "FAIL", "checklist": CHECKLIST_REL,
                "why": (f"step 36's checklist was written by "
                        f"{doc.get('program')!r}, not its producer "
                        f"{CHECKLIST_PRODUCER}")}
    summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
    missing = summary.get("blockers_missing")
    missing_items = [str(it.get("name")) for it in doc.get("items") or []
                     if isinstance(it, dict) and it.get("severity") != "advisory"
                     and not it.get("present")]
    ready = (doc.get("verdict") == CHECKLIST_READY and missing == 0
             and not missing_items)
    return {"verdict": "PASS" if ready else "FAIL", "checklist": CHECKLIST_REL,
            "checklist_verdict": doc.get("verdict"),
            "blockers_total": summary.get("blockers_total"),
            "blockers_missing": missing, "missing_blockers": missing_items,
            "why": ("every tapeout blocker is present" if ready else
                    f"the tapeout checklist reports {doc.get('verdict')!r} with "
                    f"{missing!r} blocker(s) missing: {missing_items}")}


def _record_checklist(json_path, judged) -> None:
    """Carry the checklist judgement in the gate's own report, beside it."""
    if not json_path:
        return
    try:
        payload = json.loads(Path(json_path).read_text())
    except (OSError, ValueError):
        return
    if isinstance(payload, dict):
        payload["tapeout_checklist"] = judged
        if judged["verdict"] != "PASS":
            payload["passed"] = False
        Path(json_path).write_text(json.dumps(payload, indent=2))


def run(user_args):
    if not user_args:
        user_args = ["."]
    # Inject --mode tapeout if the user hasn't explicitly overridden it
    if "--mode" not in user_args:
        user_args = user_args + ["--mode", "tapeout"]
    rc = main(user_args)
    judged = judge_checklist(user_args[0])
    _record_checklist(_json_target(user_args), judged)
    if judged["verdict"] != "PASS":
        print(f"FAIL: tapeout checklist ({CHECKLIST_REL}): {judged['why']}")
        rc = 1
    emit_receipt_for(user_args[0], _json_target(user_args))
    return rc


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
