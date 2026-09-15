"""R-0915-28 — a timing violation is judged at the basis its own report declares.

MEASURED on `subservient` x gf180mcuD (lane icsub2, run r13): one design, one
SDC (20 ns, derived from the design's own L9 table), two bases --

    pre-layout   SS 125C 4v50   worst setup -6.10 ns   TNS -371.15
    post-route   SS 125C 4v50   worst setup +0.97 ns   (multi-corner sign-off +0.03)

Placement, CTS, resizing and the repair passes are what close that corner.  The
pre-layout report says so about itself, in its own body::

    STA_BASIS: PRE_LAYOUT_ESTIMATE
    STA_BASIS_NOTE: ... excludes placement, CTS, resizing and all interconnect
    RC, so a corner shown as MET here may VIOLATE on the routed design

Raising the SIGN-OFF rule against that artefact asks it to prove a closure it
declares it cannot represent.  So the finding is KEPT -- the corner's numbers,
the file, and what they do and do not mean -- at a DISCLOSED tier, and
`STA_REAL_VIOLATION_FOUND` is raised against a post-route basis.

NOTHING IS SILENCED AND NOTHING IS INFERRED FROM SILENCE: a report that
declares NO basis is still judged as a sign-off report, because the softer tier
has to be EARNED by a declaration.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

PROG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROG))
import eda_report_audit as E  # noqa: E402

DISCLOSED = "STA_PRE_LAYOUT_VIOLATION_DISCLOSED"
SIGNOFF = "STA_REAL_VIOLATION_FOUND"

_BODY = """Startpoint: _3306_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: _3377_ (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

  Delay    Time   Description
---------------------------------------------------------
   0.00    0.00   clock clk (rise edge)
   2.04    2.04 ^ _3306_/Q (gf180mcu_fd_sc_mcu7t5v0__dffq_1)
  24.06   26.10 ^ _3377_/D (gf180mcu_fd_sc_mcu7t5v0__dffq_1)
  20.00   20.00   clock clk (rise edge)
          -6.10   slack (VIOLATED)
tns max -371.15
wns max -6.10
"""
_CLEAN = _BODY.replace("-6.10   slack (VIOLATED)", " 0.97   slack (MET)") \
              .replace("tns max -371.15", "tns max 0.00") \
              .replace("wns max -6.10", "wns max 0.00")


def _run(tmp_path, reports):
    """`reports` is {filename: body}. Returns (rc, report-dict)."""
    d = Path(tempfile.mkdtemp(dir=tmp_path))
    sta = d / "phase3" / "stage3" / "sta" / "per_corner"
    sta.mkdir(parents=True)
    for name, body in reports.items():
        (sta / name).write_text(body)
    rc = E.main([str(d), "--mode", "sta", "--under", "phase3/stage3/sta",
                 "--json", str(d / "out.json")])
    return rc, json.loads((d / "out.json").read_text())


def _rules(rep):
    return {f["rule"]: f["severity"] for f in rep["findings"]}


def _met(slack):
    """A MET report at `slack`. The three corners must differ or the unrelated
    `STA_CORNERS_NOT_DISTINCT` gate fires -- correctly, and on something this
    file is not about."""
    return (_BODY.replace("-6.10   slack (VIOLATED)", f"{slack:6.2f}   slack (MET)")
                 .replace("tns max -371.15", "tns max 0.00")
                 .replace("wns max -6.10", f"wns max {slack:.2f}"))


def _three(basis, worst=_BODY):
    stamp = f"\nSTA_BASIS: {basis}\n" if basis else "\n"
    return {"sta_SS.rpt": worst + stamp,
            "sta_TT.rpt": _met(6.21) + stamp,
            "sta_FF.rpt": _met(11.42) + stamp}


# ------------------------------------------------- the direction that must change

def test_a_pre_layout_violation_is_disclosed_not_signed_off(tmp_path):
    rc, rep = _run(tmp_path, _three("PRE_LAYOUT_ESTIMATE"))
    assert DISCLOSED in _rules(rep)
    assert _rules(rep)[DISCLOSED] == "INFO"
    assert SIGNOFF not in _rules(rep)
    assert rep["passed"] is True
    assert rc == 0


def test_the_disclosure_carries_the_numbers_and_the_sentence(tmp_path):
    """Kept, not silenced: the corner's own slack, its TNS, the file they came
    from, and what a pre-layout number does and does not mean."""
    _, rep = _run(tmp_path, _three("PRE_LAYOUT_ESTIMATE"))
    f = [x for x in rep["findings"] if x["rule"] == DISCLOSED][0]
    assert "-6.1 ns" in f["message"]
    assert "-371.1 ns" in f["message"]
    assert "closes this corner only after PnR" in f["message"]
    assert "excludes placement, CTS, resizing and interconnect RC" in f["message"]
    assert f["file"].endswith("sta_SS.rpt")


def test_the_violation_is_still_on_the_record(tmp_path):
    """The tier changed; the FACT did not. Both halves are published so a
    reader can tell 'no violation' from 'disclosed at a non-sign-off basis'."""
    _, rep = _run(tmp_path, _three("PRE_LAYOUT_ESTIMATE"))
    s = rep["summary"]
    assert s["real_violation_found"] is True
    assert s["signoff_violation_found"] is False
    assert s["violation_declared_basis"] == "PRE_LAYOUT"


# ------------------------------------------------- the directions that must NOT change

def test_the_same_numbers_on_a_post_route_basis_still_fail(tmp_path):
    """THE NEGATIVE CONTROL. Identical slack, identical TNS, identical file
    shape -- only the declared basis differs, and the sign-off rule fires."""
    rc, rep = _run(tmp_path, _three("POST_ROUTE"))
    assert _rules(rep)[SIGNOFF] == "ERROR"
    assert DISCLOSED not in _rules(rep)
    assert rep["passed"] is False
    assert rc == 1
    assert rep["summary"]["signoff_violation_found"] is True


def test_a_report_that_declares_no_basis_is_fail_closed(tmp_path):
    """Silence does not buy the softer tier. An undeclared report is judged as
    a sign-off report, and the message says why."""
    rc, rep = _run(tmp_path, _three(None))
    assert _rules(rep)[SIGNOFF] == "ERROR"
    assert DISCLOSED not in _rules(rep)
    assert rep["passed"] is False
    assert rc == 1
    f = [x for x in rep["findings"] if x["rule"] == SIGNOFF][0]
    assert "declares NO STA basis of its own" in f["message"]
    assert "cannot be inferred from silence" in f["message"]
    assert rep["summary"]["violation_declared_basis"] is None


def test_a_clean_pre_layout_run_reports_no_violation_at_all(tmp_path):
    """The disclosure is not a new thing that always fires: a pre-layout report
    with no negative slack produces neither finding."""
    _, rep = _run(tmp_path, _three("PRE_LAYOUT_ESTIMATE", worst=_met(0.97)))
    assert DISCLOSED not in _rules(rep)
    assert SIGNOFF not in _rules(rep)
    assert rep["summary"]["real_violation_found"] is False
    assert rep["passed"] is True


def test_the_tier_follows_the_evidence_report_not_the_scope(tmp_path):
    """The basis is read from the file the finding NAMES. A clean post-route
    report beside a violating pre-layout one must not drag it up a tier."""
    reports = _three("PRE_LAYOUT_ESTIMATE")
    reports["sta_post.rpt"] = _met(0.97) + "\nSTA_BASIS: POST_ROUTE\n"
    _, rep = _run(tmp_path, reports)
    assert rep["summary"]["violation_declared_basis"] == "PRE_LAYOUT"
    assert DISCLOSED in _rules(rep)
    assert SIGNOFF not in _rules(rep)
