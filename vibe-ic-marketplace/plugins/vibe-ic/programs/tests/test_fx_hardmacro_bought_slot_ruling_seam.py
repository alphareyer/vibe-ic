"""The unresolved bought-slot contract and the shared one-line ruling seam.

The fixture is the landed #2277 declaration fixture, including owner
attestation and the fetched catalogue. An operator binding, not the catalogue,
is the one changed input in the disputed case.
"""
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parents[1]
if str(PROGRAMS) not in sys.path:
    sys.path.insert(0, str(PROGRAMS))

import _tapeout_declaration as TD  # noqa: E402
import flow_compliance_check as F  # noqa: E402
import test_issue2277_hardmacro_owes_no_die_steps as BASE  # noqa: E402


@pytest.mark.parametrize("name,options,owed,ring", [
    ("macro_no_slot", {}, False, False),
    ("macro_bought_slot", {"operator": {"path": "t.yaml", "slot": "slot_1x1"}},
     None, False),
    ("die_bought_slot", {"deliverable": "DIE",
                         "operator": {"path": "t.yaml", "slot": "slot_1x1"}},
     True, True),
    ("self_tapeout", {"deliverable": "DIE", "self_tapeout": True},
     True, True),
])
def test_four_routes_report_the_pending_ruling_explicitly(
        tmp_path, name, options, owed, ring):
    project = BASE._project(tmp_path / name, **options)
    assert TD.die_outputs_owed(project) is owed
    assert TD.requests_pad_ring(project) is ring
    live = owed is not False
    for sid in ("15.5ic", "26.5ic", "37.5ic"):
        assert F._check_condition(
            project, BASE._steps()[sid]["condition"]) is live


@pytest.mark.parametrize("ruling", [True, False])
def test_either_owner_ruling_changes_producer_and_audit_together(
        tmp_path, monkeypatch, ruling):
    project = BASE._project(
        tmp_path, operator={"path": "t.yaml", "slot": "slot_1x1"})
    original = TD.die_outputs_owed
    assert original(project) is None
    monkeypatch.setattr(TD, "die_outputs_owed", lambda _project: ruling)
    assert TD.requests_pad_ring(project) is ruling
    for sid in ("15.5ic", "26.5ic", "37.5ic"):
        assert F._check_condition(
            project, BASE._steps()[sid]["condition"]) is ruling
