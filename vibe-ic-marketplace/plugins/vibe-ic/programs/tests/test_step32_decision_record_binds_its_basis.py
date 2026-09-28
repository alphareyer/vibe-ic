"""Step 32: the canonical decision record is bound to the report it decided
from, so the no-repair certificate survives the status generator that
verifies it; and the derived generators' outcomes are no longer discarded.

MEASURED (IC-path gf180mcuD run, 2026-09-28, evidence copy replayed): the
canonical `postroute_timing_repair_decision.json` said `repair_needed=false`
(multi-corner OCV, setup +2.68 ns, hold +0.44 ns, action
`no_repair_needed_flag`) and canonicalize wrote `no_repair_needed.flag`. The
Step-32 status generator dispatched after it accepts a decision receipt only
when `source_report` resolves inside the project to a file whose sha256 is
`source_report_sha256` -- the receipt shape the Step-32 LibreLane producer
publishes. The canonical record carried neither field, so the generator
answered "NOT_MEASURED: Step-32 decision invalid: measured source or sha256
absent", unlinked the flag and exited 2. The runner discarded that exit and
its message; Step 32 failed with the flag "absent" while the runner's output
list still named it.

Real code paths: `step_canonicalize_artefacts` (docker faked: only the EDA
tool's report write), the shipped status generator, the shipped
`postroute_timing_repair_audit`, and `_run_derived_artefact_generators`.
chip-agnostic: synthetic project, no design names in any assertion.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROGRAMS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROGRAMS))
import phase3_one_shot_runner as R  # noqa: E402

REPAIR = "phase3/stage3/postroute_timing_repair"
DECISION = f"{REPAIR}/postroute_timing_repair_decision.json"
FLAG = f"{REPAIR}/no_repair_needed.flag"
LOG = f"{REPAIR}/repair_log.json"
STATUS_GEN = "postroute_timing_repair_status_gen.py"

PATH_MET = """\
Startpoint: a (rising edge-triggered)
Endpoint: b (rising edge-triggered)
Path Type: max
           0.47   slack (MET)
"""
EST_MET = PATH_MET + "\ntns 0.00\n"
SPEF_MET = PATH_MET + "\ntns max 0.00\nworst slack max 0.47\n"
SPEF_VIOLATED = """\
Startpoint: a (rising edge-triggered)
Endpoint: b (rising edge-triggered)
Path Type: max
         -12.27   slack (VIOLATED)

tns max -60088.21
wns max -12.27
worst slack max -12.27
"""


def _pdk():
    return R.PdkConfig(
        name="sky130A", liberty="/foss/pdks/x.lib", tech_lef="/t.tlef",
        cell_lef="/c.lef", cell_gds=None, site="s", drc_deck=None)


def _proj(root: Path) -> Path:
    pnr = root / "phase3" / "stage3" / "pnr"
    pnr.mkdir(parents=True)
    (pnr / "chip_top.def").write_text("VERSION 5.8 ;\nDESIGN chip_top ;\n"
                                      "END DESIGN\n")
    (pnr / "sta.rpt").write_text(EST_MET)
    (pnr / "chip_top_pnr.v").write_text("module chip_top();\nendmodule\n")
    (pnr / "constraint.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    synth = root / "phase2" / "stage2" / "synth"
    synth.mkdir(parents=True)
    (synth / "chip_top_synth.v").write_text("module chip_top();\nendmodule\n")
    ext = root / "phase3" / "stage3" / "extracted"
    ext.mkdir(parents=True)
    (ext / "chip_top.spef").write_text(
        "*SPEF \"IEEE 1481-1998\"\n*DESIGN \"chip_top\"\n" + "x" * 200)
    return root


def _fake_docker(spef_sta_text):
    """Every container call succeeds; the `sta -no_init` call writes the
    SPEF-based report where the deck's `report_checks > <rpt>` names it --
    the only EDA tool write this path needs."""
    def fake(container, cmd, timeout=0, **_):
        if "sta -no_init" in cmd:
            m = re.search(r"-exit\s+(\S+\.tcl)", cmd)
            if m and Path(m.group(1)).is_file():
                rm = re.search(r"report_checks > (\S+)",
                               Path(m.group(1)).read_text())
                if rm:
                    Path(rm.group(1)).write_text(spef_sta_text)
        return (0, "", "")
    return fake


def _canonicalize(p: Path, monkeypatch, spef_text: str) -> None:
    monkeypatch.setattr(R, "_docker_exec", _fake_docker(spef_text))
    monkeypatch.setattr(R, "_to_container_path", lambda s, c: s)
    R.step_canonicalize_artefacts(p, "chip_top", _pdk(), "x")


def _bound_basis(p: Path, record: dict) -> Path:
    """The check a Step-32 receipt consumer applies before it may read the
    decision: the named report exists inside the project and its bytes are
    the ones the record was decided from."""
    rel = record.get("source_report")
    digest = record.get("source_report_sha256")
    assert isinstance(rel, str) and isinstance(digest, str), (
        "the Step-32 decision record names no measured basis "
        f"(source_report={rel!r}, source_report_sha256={digest!r})")
    source = (p / rel).resolve()
    assert source.is_relative_to(p.resolve()) and source.is_file(), rel
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, rel
    return source


def _status_gen(p: Path) -> subprocess.CompletedProcess:
    """The generator exactly as the runner dispatches it."""
    return subprocess.run([sys.executable, str(PROGRAMS / STATUS_GEN), str(p)],
                          capture_output=True, text=True, timeout=120)


def test_no_repair_record_is_bound_and_the_flag_survives_the_generator(
        tmp_path, monkeypatch):
    p = _proj(tmp_path)
    _canonicalize(p, monkeypatch, SPEF_MET)
    record = json.loads((p / DECISION).read_text())
    assert record["repair_needed"] is False
    assert record["action"] == "no_repair_needed_flag"
    assert (p / FLAG).is_file()
    basis = _bound_basis(p, record)
    # Single-corner basis here: the report canonicalize parsed for timing.
    assert basis.name == "sta_spef_based.rpt"

    cp = _status_gen(p)
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["artefact"] == FLAG
    assert (p / FLAG).is_file() and not (p / LOG).exists()

    # The binding is to BYTES: a changed basis no longer matches the record.
    basis.write_text(basis.read_text() + "\n")
    assert hashlib.sha256(basis.read_bytes()).hexdigest() \
        != record["source_report_sha256"]


def test_multi_corner_basis_binds_the_stance_the_decision_read(
        tmp_path, monkeypatch):
    p = _proj(tmp_path)
    stance = p / "reports/phase3/mcorner_ocv_stance.json"
    stance.parent.mkdir(parents=True, exist_ok=True)
    stance.write_text(json.dumps({
        "signoff_dimension": "multi_corner_ocv_process",
        "setup_process_corner": "SS", "hold_process_corner": "FF",
        "multi_process_corner": True,
        "report": "phase3/stage3/sta/sta_mcorner_ocv.rpt",
        "setup_worst_slack_ns": 2.5, "hold_worst_slack_ns": 0.4,
        "violated_corners": []}))
    # Newer than every layout input, so canonicalize reuses it as measured.
    future = time.time() + 3600
    os.utime(stance, (future, future))
    _canonicalize(p, monkeypatch, SPEF_MET)
    record = json.loads((p / DECISION).read_text())
    assert record["basis"] == "multi_corner_ocv"
    assert record["repair_needed"] is False
    assert _bound_basis(p, record) == stance.resolve()
    assert (p / FLAG).is_file()


def test_genuine_repair_needed_still_requires_a_repair_log(tmp_path, monkeypatch):
    p = _proj(tmp_path)
    _canonicalize(p, monkeypatch, SPEF_VIOLATED)
    record = json.loads((p / DECISION).read_text())
    assert record["repair_needed"] is True
    _bound_basis(p, record)
    assert not (p / FLAG).exists()

    cp = _status_gen(p)
    assert cp.returncode == 0, cp.stderr
    assert not (p / FLAG).exists()
    log = json.loads((p / LOG).read_text())
    assert log["verdict"] == "REPAIR_REQUIRED"
    # A demand, not a certificate: the audit refuses a log with no repair.
    audit = subprocess.run(
        [sys.executable, str(PROGRAMS / "postroute_timing_repair_audit.py"),
         str(p)], capture_output=True, text=True, timeout=120)
    assert audit.returncode != 0, audit.stdout


def test_no_measured_basis_binds_nothing(tmp_path):
    decision = {"repair_needed": True, "mc_ocv_available": False}
    record = R._step32_decision_record(
        tmp_path, decision, tmp_path / "reports/phase3/mcorner_ocv_stance.json",
        tmp_path / "phase3/stage3/sta/absent.rpt")
    assert record["source_report"] is None
    assert record["source_report_sha256"] is None
    assert record["repair_needed"] is True


def test_a_derived_generator_refusal_is_reported_not_discarded(
        tmp_path, monkeypatch, capsys):
    # No STA report on disk: the shipped status generator refuses (rc 2).
    monkeypatch.setattr(R, "_DERIVED_ARTEFACT_GENERATORS",
                        ((STATUS_GEN, "post-route repair no-op flag"),))
    outcomes = R._run_derived_artefact_generators(tmp_path, None)
    assert [o["program"] for o in outcomes] == [STATUS_GEN]
    assert outcomes[0]["rc"] == 2
    assert "no STA report" in outcomes[0]["message"]
    err = capsys.readouterr().err
    assert f"{STATUS_GEN} exited rc=2" in err
    assert "no STA report" in err
