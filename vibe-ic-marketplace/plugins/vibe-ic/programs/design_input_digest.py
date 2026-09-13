#!/usr/bin/env python3
"""design_input_digest.py — a published tally must carry a record of WHAT it
was computed over, so a reader can tell "the design improved" from "the ruler
changed".

THE DEFECT, measured.

    The same run directory, byte-identical, judged twice:

        plugin 1.9.76   PASS=22  FAIL=5  MISSING=0
        a newer plugin  PASS=6   FAIL=8  MISSING=2

    Sixteen of those twenty-two passes were never real; two new discriminators
    began answering questions the old plugin passed blindly. That is a
    TIGHTENING, and the correct reading of the drop is "the ruler got better",
    not "the design got worse".

    The same day, the other direction, on a different cell: an agent supplied
    real post-route 3-corner STA — strictly BETTER evidence — and the tally
    went 17 PASSes LOWER; and disabling one deliberately-installed cross-step
    check scored 2 PASSes HIGHER while the design sat untouched.

    Both readings were made from `reports/audit/phase23_completion_audit.json`,
    and NOTHING in that artefact could separate them. Measured over the 28
    audit artefacts tracked in this repo, every one of them carries the same
    `"version": "0.119.62"` — a string literal present since the initial public
    release, so an audit written by 1.0.0 and an audit written by 1.9.79 make
    byte-identical claims about which ruler produced them. And no field of any
    kind named the design the verdict was computed over.

    A tally with no record of its population is not a measurement. Inflated
    numbers were reported to the owner for a full day because of exactly this.

WHAT IS RECORDED, and why it is TWO hashes and not one.

    design_input_sha256   over the design + the evidence the verdict reads
    measurement.id        over the ruler: plugin version, flow definition,
                          and the invocation flags that select the step set

    One hash cannot answer the question. "Did the design change?" and "did the
    ruler change?" are independent, and the four combinations have four
    different readings — including the one that has no honest reading at all
    (both moved: the tally movement is not ATTRIBUTABLE to either, and cannot
    be published as progress OR as a tightening).

THE INPUT SET, and the design that was tried first and is WRONG.

    The obvious construction is a path allowlist that excludes `reports/`,
    since that is where the auditor writes. It was measured and rejected:
    across the 28 audit-bearing tracked projects, `reports/phase3/` is where
    the DRC and LVS sign-off reports THEMSELVES live (5 and 7 project roots
    respectively), next to the checker JSON the auditor emits. A prefix
    exclusion would blind the hash to precisely the arriving sign-off evidence
    that the second half of the finding above is about — and a blind hash does
    not merely miss a change, it makes the artefact assert MEASUREMENT_CHANGE
    over a design that really did move. That is fabrication, not an omission.

    So the exclusion is MEASURED, not declared by path. The tree is scanned
    once before the auditor runs anything and once after; every path the
    auditor created, removed or wrote drops out, plus the paths it writes
    after the second scan (which it names), plus the footprint the previous
    audit at this path recorded — `auditor_footprint` documents each source
    and the measured failure that required it.

    The footprint is PUBLISHED as a name set, not only as a count: it is what
    the next run carries forward, and it is the only thing a reviewer can use
    to judge whether the exclusion is honest.

    Measured on a tracked project: 241 files present, the auditor created 13
    and touched 24 (all 37 under `reports/`; only 7 of the 24 differed in
    content, which is why an exclusion built on content ALONE would report a
    footprint of 20), and the digest is taken over the remaining 217.
    Re-running it leaves the digest unchanged — which is the only property
    that makes the field mean anything.

EMPTY IS REFUSED. A digest over zero files compares equal to another digest
over zero files, so two unrelated projects would read as "the same design".
`sha256` is None with a stated reason instead, and `classify` refuses to
compare rather than returning a verdict it cannot support. Same rule
`benchmark_run_manifest` states about empty name sets, for the same reason.

Exit codes (`--compare`):
    0  the comparison was made and the tally movement is either absent or
       accompanied by a design change that could explain it
    1  the tally moved and the DESIGN did not — a measurement change or an
       unexplained move; publishing either as progress is the defect
    2  the question could not be put (unreadable file, or an audit that
       predates this record and carries no digest to compare)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1

#: Directory names never part of a design. `.git` is version-control
#: bookkeeping and `__pycache__` is a byproduct of reading the tree with
#: Python; both move without the design moving. Published in the artefact so
#: the exclusion is never silent.
EXCLUDED_DIR_NAMES: Tuple[str, ...] = (".git", "__pycache__", ".vibeic-state")

#: Bounds, so a pathological tree cannot hang a gate. Measured headroom on the
#: tracked corpus: the largest project is 873 files / 66.8 MB and the whole
#: 28-project corpus is 7175 files / 394 MB, hashed in well under a second.
#: A truncated scan is REPORTED and refuses comparison — a partial hash
#: compares equal to another partial hash over a different truncation.
LIMIT_FILES = 200_000
LIMIT_BYTES = 20 * 1024 * 1024 * 1024

#: The invocation flags that select WHICH steps are judged and HOW strictly —
#: i.e. the ruler's configuration. `project_dir`, `json`, `read_only` and
#: `list_structural_gates` are deliberately NOT here: they change where the
#: work happens, not what is asked.
#:
#: A ruler flag added later and not listed here degrades SAFELY: two runs with
#: different rulers get the same `measurement.id`, so the tally movement is
#: classified UNEXPLAINED_TALLY_MOVE, which still refuses the progress claim.
#: The reverse mistake — listing a flag that is not part of the ruler — would
#: manufacture a measurement change out of an irrelevant argument, so the list
#: is an allowlist. `test_design_input_digest` fails if any option of
#: flow_compliance_check is in neither this set nor NON_RULER_FLAGS.
RULER_FLAGS: Tuple[str, ...] = (
    "flow",
    "strict",
    "lenient",
    "stage",
    "skip_yosys_gates",
    "skip_analog",
    "skip_hardware",
    "strict_audit_evidence",
    "phase",
    "strict_structural",
    "strict_step_artifacts",
    # Changes whether a satisfied project_glob without a usable write ledger
    # is accepted or blocks DONE.  That changes the verdicting rule, so two
    # invocations with different values must not share a measurement id.
    "strict_step_binding",
    "strict_timing",
    "strict_no_os_constraints",
    "allow_thin_input",
    # BOTH OF THESE NARROW THE POPULATION THAT IS JUDGED, which is exactly
    # what a ruler does. `--exclude-step` drops named steps from the pass;
    # `--stage-id` restricts it to one stage, and its own help says it is
    # mutually exclusive with `--stage`, which has been a ruler here all
    # along. Classifying them the other way would be the one-line green that
    # costs the most: two runs over DIFFERENT step populations would share a
    # `measurement.id`, and `classify` would then read the tally movement
    # between them as DESIGN_CHANGE — publishing a change of ruler as
    # progress.
    "exclude_step",
    "stage_id",
)

#: The ruler flags that change WHICH STEPS are judged, as opposed to how they
#: are judged. A SUBSET of `RULER_FLAGS` — every name here is also there, and
#: `test_prior_tally_is_a_previous_run` asserts that.
#:
#: vibe-ic#2092 (lane icaes F07). Two audits of ONE run directory, 2.16 s
#: apart, were published as a `tally_delta`: the prior tally counted 13 steps,
#: the current 69, and `classify` reported ``MEASUREMENT_CHANGE`` — "the tally
#: moved while the design stayed BYTE-IDENTICAL; what changed is the ruler".
#: Every word of that sentence is true and the record is still wrong, because
#: 69 minus 13 is not a movement in a tally: the two numbers count DIFFERENT
#: STEP POPULATIONS, so their difference is not a delta at all. The artefact
#: recorded no scope for the prior either, so a reader could not see that.
#:
#: `MEASUREMENT_CHANGE` keeps the case it exists for — the SAME population
#: judged by a different ruler (a plugin version, a strict flag). What is
#: taken away from it is the case where the question itself changed.
POPULATION_FLAGS: Tuple[str, ...] = (
    "flow",
    "phase",
    "stage",
    "stage_id",
    "exclude_step",
)

#: Options that do not change the question asked, each with the reason.
NON_RULER_FLAGS: Dict[str, str] = {
    "help": "argparse builtin",
    "flow_def": "recorded as flow_def_sha256, which is stronger than the path",
    "json": "where the report is written, not what is judged",
    "read_only": "audits a byte-identical copy; the verdict is unchanged",
    "list_structural_gates": "prints the registry and exits without judging",
}

#: The classifications, and whether the tally movement may be published as a
#: statement about the design.
CLASSIFICATIONS = (
    "UNCHANGED",
    "DESIGN_CHANGE",
    "MEASUREMENT_CHANGE",
    "UNEXPLAINED_TALLY_MOVE",
    "NOT_ATTRIBUTABLE",
    "NOT_COMPARABLE",
)


# ─────────────────────────── scanning ────────────────────────────────


class InputScan:
    """The tree as it stood BEFORE the auditor touched it."""

    __slots__ = ("hashes", "stats", "bytes_read", "symlink_count",
                 "unreadable", "truncated", "truncated_reason")

    def __init__(self) -> None:
        self.hashes: Dict[str, str] = {}
        self.stats: Dict[str, Tuple[int, int]] = {}
        self.bytes_read: int = 0
        self.symlink_count: int = 0
        self.unreadable: List[str] = []
        self.truncated: bool = False
        self.truncated_reason: Optional[str] = None


def _walk(project: Path):
    """Every regular file and symlink under `project`, excluding
    `EXCLUDED_DIR_NAMES`. Symlinks are NOT followed: a symlinked directory
    would either double-count its target or escape the project entirely."""
    for root, dirs, files in os.walk(project, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIR_NAMES)
        for name in sorted(files):
            yield Path(root) / name


def _rel(project: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project))
    except ValueError:
        return path.name


def scan_inputs(project: Path) -> InputScan:
    """Content digest + (mtime_ns, size) for every file under `project`.

    Call BEFORE the auditor runs anything. The content digest is what the
    published hash is built from; the stats are what identify the auditor's
    own writes afterwards.
    """
    scan = InputScan()
    for path in _walk(project):
        rel = _rel(project, path)
        try:
            st = os.lstat(path)
        except OSError as exc:
            scan.unreadable.append(f"{rel}: {exc}")
            continue
        scan.stats[rel] = (st.st_mtime_ns, st.st_size)
        if len(scan.hashes) >= LIMIT_FILES:
            scan.truncated = True
            scan.truncated_reason = (
                f"file count exceeded LIMIT_FILES={LIMIT_FILES}")
            break
        if scan.bytes_read >= LIMIT_BYTES:
            scan.truncated = True
            scan.truncated_reason = (
                f"bytes read exceeded LIMIT_BYTES={LIMIT_BYTES}")
            break
        if path.is_symlink():
            # The TARGET STRING, not the target's bytes: a retargeted symlink
            # is a design change, and following it would leave the project.
            try:
                scan.hashes[rel] = "symlink:" + hashlib.sha256(
                    os.readlink(path).encode("utf-8", "surrogateescape")
                ).hexdigest()
                scan.symlink_count += 1
            except OSError as exc:
                scan.unreadable.append(f"{rel}: {exc}")
            continue
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    scan.bytes_read += len(chunk)
            scan.hashes[rel] = digest.hexdigest()
        except OSError as exc:
            scan.unreadable.append(f"{rel}: {exc}")
    return scan


def auditor_footprint(pre: InputScan, post: InputScan,
                      also_written: Optional[List[str]] = None,
                      previously_written: Optional[List[str]] = None
                      ) -> List[str]:
    """Every path the auditor created, removed, or wrote.

    THREE sources, and each one exists because the source before it was
    measured to be insufficient.

    CONTENT + STAT, between the scan taken before any sub-gate ran and the one
    taken after. Content is what decides correctness: a file whose bytes the
    auditor changed must leave the population, or the digest moves between two
    runs over an untouched tree and the artefact reports that the DESIGN
    moved. Stat catches the identical-byte rewrites on top, which keeps the
    published `auditor_written_paths_excluded` an honest description of what
    the auditor touched — measured on a tracked project, 24 pre-existing files
    were touched and only 7 of them differed in content.

    DECLARED (`also_written`): the paths written AFTER the second scan, which
    measurement cannot reach — the audit JSON itself and a `--json` report
    aimed inside the tree. Without them the previous run's audit becomes this
    run's design input and the digest moves on every second run.

    CARRIED (`previously_written`): the footprint the PREVIOUS audit at this
    path recorded. This is what makes the exclusion deterministic instead of
    merely usually-right. A rewrite that lands inside a single filesystem
    timestamp tick with unchanged size and unchanged bytes is invisible to
    content AND to stat, so a path can be excluded on one run and rejoin the
    population on the next — and that flip moves the digest over an untouched
    design, which is the fabrication direction. Measured: with detection alone
    this file's own suite failed on 4 of 12 runs.

    A clock threshold was built first and REJECTED: mtime is truncated down to
    the filesystem tick, so a threshold read from Python's fine clock lets
    writes through, and one read from a probe file's own mtime excluded every
    file created in the same tick — on a freshly built test project that was
    the whole tree, leaving a digest over 1 file out of 3. Carrying the prior
    footprint needs no clock at all.

    Carrying is MONOTONE for a given run directory: a path the auditor has
    ever been seen to write stays out. It cannot swallow the tree, because
    nothing enters it that was not measured as written at least once, and
    `build_digest` refuses a digest whose population has gone empty.
    """
    changed = set(also_written or ())
    changed.update(previously_written or ())
    for rel, st in post.stats.items():
        if rel not in pre.stats or pre.stats[rel] != st:
            changed.add(rel)
        elif pre.hashes.get(rel) != post.hashes.get(rel):
            changed.add(rel)
    for rel in pre.stats:
        if rel not in post.stats:
            changed.add(rel)
    return sorted(changed)


def kept_inputs(scan: InputScan, footprint: List[str]) -> List[str]:
    """The population the digest is taken over, by NAME.

    Exposed rather than left implicit in a count: this repo's own rule, from
    `benchmark_run_manifest`, is that a count says something moved and only a
    name set says what. A caller that needs to explain a moved digest reads
    this; the published block carries the count, because the names of every
    file in a project do not belong in an audit artefact.
    """
    excluded = set(footprint)
    return sorted(rel for rel in scan.hashes if rel not in excluded)


def build_digest(scan: InputScan, footprint: List[str]) -> Dict[str, Any]:
    """The block published beside the tally."""
    excluded = set(footprint)
    kept = kept_inputs(scan, footprint)

    block: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sha256": None,
        "file_count": len(kept),
        "bytes": scan.bytes_read,
        "symlink_count": scan.symlink_count,
        "excluded_dir_names": list(EXCLUDED_DIR_NAMES),
        "auditor_written_paths_excluded": len(excluded),
        # The NAME SET, not only the count. It is what the next run carries
        # forward to keep the exclusion deterministic, and it is the only
        # thing a reviewer can use to judge whether the exclusion is honest —
        # this repo's own rule, from `benchmark_run_manifest`: a count says
        # something moved, only a name set says what.
        "auditor_written_paths": list(footprint),
        "unreadable_count": len(scan.unreadable),
        "truncated": bool(scan.truncated),
        "truncated_reason": scan.truncated_reason,
        "unusable_reason": None,
    }

    if scan.truncated:
        # A partial hash compares equal to another partial hash over a
        # different truncation. Refused rather than published as an answer.
        block["unusable_reason"] = (
            "scan truncated; a partial digest cannot support a comparison")
        return block
    if not kept:
        # Empty compares equal to empty. Refused for the same reason.
        block["unusable_reason"] = (
            "no design inputs found under the project after excluding the "
            "auditor's own writes")
        return block

    h = hashlib.sha256()
    h.update(f"design_input_digest/v{SCHEMA_VERSION}\n".encode())
    for rel in kept:
        h.update(rel.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        h.update(scan.hashes[rel].encode())
        h.update(b"\n")
    block["sha256"] = h.hexdigest()
    return block


# ─────────────────────────── the ruler ───────────────────────────────


def build_measurement(plugin_version: str,
                      flow_def_path: Optional[Path],
                      ruler_flags: Dict[str, Any]) -> Dict[str, Any]:
    """Identify what COMPUTED the tally: release, flow definition, flags."""
    flow_sha: Optional[str] = None
    if flow_def_path is not None:
        try:
            flow_sha = hashlib.sha256(
                Path(flow_def_path).read_bytes()).hexdigest()
        except OSError:
            flow_sha = None

    normalised = {k: ruler_flags.get(k) for k in RULER_FLAGS}
    h = hashlib.sha256()
    h.update(f"measurement/v{SCHEMA_VERSION}\n".encode())
    h.update(str(plugin_version).encode())
    h.update(b"\0")
    h.update((flow_sha or "NO_FLOW_DEF").encode())
    h.update(b"\0")
    h.update(json.dumps(normalised, sort_keys=True).encode())
    return {
        "schema_version": SCHEMA_VERSION,
        "id": h.hexdigest(),
        "plugin_version": str(plugin_version),
        "flow_def_sha256": flow_sha,
        "ruler_flags": normalised,
    }


# ─────────────────────────── comparison ──────────────────────────────


def _tally_of(audit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "verdict": audit.get("verdict"),
        "step_counts": audit.get("step_counts"),
        "passed_gate_count": audit.get("passed_gate_count"),
        "failed_gate_count": audit.get("failed_gate_count"),
    }


def _design_sha(audit: Dict[str, Any]) -> Optional[str]:
    blk = audit.get("design_input_digest")
    if isinstance(blk, dict):
        sha = blk.get("sha256")
        return sha if isinstance(sha, str) and sha else None
    return None


def _measurement_id(audit: Dict[str, Any]) -> Optional[str]:
    blk = audit.get("measurement")
    if isinstance(blk, dict):
        mid = blk.get("id")
        return mid if isinstance(mid, str) and mid else None
    return None


def _flow_def_sha(audit: Dict[str, Any]) -> Optional[str]:
    blk = audit.get("measurement")
    if isinstance(blk, dict):
        sha = blk.get("flow_def_sha256")
        return sha if isinstance(sha, str) and sha else None
    return None


def _ruler_flags(audit: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    blk = audit.get("measurement")
    if isinstance(blk, dict):
        rf = blk.get("ruler_flags")
        if isinstance(rf, dict):
            return rf
    return None


def _step_population(audit: Dict[str, Any]) -> Optional[int]:
    """How many steps this audit's tally counted, or None when it says."""
    counts = audit.get("step_counts")
    if not isinstance(counts, dict):
        return None
    total = 0
    for v in counts.values():
        if not isinstance(v, int):
            return None
        total += v
    return total


def prior_is_a_previous_run(prior: Dict[str, Any],
                            current: Dict[str, Any]) -> Dict[str, Any]:
    """Is `prior` a PREVIOUS RUN of the same measurement, or another question?

    vibe-ic#2092 (lane icaes F07) — `classify`'s prior used to be whatever
    audit happened to sit at the path being overwritten. On a run that audits
    one tree twice under two different scopes that is the same path 2 s older,
    judging a DIFFERENT question, and subtracting its tally from this one
    produces a "delta" between two numbers that were never comparable: the
    measured pair counted 13 steps and 69.

    TWO REJECTIONS, each measurable from the two artefacts alone:

      * DIFFERENT QUESTION — the two differ on a `POPULATION_FLAGS` value, so
        they judged different STEP POPULATIONS.
      * DIFFERENT POPULATION SIZE UNDER ONE FLOW DEFINITION — the two tallies
        count a different number of steps while naming the SAME
        `flow_def_sha256`. The guard is load-bearing: a flow definition that
        gained a step legitimately changes the population size, and that is a
        ruler change over one run directory, not an incomparable pair.

    NOT a rejection: a different plugin version, flow definition or strict
    flag. Those are the ruler changing over ONE population — the case
    `MEASUREMENT_CHANGE` was built for, and it keeps it. Nor is an identical
    `run_at`: it is minted per invocation from the clock, so two audits that
    share one are a fixture, not a producer state, and `UNCHANGED` /
    `UNEXPLAINED_TALLY_MOVE` already own that pair.

    `comparable` is ``None`` — NOT_MEASURED, and never a pass — when neither
    rejection could be evaluated: no ruler flags on both sides AND no shared
    flow definition to size the populations under. Pure: two dicts in, one
    dict out.
    """
    p_rf, c_rf = _ruler_flags(prior), _ruler_flags(current)
    p_pop, c_pop = _step_population(prior), _step_population(current)
    p_flow, c_flow = _flow_def_sha(prior), _flow_def_sha(current)
    reasons: List[str] = []
    scope_diff: Dict[str, Any] = {}
    evaluated = False

    if p_rf is not None and c_rf is not None:
        evaluated = True
        for k in POPULATION_FLAGS:
            if p_rf.get(k) != c_rf.get(k):
                scope_diff[k] = {"prior": p_rf.get(k), "current": c_rf.get(k)}
        if scope_diff:
            reasons.append(
                f"the two audits judged DIFFERENT step populations: "
                f"{sorted(scope_diff)} differ, so their tallies answer "
                f"different questions and their difference is not a delta")

    if (p_pop is not None and c_pop is not None
            and p_flow is not None and p_flow == c_flow):
        evaluated = True
        if p_pop != c_pop:
            reasons.append(
                f"the prior tally counts {p_pop} step(s) and this one "
                f"{c_pop}, under one and the same flow definition; a "
                f"difference between two different populations is not a "
                f"movement in one")

    comparable: Optional[bool]
    if reasons:
        comparable = False
    elif evaluated:
        comparable = True
    else:
        comparable = None

    out: Dict[str, Any] = {
        "comparable": comparable,
        "reasons": reasons,
        "population_flags_differing": scope_diff,
        "prior_step_population": p_pop,
        "current_step_population": c_pop,
        "prior_flow_def_sha256": p_flow,
        "current_flow_def_sha256": c_flow,
        "prior_run_at": prior.get("run_at"),
        "current_run_at": current.get("run_at"),
    }
    if comparable is None:
        out["not_measured"] = (
            "neither audit records the ruler flags, and they name no common "
            "flow definition to size their populations under, so whether "
            "they judged the same population could not be established. "
            "NOT_MEASURED is not a pass.")
    return out


def classify(prior: Optional[Dict[str, Any]],
             current: Dict[str, Any]) -> Dict[str, Any]:
    """Which of the two — the design or the ruler — moved.

    Pure: two audit dicts in, one classification dict out. The rule it
    encodes is that a tally movement may only be read as a statement about the
    DESIGN when the design hash actually moved.
    """
    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "classification": "NOT_COMPARABLE",
        "attributable_to_design": False,
        "tally_moved": None,
        "design_moved": None,
        "measurement_moved": None,
        "statement": "",
        "prior": None,
        "current": {
            "design_input_sha256": _design_sha(current),
            "measurement_id": _measurement_id(current),
            "tally": _tally_of(current),
            "run_at": current.get("run_at"),
            # vibe-ic#2092 — the SCOPE, beside the tally. Without it a reader
            # holding two of these records cannot see that they answered
            # different questions, which is how a 13-step tally and a 69-step
            # tally were published as one movement.
            "ruler_flags": _ruler_flags(current),
        },
    }

    out["prior_eligibility"] = None
    if not isinstance(prior, dict):
        out["statement"] = (
            "No prior audit was available at this path, so this tally cannot "
            "be compared with anything.")
        return out

    # vibe-ic#2092 — WHAT IS AT THIS PATH IS NOT AUTOMATICALLY THE PREVIOUS
    # RUN. When it is not, `prior` stays ABSENT and says so by name; a delta
    # between two tallies that counted different populations is not a delta,
    # and publishing one is the defect this record exists to prevent.
    elig = prior_is_a_previous_run(prior, current)
    out["prior_eligibility"] = elig
    if elig["comparable"] is False:
        out["classification"] = "NOT_COMPARABLE"
        out["statement"] = (
            "The audit found at this path is NOT a previous run of this "
            "measurement, so there is no prior tally to compare with and "
            "none is published: " + "; ".join(elig["reasons"]) + ".")
        return out

    out["prior"] = {
        "design_input_sha256": _design_sha(prior),
        "measurement_id": _measurement_id(prior),
        "tally": _tally_of(prior),
        "run_at": prior.get("run_at"),
        "ruler_flags": _ruler_flags(prior),
    }

    p_design, c_design = _design_sha(prior), _design_sha(current)
    p_meas, c_meas = _measurement_id(prior), _measurement_id(current)

    if p_design is None or c_design is None:
        out["statement"] = (
            "One of the two audits carries no usable design-input digest, so "
            "which of the design and the ruler moved cannot be stated. An "
            "audit written before this record existed always lands here.")
        return out

    tally_moved = _tally_of(prior) != _tally_of(current)
    design_moved = p_design != c_design
    measurement_moved = (p_meas != c_meas) if (
        p_meas is not None and c_meas is not None) else None

    out["tally_moved"] = tally_moved
    out["design_moved"] = design_moved
    out["measurement_moved"] = measurement_moved

    if not tally_moved:
        out["classification"] = "UNCHANGED"
        out["statement"] = (
            "The tally is identical to the prior audit at this path"
            + (", although the design inputs moved — this verdict did not "
               "respond to that change." if design_moved else "."))
        return out

    if design_moved and measurement_moved:
        out["classification"] = "NOT_ATTRIBUTABLE"
        out["statement"] = (
            "The tally moved, and so did BOTH the design inputs and the "
            "ruler that judged them. This movement is not attributable to "
            "either; it must not be published as design progress and it must "
            "not be published as a tightening.")
        return out

    if design_moved:
        out["classification"] = "DESIGN_CHANGE"
        out["attributable_to_design"] = True
        out["statement"] = (
            "The tally moved and the design inputs moved with it, under an "
            "unchanged ruler. This movement is about the design.")
        return out

    # design_moved is False from here down.
    if measurement_moved:
        out["classification"] = "MEASUREMENT_CHANGE"
        out["statement"] = (
            "The tally moved while the design inputs stayed BYTE-IDENTICAL. "
            "What changed is the ruler, not the design. Presenting this "
            "movement as progress — or as a regression — is the defect this "
            "record exists to prevent.")
        return out

    out["classification"] = "UNEXPLAINED_TALLY_MOVE"
    out["statement"] = (
        "The tally moved while neither the design inputs nor the recorded "
        "ruler moved. Nothing measured explains it, so it says nothing about "
        "the design; the gate itself is not reproducing.")
    return out


#: Classifications where the tally moved and the design provably did not.
REFUSING_CLASSIFICATIONS = ("MEASUREMENT_CHANGE", "UNEXPLAINED_TALLY_MOVE")


def exit_code_for(classification: str) -> int:
    if classification == "NOT_COMPARABLE":
        return 2
    if classification in REFUSING_CLASSIFICATIONS:
        return 1
    return 0


# ─────────────────────────────── CLI ─────────────────────────────────


def _load(path: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=("Record, and compare, the design inputs a published "
                     "tally was computed over."))
    sub = ap.add_mutually_exclusive_group(required=True)
    sub.add_argument("--project", metavar="DIR",
                     help="Emit the design-input digest for a project tree. "
                          "No auditor footprint is subtracted, so run this "
                          "on a tree no gate is writing to.")
    sub.add_argument("--compare", nargs=2, metavar=("PRIOR", "CURRENT"),
                     help="Two phase23_completion_audit.json paths.")
    ap.add_argument("--json", metavar="PATH", help="Write the result here too.")
    args = ap.parse_args(argv)

    if args.project:
        project = Path(args.project)
        if not project.is_dir():
            print(f"design_input_digest: not a directory: {project}",
                  file=sys.stderr)
            return 2
        block = build_digest(scan_inputs(project), [])
        print(json.dumps(block, indent=2))
        if args.json:
            Path(args.json).write_text(json.dumps(block, indent=2))
        return 2 if block.get("sha256") is None else 0

    prior_path, current_path = args.compare
    prior, current = _load(prior_path), _load(current_path)
    if current is None:
        print(f"design_input_digest: unreadable audit: {current_path}",
              file=sys.stderr)
        return 2
    result = classify(prior, current)
    print(json.dumps(result, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
    return exit_code_for(result["classification"])


if __name__ == "__main__":
    sys.exit(main())
