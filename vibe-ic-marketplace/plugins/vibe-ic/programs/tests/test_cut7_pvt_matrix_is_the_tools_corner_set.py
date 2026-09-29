"""CUT_W4 step 7 (R-0929-TOOL-DEFAULT): the PVT matrix is the tool's corner set.

Two own writers used to produce `pvt_matrix.json` -- step 7c and
`step_canonicalize_artefacts` -- from a host glob, a container `ls`, and a
corner-name preference (`_select_signoff_corners`), and they drifted (#442's
disclosure lived in one of them, 04682d82b). LibreLane already answers "which
corners does this PDK sign off at": STA_CORNERS / CELL_LIBS / DEFAULT_CORNER,
resolved from the PDK config by the installed resolver. That is the one source
now (`_write_pvt_matrix` over `librelane_prelayout.resolve_pdk_view`); only the
resolver container's file write is faked here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import phase3_one_shot_runner as R  # noqa: E402
import _resolved_pdk_view_fixture as RV  # noqa: E402

_NINE = [f"{rc}_{p}" for rc in ("nom", "min", "max")
         for p in ("tt_025C_5v00", "ss_125C_4v50", "ff_n40C_5v50")]
_RESOLVED = {"STA_CORNERS": _NINE, "DEFAULT_CORNER": "nom_tt_025C_5v00",
             "CELL_LIBS": {f"*_{p}": [f"/pdk/famxD/libs.ref/sc/lib/sc__{p}.lib"]
                           for p in ("tt_025C_5v00", "ss_125C_4v50", "ff_n40C_5v50")}}


class _Pdk:
    name = "famxD"
    liberty = "/foss/pdks/famxD/libs.ref/sc/lib/sc__tt_025C_5v00.lib"


def _matrix(tmp_path, monkeypatch, resolved, **kw):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    RV.install(monkeypatch, proj, "famxD", resolved, **kw)
    out = proj / "phase2/stage2/constraints/pvt_matrix.json"
    R._write_pvt_matrix(proj, _Pdk(), out)
    return proj, json.loads(out.read_text())


def test_the_matrix_is_every_corner_the_tool_resolves(tmp_path, monkeypatch):
    """#565 / GAP-E2E-2 (the built-in PDK is invisible to a host glob) and
    626896b9e (a name preference picked a phantom 1v40 SS corner) are both moot:
    the rows are exactly the tool's STA_CORNERS, each bound to its CELL_LIBS."""
    _proj, doc = _matrix(tmp_path, monkeypatch, _RESOLVED)
    assert [c["name"] for c in doc["corners"]] == _NINE
    assert {c["label"] for c in doc["corners"]} == {"SS", "TT", "FF"}
    assert doc["primary_corner"] == "nom_tt_025C_5v00"
    assert doc["multi_corner"] is True and doc["corner_count"] == 9
    for row in doc["corners"]:
        assert row["liberty"].endswith(row["name"].split("_", 1)[1] + ".lib"), row
    assert doc["corner_source"].startswith("LibreLane resolved STA_CORNERS")


def test_an_unresolved_corner_set_is_disclosed_never_guessed(tmp_path, monkeypatch):
    """#442 stays: no resolver answer (here: the run recorded no image) is an
    empty matrix that SAYS so, and names why -- not a globbed or preferred
    corner."""
    _proj, doc = _matrix(tmp_path, monkeypatch, _RESOLVED, record_image=False)
    assert doc["corners"] == [] and doc["multi_corner"] is False
    assert doc["coverage"] == "NO_CORNERS" and "#442" in doc["note"]
    assert doc["corner_source"].startswith("NOT_READ:")
    assert "LL_RUN_IMAGE_UNRECORDED" in doc["corner_source"]


def test_a_design_that_stages_its_own_liberty_corners_declares_them(tmp_path, monkeypatch):
    """No tool destination (named in the HARVEST table): a design-staged
    Liberty is design input, so it is listed as declared."""
    proj = tmp_path / "proj"
    lib = proj / "input/pdk/liberty"
    lib.mkdir(parents=True)
    for corner in ("x__ss_125C_4v50", "x__tt_025C_5v00"):
        (lib / f"{corner}.lib").write_text("library(x){}")
    _proj, doc = _matrix(tmp_path, monkeypatch, _RESOLVED)
    assert [c["name"] for c in doc["corners"]] == ["x__ss_125C_4v50", "x__tt_025C_5v00"]
    assert doc["corner_source"].startswith("design-staged")


def test_the_tool_run_outranks_the_staged_list(tmp_path, monkeypatch):
    """When STAPrePNR already timed its own corner set, that set is the matrix."""
    proj = tmp_path / "proj"
    (proj / "input/pdk/liberty").mkdir(parents=True)
    (proj / "input/pdk/liberty/x__tt.lib").write_text("library(x){}")
    out = proj / "pvt.json"
    R._write_pvt_matrix(proj, _Pdk(), out, resolved=_RESOLVED)
    assert [c["name"] for c in json.loads(out.read_text())["corners"]] == _NINE


def test_the_chip_path_times_step7_through_the_tool(tmp_path):
    """R-0929-TOOL-DEFAULT: a DIE project's step 7 runs the LibreLane arm."""
    import librelane_contract as LC
    import _owner_declared as _OD  # the one attestation fixture
    st = tmp_path / "input/submission_template"
    st.mkdir(parents=True)
    (st / "SELF_TAPEOUT.txt").write_text("# self tape-out\n")
    (st / "tapeout_declaration.json").write_text(json.dumps(_OD.attest(
        {"schema": "vibe-ic/tapeout_declaration/1", "answers": {"deliverable": "DIE"}})))
    assert LC.design_class(tmp_path) == LC.DESIGN_CLASS_CHIP_PAD_RING
    assert LC.selected_mode(tmp_path, "7") == "librelane"
    core = tmp_path / "core"
    core.mkdir()
    assert LC.selected_mode(core, "7") == "direct"
