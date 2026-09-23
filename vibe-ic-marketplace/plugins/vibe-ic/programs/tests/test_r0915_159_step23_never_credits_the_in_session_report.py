"""R-0915-159 — post-route sign-off is never credited from the in-session report.

Step 23's gate reads ONLY `phase3/stage3/sta/post_route_timing.rpt` (the yaml
clause and the runner's inline call both pass `--under` that path). When the
single-corner SPEF STA refused or is absent, the runner writes that file as a
COPY (`# STA_ALIAS_BASIS: <name>`) of the first fresh basis among
sta_spef_based / sta_mcorner_ocv / sta_spef_multicorner / pnr/sta.rpt — and the
last is the in-session PnR STA, which stamps `STA_PARASITICS_PROVENANCE:
PNR_SESSION_UNVERIFIED`. MEASURED on spm run23's live tree (snapshot 19:12,
source ec313cde7): all three SPEF reports absent and pnr/sta.rpt present, so in
that state the alias would be of the in-session report and was GRADED.

Both directions: an in-session basis (by alias name or by stamp) reads
NOT_MEASURED naming the basis and the stamp; a SPEF-based basis is graded
exactly as the same bytes without the alias header.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROGRAMS = Path(__file__).resolve().parents[1]
if str(_PROGRAMS) not in sys.path:
    sys.path.insert(0, str(_PROGRAMS))

import eda_report_audit as ERA                    # noqa: E402

_PAD = "# " + ("=" * 78 + "\n") * 40
_BODY = (
    "OpenSTA 2.4.0 report_checks\n"
    "Startpoint: reg_a (rising edge-triggered flip-flop clocked by clk)\n"
    "Endpoint: reg_b (rising edge-triggered flip-flop clocked by clk)\n"
    "Path Type: max\n"
    "WNS = 0.15 ns\nTNS = 0.0 ns\n"
    "slack (MET)\nsetup check: PASS\nhold check: PASS\n"
    "data arrival time: 2.34 ns\n" + _PAD
)
_UNVERIFIED = "STA_PARASITICS_PROVENANCE: PNR_SESSION_UNVERIFIED\n"
_SCOPE = "phase3/stage3/sta/post_route_timing.rpt"


def _audit(tmp: Path, text: str, *, extra=None):
    sta = tmp / "phase3" / "stage3" / "sta"
    sta.mkdir(parents=True, exist_ok=True)
    (sta / "post_route_timing.rpt").write_text(text)
    for rel, body in (extra or {}).items():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text(body)
    out = tmp / "audit.json"
    argv = [str(tmp), "--mode", "sta", "--json", str(out)]
    if extra is None:
        argv += ["--under", _SCOPE]
    rc = ERA.main(argv)
    return rc, json.loads(out.read_text())


def _rules(rep):
    return {f["rule"] for f in rep["findings"]}


def test_the_premise_the_body_passes_step23_today(tmp_path):
    rc, rep = _audit(tmp_path, _BODY)
    assert rc == 0 and rep["passed"], rep


def test_an_alias_of_the_in_session_report_is_not_measured(tmp_path):
    text = ("# post_route_timing.rpt — canonical post-route STA.\n"
            "# Basis: pre-SPEF estimate (report_checks) (sta.rpt), because no "
            "single-corner SPEF STA was produced.\n"
            "# STA_ALIAS_BASIS: sta.rpt\n" + _UNVERIFIED + _BODY)
    rc, rep = _audit(tmp_path, text)
    assert rc == 1 and not rep["passed"], rep
    assert rep["verdict"] == "NOT_MEASURED", rep
    f = next(x for x in rep["findings"]
             if x["rule"] == "STA_SIGNOFF_BASIS_IS_IN_SESSION")
    assert "no SPEF-based sign-off STA ran" in f["message"]
    assert "sta.rpt" in f["message"] and "PNR_SESSION_UNVERIFIED" in f["message"]
    # the absence is the finding, not a shape defect of the report
    assert not {"STA_WNS_TNS", "STA_SETUP_HOLD",
                "STA_VALUE_UNDETERMINED"} & _rules(rep), rep


def test_the_stamp_alone_is_enough(tmp_path):
    rc, rep = _audit(tmp_path, _UNVERIFIED + _BODY)
    assert rep["verdict"] == "NOT_MEASURED" and rc == 1, rep


def test_the_alias_name_alone_is_enough(tmp_path):
    rc, rep = _audit(tmp_path, "# STA_ALIAS_BASIS: sta.rpt\n" + _BODY)
    assert rep["verdict"] == "NOT_MEASURED" and rc == 1, rep


def test_a_spef_based_basis_is_graded_as_today(tmp_path):
    for basis in ("sta_spef_based.rpt", "sta_mcorner_ocv.rpt",
                  "sta_spef_multicorner.rpt"):
        d = tmp_path / basis
        rc, rep = _audit(d, f"# STA_ALIAS_BASIS: {basis}\n" + _BODY)
        rc0, rep0 = _audit(d / "plain", _BODY)
        assert (rc, rep["passed"]) == (rc0, rep0["passed"]) == (0, True), rep
        assert "STA_SIGNOFF_BASIS_IS_IN_SESSION" not in _rules(rep)
        assert "verdict" not in rep, rep


def test_a_violation_written_in_the_in_session_report_still_fails(tmp_path):
    body = _BODY.replace("slack (MET)", "slack (VIOLATED)").replace(
        "WNS = 0.15 ns", "WNS = -0.40 ns")
    rc, rep = _audit(tmp_path, _UNVERIFIED + body)
    assert rc == 1 and rep.get("verdict") != "NOT_MEASURED", rep
    assert "STA_REAL_VIOLATION_FOUND" in _rules(rep), rep


def test_beside_a_spef_report_the_in_session_one_does_not_vote(tmp_path):
    """Project-wide discovery (no --under): the SPEF report decides, and the
    in-session report neither passes nor refuses the audit."""
    rc, rep = _audit(tmp_path, _BODY, extra={
        "phase3/stage3/pnr/sta.rpt": _UNVERIFIED + _BODY.replace(
            "WNS = 0.15 ns", "WNS = 9.99 ns")})
    assert rep.get("verdict") != "NOT_MEASURED", rep
    assert "STA_SIGNOFF_BASIS_IS_IN_SESSION" not in _rules(rep), rep
    assert [r["file"] for r in rep["summary"]["sta_in_session_reports"]] \
        == [str(tmp_path / "phase3/stage3/pnr/sta.rpt")], rep["summary"]


def test_a_free_text_mention_is_not_an_in_session_basis(tmp_path):
    text = ("# this report is not the STA_ALIAS_BASIS: sta.rpt copy\n"
            "# and it is never PNR_SESSION_UNVERIFIED\n" + _BODY)
    rc, rep = _audit(tmp_path, text)
    assert rc == 0 and rep["passed"] and "verdict" not in rep, rep
