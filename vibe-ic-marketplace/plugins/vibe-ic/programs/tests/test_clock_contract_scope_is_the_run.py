#!/usr/bin/env python3
"""The phase-1 clock-contract enforcer is the THIRD consumer of #2244.

`sdc_gen` and `l8_clock_period_actionability_check` were taught by #2244 that
the RUN's PDK selects among target-scoped L8 clock records. The phase-1
PRODUCER-side enforcer, `clock_contract.enforce`, was not — so on a real run
(opentitan_aes x sky130A, 2026-09-15) an input that ships a vendor reference
flow for ANOTHER library got a scoped record stamped beside the design's own
unscoped constraint-file record, the enforcer compared the two as equal
claims about one clock name, and phase 1 halted at 18.8 s on a project that
is fully and consistently specified.

These tests assert the FOUR OUTCOMES of the landed contract at this site, so
this consumer cannot answer differently from the other two, plus the mixed
scoped+unscoped shape the real run produced.

All fixtures are synthetic: `core_ck`, `process_family_a/b/c`, and periods
(6.25 / 3.125 ns) unlike any real design, so a fix that hardcodes a number or
a process name cannot pass.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import clock_contract as CC          # noqa: E402
import _l8_clock_scope as SCOPE      # noqa: E402


def _enforce(doc: dict, project: Path):
    """Call the enforcer, tolerating the pre-fix one-argument signature.

    Without this, every test below would go red on a TypeError the moment the
    parameter is added — an arity red proves nothing about the CONTRACT. With
    it, the RED arm measures the answer the pre-fix code actually gives.
    """
    try:
        return CC.enforce(doc, project)
    except TypeError:
        return CC.enforce(doc)


def _project(tmp_path: Path, pdk: str | None) -> Path:
    """A project whose RUN target is `pdk` (via the step-0.5ic run record)."""
    gd = tmp_path / "phase1" / "generated_docs"
    gd.mkdir(parents=True, exist_ok=True)
    if pdk is not None:
        rec = tmp_path / SCOPE.RUN_PDK_REPORT_REL
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps({"pdk": pdk}), encoding="utf-8")
    return tmp_path


def _rec(period_ns: float, scope: str | None = None) -> dict:
    rec = {"name": "core_ck", "source_pin": "core_ck", "role": "master",
           "domain_kind": "primary", "period_ns": period_ns}
    if scope is None:
        rec["source"] = "input/constraints/*.sdc"
    else:
        rec["pdk_scoped_target"] = scope
        rec["evidence"] = {"kind": "reference_flow_clock"}
    return rec


def _periods(doc: dict) -> list[float]:
    return sorted(CC.entry_period_ns(e) for e in doc["clock_domains"])


# ───────────────── OUTCOME 1 — a scoped record names the run ─────────────────

def test_scoped_match_wins_and_is_not_a_conflict(tmp_path):
    proj = _project(tmp_path, "process_family_a")
    doc = {"clock_domains": [_rec(6.25), _rec(3.125, "process_family_a")]}
    assert _enforce(doc, proj) == [], (
        "the record scoped to the run's own PDK was treated as contradicting "
        "the design's unscoped record")


# ─────────── OUTCOME 2 — no scoped record names the run: unscoped wins ───────
# This is the shape the real run produced.

def test_foreign_scope_does_not_contradict_the_designs_own_record(tmp_path):
    proj = _project(tmp_path, "process_family_b")
    doc = {"clock_domains": [_rec(6.25), _rec(3.125, "process_family_a")]}
    assert _enforce(doc, proj) == [], (
        "a reference-flow clock scoped to ANOTHER process blocked phase 1")


def test_outcome_2_keeps_both_records_rather_than_deleting_one(tmp_path):
    """Not-a-conflict must not become silent deletion."""
    proj = _project(tmp_path, "process_family_b")
    doc = {"clock_domains": [_rec(6.25), _rec(3.125, "process_family_a")]}
    _enforce(doc, proj)
    assert _periods(doc) == [3.125, 6.25], (
        "resolving by scope dropped a record instead of keeping both")


# ─────────── OUTCOME 3 — every record scoped elsewhere: REFUSE ───────────────

def test_only_foreign_scoped_records_still_refuse(tmp_path):
    """NEGATIVE CONTROL: the cross-target refusal keeps its teeth."""
    proj = _project(tmp_path, "process_family_c")
    doc = {"clock_domains": [_rec(6.25, "process_family_a"),
                             _rec(3.125, "process_family_b")]}
    conflicts = _enforce(doc, proj)
    assert conflicts, "cross-target timing reuse was admitted"
    assert conflicts[0]["clock"] == "core_ck"
    assert conflicts[0]["scopes_seen"] == ["process_family_a",
                                           "process_family_b"]


# ─────────── OUTCOME 4 — records are scoped, the run's PDK is unknown ────────

def test_scoped_records_with_no_measured_target_refuse(tmp_path):
    """NEGATIVE CONTROL: NOT_MEASURED is a refusal, never a quiet pass."""
    proj = _project(tmp_path, None)
    doc = {"clock_domains": [_rec(6.25), _rec(3.125, "process_family_a")]}
    conflicts = _enforce(doc, proj)
    assert conflicts, "an unmeasurable target silently picked a period"


# ───────────────────── the pre-existing contract, unchanged ─────────────────

def test_two_unscoped_records_still_conflict(tmp_path):
    """NEGATIVE CONTROL: the original one-name-one-period refusal survives."""
    proj = _project(tmp_path, "process_family_a")
    doc = {"clock_domains": [_rec(6.25), _rec(3.125)]}
    conflicts = _enforce(doc, proj)
    assert conflicts, "two unscoped owners stopped contradicting each other"
    assert conflicts[0]["periods_ns"] == [3.125, 6.25]


def test_two_records_in_the_same_scope_still_conflict(tmp_path):
    """NEGATIVE CONTROL: selection does not exempt the selected records."""
    proj = _project(tmp_path, "process_family_a")
    doc = {"clock_domains": [_rec(6.25, "process_family_a"),
                             _rec(3.125, "process_family_a")]}
    assert _enforce(doc, proj), (
        "two records scoped to the RUN's own PDK stopped conflicting")


def test_an_agreeing_document_never_asks_the_scope_question(tmp_path):
    """No scope machinery may fire where there is no disagreement."""
    proj = _project(tmp_path, None)            # deliberately unmeasurable
    doc = {"clock_domains": [_rec(6.25), _rec(6.25, "process_family_a")]}
    assert _enforce(doc, proj) == [], (
        "an unmeasurable target invented a refusal on a document whose "
        "records already agree")


def test_no_scoped_record_at_all_behaves_exactly_as_before(tmp_path):
    """A project with no scoped record answers as it did before #2244."""
    proj = _project(tmp_path, None)
    doc = {"clock_domains": [_rec(6.25), _rec(3.125)]}
    assert _enforce(doc, proj), "the unscoped contradiction stopped blocking"


# ───────────── the three consumers must not drift apart ─────────────────────

def test_this_consumer_delegates_to_the_landed_contract(tmp_path):
    """Same records, same target -> same answer as `_l8_clock_scope.select`."""
    records = [_rec(6.25), _rec(3.125, "process_family_a")]
    for target, expect_conflict in (("process_family_a", False),
                                    ("process_family_b", False),
                                    (None, True)):
        res = SCOPE.select(records, target)
        proj = _project(tmp_path / f"p_{target}", target)
        got = bool(_enforce({"clock_domains": [dict(r) for r in records]},
                            proj))
        assert got is (not res.selected) is expect_conflict, (
            f"target={target!r}: contract says selected={res.selected} but "
            f"this consumer {'refused' if got else 'admitted'} it")
