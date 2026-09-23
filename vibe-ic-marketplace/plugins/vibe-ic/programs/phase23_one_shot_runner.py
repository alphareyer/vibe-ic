#!/usr/bin/env python3
"""phase23_one_shot_runner.py — Phase 2 + Phase 3 chain.

Thin orchestrator that calls design_one_shot_runner.py and then
phase3_one_shot_runner.py, aggregating their results into a unified
report.

This file replaces the legacy monolithic phase23 runner — Phase 2 logic
now lives in design_one_shot_runner.py, Phase 3 in phase3_one_shot_runner.py.
phase23 is just the chain.

Usage:
    python3 phase23_one_shot_runner.py <project_dir>
                  [--top-name chip_top]
                  [--container vibeic-eda]
                  [--max-rtl-repair-retries 3]
                  [--skip-hardware]                 # forwarded to phase 2
                  [--skip-phase3]                   # stop after Phase 2
                  [--skip-phase2]                   # only run Phase 3
                  [--die-um 1500x1500]              # forwarded to phase 3
                  [--util 0.4]                      # forwarded to phase 3
                  [--pdk auto|sky130A|<custom>]     # forwarded to phase 3

Aggregate report: <project>/reports/phase23_one_shot.json
Exit: 0 PASS / PASS_WITH_WAIVERS, 1 FAIL, 2 IO error.
"""
from __future__ import annotations

# --- sibling-import path (vibe-ic#2104) ------------------------------------
# `programs/` is a flat directory whose modules import each other by BARE
# name. Python puts a file's own directory on `sys.path` only when that file
# is run as `__main__`; under `importlib.util.spec_from_file_location` — how
# the gates, the wiring audit and much of the suite load a program — it does
# not, so every bare sibling import below raises ModuleNotFoundError. Measured
# on the base tree: 454 of the 1385 top-level programs died that way. Restore
# the condition the file is written for. Idempotent, and the same shape the
# sibling programs that already carry it use.
import os as _os                                                    # noqa: E402
import sys as _sys                                                  # noqa: E402

if _os.path.dirname(_os.path.abspath(__file__)) not in _sys.path:
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
# ---------------------------------------------------------------------------


import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict
import _path_layout as _pl
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _eda_pin as _pin  # noqa: E402 — the ONE place the pin is stated
import verdict as _V  # noqa: E402 — the ONE vocabulary and its precedence


PROGRAMS_DIR = Path(__file__).resolve().parent


def _run_phase(name: str, runner: Path, args: list[str]
               ) -> tuple[int, dict]:
    print(f"\n{'='*70}\n=== {name} → {runner.name}\n{'='*70}")
    cp = subprocess.run([sys.executable, str(runner), *args])
    return cp.returncode, {}


def _phase_verdict_tiers() -> "dict[str, tuple[str, ...]]":
    """`{chained tier: (phase verdict words that roll into it, ...)}` — THE ONE SOURCE.

    ASKED RATHER THAN COPIED: every word `verdict.run_verdict` can hand up is a key here,
    in `RUN_PRECEDENCE` order, so this table cannot fall behind the vocabulary the way a
    hand-kept list does — which is exactly how it fell behind #2568 and refused
    NOT_MEASURED as unrecognised.

    `NOT_APPLICABLE` is deliberately absent. It is in `verdict.Verdict`, but
    `run_verdict` never returns it (it contributes nothing, and a run of only N/A steps is
    NOT_MEASURED), so a tier for it would be a tier with no member.

    A MODULE-LEVEL FUNCTION AND NOT A CONSTANT, because it is also what a test has to ask:
    the invariant "every word a phase can hand up is classified here" cannot be checked
    against a local inside the aggregator, and building the table twice would reinstate the
    drift this closes.
    """
    tiers: "dict[str, tuple[str, ...]]" = {v.value: (v.value,) for v in _V.RUN_PRECEDENCE}
    # THE TWO LEGACY SPELLINGS, and they are qualified, not clean. They are stated here
    # rather than derived because `verdict.parse` does not know them: they are this
    # aggregator's own compatibility surface, not part of the vocabulary.
    #   WAIVED — a phase-2 spelling kept from the original list.
    #   PASS_WITH_OPEN_SOURCE_CONSTRAINTS — `phase3_one_shot_runner._VERDICT_RANK` and
    #   `flow_compliance_check` both rank it QUALIFIED, so it is qualified here too.
    tiers[_V.Verdict.PASS_WITH_WAIVERS.value] += (
        "WAIVED", "PASS_WITH_OPEN_SOURCE_CONSTRAINTS")
    return tiers


def _known_phase_verdicts() -> "set[str]":
    """Every phase verdict word this runner can classify. `union(_TIERS.values())`."""
    return {w for words in _phase_verdict_tiers().values() for w in words}


def _aggregate_verdict(p2: dict, p3: dict, ran_p2: bool, ran_p3: bool) -> str:
    """The chained run's verdict — the WEAKER of the two phase verdicts.

    BLOCKING: `main()` returns 0 only for PASS / PASS_WITH_WAIVERS, so the
    UNKNOWN_PHASE_VERDICT refusal below exits the runner non-zero.

    vibe-ic#2153, second instance. This function had the same catch-all as
    `phase3_one_shot_runner._aggregate_verdict`: it named FAIL, named two
    qualified words, and returned "PASS" for everything else. That is not a
    separate cosmetic defect — it is the LAUNDRY for the first one:

      * phase 3 now refuses a plan it cannot grade and returns
        `UNKNOWN_STATUS:<word>@<step>`. Read here, that word matched neither
        branch and came back out as a CLEAN PASS one level up. A refusal that
        the next aggregator turns green is not a refusal.
      * `PASS_WITH_OPEN_SOURCE_CONSTRAINTS` — a real phase-3 headline; it is
        what `_derive_headline_verdict` returns when the completion audit says
        so — was ALSO being read as a clean PASS here, though both
        `phase3_one_shot_runner._VERDICT_RANK` and
        `flow_compliance_check` rank it as QUALIFIED, at the same level as
        PASS_WITH_WAIVERS. Two places in this repo already say the word is not
        clean; this function was the third and disagreed with both. Classifying
        it with the other qualified words is agreement, not a new policy.

    The known set is DERIVED — `union(_TIERS.values())`, one source — so a
    phase verdict cannot be classified-but-unknown or known-but-unclassified.

    AND SINCE #2568 IT WAS MISSING A WORD IT COULD BE HANDED. MEASURED, not read off a
    review: `verdict.RUN_PRECEDENCE` is `[FAIL, NOT_MEASURED, PASS_WITH_WAIVERS, PASS]`
    and `verdict.run_verdict` returns exactly those four; `design_one_shot_runner.
    _aggregate_verdict` delegates to it, and `phase23_one_shot_runner.main` reads the
    phase-2 report's `verdict` straight off disk. So NOT_MEASURED is directly reachable
    -- `step_final_audit`'s R-0915-159 branch publishes a NOT_MEASURED step on a real run
    (subservient x gf180mcuD as a DIE), and with no step FAILing the phase word is
    NOT_MEASURED. This table knew three of the four words, so a phase that measured
    nothing was refused as `UNKNOWN_PHASE_VERDICT` -- loud and non-zero, so nothing was
    laundered, but it is the wrong answer: the word is not unrecognised, it is a state
    this repo has a tier for.

    CORRECTING THE REVIEW THAT FOUND THIS: it named "NOT_MEASURED/INCOMPLETE". There is
    no `INCOMPLETE` at this level -- it is not in `verdict.Verdict` at all (the five are
    PASS, PASS_WITH_WAIVERS, FAIL, NOT_MEASURED, NOT_APPLICABLE) and `verdict.parse`
    refuses it, so a phase cannot hand it up. Adding a tier for it would be a tier with no
    member, which is its own defect. One word was missing, and one is added.

    THE THREE CORE TIERS ARE NOW DERIVED FROM `verdict`, not retyped. That is the point of
    this function's own complaint about the runners' "hand-maintained lists": a fourth
    hand-kept copy drifts the same way the first three did, and this is how it drifted.
    Only the two LEGACY SPELLINGS are stated here, because `verdict.parse` does not know
    them and they are this aggregator's own compatibility surface, not the vocabulary.
    """
    _TIERS = _phase_verdict_tiers()
    _known = _known_phase_verdicts()

    verdicts: list[tuple[str, str]] = []
    if ran_p2:
        verdicts.append(("phase2", p2.get("verdict", "FAIL")))
    if ran_p3:
        verdicts.append(("phase3", p3.get("verdict", "FAIL")))

    # REFUSE BY NAME, and FIRST — the same rule, for the same reason, as the
    # phase-3 aggregator this one reads. NOT a silent PASS and NOT a silent
    # FAIL: the word says which phase produced which unrecognised verdict.
    unknown = [f"{phase}={v}" for phase, v in verdicts if v not in _known]
    if unknown:
        print(f"phase23_one_shot_runner: REFUSING to aggregate — phase "
              f"verdict(s) outside the known set {sorted(_known)}: "
              f"{', '.join(unknown)}. A phase that refused to grade itself "
              f"must not be rolled up as a pass; classify the word in "
              f"`_aggregate_verdict._TIERS` (the one source) or fix the "
              f"phase that emitted it.", file=sys.stderr)
        return "UNKNOWN_PHASE_VERDICT:" + ",".join(unknown)

    # THE PRECEDENCE IS `verdict.RUN_PRECEDENCE`, WALKED — not three hand-written ifs in
    # an order someone has to keep right. FAIL > NOT_MEASURED > PASS_WITH_WAIVERS > PASS,
    # which is what `verdict.run_verdict` applies over STEPS and what the front door's
    # roll-up applies over PHASES (`fails` -> `unmeasured` -> `notes` -> pass). A phase
    # that measured nothing is therefore not green and not a failure, and the chained word
    # cannot be greener than the weakest phase.
    _words = {v for _, v in verdicts}
    for _tier in _V.RUN_PRECEDENCE:
        if _words & set(_TIERS[_tier.value]):
            return _tier.value
    # NOT a catch-all: every word was either matched above or refused by name before it.
    # Reached only when NOTHING ran, which `main` decides separately.
    return _V.Verdict.PASS.value


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("project", type=Path)
    p.add_argument("--top-name", default="chip_top")
    p.add_argument("--container", default=_pin.default_container_name())
    p.add_argument("--max-rtl-repair-retries", type=int, default=3)
    p.add_argument("--skip-hardware", action="store_true")
    p.add_argument("--force-rtl-regen", action="store_true",
                   help="Forwarded to Phase 2: let the deterministic "
                        "generator overwrite RTL it did not produce. "
                        "DESTRUCTIVE and off by default — without it "
                        "hand-authored RTL is PRESERVED and rtl_gen WAIVEs.")
    p.add_argument("--skip-phase2", action="store_true",
                   help="Skip Phase 2 (only run Phase 3 — pre supposes "
                        "rtl/ + generated_docs/ already present)")
    p.add_argument("--skip-phase3", action="store_true",
                   help="Stop after Phase 2 (= alias of /vibe-ic-phase2)")
    p.add_argument("--die-um", default="1500x1500")
    p.add_argument("--util", type=float, default=0.4)
    p.add_argument("--pdk", default="auto")
    p.add_argument("--allow-oss-pdk-fallback", action="store_true",
                   help="Pass through to phase3: acknowledge an "
                        "open-source in-container PDK fallback even "
                        "though a commercial PDK is configured for "
                        "this host. Without it a silent OSS fallback "
                        "is REFUSED (it would emit VOID sign-off "
                        "reports).")
    p.add_argument("--detect-stable", type=int, default=0, metavar="N",
                   help="If the runner has produced the same verdict on the "
                        "previous N consecutive runs, skip the heavy "
                        "Phase 2 + 3 work and exit 0 with a stability "
                        "marker. State file: "
                        "reports/orchestrator/phase23_stable_streak.json. "
                        "0 (default) disables the early-exit; useful for "
                        "/loop close-loop monitoring where re-running an "
                        "idempotent PASS_WITH_WAIVERS pipeline burns CI "
                        "without value.")
    args = p.parse_args()

    project = args.project.resolve()
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 2

    # v1.6.52 — `--detect-stable N`: if the previous N runs all produced
    # the same verdict, skip the heavy Phase 2 + 3 pipeline and emit a
    # stability marker. The state file lives in the orchestrator dir
    # next to phase23_one_shot.json; both wakeups and human-driven
    # re-runs increment the streak.
    streak_path = (_pl.reports_orchestrator_dir(project)
                   / "phase23_stable_streak.json")
    streak_path.parent.mkdir(parents=True, exist_ok=True)
    if args.detect_stable > 0:
        prev = _read_streak(streak_path)
        if (prev.get("streak", 0) >= args.detect_stable
                and prev.get("verdict") in ("PASS", "PASS_WITH_WAIVERS")):
            print(f"\n=== --detect-stable threshold reached "
                  f"({prev['streak']} >= {args.detect_stable}) — skipping "
                  f"heavy pipeline; verdict={prev['verdict']} ===")
            print(f"=== last_run_at: {prev.get('last_run_at')} ===")
            print(f"=== streak file: {streak_path} ===")
            return 0

    t0 = time.time()
    p2_runner = PROGRAMS_DIR / "design_one_shot_runner.py"
    p3_runner = PROGRAMS_DIR / "phase3_one_shot_runner.py"
    if not p2_runner.is_file() or not p3_runner.is_file():
        print(f"ERROR: child runners missing — phase2={p2_runner.is_file()} "
              f"phase3={p3_runner.is_file()}", file=sys.stderr)
        return 2

    # ---- Phase 2 ----
    p2_summary: Dict[str, Any] = {}
    p2_rc = 0
    if not args.skip_phase2:
        p2_args = [str(project),
                   "--top-name", args.top_name,
                   "--container", args.container,
                   "--max-rtl-repair-retries", str(args.max_rtl_repair_retries)]
        if args.skip_hardware:
            p2_args.append("--skip-hardware")
        if args.force_rtl_regen:
            p2_args.append("--force-rtl-regen")
        p2_rc, _ = _run_phase("PHASE 2 (= 2a + 2b)", p2_runner, p2_args)
        p2_json = _pl.report_path(project, "phase2_one_shot.json")
        if p2_json.is_file():
            try:
                p2_summary = json.loads(p2_json.read_text())
            except Exception:
                p2_summary = {"verdict": "FAIL",
                              "error": "phase2 report parse failed"}

    # Halt before Phase 3 if Phase 2 FAILed (unless explicitly skipping P2).
    if (not args.skip_phase2
            and p2_summary.get("verdict") == "FAIL"
            and not args.skip_phase3):
        print(f"\n=== HALT — Phase 2 FAIL; not entering Phase 3 ===")
        args.skip_phase3 = True

    # ---- Phase 3 ----
    p3_summary: Dict[str, Any] = {}
    p3_rc = 0
    if not args.skip_phase3:
        p3_args = [str(project),
                   "--top-name", args.top_name,
                   "--container", args.container,
                   "--die-um", args.die_um,
                   "--util", str(args.util),
                   "--pdk", args.pdk]
        if getattr(args, "allow_oss_pdk_fallback", False):
            p3_args.append("--allow-oss-pdk-fallback")
        p3_rc, _ = _run_phase("PHASE 3 (synth → PnR → GDS → DRC → LVS)",
                              p3_runner, p3_args)
        p3_json = _pl.report_path(project, "phase3_one_shot.json")
        if p3_json.is_file():
            try:
                p3_summary = json.loads(p3_json.read_text())
            except Exception:
                p3_summary = {"verdict": "FAIL",
                              "error": "phase3 report parse failed"}

    ran_p2 = not args.skip_phase2
    ran_p3 = not args.skip_phase3
    verdict = _aggregate_verdict(p2_summary, p3_summary, ran_p2, ran_p3)
    summary = {
        "phase": "23",
        "project": str(project),
        "duration_s": time.time() - t0,
        "phase2": {"ran": ran_p2,
                   "rc": p2_rc if ran_p2 else None,
                   "verdict": p2_summary.get("verdict") if ran_p2 else "SKIPPED",
                   "report_path": (str(_pl.report_path(project, "phase2_one_shot.json"))
                                   if ran_p2 else None)},
        "phase3": {"ran": ran_p3,
                   "rc": p3_rc if ran_p3 else None,
                   "verdict": p3_summary.get("verdict") if ran_p3 else "SKIPPED",
                   "report_path": (str(_pl.report_path(project, "phase3_one_shot.json"))
                                   if ran_p3 else None)},
        "verdict": verdict,
    }
    # Per-step output view — <project>/steps/<phase>/<stage>/<id>_<slug>/.
    # The chained phase2→phase3 entry is a real front door (the /vibe-ic-phase23
    # command) and used to leave no steps tree unless the run happened to be
    # started from vibe_ic_one_shot_runner. Best-effort, non-gating; the outcome
    # lands in reports/audit/steps_view.json either way.
    summary["steps_view"] = _pl.emit_steps_view(
        project, PROGRAMS_DIR, runner="phase23_one_shot_runner")
    out = _pl.report_path(project, "phase23_one_shot.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")

    # Emit the chip-AGNOSTIC final summary (canonical 8-stage tables, cell
    # count, generic 4 mandatory outputs, waivers, self-attestation cmd,
    # link to chip-specific addendum if present). Best-effort: failure here
    # must not flip the runner's verdict.
    # #525 — go through the SHARED emit_final_summary helper, whose outer
    # cap is the child's own size-adaptive audit budget + margin; the old
    # raw fixed 240s cap here silently defeated the 900-3600s inner budget on
    # every large-SoC phase23 run.
    try:
        if not _pl.emit_final_summary(project, PROGRAMS_DIR):
            print("  [WARN] final_report_generate did not complete")
    except Exception as exc:
        print(f"  [WARN] final_report_generate failed: {exc}")

    # v1.6.52 — update the stability streak. Same verdict as last run
    # increments the streak; a different verdict resets it to 1. The
    # streak file is read by future `--detect-stable N` invocations.
    _update_streak(streak_path, verdict)

    print(f"\n{'='*70}")
    print(f"=== phase23_one_shot_runner DONE — {out}")
    print(f"  verdict          : {verdict}")
    print(f"  phase2 verdict   : {summary['phase2']['verdict']}")
    print(f"  phase3 verdict   : {summary['phase3']['verdict']}")
    print(f"  duration         : {summary['duration_s']:.1f}s")
    print(f"  final summary    : reports/final_summary.md")
    print(f"{'='*70}")
    return 0 if verdict in ("PASS", "PASS_WITH_WAIVERS") else 1


def _read_streak(streak_path: Path) -> dict:
    if not streak_path.is_file():
        return {}
    try:
        return json.loads(streak_path.read_text())
    except Exception:
        return {}


def _update_streak(streak_path: Path, verdict: str) -> None:
    prev = _read_streak(streak_path)
    streak = 1
    if prev.get("verdict") == verdict:
        streak = int(prev.get("streak", 0)) + 1
    payload = {
        "verdict": verdict,
        "streak": streak,
        "last_run_at": time.strftime(
            "%Y-%m-%dT%H:%M:%S%z", time.gmtime()),
    }
    try:
        streak_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except Exception:
        # Streak tracking is advisory; never fail the run on this.
        pass


if __name__ == "__main__":
    sys.exit(main())
