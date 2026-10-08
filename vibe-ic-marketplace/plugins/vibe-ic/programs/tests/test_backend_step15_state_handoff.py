"""Bounded Step 15 state handoff remains portable and canonical."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import execution_backend_producers as producers
import execution_modes as em


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _make_published_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "producer"
    raw = project / "phase3/librelane/15-floorplan"
    pnr = project / "phase3/stage3/pnr"
    raw.mkdir(parents=True)
    pnr.mkdir(parents=True)
    floorplan = _write(raw / "floorplan.def", "VERSION 5.8 ;\nDESIGN bounded ;\n")
    odb = _write(raw / "librelane_floorplan.odb", "ODB-BYTES\n")
    netlist = _write(raw / "step15.nl.v", "module bounded; endmodule\n")
    sdc = _write(raw / "constraint.sdc", "create_clock -period 10 [get_ports clk]\n")
    upstream_sdc = _write(pnr / 'constraint.sdc', 'upstream-clock\n')
    canonical_def = pnr / "floorplan.def"
    canonical_odb = pnr / "librelane_floorplan.odb"
    shutil.copyfile(floorplan, canonical_def)
    shutil.copyfile(odb, canonical_odb)

    state = _write(raw / "state_out.json", json.dumps({
        "metrics": {"design__instance__count": 2},
        "def": str(floorplan), "odb": str(odb),
        "nl": str(netlist), "sdc": str(sdc),
    }, sort_keys=True))
    pdn = _write(raw / "pdn-state_out.json", json.dumps({
        "step": "OpenROAD.GeneratePDN", "state": "done",
    }, sort_keys=True))
    handoff = project / "reports/phase3/execution-backend-15-floorplan-handoff.json"
    _write(handoff, json.dumps({
        "state_sha256": producers.snap.sha(state),
        "views": {
            "def": {"source_sha256": producers.snap.sha(canonical_def),
                    "dest_sha256": producers.snap.sha(canonical_def)},
            "odb": {"source_sha256": producers.snap.sha(canonical_odb),
                    "dest_sha256": producers.snap.sha(canonical_odb)},
        },
    }, sort_keys=True))
    producers._publish_step15_bounded_state(project, SimpleNamespace(extras={
        "bounded_state": True,
        "native_state": str(state), "pdn_state": str(pdn),
        "handoff": str(handoff), "floorplan": str(canonical_def),
        "odb": str(canonical_odb),
    }))
    isolated = tmp_path / "isolated-consumer"
    shutil.copytree(pnr, isolated / "phase3/stage3/pnr")
    return project, isolated


def test_step15_publisher_to_isolated_reader_consumes_canonical_def_and_odb(tmp_path):
    _, isolated = _make_published_project(tmp_path)

    state = producers._read_step15_bounded_state(isolated)

    assert state["views"]["def"] == isolated / "phase3/stage3/pnr/floorplan.def"
    assert state["views"]["odb"] == isolated / "phase3/stage3/pnr/librelane_floorplan.odb"
    assert state["views"]["sdc_derived"] == isolated / "phase3/stage3/pnr/step15.constraint.sdc"
    assert (isolated / 'phase3/stage3/pnr/constraint.sdc').read_text() == 'upstream-clock\n'
    assert (isolated / 'phase3/stage3/pnr/step15.constraint.sdc').read_text() == 'create_clock -period 10 [get_ports clk]\n'


def test_step15_reader_refuses_coordinated_canonical_path_substitution(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / "phase3/stage3/pnr"
    canonical = pnr / "floorplan.def"
    alternate = pnr / "alternate.def"
    shutil.copyfile(canonical, alternate)
    canonical.write_text("CHANGED-CANONICAL\n")

    record_path = pnr / "step15_state.json"
    handoff_path = pnr / "step15_handoff.json"
    record = json.loads(record_path.read_text())
    handoff = json.loads(handoff_path.read_text())
    alternate_sha = producers.snap.sha(alternate)
    record["views"]["def"] = {
        "path": "phase3/stage3/pnr/alternate.def", "sha256": alternate_sha,
    }
    handoff["views"]["def"]["source_sha256"] = alternate_sha
    handoff["views"]["def"]["dest_sha256"] = alternate_sha
    record["handoff_views"]["def"] = handoff["views"]["def"]
    handoff_path.write_text(json.dumps(handoff, sort_keys=True))
    record["handoff"]["sha256"] = producers.snap.sha(handoff_path)
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(em.Refusal) as caught:
        producers._read_step15_bounded_state(isolated)
    assert caught.value.code == "LL_HANDOFF_VIEW_CHANGED"



@pytest.mark.parametrize('which', ['upstream', 'derived'])
def test_step15_reader_refuses_changed_upstream_or_derived_sdc(tmp_path, which):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    target = pnr / ('constraint.sdc' if which == 'upstream' else 'step15.constraint.sdc')
    target.write_text('changed-' + which + '\n')
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)


def test_step15_reader_refuses_canonical_path_substitution(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / 'phase3/stage3/pnr'
    record = json.loads((pnr / 'step15_state.json').read_text())
    record['views']['sdc_derived']['path'] = 'phase3/stage3/pnr/escape.sdc'
    (pnr / 'step15_state.json').write_text(json.dumps(record))
    with pytest.raises(em.Refusal, match='BACKEND_STEP15_SDC_BINDING_CHANGED'):
        producers._step15_sdc(isolated)

def test_step15_reader_refuses_non_mapping_handoff_with_stable_code(tmp_path):
    _, isolated = _make_published_project(tmp_path)
    pnr = isolated / "phase3/stage3/pnr"
    handoff_path = pnr / "step15_handoff.json"
    record_path = pnr / "step15_state.json"
    record = json.loads(record_path.read_text())
    handoff_path.write_text("[]\n")
    record["handoff"]["sha256"] = producers.snap.sha(handoff_path)
    record_path.write_text(json.dumps(record, sort_keys=True))

    with pytest.raises(em.Refusal) as caught:
        producers._read_step15_bounded_state(isolated)
    assert caught.value.code == "LL_HANDOFF_ABSENT"
