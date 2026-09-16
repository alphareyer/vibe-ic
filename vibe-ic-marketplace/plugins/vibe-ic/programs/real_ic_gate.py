#!/usr/bin/env python3
"""real_ic_gate.py — run ONE real IC end-to-end through the plugin's own front
door on a candidate tree, and say which step verdicts moved.

CHIP_AGNOSTIC: strict-logic — the LOGIC below names no IC, PDK, foundry,
vendor or SKU; the subject is whatever `--ic` and `--pdk` name at the call site.
The docstring names the ICs this was MEASURED on, because a rule with no
measurement behind it is the thing this repo keeps deleting.

WHY THIS EXISTS (OWNER RULING R-0915-86 (1), 2026-09-16)
========================================================
MEASURED on this tree on 2026-09-16: 3,713 test files, 173 of which invoke a
real EDA tool, and NOT ONE runs an IC end-to-end. The flow-matrix suite checks
that steps have gates WIRED — 45 of 61 cells in one of its dimensions are
stand-ins. So the suite can be entirely green while the flow it describes is
broken on a real design, and every rule bug found that day surfaced only when a
REAL run reached a later step:

    the r26 `stage_on_pass_review` cascade  — one declined review declined
                                              every later stage
    run16's INCONCLUSIVE equivalence record — booked as SKIP
    the DRV census with no parasitics in STA — reported 0 and read as clean

None of those is reachable from a fixture. Each needed an IC to actually get
that far. This program is the landing-side answer: SPM (the cheapest full-flow
PASS_WITH_WAIVERS in the corpus) goes through the FRONT DOOR on every candidate
tree, and its per-step verdict table is diffed against the published cell's.

NO WRAPPER, NO SECOND HARNESS (R-0915-85, "不要設計的疊床架屋")
==============================================================
This program does not re-implement any part of the flow. It:

  1. stages the design INPUT and nothing else,
  2. invokes `vibe_ic_one_shot_runner.py` — the plugin's one canonical front
     door — exactly as a lane does,
  3. reads the table the run's OWN `flow_compliance_check` wrote, and
  4. diffs it with `_step_verdict_table`, the module `audit_replay` shares.

There is no second runner, no hand-copied phase artefact and no verdict this
file computes for itself. Every word in the output table was written by the
run's own audit.

§4.05 — WHAT IS STAGED, AND THE GUARD THAT PROVES IT
====================================================
ONLY `ic/<ic>/input/` from the benchmark-data corpus, ALL of it (the docs, the
`step_0_5ic_answers.json` operator answers, `constraints/` where the dataset
ships them). Never `golden/`, `reference/`, `oracle/`, a published cell, or any
prior run's outputs. That is not a comment: `_assert_input_only` walks the staged
tree before the runner starts and REFUSES on any path segment naming an oracle,
and `_assert_empty_project` refuses a project directory that already holds run
output, because a table from a tree that was pre-seeded says nothing about the
candidate.

The published cell IS read — but only in THIS process, only after the run has
finished, and only as the reference TABLE. It is never placed inside the project
directory, so no step of the run can see it.

`input/submission_template/` is pre-created WRITABLE (R-0915-4): flow step 0.5ic
WRITES it, and a lane that froze all of `input/` read-only was producing a
staging error and reading it as a finding.

THE REFERENCE MUST BE A RUN OF THE SAME KIND (R-0915-88, MEASURED)
==================================================================
This gate runs the front door with NO AGENT ATTACHED. The flow is program-first
+ AI-BACKUP, and several steps complete only when an agent answers a hand-off:
`flow_compliance_check`'s own `_AWAITING_EXIT_CODE` comment says it — *"pass one
completed and pass two is somebody else's move … a PROGRAM cannot spawn the
subagent pass two needs."*

MEASURED on SPM, the published cell `ic/spm/v1.21.6_gf180mcuD` (produced by an
agent-driven lane run) against a program-only run of the SAME input:

    D1  phase1_expert_parse_track --check-report    cell rc 0 PASS / program-only rc 4 INCOMPLETE
    5   formal_proof_evidence_check                 cell PASS      / program-only FAIL

at SIX trees spanning `845ef5247..a8d39cb70` and current main — the TREE never
moved either verdict. The cell's own artefacts say why: `expert_parse_track.json`
records `ai_subtrack.status=CONSUMED, observed_ai_consumed=6`, and
`phase2/stage1/formal/formal_expert_review.json` records
`invocation_status=INVOKED, invoked_by="lane icspm3 (formal-verify expert role)"`
beside 5 hand-authored properties, where a program-only run authors 1 and then
requests the fallback it has nobody to call.

So a published cell is the WRONG reference for this gate, and on its first real
run that mistake produced a headline saying SPM had regressed when nothing had
landed. `_step_verdict_table.comparability` now REFUSES across run shapes and
names the cause; the reference to pass with `--reference` is a PRIOR RUN OF THIS
GATE. That is the same rule `audit_replay` already states for a snapshot's own
recorded table, arrived at from the other direction.

THE REFUSAL RULE — AND WHERE IT DEPARTS FROM THE BRIEF, DELIBERATELY
===================================================================
The dispatch brief said "REFUSE when the run did not complete (rc != 0 ...)".
Taken literally that is wrong in the one case the gate exists for: the runner
exits NON-ZERO exactly when the IC FAILs, and that run DID complete and DID
write the table naming which step went red. Refusing there would throw away the
only thing the gate is for — WHICH step regressed — and leave a bare "refused".

So the rule implemented here is FRESHNESS OF THE ARTEFACT, not the exit code:

    the table is read iff the completion audit exists AND was written by THIS
    run (its `run_at` is at or after the moment the runner was launched, and
    its `command_argv` names this project root).

An audit that is absent, or older than the run, or about a different project, is
a REFUSAL — `verdict: REFUSED`, one line saying why, rc 2, and NO table. The
runner's rc is always RECORDED. This is strictly stronger than the brief's rule:
"rc == 0" would happily read a stale audit left by a previous run in the same
directory, which is precisely the escape `flow_compliance_check`'s own #1001
docstring records (a published cell carrying a wrong-root audit byte-comparably).

EXIT CODES
==========
  0  the run completed and, against the reference, NO step regressed and the
     top-level verdict did not fall. IMPROVEMENTS are listed and exit 0 — a
     landing that makes SPM better must not be blocked by the gate that watches
     SPM. A top-level IMPROVEMENT additionally sets `reference_table_stale`, so
     the dispatcher knows to re-pin the reference rather than discover the new
     tier as a "regression" next time.
  1  a REGRESSION: some step's verdict fell (PASS -> anything, or any rank
     decrease, or a step the reference measured and this table does not), or
     the top-level verdict fell.
  2  a REFUSAL — nothing is claimed. The run did not produce its own table, the
     image/container could not be resolved, the input was missing, the project
     directory was not empty, or the reference is NOT COMPARABLE to this run —
     a different producer, different scoping flags, or a DIFFERENT RUN SHAPE
     (see `_step_verdict_table`). A refusal is not a pass and not a regression.

TIME IS RECORDED, NEVER ENFORCED (owner's standing rule; R-0915-5). There is no
`timeout`, no kill and no deadline anywhere in this file. `--budget-s` only
decides whether `over_budget: true` appears in the report.

chip-AGNOSTIC (declared `strict-logic` at the top): no IC, PDK, foundry, vendor
or SKU literal appears in the LOGIC. The subject is whatever `--ic` and `--pdk`
name at the call site. The docstring above names SPM and subservient because
they are the runs this was measured on, and a rule with no measurement behind it
is exactly what this repo keeps having to delete.
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
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# vibe-ic#1082: a declared report destination is written through the
# atomic writer, so a crash mid-write leaves the previous report or none,
# never a truncated JSON a later reader parses as a verdict.
from _atomic_artefact import write_json as _atomic_write_json  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple

import _step_verdict_table as _svt

#: Path segments that may never appear in a staged design input. §4.05: the run
#: reads the design INPUT and never the answer. Matched on whole path segments
#: so a legitimate name that merely CONTAINS one of these words is not refused.
ORACLE_SEGMENTS = {"golden", "oracle", "reference", "reference_flow", "answers",
                   "expected", "solution"}

#: ...with one exception the corpus itself defines. `step_0_5ic_answers.json` is
#: the OPERATOR's answers about slot geometry and deliverable class — an input
#: the dataset ships for every IC and which R-0915-3 names explicitly as
#: "stage the FULL input directory, never a 7-doc subset". It is an input, not
#: an oracle, and it is matched as a whole filename so nothing else rides in.
ORACLE_SEGMENT_EXEMPT_FILENAMES = {"step_0_5ic_answers.json"}

#: The canonical relative path of the table this gate reads. Stated once.
AUDIT_REL = Path("reports") / "audit" / "phase23_completion_audit.json"
ORCHESTRATOR_REL = Path("reports") / "orchestrator" / "vibe_ic_one_shot.json"

REFUSED = "REFUSED"


class Refusal(Exception):
    """Nothing is claimed. Carries the ONE line that says why."""


# ── staging ──────────────────────────────────────────────────────────────────

def _oracle_offenders(root: Path) -> List[str]:
    bad: List[str] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and p.name in ORACLE_SEGMENT_EXEMPT_FILENAMES:
            continue
        for seg in rel.parts:
            if seg.lower().strip("._-") in ORACLE_SEGMENTS:
                bad.append(str(rel))
                break
    return bad


def _assert_empty_project(project: Path) -> None:
    """A gate that ran on a pre-seeded tree measures the seed, not the tree."""
    if not project.exists():
        return
    leftovers = sorted(c.name for c in project.iterdir()
                       if c.name not in {"input", ".vibeic-state"})
    if leftovers:
        raise Refusal(
            "project directory %s is not empty — it already holds %s; a verdict "
            "table from a pre-seeded tree is about the seed, not the candidate"
            % (project, ", ".join(leftovers[:8])))


def stage_input(corpus: Path, ic: str, project: Path) -> Dict[str, Any]:
    """Copy `ic/<ic>/input/` -- ALL of it and ONLY it -- into `<project>/input/`.

    Returns the staging record: what was copied, from where, and the file list,
    so the report can be read without trusting this docstring.
    """
    src = corpus / "ic" / ic / "input"
    if not src.is_dir():
        raise Refusal("no design input at %s — benchmark-data ships no "
                      "`ic/%s/input/`" % (src, ic))
    files = [p for p in sorted(src.rglob("*")) if p.is_file()]
    if not files:
        raise Refusal("design input %s is empty" % src)

    _assert_empty_project(project)
    dst = project / "input"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)

    offenders = _oracle_offenders(dst)
    if offenders:
        raise Refusal(
            "§4.05: the staged input carries %d path(s) naming an oracle/golden/"
            "reference: %s" % (len(offenders), ", ".join(offenders[:6])))

    # R-0915-4: flow step 0.5ic WRITES here. Pre-create it writable; freezing
    # all of input/ is a staging error, not a finding.
    (dst / "submission_template").mkdir(parents=True, exist_ok=True)

    return {
        "source": str(src),
        "destination": str(dst),
        "file_count": len(files),
        "files": [str(p.relative_to(src)) for p in files],
        "submission_template_precreated": True,
    }


# ── the reference cell ───────────────────────────────────────────────────────

_VERSION_RE = re.compile(r"^v(\d+(?:\.\d+)*)_(.+)$")


def _version_key(name: str) -> Tuple[int, ...]:
    m = _VERSION_RE.match(name)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def resolve_reference(corpus: Path, ic: str, pdk: str,
                      explicit: Optional[Path]) -> Dict[str, Any]:
    """Which published table this run is judged against — and the alternatives.

    With `--reference` the caller decides and the choice is recorded verbatim.
    Without it, the published cells for THIS ic and THIS pdk are enumerated and
    the highest plugin VERSION wins. The rejected candidates are listed in the
    report: a reference chosen by a rule nobody can see is the "two documents
    are called contract" defect, and a silent pick of the wrong cell would make
    every number downstream meaningless.
    """
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise Refusal("--reference %s does not exist" % p)
        return {"path": str(p), "selected_by": "--reference",
                "candidates": [str(p)]}
    icdir = corpus / "ic" / ic
    if not icdir.is_dir():
        raise Refusal("no published cells for %s under %s" % (ic, corpus))
    cands = [d for d in sorted(icdir.iterdir())
             if d.is_dir() and _VERSION_RE.match(d.name)
             and _VERSION_RE.match(d.name).group(2) == pdk
             and (d / AUDIT_REL).is_file()]
    if not cands:
        raise Refusal(
            "no published cell for ic=%s pdk=%s carries %s under %s — there is "
            "nothing to diff against, and a gate with no reference must refuse "
            "rather than pass" % (ic, pdk, AUDIT_REL, icdir))
    best = max(cands, key=lambda d: _version_key(d.name))
    return {
        "path": str(best / AUDIT_REL),
        "cell": best.name,
        "selected_by": "highest published plugin version for this ic+pdk",
        "candidates": [d.name for d in cands],
        "rejected": [d.name for d in cands if d is not best],
    }


# ── the run ──────────────────────────────────────────────────────────────────

def _container_image(container: str) -> Dict[str, Any]:
    """The image the named container is actually running. Recorded on every run
    — numbers from two toolchains are not comparable."""
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", container],
            capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise Refusal("docker is not on PATH — the pinned image cannot be "
                      "resolved and no run may claim to have used it")
    if out.returncode != 0:
        raise Refusal("container %r does not exist (docker inspect rc=%d: %s)"
                      % (container, out.returncode, out.stderr.strip()[:200]))
    return {"container": container, "image": out.stdout.strip()}


def run_front_door(runner: Path, project: Path, pdk: str, ic: str,
                   container: Optional[str], require_image: Optional[str],
                   extra: List[str], log: Path) -> Dict[str, Any]:
    """Invoke the plugin's ONE canonical front door. No timeout, no kill."""
    argv = [_sys.executable, str(runner), str(project),
            "--pdk", pdk, "--ic-name", ic, "--no-dashboard"]
    if container:
        argv += ["--container", container]
    if require_image:
        argv += ["--require-image", require_image]
    argv += list(extra)

    started = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT,
                            check=False).returncode
    elapsed = time.time() - started
    return {"argv": argv, "rc": rc, "elapsed_s": round(elapsed, 3),
            "started_at": started_iso,
            "started_monotonic_epoch": started,
            "log": str(log)}


def _parse_iso(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def read_own_table(project: Path, run: Dict[str, Any]) -> Dict[str, Any]:
    """The table THIS run wrote, or a refusal. See the module docstring: the
    test is freshness and ownership, not the runner's exit code."""
    audit = project / AUDIT_REL
    if not audit.is_file():
        raise Refusal(
            "the run wrote no %s (runner rc=%s after %s s) — there is no table "
            "to read and none will be invented"
            % (AUDIT_REL, run.get("rc"), run.get("elapsed_s")))
    try:
        doc = json.loads(audit.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        raise Refusal("the run's %s is unparsable: %s" % (AUDIT_REL, exc))

    run_at = _parse_iso(doc.get("run_at"))
    # One second of slack: `run_at` is written by the audit with whole-second
    # ISO precision in some producers, and a same-second write must not read as
    # "older than the run".
    if run_at is not None and run_at < run["started_monotonic_epoch"] - 1.0:
        raise Refusal(
            "the %s in this project predates the run (audit run_at=%s, run "
            "started %s) — it is a leftover, and a leftover table says nothing "
            "about this candidate"
            % (AUDIT_REL, doc.get("run_at"), run["started_at"]))
    argv = doc.get("command_argv") or []
    if argv and not any(str(project.resolve()) in str(a) or str(project) in str(a)
                        for a in argv):
        raise Refusal(
            "the %s in this project was written about a DIFFERENT project root "
            "(its own argv: %s) — vibe-ic#1001's wrong-root audit, exactly"
            % (AUDIT_REL, " ".join(str(a) for a in argv)))
    return _svt.table_from_audit(doc, source=str(audit))


def read_orchestrator(project: Path) -> Dict[str, Any]:
    """The runner's own verdict + halted_at, verbatim. RECORDED beside the table
    and never used to override it: they are two artefacts and the gate's job is
    to show both, not to reconcile them."""
    p = project / ORCHESTRATOR_REL
    if not p.is_file():
        return {"present": False, "path": str(p)}
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"present": True, "path": str(p), "unparsable": str(exc)}
    return {"present": True, "path": str(p), "verdict": d.get("verdict"),
            "halted_at": d.get("halted_at"), "duration_s": d.get("duration_s"),
            "phases": d.get("phases"), "container_image": d.get("container_image")}


# ── main ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", required=True, type=Path,
                    help="the CANDIDATE checkout whose programs/ run the IC")
    ap.add_argument("--ic", required=True,
                    help="the benchmark-data cell name, e.g. the IC directory "
                         "under ic/")
    ap.add_argument("--pdk", required=True)
    ap.add_argument("--benchmark-data", required=True, type=Path,
                    help="a clone of the benchmark-data corpus")
    ap.add_argument("--workdir", required=True, type=Path,
                    help="where the project tree is staged and run")
    ap.add_argument("--reference", type=Path, default=None,
                    help="the reference table (a published cell's completion "
                         "audit, or a prior real_ic_gate.json). Omitted: the "
                         "highest published version for this ic+pdk.")
    ap.add_argument("--container", default=_os.environ.get("EDA_CONTAINER"))
    ap.add_argument("--require-image", default=None)
    ap.add_argument("--budget-s", type=float, default=None,
                    help="RECORDED, never enforced: sets `over_budget` in the "
                         "report. Nothing here kills a run.")
    ap.add_argument("--runner-arg", action="append", default=[],
                    help="forwarded verbatim to the front door (repeatable)")
    ap.add_argument("--json", dest="json_out", type=Path, default=None)
    ap.add_argument("--reuse-run", action="store_true",
                    help="do not stage or run; read the table already in "
                         "--workdir. For re-judging a run this gate made.")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report: Dict[str, Any] = {
        "schema_version": 1,
        "program": "real_ic_gate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ic": args.ic,
        "pdk": args.pdk,
        "tree": str(args.tree),
        "benchmark_data": str(args.benchmark_data),
        "workdir": str(args.workdir),
        "verdict": None,
        "refusal": None,
        "table": None,
        "diff": None,
    }
    project = args.workdir / args.ic

    try:
        runner = (args.tree / "vibe-ic-marketplace" / "plugins" / "vibe-ic"
                  / "programs" / "vibe_ic_one_shot_runner.py")
        if not runner.is_file():
            raise Refusal("the candidate tree has no front door at %s" % runner)
        report["runner"] = str(runner)

        report["reference_selection"] = resolve_reference(
            args.benchmark_data, args.ic, args.pdk, args.reference)
        ref_table = _svt.load_table(Path(report["reference_selection"]["path"]))
        report["reference_table"] = {
            k: ref_table[k] for k in
            ("source", "verdict", "verdict_refusal_reason", "version", "run_at",
             "command_argv", "step_count")}

        if args.reuse_run:
            report["staging"] = {"reused": True}
            report["run"] = {"reused": True, "rc": None, "elapsed_s": None,
                             "started_monotonic_epoch": 0.0,
                             "started_at": "reused"}
        else:
            report["staging"] = stage_input(args.benchmark_data, args.ic, project)
            if args.container:
                report["container"] = _container_image(args.container)
            report["run"] = run_front_door(
                runner, project, args.pdk, args.ic, args.container,
                args.require_image, list(args.runner_arg),
                args.workdir / ("%s_front_door.log" % args.ic))
            if args.budget_s:
                report["budget_s"] = args.budget_s
                report["over_budget"] = report["run"]["elapsed_s"] > args.budget_s

        cur_table = read_own_table(project, report["run"])
        report["table"] = cur_table
        report["orchestrator"] = read_orchestrator(project)
        # RECORDED BEFORE the comparability refusal, not after: the run shape is
        # the DISCLOSURE that explains a run-shape refusal, and a refusal whose
        # own evidence was dropped sends a reader looking for a defect.
        report["run_shape"] = cur_table.get("run_shape")
        report["reference_run_shape"] = ref_table.get("run_shape")

        diff = _svt.diff_tables(ref_table, cur_table)
        report["diff"] = diff

        if not diff["comparable"]:
            raise Refusal("NOT_COMPARABLE: %s" % diff["comparability_reason"])

        report["verdict"] = "REGRESSION" if diff["regressed"] else "NO_REGRESSION"
        report["reference_table_stale"] = (
            diff["verdict"]["direction"] == _svt.IMPROVEMENT)
        rc = 1 if diff["regressed"] else 0
        print(_svt.render(cur_table, diff))
        print("")
        print("REAL-IC GATE %s: ic=%s pdk=%s regressions=%d improvements=%d "
              "laterals=%d runner_rc=%s elapsed=%ss"
              % (report["verdict"], args.ic, args.pdk, len(diff["regressions"]),
                 len(diff["improvements"]), len(diff["laterals"]),
                 report["run"].get("rc"), report["run"].get("elapsed_s")))
        if report["reference_table_stale"]:
            print("REFERENCE TABLE STALE: the top-level verdict IMPROVED "
                  "(%s -> %s) — re-pin the reference cell."
                  % (diff["verdict"]["reference"], diff["verdict"]["current"]))
    except Refusal as exc:
        report["verdict"] = REFUSED
        report["refusal"] = str(exc)
        # NEVER a table from a run that did not finish: a refusal that still
        # carried a table would be read as a measurement by the next consumer.
        if report.get("diff") is None:
            report["table"] = None
        print("REAL-IC GATE REFUSED: %s" % exc)
        rc = 2

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(args.json_out, report, indent=2, sort_keys=False)
        print("wrote %s" % args.json_out)
    return rc


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
