#!/usr/bin/env python3
"""#2109 — a PSM solver error must not reach step 24 as an honest-looking SKIP.

`dynamic_ir_vectored_emit` writes the SAME structural flag
`dynamic_ir_report_emitted:false` for two different worlds:

  * "there was nothing to analyse"  — SKIPPED_MISSING_INPUTS / SKIPPED_NO_PDN,
    a legitimate skip: the dynamic tier is not in scope and the STATIC IR
    sign-off stands;
  * "the analysis ran and the tool could not answer" — ERROR_NO_PSM_IR (PSM
    solved nothing: grid disconnected / no valid resistance map / solver error)
    and ERROR_TOOL (openroad wedged or unlaunchable).

Only the `status` separates them. `dynamic_ir_drop_check._is_honest_skip`
accepted the structural flag ON ITS OWN, so the second world was laundered into
`SKIPPED_CONDITION` at rc 0 — and step 24 declares that clause blocking:

    gate.all_of:
      - program_exit_zero: "dynamic_ir_drop_check reports/phase3/dynamic_ir.json ..."

so a tool failure passed the gate. That is the vacuous pass §4.05 forbids:
*could not measure it* reaching the reader as *measured and fine*. The static IR
number cannot stand in — it is a different sign-off dimension.

The guard here is DERIVED FROM THE EMITTER'S OWN TREE rather than hand-listed:
every status the emitter can write next to `dynamic_ir_report_emitted:false` is
read out of its AST and must be classified. A new `ERROR_*` the emitter grows
tomorrow is therefore blocking by default instead of silently laundered.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import dynamic_ir_drop_check as G                 # noqa: E402
import dynamic_ir_vectored_emit as E              # noqa: E402

# The artefact the issue was filed from, verbatim in shape.
_REAL_ARTEFACT = {
    "signoff_dimension": "dynamic_transient_ir_drop",
    "analysis_mode": "transient_psm",
    "status": "ERROR_NO_PSM_IR",
    "dynamic_ir_report_emitted": False,
    "reason": ("PSM produced no 'Worst dynamic IR drop' line (grid "
               "disconnected / no valid resistance map / solver error)"),
    "period_ns": 20.0,
    "period_source": "sdc_create_clock",
}


def _write(tmp_path: Path, payload: dict) -> Path:
    j = tmp_path / "dynamic_ir.json"
    j.write_text(json.dumps(payload, indent=2))
    return j


# ── the defect, on the real artefact ────────────────────────────────────────

def test_the_real_solver_error_artefact_blocks(tmp_path):
    """On main this printed SKIPPED_CONDITION at rc 0."""
    j = _write(tmp_path, _REAL_ARTEFACT)
    res = G.check(j, None, 10.0)
    assert res["verdict"] == "TOOL_ERROR", res
    assert res["status"] == "ERROR_NO_PSM_IR", res
    assert G.main([str(j), "--budget-pct", "10"]) == 1


def test_the_tools_own_line_is_in_the_record(tmp_path):
    """A tool error is a verdict class WITH the tool's line — not a bare word.
    The reader must be able to see that PSM is what failed, and why."""
    j = _write(tmp_path, _REAL_ARTEFACT)
    res = G.check(j, None, 10.0)
    # A MEMBERSHIP comparison, not a bare `in`: the pre-fix tree DOES carry the
    # PSM line (in `detail`, as the skip reason), so it returns a real partial
    # list here rather than an empty one — the difference is that it never says
    # the analysis was attempted and failed.
    want = ["Worst dynamic IR drop", "ATTEMPTED", "not a skip"]
    haystack = res.get("tool_line", "") + " " + res.get("detail", "")
    assert [w for w in want if w in haystack] == want, res
    assert "Worst dynamic IR drop" in res.get("tool_line", ""), res


def test_a_log_tail_is_carried_when_the_emitter_recorded_one(tmp_path):
    """`ERROR_NO_PSM_IR` payloads carry `log_tail`; the tool's last line is the
    most useful thing a reader can be handed, so it survives into the record."""
    payload = dict(_REAL_ARTEFACT,
                   log_tail="[INFO] PSM-0001 reading grid\n[ERROR] PSM solve failed")
    res = G.check(_write(tmp_path, payload), None, 10.0)
    # Both fields together, so the pre-fix arm fails on a CONCRETE pair
    # (['SKIPPED_CONDITION', None]) rather than on a bare None sentinel.
    assert [res.get("verdict"), res.get("tool_log_tail_line")] == \
        ["TOOL_ERROR", "[ERROR] PSM solve failed"], res


def test_rc_is_one_not_two(tmp_path):
    """rc 2 is this flow's VACUOUS_PASS tier (`flow_compliance_check` credits it
    and marks the step DONE), so a tool error routed to rc 2 would be laundered
    a second time, one layer up."""
    assert G.main([str(_write(tmp_path, _REAL_ARTEFACT))]) == 1


# ── the honest skips must NOT have been swept up (over-correction guard) ────

@pytest.mark.parametrize("payload,label", [
    (E.skip_result("missing required input(s) --liberty"), "missing-inputs"),
    (E.skip_result("DEF has no SPECIALNETS power grid", status="SKIPPED_NO_PDN"),
     "no-PDN"),
])
def test_a_genuine_skip_is_still_rc_zero(tmp_path, payload, label):
    """The dynamic tier is legitimately out of scope when there is nothing to
    analyse; the static IR sign-off stands. Fixing #2109 must not turn those
    into blockers — that would be the opposite error."""
    j = _write(tmp_path, payload)
    assert G.check(j, 1.8)["verdict"] == "SKIPPED_CONDITION", label
    assert G.main([str(j)]) == 0, label


def test_a_real_measurement_still_passes(tmp_path):
    j = _write(tmp_path, {"dynamic_ir_report_emitted": True,
                          "max_dynamic_drop_mv": 50.0, "vdd_v": 1.8,
                          "budget_pct": 15.0})
    assert G.check(j, None)["verdict"] == "PASS"
    assert G.main([str(j)]) == 0


def test_garbage_with_no_marker_still_fails(tmp_path):
    """§4.05 unchanged: absence is never a pass."""
    assert G.check(_write(tmp_path, {"unrelated": 1}), 1.8)["verdict"] == "FAIL"


# ── the population, derived from the emitter's tree ─────────────────────────

def _emitter_statuses_with_the_skip_flag() -> set[str]:
    """Every `status` literal the emitter writes alongside
    `dynamic_ir_report_emitted: False`, read out of its AST.

    Hand-listing this is what let ERROR_NO_PSM_IR sit outside
    `_SKIP_STATUS_VALUES` doing nothing while the structural flag decided. Read
    it from the tree instead, so the population cannot drift away from the
    checker that has to classify it."""
    tree = ast.parse(Path(E.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value: v for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        flag = keys.get("dynamic_ir_report_emitted")
        if not (isinstance(flag, ast.Constant) and flag.value is False):
            continue
        st = keys.get("status")
        if isinstance(st, ast.Constant) and isinstance(st.value, str):
            found.add(st.value)
    # `skip_result` names its status through a parameter: collect its default
    # and every literal its callers pass.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "skip_result":
            for d in node.args.defaults:
                if isinstance(d, ast.Constant) and isinstance(d.value, str):
                    found.add(d.value)
        if isinstance(node, ast.Call) and \
                getattr(node.func, "id", None) == "skip_result":
            for kw in node.keywords:
                if kw.arg == "status" and isinstance(kw.value, ast.Constant):
                    found.add(kw.value.value)
    return found


def test_the_derived_population_is_not_empty_and_covers_both_worlds():
    """Prove the instrument can see before trusting what it reports: an AST
    walk that found nothing would make every assertion below vacuous."""
    statuses = _emitter_statuses_with_the_skip_flag()
    assert len(statuses) >= 4, statuses
    assert {"ERROR_NO_PSM_IR", "ERROR_TOOL"} <= statuses, statuses
    assert {"SKIPPED_MISSING_INPUTS", "SKIPPED_NO_PDN"} <= statuses, statuses


def test_every_emitter_status_is_classified_and_no_error_is_a_skip(tmp_path):
    """THE DURABLE GUARD. For every status the emitter can write with the
    structural flag: an ERROR_* one blocks with the tool's line, and a
    SKIPPED_* one is rc 0. Nothing is left to the flag alone."""
    for status in sorted(_emitter_statuses_with_the_skip_flag()):
        payload = {"status": status, "dynamic_ir_report_emitted": False,
                   "reason": f"reason for {status}"}
        j = _write(tmp_path, payload)
        res, rc = G.check(j, 1.8), G.main([str(j)])
        if status.upper().startswith("ERROR"):
            assert res["verdict"] == "TOOL_ERROR", (status, res)
            assert res.get("tool_line") == f"reason for {status}", (status, res)
            assert rc == 1, (status, rc)
        else:
            assert res["verdict"] == "SKIPPED_CONDITION", (status, res)
            assert rc == 0, (status, rc)


def test_the_structural_flag_alone_no_longer_decides():
    """The mechanism, asserted directly: with a status present, the status
    decides. The flag is consulted only when nothing contradicts it."""
    cases = {
        "error-status": {"status": "ERROR_NO_PSM_IR",
                         "dynamic_ir_report_emitted": False},
        # a status the checker does not recognise is not a skip either
        # (default-deny)
        "unknown-status": {"status": "WHO_KNOWS",
                           "dynamic_ir_report_emitted": False},
        # legacy: no status at all, nothing to contradict the flag
        "legacy-no-status": {"dynamic_ir_report_emitted": False},
    }
    still_a_skip = sorted(k for k, v in cases.items()
                          if G._is_honest_skip(v) is not None)
    # Named membership, not three `is None` sentinels: the pre-fix tree returns
    # all three here, and WHICH ones it wrongly keeps is the finding.
    assert still_a_skip == ["legacy-no-status"], still_a_skip
