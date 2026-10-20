#!/usr/bin/env python3
"""
provenance_check.py — Verify a file was produced by a logged tool run.

Pairs with provenance_logger.py. A flow gate asks:
    "does <project>/provenance.jsonl contain an entry that
     (a) names <tool> (in the allow-list),
     (b) exited 0,
     (c) declared <required_output> as one of its outputs, AND
     (d) whose logged hash still matches the file on disk?"

If yes → PASS. If no (no entry, wrong tool, hash mismatch, no file) → FAIL.

Canonical native post-map synthesis binds a measured Yosys root and the
measured OpenROAD child selected by the existing typed handoff. The tool
filter still constrains the root; the report retains both actual writers.
An invalid requested/published child blocks, including when an old alias
would otherwise match a Yosys row. Other outputs retain terminal-tool filters.

``--require-measured`` adds the question this check could never ask
-------------------------------------------------------------------
Everything above binds an ARTEFACT to a RUN. It has never asked whether that
run did any work, and MEASURED 2026-08-27 that is the gap a false sign-off
walks through: ``openroad`` executing the STA script exactly as ``eda_sta``
emitted it read no technology, linked no design, failed every report — and
**exited 0**. Every condition (a)–(d) above is satisfied by that run. The tool
named itself, the exit code was 0, the artefact hashed, and nothing measured
anything.

With ``--require-measured`` the bound entry must also carry the tool's own
``measurement`` record (see ``_mcp_measurement``), and there are THREE
outcomes, not two:

* ``measured: true``  → PASS, as before.
* ``measured: false`` with a hard class → **FAIL**, naming the class and the
  tool's own reason. This is the vacuous run, refused.
* **no record at all** → ``UNMEASURED``: the program prints an ``INCOMPLETE:``
  line and exits **0**. It is not a pass — ``flow_compliance_check`` reads that
  token and dispositions the step in the ``INCOMPLETE`` tier, which is counted
  and rendered separately from PASS and never counted into ``pass_count`` — and
  it is not a failure either, because treating "nobody stated this" as "the
  tool failed" converts an unmeasured thing into a bad result, which is the
  same lie pointing the other way. Every run predating this contract, and every
  artefact produced by a hand-run tool, lands here honestly.

``measured: false`` with the benign class ``NOTHING_TO_MEASURE`` PASSES: the
tool ran and there was legitimately nothing to judge. A gate that refuses that
refuses every design it applies to, and a gate that refuses everything gets
bypassed — which is a deleted gate.

Usage
-----
    provenance_check.py <project_dir> \\
        --output pnr/routed.def --tool openroad,openroad-flow-scripts \\
        --output pnr/drc.rpt    --tool klayout,openroad

Each --output/--tool pair is checked independently; all must pass.

    provenance_check.py <project_dir> --require-entries 5
        — simply asserts the log has >= 5 exit-0 entries (coarse sanity).

Exit codes
----------
    0 = every declared output has a matching valid log entry
    1 = one or more failed
    2 = usage / io error
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mcp_measurement  # noqa: E402
import _tool_log_provenance  # noqa: E402


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except OSError:
        return ""


def _load_log(project: Path) -> List[dict]:
    log = project / "provenance.jsonl"
    if not log.exists():
        return []
    entries: List[dict] = []
    with log.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"provenance_check: malformed line {i}: {exc}",
                      file=sys.stderr)
    return entries


def _bare_digest(value) -> str:
    """A digest with any `sha256:` prefix removed; "" when there is none."""
    s = str(value or "")
    return s[7:] if s.startswith("sha256:") else s


def _declares(entry: dict, out_rel: str, out_sha: str | None) -> bool:
    """Does this entry declare THIS artefact — by path, or by CONTENT?

    A path-only match calls a published artefact undeclared the moment the
    publisher moves it. MEASURED on the published corpus: three cells record
    `phase3/stage3/pnr/<top>.gds` in provenance and ship
    `phase3/stage4/gds/<top>.gds`, with a BYTE-IDENTICAL sha256 in both places.
    The run declared exactly this artefact; only its address changed.

    That is #448's ROUTED_AWAY distinction — "stored elsewhere" is not "never
    produced" — applied to provenance instead of citations. The digest is the
    stronger key anyway: a path match with a different digest would be a
    DIFFERENT file wearing the right name, and this rejects that too.
    """
    outputs = entry.get("outputs", {}) or {}
    if out_rel in outputs:
        return True
    # BOTH sides carry the `sha256:` prefix (`_sha256_file` emits it and the
    # log records it), so normalise both rather than one — stripping only the
    # recorded side compares a bare digest against a prefixed one and never
    # matches, which is exactly the silent no-op this guards against.
    want = _bare_digest(out_sha)
    if not want:
        return False
    return any(_bare_digest(rec) == want for rec in outputs.values())


def _find_entry(entries: List[dict], out_rel: str,
                allowed_tools: set,
                out_sha: str | None = None,
                project: Path | None = None) -> Tuple[dict | None, List[str]]:
    """Return (matching_entry, reasons_if_none).

    An entry that CLAIMS a witness (a row imported from an external flow's own
    step log, `_tool_log_provenance`, llv1 decision 4a) binds only when that
    witness re-verifies against `project`: a cited log that is gone, rewritten
    or not started by the flow is a fabricated witness, not a weaker one. A
    plain logged run or a #365 back-fill claims none and is judged as before.
    A witness whose reader-free evidence all holds but which the flow-log
    reader may not judge (UNCALIBRATED) still binds; `main` then reports that
    artefact NOT_MEASURED, never PASS, unless the run states it measured
    nothing (a --require-measured hard miss, which FAILs first).
    """
    reasons: List[str] = []
    matches = []
    for i, e in enumerate(entries):
        if not _declares(e, out_rel, out_sha):
            continue
        if _tool_log_provenance.claims_witness(e):
            ok, why = (_tool_log_provenance.verify_witness(e, project)
                       if project is not None else
                       (False, "no project to re-read the witness against"))
            if ok is not True and ok != _tool_log_provenance.UNCALIBRATED:
                reasons.append(f"entry {e.get('timestamp','?')} for {out_rel} "
                               f"claims a witness that does not hold: {why}")
                continue
        if e.get("exit_code", -1) != 0:
            reasons.append(f"entry {e.get('timestamp','?')} "
                           f"for {out_rel} has exit_code="
                           f"{e.get('exit_code')}")
            continue
        if allowed_tools and e.get("tool") not in allowed_tools:
            reasons.append(f"entry {e.get('timestamp','?')} "
                           f"tool '{e.get('tool')}' not in allowed "
                           f"{sorted(allowed_tools)}")
            continue
        matches.append((i, e))
    if not matches:
        if not reasons:
            reasons.append(f"no entry in provenance.jsonl declares "
                           f"'{out_rel}' as an output")
        return None, reasons
    # Pick the most recent matching entry. TIES BREAK TOWARD THE LATER LOG
    # POSITION, not the earlier one: `timestamp` is second-resolution, so a
    # correction written in the same second as the record it supersedes ties,
    # and a stable descending sort would then hand back the SUPERSEDED entry —
    # the opposite of "most recent". (The failure is safe-direction — the
    # superseded digest no longer matches the file, so the check FAILs rather
    # than passing — but it FAILs a corrected ledger for the wrong reason.)
    matches.sort(key=lambda ie: (ie[1].get("timestamp", ""), ie[0]),
                 reverse=True)
    return matches[0][1], []


def _postmap_provenance(project: Path, entries: List[dict], out_rel: str,
                        allowed: set, disk_hash: str) -> dict | None:
    """Bind the existing native handoff, never infer a generic transform edge.

    Called only for canonical synthesis aliases. ``bound_handoff`` already
    invokes ``librelane_postmap.selected`` and verifies the complete native
    parent/child, receipts, current inputs and aliases; do not duplicate it.
    """
    import librelane_contract as LC
    import librelane_postmap as postmap
    import synth_handoff_netlist_check as handoff

    result = {"status": "FAIL", "reasons": []}
    try:
        mode = postmap.mode(project)
        sidecar = project / "phase2/stage2/synth/synth_inputs.json"
        try:
            doc = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
        except (OSError, ValueError):
            if mode == "off":
                return None
            raise
        binding = doc.get("librelane_synthesis") if isinstance(doc, dict) else None
        if mode == "off" and (not isinstance(binding, dict) or "post_map" not in binding):
            return None
        bound = handoff.bound_handoff(project)
        result["native_handoff"] = bound
        if bound is None or bound.get("verdict") != "PASS":
            result["reasons"] = ((bound or {}).get("findings") or
                                 ["requested native post-map handoff is absent"])
            return result
        if bound["native"] == bound["parent"]:
            raise ValueError("requested/published native post-map child was not selected")
        # A terminal-only filter (e.g. --tool openroad) keeps its meaning,
        # after the requested native binding has been checked fail-closed.
        roots = {"yosys", "yosys-abc"}
        if allowed and not allowed.intersection(roots):
            return None
        roots = allowed.intersection(roots) if allowed else roots
        result.update(tool="openroad", provenance_chain=[])
        pending = []
        for role, expected_tool, producer, digest in (
                ("parent", roots, "LibreLane.Yosys.Synthesis", _sha256_file(Path(bound["parent"]))),
                ("child", {"openroad"}, postmap.STEP, disk_hash)):
            entry, reasons = _find_entry(entries, out_rel, expected_tool, digest, project)
            if entry is None:
                raise ValueError(f"native {role} ledger: {'; '.join(reasons)}")
            outputs = entry.get("outputs", {})
            logged = outputs.get(out_rel)
            if logged is None:
                logged = next((v for v in outputs.values()
                               if _bare_digest(v) == _bare_digest(digest)), None)
            if _bare_digest(logged) != _bare_digest(digest):
                raise ValueError(f"native {role} ledger hash mismatch for {out_rel}")
            if entry.get("produced_by", entry.get("producing_step")) != producer:
                raise ValueError(f"native {role} ledger does not name producer {producer}")
            component = {k: entry.get(k) for k in
                         ("tool", "produced_by", "producing_step", "timestamp", "version", "outputs", "measurement")}
            component.update(role=role, disk_hash=digest, logged_hash=logged)
            result["provenance_chain"].append(component)
            meas = _mcp_measurement.from_provenance_entry(entry)
            # This typed exception requires positive measurement on BOTH
            # actual writers, even if the terminal-only mode was not asking.
            if meas.undeclared:
                pending.append(("UNMEASURED", f"native {role} ledger declares no measurement"))
            elif not meas.positive:
                raise ValueError(f"native {role} ledger is not positively measured: {meas.describe()}")
            if _tool_log_provenance.claims_witness(entry):
                ok, why = _tool_log_provenance.verify_witness(entry, project)
                if ok == _tool_log_provenance.UNCALIBRATED:
                    pending.append(("NOT_MEASURED", f"native {role} witness is uncalibrated: {why}"))
        result.update(root_tool=result["provenance_chain"][0]["tool"],
                      timestamp=entry.get("timestamp"), version=entry.get("version"))
        result["status"] = ("UNMEASURED" if any(s == "UNMEASURED" for s, _ in pending)
                            else "NOT_MEASURED" if pending else "PASS")
        result["reasons"] = [why for _, why in pending]
        return result
    except (LC.Refusal, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result["reasons"].append(str(exc))
        return result


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("project_dir")
    p.add_argument("--output", action="append", default=[],
                   help="Required output file path (relative to project). "
                        "Pair each with --tool (same index).")
    p.add_argument("--tool", action="append", default=[],
                   help="Comma-separated allowed tool names for the matching "
                        "--output at the same position.")
    p.add_argument("--require-entries", type=int, default=0,
                   help="Shortcut: pass if log has >= N exit-0 entries")
    p.add_argument("--require-measured", action="store_true",
                   help="Also require the bound run to carry positive evidence "
                        "it performed its work (the tool's own `measurement` "
                        "record). measured:false with a hard class FAILs; an "
                        "absent record is UNMEASURED — reported as INCOMPLETE "
                        "at rc 0, which is not a PASS and not a FAIL.")
    p.add_argument("--pnr-netlist", action="store_true",
                   help="Check the technology-MAPPED synthesis netlist PnR "
                        "routes (named by phase 3 step_synth's "
                        "synth_inputs.json) as the FIRST output; the first "
                        "--tool pairs with it.")
    p.add_argument("--json", help="Write JSON report to this path")
    args = p.parse_args(argv)

    project = Path(args.project_dir).resolve()
    if not project.is_dir():
        print(f"provenance_check: not a directory: {project}",
              file=sys.stderr)
        return 2

    if args.pnr_netlist:
        # The generic pre-map netlist.v is not what PnR routes; its entry in
        # provenance.jsonl says nothing about the mapped netlist (U11).
        import _path_layout as _pl  # noqa: PLC0415
        mapped = _pl.mapped_synth_netlist(project)
        if mapped is None:
            report = {"project": str(project), "verdict": "NOT_MEASURED",
                      "reason_class": "ASKED_BEFORE_PRODUCER",
                      "reason": ("no mapped synthesis netlist named by "
                                 f"{_pl.SYNTH_INPUTS_SIDECAR}: phase 3's "
                                 "step_synth has not produced the netlist "
                                 "PnR routes")}
            if args.json:
                Path(args.json).parent.mkdir(parents=True, exist_ok=True)
                Path(args.json).write_text(json.dumps(report, indent=2))
            print(f"provenance_check: NOT_MEASURED — {report['reason']}")
            return 2
        args.output.insert(0, str(mapped.relative_to(project)))

    entries = _load_log(project)

    report: Dict = {"project": str(project), "log_entries": len(entries),
                    "checks": []}

    # Coarse mode: just count exit-0 entries
    if args.require_entries > 0:
        exit0 = sum(1 for e in entries if e.get("exit_code") == 0)
        ok = exit0 >= args.require_entries
        report["require_entries"] = args.require_entries
        report["exit_zero_entries"] = exit0
        report["ok"] = ok
        if args.json:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(json.dumps(report, indent=2))
        print(f"provenance_check: {exit0} / {args.require_entries} "
              f"exit-0 entries {'OK' if ok else 'FAIL'}")
        return 0 if ok else 1

    if len(args.output) != len(args.tool):
        print("provenance_check: --output and --tool counts must match "
              f"(got {len(args.output)} outputs, {len(args.tool)} tools)",
              file=sys.stderr)
        return 2

    if not args.output:
        print("provenance_check: nothing to check; pass --output/--tool "
              "or --require-entries", file=sys.stderr)
        return 2

    # Step 9's clause names the declared GENERIC arm `<synth>/netlist.v`. The
    # netlist PnR routes is the MAPPED arm beside it; when it exists its
    # provenance is required too, under the same tools (U11).
    try:
        import _path_layout as _pl  # noqa: PLC0415
        _generic = str(_pl.synth_dir(project).relative_to(project) / "netlist.v")
        _mapped = _pl.mapped_synth_netlist(project)
    except (ImportError, ValueError, OSError):
        _generic, _mapped = None, None
    if _mapped is not None and _generic in args.output:
        _mrel = str(_mapped.relative_to(project))
        if _mrel not in args.output:
            args.output.append(_mrel)
            args.tool.append(args.tool[args.output.index(_generic)])
            report["pnr_netlist_companion"] = _mrel

    overall_ok = True
    #: Artefacts whose bound run states nothing about whether it measured. They
    #: do not fail the gate and they do not pass it either.
    unmeasured: List[str] = []
    #: Artefacts whose claimed witness the flow-log reader may not judge
    #: (instrument_calibration's contract: NOT_MEASURED, never a FAIL).
    uncalibrated: List[str] = []

    for out_rel_pattern, tools_csv in zip(args.output, args.tool):
        allowed = {t.strip() for t in tools_csv.split(",") if t.strip()}

        # Glob expansion (chip-AGNOSTIC): flow steps reference outputs by
        # pattern (e.g. `phase3/stage3/extracted/*.spef`) because the top
        # cell name is not known at manifest-authoring time. Without
        # expansion the literal `*.spef` path "exists on disk" check fails
        # and Step 22 false-FAILs on every chip whose SPEF is registered
        # under its concrete name. Expand the pattern to the actual file(s);
        # a non-glob path resolves to itself.
        if any(ch in out_rel_pattern for ch in "*?[") :
            matches = sorted(
                str(p.relative_to(project))
                for p in project.glob(out_rel_pattern)
                if p.is_file()
            )
            if not matches:
                check = {"output": out_rel_pattern, "allowed_tools": tools_csv,
                         "status": "FAIL",
                         "reasons": [f"no file on disk matches pattern: "
                                     f"{out_rel_pattern}"]}
                report["checks"].append(check)
                overall_ok = False
                continue
            out_rels = matches
        else:
            out_rels = [out_rel_pattern]

        # Check each concrete output resolved from the pattern.
        for out_rel in out_rels:
            check: Dict = {"output": out_rel, "allowed_tools": tools_csv,
                           "status": "FAIL", "reasons": []}

            # The file must exist on disk
            abs_out = project / out_rel
            if not abs_out.exists():
                check["reasons"].append(f"file on disk missing: {out_rel}")
                report["checks"].append(check)
                overall_ok = False
                continue

            disk_hash = _sha256_file(abs_out)

            if out_rel == _generic or (_mapped is not None and abs_out == _mapped):
                native = _postmap_provenance(project, entries, out_rel, allowed, disk_hash)
                if native is not None:
                    check.update(native)
                    report["checks"].append(check)
                    if check["status"] == "FAIL":
                        overall_ok = False
                    elif check["status"] == "UNMEASURED":
                        unmeasured.append(out_rel)
                    elif check["status"] == "NOT_MEASURED":
                        uncalibrated.append(out_rel)
                    continue

            # Find a matching entry
            entry, reasons = _find_entry(entries, out_rel, allowed, disk_hash,
                                          project)
            if entry is None:
                check["reasons"].extend(reasons)
                report["checks"].append(check)
                overall_ok = False
                continue

            # Hash match. The entry may declare this artefact under the path
            # the RUN wrote (`_declares` accepts a digest match), so resolve
            # the recorded hash the same way rather than assuming the
            # published path is a key — a KeyError here would turn a
            # successful match into a crash.
            logged_hash = entry["outputs"].get(out_rel)
            if logged_hash is None:
                want = _bare_digest(disk_hash)
                logged_hash = next(
                    (str(v) for v in entry["outputs"].values()
                     if _bare_digest(v) == want), disk_hash)
                check["declared_under"] = next(
                    (k for k, v in entry["outputs"].items()
                     if _bare_digest(v) == want), None)
            if _bare_digest(logged_hash) != _bare_digest(disk_hash):
                check["reasons"].append(
                    f"hash mismatch: log={logged_hash[:18]}... disk={disk_hash[:18]}..."
                    " — file modified after tool run, or log is stale"
                )
                check["logged_hash"] = logged_hash
                check["disk_hash"] = disk_hash
                report["checks"].append(check)
                overall_ok = False
                continue

            # A witness the flow-log reader may not judge. `_find_entry` bound
            # it only after every check that needs no reader held, and it is
            # reported NOT_MEASURED only AFTER the --require-measured hard
            # miss below: a run that states it measured nothing is a FAIL
            # whether or not its witness can be judged.
            unjudged = None
            if _tool_log_provenance.claims_witness(entry):
                ok, why = _tool_log_provenance.verify_witness(entry, project)
                if ok == _tool_log_provenance.UNCALIBRATED:
                    unjudged = why

            # ── THE MEASUREMENT QUESTION, asked where the binding is
            # decided. By here the artefact IS bound to an exit-0 run by this
            # tool; the only thing still unasked is whether that run did any
            # work.
            if args.require_measured:
                meas = _mcp_measurement.from_provenance_entry(entry)
                check["measurement"] = {
                    "declared": meas.declared,
                    "measured": meas.measured,
                    "not_measured_class": meas.reason_class,
                    "not_measured_reason": meas.reason,
                }
                if meas.hard_miss:
                    check["status"] = "FAIL"
                    check["reasons"].append(
                        f"the run bound to this artefact states it measured "
                        f"NOTHING [{meas.reason_class}]: {meas.reason} — the "
                        f"artefact exists and the tool exited 0, and neither "
                        f"is evidence the work was done")
                    report["checks"].append(check)
                    overall_ok = False
                    continue

            if unjudged is not None:
                check["status"] = "NOT_MEASURED"
                check["reason_class"] = "uncalibrated"
                check["reasons"].append(
                    f"the witness of the run bound to this artefact could "
                    f"not be judged: {unjudged}")
                report["checks"].append(check)
                uncalibrated.append(out_rel)
                continue

            if args.require_measured:
                if meas.undeclared:
                    # NOT a failure and NOT a pass. See the module docstring.
                    check["status"] = "UNMEASURED"
                    check["reasons"].append(
                        f"the run bound to this artefact declares no "
                        f"measurement record, so whether {entry.get('tool')} "
                        f"performed its work is not stated anywhere — "
                        f"unmeasured, which is neither measured nor failed")
                    check["tool"] = entry.get("tool")
                    check["timestamp"] = entry.get("timestamp")
                    check["version"] = entry.get("version")
                    report["checks"].append(check)
                    unmeasured.append(out_rel)
                    continue

            check["status"] = "PASS"
            check["tool"] = entry.get("tool")
            check["timestamp"] = entry.get("timestamp")
            check["version"] = entry.get("version")
            report["checks"].append(check)

    report["ok"] = overall_ok
    report["unmeasured"] = unmeasured
    report["uncalibrated"] = uncalibrated

    # Print summary
    print(f"\n=== provenance_check ({project.name}) ===")
    print(f"  log entries: {len(entries)}")
    for c in report["checks"]:
        # Three icons for three states. UNMEASURED wearing the failure mark
        # would put a thing nobody measured in the same column as a thing that
        # was measured and was wrong.
        icon = {"PASS": "✓", "UNMEASURED": "…",
                "NOT_MEASURED": "…"}.get(c["status"], "✗")
        print(f"  {icon} [{c['status']:<10}] {c['output']}")
        if c["status"] == "PASS":
            print(f"         via {c.get('tool')} @ {c.get('timestamp')}")
        else:
            for r in c["reasons"]:
                print(f"         - {r}")

    print(f"\nOverall: {'PASS' if overall_ok else 'FAIL'}")

    # THE DISCLOSURE THAT KEEPS AN UNMEASURED STEP OUT OF THE PASS COLUMN.
    # `flow_compliance_check._stdout_signals_token` reads a line beginning
    # `INCOMPLETE:` off a passing gate and dispositions the whole step into the
    # INCOMPLETE tier — counted and rendered separately, never added to
    # `pass_count`, and never a failure. That tier is exactly what "the tool
    # never said whether it did the work" means, so this prints it rather than
    # inventing a fourth vocabulary for the same fact.
    if overall_ok and uncalibrated and not unmeasured:
        print(f"  witness not judged (uncalibrated reader): "
              f"{', '.join(uncalibrated[:5])}"
              + (f" (+{len(uncalibrated) - 5} more)" if len(uncalibrated) > 5 else ""))
        print(f"INCOMPLETE: {len(uncalibrated)} of {len(report['checks'])} "
              f"artefact(s) cite a witness the flow-log reader may not judge; "
              f"NOT_MEASURED, reason_class uncalibrated.")
    if overall_ok and unmeasured:
        if uncalibrated:
            print(f"  witness not judged (uncalibrated reader): "
                  f"{', '.join(uncalibrated[:5])}")
        # DETAIL FIRST, SENTINEL LAST, AND THE SENTINEL SHORT. The consumer
        # (`flow_compliance_check.output_snippet`) keeps only the LAST 300
        # characters of stdout and then requires the token to START A LINE, so
        # a single long line carrying both would lose the token to the tail cut
        # -- the disclosure would exist and be unreadable, which is the
        # "disclosure a path length can delete" failure this repo has already
        # measured once. The names go on their own line above; the sentinel
        # line is kept short enough to survive on its own.
        print(f"  unmeasured artefacts: {', '.join(unmeasured[:5])}"
              + (f" (+{len(unmeasured) - 5} more)" if len(unmeasured) > 5 else ""))
        print(f"INCOMPLETE: {len(unmeasured)} of {len(report['checks'])} "
              f"artefact(s) are bound to a run that declares no measurement "
              f"record; whether the tool performed its work was NOT examined.")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2))

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
