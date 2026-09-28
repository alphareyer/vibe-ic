"""Owner's IC versus IP route ruling at step 0.5ic and both consumers."""
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _tapeout_declaration as TD  # noqa: E402
import flow_compliance_check as F  # noqa: E402
import phase1_one_shot_runner as R  # noqa: E402
import tapeout_declaration_check as CHECK  # noqa: E402
import tapeout_declaration_gen as GEN  # noqa: E402
import test_issue2277_hardmacro_owes_no_die_steps as BASE  # noqa: E402

MESSAGE = ("deliverable HARDMACRO (IP path) contradicts a bought shuttle slot "
           "(IC path); declare one route in input/step_0_5ic_answers.json")


def _case(tmp_path, *, deliverable="HARDMACRO", bought=False,
          self_tapeout=False):
    operator = ({"path": "t.yaml", "slot": "slot_1x1"} if bought else None)
    return BASE._project(tmp_path, deliverable=deliverable,
                         operator=operator, self_tapeout=self_tapeout)


def test_four_routes_share_one_predicate_and_refuse_the_contradiction(tmp_path):
    """The three valid routes keep their outputs; the fourth selects neither."""
    valid = []
    for name, opts, owed, ring in (
        ("ip", {}, False, False),
        ("shuttle_die", {"deliverable": "DIE", "bought": True}, True, True),
        ("self_die", {"deliverable": "DIE", "self_tapeout": True}, True, True),
    ):
        project = _case(tmp_path / name, **opts)
        valid.append((project, owed))
        assert TD.requests_pad_ring(project) is ring
        for sid in ("15.5ic", "26.5ic", "37.5ic"):
            assert F._check_condition(
                project, BASE._steps()[sid]["condition"]) is owed
        assert F._check_condition(
            project, BASE._steps()["37.5ip"]["condition"]) is (not owed)

    bought = _case(tmp_path / "contradiction", bought=True)
    refusal = None
    try:
        TD.requests_pad_ring(bought)
    except ValueError as exc:
        refusal = str(exc)
    assert refusal == MESSAGE
    for sid in ("15.5ic", "26.5ic", "37.5ic"):
        with pytest.raises(ValueError,
                           match="HARDMACRO.*bought shuttle slot"):
            F._check_condition(bought, BASE._steps()[sid]["condition"])
    for project, owed in valid:
        assert TD.die_outputs_owed(project) is owed


def test_step_0_5ic_refuses_before_any_producer(tmp_path, monkeypatch, capsys):
    project = _case(tmp_path, bought=True)
    dispatched = []

    def no_producer(*_args, **_kwargs):
        dispatched.append(True)
        raise RuntimeError("producer dispatched")

    monkeypatch.setattr(R._wd, "run_host_supervised", no_producer)
    try:
        rc = R._run_step_0_5ic(project)
    except RuntimeError:
        rc = None
    assert dispatched == []
    assert rc == 1
    assert MESSAGE in capsys.readouterr().err


def test_direct_route_producer_and_gate_refuse_by_name(tmp_path, capsys):
    project = _case(tmp_path, bought=True)
    assert GEN.main([str(project)]) == 1
    assert MESSAGE in capsys.readouterr().err
    rec = CHECK.evaluate(project)
    assert any(r["rule"] == "HARDMACRO_BOUGHT_SLOT_CONTRADICTION"
               and r["message"] == MESSAGE
               for r in rec["refusals"])
