#!/usr/bin/env python3
"""flow_declared_producer_run.py — run the producers the FLOW already declares,
so the RUN writes the documents it is supposed to write.

ENFORCEMENT: reporting (the step gates keep their own verdicts)
CHIP_AGNOSTIC: strict

WHY THIS EXISTS, MEASURED 2026-09-15 (lane icsub2) on `subservient` x
gf180mcuD, and the same tier is what `spm` is held on::

    step 26  MISSING
      SELF-CERTIFIED EVIDENCE EXCLUDED (audit_created)
        ['reports/phase3/antenna_signoff.json']
        absent when this audit began []
        recorded by an earlier pass of this audit as its own write
        ['reports/phase3/antenna_signoff.json']
      ... PRODUCER GAP: no pre-audit producer supplied these paths; wire them
      into the owning runner before claiming this step complete.

The audit prints that remedy on ELEVEN steps. It is right, and for a
well-defined subset the flow has ALREADY NAMED the producer: the step lists a
program under `programs:`, its own gate clause invokes that same program with
`--json <path>`, and `<path>` is one of the step's `required_outputs`. The flow
says, in three places at once, that THE RUN produces that document. Nothing in
the run ran it, the AUDITOR's evaluation of the gate did, and the audit then
correctly refused its own output as evidence.

MEASURED over the shipped flow, that subset is exactly 13 clauses on 13 steps::

    2       crosslayer_rewrite_equivalence_check, rtl_hygiene_lint, rom_init_lint
    8       sdc_syntax_check           11      bsdl_emit
    15.5ic  pad_assignment_gen         26      antenna_report_check
    28      perc_signoff_check         29      post_layout_sim_check
    31      erc_density_check          36      tapeout_signoff_check
    38      foundry_handoff_package_check      M1  mixed_signal_merge_check

THIS IS NOT THE AUDITOR RUNNING ITS OWN GATES. The distinction is the whole
point of the tier being closed: a RUN executing the producers the flow lists
under its steps' `programs:` is what a flow does; an AUDITOR executing them and
then grading its own output is self-certification. So this program is invoked
by the RUNNER and refuses to run at all if it is handed the auditor's role --
there is no `--from-audit` mode, and `flow_compliance_check` does not import it.

IT ONLY FILLS A GAP; IT NEVER OVERWRITES THE RUN'S OWN WORK. A clause is
executed only when its target is ABSENT, or when the audit's own authorship
note claims the file -- i.e. the only thing that has ever written that path is
the auditor. A document the run produced is left exactly as the run left it.

A NON-ZERO rc IS RECORDED, NEVER SWALLOWED. These programs write their `--json`
document whatever they conclude, and the step's own gate re-runs them and
reaches its own verdict; this program's job is to make the document EXIST as
run evidence, not to decide anything. Its own exit code is 1 only when a
declared producer could not be executed at all -- a program that is missing is
a flow defect and says so.

chip-AGNOSTIC: everything is read from the flow yaml and the project tree.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 — declared flow programs, argv from the yaml
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _atomic_artefact import write_text as atomic_write_text  # vibe-ic#1082 (helper from PR #1094)

PROGRAM = "flow_declared_producer_run"
VERSION = "1.0.0"
PROGRAMS_DIR = Path(__file__).resolve().parent
FLOW_YAML = PROGRAMS_DIR.parent / "flow" / "phase1_phase2_phase3.yaml"
REPORT_REL = "reports/audit/flow_declared_producer_run.json"

#: The gate keys whose value is a shell-shaped command string.
_COMMAND_KEYS = ("program_exit_zero", "advisory_program_exit_zero",
                 "optional_program_exit_zero")
_JSON_RE = re.compile(r"--json[=\s]+(\S+)")


def _iter_steps(node: Any):
    if isinstance(node, dict):
        if "id" in node and ("required_outputs" in node or "gate" in node):
            yield node
        for v in node.values():
            yield from _iter_steps(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_steps(v)


def _iter_commands(node: Any, out: List[str]) -> List[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _COMMAND_KEYS:
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, dict) and isinstance(v.get("command"), str):
                    out.append(v["command"])
            else:
                _iter_commands(v, out)
    elif isinstance(node, list):
        for v in node:
            _iter_commands(v, out)
    return out


def declared_producer_clauses(flow_yaml: Path = FLOW_YAML
                              ) -> List[Dict[str, str]]:
    """Every clause the flow declares as a step's OWN producer of a declared
    output. `[{"step", "program", "command", "target"}]`, in flow order.

    THREE CONDITIONS, ALL FROM THE FLOW, none inferred:
      * the clause's program is listed under that step's `programs:`;
      * the clause names a `--json <path>`;
      * that path is one of the step's `required_outputs`.

    A clause meeting all three is the flow saying, in three places, that the
    RUN writes this document.
    """
    try:
        import yaml  # noqa: PLC0415 — optional at import time, required here
    except ImportError:
        return []
    try:
        doc = yaml.safe_load(flow_yaml.read_text(errors="replace"))
    except (OSError, ValueError):
        return []
    found: List[Dict[str, str]] = []
    for step in _iter_steps(doc):
        outs = set(step.get("required_outputs") or [])
        producers = {str(p).strip() for p in (step.get("programs") or [])
                     if isinstance(p, str) and str(p).strip()}
        if not outs or not producers:
            continue
        for cmd in _iter_commands(step.get("gate") or {}, []):
            parts = cmd.split()
            if not parts or parts[0] not in producers:
                continue
            m = _JSON_RE.search(cmd)
            if not m or m.group(1) not in outs:
                continue
            found.append({"step": str(step.get("id")), "program": parts[0],
                          "command": cmd, "target": m.group(1),
                          # The step's OTHER declared outputs — the evidence
                          # that the RUN performed this step at all. See
                          # `owed`.
                          "siblings": sorted(outs - {m.group(1)})})

    # ── THE SECOND DECLARATION CHANNEL, which is NOT a gate (vibe-ic#2277) ──
    #
    # A step may declare a producer WITHOUT putting it in the gate, and for
    # some producers it MUST: #1980 ruled that step 31's PERC and via findings
    # are ADVISORY EVIDENCE, read through `program_outputs`, with the refusal
    # predicate owned by a separate program -- so naming those producers in the
    # `gate:` promotes them into gate coverage and the enforcement register,
    # which `test_issue1980_advisory_evidence_tiers` refuses by name.
    #
    # That left a real gap rather than a settled question:
    # `reports/phase3/perc_sweep.json` is a step-31 `required_output` declared
    # with its producer under `program_outputs`, and NOTHING ran it. The run
    # produced 8 of 9 declared outputs and the ninth was owed by nobody.
    #
    # `producer_command` closes it on the channel that does not touch a gate.
    # The THREE conditions are the same three, read from the same yaml:
    #   * the entry's `program` is listed under that step's `programs:`;
    #   * the entry states the command that writes the document;
    #   * the entry's `path` is one of the step's `required_outputs`.
    # The audit still executes no gate -- `_collect_program_output_records`
    # reads these entries "WITHOUT EXECUTING A GATE" and is untouched. Only
    # THIS program, which the phase-3 runner invokes, executes them, and only
    # when the document is absent.
    #
    # An entry with no `producer_command` is NOT adopted: silence is not a
    # dispatch instruction, and guessing a producer's argv is how a declared
    # output gets written by something nobody asked for.
    for step in _iter_steps(doc):
        outs = set(step.get("required_outputs") or [])
        producers = {str(p).strip() for p in (step.get("programs") or [])
                     if isinstance(p, str) and str(p).strip()}
        if not outs or not producers:
            continue
        for spec in step.get("program_outputs") or []:
            if not isinstance(spec, dict):
                continue
            program = str(spec.get("program") or "").strip()
            target = str(spec.get("path") or "").strip()
            cmd = spec.get("producer_command")
            if (not program or program not in producers
                    or target not in outs
                    or not isinstance(cmd, str) or not cmd.strip()):
                continue
            cmd = cmd.strip()
            if cmd.split()[0] != program:
                # The command must be the program the entry names. A row that
                # declares one producer and runs another is a mis-declaration,
                # not a dispatch.
                continue
            if any(f["step"] == str(step.get("id")) and f["target"] == target
                   for f in found):
                continue
            found.append({"step": str(step.get("id")), "program": program,
                          "command": cmd, "target": target,
                          "siblings": sorted(outs - {target})})
    return found


def _glob_exists(project: Path, spec: str) -> bool:
    """Does any file match this declared-output spec?

    A spec may be a plain path, a glob, or `A OR B` — the three shapes the
    flow's `required_outputs` uses. The reader is deliberately permissive:
    the question is only "did the run leave evidence of this step", and a
    false NO leaves a gap open rather than manufacturing work.
    """
    for alt in (a.strip() for a in str(spec).split(" OR ")):
        if not alt:
            continue
        if any(ch in alt for ch in "*?["):
            try:
                if next(iter(project.glob(alt)), None) is not None:
                    return True
            except (OSError, ValueError, IndexError):
                continue
        elif (project / alt).exists():
            return True
    return False


def _audit_claims(project: Path, sid: str, rel: str) -> bool:
    """Has the AUDIT recorded this exact file as its own write?

    Delegated to `flow_compliance_check`'s own note reader so there is ONE
    definition of what that record is and what makes it stale. Absent module,
    absent note, or a note whose (size, mtime) no longer matches the live file
    all answer False -- the conservative direction, which leaves the run's own
    artefact alone.
    """
    try:
        import flow_compliance_check as _fcc  # noqa: PLC0415
        return bool(_fcc._prior_audit_created(project, sid, [rel]))
    except Exception:  # noqa: BLE001 — a sibling that cannot be read is named
        return False


def owed(project: Path, clauses: List[Dict[str, str]]
         ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """`(to_run, skipped)` — the clauses whose document the RUN has not made.

    A clause is OWED when its target is absent, or when the only thing that
    has ever written it is the auditor. Anything else is the run's own work and
    is left byte-for-byte alone.
    """
    to_run, skipped = [], []
    for c in clauses:
        target = project / c["target"]
        # DID THE RUN PERFORM THIS STEP AT ALL? Only then is its verdict
        # document owed.
        #
        # MEASURED, and this clause exists because the first version did not
        # have it. Without it the pass ran `pad_assignment_gen` for step
        # 15.5ic on a HARDMACRO delivery — a step whose own predicate
        # (`_chip_path_requests_pad_ring`, vibe-ic#2246) says NO for exactly
        # this delivery — and `post_layout_sim_check` and
        # `mixed_signal_merge_check` for steps this design never ran. The
        # result was three MISSING steps turned into three FAILs and two new
        # failed gates: the flow was made to manufacture work for steps the
        # delivery does not have, which is the opposite of closing a producer
        # gap. A step the run DID perform has left at least one of its other
        # declared outputs on disk; a step it correctly skipped has left none.
        #
        # A clause whose step declares NO other output cannot be judged this
        # way and is left alone — the conservative direction, since the only
        # cost is a gap that stays open and is still reported by the audit.
        sibs = [s for s in (c.get("siblings") or []) if s]
        ran = any(_glob_exists(project, s) for s in sibs)
        if not ran:
            row = dict(c)
            row["why"] = (
                "the run did not perform this step: none of its other declared "
                f"output(s) {sibs or '[]'} is on disk, so its verdict document "
                "is not owed")
            skipped.append(row)
            continue
        if not target.exists():
            row = dict(c); row["why"] = "target absent"
            to_run.append(row)
        elif _audit_claims(project, c["step"], c["target"]):
            row = dict(c)
            row["why"] = ("present, but the audit's own authorship note claims "
                          "it: the only writer so far is the auditor")
            to_run.append(row)
        else:
            row = dict(c); row["why"] = "the run already produced it"
            skipped.append(row)
    return to_run, skipped


def _run_one(project: Path, row: Dict[str, str], timeout: int
             ) -> Dict[str, Any]:
    prog = PROGRAMS_DIR / f"{row['program']}.py"
    out = dict(row)
    if not prog.is_file():
        out.update({"rc": None, "executed": False,
                    "note": f"{row['program']}.py is not in programs/"})
        return out
    argv = [sys.executable, str(prog)] + row["command"].split()[1:]
    t0 = time.time()
    try:
        cp = subprocess.run(  # nosec B603 — argv from the flow yaml
            argv, cwd=str(project), capture_output=True, text=True,
            timeout=timeout)
        rc = cp.returncode
        tail = (cp.stderr or cp.stdout or "").strip()[-300:]
    except subprocess.TimeoutExpired:
        out.update({"rc": None, "executed": False,
                    "note": f"did not finish in {timeout}s"})
        return out
    except OSError as exc:
        out.update({"rc": None, "executed": False, "note": str(exc)})
        return out
    out.update({"rc": rc, "executed": True,
                "elapsed_s": round(time.time() - t0, 2),
                "target_exists_after": (project / row["target"]).exists(),
                "tail": tail})
    return out


def run(project: Path, timeout: int = 900) -> Dict[str, Any]:
    clauses = declared_producer_clauses()
    to_run, skipped = owed(project, clauses)
    results = [_run_one(project, row, timeout) for row in to_run]
    unexecutable = [r for r in results if not r.get("executed")]
    produced = [r for r in results if r.get("target_exists_after")]
    # TWO DIFFERENT FACTS WERE COUNTED AS ONE (vibe-ic#619 / RC13). `skipped`
    # holds both "the run already produced it" and "the run did not perform this
    # step", and `already_produced_by_the_run` was `len(skipped)` -- so a project
    # where NOTHING ran reported every clause as already produced. MEASURED on
    # an empty directory at f19703dfa: "29 declared producer clause(s); 29
    # already produced by the run, 0 owed, 0 executed", exit 0. Every word of
    # that sentence except the first number was false.
    not_performed = [r for r in skipped
                     if str(r.get("why", "")).startswith(
                         "the run did not perform this step")]
    produced_already = [r for r in skipped if r not in not_performed]
    # THE POPULATION this census measures is the clauses whose step the run
    # actually PERFORMED. A clause skipped because its step never ran was not
    # judged; counting it as an answer is what made an empty subject look like a
    # finished flow.
    population = len(to_run) + len(produced_already)
    return {
        "program": PROGRAM, "version": VERSION,
        "declared_clauses": len(clauses),
        "owed": len(to_run),
        "already_produced_by_the_run": len(produced_already),
        "steps_the_run_did_not_perform": len(not_performed),
        "population": population,
        "executed": len(results) - len(unexecutable),
        "documents_now_present": len(produced),
        "unexecutable": unexecutable,
        "results": results, "skipped": skipped,
        "verdict": ("ZERO_POPULATION" if population == 0
                    else "INCOMPLETE" if unexecutable else "PRODUCED"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project_dir")
    ap.add_argument("--json", default="")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--list", action="store_true",
                    help="print the declared clauses and exit without running")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    project = Path(args.project_dir).resolve()
    if args.list:
        for c in declared_producer_clauses():
            print(f"{c['step']:8} {c['program']:36} -> {c['target']}")
        return 0
    if not project.is_dir():
        print(f"[CANNOT CHECK] {PROGRAM} — {project} is not a directory")
        return 2
    rec = run(project, args.timeout)
    target = Path(args.json) if args.json else project / REPORT_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    # vibe-ic#1082: the final name must appear only once the write completed --
    # this report IS a declared destination (REPORT_REL), and `required_outputs`
    # credits a step for the name existing, not for the file being whole.
    # `write_text` rather than `write_json` ON PURPOSE: `rec` carries `tail`,
    # the last 300 chars of a producer's own stderr/stdout (see `run` below),
    # and this plugin's producers quote zh-Hant spec lines. `write_json` passes
    # ensure_ascii=False, so converting through it would silently change the
    # bytes of every report that has non-ASCII in a tail. _atomic_artefact's own
    # rule is that a conversion does not alter what gets written.
    atomic_write_text(target, json.dumps(rec, indent=2) + "\n")
    # THE REFUSAL COMES BEFORE THE FINDINGS BRANCHES, and that placement is the
    # point: a refusal under `if rc == 0` is switched off by any finding,
    # including one read from the flow yaml, which says nothing about this tree.
    # The report is written FIRST (above) because a producer that writes nothing
    # when it declines cannot be told from one that never ran.
    if rec["population"] == 0:
        print(f"[ZERO_POPULATION] {PROGRAM} — none of the "
              f"{rec['declared_clauses']} declared producer clause(s) could be "
              f"judged: for every one of them the run left no other declared "
              f"output on disk, so no step is known to have been performed and "
              f"this census measured NOTHING about {project}. Not a pass: a "
              f"verdict over an empty population is the one red a census has.",
              file=sys.stderr)
        return 2
    print(f"[{rec['verdict']}] {PROGRAM} — {rec['declared_clauses']} declared "
          f"producer clause(s); {rec['population']} judged "
          f"({rec['already_produced_by_the_run']} already produced by the run, "
          f"{rec['owed']} owed, {rec['executed']} executed, "
          f"{rec['documents_now_present']} document(s) now present); "
          f"{rec['steps_the_run_did_not_perform']} clause(s) NOT judged because "
          f"the run did not perform their step")
    for r in rec["results"]:
        print(f"  step {r['step']:8} {r['program']:34} rc="
              f"{r.get('rc')} ({r.get('why')})")
    for r in rec["unexecutable"]:
        print(f"  NOT EXECUTABLE: step {r['step']} {r['program']}: "
              f"{r.get('note')}", file=sys.stderr)
    return 1 if rec["unexecutable"] else 0


if __name__ == "__main__":
    sys.exit(main())
