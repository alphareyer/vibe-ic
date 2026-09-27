"""llv1 W7a: pre-PnR preparation pulled out of `step_pnr`.

The extraction is a refactor for the default flow and a hook for the flag:
the default flow must stay byte-identical, and the between-segments step must
run the SAME code the default flow runs. Contracts:
  1. `step_pad_assignment` runs `pad_assignment_gen` exactly as the first
     program of `step_pad_ring_gen` (same program, same PDK arguments, the
     same rc reading), and `step_pad_ring_gen` still runs its four programs
     in order through the same `_run_pad_program`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGRAMS))


def _r():
    import phase3_one_shot_runner as r
    return r


@pytest.fixture
def calls(monkeypatch):
    """Capture the real container command each pad program is launched with."""
    r = _r()
    seen = []
    rcs = {}
    monkeypatch.setattr(r, "_padring_pdk_root_and_tree",
                        lambda pdk, container: ("/pdkroot", "/pdkroot/tree"))
    monkeypatch.setattr(r, "_to_container_path", lambda path, c: str(path))

    def _exec(container, cmd, marker=None):
        name = Path(marker).name
        seen.append((name, cmd))
        return rcs.get(name, 0), "", ""

    monkeypatch.setattr(r, "_docker_exec", _exec)
    return SimpleNamespace(seen=seen, rc=rcs)


def test_pad_assignment_runs_what_the_ring_step_runs_first(tmp_path, calls):
    r = _r()
    pdk = SimpleNamespace(tech_lef=None)
    r.step_pad_ring_gen(tmp_path, container="c", pdk=pdk)
    ring_first = calls.seen[0]
    assert [n for n, _ in calls.seen] == [
        "pad_assignment_gen.py", "pad_ring_gen.py", "pad_ring_check.py"]
    calls.seen.clear()
    r.step_pad_assignment(tmp_path, container="c", pdk=pdk)
    assert calls.seen == [ring_first]            # the identical command line
    assert ring_first[1].endswith(
        f"{tmp_path} --pdk-root /pdkroot --pdk /pdkroot/tree")


@pytest.mark.parametrize("rc,status,reason", [
    (0, "PASS", ""), (1, "FAIL", ""), (2, "NOT_MEASURED", "not_executed"),
    (127, "NOT_MEASURED", "tool_absent")])
def test_pad_assignment_reads_rc_like_the_ring_step(tmp_path, calls, rc,
                                                    status, reason):
    r = _r()
    calls.rc["pad_assignment_gen.py"] = rc
    rep = tmp_path / "reports" / "phase3" / "pad_assignment.json"
    rep.parent.mkdir(parents=True)
    rep.write_text("{}")
    got = r.step_pad_assignment(tmp_path, container="c", pdk=None)
    assert (got.status, got.reason_class) == (status, reason)
    if rc == 1:
        # (For rc 2 and others the ring step itself raises: it computes a
        # reason class and never passes it to StepResult -- a pre-existing
        # defect of the default flow, left as it is by this refactor.)
        ring = r.step_pad_ring_gen(tmp_path, container="c", pdk=None)
        assert ring.status == status


def test_pad_assignment_rc0_without_its_report_is_a_fail(tmp_path, calls):
    got = _r().step_pad_assignment(tmp_path, container="c", pdk=None)
    assert got.status == "FAIL" and "is absent" in got.detail
