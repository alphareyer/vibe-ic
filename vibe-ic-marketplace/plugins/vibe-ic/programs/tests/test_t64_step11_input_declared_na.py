"""The phase-3 DFT retry follows the same input-backed condition as Step 11."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import phase3_one_shot_runner as runner  # noqa: E402


def _project(tmp_path, *, input_text="No production test topology is specified."):
    p = tmp_path / "design"
    docs = p / "input/docs"
    docs.mkdir(parents=True)
    (docs / "product.md").write_text(input_text)
    ldoc = p / "phase1/generated_docs"
    ldoc.mkdir(parents=True)
    (ldoc / "L20_DFT_SCAN_TOPOLOGY.json").write_text(json.dumps({
        "doc_id": "L20", "applicability": "APPLICABLE",
        "fields": {"dft_present": False, "scan_chains": [],
                   "bist_mbist": [], "jtag_tap": None},
        "extraction_status": "NOT_YET_EXTRACTED",
    }))
    synth = p / "phase2/stage2/synth"
    synth.mkdir(parents=True)
    (synth / "netlist.v").write_text("module unit(); endmodule\n")
    return p


def test_input_absence_stands_down_phase3_retry(tmp_path):
    p = _project(tmp_path)
    needs, why = runner.step11_needs_rerun(p)
    assert needs is False
    assert "NOT_APPLICABLE" in why and "input" in why
    rows = runner.run_step11_dft_after_synth(p, "unit", "fake")
    assert len(rows) == 1 and rows[0].status == "NOT_APPLICABLE"
    assert not (p / "phase3/stage3/dft_selfheal_ledger.json").exists()


def test_input_scan_mention_keeps_step11_live(tmp_path):
    p = _project(tmp_path, input_text="The scan chain shall be tested.")
    needs, why = runner.step11_needs_rerun(p)
    assert needs is True, why
