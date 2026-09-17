#!/usr/bin/env python3
"""cpu_functional_oracle_waiver_check.py — legacy-named Step-4 functional
evidence requirement.

ENFORCEMENT: blocking. A completed connectivity simulation is useful
structural evidence, but it is not a functional oracle and cannot release
Step 4. The runner still records the connectivity result verbatim
(``CONNECTIVITY_PASS``, ``functional_verified=false`` and its transcript
pointer); this gate now reports that state as ``INCOMPLETE`` and exits 1.

Program-first routing is explicit: ``professional_tb_gen`` generates and runs
the self-checking oracle first. A class whose reference semantics are not
deterministically derivable is handed to the shipped ``testbench-gen`` expert
fallback to fill the reference hook and re-run it. Missing semantics are work
to close, never a class waiver. The filename is retained for compatibility
with shipped flow definitions and old reports; it no longer grants a waiver.

Verdicts / exit codes (chip-AGNOSTIC — project artifacts only):
  0 = N/A for this gate: sim/results.xml is a genuine functional PASS (an
      oracle / non-connectivity verdict). This gate makes no claim about it;
      the existing functional gates own it.  (Also 0 when no connectivity
      marker is present at all and the verdict is a plain functional PASS.)
  2 = VACUOUS: no sim/results.xml at all — nothing for this gate to assess
      (the absence is the standard missing-file FAIL the files_exist gate
      reports; this gate stays out of the way).
  1 = INCOMPLETE when a substantiated connectivity-only run exists without a
      real professional/oracle result; FAIL when the bridge evidence is forged
      or broken. Both are blocking and neither is a waiver.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _path_layout as _pl  # noqa: E402
import _sim_results_bridge as _srb  # noqa: E402
import _l10_execution as _l10x  # R-0915-87(2): the ONE execution reader  # noqa: E402

# The capability-gap token retained on a connectivity-PASS evidence record.
# A chip-AGNOSTIC capability identifier, NOT a chip/vendor/SKU literal.
CAP_CPU_FUNCTIONAL_ORACLE = "cap:cpu_functional_oracle"
CONNECTIVITY_VERDICT = "CONNECTIVITY_PASS"


def _read_xml_field(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.IGNORECASE | re.DOTALL)
    return (m.group(1).strip() if m else "")


def _waiver_track_class_label(xml: str) -> str:
    """ORGANIC #779 — derive the human-readable '<track> no-oracle <ic_class>
    class' label from the STRUCTURED results.xml, instead of the hardcoded
    'generic_full_stack no-oracle CPU/SoC class' literal that MISLABELS non-CPU
    classes. After #745 made arith_oracle_tb_gen DEFER for serial-parallel
    multipliers, `digital_arithmetic_primitive` ICs route into this same #654
    gate — and the hardcoded 'CPU/SoC' string then mis-described them, even
    though every structured field (verdict / capability_gap /
    functional_verified / waiver_reason) was already correct. The helper name
    and legacy XML field are retained for artifact compatibility.

    The connectivity bridge writes <verification_track> directly (e.g.
    'generic_full_stack') and embeds the real ic_class in <waiver_reason> as a
    `class '<name>'` token (from _class_uses_aid_reference_tb). Reads a dedicated
    <ic_class> tag first if a future schema adds one; falls back to a generic
    label when a field is absent (older artifact). chip-AGNOSTIC — reads the
    class from structured output, never a chip/vendor/SKU literal."""
    track = _read_xml_field(xml, "verification_track") or "generic_full_stack"
    ic_class = _read_xml_field(xml, "ic_class")
    if not ic_class:
        m = re.search(r"\bclass\s+'([A-Za-z0-9_]+)'",
                      _read_xml_field(xml, "waiver_reason"))
        ic_class = m.group(1) if m else ""
    if ic_class:
        return f"{track} no-oracle {ic_class} class"
    return f"{track} no-oracle class"


# A PROCESS MILESTONE IS NOT AN EXECUTABLE TEST.
#
# MEASURED, opentitan_aes at v1.15.80: this gate reported "103 declared L10
# row(s), 0 functional tests ran" and blocked Step 4. All 103 rows carried
# `kind: "verification_checklist"`, harvested by Phase 1 from the vendor's DV
# CHECKLIST — rows named `spec_complete`, `csr_defined`, `clkrst_connected`,
# whose stimulus is the literal string "DV checklist item SPEC_COMPLETE — Done"
# and whose expected value is "DV checklist item satisfied (Done)".
#
# Nothing can drive those. There is no stimulus and no expected VALUE in any
# circuit sense; they record that a project reached a milestone. The unit-TB
# producer was RIGHT to place 0 of 103 in its scaffold scope — the defect was
# never that the tests do not run, it is that a project-management checklist
# was counted as a test-case population, so the gate demanded execution of 103
# things that can never be executed and reported a shortfall that was
# arithmetic, not evidence.
#
# They are still REPORTED, under their own key, so a reader sees what the input
# declared and why it is not in the executable denominator. This NARROWS a
# blocking denominator, so the controls below hide a real functional row inside
# a checklist-dominated L10 and prove it is still counted and still demanded.
#
# chip-AGNOSTIC: a declared `kind`, not a chip, vendor or document literal.
_NON_EXECUTABLE_TEST_KINDS = frozenset({"verification_checklist"})


# ORGANIC #2055 — NAME THE ROW KIND THE STEP COULD NOT RUN.
#
# MEASURED, u_hawaii_adc at v1.17.83 (lane czadc28, front door, image 0.3.46):
# this gate's blocking sentence read "0 functional tests ran for 4 declared
# L10/L12 row(s). Connectivity is not a functional oracle." Every one of those
# four rows was `kind: verification_intent` — analog acceptance prose harvested
# from the input's `## Verification intent` list — and the TB producer had
# already said so in its own SKIP ("0 in scope, 4 out of scope"). The sentence
# named neither fact, so three separate lanes read the wall as a defect in the
# RTL and re-authored a testbench that was never the missing piece.
#
# The numerator and the denominator are both kept exactly as they were; this
# adds the two facts a reader needs to act: WHAT KIND the declared rows are,
# and HOW MANY of them any producer in the flow is scoped to author. The
# producer's scope is IMPORTED from `testbench_gen`, which owns it, rather than
# respelled here — the #761 two-private-scopes shape, refused a third time.
#
# chip-AGNOSTIC: a declared `kind` vocabulary, never a chip/vendor/SKU literal.
def _row_kind_disclosure(project: Path) -> str:
    """`" [verification_intent 2]; 0 of 2 authorable ..."` for the blocking
    sentence, or `""` when the rows or the producer scope cannot be read.

    "Could not read it" is not "read it and it was empty": on any failure this
    returns the empty string and the sentence keeps exactly the wording it had
    before, rather than asserting a breakdown nobody measured.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import testbench_gen as _tb
    except Exception:
        return ""
    rows: list = []
    gd = _pl.generated_docs_dir(project)
    rows.extend(_declared_rows(
        gd / "L10_TEST_CASES.json", ("test_cases", "cases", "vectors")))
    rows.extend(_declared_rows(
        gd / "L12_BEHAVIORAL_SEQUENCES.json",
        ("sequences", "behavioral_sequences")))
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return ""
    # ORGANIC #2064 — THE SCAFFOLD SCOPE IS NOT THE FLOW'S SCOPE ANY MORE.
    #
    # This sentence ended "the rest carry no stimulus ANY PRODUCER IN THE FLOW
    # is scoped to drive", and computed that claim by summing over ONE
    # producer's `SCAFFOLD_KINDS`. That was true while `testbench_gen` was the
    # only producer, and false the moment `analog_acceptance_tb_gen` began
    # authoring an executable acceptance for `verification_intent` rows.
    # MEASURED on u_hawaii_adc with that producer on the tree: this sentence
    # printed "0 of 4" while the flow could author 2 of the 4.
    #
    # The union comes from `testbench_gen.flow_authorable`, the ONE accessor
    # both readers share — the #761 two-private-scopes shape, refused a fourth
    # time. `analog_acceptance` is ABSENT (not zero) from that dict when that
    # producer could not be asked, and the sentence says so rather than
    # reporting a 0 nobody measured.
    try:
        hist = _tb.kind_histogram(rows)
        scope = _tb.SCAFFOLD_KINDS
        flow = _tb.flow_authorable(project, rows)
        authorable = int(flow["authorable"])
    except Exception:
        return ""
    kinds = ", ".join(f"{k} {v}" for k, v in hist.items()) or "(none)"
    by = [f"{flow['scaffold']} inside the TB producer's scaffold scope "
          f"{{{', '.join(sorted(scope))}}}"]
    if "analog_acceptance" in flow:
        by.append(f"{flow['analog_acceptance']} by analog_acceptance_tb_gen")
    else:
        by.append("the analog-acceptance producer could NOT be asked, so its "
                  "share is unmeasured, not zero")
    left = flow.get("unauthorable_kinds") or {}
    return (f" [{kinds}]; {authorable} of {len(rows)} row(s) are authorable by "
            f"some producer in the flow (" + "; ".join(by) + ") — the "
            f"remaining {left or '(none)'} carry no stimulus any producer is "
            f"scoped to drive, so that part of the shortfall is NOT a "
            f"statement about the RTL")


def _row_kind_denominator(project: Path) -> dict:
    """The machine-readable half of `_row_kind_disclosure`.

    Returns ``{}`` — the keys ABSENT, not zeroed — when the rows or the
    producer scope could not be read, so a consumer can tell "nothing was
    declared" from "nothing could be measured"."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import testbench_gen as _tb
    except Exception:
        return {}
    gd = _pl.generated_docs_dir(project)
    rows = [r for r in (
        _declared_rows(gd / "L10_TEST_CASES.json",
                       ("test_cases", "cases", "vectors"))
        + _declared_rows(gd / "L12_BEHAVIORAL_SEQUENCES.json",
                         ("sequences", "behavioral_sequences")))
        if isinstance(r, dict)]
    try:
        flow = _tb.flow_authorable(project, rows)
        out = {
            "declared_row_kinds": _tb.kind_histogram(rows),
            "rows_inside_tb_producer_scaffold_scope": sum(
                1 for r in rows if _tb.case_kind(r) in _tb.SCAFFOLD_KINDS),
            "tb_producer_scaffold_scope": sorted(_tb.SCAFFOLD_KINDS),
            # ORGANIC #2064 — the FLOW's answer beside this one producer's.
            # The scaffold key above keeps meaning exactly what its name says.
            "rows_authorable_by_any_producer": int(flow["authorable"]),
            "rows_not_authorable_by_any_producer": flow.get(
                "unauthorable_kinds") or {},
        }
        if "analog_acceptance" in flow:
            out["rows_authorable_by_analog_acceptance"] = int(
                flow["analog_acceptance"])
        return out
    except Exception:
        return {}


def _split_executable(rows: list) -> "tuple[list, list]":
    """(executable, process_only) over declared test rows."""
    executable, process_only = [], []
    for row in rows:
        kind = (row.get("kind") or row.get("type") or "") if isinstance(
            row, dict) else ""
        (process_only if str(kind).strip().lower()
         in _NON_EXECUTABLE_TEST_KINDS else executable).append(row)
    return executable, process_only


def _declared_rows(path: Path, keys: "tuple[str, ...]") -> list:
    """The declared list itself, without inventing one on bad input."""
    try:
        obj = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return []
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    fields = obj.get("fields") if isinstance(obj.get("fields"), dict) else obj
    for key in keys:
        value = fields.get(key)
        if isinstance(value, list):
            return value
    return []


def _list_denominator(path: Path, keys: "tuple[str, ...]") -> int:
    """Count the EXECUTABLE declared rows. See `_NON_EXECUTABLE_TEST_KINDS`."""
    executable, _process = _split_executable(_declared_rows(path, keys))
    return len(executable)


def _process_only_count(path: Path, keys: "tuple[str, ...]") -> int:
    """How many declared rows were excluded as process milestones."""
    _executable, process = _split_executable(_declared_rows(path, keys))
    return len(process)



# ── R-0915-87(2): a green ROW COUNT is not an EXECUTED ORACLE ───────────────
def _declared_l10_case_ids(project: Path) -> "list[str]":
    """Every declared EXECUTABLE L10 case id, in declaration order.

    THE SAME SCOPE THIS GATE ALREADY LANDED, APPLIED AT THE NEW DOOR. The
    executed-oracle guard (R-0915-87(2), 5fc0a5593) asked over EVERY declared
    row, and so re-opened the failure `_split_executable` above was written to
    close. MEASURED, opentitan_aes run_aes_h (e322992a6, 2026-09-18):

        only 8 of 111 declared L10 case(s) EXECUTED their own oracle ...
        Not executed: spec_complete [NOT_EXECUTED], csr_defined [NOT_EXECUTED],
        clkrst_connected [NOT_EXECUTED], ip_top, ip_instantiable,
        physical_macros_defined_80 (+97 more)

    L10 there declares 111 rows: 8 `known_answer_vector` (all 8 EXECUTED, all
    PASS) and 103 `verification_checklist` rows harvested from the vendor's DV
    CHECKLIST, whose stimulus is "DV checklist item SPEC_COMPLETE -- Done".
    Nothing can execute an oracle for a process milestone, so the guard
    demanded 103 things that can never happen, and every executable case had
    in fact run. Those rows are still declared and still REPORTED by
    `_evidence_summary` under their own key; they are only kept out of the
    executed-versus-declared comparison, exactly as the denominator path
    already keeps them out. A functional row hidden among checklist rows is
    still counted and still demanded -- the controls pin that.
    """
    gd = _pl.generated_docs_dir(project)
    out = []
    rows, _process_only = _split_executable(_declared_rows(
        gd / "L10_TEST_CASES.json", ("test_cases", "cases", "vectors")))
    for row in rows:
        if isinstance(row, dict):
            name = row.get("name") or row.get("id") or row.get("case")
            if name:
                out.append(str(name))
    return out


def _oracles_that_actually_ran(project: Path) -> dict:
    """Which declared L10 cases EXECUTED their oracle, by name.

    THE FALSE GREEN THIS CLOSES. MEASURED on the subservient tapeout run r27
    (lane icsub2, 2026-09-16), this gate returned

        PASS: the record's functional_verified=true is SUBSTANTIATED by
        .../l10_unit_tb/results.xml: tests=10 passed=10 failures=0 errors=0

    while NINE of those ten cases never executed an oracle at all. The nine
    testbenches exist and are green, but each is the SUBSTANCE FLOOR the
    generator writes when no oracle is derivable: it asserts only that no
    output stays X/Z after reset, and says so in machine-readable form with
    `VIBEIC_TB_ORACLE: NONE (substance floor only)`. Ten scaffolds that check
    almost nothing produce ten green JUnit rows, and a row count reads them as
    ten verified functions. The single real oracle was
    `reset_n_cycle_instruction` (reset-to-first-bus-activity latency). A CPU
    whose instruction set was never exercised stood one gate away from a
    published PASS.

    So the JUnit predicate stops being SUFFICIENT. It stays NECESSARY — a
    failing, vacuous or unresolvable transcript is refused exactly where it
    always was — and this adds the question the transcript cannot answer: did
    the declared case's own oracle RUN?

    ASKED THROUGH THE READER THAT ALREADY OWNS IT. `_l10_execution` is the same
    module `professional_tb_check` and `l10_tb_conformance_check` consult, and
    it is fail-closed by construction: a missing record, an unreadable one, a
    schema mismatch, a case absent from the record, or a row whose verdict is
    not backed by `sim_executed=true` are each NOT_EXECUTED, never a silent
    pass. A second reader here would be a second answer waiting to disagree
    with theirs.
    """
    declared = _declared_l10_case_ids(project)
    record = _l10x.load_record(project)
    executed: list = []
    not_executed: list = []
    for case_id in declared:
        state, why = _l10x.case_state(case_id, record)
        if state == _l10x.PASS:
            executed.append(case_id)
        else:
            not_executed.append({"case": case_id, "state": state, "why": why})
    return {
        "declared": declared,
        "declared_count": len(declared),
        "executed": executed,
        "executed_count": len(executed),
        "not_executed": not_executed,
        "not_executed_count": len(not_executed),
        "record_available": bool(record.get("available")),
        "record_reason": record.get("reason"),
        "asked_through": "_l10_execution.case_state",
    }



def _oracle_execution_refusal(project: Path, transcript: str) -> "str | None":
    """The refusal a row-count PASS owes, or None when the oracles did run.

    ONE GUARD, EVERY PASS ROUTE THAT RESTS ON A TRANSCRIPT. There are two —
    the substantiated-claim route and the professional-TB slot route — and on
    r27 BOTH would have passed off the same ten green rows. A predicate applied
    at one of two doors is not a predicate.
    """
    ran = _oracles_that_actually_ran(project)
    if ran["declared_count"] == 0:
        # NARROW ON PURPOSE. A design that declares no L10 case asks a
        # different question, and this guard does not answer it: the gate's own
        # denominator path already reports "0 functional tests ran for N
        # declared row(s)". Refusing here as well was scope I added and the
        # ruling did not ask for, and it turned four landed fixtures red for a
        # fact none of them is about. The comparison this guard owns is
        # EXECUTED-versus-DECLARED, and over an empty declaration there is
        # nothing to compare.
        return None
    if not ran["not_executed_count"]:
        return None
    named = ", ".join(f"{r['case']} [{r['state']}]"
                      for r in ran["not_executed"][:6])
    more = ran["not_executed_count"] - 6
    return (
        f"{transcript} is a passing transcript, but only "
        f"{ran['executed_count']} of {ran['declared_count']} declared L10 "
        f"case(s) EXECUTED their own oracle. A green row count over "
        f"substance-floor scaffolds is not functional verification. Not "
        f"executed: {named}" + (f" (+{more} more)" if more > 0 else "")
        + f". Asked through {ran['asked_through']}"
        + ("" if ran["record_available"]
           else f"; execution record unavailable ({ran['record_reason']})")
    )


def _evidence_summary(project: Path) -> dict:
    """Machine-readable Step-4 denominator and coverage disclosure.

    These fields do not replace the dedicated L10/L12 and Verilator gates.
    They put the numbers beside this gate's functional verdict so a reader can
    see, in one record, whether the run checked anything and whether coverage
    was actually measured.
    """
    gd = _pl.generated_docs_dir(project)
    l10 = _list_denominator(
        gd / "L10_TEST_CASES.json", ("test_cases", "cases", "vectors"))
    l12 = _list_denominator(
        gd / "L12_BEHAVIORAL_SEQUENCES.json",
        ("sequences", "behavioral_sequences"))
    # #2073 — ONE reader of the professional slot, and it is the UNION. This
    # loop already summed every sibling suite; what it could not see is a suite
    # that produced NO transcript, which is now disclosed by name below.
    union = _srb.professional_tb_union(project)

    sim_results = _pl.sim_dir(project) / "results.xml"
    xml = ""
    try:
        if sim_results.is_file():
            xml = sim_results.read_text(errors="replace")
    except OSError:
        xml = ""
    try:
        vectors_total = int(_read_xml_field(xml, "vectors_total") or 0)
        vectors_passed = int(_read_xml_field(xml, "vectors_passed") or 0)
    except ValueError:
        vectors_total = vectors_passed = 0

    functional = {
        "source": None,
        "tests_run": 0,
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_skipped": 0,
        # #2073 — a sibling suite that produced no parsable JUnit is NOT
        # measured, and a denominator that omits it reads as though the suite
        # did not exist. Named here so the record carries the whole population.
        "not_measured": list(union["not_measured"]),
    }
    if union["rel_paths"]:
        functional.update({
            "source": ";".join(union["rel_paths"]),
            "tests_run": union["tests"],
            "tests_passed": union["passed"],
            "tests_failed": union["failures"] + union["errors"],
            "tests_skipped": union["skipped"],
        })
    elif vectors_total > 0:
        functional.update({
            "source": str(sim_results.relative_to(project)),
            "tests_run": vectors_total,
            "tests_passed": vectors_passed,
            "tests_failed": max(vectors_total - vectors_passed, 0),
        })

    coverage_path = _pl.report_path(project, "coverage/coverage_verilator.json")
    try:
        coverage_source = str(coverage_path.relative_to(project))
    except ValueError:
        coverage_source = str(coverage_path)
    coverage = {"measured": False, "source": coverage_source}
    try:
        cov = json.loads(coverage_path.read_text(errors="replace"))
        totals = cov.get("totals") if isinstance(cov, dict) else None
        if isinstance(totals, dict) and totals:
            coverage.update(measured=True, totals=totals)
    except (OSError, ValueError):
        pass

    return {
        "declared_denominator": {
            "l10_test_cases": l10,
            "l10_process_only_rows_excluded": _process_only_count(
                gd / "L10_TEST_CASES.json", ("test_cases", "cases", "vectors")),
            "l10_process_only_note": (
                "rows whose declared kind is a process milestone "
                f"({', '.join(sorted(_NON_EXECUTABLE_TEST_KINDS))}) are "
                "reported but not demanded: they carry no stimulus a "
                "testbench can drive"),
            "l12_behavioral_sequences": l12,
            "total_declared_rows": l10 + l12,
            # ORGANIC #2055 — the same two facts the blocking sentence now
            # carries, in machine-readable form. Absent (not zero) when the
            # rows or the producer scope could not be read.
            **_row_kind_denominator(project),
        },
        "functional_test_denominator": functional,
        "coverage": coverage,
        "program_first": "professional_tb_gen",
        "expert_fallback": "testbench-gen",
    }


def _evaluate(project: Path) -> "tuple[int, str]":
    """Return (exit_code, message)."""
    sim_dir = _pl.sim_dir(project)
    results = sim_dir / "results.xml"
    if not results.is_file():
        return 2, ("VACUOUS_PASS: no phase2/stage1/sim/results.xml — "
                   "cpu_functional_oracle_waiver_check has nothing to assess.")
    try:
        xml = results.read_text(errors="replace")
    except OSError as exc:
        return 1, f"FAIL: could not read sim/results.xml: {exc}"

    verdict = _read_xml_field(xml, "verdict").upper().replace("_", "-")
    cap = _read_xml_field(xml, "capability_gap")
    func_verified = _read_xml_field(xml, "functional_verified").lower()

    is_connectivity = (
        verdict == CONNECTIVITY_VERDICT.replace("_", "-")
        or cap == CAP_CPU_FUNCTIONAL_ORACLE)
    if not is_connectivity:
        # A genuine functional PASS (oracle bridge) or any non-connectivity
        # verdict — not this gate's concern. The functional gates own it.
        return 0, ("PASS: sim/results.xml is not a cpu-functional-oracle "
                   "connectivity record (functional verdict owned by the "
                   "functional gates); cpu_functional_oracle_waiver_check N/A.")

    # It claims a connectivity-PASS capability-gap record. Substantiate it:
    #   (1) the capability-gap marker must be the cpu-functional-oracle one,
    #   (2) functional_verified must NOT be asserted true (a connectivity
    #       record that ALSO claims functional verification is a forgery),
    #   (3) the <evidence> pointer must dereference to a non-empty transcript
    #       that actually reached FULL_STACK_TB_DONE.
    if cap != CAP_CPU_FUNCTIONAL_ORACLE:
        return 1, (f"FAIL: connectivity verdict but capability_gap is "
                   f"{cap!r}, not {CAP_CPU_FUNCTIONAL_ORACLE!r} — "
                   f"unrecognised capability record.")
    if func_verified == "true":
        # A record may CLAIM functional verification only if it can SHOW it.
        # Before this, the claim alone was a forgery by construction, which was
        # right while no record could carry evidence — and wrong once one
        # could. `step_reference_tb`'s bridge now writes
        # `functional_verified=true` ONLY beside a `<functional_evidence>`
        # pointer to the professional cocotb transcript that closed the very
        # deferral this record's waiver_reason names, and that transcript is
        # judged by the SAME predicate the PASS branch below already applies:
        # a real JUnit document, tests > 0, failures == errors == 0.
        #
        # An unsubstantiated claim is still a forgery and still FAILs, with the
        # reason naming which half was missing — so a hand-edited flag, a
        # dangling pointer, a non-JUnit file, a vacuous zero-test result and a
        # failing one are each refused, exactly as before.
        claimed = _read_xml_field(xml, "functional_evidence")
        shown = _srb.substantiated_functional_evidence(project, claimed)
        if not shown:
            return 1, (
                "FAIL: connectivity-PASS record asserts "
                "functional_verified=true "
                + (f"and points at {claimed!r}, which does not resolve to a "
                   f"real passing JUnit transcript under this project"
                   if claimed else
                   "and carries no <functional_evidence> pointer")
                + " — a claim that cannot be shown is a forged waiver.")
        # R-0915-87(2) — the transcript proves a suite ran and did not fail;
        # it cannot prove WHAT it checked. On r27 this very transcript read
        # tests=10 passed=10 over NINE substance-floor scaffolds.
        _refusal = _oracle_execution_refusal(project, shown["rel_path"])
        if _refusal:
            return 1, (
                "FAIL: connectivity-PASS record asserts "
                f"functional_verified=true and {_refusal}.")
        return 0, (
            "PASS: the record's functional_verified=true is SUBSTANTIATED by "
            f"{shown['rel_path']}: tests={shown['tests']} "
            f"passed={shown['passed']} failures={shown['failures']} "
            f"errors={shown['errors']}, AND every declared L10 case executed "
            "its own oracle. The connectivity binding and the "
            "functional oracle are both recorded, and the "
            f"{CAP_CPU_FUNCTIONAL_ORACLE} marker is retained for the "
            "per-case oracle gap it actually names.")
    evidence = _read_xml_field(xml, "evidence")
    if not evidence:
        return 1, ("FAIL: connectivity-PASS record carries no <evidence> "
                   "pointer — unreviewable evidence is not credited.")
    ev_path = (project / evidence)
    if not ev_path.is_file() or ev_path.stat().st_size == 0:
        return 1, (f"FAIL: connectivity-PASS record <evidence> pointer "
                   f"{evidence!r} does not dereference to a non-empty "
                   f"transcript — connectivity evidence broken.")
    try:
        ev_txt = ev_path.read_text(errors="replace")
    except OSError as exc:
        return 1, f"FAIL: could not read evidence transcript: {exc}"
    if "FULL_STACK_TB_DONE" not in ev_txt:
        return 1, ("FAIL: connectivity-PASS record evidence transcript did "
                   "not reach FULL_STACK_TB_DONE — connectivity binding to "
                   "rtl/ was NOT actually demonstrated.")

    # A real functional PASS closes the connectivity-only incomplete state.
    # The bridge above honestly says functional verification was not performed
    # because the AID reference TB cannot bind this interface family. For a
    # class whose oracle is derivable, professional_tb_gen may have already
    # closed functional verification with a real cocotb streaming-scoreboard
    # against the real rtl/ and failures=0. When that real PASS is present,
    # step aside (rc=0). This remains chip-AGNOSTIC and anti-fabrication-safe:
    # a missing, failing, or vacuous professional result returns None and the
    # blocking INCOMPLETE verdict below remains in force.
    union = _srb.professional_tb_union(project)
    pro = _srb.find_professional_tb_pass(project)
    if pro:
        _nm = _srb.union_disclosure(union)
        # R-0915-87(2) — the same guard, at the same height. This route reads
        # the professional suite's own transcript, which on r27 is the file
        # with the ten green rows.
        _refusal = _oracle_execution_refusal(project, ";".join(pro["rel_paths"]))
        if _refusal:
            return 1, (
                "INCOMPLETE: the professional-TB result slot cannot supersede "
                f"the {CAP_CPU_FUNCTIONAL_ORACLE} capability record — "
                f"{_refusal}. No waiver is granted and Step 4 is NOT a "
                "functional PASS.")
        return 0, (
            "PASS: functional verification ACHIEVED by the professional-TB "
            "result slot (producer: "
            + (", ".join(pro.get("suite_names") or []) or "unnamed suite")
            + f") — {';'.join(pro['rel_paths'])}: tests="
            f"{pro['tests']} passed={pro['passed']} failures={pro['failures']} "
            f"errors={pro['errors']} over {len(pro['rel_paths'])} sibling "
            f"suite(s). The connectivity-PASS capability record "
            f"({CAP_CPU_FUNCTIONAL_ORACLE}) is "
            "SUPERSEDED by this real functional PASS; Step 4 is a genuine "
            "functional simulation PASS, not WAIVED-DEFERRED."
            + (f" [{_nm}]" if _nm else ""))

    denom = _evidence_summary(project)["declared_denominator"]
    # A functional transcript that EXISTS and did not pass is not the same fact
    # as no transcript at all, and this sentence used to report both as
    # "0 functional tests ran". Name the executed population when there is one:
    # the reader must be able to tell "nobody ran the testbenches" from "the
    # testbenches ran and the design failed them".
    # #2073 — NAME EVERY FAILURE, over the union. This used to report
    # `sorted(...)[0]` — the first transcript with tests > 0 — as "the"
    # transcript that did not pass. With sibling suites that is a coin toss:
    # when the PASSING suite sorts first the sentence named a green transcript
    # and said it did NOT pass, which is a lie about the file it cites.
    _nm = _srb.union_disclosure(union)
    if union["tests"] > 0:
        return 1, (
            f"INCOMPLETE: {_waiver_track_class_label(xml)} — connectivity-only "
            f"evidence reached FULL_STACK_TB_DONE (evidence: {evidence}), and "
            f"a functional transcript EXISTS but did NOT pass, over the "
            f"union of {len(union['rel_paths'])} sibling suite(s): "
            f"tests={union['tests']} passed={union['passed']} "
            f"failures={union['failures']} errors={union['errors']} "
            f"(errors = testbenches that never ran) for "
            f"{denom['total_declared_rows']} declared L10/L12 row(s)"
            f"{_row_kind_disclosure(project)}"
            + (f"; failing: {'; '.join(union['failing'])}"
               if union["failing"] else "")
            + (f"; {_nm}" if _nm else "")
            + ". No waiver is granted.")
    return 1, (
        f"INCOMPLETE: {_waiver_track_class_label(xml)} — connectivity-only "
        f"evidence reached FULL_STACK_TB_DONE (evidence: {evidence}), but 0 "
        f"functional tests ran for {denom['total_declared_rows']} declared "
        f"L10/L12 row(s){_row_kind_disclosure(project)}. Connectivity is not "
        "a functional oracle. Run the "
        "program-first professional_tb_gen route, then fill unsupported "
        "design-specific reference semantics with the testbench-gen expert "
        "fallback and re-run Step 4. No waiver is granted."
        + (f" [{_nm}]" if _nm else ""))


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        description="Blocking Step-4 functional-evidence requirement for "
                    "connectivity-only results (ORGANIC #654/#1975).")
    ap.add_argument("project", nargs="?", default=".",
                    help="project root (default: .)")
    ap.add_argument("--json", default=None,
                    help="optional path to write the JSON verdict report")
    ns = ap.parse_args(argv)
    project = Path(ns.project).resolve()

    code, msg = _evaluate(project)
    print(msg)
    if ns.json:
        verdict = ({0: "PASS", 2: "VACUOUS_PASS"}.get(code)
                   or ("INCOMPLETE" if msg.startswith("INCOMPLETE:")
                       else "FAIL"))
        out = {"verdict": verdict, "exit_code": code, "message": msg,
               "capability_gap": CAP_CPU_FUNCTIONAL_ORACLE,
               "gate": "cpu_functional_oracle_waiver_check",
               "enforcement": "BLOCKING",
               **_evidence_summary(project)}
        try:
            jp = Path(ns.json)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps(out, indent=2))
        except OSError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
