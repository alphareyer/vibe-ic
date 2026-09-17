"""r36 (subservient x gf180mcuD as a DIE): an SDR candidate whose placement is
MEASURED illegal is refused, whatever its router DRC says.

MEASURED: both post-route SDR children ended router_drc 0 -> 0 with
placement_violations 8 and 34 (resized buffers on top of tap cells). Both were
ACCEPTED on router DRC alone; the shipped DEF then failed PNR_PLACEMENT_ILLEGAL
and no GDS was written. The nonfatal branch already required `_sdr_pv == 0`;
the clean branch did not.

Both directions, executed in tclsh through the transaction's own harness:
a measured non-zero (or -1, check_placement failed) count refuses both a
clean-preserved and a strictly improved candidate and ships the checkpoint; a
measured zero and an unmeasured (NA) candidate are decided exactly as before.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_postroute_sdr_transaction as H  # noqa: E402


def _tclsh():
    assert shutil.which("tclsh"), "tclsh is required to execute the emitted Tcl"


@pytest.mark.parametrize("pv", [8, 34, -1])
def test_a_clean_route_on_an_illegal_placement_is_refused(tmp_path, pv):
    _tclsh()
    run, out = H._run(tmp_path, before=0, after=0, placement_violations=pv)
    assert run.returncode == 0, run.stderr
    assert "SDR_TRANSACTION_ACCEPTED" not in run.stdout
    assert f"SDR_TRANSACTION_CANDIDATE_PLACEMENT_VIOLATIONS: {pv}" in run.stdout
    assert "candidate_placement_illegal" in H._receipt(out)
    assert "FINAL:BASE" in run.stdout


def test_a_strict_router_improvement_does_not_buy_an_illegal_placement(tmp_path):
    _tclsh()
    run, out = H._run(tmp_path, before=4, after=2, placement_violations=34)
    assert "SDR_TRANSACTION_ACCEPTED" not in run.stdout
    assert "candidate_placement_illegal" in H._receipt(out)


def test_a_measured_legal_placement_is_accepted_as_before(tmp_path):
    _tclsh()
    run, out = H._run(tmp_path, before=0, after=0, placement_violations=0)
    assert "SDR_TRANSACTION_ACCEPTED: router_drc clean-preserved (0 -> 0)" in run.stdout
    assert "ACCEPTED\trouter_drc_preserved_clean\t0\t0" in H._receipt(out)


def test_an_unmeasured_placement_keeps_the_previous_decision(tmp_path):
    _tclsh()
    run, out = H._run(tmp_path, before=4, after=2, placement_violations=None)
    assert "SDR_TRANSACTION_ACCEPTED: router_drc=4 -> 2" in run.stdout
    assert "candidate_placement_illegal" not in H._receipt(out)
