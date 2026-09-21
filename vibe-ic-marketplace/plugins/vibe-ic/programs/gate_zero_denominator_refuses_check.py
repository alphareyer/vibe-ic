#!/usr/bin/env python3
"""gate_zero_denominator_refuses_check.py — a gate that read NOTHING must not exit 0.

WHY (vibe-ic#564)
=================
Three gates were found by hand, each returning a verdict about a design it had
not read:

    interface_encoding_audit   "0 interfaces analyzed"            rc 0
    fpga_qsf_lint              "ERROR: QSF file not found"        rc 1
    oe_pattern_check           "analyzed 0 file(s)"               rc 0

`gate_discloses_denominator_check` audits the same 493-program population and
passes all three, CORRECTLY — its contract is "a PASS must say how much it
looked at", and all three said so. **Disclosing a zero denominator and refusing
on one are different properties**, and only the first was enforced anywhere.

The second is not stylistic. The P0 umbrella reads EXIT CODES, so a gate that
prints `0` in prose and returns `0` in rc contributes a silent pass to a
project-level verdict: the disclosure is in the output and the aggregation reads
the code.

THE LINE THIS TURNS ON, and it is the issue's own
=================================================
    "An empty artefact is not a missing one. An empty `.v` file was opened and
     read: `analyzed 1 file(s), found 0` is a real result and rc 0 is correct."

So the predicate keys on a zero beside a POPULATION word — analysed, scanned,
examined, read — and never beside a FINDING word. `found 0 violations` over a
real population is a real clean result; `analysed 0 files` is not a result at
all. A gate that says both (`analysed 12 file(s), found 0`) states a real
population and is not a finding here.

WHAT IT DOES NOT DO
===================
It does not make any gate refuse. #564 asks for the PROBE, not a blanket
behaviour change — and a blanket change is measurably wrong: forcing refusal on
four gates was tried and flipped 182 / 159 / 94 / 42 of 182 tracked run dirs,
so it was reverted. Knowing WHICH of the population has the defect is the
deliverable; each fix is then its own measured change.

THE POPULATION AND THE DRIVER ARE IMPORTED, NOT REBUILT.
`project_check_programs` and `_drive_on_empty_project` come from
`gate_discloses_denominator_check`. A second scanner over the same question is
how this repo has previously reproduced a bug the first one already defended
against — one fresh directory per gate, because gates write into the project
they audit and a shared scratch makes gate N's report gate N+1's input.

EXEMPTIONS ARE A DATED INVENTORY, not a bare allow-list — the shape the sibling
gate already uses. An entry states WHEN it was measured and WHY the zero is a
correct pass, so the list can only shrink by a visible edit.

Exit: 0 = every gate agrees / 1 = at least one states a zero population and
exits 0 / 2 = could not probe (empty population).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess as _sp
import tempfile as _tf
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from gate_discloses_denominator_check import (  # noqa: E402
    project_check_programs as _population,
    _drive_on_empty_project as _drive,
)

TOOL = "gate_zero_denominator_refuses_check"
RC_OK, RC_FINDINGS, RC_CANNOT_PROBE = 0, 1, 2

#: Words that name the POPULATION a gate read.
_POP = (r"file|files|design|designs|interface|interfaces|net|nets|cell|cells|"
        r"instance|instances|module|modules|pin|pins|gate|gates|record|records|"
        r"entry|entries|report|reports|artefact|artefacts|document|documents|"
        r"step|steps|block|blocks|port|ports|line|lines|test|tests")
#: Words that name a FINDING. A zero beside one of these is a real clean result.
_FIND = (r"violation|violations|error|errors|warning|warnings|finding|findings|"
         r"issue|issues|mismatch|mismatches|failure|failures|defect|defects|"
         r"problem|problems|gap|gaps")

#: `analysed 0 file(s)` / `0 interfaces analyzed` / `scanned 0 designs`
_ZERO_POP_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:analy[sz]ed|scanned|examined|audited|probed|inspected|read|"
    r"processed|visited|collected)\s+0\b(?!\s*(?:" + _FIND + r")\b)"
    r"|"
    r"\b0\s+(?:" + _POP + r")\b\s*(?:\([^)]*\)\s*)?"
    r"(?:analy[sz]ed|scanned|examined|audited|probed|inspected|read|"
    r"processed|found|present|in\b)"
    r")")

#: A gate that says it could not find its input at all. Same class: it read
#: nothing. Kept separate so the finding can say which shape it was.
_NO_INPUT_RE = re.compile(
    r"(?im)^\s*(?:ERROR|WARNING)\b[^\n]*\b(?:not found|missing|does not exist|"
    r"no such file|could not (?:be )?(?:open|read|locate))\b")

_MEASURED_ON = "2026-08-01"

#: Gates whose ZERO over an empty project is a CORRECT pass, with the date the
#: exemption was measured and the reason. Dated inventory, never a bare
#: allow-list: an entry that stops being true raises STALE_INVENTORY_ENTRY.
_ZERO_IS_A_PASS: Dict[str, Dict[str, str]] = {
    name: {"measured": measured, "reason": reason}
    for name, measured, reason in (
        ("professional_tb_check", "2026-08-14",
         "A professional testbench is an OPTIONAL step. When it did not run there is "
         "no report to read, and the checker says so out loud "
         "(VACUOUS_PASS: professional_tb examined 0 testbench report(s)). Making it "
         "refuse with rc 2 would force every run WITHOUT that step to disclose a skip "
         "it does not owe, so rc 0 is the correct verdict here and the prose is the "
         "disclosure. See professional_tb_check.py:337-346, where this argument was "
         "already written in a comment this gate could not read. Recorded here so the "
         "argument lives where the gate looks, and so STALE_INVENTORY_ENTRY fires if it "
         "ever stops being true."),
    )
}


def states_zero_population(text: str) -> bool:
    """True iff the output says it read NOTHING.

    `found 0 violations` is not this — that is a finding over a population.
    `analysed 12 file(s), found 0` is not this either.
    """
    return bool(text) and bool(_ZERO_POP_RE.search(text))


def states_missing_input(text: str) -> bool:
    return bool(text) and bool(_NO_INPUT_RE.search(text))


#: THE SECOND POPULATION — THE ADVISORY CENSUSES (vibe-ic#2227).
#:
#: `project_population` selects a verdict emitter two ways, and BOTH are blind
#: to this class. By name it takes five checker suffixes; `*_census.py` is not
#: one. By behaviour it takes a source carrying a `[PASS]`/`[FAIL]` banner
#: (`checker_population_is_structural_not_filename_shaped_census._VERDICT`); an
#: advisory census prints `[CENSUS]` and `[CANNOT DETERMINE]` and so matches
#: neither. MEASURED on live main 9c653d47f: 716 programs in the population
#: (662 by suffix + 56 by behaviour), 10 `*_census.py` in the tree, 4 of them
#: inside the population and SIX outside it.
#:
#: This class is the one that most needs the check. A census's declared
#: contract is rc 0 WITH findings — its only red is the zero-population
#: refusal. So for a census that refusal is not one guard among many, it is the
#: entire difference between a measurement and a sentence.
#:
#: SELECTED FROM THE TREE BY THE SAME RELATION, NOT BY A NAME. A population
#: chosen by a filename is the defect `checker_population_is_structural_not_
#: filename_shaped_census` exists to name, so this reads the same structure it
#: does — a source that carries a census banner AND has a `__main__` entry —
#: with the census vocabulary instead of the verdict one. A file called
#: `*_census.py` that prints neither banner is not selected, and a program that
#: prints one under any other name is.
_CENSUS_BANNER = ("[CENSUS]", "[CANNOT DETERMINE]", "[CANNOT CHECK]")


def advisory_census_population(programs_dir: Path,
                               already: set) -> List[Path]:
    """Verdict-emitting censuses the primary population cannot see."""
    out = []
    for path in sorted(programs_dir.glob("*.py")):
        if path.stem == Path(__file__).stem or path.name in already:
            continue
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(b in src for b in _CENSUS_BANNER):
            continue
        if "__main__" not in src:
            continue
        out.append(path)
    return out


#: HOW TO HAND A PROGRAM AN EMPTY SUBJECT, and why this is a list rather than
#: one convention. `_drive_on_empty_project` passes a positional path and an
#: empty PROJECT. A census's subject is a REPOSITORY and four of the six
#: measured take it by option, so that driver cannot reach them at all —
#: MEASURED on 9c653d47f, four of the six answered
#: `error: unrecognized arguments: .` (rc 3), which is not a verdict about
#: anything. Each convention below is CONFIRMED against the program's own
#: `--help` before it is used, so this is not a guess about any one program:
#: the program declares what it accepts and the driver reads that declaration.
#: A program that declares none of them is NOT_MEASURED, by name — never a pass.
_SUBJECT_CONVENTIONS = ("--root", "--programs")

_NOT_MEASURED = "NOT_MEASURED"


def _accepts(prog: Path, option: str, timeout: int) -> bool:
    """Does the program's OWN `--help` declare this option?"""
    try:
        r = _sp.run([sys.executable, str(prog), "--help"],
                    capture_output=True, text=True, timeout=timeout)
    except (OSError, _sp.SubprocessError):
        return False
    return option in (r.stdout or "") + (r.stderr or "")


def _drive_on_empty_subject(prog: Path, timeout: int) -> Dict:
    """Run ONE census over an EMPTY subject, using a convention it declares.

    Returns ``rc``, or ``rc=None`` with ``not_measured`` naming why. A census
    that cannot be handed an empty subject is DISCLOSED, never scored as clean:
    a population this gate did not reach must not read as one it approved.
    """
    with _tf.TemporaryDirectory(prefix="empty_subject_") as td:
        empty = Path(td)
        argv = None
        for opt in _SUBJECT_CONVENTIONS:
            if _accepts(prog, opt, timeout):
                argv = [opt, str(empty)]
                break
        if argv is None:
            # A positional subject, or a program that reads its cwd. Both are
            # answered by running INSIDE the empty directory; the positional
            # form additionally needs the path, and a program that takes
            # neither declines with its own usage error, which is captured.
            argv = []
        try:
            r = _sp.run([sys.executable, str(prog), *argv, str(empty)]
                        if not argv else [sys.executable, str(prog), *argv],
                        capture_output=True, text=True, timeout=timeout,
                        cwd=str(empty))
        except _sp.TimeoutExpired:
            return {"gate": prog.stem, "rc": None,
                    "not_measured": f"made no progress within {timeout}s"}
        except (OSError, _sp.SubprocessError) as exc:
            return {"gate": prog.stem, "rc": None,
                    "not_measured": f"could not be driven: {exc}"}
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        # WHY THE TWO CASES ARE SEPARATED (RC13/H2). Both end in an argparse
        # usage error, and reporting them with one sentence made this gate state
        # a FALSE cause. MEASURED at f19703dfa on the four censuses it could not
        # drive: `ppa_contract_build`, `ppa_eco_spare_records` and
        # `readme_ppa_extractor` all ACCEPT `--root` -- what stopped the probe is
        # `--declaration/--out`, `--spare-plan/--stage` and `--readme`, arguments
        # this gate must not invent. Only `ppa_metric_extract` genuinely declares
        # no subject convention ("unrecognized arguments: --root"). A reader
        # acting on the old message would have added `--root` to three programs
        # that already have it.
        #
        # Neither case becomes MEASURED: inventing a `--declaration` would be
        # manufacturing the subject, which is the thing this gate exists to
        # refuse. What changes is that the disclosure names the true obstacle.
        if "unrecognized arguments" in out:
            return {"gate": prog.stem, "rc": None,
                    "not_measured": "declares none of the subject conventions "
                                    f"{_SUBJECT_CONVENTIONS} and refused the "
                                    f"positional form, so no empty subject "
                                    f"could be constructed for it"}
        if "the following arguments are required" in out:
            _req = out.rsplit("the following arguments are required:", 1)[-1]
            _req = _req.splitlines()[0].strip() or "(unnamed)"
            _has = [o for o in _SUBJECT_CONVENTIONS if _accepts(prog, o, timeout)]
            return {"gate": prog.stem, "rc": None,
                    "not_measured": (
                        f"accepts {_has or 'no'} subject convention"
                        f"{'' if len(_has) == 1 else 's'} but also requires "
                        f"{_req}, which this probe must not invent — so no empty "
                        f"subject could be constructed for it without supplying "
                        f"content the gate would then be measuring itself")}
        return {"gate": prog.stem, "rc": r.returncode,
                "output_tail": (out.splitlines()[-1][:200] if out
                                else "(no output at all)")}


def audit_advisory_censuses(programs_dir: Path, already: set,
                            timeout: int = 120,
                            workers: int = 0) -> Tuple[List[Dict], Dict]:
    """The #2227 arm: a census over an EMPTY population must not exit 0.

    NO PROSE IS PARSED HERE, deliberately. The primary arm has to read the
    output because a checker over an empty project may legitimately exit 0 and
    only its wording distinguishes the two. A census has no such ambiguity: its
    authors are unanimous that an empty population is never a pass, and five of
    the six measured already refuse. So the property is the exit code alone,
    which cannot be reworded.
    """
    progs = advisory_census_population(programs_dir, already)
    if not progs:
        return [], {"censuses_probed": 0, "censuses_refused": 0,
                    "censuses_exited_0": 0, "censuses_not_measured": 0,
                    "censuses_not_measured_names": []}
    workers = workers or min(8, (os.cpu_count() or 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda p: _drive_on_empty_subject(p, timeout),
                              progs))
    findings, refused, exited0, unmeasured = [], 0, 0, []
    for res in results:
        if res["rc"] is None:
            unmeasured.append(f"{res['gate']} ({res['not_measured']})")
            continue
        if res["rc"] == 0:
            exited0 += 1
            findings.append({
                "gate": res["gate"], "kind": "CENSUS_OVER_AN_EMPTY_POPULATION_EXITS_ZERO",
                "rc": 0, "output_tail": res.get("output_tail", ""),
                "detail": "driven over an EMPTY subject this census exited 0. A "
                          "census's only red is the zero-population refusal, so "
                          "an exit 0 here is the whole difference between a "
                          "measurement and a sentence. Refuse with rc 2 "
                          "(the disclosed-skip convention) BEFORE the findings "
                          "branches — a refusal placed under `if rc == 0` is "
                          "switched off by any finding, including one read from "
                          "an inventory FILE that says nothing about the tree.",
            })
        else:
            refused += 1
    return findings, {"censuses_probed": len(progs),
                      "censuses_refused": refused,
                      "censuses_exited_0": exited0,
                      "censuses_not_measured": len(unmeasured),
                      "censuses_not_measured_names": sorted(unmeasured)}


def audit(programs_dir: Path, timeout: int = 120,
          workers: int = 0) -> Tuple[str, List[Dict], Dict]:
    # A PROBER MUST NOT BE IN ITS OWN POPULATION. `project_check_programs` is
    # `glob("*_check.py")`, so this file is in it: probing drove a nested copy,
    # which drove another, each spawning the whole population again. It hung a
    # landing gate for 35 minutes and left 75 orphaned processes.
    #
    # `gate_discloses_denominator_check` has the same shape and survives only
    # because its 120s per-gate budget truncates the recursion at depth 1 — and
    # that truncation is exactly the "1 unrunnable" in this gate's own first
    # census. A timeout absorbing a self-inflicted recursion is not the same as
    # not having one.
    progs = [p for p in _population(programs_dir) if p.stem != Path(__file__).stem]
    if not progs:
        # This program's own denominator — never a silent PASS.
        return "NOTHING_PROBED", [], {"gates_probed": 0}
    workers = workers or min(8, (os.cpu_count() or 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda p: _drive(p, timeout), progs))

    findings: List[Dict] = []
    zero_rc0: List[str] = []
    zero_refused: List[str] = []
    unrunnable: List[str] = []
    for res in results:
        gate = res["gate"]
        if res.get("unrunnable") or res["rc"] is None:
            unrunnable.append(gate)
            continue
        tail = res.get("output_tail") or ""
        zero = states_zero_population(tail) or states_missing_input(tail)
        if not zero:
            continue
        if res["rc"] == 0:
            zero_rc0.append(gate)
            if gate not in _ZERO_IS_A_PASS:
                findings.append({
                    "gate": gate, "kind": "ZERO_DENOMINATOR_EXITS_ZERO",
                    "rc": 0, "output_tail": tail,
                    "detail": "the gate states it read NOTHING and still exits "
                              "0, so the P0 umbrella — which reads exit codes, "
                              "not prose — records a silent pass. Either make "
                              "it refuse (rc 2 is the disclosed-skip "
                              "convention) or record it in _ZERO_IS_A_PASS "
                              "with the reason its zero is a correct pass.",
                })
        else:
            zero_refused.append(gate)

    for name in sorted(_ZERO_IS_A_PASS):
        if name not in zero_rc0:
            findings.append({
                "gate": name, "kind": "STALE_INVENTORY_ENTRY",
                "detail": f"exempted on {_ZERO_IS_A_PASS[name]['measured']} but "
                          f"it no longer states a zero population while exiting "
                          f"0 — delete the entry rather than leave it "
                          f"suppressing something it no longer describes",
            })

    # vibe-ic#2227 — THE SECOND ARM, in the SAME audit, on purpose. Returning
    # it separately would let a caller run one and publish the other's silence.
    census_findings, census_stats = audit_advisory_censuses(
        programs_dir, {p.name for p in progs}, timeout, workers)
    findings.extend(census_findings)

    stats = {"gates_probed": len(progs),
             "stated_zero_population": len(zero_rc0) + len(zero_refused),
             "zero_and_exited_0": len(zero_rc0),
             "zero_and_refused": len(zero_refused),
             "exempted": len(_ZERO_IS_A_PASS),
             "unrunnable": len(unrunnable),
             **census_stats}
    verdict = "FINDINGS" if findings else "PASS"
    return verdict, findings, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--programs-dir", default=str(_HERE))
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args(argv)

    # vibe-ic#619 — THIS CHECK'S POPULATION IS THE PROGRAM REGISTRY, not a
    # project: it builds one fresh empty project PER GATE and never reads the
    # `project` argument. It still accepted one, ignored it, and answered PASS
    # — byte-identical output for a path that exists, a path that is empty, and
    # a path that is not there.
    #
    # That is the exact shape this file exists to detect. Its own subject is
    # "a zero denominator that exits 0 is a silent pass", and it was reporting
    # success over a population it had never established: a run pointed at a
    # typo'd path read PASS. Both registry-wide meta-checks caught it the moment
    # it joined the registry it audits — `PASS_ON_A_PROJECT_THAT_IS_NOT_THERE`.
    #
    # A path it will not read is a path it cannot vouch for, so an absent one is
    # NOT_CHECKED (rc 2, the disclosed-skip convention) rather than a pass. The
    # default `.` always exists, so the standing invocation
    # (`repo_hygiene_gates.sh`, no argument) is unchanged.
    _proj = Path(a.project)
    if not _proj.exists():
        print(f"[SKIP] {TOOL}: the project path {a.project!r} does not exist. "
              f"This check's population is the PROGRAM REGISTRY — it probes "
              f"each gate against its own fresh empty project and never reads "
              f"the project argument — so it cannot answer for that path, and "
              f"reporting PASS for a project that is not there is the very "
              f"defect it looks for in others.", file=sys.stderr)
        return RC_CANNOT_PROBE

    verdict, findings, stats = audit(Path(a.programs_dir), a.timeout, a.workers)
    rep = {"tool": TOOL, "verdict": verdict, "findings": findings, **stats}
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(rep, indent=2) + "\n")

    if verdict == "NOTHING_PROBED":
        print(f"[SKIP] {TOOL}: no gate program found under {a.programs_dir} — "
              f"nothing was probed, which is a gap in this check, not a pass",
              file=sys.stderr)
        return RC_CANNOT_PROBE
    for f in findings:
        print(f"[FAIL] {f['gate']}: {f['kind']} — {f['detail']}", file=sys.stderr)
        if f.get("output_tail"):
            print(f"    said: {f['output_tail']}", file=sys.stderr)
    # #619 — say WHAT was audited. "probed against an empty project" reads, to
    # anyone who passed a project path, as a statement about THEIR project.
    print(f"{TOOL}: {verdict} — {stats['gates_probed']} gate(s) probed, each "
          f"against its OWN fresh empty project (the population is the program "
          f"registry under {a.programs_dir}; {a.project!r} is not read); "
          f"{stats['stated_zero_population']} stated a zero "
          f"population, of which {stats['zero_and_refused']} refused and "
          f"{stats['zero_and_exited_0']} exited 0 "
          f"({stats['exempted']} exempted, {stats['unrunnable']} unrunnable)")
    # vibe-ic#2227 — the second population is DISCLOSED on every run, passing or
    # not. A class this gate did not reach must never read as one it approved,
    # and a population that quietly shrank is the same silent zero this whole
    # program is about.
    print(f"{TOOL}: advisory-census arm — {stats['censuses_probed']} census(es) "
          f"probed over an EMPTY subject, {stats['censuses_refused']} refused, "
          f"{stats['censuses_exited_0']} exited 0, "
          f"{stats['censuses_not_measured']} NOT_MEASURED")
    for name in stats.get("censuses_not_measured_names", []):
        print(f"    NOT_MEASURED: {name}", file=sys.stderr)
    return RC_FINDINGS if findings else RC_OK


if __name__ == "__main__":
    raise SystemExit(main())
