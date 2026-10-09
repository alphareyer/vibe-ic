"""ORGANIC #621 [LOW/P3] — the Phase-3 derived-artefact generator loop ran
tapeout_checklist_gen.py BEFORE foundry_handoff_pack_gen.py. The checklist
snapshot (reports/audit/tapeout_checklist.json) was therefore written before the
foundry handoff JSONs (mask_spec / wat_plan / corner_test_vectors) it grades
existed, so it recorded verdict=BLOCKER_MISSING listing those as missing even
though they are produced tens of ms later in the SAME run. flow_compliance Step
36 re-runs tapeout_signoff_check after the files exist (→ PASS) so the FLOW
verdict is not gated, but a user reading the snapshot is misled.

Fix: foundry_handoff_pack_gen (which PRODUCES the foundry JSONs) must run BEFORE
tapeout_checklist_gen (which GRADES them). The order is now a module constant
`_DERIVED_ARTEFACT_GENERATORS` so it is testable and a future reorder cannot
silently re-break it.

POSITIVE (#621): in the constant's order, the checklist sees the foundry
artefacts as present (present=True), no spurious BLOCKER_MISSING for them.

GUARD (documents the bug): the OLD order (checklist first) leaves the foundry
items present=False — the exact 現象 the reorder fixes.

chip-AGNOSTIC: pure generator-ordering fix; no checklist-logic change, no chip
name.
"""
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PLUGIN / "programs"))
import phase3_one_shot_runner as P  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _progress_run as _pr  # noqa: E402

PROGRAMS = PLUGIN / "programs"
_FOUNDRY_ITEMS = ("foundry_mask_spec", "foundry_wat_plan", "foundry_corner_kit")


def _run(proj, prog, *extra):
    _pr.run([sys.executable, str(PROGRAMS / prog), str(proj), *extra],
                   capture_output=True, text=True)


def _foundry_present(proj):
    cl = json.loads(
        (proj / "reports/audit/tapeout_checklist.json").read_text())
    return {it["name"]: it.get("present")
            for it in cl["items"] if it.get("name") in _FOUNDRY_ITEMS}


def test_constant_order_foundry_before_checklist():
    order = [g for g, _ in P._DERIVED_ARTEFACT_GENERATORS]
    assert order.index("foundry_handoff_pack_gen.py") \
        < order.index("tapeout_checklist_gen.py")


def test_pre_audit_release_producers_are_not_launched_again(monkeypatch, tmp_path):
    """The canonical synchronous chain owns these two writes exactly once."""
    overlap = {
        gen for gen, _kind in P._DERIVED_ARTEFACT_GENERATORS
        if gen in P._pre_audit_generator_names()
    }
    assert overlap == {
        "foundry_handoff_pack_gen.py",
        "tapeout_checklist_gen.py",
    }

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        raise AssertionError("a pre-audit producer was launched twice")

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    monkeypatch.setattr(P, "_DERIVED_ARTEFACT_GENERATORS",
                        tuple((name, "release") for name in overlap))
    assert P._run_derived_artefact_generators(
        tmp_path, None, skip_names=P._pre_audit_generator_names()) == []
    assert calls == []


def test_failed_pre_audit_invalidates_owned_output(monkeypatch, tmp_path):
    """A timeout must not leave a prior handoff eligible for the audit."""
    out = tmp_path / "phase3/stage4/foundry_handoff/mask_spec.json"
    out.parent.mkdir(parents=True)
    out.write_text('{"generated_at":"previous-run"}')

    def refused(project, name, program, out_rel, extra_argv=()):
        return P._signoff_not_checked(name, 0.0, "forced timeout")

    monkeypatch.setattr(P, "_run_declared_signoff_gate", refused)
    rows = P.run_pre_audit_producers(tmp_path,
                                     only_names=("foundry_handoff",))
    assert rows and rows[0].status == "NOT_MEASURED"
    assert not out.exists(), "failed producer must not leave stale evidence"


def test_only_current_pass_with_fresh_output_suppresses_retry(tmp_path):
    """The derived retry is skipped only for a current PASS output."""
    now = __import__("time").time()
    rows = []
    for name, _program, out_rel, _extra in P._PRE_AUDIT_PRODUCERS:
        if name == "gds_xor":
            continue
        out = tmp_path / out_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("current")
        rows.append(P.StepResult(name, "PASS", output_files=[str(out)]))

    done = P._successful_pre_audit_generator_names(tmp_path, rows,
                                                    run_started_at=now - 1)
    assert done == {
        "foundry_handoff_pack_gen.py",
        "tapeout_checklist_gen.py",
    }


def test_old_pass_output_does_not_suppress_retry(tmp_path):
    """A previous-run PASS is not a current producer invocation."""
    now = __import__("time").time()
    out = tmp_path / "reports/audit/tapeout_checklist.json"
    out.parent.mkdir(parents=True)
    out.write_text("previous")
    old = now - 60
    import os
    os.utime(out, (old, old))
    row = P.StepResult("tapeout_checklist", "PASS",
                       output_files=[str(out)])
    assert P._successful_pre_audit_generator_names(
        tmp_path, [row], run_started_at=now - 1) == frozenset()


def test_e2e_fixed_order_checklist_sees_foundry(tmp_path):
    # Run the generators in the module-constant order; the foundry-producing
    # generator runs first, so the checklist grades present artefacts.
    for gen, _kind in P._DERIVED_ARTEFACT_GENERATORS:
        if not (PROGRAMS / gen).is_file():
            continue
        extra = ("--top", "chip_top") if gen == "foundry_handoff_pack_gen.py" else ()
        _run(tmp_path, gen, *extra)
    present = _foundry_present(tmp_path)
    assert present, "checklist must enumerate the foundry items"
    assert all(v is True for v in present.values()), present


def test_e2e_wrong_order_reproduces_missing(tmp_path):
    # GUARD: the pre-#621 order (checklist BEFORE foundry) leaves the foundry
    # items present=False — the 現象 the reorder fixes.
    _run(tmp_path, "tapeout_checklist_gen.py")  # no foundry files yet
    present = _foundry_present(tmp_path)
    assert all(v is False for v in present.values()), present
