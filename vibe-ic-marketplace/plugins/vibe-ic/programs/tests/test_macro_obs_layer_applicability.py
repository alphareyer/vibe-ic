"""A complete DEF PDN read proves when an OBS layer cannot intersect supply metal."""
import json
import subprocess
import sys
from pathlib import Path

import macro_obs_geometry_intersect_check as gate
import flow_compliance_check as audit
from _hostpaths import require_repo


PROGRAM = Path(gate.__file__)
LEF = """MACRO block_a
  SIZE 100 BY 60 ;
  OBS
    LAYER MET1 ;
      RECT 0 0 100 60 ;
    LAYER MET3 ;
      RECT 0 0 100 60 ;
    LAYER nwell ;
      RECT 0 0 100 60 ;
  END
END block_a
"""
HEAD = """VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
COMPONENTS 1 ;
- u_blk block_a + FIXED ( 100000 100000 ) N ;
END COMPONENTS
SPECIALNETS 1 ;
"""
TAIL = "END SPECIALNETS\nEND DESIGN\n"


def _def(layer="MET2", *, closed=True, use="POWER"):
    use_clause = f"+ USE {use} " if use else ""
    body = (f"- supply ( * pin ) {use_clause}+ ROUTED {layer} 480 "
            "+ SHAPE STRIPE ( 0 130000 ) ( 300000 * ) ;\n")
    return HEAD + body + (TAIL if closed else "END DESIGN\n")


def _run(tmp_path, def_text):
    pnr = tmp_path / "phase3/stage3/pnr"
    pnr.mkdir(parents=True, exist_ok=True)
    (pnr / "routed.def").write_text(def_text)
    (tmp_path / "macro.lef").write_text(LEF)
    out = tmp_path / "reports/phase3/pnr/macro_obs_geometry.json"
    run = subprocess.run([sys.executable, str(PROGRAM), str(tmp_path),
                          "--json", str(out)], capture_output=True, text=True)
    return run, json.loads(out.read_text())


def test_absent_supply_layers_have_def_backed_structural_na(tmp_path):
    run, report = _run(tmp_path, _def())
    assert run.returncode == 2, run.stdout + run.stderr
    assert report["reason_class"] == "NOT_APPLICABLE_BY_STRUCTURE"
    assert report["pdn_supply_layers_read"] == ["met2"]
    assert report["pdn_read_complete"] is True
    for layer in ("met1", "met3", "nwell"):
        rec = report["obs_layer_applicability"][layer]
        assert rec["verdict"] == "NOT_APPLICABLE_BY_STRUCTURE"
        assert rec["supply_segments_on_layer"] == 0
        assert rec["proof"]["source"] == "routed DEF SPECIALNETS"


def test_routing_layer_with_intersection_still_fails(tmp_path):
    run, report = _run(tmp_path, _def("MET1"))
    assert run.returncode == 1, run.stdout + run.stderr
    assert report["findings_count"] == 1
    assert report["obs_layer_applicability"]["met1"]["verdict"] == "FAIL"
    assert report["obs_layer_applicability"]["met3"]["verdict"] == "NOT_APPLICABLE_BY_STRUCTURE"


def test_special_signal_net_is_not_a_pdn_supply_segment(tmp_path):
    run, report = _run(tmp_path, _def("MET1", use="SIGNAL"))
    assert run.returncode == 2, run.stdout + run.stderr
    assert report["reason_class"] == "NOT_APPLICABLE_BY_STRUCTURE"
    assert report["pdn_supply_layers_read"] == []


def test_unclassified_special_route_cannot_prove_no_supply(tmp_path):
    run, report = _run(tmp_path, _def("MET1", use=None))
    assert run.returncode == 2, run.stdout + run.stderr
    assert report["reason_class"] != "NOT_APPLICABLE_BY_STRUCTURE"
    assert report["obs_layer_applicability"]["met1"]["verdict"] == "NOT_MEASURED"


def test_unreadable_pdn_cannot_prove_absence(tmp_path):
    run, report = _run(tmp_path, _def(closed=False))
    assert run.returncode == 2, run.stdout + run.stderr
    assert report["reason_class"] != "NOT_APPLICABLE_BY_STRUCTURE"
    assert report["obs_layer_applicability"]["met3"]["verdict"] == "NOT_MEASURED"


def test_step_audit_maps_proven_absence_but_not_unreadable_pdn(tmp_path):
    clause = ("macro_obs_geometry_intersect_check . --json "
              "reports/phase3/pnr/macro_obs_geometry.json")
    _run(tmp_path, _def())
    decided = audit._check_program_exit_zero(tmp_path, clause)
    assert decided.exit_code == 2
    assert decided.verdict == "NOT_APPLICABLE"
    assert decided.reason_class == "NOT_APPLICABLE_BY_STRUCTURE"

    _run(tmp_path, _def(closed=False))
    unknown = audit._check_program_exit_zero(tmp_path, clause)
    assert unknown.exit_code == 2
    assert unknown.verdict in {"BLOCKED", "INCOMPLETE"}
    assert unknown.reason_class != "NOT_APPLICABLE_BY_STRUCTURE"


def test_checked_in_def_specialnet_population_is_read():
    source = require_repo(
        "vibe-ic-marketplace", "plugins", "vibe-ic", "programs",
        "calibration", "pg_supply_owned_negative.def")
    text = source.read_text()
    segments, gaps = gate.parse_routed_segments_with_gaps(text)
    evidence = gate.pdn_read_evidence(text, gaps)
    assert evidence == {"complete": True, "issue": "",
                        "declared_nets": 2, "parsed_nets": 2}
    assert segments == []  # declared PG nets have no routed supply geometry
