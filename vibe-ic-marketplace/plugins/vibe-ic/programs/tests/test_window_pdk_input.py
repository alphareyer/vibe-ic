"""A phase-3 window publishes a sign-off record that cites its own PDK file.

MEASURED (spm x gf180mcuD, `--entry-step 31 --exit-step 31`, 2026-09-29):
DRC measured 0 violations, and the window refused to publish it --
`window input outside project: .../libs.tech/klayout/tech/drc/gf180mcu.drc`
-- because `drc_signoff.json` names the DRC deck it ran, and the PDK lives
outside the project. The window stopped there and LVS never ran.

Pinned:
* a file field of the window's PDK record, under that PDK's root, recorded
  with its sha256 at the window's start, is an admitted input and its sha is
  carried in the publication receipt;
* an outside path that is not a PDK file still refuses;
* another file under the same PDK root that the PDK record does not name
  still refuses (the root is not a free pass);
* a PDK file whose bytes differ from the recorded sha refuses;
* the window writes that record before it dispatches any site.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

p3 = importlib.import_module("phase3_one_shot_runner")

RUN = "pdkwin1"


def _tree(tmp_path: Path):
    pdk_root = tmp_path / "pdks" / "calpdk"
    (pdk_root / "libs.ref/cells/lef").mkdir(parents=True)
    (pdk_root / "libs.tech/klayout/drc").mkdir(parents=True)
    lef = pdk_root / "libs.ref/cells/lef/cells.lef"
    lef.write_text("VERSION 5.8 ;\n")
    deck = pdk_root / "libs.tech/klayout/drc/cal.drc"
    deck.write_text("# calibration deck\n")
    sibling = pdk_root / "libs.tech/klayout/drc/other.rb"
    sibling.write_text("# not named by the PDK record\n")
    pdk = p3.PdkConfig(name="calpdk", liberty=str(pdk_root / "libs.ref/cells/lib/c.lib"),
                       tech_lef=str(lef), cell_lef=str(lef), cell_gds=None,
                       site="core", drc_deck=str(deck))
    project = tmp_path / "project"
    isolated = tmp_path / "window" / "project"
    for root in (project, isolated):
        (root / "reports/phase3").mkdir(parents=True)
    return pdk, project, isolated, deck, sibling


def _publish(project, isolated, cited):
    out = isolated / "reports/phase3/drc_signoff.json"
    out.write_text(json.dumps({"verdict": "PASS",
                               "summary": {"producers": [{"deck": cited}]}}))
    return p3._phase3_window_publication(project, isolated, [out], RUN)


def _record(project, pdk):
    recorder = getattr(p3, "_window_pdk_record", None)
    if recorder is not None:
        recorder(project, pdk, "", RUN)


def test_the_windows_own_pdk_deck_is_published_with_its_sha(tmp_path):
    pdk, project, isolated, deck, _ = _tree(tmp_path)
    _record(project, pdk)
    copied, error = _publish(project, isolated, str(deck))
    assert error == "", error
    assert copied
    receipt = json.loads((project / f"reports/audit/windows/{RUN}/publication.json")
                         .read_text())
    assert receipt["status"] == "PUBLISHED"
    assert receipt["pdk_inputs"] == {
        str(deck): hashlib.sha256(deck.read_bytes()).hexdigest()}


def test_an_outside_path_that_is_not_a_pdk_file_still_refuses(tmp_path):
    pdk, project, isolated, _, _ = _tree(tmp_path)
    stray = tmp_path / "elsewhere" / "x.drc"
    stray.parent.mkdir()
    stray.write_text("x\n")
    _record(project, pdk)
    copied, error = _publish(project, isolated, str(stray))
    assert copied == []
    assert error == f"window input outside project: {stray}"


def test_another_file_under_the_pdk_root_still_refuses(tmp_path):
    pdk, project, isolated, _, sibling = _tree(tmp_path)
    _record(project, pdk)
    copied, error = _publish(project, isolated, str(sibling))
    assert copied == []
    assert error == f"window input outside project: {sibling}"


def test_a_pdk_file_whose_bytes_changed_refuses(tmp_path):
    pdk, project, isolated, deck, _ = _tree(tmp_path)
    _record(project, pdk)
    deck.write_text("# edited after the window recorded it\n")
    copied, error = _publish(project, isolated, str(deck))
    assert copied == []
    assert error == ("window PDK input differs from the window's PDK record: "
                     f"{deck}")


def test_the_window_records_its_pdk_before_dispatching_a_site(tmp_path, monkeypatch):
    pdk, project, _, deck, _ = _tree(tmp_path)
    monkeypatch.setenv("VIBEIC_PHASE3_WINDOW_RUN_ID", RUN + "-dispatch")
    seen = {}

    def dispatch(project_, top, view, args, site, gate, run_id):
        seen["record"] = p3._WINDOW_PDK_RECORDS.get(run_id)
        return p3.StepResult(site, "NOT_MEASURED", 0.0, "captured",
                             reason_class="not_executed")

    monkeypatch.setattr(p3, "_direct_flow_window", dispatch)
    p3._run_phase3_window(project, "top", pdk,
                          SimpleNamespace(entry_step="37", exit_step="37",
                                          container=""), ["gds"])
    assert seen["record"] is not None
    assert seen["record"]["files"].get(str(deck)) == \
        hashlib.sha256(deck.read_bytes()).hexdigest()
    assert seen["record"]["root"] == str(tmp_path / "pdks" / "calpdk")


def test_a_pdk_field_outside_the_pdk_root_still_refuses(tmp_path):
    """The record binds the PDK TREE's files; a field pointing elsewhere is
    not a PDK input just because the configuration names it."""
    pdk, project, isolated, _, _ = _tree(tmp_path)
    outside = tmp_path / "elsewhere" / "deck.drc"
    outside.parent.mkdir()
    outside.write_text("# outside the PDK root\n")
    pdk.drc_deck = str(outside)
    _record(project, pdk)
    copied, error = _publish(project, isolated, str(outside))
    assert copied == []
    assert error == f"window input outside project: {outside}"


def test_a_sentence_too_long_to_be_a_file_name_is_not_a_citation(tmp_path):
    """A published record carries prose (an LVS verdict's `message`). Probing a
    >255-byte sentence as a path raised ENAMETOOLONG out of `is_file`, and the
    whole publication was refused (spm window, 2026-09-29)."""
    _pdk, project, isolated, _, _ = _tree(tmp_path)
    out = isolated / "reports/phase3/lvs_verdict.json"
    out.write_text(json.dumps({"status": "PASS", "message": "netgen LVS: " + "x" * 400}))
    copied, error = p3._phase3_window_publication(project, isolated, [out], RUN)
    assert error == "", error
    assert copied == [str(project / "reports/phase3/lvs_verdict.json")]
