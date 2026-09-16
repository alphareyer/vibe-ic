#!/usr/bin/env python3
"""audit_replay.py — re-judge a FROZEN run snapshot with the CURRENT tree's
programs, and say which step verdicts the JUDGEMENT moved.

CHIP_AGNOSTIC: strict-logic — the LOGIC below copies a directory and runs one
Python program over it; the ICs appear only in this docstring, as the evidence.

WHY THIS EXISTS (OWNER RULING R-0915-86 (1), 2026-09-16)
========================================================
`real_ic_gate` answers "did this change break a real IC?" by RUNNING one — eight
minutes and an EDA container for the cheapest IC in the corpus, ~55 minutes for
subservient. That is affordable nightly and on a landing, and unaffordable for
the change whose whole content IS a judgement: R-0915-85 is reducing 23 verdict
words to 5 AT THE PRODUCERS, and the question "what does that do to r26, to
run16, to SPM?" must be answerable in seconds, on trees nobody can re-run
because the tools that made them are gone.

A frozen snapshot is the answer. The artefacts are fixed; the only variable is
the tree doing the judging. So:

    SUBJECT     a frozen project snapshot (reports/ + input/ + generated docs).
                Never modified — the replay works on a copy.
    INSTRUMENT  THIS tree's `flow_compliance_check ... --strict`, which is the
                producer of the completion audit and of the per-step table.
    ANSWER      the same per-step table `real_ic_gate` emits, through the same
                `_step_verdict_table` module, diffed the same way.

No containers, no tools, no second harness (R-0915-85). MEASURED 2026-09-16 on
`<home>/_frozen/subservient_r26`: 7.4 s wall for a 69-step table, and
the snapshot byte-count unchanged (418 files before and after).

`--baseline`: RECORD THE REFERENCE, ONCE, AT A NAMED TREE
=========================================================
MEASURED on the third arm's first full landing run (2026-09-16): BOTH frozen
snapshots came back `NOT_COMPARABLE`, because the reference each one offers is
its own recorded audit and the LAST compliance invocation of a run overwrites
that file —

    subservient_r26      reference written by stage4_compliance.py (9 steps)
    sha256_run16_pass2   reference flags ['--stage-id=stage_analog','--strict'] (9 steps)

against a full `--strict` replay's 69. The refusal was CORRECT and the arm was
still useless: every replay refused forever, and a gate that always refuses is a
gate nobody reads.

`--baseline` closes it without weakening anything. It replays the snapshot ONCE
at the full scope this program always uses, and writes the resulting table as
the snapshot's REFERENCE FILE, stamped with the tree sha that produced it and
the argv that produced it. Every later replay diffs against THAT — same subject,
same instrument scope, same run shape by construction, and the only variable is
the tree, which is the experiment.

IT IS NOT A `--write-baseline`. Nothing is re-tiered, no verdict is edited and
no refusal is turned green: the file recorded is the table the current tree
ACTUALLY produced, byte for byte, and a later diff against it is refused exactly
as strictly as before if its producer or scope does not match. The only thing
`--baseline` adds is that the reference is a table this program made rather than
one a run happened to leave behind. The tree sha is stamped so a reader can
always ask "compared with WHAT", and `--baseline` REFUSES to overwrite an
existing reference unless `--force-baseline` is given, so a baseline cannot be
silently re-cut to make a red diff disappear.

THE REFERENCE IS THE HARD PART, AND IT IS WHY THIS PROGRAM REFUSES
==================================================================
MEASURED, and the reason `comparability` exists in `_step_verdict_table`: the
LAST compliance invocation of a run overwrites
`reports/audit/phase23_completion_audit.json`, so a STAGE-SCOPED invocation
replaces the full table with its own. Both snapshots the orchestrator supplied
carry such a table:

    subservient_r26       9 steps   stage4_compliance.py . --exclude-step 39
    sha256_run16_pass2    9 steps   flow_compliance_check.py ... --stage-id stage_analog

while a full `--strict` pass over the same trees yields 69. Diffing those would
print 60 REMOVED steps and read as a catastrophe that never happened. So the
default reference — the snapshot's own recorded audit — is CHECKED for
comparability and the diff REFUSES (rc 2) when the scopes differ. The table is
still emitted in full, because that emitted table is the BASELINE: the intended
use is

    today   audit_replay <snap> --json baseline.json        # on main as it is
    later   audit_replay <snap> --reference baseline.json   # on main + the reform

where both sides were produced by the same fixed invocation on the same frozen
subject, so tool availability, snapshot completeness and every other confound is
held constant and the ONLY difference is the tree. That is the experiment; the
snapshot's own recorded table is a convenience that works only when the run's
last compliance pass happened to be the full one.

WHAT A DIFFERENCE HERE MEANS — AND WHAT IT DOES NOT
===================================================
A snapshot is a STRIPPED tree: it carries reports and docs, not a 2 GB layout.
So a full `--strict` replay calls absent artefacts MISSING and cascades from
them — measured on r26: PASS 0 / FAIL 18 / MISSING 16 / INCOMPLETE 4 /
PASS-VOIDED 5 / SKIPPED-CONDITION 24 / WAIVED 2. That is NOT a claim about the
run; it is a property of the snapshot, and it is IDENTICAL on both sides of a
tree-vs-tree diff, which is exactly why the diff is still valid. This program
therefore never prints the replayed verdict as the run's verdict: the snapshot's
OWN recorded verdict is carried into the report beside it, labelled.

EXIT CODES
==========
  0  replayed, and against the reference NO step regressed and the top-level
     word did not fall. Improvements are listed.
  1  a REGRESSION: a step's verdict fell under the current tree's judgement.
  2  a REFUSAL — nothing is claimed: the snapshot is unreadable, the replay
     produced no table, or the reference is NOT COMPARABLE.

chip-AGNOSTIC and tool-AGNOSTIC: this program copies a directory and runs one
Python program over it.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------

import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# vibe-ic#1082: a declared report destination is written through the
# atomic writer, so a crash mid-write leaves the previous report or none,
# never a truncated JSON a later reader parses as a verdict.
from _atomic_artefact import write_json as _atomic_write_json  # noqa: E402
from typing import Any, Dict, List, Optional

import _step_verdict_table as _svt

AUDIT_REL = Path("reports") / "audit" / "phase23_completion_audit.json"
REFUSED = "REFUSED"


class Refusal(Exception):
    """Nothing is claimed. Carries the ONE line that says why."""


def read_recorded(snapshot: Path) -> Optional[Dict[str, Any]]:
    """The snapshot's OWN table, read and kept BEFORE anything is replayed.

    Returns None when the snapshot carries no completion audit at all — which is
    not fatal by itself (the caller may supply `--reference`), and IS fatal when
    it is also the reference, because a reference that does not exist cannot be
    diffed against and a gate with no reference must refuse rather than pass.
    """
    p = snapshot / AUDIT_REL
    if not p.is_file():
        return None
    try:
        return _svt.load_table(p)
    except _svt.TableUnreadable as exc:
        raise Refusal("the snapshot's own %s is unreadable: %s" % (AUDIT_REL, exc))


def replay(snapshot: Path, tree: Path, workdir: Path,
           extra: List[str]) -> Dict[str, Any]:
    """Copy the snapshot and run THIS tree's compliance pass over the copy.

    The copy is why the original is never touched. `flow_compliance_check` is a
    PRODUCER as well as a judge — its own `--read-only` docstring records 25
    files added and 17 rewritten by one invocation over a published tree — so
    pointing it at evidence and hoping is not an option. A copy also lets the
    canonical `reports/audit/phase23_completion_audit.json` land at its real
    path, which is the artefact shape every consumer keys on; `--read-only`
    would discard it with the disposable copy it makes internally.
    """
    checker = (tree / "vibe-ic-marketplace" / "plugins" / "vibe-ic" / "programs"
               / "flow_compliance_check.py")
    if not checker.is_file():
        raise Refusal("the tree has no compliance checker at %s" % checker)
    if not (snapshot / "reports").is_dir():
        raise Refusal("%s is not a run snapshot — it has no reports/ directory"
                      % snapshot)

    copy = workdir / ("replay_" + snapshot.name)
    if copy.exists():
        shutil.rmtree(copy)
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot, copy, symlinks=True)

    # The replay must not read a table it did not write: delete the recorded one
    # from the COPY (never from the snapshot) so an invocation that silently
    # produced nothing cannot be mistaken for one that agreed with the record.
    stale = copy / AUDIT_REL
    if stale.is_file():
        stale.unlink()

    argv = [_sys.executable, str(checker), str(copy), "--strict"] + list(extra)
    log = workdir / ("replay_%s.log" % snapshot.name)
    started = time.time()
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT,
                            check=False).returncode
    elapsed = time.time() - started

    if not stale.is_file():
        raise Refusal(
            "the replay wrote no %s (checker rc=%d after %.1f s; log %s) — there "
            "is no table to read and none will be invented"
            % (AUDIT_REL, rc, elapsed, log))
    try:
        doc = json.loads(stale.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        raise Refusal("the replay's %s is unparsable: %s" % (AUDIT_REL, exc))

    table = _svt.table_from_audit(doc, source=str(stale))
    return {"argv": argv, "rc": rc, "elapsed_s": round(elapsed, 3),
            "log": str(log), "copy": str(copy), "table": table}


def tree_identity(tree: Path) -> Dict[str, Any]:
    """WHICH tree did the judging. A baseline that cannot say what produced it
    answers "did the judgement change?" with "changed from what?"."""
    try:
        out = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=False)
        head = out.stdout.strip() if out.returncode == 0 else None
    except Exception:                                       # pragma: no cover
        head = None
    return {"tree": str(tree), "head": head}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", type=Path,
                    help="a FROZEN project snapshot. Never modified.")
    ap.add_argument("--tree", required=True, type=Path,
                    help="the checkout whose programs do the judging")
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--reference", type=Path, default=None,
                    help="the table to diff against — a prior audit_replay.json "
                         "or any completion audit. Omitted: the snapshot's own "
                         "recorded table, which is comparable only when the "
                         "run's LAST compliance pass was the same invocation.")
    ap.add_argument("--checker-arg", action="append", default=[],
                    help="forwarded verbatim to flow_compliance_check "
                         "(repeatable). Changing it changes the SCOPE, so two "
                         "tables produced with different values are refused as "
                         "NOT_COMPARABLE rather than diffed.")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="RECORD the replayed table at this path as the "
                         "snapshot's reference, stamped with the tree sha and "
                         "argv that produced it, and exit 0. Later runs pass "
                         "the same path as --reference. REFUSES to overwrite an "
                         "existing file without --force-baseline: a reference "
                         "that can be silently re-cut is a reference that makes "
                         "red diffs disappear.")
    ap.add_argument("--force-baseline", action="store_true",
                    help="overwrite an existing --baseline file. State WHY in "
                         "the handback: every diff taken against the old one "
                         "becomes unreproducible.")
    ap.add_argument("--json", dest="json_out", type=Path, default=None)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # BOTH ARE REQUIRED AND THEY ARE NOT ALTERNATIVES: the snapshot is what is
    # judged, --tree is the checkout whose programs judge it. The both-given
    # case is therefore decided here, not refused -- except where the two
    # names COLLIDE. A --tree that is the snapshot, or contains it, or sits
    # inside it, would have the snapshot re-judged by its own frozen programs
    # (or the judge read from the thing being judged), and the replay would
    # report a verdict about a comparison that never happened.
    if args.snapshot and args.tree:
        _snap, _judge = args.snapshot.resolve(), args.tree.resolve()
        if _snap == _judge or _judge in _snap.parents or _snap in _judge.parents:
            print("AUDIT REPLAY REFUSED: the snapshot (%s) and --tree (%s) "
                  "name the same or nested directories; the judging checkout "
                  "must be separate from the snapshot it judges"
                  % (args.snapshot, args.tree), file=_sys.stderr)
            return 2
    report: Dict[str, Any] = {
        "schema_version": 1,
        "program": "audit_replay",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": str(args.snapshot),
        "tree": str(args.tree),
        "verdict": None,
        "refusal": None,
        "table": None,
        "diff": None,
    }
    try:
        if not args.snapshot.is_dir():
            raise Refusal("no such snapshot: %s" % args.snapshot)
        recorded = read_recorded(args.snapshot)
        report["snapshot_recorded"] = None if recorded is None else {
            k: recorded[k] for k in
            ("source", "verdict", "verdict_refusal_reason", "version", "run_at",
             "command_argv", "step_count")}

        args.workdir.mkdir(parents=True, exist_ok=True)
        rep = replay(args.snapshot, args.tree, args.workdir,
                     list(args.checker_arg))
        cur = rep.pop("table")
        report["replay"] = rep
        report["table"] = cur

        if args.baseline is not None:
            # RECORD, then stop. Nothing is diffed and nothing is graded: the
            # table below is what the CURRENT tree actually produced on this
            # frozen subject, and a later run diffs against it. See the
            # `--baseline` section of the module docstring for why this is not
            # a `--write-baseline`.
            if args.baseline.exists() and not args.force_baseline:
                raise Refusal(
                    "%s already exists — a reference that can be silently "
                    "re-cut is a reference that makes red diffs disappear. Pass "
                    "--force-baseline and say WHY in the handback, or diff "
                    "against it with --reference." % args.baseline)
            record = {
                "schema_version": 1,
                "program": "audit_replay --baseline",
                "generated_at": report["generated_at"],
                "snapshot": str(args.snapshot),
                "recorded_by_tree": tree_identity(args.tree),
                "replay_argv": rep["argv"],
                "snapshot_recorded": report["snapshot_recorded"],
                "table": cur,
            }
            args.baseline.parent.mkdir(parents=True, exist_ok=True)
            args.baseline.write_text(
                json.dumps(record, indent=2), encoding="utf-8")
            report["verdict"] = "BASELINE_RECORDED"
            report["baseline"] = str(args.baseline)
            report["recorded_by_tree"] = record["recorded_by_tree"]
            print(_svt.render(cur))
            print("")
            print("BASELINE RECORDED: %s — %d step(s) from %s at tree %s. "
                  "Diff later runs against it with --reference %s"
                  % (args.baseline, cur.get("step_count"), args.snapshot.name,
                     (record["recorded_by_tree"].get("head") or "<unknown>")[:9],
                     args.baseline))
            if args.json_out:
                args.json_out.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(args.json_out, report, indent=2)
                print("wrote %s" % args.json_out)
            return 0

        if args.reference is not None:
            ref = _svt.load_table(args.reference)
            report["reference_selection"] = {"path": str(args.reference),
                                             "selected_by": "--reference"}
        elif recorded is not None:
            ref = recorded
            report["reference_selection"] = {
                "path": recorded.get("source"),
                "selected_by": "the snapshot's own recorded completion audit"}
        else:
            raise Refusal(
                "the snapshot carries no %s and no --reference was given — "
                "there is nothing to diff against, and a gate with no reference "
                "must refuse rather than pass" % AUDIT_REL)

        diff = _svt.diff_tables(ref, cur)
        report["diff"] = diff
        print(_svt.render(cur, diff))

        if not diff["comparable"]:
            raise Refusal("NOT_COMPARABLE: %s" % diff["comparability_reason"])
        report["verdict"] = "REGRESSION" if diff["regressed"] else "NO_REGRESSION"
        rc = 1 if diff["regressed"] else 0
        print("")
        print("AUDIT REPLAY %s: snapshot=%s regressions=%d improvements=%d "
              "laterals=%d elapsed=%ss"
              % (report["verdict"], args.snapshot.name,
                 len(diff["regressions"]), len(diff["improvements"]),
                 len(diff["laterals"]), rep["elapsed_s"]))
    except (Refusal, _svt.TableUnreadable) as exc:
        report["verdict"] = REFUSED
        report["refusal"] = str(exc)
        print("AUDIT REPLAY REFUSED: %s" % exc)
        rc = 2

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(args.json_out, report, indent=2, sort_keys=False)
        print("wrote %s" % args.json_out)
    return rc


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
