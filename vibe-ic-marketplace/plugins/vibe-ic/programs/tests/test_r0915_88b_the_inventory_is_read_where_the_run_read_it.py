"""icslot51 — `partial_population` was collecting five different conditions.

MEASURED on run21 (`flow_compliance_check --strict`), five steps carried
`reason_class=partial_population` and the same sentence, "the gate reports its
input was applicable and was NOT examined":

  step 2      the NESTED `flow_compliance_check --stage-id stage_phase1` hint
              carries `[verdict=NOT_MEASURED, reason_class=EXECUTION_ERROR]`
  step 5      `formal_proof_evidence_check` -- a GENUINE partial population; its
              report carries `unresolved_obligations` against a
              `property_denominator`. The label is CORRECT here.
  step 21     `macro_obs_geometry_intersect_check` -- "physical view inventory is
              incomplete: 31 LEF view(s) missing or unreadable", its own report
              saying `reason_class: "BLOCKED_BY_UPSTREAM"`
  step 26.5ic `die_finishing_check` -- seal ring VERIFIED, die identification
              NOT_DETERMINED because packaging is undeclared and the requirement
              is CONDITIONAL on it
  step 31     `provenance_check --require-measured` -- `drc_signoff.rpt` is
              UNMEASURED: "the run bound to this artefact declares no measurement
              record"

Five conditions, five different correct responses, one word. This file pins the
one of them that was a READER defect with a design consequence.

STEP 21: THE INVENTORY WAS FINE; THE READER LOOKED IN THE WRONG PLACE.
`reports/phase3/physical_view_inventory.json` records the 32 LEFs PnR actually
read -- ONE absolute host path to a LEF the run produced itself, and 31
container-side PDK paths under /foss/pdks/... that exist only INSIDE the EDA
image. The gate tested all 32 with a bare host `is_file()`, so 31 came back
"missing or unreadable" and the step reported NOT_MEASURED. That gate had
therefore never examined this design at all.

AND THE HALF-FIX WAS WORSE THAN THE DEFECT, which is why the content channel is
pinned here too: with existence resolved but the LEF BODIES still read
host-side, the gate parsed one file, found no OBS among 49 placed masters and
reported `DESIGN_DECLARED_NA -> SKIP` -- a PASS tier, over a set it had not read.
Measured in that intermediate state before this file existed.

With both channels on the run's own reader, step 21 on run21 reports:
`[PASS] 1283 placed instance(s) of 31 placed master(s) whose LEF declares an OBS,
778 supply segment(s), 0 path(s) abandoned — none spans an obstruction`, and
says which 18 masters it is SILENT about.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROGRAMS = Path(__file__).resolve().parent.parent
MOD = PROGRAMS / "macro_obs_geometry_intersect_check.py"
sys.path.insert(0, str(PROGRAMS))


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name, MOD)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


class _Reader:
    """Stands in for the volume the run recorded: knows paths this host does not."""

    kind = "stub"

    def __init__(self, files: dict):
        self._files = files

    def is_file(self, path: str) -> bool:
        return path in self._files

    def read_text(self, path: str):
        return self._files.get(path)


_PDK = "/foss/pdks/ciel/gf180mcu/versions/deadbeef/gf180mcuD/libs.ref/x/lef/a.lef"


def _inventory(project: Path, paths) -> None:
    rec = project / "reports/phase3/physical_view_inventory.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({
        "schema": "vibe-ic/physical-view-inventory/1",
        "producer": "phase3_one_shot_runner",
        "verdict": "RECORDED",
        "scope": "pnr_read_lef_inputs",
        "read_lef_paths": [str(p) for p in paths],
    }, indent=2) + "\n")


# ── re-anchoring a run-produced path ─────────────────────────────────────────

def test_a_recorded_host_path_is_reanchored_to_this_project(tmp_path):
    """run21's first entry is an absolute path into the ORIGINAL run root. Read
    from any other tree -- a copy, another lane's host -- it points outside the
    project entirely."""
    m = _mod("mo_1")
    real = tmp_path / "phase3/stage3/pnr/active_via_legalized.tlef"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("VERSION 5.8 ;\n")
    recorded = Path("/home/someone/_lane_other/run21/phase3/stage3/pnr/"
                    "active_via_legalized.tlef")
    assert m._reanchored_in_this_project(tmp_path, recorded) == real


def test_reanchoring_never_invents_a_file(tmp_path):
    """Existence-tested. A path with no existing tail comes back unchanged so the
    caller's own failure names the real path, not a plausible-looking one."""
    m = _mod("mo_2")
    recorded = Path("/home/someone/_lane_other/run21/phase3/stage3/pnr/absent.tlef")
    assert m._reanchored_in_this_project(tmp_path, recorded) == recorded


# ── the two channels: existence AND content ──────────────────────────────────

def test_a_container_side_view_is_present_through_the_runs_reader(tmp_path):
    """THE DEFECT. 31 PDK paths exist only inside the image; a host `is_file()`
    calls every one of them missing and the step never examines the design."""
    m = _mod("mo_3")
    _inventory(tmp_path, [_PDK])
    m._reader_the_run_recorded = lambda project: (
        _Reader({_PDK: "MACRO a\n  OBS\n  END\nEND a\n"}), "stub volume")
    paths, why = m._recorded_physical_view_lefs(tmp_path)
    assert why == "", why
    assert [str(p) for p in paths] == [_PDK]


def test_the_body_is_read_through_the_same_reader(tmp_path):
    """THE HALF-FIX THAT WAS WORSE. Existence alone let the gate parse nothing and
    report DESIGN_DECLARED_NA -> SKIP, a PASS tier, over an unread set."""
    m = _mod("mo_4")
    _inventory(tmp_path, [_PDK])
    body = "MACRO a\n  OBS\n  END\nEND a\n"
    m._reader_the_run_recorded = lambda project: (_Reader({_PDK: body}), "stub")
    m._recorded_physical_view_lefs(tmp_path)
    assert m._read_input_text(Path(_PDK)) == body


def test_a_view_absent_from_host_and_reader_alike_is_still_missing(tmp_path):
    """THE TEETH. The reader is a second place to look, never an excuse: a view
    nothing can produce is still reported, and the message names the reader that
    was consulted so 'not found' can be told from 'never asked'."""
    m = _mod("mo_5")
    _inventory(tmp_path, [_PDK])
    m._reader_the_run_recorded = lambda project: (_Reader({}), "stub volume")
    paths, why = m._recorded_physical_view_lefs(tmp_path)
    assert paths == []
    assert "missing or unreadable" in why
    assert "stub volume" in why


def test_with_no_reader_at_all_an_absent_view_is_missing(tmp_path):
    """A host with no PDK and no reader must not silently pass."""
    m = _mod("mo_6")
    _inventory(tmp_path, [_PDK])
    m._reader_the_run_recorded = lambda project: (None, "no reader resolved")
    paths, why = m._recorded_physical_view_lefs(tmp_path)
    assert paths == []
    assert "missing or unreadable" in why


def test_an_empty_view_is_not_a_read_view(tmp_path):
    """A reader that answers with nothing has not supplied the view; counting it
    as read is the same fail-open in a quieter form."""
    m = _mod("mo_7")
    _inventory(tmp_path, [_PDK])
    m._reader_the_run_recorded = lambda project: (_Reader({_PDK: "   \n"}), "stub")
    paths, why = m._recorded_physical_view_lefs(tmp_path)
    assert paths == []
    assert "empty" in why


def test_a_host_readable_view_still_reads_from_the_host(tmp_path):
    """CONTROL. The reader is a FALLBACK; nothing about the ordinary path moves,
    so a project whose LEFs are on disk is unaffected by any of this."""
    m = _mod("mo_8")
    here = tmp_path / "local.lef"
    here.write_text("MACRO b\nEND b\n")
    _inventory(tmp_path, [here])
    m._reader_the_run_recorded = lambda project: (None, "not consulted")
    paths, why = m._recorded_physical_view_lefs(tmp_path)
    assert why == "" and [str(p) for p in paths] == [str(here)]
